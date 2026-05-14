import unittest

from appstore.chromium_mirrors import (
    OFFICIAL_CHROMIUM_DOWNLOAD_HOST,
    configure_pyppeteer_download_environment,
    detect_chromium_download_region,
    preferred_chromium_hosts,
    rewrite_chromium_download_url,
)


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
