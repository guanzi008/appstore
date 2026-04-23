from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from appstore.appstore_client import AppStoreClient
from appstore.models import BaselineOption, CapabilityCache, SystemLine, SystemTemplate


def _as_mapping(payload):
    if not isinstance(payload, dict):
        raise ValueError(f"unexpected capability payload: {payload}")
    return payload


def normalize_linglong_system_lines(payload: dict) -> dict[str, SystemLine]:
    rows = _as_mapping(payload).get("datas", [])
    if not isinstance(rows, list):
        raise ValueError(f"unexpected linglong system line payload: {payload}")
    normalized: dict[str, SystemLine] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"unexpected linglong system line row: {row}")
        code = str(row.get("dictValue", "")).strip()
        label = str(row.get("dictLabel", "")).strip()
        if not code:
            continue
        normalized[code] = SystemLine(code=code, label=label, family="linglong")
    return normalized


def normalize_deb_system_lines(adapt_info: dict) -> dict[str, SystemLine]:
    datas = _as_mapping(adapt_info).get("datas", {})
    if not isinstance(datas, dict):
        raise ValueError(f"unexpected adapt-info payload: {adapt_info}")
    rows = datas.get("systemPlatformList", [])
    if not isinstance(rows, list):
        raise ValueError(f"unexpected adapt-info payload: {adapt_info}")
    normalized: dict[str, SystemLine] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"unexpected adapt-info system platform row: {row}")
        code = str(row.get("code", "")).strip()
        label = str(row.get("name", "")).strip()
        if not code:
            continue
        normalized[code] = SystemLine(code=code, label=label, family="deb")
    return normalized


def normalize_baseline_options(adapt_info: dict) -> dict[str, tuple[BaselineOption, ...]]:
    datas = _as_mapping(adapt_info).get("datas", {})
    if not isinstance(datas, dict):
        raise ValueError(f"unexpected adapt-info payload: {adapt_info}")
    rows = datas.get("shopVersionList", [])
    if not isinstance(rows, list):
        raise ValueError(f"unexpected adapt-info payload: {adapt_info}")
    grouped: dict[str, list[BaselineOption]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"unexpected adapt-info baseline row: {row}")
        system_line_code = str(row.get("system_platform", "")).strip()
        baseline_id = str(row.get("id", "")).strip()
        minor_version = str(row.get("minor_version", "")).strip()
        if not system_line_code or not baseline_id:
            continue
        family = "linglong" if int(row.get("pkgInstallMode", 1) or 1) == 2 else "deb"
        key = f"{family}:{system_line_code}"
        grouped.setdefault(key, []).append(
            BaselineOption(
                system_line_code=system_line_code,
                baseline_id=baseline_id,
                minor_version=minor_version,
            )
        )
    return {key: tuple(value) for key, value in grouped.items()}


def write_capability_cache(cache_dir: Path, cache: CapabilityCache) -> Path:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    timestamp_label = cache.generated_at.replace(":", "-")
    timestamp_path = cache_dir / f"{timestamp_label}.json"
    latest_path = cache_dir / "latest.json"
    payload = json.dumps(asdict(cache), ensure_ascii=False, indent=2)
    timestamp_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")
    return latest_path


def load_capability_cache(path: Path | str) -> CapabilityCache:
    cache_path = Path(path)
    if cache_path.is_dir():
        cache_path = cache_path / "latest.json"
    payload = json.loads(cache_path.read_text(encoding="utf-8"))

    def load_system_lines(data: dict[str, dict]) -> dict[str, SystemLine]:
        return {
            code: SystemLine(code=entry["code"], label=entry["label"], family=entry["family"])
            for code, entry in data.items()
        }

    def load_baseline_options(data: dict[str, list[dict]]) -> dict[str, tuple[BaselineOption, ...]]:
        return {
            key: tuple(
                BaselineOption(
                    system_line_code=item["system_line_code"],
                    baseline_id=item["baseline_id"],
                    minor_version=item["minor_version"],
                )
                for item in items
            )
            for key, items in data.items()
        }

    return CapabilityCache(
        generated_at=payload["generated_at"],
        deb_system_lines=load_system_lines(payload.get("deb_system_lines", {})),
        linglong_system_lines=load_system_lines(payload.get("linglong_system_lines", {})),
        baseline_options=load_baseline_options(payload.get("baseline_options", {})),
    )


def build_system_templates(cache: CapabilityCache) -> tuple[SystemTemplate, ...]:
    templates: list[SystemTemplate] = []

    for package_family, system_lines in (
        ("deb", cache.deb_system_lines),
        ("linglong", cache.linglong_system_lines),
    ):
        for sup_sys_code, system_line in sorted(system_lines.items()):
            baseline_key = f"{package_family}:{sup_sys_code}"
            templates.append(
                SystemTemplate(
                    column_prefix=_template_column_prefix(
                        package_family=package_family,
                        sup_sys_code=sup_sys_code,
                    ),
                    package_family=package_family,
                    sup_sys_code=sup_sys_code,
                    system_label=system_line.label,
                    baseline_options=cache.baseline_options.get(baseline_key, ()),
                )
            )
    return tuple(templates)


def _template_column_prefix(*, package_family: str, sup_sys_code: str) -> str:
    return f"sys__{package_family}__{sup_sys_code}"


def sync_capabilities_to_cache(client: AppStoreClient, cache_dir: Path) -> Path:
    linglong_rows = client.fetch_linglong_system_lines()
    adapt_info = client.fetch_adapt_info()
    cache = CapabilityCache(
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        deb_system_lines=normalize_deb_system_lines(adapt_info),
        linglong_system_lines=normalize_linglong_system_lines({"datas": linglong_rows}),
        baseline_options=normalize_baseline_options(adapt_info),
    )
    return write_capability_cache(cache_dir, cache)
