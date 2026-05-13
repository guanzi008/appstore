import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appstore.models import BaselineOption, CapabilityCache, StoreAdaptOption, SystemLine
from appstore.capture_workflow import CapturePackageResult
from appstore.session_state import BrowserSessionState, SessionStateStore
from ui.assets import AssetBundle, _prepare_screenshot
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
from ui.qt_compat import QtGui
from ui.package_meta import PackageGroup, PackageMetadata
from ui.preferences import PreferenceStore, UIPreferences
from ui.cpp_bridge import (
    _group_payload_to_package_group,
    _group_payload_to_targets,
    _group_to_json,
    _online_group_from_detail,
    _sync_existing_detail_assets,
)


class _CredentialClient:
    def __init__(self, session=None) -> None:
        self.session = session

    async def _login_and_export_state(self, username: str, password: str):
        return (
            [{"name": "token", "value": "abc", "domain": ".uniontech.com", "path": "/"}],
            {"authorization": "Bearer abc"},
            {},
        )

    def fetch_dev_info(self):
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


class UiBackendCacheTests(unittest.TestCase):
    @staticmethod
    def _write_test_image(path: Path, width: int, height: int) -> None:
        image_format = (
            QtGui.QImage.Format.Format_RGB32
            if hasattr(QtGui.QImage, "Format")
            else QtGui.QImage.Format_RGB32
        )
        image = QtGui.QImage(width, height, image_format)
        image.fill(QtGui.QColor(70, 120, 180))
        if not image.save(str(path), "PNG"):
            raise RuntimeError(f"failed to write test image: {path}")

    def test_prepare_screenshot_normalizes_landscape_to_store_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "wide.png"
            self._write_test_image(source, 1920, 1080)

            result = _prepare_screenshot(source, root / "out" / "screen")
            image = QtGui.QImage(str(result))

            self.assertEqual((image.width(), image.height()), (1620, 1080))
            self.assertEqual(image.width() * 2, image.height() * 3)
            self.assertTrue(result.exists())

    def test_prepare_screenshot_normalizes_portrait_to_store_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "portrait.png"
            self._write_test_image(source, 1000, 2000)

            result = _prepare_screenshot(source, root / "out" / "screen")
            image = QtGui.QImage(str(result))

            self.assertEqual((image.width(), image.height()), (900, 1600))
            self.assertEqual(image.width() * 16, image.height() * 9)
            self.assertTrue(result.exists())

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
    def test_round_trips_last_session_account(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = PreferenceStore(Path(temp_dir) / "preferences.json")
            store.save(UIPreferences(last_session_account="manual-login"))
            loaded = store.load()
            self.assertEqual(loaded.last_session_account, "manual-login")


class UiCppBridgeOnlineDetailTests(unittest.TestCase):
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
    def test_build_target_options_defaults_to_latest_baseline(self) -> None:
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
            self.assertEqual(options[0].selected_baseline_ids, ("2301",))

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

def _make_package_group(package_path: Path, *, pkg_name: str = "demo-app", pkg_version: str = "1.0.0") -> PackageGroup:
    return PackageGroup(
        packages=(
            PackageMetadata(
                path=package_path,
                package_family="deb",
                package_format="deb",
                pkg_name=pkg_name,
                pkg_version=pkg_version,
                pkg_arch="amd64",
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
