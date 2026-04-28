from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrowserSessionState:
    account: str
    cookies: list[dict]
    local_storage: dict[str, str]
    session_storage: dict[str, str]
    user_agent: str
    last_verified_at: str = ""


class SessionStateStore:
    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)

    def _path_for(self, account: str) -> Path:
        safe_name = account.replace("/", "_")
        return self.base_dir / f"{safe_name}.json"

    def load(self, account: str) -> BrowserSessionState | None:
        path = self._path_for(account)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("account", account)
        payload.setdefault("cookies", [])
        payload.setdefault("local_storage", {})
        payload.setdefault("session_storage", {})
        payload.setdefault("user_agent", "Mozilla/5.0")
        payload.setdefault("last_verified_at", "")
        return BrowserSessionState(**payload)

    def list_accounts(self) -> tuple[str, ...]:
        if not self.base_dir.exists():
            return ()
        entries: list[tuple[float, str]] = []
        for path in self.base_dir.glob("*.json"):
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            entries.append((mtime, path.stem))
        entries.sort(key=lambda item: item[0], reverse=True)
        return tuple(name for _mtime, name in entries)

    def save(self, state: BrowserSessionState) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(state.account)
        path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def invalidate(self, account: str) -> None:
        path = self._path_for(account)
        if path.exists():
            path.unlink()
