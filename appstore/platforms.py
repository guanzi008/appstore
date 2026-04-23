from __future__ import annotations

from dataclasses import dataclass


class PlatformMappingError(ValueError):
    pass


@dataclass(frozen=True)
class StorePlatform:
    manifest_value: str
    code: int
    pkg_type: int
    system_label: str
    sup_sys: str


@dataclass(frozen=True)
class StoreArch:
    manifest_value: str
    code: str
    label: str


SYSTEM_PLATFORMS = {
    "deepin_23": StorePlatform("Deepin_23", 11, 11, " 社区版V23", "11"),
}

ARCHITECTURES = {
    "amd64": StoreArch("amd64", "4", "X86"),
    "x86_64": StoreArch("amd64", "4", "X86"),
}


def resolve_store_platform(value: str) -> StorePlatform:
    normalized = value.strip().lower()
    try:
        return SYSTEM_PLATFORMS[normalized]
    except KeyError as exc:
        raise PlatformMappingError(f"unsupported store platform mapping: {value}") from exc


def resolve_store_arch(value: str) -> StoreArch:
    normalized = value.strip().lower()
    try:
        return ARCHITECTURES[normalized]
    except KeyError as exc:
        raise PlatformMappingError(f"unsupported store architecture mapping: {value}") from exc
