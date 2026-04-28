import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openGA.ocr import OCRLine

from appstore.capture_steps import CaptureStep
from appstore.capture_workflow import (
    _fallback_retry_capture_steps,
    _make_semantic_validator,
    _scene_elements_from_lines,
    _write_capture_reports,
    _write_package_metadata,
    CapturePackageResult,
    build_install_command,
    parse_screen_size,
    render_shell_template,
    resolve_capture_steps,
    run_capture_steps,
    sanitize_label,
)


class CaptureWorkflowTests(unittest.TestCase):
    def test_render_shell_template_quotes_values(self) -> None:
        command = render_shell_template(
            "echo {package_path} {package_name}",
            package_path="/tmp/demo app.deb",
            package_name="demo-app",
        )
        self.assertEqual(command[:2], ["sh", "-lc"])
        self.assertIn("'/tmp/demo app.deb'", command[2])

    def test_build_install_command_defaults_to_apt_for_deb(self) -> None:
        with patch("appstore.capture_workflow._command_exists", return_value=True):
            command = build_install_command(
                package_family="deb",
                package_path="/tmp/demo.deb",  # type: ignore[arg-type]
                package_name="demo-app",
            )
        self.assertIn("pkexec apt-get install --allow-downgrades -y", command[2])

    def test_build_install_command_uses_sudo_stdin_when_password_provided(self) -> None:
        command = build_install_command(
            package_family="deb",
            package_path="/tmp/demo.deb",  # type: ignore[arg-type]
            package_name="demo-app",
            sudo_password="123",
        )
        self.assertIn("sudo -S -p '' apt-get install --allow-downgrades -y", command[2])
        self.assertIn("APPSTORE_SUDO_PASSWORD", command[2])

    def test_build_install_command_fails_when_pkexec_missing_and_no_password(self) -> None:
        with patch("appstore.capture_workflow._command_exists", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "pkexec not found"):
                build_install_command(
                    package_family="deb",
                    package_path="/tmp/demo.deb",  # type: ignore[arg-type]
                    package_name="demo-app",
                )

    def test_resolve_capture_steps_prefers_explicit_steps_over_ai_prompt(self) -> None:
        with patch("appstore.capture_workflow.plan_capture_steps") as planner:
            steps = resolve_capture_steps(
                step_texts=("screenshot:home",),
                ai_prompt="ignore me",
                ai_base_url="http://127.0.0.1:8787/v1",
                ai_model="openai-codex/gpt-5.4",
                ai_api_key="",
                package_name="demo-app",
                app_name="Demo App",
                min_screenshots=1,
                max_screenshots=6,
            )

        self.assertEqual(steps, (CaptureStep(action="screenshot", value="home"),))
        planner.assert_not_called()

    def test_resolve_capture_steps_autoplans_when_multiple_screenshots_are_required(self) -> None:
        with patch("appstore.capture_workflow.plan_capture_steps") as planner:
            steps = resolve_capture_steps(
                step_texts=(),
                ai_prompt="",
                ai_base_url="http://127.0.0.1:8787/v1",
                ai_model="openai-codex/gpt-5.4",
                ai_api_key="",
                package_name="demo-app",
                app_name="Demo App",
                min_screenshots=3,
                max_screenshots=6,
            )

        self.assertEqual(
            steps,
            (
                CaptureStep(action="wait-window", seconds=30.0),
                CaptureStep(action="sleep", seconds=2.0),
                CaptureStep(action="screenshot", value=""),
            ),
        )
        planner.assert_not_called()

    def test_resolve_capture_steps_uses_planner_when_user_prompt_is_present(self) -> None:
        with patch(
            "appstore.capture_workflow.plan_capture_steps",
            return_value=("wait-window:30", "click-text:排行榜", "screenshot:ranking"),
        ) as planner:
            steps = resolve_capture_steps(
                step_texts=(),
                ai_prompt="探索不同页面并截图",
                ai_base_url="http://127.0.0.1:8787/v1",
                ai_model="openai-codex/gpt-5.4",
                ai_api_key="",
                package_name="demo-app",
                app_name="Demo App",
                min_screenshots=3,
                max_screenshots=6,
            )

        self.assertEqual(
            steps,
            (
                CaptureStep(action="wait-window", seconds=30.0),
                CaptureStep(action="click-text", value="排行榜"),
                CaptureStep(action="screenshot", value="ranking"),
            ),
        )
        self.assertEqual(planner.call_args.kwargs["prompt"], "探索不同页面并截图")

    def test_resolve_capture_steps_uses_retry_fallback_when_planner_fails(self) -> None:
        with patch("appstore.capture_workflow.plan_capture_steps", side_effect=RuntimeError("boom")):
            steps = resolve_capture_steps(
                step_texts=(),
                ai_prompt="",
                ai_base_url="http://127.0.0.1:8787/v1",
                ai_model="openai-codex/gpt-5.4",
                ai_api_key="",
                package_name="demo-app",
                app_name="Demo App",
                min_screenshots=3,
                max_screenshots=6,
                accepted_labels=("home",),
                rejected_reasons=("01-home.png: duplicate of home",),
            )

        self.assertEqual(steps[-1], CaptureStep(action="screenshot", value="screen-03"))
        self.assertNotIn(CaptureStep(action="screenshot", value="home"), steps)

    def test_run_capture_steps_stops_when_max_screenshots_reached(self) -> None:
        with TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir)
            with patch("appstore.capture_workflow.capture_display"), patch(
                "appstore.capture_workflow.normalize_capture"
            ), patch("appstore.capture_workflow.get_window_geometry", return_value=None):
                artifacts, returned_window_id = run_capture_steps(
                    steps=(
                        CaptureStep(action="screenshot", value="home"),
                        CaptureStep(action="screenshot", value="settings"),
                        CaptureStep(action="screenshot", value="help"),
                    ),
                    env={"DISPLAY": ":99"},
                    window_id="100",
                    window_name="Demo App",
                    window_class="",
                    asset_dir=asset_dir,
                    capture_tool="scrot",
                    scale_filter="1280:-2",
                    screen_size="1920x1080x24",
                    settle_time=0.0,
                    start_index=0,
                    max_screenshots=2,
                    ocr_backend="auto",
                    ocr_python="",
                    ocr_min_score=0.35,
                )

        self.assertEqual(returned_window_id, "100")
        self.assertEqual([artifact.label for artifact in artifacts], ["home", "settings"])

    def test_run_capture_steps_click_text_uses_ocr_match_center(self) -> None:
        with TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir)
            calls: list[list[str]] = []

            def _record_run(args, **kwargs):
                calls.append(list(args))
                class _Result:
                    returncode = 0
                return _Result()

            with patch(
                "appstore.capture_workflow._resolve_click_text_target",
                return_value=(88, 144),
            ), patch("appstore.capture_workflow.subprocess.run", side_effect=_record_run):
                artifacts, returned_window_id = run_capture_steps(
                    steps=(CaptureStep(action="click-text", value="排行榜"),),
                    env={"DISPLAY": ":99"},
                    window_id="100",
                    window_name="Demo App",
                    window_class="",
                    asset_dir=asset_dir,
                    capture_tool="scrot",
                    scale_filter="1280:-2",
                    screen_size="1920x1080x24",
                    settle_time=0.0,
                    start_index=0,
                    max_screenshots=2,
                    ocr_backend="auto",
                    ocr_python="",
                    ocr_min_score=0.35,
                )

        self.assertEqual(artifacts, [])
        self.assertEqual(returned_window_id, "100")
        self.assertIn(["xdotool", "mousemove", "--window", "100", "88", "144", "click", "1"], calls)

    def test_make_semantic_validator_rejects_near_duplicate_ocr_text(self) -> None:
        validator = _make_semantic_validator(
            ocr_backend="auto",
            ocr_python="",
            package_name="demo-app",
            app_name="Demo App",
            ai_base_url="http://127.0.0.1:8787/v1",
            ai_model="anthropic/glm-5",
            ai_api_key="",
        )
        with patch("appstore.capture_workflow.semantic_rejection_reasons", return_value=()), patch(
            "appstore.capture_workflow.semantic_text_signature",
            side_effect=("推荐 排行 查看详情", "推荐 排行 查看详情"),
        ):
            with patch("appstore.capture_workflow.review_capture_text", side_effect=RuntimeError("skip ai")):
                first_reasons = validator(Path("/tmp/01-home.png"))
                second_reasons = validator(Path("/tmp/02-home.png"))

        self.assertEqual(first_reasons, ())
        self.assertIn("ocr text similar to 01-home.png", second_reasons[0])

    def test_make_semantic_validator_uses_ai_review_instead_of_hardcoded_labels(self) -> None:
        validator = _make_semantic_validator(
            ocr_backend="auto",
            ocr_python="",
            package_name="demo-app",
            app_name="Demo App",
            ai_base_url="http://127.0.0.1:8787/v1",
            ai_model="anthropic/glm-5",
            ai_api_key="",
        )
        with patch("appstore.capture_workflow.semantic_rejection_reasons", return_value=()), patch(
            "appstore.capture_workflow.semantic_text_signature",
            return_value="推荐 排行 查看详情 安装 更新",
        ), patch(
            "appstore.capture_workflow.review_capture_text",
            return_value=type("Review", (), {"useful": False, "reason": "same home interface"})(),
        ):
            reasons = validator(Path("/tmp/02-anything.png"))

        self.assertIn("ai review rejected screenshot: same home interface", reasons[0])

    def test_scene_elements_from_lines_keeps_full_scene_without_preselecting_targets(self) -> None:
        lines = (
            OCRLine(text="推荐", score=0.99, box=((20, 54), (60, 54), (60, 71), (20, 71))),
            OCRLine(text="全部", score=0.99, box=((20, 88), (60, 88), (60, 106), (20, 106))),
            OCRLine(text="排行榜", score=0.99, box=((20, 120), (68, 120), (68, 143), (20, 143))),
            OCRLine(text="办公", score=0.99, box=((20, 196), (68, 196), (68, 213), (20, 213))),
            OCRLine(text="查看详情", score=0.99, box=((264, 90), (304, 90), (304, 107), (264, 107))),
            OCRLine(text="微信", score=0.99, box=((168, 265), (196, 265), (196, 283), (168, 283))),
            OCRLine(text="安装", score=0.99, box=((369, 273), (403, 273), (403, 291), (369, 291))),
        )

        elements = _scene_elements_from_lines(lines, tried_click_targets=("推荐",))

        self.assertEqual(elements[0].text, "全部")
        self.assertTrue(elements[0].element_id.startswith("node-"))
        self.assertIn("查看详情", [element.text for element in elements])
        self.assertIn("安装", [element.text for element in elements])

    def test_fallback_retry_capture_steps_uses_scene_element_coordinates_when_available(self) -> None:
        steps = _fallback_retry_capture_steps(
            target_screenshots=3,
            accepted_count=1,
            scene_elements=_scene_elements_from_lines(
                (
                    OCRLine(text="排行榜", score=0.99, box=((20, 120), (68, 120), (68, 143), (20, 143))),
                    OCRLine(text="查看详情", score=0.99, box=((264, 90), (304, 90), (304, 107), (264, 107))),
                ),
                tried_click_targets=(),
            ),
            accepted_texts=(),
        )

        self.assertEqual(steps[0].action, "click")
        self.assertEqual(steps[0].value, "node-01")
        self.assertIsNotNone(steps[0].x)
        self.assertIsNotNone(steps[0].y)
        self.assertIn(CaptureStep(action="key", value="Alt+Left"), steps)
        self.assertIn(CaptureStep(action="screenshot", value="screen-02"), steps)

    def test_parse_screen_size_and_sanitize_label(self) -> None:
        self.assertEqual(parse_screen_size("1920x1080x24"), (1920, 1080, 24))
        self.assertEqual(sanitize_label(" Settings Page "), "settings-page")

    def test_write_package_metadata_includes_execution_trace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir)
            result = CapturePackageResult(
                row_id=1,
                package_path=Path("/tmp/demo.deb"),
                pkg_name="demo-app",
                pkg_version="1.0.0",
                pkg_arch="amd64",
                status="capture_failed",
                message="timed out",
                asset_dir=asset_dir,
                execution_trace={
                    "capture_stage": "waiting_for_window",
                    "effective_ocr_backend": "rapidocr",
                    "ocr_calls": 2,
                    "ai_planning_calls": 1,
                    "ai_click_selection_calls": 1,
                    "ai_review_calls": 0,
                },
            )

            _write_package_metadata(result=result, asset_dir=asset_dir)
            payload = json.loads((asset_dir / "metadata.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["execution_trace"]["capture_stage"], "waiting_for_window")
        self.assertEqual(payload["execution_trace"]["ocr_calls"], 2)
        self.assertEqual(payload["execution_trace"]["ai_planning_calls"], 1)

    def test_write_capture_reports_includes_trace_columns(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            result = CapturePackageResult(
                row_id=1,
                package_path=Path("/tmp/demo.deb"),
                pkg_name="demo-app",
                pkg_version="1.0.0",
                pkg_arch="amd64",
                status="captured",
                message="captured 2 screenshot(s)",
                asset_dir=output_dir / "demo-app" / "1.0.0-amd64",
                execution_trace={
                    "capture_stage": "completed",
                    "ocr_calls": 6,
                    "ai_planning_calls": 1,
                    "ai_click_selection_calls": 2,
                    "ai_review_calls": 3,
                },
            )

            _write_capture_reports(output_dir=output_dir, results=[result])
            payload = json.loads((output_dir / "capture-report.json").read_text(encoding="utf-8"))

        self.assertEqual(payload[0]["capture_stage"], "completed")
        self.assertEqual(payload[0]["ocr_calls"], 6)
        self.assertEqual(payload[0]["ai_click_selection_calls"], 2)


if __name__ == "__main__":
    unittest.main()
