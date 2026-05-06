from __future__ import annotations
import logging
from pathlib import Path
import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class KanboardColumns(BaseModel):
    inbox: str = "Inbox"
    positive: str = "Positive"
    negative: str = "Negative"


class KanboardConfig(BaseModel):
    url: str
    username: str = "jsonrpc"
    api_token: str
    project_id: int
    columns: KanboardColumns = Field(default_factory=KanboardColumns)


class AppConfig(BaseModel):
    kanboard: KanboardConfig
    cycle_interval_minutes: int = 60
    max_inbox_items: int = 30


class FeedConfig(BaseModel):
    name: str
    url: str
    fetch_content: bool = False
    instruction: str = ""


class ConfigLoader:
    def __init__(self, base_dir: Path):
        self._base = base_dir
        self._app: AppConfig | None = None
        self._feeds: list[FeedConfig] = []
        self._format_md: str = ""
        self._preferences_md: str = ""
        self._mtimes: dict[str, float] = {}

    def _mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return 0.0

    def reload_if_changed(self) -> None:
        paths = {
            "config": self._base / "config" / "config.yaml",
            "feeds": self._base / "config" / "feeds.yaml",
            "format": self._base / "config" / "format.md",
            "preferences": self._base / "config" / "preferences.md",
        }
        if all(self._mtimes.get(k) == self._mtime(v) for k, v in paths.items()):
            return
        logger.info("Config changed, reloading")
        for k, v in paths.items():
            self._mtimes[k] = self._mtime(v)
        self._load(paths)

    def _load(self, paths: dict[str, Path]) -> None:
        with open(paths["config"]) as f:
            data = yaml.safe_load(f)
        self._app = AppConfig(**data)

        with open(paths["feeds"]) as f:
            feeds_data = yaml.safe_load(f) or []
        self._feeds = [FeedConfig(**fd) for fd in feeds_data]

        self._format_md = paths["format"].read_text() if paths["format"].exists() else ""
        self._preferences_md = paths["preferences"].read_text() if paths["preferences"].exists() else ""

    @property
    def app(self) -> AppConfig:
        if self._app is None:
            raise RuntimeError("Config not loaded. Call reload_if_changed() first.")
        return self._app

    @property
    def feeds(self) -> list[FeedConfig]:
        return self._feeds

    @property
    def format_md(self) -> str:
        return self._format_md

    @property
    def preferences_md(self) -> str:
        return self._preferences_md

    def read_feedback(self) -> str:
        path = self._base / "data" / "feedback.md"
        return path.read_text() if path.exists() else ""

    def write_feedback(self, content: str) -> None:
        path = self._base / "data" / "feedback.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
