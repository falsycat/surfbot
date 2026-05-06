from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone

from surfbot import llm
from surfbot.config import ConfigLoader
from surfbot.fetcher import FeedItem, fetch
from surfbot.kanboard import KanboardClient, KanboardTask
from surfbot.state import StateManager

logger = logging.getLogger(__name__)


async def run(
    config: ConfigLoader,
    kanboard: KanboardClient,
    state: StateManager,
) -> None:
    cfg = config.app
    cols = cfg.kanboard.columns

    # Phase 1: フィードバック処理
    try:
        await _phase_feedback(config, kanboard, cols.positive, cols.negative)
    except Exception:
        logger.exception("Phase 1 (feedback) failed")

    # Phase 2: フィード取得・統合評価・Inbox 更新
    try:
        await _phase_fetch_and_rank(config, kanboard, state, cols.inbox)
    except Exception:
        logger.exception("Phase 2 (fetch+rank) failed")


async def _phase_feedback(
    config: ConfigLoader,
    kanboard: KanboardClient,
    positive_col: str,
    negative_col: str,
) -> None:
    positive_tasks, negative_tasks = await asyncio.gather(
        kanboard.get_tasks(positive_col),
        kanboard.get_tasks(negative_col),
    )

    if not positive_tasks and not negative_tasks:
        return

    logger.info(
        "Processing feedback: %d positive, %d negative",
        len(positive_tasks), len(negative_tasks),
    )

    all_tasks = positive_tasks + negative_tasks
    comments_list = await asyncio.gather(
        *[kanboard.get_task_comments(t.id) for t in all_tasks]
    )
    for task, comments in zip(all_tasks, comments_list):
        task.comments = comments

    current_profile = config.read_feedback()
    new_profile, summary = await llm.update_interest_profile(
        positive_tasks=positive_tasks,
        negative_tasks=negative_tasks,
        current_profile=current_profile,
        preferences_md=config.preferences_md,
    )

    await asyncio.gather(*[
        kanboard.add_comment(t.id, f"surfbot: Learned from feedback. {summary}")
        for t in all_tasks
    ])
    await asyncio.gather(*[
        kanboard.close_task(t.id) for t in all_tasks
    ])

    config.write_feedback(new_profile)
    logger.info("Feedback processed: %s", summary)


async def _phase_fetch_and_rank(
    config: ConfigLoader,
    kanboard: KanboardClient,
    state: StateManager,
    inbox_col: str,
) -> None:
    cfg = config.app
    now = datetime.now(tz=timezone.utc)

    # 新着アイテム取得
    all_new_items: list[FeedItem] = []
    for feed in config.feeds:
        last_fetched = state.get_last_fetched(feed.name)
        items = await fetch(feed, since=last_fetched)
        state.update_last_fetched(feed.name, now)
        all_new_items.extend(items)
        logger.info("Feed %s: %d new items", feed.name, len(items))

    if not all_new_items:
        logger.info("No new items to process")
        return

    # 既存 Inbox タスクと統合評価
    inbox_tasks = await kanboard.get_tasks(inbox_col)
    all_items = all_new_items + inbox_tasks  # type: ignore[list-item]

    preferences_md = config.preferences_md
    feedback_md = config.read_feedback()

    scored = await llm.evaluate_importance(all_items, preferences_md, feedback_md)
    ranked = sorted(scored, key=lambda x: x.score, reverse=True)

    top = ranked[: cfg.max_inbox_items]
    to_close = [x for x in ranked[cfg.max_inbox_items:] if isinstance(x, KanboardTask)]
    to_create = [x for x in top if isinstance(x, FeedItem)]

    logger.info(
        "Ranking done: %d total, %d to create, %d to close",
        len(ranked), len(to_create), len(to_close),
    )

    # 劣後した既存タスクをクローズ
    if to_close:
        await asyncio.gather(*[
            kanboard.add_comment(t.id, "surfbot: Closed — ranked below inbox limit.")
            for t in to_close
        ])
        await asyncio.gather(*[kanboard.close_task(t.id) for t in to_close])

    # 上位の新着アイテムをカード生成・作成
    if to_create:
        format_md = config.format_md
        cards_or_errors = await asyncio.gather(*[
            llm.generate_card(item, item.feed.instruction, format_md)
            for item in to_create
        ], return_exceptions=True)

        valid_cards = []
        for item, result in zip(to_create, cards_or_errors):
            if isinstance(result, BaseException):
                logger.warning("Card generation failed for %s: %s", item.url, result)
            else:
                valid_cards.append(result)

        await asyncio.gather(*[
            kanboard.create_task(card) for card in valid_cards
        ], return_exceptions=True)

    # Inbox 全体をスコア順にソート
    score_map: dict = {}
    for item in scored:
        if isinstance(item, FeedItem):
            score_map[item.url] = item.score
        else:
            score_map[item.id] = item.score

    inbox_final = await kanboard.get_tasks(inbox_col)
    inbox_sorted = sorted(
        inbox_final,
        key=lambda t: score_map.get(t.id, score_map.get(t.external_link, 0.0)),
        reverse=True,
    )
    await asyncio.gather(*[
        kanboard.update_task_position(t.id, t.column_id, pos)
        for pos, t in enumerate(inbox_sorted)
    ], return_exceptions=True)
