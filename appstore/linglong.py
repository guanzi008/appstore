from __future__ import annotations

import hashlib
import json
import tarfile
import zipfile
from pathlib import Path

from appstore.models import PackageInfo


class LinglongParseError(RuntimeError):
    pass


NATIVE_LAYER_MAGIC = b"<<< deepin linglong layer archive >>>"
NATIVE_LAYER_METADATA_PREFIX_SIZE = len(NATIVE_LAYER_MAGIC) + 7
MAX_NATIVE_LAYER_METADATA_SIZE = 16 * 1024 * 1024


def _hash_package_file(package_path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = package_path.stat().st_size
    with package_path.open("rb") as package_file:
        for chunk in iter(lambda: package_file.read(8192), b""):
            digest.update(chunk)
    return size, digest.hexdigest()


def _normalized_member_name(member_name: str) -> str:
    return member_name.lstrip("./").rstrip("/")


def _candidate_metadata_members(member_names: list[str]) -> list[str]:
    candidates: list[str] = []
    for member_name in member_names:
        normalized = _normalized_member_name(member_name)
        if not normalized:
            continue
        if normalized in {"info.json", "linglong.meta"}:
            candidates.append(normalized)
    return candidates


def _read_member_bytes(package_path: Path, member_name: str) -> bytes:
    if zipfile.is_zipfile(package_path):
        with zipfile.ZipFile(package_path) as archive:
            for archive_name in archive.namelist():
                if _normalized_member_name(archive_name) == member_name:
                    return archive.read(archive_name)
    elif tarfile.is_tarfile(package_path):
        with tarfile.open(package_path) as archive:
            for member in archive.getmembers():
                if _normalized_member_name(member.name) != member_name:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    break
                return extracted.read()
    raise LinglongParseError(f"missing Linglong metadata in {package_path}")


def _read_linglong_metadata(package_path: Path | str) -> dict[str, object]:
    package_path = Path(package_path)
    if not package_path.exists():
        raise LinglongParseError(f"linglong package not found: {package_path}")

    if zipfile.is_zipfile(package_path):
        with zipfile.ZipFile(package_path) as archive:
            candidate_members = _candidate_metadata_members(archive.namelist())
    elif tarfile.is_tarfile(package_path):
        with tarfile.open(package_path) as archive:
            candidate_members = _candidate_metadata_members([member.name for member in archive.getmembers()])
    else:
        return _read_native_layer_metadata(package_path)

    if not candidate_members:
        raise LinglongParseError(f"missing Linglong metadata in {package_path}")
    if len(candidate_members) > 1:
        raise LinglongParseError(f"multiple Linglong metadata files in {package_path}")

    raw_metadata = _read_member_bytes(package_path, candidate_members[0])
    try:
        metadata = json.loads(raw_metadata.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LinglongParseError(f"failed to parse Linglong metadata for {package_path}") from exc
    if not isinstance(metadata, dict):
        raise LinglongParseError(f"failed to parse Linglong metadata for {package_path}")
    return metadata


def _read_native_layer_metadata(package_path: Path) -> dict[str, object]:
    metadata_size = _read_native_layer_metadata_size(package_path)
    with package_path.open("rb") as package_file:
        package_file.seek(NATIVE_LAYER_METADATA_PREFIX_SIZE)
        raw_metadata = package_file.read(metadata_size)
        if len(raw_metadata) != metadata_size:
            raise LinglongParseError(f"failed to parse Linglong metadata for {package_path}")

    try:
        metadata = json.loads(raw_metadata.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LinglongParseError(f"failed to parse Linglong metadata for {package_path}") from exc
    if not isinstance(metadata, dict):
        raise LinglongParseError(f"failed to parse Linglong metadata for {package_path}")
    return metadata


def _read_native_layer_metadata_size(package_path: Path) -> int:
    with package_path.open("rb") as package_file:
        prefix = package_file.read(NATIVE_LAYER_METADATA_PREFIX_SIZE)
        if not prefix.startswith(NATIVE_LAYER_MAGIC) or len(prefix) < NATIVE_LAYER_METADATA_PREFIX_SIZE:
            raise LinglongParseError(f"unsupported linglong package format: {package_path}")

        metadata_size = int.from_bytes(prefix[-4:], byteorder="little", signed=False)
        if metadata_size <= 0 or metadata_size > MAX_NATIVE_LAYER_METADATA_SIZE:
            raise LinglongParseError(f"malformed Linglong metadata in {package_path}")
        return metadata_size


def native_layer_payload_offset(package_path: Path | str) -> int:
    package_path = Path(package_path)
    return NATIVE_LAYER_METADATA_PREFIX_SIZE + _read_native_layer_metadata_size(package_path)


def _read_app_layer(metadata: dict[str, object], package_path: Path) -> dict[str, object]:
    layers = metadata.get("layers")
    if not isinstance(layers, list):
        info = metadata.get("info")
        if isinstance(info, dict) and str(info.get("kind", "")).strip().lower() == "app":
            return info
        raise LinglongParseError(f"missing application layer in {package_path}")

    app_layers: list[dict[str, object]] = []
    for layer in layers:
        if not isinstance(layer, dict):
            raise LinglongParseError(f"malformed Linglong metadata in {package_path}")
        info = layer.get("info")
        if not isinstance(info, dict):
            raise LinglongParseError(f"malformed Linglong metadata in {package_path}")
        if str(info.get("kind", "")).strip().lower() == "app":
            app_layers.append(info)

    if not app_layers:
        raise LinglongParseError(f"missing application layer in {package_path}")
    if len(app_layers) > 1:
        raise LinglongParseError(f"multiple application layers in {package_path}")
    return app_layers[0]


def _normalize_architecture(info: dict[str, object], package_path: Path) -> str:
    raw_arch = info.get("arch")
    if isinstance(raw_arch, list):
        if not raw_arch:
            raise LinglongParseError(f"missing architecture in Linglong metadata for {package_path}")
        architecture = str(raw_arch[0]).strip()
    else:
        architecture = str(raw_arch or "").strip()
    if not architecture:
        raise LinglongParseError(f"missing architecture in Linglong metadata for {package_path}")
    return architecture


def _build_package_info(package_path: Path | str, *, package_format: str) -> PackageInfo:
    package_path = Path(package_path)
    metadata = _read_linglong_metadata(package_path)
    info = _read_app_layer(metadata, package_path)

    pkg_name = str(info.get("id") or "").strip()
    pkg_version = str(info.get("version") or "").strip()
    pkg_arch = _normalize_architecture(info, package_path)
    if not pkg_name or not pkg_version:
        raise LinglongParseError(f"malformed Linglong metadata in {package_path}")

    pkg_size, sha256 = _hash_package_file(package_path)
    return PackageInfo(
        pkg_name=pkg_name,
        pkg_version=pkg_version,
        pkg_arch=pkg_arch,
        pkg_size=pkg_size,
        sha256=sha256,
        file_path=package_path,
        package_family="linglong",
        package_format=package_format,
    )


def read_uab_package_info(package_path: Path | str) -> PackageInfo:
    return _build_package_info(package_path, package_format="uab")


def read_layer_package_info(package_path: Path | str) -> PackageInfo:
    return _build_package_info(package_path, package_format="layer")


def read_linglong_metadata(package_path: Path | str) -> dict[str, object]:
    return _read_linglong_metadata(package_path)
