from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    if value is None:
        return ""
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


def _coerce_code(value: Any) -> str | int:
    normalized = _text(value)
    if normalized.isdigit():
        return int(normalized)
    return normalized


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


def extract_detail_data(existing_app_detail: dict | None) -> dict:
    if not existing_app_detail:
        return {}
    datas = existing_app_detail.get("datas")
    if isinstance(datas, dict):
        return datas
    return existing_app_detail


def build_reused_lan_infos(existing_app_detail: dict | None, *, release_note: str) -> list[dict]:
    detail_data = extract_detail_data(existing_app_detail)
    lan_infos = detail_data.get("app_lan_infos")
    if not isinstance(lan_infos, list) or not lan_infos:
        lan_infos = [{}]

    normalized_infos: list[dict] = []
    for info in lan_infos:
        lan = _text(info.get("lan")) or "zh_CN"
        label = _text(info.get("label")) or "中文（简体）"
        lan_str = _text(info.get("lanStr")) or label
        screenshots: list[dict] = []
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
                "name": _text(info.get("name")),
                "brief_info": _text(info.get("brief_info")),
                "desc_info": _text(info.get("desc_info")),
                "update_desc": release_note.strip() or _text(info.get("update_desc")),
                "dev_name": _text(info.get("dev_name")),
                "icon_save_key": _text(info.get("icon_save_key")),
                "appScreenShotList": screenshots,
            }
        )
    return normalized_infos


def build_reused_basic_info(
    existing_app_detail: dict | None,
    *,
    package_install_mode: int,
    region: str,
) -> dict:
    detail_data = extract_detail_data(existing_app_detail)
    basic_info = detail_data.get("app_basic_info") or {}
    return {
        "default_lan": _text(basic_info.get("default_lan")) or "zh_CN",
        "pkg_mode": basic_info.get("pkg_mode", 0) or 0,
        "pkgInstallMode": package_install_mode,
        "inAppPayment": basic_info.get("inAppPayment", 0) or 0,
        "category_id": basic_info.get("category_id"),
        "website": _text(basic_info.get("website")),
        "region": region or _text(basic_info.get("region")) or "1",
    }


def build_reused_fit_info(
    existing_app_detail: dict | None,
    *,
    fit_system_codes: list[str],
    fit_baseline_ids: list[str],
    fit_unsupported_ids: list[str],
    fit_arch_codes: list[str],
    region_code: int,
) -> dict:
    detail_data = extract_detail_data(existing_app_detail)
    fit_info = detail_data.get("app_fit_info") or {}

    system_mode_codes = _code_items(fit_info.get("system_mode")) or ["1"]
    system_platform_codes = _code_items(fit_info.get("system_platform"))
    arch_codes = _code_items(fit_info.get("arch"))
    region_codes = _code_items(fit_info.get("region")) or [str(region_code)]
    baseline_ids = list(_csv_tokens(fit_info.get("baseline")))
    unsupported_ids = list(_csv_tokens(fit_info.get("unsupportBaseline")))

    for code in fit_system_codes:
        _append_unique(system_platform_codes, code)
    for code in fit_arch_codes:
        _append_unique(arch_codes, code)
    for baseline_id in fit_baseline_ids:
        _append_unique(baseline_ids, baseline_id)
    for baseline_id in fit_unsupported_ids:
        _append_unique(unsupported_ids, baseline_id)

    return {
        "system_mode": [{"code": _coerce_code(code)} for code in system_mode_codes],
        "baseline": baseline_ids,
        "unsupportBaseline": unsupported_ids,
        "system_platform": [{"code": _coerce_code(code)} for code in system_platform_codes],
        "region": [{"code": _coerce_code(code)} for code in region_codes],
        "arch": [{"code": _coerce_code(code)} for code in arch_codes],
        "cpu_clip": fit_info.get("cpu_clip") or [],
        "motherboard": fit_info.get("motherboard") or [],
        "supWayland": fit_info.get("supWayland", 0) or 0,
    }


def normalize_origin_pkg(origin_pkg: dict) -> dict:
    sup_sys_codes = list(_csv_tokens(origin_pkg.get("supSys")))
    if not sup_sys_codes:
        sup_sys_codes = _code_items(origin_pkg.get("system_platform"))
    baseline_ids = list(_csv_tokens(origin_pkg.get("supBlineVer")))
    if not baseline_ids:
        baseline_ids = list(_csv_tokens(origin_pkg.get("baseline")))
    unsupported_ids = list(_csv_tokens(origin_pkg.get("unsupportBlineVers")))
    if not unsupported_ids:
        unsupported_ids = list(_csv_tokens(origin_pkg.get("unsupportBaseline")))

    return {
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
        "baseline": baseline_ids,
        "supBlineVer": ",".join(baseline_ids),
        "unsupportBaseline": unsupported_ids,
        "unsupportBlineVers": ",".join(unsupported_ids),
        "systemStr": _text(origin_pkg.get("systemStr")),
    }


def _origin_pkg_identity(origin_pkg: dict) -> tuple[str, str, str, tuple[str, ...]]:
    normalized = normalize_origin_pkg(origin_pkg)
    return (
        _text(normalized.get("pkg_arch")),
        _text(normalized.get("pkg_version")),
        _text(normalized.get("pkgType")),
        tuple(normalized.get("system_platform") or ()),
    )


def merge_origin_pkgs(existing_app_detail: dict | None, new_origin_pkgs: list[dict]) -> list[dict]:
    detail_data = extract_detail_data(existing_app_detail)
    merged = [
        normalize_origin_pkg(origin_pkg)
        for origin_pkg in (detail_data.get("app_origin_pkgs") or ())
    ]
    for origin_pkg in new_origin_pkgs:
        normalized = normalize_origin_pkg(origin_pkg)
        identity = _origin_pkg_identity(normalized)
        for index, existing in enumerate(merged):
            if _origin_pkg_identity(existing) == identity:
                merged[index] = normalized
                break
        else:
            merged.append(normalized)

    for index, origin_pkg in enumerate(merged):
        origin_pkg["index"] = index
    return merged
