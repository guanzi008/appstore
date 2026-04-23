from __future__ import annotations

from pathlib import Path

from appstore.deb import read_deb_package_info
from appstore.linglong import read_layer_package_info, read_uab_package_info
from appstore.models import PackageInfo, PackageRecord


def inspect_package(package: PackageRecord) -> PackageInfo:
    normalized_family = package.package_family.strip().lower()
    normalized_format = package.package_format.strip().lower()

    if normalized_family == "deb" and normalized_format == "deb":
        return read_deb_package_info(package.file_path)
    if normalized_family == "linglong" and normalized_format == "uab":
        return read_uab_package_info(package.file_path)
    if normalized_family == "linglong" and normalized_format == "layer":
        return read_layer_package_info(package.file_path)
    raise ValueError(f"unsupported package family/format: {package.package_family}/{package.package_format}")


def read_package_info(package_family: str, package_format: str, package_path: Path | str) -> PackageInfo:
    package = PackageRecord(
        row_id=0,
        app_key="",
        release_key="",
        package_key="",
        package_family=package_family,
        package_format=package_format,
        file_path=Path(package_path),
    )
    return inspect_package(package)
