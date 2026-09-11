#!/usr/bin/env python3
"""Provision client reporting tables; --apply is required for DDL."""
import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from samchat.client_reporting.service import ensure_client_reporting_schema  # noqa: E402


async def run(apply: bool) -> int:
    if not apply:
        print('{"apply": false, "status": "dry_run_no_ddl"}')
        return 0
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine)() as session:
            await ensure_client_reporting_schema(session)
            await session.commit()
    finally:
        await engine.dispose()
    print('{"apply": true, "status": "provisioned"}')
    return 0


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--apply", action="store_true")
if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parser.parse_args().apply)))
