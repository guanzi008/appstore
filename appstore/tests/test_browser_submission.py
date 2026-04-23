import unittest
from pathlib import Path

from appstore.browser_submission import build_release_browser_plan
from appstore.models import PackageRecord, ReleaseRecord, TargetRecord


class BrowserSubmissionPlanTests(unittest.TestCase):
    def test_multi_package_release_uses_staged_save_plan(self) -> None:
        release = ReleaseRecord(
            row_id=2,
            app_key="labelnova",
            release_key="stable",
            release_name="Stable",
            execution_mode="browser",
        )
        packages = (
            PackageRecord(3, "labelnova", "stable", "arm", "deb", "deb", Path("a.deb"), "arm64", "", ""),
            PackageRecord(4, "labelnova", "stable", "loong", "deb", "deb", Path("b.deb"), "loong64", "", ""),
        )
        targets = {
            "arm": (TargetRecord(5, "labelnova", "stable", "arm", "11", "2300", (), ""),),
            "loong": (TargetRecord(6, "labelnova", "stable", "loong", "11", "2300", (), ""),),
        }

        plan = build_release_browser_plan(release=release, packages=packages, targets_by_package=targets)

        self.assertEqual(plan.steps[0].action, "open_release")
        self.assertEqual([step.action for step in plan.steps].count("save_release"), 2)
        self.assertEqual([step.action for step in plan.steps].count("reopen_release"), 1)
        self.assertEqual(plan.steps[-1].action, "submit_release")
