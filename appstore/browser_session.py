from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class BrowserSessionStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, account: str) -> Path:
        safe_name = account.replace("/", "_")
        return self.root / f"{safe_name}.json"

    def load(self, account: str) -> dict | None:
        path = self._path_for(account)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, account: str, state: dict) -> Path:
        payload = dict(state)
        payload["last_verified_at"] = datetime.now().isoformat()
        path = self._path_for(account)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def invalidate(self, account: str) -> None:
        path = self._path_for(account)
        if path.exists():
            path.unlink()
