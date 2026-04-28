from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from appstore.inspectors import read_package_info
from appstore.linglong import (
    LinglongParseError,
    native_layer_payload_offset,
    read_linglong_metadata as read_linglong_package_metadata,
)


PACKAGE_SUFFIXES = {
    ".deb": ("deb", "deb"),
    ".uab": ("linglong", "uab"),
    ".layer": ("linglong", "layer"),
}


@dataclass(frozen=True)
class PackageMetadata:
    path: Path
    package_family: str
    package_format: str
    pkg_name: str
    pkg_version: str
    pkg_arch: str
    pkg_size: int
    sha256: str
    display_name: str
    short_description: str
    full_description: str
    homepage: str


@dataclass(frozen=True)
class PackageGroup:
    packages: tuple[PackageMetadata, ...]

    @property
    def pkg_name(self) -> str:
        return self.packages[0].pkg_name

    @property
    def package_family(self) -> str:
        return self.packages[0].package_family

    @property
    def package_format(self) -> str:
        return self.packages[0].package_format

    @property
    def pkg_version(self) -> str:
        return self.packages[0].pkg_version

    @property
    def pkg_arches(self) -> tuple[str, ...]:
        return tuple(package.pkg_arch for package in self.packages)

    @property
    def display_name(self) -> str:
        for package in self.packages:
            if package.display_name.strip():
                return package.display_name.strip()
        return self.pkg_name

    @property
    def short_description(self) -> str:
        for package in self.packages:
            if package.short_description.strip():
                return package.short_description.strip()
        return f"{self.display_name} 应用程序"

    @property
    def full_description(self) -> str:
        for package in self.packages:
            if package.full_description.strip():
                return package.full_description.strip()
        return self.short_description

    @property
    def homepage(self) -> str:
        for package in self.packages:
            if package.homepage.strip():
                return package.homepage.strip()
        return ""


def infer_package_kind(package_path: Path | str) -> tuple[str, str]:
    target = Path(package_path)
    try:
        return PACKAGE_SUFFIXES[target.suffix.lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported package format: {target.name}") from exc


def find_package_files(directory: Path | str) -> tuple[Path, ...]:
    root = Path(directory).expanduser().resolve()
    candidates = [
        path
        for path in sorted(root.iterdir())
        if path.is_file() and path.suffix.lower() in PACKAGE_SUFFIXES
    ]
    return tuple(candidates)


def analyze_package_group(paths: list[str] | list[Path] | tuple[str, ...] | tuple[Path, ...]) -> PackageGroup:
    if not paths:
        raise ValueError("at least one package path is required")

    packages = [analyze_package(path) for path in paths]
    _validate_package_metadata_group(packages)
    packages.sort(key=lambda item: (_arch_sort_key(item.pkg_arch), item.pkg_arch, item.path.name))
    return PackageGroup(packages=tuple(packages))


def analyze_package_groups(
    paths: list[str] | list[Path] | tuple[str, ...] | tuple[Path, ...],
) -> tuple[PackageGroup, ...]:
    if not paths:
        raise ValueError("at least one package path is required")
    packages = [analyze_package(path) for path in paths]
    grouped = _group_packages(packages)
    result: list[PackageGroup] = []
    for group_packages in grouped.values():
        _validate_package_metadata_group(group_packages)
        group_packages.sort(key=lambda item: (_arch_sort_key(item.pkg_arch), item.pkg_arch, item.path.name))
        result.append(PackageGroup(packages=tuple(group_packages)))
    result.sort(key=lambda group: (group.pkg_name, group.pkg_version, group.package_family, group.package_format))
    return tuple(result)


def filter_compatible_package_paths(
    existing_paths: list[Path] | tuple[Path, ...],
    incoming_paths: list[Path] | tuple[Path, ...],
) -> tuple[tuple[Path, ...], tuple[tuple[Path, str], ...]]:
    accepted: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    current_paths = [Path(path).expanduser().resolve() for path in existing_paths]
    for incoming in incoming_paths:
        normalized = Path(incoming).expanduser().resolve()
        try:
            analyze_package_group((*current_paths, *accepted, normalized))
        except ValueError as exc:
            skipped.append((normalized, str(exc)))
            continue
        accepted.append(normalized)
    return tuple(accepted), tuple(skipped)


def _validate_package_metadata_group(packages: list[PackageMetadata]) -> None:
    first = packages[0]
    pkg_name_groups = _group_packages_by(packages, lambda package: package.pkg_name)
    if len(pkg_name_groups) > 1:
        raise ValueError(
            "mixed package names are not supported in one submission: "
            f"{_format_group_summary(pkg_name_groups)}"
        )
    pkg_version_groups = _group_packages_by(packages, lambda package: package.pkg_version)
    if len(pkg_version_groups) > 1:
        raise ValueError(
            "mixed package versions are not supported in one submission: "
            f"{_format_group_summary(pkg_version_groups)}"
        )
    family_groups = _group_packages_by(packages, lambda package: package.package_family)
    if len(family_groups) > 1:
        raise ValueError(
            "mixed package families are not supported in one submission: "
            f"{_format_group_summary(family_groups)}"
        )
    for package in packages[1:]:
        if package.pkg_name != first.pkg_name:
            raise AssertionError("unreachable pkg_name mismatch after grouped validation")
        if package.pkg_version != first.pkg_version:
            raise AssertionError("unreachable pkg_version mismatch after grouped validation")
        if package.package_family != first.package_family:
            raise AssertionError("unreachable package_family mismatch after grouped validation")


def _group_packages_by(
    packages: list[PackageMetadata],
    key_fn,
) -> dict[str, list[PackageMetadata]]:
    grouped: dict[str, list[PackageMetadata]] = {}
    for package in packages:
        key = str(key_fn(package)).strip() or "-"
        grouped.setdefault(key, []).append(package)
    return grouped


def _group_packages(packages: list[PackageMetadata]) -> dict[tuple[str, str, str, str], list[PackageMetadata]]:
    grouped: dict[tuple[str, str, str, str], list[PackageMetadata]] = {}
    for package in packages:
        key = (
            package.pkg_name.strip(),
            package.pkg_version.strip(),
            package.package_family.strip(),
            package.package_format.strip(),
        )
        grouped.setdefault(key, []).append(package)
    return grouped


def _format_group_summary(grouped: dict[str, list[PackageMetadata]]) -> str:
    parts: list[str] = []
    for key in sorted(grouped):
        filenames = ", ".join(sorted(package.path.name for package in grouped[key]))
        parts.append(f"{key} [{filenames}]")
    return "; ".join(parts)


def analyze_package(package_path: Path | str) -> PackageMetadata:
    target = Path(package_path).expanduser().resolve()
    package_family, package_format = infer_package_kind(target)
    package_info = read_package_info(package_family, package_format, target)
    if package_family == "deb":
        extra = _read_deb_extra_metadata(target)
    else:
        extra = _read_linglong_extra_metadata(target)
    display_name = extra.get("display_name", "").strip() or package_info.pkg_name
    short_description = extra.get("short_description", "").strip()
    full_description = extra.get("full_description", "").strip() or short_description
    homepage = extra.get("homepage", "").strip()
    return PackageMetadata(
        path=target,
        package_family=package_family,
        package_format=package_format,
        pkg_name=package_info.pkg_name,
        pkg_version=package_info.pkg_version,
        pkg_arch=package_info.pkg_arch,
        pkg_size=package_info.pkg_size,
        sha256=package_info.sha256,
        display_name=display_name,
        short_description=short_description or f"{display_name} 应用程序",
        full_description=full_description or short_description or f"{display_name} 应用程序",
        homepage=homepage,
    )


def extract_deb_icon(package_path: Path, *, pkg_name: str, output_dir: Path) -> Path | None:
    with tempfile.TemporaryDirectory(prefix="appstore-ui-deb-") as temp_dir:
        extracted_root = Path(temp_dir)
        subprocess.run(
            ["dpkg-deb", "-x", str(package_path), str(extracted_root)],
            check=True,
            capture_output=True,
            text=True,
        )
        return _extract_icon_from_tree(extracted_root, pkg_name=pkg_name, output_dir=output_dir)


def extract_archive_icon(package_path: Path, *, pkg_name: str, output_dir: Path) -> Path | None:
    with tempfile.TemporaryDirectory(prefix="appstore-ui-archive-") as temp_dir:
        extracted_root = Path(temp_dir)
        if zipfile.is_zipfile(package_path):
            with zipfile.ZipFile(package_path) as archive:
                archive.extractall(extracted_root)
        elif tarfile.is_tarfile(package_path):
            with tarfile.open(package_path) as archive:
                archive.extractall(extracted_root)
        else:
            return _extract_native_linglong_icon(package_path, pkg_name=pkg_name, output_dir=output_dir)
        return _extract_icon_from_tree(extracted_root, pkg_name=pkg_name, output_dir=output_dir)


def _read_deb_extra_metadata(package_path: Path) -> dict[str, str]:
    completed = subprocess.run(
        ["dpkg-deb", "-f", str(package_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    fields = _parse_rfc822_fields(completed.stdout)
    summary, description = _split_control_description(fields.get("Description", ""))
    homepage = (fields.get("Homepage", "") or fields.get("HomePage", "")).strip()
    display_name = fields.get("X-AppName", "").strip() or fields.get("Package", "").strip()
    return {
        "display_name": display_name,
        "short_description": summary,
        "full_description": description,
        "homepage": homepage,
    }


def _read_linglong_extra_metadata(package_path: Path) -> dict[str, str]:
    metadata = _read_linglong_metadata(package_path)
    app_info = _pick_linglong_app_info(metadata)
    display_name = _first_text(
        app_info,
        keys=("name", "displayName", "display_name", "title"),
    )
    description = _first_text(
        app_info,
        keys=("description", "desc", "summary", "intro"),
    )
    homepage = _first_text(
        app_info,
        keys=("homepage", "homePage", "website", "url"),
    )
    return {
        "display_name": display_name,
        "short_description": description.splitlines()[0].strip() if description.strip() else "",
        "full_description": description,
        "homepage": homepage,
    }


def _parse_rfc822_fields(payload: str) -> dict[str, str]:
    fields: dict[str, list[str]] = {}
    current_key = ""
    for raw_line in payload.splitlines():
        if not raw_line:
            continue
        if raw_line[0].isspace() and current_key:
            fields[current_key].append(raw_line[1:])
            continue
        key, separator, value = raw_line.partition(":")
        if not separator:
            continue
        current_key = key.strip()
        fields[current_key] = [value.lstrip()]
    return {
        key: "\n".join(values).rstrip()
        for key, values in fields.items()
    }


def _split_control_description(raw_description: str) -> tuple[str, str]:
    lines = [line.rstrip() for line in raw_description.splitlines()]
    if not lines:
        return "", ""
    summary = lines[0].strip()
    detail_lines: list[str] = []
    for line in lines[1:]:
        normalized = line.strip()
        if normalized == ".":
            detail_lines.append("")
            continue
        detail_lines.append(normalized)
    detail = "\n".join(detail_lines).strip()
    if detail:
        return summary, f"{summary}\n\n{detail}"
    return summary, summary


def _read_linglong_metadata(package_path: Path) -> dict[str, Any]:
    return read_linglong_package_metadata(package_path)


def _choose_linglong_metadata_name(names) -> str:
    normalized = [_normalize_archive_name(name) for name in names]
    for candidate in ("info.json", "linglong.meta"):
        if candidate in normalized:
            return candidate
    raise ValueError("linglong metadata file not found")


def _normalize_archive_name(name: str) -> str:
    return str(name).strip().lstrip("./").rstrip("/")


def _pick_linglong_app_info(metadata: dict[str, Any]) -> dict[str, Any]:
    layers = metadata.get("layers")
    if isinstance(layers, list):
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            info = layer.get("info")
            if not isinstance(info, dict):
                continue
            if str(info.get("kind", "")).strip().lower() == "app":
                return info
    info = metadata.get("info")
    if isinstance(info, dict) and str(info.get("kind", "")).strip().lower() == "app":
        return info
    return metadata


def _first_text(mapping: dict[str, Any], *, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, dict):
            nested = _first_text(value, keys=("zh_CN", "zh-CN", "zh", "en_US", "en"))
            if nested:
                return nested
    return ""


def _arch_sort_key(arch: str) -> tuple[int, str]:
    order = {"amd64": 0, "x86_64": 0, "arm64": 1, "aarch64": 1, "loong64": 2, "loongarch64": 2}
    normalized = arch.strip().lower()
    return order.get(normalized, 99), normalized


IMAGE_ICON_SUFFIXES = (".png", ".svg", ".webp", ".jpg", ".jpeg", ".bmp", ".ico")
THEMED_ICON_SIZES = ("1024x1024", "512x512", "256x256", "192x192", "128x128", "96x96", "64x64", "48x48", "32x32", "24x24", "22x22", "16x16", "scalable")


def _extract_icon_from_tree(root: Path, *, pkg_name: str, output_dir: Path) -> Path | None:
    desktop_icon_names = _read_desktop_icon_names(root)
    best = _find_packaged_desktop_icon(root, desktop_icon_names)
    icon_names = _normalized_native_icon_names((*desktop_icon_names, *_read_store_appids(root), pkg_name))
    if best is None:
        best = _find_packaged_desktop_icon(root, icon_names)
    if best is None:
        best = _find_limited_fallback_icon(root, pkg_name=pkg_name)
    if best is None:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"extracted-icon{best.suffix.lower()}"
    shutil.copy2(best, target)
    return target

def _find_packaged_desktop_icon(root: Path, icon_names: tuple[str, ...]) -> Path | None:
    for icon_name in icon_names:
        icon_path = Path(icon_name)
        if icon_path.is_absolute():
            candidate = root / icon_path.relative_to("/")
            if _is_icon_file(candidate):
                return candidate
        if "/" in icon_name:
            candidate = root / icon_name
            if _is_icon_file(candidate):
                return candidate

    search_roots = _packaged_icon_search_roots(root)
    for icon_name in icon_names:
        for base in search_roots:
            if not base.exists():
                continue
            for candidate in _themed_icon_candidates(base, icon_name):
                if _is_icon_file(candidate):
                    return candidate
    return None


def _packaged_icon_search_roots(root: Path) -> tuple[Path, ...]:
    roots: list[Path] = []
    apps_root = root / "opt/apps"
    if apps_root.exists():
        roots.extend(sorted(path for path in apps_root.glob("*/entries") if path.is_dir()))
        roots.extend(sorted(path for path in apps_root.glob("*/files/share") if path.is_dir()))
    roots.extend(
        [
            root / "entries",
            root / "files/share",
            root / "usr/share",
            root / "usr/local/share",
        ]
    )
    return tuple(roots)


def _themed_icon_candidates(base: Path, icon_name: str) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for size in THEMED_ICON_SIZES:
        for suffix in IMAGE_ICON_SUFFIXES:
            candidates.append(base / "icons" / "hicolor" / size / "apps" / f"{icon_name}{suffix}")
            candidates.append(base / "icons" / "hicolor" / size / f"{icon_name}{suffix}")
    for suffix in IMAGE_ICON_SUFFIXES:
        candidates.append(base / "pixmaps" / f"{icon_name}{suffix}")
    return tuple(candidates)


def _find_limited_fallback_icon(root: Path, *, pkg_name: str) -> Path | None:
    fallback_names = _normalized_native_icon_names((pkg_name, "icon"))
    return _find_packaged_desktop_icon(root, fallback_names)


def _is_icon_file(path: Path) -> bool:
    return path.exists() and path.is_file() and path.suffix.lower() in IMAGE_ICON_SUFFIXES and path.stat().st_size > 0


def _read_desktop_icon_names(root: Path) -> tuple[str, ...]:
    names: list[str] = []
    for desktop_file in _ordered_desktop_files(root):
        try:
            for line in desktop_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("Icon="):
                    value = line.split("=", 1)[1].strip()
                    if value and value not in names:
                        names.append(value)
        except OSError:
            continue
    return tuple(names)


def _ordered_desktop_files(root: Path) -> tuple[Path, ...]:
    ordered: list[Path] = []
    patterns = (
        "opt/apps/*/entries/applications/*.desktop",
        "entries/applications/*.desktop",
        "files/share/applications/*.desktop",
        "usr/share/applications/*.desktop",
        "usr/local/share/applications/*.desktop",
    )
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path not in ordered:
                ordered.append(path)
    for path in sorted(root.rglob("*.desktop")):
        if path.is_file() and path not in ordered:
            ordered.append(path)
    return tuple(ordered)


def _read_store_appids(root: Path) -> tuple[str, ...]:
    appids: list[str] = []
    for info_file in _ordered_store_info_files(root):
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(info, dict):
            continue
        appid = str(info.get("appid", "") or info.get("id", "")).strip()
        if appid and appid not in appids:
            appids.append(appid)
    apps_root = root / "opt/apps"
    if apps_root.exists():
        for app_dir in sorted(path for path in apps_root.iterdir() if path.is_dir()):
            if app_dir.name not in appids:
                appids.append(app_dir.name)
    return tuple(appids)


def _ordered_store_info_files(root: Path) -> tuple[Path, ...]:
    candidates = [root / "info"]
    apps_root = root / "opt/apps"
    if apps_root.exists():
        candidates.extend(sorted(path / "info" for path in apps_root.iterdir() if path.is_dir()))
    return tuple(path for path in candidates if path.is_file())


def _extract_native_linglong_icon(package_path: Path, *, pkg_name: str, output_dir: Path) -> Path | None:
    dump_erofs = shutil.which("dump.erofs")
    if dump_erofs is None:
        return None
    try:
        offset = native_layer_payload_offset(package_path)
    except LinglongParseError:
        return None

    desktop_icons = _read_native_linglong_desktop_icon_names(
        package_path,
        offset=offset,
        dump_erofs=dump_erofs,
    )
    direct_icon = _extract_direct_native_linglong_icon(
        package_path,
        offset=offset,
        dump_erofs=dump_erofs,
        icon_names=desktop_icons,
        output_dir=output_dir,
    )
    if direct_icon is not None:
        return direct_icon
    icon_names = _normalized_native_icon_names((pkg_name, *desktop_icons))
    for candidate_path, suffix in _native_linglong_icon_candidates(icon_names):
        icon_bytes = _dump_erofs_cat(
            package_path,
            offset=offset,
            dump_erofs=dump_erofs,
            path=candidate_path,
        )
        if not icon_bytes:
            continue
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"extracted-icon{suffix}"
        target.write_bytes(icon_bytes)
        return target
    return None


def _extract_direct_native_linglong_icon(
    package_path: Path,
    *,
    offset: int,
    dump_erofs: str,
    icon_names: tuple[str, ...],
    output_dir: Path,
) -> Path | None:
    for raw_name in icon_names:
        normalized = raw_name.strip()
        if not normalized or "/" not in normalized:
            continue
        suffix = Path(normalized).suffix.lower()
        if suffix not in IMAGE_ICON_SUFFIXES:
            continue
        candidate_path = normalized if normalized.startswith("/") else f"/{normalized.lstrip('./')}"
        icon_bytes = _dump_erofs_cat(
            package_path,
            offset=offset,
            dump_erofs=dump_erofs,
            path=candidate_path,
        )
        if not icon_bytes:
            continue
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"extracted-icon{suffix}"
        target.write_bytes(icon_bytes)
        return target
    return None


def _read_native_linglong_desktop_icon_names(package_path: Path, *, offset: int, dump_erofs: str) -> tuple[str, ...]:
    icon_names: list[str] = []
    for applications_dir in ("/entries/applications", "/files/share/applications", "/entries/share/applications", "/share/applications"):
        for filename in _dump_erofs_list(package_path, offset=offset, dump_erofs=dump_erofs, path=applications_dir):
            if not filename.endswith(".desktop"):
                continue
            desktop_payload = _dump_erofs_cat(
                package_path,
                offset=offset,
                dump_erofs=dump_erofs,
                path=f"{applications_dir}/{filename}",
            )
            if not desktop_payload:
                continue
            for line in desktop_payload.decode("utf-8", errors="ignore").splitlines():
                if not line.startswith("Icon="):
                    continue
                icon_name = line.split("=", 1)[1].strip()
                if icon_name:
                    icon_names.append(icon_name)
    return tuple(icon_names)


def _dump_erofs_list(package_path: Path, *, offset: int, dump_erofs: str, path: str) -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            [dump_erofs, f"--offset={offset}", "--ls", f"--path={path}", str(package_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ()

    filenames: list[str] = []
    for raw_line in completed.stdout.splitlines():
        parts = raw_line.split(maxsplit=2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        filename = parts[2].strip()
        if filename and filename not in {".", ".."}:
            filenames.append(filename)
    return tuple(filenames)


def _dump_erofs_cat(package_path: Path, *, offset: int, dump_erofs: str, path: str) -> bytes:
    try:
        completed = subprocess.run(
            [dump_erofs, f"--offset={offset}", "--cat", f"--path={path}", str(package_path)],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return b""
    return completed.stdout


def _normalized_native_icon_names(names: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for raw_name in names:
        name = raw_name.strip()
        if not name:
            continue
        candidate = Path(name)
        suffix = candidate.suffix.lower()
        stem = candidate.stem if suffix in IMAGE_ICON_SUFFIXES else candidate.name
        normalized = stem.strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _native_linglong_icon_candidates(icon_names: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    sizes = ("512x512", "256x256", "192x192", "128x128", "96x96", "64x64", "48x48", "32x32", "24x24", "22x22", "16x16")
    candidates: list[tuple[str, str]] = []
    for icon_name in icon_names:
        for icon_root in ("/entries/icons/hicolor", "/files/share/icons/hicolor", "/entries/share/icons/hicolor", "/share/icons/hicolor"):
            for size in sizes:
                for suffix in IMAGE_ICON_SUFFIXES:
                    candidates.append((f"{icon_root}/{size}/apps/{icon_name}{suffix}", suffix))
                    candidates.append((f"{icon_root}/{size}/{icon_name}{suffix}", suffix))
        for pixmap_root in ("/entries/pixmaps", "/files/share/pixmaps", "/entries/share/pixmaps", "/share/pixmaps"):
            for suffix in IMAGE_ICON_SUFFIXES:
                candidates.append((f"{pixmap_root}/{icon_name}{suffix}", suffix))
    return tuple(candidates)
