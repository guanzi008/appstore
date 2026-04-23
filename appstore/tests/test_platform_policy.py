import unittest
from pathlib import Path

from appstore.models import PackageRecord, ReleaseRecord, TargetRecord
from appstore.platform_policy import decide_execution_mode, resolve_target_system_line


class PlatformPolicyTests(unittest.TestCase):
    def test_release_execution_mode_defaults_to_cli_mode(self) -> None:
        release = ReleaseRecord(
            row_id=2,
            app_key="labelnova",
            release_key="stable",
            release_name="Stable",
        )

        self.assertEqual(decide_execution_mode(release=release, cli_mode="browser"), "browser")

    def test_release_execution_mode_prefers_release_override(self) -> None:
        release = ReleaseRecord(
            row_id=2,
            app_key="labelnova",
            release_key="stable",
            release_name="Stable",
            execution_mode="api",
        )

        self.assertEqual(decide_execution_mode(release=release, cli_mode="browser"), "api")

    def test_loong64_target_is_promoted_to_v25(self) -> None:
        package = PackageRecord(
            row_id=3,
            app_key="labelnova",
            release_key="stable",
            package_key="loong",
            package_family="deb",
            package_format="deb",
            file_path=Path("labelnova_loong64.deb"),
            declared_arch="loong64",
        )
        target = TargetRecord(
            row_id=4,
            app_key="labelnova",
            release_key="stable",
            package_key="loong",
            sup_sys_code="11",
            baseline_id="2300",
        )

        resolved = resolve_target_system_line(package=package, target=target)

        self.assertEqual(resolved.sup_sys_code, "21")
        self.assertEqual(resolved.baseline_id, "2300")
