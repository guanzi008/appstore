from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import Workbook

from openGA.ocr import OCRLine, OCRMatchError, default_ocr_python, find_text_match, ocr_image
from appstore.ai_click_targets import AIClickTargetsConfig, choose_click_targets
from appstore.ai_capture_review import AICaptureReviewConfig, review_capture_text
from appstore.ai_capture_planner import (
    AICapturePlannerConfig,
    default_capture_prompt,
    plan_capture_steps,
    retry_capture_prompt,
)
from appstore.capture_steps import CaptureStep, default_capture_steps, parse_capture_steps
from appstore.desktop_entry import DesktopEntry, choose_desktop_entry, load_desktop_entry
from appstore.inspectors import read_package_info
from appstore.models import PackageInfo
from appstore.screenshot_semantics import semantic_rejection_reasons, semantic_similarity, semantic_text_signature
from appstore.screenshot_validation import (
    ScreenshotValidationReport,
    validate_screenshot_paths,
    validation_report_json,
    validation_report_payload,
)


DEFAULT_DEB_INSTALL_TEMPLATE = "sudo apt-get install --allow-downgrades -y {package_path}"
DEFAULT_DEB_UNINSTALL_TEMPLATE = "sudo apt-get remove -y {package_name}"
DEFAULT_LINGLONG_INSTALL_TEMPLATE = "ll-cli install {package_path}"
DEFAULT_LINGLONG_UNINSTALL_TEMPLATE = "ll-cli uninstall {package_name}"
DEFAULT_SCREEN_SIZE = "1920x1080x24"
DEFAULT_SCALE_FILTER = "1280:-2"
APPSTORE_SUDO_PASSWORD_ENV = "APPSTORE_SUDO_PASSWORD"
_SUDO_PREFIX_RE = re.compile(r"^\s*sudo(?:\s+-\S+)*\s+")


@dataclass(frozen=True)
class CaptureOptions:
    output_dir: Path
    steps: tuple[str, ...] = ()
    ai_prompt: str = ""
    ai_base_url: str = "http://127.0.0.1:8787/v1"
    ai_model: str = "openai-codex/gpt-5.4"
    ai_api_key: str = ""
    launch_command: str = ""
    desktop_file: str = ""
    window_name: str = ""
    window_class: str = ""
    install_command: str = ""
    uninstall_command: str = ""
    sudo_password: str = ""
    screen_size: str = DEFAULT_SCREEN_SIZE
    scale_filter: str = DEFAULT_SCALE_FILTER
    capture_tool: str = "scrot"
    ocr_backend: str = "auto"
    ocr_python: str = ""
    ocr_min_score: float = 0.35
    skip_install: bool = False
    keep_installed: bool = False
    dbus_session: bool = True
    window_timeout: float = 30.0
    settle_time: float = 1.5
    validate_screenshots: bool = True
    min_screenshots: int = 1
    max_screenshots: int = 6
    ai_review_rounds: int = 2
    min_screenshot_width: int = 640
    min_screenshot_height: int = 360
    min_screenshot_bytes: int = 4096
    min_screenshot_stddev: float = 2.5
    min_screenshot_gray_levels: int = 8


@dataclass(frozen=True)
class LaunchSpec:
    command: tuple[str, ...]
    window_name: str
    window_class: str
    desktop_file: str = ""


@dataclass(frozen=True)
class CaptureArtifact:
    label: str
    raw_path: Path
    image_path: Path


@dataclass(frozen=True)
class SceneElement:
    element_id: str
    text: str
    center_x: int
    center_y: int
    left: int
    top: int
    right: int
    bottom: int
    score: float


@dataclass
class CaptureExecutionTrace:
    requested_ocr_backend: str
    requested_ai_base_url: str
    requested_ai_model: str
    capture_stage: str = "queued"
    stage_history: list[str] = field(default_factory=lambda: ["queued"])
    effective_ocr_backend: str = ""
    ocr_calls: int = 0
    ai_planning_calls: int = 0
    ai_click_selection_calls: int = 0
    ai_review_calls: int = 0
    used_ocr: bool = False
    used_ai_planning: bool = False
    used_ai_click_selection: bool = False
    used_ai_review: bool = False

    def set_stage(self, stage: str) -> None:
        normalized = stage.strip() or self.capture_stage
        self.capture_stage = normalized
        if not self.stage_history or self.stage_history[-1] != normalized:
            self.stage_history.append(normalized)

    def note_ocr(self, backend: str) -> None:
        self.ocr_calls += 1
        self.used_ocr = True
        resolved_backend = _resolved_ocr_backend(backend)
        if resolved_backend:
            self.effective_ocr_backend = resolved_backend

    def note_ai_planning(self) -> None:
        self.ai_planning_calls += 1
        self.used_ai_planning = True

    def note_ai_click_selection(self) -> None:
        self.ai_click_selection_calls += 1
        self.used_ai_click_selection = True

    def note_ai_review(self) -> None:
        self.ai_review_calls += 1
        self.used_ai_review = True

    def to_payload(self) -> dict[str, object]:
        return {
            "capture_stage": self.capture_stage,
            "stage_history": list(self.stage_history),
            "requested_ocr_backend": self.requested_ocr_backend,
            "effective_ocr_backend": self.effective_ocr_backend,
            "ocr_calls": self.ocr_calls,
            "requested_ai_base_url": self.requested_ai_base_url,
            "requested_ai_model": self.requested_ai_model,
            "ai_planning_calls": self.ai_planning_calls,
            "ai_click_selection_calls": self.ai_click_selection_calls,
            "ai_review_calls": self.ai_review_calls,
            "used_ocr": self.used_ocr,
            "used_ai_planning": self.used_ai_planning,
            "used_ai_click_selection": self.used_ai_click_selection,
            "used_ai_review": self.used_ai_review,
        }


@dataclass(frozen=True)
class CapturePackageResult:
    row_id: int
    package_path: Path
    pkg_name: str
    pkg_version: str
    pkg_arch: str
    status: str
    message: str
    asset_dir: Path
    desktop_file: str = ""
    launch_command: tuple[str, ...] = ()
    screenshots: tuple[Path, ...] = ()
    rejected_screenshots: tuple[Path, ...] = ()
    execution_trace: dict[str, object] = field(default_factory=dict)


@dataclass
class XvfbSession:
    display: str
    process: subprocess.Popen[str]
    env: dict[str, str]


def capture_packages(
    *,
    package_paths: list[Path] | tuple[Path, ...],
    options: CaptureOptions,
) -> list[CapturePackageResult]:
    output_dir = Path(options.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[CapturePackageResult] = []
    for index, package_path in enumerate(package_paths, start=1):
        package_path = Path(package_path)
        package_info: PackageInfo | None = None
        asset_dir: Path | None = None
        trace = CaptureExecutionTrace(
            requested_ocr_backend=options.ocr_backend,
            requested_ai_base_url=options.ai_base_url,
            requested_ai_model=options.ai_model,
        )
        try:
            trace.set_stage("inspect_package")
            package_info = _inspect_package_path(package_path)
            trace.set_stage("package_inspected")
            asset_dir = output_dir / package_info.pkg_name / f"{package_info.pkg_version}-{package_info.pkg_arch}"
            _reset_asset_dir(asset_dir)
            result = _capture_single_package(
                row_id=index,
                package_path=package_path,
                package_info=package_info,
                options=options,
                asset_dir=asset_dir,
                trace=trace,
            )
        except Exception as exc:
            asset_dir = asset_dir or (output_dir / package_path.stem)
            asset_dir.mkdir(parents=True, exist_ok=True)
            result = CapturePackageResult(
                row_id=index,
                package_path=package_path,
                pkg_name=package_path.stem if package_info is None else package_info.pkg_name,
                pkg_version="" if package_info is None else package_info.pkg_version,
                pkg_arch="" if package_info is None else package_info.pkg_arch,
                status="capture_failed",
                message=str(exc),
                asset_dir=asset_dir,
                execution_trace=trace.to_payload(),
            )
            _write_package_metadata(result=result, asset_dir=asset_dir)
        results.append(result)

    _write_capture_reports(output_dir=output_dir, results=results)
    return results


def _capture_single_package(
    *,
    row_id: int,
    package_path: Path,
    package_info: PackageInfo,
    options: CaptureOptions,
    asset_dir: Path,
    trace: CaptureExecutionTrace,
) -> CapturePackageResult:
    min_screenshots, max_screenshots = _normalize_capture_limits(
        min_screenshots=options.min_screenshots,
        max_screenshots=options.max_screenshots,
    )
    family, _package_format = _infer_package_kind_from_path(package_path)
    logs_dir = asset_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    install_log = logs_dir / "install.log"
    uninstall_log = logs_dir / "uninstall.log"
    launch_stdout_log = logs_dir / "app.stdout.log"
    launch_stderr_log = logs_dir / "app.stderr.log"

    if not options.skip_install:
        trace.set_stage("installing_package")
        install_command = build_install_command(
            package_family=family,
            package_path=package_path,
            package_name=package_info.pkg_name,
            override=options.install_command,
            sudo_password=options.sudo_password,
        )
        _run_logged_command(
            install_command,
            log_path=install_log,
            env=_privileged_command_env(sudo_password=options.sudo_password),
        )
        trace.set_stage("package_installed")

    launch_spec: LaunchSpec | None = None
    xvfb_session: XvfbSession | None = None
    app_process: subprocess.Popen[str] | None = None
    accepted_artifacts: list[CaptureArtifact] = []
    all_artifacts: list[CaptureArtifact] = []
    captured_artifact_count = 0
    validation_report: ScreenshotValidationReport | None = None
    tried_click_targets: set[str] = set()
    try:
        trace.set_stage("resolving_launch")
        launch_spec = resolve_launch_spec(
            package_family=family,
            package_info=package_info,
            launch_command=options.launch_command,
            desktop_file=options.desktop_file,
            window_name=options.window_name,
            window_class=options.window_class,
        )
        steps = resolve_capture_steps(
            step_texts=options.steps,
            ai_prompt=options.ai_prompt,
            ai_base_url=options.ai_base_url,
            ai_model=options.ai_model,
            ai_api_key=options.ai_api_key,
            package_name=package_info.pkg_name,
            app_name=launch_spec.window_name or package_info.pkg_name,
            min_screenshots=min_screenshots,
            max_screenshots=max_screenshots,
            trace=trace,
        )
        trace.set_stage("starting_display")
        xvfb_session = start_xvfb(screen_size=options.screen_size)
        trace.set_stage("launching_application")
        app_process = launch_application(
            command=launch_spec.command,
            env=xvfb_session.env,
            stdout_log=launch_stdout_log,
            stderr_log=launch_stderr_log,
            dbus_session=options.dbus_session,
        )
        trace.set_stage("waiting_for_window")
        window_id = wait_for_window(
            env=xvfb_session.env,
            timeout=options.window_timeout,
            pid=app_process.pid,
            window_name=launch_spec.window_name,
            window_class=launch_spec.window_class,
        )
        prepare_window(
            env=xvfb_session.env,
            window_id=window_id,
            screen_size=options.screen_size,
        )
        time.sleep(options.settle_time)
        current_window_id = window_id
        trace.set_stage("capturing_screens")
        review_rounds_remaining = max(max_screenshots - 1, max(0, int(options.ai_review_rounds)))
        current_steps = steps
        while True:
            batch_artifacts, current_window_id = run_capture_steps(
                steps=current_steps,
                env=xvfb_session.env,
                window_id=current_window_id,
                window_name=launch_spec.window_name,
                window_class=launch_spec.window_class,
                asset_dir=asset_dir,
                capture_tool=options.capture_tool,
                scale_filter=options.scale_filter,
                screen_size=options.screen_size,
                settle_time=options.settle_time,
                start_index=len(all_artifacts),
                max_screenshots=max_screenshots,
                ocr_backend=options.ocr_backend,
                ocr_python=options.ocr_python,
                ocr_min_score=options.ocr_min_score,
                trace=trace,
            )
            all_artifacts.extend(batch_artifacts)
            captured_artifact_count = len(all_artifacts)
            tried_click_targets.update(
                step.value.strip()
                for step in current_steps
                if step.action in {"click-text", "click"} and step.value.strip()
            )

            if options.validate_screenshots:
                trace.set_stage("validating_screenshots")
                semantic_validator = _make_semantic_validator(
                    ocr_backend=options.ocr_backend,
                    ocr_python=options.ocr_python,
                    package_name=package_info.pkg_name,
                    app_name=launch_spec.window_name or package_info.pkg_name,
                    ai_base_url=options.ai_base_url,
                    ai_model=options.ai_model,
                    ai_api_key=options.ai_api_key,
                    trace=trace,
                )
                validation_report = validate_screenshot_paths(
                    tuple(artifact.image_path for artifact in all_artifacts),
                    min_width=options.min_screenshot_width,
                    min_height=options.min_screenshot_height,
                    min_file_size=options.min_screenshot_bytes,
                    min_gray_stddev=options.min_screenshot_stddev,
                    min_unique_gray_levels=options.min_screenshot_gray_levels,
                    semantic_validator=semantic_validator,
                )
                _write_screenshot_validation_report(asset_dir=asset_dir, report=validation_report)
                accepted_paths = set(validation_report.accepted_paths)
                accepted_artifacts = [
                    artifact for artifact in all_artifacts if artifact.image_path in accepted_paths
                ]
            else:
                accepted_artifacts = list(all_artifacts)

            if len(accepted_artifacts) >= min_screenshots:
                break
            if captured_artifact_count >= max_screenshots:
                break
            if review_rounds_remaining <= 0:
                break

            trace.set_stage("runtime_replanning")
            current_steps = _resolve_runtime_capture_steps(
                env=xvfb_session.env,
                window_id=current_window_id,
                capture_tool=options.capture_tool,
                screen_size=options.screen_size,
                ai_base_url=options.ai_base_url,
                ai_model=options.ai_model,
                ai_api_key=options.ai_api_key,
                package_name=package_info.pkg_name,
                app_name=launch_spec.window_name or package_info.pkg_name,
                min_screenshots=min_screenshots,
                max_screenshots=max_screenshots,
                accepted_labels=tuple(artifact.label for artifact in accepted_artifacts),
                rejected_reasons=_summarize_rejected_reasons(validation_report),
                tried_click_targets=tuple(sorted(tried_click_targets)),
                accepted_paths=tuple(artifact.image_path for artifact in accepted_artifacts),
                ocr_backend=options.ocr_backend,
                ocr_python=options.ocr_python,
                trace=trace,
            )
            review_rounds_remaining -= 1
            if not current_steps:
                break

        if len(accepted_artifacts) < min_screenshots:
            raise RuntimeError(
                f"captured screenshots below minimum: got {len(accepted_artifacts)}, "
                f"require at least {min_screenshots}"
            )
    finally:
        if app_process is not None:
            terminate_process(app_process)
        stop_xvfb(xvfb_session)
        if not options.keep_installed and not options.skip_install:
            uninstall_command = build_uninstall_command(
                package_family=family,
                package_name=package_info.pkg_name,
                override=options.uninstall_command,
                sudo_password=options.sudo_password,
            )
            _run_logged_command(
                uninstall_command,
                log_path=uninstall_log,
                env=_privileged_command_env(sudo_password=options.sudo_password),
                check=False,
            )

    result = CapturePackageResult(
        row_id=row_id,
        package_path=package_path,
        pkg_name=package_info.pkg_name,
        pkg_version=package_info.pkg_version,
        pkg_arch=package_info.pkg_arch,
        status="captured",
        message=_capture_result_message(
            captured_count=captured_artifact_count,
            accepted_count=len(accepted_artifacts),
            rejected_count=0 if validation_report is None else len(validation_report.rejected_paths),
        ),
        asset_dir=asset_dir,
        desktop_file="" if launch_spec is None else launch_spec.desktop_file,
        launch_command=() if launch_spec is None else launch_spec.command,
        screenshots=tuple(artifact.image_path for artifact in accepted_artifacts),
        rejected_screenshots=() if validation_report is None else validation_report.rejected_paths,
        execution_trace=trace.to_payload(),
    )
    trace.set_stage("completed")
    result = CapturePackageResult(
        row_id=result.row_id,
        package_path=result.package_path,
        pkg_name=result.pkg_name,
        pkg_version=result.pkg_version,
        pkg_arch=result.pkg_arch,
        status=result.status,
        message=result.message,
        asset_dir=result.asset_dir,
        desktop_file=result.desktop_file,
        launch_command=result.launch_command,
        screenshots=result.screenshots,
        rejected_screenshots=result.rejected_screenshots,
        execution_trace=trace.to_payload(),
    )
    _write_package_metadata(result=result, asset_dir=asset_dir, validation_report=validation_report)
    return result


def _reset_asset_dir(asset_dir: Path) -> None:
    if asset_dir.exists():
        shutil.rmtree(asset_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)


def resolve_capture_steps(
    *,
    step_texts: tuple[str, ...] | list[str],
    ai_prompt: str,
    ai_base_url: str,
    ai_model: str,
    ai_api_key: str,
    package_name: str,
    app_name: str,
    min_screenshots: int = 1,
    max_screenshots: int = 6,
    accepted_labels: tuple[str, ...] | list[str] = (),
    rejected_reasons: tuple[str, ...] | list[str] = (),
    trace: CaptureExecutionTrace | None = None,
) -> tuple[CaptureStep, ...]:
    target_screenshots = max(1, min(int(min_screenshots), int(max_screenshots)))
    if step_texts:
        return parse_capture_steps(step_texts, default_screenshot_count=target_screenshots)

    planner_prompt = ai_prompt.strip()
    if not planner_prompt and not accepted_labels:
        return default_capture_steps(screenshot_count=1)
    if not planner_prompt and accepted_labels:
        planner_prompt = retry_capture_prompt(
            package_name=package_name,
            app_name=app_name,
            min_screenshots=min_screenshots,
            max_screenshots=max_screenshots,
            accepted_labels=accepted_labels,
            rejected_reasons=rejected_reasons,
        )

    if planner_prompt:
        try:
            if trace is not None:
                trace.note_ai_planning()
            planned_steps = plan_capture_steps(
                prompt=planner_prompt,
                package_name=package_name,
                app_name=app_name,
                config=AICapturePlannerConfig(
                    base_url=ai_base_url,
                    model=ai_model,
                    api_key=ai_api_key,
                ),
            )
            return parse_capture_steps(planned_steps, default_screenshot_count=target_screenshots)
        except Exception:
            if accepted_labels:
                return _fallback_retry_capture_steps(
                    target_screenshots=target_screenshots,
                    accepted_count=len(tuple(accepted_labels)),
                    scene_elements=(),
                    accepted_texts=(),
                )
            return default_capture_steps(screenshot_count=target_screenshots)

    return parse_capture_steps((), default_screenshot_count=target_screenshots)


def _resolve_runtime_capture_steps(
    *,
    env: dict[str, str],
    window_id: str,
    capture_tool: str,
    screen_size: str,
    ai_base_url: str,
    ai_model: str,
    ai_api_key: str,
    package_name: str,
    app_name: str,
    min_screenshots: int,
    max_screenshots: int,
    accepted_labels: tuple[str, ...] | list[str],
    rejected_reasons: tuple[str, ...] | list[str],
    tried_click_targets: tuple[str, ...] | list[str],
    accepted_paths: tuple[Path, ...] | list[Path],
    ocr_backend: str,
    ocr_python: str,
    trace: CaptureExecutionTrace | None = None,
) -> tuple[CaptureStep, ...]:
    visible_texts, scene_elements = _current_window_ocr_context(
        env=env,
        window_id=window_id,
        capture_tool=capture_tool,
        screen_size=screen_size,
        tried_click_targets=tried_click_targets,
        ocr_backend=ocr_backend,
        ocr_python=ocr_python,
    )
    accepted_texts = tuple(
        text
        for text in (
            semantic_text_signature(
                path,
                ocr_backend=ocr_backend,
                ocr_python=ocr_python or default_ocr_python(),
            )
            for path in accepted_paths
        )
        if text.strip()
    )
    planner_prompt = retry_capture_prompt(
        package_name=package_name,
        app_name=app_name,
        min_screenshots=min_screenshots,
        max_screenshots=max_screenshots,
        accepted_labels=accepted_labels,
        rejected_reasons=rejected_reasons,
    )
    remaining_targets = 1
    if scene_elements:
        try:
            if trace is not None:
                trace.note_ai_click_selection()
            selected_targets = choose_click_targets(
                package_name=package_name,
                app_name=app_name,
                visible_texts=visible_texts,
                scene_elements=tuple(_format_scene_element(element) for element in scene_elements),
                accepted_texts=accepted_texts,
                rejected_reasons=rejected_reasons,
                tried_targets=tried_click_targets,
                max_targets=remaining_targets,
                config=AIClickTargetsConfig(
                    base_url=ai_base_url,
                    model=ai_model,
                    api_key=ai_api_key,
                ),
            )
        except Exception:
            selected_targets = ()
        if selected_targets:
            element_map = {element.element_id: element for element in scene_elements}
            selected_elements = tuple(
                element_map[element_id]
                for element_id in selected_targets
                if element_id in element_map
            )
            return _steps_for_scene_elements(
                elements=selected_elements,
                accepted_count=len(tuple(accepted_labels)),
            )
    if planner_prompt:
        try:
            if trace is not None:
                trace.note_ai_planning()
            planned_steps = plan_capture_steps(
                prompt=planner_prompt,
                package_name=package_name,
                app_name=app_name,
                current_visible_texts=visible_texts,
                clickable_texts=(),
                config=AICapturePlannerConfig(
                    base_url=ai_base_url,
                    model=ai_model,
                    api_key=ai_api_key,
                ),
            )
            return parse_capture_steps(planned_steps, default_screenshot_count=max(1, min_screenshots))
        except Exception:
            pass
    return _fallback_retry_capture_steps(
        target_screenshots=max(1, min_screenshots),
        accepted_count=len(tuple(accepted_labels)),
        scene_elements=scene_elements,
        accepted_texts=accepted_texts,
    )


def resolve_launch_spec(
    *,
    package_family: str,
    package_info: PackageInfo,
    launch_command: str,
    desktop_file: str,
    window_name: str,
    window_class: str,
) -> LaunchSpec:
    if launch_command.strip():
        return LaunchSpec(
            command=tuple(shlex.split(launch_command)),
            window_name=window_name.strip() or package_info.pkg_name,
            window_class=window_class.strip(),
        )

    if package_family == "deb":
        entries = discover_deb_desktop_entries(package_info.pkg_name)
        if entries:
            entry = choose_desktop_entry(entries, preferred=desktop_file)
            return LaunchSpec(
                command=entry.exec_command,
                window_name=window_name.strip() or entry.name or package_info.pkg_name,
                window_class=window_class.strip() or entry.startup_wm_class,
                desktop_file=str(entry.path),
            )
        return LaunchSpec(
            command=(package_info.pkg_name,),
            window_name=window_name.strip() or package_info.pkg_name,
            window_class=window_class.strip(),
        )

    if package_family == "linglong":
        return LaunchSpec(
            command=("ll-cli", "run", package_info.pkg_name),
            window_name=window_name.strip() or package_info.pkg_name,
            window_class=window_class.strip(),
        )
    raise ValueError(f"unsupported package family for launch: {package_family}")


def discover_deb_desktop_entries(package_name: str) -> tuple[DesktopEntry, ...]:
    completed = subprocess.run(
        ["dpkg-query", "-L", package_name],
        capture_output=True,
        check=True,
        text=True,
    )
    entries: list[DesktopEntry] = []
    for line in completed.stdout.splitlines():
        normalized = line.strip()
        if not normalized.endswith(".desktop"):
            continue
        path = Path(normalized)
        if not path.exists():
            continue
        try:
            entries.append(load_desktop_entry(path))
        except Exception:
            continue
    return tuple(entries)


def build_install_command(
    *,
    package_family: str,
    package_path: Path,
    package_name: str,
    override: str = "",
    sudo_password: str = "",
) -> list[str]:
    template = override.strip()
    if not template:
        if package_family == "deb":
            template = DEFAULT_DEB_INSTALL_TEMPLATE
        elif package_family == "linglong":
            template = DEFAULT_LINGLONG_INSTALL_TEMPLATE
        else:
            raise ValueError(f"unsupported package family for install: {package_family}")
    template = _authorize_privileged_template(template, sudo_password=sudo_password)
    return render_shell_template(
        template,
        package_path=str(package_path),
        package_name=package_name,
    )


def build_uninstall_command(
    *,
    package_family: str,
    package_name: str,
    override: str = "",
    sudo_password: str = "",
) -> list[str]:
    template = override.strip()
    if not template:
        if package_family == "deb":
            template = DEFAULT_DEB_UNINSTALL_TEMPLATE
        elif package_family == "linglong":
            template = DEFAULT_LINGLONG_UNINSTALL_TEMPLATE
        else:
            raise ValueError(f"unsupported package family for uninstall: {package_family}")
    template = _authorize_privileged_template(template, sudo_password=sudo_password)
    return render_shell_template(
        template,
        package_name=package_name,
    )


def _authorize_privileged_template(template: str, *, sudo_password: str) -> str:
    normalized = template.strip()
    match = _SUDO_PREFIX_RE.match(normalized)
    if match is None:
        return normalized
    remainder = normalized[match.end() :].lstrip()
    if not remainder:
        raise RuntimeError("invalid sudo command template")
    if sudo_password:
        return f"printf '%s\\n' \"$APPSTORE_SUDO_PASSWORD\" | sudo -S -p '' {remainder}"
    if not _command_exists("pkexec"):
        raise RuntimeError("pkexec not found and APPSTORE_SUDO_PASSWORD is not configured")
    return f"pkexec {remainder}"


def render_shell_template(template: str, **values: str) -> list[str]:
    rendered = template.format(**{key: shlex.quote(value) for key, value in values.items()})
    return ["sh", "-lc", rendered]


def start_xvfb(*, screen_size: str = DEFAULT_SCREEN_SIZE) -> XvfbSession:
    process = subprocess.Popen(
        ["Xvfb", "-displayfd", "1", "-screen", "0", screen_size, "-ac"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.stdout is None:
        raise RuntimeError("Xvfb did not expose stdout for display discovery")
    display_number = process.stdout.readline().strip()
    if not display_number:
        stderr_output = ""
        if process.stderr is not None:
            stderr_output = process.stderr.read().strip()
        process.terminate()
        raise RuntimeError(f"failed to start Xvfb: {stderr_output or 'no display assigned'}")

    display = f":{display_number}"
    env = os.environ.copy()
    env["DISPLAY"] = display
    for _ in range(20):
        probe = subprocess.run(
            ["xdpyinfo"],
            env=env,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return XvfbSession(display=display, process=process, env=env)
        time.sleep(0.25)
    stop_xvfb(XvfbSession(display=display, process=process, env=env))
    raise RuntimeError(f"Xvfb display did not become ready: {display}")


def stop_xvfb(session: XvfbSession | None) -> None:
    if session is None:
        return
    if session.process.poll() is not None:
        return
    session.process.terminate()
    try:
        session.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        session.process.kill()
        session.process.wait(timeout=5)


def launch_application(
    *,
    command: tuple[str, ...],
    env: dict[str, str],
    stdout_log: Path,
    stderr_log: Path,
    dbus_session: bool,
) -> subprocess.Popen[str]:
    effective_command = list(command)
    if dbus_session and _command_exists("dbus-run-session"):
        effective_command = ["dbus-run-session", "--", *effective_command]
    stdout_handle = stdout_log.open("w", encoding="utf-8")
    stderr_handle = stderr_log.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            effective_command,
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()
    return process


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def wait_for_window(
    *,
    env: dict[str, str],
    timeout: float,
    pid: int | None,
    window_name: str,
    window_class: str,
) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        window_ids = search_window_ids(
            env=env,
            pid=pid,
            window_name=window_name,
            window_class=window_class,
        )
        if window_ids:
            return window_ids[0]
        time.sleep(0.5)
    raise RuntimeError(
        f"timed out waiting for window: name={window_name!r} class={window_class!r} pid={pid!r}"
    )


def search_window_ids(
    *,
    env: dict[str, str],
    pid: int | None,
    window_name: str,
    window_class: str,
) -> list[str]:
    candidates: list[str] = []
    if window_class.strip():
        candidates.extend(_xdotool_search(env, ["search", "--onlyvisible", "--class", window_class.strip()]))
    if window_name.strip():
        candidates.extend(_xdotool_search(env, ["search", "--onlyvisible", "--name", window_name.strip()]))
    if pid is not None:
        candidates.extend(_xdotool_search(env, ["search", "--onlyvisible", "--pid", str(pid)]))

    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return ordered


def prepare_window(*, env: dict[str, str], window_id: str, screen_size: str) -> None:
    width, height, _depth = parse_screen_size(screen_size)
    subprocess.run(["xdotool", "windowmove", window_id, "0", "0"], env=env, check=False)
    subprocess.run(["xdotool", "windowsize", window_id, str(width), str(height)], env=env, check=False)
    subprocess.run(["xdotool", "windowfocus", window_id], env=env, check=False)


def run_capture_steps(
    *,
    steps: tuple[CaptureStep, ...],
    env: dict[str, str],
    window_id: str,
    window_name: str,
    window_class: str,
    asset_dir: Path,
    capture_tool: str,
    scale_filter: str,
    screen_size: str,
    settle_time: float,
    start_index: int = 0,
    max_screenshots: int | None = None,
    ocr_backend: str = "auto",
    ocr_python: str = "",
    ocr_min_score: float = 0.35,
    trace: CaptureExecutionTrace | None = None,
) -> tuple[list[CaptureArtifact], str]:
    raw_dir = asset_dir / "raw"
    screenshot_dir = asset_dir / "screenshots"
    raw_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[CaptureArtifact] = []
    screenshot_index = start_index
    current_window_id = window_id
    for step in steps:
        if step.action == "wait-window":
            if not window_name.strip() and not window_class.strip():
                time.sleep(step.seconds)
                continue
            current_window_id = wait_for_window(
                env=env,
                timeout=step.seconds,
                pid=None,
                window_name=window_name,
                window_class=window_class,
            )
            prepare_window(env=env, window_id=current_window_id, screen_size=screen_size)
            time.sleep(settle_time)
            continue
        if step.action == "activate":
            subprocess.run(["xdotool", "windowfocus", current_window_id], env=env, check=False)
            time.sleep(settle_time)
            continue
        if step.action == "sleep":
            time.sleep(step.seconds)
            continue
        if step.action == "key":
            subprocess.run(["xdotool", "key", "--window", current_window_id, step.value], env=env, check=True)
            time.sleep(settle_time)
            continue
        if step.action == "type":
            subprocess.run(
                ["xdotool", "type", "--window", current_window_id, "--delay", "20", step.value],
                env=env,
                check=True,
            )
            time.sleep(settle_time)
            continue
        if step.action == "click":
            subprocess.run(
                ["xdotool", "mousemove", "--window", current_window_id, str(step.x), str(step.y), "click", "1"],
                env=env,
                check=True,
            )
            time.sleep(settle_time)
            continue
        if step.action == "click-text":
            click_x, click_y = _resolve_click_text_target(
                env=env,
                window_id=current_window_id,
                capture_tool=capture_tool,
                screen_size=screen_size,
                target_text=step.value,
                ocr_backend=ocr_backend,
                ocr_python=ocr_python,
                ocr_min_score=ocr_min_score,
                trace=trace,
            )
            subprocess.run(
                ["xdotool", "mousemove", "--window", current_window_id, str(click_x), str(click_y), "click", "1"],
                env=env,
                check=True,
            )
            time.sleep(settle_time)
            continue
        if step.action == "screenshot":
            if max_screenshots is not None and screenshot_index >= max_screenshots:
                break
            _wait_for_useful_window_content(
                env=env,
                window_id=current_window_id,
                capture_tool=capture_tool,
                screen_size=screen_size,
                ocr_backend=ocr_backend,
                ocr_python=ocr_python,
                timeout=max(6.0, min(20.0, settle_time * 4.0)),
                trace=trace,
            )
            screenshot_index += 1
            label = sanitize_label(step.value or f"screen-{screenshot_index:02d}")
            raw_path = raw_dir / f"raw-{screenshot_index:02d}.png"
            image_path = screenshot_dir / f"screen-{screenshot_index:02d}.png"
            capture_display(
                env=env,
                output_path=raw_path,
                capture_tool=capture_tool,
                screen_size=screen_size,
            )
            geometry = get_window_geometry(env=env, window_id=current_window_id)
            normalize_capture(
                raw_path=raw_path,
                output_path=image_path,
                geometry=geometry,
                scale_filter=scale_filter,
            )
            artifacts.append(CaptureArtifact(label=label, raw_path=raw_path, image_path=image_path))
            continue
        raise ValueError(f"unsupported capture step action: {step.action}")
    return artifacts, current_window_id


def _wait_for_useful_window_content(
    *,
    env: dict[str, str],
    window_id: str,
    capture_tool: str,
    screen_size: str,
    ocr_backend: str,
    ocr_python: str,
    timeout: float,
    trace: CaptureExecutionTrace | None,
) -> None:
    normalized_backend = ocr_backend.strip().lower() or "auto"
    if normalized_backend == "off":
        return
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        try:
            with tempfile.TemporaryDirectory(prefix="appstore-ready-") as temp_dir:
                window_image = _capture_window_for_ocr(
                    env=env,
                    window_id=window_id,
                    capture_tool=capture_tool,
                    screen_size=screen_size,
                    temp_dir=Path(temp_dir),
                )
                reasons = semantic_rejection_reasons(
                    window_image,
                    ocr_backend=ocr_backend,
                    ocr_python=ocr_python or default_ocr_python(),
                )
                if trace is not None:
                    trace.note_ocr(ocr_backend)
        except Exception:
            return
        loading_reasons = [reason for reason in reasons if "loading screen" in reason or "splash screen" in reason]
        if not loading_reasons:
            return
        time.sleep(1.5)


def _current_window_ocr_context(
    *,
    env: dict[str, str],
    window_id: str,
    capture_tool: str,
    screen_size: str,
    tried_click_targets: tuple[str, ...] | list[str],
    ocr_backend: str,
    ocr_python: str,
    trace: CaptureExecutionTrace | None = None,
) -> tuple[tuple[str, ...], tuple[SceneElement, ...]]:
    normalized_backend = ocr_backend.strip().lower() or "auto"
    if normalized_backend == "off":
        return (), ()
    try:
        with tempfile.TemporaryDirectory(prefix="appstore-ocr-context-") as temp_dir:
            window_image = _capture_window_for_ocr(
                env=env,
                window_id=window_id,
                capture_tool=capture_tool,
                screen_size=screen_size,
                temp_dir=Path(temp_dir),
            )
            if trace is not None:
                trace.note_ocr(ocr_backend)
            lines = ocr_image(window_image, python_executable=ocr_python or default_ocr_python())
    except Exception:
        return (), ()
    visible_texts = tuple(line.text.strip() for line in lines if line.text.strip())
    scene_elements = _scene_elements_from_lines(lines, tried_click_targets=tried_click_targets)
    return visible_texts, scene_elements


def _scene_elements_from_lines(
    lines: tuple[OCRLine, ...] | list[OCRLine],
    *,
    tried_click_targets: tuple[str, ...] | list[str],
) -> tuple[SceneElement, ...]:
    tried = {_normalize_click_text(text) for text in tried_click_targets if _normalize_click_text(text)}
    seen: set[tuple[str, int, int]] = set()
    elements: list[SceneElement] = []
    ordered_lines = sorted(
        list(lines),
        key=lambda line: (_ocr_line_bounds(line)[1], _ocr_line_bounds(line)[0], str(line.text).strip()),
    )
    for index, line in enumerate(ordered_lines, start=1):
        text = str(line.text).strip()
        normalized = _normalize_click_text(text)
        if not normalized:
            continue
        left, top, right, bottom = _ocr_line_bounds(line)
        if (normalized, left, top) in seen:
            continue
        seen.add((normalized, left, top))
        element_id = f"node-{index:02d}"
        if element_id in tried or normalized in tried:
            continue
        center_x, center_y = line.center
        elements.append(
            SceneElement(
                element_id=element_id,
                text=text,
                center_x=center_x,
                center_y=center_y,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                score=float(line.score),
            )
        )
        if len(elements) >= 40:
            break
    return tuple(elements)


def _ocr_line_bounds(line: OCRLine) -> tuple[int, int, int, int]:
    if not line.box:
        center_x, center_y = line.center
        return center_x, center_y, center_x, center_y
    xs = [int(round(point[0])) for point in line.box]
    ys = [int(round(point[1])) for point in line.box]
    return min(xs), min(ys), max(xs), max(ys)


def _format_scene_element(element: SceneElement) -> str:
    return (
        f"{element.element_id} "
        f'text="{element.text}" '
        f"center=({element.center_x},{element.center_y}) "
        f"box=({element.left},{element.top},{element.right},{element.bottom}) "
        f"score={element.score:.2f}"
    )


def _normalize_click_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value).strip().lower())


def capture_display(
    *,
    env: dict[str, str],
    output_path: Path,
    capture_tool: str,
    screen_size: str,
) -> None:
    if capture_tool == "scrot":
        subprocess.run(["scrot", str(output_path)], env=env, check=True)
        return
    if capture_tool == "ffmpeg":
        width, height, _depth = parse_screen_size(screen_size)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "x11grab",
                "-video_size",
                f"{width}x{height}",
                "-i",
                env["DISPLAY"],
                "-frames:v",
                "1",
                str(output_path),
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        return
    raise ValueError(f"unsupported capture tool: {capture_tool}")


def _resolve_click_text_target(
    *,
    env: dict[str, str],
    window_id: str,
    capture_tool: str,
    screen_size: str,
    target_text: str,
    ocr_backend: str,
    ocr_python: str,
    ocr_min_score: float,
    trace: CaptureExecutionTrace | None = None,
) -> tuple[int, int]:
    normalized_backend = ocr_backend.strip().lower() or "auto"
    if normalized_backend == "off":
        raise OCRMatchError("click-text requires OCR, but OCR backend is disabled")
    if normalized_backend not in {"auto", "rapidocr"}:
        raise OCRMatchError(f"unsupported OCR backend: {ocr_backend}")

    with tempfile.TemporaryDirectory(prefix="appstore-ocr-") as temp_dir:
        window_image = _capture_window_for_ocr(
            env=env,
            window_id=window_id,
            capture_tool=capture_tool,
            screen_size=screen_size,
            temp_dir=Path(temp_dir),
        )
        if trace is not None:
            trace.note_ocr(ocr_backend)
        line = find_text_match(
            window_image,
            target_text=target_text,
            python_executable=ocr_python or default_ocr_python(),
            min_score=ocr_min_score,
        )
    return line.center


def _capture_window_for_ocr(
    *,
    env: dict[str, str],
    window_id: str,
    capture_tool: str,
    screen_size: str,
    temp_dir: Path,
) -> Path:
    geometry = get_window_geometry(env=env, window_id=window_id)
    if geometry is None:
        raise OCRMatchError(f"unable to determine window geometry for OCR: {window_id}")

    raw_path = temp_dir / "ocr-raw.png"
    crop_path = temp_dir / "ocr-window.png"
    capture_display(
        env=env,
        output_path=raw_path,
        capture_tool=capture_tool,
        screen_size=screen_size,
    )
    normalize_capture(
        raw_path=raw_path,
        output_path=crop_path,
        geometry=geometry,
        scale_filter="",
    )
    return crop_path


def normalize_capture(
    *,
    raw_path: Path,
    output_path: Path,
    geometry: dict[str, int] | None,
    scale_filter: str,
) -> None:
    filters: list[str] = []
    if geometry:
        filters.append(
            "crop={width}:{height}:{x}:{y}".format(
                width=geometry["WIDTH"],
                height=geometry["HEIGHT"],
                x=geometry["X"],
                y=geometry["Y"],
            )
        )
    if scale_filter.strip():
        filters.append(f"scale={scale_filter.strip()}")
    command = ["ffmpeg", "-y", "-i", str(raw_path)]
    if filters:
        command.extend(["-vf", ",".join(filters)])
    command.append(str(output_path))
    subprocess.run(command, check=True, capture_output=True, text=True)


def get_window_geometry(*, env: dict[str, str], window_id: str) -> dict[str, int] | None:
    completed = subprocess.run(
        ["xdotool", "getwindowgeometry", "--shell", window_id],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    geometry: dict[str, int] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if key in {"X", "Y", "WIDTH", "HEIGHT"}:
            try:
                geometry[key] = int(value)
            except ValueError:
                return None
    if {"X", "Y", "WIDTH", "HEIGHT"} - geometry.keys():
        return None
    return geometry


def parse_screen_size(screen_size: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)x(\d+)", screen_size.strip())
    if not match:
        raise ValueError(f"invalid screen size: {screen_size}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _normalize_capture_limits(*, min_screenshots: int, max_screenshots: int) -> tuple[int, int]:
    normalized_min = max(1, int(min_screenshots))
    normalized_max = max(normalized_min, int(max_screenshots))
    return normalized_min, normalized_max


def _summarize_rejected_reasons(report: ScreenshotValidationReport | None) -> tuple[str, ...]:
    if report is None:
        return ()
    reasons: list[str] = []
    for item in report.items:
        if item.accepted:
            continue
        if item.reasons:
            reasons.append(f"{item.analysis.path.name}: {', '.join(item.reasons)}")
        else:
            reasons.append(f"{item.analysis.path.name}: rejected")
    return tuple(reasons)


def _make_semantic_validator(
    *,
    ocr_backend: str,
    ocr_python: str,
    package_name: str,
    app_name: str,
    ai_base_url: str,
    ai_model: str,
    ai_api_key: str,
    trace: CaptureExecutionTrace | None = None,
):
    accepted_signatures: list[tuple[Path, str]] = []

    def _validator(path: Path) -> tuple[str, ...]:
        reasons = list(
            semantic_rejection_reasons(
                path,
                ocr_backend=ocr_backend,
                ocr_python=ocr_python or default_ocr_python(),
            )
        )
        if trace is not None:
            trace.note_ocr(ocr_backend)
        signature = semantic_text_signature(
            path,
            ocr_backend=ocr_backend,
            ocr_python=ocr_python or default_ocr_python(),
        )
        if trace is not None:
            trace.note_ocr(ocr_backend)
        if signature:
            ai_reasons = _ai_capture_review_reasons(
                package_name=package_name,
                app_name=app_name,
                current_signature=signature,
                accepted_signatures=tuple(previous_signature for _, previous_signature in accepted_signatures),
                ai_base_url=ai_base_url,
                ai_model=ai_model,
                ai_api_key=ai_api_key,
                trace=trace,
            )
            reasons.extend(ai_reasons)
            for previous_path, previous_signature in accepted_signatures:
                similarity = semantic_similarity(signature, previous_signature)
                if similarity >= 0.88:
                    reasons.append(
                        f"ocr text similar to {previous_path.name}: {similarity:.2f}"
                    )
                    break
        if not reasons and signature:
            accepted_signatures.append((path, signature))
        return tuple(reasons)

    return _validator


def _ai_capture_review_reasons(
    *,
    package_name: str,
    app_name: str,
    current_signature: str,
    accepted_signatures: tuple[str, ...],
    ai_base_url: str,
    ai_model: str,
    ai_api_key: str,
    trace: CaptureExecutionTrace | None = None,
) -> list[str]:
    if not ai_base_url.strip() or not ai_model.strip() or not current_signature.strip():
        return []
    try:
        if trace is not None:
            trace.note_ai_review()
        review = review_capture_text(
            package_name=package_name,
            app_name=app_name,
            current_text=current_signature,
            accepted_texts=accepted_signatures,
            config=AICaptureReviewConfig(
                base_url=ai_base_url,
                model=ai_model,
                api_key=ai_api_key,
            ),
        )
    except Exception:
        return []
    if review.useful:
        return []
    return [f"ai review rejected screenshot: {review.reason}"]


def _steps_for_scene_elements(
    *,
    elements: tuple[SceneElement, ...] | list[SceneElement],
    accepted_count: int,
) -> tuple[CaptureStep, ...]:
    steps: list[CaptureStep] = []
    for index, element in enumerate(elements, start=1):
        page_number = accepted_count + index
        steps.extend(
            [
                CaptureStep(
                    action="click",
                    value=str(element.element_id),
                    x=int(element.center_x),
                    y=int(element.center_y),
                ),
                CaptureStep(action="sleep", seconds=2.0),
                CaptureStep(action="screenshot", value=f"screen-{page_number:02d}"),
                CaptureStep(action="key", value="Alt+Left"),
                CaptureStep(action="sleep", seconds=1.5),
                CaptureStep(action="key", value="Escape"),
                CaptureStep(action="sleep", seconds=1.0),
            ]
        )
    return tuple(steps)


def _fallback_retry_capture_steps(
    *,
    target_screenshots: int,
    accepted_count: int,
    scene_elements: tuple[SceneElement, ...] | list[SceneElement] = (),
    accepted_texts: tuple[str, ...] | list[str] = (),
) -> tuple[CaptureStep, ...]:
    remaining = max(1, target_screenshots - max(0, accepted_count))
    safe_elements = [
        element
        for element in scene_elements
        if not any(semantic_similarity(element.text, accepted_text) >= 0.92 for accepted_text in accepted_texts)
        and not _is_unsafe_scene_text(element.text)
    ]
    if safe_elements:
        return _steps_for_scene_elements(
            elements=tuple(safe_elements[:remaining]),
            accepted_count=accepted_count,
        )

    steps: list[CaptureStep] = []
    for index in range(remaining):
        page_number = accepted_count + index + 1
        steps.extend(
            [
                CaptureStep(action="key", value="Tab"),
                CaptureStep(action="sleep", seconds=1.0),
                CaptureStep(action="key", value="Return"),
                CaptureStep(action="sleep", seconds=2.0),
                CaptureStep(action="screenshot", value=f"screen-{page_number:02d}"),
            ]
        )
    return tuple(steps)


def _is_unsafe_scene_text(text: str) -> bool:
    normalized = _normalize_click_text(text)
    if not normalized:
        return True
    if re.fullmatch(r"[0-9.:%/-]+", str(text).strip()):
        return True
    danger_keywords = (
        "安装",
        "卸载",
        "删除",
        "移除",
        "付款",
        "支付",
        "购买",
        "下载",
        "提交",
        "确认",
        "apply",
        "install",
        "uninstall",
        "delete",
        "remove",
        "buy",
        "purchase",
        "submit",
        "confirm",
        "download",
    )
    return any(keyword in normalized for keyword in danger_keywords)


def sanitize_label(label: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", label.strip().lower()).strip("-")
    return normalized or "shot"


def _inspect_package_path(package_path: Path) -> PackageInfo:
    family, package_format = _infer_package_kind_from_path(package_path)
    return read_package_info(family, package_format, package_path)


def _infer_package_kind_from_path(file_path: Path) -> tuple[str, str]:
    suffix = file_path.suffix.lower()
    if suffix == ".deb":
        return "deb", "deb"
    if suffix == ".uab":
        return "linglong", "uab"
    if suffix == ".layer":
        return "linglong", "layer"
    raise ValueError(f"unsupported package format for file: {file_path.name}")


def _command_exists(command_name: str) -> bool:
    probe = subprocess.run(
        ["sh", "-lc", f"command -v {shlex.quote(command_name)} >/dev/null 2>&1"],
        check=False,
    )
    return probe.returncode == 0


def _privileged_command_env(
    *,
    sudo_password: str,
    base_env: dict[str, str] | None = None,
) -> dict[str, str] | None:
    if not sudo_password:
        return base_env
    env = dict(os.environ) if base_env is None else dict(base_env)
    env[APPSTORE_SUDO_PASSWORD_ENV] = sudo_password
    return env


def _xdotool_search(env: dict[str, str], args: list[str]) -> list[str]:
    completed = subprocess.run(
        ["xdotool", *args],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _run_logged_command(
    command: list[str],
    *,
    log_path: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        completed = subprocess.run(
            command,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if check and completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}")


def _write_package_metadata(
    *,
    result: CapturePackageResult,
    asset_dir: Path,
    validation_report: ScreenshotValidationReport | None = None,
) -> None:
    payload = {
        "row_id": result.row_id,
        "package_path": str(result.package_path),
        "pkg_name": result.pkg_name,
        "pkg_version": result.pkg_version,
        "pkg_arch": result.pkg_arch,
        "status": result.status,
        "message": result.message,
        "asset_dir": str(result.asset_dir),
        "desktop_file": result.desktop_file,
        "launch_command": list(result.launch_command),
        "screenshots": [str(path) for path in result.screenshots],
        "rejected_screenshots": [str(path) for path in result.rejected_screenshots],
        "execution_trace": result.execution_trace,
        "screenshot_validation": None if validation_report is None else validation_report_payload(validation_report),
    }
    (asset_dir / "metadata.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_screenshot_validation_report(*, asset_dir: Path, report: ScreenshotValidationReport) -> None:
    (asset_dir / "screenshot-validation.json").write_text(
        validation_report_json(report),
        encoding="utf-8",
    )


def _capture_result_message(*, captured_count: int, accepted_count: int, rejected_count: int) -> str:
    if captured_count == accepted_count and rejected_count == 0:
        return f"captured {accepted_count} screenshot(s)"
    return (
        f"captured {captured_count} screenshot(s), "
        f"accepted {accepted_count}, rejected {rejected_count}"
    )


def _write_capture_reports(*, output_dir: Path, results: list[CapturePackageResult]) -> None:
    report_rows = [
        {
            "row_id": result.row_id,
            "package_path": str(result.package_path),
            "pkg_name": result.pkg_name,
            "pkg_version": result.pkg_version,
            "pkg_arch": result.pkg_arch,
            "status": result.status,
            "message": result.message,
            "asset_dir": str(result.asset_dir),
            "desktop_file": result.desktop_file,
            "launch_command": " ".join(result.launch_command),
            "screenshot_count": len(result.screenshots),
            "capture_stage": str(result.execution_trace.get("capture_stage", "")),
            "ocr_calls": int(result.execution_trace.get("ocr_calls", 0)),
            "ai_planning_calls": int(result.execution_trace.get("ai_planning_calls", 0)),
            "ai_click_selection_calls": int(result.execution_trace.get("ai_click_selection_calls", 0)),
            "ai_review_calls": int(result.execution_trace.get("ai_review_calls", 0)),
        }
        for result in results
    ]
    (output_dir / "capture-report.json").write_text(
        json.dumps(report_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "capture-report"
    sheet.append(
        [
            "row_id",
            "package_path",
            "pkg_name",
            "pkg_version",
            "pkg_arch",
            "status",
            "message",
            "asset_dir",
            "desktop_file",
            "launch_command",
            "screenshot_count",
            "capture_stage",
            "ocr_calls",
            "ai_planning_calls",
            "ai_click_selection_calls",
            "ai_review_calls",
        ]
    )
    for row in report_rows:
        sheet.append(
            [
                row["row_id"],
                row["package_path"],
                row["pkg_name"],
                row["pkg_version"],
                row["pkg_arch"],
                row["status"],
                row["message"],
                row["asset_dir"],
                row["desktop_file"],
                row["launch_command"],
                row["screenshot_count"],
                row["capture_stage"],
                row["ocr_calls"],
                row["ai_planning_calls"],
                row["ai_click_selection_calls"],
                row["ai_review_calls"],
            ]
        )
    workbook.save(output_dir / "capture-report.xlsx")


def _resolved_ocr_backend(ocr_backend: str) -> str:
    normalized_backend = ocr_backend.strip().lower() or "auto"
    if normalized_backend == "off":
        return ""
    if normalized_backend in {"auto", "rapidocr"}:
        return "rapidocr"
    return normalized_backend
