import json
import subprocess
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, call, patch

from appstore.appstore_client import build_requests_session
from appstore.models import BaselineOption, CapabilityCache, StoreAdaptOption, SystemLine, UploadedFileRef
from appstore.capture_workflow import CapturePackageResult
from appstore.session_state import BrowserSessionState, SessionStateStore
from ui.assets import AssetBundle, _prepare_icon, _prepare_screenshot, detect_asset_candidates, preprocess_assets
from ui.backend import (
    BatchGroupSubmissionPlan,
    LoginContext,
    StoreAppMatch,
    SystemTargetOption,
    _build_localized_lan_texts,
    _submit_grouped_release,
    build_existing_detail_editor_defaults,
    build_target_options,
    capture_screenshots_for_group,
    login_with_credentials,
    submit_applications_batch,
    submit_existing_application,
    submit_existing_applications_batch,
    try_restore_cached_login,
)
from ui.package_meta import PackageGroup, PackageMetadata
from ui.preferences import DefaultTargetRange, PreferenceStore, UIPreferences
from ui.cpp_bridge import (
    _capability_cache_to_json,
    _group_payload_to_package_group,
    _group_payload_to_plan,
    _group_payload_to_targets,
    _group_to_json,
    _online_group_from_detail,
    _refresh_group_system_lines,
    _sync_existing_detail_assets,
    _targets_for_auto_matched_packages,
    command_analyze,
    command_get_app_history_notes,
    command_get_app_workflow,
    command_login_credentials,
    command_save_preferences,
)


class _CredentialClient:
    def __init__(self, session=None) -> None:
        self.session = session

    def login(self, username: str, password: str):
        self.session = build_requests_session(
            cookies=[{"name": "access-token", "value": "abc", "domain": "appstore-dev.uniontech.com", "path": "/"}],
        )
        return {"dev_name": "odatacc"}


class _CachedClient:
    def __init__(self, session=None) -> None:
        self.session = session

    def fetch_dev_info(self):
        return {"dev_name": "odatacc"}


class _InvalidCachedClient:
    def __init__(self, session=None) -> None:
        self.session = session

    def fetch_dev_info(self):
        raise RuntimeError("expired")


class _NoUploadSubmitClient:
    def __init__(self) -> None:
        self.payload = None

    def upload_file_bytes(self, **_kwargs):
        raise AssertionError("online-only updates must not upload package files")

    def submit_payload(self, payload: dict):
        self.payload = payload
        return {"ok": True, "datas": {"app_id": "42"}}


class _DetailClient:
    def __init__(self) -> None:
        self.session = object()


def _solid_png_bytes(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum)
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum & 0xFFFFFFFF)

    scanline = b"\x00" + bytes(color) * width
    raw = scanline * height
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class UiBackendCacheTests(unittest.TestCase):
    @staticmethod
    def _write_test_image(path: Path, width: int, height: int) -> None:
        path.write_bytes(_solid_png_bytes(width, height, (70, 120, 180)))

    @staticmethod
    def _png_size(path: Path) -> tuple[int, int]:
        data = path.read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError(f"not a PNG image: {path}")
        return struct.unpack(">II", data[16:24])

    @staticmethod
    def _rgb_pixel(path: Path, x: int, y: int, width: int) -> tuple[int, int, int]:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-frames:v",
                "1",
                "-",
            ],
            capture_output=True,
            check=True,
        )
        offset = (y * width + x) * 3
        return tuple(completed.stdout[offset : offset + 3])

    def test_prepare_screenshot_normalizes_landscape_to_store_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "wide.png"
            self._write_test_image(source, 1920, 1080)

            result = _prepare_screenshot(source, root / "out" / "screen")
            width, height = self._png_size(result)

            self.assertEqual((width, height), (1920, 1280))
            self.assertEqual(width * 2, height * 3)
            self.assertEqual(self._rgb_pixel(result, 0, 0, width), (0, 0, 0))
            self.assertEqual(self._rgb_pixel(result, width // 2, height // 2, width), (70, 120, 180))
            self.assertTrue(result.exists())

    def test_prepare_screenshot_normalizes_portrait_to_store_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "portrait.png"
            self._write_test_image(source, 1000, 2000)

            result = _prepare_screenshot(source, root / "out" / "screen")
            width, height = self._png_size(result)

            self.assertEqual((width, height), (900, 1600))
            self.assertEqual(width * 16, height * 9)
            self.assertEqual(self._rgb_pixel(result, 0, 0, width), (0, 0, 0))
            self.assertEqual(self._rgb_pixel(result, width // 2, height // 2, width), (70, 120, 180))
            self.assertTrue(result.exists())

    def test_prepare_icon_normalizes_without_qt_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "icon.png"
            self._write_test_image(source, 300, 120)

            result = _prepare_icon(source, root / "out" / "icon.png")

            self.assertEqual(self._png_size(result), (512, 512))
            self.assertTrue(result.exists())

    def test_preprocess_preserves_valid_screenshots_when_the_icon_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = _make_package_group(root / "fixture.deb", pkg_name="fixture")
            icon = root / "icon.png"
            self._write_test_image(icon, 32, 32)
            self.assertLess(icon.stat().st_size, 1024)
            self.assertEqual(detect_asset_candidates(package, asset_dir=root)[0], icon)
            icon.write_bytes(b"corrupt icon")
            screenshots = root / "screenshots"
            screenshots.mkdir()
            for dimensions in ("360x640", "1051x702"):
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc=s={dimensions}",
                     "-frames:v", "1", "-threads", "1", str(screenshots / f"{dimensions}.png")],
                    check=True,
                    capture_output=True,
                )

            bundle = preprocess_assets(package, output_dir=root / "processed", asset_dir=root)
            self.assertIsNone(bundle.icon_path)
            self.assertIn("图标处理失败", bundle.warnings[0])
            self.assertCountEqual(map(self._png_size, bundle.screenshot_paths), [(360, 640), (1056, 704)])

            cleared = preprocess_assets(package, output_dir=root / "processed", asset_dir=root, manual_screenshot_paths=())
            self.assertEqual(cleared.screenshot_paths, ())

    def test_prepare_screenshot_uses_display_orientation_from_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "source.png"
            source = root / "rotation.jpg"
            self._write_test_image(original, 641, 439)
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(original), "-frames:v", "1", str(source)],
                capture_output=True,
                check=True,
            )
            # Add EXIF orientation 6 to represent a camera image displayed in portrait.
            exif = b"Exif\x00\x00II" + struct.pack("<HIHHHIHI", 42, 8, 1, 274, 3, 1, 6, 0)
            jpeg = source.read_bytes()
            source.write_bytes(jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:])

            self.assertEqual(self._png_size(_prepare_screenshot(source, root / "processed")), (450, 800))

    def test_login_with_credentials_saves_session_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "session-state"
            with patch("ui.backend.AppStoreClient", _CredentialClient):
                context = login_with_credentials("odatacc", "secret", session_cache_dir=cache_dir)

            self.assertIsNotNone(context.session_state_path)
            self.assertTrue(context.session_state_path.exists())
            cached = SessionStateStore(cache_dir).load("odatacc")
            self.assertIsNotNone(cached)
            self.assertEqual(cached.account, "odatacc")
            self.assertEqual(cached.cookies[0]["name"], "access-token")

    def test_try_restore_cached_login_reuses_valid_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "session-state"
            store = SessionStateStore(cache_dir)
            store.save(
                BrowserSessionState(
                    account="manual-login",
                    cookies=[{"name": "token", "value": "abc", "domain": ".uniontech.com", "path": "/"}],
                    local_storage={"authorization": "Bearer abc"},
                    session_storage={},
                    user_agent="Mozilla/5.0",
                    last_verified_at="2026-04-20T08:00:00",
                )
            )

            with patch("ui.backend.AppStoreClient", _CachedClient):
                context = try_restore_cached_login("manual-login", session_cache_dir=cache_dir)

            self.assertIsNotNone(context)
            self.assertEqual(context.login_mode, "cached")
            self.assertEqual(context.account_label, "odatacc")
            refreshed = store.load("manual-login")
            self.assertIsNotNone(refreshed)
            self.assertNotEqual(refreshed.last_verified_at, "2026-04-20T08:00:00")

    def test_try_restore_cached_login_invalidates_expired_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "session-state"
            store = SessionStateStore(cache_dir)
            store.save(
                BrowserSessionState(
                    account="manual-login",
                    cookies=[],
                    local_storage={},
                    session_storage={},
                    user_agent="Mozilla/5.0",
                    last_verified_at="2026-04-20T08:00:00",
                )
            )

            with patch("ui.backend.AppStoreClient", _InvalidCachedClient):
                context = try_restore_cached_login("manual-login", session_cache_dir=cache_dir)

            self.assertIsNone(context)
            self.assertIsNone(store.load("manual-login"))


class UiPreferencesTests(unittest.TestCase):
    def test_saves_specific_versions_and_preserves_legacy_system_line_defaults(self) -> None:
        ranges = {
            "deb:x86": {"2": {"baseline_id": "121", "unsupported_baseline_ids": ["128"]}},
            "linglong:x86": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            store = PreferenceStore(Path(directory) / "preferences.json")
            store.save(UIPreferences(default_target_system_lines={"x86": ("11",)}))
            with patch("ui.cpp_bridge.PreferenceStore", return_value=store):
                saved = command_save_preferences({"preferences": {
                    "default_target_system_lines": {"x86": ["11"]},
                    "default_target_ranges": ranges,
                }})["preferences"]
                command_save_preferences({"preferences": {"default_target_system_lines": {"x86": ["11"]}}})
            self.assertEqual(saved["default_target_ranges"], ranges)
            loaded = store.load()
            self.assertEqual(loaded.target_ranges("deb", "x86"), {"2": DefaultTargetRange("121", ("128",))})
            self.assertEqual(loaded.target_ranges("linglong", "x86"), {})

    def test_catalog_and_new_package_defaults_keep_each_professional_version(self) -> None:
        versions = (BaselineOption("2", "114", "1073"), BaselineOption("2", "121", "1074"), BaselineOption("2", "128", "1075"))
        cache = CapabilityCache(
            generated_at="2026-11-20T00:00:00+08:00",
            deb_system_lines={"2": SystemLine("2", "专业版", "deb")},
            linglong_system_lines={"2": SystemLine("2", "professionalV20", "linglong")},
            baseline_options={"deb:2": versions, "linglong:2": versions},
        )
        catalog = _capability_cache_to_json(cache)["default_system_line_options"]
        deb = catalog["deb"]["x86"][0]["baseline_options"]
        linglong = catalog["linglong"]["x86"][0]["baseline_options"]
        self.assertEqual([(v["version"], v["id"]) for v in deb if v["selectable"]], [
            ("1075", "128"), ("1074", "121"), ("1073", "114"),
        ])
        self.assertEqual([v["version"] for v in linglong if v["selectable"]], ["1075"])
        preferences = UIPreferences(default_target_ranges={
            "deb:x86": {"2": DefaultTargetRange("121", ("128",))},
            "linglong:x86": {"2": DefaultTargetRange("128")},
        })
        package = _make_package_group(Path("/fixture.deb"))
        row, = build_target_options(cache, package_group=package, preferences=preferences)
        self.assertTrue(row.selected)
        self.assertEqual(row.selected_baseline_ids, ("121",))
        self.assertEqual(row.unsupported_baseline_ids, ("128",))
        refresh, = _refresh_group_system_lines({
            "package_family": "deb", "packages": [{"path": "/fixture.deb", "arch": "amd64"}],
        }, cache, preferences)
        self.assertEqual(refresh["selected_baseline_ids"], ["121"])

    def test_round_trips_last_session_account(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = PreferenceStore(Path(temp_dir) / "preferences.json")
            store.save(UIPreferences(last_session_account="manual-login"))
            loaded = store.load()
            self.assertEqual(loaded.last_session_account, "manual-login")

    def test_round_trips_remembered_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = PreferenceStore(Path(temp_dir) / "preferences.json")
            store.save(
                UIPreferences(
                    last_session_account="odatacc",
                    remember_credentials=True,
                    remembered_username="odatacc",
                    remembered_password="secret",
                )
            )
            loaded = store.load()
            self.assertTrue(loaded.remember_credentials)
            self.assertEqual(loaded.remembered_username, "odatacc")
            self.assertEqual(loaded.remembered_password, "secret")

    def test_login_credentials_persists_remember_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = PreferenceStore(Path(temp_dir) / "preferences.json")
            login = LoginContext(
                client=_CachedClient(),
                account_label="odatacc",
                session_state_path=None,
                login_mode="credentials",
                can_use_browser_mode=True,
            )
            with (
                patch("ui.cpp_bridge.PreferenceStore", return_value=store),
                patch("ui.cpp_bridge.login_with_credentials", return_value=login),
                patch("ui.cpp_bridge._load_capabilities", return_value=None),
                patch("ui.cpp_bridge._load_categories", return_value=[]),
            ):
                result = command_login_credentials(
                    {
                        "username": "odatacc",
                        "password": "secret",
                        "remember_credentials": True,
                    }
                )

            preferences = store.load()
            self.assertTrue(result["preferences"]["remember_credentials"])
            self.assertEqual(preferences.last_session_account, "odatacc")
            self.assertEqual(preferences.remembered_username, "odatacc")
            self.assertEqual(preferences.remembered_password, "secret")

    def test_login_credentials_clears_saved_password_when_not_remembered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = PreferenceStore(Path(temp_dir) / "preferences.json")
            store.save(
                UIPreferences(
                    last_session_account="odatacc",
                    remember_credentials=True,
                    remembered_username="odatacc",
                    remembered_password="old",
                )
            )
            login = LoginContext(
                client=_CachedClient(),
                account_label="odatacc",
                session_state_path=None,
                login_mode="credentials",
                can_use_browser_mode=True,
            )
            with (
                patch("ui.cpp_bridge.PreferenceStore", return_value=store),
                patch("ui.cpp_bridge.login_with_credentials", return_value=login),
                patch("ui.cpp_bridge._load_capabilities", return_value=None),
                patch("ui.cpp_bridge._load_categories", return_value=[]),
            ):
                command_login_credentials(
                    {
                        "username": "odatacc",
                        "password": "new",
                        "remember_credentials": False,
                    }
                )

            preferences = store.load()
            self.assertFalse(preferences.remember_credentials)
            self.assertEqual(preferences.last_session_account, "odatacc")
            self.assertEqual(preferences.remembered_username, "")
            self.assertEqual(preferences.remembered_password, "")


class UiCppBridgeOnlineDetailTests(unittest.TestCase):
    def test_update_history_pages_keep_separate_submissions_of_the_same_version(self) -> None:
        client = Mock()
        client.get_app_history.side_effect = [
            {"total": 3, "rows": [
                {"id": "revision-3", "pkg_version": "2.0, 2.0, 2.0", "statusStr": "审核中", "submit_time": "2026-11-23 10:00:00"},
                {"id": "revision-2", "pkg_version": "2.0", "statusStr": "已上架", "submit_time": "2026-11-22 10:00:00"},
            ]},
            {"total": 3, "rows": [{"id": "revision-1", "pkg_version": "1.0", "statusStr": "已上架"}]},
        ]
        login = LoginContext(client, "test-account", None, "cached", False)
        with patch("ui.cpp_bridge.try_restore_cached_login", return_value=login):
            first = command_get_app_workflow({"app_id": "app-42", "page_num": 1, "page_size": 2})["history"]
            second = command_get_app_workflow({"app_id": "app-42", "page_num": 2, "page_size": 2})["history"]

        self.assertEqual(client.get_app_history.call_args_list, [
            call("app-42", page_num=1, page_size=2),
            call("app-42", page_num=2, page_size=2),
        ])
        client.get_app_audit_status.assert_not_called()
        self.assertEqual((first["page_num"], second["page_num"], second["total"]), (1, 2, 3))
        self.assertEqual(
            [(row["detail_id"], row["pkg_version"], row["status_str"]) for row in first["rows"] + second["rows"]],
            [("revision-3", "2.0", "审核中"), ("revision-2", "2.0", "已上架"), ("revision-1", "1.0", "已上架")],
        )
        self.assertEqual(first["rows"][0]["submit_time"], "2026-11-23 10:00:00")

    def test_history_notes_load_the_selected_revision_and_preserve_languages(self) -> None:
        client = Mock()
        client.get_app_detail.return_value = {"app_lan_infos": [
            {
                "lan": "zh_CN",
                "update_desc": "旧的通用说明",
                "pkgUptDesc": [
                    {"pkgArchVersion": "arm64 2.0,x86 2.0,loongarch64 2.0,loong64 2.0", "uptDesc": "四个架构均改用接口登录。\n"},
                    {"pkgArchVersion": "x86 1.9", "uptDesc": "保留另一版本的原说明。"},
                ],
            },
            {"lan": "en_US", "update_desc": "Keep earlier imports."},
            {"lan": "fr_FR", "update_desc": " "},
        ]}
        login = LoginContext(client, "test-account", None, "cached", False)
        with patch("ui.cpp_bridge.try_restore_cached_login", return_value=login):
            notes = command_get_app_history_notes({"detail_id": "revision-2"})["notes"]

        client.get_app_detail.assert_called_once_with("revision-2")
        self.assertEqual(notes, [
            {
                "language": "中文（简体）", "scope": "package",
                "packages": ("arm64 2.0", "x86 2.0", "loongarch64 2.0", "loong64 2.0"),
                "text": "四个架构均改用接口登录。\n",
            },
            {"language": "中文（简体）", "scope": "package", "packages": ("x86 1.9",), "text": "保留另一版本的原说明。"},
            {"language": "中文（简体）", "scope": "general", "text": "旧的通用说明"},
            {"language": "英文", "scope": "general", "text": "Keep earlier imports."},
        ])

    def test_current_note_editor_loads_saved_draft_notes_only(self) -> None:
        match = StoreAppMatch("app-42", "revision-2", "fixture", "Fixture")
        defaults = {"note_zh": "原中文说明", "note_en": "Original English notes"}
        for status in (101, -99):
            with self.subTest(status=status):
                detail = {"app_basic_info": {"id": "revision-2", "status": status}}
                group = _online_group_from_detail(match, detail, defaults, None)
                self.assertEqual(group["note_zh"], defaults["note_zh"] if status == -99 else "")
                self.assertEqual(group["note_en"], defaults["note_en"] if status == -99 else "")

    def test_import_appends_local_packages_and_keeps_workspace_edits(self) -> None:
        old_path = Path("/tmp/demo_2.0_loongarch64.deb")
        new_path = Path("/tmp/demo_2.0_loong64.deb")
        arm_path = "online://draft/arm64"
        online_loong_path = "online://draft/loong64"
        first_batch = _make_package_group(old_path, pkg_name="demo", pkg_version="2.0", pkg_arch="loongarch64")
        second_batch = _make_package_group(new_path, pkg_name="demo", pkg_version="2.0", pkg_arch="loong64")
        combined = PackageGroup(packages=first_batch.packages + second_batch.packages)
        local_loongarch = {"path": str(old_path), "arch": "loongarch64"}
        local_loong = {"path": str(new_path), "arch": "loong64"}
        local_target = {"package_path": str(old_path), "package_arch": "loongarch64", "code": "2", "selected": False}
        group = {
            "key": "demo|2.0|deb|deb", "pkg_name": "demo", "pkg_version": "2.0",
            "package_family": "deb", "package_format": "deb",
            "packages": [local_loongarch, local_loong],
            "targets": [
                local_target,
                {"package_path": str(new_path), "package_arch": "loong64", "code": "21", "selected": False},
            ],
        }
        previous = {
            **group,
            "key": "online|app-id",
            "online_only": True,
            "pkg_version": "1.0",
            "submission_mode": "update",
            "selected_match_app_id": "app-id",
            "existing_matches": [{"app_id": "app-id", "detail_id": "draft-revision"}],
            "draft_detail_id": "draft-revision",
            "removed_draft_package_ids": ["removed-row"],
            "app_name_zh": "编辑后的名称",
            "screenshot_paths": ["/tmp/edited-shot.png"],
            "packages": [
                {"path": arm_path, "arch": "arm64", "online": True},
                {"path": online_loong_path, "arch": "loong64", "online": True},
            ],
            "targets": [
                {"package_path": arm_path, "package_arch": "arm64", "code": "21", "selected": True},
                {"package_path": online_loong_path, "package_arch": "loong64", "code": "21", "selected": True},
            ],
        }
        first_group = {**group, "packages": [local_loongarch], "targets": [local_target]}
        with (
            patch("ui.cpp_bridge.try_restore_cached_login", return_value=None),
            patch("ui.cpp_bridge._load_capabilities", return_value=None),
            patch("ui.cpp_bridge.PreferenceStore"),
            patch("ui.cpp_bridge.analyze_package_group", side_effect=(first_batch, combined)) as analyze,
            patch("ui.cpp_bridge._group_to_json", side_effect=(first_group, group)),
        ):
            imported = command_analyze({"package_paths": [str(old_path)], "current_group": previous})["groups"][0]
            self.assertCountEqual(
                [package["path"] for package in imported["packages"]],
                [str(old_path), arm_path, online_loong_path],
            )
            for target in imported["targets"]:
                if target["package_path"] == str(old_path):
                    target.update(selected=True, selected_baseline_ids=["1074-id"])
            result = command_analyze({
                "package_paths": [str(new_path), str(new_path)],
                "current_group": imported,
            })["groups"][0]

        self.assertEqual([call.args[0] for call in analyze.call_args_list], [[old_path], [old_path, new_path]])
        self.assertCountEqual([package["path"] for package in result["packages"]], [str(old_path), str(new_path), arm_path])
        self.assertFalse(result["online_only"])
        self.assertEqual(result["pkg_version"], "2.0")
        self.assertEqual(result["submission_mode"], "update")
        self.assertEqual(result["app_name_zh"], "编辑后的名称")
        self.assertEqual(result["screenshot_paths"], ["/tmp/edited-shot.png"])
        self.assertEqual(result["draft_detail_id"], "draft-revision")
        self.assertEqual(result["existing_matches"], previous["existing_matches"])
        self.assertEqual(result["removed_draft_package_ids"], ["removed-row"])
        targets = {row["package_path"]: row for row in result["targets"]}
        self.assertTrue(targets[str(old_path)]["selected"])
        self.assertEqual(targets[str(old_path)]["selected_baseline_ids"], ["1074-id"])
        self.assertTrue(targets[str(new_path)]["selected"])
        self.assertTrue(targets[arm_path]["selected"])

    def test_online_versions_stay_with_their_package_and_line(self) -> None:
        cache = CapabilityCache(
            generated_at="2026-11-20T16:00:00+08:00",
            deb_system_lines={"11": SystemLine("11", "社区版V23", "deb")},
            linglong_system_lines={},
            baseline_options={"deb:11": (BaselineOption("11", "101", "23.0"), BaselineOption("11", "102", "23.1"))},
        )
        group = _online_group_from_detail(
            StoreAppMatch(app_id="100", detail_id="detail-100", pkg_name="fixture", app_name="Fixture"),
            {
                "app_basic_info": {"pkgInstallMode": 1},
                "app_fit_info": {"baseline": [{"id": "101"}]},
                "app_origin_pkgs": [
                    None,
                    {"pkg_arch": "4", "supSys": "11", "supBlineVer": "", "unsupportBlineVers": ""},
                    {"pkg_arch": "3", "supSys": "11", "supBlineVer": "101,102", "unsupportBlineVers": "102,103"},
                ],
            },
            {"app_name_zh": "Fixture"},
            cache,
        )
        rows = group["targets"]
        self.assertEqual([row["selected"] for row in rows], [True, True])
        self.assertEqual(rows[0]["selected_baseline_ids"], [])
        self.assertEqual(rows[1]["selected_baseline_ids"], ["101", "102"])
        self.assertEqual(rows[1]["unsupported_baseline_ids"], ["102", "103"])
        refreshed = _refresh_group_system_lines(group, cache, UIPreferences())
        self.assertEqual(refreshed, rows)

    def test_replacement_inherits_selected_lines_from_multiple_rows_of_the_same_arch(self) -> None:
        package = {"path": "/new.deb", "arch": "loong64"}
        local = {"packages": [package], "targets": [{"package_path": "/new.deb", "code": code} for code in ("11", "21")]}
        online = {"targets": [
            {"package_path": "old-a", "package_arch": "loong64", "code": "11", "selected": True},
            {"package_path": "old-a", "package_arch": "loong64", "code": "21", "selected": False},
            {"package_path": "old-b", "package_arch": "loong64", "code": "21", "selected": True},
            {"package_path": "old-c", "package_arch": "loongarch64", "code": "2", "selected": True},
        ]}
        selected = _targets_for_auto_matched_packages(local, online, [package])
        self.assertEqual([row["code"] for row in selected if row["selected"]], ["11", "21"])

    def test_submission_preprocesses_imported_assets_and_reuses_checked_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = _make_package_group(root / "fixture.deb")
            icon = root / "icon.png"
            screenshot = root / "screenshot.png"
            payload = {"icon_path": str(icon), "screenshot_paths": [str(screenshot)]}

            with patch("ui.cpp_bridge._group_payload_to_package_group", return_value=package):
                raw, _ = _group_payload_to_plan(payload)
                checked, _ = _group_payload_to_plan(dict(payload, assets_preprocessed=True))

            self.assertEqual(raw.manual_icon_path, icon)
            self.assertEqual(raw.manual_screenshot_paths, (screenshot,))
            self.assertIsNone(raw.prepared_icon_path)
            self.assertEqual(raw.prepared_screenshot_paths, ())
            self.assertEqual(checked.prepared_icon_path, icon)
            self.assertEqual(checked.prepared_screenshot_paths, (screenshot,))

    def test_local_package_auto_match_loads_online_assets_and_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            package_path = temp_root / "labelnova_1.0.4-1_amd64.deb"
            package_path.write_bytes(b"deb")
            package_group = _make_package_group(package_path, pkg_name="labelnova", pkg_version="1.0.4-1")
            cache = CapabilityCache(
                generated_at="2026-04-27T18:00:00+08:00",
                deb_system_lines={
                    "11": SystemLine(code="11", label="社区版V23", family="deb"),
                    "21": SystemLine(code="21", label="专业版V25", family="deb"),
                },
                linglong_system_lines={},
                baseline_options={
                    "deb:11": (BaselineOption(system_line_code="11", baseline_id="2301", minor_version="23.0.1"),),
                    "deb:21": (BaselineOption(system_line_code="21", baseline_id="2501", minor_version="25.0.1"),),
                },
                arch_options={
                    "3": StoreAdaptOption(code="3", label="arm64"),
                    "4": StoreAdaptOption(code="4", label="x86"),
                },
            )
            match = StoreAppMatch(
                app_id="1001",
                detail_id="detail-1001",
                pkg_name="labelnova",
                app_name="LabelNova",
            )
            detail = {
                "datas": {
                    "app_basic_info": {"pkgInstallMode": 1},
                    "app_fit_info": {
                        "arch": [{"code": 3}, {"code": 4}],
                        "system_platform": [{"code": 11}, {"code": 21}],
                        "baseline": ["2301", "2501"],
                    },
                    "app_origin_pkgs": [
                        {
                            "pkg_name": "labelnova",
                            "pkg_version": "1.0.4-1",
                            "pkg_arch": "3",
                            "pkgArch": "ARM",
                            "system_platform": ["11"],
                            "baseline": ["2301"],
                        },
                        {
                            "pkg_name": "labelnova",
                            "pkg_version": "1.0.4-1",
                            "pkg_arch": "4",
                            "pkgArch": "X86",
                            "system_platform": ["21"],
                            "baseline": ["2501"],
                        },
                    ],
                }
            }
            login = LoginContext(
                client=_DetailClient(),
                account_label="odatacc",
                session_state_path=None,
                login_mode="cached",
                can_use_browser_mode=True,
            )

            with (
                patch("ui.cpp_bridge.detect_asset_candidates", return_value=(None, ())),
                patch("ui.cpp_bridge._extract_initial_icon", return_value=None),
                patch("ui.cpp_bridge._extract_package_icon", return_value=None),
                patch("ui.cpp_bridge.find_existing_apps", return_value=(match,)),
                patch("ui.cpp_bridge.fetch_existing_app_detail", return_value=detail),
                patch(
                    "ui.cpp_bridge.build_existing_detail_editor_defaults",
                    return_value={
                        "app_name_zh": "LabelNova",
                        "website": "https://mm.md/p/",
                        "short_desc_zh": "开源标签工具",
                        "full_desc_zh": "条码标签设计与打印工具",
                        "category_id": "1",
                        "region_codes": ["1"],
                    },
                ),
                patch(
                    "ui.cpp_bridge._sync_existing_detail_assets",
                    return_value={
                        "icon_path": "/tmp/online-labelnova.png",
                        "screenshot_paths": ["/tmp/online-shot-1.png", "/tmp/online-shot-2.png"],
                        "asset_warnings": [],
                    },
                ),
            ):
                group = _group_to_json(
                    package_group,
                    login_context=login,
                    capability_cache=cache,
                    asset_dir=None,
                )

            self.assertTrue(group["auto_matched_online_app"])
            self.assertEqual(group["selected_match_app_id"], "1001")
            self.assertEqual(group["submission_mode"], "update")
            self.assertEqual(group["app_name_zh"], "LabelNova")
            self.assertEqual(group["icon_path"], "/tmp/online-labelnova.png")
            self.assertEqual(group["packages"][0]["icon_path"], "/tmp/online-labelnova.png")
            self.assertEqual(group["screenshot_paths"], ["/tmp/online-shot-1.png", "/tmp/online-shot-2.png"])
            self.assertEqual(len(group["packages"]), 2)
            self.assertEqual(group["packages"][0]["path"], str(package_path))
            self.assertEqual(group["packages"][0]["arch"], "amd64")
            self.assertEqual(group["packages"][1]["arch"], "arm64")
            self.assertTrue(group["packages"][1]["online"])

            targets = [target for target in group["targets"] if target["package_path"] == str(package_path)]
            self.assertTrue(any(target["code"] == "21" and target["selected"] for target in targets))
            self.assertFalse(any(target["code"] == "11" and target["selected"] for target in targets))
            self.assertTrue(all(target["package_arch"] == "amd64" for target in targets))
            online_package_path = group["packages"][1]["path"]
            online_targets = [target for target in group["targets"] if target["package_path"] == online_package_path]
            self.assertTrue(any(target["code"] == "11" and target["selected"] for target in online_targets))

            with patch("ui.cpp_bridge.analyze_package_group", return_value=package_group):
                payload_group = _group_payload_to_package_group(group)
            self.assertEqual(len(payload_group.packages), 2)
            self.assertEqual([package.pkg_arch for package in payload_group.packages], ["amd64", "arm64"])

    def test_local_package_auto_match_filters_same_name_by_package_family(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            package_path = temp_root / "demo_1.0.0_amd64.deb"
            package_path.write_bytes(b"deb")
            package_group = _make_package_group(package_path, pkg_name="same-name", pkg_version="1.0.0")
            deb_match = StoreAppMatch(
                app_id="deb-app",
                detail_id="detail-deb",
                pkg_name="same-name",
                app_name="Same Name DEB",
            )
            linglong_match = StoreAppMatch(
                app_id="linglong-app",
                detail_id="detail-linglong",
                pkg_name="same-name",
                app_name="Same Name Linglong",
            )
            details = {
                "detail-deb": {
                    "datas": {
                        "app_basic_info": {"pkgInstallMode": 1},
                        "app_fit_info": {},
                        "app_origin_pkgs": [],
                    }
                },
                "detail-linglong": {
                    "datas": {
                        "app_basic_info": {"pkgInstallMode": 2},
                        "app_fit_info": {},
                        "app_origin_pkgs": [],
                    }
                },
            }
            login = LoginContext(
                client=_DetailClient(),
                account_label="odatacc",
                session_state_path=None,
                login_mode="cached",
                can_use_browser_mode=True,
            )

            with (
                patch("ui.cpp_bridge.detect_asset_candidates", return_value=(None, ())),
                patch("ui.cpp_bridge._extract_initial_icon", return_value=None),
                patch("ui.cpp_bridge._extract_package_icon", return_value=None),
                patch("ui.cpp_bridge.find_existing_apps", return_value=(linglong_match, deb_match)),
                patch("ui.cpp_bridge.fetch_existing_app_detail", side_effect=lambda _client, match: details[match.detail_id]),
                patch(
                    "ui.cpp_bridge.build_existing_detail_editor_defaults",
                    side_effect=lambda _detail, fallback_name: {
                        "app_name_zh": fallback_name,
                        "category_id": "1",
                        "region_codes": ["1"],
                    },
                ),
                patch("ui.cpp_bridge._sync_existing_detail_assets", return_value={"asset_warnings": []}),
            ):
                group = _group_to_json(
                    package_group,
                    login_context=login,
                    capability_cache=None,
                    asset_dir=None,
                )

            self.assertTrue(group["auto_matched_online_app"])
            self.assertEqual(group["selected_match_app_id"], "deb-app")
            self.assertEqual(group["package_family"], "deb")
            self.assertEqual(group["existing_matches"][0]["package_family"], "deb")

    def test_online_app_detail_is_exposed_as_package_rows_per_arch(self) -> None:
        cache = CapabilityCache(
            generated_at="2026-04-27T18:00:00+08:00",
            deb_system_lines={
                "11": SystemLine(code="11", label="社区版V23", family="deb"),
                "21": SystemLine(code="21", label="专业版V25", family="deb"),
            },
            linglong_system_lines={},
            baseline_options={
                "deb:11": (
                    BaselineOption(system_line_code="11", baseline_id="2300", minor_version="23.0.0"),
                    BaselineOption(system_line_code="11", baseline_id="2301", minor_version="23.0.1"),
                ),
                "deb:21": (
                    BaselineOption(system_line_code="21", baseline_id="2500", minor_version="25.0.0"),
                    BaselineOption(system_line_code="21", baseline_id="2501", minor_version="25.0.1"),
                ),
            },
            arch_options={
                "3": StoreAdaptOption(code="3", label="arm64"),
                "4": StoreAdaptOption(code="4", label="x86"),
            },
        )
        match = StoreAppMatch(
            app_id="1096239",
            detail_id="4ad35608e3ed432291943769a9ac9c32",
            pkg_name="linglong-store",
            app_name="玲珑应用商店社区版",
        )
        detail = {
            "datas": {
                "app_basic_info": {"pkgInstallMode": 1},
                "app_fit_info": {
                    "arch": [{"code": 3}, {"code": 4}],
                    "system_platform": [{"code": 11}, {"code": 21}],
                    "baseline": ["2301", "2501"],
                },
                "app_origin_pkgs": [
                    {
                        "pkg_name": "linglong-store",
                        "pkg_version": "3.3.0",
                        "pkg_arch": "3",
                        "pkgArch": "ARM",
                        "pkg_size": 13316915,
                        "progressPercent": 100,
                        "system_platform": ["11"],
                        "baseline": ["2301"],
                        "systemStr": "社区版V23",
                        "upload_time": "2026-04-24 10:19:33",
                    },
                    {
                        "pkg_name": "linglong-store",
                        "pkg_version": "3.3.0",
                        "pkg_arch": "4",
                        "pkgArch": "X86",
                        "pkg_size": 13841203,
                        "progressPercent": 100,
                        "system_platform": ["21"],
                        "baseline": ["2501"],
                        "systemStr": "专业版V25",
                        "upload_time": "2026-04-24 10:19:33",
                    },
                ],
            }
        }

        group = _online_group_from_detail(
            match,
            detail,
            {
                "app_name_zh": "玲珑应用商店社区版",
                "website": "https://store.linyaps.org.cn/",
                "short_desc_zh": "一站式玲珑应用管理工具",
                "full_desc_zh": "社区版应用商店",
                "category_id": "1",
                "icon_path": "/tmp/online-icon.png",
                "screenshot_paths": ["/tmp/online-shot-1.png", "/tmp/online-shot-2.png"],
            },
            cache,
        )

        packages = group["packages"]
        self.assertEqual(len(packages), 2)
        self.assertEqual([package["arch"] for package in packages], ["arm64", "x86"])
        self.assertEqual([package["version"] for package in packages], ["3.3.0", "3.3.0"])
        self.assertEqual(packages[0]["status_text"], "上传完成")
        self.assertEqual(packages[0]["icon_path"], "/tmp/online-icon.png")
        self.assertEqual(group["icon_path"], "/tmp/online-icon.png")
        self.assertEqual(group["screenshot_paths"], ["/tmp/online-shot-1.png", "/tmp/online-shot-2.png"])
        self.assertEqual(group["selected_package_path"], packages[0]["path"])

        targets_by_package = {
            package["path"]: [target for target in group["targets"] if target["package_path"] == package["path"]]
            for package in packages
        }
        self.assertTrue(any(target["code"] == "11" and target["selected"] for target in targets_by_package[packages[0]["path"]]))
        self.assertTrue(any(target["code"] == "21" and target["selected"] for target in targets_by_package[packages[1]["path"]]))

        package_group = _group_payload_to_package_group(group)
        submit_targets = _group_payload_to_targets(group)
        self.assertEqual(len(package_group.packages), 2)
        self.assertTrue(str(package_group.packages[0].path).startswith("online/"))
        self.assertEqual(submit_targets[0].package_path, str(package_group.packages[0].path))

    def test_online_system_line_without_baseline_stays_without_specific_version(self) -> None:
        cache = CapabilityCache(
            generated_at="2026-04-27T18:00:00+08:00",
            deb_system_lines={
                "11": SystemLine(code="11", label="社区版V23", family="deb"),
            },
            linglong_system_lines={},
            baseline_options={
                "deb:11": (
                    BaselineOption(system_line_code="11", baseline_id="2300", minor_version="23.0"),
                    BaselineOption(system_line_code="11", baseline_id="2303", minor_version="23.3"),
                ),
            },
            arch_options={"4": StoreAdaptOption(code="4", label="x86")},
        )
        detail = {
            "datas": {
                "app_basic_info": {"pkgInstallMode": 1},
                "app_fit_info": {"arch": [{"code": 4}], "system_platform": [{"code": 11}]},
                "app_origin_pkgs": [
                    {
                        "pkg_name": "labelnova",
                        "pkg_version": "1.0.4-1",
                        "pkg_arch": "4",
                        "pkgArch": "X86",
                        "system_platform": ["11"],
                        "systemStr": "社区版V23",
                    },
                ],
            }
        }

        group = _online_group_from_detail(
            StoreAppMatch(app_id="1001", detail_id="detail-1001", pkg_name="labelnova", app_name="LabelNova"),
            detail,
            {
                "app_name_zh": "LabelNova",
                "website": "https://mm.md/p/",
                "short_desc_zh": "开源标签工具",
                "full_desc_zh": "条码标签设计与打印工具",
                "category_id": "1",
            },
            cache,
        )

        target = group["targets"][0]
        self.assertEqual(target["code"], "11")
        self.assertTrue(target["selected"])
        self.assertEqual(target["baseline_id"], "")
        self.assertEqual(target["selected_baseline_ids"], [])

    def test_sync_existing_detail_assets_preserves_store_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            icon_path = temp_root / "icon.png"
            shot_1 = temp_root / "shot-1.png"
            shot_2 = temp_root / "shot-2.png"
            for path in (icon_path, shot_1, shot_2):
                path.write_bytes(b"image")

            result = _sync_existing_detail_assets(
                StoreAppMatch(
                    app_id="100",
                    detail_id="detail",
                    pkg_name="demo",
                    app_name="Demo",
                ),
                {
                    "datas": {
                        "app_lan_infos": [
                            {
                                "lan": "zh_CN",
                                "icon_save_key": str(icon_path),
                                "appScreenShotList": [
                                    {"screen_shot_key": str(shot_2), "sort": 2},
                                    {"screen_shot_key": str(shot_1), "sort": 1},
                                ],
                            }
                        ]
                    }
                },
            )

            self.assertEqual(result["icon_path"], str(icon_path.resolve()))
            self.assertEqual(result["screenshot_paths"], [str(shot_1.resolve()), str(shot_2.resolve())])
            self.assertEqual(result["asset_warnings"], [])


class UiBackendCaptureTests(unittest.TestCase):
    def test_capture_screenshots_for_group_keeps_partial_valid_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            package_path = temp_root / "demo_1.0.0_amd64.deb"
            package_path.write_bytes(b"deb")
            package_group = _make_package_group(package_path)
            asset_dir = temp_root / "capture" / "demo-app" / "1.0.0-amd64"
            screenshots_dir = asset_dir / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            screen_01 = screenshots_dir / "screen-01.png"
            screen_04 = screenshots_dir / "screen-04.png"
            screen_01.write_bytes(b"01")
            screen_04.write_bytes(b"04")
            (asset_dir / "screenshot-validation.json").write_text(
                json.dumps(
                    {
                        "accepted_paths": [str(screen_01), str(screen_04)],
                        "rejected_paths": [str(screenshots_dir / "screen-02.png")],
                        "items": [
                            {
                                "accepted": False,
                                "reasons": ["duplicate of screen-01.png"],
                                "analysis": {"path": str(screenshots_dir / "screen-02.png")},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = CapturePackageResult(
                row_id=1,
                package_path=package_path,
                pkg_name="demo-app",
                pkg_version="1.0.0",
                pkg_arch="amd64",
                status="capture_failed",
                message="captured screenshots below minimum: got 2, require at least 3",
                asset_dir=asset_dir,
            )

            logs: list[str] = []
            with patch("ui.backend.capture_packages", return_value=[result]):
                screenshots = capture_screenshots_for_group(
                    package_group,
                    output_dir=temp_root / "output",
                    log=logs.append,
                )

            self.assertEqual(screenshots, (screen_01, screen_04))
            self.assertTrue(any("已保留 2 张有效截图" in line for line in logs))
            self.assertTrue(any("duplicate of screen-01.png x1" in line for line in logs))

    def test_submit_existing_application_requires_three_screenshots_when_replacing_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            package_path = temp_root / "demo_1.0.0_amd64.deb"
            package_path.write_bytes(b"deb")
            icon_path = temp_root / "icon.png"
            icon_path.write_bytes(b"icon")
            screenshot_01 = temp_root / "screen-01.png"
            screenshot_02 = temp_root / "screen-02.png"
            screenshot_01.write_bytes(b"01")
            screenshot_02.write_bytes(b"02")
            package_group = _make_package_group(package_path)
            login = LoginContext(
                client=object(),
                account_label="odatacc",
                session_state_path=None,
                login_mode="cached",
                can_use_browser_mode=True,
            )
            assets = AssetBundle(
                icon_source=icon_path,
                screenshot_sources=(screenshot_01, screenshot_02),
                icon_path=icon_path,
                screenshot_paths=(screenshot_01, screenshot_02),
                validation_report=None,
                warnings=(),
            )

            with self.assertRaisesRegex(RuntimeError, "at least 3 valid screenshots"):
                submit_existing_application(
                    login,
                    package_group=package_group,
                    cache=None,
                    match=StoreAppMatch(
                        app_id="1001",
                        detail_id="detail-1001",
                        pkg_name="demo-app",
                        app_name="Demo App",
                    ),
                    app_name_zh="Demo App",
                    website="https://example.com",
                    short_desc_zh="short",
                    full_desc_zh="full",
                    category_id=1,
                    region_codes=("1",),
                    note="note",
                    release_key="stable",
                    pkg_channel="stable",
                    assets=assets,
                    selected_targets=(),
                    replace_assets=True,
                    output_dir=temp_root / "submit-output",
                )


class UiBackendBatchSubmitTests(unittest.TestCase):
    def test_build_target_options_for_new_app_start_unselected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package_path = Path(temp_dir) / "demo_1.0.0_amd64.deb"
            package_path.write_bytes(b"deb")
            package_group = _make_package_group(package_path)
            cache = CapabilityCache(
                generated_at="2026-04-25T18:00:00+08:00",
                deb_system_lines={
                    "11": SystemLine(code="11", label="社区版V23", family="deb"),
                },
                linglong_system_lines={},
                baseline_options={
                    "deb:11": (
                        BaselineOption(system_line_code="11", baseline_id="2300", minor_version="23.0.0"),
                        BaselineOption(system_line_code="11", baseline_id="2301", minor_version="23.0.1"),
                    ),
                },
            )

            options = build_target_options(cache, package_group=package_group)

            self.assertEqual(len(options), 1)
            self.assertFalse(options[0].selected)
            self.assertEqual(options[0].baseline_id, "")
            self.assertEqual(options[0].selected_baseline_ids, ())
            self.assertEqual(options[0].unsupported_baseline_ids, ())

    def test_build_target_options_uses_arch_default_system_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package_path = Path(temp_dir) / "demo_1.0.0_amd64.deb"
            package_path.write_bytes(b"deb")
            package_group = _make_package_group(package_path)
            cache = CapabilityCache(
                generated_at="2026-04-25T18:00:00+08:00",
                deb_system_lines={
                    "11": SystemLine(code="11", label="社区版V23", family="deb"),
                    "21": SystemLine(code="21", label="专业版V25", family="deb"),
                },
                linglong_system_lines={},
                baseline_options={},
            )

            options = build_target_options(
                cache,
                package_group=package_group,
                default_target_system_lines={"x86": ("21",)},
            )

            selected_codes = tuple(option.code for option in options if option.selected)
            self.assertEqual(selected_codes, ("21",))
            self.assertTrue(all(option.baseline_id == "" for option in options))

    def test_build_target_options_supports_loongarch64_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package_path = Path(temp_dir) / "demo_1.0.0_loongarch64.deb"
            package_path.write_bytes(b"deb")
            package_group = _make_package_group(package_path, pkg_arch="loongarch64")
            cache = CapabilityCache(
                generated_at="2026-04-25T18:00:00+08:00",
                deb_system_lines={"11": SystemLine(code="11", label="社区版V23", family="deb")},
                linglong_system_lines={},
                baseline_options={},
            )

            options = build_target_options(cache, package_group=package_group)

            self.assertEqual(len(options), 1)
            self.assertEqual(options[0].package_arch, "loongarch64")
            self.assertEqual(options[0].code, "11")

    def test_build_target_options_supports_wine_deb_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package_path = Path(temp_dir) / "wine-helper_1.0.0_amd64.deb"
            package_path.write_bytes(b"deb")
            package_group = _make_package_group(package_path, pkg_name="wine-helper")
            cache = CapabilityCache(
                generated_at="2026-04-25T18:00:00+08:00",
                deb_system_lines={"11": SystemLine(code="11", label="社区版V23", family="deb")},
                linglong_system_lines={},
                baseline_options={},
            )

            options = build_target_options(cache, package_group=package_group)

            self.assertEqual(len(options), 1)
            self.assertEqual(options[0].package_arch, "amd64")
            self.assertEqual(options[0].code, "11")

    def test_submit_applications_batch_supports_mixed_update_and_new(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            group_one = _make_package_group(
                temp_root / "labelnova_1.0.4-1_amd64.deb",
                pkg_name="labelnova",
                pkg_version="1.0.4-1",
            )
            group_two = _make_package_group(
                temp_root / "brand-new_2.0.0_amd64.deb",
                pkg_name="brand-new",
                pkg_version="2.0.0",
            )
            login = LoginContext(
                client=object(),
                account_label="odatacc",
                session_state_path=None,
                login_mode="cached",
                can_use_browser_mode=True,
            )
            targets = (
                _target_option(group_one.packages[0].path, "11", "baseline-11"),
                _target_option(group_two.packages[0].path, "11", "baseline-11"),
            )
            fake_detail = {
                "datas": {
                    "app_basic_info": {"category_id": 1, "website": "https://example.com", "region": "1"},
                    "app_lan_infos": [{"lan": "zh_CN", "name": "Demo", "brief_info": "short", "desc_info": "full"}],
                    "app_fit_info": {"region": [{"code": 1}]},
                }
            }
            assets = AssetBundle(
                icon_source=temp_root / "icon.png",
                screenshot_sources=(
                    temp_root / "s1.png",
                    temp_root / "s2.png",
                    temp_root / "s3.png",
                ),
                icon_path=temp_root / "icon.png",
                screenshot_paths=(
                    temp_root / "s1.png",
                    temp_root / "s2.png",
                    temp_root / "s3.png",
                ),
                validation_report=None,
                warnings=(),
            )
            for path in (assets.icon_path, *assets.screenshot_paths):
                path.write_bytes(b"img")

            plans = (
                BatchGroupSubmissionPlan(
                    package_group=group_one,
                    submission_mode="update",
                    selected_match=StoreAppMatch(app_id="1001", detail_id="d1", pkg_name="labelnova", app_name="LabelNova"),
                    app_name_zh="",
                    website="",
                    short_desc_zh="",
                    full_desc_zh="",
                    category_id="1",
                    region_codes=("1",),
                    asset_dir=None,
                    metadata_edited=False,
                ),
                BatchGroupSubmissionPlan(
                    package_group=group_two,
                    submission_mode="new",
                    selected_match=None,
                    app_name_zh="Brand New",
                    website="https://new.example.com",
                    short_desc_zh="short",
                    full_desc_zh="full",
                    category_id="2",
                    region_codes=("1", "2"),
                    asset_dir=temp_root,
                    metadata_edited=True,
                ),
            )

            with (
                patch("ui.backend.fetch_existing_app_detail", return_value=fake_detail),
                patch("ui.backend.preprocess_submission_assets", return_value=assets),
                patch("ui.backend._build_localized_lan_texts", return_value={"zh_CN": {}, "en_US": {}}),
                patch(
                    "ui.backend._submit_grouped_release",
                    side_effect=[
                        _submission_result(temp_root / "out/01", "labelnova", "1.0.4-1"),
                        _submission_result(temp_root / "out/02", "brand-new", "2.0.0"),
                    ],
                ) as submit_mock,
            ):
                result = submit_applications_batch(
                    login,
                    plans=plans,
                    cache=object(),
                    note="批量提交",
                    release_key="stable",
                    pkg_channel="stable",
                    selected_targets=targets,
                    output_dir=temp_root / "batch-output",
                )

        self.assertEqual(len(result.rows), 2)
        self.assertEqual([row["app_key"] for row in result.rows], ["labelnova", "brand-new"])
        self.assertEqual(submit_mock.call_args_list[0].kwargs["target_app_id"], "1001")
        self.assertEqual(submit_mock.call_args_list[1].kwargs["target_app_id"], "")
        self.assertEqual(submit_mock.call_args_list[1].kwargs["category_id"], 2)
        self.assertEqual(submit_mock.call_args_list[1].kwargs["region_codes"], ("1", "2"))

    def test_submit_applications_batch_update_can_replace_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            group = _make_package_group(
                temp_root / "labelnova_1.0.4-1_amd64.deb",
                pkg_name="labelnova",
                pkg_version="1.0.4-1",
            )
            login = LoginContext(
                client=object(),
                account_label="odatacc",
                session_state_path=None,
                login_mode="cached",
                can_use_browser_mode=True,
            )
            targets = (_target_option(group.packages[0].path, "11", "baseline-11"),)
            fake_detail = {
                "datas": {
                    "app_basic_info": {"category_id": 1, "website": "https://example.com", "region": "1"},
                    "app_lan_infos": [{"lan": "zh_CN", "name": "Demo", "brief_info": "short", "desc_info": "full"}],
                    "app_fit_info": {"region": [{"code": 1}]},
                }
            }
            icon_path = temp_root / "icon.png"
            screen_1 = temp_root / "s1.png"
            screen_2 = temp_root / "s2.png"
            screen_3 = temp_root / "s3.png"
            for path in (icon_path, screen_1, screen_2, screen_3):
                path.write_bytes(b"img")
            assets = AssetBundle(
                icon_source=icon_path,
                screenshot_sources=(screen_1, screen_2, screen_3),
                icon_path=icon_path,
                screenshot_paths=(screen_1, screen_2, screen_3),
                validation_report=None,
                warnings=(),
            )
            plans = (
                BatchGroupSubmissionPlan(
                    package_group=group,
                    submission_mode="update",
                    selected_match=StoreAppMatch(app_id="1001", detail_id="d1", pkg_name="labelnova", app_name="LabelNova"),
                    app_name_zh="LabelNova",
                    website="https://example.com",
                    short_desc_zh="short",
                    full_desc_zh="full",
                    category_id="1",
                    region_codes=("1",),
                    asset_dir=temp_root,
                    replace_assets=True,
                    metadata_edited=True,
                ),
            )

            with (
                patch("ui.backend.fetch_existing_app_detail", return_value=fake_detail),
                patch("ui.backend._resolve_batch_assets", return_value=assets),
                patch("ui.backend._build_localized_lan_texts", return_value={"zh_CN": {}}),
                patch(
                    "ui.backend._submit_grouped_release",
                    return_value=_submission_result(temp_root / "out/01", "labelnova", "1.0.4-1"),
                ) as submit_mock,
            ):
                submit_applications_batch(
                    login,
                    plans=plans,
                    cache=object(),
                    note="批量更新",
                    release_key="stable",
                    pkg_channel="stable",
                    selected_targets=targets,
                    output_dir=temp_root / "batch-output",
                )

        self.assertEqual(submit_mock.call_args.kwargs["assets"].icon_path, icon_path)
        self.assertEqual(len(submit_mock.call_args.kwargs["assets"].screenshot_paths), 3)

    def test_submit_grouped_release_reuses_online_package_without_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            package_path = Path("online/42/0/demo/1.0.0/x86")
            package_group = PackageGroup(
                packages=(
                    PackageMetadata(
                        path=package_path,
                        package_family="deb",
                        package_format="deb",
                        pkg_name="demo",
                        pkg_version="1.0.0",
                        pkg_arch="amd64",
                        pkg_size=10,
                        sha256="old-hash",
                        display_name="Demo",
                        short_description="short",
                        full_description="full",
                        homepage="https://example.com",
                    ),
                )
            )
            client = _NoUploadSubmitClient()
            login = LoginContext(
                client=client,
                account_label="odatacc",
                session_state_path=None,
                login_mode="cached",
                can_use_browser_mode=False,
            )
            cache = CapabilityCache(
                generated_at="2026-04-27T18:00:00+08:00",
                deb_system_lines={"21": SystemLine(code="21", label="专业版V25", family="deb")},
                linglong_system_lines={},
                baseline_options={
                    "deb:21": (BaselineOption(system_line_code="21", baseline_id="2500", minor_version="25.0.0"),),
                },
            )
            existing_detail = {
                "datas": {
                    "app_basic_info": {"category_id": 1, "website": "https://example.com", "region": "1"},
                    "app_lan_infos": [{"lan": "zh_CN", "name": "Demo", "brief_info": "short", "desc_info": "full"}],
                    "app_fit_info": {"system_platform": [{"code": 21}], "arch": [{"code": 4}], "region": [{"code": 1}]},
                    "app_origin_pkgs": [
                        {
                            "pkg_name": "demo",
                            "pkg_version": "1.0.0",
                            "pkg_arch": "4",
                            "pkgArch": "X86",
                            "pkgType": 11,
                            "pkg_size": 10,
                            "sha256": "old-hash",
                            "file_save_key": "existing-x86",
                            "progressPercent": 100,
                            "supSys": "21",
                            "supBlineVer": "2500",
                            "systemStr": "专业版V25",
                        }
                    ],
                }
            }

            result = _submit_grouped_release(
                login=login,
                package_group=package_group,
                cache=cache,
                app_name_zh="Demo",
                website="https://example.com",
                short_desc_zh="short",
                full_desc_zh="full",
                keywords_zh="",
                category_id=1,
                region_codes=("1",),
                note="仅更新资料",
                release_key="stable",
                pkg_channel="stable",
                assets=AssetBundle(None, (), None, (), None, ()),
                selected_targets=(_target_option(package_path, "21", "2500"),),
                output_dir=temp_root / "out",
                target_app_id="42",
                existing_app_detail=existing_detail,
                existing_app_overrides={"app_name_zh": "Demo"},
                desired_lans=("zh_CN",),
                localized_lan_texts={"zh_CN": {"name": "Demo"}},
                developer_name="",
                cpu_clip_codes=None,
                motherboard_codes=None,
                log=None,
            )

        self.assertEqual(result.rows[0]["status"], "submitted")
        self.assertIsNotNone(client.payload)
        self.assertEqual(client.payload["app_info"]["app_origin_pkgs"][0]["file_save_key"], "existing-x86")

    def test_save_draft_replaces_and_removes_packages_by_revision(self) -> None:
        draft_id = "draft-revision"
        app_id = "published-app"
        detail = {
            "app_basic_info": {"id": draft_id, "app_id": app_id, "status": -99, "category_id": 1, "region": "1,2"},
            "app_lan_infos": [
                {
                    "lan": "zh_CN", "name": "Demo", "update_desc": "旧通用说明",
                    "pkgUptDesc": [{"originPkgKeys": ["arm-row", "old-loongarch", "other-loong"], "uptDesc": "旧包说明"}],
                },
                {"lan": "en_US", "name": "Demo", "update_desc": "Previous general notes.", "pkgUptDesc": []},
            ],
            "app_origin_pkgs": [
                {
                    "id": row_id, "pkg_name": "demo", "pkg_version": "1.0", "pkg_arch": arch,
                    "pkgType": 11, "supSys": "21", "supBlineVer": "2500", "file_save_key": row_id,
                }
                for row_id, arch in (("arm-row", "3"), ("old-loongarch", "5"), ("other-loong", "6"))
            ],
        }
        cache = CapabilityCache(
            generated_at="2026-04-27T18:00:00+08:00",
            deb_system_lines={"21": SystemLine("21", "社区版V25", "deb")},
            linglong_system_lines={},
            baseline_options={"deb:21": (BaselineOption("21", "2500", "25.0.0"),)},
        )
        client = _NoUploadSubmitClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "demo_2.0_loongarch64.deb"
            path.write_bytes(b"new-package")
            plan = BatchGroupSubmissionPlan(
                package_group=_make_package_group(path, pkg_name="demo", pkg_version="2.0", pkg_arch="loongarch64"),
                submission_mode="update",
                selected_match=StoreAppMatch(app_id, draft_id, "demo", "Demo"),
                app_name_zh="Demo", website="", short_desc_zh="", full_desc_zh="",
                category_id="1", region_codes=("1", "2"), asset_dir=None,
                note_zh="本次只更新 loongarch64",
                auto_translate_en=False, manual_en_edited=True,
                app_name_en="Demo", short_desc_en="Brief", full_desc_en="Description",
                save_draft=True, expected_draft_id=draft_id, removed_draft_package_ids=("arm-row",),
                use_package_update_notes=True,
            )
            with (
                patch.object(client, "get_app_detail", create=True, return_value=detail) as fetch,
                patch.object(client, "upload_file_bytes", return_value=UploadedFileRef("temppkg", "new-key", 11, "hash")),
                patch.object(client, "update_draft", create=True, return_value={"datas": {"app_id": app_id}}) as update,
                patch.object(client, "submit_payload", side_effect=AssertionError("expected draft update")),
            ):
                result = submit_applications_batch(
                    LoginContext(client, "demo", None, "cached", False),
                    plans=(plan,), cache=cache, note="", release_key="stable", pkg_channel="stable",
                    selected_targets=(_target_option(path, "21", "2500"),), output_dir=root / "output",
                )

        fetch.assert_called_once_with(draft_id)
        self.assertEqual(result.rows[0]["status"], "submitted", result.rows[0]["message"])
        update.assert_called_once()
        detail_id, payload = update.call_args.args
        self.assertEqual(detail_id, draft_id)
        self.assertEqual(payload["operate_type"], -99)
        self.assertNotIn("app_id", payload)
        app_info = payload["app_info"]
        self.assertEqual(app_info["app_basic_info"]["id"], draft_id)
        self.assertEqual(app_info["app_basic_info"]["app_id"], app_id)
        self.assertEqual(
            {row["id"]: row["file_save_key"] for row in app_info["app_origin_pkgs"]},
            {"old-loongarch": "new-key", "other-loong": "other-loong"},
        )
        self.assertCountEqual(app_info["app_fit_info"]["arch"], [{"code": 5}, {"code": 6}])
        languages = {row["lan"]: row for row in app_info["app_lan_infos"]}
        self.assertEqual(languages["zh_CN"]["update_desc"], "旧通用说明")
        self.assertEqual(languages["zh_CN"]["pkgUptDesc"], [
            {"originPkgKeyStr": "other-loong", "uptDesc": "旧包说明"},
            {"originPkgKeyStr": "new-key", "uptDesc": "本次只更新 loongarch64"},
        ])
        self.assertEqual(languages["en_US"]["update_desc"], "Previous general notes.")
        self.assertEqual(languages["en_US"]["pkgUptDesc"], [])

    def test_draft_submission_stops_before_upload_if_revision_is_no_longer_a_draft(self) -> None:
        draft_id = "draft-revision"
        app_id = "published-app"
        client = _NoUploadSubmitClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = BatchGroupSubmissionPlan(
                package_group=_make_package_group(root / "demo_2.0_amd64.deb", pkg_name="demo", pkg_version="2.0"),
                submission_mode="update",
                selected_match=StoreAppMatch(app_id, draft_id, "demo", "Demo"),
                app_name_zh="Demo", website="", short_desc_zh="", full_desc_zh="",
                category_id="1", region_codes=("1",), asset_dir=None,
                expected_draft_id=draft_id,
            )
            with (
                patch.object(client, "get_app_detail", create=True, return_value={
                    "app_basic_info": {"id": draft_id, "app_id": app_id, "status": 101},
                }),
                patch.object(client, "upload_file_bytes") as upload,
                patch.object(client, "submit_payload") as submit,
                patch.object(client, "update_draft", create=True) as update,
            ):
                result = submit_applications_batch(
                    LoginContext(client, "demo", None, "cached", False),
                    plans=(plan,), cache=object(), note="", release_key="stable", pkg_channel="stable",
                    selected_targets=(), output_dir=root / "output",
                )

        self.assertIn("草稿状态已变化", result.rows[0]["message"])
        upload.assert_not_called()
        submit.assert_not_called()
        update.assert_not_called()

    def test_submit_applications_batch_new_reuses_prepared_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            group = _make_package_group(
                temp_root / "brand-new_2.0.0_amd64.deb",
                pkg_name="brand-new",
                pkg_version="2.0.0",
            )
            login = LoginContext(
                client=object(),
                account_label="odatacc",
                session_state_path=None,
                login_mode="cached",
                can_use_browser_mode=True,
            )
            targets = (_target_option(group.packages[0].path, "11", "baseline-11"),)
            icon_path = temp_root / "prepared-icon.png"
            screen_1 = temp_root / "prepared-1.png"
            screen_2 = temp_root / "prepared-2.png"
            screen_3 = temp_root / "prepared-3.png"
            for path in (icon_path, screen_1, screen_2, screen_3):
                path.write_bytes(b"img")
            plans = (
                BatchGroupSubmissionPlan(
                    package_group=group,
                    submission_mode="new",
                    selected_match=None,
                    app_name_zh="Brand New",
                    website="https://new.example.com",
                    short_desc_zh="short",
                    full_desc_zh="full",
                    category_id="2",
                    region_codes=("1", "2"),
                    asset_dir=temp_root,
                    prepared_icon_path=icon_path,
                    prepared_screenshot_paths=(screen_1, screen_2, screen_3),
                    metadata_edited=True,
                ),
            )

            with (
                patch("ui.backend.preprocess_submission_assets", side_effect=AssertionError("should not preprocess again")),
                patch("ui.backend._build_localized_lan_texts", return_value={"zh_CN": {}, "en_US": {}}),
                patch(
                    "ui.backend._submit_grouped_release",
                    return_value=_submission_result(temp_root / "out/02", "brand-new", "2.0.0"),
                ) as submit_mock,
            ):
                submit_applications_batch(
                    login,
                    plans=plans,
                    cache=object(),
                    note="批量提交",
                    release_key="stable",
                    pkg_channel="stable",
                    selected_targets=targets,
                    output_dir=temp_root / "batch-output",
                )

        self.assertEqual(submit_mock.call_args.kwargs["assets"].icon_path, icon_path)
        self.assertEqual(submit_mock.call_args.kwargs["assets"].screenshot_paths, (screen_1, screen_2, screen_3))

    def test_build_localized_lan_texts_uses_manual_english_when_auto_disabled(self) -> None:
        localized = _build_localized_lan_texts(
            app_name_zh="示例应用",
            short_desc_zh="中文简介",
            full_desc_zh="中文详情",
            note="中文更新说明",
            region_codes=("1", "2"),
            existing_app_detail=None,
            manual_en_texts={
                "name": "Example App",
                "brief_info": "English summary",
                "desc_info": "English detail",
                "update_desc": "English note",
            },
            allow_auto_translate=False,
            log=None,
        )

        self.assertEqual(localized["en_US"]["name"], "Example App")
        self.assertEqual(localized["en_US"]["brief_info"], "English summary")
        self.assertEqual(localized["en_US"]["desc_info"], "English detail")
        self.assertEqual(localized["en_US"]["update_desc"], "English note")

    def test_build_existing_detail_editor_defaults_extracts_multilingual_fields(self) -> None:
        detail = {
            "datas": {
                "app_basic_info": {
                    "website": "https://example.test",
                    "category_id": 12,
                    "region": "1,2",
                },
                "app_fit_info": {
                    "region": [{"code": 1}, {"code": 2}],
                },
                "app_lan_infos": [
                    {
                        "lan": "zh_CN",
                        "name": "中文名",
                        "brief_info": "中文简介",
                        "desc_info": "中文详情",
                        "update_desc": "中文更新说明",
                        "dev_name": "中文开发者",
                    },
                    {
                        "lan": "en_US",
                        "name": "English Name",
                        "brief_info": "English brief",
                        "desc_info": "English detail",
                        "update_desc": "English update note",
                    },
                ],
            }
        }

        defaults = build_existing_detail_editor_defaults(detail, fallback_name="Fallback")

        self.assertEqual(defaults["app_name_zh"], "中文名")
        self.assertEqual(defaults["website"], "https://example.test")
        self.assertEqual(defaults["category_id"], "12")
        self.assertEqual(defaults["region_codes"], ("1", "2"))
        self.assertEqual(defaults["developer_name"], "中文开发者")
        self.assertEqual(defaults["app_name_en"], "English Name")
        self.assertEqual(defaults["short_desc_en"], "English brief")
        self.assertEqual(defaults["full_desc_en"], "English detail")
        self.assertEqual(defaults["note_en"], "English update note")

    def test_build_localized_lan_texts_requires_complete_manual_english_when_auto_disabled(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "英文文案未填写完整"):
            _build_localized_lan_texts(
                app_name_zh="示例应用",
                short_desc_zh="中文简介",
                full_desc_zh="中文详情",
                note="中文更新说明",
                region_codes=("1", "2"),
                existing_app_detail=None,
                manual_en_texts={"name": "Example App"},
                allow_auto_translate=False,
                log=None,
            )

    def test_submit_existing_applications_batch_submits_multiple_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            group_one = _make_package_group(temp_root / "labelnova_1.0.4-1_amd64.deb", pkg_name="labelnova", pkg_version="1.0.4-1")
            group_two = _make_package_group(temp_root / "uos-ai-agent_1.1.56_amd64.deb", pkg_name="uos-ai-agent", pkg_version="1.1.56")
            login = LoginContext(
                client=object(),
                account_label="odatacc",
                session_state_path=None,
                login_mode="cached",
                can_use_browser_mode=True,
            )
            targets = (
                _target_option(group_one.packages[0].path, "11", "baseline-11"),
                _target_option(group_two.packages[0].path, "11", "baseline-11"),
            )
            fake_detail = {
                "datas": {
                    "app_basic_info": {"category_id": 1, "website": "https://example.com", "region": "1"},
                    "app_lan_infos": [{"lan": "zh_CN", "name": "Demo", "brief_info": "short", "desc_info": "full"}],
                    "app_fit_info": {"region": [{"code": 1}]},
                }
            }

            with (
                patch(
                    "ui.backend.find_existing_apps",
                    side_effect=[
                        (StoreAppMatch(app_id="1001", detail_id="d1", pkg_name="labelnova", app_name="LabelNova"),),
                        (StoreAppMatch(app_id="1002", detail_id="d2", pkg_name="uos-ai-agent", app_name="UOS AI Agent"),),
                    ],
                ),
                patch("ui.backend.fetch_existing_app_detail", return_value=fake_detail),
                patch(
                    "ui.backend._submit_grouped_release",
                    side_effect=[
                        _submission_result(temp_root / "out/01", "labelnova", "1.0.4-1"),
                        _submission_result(temp_root / "out/02", "uos-ai-agent", "1.1.56"),
                    ],
                ),
            ):
                result = submit_existing_applications_batch(
                    login,
                    package_groups=(group_one, group_two),
                    cache=object(),
                    note="批量更新",
                    release_key="stable",
                    pkg_channel="stable",
                    selected_targets=targets,
                    output_dir=temp_root / "batch-output",
                )
                report_exists = result.report_path.exists()

        self.assertEqual(len(result.rows), 2)
        self.assertEqual([row["row_id"] for row in result.rows], [1, 2])
        self.assertEqual([row["app_key"] for row in result.rows], ["labelnova", "uos-ai-agent"])
        self.assertTrue(report_exists)

    def test_submit_existing_applications_batch_records_group_failure_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            group_one = _make_package_group(temp_root / "labelnova_1.0.4-1_amd64.deb", pkg_name="labelnova", pkg_version="1.0.4-1")
            group_two = _make_package_group(temp_root / "uos-ai-agent_1.1.56_amd64.deb", pkg_name="uos-ai-agent", pkg_version="1.1.56")
            login = LoginContext(
                client=object(),
                account_label="odatacc",
                session_state_path=None,
                login_mode="cached",
                can_use_browser_mode=True,
            )
            targets = (
                _target_option(group_one.packages[0].path, "11", "baseline-11"),
                _target_option(group_two.packages[0].path, "11", "baseline-11"),
            )
            fake_detail = {
                "datas": {
                    "app_basic_info": {"category_id": 1, "website": "https://example.com", "region": "1"},
                    "app_lan_infos": [{"lan": "zh_CN", "name": "Demo", "brief_info": "short", "desc_info": "full"}],
                    "app_fit_info": {"region": [{"code": 1}]},
                }
            }

            with (
                patch(
                    "ui.backend.find_existing_apps",
                    side_effect=[
                        (StoreAppMatch(app_id="1001", detail_id="d1", pkg_name="labelnova", app_name="LabelNova"),),
                        (),
                    ],
                ),
                patch("ui.backend.fetch_existing_app_detail", return_value=fake_detail),
                patch(
                    "ui.backend._submit_grouped_release",
                    return_value=_submission_result(temp_root / "out/01", "labelnova", "1.0.4-1"),
                ),
            ):
                result = submit_existing_applications_batch(
                    login,
                    package_groups=(group_one, group_two),
                    cache=object(),
                    note="批量更新",
                    release_key="stable",
                    pkg_channel="stable",
                    selected_targets=targets,
                    output_dir=temp_root / "batch-output",
                )

        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.rows[0]["status"], "submitted")
        self.assertEqual(result.rows[1]["status"], "submit_failed")
        self.assertIn("existing app not found", result.rows[1]["message"])

def _make_package_group(
    package_path: Path,
    *,
    pkg_name: str = "demo-app",
    pkg_version: str = "1.0.0",
    pkg_arch: str = "amd64",
    package_family: str = "deb",
    package_format: str = "deb",
) -> PackageGroup:
    return PackageGroup(
        packages=(
            PackageMetadata(
                path=package_path,
                package_family=package_family,
                package_format=package_format,
                pkg_name=pkg_name,
                pkg_version=pkg_version,
                pkg_arch=pkg_arch,
                pkg_size=1234,
                sha256="abc123",
                display_name=pkg_name,
                short_description="short",
                full_description="full",
                homepage="https://example.com",
            ),
        )
    )


def _target_option(package_path: Path, code: str, baseline_id: str) -> SystemTargetOption:
    return SystemTargetOption(
        package_path=str(package_path),
        package_label=package_path.name,
        package_arch="amd64",
        code=code,
        label="UOS",
        package_family="deb",
        baseline_options=((baseline_id, baseline_id),),
        selected=True,
        baseline_id=baseline_id,
    )


def _submission_result(output_dir: Path, pkg_name: str, pkg_version: str) -> object:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.json"
    report_path.write_text("{}", encoding="utf-8")
    from ui.backend import SubmissionResult

    return SubmissionResult(
        output_dir=output_dir,
        report_path=report_path,
        rows=(
            {
                "row_id": 1,
                "app_key": pkg_name,
                "deb_path": str(output_dir / f"{pkg_name}.deb"),
                "status": "submitted",
                "message": "submitted",
                "app_id": "1096227",
                "pkg_name": pkg_name,
                "pkg_version": pkg_version,
                "selector": "pkg:1",
            },
        ),
    )
