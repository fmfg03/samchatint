"""
Low-level Telegram Bot API helpers for gastos notifications.

Shared by Tocino webhooks, document workflow notifications, and other services.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)


def get_telegram_bot_token() -> str:
    return (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


async def send_telegram_message(
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = "Markdown",
    reply_markup: Optional[Dict[str, Any]] = None,
) -> bool:
    """Send a message to a Telegram chat. Returns True on HTTP 200."""
    token = get_telegram_bot_token()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set, cannot send Telegram message")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as response:
                if response.status == 200:
                    return True
                body = await response.text()
                logger.error(
                    "Failed to send Telegram message",
                    extra={"status_code": response.status, "response": body},
                )
                # Retry without parse_mode when Markdown entities fail
                if payload.get("parse_mode") and response.status == 400:
                    desc = body.lower()
                    if "parse" in desc or "can't parse" in desc:
                        payload.pop("parse_mode", None)
                        async with session.post(url, json=payload, timeout=10) as retry_resp:
                            ok = retry_resp.status == 200
                            if not ok:
                                logger.error(
                                    "Telegram retry without parse_mode failed",
                                    extra={
                                        "status_code": retry_resp.status,
                                        "response": await retry_resp.text(),
                                    },
                                )
                            return ok
                return False
    except Exception as e:
        logger.error("Failed to send Telegram message", extra={"error": str(e)})
        return False


def schedule_fire_and_forget(coro) -> None:
    """Run coroutine in the background; swallow errors after logging."""
    async def _wrapper():
        try:
            await coro
        except Exception:
            logger.exception("Background Telegram task failed")

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_wrapper())
        else:
            asyncio.run(_wrapper())
    except RuntimeError:
        asyncio.run(_wrapper())
