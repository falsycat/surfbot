from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import yaml


class StateManager:
    def __init__(self, path: Path):
        self._path = path
        self._data: dict[str, datetime] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._data = {}
            return
        with open(self._path) as f:
            raw = yaml.safe_load(f) or {}
        last = raw.get("last_fetched", {})
        self._data = {
            name: datetime.fromisoformat(ts)
            for name, ts in last.items()
        }

    def get_last_fetched(self, feed_name: str) -> datetime | None:
        return self._data.get(feed_name)

    def update_last_fetched(self, feed_name: str, dt: datetime) -> None:
        self._data[feed_name] = dt
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "last_fetched": {
                name: dt.isoformat()
                for name, dt in self._data.items()
            }
        }
        with open(self._path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
