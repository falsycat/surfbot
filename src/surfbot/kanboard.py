from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any
import httpx

logger = logging.getLogger(__name__)

_ALLOWED_CARD_FIELDS = {
    "title", "description", "tags", "color_id",
    "date_due", "priority", "score",
}


@dataclass
class KanboardTask:
    id: int
    title: str
    description: str
    column_id: int
    external_link: str
    score: float = 0.0
    comments: list[dict] = field(default_factory=list)


class KanboardClient:
    def __init__(self, url: str, api_token: str, project_id: int, username: str = "jsonrpc"):
        self._url = url
        self._project_id = project_id
        self._client = httpx.AsyncClient(
            auth=(username, api_token),
            timeout=30.0,
        )
        self._call_id = 0
        self._column_cache: dict[str, int] = {}
        self._user_id: int | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, **params: Any) -> Any:
        self._call_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._call_id,
            "method": method,
            "params": params,
        }
        resp = await self._client.post(self._url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Kanboard API error ({method}): {data['error']}")
        return data.get("result")

    async def _resolve_column(self, column_name: str) -> int:
        if column_name not in self._column_cache:
            columns = await self._call("getColumns", project_id=self._project_id) or []
            for col in columns:
                self._column_cache[col["title"]] = int(col["id"])
        if column_name not in self._column_cache:
            raise ValueError(f"Column '{column_name}' not found in project {self._project_id}")
        return self._column_cache[column_name]

    async def _get_user_id(self) -> int:
        if self._user_id is None:
            me = await self._call("getMe") or {}
            self._user_id = int(me.get("id", 1))
        return self._user_id

    async def get_tasks(self, column_name: str) -> list[KanboardTask]:
        column_id = await self._resolve_column(column_name)
        all_tasks = await self._call(
            "getAllTasks",
            project_id=self._project_id,
            status_id=1,
        ) or []
        tasks = [t for t in all_tasks if int(t.get("column_id", 0)) == column_id]
        return [
            KanboardTask(
                id=int(t["id"]),
                title=t.get("title", ""),
                description=t.get("description", ""),
                column_id=int(t.get("column_id", column_id)),
                external_link=t.get("external_link", ""),
            )
            for t in tasks
        ]

    async def get_task_comments(self, task_id: int) -> list[dict]:
        return await self._call("getAllTaskComments", task_id=task_id) or []

    async def create_task(self, card: dict) -> int:
        sanitized = {k: v for k, v in card.items() if k in _ALLOWED_CARD_FIELDS}
        sanitized["project_id"] = self._project_id
        result = await self._call("createTask", **sanitized)
        return int(result) if result else 0

    async def add_comment(self, task_id: int, text: str) -> None:
        user_id = await self._get_user_id()
        try:
            await self._call("createComment", task_id=task_id, user_id=user_id, content=text)
        except Exception as e:
            logger.warning("Failed to add comment to task %d: %s", task_id, e)

    async def close_task(self, task_id: int) -> None:
        await self._call("closeTask", task_id=task_id)

    async def update_task_position(self, task_id: int, column_id: int, position: int) -> None:
        await self._call(
            "moveTaskPosition",
            project_id=self._project_id,
            task_id=task_id,
            column_id=column_id,
            position=position + 1,  # Kanboard positions are 1-based
        )
