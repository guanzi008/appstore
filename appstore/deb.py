from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from appstore.models import PackageInfo


class DebParseError(RuntimeError):
    pass


def _parse_metadata(stdout: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata


def _hash_deb_file(deb_path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = deb_path.stat().st_size
    with deb_path.open("rb") as deb_file:
        for chunk in iter(lambda: deb_file.read(8192), b""):
            digest.update(chunk)
    return size, digest.hexdigest()


def read_deb_package_info(deb_path: Path | str) -> PackageInfo:
    deb_path = Path(deb_path)

    try:
        completed = subprocess.run(
            ["dpkg-deb", "-f", str(deb_path), "Package", "Version", "Architecture"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise DebParseError(f"failed to parse deb metadata for {deb_path}") from exc

    metadata = _parse_metadata(completed.stdout)
    try:
        pkg_name = metadata["Package"]
        pkg_version = metadata["Version"]
        pkg_arch = metadata["Architecture"]
    except KeyError as exc:
        raise DebParseError(f"missing required deb metadata in {deb_path}") from exc

    pkg_size, sha256 = _hash_deb_file(deb_path)
    return PackageInfo(
        pkg_name=pkg_name,
        pkg_version=pkg_version,
        pkg_arch=pkg_arch,
        pkg_size=pkg_size,
        sha256=sha256,
        file_path=deb_path,
        package_family="deb",
        package_format="deb",
    )
