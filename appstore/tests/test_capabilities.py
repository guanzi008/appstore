import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from appstore.capabilities import (
    load_capability_cache,
    normalize_baseline_options,
    normalize_deb_system_lines,
    normalize_linglong_system_lines,
    sync_capabilities_to_cache,
    write_capability_cache,
)
from appstore.models import BaselineOption, CapabilityCache, SystemLine


class CapabilityCacheTests(unittest.TestCase):
    def test_write_and_load_capability_cache_round_trip(self) -> None:
        cache = CapabilityCache(
            generated_at="2026-04-22T12:00:00+08:00",
            deb_system_lines={"11": SystemLine(code="11", label="communityV23", family="deb")},
            linglong_system_lines={"21": SystemLine(code="21", label="communityV25", family="linglong")},
            baseline_options={
                "deb:11": (BaselineOption(system_line_code="11", baseline_id="2300", minor_version="23.0.0"),),
                "linglong:21": (BaselineOption(system_line_code="21", baseline_id="2500", minor_version="25.0.0"),),
            },
        )

        with TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            latest_path = write_capability_cache(cache_dir, cache)
            loaded = load_capability_cache(latest_path)
            self.assertTrue(latest_path.exists())
            self.assertEqual(loaded, cache)

    def test_normalize_linglong_system_lines_uses_raw_store_codes(self) -> None:
        payload = {
            "datas": [
                {"dictLabel": "communityV23", "dictValue": "11"},
                {"dictLabel": "professionalV25", "dictValue": "22"},
            ],
            "status": 200,
        }

        cache = normalize_linglong_system_lines(payload)

        self.assertEqual(sorted(cache.keys()), ["11", "22"])
        self.assertEqual(cache["11"].family, "linglong")
        self.assertEqual(cache["11"].label, "communityV23")

    def test_normalize_deb_system_lines_uses_store_ids(self) -> None:
        adapt_info = {
            "datas": {
                "systemPlatformList": [
                    {"code": "11", "name": "communityV23"},
                    {"code": "21", "name": "communityV25"},
                ]
            }
        }

        cache = normalize_deb_system_lines(adapt_info)

        self.assertEqual(sorted(cache.keys()), ["11", "21"])
        self.assertEqual(cache["21"], SystemLine(code="21", label="communityV25", family="deb"))

    def test_normalize_baseline_options_groups_by_family_and_system_line(self) -> None:
        adapt_info = {
            "datas": {
                "shopVersionList": [
                    {"pkgInstallMode": 1, "system_platform": "11", "id": "2300", "minor_version": "23.0.0"},
                    {"pkgInstallMode": 2, "system_platform": "21", "id": "2500", "minor_version": "25.0.0"},
                ]
            }
        }

        cache = normalize_baseline_options(adapt_info)

        self.assertEqual(cache["deb:11"][0], BaselineOption(system_line_code="11", baseline_id="2300", minor_version="23.0.0"))
        self.assertEqual(
            cache["linglong:21"][0],
            BaselineOption(system_line_code="21", baseline_id="2500", minor_version="25.0.0"),
        )

    def test_sync_capabilities_to_cache_writes_latest_json(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def fetch_linglong_system_lines(self) -> list[dict]:
                self.calls.append("linglong")
                return [{"dictLabel": "communityV23", "dictValue": "11"}]

            def fetch_adapt_info(self) -> dict:
                self.calls.append("adapt")
                return {
                    "datas": {
                        "systemPlatformList": [{"code": "11", "name": "communityV23"}],
                        "shopVersionList": [
                            {"pkgInstallMode": 1, "system_platform": "11", "id": "2300", "minor_version": "23.0.0"}
                        ],
                    }
                }

        with TemporaryDirectory() as tmpdir:
            client = FakeClient()
            latest_path = sync_capabilities_to_cache(client, Path(tmpdir))
            self.assertEqual(client.calls, ["linglong", "adapt"])
            loaded = load_capability_cache(latest_path)

        self.assertEqual(loaded.deb_system_lines["11"].label, "communityV23")


if __name__ == "__main__":
    unittest.main()
