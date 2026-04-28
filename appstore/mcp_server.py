from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from appstore.appstore_client import AppStoreClient
from appstore.capabilities import load_capability_cache, sync_capabilities_to_cache
from appstore.examples.generate_template import generate_template
from appstore.inspectors import read_package_info
from appstore.new_app_workbook import prepare_new_app_workbook
from appstore.upload_batch import main as upload_batch_main

LOGGER = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = REPO_ROOT / "appstore" / "cache" / "capabilities"
DEFAULT_SESSION_CACHE_DIR = REPO_ROOT / "appstore" / "cache" / "session-state"
DEFAULT_OUTPUT_ROOT = Path(os.environ.get("APPSTORE_MCP_OUTPUT_ROOT", "")) if os.environ.get("APPSTORE_MCP_OUTPUT_ROOT", "").strip() else Path(tempfile.gettempdir()) / "appstore-mcp-output"
DEFAULT_JOB_ROOT = Path(os.environ.get("APPSTORE_MCP_JOB_ROOT", "")) if os.environ.get("APPSTORE_MCP_JOB_ROOT", "").strip() else Path(tempfile.gettempdir()) / "appstore-mcp-jobs"
DEFAULT_AI_BASE_URL = os.environ.get("APPSTORE_AI_BASE_URL", "http://127.0.0.1:8787/v1")
DEFAULT_AI_MODEL = os.environ.get("APPSTORE_AI_MODEL", "openai-codex/gpt-5.4")
DEFAULT_SUDO_PASSWORD = os.environ.get("APPSTORE_SUDO_PASSWORD", "")
_SERVER_PROTOCOL_VERSION = "2025-03-26"


class MCPError(RuntimeError):
    def __init__(self, message: str, *, code: int = -32000) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]


def _configure_logging() -> None:
    level_name = os.environ.get("APPSTORE_MCP_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _result_payload(payload: dict[str, Any], *, summary: str) -> dict[str, Any]:
    text = _result_text(payload, summary=summary)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": False,
    }


def _result_text(payload: dict[str, Any], *, summary: str) -> str:
    lines = [summary]
    for line in _payload_brief_lines(payload):
        if line not in lines:
            lines.append(line)
    return "\n".join(lines)


def _payload_brief_lines(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    package_count = payload.get("package_count")
    if isinstance(package_count, int):
        lines.append(f"package_count: {package_count}")

    for key in ("pkg_name", "app_name_zh", "output_path", "cache_dir", "latest_path", "report_path", "capture_report_path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{key}: {value}")

    exit_code = payload.get("exit_code")
    if isinstance(exit_code, int):
        lines.append(f"exit_code: {exit_code}")

    status_counts = payload.get("status_counts")
    if isinstance(status_counts, dict) and status_counts:
        compact = ", ".join(f"{key}={value}" for key, value in sorted(status_counts.items()))
        lines.append(f"status_counts: {compact}")

    if isinstance(payload.get("ready_for_upload"), bool):
        lines.append(f"ready_for_upload: {payload['ready_for_upload']}")

    for key in ("missing_fields", "placeholder_fields", "selected_system_line_codes"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            preview = ", ".join(str(item) for item in value[:6])
            if len(value) > 6:
                preview += ", ..."
            lines.append(f"{key}: {preview}")

    workflow_hint = payload.get("workflow_hint")
    if isinstance(workflow_hint, dict) and workflow_hint:
        tool_name = str(workflow_hint.get("recommended_tool", "")).strip()
        reason = str(workflow_hint.get("reason", "")).strip()
        if tool_name:
            lines.append(f"workflow_hint: use {tool_name}")
        if reason:
            lines.append(f"workflow_reason: {reason}")

    packages = payload.get("packages")
    if isinstance(packages, list) and packages and isinstance(packages[0], dict):
        compact_packages: list[str] = []
        for item in packages[:4]:
            name = str(item.get("pkg_name", "")).strip()
            version = str(item.get("pkg_version", "")).strip()
            arch = str(item.get("pkg_arch", "")).strip()
            path = str(item.get("path", "")).strip()
            if name and version and arch:
                compact_packages.append(f"{name}@{version}[{arch}]")
            elif path:
                compact_packages.append(Path(path).name)
        if compact_packages:
            suffix = ", ..." if len(packages) > 4 else ""
            lines.append(f"packages: {', '.join(compact_packages)}{suffix}")

    report = payload.get("report")
    if isinstance(report, list) and report:
        report_lines = _brief_report_lines(report)
        lines.extend(report_lines)

    capture_details = payload.get("capture_details")
    if isinstance(capture_details, list) and capture_details:
        lines.extend(_brief_capture_detail_lines(capture_details))

    next_actions = payload.get("next_actions")
    if isinstance(next_actions, list) and next_actions:
        action = str(next_actions[0]).strip()
        if action:
            lines.append(f"next_action: {action}")

    return lines[:16]


def _brief_report_lines(report_rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    compact_rows: list[str] = []
    for row in report_rows[:4]:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status", "")).strip() or "unknown"
        pkg_name = str(row.get("pkg_name", "")).strip()
        pkg_arch = str(row.get("pkg_arch", "")).strip()
        message = str(row.get("message", "")).strip()
        head = _package_head(pkg_name=pkg_name, pkg_arch=pkg_arch)
        if head and message:
            compact_rows.append(f"{head}: {status} ({message})")
        elif head:
            compact_rows.append(f"{head}: {status}")
        elif message:
            compact_rows.append(f"{status}: {message}")
        else:
            compact_rows.append(status)
    if compact_rows:
        lines.append("report: " + " | ".join(compact_rows))
    return lines


def _brief_capture_detail_lines(details: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    compact: list[str] = []
    for item in details[:4]:
        if not isinstance(item, dict):
            continue
        pkg_name = str(item.get("pkg_name", "")).strip()
        pkg_arch = str(item.get("pkg_arch", "")).strip()
        status = str(item.get("status", "")).strip() or "unknown"
        screenshots = item.get("screenshots")
        rejected = item.get("rejected_screenshots")
        accepted_count = len(screenshots) if isinstance(screenshots, list) else 0
        rejected_count = len(rejected) if isinstance(rejected, list) else 0
        trace = item.get("execution_trace") if isinstance(item.get("execution_trace"), dict) else {}
        trace_parts: list[str] = []
        capture_stage = str(trace.get("capture_stage", "")).strip()
        if capture_stage:
            trace_parts.append(f"stage={capture_stage}")
        effective_ocr_backend = str(trace.get("effective_ocr_backend", "")).strip()
        ocr_calls = trace.get("ocr_calls")
        if effective_ocr_backend or isinstance(ocr_calls, int):
            trace_parts.append(f"ocr={effective_ocr_backend or 'off'}/{int(ocr_calls or 0)}")
        for key, label in (
            ("ai_planning_calls", "ai_plan"),
            ("ai_click_selection_calls", "ai_click"),
            ("ai_review_calls", "ai_review"),
        ):
            value = trace.get(key)
            if isinstance(value, int):
                trace_parts.append(f"{label}={value}")
        head = _package_head(pkg_name=pkg_name, pkg_arch=pkg_arch)
        compact_line = f"{head}: {status}, screenshots={accepted_count}, rejected={rejected_count}".strip()
        if trace_parts:
            compact_line += ", " + ", ".join(trace_parts)
        compact.append(compact_line)
    if compact:
        lines.append("capture: " + " | ".join(compact))
    return lines


def _package_head(*, pkg_name: str, pkg_arch: str) -> str:
    if pkg_name and pkg_arch:
        return f"{pkg_name}[{pkg_arch}]"
    return pkg_name or pkg_arch


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_string(value: Any, *, field_name: str, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    raise MCPError(f"{field_name} must be a string")


def _ensure_optional_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError as exc:
            raise MCPError(f"{field_name} must be a number") from exc
    raise MCPError(f"{field_name} must be a number")


def _ensure_optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise MCPError(f"{field_name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError as exc:
            raise MCPError(f"{field_name} must be an integer") from exc
    raise MCPError(f"{field_name} must be an integer")


def _ensure_optional_bool(value: Any, *, field_name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise MCPError(f"{field_name} must be a boolean")


def _ensure_string_list(value: Any, *, field_name: str, required: bool = False) -> list[str]:
    if value is None:
        if required:
            raise MCPError(f"{field_name} is required")
        return []
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            if required:
                raise MCPError(f"{field_name} is required")
            return []
        return [normalized]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise MCPError(f"{field_name} must contain only non-empty strings")
            result.append(item.strip())
        if required and not result:
            raise MCPError(f"{field_name} is required")
        return result
    raise MCPError(f"{field_name} must be a string or string array")


def _reject_unknown_fields(params: dict[str, Any], *, allowed_fields: set[str], tool_name: str) -> None:
    unknown_fields = sorted(set(params.keys()) - allowed_fields)
    if unknown_fields:
        raise MCPError(
            f"{tool_name} received unsupported field(s): {', '.join(unknown_fields)}"
        )


def _timestamp_label() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _default_output_dir(prefix: str) -> Path:
    root = DEFAULT_OUTPUT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{prefix}-{_timestamp_label()}"


def _normalize_output_dir(value: Any, *, prefix: str) -> Path:
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser().resolve()
    return _default_output_dir(prefix)


def _append_option(argv: list[str], option: str, value: Any) -> None:
    flag = f"--{option.replace('_', '-')}"
    if value is None:
        return
    if isinstance(value, bool):
        argv.append(flag if value else f"--no-{option.replace('_', '-')}")
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            if item is None:
                continue
            argv.extend([flag, str(item)])
        return
    text = str(value)
    if text == "":
        return
    argv.extend([flag, text])


def _report_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "unknown")).strip() or "unknown"
        summary[status] = summary.get(status, 0) + 1
    return summary


def _capture_details_from_report(report_rows: Any) -> list[dict[str, Any]]:
    if not isinstance(report_rows, list):
        return []
    details: list[dict[str, Any]] = []
    for row in report_rows:
        if not isinstance(row, dict):
            continue
        asset_dir = row.get("asset_dir")
        if not isinstance(asset_dir, str) or not asset_dir.strip():
            continue
        metadata_path = Path(asset_dir).expanduser().resolve() / "metadata.json"
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                metadata = {
                    "asset_dir": str(metadata_path.parent),
                    "metadata_path": str(metadata_path),
                    "status": "invalid_metadata_json",
                }
        else:
            metadata = {
                "asset_dir": str(Path(asset_dir).expanduser().resolve()),
                "metadata_path": "",
                "status": "metadata_missing",
                "screenshots": [],
                "rejected_screenshots": [],
            }
        details.append(metadata)
    return details


def _job_root() -> Path:
    DEFAULT_JOB_ROOT.mkdir(parents=True, exist_ok=True)
    return DEFAULT_JOB_ROOT


def _job_dir(job_id: str) -> Path:
    return _job_root() / job_id


def _job_state_path(job_id: str) -> Path:
    return _job_dir(job_id) / "state.json"


def _job_result_path(job_id: str) -> Path:
    return _job_dir(job_id) / "result.json"


def _job_request_path(job_id: str) -> Path:
    return _job_dir(job_id) / "request.json"


def _job_stdout_path(job_id: str) -> Path:
    return _job_dir(job_id) / "worker.stdout.log"


def _job_stderr_path(job_id: str) -> Path:
    return _job_dir(job_id) / "worker.stderr.log"


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _new_job_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _job_summary_payload(job_id: str, state: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_id": job_id,
        "job_type": state.get("job_type", ""),
        "status": state.get("status", "unknown"),
        "message": state.get("message", ""),
        "created_at": state.get("created_at", ""),
        "started_at": state.get("started_at", ""),
        "finished_at": state.get("finished_at", ""),
        "job_dir": str(_job_dir(job_id)),
        "stdout_path": str(_job_stdout_path(job_id)),
        "stderr_path": str(_job_stderr_path(job_id)),
        "result_path": str(_job_result_path(job_id)) if _job_result_path(job_id).exists() else "",
    }
    if result is not None:
        payload["result"] = result
        report = result.get("report")
        if report is not None:
            payload["report"] = report
        capture_details = result.get("capture_details")
        if capture_details is not None:
            payload["capture_details"] = capture_details
        status_counts = result.get("status_counts")
        if status_counts is not None:
            payload["status_counts"] = status_counts
        for key in ("report_path", "capture_report_path", "workflow_hint"):
            value = result.get(key)
            if value:
                payload[key] = value
    return payload


def _load_job_payload(job_id: str) -> dict[str, Any]:
    state_path = _job_state_path(job_id)
    if not state_path.exists():
        raise MCPError(f"job not found: {job_id}", code=-32001)
    state = _read_json_file(state_path)
    result = None
    result_path = _job_result_path(job_id)
    if result_path.exists():
        result = _read_json_file(result_path)
    return _job_summary_payload(job_id, state, result)


def _spawn_async_job(*, job_type: str, spec: dict[str, Any]) -> dict[str, Any]:
    job_id = _new_job_id(job_type)
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    request_path = _job_request_path(job_id)
    state_path = _job_state_path(job_id)
    stdout_path = _job_stdout_path(job_id)
    stderr_path = _job_stderr_path(job_id)

    _write_json_file(
        request_path,
        {
            "job_id": job_id,
            "job_type": job_type,
            "created_at": _utc_now_iso(),
            **spec,
        },
    )
    _write_json_file(
        state_path,
        {
            "job_id": job_id,
            "job_type": job_type,
            "status": "queued",
            "message": "job queued",
            "created_at": _utc_now_iso(),
        },
    )

    env = os.environ.copy()
    stdout_handle = stdout_path.open("w", encoding="utf-8")
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "appstore.mcp_job_runner", str(request_path)],
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            env=env,
            start_new_session=True,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()

    queued_state = _read_json_file(state_path)
    queued_state["pid"] = process.pid
    queued_state["message"] = "job started"
    _write_json_file(state_path, queued_state)
    return _job_summary_payload(job_id, queued_state)


def _workflow_hint_from_report(report_rows: Any) -> dict[str, Any]:
    if not isinstance(report_rows, list):
        return {}
    messages = [
        str(row.get("message", "")).strip().lower()
        for row in report_rows
        if isinstance(row, dict)
    ]
    if messages and any("existing app not found" in message for message in messages):
        return {
            "recommended_tool": "prepare_new_app_workbook",
            "reason": "current report indicates the package name does not exist in the store yet, so this is a new-app submission instead of an existing-app update",
        }
    return {}


def _run_upload_batch_command(
    *,
    command: str,
    positional: list[str],
    output_dir: Path,
    options: dict[str, Any],
    report_name: str,
) -> dict[str, Any]:
    argv = [command, *positional, "--output-dir", str(output_dir)]
    for option_name, value in options.items():
        _append_option(argv, option_name, value)

    exit_code = 0
    try:
        exit_code = int(upload_batch_main(argv))
    except SystemExit as exc:
        exit_code = int(exc.code or 0)

    report_path = output_dir / report_name
    report_payload: Any = None
    if report_path.exists():
        report_payload = json.loads(report_path.read_text(encoding="utf-8"))

    result = {
        "command": command,
        "argv": argv,
        "exit_code": exit_code,
        "output_dir": str(output_dir),
        "report_path": str(report_path) if report_path.exists() else "",
        "report": report_payload,
    }
    if isinstance(report_payload, list):
        result["status_counts"] = _report_summary(report_payload)

    if exit_code != 0 and report_payload is None:
        raise MCPError(f"{command} failed with exit code {exit_code}")
    return result


def _default_username(value: Any) -> str:
    candidate = _ensure_string(value, field_name="username", default="")
    return candidate or os.environ.get("APPSTORE_USERNAME", "").strip()


def _default_password(value: Any) -> str:
    candidate = _ensure_string(value, field_name="password", default="")
    return candidate or os.environ.get("APPSTORE_PASSWORD", "")


def _resolve_credentials(params: dict[str, Any]) -> tuple[str, str]:
    username = _default_username(params.get("username"))
    password = _default_password(params.get("password"))
    if not username:
        raise MCPError("username is required or APPSTORE_USERNAME must be set")
    if not password:
        raise MCPError("password is required or APPSTORE_PASSWORD must be set")
    return username, password


def _infer_package_kind(package_path: Path) -> tuple[str, str]:
    suffix = package_path.suffix.lower()
    if suffix == ".deb":
        return "deb", "deb"
    if suffix == ".uab":
        return "linglong", "uab"
    if suffix == ".layer":
        return "linglong", "layer"
    raise MCPError(f"unsupported package format: {package_path.name}")


def _tool_inspect_packages(params: dict[str, Any]) -> dict[str, Any]:
    package_paths = [Path(item).expanduser().resolve() for item in _ensure_string_list(params.get("packages"), field_name="packages", required=True)]
    packages: list[dict[str, Any]] = []
    for package_path in package_paths:
        family, package_format = _infer_package_kind(package_path)
        info = read_package_info(family, package_format, package_path)
        packages.append(
            {
                "path": str(package_path),
                "package_family": info.package_family,
                "package_format": info.package_format,
                "pkg_name": info.pkg_name,
                "pkg_version": info.pkg_version,
                "pkg_arch": info.pkg_arch,
                "pkg_size": info.pkg_size,
                "sha256": info.sha256,
            }
        )
    payload = {"package_count": len(packages), "packages": packages}
    return _result_payload(payload, summary=f"inspect_packages completed: {len(packages)} package(s)")


def _tool_sync_capabilities(params: dict[str, Any]) -> dict[str, Any]:
    username, password = _resolve_credentials(params)
    cache_dir = Path(_ensure_string(params.get("cache_dir"), field_name="cache_dir", default=str(DEFAULT_CACHE_DIR))).expanduser().resolve()
    client = AppStoreClient()
    client.login(username, password)
    latest_path = sync_capabilities_to_cache(client, cache_dir)
    cache = load_capability_cache(cache_dir)
    payload = {
        "cache_dir": str(cache_dir),
        "latest_path": str(latest_path),
        "generated_at": cache.generated_at,
        "deb_system_lines": len(cache.deb_system_lines),
        "linglong_system_lines": len(cache.linglong_system_lines),
        "baseline_groups": len(cache.baseline_options),
    }
    return _result_payload(payload, summary="sync_capabilities completed")


def _tool_generate_template(params: dict[str, Any]) -> dict[str, Any]:
    output_path = Path(_ensure_string(params.get("output_path"), field_name="output_path", default=str(REPO_ROOT / "appstore" / "examples" / "template.xlsx"))).expanduser().resolve()
    capability_cache = _ensure_string(params.get("capabilities_cache"), field_name="capabilities_cache", default=str(DEFAULT_CACHE_DIR))
    generate_template(output_path, capability_cache_path=capability_cache)
    payload = {
        "output_path": str(output_path),
        "capabilities_cache": str(Path(capability_cache).expanduser().resolve()),
        "assets_dir": str(output_path.parent / "assets"),
        "packages_dir": str(output_path.parent / "packages"),
    }
    return _result_payload(payload, summary="generate_template completed")


def _tool_prepare_new_app_workbook(params: dict[str, Any]) -> dict[str, Any]:
    package_paths = [str(Path(item).expanduser().resolve()) for item in _ensure_string_list(params.get("packages"), field_name="packages", required=True)]
    output_path_value = _ensure_string(params.get("output_path"), field_name="output_path", default="")
    if output_path_value:
        output_path = Path(output_path_value).expanduser().resolve()
    else:
        first_package = Path(package_paths[0])
        family, package_format = _infer_package_kind(first_package)
        info = read_package_info(family, package_format, first_package)
        output_path = first_package.parent / f"{info.pkg_name}-submission.xlsx"

    prepared = prepare_new_app_workbook(
        packages=package_paths,
        output_path=output_path,
        capabilities_cache=_ensure_string(params.get("capabilities_cache"), field_name="capabilities_cache", default=str(DEFAULT_CACHE_DIR)),
        app_key=_ensure_string(params.get("app_key"), field_name="app_key", default=""),
        app_name_zh=_ensure_string(params.get("app_name_zh"), field_name="app_name_zh", default=""),
        category_id=_ensure_optional_int(params.get("category_id"), field_name="category_id") or 1,
        website=_ensure_string(params.get("website"), field_name="website", default=""),
        short_desc_zh=_ensure_string(params.get("short_desc_zh"), field_name="short_desc_zh", default=""),
        full_desc_zh=_ensure_string(params.get("full_desc_zh"), field_name="full_desc_zh", default=""),
        icon_path=_ensure_string(params.get("icon_path"), field_name="icon_path", default=""),
        screenshot_paths=_ensure_string_list(params.get("screenshot_paths"), field_name="screenshot_paths"),
        keywords_zh=_ensure_string(params.get("keywords_zh"), field_name="keywords_zh", default=""),
        release_key=_ensure_string(params.get("release_key"), field_name="release_key", default="stable"),
        execution_mode=_ensure_string(params.get("execution_mode"), field_name="execution_mode", default="api"),
        region=_ensure_string(params.get("region"), field_name="region", default="1"),
        note=_ensure_string(params.get("note"), field_name="note", default=""),
        pkg_channel=_ensure_string(params.get("pkg_channel"), field_name="pkg_channel", default="stable"),
        system_line_codes=_ensure_string_list(params.get("system_line_codes"), field_name="system_line_codes"),
    )
    payload = {
        "output_path": str(prepared.output_path),
        "package_family": prepared.package_family,
        "package_format": prepared.package_format,
        "pkg_name": prepared.pkg_name,
        "app_key": prepared.app_key,
        "app_name_zh": prepared.app_name_zh,
        "release_key": prepared.release_key,
        "packages": [str(path) for path in prepared.package_paths],
        "selected_system_line_codes": list(prepared.selected_system_line_codes),
        "missing_fields": list(prepared.missing_fields),
        "placeholder_fields": list(prepared.placeholder_fields),
        "auto_detected_assets": prepared.auto_detected_assets,
        "ready_for_upload": prepared.ready_for_upload,
        "next_actions": [
            "如缺少 icon_path 或 screenshot_*，先准备素材或调用 capture_packages。",
            "如缺少 system_line_codes，请结合 sync_capabilities 返回的能力缓存补全系统线。",
            "完成素材和文案后再调用 upload_workbook。",
        ],
    }
    return _result_payload(payload, summary="prepare_new_app_workbook completed")


def _tool_validate_workbook(params: dict[str, Any]) -> dict[str, Any]:
    workbook = Path(_ensure_string(params.get("workbook"), field_name="workbook")).expanduser().resolve()
    output_dir = _normalize_output_dir(params.get("output_dir"), prefix="validate")
    result = _run_upload_batch_command(
        command="validate",
        positional=[str(workbook)],
        output_dir=output_dir,
        options={
            "rows": _ensure_string(params.get("rows"), field_name="rows", default=""),
            "capabilities_cache": _ensure_string(params.get("capabilities_cache"), field_name="capabilities_cache", default=str(DEFAULT_CACHE_DIR)),
        },
        report_name="report.json",
    )
    return _result_payload(result, summary="validate_workbook completed")


def _tool_upload_workbook(params: dict[str, Any]) -> dict[str, Any]:
    workbook = Path(_ensure_string(params.get("workbook"), field_name="workbook")).expanduser().resolve()
    output_dir = _normalize_output_dir(params.get("output_dir"), prefix="upload")
    dry_run = _ensure_optional_bool(params.get("dry_run"), field_name="dry_run")
    username = ""
    password = ""
    if not dry_run:
        username, password = _resolve_credentials(params)
    result = _run_upload_batch_command(
        command="upload",
        positional=[str(workbook)],
        output_dir=output_dir,
        options={
            "rows": _ensure_string(params.get("rows"), field_name="rows", default=""),
            "dry_run": dry_run,
            "capabilities_cache": _ensure_string(params.get("capabilities_cache"), field_name="capabilities_cache", default=str(DEFAULT_CACHE_DIR)),
            "username": username,
            "password": password,
            "mode": _ensure_string(params.get("mode"), field_name="mode", default="auto"),
            "session_cache_dir": _ensure_string(params.get("session_cache_dir"), field_name="session_cache_dir", default=str(DEFAULT_SESSION_CACHE_DIR)),
            "artifact_dir": _ensure_string(params.get("artifact_dir"), field_name="artifact_dir", default=""),
            "headless": _ensure_optional_bool(params.get("headless"), field_name="headless"),
        },
        report_name="report.json",
    )
    return _result_payload(result, summary="upload_workbook completed")


def _tool_upload_packages(params: dict[str, Any]) -> dict[str, Any]:
    package_paths = [str(Path(item).expanduser().resolve()) for item in _ensure_string_list(params.get("packages"), field_name="packages", required=True)]
    output_dir = _normalize_output_dir(params.get("output_dir"), prefix="upload-packages")
    username, password = _resolve_credentials(params)
    result = _run_upload_batch_command(
        command="upload-packages",
        positional=package_paths,
        output_dir=output_dir,
        options={
            "capabilities_cache": _ensure_string(params.get("capabilities_cache"), field_name="capabilities_cache", default=str(DEFAULT_CACHE_DIR)),
            "username": username,
            "password": password,
            "mode": _ensure_string(params.get("mode"), field_name="mode", default="api"),
            "session_cache_dir": _ensure_string(params.get("session_cache_dir"), field_name="session_cache_dir", default=str(DEFAULT_SESSION_CACHE_DIR)),
            "artifact_dir": _ensure_string(params.get("artifact_dir"), field_name="artifact_dir", default=""),
            "headless": _ensure_optional_bool(params.get("headless"), field_name="headless"),
            "app_id": _ensure_string(params.get("app_id"), field_name="app_id", default=""),
            "note": _ensure_string(params.get("note"), field_name="note", default=""),
            "release_key": _ensure_string(params.get("release_key"), field_name="release_key", default="direct-update"),
            "pkg_channel": _ensure_string(params.get("pkg_channel"), field_name="pkg_channel", default=""),
            "region": _ensure_string(params.get("region"), field_name="region", default=""),
            "screenshot": _ensure_string_list(params.get("screenshot_paths"), field_name="screenshot_paths"),
            "icon": _ensure_string(params.get("icon_path"), field_name="icon_path", default=""),
        },
        report_name="report.json",
    )
    result["workflow_hint"] = _workflow_hint_from_report(result.get("report"))
    return _result_payload(result, summary="upload_packages completed")


def _capture_common_options(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "step": _ensure_string_list(params.get("steps"), field_name="steps"),
        "ai_prompt": _ensure_string(params.get("ai_prompt"), field_name="ai_prompt", default=""),
        "ai_base_url": _ensure_string(params.get("ai_base_url"), field_name="ai_base_url", default=DEFAULT_AI_BASE_URL),
        "ai_model": _ensure_string(params.get("ai_model"), field_name="ai_model", default=DEFAULT_AI_MODEL),
        "ai_api_key": _ensure_string(params.get("ai_api_key"), field_name="ai_api_key", default=os.environ.get("APPSTORE_AI_API_KEY", "")),
        "launch_command": _ensure_string(params.get("launch_command"), field_name="launch_command", default=""),
        "desktop_file": _ensure_string(params.get("desktop_file"), field_name="desktop_file", default=""),
        "window_name": _ensure_string(params.get("window_name"), field_name="window_name", default=""),
        "window_class": _ensure_string(params.get("window_class"), field_name="window_class", default=""),
        "install_command": _ensure_string(params.get("install_command"), field_name="install_command", default=""),
        "uninstall_command": _ensure_string(params.get("uninstall_command"), field_name="uninstall_command", default=""),
        "sudo_password": _ensure_string(params.get("sudo_password"), field_name="sudo_password", default=DEFAULT_SUDO_PASSWORD),
        "screen_size": _ensure_string(params.get("screen_size"), field_name="screen_size", default="1920x1080x24"),
        "scale_filter": _ensure_string(params.get("scale_filter"), field_name="scale_filter", default="1280:-2"),
        "capture_tool": _ensure_string(params.get("capture_tool"), field_name="capture_tool", default="scrot"),
        "ocr_backend": _ensure_string(params.get("ocr_backend"), field_name="ocr_backend", default="auto"),
        "ocr_python": _ensure_string(params.get("ocr_python"), field_name="ocr_python", default=os.environ.get("APPSTORE_OCR_PYTHON", "")),
        "ocr_min_score": _ensure_optional_float(params.get("ocr_min_score"), field_name="ocr_min_score"),
        "skip_install": _ensure_optional_bool(params.get("skip_install"), field_name="skip_install"),
        "keep_installed": _ensure_optional_bool(params.get("keep_installed"), field_name="keep_installed"),
        "dbus_session": _ensure_optional_bool(params.get("dbus_session"), field_name="dbus_session"),
        "window_timeout": _ensure_optional_float(params.get("window_timeout"), field_name="window_timeout"),
        "settle_time": _ensure_optional_float(params.get("settle_time"), field_name="settle_time"),
        "validate_screenshots": _ensure_optional_bool(params.get("validate_screenshots"), field_name="validate_screenshots"),
        "min_screenshots": _ensure_optional_int(params.get("min_screenshots"), field_name="min_screenshots"),
        "max_screenshots": _ensure_optional_int(params.get("max_screenshots"), field_name="max_screenshots"),
        "min_screenshot_width": _ensure_optional_int(params.get("min_screenshot_width"), field_name="min_screenshot_width"),
        "min_screenshot_height": _ensure_optional_int(params.get("min_screenshot_height"), field_name="min_screenshot_height"),
        "min_screenshot_bytes": _ensure_optional_int(params.get("min_screenshot_bytes"), field_name="min_screenshot_bytes"),
        "min_screenshot_stddev": _ensure_optional_float(params.get("min_screenshot_stddev"), field_name="min_screenshot_stddev"),
        "min_screenshot_gray_levels": _ensure_optional_int(params.get("min_screenshot_gray_levels"), field_name="min_screenshot_gray_levels"),
    }


def _validate_capture_request(params: dict[str, Any], *, tool_name: str) -> None:
    steps = _ensure_string_list(params.get("steps"), field_name="steps")
    ocr_backend = _ensure_string(params.get("ocr_backend"), field_name="ocr_backend", default="auto").lower() or "auto"
    validate_screenshots = _ensure_optional_bool(params.get("validate_screenshots"), field_name="validate_screenshots")
    uses_click_text = any(step.lower().startswith("click-text:") for step in steps)
    auto_planned_capture = not steps

    if ocr_backend == "off" and (uses_click_text or auto_planned_capture):
        raise MCPError(
            f"{tool_name} does not allow ocr_backend=off for auto-planned capture or click-text steps"
        )
    if auto_planned_capture and validate_screenshots is False:
        raise MCPError(
            f"{tool_name} does not allow validate_screenshots=false for auto-planned capture"
        )


def _tool_capture_packages(params: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_fields(
        params,
        allowed_fields={
            "packages",
            "output_dir",
            "steps",
            "ai_prompt",
            "ai_base_url",
            "ai_model",
            "ai_api_key",
            "launch_command",
            "desktop_file",
            "window_name",
            "window_class",
            "install_command",
            "uninstall_command",
            "sudo_password",
            "screen_size",
            "scale_filter",
            "capture_tool",
            "ocr_backend",
            "ocr_python",
            "ocr_min_score",
            "skip_install",
            "keep_installed",
            "dbus_session",
            "window_timeout",
            "settle_time",
            "validate_screenshots",
            "min_screenshots",
            "max_screenshots",
            "min_screenshot_width",
            "min_screenshot_height",
            "min_screenshot_bytes",
            "min_screenshot_stddev",
            "min_screenshot_gray_levels",
        },
        tool_name="capture_packages",
    )
    _validate_capture_request(params, tool_name="capture_packages")
    package_paths = [str(Path(item).expanduser().resolve()) for item in _ensure_string_list(params.get("packages"), field_name="packages", required=True)]
    output_dir = _normalize_output_dir(params.get("output_dir"), prefix="capture")
    result = _run_upload_batch_command(
        command="capture-packages",
        positional=package_paths,
        output_dir=output_dir,
        options=_capture_common_options(params),
        report_name="capture-report.json",
    )
    result["capture_details"] = _capture_details_from_report(result.get("report"))
    return _result_payload(result, summary="capture_packages completed")


def _tool_start_capture_packages(params: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_fields(
        params,
        allowed_fields={
            "packages",
            "output_dir",
            "steps",
            "ai_prompt",
            "ai_base_url",
            "ai_model",
            "ai_api_key",
            "launch_command",
            "desktop_file",
            "window_name",
            "window_class",
            "install_command",
            "uninstall_command",
            "sudo_password",
            "screen_size",
            "scale_filter",
            "capture_tool",
            "ocr_backend",
            "ocr_python",
            "ocr_min_score",
            "skip_install",
            "keep_installed",
            "dbus_session",
            "window_timeout",
            "settle_time",
            "validate_screenshots",
            "min_screenshots",
            "max_screenshots",
            "min_screenshot_width",
            "min_screenshot_height",
            "min_screenshot_bytes",
            "min_screenshot_stddev",
            "min_screenshot_gray_levels",
        },
        tool_name="start_capture_packages",
    )
    _validate_capture_request(params, tool_name="start_capture_packages")
    package_paths = [str(Path(item).expanduser().resolve()) for item in _ensure_string_list(params.get("packages"), field_name="packages", required=True)]
    output_dir = _normalize_output_dir(params.get("output_dir"), prefix="capture")
    payload = _spawn_async_job(
        job_type="capture",
        spec={
            "command": "capture-packages",
            "positional": package_paths,
            "output_dir": str(output_dir),
            "options": _capture_common_options(params),
            "report_name": "capture-report.json",
            "postprocess": "capture",
        },
    )
    return _result_payload(payload, summary="start_capture_packages accepted")


def _tool_auto_upload_packages(params: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_fields(
        params,
        allowed_fields={
            "packages",
            "username",
            "password",
            "capabilities_cache",
            "output_dir",
            "mode",
            "session_cache_dir",
            "artifact_dir",
            "headless",
            "app_id",
            "note",
            "release_key",
            "pkg_channel",
            "region",
            "icon_path",
            "capture_package",
            "min_screenshots",
            "max_screenshots",
            "steps",
            "ai_prompt",
            "ai_base_url",
            "ai_model",
            "ai_api_key",
            "launch_command",
            "desktop_file",
            "window_name",
            "window_class",
            "install_command",
            "uninstall_command",
            "sudo_password",
            "screen_size",
            "scale_filter",
            "capture_tool",
            "ocr_backend",
            "ocr_python",
            "ocr_min_score",
            "skip_install",
            "keep_installed",
            "dbus_session",
            "window_timeout",
            "settle_time",
            "validate_screenshots",
            "min_screenshot_width",
            "min_screenshot_height",
            "min_screenshot_bytes",
            "min_screenshot_stddev",
            "min_screenshot_gray_levels",
        },
        tool_name="auto_upload_packages",
    )
    _validate_capture_request(params, tool_name="auto_upload_packages")
    package_paths = [str(Path(item).expanduser().resolve()) for item in _ensure_string_list(params.get("packages"), field_name="packages", required=True)]
    output_dir = _normalize_output_dir(params.get("output_dir"), prefix="auto-upload-packages")
    username, password = _resolve_credentials(params)
    options = _capture_common_options(params)
    options.update(
        {
            "capabilities_cache": _ensure_string(params.get("capabilities_cache"), field_name="capabilities_cache", default=str(DEFAULT_CACHE_DIR)),
            "username": username,
            "password": password,
            "mode": _ensure_string(params.get("mode"), field_name="mode", default="api"),
            "session_cache_dir": _ensure_string(params.get("session_cache_dir"), field_name="session_cache_dir", default=str(DEFAULT_SESSION_CACHE_DIR)),
            "artifact_dir": _ensure_string(params.get("artifact_dir"), field_name="artifact_dir", default=""),
            "headless": _ensure_optional_bool(params.get("headless"), field_name="headless"),
            "app_id": _ensure_string(params.get("app_id"), field_name="app_id", default=""),
            "note": _ensure_string(params.get("note"), field_name="note", default=""),
            "release_key": _ensure_string(params.get("release_key"), field_name="release_key", default="direct-update"),
            "pkg_channel": _ensure_string(params.get("pkg_channel"), field_name="pkg_channel", default=""),
            "region": _ensure_string(params.get("region"), field_name="region", default=""),
            "icon": _ensure_string(params.get("icon_path"), field_name="icon_path", default=""),
            "capture_package": _ensure_string(params.get("capture_package"), field_name="capture_package", default=""),
            "min_screenshots": _ensure_optional_int(params.get("min_screenshots"), field_name="min_screenshots"),
            "max_screenshots": _ensure_optional_int(params.get("max_screenshots"), field_name="max_screenshots"),
        }
    )
    result = _run_upload_batch_command(
        command="auto-upload-packages",
        positional=package_paths,
        output_dir=output_dir,
        options=options,
        report_name="report.json",
    )
    result["workflow_hint"] = _workflow_hint_from_report(result.get("report"))
    capture_report_path = output_dir / "capture" / "capture-report.json"
    capture_report: Any = None
    if capture_report_path.exists():
        capture_report = json.loads(capture_report_path.read_text(encoding="utf-8"))
    result["capture_report_path"] = str(capture_report_path) if capture_report_path.exists() else ""
    result["capture_report"] = capture_report
    result["capture_details"] = _capture_details_from_report(capture_report)
    return _result_payload(result, summary="auto_upload_packages completed")


def _tool_start_auto_upload_packages(params: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_fields(
        params,
        allowed_fields={
            "packages",
            "username",
            "password",
            "capabilities_cache",
            "output_dir",
            "mode",
            "session_cache_dir",
            "artifact_dir",
            "headless",
            "app_id",
            "note",
            "release_key",
            "pkg_channel",
            "region",
            "icon_path",
            "capture_package",
            "min_screenshots",
            "max_screenshots",
            "steps",
            "ai_prompt",
            "ai_base_url",
            "ai_model",
            "ai_api_key",
            "launch_command",
            "desktop_file",
            "window_name",
            "window_class",
            "install_command",
            "uninstall_command",
            "sudo_password",
            "screen_size",
            "scale_filter",
            "capture_tool",
            "ocr_backend",
            "ocr_python",
            "ocr_min_score",
            "skip_install",
            "keep_installed",
            "dbus_session",
            "window_timeout",
            "settle_time",
            "validate_screenshots",
            "min_screenshot_width",
            "min_screenshot_height",
            "min_screenshot_bytes",
            "min_screenshot_stddev",
            "min_screenshot_gray_levels",
        },
        tool_name="start_auto_upload_packages",
    )
    _validate_capture_request(params, tool_name="start_auto_upload_packages")
    package_paths = [str(Path(item).expanduser().resolve()) for item in _ensure_string_list(params.get("packages"), field_name="packages", required=True)]
    output_dir = _normalize_output_dir(params.get("output_dir"), prefix="auto-upload-packages")
    username, password = _resolve_credentials(params)
    options = _capture_common_options(params)
    options.update(
        {
            "capabilities_cache": _ensure_string(params.get("capabilities_cache"), field_name="capabilities_cache", default=str(DEFAULT_CACHE_DIR)),
            "username": username,
            "password": password,
            "mode": _ensure_string(params.get("mode"), field_name="mode", default="api"),
            "session_cache_dir": _ensure_string(params.get("session_cache_dir"), field_name="session_cache_dir", default=str(DEFAULT_SESSION_CACHE_DIR)),
            "artifact_dir": _ensure_string(params.get("artifact_dir"), field_name="artifact_dir", default=""),
            "headless": _ensure_optional_bool(params.get("headless"), field_name="headless"),
            "app_id": _ensure_string(params.get("app_id"), field_name="app_id", default=""),
            "note": _ensure_string(params.get("note"), field_name="note", default=""),
            "release_key": _ensure_string(params.get("release_key"), field_name="release_key", default="direct-update"),
            "pkg_channel": _ensure_string(params.get("pkg_channel"), field_name="pkg_channel", default=""),
            "region": _ensure_string(params.get("region"), field_name="region", default=""),
            "icon": _ensure_string(params.get("icon_path"), field_name="icon_path", default=""),
            "capture_package": _ensure_string(params.get("capture_package"), field_name="capture_package", default=""),
            "min_screenshots": _ensure_optional_int(params.get("min_screenshots"), field_name="min_screenshots"),
            "max_screenshots": _ensure_optional_int(params.get("max_screenshots"), field_name="max_screenshots"),
        }
    )
    payload = _spawn_async_job(
        job_type="auto-upload",
        spec={
            "command": "auto-upload-packages",
            "positional": package_paths,
            "output_dir": str(output_dir),
            "options": options,
            "report_name": "report.json",
            "postprocess": "auto-upload",
        },
    )
    return _result_payload(payload, summary="start_auto_upload_packages accepted")


def _tool_get_job_status(params: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_fields(params, allowed_fields={"job_id"}, tool_name="get_job_status")
    job_id = _ensure_string(params.get("job_id"), field_name="job_id")
    if not job_id:
        raise MCPError("job_id is required")
    payload = _load_job_payload(job_id)
    return _result_payload(payload, summary="get_job_status completed")


TOOLS: dict[str, MCPTool] = {
    "inspect_packages": MCPTool(
        name="inspect_packages",
        description="解析真实包文件的包名、版本、架构、大小和 sha256。常用于提审前确认多架构包是否一致。",
        input_schema={
            "type": "object",
            "properties": {
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "待解析的包文件绝对路径列表。",
                }
            },
            "required": ["packages"],
        },
        handler=_tool_inspect_packages,
    ),
    "sync_capabilities": MCPTool(
        name="sync_capabilities",
        description="登录应用商店并同步系统线、baseline 能力缓存。新应用 workbook 生成或真实上传前应先调用一次。",
        input_schema={
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "应用商店用户名。可省略并改用 APPSTORE_USERNAME。"},
                "password": {"type": "string", "description": "应用商店密码。可省略并改用 APPSTORE_PASSWORD。"},
                "cache_dir": {"type": "string", "description": "能力缓存目录。默认使用仓库内 cache/capabilities。"},
            },
        },
        handler=_tool_sync_capabilities,
    ),
    "generate_example_template": MCPTool(
        name="generate_example_template",
        description="仅生成 LabelNova 示例 workbook、示例包和占位图片，用于演示或 dry-run。不要直接拿它提交真实应用。",
        input_schema={
            "type": "object",
            "properties": {
                "output_path": {"type": "string", "description": "生成的示例 xlsx 绝对路径。"},
                "capabilities_cache": {"type": "string", "description": "能力缓存目录。"},
            },
        },
        handler=_tool_generate_template,
    ),
    "prepare_new_app_workbook": MCPTool(
        name="prepare_new_app_workbook",
        description="为真实新应用生成 workbook。会读取你提供的真实包路径，不会写入 LabelNova 示例包；缺少图标、截图或系统线时会显式标记。",
        input_schema={
            "type": "object",
            "properties": {
                "packages": {"type": "array", "items": {"type": "string"}, "description": "真实包文件绝对路径列表。"},
                "output_path": {"type": "string", "description": "输出 workbook 路径。默认写到首个包所在目录下的 <pkg_name>-submission.xlsx。"},
                "capabilities_cache": {"type": "string", "description": "能力缓存目录。默认使用仓库内 cache/capabilities。"},
                "app_key": {"type": "string", "description": "可选应用键。默认直接使用 pkg_name。"},
                "app_name_zh": {"type": "string", "description": "应用中文名。缺省时会先回填 pkg_name，稍后建议人工确认。"},
                "category_id": {"type": "integer", "description": "应用分类 ID。缺省为 1。"},
                "website": {"type": "string", "description": "应用官网。缺省时会写入 example.invalid 占位值。"},
                "short_desc_zh": {"type": "string", "description": "一句话简介。"},
                "full_desc_zh": {"type": "string", "description": "详细描述。"},
                "icon_path": {"type": "string", "description": "图标路径。未提供时会尝试自动探测同目录 icon.png。"},
                "screenshot_paths": {"type": "array", "items": {"type": "string"}, "description": "最多 3 张截图路径。未提供时会尝试自动探测 screenshots/。"},
                "keywords_zh": {"type": "string", "description": "中文关键词。"},
                "release_key": {"type": "string", "description": "发布键。默认 stable。"},
                "execution_mode": {"type": "string", "enum": ["auto", "api", "browser"], "description": "workbook 中的执行模式。默认 api。"},
                "region": {"type": "string", "description": "区域。默认 1。"},
                "note": {"type": "string", "description": "更新说明。"},
                "pkg_channel": {"type": "string", "description": "包通道。默认 stable。"},
                "system_line_codes": {"type": "array", "items": {"type": "string"}, "description": "要启用的系统线 code 列表。缺省时不会自动勾选，避免错误声明兼容性。"},
            },
            "required": ["packages"],
        },
        handler=_tool_prepare_new_app_workbook,
    ),
    "validate_workbook": MCPTool(
        name="validate_workbook",
        description="校验 workbook 是否足够完整，可以用于批量提审或更新。",
        input_schema={
            "type": "object",
            "properties": {
                "workbook": {"type": "string", "description": "待校验 workbook 绝对路径。"},
                "rows": {"type": "string", "description": "可选行过滤，如 package:2,release:5。"},
                "capabilities_cache": {"type": "string", "description": "能力缓存目录。"},
                "output_dir": {"type": "string", "description": "输出报告目录。"},
            },
            "required": ["workbook"],
        },
        handler=_tool_validate_workbook,
    ),
    "upload_workbook": MCPTool(
        name="upload_workbook",
        description="按 workbook 执行新应用提审或已有应用更新。新应用建议先用 prepare_new_app_workbook 生成真实 workbook。",
        input_schema={
            "type": "object",
            "properties": {
                "workbook": {"type": "string", "description": "workbook 绝对路径。"},
                "username": {"type": "string"},
                "password": {"type": "string"},
                "rows": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "capabilities_cache": {"type": "string"},
                "output_dir": {"type": "string"},
                "mode": {"type": "string", "enum": ["auto", "api", "browser"]},
                "session_cache_dir": {"type": "string"},
                "artifact_dir": {"type": "string"},
                "headless": {"type": "boolean"},
            },
            "required": ["workbook"],
        },
        handler=_tool_upload_workbook,
    ),
    "upload_packages": MCPTool(
        name="upload_packages",
        description="仅用于已有应用更新。若返回 existing app not found，说明它不是新应用提审入口，应改用 prepare_new_app_workbook + upload_workbook。",
        input_schema={
            "type": "object",
            "properties": {
                "packages": {"type": "array", "items": {"type": "string"}},
                "username": {"type": "string"},
                "password": {"type": "string"},
                "capabilities_cache": {"type": "string"},
                "output_dir": {"type": "string"},
                "mode": {"type": "string", "enum": ["auto", "api", "browser"]},
                "session_cache_dir": {"type": "string"},
                "artifact_dir": {"type": "string"},
                "headless": {"type": "boolean"},
                "app_id": {"type": "string"},
                "note": {"type": "string"},
                "release_key": {"type": "string"},
                "pkg_channel": {"type": "string"},
                "region": {"type": "string"},
                "screenshot_paths": {"type": "array", "items": {"type": "string"}},
                "icon_path": {"type": "string"},
            },
            "required": ["packages"],
        },
        handler=_tool_upload_packages,
    ),
    "capture_packages": MCPTool(
        name="capture_packages",
        description="无头安装、启动、操作应用并自动截图。它只负责产出截图和报告，不会直接提交新应用。适合短时同步调试；真实 GUI 截图任务通常超过 180 秒，建议优先改用 start_capture_packages 异步启动后再用 get_job_status 轮询。若未显式提供 steps，当前实现会先截首屏，再把整张界面的 OCR 文本块和坐标交给 AI 逐轮决定下一步点击，每拍完一张都会重新识别当前界面后再继续，不依赖写死页面标签。显式 steps 仍可使用 click-text:<界面文字> 触发 OCR 点击。自动规划模式下不要关闭 OCR 或截图校验。若 env 里配置 APPSTORE_SUDO_PASSWORD 则安装/卸载走 sudo -S，否则默认 sudo 命令会改走 pkexec。",
        input_schema={
            "type": "object",
            "properties": {
                "packages": {"type": "array", "items": {"type": "string"}},
                "output_dir": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "ai_prompt": {"type": "string"},
                "ai_base_url": {"type": "string"},
                "ai_model": {"type": "string"},
                "ai_api_key": {"type": "string"},
                "launch_command": {"type": "string"},
                "desktop_file": {"type": "string"},
                "window_name": {"type": "string"},
                "window_class": {"type": "string"},
                "install_command": {"type": "string"},
                "uninstall_command": {"type": "string"},
                "sudo_password": {"type": "string", "description": "可选 sudo 密码。不传则读取 APPSTORE_SUDO_PASSWORD；若仍为空，默认 sudo 安装/卸载命令会改走 pkexec。"},
                "screen_size": {"type": "string"},
                "scale_filter": {"type": "string"},
                "capture_tool": {"type": "string", "enum": ["scrot", "ffmpeg"]},
                "ocr_backend": {"type": "string", "enum": ["auto", "rapidocr", "off"]},
                "ocr_python": {"type": "string", "description": "OCR Python 解释器路径，通常指向 .venv-ocr/bin/python3。"},
                "ocr_min_score": {"type": "number"},
                "skip_install": {"type": "boolean"},
                "keep_installed": {"type": "boolean"},
                "dbus_session": {"type": "boolean"},
                "window_timeout": {"type": "number"},
                "settle_time": {"type": "number"},
                "validate_screenshots": {"type": "boolean"},
                "min_screenshots": {"type": "integer", "description": "期望至少生成多少张有效截图。默认 1。"},
                "max_screenshots": {"type": "integer", "description": "最多保留多少张有效截图。默认 6。"},
                "min_screenshot_width": {"type": "integer"},
                "min_screenshot_height": {"type": "integer"},
                "min_screenshot_bytes": {"type": "integer"},
                "min_screenshot_stddev": {"type": "number"},
                "min_screenshot_gray_levels": {"type": "integer"},
            },
            "required": ["packages"],
        },
        handler=_tool_capture_packages,
    ),
    "start_capture_packages": MCPTool(
        name="start_capture_packages",
        description="异步启动长时间截图任务。适合真实 GUI 安装、OCR、AI 分析、多轮点击和多张截图；会立刻返回 job_id，之后用 get_job_status 查看进度和最终结果，避免 180 秒超时。",
        input_schema={
            "type": "object",
            "properties": {
                "packages": {"type": "array", "items": {"type": "string"}},
                "output_dir": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "ai_prompt": {"type": "string"},
                "ai_base_url": {"type": "string"},
                "ai_model": {"type": "string"},
                "ai_api_key": {"type": "string"},
                "launch_command": {"type": "string"},
                "desktop_file": {"type": "string"},
                "window_name": {"type": "string"},
                "window_class": {"type": "string"},
                "install_command": {"type": "string"},
                "uninstall_command": {"type": "string"},
                "sudo_password": {"type": "string", "description": "可选 sudo 密码。不传则读取 APPSTORE_SUDO_PASSWORD；若仍为空，默认 sudo 安装/卸载命令会改走 pkexec。"},
                "screen_size": {"type": "string"},
                "scale_filter": {"type": "string"},
                "capture_tool": {"type": "string", "enum": ["scrot", "ffmpeg"]},
                "ocr_backend": {"type": "string", "enum": ["auto", "rapidocr", "off"]},
                "ocr_python": {"type": "string"},
                "ocr_min_score": {"type": "number"},
                "skip_install": {"type": "boolean"},
                "keep_installed": {"type": "boolean"},
                "dbus_session": {"type": "boolean"},
                "window_timeout": {"type": "number"},
                "settle_time": {"type": "number"},
                "validate_screenshots": {"type": "boolean"},
                "min_screenshots": {"type": "integer"},
                "max_screenshots": {"type": "integer"},
                "min_screenshot_width": {"type": "integer"},
                "min_screenshot_height": {"type": "integer"},
                "min_screenshot_bytes": {"type": "integer"},
                "min_screenshot_stddev": {"type": "number"},
                "min_screenshot_gray_levels": {"type": "integer"},
            },
            "required": ["packages"],
        },
        handler=_tool_start_capture_packages,
    ),
    "auto_upload_packages": MCPTool(
        name="auto_upload_packages",
        description="先自动截图，再把截图和包一起更新到应用商店。只适用于已有应用更新，不适用于新应用首提。同步模式适合短时调试；真实长链路建议改用 start_auto_upload_packages 异步启动后再用 get_job_status 轮询。若未显式提供 steps，截图阶段会先截首屏，再把整张界面的 OCR 文本块和坐标交给 AI 逐轮决定下一步点击，每拍完一张都会重新识别当前界面后再继续，不依赖写死页面标签。显式 steps 仍可使用 click-text:<界面文字> 触发 OCR 点击。自动规划模式下不要关闭 OCR 或截图校验。若 env 里配置 APPSTORE_SUDO_PASSWORD 则安装/卸载走 sudo -S，否则默认 sudo 命令会改走 pkexec。",
        input_schema={
            "type": "object",
            "properties": {
                "packages": {"type": "array", "items": {"type": "string"}},
                "username": {"type": "string"},
                "password": {"type": "string"},
                "capabilities_cache": {"type": "string"},
                "output_dir": {"type": "string"},
                "mode": {"type": "string", "enum": ["auto", "api", "browser"]},
                "session_cache_dir": {"type": "string"},
                "artifact_dir": {"type": "string"},
                "headless": {"type": "boolean"},
                "app_id": {"type": "string"},
                "note": {"type": "string"},
                "release_key": {"type": "string"},
                "pkg_channel": {"type": "string"},
                "region": {"type": "string"},
                "icon_path": {"type": "string"},
                "capture_package": {"type": "string"},
                "min_screenshots": {"type": "integer"},
                "max_screenshots": {"type": "integer"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "ai_prompt": {"type": "string"},
                "ai_base_url": {"type": "string"},
                "ai_model": {"type": "string"},
                "ai_api_key": {"type": "string"},
                "launch_command": {"type": "string"},
                "desktop_file": {"type": "string"},
                "window_name": {"type": "string"},
                "window_class": {"type": "string"},
                "install_command": {"type": "string"},
                "uninstall_command": {"type": "string"},
                "sudo_password": {"type": "string", "description": "可选 sudo 密码。不传则读取 APPSTORE_SUDO_PASSWORD；若仍为空，默认 sudo 安装/卸载命令会改走 pkexec。"},
                "screen_size": {"type": "string"},
                "scale_filter": {"type": "string"},
                "capture_tool": {"type": "string", "enum": ["scrot", "ffmpeg"]},
                "ocr_backend": {"type": "string", "enum": ["auto", "rapidocr", "off"]},
                "ocr_python": {"type": "string"},
                "ocr_min_score": {"type": "number"},
                "skip_install": {"type": "boolean"},
                "keep_installed": {"type": "boolean"},
                "dbus_session": {"type": "boolean"},
                "window_timeout": {"type": "number"},
                "settle_time": {"type": "number"},
                "validate_screenshots": {"type": "boolean"},
                "min_screenshot_width": {"type": "integer"},
                "min_screenshot_height": {"type": "integer"},
                "min_screenshot_bytes": {"type": "integer"},
                "min_screenshot_stddev": {"type": "number"},
                "min_screenshot_gray_levels": {"type": "integer"},
            },
            "required": ["packages"],
        },
        handler=_tool_auto_upload_packages,
    ),
    "start_auto_upload_packages": MCPTool(
        name="start_auto_upload_packages",
        description="异步启动自动截图并上传的长链路任务。会立刻返回 job_id，之后用 get_job_status 轮询，避免真实截图和上传流程超过 180 秒。",
        input_schema={
            "type": "object",
            "properties": {
                "packages": {"type": "array", "items": {"type": "string"}},
                "username": {"type": "string"},
                "password": {"type": "string"},
                "capabilities_cache": {"type": "string"},
                "output_dir": {"type": "string"},
                "mode": {"type": "string", "enum": ["auto", "api", "browser"]},
                "session_cache_dir": {"type": "string"},
                "artifact_dir": {"type": "string"},
                "headless": {"type": "boolean"},
                "app_id": {"type": "string"},
                "note": {"type": "string"},
                "release_key": {"type": "string"},
                "pkg_channel": {"type": "string"},
                "region": {"type": "string"},
                "icon_path": {"type": "string"},
                "capture_package": {"type": "string"},
                "min_screenshots": {"type": "integer"},
                "max_screenshots": {"type": "integer"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "ai_prompt": {"type": "string"},
                "ai_base_url": {"type": "string"},
                "ai_model": {"type": "string"},
                "ai_api_key": {"type": "string"},
                "launch_command": {"type": "string"},
                "desktop_file": {"type": "string"},
                "window_name": {"type": "string"},
                "window_class": {"type": "string"},
                "install_command": {"type": "string"},
                "uninstall_command": {"type": "string"},
                "sudo_password": {"type": "string", "description": "可选 sudo 密码。不传则读取 APPSTORE_SUDO_PASSWORD；若仍为空，默认 sudo 安装/卸载命令会改走 pkexec。"},
                "screen_size": {"type": "string"},
                "scale_filter": {"type": "string"},
                "capture_tool": {"type": "string", "enum": ["scrot", "ffmpeg"]},
                "ocr_backend": {"type": "string", "enum": ["auto", "rapidocr", "off"]},
                "ocr_python": {"type": "string"},
                "ocr_min_score": {"type": "number"},
                "skip_install": {"type": "boolean"},
                "keep_installed": {"type": "boolean"},
                "dbus_session": {"type": "boolean"},
                "window_timeout": {"type": "number"},
                "settle_time": {"type": "number"},
                "validate_screenshots": {"type": "boolean"},
                "min_screenshot_width": {"type": "integer"},
                "min_screenshot_height": {"type": "integer"},
                "min_screenshot_bytes": {"type": "integer"},
                "min_screenshot_stddev": {"type": "number"},
                "min_screenshot_gray_levels": {"type": "integer"},
            },
            "required": ["packages"],
        },
        handler=_tool_start_auto_upload_packages,
    ),
    "get_job_status": MCPTool(
        name="get_job_status",
        description="读取 start_capture_packages 或 start_auto_upload_packages 启动的后台作业状态。如果任务已完成，会返回 report、capture_details 和执行摘要。",
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
            },
            "required": ["job_id"],
        },
        handler=_tool_get_job_status,
    ),
}


def _handle_initialize(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol_version = _ensure_string(params.get("protocolVersion"), field_name="protocolVersion", default=_SERVER_PROTOCOL_VERSION) or _SERVER_PROTOCOL_VERSION
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "appstore-mcp",
                "version": "0.1.0",
            },
        },
    }


def _handle_tools_list(request_id: Any) -> dict[str, Any]:
    tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }
        for tool in TOOLS.values()
    ]
    return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}


def _handle_tools_call(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    tool_name = _ensure_string(params.get("name"), field_name="name")
    if not tool_name:
        raise MCPError("tools/call requires name", code=-32602)
    tool = TOOLS.get(tool_name)
    if tool is None:
        raise MCPError(f"unknown tool: {tool_name}", code=-32601)
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise MCPError("tools/call arguments must be an object", code=-32602)
    return {"jsonrpc": "2.0", "id": request_id, "result": tool.handler(arguments)}


def _success_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, *, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise MCPError("params must be an object", code=-32602)

    if request_id is None:
        if method == "notifications/initialized":
            return None
        if method == "notifications/cancelled":
            return None
        return None

    if method == "initialize":
        return _handle_initialize(request_id, params)
    if method == "ping":
        return _success_response(request_id, {})
    if method == "tools/list":
        return _handle_tools_list(request_id)
    if method == "tools/call":
        return _handle_tools_call(request_id, params)
    raise MCPError(f"unsupported method: {method}", code=-32601)


def _decode_message(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MCPError("invalid JSON-RPC payload", code=-32700) from exc
    if not isinstance(payload, dict):
        raise MCPError("JSON-RPC payload must be an object", code=-32600)
    return payload


def _extract_message(buffer: bytearray) -> dict[str, Any] | None:
    while buffer and buffer[0] in b" \t\r\n":
        del buffer[0]
    if not buffer:
        return None

    if buffer.lower().startswith(b"content-length:"):
        header_end = buffer.find(b"\r\n\r\n")
        separator_size = 4
        if header_end < 0:
            header_end = buffer.find(b"\n\n")
            separator_size = 2
        if header_end < 0:
            return None
        header_blob = bytes(buffer[:header_end]).decode("utf-8", errors="replace")
        content_length = None
        for line in header_blob.splitlines():
            name, _, value = line.partition(":")
            if name.lower().strip() == "content-length":
                try:
                    content_length = int(value.strip())
                except ValueError as exc:
                    raise MCPError("invalid Content-Length", code=-32700) from exc
                break
        if content_length is None:
            raise MCPError("missing Content-Length", code=-32700)
        total = header_end + separator_size + content_length
        if len(buffer) < total:
            return None
        body = bytes(buffer[header_end + separator_size : total])
        del buffer[:total]
        return _decode_message(body)

    newline_index = buffer.find(b"\n")
    if newline_index < 0:
        return None
    line = bytes(buffer[:newline_index]).strip()
    del buffer[: newline_index + 1]
    if not line:
        return None
    return _decode_message(line)


def _write_message(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve() -> int:
    _configure_logging()
    buffer = bytearray()
    stdin = sys.stdin.buffer
    while True:
        chunk = stdin.read1(65536)
        if not chunk:
            break
        buffer.extend(chunk)
        while True:
            message = _extract_message(buffer)
            if message is None:
                break
            request_id = message.get("id")
            try:
                response = _dispatch(message)
            except MCPError as exc:
                response = None if request_id is None else _error_response(request_id, code=exc.code, message=str(exc))
            except Exception as exc:
                LOGGER.exception("Unhandled appstore MCP error")
                response = None if request_id is None else _error_response(request_id, code=-32603, message=str(exc))
            if response is not None:
                _write_message(response)
    return 0


def main(argv: list[str] | None = None) -> int:
    _ = argv
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
