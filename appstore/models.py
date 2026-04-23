from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AppRecord:
    app_key: str
    app_name_zh: str
    pkg_name: str
    category_id: int
    website: str
    short_desc_zh: str
    full_desc_zh: str
    icon_path: Path
    screenshot_paths: tuple[Path, ...]
    keywords_zh: str = ""
    app_id_override: str = ""


@dataclass(frozen=True)
class ReleaseRecord:
    row_id: int
    app_key: str
    release_key: str
    release_name: str
    execution_mode: str = ""
    region: str = ""
    note: str = ""


@dataclass(frozen=True)
class PackageRecord:
    row_id: int
    app_key: str
    release_key: str
    package_key: str
    package_family: str
    package_format: str
    file_path: Path
    declared_arch: str = ""
    pkg_channel: str = ""
    note: str = ""


@dataclass(frozen=True)
class TargetRecord:
    row_id: int
    app_key: str
    release_key: str
    package_key: str
    sup_sys_code: str
    baseline_id: str = ""
    unsupport_baseline_ids: tuple[str, ...] = ()
    target_note: str = ""


@dataclass(frozen=True)
class SystemLine:
    code: str
    label: str
    family: str


@dataclass(frozen=True)
class BaselineOption:
    system_line_code: str
    baseline_id: str
    minor_version: str


@dataclass(frozen=True)
class SystemTemplate:
    column_prefix: str
    package_family: str
    sup_sys_code: str
    system_label: str
    baseline_options: tuple[BaselineOption, ...] = ()


@dataclass(frozen=True)
class CapabilityCache:
    generated_at: str
    deb_system_lines: dict[str, SystemLine]
    linglong_system_lines: dict[str, SystemLine]
    baseline_options: dict[str, tuple[BaselineOption, ...]]


@dataclass(frozen=True, init=False)
class PackageInfo:
    pkg_name: str
    pkg_version: str
    pkg_arch: str
    pkg_size: int
    sha256: str
    file_path: Path
    package_family: str
    package_format: str

    def __init__(
        self,
        *args,
        pkg_name: str | None = None,
        pkg_version: str | None = None,
        pkg_arch: str | None = None,
        pkg_size: int | None = None,
        sha256: str | None = None,
        file_path: Path | None = None,
        package_family: str = "deb",
        package_format: str = "deb",
        deb_path: Path | None = None,
    ) -> None:
        positional_names = ("pkg_name", "pkg_version", "pkg_arch", "pkg_size", "sha256", "file_path")
        if args:
            if len(args) > len(positional_names):
                raise TypeError(f"expected at most {len(positional_names)} positional arguments, got {len(args)}")
            values = {
                "pkg_name": pkg_name,
                "pkg_version": pkg_version,
                "pkg_arch": pkg_arch,
                "pkg_size": pkg_size,
                "sha256": sha256,
                "file_path": file_path,
            }
            for index, value in enumerate(args):
                name = positional_names[index]
                if values[name] is not None:
                    raise TypeError(f"{name} specified both positionally and by keyword")
                values[name] = value
            pkg_name = values["pkg_name"]
            pkg_version = values["pkg_version"]
            pkg_arch = values["pkg_arch"]
            pkg_size = values["pkg_size"]
            sha256 = values["sha256"]
            file_path = values["file_path"]

        normalized_path = file_path if file_path is not None else deb_path
        if normalized_path is None:
            raise TypeError("file_path is required")
        if pkg_name is None or pkg_version is None or pkg_arch is None or pkg_size is None or sha256 is None:
            raise TypeError("pkg_name, pkg_version, pkg_arch, pkg_size, and sha256 are required")
        object.__setattr__(self, "pkg_name", pkg_name)
        object.__setattr__(self, "pkg_version", pkg_version)
        object.__setattr__(self, "pkg_arch", pkg_arch)
        object.__setattr__(self, "pkg_size", pkg_size)
        object.__setattr__(self, "sha256", sha256)
        object.__setattr__(self, "file_path", Path(normalized_path))
        object.__setattr__(self, "package_family", package_family)
        object.__setattr__(self, "package_format", package_format)

    @property
    def deb_path(self) -> Path:
        return self.file_path


DebPackageInfo = PackageInfo


@dataclass(frozen=True)
class UploadedFileRef:
    kind: str
    file_save_key: str
    size: int
    file_hash: str


@dataclass(frozen=True)
class LoadedManifest:
    workbook_path: Path
    apps: dict[str, AppRecord]
    releases: dict[tuple[str, str], ReleaseRecord]
    packages: dict[tuple[str, str], tuple[PackageRecord, ...]] = field(default_factory=dict)
    targets: dict[tuple[str, str, str], tuple[TargetRecord, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class RowResult:
    row_id: int
    app_key: str
    deb_path: Path
    status: str
    message: str
    app_id: str = ""
    pkg_name: str = ""
    pkg_version: str = ""
    selector: str = ""
    selector: str = ""
