from __future__ import annotations
import argparse
import asyncio
import logging
import signal
from pathlib import Path

from surfbot.config import ConfigLoader
from surfbot.kanboard import KanboardClient
from surfbot.state import StateManager
from surfbot import cycle

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent


async def _main(once: bool = False) -> None:
    config = ConfigLoader(BASE_DIR)
    config.reload_if_changed()

    cfg = config.app
    state = StateManager(BASE_DIR / "data" / "state.yaml")

    kanboard = KanboardClient(
        url=cfg.kanboard.url,
        api_token=cfg.kanboard.api_token,
        project_id=cfg.kanboard.project_id,
        username=cfg.kanboard.username,
    )

    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("surfbot started (interval=%dm, max_inbox=%d)",
                cfg.cycle_interval_minutes, cfg.max_inbox_items)

    try:
        while not shutdown.is_set():
            config.reload_if_changed()
            logger.info("Starting cycle")
            await cycle.run(config, kanboard, state)
            logger.info("Cycle complete")

            if once:
                break

            interval = config.app.cycle_interval_minutes * 60
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    finally:
        await kanboard.close()
        logger.info("surfbot stopped")


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args()
    asyncio.run(_main(once=args.once))


if __name__ == "__main__":
    run()
