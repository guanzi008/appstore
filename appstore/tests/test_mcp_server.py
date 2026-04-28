from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from appstore.capabilities import write_capability_cache
from appstore import mcp_server
from appstore.models import BaselineOption, CapabilityCache, SystemLine


class MCPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.package_path = self.repo_root / "appstore" / "examples" / "packages" / "labelnova_1.0.4-1_amd64.deb"

    def _start_server(self) -> subprocess.Popen[bytes]:
        env = os.environ.copy()
        python_path_entries = [str(self.repo_root)]
        existing_python_path = env.get("PYTHONPATH", "").strip()
        if existing_python_path:
            python_path_entries.append(existing_python_path)
        env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
        return subprocess.Popen(
            [sys.executable, "-m", "appstore.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def _request(self, process: subprocess.Popen[bytes], payload: dict) -> dict:
        assert process.stdin is not None
        assert process.stdout is not None

        process.stdin.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        process.stdin.flush()

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            events = selector.select(timeout=5.0)
        finally:
            selector.close()
        if not events:
            self.fail(f"Timed out waiting for MCP response. returncode={process.poll()}")

        raw = process.stdout.readline()
        self.assertTrue(raw, "MCP server returned EOF")
        return json.loads(raw.decode("utf-8"))

    def _initialize(self, process: subprocess.Popen[bytes]) -> None:
        init_response = self._request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "unittest", "version": "1.0"},
                },
            },
        )
        self.assertEqual(init_response["result"]["serverInfo"]["name"], "appstore-mcp")
        assert process.stdin is not None
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                },
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )
        process.stdin.flush()

    def _stop_server(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    def test_tools_list_and_inspect_packages(self) -> None:
        process = self._start_server()
        try:
            self._initialize(process)

            tools_response = self._request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
            )
            tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}
            self.assertIn("inspect_packages", tool_names)
            self.assertIn("prepare_new_app_workbook", tool_names)
            self.assertIn("generate_example_template", tool_names)
            self.assertIn("upload_packages", tool_names)
            self.assertIn("auto_upload_packages", tool_names)

            inspect_response = self._request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "inspect_packages",
                        "arguments": {
                            "packages": [str(self.package_path)],
                        },
                    },
                },
            )
            payload = inspect_response["result"]["structuredContent"]
            self.assertEqual(payload["package_count"], 1)
            self.assertEqual(payload["packages"][0]["pkg_name"], "labelnova")
            self.assertEqual(payload["packages"][0]["pkg_arch"], "amd64")
        finally:
            self._stop_server(process)

    def test_validate_workbook_tool_runs_cli_wrapper(self) -> None:
        process = self._start_server()
        try:
            self._initialize(process)
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_root = Path(temp_dir)
                cache_dir = temp_root / "cache"
                output_path = temp_root / "template.xlsx"
                cache = CapabilityCache(
                    generated_at="2026-04-23T12:00:00+08:00",
                    deb_system_lines={
                        "11": SystemLine(code="11", label="communityV23", family="deb"),
                        "21": SystemLine(code="21", label="communityV25", family="deb"),
                    },
                    linglong_system_lines={},
                    baseline_options={
                        "deb:11": (BaselineOption(system_line_code="11", baseline_id="2300", minor_version="23.0.0"),),
                        "deb:21": (BaselineOption(system_line_code="21", baseline_id="2500", minor_version="25.0.0"),),
                    },
                )
                write_capability_cache(cache_dir, cache)

                generate_response = self._request(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {
                            "name": "generate_example_template",
                            "arguments": {
                                "output_path": str(output_path),
                                "capabilities_cache": str(cache_dir),
                            },
                        },
                    },
                )
                self.assertTrue(output_path.exists())
                self.assertEqual(
                    generate_response["result"]["structuredContent"]["output_path"],
                    str(output_path),
                )

                validate_response = self._request(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "tools/call",
                        "params": {
                            "name": "validate_workbook",
                            "arguments": {
                                "workbook": str(output_path),
                                "capabilities_cache": str(cache_dir),
                            },
                        },
                    },
                )
                payload = validate_response["result"]["structuredContent"]
                self.assertEqual(payload["exit_code"], 0)
                self.assertEqual(payload["status_counts"], {"validated": 3})
                self.assertTrue(Path(payload["report_path"]).exists())
        finally:
            self._stop_server(process)

    def test_capture_packages_rejects_unknown_arguments(self) -> None:
        process = self._start_server()
        try:
            self._initialize(process)
            response = self._request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "capture_packages",
                        "arguments": {
                            "packages": [str(self.package_path)],
                            "unexpected_flag": 1,
                        },
                    },
                },
            )
            self.assertIn("error", response)
            self.assertIn("unsupported field", response["error"]["message"])
        finally:
            self._stop_server(process)

    def test_capture_packages_rejects_disabling_ocr_for_auto_capture(self) -> None:
        process = self._start_server()
        try:
            self._initialize(process)
            response = self._request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {
                        "name": "capture_packages",
                        "arguments": {
                            "packages": [str(self.package_path)],
                            "ocr_backend": "off",
                        },
                    },
                },
            )
            self.assertIn("error", response)
            self.assertIn("does not allow ocr_backend=off", response["error"]["message"])
        finally:
            self._stop_server(process)

    def test_prepare_new_app_workbook_tool_uses_real_packages(self) -> None:
        process = self._start_server()
        try:
            self._initialize(process)
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_root = Path(temp_dir)
                amd64 = temp_root / "labelnova_1.0.4-1_amd64.deb"
                arm64 = temp_root / "labelnova_1.0.4-1_arm64.deb"
                shutil.copy2(self.repo_root / "appstore" / "examples" / "packages" / amd64.name, amd64)
                shutil.copy2(self.repo_root / "appstore" / "examples" / "packages" / arm64.name, arm64)
                (temp_root / "assets").mkdir(parents=True, exist_ok=True)
                (temp_root / "screenshots").mkdir(parents=True, exist_ok=True)
                (temp_root / "assets" / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 2048)
                for index in range(1, 4):
                    (temp_root / "screenshots" / f"screenshot_{index}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 2048)

                cache = CapabilityCache(
                    generated_at="2026-04-23T12:00:00+08:00",
                    deb_system_lines={
                        "11": SystemLine(code="11", label="communityV23", family="deb"),
                    },
                    linglong_system_lines={},
                    baseline_options={
                        "deb:11": (BaselineOption(system_line_code="11", baseline_id="2300", minor_version="23.0.0"),),
                    },
                )
                cache_dir = temp_root / "cache"
                write_capability_cache(cache_dir, cache)

                output_path = temp_root / "labelnova-submission.xlsx"
                response = self._request(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "tools/call",
                        "params": {
                            "name": "prepare_new_app_workbook",
                            "arguments": {
                                "packages": [str(amd64), str(arm64)],
                                "output_path": str(output_path),
                                "capabilities_cache": str(cache_dir),
                                "app_name_zh": "标签打印工具",
                                "short_desc_zh": "真实包模板",
                                "full_desc_zh": "使用真实包路径生成的新应用 workbook。",
                                "website": "https://example.invalid/labelnova-real",
                                "keywords_zh": "标签,打印",
                                "system_line_codes": ["11"],
                            },
                        },
                    },
                )
                payload = response["result"]["structuredContent"]
                self.assertEqual(payload["pkg_name"], "labelnova")
                self.assertEqual(payload["packages"], [str(amd64), str(arm64)])
                self.assertEqual(payload["selected_system_line_codes"], ["11"])
                self.assertEqual(payload["missing_fields"], [])
                self.assertTrue(output_path.exists())
        finally:
            self._stop_server(process)

    def test_launcher_script_exists_and_is_executable(self) -> None:
        launcher = self.repo_root / "scripts" / "appstore-mcp"
        self.assertTrue(launcher.exists())
        self.assertTrue(os.access(launcher, os.X_OK))

    def test_start_capture_packages_creates_background_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            with patch.object(mcp_server, "DEFAULT_JOB_ROOT", temp_root / "jobs"), patch.object(
                mcp_server, "DEFAULT_OUTPUT_ROOT", temp_root / "output"
            ), patch("appstore.mcp_server.subprocess.Popen", return_value=SimpleNamespace(pid=4321)):
                response = mcp_server._tool_start_capture_packages(  # type: ignore[attr-defined]
                    {
                        "packages": [str(self.package_path)],
                    }
                )

            payload = response["structuredContent"]
            self.assertEqual(payload["status"], "queued")
            self.assertTrue(str(payload["job_id"]).startswith("capture-"))
            state_path = Path(payload["job_dir"]) / "state.json"
            self.assertTrue(state_path.exists())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["pid"], 4321)

    def test_get_job_status_returns_completed_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            job_root = temp_root / "jobs"
            job_id = "capture-demo123"
            job_dir = job_root / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "state.json").write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "job_type": "capture",
                        "status": "completed",
                        "message": "job completed",
                        "created_at": "2026-04-24T00:00:00Z",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (job_dir / "result.json").write_text(
                json.dumps(
                    {
                        "report": [
                            {
                                "pkg_name": "labelnova",
                                "pkg_arch": "amd64",
                                "status": "captured",
                                "message": "captured 3 screenshot(s)",
                            }
                        ],
                        "capture_details": [
                            {
                                "pkg_name": "labelnova",
                                "pkg_arch": "amd64",
                                "status": "captured",
                                "screenshots": ["/tmp/1.png"],
                                "rejected_screenshots": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch.object(mcp_server, "DEFAULT_JOB_ROOT", job_root):
                response = mcp_server._tool_get_job_status({"job_id": job_id})  # type: ignore[attr-defined]

            payload = response["structuredContent"]
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["report"][0]["status"], "captured")
            self.assertEqual(payload["capture_details"][0]["pkg_name"], "labelnova")
