import unittest
from pathlib import Path

from appstore.models import (
    AppRecord,
    BaselineOption,
    CapabilityCache,
    PackageInfo,
    PackageRecord,
    ReleaseRecord,
    SystemLine,
    TargetRecord,
    UploadedFileRef,
)
from appstore.submission import (
    ValidationError,
    ValidatedRelease,
    build_release_payload,
    validate_release_group,
)


def _app() -> AppRecord:
    return AppRecord(
        app_key="demo",
        app_name_zh="演示应用",
        pkg_name="demo",
        category_id=7,
        website="https://example.com/app",
        short_desc_zh="简短说明",
        full_desc_zh="详细说明",
        icon_path=Path("/tmp/icon.png"),
        screenshot_paths=(Path("/tmp/shot-1.png"), Path("/tmp/shot-2.png"), Path("/tmp/shot-3.png")),
    )


def _release() -> ReleaseRecord:
    return ReleaseRecord(row_id=2, app_key="demo", release_key="stable", release_name="稳定版", region="1", note="")


def _cache() -> CapabilityCache:
    return CapabilityCache(
        generated_at="2026-04-22T12:00:00+08:00",
        deb_system_lines={
            "11": SystemLine(code="11", label="communityV23", family="deb"),
            "21": SystemLine(code="21", label="communityV25", family="deb"),
        },
        linglong_system_lines={
            "11": SystemLine(code="11", label="communityV23", family="linglong"),
            "21": SystemLine(code="21", label="communityV25", family="linglong"),
        },
        baseline_options={
            "deb:11": (BaselineOption(system_line_code="11", baseline_id="2300", minor_version="23.0.0"),),
            "deb:21": (BaselineOption(system_line_code="21", baseline_id="2500", minor_version="25.0.0"),),
            "linglong:11": (BaselineOption(system_line_code="11", baseline_id="2300", minor_version="23.0.0"),),
            "linglong:21": (BaselineOption(system_line_code="21", baseline_id="2500", minor_version="25.0.0"),),
        },
    )


class GroupedSubmissionTests(unittest.TestCase):
    def test_build_release_payload_reuses_existing_detail_for_update(self) -> None:
        app = _app()
        release = ReleaseRecord(
            row_id=2,
            app_key="demo",
            release_key="stable",
            release_name="稳定版",
            region="1",
            note="新增 arm64 包并更新说明",
        )
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-arm", "deb", "deb", Path("/tmp/demo-arm.deb"), declared_arch="arm64"),
        )
        inspected = {
            "pkg-arm": PackageInfo("demo", "1.1.0", "arm64", 11, "hash-arm", Path("/tmp/demo-arm.deb")),
        }
        targets = {
            "pkg-arm": (TargetRecord(30, "demo", "stable", "pkg-arm", "11", baseline_id="2300"),),
        }
        validated = validate_release_group(app, release, packages, targets, inspected, _cache())
        existing_detail = {
            "app_basic_info": {
                "id": "detail-uuid",
                "app_id": 42,
                "status": 101,
                "category_id": 9,
                "website": "https://existing.example/demo",
                "region": "1",
                "default_lan": "zh_CN",
                "pkg_mode": 0,
                "inAppPayment": 0,
            },
            "app_lan_infos": [
                {
                    "lan": "zh_CN",
                    "name": "旧名称",
                    "brief_info": "旧简介",
                    "desc_info": "旧详情",
                    "update_desc": "旧更新说明",
                    "icon_save_key": "existing-icon",
                    "appScreenShotList": [
                        {"screen_shot_key": "existing-shot-1", "image_mode": 1, "sort": 0},
                        {"screen_shot_key": "existing-shot-2", "image_mode": 1, "sort": 1},
                        {"screen_shot_key": "existing-shot-3", "image_mode": 1, "sort": 2},
                    ],
                }
            ],
            "app_fit_info": {
                "system_mode": [{"code": 1}],
                "system_platform": [{"code": 11}],
                "region": [{"code": 1}],
                "arch": [{"code": 4}],
                "baseline": None,
            },
            "app_origin_pkgs": [
                {
                    "pkg_name": "demo",
                    "pkg_version": "1.0.0",
                    "pkg_arch": "4",
                    "pkgArch": "X86",
                    "pkgType": 11,
                    "pkg_mode": 0,
                    "pkg_size": 10,
                    "sha256": "old-hash",
                    "file_save_key": "existing-x86",
                    "progressPercent": 101,
                    "supSys": "11",
                    "supBlineVer": "",
                    "unsupportBlineVers": "",
                    "systemStr": "社区版V23",
                }
            ],
        }

        payload = build_release_payload(
            validated,
            uploads_by_package={
                "pkg-arm": UploadedFileRef(kind="temppkg", file_save_key="pkg-arm-key", size=11, file_hash="hash-arm"),
            },
            app_uploads=None,
            target_app_id="42",
            existing_app_detail=existing_detail,
        )

        self.assertEqual(payload["app_id"], "42")
        self.assertEqual(payload["app_info"]["app_lan_infos"][0]["icon_save_key"], "existing-icon")
        self.assertEqual(payload["app_info"]["app_lan_infos"][0]["update_desc"], "新增 arm64 包并更新说明")
        self.assertEqual(payload["app_info"]["app_basic_info"]["category_id"], 9)
        self.assertEqual(payload["app_info"]["app_basic_info"]["id"], "detail-uuid")
        self.assertEqual(payload["app_info"]["app_basic_info"]["app_id"], 42)
        self.assertEqual(payload["app_info"]["app_basic_info"]["status"], 101)
        self.assertEqual(len(payload["app_info"]["app_origin_pkgs"]), 2)
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["file_save_key"], "existing-x86")
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][1]["file_save_key"], "pkg-arm-key")
        self.assertEqual(payload["app_info"]["app_fit_info"]["arch"], [{"code": 4}, {"code": 3}])

    def test_build_release_payload_reuses_existing_package_without_upload(self) -> None:
        app = _app()
        release = ReleaseRecord(
            row_id=2,
            app_key="demo",
            release_key="stable",
            release_name="稳定版",
            region="1",
            note="只更新文案和系统线",
        )
        packages = (
            PackageRecord(
                20,
                "demo",
                "stable",
                "pkg-online-x86",
                "deb",
                "deb",
                Path("online/42/0/demo/1.0.0/x86"),
                declared_arch="amd64",
            ),
        )
        inspected = {
            "pkg-online-x86": PackageInfo("demo", "1.0.0", "amd64", 10, "old-hash", packages[0].file_path),
        }
        targets = {
            "pkg-online-x86": (TargetRecord(30, "demo", "stable", "pkg-online-x86", "21", baseline_id="2500"),),
        }
        validated = validate_release_group(app, release, packages, targets, inspected, _cache())
        existing_detail = {
            "app_basic_info": {"category_id": 9, "website": "https://existing.example/demo", "region": "1"},
            "app_lan_infos": [
                {
                    "lan": "zh_CN",
                    "name": "旧名称",
                    "brief_info": "旧简介",
                    "desc_info": "旧详情",
                    "update_desc": "旧更新说明",
                    "icon_save_key": "existing-icon",
                    "appScreenShotList": [{"screen_shot_key": "existing-shot-1", "image_mode": 1, "sort": 0}],
                }
            ],
            "app_fit_info": {
                "system_mode": [{"code": 1}],
                "system_platform": [{"code": 11}],
                "region": [{"code": 1}],
                "arch": [{"code": 4}],
                "baseline": ["2300"],
            },
            "app_origin_pkgs": [
                {
                    "pkg_name": "demo",
                    "pkg_version": "1.0.0",
                    "pkg_arch": "4",
                    "pkgArch": "X86",
                    "pkgType": 11,
                    "pkg_mode": 0,
                    "pkg_size": 10,
                    "sha256": "old-hash",
                    "file_save_key": "existing-x86",
                    "progressPercent": 100,
                    "upload_time": "2026-04-24 10:19:33",
                    "supSys": "11",
                    "supBlineVer": "2300",
                    "systemStr": "社区版V23",
                }
            ],
        }

        payload = build_release_payload(
            validated,
            uploads_by_package={},
            app_uploads=None,
            target_app_id="42",
            existing_app_detail=existing_detail,
            existing_app_overrides={
                "app_name_zh": "新名称",
                "short_desc_zh": "新简介",
                "full_desc_zh": "新详情",
            },
        )

        origin_pkgs = payload["app_info"]["app_origin_pkgs"]
        self.assertEqual(len(origin_pkgs), 1)
        self.assertEqual(origin_pkgs[0]["file_save_key"], "existing-x86")
        self.assertEqual(origin_pkgs[0]["system_platform"], ["21"])
        self.assertEqual(origin_pkgs[0]["baseline"], [{"system_platform": 21, "id": "2500"}])
        self.assertEqual(payload["app_info"]["app_fit_info"]["system_platform"], [{"code": 21}])
        self.assertEqual(payload["app_info"]["app_fit_info"]["baseline"], [{"id": "2500"}])
        self.assertEqual(payload["app_info"]["app_lan_infos"][0]["name"], "新名称")
        self.assertEqual(payload["app_info"]["app_lan_infos"][0]["update_desc"], "只更新文案和系统线")

    def test_build_release_payload_carries_cpu_and_motherboard_fit_options(self) -> None:
        app = _app()
        release = ReleaseRecord(
            row_id=2,
            app_key="demo",
            release_key="stable",
            release_name="稳定版",
            region="1",
            note="",
            cpu_clip_codes=("0", "3", "4"),
            motherboard_codes=("1",),
        )
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-arm", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="arm64"),
        )
        inspected = {
            "pkg-arm": PackageInfo("demo", "1.0.1", "arm64", 11, "hash-arm", Path("/tmp/demo-1.deb")),
        }
        targets = {"pkg-arm": (TargetRecord(30, "demo", "stable", "pkg-arm", "11", baseline_id="2300"),)}
        validated = validate_release_group(app, release, packages, targets, inspected, _cache())

        payload = build_release_payload(
            validated,
            uploads_by_package={
                "pkg-arm": UploadedFileRef(kind="temppkg", file_save_key="pkg-arm-key", size=11, file_hash="hash-arm"),
            },
            app_uploads={
                "icon": UploadedFileRef(kind="icon", file_save_key="icon-key", size=1, file_hash="icon-hash"),
                "screenshots": (
                    UploadedFileRef(kind="image", file_save_key="shot-1", size=1, file_hash="shot-1"),
                    UploadedFileRef(kind="image", file_save_key="shot-2", size=1, file_hash="shot-2"),
                    UploadedFileRef(kind="image", file_save_key="shot-3", size=1, file_hash="shot-3"),
                ),
            },
            target_app_id="",
        )

        fit_info = payload["app_info"]["app_fit_info"]
        self.assertEqual(fit_info["cpu_clip"], [{"code": 0}, {"code": 3}, {"code": 4}])
        self.assertEqual(fit_info["motherboard"], [{"code": 1}])

    def test_build_release_payload_overrides_existing_screenshots_when_uploads_provided(self) -> None:
        app = _app()
        release = ReleaseRecord(
            row_id=2,
            app_key="demo",
            release_key="stable",
            release_name="稳定版",
            region="1",
            note="替换截图",
        )
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-arm", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="arm64"),
        )
        inspected = {
            "pkg-arm": PackageInfo("demo", "1.0.1", "arm64", 11, "hash-arm", Path("/tmp/demo-1.deb")),
        }
        targets = {"pkg-arm": (TargetRecord(30, "demo", "stable", "pkg-arm", "11", baseline_id="2300"),)}
        validated = validate_release_group(app, release, packages, targets, inspected, _cache())
        existing_detail = {
            "app_basic_info": {"category_id": 9, "website": "https://existing.example/demo", "region": "1"},
            "app_lan_infos": [
                {
                    "lan": "zh_CN",
                    "name": "旧名称",
                    "brief_info": "旧简介",
                    "desc_info": "旧详情",
                    "update_desc": "旧更新说明",
                    "icon_save_key": "existing-icon",
                    "appScreenShotList": [
                        {"screen_shot_key": "existing-shot-1", "image_mode": 1, "sort": 0},
                    ],
                }
            ],
            "app_fit_info": {
                "system_mode": [{"code": 1}],
                "system_platform": [{"code": 11}],
                "region": [{"code": 1}],
                "arch": [{"code": 4}],
                "baseline": None,
            },
            "app_origin_pkgs": [],
        }

        payload = build_release_payload(
            validated,
            uploads_by_package={
                "pkg-arm": UploadedFileRef(kind="temppkg", file_save_key="pkg-arm-key", size=11, file_hash="hash-arm"),
            },
            app_uploads={
                "screenshots": (
                    UploadedFileRef(kind="image", file_save_key="new-shot-1", size=4, file_hash="new-shot-1-hash"),
                    UploadedFileRef(kind="image", file_save_key="new-shot-2", size=5, file_hash="new-shot-2-hash"),
                    UploadedFileRef(kind="image", file_save_key="new-shot-3", size=6, file_hash="new-shot-3-hash"),
                ),
            },
            target_app_id="42",
            existing_app_detail=existing_detail,
        )

        screenshots = payload["app_info"]["app_lan_infos"][0]["appScreenShotList"]
        self.assertEqual([shot["screen_shot_key"] for shot in screenshots], ["new-shot-1", "new-shot-2", "new-shot-3"])
        self.assertEqual(payload["app_info"]["app_lan_infos"][0]["icon_save_key"], "existing-icon")

    def test_build_release_payload_applies_existing_app_overrides_when_provided(self) -> None:
        app = _app()
        release = ReleaseRecord(
            row_id=2,
            app_key="demo",
            release_key="stable",
            release_name="稳定版",
            region="1,2",
            note="改基础信息",
        )
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-arm", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="arm64"),
        )
        inspected = {
            "pkg-arm": PackageInfo("demo", "1.0.1", "arm64", 11, "hash-arm", Path("/tmp/demo-1.deb")),
        }
        targets = {"pkg-arm": (TargetRecord(30, "demo", "stable", "pkg-arm", "11", baseline_id="2300"),)}
        validated = validate_release_group(app, release, packages, targets, inspected, _cache())
        existing_detail = {
            "app_basic_info": {"category_id": 9, "website": "https://existing.example/demo", "region": "1", "default_lan": "zh_CN"},
            "app_lan_infos": [
                {
                    "lan": "zh_CN",
                    "name": "旧名称",
                    "brief_info": "旧简介",
                    "desc_info": "旧详情",
                    "update_desc": "旧更新说明",
                    "icon_save_key": "existing-icon",
                    "appScreenShotList": [
                        {"screen_shot_key": "existing-shot-1", "image_mode": 1, "sort": 0},
                    ],
                }
            ],
            "app_fit_info": {
                "system_mode": [{"code": 1}],
                "system_platform": [{"code": 11}],
                "region": [{"code": 1}],
                "arch": [{"code": 4}],
                "baseline": None,
            },
            "app_origin_pkgs": [],
        }

        payload = build_release_payload(
            validated,
            uploads_by_package={
                "pkg-arm": UploadedFileRef(kind="temppkg", file_save_key="pkg-arm-key", size=11, file_hash="hash-arm"),
            },
            app_uploads=None,
            target_app_id="42",
            existing_app_detail=existing_detail,
            existing_app_overrides={
                "app_name_zh": "新名称",
                "short_desc_zh": "新简介",
                "full_desc_zh": "新详情",
                "category_id": 3,
                "website": "https://override.example/demo",
            },
        )

        lan_info = payload["app_info"]["app_lan_infos"][0]
        self.assertEqual(lan_info["name"], "新名称")
        self.assertEqual(lan_info["brief_info"], "新简介")
        self.assertEqual(lan_info["desc_info"], "新详情")
        self.assertEqual(lan_info["update_desc"], "改基础信息")
        self.assertEqual(payload["app_info"]["app_basic_info"]["category_id"], 3)
        self.assertEqual(payload["app_info"]["app_basic_info"]["website"], "https://override.example/demo")
        self.assertEqual(payload["app_info"]["app_basic_info"]["region"], "1,2")

    def test_build_release_payload_builds_multilingual_copy_for_other_regions(self) -> None:
        app = _app()
        release = ReleaseRecord(
            row_id=2,
            app_key="demo",
            release_key="stable",
            release_name="稳定版",
            region="1,2",
            note="修复登录问题",
        )
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-a", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="amd64"),
        )
        inspected = {
            "pkg-a": PackageInfo("demo", "1.0.0", "amd64", 1, "hash1", Path("/tmp/demo-1.deb")),
        }
        targets = {"pkg-a": (TargetRecord(30, "demo", "stable", "pkg-a", "11", baseline_id="2300"),)}
        validated = validate_release_group(app, release, packages, targets, inspected, _cache())

        payload = build_release_payload(
            validated,
            uploads_by_package={"pkg-a": UploadedFileRef(kind="temppkg", file_save_key="pkg-a-key", size=1, file_hash="hash1")},
            app_uploads={
                "icon": UploadedFileRef(kind="icon", file_save_key="icon-key", size=3, file_hash="icon-hash"),
                "screenshots": (
                    UploadedFileRef(kind="image", file_save_key="shot-1", size=4, file_hash="shot-1-hash"),
                    UploadedFileRef(kind="image", file_save_key="shot-2", size=5, file_hash="shot-2-hash"),
                    UploadedFileRef(kind="image", file_save_key="shot-3", size=6, file_hash="shot-3-hash"),
                ),
            },
            target_app_id="",
            developer_name="徐浩",
            localized_lan_texts={
                "zh_CN": {"name": "演示应用", "brief_info": "简短说明", "desc_info": "详细说明", "update_desc": "修复登录问题"},
                "en_US": {"name": "Demo App", "brief_info": "Brief", "desc_info": "Full description", "update_desc": "Fix login issues"},
            },
            desired_lans=("zh_CN", "en_US"),
        )

        self.assertEqual([item["lan"] for item in payload["app_info"]["app_lan_infos"]], ["zh_CN", "en_US"])
        self.assertEqual(payload["app_info"]["app_lan_infos"][1]["name"], "Demo App")
        self.assertEqual(payload["app_info"]["app_lan_infos"][1]["update_desc"], "Fix login issues")

    def test_validate_release_group_rejects_mixed_package_families(self) -> None:
        app = _app()
        release = _release()
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-deb", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="amd64"),
            PackageRecord(21, "demo", "stable", "pkg-uab", "linglong", "uab", Path("/tmp/demo-2.uab"), declared_arch="x86_64"),
        )
        inspected = {
            "pkg-deb": PackageInfo("demo", "1.0.0", "amd64", 1, "hash1", Path("/tmp/demo-1.deb")),
            "pkg-uab": PackageInfo("demo", "2.0.0", "x86_64", 2, "hash2", Path("/tmp/demo-2.uab"), package_family="linglong", package_format="uab"),
        }
        targets = {
            "pkg-deb": (TargetRecord(30, "demo", "stable", "pkg-deb", "11", baseline_id="2300"),),
            "pkg-uab": (TargetRecord(31, "demo", "stable", "pkg-uab", "11", baseline_id="2300"),),
        }

        with self.assertRaisesRegex(ValidationError, "mix deb and linglong"):
            validate_release_group(app, release, packages, targets, inspected, _cache())

    def test_validate_release_group_returns_multi_package_release(self) -> None:
        app = _app()
        release = _release()
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-a", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="amd64", pkg_channel="stable"),
            PackageRecord(21, "demo", "stable", "pkg-b", "deb", "deb", Path("/tmp/demo-2.deb"), declared_arch="amd64", pkg_channel="beta"),
        )
        inspected = {
            "pkg-a": PackageInfo("demo", "1.0.0", "amd64", 1, "hash1", Path("/tmp/demo-1.deb")),
            "pkg-b": PackageInfo("demo", "2.0.0", "amd64", 2, "hash2", Path("/tmp/demo-2.deb")),
        }
        targets = {
            "pkg-a": (TargetRecord(30, "demo", "stable", "pkg-a", "11", baseline_id="2300"),),
            "pkg-b": (TargetRecord(31, "demo", "stable", "pkg-b", "21", baseline_id="2500"),),
        }

        validated = validate_release_group(app, release, packages, targets, inspected, _cache())
        self.assertIsInstance(validated, ValidatedRelease)
        self.assertEqual(validated.package_family, "deb")
        self.assertEqual(len(validated.packages), 2)

        payload = build_release_payload(
            validated,
            uploads_by_package={
                "pkg-a": UploadedFileRef(kind="temppkg", file_save_key="pkg-a-key", size=1, file_hash="hash1"),
                "pkg-b": UploadedFileRef(kind="temppkg", file_save_key="pkg-b-key", size=2, file_hash="hash2"),
            },
            app_uploads={
                "icon": UploadedFileRef(kind="icon", file_save_key="icon-key", size=3, file_hash="icon-hash"),
                "screenshots": (
                    UploadedFileRef(kind="image", file_save_key="shot-1", size=4, file_hash="shot-1-hash"),
                    UploadedFileRef(kind="image", file_save_key="shot-2", size=5, file_hash="shot-2-hash"),
                    UploadedFileRef(kind="image", file_save_key="shot-3", size=6, file_hash="shot-3-hash"),
                ),
            },
            target_app_id="42",
            developer_name="徐浩",
        )

        self.assertEqual(payload["app_id"], "42")
        self.assertEqual(payload["app_info"]["app_basic_info"]["pkgInstallMode"], 1)
        self.assertEqual(payload["app_info"]["app_lan_infos"][0]["dev_name"], "徐浩")
        self.assertEqual(len(payload["app_info"]["app_origin_pkgs"]), 2)
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["pkgType"], 11)
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["sums"], 0)
        self.assertTrue(payload["app_info"]["app_origin_pkgs"][0]["upload_time"].strip())
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["system_platform"], ["11"])
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["systemStr"], "communityV23")
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][1]["system_platform"], ["21"])
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][1]["systemStr"], "communityV25")
        self.assertEqual(payload["app_info"]["app_fit_info"]["system_platform"], [{"code": "11"}, {"code": "21"}])
        self.assertEqual(payload["app_info"]["app_fit_info"]["arch"], [{"code": "4"}])
        self.assertEqual(payload["app_info"]["app_fit_info"]["baseline"], [{"id": "2300"}, {"id": "2500"}])

    def test_build_release_payload_supports_multiple_baselines_per_system_line(self) -> None:
        app = _app()
        release = _release()
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-a", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="amd64"),
        )
        inspected = {
            "pkg-a": PackageInfo("demo", "1.0.0", "amd64", 1, "hash1", Path("/tmp/demo-1.deb")),
        }
        cache = CapabilityCache(
            generated_at="2026-04-22T12:00:00+08:00",
            deb_system_lines={
                "11": SystemLine(code="11", label="communityV23", family="deb"),
            },
            linglong_system_lines={},
            baseline_options={
                "deb:11": (
                    BaselineOption(system_line_code="11", baseline_id="2300", minor_version="23.0.0"),
                    BaselineOption(system_line_code="11", baseline_id="2301", minor_version="23.0.1"),
                ),
            },
        )
        targets = {
            "pkg-a": (
                TargetRecord(
                    30,
                    "demo",
                    "stable",
                    "pkg-a",
                    "11",
                    baseline_id="2300",
                    baseline_ids=("2300", "2301"),
                ),
            ),
        }

        validated = validate_release_group(app, release, packages, targets, inspected, cache)
        payload = build_release_payload(
            validated,
            uploads_by_package={
                "pkg-a": UploadedFileRef(kind="temppkg", file_save_key="pkg-a-key", size=1, file_hash="hash1"),
            },
            app_uploads={
                "icon": UploadedFileRef(kind="icon", file_save_key="icon-key", size=3, file_hash="icon-hash"),
                "screenshots": (
                    UploadedFileRef(kind="image", file_save_key="shot-1", size=4, file_hash="shot-1-hash"),
                    UploadedFileRef(kind="image", file_save_key="shot-2", size=5, file_hash="shot-2-hash"),
                    UploadedFileRef(kind="image", file_save_key="shot-3", size=6, file_hash="shot-3-hash"),
                ),
            },
            target_app_id="42",
        )

        self.assertEqual(
            payload["app_info"]["app_origin_pkgs"][0]["baseline"],
            [{"system_platform": 11, "id": "2300"}, {"system_platform": 11, "id": "2301"}],
        )
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["supBlineVer"], "2300,2301")
        self.assertEqual(payload["app_info"]["app_fit_info"]["baseline"], [{"id": "2300"}, {"id": "2301"}])

    def test_validate_release_group_rejects_same_arch_system_version_collision(self) -> None:
        app = _app()
        release = _release()
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-a", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="amd64"),
            PackageRecord(21, "demo", "stable", "pkg-b", "deb", "deb", Path("/tmp/demo-2.deb"), declared_arch="amd64"),
        )
        inspected = {
            "pkg-a": PackageInfo("demo", "1.0.0", "amd64", 1, "hash1", Path("/tmp/demo-1.deb")),
            "pkg-b": PackageInfo("demo", "1.0.0", "amd64", 2, "hash2", Path("/tmp/demo-2.deb")),
        }
        targets = {
            "pkg-a": (TargetRecord(30, "demo", "stable", "pkg-a", "11", baseline_id="2300"),),
            "pkg-b": (TargetRecord(31, "demo", "stable", "pkg-b", "11", baseline_id="2300"),),
        }

        with self.assertRaisesRegex(ValidationError, "same architecture/system/version collision"):
            validate_release_group(app, release, packages, targets, inspected, _cache())

    def test_validate_release_group_rejects_missing_or_invalid_baseline_when_required(self) -> None:
        app = _app()
        release = _release()
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-a", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="amd64"),
        )
        inspected = {"pkg-a": PackageInfo("demo", "1.0.0", "amd64", 1, "hash1", Path("/tmp/demo-1.deb"))}

        missing_baseline_targets = {"pkg-a": (TargetRecord(30, "demo", "stable", "pkg-a", "11", baseline_id=""),)}
        with self.assertRaisesRegex(ValidationError, "baseline required"):
            validate_release_group(app, release, packages, missing_baseline_targets, inspected, _cache())

        invalid_baseline_targets = {"pkg-a": (TargetRecord(30, "demo", "stable", "pkg-a", "11", baseline_id="9999"),)}
        with self.assertRaisesRegex(ValidationError, "unsupported baseline"):
            validate_release_group(app, release, packages, invalid_baseline_targets, inspected, _cache())

    def test_validate_release_group_accepts_declared_arch_alias_equivalence(self) -> None:
        app = _app()
        release = _release()
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-a", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="x86_64"),
        )
        inspected = {"pkg-a": PackageInfo("demo", "1.0.0", "amd64", 1, "hash1", Path("/tmp/demo-1.deb"))}
        targets = {"pkg-a": (TargetRecord(30, "demo", "stable", "pkg-a", "11", baseline_id="2300"),)}

        validated = validate_release_group(app, release, packages, targets, inspected, _cache())
        payload = build_release_payload(
            validated,
            uploads_by_package={"pkg-a": UploadedFileRef(kind="temppkg", file_save_key="pkg-a-key", size=1, file_hash="hash1")},
            app_uploads={
                "icon": UploadedFileRef(kind="icon", file_save_key="icon-key", size=3, file_hash="icon-hash"),
                "screenshots": (
                    UploadedFileRef(kind="image", file_save_key="shot-1", size=4, file_hash="shot-1-hash"),
                    UploadedFileRef(kind="image", file_save_key="shot-2", size=5, file_hash="shot-2-hash"),
                    UploadedFileRef(kind="image", file_save_key="shot-3", size=6, file_hash="shot-3-hash"),
                ),
            },
            target_app_id="",
        )

        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["pkg_arch"], "4")
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["pkgArch"], "X86")

    def test_validate_release_group_rejects_alias_equivalent_arch_collision(self) -> None:
        app = _app()
        release = _release()
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-a", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="amd64"),
            PackageRecord(21, "demo", "stable", "pkg-b", "deb", "deb", Path("/tmp/demo-2.deb"), declared_arch="x86_64"),
        )
        inspected = {
            "pkg-a": PackageInfo("demo", "1.0.0", "amd64", 1, "hash1", Path("/tmp/demo-1.deb")),
            "pkg-b": PackageInfo("demo", "1.0.0", "x86_64", 2, "hash2", Path("/tmp/demo-2.deb")),
        }
        targets = {
            "pkg-a": (TargetRecord(30, "demo", "stable", "pkg-a", "11", baseline_id="2300"),),
            "pkg-b": (TargetRecord(31, "demo", "stable", "pkg-b", "11", baseline_id="2300"),),
        }

        with self.assertRaisesRegex(ValidationError, "same architecture/system/version collision"):
            validate_release_group(app, release, packages, targets, inspected, _cache())

    def test_validate_release_group_rejects_unknown_system_line(self) -> None:
        app = _app()
        release = _release()
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-a", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="amd64"),
        )
        inspected = {"pkg-a": PackageInfo("demo", "1.0.0", "amd64", 1, "hash1", Path("/tmp/demo-1.deb"))}
        targets = {"pkg-a": (TargetRecord(30, "demo", "stable", "pkg-a", "99", baseline_id="2300"),)}

        with self.assertRaisesRegex(ValidationError, "unsupported system line"):
            validate_release_group(app, release, packages, targets, inspected, _cache())

    def test_validate_release_group_rejects_invalid_family_format_combination(self) -> None:
        app = _app()
        release = _release()
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-a", "deb", "uab", Path("/tmp/demo-1.uab"), declared_arch="amd64"),
        )
        inspected = {
            "pkg-a": PackageInfo(
                "demo",
                "1.0.0",
                "amd64",
                1,
                "hash1",
                Path("/tmp/demo-1.uab"),
                package_family="deb",
                package_format="uab",
            )
        }
        targets = {"pkg-a": (TargetRecord(30, "demo", "stable", "pkg-a", "11", baseline_id="2300"),)}

        with self.assertRaisesRegex(ValidationError, "unsupported package family/format"):
            validate_release_group(app, release, packages, targets, inspected, _cache())

    def test_validate_release_group_rejects_package_name_and_declared_arch_mismatch(self) -> None:
        app = _app()
        release = _release()
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-a", "deb", "deb", Path("/tmp/demo-1.deb"), declared_arch="amd64"),
        )
        inspected = {"pkg-a": PackageInfo("wrong-name", "1.0.0", "amd64", 1, "hash1", Path("/tmp/demo-1.deb"))}
        targets = {"pkg-a": (TargetRecord(30, "demo", "stable", "pkg-a", "11", baseline_id="2300"),)}

        with self.assertRaisesRegex(ValidationError, "package name mismatch"):
            validate_release_group(app, release, packages, targets, inspected, _cache())

        inspected = {"pkg-a": PackageInfo("demo", "1.0.0", "arm64", 1, "hash1", Path("/tmp/demo-1.deb"))}
        with self.assertRaisesRegex(ValidationError, "declared arch mismatch"):
            validate_release_group(app, release, packages, targets, inspected, _cache())

    def test_validate_release_group_builds_linglong_payload_flags(self) -> None:
        app = _app()
        release = _release()
        packages = (
            PackageRecord(20, "demo", "stable", "pkg-a", "linglong", "uab", Path("/tmp/demo-1.uab"), declared_arch="x86_64"),
        )
        inspected = {
            "pkg-a": PackageInfo(
                "demo",
                "1.0.0",
                "x86_64",
                1,
                "hash1",
                Path("/tmp/demo-1.uab"),
                package_family="linglong",
                package_format="uab",
            )
        }
        targets = {"pkg-a": (TargetRecord(30, "demo", "stable", "pkg-a", "11", baseline_id="2300"),)}

        validated = validate_release_group(app, release, packages, targets, inspected, _cache())
        payload = build_release_payload(
            validated,
            uploads_by_package={
                "pkg-a": UploadedFileRef(kind="temppkg", file_save_key="pkg-a-key", size=1, file_hash="hash1")
            },
            app_uploads={
                "icon": UploadedFileRef(kind="icon", file_save_key="icon-key", size=3, file_hash="icon-hash"),
                "screenshots": (
                    UploadedFileRef(kind="image", file_save_key="shot-1", size=4, file_hash="shot-1-hash"),
                    UploadedFileRef(kind="image", file_save_key="shot-2", size=5, file_hash="shot-2-hash"),
                    UploadedFileRef(kind="image", file_save_key="shot-3", size=6, file_hash="shot-3-hash"),
                ),
            },
            target_app_id="",
        )

        self.assertEqual(payload["app_info"]["app_basic_info"]["pkgInstallMode"], 2)
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["pkgType"], 22)


if __name__ == "__main__":
    unittest.main()
