from __future__ import annotations

from dataclasses import dataclass

from appstore.models import PackageRecord, ReleaseRecord, TargetRecord


@dataclass(frozen=True)
class ResolvedTarget:
    sup_sys_code: str
    baseline_id: str


def decide_execution_mode(*, release: ReleaseRecord, cli_mode: str) -> str:
    release_mode = (release.execution_mode or "").strip().lower()
    if release_mode:
        return release_mode
    return (cli_mode or "auto").strip().lower() or "auto"


def resolve_target_system_line(*, package: PackageRecord, target: TargetRecord) -> ResolvedTarget:
    arch = (package.declared_arch or "").strip().lower()
    sup_sys_code = (target.sup_sys_code or "").strip()
    baseline_id = (target.baseline_id or "").strip()
    if arch in {"loong64", "loongarch64"} and sup_sys_code == "11":
        return ResolvedTarget(sup_sys_code="21", baseline_id=baseline_id)
    return ResolvedTarget(sup_sys_code=sup_sys_code, baseline_id=baseline_id)
