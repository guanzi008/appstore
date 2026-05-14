import asyncio
import os
import unittest
from unittest.mock import patch

from appstore.chromium_mirrors import (
    OFFICIAL_CHROMIUM_DOWNLOAD_HOST,
    configure_pyppeteer_download_environment,
    detect_chromium_download_region,
    preferred_chromium_hosts,
    rewrite_chromium_download_url,
)
from appstore import pyppeteer_runtime


class ChromiumMirrorTests(unittest.TestCase):
    def test_detect_region_uses_china_timezone(self) -> None:
        region = detect_chromium_download_region({"TZ": "Asia/Shanghai"})

        self.assertEqual(region, "cn")

    def test_preferred_hosts_use_domestic_mirrors_for_china(self) -> None:
        hosts = preferred_chromium_hosts(environ={"UTPUBLISHER_CHROMIUM_REGION": "cn"})

        self.assertEqual(hosts[0], "https://repo.huaweicloud.com")
        self.assertIn("https://mirrors.huaweicloud.com", hosts)
        self.assertIn("https://npmmirror.com/mirrors", hosts)
        self.assertEqual(hosts[-1], OFFICIAL_CHROMIUM_DOWNLOAD_HOST)

    def test_preferred_hosts_use_official_first_for_global_region(self) -> None:
        hosts = preferred_chromium_hosts(environ={"UTPUBLISHER_CHROMIUM_REGION": "global"})

        self.assertEqual(hosts[0], OFFICIAL_CHROMIUM_DOWNLOAD_HOST)
        self.assertIn("https://repo.huaweicloud.com", hosts)

    def test_explicit_pyppeteer_host_wins(self) -> None:
        hosts = preferred_chromium_hosts(
            environ={
                "UTPUBLISHER_CHROMIUM_REGION": "cn",
                "PYPPETEER_DOWNLOAD_HOST": "https://example.invalid/chromium",
            }
        )

        self.assertEqual(hosts[0], "https://example.invalid/chromium")

    def test_named_override_is_supported(self) -> None:
        hosts = preferred_chromium_hosts(environ={"UTPUBLISHER_CHROMIUM_MIRRORS": "npmmirror,official"})

        self.assertEqual(hosts[:2], ("https://npmmirror.com/mirrors", OFFICIAL_CHROMIUM_DOWNLOAD_HOST))

    def test_configure_sets_pyppeteer_host_before_import(self) -> None:
        env: dict[str, str] = {"UTPUBLISHER_CHROMIUM_REGION": "cn"}

        selected = configure_pyppeteer_download_environment(env)

        self.assertEqual(selected, "https://repo.huaweicloud.com")
        self.assertEqual(env["PYPPETEER_DOWNLOAD_HOST"], "https://repo.huaweicloud.com")

    def test_rewrite_download_url_swaps_only_host(self) -> None:
        url = "https://storage.googleapis.com/chromium-browser-snapshots/Linux_x64/1181205/chrome-linux.zip"

        rewritten = rewrite_chromium_download_url(url, "https://repo.huaweicloud.com")

        self.assertEqual(
            rewritten,
            "https://repo.huaweicloud.com/chromium-browser-snapshots/Linux_x64/1181205/chrome-linux.zip",
        )

    def test_explicit_browser_executable_is_supported(self) -> None:
        with patch.dict(os.environ, {"UTPUBLISHER_CHROMIUM_EXECUTABLE": "/usr/bin/chromium"}, clear=False):
            self.assertEqual(pyppeteer_runtime._browser_executable_path(), "/usr/bin/chromium")

    def test_loongarch64_uses_system_browser_when_available(self) -> None:
        def fake_which(command: str) -> str | None:
            return "/usr/bin/chromium" if command == "chromium" else None

        with patch.object(pyppeteer_runtime.platform, "machine", return_value="loongarch64"):
            with patch.object(pyppeteer_runtime.shutil, "which", side_effect=fake_which):
                with patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(pyppeteer_runtime._browser_executable_path(), "/usr/bin/chromium")

    def test_launch_merges_executable_path_at_call_time(self) -> None:
        captured: dict[str, object] = {}

        async def fake_launch(options: dict[str, object]):
            captured.update(options)
            return object()

        with patch.dict(os.environ, {"UTPUBLISHER_CHROMIUM_EXECUTABLE": "/opt/chromium"}, clear=False):
            with patch.object(pyppeteer_runtime, "_pyppeteer_launch", side_effect=fake_launch):
                asyncio.run(pyppeteer_runtime.launch(headless=True))

        self.assertEqual(captured["executablePath"], "/opt/chromium")
        self.assertTrue(captured["headless"])
