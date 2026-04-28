from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from appstore.appstore_client import AppStoreClient
from appstore.capabilities import CapabilityCache
from appstore.models import AppRecord, PackageInfo, PackageRecord, ReleaseRecord, TargetRecord, UploadedFileRef
from appstore.update_payload import (
    build_reused_basic_info,
    build_reused_fit_info,
    build_reused_lan_infos,
    merge_origin_pkgs,
)


class ValidationError(RuntimeError):
    pass


ARCH_CODE_MAP = {
    "amd64": ("4", "X86"),
    "x86_64": ("4", "X86"),
    "arm64": ("3", "ARM"),
    "loong64": ("6", "loong"),
    "sw64": ("8", "sw64"),
}

PKG_TYPE_MAP = {
    ("deb", "deb"): 11,
    ("linglong", "uab"): 22,
    ("linglong", "layer"): 22,
}


@dataclass(frozen=True)
class ValidatedPackage:
    package: PackageRecord
    package_info: PackageInfo
    targets: tuple[TargetRecord, ...]


@dataclass(frozen=True)
class ValidatedRelease:
    app: AppRecord
    release: ReleaseRecord
    package_family: str
    packages: tuple[ValidatedPackage, ...]
    system_line_labels: dict[str, str]


def _target_display_label(target: TargetRecord) -> str:
    baseline_ids = _target_baseline_ids(target)
    if baseline_ids:
        return f"{target.sup_sys_code}:{','.join(baseline_ids)}"
    if target.baseline_id:
        return f"{target.sup_sys_code}:{target.baseline_id}"
    return target.sup_sys_code


def _target_baseline_ids(target: TargetRecord) -> tuple[str, ...]:
    normalized: list[str] = []
    explicit = tuple(str(value).strip() for value in getattr(target, "baseline_ids", ()) if str(value).strip())
    if explicit:
        for baseline_id in explicit:
            if baseline_id not in normalized:
                normalized.append(baseline_id)
        return tuple(normalized)
    if target.baseline_id:
        return (target.baseline_id,)
    return ()


def _resolve_region_codes(region: str) -> list[int]:
    normalized = [token.strip() for token in str(region or "1").split(",")]
    result: list[int] = []
    for token in normalized:
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        if value not in result:
            result.append(value)
    return result or [1]


def _adapt_code_objects(codes: tuple[str, ...] | list[str] | None) -> list[dict]:
    if codes is None:
        return []
    result: list[dict] = []
    seen: set[str] = set()
    for code in codes:
        normalized = str(code).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append({"code": int(normalized) if normalized.isdigit() else normalized})
    return result


def _upload_time_label() -> str:
    now = datetime.now()
    return f"{now.year}-{now.month}-{now.day}  {now.hour}:{now.minute}:{now.second}  "


def _resolve_store_arch(arch: str) -> tuple[str, str]:
    normalized = arch.strip().lower()
    try:
        return ARCH_CODE_MAP[normalized]
    except KeyError as exc:
        raise ValidationError(f"unsupported architecture: {arch}") from exc


def _get_system_lines(cache: CapabilityCache, package_family: str) -> dict[str, object]:
    if package_family == "deb":
        return cache.deb_system_lines
    if package_family == "linglong":
        return cache.linglong_system_lines
    raise ValidationError(f"unsupported package family: {package_family}")


def _assert_package_supported(package: PackageRecord) -> None:
    if (package.package_family, package.package_format) not in PKG_TYPE_MAP:
        raise ValidationError(
            f"unsupported package family/format: {package.package_family}/{package.package_format}"
        )


def _assert_target_supported(package_family: str, target: TargetRecord, capability_cache: CapabilityCache) -> None:
    system_lines = _get_system_lines(capability_cache, package_family)
    if target.sup_sys_code not in system_lines:
        raise ValidationError(f"unsupported system line for {package_family}: {target.sup_sys_code}")

    baseline_key = f"{package_family}:{target.sup_sys_code}"
    allowed_baselines = capability_cache.baseline_options.get(baseline_key, ())
    allowed_ids = {option.baseline_id for option in allowed_baselines}
    target_baseline_ids = _target_baseline_ids(target)
    if allowed_ids and not target_baseline_ids:
        raise ValidationError(f"baseline required for system line {target.sup_sys_code}")
    for baseline_id in target_baseline_ids:
        if baseline_id not in allowed_ids:
            raise ValidationError(f"unsupported baseline for system line {target.sup_sys_code}: {baseline_id}")
    for baseline_id in target.unsupport_baseline_ids:
        if baseline_id not in allowed_ids:
            raise ValidationError(f"unsupported baseline for system line {target.sup_sys_code}: {baseline_id}")


def validate_release_group(
    app: AppRecord,
    release: ReleaseRecord,
    packages: tuple[PackageRecord, ...],
    targets_by_package: dict[str, tuple[TargetRecord, ...]],
    inspected_by_package: dict[str, PackageInfo],
    capability_cache: CapabilityCache,
) -> ValidatedRelease:
    families = {package.package_family for package in packages}
    if len(families) != 1:
        raise ValidationError("release cannot mix deb and linglong packages")
    package_family = next(iter(families))

    system_lines = _get_system_lines(capability_cache, package_family)
    validated_packages: list[ValidatedPackage] = []
    seen_arch_system_version: set[tuple[str, str, str]] = set()
    for package in packages:
        _assert_package_supported(package)
        package_info = inspected_by_package[package.package_key]
        if package_info.pkg_name != app.pkg_name:
            raise ValidationError(f"package name mismatch for {package.package_key}: {package_info.pkg_name}")
        inspected_arch_code, _inspected_arch_label = _resolve_store_arch(package_info.pkg_arch)
        if package.declared_arch:
            declared_arch_code, _declared_arch_label = _resolve_store_arch(package.declared_arch)
            if inspected_arch_code != declared_arch_code:
                raise ValidationError(f"declared arch mismatch for {package.package_key}")
        targets = targets_by_package.get(package.package_key, ())
        if not targets:
            raise ValidationError(f"package has no targets: {package.package_key}")
        for target in targets:
            _assert_target_supported(package_family, target, capability_cache)
            collision_key = (inspected_arch_code, target.sup_sys_code, package_info.pkg_version)
            if collision_key in seen_arch_system_version:
                raise ValidationError("same architecture/system/version collision in release")
            seen_arch_system_version.add(collision_key)
        validated_packages.append(
            ValidatedPackage(package=package, package_info=package_info, targets=targets)
        )

    return ValidatedRelease(
        app=app,
        release=release,
        package_family=package_family,
        packages=tuple(validated_packages),
        system_line_labels={code: line.label for code, line in system_lines.items()},
    )


def build_release_payload(
    validated_release: ValidatedRelease,
    uploads_by_package: dict[str, UploadedFileRef],
    app_uploads: dict[str, UploadedFileRef | tuple[UploadedFileRef, ...]] | None,
    target_app_id: str,
    existing_app_detail: dict | None = None,
    existing_app_overrides: dict[str, object] | None = None,
    localized_lan_texts: dict[str, dict[str, str]] | None = None,
    desired_lans: tuple[str, ...] | None = None,
    developer_name: str = "",
) -> dict:
    fit_system_codes: list[str] = []
    fit_baseline_ids: list[str] = []
    fit_unsupported_ids: list[str] = []
    fit_arch_codes: list[str] = []
    origin_pkgs: list[dict] = []
    for index, validated_package in enumerate(validated_release.packages):
        package = validated_package.package
        package_info = validated_package.package_info
        sup_sys_codes = [target.sup_sys_code for target in validated_package.targets]
        baseline_ids: list[str] = []
        for target in validated_package.targets:
            for baseline_id in _target_baseline_ids(target):
                if baseline_id not in baseline_ids:
                    baseline_ids.append(baseline_id)
        unsupported_ids = [
            baseline_id for target in validated_package.targets for baseline_id in target.unsupport_baseline_ids
        ]
        arch_code, arch_label = _resolve_store_arch(package_info.pkg_arch)
        system_labels = [
            validated_release.system_line_labels.get(target.sup_sys_code, _target_display_label(target))
            for target in validated_package.targets
        ]
        for code in sup_sys_codes:
            if code not in fit_system_codes:
                fit_system_codes.append(code)
        for baseline_id in baseline_ids:
            if baseline_id not in fit_baseline_ids:
                fit_baseline_ids.append(baseline_id)
        for unsupported_id in unsupported_ids:
            if unsupported_id not in fit_unsupported_ids:
                fit_unsupported_ids.append(unsupported_id)
        if arch_code not in fit_arch_codes:
            fit_arch_codes.append(arch_code)
        origin_pkgs.append(
            {
                "pkg_name": package_info.pkg_name,
                "pkg_version": package_info.pkg_version,
                "pkg_arch": arch_code,
                "pkgArch": arch_label,
                "pkgType": PKG_TYPE_MAP[(package.package_family, package.package_format)],
                "pkg_mode": 0,
                "pkgChannel": package.pkg_channel or None,
                "sums": 0,
                "pkg_size": package_info.pkg_size,
                "sha256": package_info.sha256,
                "file_save_key": uploads_by_package[package.package_key].file_save_key,
                "progressPercent": 100,
                "upload_time": _upload_time_label(),
                "index": index,
                "system_platform": sup_sys_codes,
                "supSys": ",".join(sup_sys_codes),
                "baseline": baseline_ids,
                "supBlineVer": ",".join(baseline_ids),
                "unsupportBaseline": unsupported_ids,
                "unsupportBlineVers": ",".join(unsupported_ids),
                "systemStr": " ".join(system_labels),
            }
        )

    package_install_mode = 1 if validated_release.package_family == "deb" else 2
    region_codes = _resolve_region_codes(validated_release.release.region)
    region_value = ",".join(str(code) for code in region_codes)
    cpu_clip_codes = getattr(validated_release.release, "cpu_clip_codes", None)
    motherboard_codes = getattr(validated_release.release, "motherboard_codes", None)
    if existing_app_detail is not None:
        overrides = existing_app_overrides or {}
        app_info = {
            "app_lan_infos": build_reused_lan_infos(
                existing_app_detail,
                release_note=validated_release.release.note,
                screenshot_uploads=(
                    tuple(app_uploads["screenshots"])
                    if app_uploads is not None and "screenshots" in app_uploads
                    else None
                ),
                icon_upload=(
                    app_uploads["icon"]
                    if app_uploads is not None and "icon" in app_uploads
                    else None
                ),
                name=(
                    str(overrides["app_name_zh"]).strip()
                    if "app_name_zh" in overrides and overrides["app_name_zh"] is not None
                    else None
                ),
                brief_info=(
                    str(overrides["short_desc_zh"]).strip()
                    if "short_desc_zh" in overrides and overrides["short_desc_zh"] is not None
                    else None
                ),
                desc_info=(
                    str(overrides["full_desc_zh"]).strip()
                    if "full_desc_zh" in overrides and overrides["full_desc_zh"] is not None
                    else None
                ),
                localized_texts=localized_lan_texts,
                desired_lans=desired_lans,
            ),
            "app_basic_info": build_reused_basic_info(
                existing_app_detail,
                package_install_mode=package_install_mode,
                region=region_value,
                category_id=(
                    int(overrides["category_id"])
                    if "category_id" in overrides and overrides["category_id"] is not None
                    else None
                ),
                website=(
                    str(overrides["website"]).strip()
                    if "website" in overrides and overrides["website"] is not None
                    else None
                ),
            ),
            "app_fit_info": build_reused_fit_info(
                existing_app_detail,
                fit_system_codes=fit_system_codes,
                fit_baseline_ids=fit_baseline_ids,
                fit_unsupported_ids=fit_unsupported_ids,
                fit_arch_codes=fit_arch_codes,
                region_codes=region_codes,
                fit_cpu_clip_codes=list(cpu_clip_codes) if cpu_clip_codes is not None else None,
                fit_motherboard_codes=list(motherboard_codes) if motherboard_codes is not None else None,
            ),
            "app_origin_pkgs": merge_origin_pkgs(existing_app_detail, origin_pkgs),
        }
    else:
        if not app_uploads:
            raise ValidationError("app uploads required for new app submission")
        app_info = {
            "app_lan_infos": build_reused_lan_infos(
                None,
                release_note=validated_release.release.note,
                screenshot_uploads=tuple(app_uploads["screenshots"]),
                icon_upload=app_uploads["icon"],
                localized_texts=localized_lan_texts
                or {
                    "zh_CN": {
                        "name": validated_release.app.app_name_zh,
                        "brief_info": validated_release.app.short_desc_zh,
                        "desc_info": validated_release.app.full_desc_zh,
                    }
                },
                desired_lans=desired_lans or ("zh_CN",),
                developer_name=developer_name,
            ),
            "app_basic_info": {
                "default_lan": "zh_CN",
                "pkg_mode": 0,
                "pkgInstallMode": package_install_mode,
                "inAppPayment": 0,
                "category_id": validated_release.app.category_id,
                "website": validated_release.app.website,
                "region": region_value,
            },
            "app_fit_info": {
                "system_mode": [{"code": 1}],
                "baseline": fit_baseline_ids,
                "unsupportBaseline": fit_unsupported_ids,
                "system_platform": [{"code": code} for code in fit_system_codes],
                "region": [{"code": code} for code in region_codes],
                "arch": [{"code": code} for code in fit_arch_codes],
                "cpu_clip": _adapt_code_objects(cpu_clip_codes),
                "motherboard": _adapt_code_objects(motherboard_codes),
                "supWayland": 0,
            },
            "app_origin_pkgs": origin_pkgs,
        }
    payload = {
        "operate_type": 52,
        "app_info": app_info,
    }
    if target_app_id:
        payload["app_id"] = target_app_id
    return payload


def submit_grouped_release(
    client: AppStoreClient,
    validated_release: ValidatedRelease,
    app_uploads: dict[str, UploadedFileRef | tuple[UploadedFileRef, ...]] | None,
    uploads_by_package: dict[str, UploadedFileRef],
    target_app_id: str = "",
    existing_app_detail: dict | None = None,
    existing_app_overrides: dict[str, object] | None = None,
    localized_lan_texts: dict[str, dict[str, str]] | None = None,
    desired_lans: tuple[str, ...] | None = None,
) -> dict:
    developer_name = ""
    if existing_app_detail is None:
        dev_info = client.fetch_dev_info()
        developer_name = (
            str(dev_info.get("enterprise_name", "") or "").strip()
            or str(dev_info.get("dev_name", "") or "").strip()
            or str(dev_info.get("nick_name", "") or "").strip()
        )
    payload = build_release_payload(
        validated_release=validated_release,
        uploads_by_package=uploads_by_package,
        app_uploads=app_uploads,
        target_app_id=target_app_id,
        existing_app_detail=existing_app_detail,
        existing_app_overrides=existing_app_overrides,
        localized_lan_texts=localized_lan_texts,
        desired_lans=desired_lans,
        developer_name=developer_name,
    )
    return client.submit_payload(payload)
