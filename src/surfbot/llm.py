from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Any, Union

from surfbot.fetcher import FeedItem
from surfbot.kanboard import KanboardTask

logger = logging.getLogger(__name__)

ScoredItem = Union[FeedItem, KanboardTask]

_SCORE_MAX_CONTENT = 1000
_CARD_MAX_CONTENT = 5000


async def _run_claude(prompt: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt, "--output-format", "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): {stderr.decode()[:500]}")
    data = json.loads(stdout.decode())
    if data.get("is_error"):
        raise RuntimeError(f"claude CLI error: {data.get('result', '')}")
    return data.get("result", "")


def _extract_json(text: str) -> Any:
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        return json.loads(match.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    return json.loads(text.strip())


def _item_key(item: ScoredItem) -> str:
    if isinstance(item, FeedItem):
        return f"FEED:{item.url}"
    return f"TASK:{item.id}"


def _format_item_for_prompt(item: ScoredItem) -> str:
    if isinstance(item, FeedItem):
        pub = item.published_at.isoformat() if item.published_at else "unknown"
        return (
            f"ID: FEED:{item.url}\n"
            f"Title: {item.title}\n"
            f"Published: {pub}\n"
            f"Summary: {item.summary[:_SCORE_MAX_CONTENT]}"
        )
    comments_text = ""
    if item.comments:
        comments_text = "\nComments:\n" + "\n".join(
            f"  - {c.get('content', '')}" for c in item.comments
        )
    return (
        f"ID: TASK:{item.id}\n"
        f"Title: {item.title}\n"
        f"Description: {item.description[:_SCORE_MAX_CONTENT]}"
        f"{comments_text}"
    )


async def evaluate_importance(
    items: list[ScoredItem],
    preferences_md: str,
    feedback_md: str,
) -> list[ScoredItem]:
    if not items:
        return items

    items_text = "\n---\n".join(_format_item_for_prompt(item) for item in items)

    prompt = f"""You are evaluating the importance of information items for a user.

## User Preferences
{preferences_md}

## User Interest Profile (takes priority over preferences above)
{feedback_md if feedback_md else "(no profile yet)"}

## Task
Score each item from 0 to 100 based on how important and interesting it is to the user.
Higher scores mean more important. Consider relevance, quality, and uniqueness.

## Items
{items_text}

Respond with ONLY a JSON object mapping each item ID to its integer score. Example:
{{"FEED:https://example.com/article": 75, "TASK:42": 30}}"""

    result = await _run_claude(prompt)
    scores: dict[str, int] = _extract_json(result)

    for item in items:
        key = _item_key(item)
        if key in scores:
            item.score = float(scores[key])

    return items


async def generate_card(
    item: FeedItem,
    feed_instruction: str,
    format_md: str,
) -> dict:
    content_preview = item.content[:_CARD_MAX_CONTENT] if item.content else ""
    pub = item.published_at.isoformat() if item.published_at else "unknown"

    prompt = f"""You are generating a Kanboard card for a news/article item.

## Card Format Instructions
{format_md}

## Feed-specific Instructions
{feed_instruction if feed_instruction else "(none)"}

## Article
Title: {item.title}
URL: {item.url}
Published: {pub}
Content:
{content_preview}

Generate a Kanboard card. Respond with ONLY a JSON object using these allowed fields:
- title (string, required)
- description (string, markdown with a link to the article)
- tags (array of strings)
- color_id (string: "yellow", "blue", "red", "orange", "green", "purple", "teal", "maroon", "olive", "lime", "cyan", "silver", "grey", "brown", "black")
- date_due (string, ISO 8601 date, optional)
- priority (integer 0-3, optional)
- score (integer, optional)

Example: {{"title": "Article Title", "description": "Summary...", "tags": ["rust", "performance"], "color_id": "blue"}}"""

    result = await _run_claude(prompt)
    card: dict = _extract_json(result)

    card["external_link"] = item.url
    if item.published_at:
        card["date_started"] = int(item.published_at.timestamp())

    return card


async def update_interest_profile(
    positive_tasks: list[KanboardTask],
    negative_tasks: list[KanboardTask],
    current_profile: str,
    preferences_md: str,
) -> tuple[str, str]:
    def format_task(task: KanboardTask) -> str:
        comments_text = ""
        if task.comments:
            comments_text = "\nUser comments:\n" + "\n".join(
                f"  - {c.get('content', '')}" for c in task.comments
            )
        return f"Title: {task.title}\nDescription: {task.description[:500]}{comments_text}"

    positive_text = "\n---\n".join(format_task(t) for t in positive_tasks) if positive_tasks else "(none)"
    negative_text = "\n---\n".join(format_task(t) for t in negative_tasks) if negative_tasks else "(none)"

    prompt = f"""You are updating a user's interest profile based on their feedback on news/article items.

## Base Preferences
{preferences_md}

## Current Interest Profile
{current_profile if current_profile else "(empty - this is the first feedback)"}

## Positive Feedback (user found these valuable)
{positive_text}

## Negative Feedback (user found these not valuable or uninteresting)
{negative_text}

Update the interest profile to reflect what was learned from this feedback.
The profile should capture patterns of what the user likes and dislikes to help score future items.
Keep it concise (under 500 words). The profile should complement the base preferences, not repeat them.

Respond with ONLY a JSON object with these fields:
- profile (string, the full updated profile in Markdown)
- summary (string, a 1-2 sentence summary of what was learned)

Example: {{"profile": "## User Interest Profile\\n...", "summary": "User prefers Rust performance content."}}"""

    result = await _run_claude(prompt)
    data: dict = _extract_json(result)

    new_profile: str = data.get("profile", current_profile)
    summary: str = data.get("summary", "Feedback processed.")

    return new_profile, summary
