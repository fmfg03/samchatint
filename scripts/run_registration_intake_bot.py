#!/usr/bin/env python3
"""Run the dedicated Copa Telmex registration intake Telegram bot."""

import asyncio
import logging

from devnous.tournaments.instances.copa_telmex.registration_bot import (
    create_registration_intake_bot,
)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = await create_registration_intake_bot()
    await bot.run_telegram_bot()


if __name__ == "__main__":
    asyncio.run(main())
