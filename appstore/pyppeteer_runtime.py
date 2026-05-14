from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import Any

from appstore.chromium_mirrors import (
    configure_pyppeteer_download_environment,
    preferred_chromium_hosts,
    rewrite_chromium_download_url,
)


configure_pyppeteer_download_environment()

from pyppeteer import launch as _pyppeteer_launch  # noqa: E402


SYSTEM_CHROMIUM_ARCHES = {"aarch64", "arm64", "loong64", "loongarch64"}
SYSTEM_CHROMIUM_COMMANDS = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "microsoft-edge",
    "microsoft-edge-stable",
)


def _current_machine() -> str:
    return platform.machine().strip().lower()


def _configured_browser_executable() -> str:
    for name in (
        "UTPUBLISHER_CHROMIUM_EXECUTABLE",
        "PYPPETEER_EXECUTABLE_PATH",
        "CHROME_EXECUTABLE",
        "CHROMIUM_EXECUTABLE",
    ):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _system_browser_executable() -> str:
    if _current_machine() not in SYSTEM_CHROMIUM_ARCHES:
        return ""
    for command in SYSTEM_CHROMIUM_COMMANDS:
        path = shutil.which(command)
        if path:
            return path
    return ""


def _browser_executable_path() -> str:
    return _configured_browser_executable() or _system_browser_executable()


def _launch_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "args": ["--no-sandbox"],
        "handleSIGINT": False,
        "handleSIGTERM": False,
        "handleSIGHUP": False,
    }
    executable_path = _browser_executable_path()
    if executable_path:
        options["executablePath"] = executable_path
    return options


PYPPETEER_LAUNCH_OPTIONS: dict[str, Any] = _launch_options()


async def launch(options: dict[str, Any] | None = None, **kwargs: Any):
    merged: dict[str, Any] = {}
    if options:
        merged.update(options)
    merged.update(kwargs)
    if not merged.get("executablePath"):
        executable_path = _browser_executable_path()
        if executable_path:
            merged["executablePath"] = executable_path

    if _current_machine() in SYSTEM_CHROMIUM_ARCHES and not merged.get("executablePath"):
        raise RuntimeError(
            "Pyppeteer does not provide a bundled Chromium snapshot for this architecture. "
            "Install a system Chromium/Chrome package, or set UTPUBLISHER_CHROMIUM_EXECUTABLE."
        )
    return await _pyppeteer_launch(merged)


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
