from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _preferences_path_from_env() -> Path:
    raw = os.environ.get("UTPUBLISHER_PREFERENCES_PATH", "").strip()
    return Path(raw).expanduser() if raw else REPO_ROOT / "ui" / "cache" / "preferences.json"


DEFAULT_PREFERENCES_PATH = _preferences_path_from_env()


@dataclass(frozen=True)
class UIPreferences:
    recent_category_ids: tuple[str, ...] = ("1",)
    recent_regions: tuple[str, ...] = ("1",)
    last_output_dir: str = ""
    last_asset_dir: str = ""
    last_release_key: str = "stable"
    last_pkg_channel: str = "stable"
    last_session_account: str = ""
    remember_credentials: bool = False
    remembered_username: str = ""
    remembered_password: str = ""
    default_target_system_lines: dict[str, tuple[str, ...]] = field(default_factory=dict)


class PreferenceStore:
    def __init__(self, path: Path = DEFAULT_PREFERENCES_PATH) -> None:
        self.path = path

    def load(self) -> UIPreferences:
        if not self.path.exists():
            return UIPreferences()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return UIPreferences()
        if not isinstance(payload, dict):
            return UIPreferences()
        return UIPreferences(
            recent_category_ids=_normalize_items(payload.get("recent_category_ids"), default=("1",)),
            recent_regions=_normalize_items(payload.get("recent_regions"), default=("1",)),
            last_output_dir=str(payload.get("last_output_dir", "") or ""),
            last_asset_dir=str(payload.get("last_asset_dir", "") or ""),
            last_release_key=str(payload.get("last_release_key", "") or "stable"),
            last_pkg_channel=str(payload.get("last_pkg_channel", "") or "stable"),
            last_session_account=str(payload.get("last_session_account", "") or ""),
            remember_credentials=_normalize_bool(payload.get("remember_credentials", False)),
            remembered_username=str(payload.get("remembered_username", "") or ""),
            remembered_password=str(payload.get("remembered_password", "") or ""),
            default_target_system_lines=_normalize_system_line_defaults(payload.get("default_target_system_lines")),
        )

    def save(self, preferences: UIPreferences) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(preferences), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.path


def remember_value(items: tuple[str, ...], value: str, *, limit: int = 12) -> tuple[str, ...]:
    normalized = value.strip()
    if not normalized:
        return items
    merged = [normalized]
    merged.extend(item for item in items if item.strip() and item.strip() != normalized)
    return tuple(merged[:limit])


def _normalize_items(raw, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return default
    normalized = tuple(
        str(item).strip()
        for item in raw
        if str(item).strip()
    )
    return normalized or default


def _normalize_bool(raw) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_system_line_defaults(raw) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for key, values in raw.items():
        arch = str(key).strip().lower()
        if not arch or not isinstance(values, list):
            continue
        codes: list[str] = []
        for value in values:
            code = str(value).strip()
            if code and code not in codes:
                codes.append(code)
        if codes:
            result[arch] = tuple(codes)
    return result
