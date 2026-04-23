from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
import getpass
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook

from appstore.appstore_client import (
    AppStoreClient,
    AuthenticationError,
    BASE,
    REQUEST_TIMEOUT,
    build_submit_payload,
    choose_target_app_id,
)
from appstore.browser_submission import BrowserSubmissionRunner
from appstore.capabilities import load_capability_cache, sync_capabilities_to_cache
from appstore.examples.generate_template import generate_template
from appstore.inspectors import inspect_package, read_package_info
from appstore.deb import read_deb_package_info
from appstore.manifest import load_manifest
from appstore.models import AppRecord, DebPackageInfo, LoadedManifest, PackageRecord, ReleaseRecord, RowResult, TargetRecord, UploadedFileRef
from appstore.platform_policy import decide_execution_mode
from appstore.submission import ARCH_CODE_MAP, submit_grouped_release, validate_release_group


@dataclass(frozen=True)
class SubmissionRelease:
    row_id: int
    app_key: str
    release_key: str
    release_name: str
    region: str
    note: str
    system_platform: str
    arch: str
    baseline: str
    deb_path: Path


def _infer_package_kind_from_path(file_path: Path) -> tuple[str, str]:
    suffix = file_path.suffix.lower()
    if suffix == ".deb":
        return "deb", "deb"
    if suffix == ".uab":
        return "linglong", "uab"
    if suffix == ".layer":
        return "linglong", "layer"
    raise RuntimeError(f"unsupported package format for file: {file_path.name}")


def _resolve_arch_code_label(arch: str) -> tuple[str, str]:
    try:
        return ARCH_CODE_MAP[arch.strip().lower()]
    except KeyError as exc:
        raise RuntimeError(f"unsupported architecture: {arch}") from exc


def _timestamp_label() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _write_reports(output_dir: Path | str, results: list[RowResult]) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_rows = [
        {
            "row_id": result.row_id,
            "app_key": result.app_key,
            "deb_path": str(result.deb_path),
            "status": result.status,
            "message": result.message,
            "app_id": result.app_id,
            "pkg_name": result.pkg_name,
            "pkg_version": result.pkg_version,
            "selector": result.selector,
        }
        for result in results
    ]
    (output_dir / "report.json").write_text(
        json.dumps(report_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "report"
    sheet.append(["row_id", "app_key", "deb_path", "status", "message", "app_id", "pkg_name", "pkg_version", "selector"])
    for row in report_rows:
        sheet.append(
            [
                row["row_id"],
                row["app_key"],
                row["deb_path"],
                row["status"],
                row["message"],
                row["app_id"],
                row["pkg_name"],
                row["pkg_version"],
                row["selector"],
            ]
        )
    workbook.save(output_dir / "report.xlsx")


def _parse_row_filter(raw_value: str | None) -> set[int]:
    if raw_value is None:
        return set()
    normalized = raw_value.strip()
    if not normalized:
        return set()

    selected_rows: set[int] = set()
    for part in normalized.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text.strip())
            end = int(end_text.strip())
            if end < start:
                raise ValueError(f"invalid row range: {token}")
            selected_rows.update(range(start, end + 1))
            continue
        selected_rows.add(int(token))
    return selected_rows


def _csv_tokens(value) -> tuple[str, ...]:
    if value is None:
        return ()
    tokens = []
    for token in str(value).split(","):
        normalized = token.strip()
        if normalized:
            tokens.append(normalized)
    return tuple(tokens)


@dataclass(frozen=True)
class GroupedRowFilter:
    package_row_ids: set[int]
    release_row_ids: set[int]


def _parse_grouped_row_filter(raw_value: str | None) -> GroupedRowFilter:
    if raw_value is None:
        return GroupedRowFilter(package_row_ids=set(), release_row_ids=set())
    normalized = raw_value.strip()
    if not normalized:
        return GroupedRowFilter(package_row_ids=set(), release_row_ids=set())

    package_row_ids: set[int] = set()
    release_row_ids: set[int] = set()
    for part in normalized.split(","):
        token = part.strip()
        if not token:
            continue
        if token.startswith("package:") or token.startswith("p:"):
            _, value = token.split(":", 1)
            package_row_ids.add(int(value.strip()))
            continue
        if token.startswith("release:") or token.startswith("r:"):
            _, value = token.split(":", 1)
            release_row_ids.add(int(value.strip()))
            continue
        package_row_ids.add(int(token))
    return GroupedRowFilter(package_row_ids=package_row_ids, release_row_ids=release_row_ids)


def _validate_submit_response(response, action: str) -> dict | list:
    status_code = getattr(response, "status_code", 200)
    if status_code >= 400:
        try:
            detail_payload = response.json()
        except Exception:
            detail_payload = None
        if isinstance(detail_payload, (dict, list)):
            raise RuntimeError(f"{action} failed with HTTP {status_code}: {detail_payload}")
        detail_text = str(getattr(response, "text", "") or "").strip()
        if detail_text:
            raise RuntimeError(f"{action} failed with HTTP {status_code}: {detail_text[:500]}")
        raise RuntimeError(f"{action} failed with HTTP {status_code}")

    payload = response.json()
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise RuntimeError(f"{action} returned unexpected payload: {payload}")

    status = payload.get("status")
    if status not in (None, 200):
        raise RuntimeError(f"{action} failed: {payload}")
    return payload


def _submit_payload(client, payload: dict) -> dict:
    if hasattr(client, "submit_payload"):
        response = client.submit_payload(payload)
        if isinstance(response, dict):
            return response
        raise RuntimeError(f"submit_app returned unexpected payload: {response}")

    session = getattr(client, "session", None)
    if session is None or not hasattr(session, "post"):
        raise RuntimeError("client does not support payload submission")

    response = session.post(f"{BASE}/store-dev-app/app", json=payload, timeout=REQUEST_TIMEOUT)
    return _validate_submit_response(response, "submit_app")


def _build_placeholder_uploads(
    *,
    app: AppRecord,
    release: SubmissionRelease,
    package_info: DebPackageInfo,
) -> dict[str, UploadedFileRef | tuple[UploadedFileRef, ...]]:
    def placeholder(path: Path, upload_type: str, *, size: int | None = None) -> UploadedFileRef:
        return UploadedFileRef(
            kind=upload_type,
            file_save_key=f"preflight:{upload_type}:{path.name}",
            size=path.stat().st_size if size is None else size,
            file_hash="",
        )

    return {
        "icon": placeholder(app.icon_path, "icon"),
        "screenshots": tuple(placeholder(path, "image") for path in app.screenshot_paths),
        "package": placeholder(release.deb_path, "temppkg", size=package_info.pkg_size),
    }


def _extract_response_app_id(response: dict | None) -> str:
    if not isinstance(response, dict):
        return ""
    datas = response.get("datas")
    if isinstance(datas, dict):
        app_id = datas.get("app_id")
        if app_id:
            return str(app_id)
    app_id = response.get("app_id")
    if app_id:
        return str(app_id)
    return ""


def _resolve_target_app_id(
    client,
    app: AppRecord,
    app_id_cache: dict[str, str],
    app_entry_cache: dict[str, dict] | None = None,
) -> str:
    cached_app_id = app_id_cache.get(app.app_key, "")
    if cached_app_id:
        return cached_app_id
    if app.app_id_override:
        app_id_cache[app.app_key] = app.app_id_override
        return app.app_id_override

    matches = client.find_apps_by_pkg_name(app.pkg_name)
    target_app_id = choose_target_app_id(matches, app.app_id_override)
    if target_app_id:
        app_id_cache[app.app_key] = target_app_id
        if app_entry_cache is not None:
            for match in matches:
                if str(match.get("app_id", "")).strip() == str(target_app_id).strip():
                    app_entry_cache[app.app_key] = match
                    break
    return target_app_id


def _resolve_target_app_entry(
    client,
    app: AppRecord,
    target_app_id: str,
    app_entry_cache: dict[str, dict] | None = None,
) -> dict | None:
    cached = None if app_entry_cache is None else app_entry_cache.get(app.app_key)
    if cached is not None:
        return cached
    matches = client.find_apps_by_pkg_name(app.pkg_name)
    if not matches:
        return None
    if target_app_id:
        for match in matches:
            if str(match.get("app_id", "")).strip() == str(target_app_id).strip():
                if app_entry_cache is not None:
                    app_entry_cache[app.app_key] = match
                return match
        raise RuntimeError(f"unable to resolve app detail row for app_id {target_app_id}")
    if len(matches) == 1:
        if app_entry_cache is not None:
            app_entry_cache[app.app_key] = matches[0]
        return matches[0]
    return None


def _load_existing_app_detail(
    client,
    app: AppRecord,
    target_app_id: str,
    app_detail_cache: dict[str, dict],
    app_entry_cache: dict[str, dict] | None = None,
) -> dict | None:
    if not target_app_id:
        return None
    cached = app_detail_cache.get(app.app_key)
    if cached is not None:
        return cached
    if app_entry_cache is not None and app.app_key not in app_entry_cache and not app.app_id_override:
        return None
    if not hasattr(client, "get_app_detail"):
        return None
    entry = _resolve_target_app_entry(client, app, target_app_id, app_entry_cache)
    if not entry:
        return None
    detail_id = str(entry.get("id", "")).strip()
    if not detail_id:
        return None
    detail = client.get_app_detail(detail_id)
    app_detail_cache[app.app_key] = detail
    return detail


def _detail_app_name(existing_app_detail: dict, pkg_name: str) -> str:
    lan_infos = existing_app_detail.get("app_lan_infos") or []
    if lan_infos:
        name = str(lan_infos[0].get("name", "")).strip()
        if name:
            return name
    basic_info = existing_app_detail.get("app_basic_info") or {}
    name = str(basic_info.get("app_name", "")).strip()
    if name:
        return name
    return pkg_name


def _build_direct_app_record(
    *,
    pkg_name: str,
    target_app_id: str,
    existing_app_detail: dict,
) -> AppRecord:
    basic_info = existing_app_detail.get("app_basic_info") or {}
    lan_infos = existing_app_detail.get("app_lan_infos") or [{}]
    lan_info = lan_infos[0] if lan_infos else {}
    return AppRecord(
        app_key=pkg_name,
        app_name_zh=_detail_app_name(existing_app_detail, pkg_name),
        pkg_name=pkg_name,
        category_id=int(basic_info.get("category_id") or 0),
        website=str(basic_info.get("website", "") or "").strip(),
        short_desc_zh=str(lan_info.get("brief_info", "") or "").strip(),
        full_desc_zh=str(lan_info.get("desc_info", "") or "").strip(),
        icon_path=Path("."),
        screenshot_paths=(),
        keywords_zh="",
        app_id_override=target_app_id,
    )


def _build_direct_release(
    *,
    app_key: str,
    release_key: str,
    note: str,
    region: str,
    package_infos: list[DebPackageInfo],
) -> ReleaseRecord:
    versions = sorted({info.pkg_version for info in package_infos})
    release_name = versions[0] if len(versions) == 1 else f"{versions[0]}+{len(versions) - 1}"
    if note.strip():
        normalized_note = note.strip()
    elif len(versions) == 1:
        normalized_note = f"更新到 {versions[0]}"
    else:
        normalized_note = f"批量更新版本：{', '.join(versions)}"
    return ReleaseRecord(
        row_id=1,
        app_key=app_key,
        release_key=release_key,
        release_name=release_name,
        region=region,
        note=normalized_note,
    )


def _existing_pkg_target(existing_app_detail: dict, package_info: DebPackageInfo) -> tuple[str, str, tuple[str, ...]]:
    detail_packages = existing_app_detail.get("app_origin_pkgs") or []
    arch_code, arch_label = _resolve_arch_code_label(package_info.pkg_arch)
    normalized_label = arch_label.strip().lower()
    for existing_pkg in detail_packages:
        pkg_arch_code = str(existing_pkg.get("pkg_arch", "")).strip()
        pkg_arch_label = str(existing_pkg.get("pkgArch", "")).strip().lower()
        if pkg_arch_code != arch_code and pkg_arch_label != normalized_label:
            continue
        sup_sys_values = _csv_tokens(existing_pkg.get("supSys"))
        baseline_values = _csv_tokens(existing_pkg.get("supBlineVer"))
        unsupported_values = _csv_tokens(existing_pkg.get("unsupportBlineVers"))
        return (
            sup_sys_values[0] if sup_sys_values else "",
            baseline_values[0] if baseline_values else "",
            unsupported_values,
        )
    return "", "", ()


def _default_sup_sys_code(package_info: DebPackageInfo) -> str:
    arch = package_info.pkg_arch.strip().lower()
    if arch in {"loong64", "loongarch64"}:
        return "21"
    return "11"


def _default_baseline_id(*, capability_cache, package_family: str, sup_sys_code: str) -> str:
    options = capability_cache.baseline_options.get(f"{package_family}:{sup_sys_code}", ())
    if not options:
        return ""
    return options[0].baseline_id


def _normalize_direct_target(
    *,
    package_record: PackageRecord,
    package_info: DebPackageInfo,
    existing_app_detail: dict,
    capability_cache,
) -> TargetRecord:
    sup_sys_code, baseline_id, unsupported_ids = _existing_pkg_target(existing_app_detail, package_info)
    if not sup_sys_code:
        sup_sys_code = _default_sup_sys_code(package_info)
    allowed_ids = {
        option.baseline_id for option in capability_cache.baseline_options.get(f"{package_record.package_family}:{sup_sys_code}", ())
    }
    if baseline_id and allowed_ids and baseline_id not in allowed_ids:
        baseline_id = ""
    if not baseline_id:
        baseline_id = _default_baseline_id(
            capability_cache=capability_cache,
            package_family=package_record.package_family,
            sup_sys_code=sup_sys_code,
        )
    unsupported_ids = tuple(baseline for baseline in unsupported_ids if not allowed_ids or baseline in allowed_ids)
    return TargetRecord(
        row_id=package_record.row_id,
        app_key=package_record.app_key,
        release_key=package_record.release_key,
        package_key=package_record.package_key,
        sup_sys_code=sup_sys_code,
        baseline_id=baseline_id,
        unsupport_baseline_ids=unsupported_ids,
    )


def _build_direct_packages(
    *,
    package_paths: list[Path],
    release_key: str,
    pkg_channel: str,
) -> tuple[str, tuple[PackageRecord, ...], dict[str, DebPackageInfo]]:
    package_infos: list[DebPackageInfo] = []
    package_records: list[PackageRecord] = []
    pkg_name: str | None = None
    package_family: str | None = None
    for index, package_path in enumerate(package_paths, start=1):
        family, package_format = _infer_package_kind_from_path(package_path)
        package_info = read_package_info(family, package_format, package_path)
        if pkg_name is None:
            pkg_name = package_info.pkg_name
        elif package_info.pkg_name != pkg_name:
            raise RuntimeError(
                f"package name mismatch between files: {pkg_name} vs {package_info.pkg_name}"
            )
        if package_family is None:
            package_family = family
        elif family != package_family:
            raise RuntimeError(f"cannot mix package families in direct upload: {package_family} vs {family}")
        package_key = package_path.stem
        package_records.append(
            PackageRecord(
                row_id=index,
                app_key=pkg_name,
                release_key=release_key,
                package_key=package_key,
                package_family=family,
                package_format=package_format,
                file_path=package_path,
                declared_arch=package_info.pkg_arch,
                pkg_channel=pkg_channel,
            )
        )
        package_infos.append(package_info)
    if pkg_name is None:
        raise RuntimeError("no package files supplied")
    return pkg_name, tuple(package_records), {record.package_key: info for record, info in zip(package_records, package_infos)}


def _load_release_context(
    *,
    manifest: LoadedManifest,
    release: ReleaseRecord,
    package_reader,
) -> tuple[AppRecord | None, PackageRecord, TargetRecord, DebPackageInfo]:
    package_record, target_record = _resolve_release_artifacts(manifest=manifest, release=release)
    package_info = package_reader(package_record.file_path)
    app = manifest.apps.get(release.app_key)
    return app, package_record, target_record, package_info


def _resolve_release_artifacts(*, manifest: LoadedManifest, release: ReleaseRecord) -> tuple[PackageRecord, TargetRecord]:
    package_group = manifest.packages.get((release.app_key, release.release_key))
    if not package_group:
        raise RuntimeError(f"unknown package for release: {release.app_key}/{release.release_key}")
    if len(package_group) != 1:
        raise RuntimeError(f"release has multiple packages: {release.app_key}/{release.release_key}")
    package_record = package_group[0]

    target_group = manifest.targets.get((release.app_key, release.release_key, package_record.package_key))
    if not target_group:
        raise RuntimeError(
            f"unknown target for release: {release.app_key}/{release.release_key}/{package_record.package_key}"
        )
    if len(target_group) != 1:
        raise RuntimeError(f"release has multiple targets: {release.app_key}/{release.release_key}/{package_record.package_key}")
    return package_record, target_group[0]


def _best_effort_release_deb_path(*, manifest: LoadedManifest, release: ReleaseRecord) -> Path:
    package_group = manifest.packages.get((release.app_key, release.release_key))
    if not package_group:
        return Path("")
    return package_group[0].file_path


def _build_submission_release(
    *,
    release: ReleaseRecord,
    package_record: PackageRecord,
    target_record: TargetRecord,
    package_info: DebPackageInfo,
) -> SubmissionRelease:
    return SubmissionRelease(
        row_id=release.row_id,
        app_key=release.app_key,
        release_key=release.release_key,
        release_name=release.release_name,
        region=release.region,
        note=release.note,
        system_platform=target_record.sup_sys_code,
        arch=package_record.declared_arch or package_info.pkg_arch,
        baseline=target_record.baseline_id,
        deb_path=package_record.file_path,
    )


def _validate_preflight_payload(
    *,
    app: AppRecord,
    release: SubmissionRelease,
    package_info: DebPackageInfo,
    target_app_id: str,
) -> None:
    build_submit_payload(
        app=app,
        release=release,
        package_info=package_info,
        uploads=_build_placeholder_uploads(app=app, release=release, package_info=package_info),
        target_app_id=target_app_id,
    )


def _result_for_release(
    *,
    release: SubmissionRelease,
    status: str,
    message: str,
    app_id: str = "",
    package_info: DebPackageInfo | None = None,
    selector: str = "",
) -> RowResult:
    return RowResult(
        row_id=release.row_id,
        app_key=release.app_key,
        deb_path=release.deb_path,
        status=status,
        message=message,
        app_id=app_id,
        pkg_name="" if package_info is None else package_info.pkg_name,
        pkg_version="" if package_info is None else package_info.pkg_version,
        selector=selector,
    )


def _result_for_package(
    *,
    package: PackageRecord,
    status: str,
    message: str,
    app_id: str = "",
    package_info: DebPackageInfo | None = None,
    selector: str = "",
) -> RowResult:
    return RowResult(
        row_id=package.row_id,
        app_key=package.app_key,
        deb_path=package.file_path,
        status=status,
        message=message,
        app_id=app_id,
        pkg_name="" if package_info is None else package_info.pkg_name,
        pkg_version="" if package_info is None else package_info.pkg_version,
        selector=selector,
    )


def _selected_releases(manifest: LoadedManifest, row_filter: set[int] | None = None) -> list[ReleaseRecord]:
    selected_rows = row_filter or set()
    return [
        release
        for release in sorted(manifest.releases.values(), key=lambda item: item.row_id)
        if not selected_rows or release.row_id in selected_rows
    ]


def _release_matches_row_filter(
    manifest: LoadedManifest,
    release: ReleaseRecord,
    selected_rows: GroupedRowFilter,
) -> bool:
    packages = manifest.packages.get((release.app_key, release.release_key), ())
    if packages:
        return any(package.row_id in selected_rows.package_row_ids for package in packages)
    return release.row_id in selected_rows.release_row_ids


def _selected_grouped_releases(
    manifest: LoadedManifest,
    row_filter: GroupedRowFilter | None = None,
) -> list[ReleaseRecord]:
    selected_rows = row_filter or GroupedRowFilter(package_row_ids=set(), release_row_ids=set())
    return [
        release
        for release in sorted(manifest.releases.values(), key=lambda item: item.row_id)
        if (
            not selected_rows.package_row_ids and not selected_rows.release_row_ids
        )
        or _release_matches_row_filter(manifest, release, selected_rows)
    ]


def _selected_packages(manifest: LoadedManifest, release: ReleaseRecord) -> tuple[PackageRecord, ...]:
    return tuple(manifest.packages.get((release.app_key, release.release_key), ()))


def _selected_targets(manifest: LoadedManifest, release: ReleaseRecord, package: PackageRecord) -> tuple[TargetRecord, ...]:
    return tuple(manifest.targets.get((release.app_key, release.release_key, package.package_key), ()))


def _package_info_by_key(packages: tuple[PackageRecord, ...]) -> dict[str, DebPackageInfo]:
    return {package.package_key: inspect_package(package) for package in packages}


def _validated_release_for_manifest(
    *,
    manifest: LoadedManifest,
    release: ReleaseRecord,
    capability_cache,
) -> tuple[AppRecord, tuple[PackageRecord, ...], dict[str, DebPackageInfo], object]:
    app = manifest.apps.get(release.app_key)
    if app is None:
        raise RuntimeError(f"unknown app_key: {release.app_key}")

    packages = _selected_packages(manifest, release)
    targets_by_package = {
        package.package_key: _selected_targets(manifest, release, package) for package in packages
    }
    package_infos = _package_info_by_key(packages)
    validated_release = validate_release_group(
        app=app,
        release=release,
        packages=packages,
        targets_by_package=targets_by_package,
        inspected_by_package=package_infos,
        capability_cache=capability_cache,
    )
    return app, packages, package_infos, validated_release


def _group_results_for_packages(
    *,
    packages: tuple[PackageRecord, ...],
    package_infos: dict[str, DebPackageInfo],
    status: str,
    message: str,
    app_id: str = "",
) -> list[RowResult]:
    return [
        _result_for_package(
            package=package,
            status=status,
            message=message,
            app_id=app_id,
            package_info=package_infos.get(package.package_key),
            selector=str(package.row_id),
        )
        for package in sorted(packages, key=lambda item: item.row_id)
    ]


def _failure_results_for_release(
    *,
    manifest: LoadedManifest,
    release: ReleaseRecord,
    packages: tuple[PackageRecord, ...],
    package_infos: dict[str, DebPackageInfo],
    status: str,
    message: str,
    app_id: str = "",
) -> list[RowResult]:
    if packages:
        return _group_results_for_packages(
            packages=packages,
            package_infos=package_infos,
            status=status,
            message=message,
            app_id=app_id,
        )
    return [
        RowResult(
            row_id=release.row_id,
            app_key=release.app_key,
            deb_path=manifest.workbook_path,
            status=status,
            message=message,
            app_id=app_id,
            selector=f"r:{release.row_id}",
        )
    ]


def _write_manifest_failure(output_dir: Path, workbook: Path, message: str, status: str = "workbook_failed") -> int:
    _write_reports(
        output_dir,
        [
            RowResult(
                row_id=0,
                app_key="",
                deb_path=workbook,
                status=status,
                message=message,
            )
        ],
    )
    return 1


def _resolve_credentials(username: str | None, password: str | None) -> tuple[str, str]:
    resolved_username = (username or os.environ.get("APPSTORE_USERNAME", "")).strip()
    resolved_password = password or os.environ.get("APPSTORE_PASSWORD", "")
    if not resolved_username:
        resolved_username = input("Username: ").strip()
    if not resolved_password:
        resolved_password = getpass.getpass("Password: ")
    return resolved_username, resolved_password


def _run_sync_capabilities(args) -> int:
    username, password = _resolve_credentials(args.username, args.password)
    client = AppStoreClient()
    client.login(username, password)
    sync_capabilities_to_cache(client, Path(args.cache_dir))
    return 0


def _run_validate(args) -> int:
    output_dir = Path(args.output_dir) if args.output_dir else Path("appstore/output") / _timestamp_label()
    workbook = Path(args.workbook)
    try:
        manifest = load_manifest(workbook)
    except Exception as exc:
        return _write_manifest_failure(output_dir, workbook, str(exc))

    try:
        capability_cache = load_capability_cache(args.capabilities_cache)
    except Exception as exc:
        return _write_manifest_failure(output_dir, workbook, f"capability cache failed: {exc}", status="cache_failed")

    results: list[RowResult] = []
    for release in _selected_grouped_releases(manifest, _parse_grouped_row_filter(args.rows)):
        try:
            _app, packages, package_infos, validated_release = _validated_release_for_manifest(
                manifest=manifest,
                release=release,
                capability_cache=capability_cache,
            )
            results.extend(
                _group_results_for_packages(
                    packages=packages,
                    package_infos={package.package_key: validated_package.package_info for package, validated_package in zip(packages, validated_release.packages)},
                    status="validated",
                    message="validated",
                )
            )
        except Exception as exc:
            packages = _selected_packages(manifest, release)
            package_infos: dict[str, DebPackageInfo] = {}
            try:
                package_infos = _package_info_by_key(packages)
            except Exception:
                package_infos = {}
            results.extend(
                _failure_results_for_release(
                    manifest=manifest,
                    release=release,
                    packages=packages,
                    package_infos=package_infos,
                    status="validate_failed",
                    message=str(exc),
                )
            )
    _write_reports(output_dir, results)
    return 0


def _build_app_uploads(client, app: AppRecord) -> dict[str, UploadedFileRef | tuple[UploadedFileRef, ...]]:
    return {
        "icon": client.upload_file_bytes(
            filename=app.icon_path.name,
            data=app.icon_path.read_bytes(),
            upload_type="icon",
        ),
        "screenshots": tuple(
            client.upload_file_bytes(
                filename=screenshot_path.name,
                data=screenshot_path.read_bytes(),
                upload_type="image",
            )
            for screenshot_path in app.screenshot_paths
        ),
    }


def _build_package_uploads(client, packages: tuple[PackageRecord, ...]) -> dict[str, UploadedFileRef]:
    return {
        package.package_key: client.upload_file_bytes(
            filename=package.file_path.name,
            data=package.file_path.read_bytes(),
            upload_type="temppkg",
        )
        for package in packages
    }


def _run_upload(args) -> int:
    output_dir = Path(args.output_dir) if args.output_dir else Path("appstore/output") / _timestamp_label()
    workbook = Path(args.workbook)
    row_filter = _parse_grouped_row_filter(args.rows)
    try:
        manifest = load_manifest(workbook)
    except Exception as exc:
        return _write_manifest_failure(output_dir, workbook, str(exc))

    try:
        capability_cache = load_capability_cache(args.capabilities_cache)
    except Exception as exc:
        return _write_manifest_failure(output_dir, workbook, f"capability cache failed: {exc}", status="cache_failed")

    selected_releases = _selected_grouped_releases(manifest, row_filter)
    if args.dry_run:
        results: list[RowResult] = []
        for release in selected_releases:
            try:
                _app, packages, package_infos, validated_release = _validated_release_for_manifest(
                    manifest=manifest,
                    release=release,
                    capability_cache=capability_cache,
                )
                results.extend(
                    _group_results_for_packages(
                        packages=packages,
                        package_infos={package.package_key: validated_package.package_info for package, validated_package in zip(packages, validated_release.packages)},
                        status="dry_run",
                        message="dry-run: validated grouped release",
                    )
                )
            except Exception as exc:
                packages = _selected_packages(manifest, release)
                package_infos: dict[str, DebPackageInfo] = {}
                try:
                    package_infos = _package_info_by_key(packages)
                except Exception:
                    package_infos = {}
                results.extend(
                    _failure_results_for_release(
                        manifest=manifest,
                        release=release,
                        packages=packages,
                        package_infos=package_infos,
                        status="submit_failed",
                        message=str(exc),
                    )
                )
        _write_reports(output_dir, results)
        return 0

    username, password = _resolve_credentials(args.username, args.password)
    client = AppStoreClient()
    try:
        client.login(username, password)
    except Exception as exc:
        message = str(exc)
        if not isinstance(exc, AuthenticationError):
            message = f"{exc.__class__.__name__}: {message}"
        results: list[RowResult] = []
        for release in selected_releases:
            packages = _selected_packages(manifest, release)
            package_infos: dict[str, DebPackageInfo] = {}
            try:
                package_infos = _package_info_by_key(packages)
            except Exception:
                package_infos = {}
            results.extend(
                _failure_results_for_release(
                    manifest=manifest,
                    release=release,
                    packages=packages,
                    package_infos=package_infos,
                    status="auth_failed",
                    message=message,
                )
            )
        _write_reports(output_dir, results)
        return 0

    browser_runner = BrowserSubmissionRunner(
        username=username,
        password=password,
        session_cache_dir=args.session_cache_dir,
        headless=args.headless,
    )
    results: list[RowResult] = []
    app_id_cache: dict[str, str] = {}
    app_entry_cache: dict[str, dict] = {}
    app_detail_cache: dict[str, dict] = {}
    for release in selected_releases:
        try:
            app, packages, package_infos, validated_release = _validated_release_for_manifest(
                manifest=manifest,
                release=release,
                capability_cache=capability_cache,
            )
            target_app_id = _resolve_target_app_id(client, app, app_id_cache, app_entry_cache)
            mode = decide_execution_mode(release=release, cli_mode=args.mode)
            if mode == "auto":
                mode = "api"

            if mode == "browser":
                artifact_root = Path(args.artifact_dir) if args.artifact_dir else output_dir / "debug-traces" / f"{release.app_key}-{release.release_key}-{release.row_id}"
                browser_result = browser_runner.submit_release_group(
                    client=client,
                    app=app,
                    release=release,
                    packages=packages,
                    targets_by_package={
                        package.package_key: _selected_targets(manifest, release, package) for package in packages
                    },
                    target_app_id=target_app_id,
                    artifact_root=artifact_root,
                )
                resolved_app_id = getattr(browser_result, "app_id", "") or target_app_id
            else:
                existing_app_detail = _load_existing_app_detail(
                    client,
                    app,
                    target_app_id,
                    app_detail_cache,
                    app_entry_cache,
                )
                app_uploads = None if existing_app_detail is not None else _build_app_uploads(client, app)
                uploads_by_package = _build_package_uploads(client, packages)
                response = submit_grouped_release(
                    client=client,
                    validated_release=validated_release,
                    app_uploads=app_uploads,
                    uploads_by_package=uploads_by_package,
                    target_app_id=target_app_id,
                    existing_app_detail=existing_app_detail,
                )
                resolved_app_id = _extract_response_app_id(response) or target_app_id
            if resolved_app_id:
                app_id_cache[app.app_key] = resolved_app_id
            results.extend(
                _group_results_for_packages(
                    packages=packages,
                    package_infos={package.package_key: validated_package.package_info for package, validated_package in zip(packages, validated_release.packages)},
                    status="submitted",
                    message="submitted",
                    app_id=resolved_app_id,
                )
            )
        except Exception as exc:
            packages = _selected_packages(manifest, release)
            package_infos: dict[str, DebPackageInfo] = {}
            try:
                package_infos = _package_info_by_key(packages)
            except Exception:
                package_infos = {}
            results.extend(
                _failure_results_for_release(
                    manifest=manifest,
                    release=release,
                    packages=packages,
                    package_infos=package_infos,
                    status="submit_failed",
                    message=str(exc),
                )
            )

    _write_reports(output_dir, results)
    return 0


def _run_upload_packages(args) -> int:
    output_dir = Path(args.output_dir) if args.output_dir else Path("appstore/output") / _timestamp_label()
    package_paths = [Path(path) for path in args.packages]
    results: list[RowResult] = []

    try:
        capability_cache = load_capability_cache(args.capabilities_cache)
    except Exception as exc:
        fallback_path = package_paths[0] if package_paths else Path("")
        _write_reports(
            output_dir,
            [
                RowResult(
                    row_id=1,
                    app_key="",
                    deb_path=fallback_path,
                    status="cache_failed",
                    message=f"capability cache failed: {exc}",
                )
            ],
        )
        return 1

    try:
        pkg_name, packages, package_infos = _build_direct_packages(
            package_paths=package_paths,
            release_key=args.release_key,
            pkg_channel=args.pkg_channel,
        )
    except Exception as exc:
        for index, package_path in enumerate(package_paths, start=1):
            results.append(
                RowResult(
                    row_id=index,
                    app_key="",
                    deb_path=package_path,
                    status="submit_failed",
                    message=str(exc),
                    selector=f"pkg:{index}",
                )
            )
        _write_reports(output_dir, results)
        return 0

    username, password = _resolve_credentials(args.username, args.password)
    client = AppStoreClient()
    try:
        client.login(username, password)
    except Exception as exc:
        message = str(exc)
        if not isinstance(exc, AuthenticationError):
            message = f"{exc.__class__.__name__}: {message}"
        for package in packages:
            package_info = package_infos[package.package_key]
            results.append(
                _result_for_package(
                    package=package,
                    status="auth_failed",
                    message=message,
                    package_info=package_info,
                    selector=f"pkg:{package.row_id}",
                )
            )
        _write_reports(output_dir, results)
        return 0

    app_stub = AppRecord(
        app_key=pkg_name,
        app_name_zh=pkg_name,
        pkg_name=pkg_name,
        category_id=0,
        website="",
        short_desc_zh="",
        full_desc_zh="",
        icon_path=Path("."),
        screenshot_paths=(),
        app_id_override=args.app_id,
    )
    app_id_cache: dict[str, str] = {}
    app_entry_cache: dict[str, dict] = {}
    app_detail_cache: dict[str, dict] = {}

    try:
        target_app_id = _resolve_target_app_id(client, app_stub, app_id_cache, app_entry_cache)
        if not target_app_id:
            raise RuntimeError(f"existing app not found for package name: {pkg_name}")
        existing_app_detail = _load_existing_app_detail(
            client,
            app_stub,
            target_app_id,
            app_detail_cache,
            app_entry_cache,
        )
        if existing_app_detail is None:
            raise RuntimeError("failed to load existing app detail")

        app = _build_direct_app_record(
            pkg_name=pkg_name,
            target_app_id=target_app_id,
            existing_app_detail=existing_app_detail,
        )
        region = args.region.strip() or str((existing_app_detail.get("app_basic_info") or {}).get("region", "") or "1")
        release = _build_direct_release(
            app_key=app.app_key,
            release_key=args.release_key,
            note=args.note,
            region=region,
            package_infos=list(package_infos.values()),
        )
        targets_by_package = {
            package.package_key: (
                _normalize_direct_target(
                    package_record=package,
                    package_info=package_infos[package.package_key],
                    existing_app_detail=existing_app_detail,
                    capability_cache=capability_cache,
                ),
            )
            for package in packages
        }
        validated_release = validate_release_group(
            app=app,
            release=release,
            packages=packages,
            targets_by_package=targets_by_package,
            inspected_by_package=package_infos,
            capability_cache=capability_cache,
        )

        mode = (args.mode or "api").strip().lower() or "api"
        if mode == "auto":
            mode = "api"

        if mode == "browser":
            browser_runner = BrowserSubmissionRunner(
                username=username,
                password=password,
                session_cache_dir=args.session_cache_dir,
                headless=args.headless,
            )
            artifact_root = (
                Path(args.artifact_dir)
                if args.artifact_dir
                else output_dir / "debug-traces" / f"{pkg_name}-{args.release_key}"
            )
            browser_result = browser_runner.submit_release_group(
                client=client,
                app=app,
                release=release,
                packages=packages,
                targets_by_package=targets_by_package,
                target_app_id=target_app_id,
                artifact_root=artifact_root,
            )
            resolved_app_id = getattr(browser_result, "app_id", "") or target_app_id
        else:
            uploads_by_package = _build_package_uploads(client, packages)
            response = submit_grouped_release(
                client=client,
                validated_release=validated_release,
                app_uploads=None,
                uploads_by_package=uploads_by_package,
                target_app_id=target_app_id,
                existing_app_detail=existing_app_detail,
            )
            resolved_app_id = _extract_response_app_id(response) or target_app_id

        results.extend(
            _group_results_for_packages(
                packages=packages,
                package_infos={package.package_key: validated_package.package_info for package, validated_package in zip(packages, validated_release.packages)},
                status="submitted",
                message="submitted",
                app_id=resolved_app_id,
            )
        )
    except Exception as exc:
        for package in packages:
            package_info = package_infos.get(package.package_key)
            results.append(
                _result_for_package(
                    package=package,
                    status="submit_failed",
                    message=str(exc),
                    package_info=package_info,
                    selector=f"pkg:{package.row_id}",
                )
            )

    _write_reports(output_dir, results)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync-capabilities")
    sync_parser.add_argument("--cache-dir", default="appstore/cache/capabilities")
    sync_parser.add_argument("--username", default="")
    sync_parser.add_argument("--password", default="")

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("workbook")
    validate_parser.add_argument("--output-dir", default="")
    validate_parser.add_argument("--rows", default="")
    validate_parser.add_argument("--capabilities-cache", default="appstore/cache/capabilities")

    upload_parser = subparsers.add_parser("upload")
    upload_parser.add_argument("workbook")
    upload_parser.add_argument("--output-dir", default="")
    upload_parser.add_argument("--rows", default="")
    upload_parser.add_argument("--dry-run", action="store_true")
    upload_parser.add_argument("--capabilities-cache", default="appstore/cache/capabilities")
    upload_parser.add_argument("--username", default="")
    upload_parser.add_argument("--password", default="")
    upload_parser.add_argument("--mode", choices=("auto", "api", "browser"), default="auto")
    upload_parser.add_argument("--session-cache-dir", default="appstore/cache/session-state")
    upload_parser.add_argument("--artifact-dir", default="")
    upload_parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)

    upload_packages_parser = subparsers.add_parser("upload-packages")
    upload_packages_parser.add_argument("packages", nargs="+")
    upload_packages_parser.add_argument("--output-dir", default="")
    upload_packages_parser.add_argument("--capabilities-cache", default="appstore/cache/capabilities")
    upload_packages_parser.add_argument("--username", default="")
    upload_packages_parser.add_argument("--password", default="")
    upload_packages_parser.add_argument("--mode", choices=("auto", "api", "browser"), default="api")
    upload_packages_parser.add_argument("--session-cache-dir", default="appstore/cache/session-state")
    upload_packages_parser.add_argument("--artifact-dir", default="")
    upload_packages_parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    upload_packages_parser.add_argument("--app-id", default="")
    upload_packages_parser.add_argument("--note", default="")
    upload_packages_parser.add_argument("--release-key", default="direct-update")
    upload_packages_parser.add_argument("--pkg-channel", default="")
    upload_packages_parser.add_argument("--region", default="")

    template_parser = subparsers.add_parser("generate-template")
    template_parser.add_argument("output_path", nargs="?", default="appstore/examples/template.xlsx")
    template_parser.add_argument("--capabilities-cache", default="appstore/cache/capabilities")
    return parser


def run_batch(
    *,
    manifest: LoadedManifest,
    client,
    username: str,
    password: str,
    output_dir: Path | str,
    package_reader=read_deb_package_info,
    dry_run: bool,
    row_filter: set[int] | None = None,
) -> list[RowResult]:
    output_dir = Path(output_dir)
    selected_rows = row_filter or set()
    selected_releases = [
        release for release in manifest.releases.values() if not selected_rows or release.row_id in selected_rows
    ]

    if dry_run:
        results: list[RowResult] = []
        for release in selected_releases:
            package_info = None
            package_record = None
            target_record = None
            submission_release = SubmissionRelease(
                row_id=release.row_id,
                app_key=release.app_key,
                release_key=release.release_key,
                release_name=release.release_name,
                region=release.region,
                note=release.note,
                system_platform="",
                arch="",
                baseline="",
                deb_path=Path(""),
            )
            try:
                package_record, target_record = _resolve_release_artifacts(manifest=manifest, release=release)
                package_info = package_reader(package_record.file_path)
                app = manifest.apps.get(release.app_key)
                if app is None:
                    raise RuntimeError(f"unknown app_key: {release.app_key}")
                if package_info.pkg_name != app.pkg_name:
                    raise RuntimeError(f"package name mismatch: expected {app.pkg_name}, got {package_info.pkg_name}")
                submission_release = _build_submission_release(
                    release=release,
                    package_record=package_record,
                    target_record=target_record,
                    package_info=package_info,
                )
                _validate_preflight_payload(
                    app=app,
                    release=submission_release,
                    package_info=package_info,
                    target_app_id="",
                )
                results.append(
                    _result_for_release(
                        release=submission_release,
                        status="dry_run",
                        message="dry-run: validated locally",
                        package_info=package_info,
                    )
                )
            except Exception as exc:
                if package_record is not None and target_record is not None:
                    submission_release = _build_submission_release(
                        release=release,
                        package_record=package_record,
                        target_record=target_record,
                        package_info=package_info or DebPackageInfo(
                            pkg_name="",
                            pkg_version="",
                            pkg_arch="",
                            pkg_size=0,
                            sha256="",
                            deb_path=package_record.file_path,
                        ),
                    )
                results.append(
                    _result_for_release(
                        release=submission_release,
                        status="submit_failed",
                        message=str(exc),
                        package_info=package_info,
                    )
                )
        _write_reports(output_dir, results)
        return results

    try:
        client.login(username, password)
    except Exception as exc:
        message = str(exc)
        if not isinstance(exc, AuthenticationError):
            message = f"{exc.__class__.__name__}: {message}"
        results = []
        for release in selected_releases:
            results.append(
                RowResult(
                    row_id=release.row_id,
                    app_key=release.app_key,
                    deb_path=_best_effort_release_deb_path(manifest=manifest, release=release),
                    status="auth_failed",
                    message=message,
                )
            )
        _write_reports(output_dir, results)
        return results

    results: list[RowResult] = []
    app_id_cache: dict[str, str] = {}
    app_entry_cache: dict[str, dict] = {}
    app_detail_cache: dict[str, dict] = {}
    for release in selected_releases:
        package_info = None
        package_record = None
        target_record = None
        submission_release = SubmissionRelease(
            row_id=release.row_id,
            app_key=release.app_key,
            release_key=release.release_key,
            release_name=release.release_name,
            region=release.region,
            note=release.note,
            system_platform="",
            arch="",
            baseline="",
            deb_path=Path(""),
        )
        try:
            package_record, target_record = _resolve_release_artifacts(manifest=manifest, release=release)
            package_info = package_reader(package_record.file_path)
            app = manifest.apps.get(release.app_key)
            if app is None:
                raise RuntimeError(f"unknown app_key: {release.app_key}")
            submission_release = _build_submission_release(
                release=release,
                package_record=package_record,
                target_record=target_record,
                package_info=package_info,
            )
            if package_info.pkg_name != app.pkg_name:
                raise RuntimeError(f"package name mismatch: expected {app.pkg_name}, got {package_info.pkg_name}")
            target_app_id = _resolve_target_app_id(client, app, app_id_cache, app_entry_cache)
            existing_app_detail = _load_existing_app_detail(
                client,
                app,
                target_app_id,
                app_detail_cache,
                app_entry_cache,
            )
            _validate_preflight_payload(
                app=app,
                release=submission_release,
                package_info=package_info,
                target_app_id=target_app_id,
            )
            uploads = {
                "package": client.upload_file_bytes(
                    filename=package_record.file_path.name,
                    data=package_record.file_path.read_bytes(),
                    upload_type="temppkg",
                ),
            }
            if existing_app_detail is None:
                uploads["icon"] = client.upload_file_bytes(
                    filename=app.icon_path.name,
                    data=app.icon_path.read_bytes(),
                    upload_type="icon",
                )
                uploads["screenshots"] = tuple(
                    client.upload_file_bytes(
                        filename=screenshot_path.name,
                        data=screenshot_path.read_bytes(),
                        upload_type="image",
                    )
                    for screenshot_path in app.screenshot_paths
                )
            payload = build_submit_payload(
                app=app,
                release=submission_release,
                package_info=package_info,
                uploads=uploads,
                target_app_id=target_app_id,
                existing_app_detail=existing_app_detail,
            )
            response = _submit_payload(client, payload)
            response_app_id = _extract_response_app_id(response)
            resolved_app_id = response_app_id or target_app_id
            if not resolved_app_id:
                try:
                    resolved_app_id = choose_target_app_id(client.find_apps_by_pkg_name(app.pkg_name), "")
                except Exception:
                    resolved_app_id = ""
            if resolved_app_id:
                app_id_cache[release.app_key] = resolved_app_id
            results.append(
                _result_for_release(
                    release=submission_release,
                    status="submitted",
                    message="submitted",
                    app_id=resolved_app_id,
                    package_info=package_info,
                )
            )
        except Exception as exc:
            if package_record is not None and target_record is not None:
                submission_release = SubmissionRelease(
                    row_id=release.row_id,
                    app_key=release.app_key,
                    release_key=release.release_key,
                    release_name=release.release_name,
                    region=release.region,
                    note=release.note,
                    system_platform=target_record.sup_sys_code,
                    arch=package_record.declared_arch,
                    baseline=target_record.baseline_id,
                    deb_path=package_record.file_path,
                )
            results.append(
                _result_for_release(release=submission_release, status="submit_failed", message=str(exc), package_info=package_info)
            )

    _write_reports(output_dir, results)
    return results


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "sync-capabilities":
        return _run_sync_capabilities(args)
    if args.command == "validate":
        return _run_validate(args)
    if args.command == "upload":
        return _run_upload(args)
    if args.command == "upload-packages":
        return _run_upload_packages(args)
    if args.command == "generate-template":
        generate_template(args.output_path, capability_cache_path=args.capabilities_cache)
        return 0
    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
