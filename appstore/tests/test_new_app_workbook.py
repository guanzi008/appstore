from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from appstore.capabilities import CapabilityCache, write_capability_cache
from appstore.models import BaselineOption, SystemLine
from appstore.new_app_workbook import prepare_new_app_workbook


class PrepareNewAppWorkbookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.example_packages = self.repo_root / "appstore" / "examples" / "packages"

    def _write_large_png(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 2048)

    def test_prepare_new_app_workbook_uses_real_packages_and_detected_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            amd64 = temp_root / "labelnova_1.0.4-1_amd64.deb"
            arm64 = temp_root / "labelnova_1.0.4-1_arm64.deb"
            shutil.copy2(self.example_packages / amd64.name, amd64)
            shutil.copy2(self.example_packages / arm64.name, arm64)

            self._write_large_png(temp_root / "assets" / "icon.png")
            self._write_large_png(temp_root / "screenshots" / "screenshot_1.png")
            self._write_large_png(temp_root / "screenshots" / "screenshot_2.png")
            self._write_large_png(temp_root / "screenshots" / "screenshot_3.png")

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
            cache_dir = temp_root / "cache"
            write_capability_cache(cache_dir, cache)

            output_path = temp_root / "labelnova-submission.xlsx"
            prepared = prepare_new_app_workbook(
                packages=[str(amd64), str(arm64)],
                output_path=output_path,
                capabilities_cache=cache_dir,
                app_name_zh="标签打印工具",
                short_desc_zh="真实包生成的新应用模板",
                full_desc_zh="这是根据真实 deb 包自动生成的 workbook，不包含示例包。",
                website="https://example.invalid/labelnova-real",
                keywords_zh="标签,打印",
                system_line_codes=["11"],
            )

            self.assertEqual(prepared.pkg_name, "labelnova")
            self.assertEqual(prepared.selected_system_line_codes, ("11",))
            self.assertEqual(prepared.missing_fields, ())
            self.assertEqual(prepared.placeholder_fields, ())
            self.assertTrue(prepared.ready_for_upload)

            workbook = load_workbook(output_path)
            self.assertEqual(workbook["apps"]["A2"].value, "labelnova")
            self.assertEqual(workbook["apps"]["B2"].value, "标签打印工具")
            self.assertEqual(workbook["apps"]["H2"].value, "assets/icon.png")
            self.assertEqual(workbook["apps"]["I2"].value, "screenshots/screenshot_1.png")
            self.assertEqual(workbook["apps"]["K2"].value, "screenshots/screenshot_3.png")
            self.assertEqual(workbook["packages"]["D2"].value, "labelnova-amd64")
            self.assertEqual(workbook["packages"]["E2"].value, amd64.name)
            self.assertEqual(workbook["packages"]["D3"].value, "labelnova-arm64")
            self.assertEqual(workbook["packages"]["E3"].value, arm64.name)
            self.assertEqual(workbook["packages"]["H2"].value, "Y")
            self.assertEqual(workbook["packages"]["I2"].value, "2300")
            self.assertEqual(workbook["system_templates"]["A2"].value, "sys__deb__11")
            self.assertEqual(workbook["system_templates"]["A3"].value, "sys__deb__21")
