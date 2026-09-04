#!/usr/bin/env python3
"""Launch the Copa Telmex Telegram bot."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path


try:
    from dotenv import load_dotenv
except ImportError:
    # The systemd runtime normally supplies env files; keep local launches usable.
    logging.getLogger(__name__).debug("python-dotenv is unavailable")
else:
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)


sys.path.insert(0, str(Path(__file__).parent / "src"))

from devnous.tournaments.instances.copa_telmex.bot import create_copa_telmex_bot


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Create the Copa Telmex bot and run the Telegram polling loop."""
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if not telegram_token:
        print("TELEGRAM_BOT_TOKEN is not set")
        sys.exit(1)

    if not anthropic_key and not openai_key:
        print("Missing OCR keys: set at least one of ANTHROPIC_API_KEY or OPENAI_API_KEY")
        sys.exit(1)

    bot = await create_copa_telmex_bot(
        telegram_token=telegram_token,
        anthropic_key=anthropic_key,
        openai_key=openai_key,
    )

    try:
        await bot.ensure_schema()
    except Exception as exc:
        logger.error("ensure_schema failed: %s", exc, exc_info=True)

    try:
        await bot.run_telegram_bot()
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as exc:
        logger.error("Copa Telmex bot failed: %s", exc, exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
