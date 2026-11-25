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
from unittest.mock import Mock, patch

from openpyxl import load_workbook

from appstore.appstore_client import AppStoreClient
from appstore.capabilities import write_capability_cache
from appstore import mcp_server
from appstore.models import BaselineOption, CapabilityCache, StoreAdaptOption, SystemLine, UploadedFileRef
from appstore.tests.test_linglong import _write_native_layer, _write_native_uab
from appstore.upload_batch import _build_direct_packages, _normalize_direct_targets, _select_capture_package_path
from ui.backend import LoginContext
from ui.preferences import PreferenceStore, UIPreferences


class MCPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.package_path = self.repo_root / "appstore" / "examples" / "packages" / "labelnova_1.0.4-1_amd64.deb"

    def _start_server(self, *, preferences_path: Path | None = None) -> subprocess.Popen[bytes]:
        env = os.environ.copy()
        python_path_entries = [str(self.repo_root)]
        existing_python_path = env.get("PYTHONPATH", "").strip()
        if existing_python_path:
            python_path_entries.append(existing_python_path)
        env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
        argv = [sys.executable, "-m", "appstore.mcp_server"]
        if preferences_path is not None:
            env["UTPUBLISHER_PREFERENCES_PATH"] = str(preferences_path)
            argv = [sys.executable, "-m", "ui.cpp_bridge"]
        return subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def _request(self, process: subprocess.Popen[bytes], payload: dict | list, *, length_prefixed: bool = False) -> dict | list:
        assert process.stdin is not None
        assert process.stdout is not None

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if length_prefixed:
            process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        else:
            process.stdin.write(body + b"\n")
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
        if length_prefixed:
            self.assertTrue(raw.startswith(b"Content-Length:"), raw)
            length = int(raw.partition(b":")[2])
            self.assertEqual(process.stdout.readline(), b"\r\n")
            raw = process.stdout.read(length)
        return json.loads(raw.decode("utf-8"))

    def _initialize(self, process: subprocess.Popen[bytes], *, version: str = "2025-11-25") -> None:
        init_response = self._request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "unittest", "version": "1.0"},
                },
            },
        )
        self.assertEqual(init_response["result"]["serverInfo"]["name"], "appstore-mcp")
        self.assertEqual(init_response["result"]["protocolVersion"], version)
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
            self.assertEqual(len(tool_names), 20)
            self.assertTrue({
                "list_my_apps", "get_app_workflow", "get_app_history_notes", "prepare_app_update",
                "preprocess_assets", "submit_app_update", "delete_draft",
            }.issubset(tool_names))
            annotations = {tool["name"]: tool.get("annotations", {}) for tool in tools_response["result"]["tools"]}
            self.assertTrue(annotations["get_app_history_notes"]["readOnlyHint"])
            self.assertTrue(annotations["delete_draft"]["destructiveHint"])

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

    def test_legacy_revision_receives_json_text_tool_results(self) -> None:
        process = self._start_server()
        try:
            self._initialize(process, version="2024-11-05")
            response = self._request(process, {
                "jsonrpc": "2.0", "id": "inspect", "method": "tools/call",
                "params": {"name": "inspect_packages", "arguments": {"packages": [str(self.package_path)]}},
            })
            result = response["result"]
            self.assertEqual(response["id"], "inspect")
            self.assertNotIn("structuredContent", result)
            self.assertEqual(json.loads(result["content"][0]["text"])["packages"][0]["pkg_name"], "labelnova")
        finally:
            self._stop_server(process)

    def test_length_prefixed_replies_use_utf8_independent_of_stdio_locale(self) -> None:
        with patch.dict(os.environ, {"PYTHONIOENCODING": "ascii"}):
            process = self._start_server()
        try:
            handshake = self._request(process, {
                "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"},
            }, length_prefixed=True)
            self.assertEqual(handshake["result"]["protocolVersion"], "2025-11-25")
            response = self._request(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, length_prefixed=True)
            self.assertIn("解析", response["result"]["tools"][0]["description"])
        finally:
            self._stop_server(process)

    def test_march_2025_revision_receives_json_rpc_batches(self) -> None:
        process = self._start_server()
        try:
            self._initialize(process, version="2025-03-26")
            response = self._request(process, [
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
                {"jsonrpc": "2.0", "method": "notifications/cancelled"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            ])
            self.assertEqual([item["id"] for item in response], [2, 3])
            self.assertIn("tools", response[1]["result"])
        finally:
            self._stop_server(process)

    def test_modern_discovery_and_calls_use_per_request_metadata(self) -> None:
        process = self._start_server()
        try:
            metadata = {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            }
            result = self._request(process, {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"_meta": metadata, "name": "inspect_packages", "arguments": {"packages": [str(self.package_path)]}},
            })["result"]
            self.assertEqual(result["resultType"], "complete")
            self.assertEqual(result["structuredContent"]["package_count"], 1)
            self.assertEqual(json.loads(result["content"][-1]["text"]), result["structuredContent"])

            discovery = self._request(process, {
                "jsonrpc": "2.0", "id": 2, "method": "server/discover", "params": {"_meta": metadata},
            })["result"]
            self.assertIn("2026-07-28", discovery["supportedVersions"])
            self.assertEqual(discovery["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "appstore-mcp")
            tools = self._request(process, {
                "jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"_meta": metadata},
            })["result"]
            self.assertEqual((tools["ttlMs"], tools["cacheScope"]), (0, "private"))
            metadata["io.modelcontextprotocol/protocolVersion"] = "2099-01-01"
            unsupported = self._request(process, {
                "jsonrpc": "2.0", "id": 4, "method": "server/discover", "params": {"_meta": metadata},
            })["error"]
            self.assertEqual(unsupported["code"], -32022)
            self.assertIn("2025-11-25", unsupported["data"]["supported"])
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

    def test_desktop_preferences_control_managed_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            store = PreferenceStore(temp_root / "preferences.json")
            output_root = temp_root / "reports"
            cache_dir = temp_root / "capabilities"
            write_capability_cache(cache_dir, CapabilityCache(
                generated_at="2026-11-25T00:00:00+08:00",
                deb_system_lines={"11": SystemLine("11", "communityV23", "deb")},
                linglong_system_lines={},
                baseline_options={},
            ))
            store.save(UIPreferences(mcp_output_dir=str(output_root)))
            with patch.dict(os.environ, {"UTPUBLISHER_CAPABILITY_CACHE_DIR": str(cache_dir)}):
                process = self._start_server(preferences_path=store.path)
            try:
                disabled = self._request(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
                self.assertIn("MCP 已关闭", disabled["error"]["message"])
                store.save(UIPreferences(mcp_enabled=True, mcp_output_dir=str(output_root)))
                self._initialize(process)

                tools_request = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
                available = {tool["name"] for tool in self._request(process, tools_request)["result"]["tools"]}
                self.assertIn("get_capabilities", available)
                self.assertIn("prepare_app_update", available)
                self.assertNotIn("upload_packages", available)
                self.assertNotIn("submit_app_update", available)
                self.assertNotIn("delete_draft", available)

                denied = self._request(process, {
                    "jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "upload_packages"},
                })
                self.assertIn("允许投递", denied["error"]["message"])

                workbook = self._request(process, {
                    "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {"name": "prepare_new_app_workbook", "arguments": {"packages": [str(self.package_path)]}},
                })
                path = Path(workbook["result"]["structuredContent"]["output_path"])
                self.assertTrue(path.is_relative_to(output_root))
                self.assertTrue(path.is_file())

                store.save(UIPreferences(mcp_enabled=True, mcp_allow_actions=True))
                available = {tool["name"] for tool in self._request(process, tools_request)["result"]["tools"]}
                self.assertIn("upload_packages", available)
                self.assertIn("submit_app_update", available)
                self.assertIn("delete_draft", available)
                store.save(UIPreferences())
                self.assertIn("error", self._request(process, tools_request))
            finally:
                self._stop_server(process)

    def test_store_tools_page_apps_and_load_original_revision_notes(self) -> None:
        client = Mock()
        client.list_apps_paged.return_value = {
            "total": 4, "rows": [{"id": "draft-a", "app_id": "app-a", "app_name": "测试应用", "status": -99}],
        }
        client.get_app_history.return_value = {"total": 4, "rows": [
            {"id": "revision-2", "pkg_version": "1.0,1.0"}, {"id": "revision-1", "pkg_version": "1.0"},
        ]}
        client.get_app_detail.return_value = {"app_lan_infos": [{
            "lan": "zh_CN", "update_desc": "原通用说明", "pkgUptDesc": [
                {"pkgArchVersion": "loongarch64 1.0,loong64 1.0", "uptDesc": "原按包说明\n"},
            ],
        }]}
        login = LoginContext(client, "fixture", None, "cached", False)
        with patch("appstore.mcp_server._store_login", return_value=login):
            apps = mcp_server._tool_list_my_apps({"keyword": "测试", "page_num": 2})["structuredContent"]
            history = mcp_server._tool_get_app_workflow({"app_id": "app-a", "page_num": 2})["structuredContent"]["history"]
            notes = mcp_server._tool_get_app_history_notes({"detail_id": "revision-1"})["structuredContent"]["notes"]

        client.list_apps_paged.assert_called_once_with(page_num=2, page_size=20, name_or_app_id="测试")
        client.report_my_apps.assert_not_called()
        self.assertTrue(apps["rows"][0]["is_draft"])
        self.assertEqual([(row["detail_id"], row["pkg_version"]) for row in history["rows"]], [("revision-2", "1.0"), ("revision-1", "1.0")])
        client.get_app_detail.assert_called_once_with("revision-1")
        self.assertEqual(notes[0]["packages"], ("loongarch64 1.0", "loong64 1.0"))
        self.assertEqual([note["text"] for note in notes], ["原按包说明\n", "原通用说明"])

    def test_draft_update_tool_reuses_the_revision_endpoint_and_keeps_both_loong_architectures(self) -> None:
        cache = CapabilityCache(
            generated_at="2026-11-25T00:00:00+08:00", deb_system_lines={},
            linglong_system_lines={"2": SystemLine("2", "专业版 V20", "linglong")},
            baseline_options={"linglong:2": (BaselineOption("2", "128", "1075"),)},
        )
        client = Mock()
        client.get_app_detail.return_value = {
            "app_basic_info": {"id": "draft-a", "app_id": "app-a", "status": -99, "category_id": 1, "region": "1"},
            "app_lan_infos": [{"lan": "zh_CN", "name": "测试应用", "update_desc": "保留通用说明"}],
            "app_origin_pkgs": [{
                "id": key, "pkg_name": "org.example.demo", "pkg_version": "1.0.0.0", "pkg_arch": arch,
                "pkgType": 22, "supSys": "2", "supBlineVer": "128", "file_save_key": key,
            } for key, arch in (("arm-row", "3"), ("old-loong-row", "5"), ("new-loong-row", "6"))],
        }
        client.upload_file_bytes.return_value = UploadedFileRef("temppkg", "replacement-file", 22, "hash")
        client.update_draft.return_value = {"datas": {"app_id": "app-a"}}
        login = LoginContext(client, "fixture", None, "cached", False)
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "replacement.uab"
            _write_native_uab(package, [{"info": {
                "kind": "app", "id": "org.example.demo", "version": "2.0.0.0", "arch": ["loongarch64"],
            }}])
            request = {"detail_id": "draft-a", "packages": [str(package)], "remove_package_ids": ["arm-row"],
                       "metadata": {"note_zh": "只更新旧世界安装包"}}
            with (
                patch("appstore.mcp_server._store_login", return_value=login),
                patch("ui.backend.load_or_sync_capabilities", return_value=cache),
                patch("ui.cpp_bridge._extract_initial_icon", return_value=None),
                patch("ui.cpp_bridge._extract_package_icon", return_value=None),
            ):
                preview = mcp_server._tool_prepare_app_update(request)["structuredContent"]
                client.update_draft.assert_not_called()
                self.assertEqual({p["arch"] for p in preview["packages"]}, {"loongarch64", "loong64"})
                self.assertEqual(preview["packages_without_targets"], [])
                for operation, operate_type in (("save_draft", -99), ("submit_draft", 52)):
                    result = mcp_server._tool_submit_app_update({**request, "operation": operation, "output_dir": directory})
                    self.assertFalse(result["isError"], result)
                    revision, payload = client.update_draft.call_args.args
                    self.assertEqual((revision, payload["operate_type"]), ("draft-a", operate_type))
                    detail = payload["app_info"]
                    files = {row["id"]: row["file_save_key"] for row in detail["app_origin_pkgs"]}
                    self.assertEqual(files, {"old-loong-row": "replacement-file", "new-loong-row": "new-loong-row"})
                    zh = next(row for row in detail["app_lan_infos"] if row["lan"] == "zh_CN")
                    self.assertEqual(zh["update_desc"], "保留通用说明")
                    self.assertEqual(zh["pkgUptDesc"][0]["uptDesc"], "只更新旧世界安装包")
        client.submit_payload.assert_not_called()

    def test_published_metadata_and_target_update_reuses_the_online_package(self) -> None:
        cache = CapabilityCache(
            generated_at="2026-11-25T00:00:00+08:00", linglong_system_lines={},
            deb_system_lines={"2": SystemLine("2", "专业版 V20", "deb")},
            baseline_options={"deb:2": (BaselineOption("2", "114", "1073"), BaselineOption("2", "121", "1074"))},
        )
        client = Mock()
        client.get_app_detail.return_value = {
            "app_basic_info": {"id": "revision-a", "app_id": "app-a", "status": 101, "category_id": 1, "region": "1"},
            "app_lan_infos": [{"lan": "zh_CN", "name": "测试应用", "brief_info": "旧简介"}],
            "app_origin_pkgs": [{
                "id": "x86-row", "pkg_name": "demo", "pkg_version": "2.0", "pkg_arch": "4",
                "pkgType": 11, "supSys": "2", "supBlineVer": "114", "file_save_key": "online-file",
            }],
        }
        client.submit_payload.return_value = {"datas": {"app_id": "app-a"}}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("appstore.mcp_server._store_login", return_value=LoginContext(client, "fixture", None, "cached", False)),
            patch("ui.backend.load_or_sync_capabilities", return_value=cache),
        ):
            preview = mcp_server._tool_prepare_app_update({"detail_id": "revision-a"})["structuredContent"]
            result = mcp_server._tool_submit_app_update({
                "detail_id": "revision-a", "operation": "submit_update", "output_dir": directory,
                "metadata": {"short_desc_zh": "新简介"}, "targets": [{
                    "package_path": preview["packages"][0]["path"], "system_line_code": "2", "selected": True,
                    "baseline_ids": ["121"],
                }],
            })
        self.assertFalse(result["isError"], result)
        payload, = client.submit_payload.call_args.args
        self.assertEqual(payload["app_id"], "app-a")
        self.assertEqual(payload["app_info"]["app_lan_infos"][0]["brief_info"], "新简介")
        package, = payload["app_info"]["app_origin_pkgs"]
        self.assertEqual(package["file_save_key"], "online-file")
        self.assertEqual(package["baseline"], [{"system_platform": 2, "id": "121"}])
        client.upload_file_bytes.assert_not_called()
        client.update_draft.assert_not_called()

    def test_delete_draft_tool_checks_the_current_revision_state(self) -> None:
        session = Mock()
        client = AppStoreClient(session)
        login = LoginContext(client, "fixture", None, "cached", False)
        with (
            patch("appstore.mcp_server._store_login", return_value=login),
            patch.object(client, "get_app_detail", return_value={"app_basic_info": {"id": "revision-a", "status": 101}}) as detail,
            patch.object(client, "_ensure_ok", return_value={}),
        ):
            with self.assertRaisesRegex(RuntimeError, "草稿状态已变化"):
                mcp_server._tool_delete_draft({"detail_id": "revision-a"})
            session.delete.assert_not_called()
            detail.return_value["app_basic_info"]["status"] = -99
            result = mcp_server._tool_delete_draft({"detail_id": "revision-a"})
        self.assertEqual(result["structuredContent"]["detail_id"], "revision-a")
        self.assertTrue(session.delete.call_args.args[0].endswith("/revision-a"))

    def test_store_login_does_not_switch_from_requested_account(self) -> None:
        with patch("ui.backend.try_restore_cached_login", return_value=None) as restore:
            with self.assertRaisesRegex(mcp_server.MCPError, "未找到有效的商店登录态"):
                mcp_server._store_login({"account": "selected-account"})
        restore.assert_called_once_with(
            "selected-account", session_cache_dir=mcp_server.DEFAULT_SESSION_CACHE_DIR, allow_fallback=False,
        )

    def test_linglong_loong_architectures_get_distinct_system_lines_and_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            old_package = directory / "demo.layer"
            new_package = directory / "demo.uab"
            info = {"kind": "app", "id": "org.example.demo", "version": "1.2.3"}
            _write_native_layer(old_package, {**info, "arch": ["loongarch64"]})
            _write_native_uab(new_package, [{"info": {**info, "arch": ["loong64"]}}])

            cache = CapabilityCache(
                generated_at="2026-11-25T00:00:00+08:00",
                deb_system_lines={},
                linglong_system_lines={
                    "2": SystemLine("2", "professionalV20", "linglong"),
                    "21": SystemLine("21", "communityV25", "linglong"),
                },
                arch_options={
                    "5": StoreAdaptOption("5", "loongarch64", system_line_codes=("2",)),
                    "6": StoreAdaptOption("6", "loong64", system_line_codes=("21",)),
                },
                baseline_options={
                    "linglong:2": (BaselineOption("2", "114", "1073"), BaselineOption("2", "128", "1075")),
                },
            )
            cache_path = write_capability_cache(directory / "cache", cache)
            paths = [old_package, new_package]
            package_args = {"packages": [str(path) for path in paths]}
            inspected = mcp_server._tool_inspect_packages(package_args)["structuredContent"]["packages"]
            self.assertEqual([(item["package_format"], item["architecture"], item["arch_code"]) for item in inspected], [
                ("layer", "loongarch64", "5"), ("uab", "loong64", "6"),
            ])

            capabilities = mcp_server._tool_get_capabilities({
                "package_family": "linglong", "architecture": "loongarch64", "cache_dir": str(cache_path),
            })["structuredContent"]
            line, = capabilities["system_lines"]
            self.assertEqual(line["code"], "2")
            self.assertEqual([(item["version"], item["selectable"]) for item in line["baselines"]], [("1073", False), ("1075", True)])

            workbook_path = directory / "submission.xlsx"
            workbook_args = {
                **package_args, "output_path": str(workbook_path), "capabilities_cache": str(cache_path),
                "system_line_codes": ["2", "21"],
            }
            prepared = mcp_server._tool_prepare_new_app_workbook(workbook_args)["structuredContent"]
            self.assertEqual(prepared["selected_system_line_codes"], ["2", "21"])
            workbook = load_workbook(workbook_path, read_only=True)
            try:
                headers, old_row, new_row = workbook["packages"].values
                old_values = dict(zip(headers, old_row))
                new_values = dict(zip(headers, new_row))
                self.assertEqual(old_values["sys__linglong__2__baseline"], "128")
                self.assertIsNone(old_values["sys__linglong__21__enabled"])
                self.assertIsNone(new_values["sys__linglong__2__enabled"])
                self.assertEqual(new_values["sys__linglong__21__enabled"], "Y")
            finally:
                workbook.close()
            partial = mcp_server._tool_prepare_new_app_workbook({**workbook_args, "system_line_codes": ["21"]})["structuredContent"]
            self.assertIn("system_line_codes:loongarch64", partial["missing_fields"])

            _, records, infos = _build_direct_packages(package_paths=paths, release_key="stable", pkg_channel="stable")
            self.assertEqual(len(infos), 2)
            by_path = {record.file_path: infos[record.package_key] for record in records}
            with patch("appstore.upload_batch._detect_host_arch", return_value="loongarch64"):
                self.assertEqual(_select_capture_package_path(package_paths=paths, package_infos=by_path), old_package)
            with patch("appstore.upload_batch._detect_host_arch", return_value="loong64"):
                self.assertEqual(_select_capture_package_path(package_paths=paths, package_infos=by_path), new_package)
            old_target, = _normalize_direct_targets(
                package_record=records[0], package_info=by_path[old_package], existing_app_detail={}, capability_cache=cache,
            )
            self.assertEqual((old_target.sup_sys_code, old_target.baseline_id), ("2", "128"))

    def test_auto_upload_reports_failures_without_returning_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            response = mcp_server._tool_auto_upload_packages({
                "packages": [str(self.package_path)], "output_dir": directory,
                "username": "demo", "password": "test-password", "min_screenshots": 0,
            })
            result = response["structuredContent"]
            self.assertTrue(response["isError"])
            self.assertEqual(result["status_counts"], {"capture_failed": 1})
            self.assertEqual(Path(result["report_path"]), Path(directory) / "upload" / "report.json")
            self.assertNotIn("test-password", json.dumps(response))

    def test_start_capture_packages_creates_background_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            with patch.object(mcp_server, "DEFAULT_JOB_ROOT", temp_root / "jobs"), patch.object(
                mcp_server, "DEFAULT_OUTPUT_ROOT", temp_root / "output"
            ), patch("appstore.mcp_server.subprocess.Popen", return_value=Mock(wait=Mock(return_value=0))) as popen:
                response = mcp_server._tool_start_capture_packages(  # type: ignore[attr-defined]
                    {
                        "packages": [str(self.package_path)],
                        "sudo_password": "test-sudo-password",
                    }
                )

            payload = response["structuredContent"]
            self.assertEqual(payload["status"], "queued")
            self.assertTrue(str(payload["job_id"]).startswith("capture-"))
            state_path = Path(payload["job_dir"]) / "state.json"
            self.assertTrue(state_path.exists())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "queued")
            request = state_path.with_name("request.json")
            self.assertNotIn("test-sudo-password", request.read_text(encoding="utf-8"))
            self.assertEqual(request.stat().st_mode & 0o777, 0o600)
            worker_env = popen.call_args.kwargs["env"]
            self.assertEqual(json.loads(worker_env["APPSTORE_MCP_JOB_SECRETS"])["sudo_password"], "test-sudo-password")

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
