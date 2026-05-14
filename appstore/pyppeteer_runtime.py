from __future__ import annotations

import sys
from typing import Any

from appstore.chromium_mirrors import (
    configure_pyppeteer_download_environment,
    preferred_chromium_hosts,
    rewrite_chromium_download_url,
)


configure_pyppeteer_download_environment()

from pyppeteer import launch  # noqa: E402


PYPPETEER_LAUNCH_OPTIONS: dict[str, Any] = {
    "args": ["--no-sandbox"],
    "handleSIGINT": False,
    "handleSIGTERM": False,
    "handleSIGHUP": False,
}


def install_chromium_download_retry() -> None:
    try:
        import pyppeteer.chromium_downloader as chromium_downloader
    except Exception:
        return

    if getattr(chromium_downloader, "_utpublisher_download_retry_installed", False):
        return

    original_download_zip = chromium_downloader.download_zip

    def download_zip_with_mirrors(url: str):
        last_error: Exception | None = None
        attempted: set[str] = set()
        for host in preferred_chromium_hosts():
            candidate_url = rewrite_chromium_download_url(url, host)
            if candidate_url in attempted:
                continue
            attempted.add(candidate_url)
            try:
                return original_download_zip(candidate_url)
            except Exception as exc:
                last_error = exc
                print(
                    f"[WARN] Chromium download failed from {host}: {exc}",
                    file=sys.stderr,
                )
        if last_error is not None:
            raise last_error
        return original_download_zip(url)

    chromium_downloader.download_zip = download_zip_with_mirrors
    chromium_downloader._utpublisher_download_retry_installed = True


install_chromium_download_retry()
