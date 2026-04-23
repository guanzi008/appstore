import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import appstore.upload_batch as upload_batch
from appstore.appstore_client import AuthenticationError
from appstore.capabilities import CapabilityCache
from appstore.models import (
    AppRecord,
    BaselineOption,
    DebPackageInfo,
    LoadedManifest,
    PackageInfo,
    PackageRecord,
    ReleaseRecord,
    RowResult,
    SystemLine,
    TargetRecord,
)
from appstore.submission import ValidatedPackage, ValidatedRelease
from appstore.upload_batch import main, run_batch


class FakeClient:
    def __init__(self) -> None:
        self.login_calls: list[tuple[str, str]] = []
        self.find_calls: list[str] = []
        self.upload_calls: list[tuple[str, bytes, str]] = []
        self.submit_calls: list[dict] = []
        self.matches_by_pkg_name: dict[str, list[dict]] = {}
        self.detail_by_id: dict[str, dict] = {}
        self.login_error: Exception | None = None

    def login(self, username: str, password: str) -> None:
        self.login_calls.append((username, password))
        if self.login_error is not None:
            raise self.login_error

    def find_apps_by_pkg_name(self, pkg_name: str) -> list[dict]:
        self.find_calls.append(pkg_name)
        return list(self.matches_by_pkg_name.get(pkg_name, []))

    def upload_file_bytes(self, filename: str, data: bytes, upload_type: str):
        self.upload_calls.append((filename, data, upload_type))
        return type(
            "UploadedRef",
            (),
            {
                "kind": upload_type,
                "file_save_key": f"{upload_type}:{filename}",
                "size": len(data),
                "file_hash": f"hash-{filename}",
            },
        )()

    def get_app_detail(self, detail_id: str) -> dict:
        return dict(self.detail_by_id[detail_id])

    def submit_payload(self, payload: dict) -> dict:
        self.submit_calls.append(payload)
        return {}


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data=None) -> None:
        self.status_code = status_code
        self._json_data = {} if json_data is None else json_data

    def json(self):
        return self._json_data


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._responses.pop(0)


class CompatibilityClient:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self.login_calls: list[tuple[str, str]] = []
        self.find_calls: list[str] = []
        self.upload_calls: list[tuple[str, bytes, str]] = []
        self.matches_by_pkg_name: dict[str, list[dict]] = {}
        self.detail_by_id: dict[str, dict] = {}

    def login(self, username: str, password: str) -> None:
        self.login_calls.append((username, password))

    def find_apps_by_pkg_name(self, pkg_name: str) -> list[dict]:
        self.find_calls.append(pkg_name)
        return list(self.matches_by_pkg_name.get(pkg_name, []))

    def upload_file_bytes(self, filename: str, data: bytes, upload_type: str):
        self.upload_calls.append((filename, data, upload_type))
        return type(
            "UploadedRef",
            (),
            {
                "kind": upload_type,
                "file_save_key": f"{upload_type}:{filename}",
                "size": len(data),
                "file_hash": f"hash-{filename}",
            },
        )()

    def get_app_detail(self, detail_id: str) -> dict:
        return dict(self.detail_by_id[detail_id])


class SequencedSubmitClient(FakeClient):
    def __init__(self, responses: list[dict]) -> None:
        super().__init__()
        self._responses = list(responses)

    def submit_payload(self, payload: dict) -> dict:
        self.submit_calls.append(payload)
        return self._responses.pop(0)


class FakeBrowserRunner:
    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[dict] = []

    def submit_release_group(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "BrowserResult",
            (),
            {
                "app_id": "browser-app-id",
                "status_text": "审核中",
                "artifact_dir": kwargs["artifact_root"],
                "detail_id": "detail-id",
            },
        )()


class RunBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

        self.icon_path = self.root / "icon.png"
        self.icon_path.write_bytes(b"icon-bytes")
        self.screenshot_paths = []
        for index in range(1, 4):
            path = self.root / f"shot-{index}.png"
            path.write_bytes(f"shot-{index}".encode("utf-8"))
            self.screenshot_paths.append(path)
        self.deb_paths = [self.root / "alpha.deb", self.root / "beta.deb"]
        for path in self.deb_paths:
            path.write_bytes(b"deb-bytes")

        app = AppRecord(
            app_key="demo-app",
            app_name_zh="演示应用",
            pkg_name="demo-app",
            category_id=7,
            website="https://example.com/app",
            short_desc_zh="简短说明",
            full_desc_zh="详细说明",
            icon_path=self.icon_path,
            screenshot_paths=tuple(self.screenshot_paths),
            keywords_zh="工具,效率",
            app_id_override="",
        )
        releases = {
            ("demo-app", "stable"): ReleaseRecord(
                row_id=2,
                app_key="demo-app",
                release_key="stable",
                release_name="稳定版",
                region="1",
                note="",
            ),
            ("demo-app", "beta"): ReleaseRecord(
                row_id=3,
                app_key="demo-app",
                release_key="beta",
                release_name="测试版",
                region="1",
                note="",
            ),
        }
        packages = {
            ("demo-app", "stable"): (
                PackageRecord(
                    row_id=20,
                    app_key="demo-app",
                    release_key="stable",
                    package_key="pkg-stable",
                    package_family="demo-app",
                    package_format="deb",
                    file_path=self.deb_paths[0],
                    declared_arch="amd64",
                    pkg_channel="stable",
                    note="",
                ),
            ),
            ("demo-app", "beta"): (
                PackageRecord(
                    row_id=21,
                    app_key="demo-app",
                    release_key="beta",
                    package_key="pkg-beta",
                    package_family="demo-app",
                    package_format="deb",
                    file_path=self.deb_paths[1],
                    declared_arch="amd64",
                    pkg_channel="beta",
                    note="",
                ),
            ),
        }
        targets = {
            ("demo-app", "stable", "pkg-stable"): (
                TargetRecord(
                    row_id=30,
                    app_key="demo-app",
                    release_key="stable",
                    package_key="pkg-stable",
                    sup_sys_code="Deepin_23",
                    baseline_id="",
                    unsupport_baseline_ids=(),
                    target_note="",
                ),
            ),
            ("demo-app", "beta", "pkg-beta"): (
                TargetRecord(
                    row_id=31,
                    app_key="demo-app",
                    release_key="beta",
                    package_key="pkg-beta",
                    sup_sys_code="Deepin_23",
                    baseline_id="",
                    unsupport_baseline_ids=(),
                    target_note="",
                ),
            ),
        }
        self.manifest = LoadedManifest(
            workbook_path=self.root / "manifest.xlsx",
            apps={app.app_key: app},
            releases=releases,
            packages=packages,
            targets=targets,
        )

    def test_resolve_target_app_id_prefers_override_without_listing_apps(self) -> None:
        client = Mock()
        app = AppRecord(
            app_key="labelnova",
            app_name_zh="LabelNova",
            pkg_name="labelnova",
            category_id=1,
            website="https://example.com/labelnova",
            short_desc_zh="标签打印工具",
            full_desc_zh="用于设计并打印标签的示例应用。",
            icon_path=self.icon_path,
            screenshot_paths=tuple(self.screenshot_paths),
            app_id_override="1096227",
        )

        resolved = upload_batch._resolve_target_app_id(client, app, {})

        self.assertEqual(resolved, "1096227")
        client.find_apps_by_pkg_name.assert_not_called()

    def _build_grouped_manifest(self) -> LoadedManifest:
        release = ReleaseRecord(
            row_id=2,
            app_key="demo-app",
            release_key="stable",
            release_name="稳定版",
            region="1",
            note="",
        )
        package_one = PackageRecord(
            row_id=20,
            app_key="demo-app",
            release_key="stable",
            package_key="pkg-stable-a",
            package_family="demo-app",
            package_format="deb",
            file_path=self.deb_paths[0],
            declared_arch="amd64",
            pkg_channel="stable",
            note="",
        )
        package_two = PackageRecord(
            row_id=21,
            app_key="demo-app",
            release_key="stable",
            package_key="pkg-stable-b",
            package_family="demo-app",
            package_format="deb",
            file_path=self.deb_paths[1],
            declared_arch="amd64",
            pkg_channel="stable",
            note="",
        )
        target_one = TargetRecord(
            row_id=30,
            app_key="demo-app",
            release_key="stable",
            package_key="pkg-stable-a",
            sup_sys_code="Deepin_23",
            baseline_id="",
            unsupport_baseline_ids=(),
            target_note="",
        )
        target_two = TargetRecord(
            row_id=31,
            app_key="demo-app",
            release_key="stable",
            package_key="pkg-stable-b",
            sup_sys_code="Deepin_23",
            baseline_id="",
            unsupport_baseline_ids=(),
            target_note="",
        )
        return LoadedManifest(
            workbook_path=self.manifest.workbook_path,
            apps=self.manifest.apps,
            releases={("demo-app", "stable"): release},
            packages={("demo-app", "stable"): (package_one, package_two)},
            targets={
                ("demo-app", "stable", "pkg-stable-a"): (target_one,),
                ("demo-app", "stable", "pkg-stable-b"): (target_two,),
            },
        )

    def _build_empty_group_manifest(self) -> LoadedManifest:
        return LoadedManifest(
            workbook_path=self.manifest.workbook_path,
            apps=self.manifest.apps,
            releases={("demo-app", "stable"): self.manifest.releases[("demo-app", "stable")]},
            packages={},
            targets={},
        )

    def _build_collision_manifest(self) -> LoadedManifest:
        release_with_package = ReleaseRecord(
            row_id=20,
            app_key="demo-app",
            release_key="package-release",
            release_name="包行",
            region="1",
            note="",
        )
        empty_release = ReleaseRecord(
            row_id=20,
            app_key="demo-app",
            release_key="empty-release",
            release_name="空行",
            region="1",
            note="",
        )
        package_row = PackageRecord(
            row_id=20,
            app_key="demo-app",
            release_key="package-release",
            package_key="pkg-package-row",
            package_family="demo-app",
            package_format="deb",
            file_path=self.deb_paths[0],
            declared_arch="amd64",
            pkg_channel="stable",
            note="",
        )
        target_row = TargetRecord(
            row_id=30,
            app_key="demo-app",
            release_key="package-release",
            package_key="pkg-package-row",
            sup_sys_code="Deepin_23",
            baseline_id="",
            unsupport_baseline_ids=(),
            target_note="",
        )
        return LoadedManifest(
            workbook_path=self.manifest.workbook_path,
            apps=self.manifest.apps,
            releases={
                ("demo-app", "package-release"): release_with_package,
                ("demo-app", "empty-release"): empty_release,
            },
            packages={
                ("demo-app", "package-release"): (package_row,),
            },
            targets={
                ("demo-app", "package-release", "pkg-package-row"): (target_row,),
            },
        )

    def _build_row_collision_manifest(self) -> LoadedManifest:
        release_release_row = ReleaseRecord(
            row_id=20,
            app_key="demo-app",
            release_key="release-row",
            release_name="发布行",
            region="1",
            note="",
        )
        release_package_row = ReleaseRecord(
            row_id=30,
            app_key="demo-app",
            release_key="package-row",
            release_name="包行",
            region="1",
            note="",
        )
        package_release_row = PackageRecord(
            row_id=21,
            app_key="demo-app",
            release_key="release-row",
            package_key="pkg-release-row",
            package_family="demo-app",
            package_format="deb",
            file_path=self.deb_paths[0],
            declared_arch="amd64",
            pkg_channel="stable",
            note="",
        )
        package_package_row = PackageRecord(
            row_id=20,
            app_key="demo-app",
            release_key="package-row",
            package_key="pkg-package-row",
            package_family="demo-app",
            package_format="deb",
            file_path=self.deb_paths[1],
            declared_arch="amd64",
            pkg_channel="stable",
            note="",
        )
        target_release_row = TargetRecord(
            row_id=31,
            app_key="demo-app",
            release_key="release-row",
            package_key="pkg-release-row",
            sup_sys_code="Deepin_23",
            baseline_id="",
            unsupport_baseline_ids=(),
            target_note="",
        )
        target_package_row = TargetRecord(
            row_id=32,
            app_key="demo-app",
            release_key="package-row",
            package_key="pkg-package-row",
            sup_sys_code="Deepin_23",
            baseline_id="",
            unsupport_baseline_ids=(),
            target_note="",
        )
        return LoadedManifest(
            workbook_path=self.manifest.workbook_path,
            apps=self.manifest.apps,
            releases={
                ("demo-app", "release-row"): release_release_row,
                ("demo-app", "package-row"): release_package_row,
            },
            packages={
                ("demo-app", "release-row"): (package_release_row,),
                ("demo-app", "package-row"): (package_package_row,),
            },
            targets={
                ("demo-app", "release-row", "pkg-release-row"): (target_release_row,),
                ("demo-app", "package-row", "pkg-package-row"): (target_package_row,),
            },
        )

    def _build_grouped_validation_result(self, manifest: LoadedManifest) -> ValidatedRelease:
        release = manifest.releases[("demo-app", "stable")]
        app = manifest.apps["demo-app"]
        package_one, package_two = manifest.packages[("demo-app", "stable")]
        target_one = manifest.targets[("demo-app", "stable", "pkg-stable-a")][0]
        target_two = manifest.targets[("demo-app", "stable", "pkg-stable-b")][0]
        return ValidatedRelease(
            app=app,
            release=release,
            package_family="deb",
            packages=(
                ValidatedPackage(
                    package=package_one,
                    package_info=PackageInfo(
                        pkg_name="demo-app",
                        pkg_version="1.0.0",
                        pkg_arch="amd64",
                        pkg_size=package_one.file_path.stat().st_size,
                        sha256="sha-a",
                        file_path=package_one.file_path,
                        package_family="deb",
                        package_format="deb",
                    ),
                    targets=(target_one,),
                ),
                ValidatedPackage(
                    package=package_two,
                    package_info=PackageInfo(
                        pkg_name="demo-app",
                        pkg_version="2.0.0",
                        pkg_arch="amd64",
                        pkg_size=package_two.file_path.stat().st_size,
                        sha256="sha-b",
                        file_path=package_two.file_path,
                        package_family="deb",
                        package_format="deb",
                    ),
                    targets=(target_two,),
                ),
            ),
        )

    def _build_capability_cache(self) -> CapabilityCache:
        return CapabilityCache(
            generated_at="2026-04-22T00:00:00+08:00",
            deb_system_lines={
                "Deepin_23": SystemLine(code="Deepin_23", label="Deepin 23", family="deb"),
            },
            linglong_system_lines={},
            baseline_options={},
        )

    def _build_store_capability_cache(self) -> CapabilityCache:
        return CapabilityCache(
            generated_at="2026-04-22T00:00:00+08:00",
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

    def test_run_batch_continues_after_row_failure(self) -> None:
        client = FakeClient()
        client.matches_by_pkg_name["demo-app"] = [{"app_id": 42}]

        package_info_by_path = {
            self.deb_paths[0]: DebPackageInfo(
                pkg_name="wrong-name",
                pkg_version="1.0.0",
                pkg_arch="amd64",
                pkg_size=10,
                sha256="sha-a",
                deb_path=self.deb_paths[0],
            ),
            self.deb_paths[1]: DebPackageInfo(
                pkg_name="demo-app",
                pkg_version="2.0.0",
                pkg_arch="amd64",
                pkg_size=20,
                sha256="sha-b",
                deb_path=self.deb_paths[1],
            ),
        }

        def package_reader(deb_path: Path) -> DebPackageInfo:
            return package_info_by_path[Path(deb_path)]

        results = run_batch(
            manifest=self.manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            package_reader=package_reader,
            dry_run=False,
        )

        self.assertEqual(
            results,
            [
                RowResult(
                    row_id=2,
                    app_key="demo-app",
                    deb_path=self.deb_paths[0],
                    status="submit_failed",
                    message="package name mismatch: expected demo-app, got wrong-name",
                    pkg_name="wrong-name",
                    pkg_version="1.0.0",
                ),
                RowResult(
                    row_id=3,
                    app_key="demo-app",
                    deb_path=self.deb_paths[1],
                    status="submitted",
                    message="submitted",
                    app_id="42",
                    pkg_name="demo-app",
                    pkg_version="2.0.0",
                ),
            ],
        )
        self.assertEqual(client.login_calls, [("demo", "secret")])
        self.assertEqual(len(client.submit_calls), 1)
        self.assertNotIn("debug_row_id", client.submit_calls[0])
        self.assertEqual(len(client.upload_calls), 5)
        self.assertTrue((self.root / "report.json").exists())
        self.assertTrue((self.root / "report.xlsx").exists())
        report = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([item["status"] for item in report], ["submit_failed", "submitted"])

    def test_run_batch_stops_on_login_failure_with_auth_failed_results(self) -> None:
        client = FakeClient()
        client.login_error = AuthenticationError("bad credentials")

        results = run_batch(
            manifest=self.manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            dry_run=False,
        )

        self.assertEqual(
            results,
            [
                RowResult(
                    row_id=2,
                    app_key="demo-app",
                    deb_path=self.deb_paths[0],
                    status="auth_failed",
                    message="bad credentials",
                ),
                RowResult(
                    row_id=3,
                    app_key="demo-app",
                    deb_path=self.deb_paths[1],
                    status="auth_failed",
                    message="bad credentials",
                ),
            ],
        )
        self.assertEqual(client.login_calls, [("demo", "secret")])
        self.assertEqual(client.find_calls, [])
        self.assertEqual(client.upload_calls, [])
        self.assertEqual(client.submit_calls, [])

    def test_run_batch_auth_failure_does_not_strictly_resolve_multiple_targets(self) -> None:
        client = FakeClient()
        client.login_error = AuthenticationError("bad credentials")
        manifest = LoadedManifest(
            workbook_path=self.manifest.workbook_path,
            apps=self.manifest.apps,
            releases=self.manifest.releases,
            packages=self.manifest.packages,
            targets={
                ("demo-app", "stable", "pkg-stable"): (
                    self.manifest.targets[("demo-app", "stable", "pkg-stable")][0],
                    TargetRecord(
                        row_id=32,
                        app_key="demo-app",
                        release_key="stable",
                        package_key="pkg-stable",
                        sup_sys_code="Deepin_23",
                        baseline_id="",
                        unsupport_baseline_ids=(),
                        target_note="duplicate",
                    ),
                ),
                ("demo-app", "beta", "pkg-beta"): self.manifest.targets[("demo-app", "beta", "pkg-beta")],
            },
        )

        results = run_batch(
            manifest=manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            dry_run=False,
            row_filter={2},
        )

        self.assertEqual(
            results,
            [
                RowResult(
                    row_id=2,
                    app_key="demo-app",
                    deb_path=self.deb_paths[0],
                    status="auth_failed",
                    message="bad credentials",
                )
            ],
        )
        self.assertEqual(client.login_calls, [("demo", "secret")])
        self.assertEqual(client.find_calls, [])
        self.assertEqual(client.upload_calls, [])
        self.assertEqual(client.submit_calls, [])

    def test_run_batch_includes_exception_type_for_non_auth_login_failure(self) -> None:
        client = FakeClient()
        client.login_error = RuntimeError("browser launch failed")

        results = run_batch(
            manifest=self.manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            dry_run=False,
        )

        self.assertEqual(
            results,
            [
                RowResult(
                    row_id=2,
                    app_key="demo-app",
                    deb_path=self.deb_paths[0],
                    status="auth_failed",
                    message="RuntimeError: browser launch failed",
                ),
                RowResult(
                    row_id=3,
                    app_key="demo-app",
                    deb_path=self.deb_paths[1],
                    status="auth_failed",
                    message="RuntimeError: browser launch failed",
                ),
            ],
        )

    def test_run_batch_rejects_public_submit_without_dict_response(self) -> None:
        class BrokenSubmitClient(FakeClient):
            def submit_payload(self, payload: dict):
                self.submit_calls.append(payload)
                return None

        client = BrokenSubmitClient()
        client.matches_by_pkg_name["demo-app"] = [{"app_id": 42}]

        def package_reader(deb_path: Path) -> DebPackageInfo:
            return DebPackageInfo(
                pkg_name="demo-app",
                pkg_version="2.0.0",
                pkg_arch="amd64",
                pkg_size=deb_path.stat().st_size,
                sha256="sha-b",
                deb_path=deb_path,
            )

        results = run_batch(
            manifest=self.manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            package_reader=package_reader,
            dry_run=False,
            row_filter={3},
        )

        self.assertEqual(
            results,
            [
                RowResult(
                    row_id=3,
                    app_key="demo-app",
                    deb_path=self.deb_paths[1],
                    status="submit_failed",
                    message="submit_app returned unexpected payload: None",
                    pkg_name="demo-app",
                    pkg_version="2.0.0",
                )
            ],
        )

    def test_run_batch_dry_run_parses_package_and_reports_row_failure_without_login(self) -> None:
        client = FakeClient()

        def package_reader(_deb_path: Path) -> DebPackageInfo:
            raise RuntimeError("bad deb")

        results = run_batch(
            manifest=self.manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            package_reader=package_reader,
            dry_run=True,
            row_filter={3},
        )

        self.assertEqual(
            results,
            [
                RowResult(
                    row_id=3,
                    app_key="demo-app",
                    deb_path=self.deb_paths[1],
                    status="submit_failed",
                    message="bad deb",
                )
            ],
        )
        self.assertEqual(client.login_calls, [])
        self.assertEqual(client.find_calls, [])
        self.assertEqual(client.upload_calls, [])
        self.assertEqual(client.submit_calls, [])

    def test_run_batch_invalid_region_fails_before_upload(self) -> None:
        client = FakeClient()
        invalid_release = ReleaseRecord(
            row_id=4,
            app_key="demo-app",
            release_key="stable",
            release_name="稳定版",
            region="cn",
            note="",
        )
        manifest = LoadedManifest(
            workbook_path=self.manifest.workbook_path,
            apps=self.manifest.apps,
            releases={("demo-app", "stable"): invalid_release},
            packages=self.manifest.packages,
            targets=self.manifest.targets,
        )

        def package_reader(deb_path: Path) -> DebPackageInfo:
            return DebPackageInfo(
                pkg_name="demo-app",
                pkg_version="1.0.0",
                pkg_arch="amd64",
                pkg_size=deb_path.stat().st_size,
                sha256="sha-a",
                deb_path=deb_path,
            )

        results = run_batch(
            manifest=manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            package_reader=package_reader,
            dry_run=False,
        )

        self.assertEqual(
            results,
            [
                RowResult(
                    row_id=4,
                    app_key="demo-app",
                    deb_path=self.deb_paths[0],
                    status="submit_failed",
                    message="invalid region value: cn",
                    pkg_name="demo-app",
                    pkg_version="1.0.0",
                )
            ],
        )
        self.assertEqual(client.login_calls, [("demo", "secret")])
        self.assertEqual(client.find_calls, ["demo-app"])
        self.assertEqual(client.upload_calls, [])
        self.assertEqual(client.submit_calls, [])

    def test_run_batch_reuses_created_app_id_for_later_release_rows(self) -> None:
        client = SequencedSubmitClient([{"datas": {"app_id": "new-app-1"}}, {}])

        def package_reader(deb_path: Path) -> DebPackageInfo:
            version = "1.0.0" if deb_path == self.deb_paths[0] else "2.0.0"
            return DebPackageInfo(
                pkg_name="demo-app",
                pkg_version=version,
                pkg_arch="amd64",
                pkg_size=deb_path.stat().st_size,
                sha256=f"sha-{version}",
                deb_path=deb_path,
            )

        results = run_batch(
            manifest=self.manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            package_reader=package_reader,
            dry_run=False,
        )

        self.assertEqual([result.status for result in results], ["submitted", "submitted"])
        self.assertEqual(client.find_calls, ["demo-app"])
        self.assertEqual(client.submit_calls[0].get("app_id"), None)
        self.assertEqual(client.submit_calls[1]["app_id"], "new-app-1")

    def test_run_batch_records_submitted_row_even_without_resolved_app_id(self) -> None:
        client = FakeClient()

        def package_reader(deb_path: Path) -> DebPackageInfo:
            return DebPackageInfo(
                pkg_name="demo-app",
                pkg_version="2.0.0",
                pkg_arch="amd64",
                pkg_size=deb_path.stat().st_size,
                sha256="sha-b",
                deb_path=deb_path,
            )

        results = run_batch(
            manifest=self.manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            package_reader=package_reader,
            dry_run=False,
            row_filter={3},
        )

        self.assertEqual(
            results,
            [
                RowResult(
                    row_id=3,
                    app_key="demo-app",
                    deb_path=self.deb_paths[1],
                    status="submitted",
                    message="submitted",
                    app_id="",
                    pkg_name="demo-app",
                    pkg_version="2.0.0",
                )
            ],
        )
        self.assertEqual(client.find_calls, ["demo-app", "demo-app"])
        self.assertEqual(len(client.submit_calls), 1)

    def test_run_batch_rejects_multiple_packages_for_one_release(self) -> None:
        client = FakeClient()
        manifest = LoadedManifest(
            workbook_path=self.manifest.workbook_path,
            apps=self.manifest.apps,
            releases=self.manifest.releases,
            packages={
                ("demo-app", "stable"): (
                    self.manifest.packages[("demo-app", "stable")][0],
                    PackageRecord(
                        row_id=22,
                        app_key="demo-app",
                        release_key="stable",
                        package_key="pkg-alt",
                        package_family="demo-app",
                        package_format="deb",
                        file_path=self.deb_paths[1],
                        declared_arch="amd64",
                        pkg_channel="stable",
                        note="",
                    ),
                ),
                ("demo-app", "beta"): self.manifest.packages[("demo-app", "beta")],
            },
            targets=self.manifest.targets,
        )

        def package_reader(_deb_path: Path) -> DebPackageInfo:
            raise AssertionError("package_reader should not be called")

        results = run_batch(
            manifest=manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            package_reader=package_reader,
            dry_run=False,
            row_filter={2},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "submit_failed")
        self.assertIn("multiple packages", results[0].message)
        self.assertEqual(client.upload_calls, [])
        self.assertEqual(client.submit_calls, [])

    def test_run_batch_rejects_multiple_targets_for_one_release(self) -> None:
        client = FakeClient()
        manifest = LoadedManifest(
            workbook_path=self.manifest.workbook_path,
            apps=self.manifest.apps,
            releases=self.manifest.releases,
            packages=self.manifest.packages,
            targets={
                ("demo-app", "stable", "pkg-stable"): (
                    self.manifest.targets[("demo-app", "stable", "pkg-stable")][0],
                    TargetRecord(
                        row_id=32,
                        app_key="demo-app",
                        release_key="stable",
                        package_key="pkg-stable",
                        sup_sys_code="Deepin_23",
                        baseline_id="",
                        unsupport_baseline_ids=(),
                        target_note="duplicate",
                    ),
                ),
                ("demo-app", "beta", "pkg-beta"): self.manifest.targets[("demo-app", "beta", "pkg-beta")],
            },
        )

        def package_reader(_deb_path: Path) -> DebPackageInfo:
            raise AssertionError("package_reader should not be called")

        results = run_batch(
            manifest=manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            package_reader=package_reader,
            dry_run=False,
            row_filter={2},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "submit_failed")
        self.assertIn("multiple targets", results[0].message)
        self.assertEqual(client.upload_calls, [])
        self.assertEqual(client.submit_calls, [])

    def test_run_batch_collects_missing_app_key_as_submit_failed(self) -> None:
        client = FakeClient()
        client.matches_by_pkg_name["demo-app"] = [{"app_id": 42}]
        missing_release = ReleaseRecord(
            row_id=4,
            app_key="missing-app",
            release_key="stable",
            release_name="缺失应用",
            region="1",
            note="",
        )
        missing_deb_path = self.root / "missing.deb"
        missing_deb_path.write_bytes(b"missing")
        missing_package = PackageRecord(
            row_id=40,
            app_key="missing-app",
            release_key="stable",
            package_key="pkg-missing",
            package_family="missing-app",
            package_format="deb",
            file_path=missing_deb_path,
            declared_arch="amd64",
            pkg_channel="stable",
            note="",
        )
        missing_target = TargetRecord(
            row_id=41,
            app_key="missing-app",
            release_key="stable",
            package_key="pkg-missing",
            sup_sys_code="Deepin_23",
            baseline_id="",
            unsupport_baseline_ids=(),
            target_note="",
        )
        manifest = LoadedManifest(
            workbook_path=self.manifest.workbook_path,
            apps=self.manifest.apps,
            releases={("missing-app", "stable"): missing_release, ("demo-app", "beta"): self.manifest.releases[("demo-app", "beta")]},
            packages={("missing-app", "stable"): (missing_package,), ("demo-app", "beta"): self.manifest.packages[("demo-app", "beta")]},
            targets={("missing-app", "stable", "pkg-missing"): (missing_target,), ("demo-app", "beta", "pkg-beta"): self.manifest.targets[("demo-app", "beta", "pkg-beta")]},
        )

        def package_reader(deb_path: Path) -> DebPackageInfo:
            return DebPackageInfo(
                pkg_name="demo-app",
                pkg_version="2.0.0",
                pkg_arch="amd64",
                pkg_size=deb_path.stat().st_size,
                sha256="sha-b",
                deb_path=deb_path,
            )

        results = run_batch(
            manifest=manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            package_reader=package_reader,
            dry_run=False,
        )

        self.assertEqual(
            results,
            [
                RowResult(
                    row_id=4,
                    app_key="missing-app",
                    deb_path=missing_deb_path,
                    status="submit_failed",
                    message="unknown app_key: missing-app",
                    pkg_name="demo-app",
                    pkg_version="2.0.0",
                ),
                RowResult(
                    row_id=3,
                    app_key="demo-app",
                    deb_path=self.deb_paths[1],
                    status="submitted",
                    message="submitted",
                    app_id="42",
                    pkg_name="demo-app",
                    pkg_version="2.0.0",
                ),
            ],
        )
        self.assertTrue((self.root / "report.json").exists())
        self.assertTrue((self.root / "report.xlsx").exists())
        report = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([item["status"] for item in report], ["submit_failed", "submitted"])
        self.assertEqual(len(client.submit_calls), 1)
        self.assertNotIn("debug_row_id", client.submit_calls[0])

    def test_run_batch_submits_without_public_submit_method_using_session_post(self) -> None:
        session = _FakeSession([_FakeResponse(json_data={"status": 200, "datas": {"app_id": "server-app-id"}})])
        client = CompatibilityClient(session=session)
        client.matches_by_pkg_name["demo-app"] = [{"app_id": 42}]

        def package_reader(deb_path: Path) -> DebPackageInfo:
            return DebPackageInfo(
                pkg_name="demo-app",
                pkg_version="2.0.0",
                pkg_arch="amd64",
                pkg_size=deb_path.stat().st_size,
                sha256="sha-b",
                deb_path=deb_path,
            )

        results = run_batch(
            manifest=self.manifest,
            client=client,
            username="demo",
            password="secret",
            output_dir=self.root,
            package_reader=package_reader,
            dry_run=False,
            row_filter={3},
        )

        self.assertEqual(
            results,
            [
                RowResult(
                    row_id=3,
                    app_key="demo-app",
                    deb_path=self.deb_paths[1],
                    status="submitted",
                    message="submitted",
                    app_id="server-app-id",
                    pkg_name="demo-app",
                    pkg_version="2.0.0",
                )
            ],
        )
        self.assertEqual(client.login_calls, [("demo", "secret")])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][0], "POST")
        self.assertIn("/store-dev-app/app", session.calls[0][1])
        self.assertNotIn("debug_row_id", session.calls[0][2]["json"])

    def test_main_sync_capabilities_subcommand_calls_sync_helper(self) -> None:
        client = FakeClient()
        cache_dir = self.root / "capabilities-cache"
        with (
            patch("appstore.upload_batch.AppStoreClient", return_value=client),
            patch("appstore.upload_batch.sync_capabilities_to_cache", return_value=cache_dir / "latest.json") as sync_mock,
        ):
            exit_code = main(
                [
                    "sync-capabilities",
                    "--cache-dir",
                    str(cache_dir),
                    "--username",
                    "demo",
                    "--password",
                    "secret",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(client.login_calls, [("demo", "secret")])
        sync_mock.assert_called_once_with(client, cache_dir)

    def test_main_validate_subcommand_writes_grouped_validation_results(self) -> None:
        manifest = self._build_grouped_manifest()
        validated_release = self._build_grouped_validation_result(manifest)
        output_dir = self.root / "validate-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()

        with (
            patch("appstore.upload_batch.load_manifest", return_value=manifest),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_capability_cache()),
            patch("appstore.upload_batch.inspect_package", side_effect=[pkg.package_info for pkg in validated_release.packages]),
            patch("appstore.upload_batch.validate_release_group", return_value=validated_release) as validate_mock,
        ):
            exit_code = main(
                [
                    "validate",
                    str(manifest.workbook_path),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--rows",
                    "20",
                ]
            )

        self.assertEqual(exit_code, 0)
        validate_mock.assert_called_once()
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([row["status"] for row in report], ["validated", "validated"])
        self.assertEqual([row["row_id"] for row in report], [20, 21])
        self.assertEqual([row["selector"] for row in report], ["20", "21"])

    def test_main_upload_subcommand_uses_grouped_release_submission_path(self) -> None:
        manifest = self._build_grouped_manifest()
        validated_release = self._build_grouped_validation_result(manifest)
        output_dir = self.root / "upload-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()
        client = FakeClient()
        client.matches_by_pkg_name["demo-app"] = [{"app_id": "demo-app-id"}]

        with (
            patch("appstore.upload_batch.AppStoreClient", return_value=client),
            patch("appstore.upload_batch.load_manifest", return_value=manifest),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_capability_cache()),
            patch("appstore.upload_batch.inspect_package", side_effect=[pkg.package_info for pkg in validated_release.packages]),
            patch("appstore.upload_batch.validate_release_group", return_value=validated_release),
            patch("appstore.upload_batch.submit_grouped_release", return_value={"datas": {"app_id": "server-app-id"}}) as submit_mock,
        ):
            exit_code = main(
                [
                    "upload",
                    str(manifest.workbook_path),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--rows",
                    "20",
                    "--username",
                    "demo",
                    "--password",
                    "secret",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(client.login_calls, [("demo", "secret")])
        submit_mock.assert_called_once()
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([row["status"] for row in report], ["submitted", "submitted"])
        self.assertEqual([row["app_id"] for row in report], ["server-app-id", "server-app-id"])
        self.assertEqual([row["selector"] for row in report], ["20", "21"])

    def test_main_upload_subcommand_reuses_existing_app_detail_for_updates(self) -> None:
        manifest = self._build_grouped_manifest()
        release = ReleaseRecord(
            row_id=2,
            app_key="demo-app",
            release_key="stable",
            release_name="稳定版",
            region="1",
            note="只改更新内容",
        )
        manifest = LoadedManifest(
            workbook_path=manifest.workbook_path,
            apps=manifest.apps,
            releases={("demo-app", "stable"): release},
            packages=manifest.packages,
            targets=manifest.targets,
        )
        validated_release = self._build_grouped_validation_result(manifest)
        output_dir = self.root / "upload-update-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()
        client = FakeClient()
        client.matches_by_pkg_name["demo-app"] = [{"app_id": "demo-app-id", "id": "detail-id"}]
        client.detail_by_id["detail-id"] = {
            "app_basic_info": {
                "category_id": 9,
                "website": "https://existing.example/demo-app",
                "region": "1",
                "default_lan": "zh_CN",
                "pkg_mode": 0,
                "inAppPayment": 0,
            },
            "app_lan_infos": [
                {
                    "lan": "zh_CN",
                    "name": "旧应用",
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
                    "pkg_name": "demo-app",
                    "pkg_version": "0.9.0",
                    "pkg_arch": "4",
                    "pkgArch": "X86",
                    "pkgType": 11,
                    "pkg_mode": 0,
                    "pkg_size": 10,
                    "sha256": "old-hash",
                    "file_save_key": "existing-pkg",
                    "progressPercent": 101,
                    "supSys": "11",
                    "supBlineVer": "",
                    "unsupportBlineVers": "",
                    "systemStr": "社区版V23",
                }
            ],
        }

        with (
            patch("appstore.upload_batch.AppStoreClient", return_value=client),
            patch("appstore.upload_batch.load_manifest", return_value=manifest),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_capability_cache()),
            patch("appstore.upload_batch.inspect_package", side_effect=[pkg.package_info for pkg in validated_release.packages]),
            patch("appstore.upload_batch.validate_release_group", return_value=validated_release),
            patch("appstore.upload_batch.submit_grouped_release", return_value={"datas": {"app_id": "demo-app-id"}}) as submit_mock,
        ):
            exit_code = main(
                [
                    "upload",
                    str(manifest.workbook_path),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--rows",
                    "20",
                    "--username",
                    "demo",
                    "--password",
                    "secret",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(client.login_calls, [("demo", "secret")])
        self.assertEqual([call[2] for call in client.upload_calls], ["temppkg", "temppkg"])
        submit_mock.assert_called_once()
        self.assertIsNone(submit_mock.call_args.kwargs["app_uploads"])
        self.assertEqual(submit_mock.call_args.kwargs["existing_app_detail"]["app_lan_infos"][0]["icon_save_key"], "existing-icon")
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([row["status"] for row in report], ["submitted", "submitted"])

    def test_main_upload_subcommand_uses_browser_runner_when_mode_is_browser(self) -> None:
        manifest = self._build_grouped_manifest()
        validated_release = self._build_grouped_validation_result(manifest)
        output_dir = self.root / "upload-browser-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()
        client = FakeClient()
        client.matches_by_pkg_name["demo-app"] = [{"app_id": "demo-app-id", "id": "detail-id"}]
        browser_runner = FakeBrowserRunner()

        with (
            patch("appstore.upload_batch.AppStoreClient", return_value=client),
            patch("appstore.upload_batch.BrowserSubmissionRunner", return_value=browser_runner),
            patch("appstore.upload_batch.load_manifest", return_value=manifest),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_capability_cache()),
            patch("appstore.upload_batch.inspect_package", side_effect=[pkg.package_info for pkg in validated_release.packages]),
            patch("appstore.upload_batch.validate_release_group", return_value=validated_release),
        ):
            exit_code = main(
                [
                    "upload",
                    str(manifest.workbook_path),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--rows",
                    "20",
                    "--mode",
                    "browser",
                    "--username",
                    "demo",
                    "--password",
                    "secret",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(client.login_calls, [("demo", "secret")])
        self.assertEqual(len(browser_runner.calls), 1)
        self.assertEqual(browser_runner.calls[0]["release"].release_key, "stable")
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([row["status"] for row in report], ["submitted", "submitted"])
        self.assertEqual([row["app_id"] for row in report], ["browser-app-id", "browser-app-id"])

    def test_main_upload_packages_subcommand_updates_existing_app_without_workbook(self) -> None:
        output_dir = self.root / "upload-packages-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()
        client = FakeClient()
        client.matches_by_pkg_name["labelnova"] = [{"app_id": "1096227", "id": "detail-id"}]
        client.detail_by_id["detail-id"] = {
            "app_basic_info": {
                "app_name": "LabelNova",
                "category_id": 1,
                "website": "https://mm.md/p/",
                "region": "1",
                "default_lan": "zh_CN",
                "pkg_mode": 0,
                "inAppPayment": 0,
            },
            "app_lan_infos": [
                {
                    "lan": "zh_CN",
                    "name": "LabelNova",
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
                    "pkg_name": "labelnova",
                    "pkg_version": "1.0.3-1",
                    "pkg_arch": "4",
                    "pkgArch": "X86",
                    "pkgType": 11,
                    "pkg_mode": 0,
                    "pkg_size": 123,
                    "sha256": "old-sha",
                    "file_save_key": "old-x86",
                    "progressPercent": 101,
                    "supSys": "11",
                    "supBlineVer": "",
                    "unsupportBlineVers": "",
                    "systemStr": "社区版V23",
                }
            ],
        }

        with (
            patch("appstore.upload_batch.AppStoreClient", return_value=client),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_store_capability_cache()),
            patch(
                "appstore.upload_batch.read_package_info",
                side_effect=[
                    DebPackageInfo(
                        pkg_name="labelnova",
                        pkg_version="1.0.5-1",
                        pkg_arch="amd64",
                        pkg_size=111,
                        sha256="sha-amd64",
                        deb_path=self.deb_paths[0],
                    ),
                    DebPackageInfo(
                        pkg_name="labelnova",
                        pkg_version="1.0.5-1",
                        pkg_arch="arm64",
                        pkg_size=222,
                        sha256="sha-arm64",
                        deb_path=self.deb_paths[1],
                    ),
                ],
            ),
            patch("appstore.upload_batch.submit_grouped_release", return_value={"datas": {"app_id": "1096227"}}) as submit_mock,
        ):
            exit_code = main(
                [
                    "upload-packages",
                    str(self.deb_paths[0]),
                    str(self.deb_paths[1]),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--username",
                    "demo",
                    "--password",
                    "secret",
                    "--note",
                    "自动更新到 1.0.5-1",
                    "--mode",
                    "api",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(client.login_calls, [("demo", "secret")])
        self.assertEqual(client.find_calls, ["labelnova"])
        submit_mock.assert_called_once()
        validated_release = submit_mock.call_args.kwargs["validated_release"]
        self.assertEqual(validated_release.app.pkg_name, "labelnova")
        self.assertEqual(validated_release.release.note, "自动更新到 1.0.5-1")
        self.assertEqual(submit_mock.call_args.kwargs["app_uploads"], None)
        self.assertEqual(submit_mock.call_args.kwargs["existing_app_detail"]["app_lan_infos"][0]["name"], "LabelNova")
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([row["status"] for row in report], ["submitted", "submitted"])
        self.assertEqual([row["app_id"] for row in report], ["1096227", "1096227"])

    def test_main_upload_packages_subcommand_rejects_mixed_package_names(self) -> None:
        output_dir = self.root / "upload-packages-invalid-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()

        with (
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_store_capability_cache()),
            patch(
                "appstore.upload_batch.read_package_info",
                side_effect=[
                    DebPackageInfo(
                        pkg_name="first-app",
                        pkg_version="1.0.0",
                        pkg_arch="amd64",
                        pkg_size=111,
                        sha256="sha-first",
                        deb_path=self.deb_paths[0],
                    ),
                    DebPackageInfo(
                        pkg_name="second-app",
                        pkg_version="1.0.0",
                        pkg_arch="arm64",
                        pkg_size=222,
                        sha256="sha-second",
                        deb_path=self.deb_paths[1],
                    ),
                ],
            ),
        ):
            exit_code = main(
                [
                    "upload-packages",
                    str(self.deb_paths[0]),
                    str(self.deb_paths[1]),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--username",
                    "demo",
                    "--password",
                    "secret",
                ]
            )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([row["status"] for row in report], ["submit_failed", "submit_failed"])
        self.assertIn("package name mismatch between files", report[0]["message"])

    def test_main_validate_subcommand_reports_failure_for_empty_package_group(self) -> None:
        manifest = self._build_empty_group_manifest()
        output_dir = self.root / "empty-validate-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()

        with (
            patch("appstore.upload_batch.load_manifest", return_value=manifest),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_capability_cache()),
        ):
            exit_code = main(
                [
                    "validate",
                    str(manifest.workbook_path),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                ]
            )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["status"], "validate_failed")
        self.assertEqual(report[0]["row_id"], 2)
        self.assertEqual(report[0]["deb_path"], str(manifest.workbook_path))
        self.assertEqual(report[0]["selector"], "r:2")

    def test_main_upload_subcommand_reports_failure_for_empty_package_group(self) -> None:
        manifest = self._build_empty_group_manifest()
        output_dir = self.root / "empty-upload-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()

        with (
            patch("appstore.upload_batch.load_manifest", return_value=manifest),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_capability_cache()),
        ):
            exit_code = main(
                [
                    "upload",
                    str(manifest.workbook_path),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["status"], "submit_failed")
        self.assertEqual(report[0]["row_id"], 2)
        self.assertEqual(report[0]["deb_path"], str(manifest.workbook_path))
        self.assertEqual(report[0]["selector"], "r:2")

    def test_main_validate_subcommand_uses_package_selector_not_release_collision(self) -> None:
        manifest = self._build_collision_manifest()
        output_dir = self.root / "collision-select-validate-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()

        with (
            patch("appstore.upload_batch.load_manifest", return_value=manifest),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_capability_cache()),
        ):
            exit_code = main(
                [
                    "validate",
                    str(manifest.workbook_path),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--rows",
                    "20",
                ]
            )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["row_id"], 20)
        self.assertEqual(report[0]["selector"], "20")
        self.assertEqual(report[0]["deb_path"], str(self.deb_paths[0]))

    def test_main_validate_subcommand_uses_release_selector_for_empty_group(self) -> None:
        manifest = self._build_collision_manifest()
        output_dir = self.root / "collision-empty-validate-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()

        with (
            patch("appstore.upload_batch.load_manifest", return_value=manifest),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_capability_cache()),
        ):
            exit_code = main(
                [
                    "validate",
                    str(manifest.workbook_path),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--rows",
                    "r:20",
                ]
            )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["row_id"], 20)
        self.assertEqual(report[0]["selector"], "r:20")
        self.assertEqual(report[0]["status"], "validate_failed")
        self.assertEqual(report[0]["deb_path"], str(manifest.workbook_path))

    def test_main_upload_subcommand_uses_package_selector_not_release_collision(self) -> None:
        manifest = self._build_collision_manifest()
        output_dir = self.root / "collision-select-upload-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()
        client = FakeClient()
        client.matches_by_pkg_name["demo-app"] = [{"app_id": "demo-app-id"}]

        with (
            patch("appstore.upload_batch.AppStoreClient", return_value=client),
            patch("appstore.upload_batch.load_manifest", return_value=manifest),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_capability_cache()),
        ):
            exit_code = main(
                [
                    "upload",
                    str(manifest.workbook_path),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--username",
                    "demo",
                    "--password",
                    "secret",
                    "--rows",
                    "20",
                ]
            )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["row_id"], 20)
        self.assertEqual(report[0]["selector"], "20")
        self.assertEqual(report[0]["deb_path"], str(self.deb_paths[0]))

    def test_main_validate_subcommand_prefers_package_row_ids_over_release_row_ids(self) -> None:
        manifest = self._build_row_collision_manifest()
        output_dir = self.root / "collision-validate-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()

        with (
            patch("appstore.upload_batch.load_manifest", return_value=manifest),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_capability_cache()),
        ):
            exit_code = main(
                [
                    "validate",
                    str(manifest.workbook_path),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--rows",
                    "20",
                ]
            )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([row["row_id"] for row in report], [20])
        self.assertEqual([row["deb_path"] for row in report], [str(self.deb_paths[1])])

    def test_main_upload_subcommand_prefers_package_row_ids_over_release_row_ids(self) -> None:
        manifest = self._build_row_collision_manifest()
        output_dir = self.root / "collision-upload-output"
        cache_dir = self.root / "capabilities-cache"
        cache_dir.mkdir()
        client = FakeClient()
        client.matches_by_pkg_name["demo-app"] = [{"app_id": "demo-app-id"}]

        with (
            patch("appstore.upload_batch.AppStoreClient", return_value=client),
            patch("appstore.upload_batch.load_manifest", return_value=manifest),
            patch("appstore.upload_batch.load_capability_cache", return_value=self._build_capability_cache()),
        ):
            exit_code = main(
                [
                    "upload",
                    str(manifest.workbook_path),
                    "--output-dir",
                    str(output_dir),
                    "--capabilities-cache",
                    str(cache_dir),
                    "--username",
                    "demo",
                    "--password",
                    "secret",
                    "--rows",
                    "20",
                ]
            )

        self.assertEqual(exit_code, 0)
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([row["row_id"] for row in report], [20])
        self.assertEqual([row["deb_path"] for row in report], [str(self.deb_paths[1])])

    def test_main_writes_report_for_malformed_workbook(self) -> None:
        workbook_path = self.root / "broken.xlsx"
        workbook_path.write_text("not an xlsx", encoding="utf-8")
        output_dir = self.root / "report-output"

        with patch("appstore.upload_batch._timestamp_label", return_value="20260422-000000"):
            exit_code = main(["validate", str(workbook_path), "--output-dir", str(output_dir)])

        self.assertEqual(exit_code, 1)
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["status"], "workbook_failed")
        self.assertIn("broken.xlsx", report[0]["deb_path"])


if __name__ == "__main__":
    unittest.main()
