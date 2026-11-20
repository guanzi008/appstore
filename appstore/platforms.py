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
    "arm64": StoreArch("arm64", "3", "ARM"),
    "loong64": StoreArch("loong64", "6", "loong"),
    "loongarch64": StoreArch("loongarch64", "5", "loongarch64"),
    "mips64": StoreArch("mips64", "1", "mips64"),
    "riscv64": StoreArch("riscv64", "7", "riscv64"),
    "sw64": StoreArch("sw64", "8", "sw64"),
    "sw_64": StoreArch("sw_64", "2", "sw_64"),
}

_ARCH_ALIASES = {
    "x86": "amd64", "x86_64": "amd64", "x86-64": "amd64", "x64": "amd64",
    "aarch64": "arm64", "arm": "arm64", "armv8": "arm64", "loong": "loong64", "mips64el": "mips64",
}
_ARCH_BY_CODE = {arch.code: arch.manifest_value for arch in ARCHITECTURES.values()}


def normalize_package_arch(value: str) -> str:
    normalized = value.strip().lower()
    return _ARCH_BY_CODE.get(normalized, _ARCH_ALIASES.get(normalized, normalized))


def resolve_store_platform(value: str) -> StorePlatform:
    normalized = value.strip().lower()
    try:
        return SYSTEM_PLATFORMS[normalized]
    except KeyError as exc:
        raise PlatformMappingError(f"unsupported store platform mapping: {value}") from exc


def resolve_store_arch(value: str) -> StoreArch:
    normalized = normalize_package_arch(value)
    try:
        return ARCHITECTURES[normalized]
    except KeyError as exc:
        raise PlatformMappingError(f"unsupported store architecture mapping: {value}") from exc
