from __future__ import annotations

from typing import Any

from appstore.models import UploadedFileRef
from appstore.translation import LANGUAGE_LABELS


_PRESERVED_ORIGIN_PKG_KEYS = (
    "id",
    "app_id",
    "appId",
    "app_info_id",
    "appInfoId",
    "app_origin_pkg_id",
    "appOriginPkgId",
    "origin_pkg_id",
    "originPkgId",
    "package_id",
    "packageId",
    "pkg_id",
    "pkgId",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip()


def _csv_tokens(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).split(",")
    tokens: list[str] = []
    for item in items:
        normalized = _text(item)
        if normalized:
            tokens.append(normalized)
    return tuple(tokens)


def _id_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        values = value
    else:
        values = str(value).split(",")

    items: list[str] = []
    for item in values:
        if isinstance(item, dict):
            normalized = (
                _text(item.get("id"))
                or _text(item.get("baseline_id"))
                or _text(item.get("baselineId"))
                or _text(item.get("code"))
            )
        else:
            normalized = _text(item)
        if normalized and normalized not in items:
            items.append(normalized)
    return items


def baseline_id_objects(values: list[str] | tuple[str, ...]) -> list[dict[str, str]]:
    return [{"id": value} for value in _id_items(list(values))]


def _coerce_code(value: Any) -> str | int:
    normalized = _text(value)
    if normalized.isdigit():
        return int(normalized)
    return normalized


def baseline_ids_from_value(value: Any) -> list[str]:
    return _id_items(value)


def baseline_system_id_objects(system_codes: list[str] | tuple[str, ...], values: list[str] | tuple[str, ...]) -> list[dict]:
    entries: list[dict] = []
    for system_code in _code_items(list(system_codes)):
        for baseline_id in _id_items(list(values)):
            entry = {"system_platform": _coerce_code(system_code), "id": baseline_id}
            if entry not in entries:
                entries.append(entry)
    return entries


def _append_unique(sequence: list[Any], value: Any) -> None:
    if value in sequence:
        return
    sequence.append(value)


def _code_items(value: Any) -> list[str]:
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, dict):
                code = _text(item.get("code"))
            else:
                code = _text(item)
            if code:
                items.append(code)
        return items
    return list(_csv_tokens(value))


def _baseline_system_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    codes: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        code = _text(item.get("system_platform")) or _text(item.get("systemPlatform"))
        if code and code not in codes:
            codes.append(code)
    return codes


def _normalize_baseline_entries(value: Any, system_codes: list[str], fallback_ids: list[str]) -> list[dict]:
    entries: list[dict] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            baseline_id = (
                _text(item.get("id"))
                or _text(item.get("baseline_id"))
                or _text(item.get("baselineId"))
                or _text(item.get("code"))
            )
            if not baseline_id:
                continue
            system_code = _text(item.get("system_platform")) or _text(item.get("systemPlatform"))
            if system_code:
                entry = {"system_platform": _coerce_code(system_code), "id": baseline_id}
                if entry not in entries:
                    entries.append(entry)
                continue
            for entry in baseline_system_id_objects(system_codes, [baseline_id]):
                if entry not in entries:
                    entries.append(entry)
    if entries:
        return entries
    return baseline_system_id_objects(system_codes, fallback_ids)


def extract_detail_data(existing_app_detail: dict | None) -> dict:
    if not existing_app_detail:
        return {}
    datas = existing_app_detail.get("datas")
    if isinstance(datas, dict):
        return datas
    return existing_app_detail


def build_reused_lan_infos(
    existing_app_detail: dict | None,
    *,
    release_note: str,
    screenshot_uploads: tuple[UploadedFileRef, ...] | None = None,
    icon_upload: UploadedFileRef | None = None,
    name: str | None = None,
    brief_info: str | None = None,
    desc_info: str | None = None,
    target_lan: str | None = None,
    localized_texts: dict[str, dict[str, str]] | None = None,
    desired_lans: tuple[str, ...] | None = None,
    developer_name: str | None = None,
) -> list[dict]:
    detail_data = extract_detail_data(existing_app_detail)
    lan_infos = detail_data.get("app_lan_infos")
    if not isinstance(lan_infos, list) or not lan_infos:
        lan_infos = [{}]

    override_name = _optional_text(name)
    override_brief = _optional_text(brief_info)
    override_desc = _optional_text(desc_info)
    override_lan = _optional_text(target_lan) or _text((detail_data.get("app_basic_info") or {}).get("default_lan")) or "zh_CN"
    effective_localized: dict[str, dict[str, str]] = {
        str(lan).strip(): {str(key): str(value).strip() for key, value in values.items() if str(value).strip()}
        for lan, values in (localized_texts or {}).items()
        if str(lan).strip() and isinstance(values, dict)
    }
    if any(value is not None for value in (override_name, override_brief, override_desc)):
        lan_entry = effective_localized.setdefault(override_lan, {})
        if override_name is not None:
            lan_entry["name"] = override_name
        if override_brief is not None:
            lan_entry["brief_info"] = override_brief
        if override_desc is not None:
            lan_entry["desc_info"] = override_desc

    info_by_lan: dict[str, dict] = {}
    ordered_existing_lans: list[str] = []
    for info in lan_infos:
        lan = _text(info.get("lan")) or "zh_CN"
        if lan not in info_by_lan:
            ordered_existing_lans.append(lan)
        info_by_lan[lan] = info

    if desired_lans:
        ordered_lans = [str(lan).strip() for lan in desired_lans if str(lan).strip()]
    else:
        ordered_lans = ordered_existing_lans or ["zh_CN"]
    if not ordered_lans:
        ordered_lans = ["zh_CN"]

    normalized_infos: list[dict] = []
    for lan in ordered_lans:
        info = info_by_lan.get(lan, {})
        localized = effective_localized.get(lan, {})
        label = _text(info.get("label")) or LANGUAGE_LABELS.get(lan, lan)
        lan_str = _text(info.get("lanStr")) or label
        if screenshot_uploads is not None:
            screenshots = [
                {
                    "screen_shot_key": shot.file_save_key,
                    "image_mode": 1,
                    "sort": index,
                    "size": shot.size,
                }
                for index, shot in enumerate(screenshot_uploads)
            ]
        else:
            screenshots = []
            for index, shot in enumerate(info.get("appScreenShotList") or ()):
                screen_shot_key = _text(shot.get("screen_shot_key"))
                if not screen_shot_key:
                    continue
                screenshot = {
                    "screen_shot_key": screen_shot_key,
                    "image_mode": shot.get("image_mode", 1),
                    "sort": shot.get("sort", index),
                }
                if shot.get("size") is not None:
                    screenshot["size"] = shot.get("size")
                screenshots.append(screenshot)
        normalized_infos.append(
            {
                "lan": lan,
                "label": label,
                "lanStr": lan_str,
                "name": _text(localized.get("name")) or _text(info.get("name")),
                "brief_info": _text(localized.get("brief_info")) or _text(info.get("brief_info")),
                "desc_info": _text(localized.get("desc_info")) or _text(info.get("desc_info")),
                "update_desc": _text(localized.get("update_desc")) or release_note.strip() or _text(info.get("update_desc")),
                "dev_name": _text(info.get("dev_name")) or _text(developer_name),
                "icon_save_key": icon_upload.file_save_key if icon_upload is not None else _text(info.get("icon_save_key")),
                "appScreenShotList": screenshots,
            }
        )
    return normalized_infos


def build_reused_basic_info(
    existing_app_detail: dict | None,
    *,
    package_install_mode: int,
    region: str,
    category_id: int | None = None,
    website: str | None = None,
) -> dict:
    detail_data = extract_detail_data(existing_app_detail)
    basic_info = detail_data.get("app_basic_info") or {}
    reused = dict(basic_info) if isinstance(basic_info, dict) else {}
    reused["default_lan"] = _text(reused.get("default_lan")) or "zh_CN"
    reused["pkg_mode"] = reused.get("pkg_mode", 0) or 0
    reused["pkgInstallMode"] = package_install_mode
    reused["inAppPayment"] = reused.get("inAppPayment", 0) or 0
    reused["category_id"] = reused.get("category_id") if category_id is None else category_id
    reused["website"] = _text(reused.get("website")) if website is None else website.strip()
    reused["region"] = region or _text(reused.get("region")) or "1"
    return reused


def build_reused_fit_info(
    existing_app_detail: dict | None,
    *,
    fit_system_codes: list[str],
    fit_baseline_ids: list[str],
    fit_unsupported_ids: list[str],
    fit_arch_codes: list[str],
    region_codes: list[int],
    fit_cpu_clip_codes: list[str] | None = None,
    fit_motherboard_codes: list[str] | None = None,
    replace_fit_values: bool = False,
) -> dict:
    detail_data = extract_detail_data(existing_app_detail)
    fit_info = detail_data.get("app_fit_info") or {}

    system_mode_codes = _code_items(fit_info.get("system_mode")) or ["1"]
    system_platform_codes = [] if replace_fit_values else _code_items(fit_info.get("system_platform"))
    arch_codes = [] if replace_fit_values else _code_items(fit_info.get("arch"))
    normalized_region_codes = [] if replace_fit_values else _code_items(fit_info.get("region"))
    if not normalized_region_codes:
        normalized_region_codes = [str(code) for code in region_codes] or ["1"]
    baseline_ids = [] if replace_fit_values else _id_items(fit_info.get("baseline"))
    unsupported_ids = [] if replace_fit_values else _id_items(fit_info.get("unsupportBaseline"))
    cpu_clip_codes = _code_items(fit_info.get("cpu_clip"))
    motherboard_codes = _code_items(fit_info.get("motherboard"))

    for code in fit_system_codes:
        _append_unique(system_platform_codes, code)
    for code in fit_arch_codes:
        _append_unique(arch_codes, code)
    for code in [str(code) for code in region_codes]:
        _append_unique(normalized_region_codes, code)
    for baseline_id in fit_baseline_ids:
        _append_unique(baseline_ids, baseline_id)
    for baseline_id in fit_unsupported_ids:
        _append_unique(unsupported_ids, baseline_id)
    if fit_cpu_clip_codes is not None:
        cpu_clip_codes = []
        for code in fit_cpu_clip_codes:
            _append_unique(cpu_clip_codes, code)
    if fit_motherboard_codes is not None:
        motherboard_codes = []
        for code in fit_motherboard_codes:
            _append_unique(motherboard_codes, code)

    return {
        "system_mode": [{"code": _coerce_code(code)} for code in system_mode_codes],
        "baseline": baseline_id_objects(baseline_ids),
        "unsupportBaseline": baseline_id_objects(unsupported_ids),
        "system_platform": [{"code": _coerce_code(code)} for code in system_platform_codes],
        "region": [{"code": _coerce_code(code)} for code in normalized_region_codes],
        "arch": [{"code": _coerce_code(code)} for code in arch_codes],
        "cpu_clip": [{"code": _coerce_code(code)} for code in cpu_clip_codes],
        "motherboard": [{"code": _coerce_code(code)} for code in motherboard_codes],
        "supWayland": fit_info.get("supWayland", 0) or 0,
    }


def normalize_origin_pkg(origin_pkg: dict) -> dict:
    sup_sys_codes = list(_csv_tokens(origin_pkg.get("supSys")))
    if not sup_sys_codes:
        sup_sys_codes = _code_items(origin_pkg.get("system_platform"))
    if not sup_sys_codes:
        sup_sys_codes = _baseline_system_codes(origin_pkg.get("baseline"))
    baseline_ids = list(_csv_tokens(origin_pkg.get("supBlineVer")))
    if not baseline_ids:
        baseline_ids = _id_items(origin_pkg.get("baseline"))
    unsupported_ids = list(_csv_tokens(origin_pkg.get("unsupportBlineVers")))
    if not unsupported_ids:
        unsupported_ids = _id_items(origin_pkg.get("unsupportBaseline"))
    baseline_entries = _normalize_baseline_entries(origin_pkg.get("baseline"), sup_sys_codes, baseline_ids)
    unsupported_entries = _normalize_baseline_entries(
        origin_pkg.get("unsupportBaseline"),
        sup_sys_codes,
        unsupported_ids,
    )

    normalized = {
        "pkg_name": _text(origin_pkg.get("pkg_name")),
        "pkg_version": _text(origin_pkg.get("pkg_version")),
        "pkg_arch": _text(origin_pkg.get("pkg_arch")),
        "pkgArch": _text(origin_pkg.get("pkgArch")),
        "pkgType": origin_pkg.get("pkgType"),
        "pkg_mode": origin_pkg.get("pkg_mode", 0) or 0,
        "pkgChannel": origin_pkg.get("pkgChannel"),
        "pkg_size": origin_pkg.get("pkg_size", 0) or 0,
        "sha256": _text(origin_pkg.get("sha256")),
        "file_save_key": _text(origin_pkg.get("file_save_key")),
        "progressPercent": origin_pkg.get("progressPercent", 100) or 100,
        "index": origin_pkg.get("index"),
        "system_platform": sup_sys_codes,
        "supSys": ",".join(sup_sys_codes),
        "baseline": baseline_entries,
        "supBlineVer": ",".join(baseline_ids),
        "unsupportBaseline": unsupported_entries,
        "unsupportBlineVers": ",".join(unsupported_ids),
        "systemStr": _text(origin_pkg.get("systemStr")),
    }
    for key in _PRESERVED_ORIGIN_PKG_KEYS:
        if key in origin_pkg and origin_pkg[key] not in (None, ""):
            normalized[key] = origin_pkg[key]
    return normalized


def _origin_pkg_identity(origin_pkg: dict) -> tuple[str, str, str, str]:
    normalized = normalize_origin_pkg(origin_pkg)
    return (
        _text(normalized.get("pkg_name")),
        _text(normalized.get("pkg_arch")),
        _text(normalized.get("pkg_version")),
        _text(normalized.get("pkgType")),
    )


def _origin_pkg_package_identity(origin_pkg: dict) -> tuple[str, str, str]:
    normalized = normalize_origin_pkg(origin_pkg)
    return (
        _text(normalized.get("pkg_name")),
        _text(normalized.get("pkg_arch")),
        _text(normalized.get("pkgType")),
    )


def _origin_pkg_persistent_identity(origin_pkg: dict) -> tuple[str, str]:
    for key in _PRESERVED_ORIGIN_PKG_KEYS:
        value = _text(origin_pkg.get(key))
        if value:
            return key, value
    return "", ""


def _entry_system_code(entry: Any) -> str:
    if isinstance(entry, dict):
        return _text(entry.get("system_platform")) or _text(entry.get("systemPlatform"))
    return ""


def _remove_origin_pkg_systems(origin_pkg: dict, system_codes: set[str]) -> dict:
    normalized = normalize_origin_pkg(origin_pkg)
    remaining_codes = [
        code
        for code in (normalized.get("system_platform") or [])
        if _text(code) not in system_codes
    ]
    if len(remaining_codes) == len(normalized.get("system_platform") or []):
        return normalized

    baseline_entries = [
        entry
        for entry in (normalized.get("baseline") or [])
        if _entry_system_code(entry) not in system_codes
    ]
    unsupported_entries = [
        entry
        for entry in (normalized.get("unsupportBaseline") or [])
        if _entry_system_code(entry) not in system_codes
    ]
    normalized["system_platform"] = remaining_codes
    normalized["supSys"] = ",".join(remaining_codes)
    normalized["baseline"] = baseline_entries
    normalized["supBlineVer"] = ",".join(_id_items(baseline_entries))
    normalized["unsupportBaseline"] = unsupported_entries
    normalized["unsupportBlineVers"] = ",".join(_id_items(unsupported_entries))
    return normalized


def merge_origin_pkgs(existing_app_detail: dict | None, new_origin_pkgs: list[dict]) -> list[dict]:
    detail_data = extract_detail_data(existing_app_detail)
    merged = [
        normalize_origin_pkg(origin_pkg)
        for origin_pkg in (detail_data.get("app_origin_pkgs") or ())
    ]
    for origin_pkg in new_origin_pkgs:
        normalized = normalize_origin_pkg(origin_pkg)
        identity = _origin_pkg_identity(normalized)
        package_identity = _origin_pkg_package_identity(normalized)
        persistent_identity = _origin_pkg_persistent_identity(normalized)
        replacement_codes = {_text(code) for code in (normalized.get("system_platform") or []) if _text(code)}
        next_merged: list[dict] = []
        for index, existing in enumerate(merged):
            if persistent_identity[0] and _origin_pkg_persistent_identity(existing) == persistent_identity:
                next_merged.append(normalized)
                next_merged.extend(merged[index + 1 :])
                break
            if _origin_pkg_identity(existing) == identity:
                next_merged.append(normalized)
                next_merged.extend(merged[index + 1 :])
                break
            if replacement_codes and _origin_pkg_package_identity(existing) == package_identity:
                trimmed = _remove_origin_pkg_systems(existing, replacement_codes)
                if trimmed.get("system_platform"):
                    next_merged.append(trimmed)
                continue
            next_merged.append(existing)
        else:
            next_merged.append(normalized)
        merged = next_merged

    for index, origin_pkg in enumerate(merged):
        origin_pkg["index"] = index
    return merged
