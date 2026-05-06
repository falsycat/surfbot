from __future__ import annotations
import calendar
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import feedparser
import httpx

if TYPE_CHECKING:
    from surfbot.config import FeedConfig

logger = logging.getLogger(__name__)

_CONTENT_MAX_BYTES = 20_000


@dataclass
class FeedItem:
    url: str
    title: str
    published_at: datetime | None
    content: str
    feed: "FeedConfig"
    score: float = 0.0


async def fetch(feed: "FeedConfig", since: datetime | None = None) -> list[FeedItem]:
    try:
        parsed = feedparser.parse(feed.url)
    except Exception as e:
        logger.warning("Failed to parse feed %s: %s", feed.name, e)
        return []

    if parsed.get("bozo") and not parsed.entries:
        logger.warning("Feed %s returned bozo error: %s", feed.name, parsed.get("bozo_exception"))
        return []

    urls_to_fetch: list[str] = []
    raw_items: list[tuple[str, str, datetime | None, str]] = []

    for entry in parsed.entries:
        url = entry.get("link", "")
        title = entry.get("title", "(no title)")
        published_at = _parse_date(entry)

        if since is not None and published_at is not None and published_at <= since:
            continue

        content = _extract_content(entry)
        raw_items.append((url, title, published_at, content))
        if feed.fetch_content and url:
            urls_to_fetch.append(url)

    if not urls_to_fetch:
        return [
            FeedItem(url=url, title=title, published_at=pub, content=content, feed=feed)
            for url, title, pub, content in raw_items
        ]

    fetched_contents: dict[str, str] = {}
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        for url in urls_to_fetch:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                fetched_contents[url] = resp.text[:_CONTENT_MAX_BYTES]
            except Exception as e:
                logger.warning("Failed to fetch content for %s: %s", url, e)

    return [
        FeedItem(
            url=url,
            title=title,
            published_at=pub,
            content=fetched_contents.get(url, content) if feed.fetch_content else content,
            feed=feed,
        )
        for url, title, pub, content in raw_items
    ]


def _parse_date(entry: feedparser.FeedParserDict) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(attr)
        if t:
            ts = calendar.timegm(t)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def _extract_content(entry: feedparser.FeedParserDict) -> str:
    if entry.get("content"):
        return entry.content[0].value[:_CONTENT_MAX_BYTES]
    return entry.get("summary", "")[:_CONTENT_MAX_BYTES]
