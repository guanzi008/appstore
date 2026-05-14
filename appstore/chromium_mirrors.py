from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


OFFICIAL_CHROMIUM_DOWNLOAD_HOST = "https://storage.googleapis.com"
CHROMIUM_SNAPSHOTS_PATH = "chromium-browser-snapshots"


@dataclass(frozen=True)
class ChromiumMirror:
    name: str
    host: str
    regions: tuple[str, ...]


DOMESTIC_CHROMIUM_MIRRORS: tuple[ChromiumMirror, ...] = (
    ChromiumMirror("huaweicloud", "https://repo.huaweicloud.com", ("cn", "china", "mainland")),
    ChromiumMirror("huaweicloud-mirror", "https://mirrors.huaweicloud.com", ("cn", "china", "mainland")),
    ChromiumMirror("npmmirror", "https://npmmirror.com/mirrors", ("cn", "china", "mainland")),
)

GLOBAL_CHROMIUM_MIRRORS: tuple[ChromiumMirror, ...] = (
    ChromiumMirror("official", OFFICIAL_CHROMIUM_DOWNLOAD_HOST, ("global", "official")),
)

KNOWN_CHROMIUM_MIRRORS: tuple[ChromiumMirror, ...] = DOMESTIC_CHROMIUM_MIRRORS + GLOBAL_CHROMIUM_MIRRORS
CHINA_TIMEZONES = {
    "Asia/Shanghai",
    "Asia/Chongqing",
    "Asia/Harbin",
    "Asia/Urumqi",
    "Asia/Hong_Kong",
    "Asia/Macau",
}


def normalize_chromium_host(host: str) -> str:
    return host.strip().rstrip("/")


def chromium_snapshots_base_url(host: str) -> str:
    return f"{normalize_chromium_host(host)}/{CHROMIUM_SNAPSHOTS_PATH}"


def _split_env_list(value: str) -> tuple[str, ...]:
    result: list[str] = []
    for item in value.replace(";", ",").split(","):
        normalized = normalize_chromium_host(item)
        if normalized:
            result.append(normalized)
    return tuple(result)


def _known_host_by_name_or_host(value: str) -> str:
    normalized = normalize_chromium_host(value)
    normalized_name = normalized.lower()
    for mirror in KNOWN_CHROMIUM_MIRRORS:
        if normalized_name in {mirror.name.lower(), mirror.host.lower()}:
            return mirror.host
    return normalized


def _timezone_text(environ: Mapping[str, str] | None = None) -> str:
    env = environ or os.environ
    tz = env.get("TZ", "").strip()
    if tz:
        return tz
    for path in (Path("/etc/timezone"), Path("/var/db/zoneinfo")):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    try:
        localtime = Path("/etc/localtime").resolve()
    except OSError:
        return ""
    marker = "zoneinfo/"
    text = str(localtime)
    if marker in text:
        return text.split(marker, 1)[1]
    return ""


def detect_chromium_download_region(environ: Mapping[str, str] | None = None) -> str:
    env = environ or os.environ
    explicit = env.get("UTPUBLISHER_CHROMIUM_REGION", "").strip().lower()
    if explicit and explicit != "auto":
        return explicit

    timezone = _timezone_text(env)
    if timezone in CHINA_TIMEZONES:
        return "cn"

    locale_values = " ".join(
        env.get(name, "")
        for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")
    ).lower()
    if any(marker in locale_values for marker in ("zh_cn", "zh-hans", "zh_sg")):
        return "cn"
    return "global"


def preferred_chromium_hosts(
    *,
    region: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    env = environ or os.environ
    override_hosts = tuple(
        _known_host_by_name_or_host(host)
        for host in _split_env_list(env.get("UTPUBLISHER_CHROMIUM_MIRRORS", ""))
    )
    if override_hosts:
        hosts = list(override_hosts)
    else:
        selected = env.get("UTPUBLISHER_CHROMIUM_MIRROR", "").strip()
        hosts = [_known_host_by_name_or_host(selected)] if selected else []
        resolved_region = (region or detect_chromium_download_region(env)).strip().lower()
        if resolved_region in {"cn", "china", "mainland"}:
            hosts.extend(mirror.host for mirror in DOMESTIC_CHROMIUM_MIRRORS)
            hosts.extend(mirror.host for mirror in GLOBAL_CHROMIUM_MIRRORS)
        else:
            hosts.extend(mirror.host for mirror in GLOBAL_CHROMIUM_MIRRORS)
            hosts.extend(mirror.host for mirror in DOMESTIC_CHROMIUM_MIRRORS)

    explicit_pyppeteer_host = env.get("PYPPETEER_DOWNLOAD_HOST", "").strip()
    if explicit_pyppeteer_host:
        hosts.insert(0, explicit_pyppeteer_host)

    deduped: list[str] = []
    seen: set[str] = set()
    for host in hosts:
        normalized = normalize_chromium_host(host)
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return tuple(deduped)


def configure_pyppeteer_download_environment(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    existing = normalize_chromium_host(env.get("PYPPETEER_DOWNLOAD_HOST", ""))
    if existing:
        env["PYPPETEER_DOWNLOAD_HOST"] = existing
        return existing

    hosts = preferred_chromium_hosts(environ=env)
    selected = hosts[0] if hosts else OFFICIAL_CHROMIUM_DOWNLOAD_HOST
    env["PYPPETEER_DOWNLOAD_HOST"] = selected
    env.setdefault("UTPUBLISHER_CHROMIUM_DOWNLOAD_HOST", selected)
    return selected


def rewrite_chromium_download_url(url: str, host: str) -> str:
    marker = f"/{CHROMIUM_SNAPSHOTS_PATH}/"
    if marker not in url:
        return url
    suffix = url.split(marker, 1)[1]
    return f"{chromium_snapshots_base_url(host)}/{suffix}"
