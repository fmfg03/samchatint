#!/usr/bin/env python3
"""Monitor and repair workflow Telegram notification gaps."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from devnous.gastos.services.documento_telegram import (
    get_notification_session_maker,
    monitor_workflow_telegram_notifications,
)


def _load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


async def _run(args: argparse.Namespace) -> dict[str, int]:
    if args.env_file:
        _load_env_file(args.env_file)
    session_maker = get_notification_session_maker()
    if session_maker is None:
        raise RuntimeError("No notification database session maker available")
    async with session_maker() as session:
        return await monitor_workflow_telegram_notifications(
            session,
            older_than_minutes=args.older_than_minutes,
            limit=args.limit,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Monitor SamChat workflow Telegram notifications."
    )
    parser.add_argument("--env-file", default=os.getenv("SAMCHAT_ENV_FILE", ""))
    parser.add_argument("--older-than-minutes", type=int, default=5)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    stats = asyncio.run(_run(args))
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
