from __future__ import annotations

import json
import re
import sys
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui.assets import detect_asset_candidates
from ui.backend import (
    BatchGroupSubmissionPlan,
    build_existing_detail_editor_defaults,
    DEFAULT_SESSION_CACHE_DIR,
    capture_screenshots_for_group,
    DEFAULT_OUTPUT_ROOT,
    fetch_category_options,
    fetch_existing_app_detail,
    find_existing_apps,
    generate_english_listing_texts,
    load_or_sync_capabilities,
    login_with_credentials,
    login_with_browser_state,
    preprocess_submission_assets,
    StoreAppMatch,
    SubmissionResult,
    adapt_arch_label,
    build_cpu_clip_options,
    build_motherboard_options,
    submit_applications_batch,
    sync_capabilities,
    SystemTargetOption,
    try_restore_cached_login,
    build_target_options,
    default_target_ranges,
    package_group_store_arch_codes,
    preference_baseline_selectable,
    preference_arch_key,
    store_arch_code,
)
from appstore.appstore_client import package_family_from_app_detail, package_format_from_app_detail
from appstore.capabilities import system_lines_for_arch
from appstore.platforms import ARCHITECTURES, normalize_package_arch
from appstore.translation import LANGUAGE_LABELS
from appstore.update_payload import DRAFT_STATUS, draft_detail_id, extract_detail_data, origin_pkg_arch
from ui.package_meta import PackageGroup, PackageMetadata, analyze_package_group, extract_archive_icon, extract_deb_icon
from ui.preferences import PreferenceStore, UIPreferences, normalize_default_target_ranges
from appstore.session_state import SessionStateStore


CDN_BASE_URL = "https://app-store-files.uniontech.com/"


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    return payload


def _login_to_json(context) -> dict:
    if context is None:
        return {
            "logged_in": False,
            "account_label": "",
            "login_mode": "",
            "session_account": "",
        }
    session_account = context.session_state_path.stem if context.session_state_path is not None else ""
    return {
        "logged_in": True,
        "account_label": context.account_label,
        "login_mode": context.login_mode,
        "session_account": session_account,
    }


def _capability_cache_to_json(cache) -> dict:
    if cache is None:
        return {
            "loaded": False,
            "generated_at": "",
            "deb_system_line_count": 0,
            "linglong_system_line_count": 0,
            "baseline_group_count": 0,
            "arch_option_count": 0,
            "cpu_clip_option_count": 0,
            "motherboard_option_count": 0,
        }
    default_options = {}
    for family in ("deb", "linglong"):
        package_options = {}
        for arch in ARCHITECTURES.values():
            lines = system_lines_for_arch(cache, family, arch.code)
            if not lines:
                continue
            package_options[preference_arch_key(arch.manifest_value)] = [
                {
                    "code": code,
                    "label": line.label,
                    "baseline_options": [
                        {
                            "id": option.baseline_id,
                            "version": option.minor_version,
                            "selectable": preference_baseline_selectable(family, code, option.minor_version),
                        }
                        for option in reversed(cache.baseline_options.get(f"{family}:{code}", ()))
                    ],
                }
                for code, line in lines.items()
            ]
        if package_options:
            default_options[family] = package_options
    return {
        "loaded": True,
        "generated_at": cache.generated_at,
        "deb_system_line_count": len(cache.deb_system_lines),
        "linglong_system_line_count": len(cache.linglong_system_lines),
        "baseline_group_count": len(cache.baseline_options),
        "arch_option_count": len(cache.arch_options),
        "cpu_clip_option_count": len(cache.cpu_clip_options),
        "motherboard_option_count": len(cache.motherboard_options),
        "default_system_line_options": default_options,
    }


def _category_to_json(option) -> dict:
    return {
        "id": option.category_id,
        "name": option.name,
        "english_name": option.english_name,
    }


def _match_to_json(match) -> dict:
    return {
        "app_id": match.app_id,
        "detail_id": match.detail_id,
        "pkg_name": match.pkg_name,
        "app_name": match.app_name,
        "package_family": getattr(match, "package_family", ""),
        "package_format": getattr(match, "package_format", ""),
    }


def _target_to_json(option) -> dict:
    return {
        "package_path": option.package_path,
        "package_label": option.package_label,
        "package_arch": option.package_arch,
        "code": option.code,
        "label": option.label,
        "package_family": option.package_family,
        "selected": option.selected,
        "baseline_id": option.baseline_id,
        "selected_baseline_ids": list(option.selected_baseline_ids),
        "unsupported_baseline_ids": list(option.unsupported_baseline_ids),
        "baseline_options": [
            {
                "id": baseline_id,
                "version": version,
            }
            for baseline_id, version in option.baseline_options
        ],
    }


def _selected_codes_from_options(options: tuple[dict[str, object], ...]) -> tuple[str, ...]:
    return tuple(
        str(option.get("code", "")).strip()
        for option in options
        if bool(option.get("selected", False)) and str(option.get("code", "")).strip()
    )


def _code_items(value) -> tuple[str, ...]:
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                code = str(
                    item.get("code", "")
                    or item.get("id", "")
                    or item.get("system_platform", "")
                    or item.get("dictValue", "")
                ).strip()
            else:
                code = str(item).strip()
            if code:
                result.append(code)
        return tuple(result)
    if value is None:
        return ()
    return tuple(token.strip() for token in str(value).split(",") if token.strip())


def _dedupe(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _safe_asset_stem(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return (normalized or "asset")[:80]


def _asset_source_from_value(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("//"):
        return f"https:{text}"

    path = Path(text).expanduser()
    if path.is_absolute() and path.exists():
        return str(path)
    if text.startswith("/"):
        return f"{CDN_BASE_URL.rstrip('/')}{text}"
    if "/" in text and not text.startswith("online://") and not text.startswith("data:"):
        return f"{CDN_BASE_URL}{text.lstrip('/')}"
    return ""


def _image_suffix(data: bytes, source: str) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    suffix = Path(source.split("?", 1)[0]).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        return suffix
    return ".png"


def _download_asset_source(source: str, *, output_dir: Path, filename_stem: str, session=None) -> Path | None:
    normalized = _asset_source_from_value(source)
    if not normalized:
        return None

    local_path = Path(normalized).expanduser()
    if local_path.is_absolute() and local_path.exists():
        return local_path.resolve()

    output_dir.mkdir(parents=True, exist_ok=True)
    getter = session.get if session is not None and hasattr(session, "get") else requests.get
    response = getter(normalized, timeout=60)
    response.raise_for_status()
    data = response.content
    if not data:
        return None
    target = output_dir / f"{_safe_asset_stem(filename_stem)}{_image_suffix(data, normalized)}"
    target.write_bytes(data)
    return target


def _detail_lan_infos(existing_app_detail: dict) -> list[dict]:
    infos = _detail_data(existing_app_detail).get("app_lan_infos") or []
    return [info for info in infos if isinstance(info, dict)]


def _preferred_asset_lan_info(existing_app_detail: dict) -> dict:
    infos = _detail_lan_infos(existing_app_detail)
    for lan in ("zh_CN", "zh", "en_US", "en"):
        for info in infos:
            if str(info.get("lan", "")).strip() == lan:
                return info
    return infos[0] if infos else {}


def _icon_source_from_lan_info(lan_info: dict) -> str:
    for key in (
        "icon_save_key",
        "iconSaveKey",
        "icon_url",
        "iconUrl",
        "icon_path",
        "iconPath",
        "logo_url",
        "logoUrl",
        "cover_save_key",
        "coverSaveKey",
    ):
        source = _asset_source_from_value(lan_info.get(key))
        if source:
            return source
    return ""


def _screenshot_sources_from_lan_info(lan_info: dict) -> tuple[str, ...]:
    screenshot_rows = lan_info.get("appScreenShotList")
    if not isinstance(screenshot_rows, list):
        return ()

    def sort_key(item: dict) -> int:
        try:
            return int(item.get("sort", 0) or 0)
        except (TypeError, ValueError):
            return 0

    sources: list[str] = []
    for item in sorted((row for row in screenshot_rows if isinstance(row, dict)), key=sort_key):
        for key in (
            "screen_shot_key",
            "screenShotKey",
            "screenshot_key",
            "screenshotKey",
            "image_key",
            "imageKey",
            "image_url",
            "imageUrl",
            "url",
            "path",
        ):
            source = _asset_source_from_value(item.get(key))
            if source:
                sources.append(source)
                break
    return tuple(_dedupe(sources))


def _sync_existing_detail_assets(match: StoreAppMatch, existing_app_detail: dict, *, session=None) -> dict[str, object]:
    lan_info = _preferred_asset_lan_info(existing_app_detail)
    if not lan_info:
        return {"icon_path": "", "screenshot_paths": [], "asset_warnings": []}

    output_key = match.app_id or match.detail_id or match.pkg_name or match.app_name or "online-app"
    output_dir = DEFAULT_OUTPUT_ROOT / "cpp" / "assets" / _safe_asset_stem(output_key) / "synced"
    warnings: list[str] = []

    icon_path: Path | None = None
    icon_source = _icon_source_from_lan_info(lan_info)
    if icon_source:
        try:
            icon_path = _download_asset_source(
                icon_source,
                output_dir=output_dir,
                filename_stem="icon",
                session=session,
            )
        except Exception as exc:
            warnings.append(f"线上图标同步失败：{exc}")

    screenshot_paths: list[Path] = []
    for index, source in enumerate(_screenshot_sources_from_lan_info(lan_info), start=1):
        try:
            synced = _download_asset_source(
                source,
                output_dir=output_dir,
                filename_stem=f"screenshot-{index:02d}",
                session=session,
            )
        except Exception as exc:
            warnings.append(f"第 {index} 张线上截图同步失败：{exc}")
            continue
        if synced is not None:
            screenshot_paths.append(synced)

    return {
        "icon_path": str(icon_path) if icon_path is not None else "",
        "screenshot_paths": [str(path) for path in screenshot_paths],
        "asset_warnings": warnings,
    }


def _detail_data(existing_app_detail: dict) -> dict:
    datas = existing_app_detail.get("datas")
    if isinstance(datas, dict):
        return datas
    return existing_app_detail


def _adapt_options_from_codes(options: dict, selected_codes: tuple[str, ...]) -> list[dict]:
    selected = {str(code).strip() for code in selected_codes if str(code).strip()}
    result: list[dict] = []
    for code, option in options.items():
        normalized_code = str(code).strip()
        result.append(
            {
                "code": normalized_code,
                "label": str(getattr(option, "label", "") or normalized_code).strip(),
                "selected": normalized_code in selected,
            }
        )
    return result


def _arch_label_from_codes(capability_cache, arch_codes: tuple[str, ...]) -> str:
    labels: list[str] = []
    for code in arch_codes:
        option = capability_cache.arch_options.get(code) if capability_cache is not None else None
        label = str(getattr(option, "label", "") or code).strip()
        if label and label not in labels:
            labels.append(label)
    return ",".join(labels)


def _origin_pkg_value(origin_pkg: dict, *keys: str) -> str:
    for key in keys:
        value = origin_pkg.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return ""


def _display_arch_from_origin_pkg(capability_cache, origin_pkg: dict) -> tuple[str, str]:
    raw_code, raw_label = origin_pkg_arch(origin_pkg)
    label = ""
    if capability_cache is not None and raw_code:
        option = capability_cache.arch_options.get(raw_code)
        if option is not None:
            label = str(option.label or "").strip()
    if not label and raw_label:
        label = {
            "x86": "x86",
            "amd64": "x86",
            "x86_64": "x86",
            "arm": "arm64",
            "arm64": "arm64",
            "aarch64": "arm64",
            "loong": "loong64",
            "loong64": "loong64",
            "loongarch64": "loongarch64",
            "sw64": "sw64",
            "sw_64": "sw64",
        }.get(raw_label.strip().lower(), raw_label)
    if not label and raw_code:
        label = {
            "3": "arm64",
            "4": "x86",
            "5": "loongarch64",
            "6": "loong64",
            "8": "sw64",
        }.get(raw_code, raw_code)
    return raw_code, label


def _online_package_key(match: StoreAppMatch, origin_pkg: dict, index: int, arch_label: str) -> str:
    app_key = match.app_id or match.detail_id or match.pkg_name or "app"
    pkg_name = _origin_pkg_value(origin_pkg, "pkg_name", "packageName") or match.pkg_name or "package"
    version = _origin_pkg_value(origin_pkg, "pkg_version", "pkgVersion") or "unknown"
    return f"online://{app_key}/{index}/{pkg_name}/{version}/{arch_label or 'arch'}"


def _status_text_from_origin_pkg(origin_pkg: dict) -> str:
    progress = _origin_pkg_value(origin_pkg, "progressPercent")
    if progress:
        try:
            percent = int(float(progress))
        except ValueError:
            percent = -1
        if percent >= 100:
            return "上传完成"
        if percent >= 0:
            return f"{percent}%"
    return _origin_pkg_value(origin_pkg, "statusStr", "status", "progressStr") or "已上传"


def _size_text_from_origin_pkg(origin_pkg: dict) -> str:
    value = origin_pkg.get("pkg_size", origin_pkg.get("pkgSize", ""))
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if any(unit in text.lower() for unit in ("kb", "mb", "gb")):
            return text
        try:
            value = float(text)
        except ValueError:
            return text
    try:
        size = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    if size <= 0:
        return ""
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.1f}GB"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}MB"
    if size >= 1024:
        return f"{size / 1024:.1f}KB"
    return f"{int(size)}B"


def _origin_pkg_selection(origin_pkg: dict, fit_info: dict, field: str, legacy_field: str):
    for key in (field, legacy_field):
        if origin_pkg.get(key) is not None:
            return origin_pkg[key]
    return fit_info.get(field)


def _versions_for_line(value, code: str, known_ids: set[str], only_selected_line: bool) -> tuple[str, ...]:
    entries = value if isinstance(value, list) else _code_items(value)
    result: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            line = _origin_pkg_value(entry, "system_platform", "systemPlatform")
            if line and line != code:
                continue
            baseline_id = _origin_pkg_value(entry, "id", "baseline_id", "baselineId", "code")
            ids = (baseline_id,) if baseline_id else ()
        else:
            line = ""
            ids = _code_items(entry)
        result.extend(baseline_id for baseline_id in ids if line or only_selected_line or baseline_id in known_ids)
    return _dedupe(result)


def _online_packages_from_detail(
    match: StoreAppMatch,
    origin_pkgs: list,
    *,
    package_family: str,
    package_format: str,
    capability_cache,
    icon_path: str,
) -> list[dict]:
    packages: list[dict] = []
    for index, item in enumerate(origin_pkgs):
        if not isinstance(item, dict):
            continue
        arch_code, arch_label = _display_arch_from_origin_pkg(capability_cache, item)
        pkg_name = _origin_pkg_value(item, "pkg_name", "packageName") or match.pkg_name
        version = _origin_pkg_value(item, "pkg_version", "pkgVersion")
        file_name = " ".join(part for part in (pkg_name, version) if part).strip() or "线上包"
        packages.append(
            {
                "path": _online_package_key(match, item, index, arch_label),
                "origin_id": _origin_pkg_value(item, "id"),
                "online": True,
                "file_name": file_name,
                "pkg_name": pkg_name,
                "version": version,
                "arch": arch_label or arch_code,
                "arch_code": arch_code,
                "family": package_family,
                "format": package_format,
                "status_text": _status_text_from_origin_pkg(item),
                "size_text": _size_text_from_origin_pkg(item),
                "system_text": _origin_pkg_value(item, "systemStr", "system_text", "systemPlatform"),
                "upload_time": _origin_pkg_value(item, "upload_time", "uploadTime"),
                "icon_path": icon_path,
            }
        )
    return packages


def _target_rows_for_selection(
    capability_cache,
    *,
    package_family: str,
    package_path: str,
    package_label: str,
    arch_label: str,
    arch_code: str = "",
    selected_system_codes: tuple[str, ...],
    selected_baselines,
    selected_unsupported_baselines,
) -> list[dict]:
    selected_system_code_set = set(selected_system_codes)
    system_lines = {}
    if capability_cache is not None:
        system_lines = system_lines_for_arch(capability_cache, package_family, arch_code or store_arch_code(arch_label))
    family_lines = system_lines_for_arch(capability_cache, package_family, "") if capability_cache is not None else {}
    targets: list[dict] = []
    for code in _dedupe([*system_lines, *selected_system_codes]):
        if code == "0":
            continue
        system_line = family_lines.get(code)
        baseline_options = capability_cache.baseline_options.get(f"{package_family}:{code}", ()) if capability_cache is not None else ()
        selected = code in selected_system_code_set
        known_ids = {option.baseline_id for option in baseline_options}
        only_selected_line = selected and len(selected_system_code_set) == 1
        effective_baselines = _versions_for_line(selected_baselines, code, known_ids, only_selected_line) if selected else ()
        unsupported_baselines = _versions_for_line(selected_unsupported_baselines, code, known_ids, only_selected_line) if selected else ()
        targets.append(
            {
                "package_path": package_path,
                "package_label": package_label,
                "package_arch": normalize_package_arch(arch_code or arch_label),
                "code": code,
                "label": system_line.label if system_line is not None else f"系统线 {code}",
                "package_family": package_family,
                "selected": selected,
                "baseline_id": effective_baselines[0] if effective_baselines else "",
                "selected_baseline_ids": list(effective_baselines),
                "unsupported_baseline_ids": list(unsupported_baselines),
                "baseline_options": [
                    {
                        "id": option.baseline_id,
                        "version": option.minor_version,
                    }
                    for option in baseline_options
                ],
            }
        )
    return targets


def _online_targets_from_detail(
    capability_cache,
    detail_data: dict,
    *,
    match: StoreAppMatch,
    package_family: str,
    package_label: str,
    arch_label: str,
    packages: list[dict],
) -> list[dict]:
    fit_info = detail_data.get("app_fit_info") or {}
    raw_packages = detail_data.get("app_origin_pkgs")
    origin_pkgs = [pkg for pkg in raw_packages if isinstance(pkg, dict)] if isinstance(raw_packages, list) else []
    if packages and origin_pkgs:
        targets: list[dict] = []
        for index, package in enumerate(packages):
            origin_pkg = origin_pkgs[index] if index < len(origin_pkgs) else {}
            targets.extend(
                _target_rows_for_selection(
                    capability_cache,
                    package_family=package_family,
                    package_path=str(package.get("path", "")).strip(),
                    package_label=str(package.get("file_name", "") or package_label).strip(),
                    arch_label=str(package.get("arch", "")).strip(),
                    arch_code=str(package.get("arch_code", "")).strip(),
                    selected_system_codes=_code_items(_origin_pkg_selection(origin_pkg, fit_info, "system_platform", "supSys")),
                    selected_baselines=_origin_pkg_selection(origin_pkg, fit_info, "baseline", "supBlineVer"),
                    selected_unsupported_baselines=_origin_pkg_selection(origin_pkg, fit_info, "unsupportBaseline", "unsupportBlineVers"),
                )
            )
        return targets

    return _target_rows_for_selection(
        capability_cache,
        package_family=package_family,
        package_path="",
        package_label=package_label,
        arch_label=arch_label,
        selected_system_codes=_code_items(fit_info.get("system_platform")),
        selected_baselines=fit_info.get("baseline"),
        selected_unsupported_baselines=fit_info.get("unsupportBaseline"),
    )


def _online_group_from_detail(match: StoreAppMatch, existing_app_detail: dict, defaults: dict, capability_cache) -> dict:
    data = _detail_data(existing_app_detail)
    draft_id = draft_detail_id(existing_app_detail)
    fit_info = data.get("app_fit_info") or {}
    origin_pkgs = data.get("app_origin_pkgs") if isinstance(data.get("app_origin_pkgs"), list) else []
    package_family = package_family_from_app_detail(existing_app_detail) or "deb"
    package_format = package_format_from_app_detail(existing_app_detail, package_family) or (
        "uab" if package_family == "linglong" else "deb"
    )

    arch_codes = _dedupe(_code_items(fit_info.get("arch")))
    if not arch_codes:
        arch_codes = _dedupe([origin_pkg_arch(pkg)[0] for pkg in origin_pkgs if isinstance(pkg, dict)])
    arch_label = _arch_label_from_codes(capability_cache, arch_codes)
    version_values = _dedupe(
        [
            str(pkg.get("pkg_version", "")).strip()
            for pkg in origin_pkgs
            if isinstance(pkg, dict) and str(pkg.get("pkg_version", "")).strip()
        ]
    )
    cpu_codes = _dedupe(_code_items(fit_info.get("cpu_clip")))
    motherboard_codes = _dedupe(_code_items(fit_info.get("motherboard")))
    app_name = str(defaults.get("app_name_zh", "") or match.app_name or match.pkg_name).strip()
    developer_name = str(defaults.get("developer_name", "") or "").strip()
    pkg_name = match.pkg_name or str(data.get("pkg_name", "") or "").strip() or app_name
    icon_path = str(defaults.get("icon_path", "") or "").strip()
    packages = _online_packages_from_detail(
        match,
        origin_pkgs,
        package_family=package_family,
        package_format=package_format,
        capability_cache=capability_cache,
        icon_path=icon_path,
    )
    if packages:
        arch_values = list(_dedupe([str(package.get("arch", "")).strip() for package in packages]))
        arch_label = ",".join(arch_values)
    else:
        arch_values = [part.strip() for part in arch_label.split(",") if part.strip()] if arch_label else list(arch_codes)

    return {
        "key": f"online|{match.app_id or match.detail_id or pkg_name}",
        "online_only": True,
        "draft_detail_id": draft_id,
        "pkg_name": pkg_name,
        "pkg_version": ",".join(version_values),
        "package_family": package_family,
        "package_format": package_format,
        "pkg_arches": arch_values,
        "display_name": app_name,
        "short_description": str(defaults.get("short_desc_zh", "") or "").strip(),
        "full_description": str(defaults.get("full_desc_zh", "") or "").strip(),
        "homepage": str(defaults.get("website", "") or "").strip(),
        "packages": packages,
        "selected_package_path": packages[0]["path"] if packages else "",
        "icon_path": icon_path,
        "screenshot_paths": list(defaults.get("screenshot_paths", []) or []),
        "asset_warnings": list(defaults.get("asset_warnings", []) or []),
        "existing_matches": [_match_to_json(match)],
        "selected_match_app_id": match.app_id,
        "submission_mode": "update",
        "app_name_zh": app_name,
        "developer_name": developer_name,
        "website": str(defaults.get("website", "") or "").strip(),
        "short_desc_zh": str(defaults.get("short_desc_zh", "") or "").strip(),
        "full_desc_zh": str(defaults.get("full_desc_zh", "") or "").strip(),
        "category_id": str(defaults.get("category_id", "") or "1"),
        "region_codes": list(defaults.get("region_codes", ("1",)) or ("1",)),
        "note_zh": str(defaults.get("note_zh", "") or "").strip() if draft_id else "",
        "app_name_en": str(defaults.get("app_name_en", "") or "").strip(),
        "short_desc_en": str(defaults.get("short_desc_en", "") or "").strip(),
        "full_desc_en": str(defaults.get("full_desc_en", "") or "").strip(),
        "note_en": str(defaults.get("note_en", "") or "").strip() if draft_id else "",
        "manual_en_edited": bool(
            defaults.get("app_name_en") or defaults.get("short_desc_en") or defaults.get("full_desc_en")
            or (draft_id and defaults.get("note_en"))
        ),
        "metadata_edited": True,
        "replace_assets": False,
        "adapt_arch_codes": list(arch_codes),
        "adapt_arch_label": arch_label,
        "cpu_clip_options": _adapt_options_from_codes(capability_cache.cpu_clip_options, cpu_codes) if capability_cache is not None else [],
        "cpu_clip_codes": list(cpu_codes),
        "motherboard_options": _adapt_options_from_codes(capability_cache.motherboard_options, motherboard_codes) if capability_cache is not None else [],
        "motherboard_codes": list(motherboard_codes),
        "targets": _online_targets_from_detail(
            capability_cache,
            data,
            match=match,
            package_family=package_family,
            package_label=app_name,
            arch_label=arch_label,
            packages=packages,
        ),
    }


def _normalized_arch_key(value: object) -> str:
    return normalize_package_arch(str(value or ""))


def _is_online_package(package: dict) -> bool:
    return bool(package.get("online")) or str(package.get("path", "")).strip().startswith("online://")


def _local_package_paths(group: dict) -> list[Path]:
    paths: list[Path] = []
    for package in group.get("packages", []):
        if not isinstance(package, dict) or _is_online_package(package):
            continue
        path = str(package.get("path", "")).strip()
        if path:
            paths.append(Path(path).expanduser().resolve())
    return paths


def _dedupe_json_strings(values: list[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _remap_target_to_package(target: dict, package: dict) -> dict:
    remapped = dict(target)
    remapped["package_path"] = str(package.get("path", "")).strip()
    remapped["package_label"] = str(package.get("file_name") or package.get("pkg_name") or target.get("package_label") or "").strip()
    remapped["package_arch"] = str(package.get("arch") or target.get("package_arch") or "").strip()
    return remapped


def _dedupe_target_rows(targets: list[dict]) -> list[dict]:
    result: dict[tuple[str, str], dict] = {}
    for target in targets:
        key = (
            str(target.get("package_path", "")).strip(),
            str(target.get("code", "")).strip(),
        )
        if key not in result or (target.get("selected") and not result[key].get("selected")):
            result[key] = target
    return list(result.values())


def _packages_with_online_remainder(base_group: dict, online_group: dict) -> list[dict]:
    packages = _local_packages_with_online_icon(base_group, online_group)
    local_arch_keys = {
        _normalized_arch_key(package.get("arch"))
        for package in packages
        if _normalized_arch_key(package.get("arch"))
    }
    for online_package in online_group.get("packages", []) or []:
        if not isinstance(online_package, dict):
            continue
        online_arch_key = _normalized_arch_key(online_package.get("arch_code") or online_package.get("arch"))
        if online_arch_key and online_arch_key in local_arch_keys:
            continue
        packages.append(dict(online_package))
    return packages


def _targets_for_auto_matched_packages(base_group: dict, online_group: dict, packages: list[dict]) -> list[dict]:
    base_targets = [value for value in base_group.get("targets", []) if isinstance(value, dict)]
    online_targets = [value for value in online_group.get("targets", []) if isinstance(value, dict)]
    if not packages or not online_targets:
        return base_targets

    remapped_targets: list[dict] = []
    local_paths = {
        str(package.get("path", "")).strip()
        for package in base_group.get("packages", [])
        if isinstance(package, dict)
    }
    for package in packages:
        package_path = str(package.get("path", "")).strip()
        if not package_path:
            continue
        fallback = [target for target in base_targets if str(target.get("package_path", "")).strip() == package_path]
        if package_path in local_paths:
            package_arch_key = _normalized_arch_key(package.get("arch"))
            available_codes = {target.get("code") for target in fallback}
            matched_targets = [
                target
                for target in online_targets
                if package_arch_key and _normalized_arch_key(target.get("package_arch")) == package_arch_key
                and (not available_codes or target.get("code") in available_codes)
            ]
        else:
            matched_targets = [
                target
                for target in online_targets
                if str(target.get("package_path", "")).strip() == package_path
            ]
        if matched_targets:
            remapped_targets.extend(_remap_target_to_package(target, package) for target in matched_targets)
        else:
            remapped_targets.extend(fallback)

    if not remapped_targets:
        return base_targets
    return _dedupe_target_rows(remapped_targets)


def _local_packages_with_online_icon(base_group: dict, online_group: dict) -> list[dict]:
    packages = [dict(value) for value in base_group.get("packages", []) if isinstance(value, dict)]
    online_icon_path = str(online_group.get("icon_path", "") or "").strip()
    if not online_icon_path:
        return packages
    for package in packages:
        package["icon_path"] = online_icon_path
    return packages


def _append_imported_packages(group: dict, previous: dict) -> dict:
    previous_packages = {
        package["path"]: package
        for package in previous.get("packages", [])
        if isinstance(package, dict) and package.get("path")
    }
    previous_targets = [row for row in previous.get("targets", []) if isinstance(row, dict)]
    online_packages = [package for package in previous_packages.values() if _is_online_package(package)]
    online_paths = {package["path"] for package in online_packages}
    online_group = {
        "packages": online_packages,
        "targets": [row for row in previous_targets if row.get("package_path") in online_paths],
        "icon_path": previous.get("icon_path") if previous.get("auto_matched_online_app") or online_packages else "",
    }
    packages = _packages_with_online_remainder(group, online_group)
    targets = _targets_for_auto_matched_packages(group, online_group, packages)
    retained_paths: set[str] = set()
    for package in packages:
        old_package = previous_packages.get(package.get("path"))
        if old_package and _normalized_arch_key(old_package.get("arch")) == _normalized_arch_key(package.get("arch")):
            retained_paths.add(package["path"])
            if old_package.get("icon_path"):
                package["icon_path"] = old_package["icon_path"]

    retained_targets = [row for row in previous_targets if row.get("package_path") in retained_paths]
    retained_target_paths = {row["package_path"] for row in retained_targets}
    arches = _dedupe_json_strings([package.get("arch") for package in packages])
    merged = {**group, **previous}
    for key in ("key", "pkg_name", "pkg_version", "package_family", "package_format"):
        merged[key] = group[key]
    merged.update(
        online_only=False,
        packages=packages,
        pkg_arches=arches,
        targets=retained_targets + [row for row in targets if row.get("package_path") not in retained_target_paths],
        adapt_arch_codes=_dedupe_json_strings([store_arch_code(arch) for arch in arches]),
        adapt_arch_label=", ".join(arches),
        asset_warnings=_dedupe_json_strings([*(previous.get("asset_warnings") or []), *(group.get("asset_warnings") or [])]),
    )
    return merged


def build_revision_update_group(
    client, detail_id: str, *, capability_cache, package_paths: tuple[Path, ...] = (),
    removed_package_ids: tuple[str, ...] = (), asset_dir: Path | None = None,
) -> tuple[dict, str]:
    detail = client.get_app_detail(detail_id)
    data = extract_detail_data(detail)
    basic = data.get("app_basic_info") or {}
    revision_id = str(basic.get("id") or "").strip()
    if revision_id and revision_id != detail_id:
        raise ValueError("商店返回的版本记录与请求不一致。")
    app_id = str(basic.get("app_id") or data.get("app_id") or "").strip()
    package_names = _dedupe_json_strings([
        basic.get("pkg_name"), data.get("pkg_name"),
        *(_origin_pkg_value(package, "pkg_name", "packageName") for package in data.get("app_origin_pkgs") or ()
          if isinstance(package, dict)),
    ])
    if not app_id or not package_names:
        raise ValueError("版本记录缺少应用 ID 或包名。")
    pkg_name = package_names[0]
    defaults = build_existing_detail_editor_defaults(detail, fallback_name=pkg_name)
    match = StoreAppMatch(app_id, detail_id, pkg_name, str(defaults["app_name_zh"]))
    group = _online_group_from_detail(match, detail, defaults, capability_cache)
    origin_ids = {package["origin_id"] for package in group["packages"]}
    if removed_package_ids:
        if not group["draft_detail_id"]:
            raise ValueError("只有草稿可以移除已有安装包。")
        unknown_ids = set(removed_package_ids) - origin_ids
        if unknown_ids:
            raise ValueError(f"当前草稿没有安装包记录 {', '.join(sorted(unknown_ids))}。")

    if package_paths:
        package_group = analyze_package_group(list(package_paths))
        if package_group.pkg_name != pkg_name or package_group.package_family != group["package_family"]:
            raise ValueError("安装包的包名或类型与选中的版本记录不一致。")
        local_group = _group_to_json(
            package_group, login_context=None, capability_cache=capability_cache,
            asset_dir=asset_dir, preferences=PreferenceStore().load(),
        )
        group = _append_imported_packages(local_group, group)

    if removed_package_ids:
        group["removed_draft_package_ids"] = list(removed_package_ids)
        group["packages"] = [package for package in group["packages"] if package.get("origin_id") not in removed_package_ids]
        retained_paths = {package["path"] for package in group["packages"]}
        group["targets"] = [target for target in group["targets"] if target["package_path"] in retained_paths]
    if not group["packages"]:
        raise ValueError("版本记录需要至少一个安装包。")
    if asset_dir is not None:
        group["asset_dir"] = str(asset_dir)
    return group, str(basic.get("status") or "").strip()


def _merge_auto_matched_online_group(base_group: dict, online_group: dict) -> dict:
    merged = dict(base_group)
    always_copy_keys = (
        "app_name_zh",
        "website",
        "developer_name",
        "short_desc_zh",
        "full_desc_zh",
        "note_zh",
        "app_name_en",
        "short_desc_en",
        "full_desc_en",
        "note_en",
        "manual_en_edited",
        "metadata_edited",
        "category_id",
        "region_codes",
        "replace_assets",
        "existing_matches",
        "selected_match_app_id",
        "submission_mode",
        "draft_detail_id",
        "cpu_clip_options",
        "cpu_clip_codes",
        "motherboard_options",
        "motherboard_codes",
    )
    for key in always_copy_keys:
        if key in online_group:
            merged[key] = online_group[key]

    for key in ("display_name", "short_description", "full_description", "homepage"):
        value = str(online_group.get(key, "") or "").strip()
        if value:
            merged[key] = value

    online_icon_path = str(online_group.get("icon_path", "") or "").strip()
    if online_icon_path:
        merged["icon_path"] = online_icon_path
    packages = _packages_with_online_remainder(base_group, online_group)
    if packages:
        merged["packages"] = packages
        merged["pkg_arches"] = _dedupe_json_strings([package.get("arch") for package in packages])

    online_screenshots = [str(path).strip() for path in online_group.get("screenshot_paths", []) if str(path).strip()]
    if online_screenshots:
        merged["screenshot_paths"] = online_screenshots

    merged["targets"] = _targets_for_auto_matched_packages(base_group, online_group, packages)
    merged["online_packages"] = list(online_group.get("packages", []) or [])
    merged["online_selected_package_path"] = str(online_group.get("selected_package_path", "") or "")
    merged["auto_matched_online_app"] = True

    base_warnings = list(base_group.get("asset_warnings", []) or [])
    online_warnings = list(online_group.get("asset_warnings", []) or [])
    merged["asset_warnings"] = _dedupe_json_strings(base_warnings + online_warnings)
    return merged


def _group_with_auto_matched_online_defaults(
    group: dict,
    *,
    matches: tuple[StoreAppMatch, ...],
    login_context,
    capability_cache,
    preferred_app_id: str = "",
) -> dict:
    if login_context is None or not matches:
        return group
    target_family = str(group.get("package_family", "") or "").strip()
    online_groups: list[dict] = []
    match_warnings: list[str] = []

    for match in matches:
        if preferred_app_id and match.app_id != preferred_app_id:
            continue
        if not match.detail_id:
            continue
        if target_family and getattr(match, "package_family", "") and match.package_family != target_family:
            continue
        try:
            detail = fetch_existing_app_detail(login_context.client, match)
            detail_family = package_family_from_app_detail(detail)
            if target_family and detail_family and detail_family != target_family:
                continue
            detail_format = package_format_from_app_detail(detail, detail_family)
            effective_match = StoreAppMatch(
                app_id=match.app_id,
                detail_id=match.detail_id,
                pkg_name=match.pkg_name,
                app_name=match.app_name,
                package_family=detail_family or getattr(match, "package_family", ""),
                package_format=detail_format or getattr(match, "package_format", ""),
            )
            defaults = build_existing_detail_editor_defaults(
                detail,
                fallback_name=str(group.get("display_name") or effective_match.app_name or effective_match.pkg_name),
            )
            defaults.update(_sync_existing_detail_assets(effective_match, detail, session=getattr(login_context.client, "session", None)))
            online_group = _online_group_from_detail(effective_match, detail, defaults, capability_cache)
            if target_family and str(online_group.get("package_family", "") or "").strip() != target_family:
                continue
            online_groups.append(online_group)
        except Exception as exc:
            match_warnings.append(f"{match.app_name or match.pkg_name or match.app_id}: {exc}")

    if match_warnings:
        warnings = list(group.get("asset_warnings", []) or [])
        warnings.extend(f"自动匹配线上应用失败：{warning}" for warning in match_warnings)
        group = dict(group)
        group["asset_warnings"] = _dedupe_json_strings(warnings)

    if len(online_groups) != 1:
        return group
    online_group = online_groups[0]
    return _merge_auto_matched_online_group(group, online_group)


def _group_to_json(
    package_group, *, login_context, capability_cache, asset_dir: Path | None,
    preferences: UIPreferences | None = None, preferred_app_id: str = "",
) -> dict:
    icon_path, screenshot_paths = detect_asset_candidates(package_group, asset_dir=asset_dir)
    asset_warnings: list[str] = []
    package_icon_paths: dict[str, Path] = {}
    if icon_path is None:
        try:
            icon_path = _extract_initial_icon(package_group)
        except Exception as exc:
            asset_warnings.append(f"图标提取失败：{exc}")
    for package in package_group.packages:
        try:
            package_icon = _extract_package_icon(package_group, package)
        except Exception as exc:
            asset_warnings.append(f"{package.path.name} 图标提取失败：{exc}")
            continue
        if package_icon is not None:
            package_icon_paths[str(package.path)] = package_icon
    if icon_path is None and package_icon_paths:
        icon_path = next(iter(package_icon_paths.values()))
    matches = ()
    if login_context is not None:
        try:
            matches = find_existing_apps(login_context.client, pkg_name=package_group.pkg_name)
        except Exception:
            matches = ()
    targets = ()
    if capability_cache is not None:
        try:
            targets = build_target_options(
                capability_cache,
                package_group=package_group,
                preferences=preferences,
            )
        except Exception:
            targets = ()
    arch_codes = package_group_store_arch_codes(package_group)
    cpu_clip_options = build_cpu_clip_options(capability_cache, package_group)
    motherboard_options = build_motherboard_options(capability_cache)
    cpu_clip_codes = _selected_codes_from_options(cpu_clip_options)
    motherboard_codes = _selected_codes_from_options(motherboard_options)

    group = {
        "key": "|".join(
            (
                package_group.pkg_name,
                package_group.pkg_version,
                package_group.package_family,
                package_group.package_format,
            )
        ),
        "pkg_name": package_group.pkg_name,
        "pkg_version": package_group.pkg_version,
        "package_family": package_group.package_family,
        "package_format": package_group.package_format,
        "pkg_arches": list(package_group.pkg_arches),
        "display_name": package_group.display_name,
        "short_description": package_group.short_description,
        "full_description": package_group.full_description,
        "homepage": package_group.homepage,
        "packages": [
            {
                "path": str(package.path),
                "file_name": package.path.name,
                "arch": package.pkg_arch,
                "family": package.package_family,
                "format": package.package_format,
                "icon_path": str(package_icon_paths.get(str(package.path), icon_path or "")),
            }
            for package in package_group.packages
        ],
        "icon_path": str(icon_path) if icon_path is not None else "",
        "screenshot_paths": [str(path) for path in screenshot_paths],
        "asset_warnings": asset_warnings,
        "existing_matches": [_match_to_json(match) for match in matches],
        "targets": [_target_to_json(option) for option in targets],
        "adapt_arch_codes": list(arch_codes),
        "adapt_arch_label": adapt_arch_label(capability_cache, package_group) if capability_cache is not None else ", ".join(arch_codes),
        "cpu_clip_options": list(cpu_clip_options),
        "cpu_clip_codes": list(cpu_clip_codes),
        "motherboard_options": list(motherboard_options),
        "motherboard_codes": list(motherboard_codes),
    }
    return _group_with_auto_matched_online_defaults(
        group,
        matches=tuple(matches),
        login_context=login_context,
        capability_cache=capability_cache,
        preferred_app_id=preferred_app_id,
    )


def _submission_row_to_json(row: dict) -> dict:
    return {
        "row_id": int(row.get("row_id", 0)),
        "app_key": str(row.get("app_key", "")).strip(),
        "deb_path": str(row.get("deb_path", "")).strip(),
        "status": str(row.get("status", "")).strip(),
        "message": str(row.get("message", "")).strip(),
        "app_id": str(row.get("app_id", "")).strip(),
        "pkg_name": str(row.get("pkg_name", "")).strip(),
        "pkg_version": str(row.get("pkg_version", "")).strip(),
        "selector": str(row.get("selector", "")).strip(),
    }


def _submission_result_to_json(result: SubmissionResult) -> dict:
    rows = [_submission_row_to_json(row) for row in result.rows]
    status_counts: dict[str, int] = {}
    for row in rows:
        status = row["status"] or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
    failure_count = sum(count for status, count in status_counts.items() if status != "submitted")
    return {
        "output_dir": str(result.output_dir),
        "report_path": str(result.report_path),
        "rows": rows,
        "status_counts": status_counts,
        "success_count": status_counts.get("submitted", 0),
        "failure_count": failure_count,
    }


def _store_app_row_to_json(row: dict) -> dict:
    detail_id = str(row.get("id", "") or row.get("detail_id", "") or "").strip()
    return {
        "id": detail_id,
        "detail_id": detail_id,
        "app_id": str(row.get("app_id", "") or "").strip(),
        "app_name": str(row.get("app_name", "") or row.get("name", "") or "").strip(),
        "pkg_name": str(row.get("pkg_name", "") or "").strip(),
        "pkg_version": str(row.get("pkg_version", "") or "").strip(),
        "pkg_mode": str(row.get("pkgMode", "") or row.get("pkg_mode", "") or "").strip(),
        "status": str(row.get("status", "") or "").strip(),
        "is_draft": str(row.get("status", "")).strip() == DRAFT_STATUS,
        "status_str": str(row.get("statusStr", "") or "").strip(),
        "submit_time": str(row.get("submit_time", "") or "").strip(),
        "website": str(row.get("website", "") or "").strip(),
        "system_platform": str(row.get("systemPlatform", "") or row.get("system_platform", "") or "").strip(),
        "category_name": str(row.get("category_name", "") or "").strip(),
        "region": str(row.get("regionStr", "") or row.get("region", "") or "").strip(),
    }


def _history_row_to_json(row: dict) -> dict:
    record = _store_app_row_to_json(row)
    versions = _dedupe([version.strip() for version in record["pkg_version"].split(",") if version.strip()])
    record["pkg_version"] = ", ".join(versions)
    return record


def _audit_status_to_json(payload: dict) -> dict:
    rows = payload.get("datas", [])
    if not isinstance(rows, list):
        rows = []
    labels = {
        10001: "包格式检测",
        10002: "安全检测",
        10003: "包签名",
        10004: "人工审核",
        10005: "审核通过",
    }
    status_labels = {
        None: "未开始",
        0: "等待中",
        1: "通过",
        2: "未通过",
    }
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        check_item = row.get("check_item")
        status = row.get("status")
        items.append(
            {
                "check_item": str(check_item or ""),
                "title": labels.get(check_item, str(check_item or "")),
                "status": "" if status is None else str(status),
                "status_label": status_labels.get(status, str(status)),
                "desc": str(row.get("desc", "") or "").strip(),
                "finish_time": str(row.get("finish_time", "") or "").strip(),
            }
        )
    return {
        "submit_time": str(payload.get("submit_time", "") or "").strip(),
        "items": items,
    }


def _preferences_to_json(preferences: UIPreferences) -> dict:
    return {
        "recent_category_ids": list(preferences.recent_category_ids),
        "recent_regions": list(preferences.recent_regions),
        "last_output_dir": preferences.last_output_dir,
        "last_asset_dir": preferences.last_asset_dir,
        "last_release_key": preferences.last_release_key,
        "last_pkg_channel": preferences.last_pkg_channel,
        "last_session_account": preferences.last_session_account,
        "remember_credentials": preferences.remember_credentials,
        "remembered_username": preferences.remembered_username,
        "remembered_password": preferences.remembered_password,
        "mcp_enabled": preferences.mcp_enabled,
        "mcp_allow_actions": preferences.mcp_allow_actions,
        "mcp_output_dir": preferences.mcp_output_dir,
        "mcp_http_enabled": preferences.mcp_http_enabled,
        "mcp_http_port": preferences.mcp_http_port,
        "mcp_http_token": preferences.mcp_http_token,
        "mcp_sse_enabled": preferences.mcp_sse_enabled,
        "mcp_sse_port": preferences.mcp_sse_port,
        "mcp_sse_token": preferences.mcp_sse_token,
        "default_target_system_lines": {
            str(key): list(values)
            for key, values in preferences.default_target_system_lines.items()
        },
        "default_target_ranges": {
            scope: {
                code: {
                    "baseline_id": target.baseline_id,
                    "unsupported_baseline_ids": list(target.unsupported_baseline_ids),
                }
                for code, target in targets.items()
            }
            for scope, targets in preferences.default_target_ranges.items()
        },
    }


def _normalize_default_target_system_lines(raw) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for raw_key, raw_values in raw.items():
        key = str(raw_key).strip().lower()
        if not key or not isinstance(raw_values, list):
            continue
        codes: list[str] = []
        for value in raw_values:
            code = str(value).strip()
            if code and code not in codes:
                codes.append(code)
        if codes:
            result[key] = tuple(codes)
    return result


def _payload_package_path_identity(path_value: str) -> str:
    normalized = str(path_value or "").strip()
    if not normalized:
        return ""
    if normalized.startswith("online://"):
        return f"online/{normalized.removeprefix('online://')}"
    return str(Path(normalized).expanduser().resolve())


def _coerce_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip()
    if not text:
        return 0
    try:
        return max(0, int(float(text)))
    except ValueError:
        return 0


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _online_payload_pkg_arch(package_payload: dict) -> str:
    raw_label = str(package_payload.get("arch", "") or "").strip().lower()
    raw_code = str(package_payload.get("arch_code", "") or package_payload.get("pkg_arch", "") or "").strip()
    return normalize_package_arch(raw_code or raw_label) or "amd64"


def _online_package_group_from_payload(group_payload: dict) -> PackageGroup | None:
    packages: list[PackageMetadata] = []
    display_name = str(group_payload.get("display_name", "") or group_payload.get("app_name_zh", "")).strip()
    short_description = str(group_payload.get("short_description", "") or group_payload.get("short_desc_zh", "")).strip()
    full_description = str(group_payload.get("full_description", "") or group_payload.get("full_desc_zh", "")).strip()
    homepage = str(group_payload.get("homepage", "") or group_payload.get("website", "")).strip()
    group_family = str(group_payload.get("package_family", "") or "deb").strip() or "deb"
    group_format = str(group_payload.get("package_format", "") or ("uab" if group_family == "linglong" else "deb")).strip()
    for index, package_payload in enumerate(group_payload.get("packages", [])):
        if not isinstance(package_payload, dict):
            continue
        path_value = str(package_payload.get("path", "")).strip()
        if not _is_online_package(package_payload):
            continue
        package_path = _payload_package_path_identity(path_value or f"online://{index}")
        pkg_name = str(package_payload.get("pkg_name", "") or group_payload.get("pkg_name", "")).strip()
        pkg_version = str(package_payload.get("version", "") or package_payload.get("pkg_version", "") or group_payload.get("pkg_version", "")).strip()
        pkg_arch = _online_payload_pkg_arch(package_payload)
        package_family = str(package_payload.get("family", "") or group_family).strip() or group_family
        package_format = str(package_payload.get("format", "") or group_format).strip() or group_format
        packages.append(
            PackageMetadata(
                path=Path(package_path),
                package_family=package_family,
                package_format=package_format,
                pkg_name=pkg_name or display_name or "online-app",
                pkg_version=pkg_version or "0",
                pkg_arch=pkg_arch or "amd64",
                pkg_size=_coerce_int(package_payload.get("pkg_size", package_payload.get("size", 0))),
                sha256=str(package_payload.get("sha256", "") or "").strip(),
                display_name=display_name or pkg_name,
                short_description=short_description,
                full_description=full_description,
                homepage=homepage,
            )
        )
    if not packages:
        return None
    return PackageGroup(packages=tuple(packages))


def _group_payload_to_package_group(group_payload: dict):
    package_paths = [path for path in _local_package_paths(group_payload) if path.exists()]
    if package_paths:
        local_group = analyze_package_group(package_paths)
        online_group = _online_package_group_from_payload(group_payload)
        if online_group is None:
            return local_group
        local_arch_keys = {
            _normalized_arch_key(package.pkg_arch)
            for package in local_group.packages
            if _normalized_arch_key(package.pkg_arch)
        }
        online_remainder = tuple(
            package
            for package in online_group.packages
            if _normalized_arch_key(package.pkg_arch) not in local_arch_keys
        )
        return PackageGroup(packages=local_group.packages + online_remainder)
    online_group = _online_package_group_from_payload(group_payload)
    if online_group is not None:
        return online_group
    raise ValueError("需要先选择已上架应用，或拖入可读取的本地 .deb / linglong 包")


def _group_payload_to_targets(group_payload: dict) -> tuple[SystemTargetOption, ...]:
    result: list[SystemTargetOption] = []
    for target_payload in group_payload.get("targets", []):
        if not isinstance(target_payload, dict):
            continue
        baseline_options = []
        for baseline_payload in target_payload.get("baseline_options", []):
            if not isinstance(baseline_payload, dict):
                continue
            baseline_id = str(baseline_payload.get("id", "")).strip()
            version = str(baseline_payload.get("version", "")).strip()
            if baseline_id:
                baseline_options.append((baseline_id, version))
        selected_baseline_ids = tuple(
            str(value).strip()
            for value in target_payload.get("selected_baseline_ids", [])
            if str(value).strip()
        )
        unsupported_baseline_ids = tuple(
            str(value).strip()
            for value in target_payload.get("unsupported_baseline_ids", [])
            if str(value).strip()
        )
        raw_package_path = str(target_payload.get("package_path", "")).strip()
        result.append(
            SystemTargetOption(
                package_path=_payload_package_path_identity(raw_package_path),
                package_label=str(target_payload.get("package_label", "")).strip(),
                package_arch=str(target_payload.get("package_arch", "")).strip(),
                code=str(target_payload.get("code", "")).strip(),
                label=str(target_payload.get("label", "")).strip(),
                package_family=str(target_payload.get("package_family", "")).strip(),
                baseline_options=tuple(baseline_options),
                selected=bool(target_payload.get("selected", False)),
                baseline_id=str(target_payload.get("baseline_id", "")).strip(),
                selected_baseline_ids=selected_baseline_ids,
                unsupported_baseline_ids=unsupported_baseline_ids,
            )
        )
    return tuple(result)


def _group_payload_to_selected_match(group_payload: dict) -> StoreAppMatch | None:
    selected_id = str(group_payload.get("selected_match_app_id", "")).strip()
    matches = group_payload.get("existing_matches", [])
    if not selected_id and len(matches) == 1 and isinstance(matches[0], dict):
        selected_id = str(matches[0].get("app_id", "")).strip()
    if not selected_id:
        return None
    for match_payload in matches:
        if not isinstance(match_payload, dict):
            continue
        if str(match_payload.get("app_id", "")).strip() != selected_id:
            continue
        return StoreAppMatch(
            app_id=selected_id,
            detail_id=str(match_payload.get("detail_id", "")).strip(),
            pkg_name=str(match_payload.get("pkg_name", "")).strip(),
            app_name=str(match_payload.get("app_name", "")).strip(),
            package_family=str(match_payload.get("package_family", "")).strip(),
            package_format=str(match_payload.get("package_format", "")).strip(),
        )
    return None


def _group_payload_selected_codes(group_payload: dict, code_key: str, option_key: str) -> tuple[str, ...] | None:
    explicit_value = group_payload.get(code_key)
    if isinstance(explicit_value, list):
        return tuple(str(value).strip() for value in explicit_value if str(value).strip())
    options_value = group_payload.get(option_key)
    if isinstance(options_value, list):
        codes: list[str] = []
        for option in options_value:
            if not isinstance(option, dict) or not bool(option.get("selected", False)):
                continue
            code = str(option.get("code", "")).strip()
            if code:
                codes.append(code)
        return tuple(codes)
    return None


def _group_asset_paths(group_payload: dict) -> tuple[Path | None, tuple[Path, ...] | None]:
    icon = str(group_payload.get("icon_path", "")).strip()
    screenshots = group_payload.get("screenshot_paths")
    return (
        Path(icon).expanduser().resolve() if icon else None,
        tuple(Path(path).expanduser().resolve() for path in screenshots if isinstance(path, str) and path.strip())
        if isinstance(screenshots, list)
        else None,
    )


def _group_payload_to_plan(
    group_payload: dict, *, save_draft: bool = False,
) -> tuple[BatchGroupSubmissionPlan, tuple[SystemTargetOption, ...]]:
    package_group = _group_payload_to_package_group(group_payload)
    asset_dir_value = str(group_payload.get("asset_dir", "")).strip()
    asset_dir = Path(asset_dir_value).expanduser().resolve() if asset_dir_value else None
    region_codes = tuple(
        str(value).strip()
        for value in group_payload.get("region_codes", [])
        if str(value).strip()
    ) or ("1",)
    icon_path, screenshot_paths = _group_asset_paths(group_payload)
    assets_preprocessed = group_payload.get("assets_preprocessed") is True
    plan = BatchGroupSubmissionPlan(
        package_group=package_group,
        submission_mode=str(group_payload.get("submission_mode", "auto")).strip() or "auto",
        selected_match=_group_payload_to_selected_match(group_payload),
        app_name_zh=str(group_payload.get("app_name_zh", "")).strip(),
        website=str(group_payload.get("website", "")).strip(),
        short_desc_zh=str(group_payload.get("short_desc_zh", "")).strip(),
        full_desc_zh=str(group_payload.get("full_desc_zh", "")).strip(),
        category_id=str(group_payload.get("category_id", "")).strip(),
        region_codes=region_codes,
        asset_dir=asset_dir,
        replace_assets=bool(group_payload.get("replace_assets", False)),
        note_zh=str(group_payload.get("note_zh", "")).strip(),
        developer_name=str(group_payload.get("developer_name", "")).strip(),
        app_name_en=str(group_payload.get("app_name_en", "")).strip(),
        short_desc_en=str(group_payload.get("short_desc_en", "")).strip(),
        full_desc_en=str(group_payload.get("full_desc_en", "")).strip(),
        note_en=str(group_payload.get("note_en", "")).strip(),
        auto_translate_en=not bool(group_payload.get("manual_en_edited", False)),
        manual_icon_path=icon_path,
        manual_screenshot_paths=screenshot_paths,
        prepared_icon_path=icon_path if assets_preprocessed else None,
        prepared_screenshot_paths=(screenshot_paths or ()) if assets_preprocessed else (),
        asset_warnings=tuple(str(value).strip() for value in group_payload.get("asset_warnings", []) if str(value).strip())
        if isinstance(group_payload.get("asset_warnings", []), list)
        else (),
        metadata_edited=bool(group_payload.get("metadata_edited", False)),
        manual_en_edited=bool(group_payload.get("manual_en_edited", False)),
        save_draft=save_draft,
        use_package_update_notes=True,
        expected_draft_id=str(group_payload.get("draft_detail_id") or "").strip(),
        removed_draft_package_ids=_dedupe(_code_items(group_payload.get("removed_draft_package_ids"))),
        cpu_clip_codes=_group_payload_selected_codes(
            group_payload,
            "cpu_clip_codes",
            "cpu_clip_options",
        ),
        motherboard_codes=_group_payload_selected_codes(
            group_payload,
            "motherboard_codes",
            "motherboard_options",
        ),
    )
    return plan, _group_payload_to_targets(group_payload)


def _default_submission_output_dir() -> Path:
    return DEFAULT_OUTPUT_ROOT / "cpp" / datetime.now().strftime("submit-%Y%m%d-%H%M%S")


def _safe_group_key(*parts: str) -> str:
    group_key = "|".join(part.strip() for part in parts if part.strip()) or "app"
    return (
        group_key.replace("|", "__")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
    )


def _extract_initial_icon(package_group) -> Path | None:
    output_dir = DEFAULT_OUTPUT_ROOT / "cpp" / "assets" / _safe_group_key(
        package_group.pkg_name,
        package_group.pkg_version,
        package_group.package_family,
        package_group.package_format,
    ) / "detected" / "extracted"
    first_package = package_group.packages[0]
    if first_package.package_family == "deb":
        return extract_deb_icon(
            first_package.path,
            pkg_name=package_group.pkg_name,
            output_dir=output_dir,
        )
    return extract_archive_icon(
        first_package.path,
        pkg_name=package_group.pkg_name,
        output_dir=output_dir,
    )


def _extract_package_icon(package_group, package) -> Path | None:
    output_dir = DEFAULT_OUTPUT_ROOT / "cpp" / "assets" / _safe_group_key(
        package_group.pkg_name,
        package_group.pkg_version,
        package_group.package_family,
        package_group.package_format,
        package.path.stem,
    ) / "detected" / "extracted"
    if package.package_family == "deb":
        return extract_deb_icon(
            package.path,
            pkg_name=package_group.pkg_name,
            output_dir=output_dir,
        )
    return extract_archive_icon(
        package.path,
        pkg_name=package_group.pkg_name,
        output_dir=output_dir,
    )


def _group_asset_output_dir(group_payload: dict, stage: str) -> Path:
    pkg_name = str(group_payload.get("pkg_name", "")).strip() or "app"
    pkg_version = str(group_payload.get("pkg_version", "")).strip() or "unknown"
    group_key = str(group_payload.get("key", "")).strip() or f"{pkg_name}|{pkg_version}"
    return DEFAULT_OUTPUT_ROOT / "cpp" / "assets" / _safe_group_key(group_key) / stage


def _update_group_assets(
    group_payload: dict,
    *,
    asset_dir: Path,
    icon_path: Path | None,
    screenshot_paths: tuple[Path, ...],
    warnings: list[str],
    assets_preprocessed: bool = False,
) -> dict:
    updated = dict(group_payload)
    updated["asset_dir"] = str(asset_dir)
    updated["icon_path"] = str(icon_path) if icon_path is not None else ""
    updated["screenshot_paths"] = [str(path) for path in screenshot_paths]
    updated["assets_preprocessed"] = assets_preprocessed
    updated["asset_warnings"] = warnings
    return updated


def _load_categories(login_context) -> list[dict]:
    if login_context is None:
        return []
    try:
        return [_category_to_json(option) for option in fetch_category_options(login_context.client)]
    except Exception:
        return []


def _load_capabilities(login_context):
    client = login_context.client if login_context is not None else None
    try:
        return load_or_sync_capabilities(client)
    except Exception:
        return None


def command_bootstrap(payload: dict) -> dict:
    preferences = PreferenceStore().load()
    preferred_account = str(payload.get("preferred_account", "")).strip() or preferences.last_session_account.strip()
    login_context = try_restore_cached_login(preferred_account)
    capability_cache = _load_capabilities(login_context)
    return {
        "login": _login_to_json(login_context),
        "capabilities": _capability_cache_to_json(capability_cache),
        "categories": _load_categories(login_context),
        "preferences": _preferences_to_json(preferences),
    }


def command_login_credentials(payload: dict) -> dict:
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    remember_credentials = _coerce_bool(payload.get("remember_credentials", False))
    if not username or not password:
        raise ValueError("username and password are required")
    login_context = login_with_credentials(username, password)
    capability_cache = _load_capabilities(login_context)
    preference_store = PreferenceStore()
    current_preferences = preference_store.load()
    preferences = replace(
        current_preferences,
        last_session_account=username,
        remember_credentials=remember_credentials,
        remembered_username=username if remember_credentials else "",
        remembered_password=password if remember_credentials else "",
    )
    preference_store.save(preferences)
    return {
        "login": _login_to_json(login_context),
        "capabilities": _capability_cache_to_json(capability_cache),
        "categories": _load_categories(login_context),
        "preferences": _preferences_to_json(preferences),
    }


def command_login_wechat_qr_stream(payload: dict) -> dict:
    from ui.wechat_qr_backend import run_wechat_qr_login

    account_label = str(payload.get("account_label", "")).strip() or "manual-login"

    def emit_event(event: dict) -> None:
        print(json.dumps(event, ensure_ascii=False), flush=True)

    state = run_wechat_qr_login(account_label, event_callback=emit_event)
    login_context = login_with_browser_state(state, login_mode="wechat")
    capability_cache = _load_capabilities(login_context)
    return {
        "canceled": False,
        "login": _login_to_json(login_context),
        "capabilities": _capability_cache_to_json(capability_cache),
        "categories": _load_categories(login_context),
    }


def command_login_wechat_qr(payload: dict) -> dict:
    return command_login_wechat_qr_stream(payload)


def command_logout(payload: dict) -> dict:
    preferred_account = str(payload.get("preferred_account", "")).strip()
    store = SessionStateStore(DEFAULT_SESSION_CACHE_DIR)
    logs: list[str] = []
    if preferred_account:
        store.invalidate(preferred_account)
        logs.append(f"已清理登录缓存：{preferred_account}")
    else:
        for account in store.list_accounts():
            store.invalidate(account)
        logs.append("已清理全部登录缓存。")
    return {
        "login": _login_to_json(None),
        "capabilities": _capability_cache_to_json(_load_capabilities(None)),
        "categories": [],
        "logs": logs,
    }


def _refresh_group_system_lines(group: dict, cache, preferences: UIPreferences) -> list[dict]:
    targets = [row for row in group.get("targets", []) if isinstance(row, dict)]
    refreshed: list[dict] = []
    for package in group.get("packages", []):
        if not isinstance(package, dict):
            continue
        path = str(package.get("path", "")).strip()
        arch = str(package.get("arch", "")).strip()
        family = str(package.get("family") or group.get("package_family") or "").strip()
        previous = {row["code"]: row for row in targets if row.get("package_path") == path and row.get("code")}
        selected = tuple(code for code, row in previous.items() if row.get("selected"))
        defaults = {}
        if not previous and not package.get("online"):
            defaults = default_target_ranges(cache, family, arch, preferences)
            selected = tuple(defaults)
        rows = _target_rows_for_selection(
            cache, package_family=family, package_path=path, package_label=str(package.get("file_name", "")),
            arch_label=arch, arch_code=str(package.get("arch_code", "")), selected_system_codes=selected,
            selected_baselines=(), selected_unsupported_baselines=(),
        )
        for row in rows:
            old = previous.get(row["code"], {})
            for key in ("baseline_id", "selected_baseline_ids", "unsupported_baseline_ids"):
                if key in old:
                    row[key] = old[key]
            default = defaults.get(row["code"])
            if default is not None:
                row["baseline_id"] = default.baseline_id
                row["selected_baseline_ids"] = [default.baseline_id] if default.baseline_id else []
                row["unsupported_baseline_ids"] = list(default.unsupported_baseline_ids)
        refreshed.extend(rows)
    return refreshed


def command_sync_store_data(payload: dict) -> dict:
    preferred_account = str(payload.get("preferred_account", "")).strip()
    logs: list[str] = []
    login_context = try_restore_cached_login(preferred_account, log=logs.append)
    if login_context is None:
        raise RuntimeError("未找到可用登录态，请先登录。")
    capability_cache = sync_capabilities(login_context.client, log=logs.append)
    categories = [_category_to_json(option) for option in fetch_category_options(login_context.client, log=logs.append)]
    result = {
        "login": _login_to_json(login_context),
        "capabilities": _capability_cache_to_json(capability_cache),
        "categories": categories,
        "logs": logs,
    }
    group = payload.get("group")
    if isinstance(group, dict) and group.get("key"):
        result["group_key"] = group["key"]
        result["targets"] = _refresh_group_system_lines(group, capability_cache, PreferenceStore().load())
    return result


def command_list_my_apps(payload: dict, *, login_context=None, include_reports: bool = True) -> dict:
    preferred_account = str(payload.get("preferred_account", "")).strip()
    page_num = int(payload.get("page_num", 1) or 1)
    page_size = int(payload.get("page_size", 50) or 50)
    keyword = str(payload.get("keyword", "")).strip()
    login_context = login_context or try_restore_cached_login(preferred_account)
    if login_context is None:
        raise RuntimeError("未找到可用登录态，请先登录。")
    filters = {"name_or_app_id": keyword} if keyword else {}
    paged = login_context.client.list_apps_paged(page_num=page_num, page_size=page_size, **filters)
    reports = {
        "apps": login_context.client.report_my_apps(),
        "downcounts": login_context.client.report_my_downcounts(),
        "comments": login_context.client.report_my_comments(),
    } if include_reports else {}
    return {
        "login": _login_to_json(login_context),
        "total": int(paged.get("total", 0) or 0),
        "rows": [_store_app_row_to_json(row) for row in paged.get("rows", [])],
        "reports": reports,
    }


def command_get_app_workflow(payload: dict, *, login_context=None) -> dict:
    preferred_account = str(payload.get("preferred_account", "")).strip()
    detail_id = str(payload.get("detail_id", "")).strip()
    app_id = str(payload.get("app_id", "")).strip()
    login_context = login_context or try_restore_cached_login(preferred_account)
    if login_context is None:
        raise RuntimeError("未找到可用登录态，请先登录。")
    if not detail_id and not app_id:
        raise ValueError("detail_id or app_id is required")
    page_num = int(payload.get("page_num", 1))
    page_size = int(payload.get("page_size", 20))
    if page_num < 1 or page_size < 1:
        raise ValueError("页码和每页记录数必须大于零。")
    audit = _audit_status_to_json(login_context.client.get_app_audit_status(detail_id)) if detail_id else {
        "submit_time": "",
        "items": [],
    }
    history = login_context.client.get_app_history(app_id, page_num=page_num, page_size=page_size) if app_id else {
        "total": 0,
        "rows": [],
    }
    return {
        "login": _login_to_json(login_context),
        "audit": audit,
        "history": {
            "page_num": page_num,
            "page_size": page_size,
            "total": int(history.get("total", 0) or 0),
            "rows": [_history_row_to_json(row) for row in history.get("rows", []) if isinstance(row, dict)],
        },
    }


def command_get_app_history_notes(payload: dict, *, login_context=None) -> dict:
    detail_id = str(payload.get("detail_id", "") or "").strip()
    if not detail_id:
        raise ValueError("请选择一条更新记录。")
    login_context = login_context or try_restore_cached_login(str(payload.get("preferred_account", "")).strip())
    if login_context is None:
        raise RuntimeError("未找到可用登录态，请先登录。")
    detail = extract_detail_data(login_context.client.get_app_detail(detail_id))
    package_notes = []
    general_notes = []
    for entry in detail.get("app_lan_infos") or ():
        if not isinstance(entry, dict):
            continue
        lan = str(entry.get("lan") or "").strip()
        language = LANGUAGE_LABELS.get(lan, lan)
        for note in entry.get("pkgUptDesc") or ():
            if not isinstance(note, dict):
                continue
            text = note.get("uptDesc")
            if isinstance(text, str) and text.strip():
                packages = _dedupe([
                    item.strip() for item in str(note.get("pkgArchVersion") or "").split(",") if item.strip()
                ])
                package_notes.append({"language": language, "scope": "package", "packages": packages, "text": text})
        text = entry.get("update_desc")
        if isinstance(text, str) and text.strip():
            general_notes.append({"language": language, "scope": "general", "text": text})
    return {"notes": package_notes + general_notes}


def command_load_preferences(payload: dict) -> dict:
    preferences = PreferenceStore().load()
    return {
        "preferences": _preferences_to_json(preferences),
    }


def command_save_preferences(payload: dict) -> dict:
    preferences_payload = payload.get("preferences", {})
    if not isinstance(preferences_payload, dict):
        raise ValueError("preferences must be an object")
    preference_store = PreferenceStore()
    preferences_payload = {**_preferences_to_json(preference_store.load()), **preferences_payload}
    mcp_http_enabled = _coerce_bool(preferences_payload["mcp_http_enabled"])
    mcp_sse_enabled = _coerce_bool(preferences_payload["mcp_sse_enabled"])
    mcp_http_port = preferences_payload["mcp_http_port"]
    mcp_sse_port = preferences_payload["mcp_sse_port"]
    if any(type(port) is not int or not 1 <= port <= 65535 for port in (mcp_http_port, mcp_sse_port)):
        raise ValueError("MCP 端口必须在 1 到 65535 之间。")
    if mcp_http_enabled and mcp_sse_enabled and mcp_http_port == mcp_sse_port:
        raise ValueError("HTTP 和 SSE 需要不同端口。")
    preferences = UIPreferences(
        recent_category_ids=tuple(
            str(value).strip()
            for value in preferences_payload.get("recent_category_ids", [])
            if str(value).strip()
        ) or ("1",),
        recent_regions=tuple(
            str(value).strip()
            for value in preferences_payload.get("recent_regions", [])
            if str(value).strip()
        ) or ("1",),
        last_output_dir=str(preferences_payload.get("last_output_dir", "") or ""),
        last_asset_dir=str(preferences_payload.get("last_asset_dir", "") or ""),
        last_release_key=str(preferences_payload.get("last_release_key", "") or "stable"),
        last_pkg_channel=str(preferences_payload.get("last_pkg_channel", "") or "stable"),
        last_session_account=str(preferences_payload.get("last_session_account", "") or ""),
        remember_credentials=_coerce_bool(preferences_payload.get("remember_credentials", False)),
        remembered_username=str(preferences_payload.get("remembered_username", "") or ""),
        remembered_password=str(preferences_payload.get("remembered_password", "") or ""),
        default_target_system_lines=_normalize_default_target_system_lines(
            preferences_payload.get("default_target_system_lines", {})
        ),
        default_target_ranges=normalize_default_target_ranges(preferences_payload["default_target_ranges"]),
        mcp_enabled=_coerce_bool(preferences_payload["mcp_enabled"]),
        mcp_allow_actions=_coerce_bool(preferences_payload["mcp_allow_actions"]),
        mcp_output_dir=str(preferences_payload["mcp_output_dir"] or "").strip(),
        mcp_http_enabled=mcp_http_enabled,
        mcp_http_port=mcp_http_port,
        mcp_http_token=str(preferences_payload["mcp_http_token"] or "").strip(),
        mcp_sse_enabled=mcp_sse_enabled,
        mcp_sse_port=mcp_sse_port,
        mcp_sse_token=str(preferences_payload["mcp_sse_token"] or "").strip(),
    )
    path = preference_store.save(preferences)
    return {
        "path": str(path),
        "preferences": _preferences_to_json(preferences),
    }


def command_analyze(payload: dict) -> dict:
    package_paths = payload.get("package_paths", [])
    if not isinstance(package_paths, list) or not package_paths:
        raise ValueError("package_paths is required")
    asset_dir_value = str(payload.get("asset_dir", "")).strip()
    asset_dir = Path(asset_dir_value).expanduser().resolve() if asset_dir_value else None
    preferred_account = str(payload.get("preferred_account", "")).strip()
    preferred_app_id = str(payload.get("preferred_app_id", "")).strip()
    previous = payload.get("current_group")
    if not isinstance(previous, dict):
        previous = {}
    previous_paths = _local_package_paths(previous)
    incoming_paths = [Path(path).expanduser().resolve() for path in package_paths]
    package_group = analyze_package_group(list(dict.fromkeys(previous_paths + incoming_paths)))
    same_app = (
        previous.get("pkg_name") == package_group.pkg_name
        and previous.get("package_family") == package_group.package_family
    )
    if previous.get("draft_detail_id") and not same_app:
        raise ValueError("安装包的包名或类型与当前草稿不一致。")
    appended = same_app and bool(previous_paths or previous.get("online_only"))

    login_context = try_restore_cached_login(preferred_account)
    capability_cache = _load_capabilities(login_context)
    preferences = PreferenceStore().load()
    group = _group_to_json(
        package_group,
        login_context=None if appended else login_context,
        capability_cache=capability_cache,
        asset_dir=asset_dir,
        preferences=preferences,
        preferred_app_id=preferred_app_id,
    )
    if appended:
        group = _append_imported_packages(group, previous)
    return {
        "login": _login_to_json(login_context),
        "capabilities": _capability_cache_to_json(capability_cache),
        "categories": _load_categories(login_context),
        "project": {
            "group_count": 1,
            "package_count": len(group["packages"]),
            "mode": "single",
            "source_group_key": previous.get("key", ""),
            "appended": appended,
        },
        "groups": [group],
    }


def command_generate_english(payload: dict) -> dict:
    app_name_zh = str(payload.get("app_name_zh", "")).strip()
    short_desc_zh = str(payload.get("short_desc_zh", "")).strip()
    full_desc_zh = str(payload.get("full_desc_zh", "")).strip()
    note_zh = str(payload.get("note_zh", "")).strip()
    if not app_name_zh and not short_desc_zh and not full_desc_zh:
        raise ValueError("at least one Chinese listing field is required")
    translated = generate_english_listing_texts(
        app_name_zh=app_name_zh,
        short_desc_zh=short_desc_zh,
        full_desc_zh=full_desc_zh,
        note_zh=note_zh,
    )
    return translated


def command_preprocess_assets(payload: dict) -> dict:
    group_payload = payload.get("group")
    if not isinstance(group_payload, dict):
        raise ValueError("group is required")
    package_group = _group_payload_to_package_group(group_payload)
    asset_dir_value = str(group_payload.get("asset_dir", "")).strip()
    asset_dir = Path(asset_dir_value).expanduser().resolve() if asset_dir_value else None
    logs: list[str] = []
    output_dir = _group_asset_output_dir(group_payload, "preprocessed")
    manual_icon_path, manual_screenshot_paths = _group_asset_paths(group_payload)
    bundle = preprocess_submission_assets(
        package_group,
        asset_dir=asset_dir,
        manual_icon_path=manual_icon_path,
        manual_screenshot_paths=manual_screenshot_paths,
        output_dir=output_dir,
        log=logs.append,
    )
    return {
        "group": _update_group_assets(
            group_payload,
            asset_dir=output_dir,
            icon_path=bundle.icon_path,
            screenshot_paths=bundle.screenshot_paths,
            warnings=list(bundle.warnings),
            assets_preprocessed=True,
        ),
        "logs": logs,
    }


def command_capture_screenshots(payload: dict) -> dict:
    group_payload = payload.get("group")
    if not isinstance(group_payload, dict):
        raise ValueError("group is required")
    package_group = _group_payload_to_package_group(group_payload)
    logs: list[str] = []
    output_dir = _group_asset_output_dir(group_payload, "captured")
    screenshots = capture_screenshots_for_group(
        package_group,
        output_dir=output_dir,
        log=logs.append,
    )
    asset_dir = screenshots[0].parent.parent if screenshots else output_dir
    warnings = list(group_payload.get("asset_warnings", [])) if isinstance(group_payload.get("asset_warnings"), list) else []
    return {
        "group": _update_group_assets(
            group_payload,
            asset_dir=asset_dir,
            icon_path=Path(str(group_payload.get("icon_path", "")).strip()).expanduser().resolve()
            if str(group_payload.get("icon_path", "")).strip()
            else None,
            screenshot_paths=screenshots,
            warnings=logs or warnings,
        ),
        "logs": logs,
    }


def command_fetch_match_defaults(payload: dict) -> dict:
    preferred_account = str(payload.get("preferred_account", "")).strip()
    login_context = try_restore_cached_login(preferred_account)
    if login_context is None:
        raise RuntimeError("未找到可用登录态，请先登录。")
    capability_cache = _load_capabilities(login_context)

    match_payload = payload.get("match")
    if not isinstance(match_payload, dict):
        raise ValueError("match is required")
    match = StoreAppMatch(
        app_id=str(match_payload.get("app_id", "")).strip(),
        detail_id=str(match_payload.get("detail_id", "")).strip(),
        pkg_name=str(match_payload.get("pkg_name", "")).strip(),
        app_name=str(match_payload.get("app_name", "")).strip(),
    )
    if not match.detail_id:
        raise ValueError("selected match is missing detail_id")

    fallback_name = str(payload.get("fallback_name", "")).strip() or match.app_name or match.pkg_name
    detail = fetch_existing_app_detail(login_context.client, match)
    defaults = build_existing_detail_editor_defaults(detail, fallback_name=fallback_name)
    defaults.update(_sync_existing_detail_assets(match, detail, session=login_context.client.session))
    defaults["app_id"] = match.app_id
    defaults["detail_id"] = match.detail_id
    result = dict(defaults)
    result.update({
        "login": _login_to_json(login_context),
        "capabilities": _capability_cache_to_json(capability_cache),
        "categories": _load_categories(login_context),
        "defaults": defaults,
        "group": _online_group_from_detail(match, detail, defaults, capability_cache),
    })
    return result


def command_delete_draft(payload: dict, *, login_context=None) -> dict:
    detail_id = str(payload.get("detail_id") or "").strip()
    if not detail_id:
        raise ValueError("请选择要删除的草稿。")
    login_context = login_context or try_restore_cached_login(str(payload.get("preferred_account", "")).strip())
    if login_context is None:
        raise RuntimeError("未找到可用登录态，请先登录。")
    login_context.client.delete_draft(detail_id)
    return {"detail_id": detail_id}


def command_submit(payload: dict) -> dict:
    preferred_account = str(payload.get("preferred_account", "")).strip()
    logs: list[str] = []
    login_context = try_restore_cached_login(preferred_account, log=logs.append)
    if login_context is None:
        raise RuntimeError("未找到可用登录态，请先登录。")

    groups_payload = payload.get("groups")
    if not isinstance(groups_payload, list) or not groups_payload:
        raise ValueError("groups is required")

    selected_keys = {
        str(value).strip()
        for value in payload.get("selected_keys", [])
        if str(value).strip()
    }
    selected_groups: list[dict] = []
    for group_payload in groups_payload:
        if not isinstance(group_payload, dict):
            continue
        group_key = str(group_payload.get("key", "")).strip()
        if selected_keys and group_key not in selected_keys:
            continue
        selected_groups.append(group_payload)
    if not selected_groups:
        raise RuntimeError("没有可提交的应用。")

    capability_cache = load_or_sync_capabilities(login_context.client, log=logs.append)
    plans: list[BatchGroupSubmissionPlan] = []
    selected_targets: list[SystemTargetOption] = []
    save_draft = payload.get("save_draft") is True
    for group_payload in selected_groups:
        plan, targets = _group_payload_to_plan(group_payload, save_draft=save_draft)
        plans.append(plan)
        selected_targets.extend(targets)

    output_dir_value = str(payload.get("output_dir", "")).strip()
    output_dir = Path(output_dir_value).expanduser().resolve() if output_dir_value else _default_submission_output_dir()
    note = str(payload.get("note", "")).strip()
    release_key = str(payload.get("release_key", "")).strip() or "stable"
    pkg_channel = str(payload.get("pkg_channel", "")).strip() or "stable"
    submission = submit_applications_batch(
        login_context,
        plans=tuple(plans),
        cache=capability_cache,
        note=note,
        release_key=release_key,
        pkg_channel=pkg_channel,
        selected_targets=tuple(selected_targets),
        output_dir=output_dir,
        log=logs.append,
    )
    return {
        "login": _login_to_json(login_context),
        "capabilities": _capability_cache_to_json(capability_cache),
        "categories": _load_categories(login_context),
        "report": _submission_result_to_json(submission),
        "saved_draft": save_draft,
        "draft_detail_id": selected_groups[0].get("draft_detail_id", "") if len(selected_groups) == 1 else "",
        "submitted_group_count": len(plans),
        "logs": logs,
    }


COMMANDS = {
    "bootstrap": command_bootstrap,
    "list_my_apps": command_list_my_apps,
    "get_app_workflow": command_get_app_workflow,
    "get_app_history_notes": command_get_app_history_notes,
    "load_preferences": command_load_preferences,
    "save_preferences": command_save_preferences,
    "login_credentials": command_login_credentials,
    "login_wechat_qr": command_login_wechat_qr,
    "login_wechat_qr_stream": command_login_wechat_qr_stream,
    "logout": command_logout,
    "sync_store_data": command_sync_store_data,
    "analyze": command_analyze,
    "generate_english": command_generate_english,
    "preprocess_assets": command_preprocess_assets,
    "capture_screenshots": command_capture_screenshots,
    "fetch_match_defaults": command_fetch_match_defaults,
    "delete_draft": command_delete_draft,
    "submit": command_submit,
}


def main() -> int:
    command = sys.argv[1].strip() if len(sys.argv) > 1 else "mcp"
    if command == "mcp":
        from appstore.mcp_server import serve_from_preferences

        return serve_from_preferences(sys.argv[2:])
    if command == "mcp_job":
        from appstore.mcp_job_runner import main as run_mcp_job

        return run_mcp_job(sys.argv[2:])
    handler = COMMANDS.get(command)
    if handler is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"unsupported command: {command}",
                },
                ensure_ascii=False,
            )
        )
        return 1

    payload = _read_payload()
    try:
        data = handler(payload)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc) or exc.__class__.__name__,
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
            )
        )
        return 1

    print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
