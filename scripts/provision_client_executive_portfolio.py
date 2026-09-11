#!/usr/bin/env python3
"""Provision an audited client executive portfolio; dry-run is the default."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from uuid import uuid4
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from samchat.client_executive.service import ensure_client_executive_schema  # noqa: E402


def _database_url() -> str:
    value = (os.getenv("DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _run(args: argparse.Namespace) -> int:
    engine = create_async_engine(_database_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    receipt: dict[str, Any] = {
        "apply": bool(args.apply),
        "portfolio_id": args.portfolio_id,
        "members": sorted(set(args.empleado_id)),
        "tournaments": sorted(set(args.tournament_id)),
    }
    try:
        async with maker() as session:
            if args.apply:
                await ensure_client_executive_schema(session)
                await session.execute(
                    text("""INSERT INTO client_executive_portfolios (id, label)
                    VALUES (:id, :label) ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label"""),
                    {"id": args.portfolio_id, "label": args.label},
                )
                for empleado_id in receipt["members"]:
                    await session.execute(text("""INSERT INTO client_executive_portfolio_members
                    (portfolio_id, empleado_id) VALUES (:portfolio_id, :empleado_id)
                    ON CONFLICT (portfolio_id, empleado_id) DO UPDATE SET active = TRUE"""),
                    {"portfolio_id": args.portfolio_id, "empleado_id": empleado_id})
                for tournament_id in receipt["tournaments"]:
                    await session.execute(text("""INSERT INTO client_executive_portfolio_tournaments
                    (portfolio_id, tournament_id) VALUES (:portfolio_id, :tournament_id)
                    ON CONFLICT (portfolio_id, tournament_id) DO UPDATE SET active = TRUE"""),
                    {"portfolio_id": args.portfolio_id, "tournament_id": tournament_id})
                await session.execute(
                    text("""INSERT INTO client_executive_access_audit_logs
                    (id, portfolio_id, event_type, detail)
                    VALUES (:id, :portfolio_id, 'portfolio_provisioned', CAST(:detail AS JSONB))"""),
                    {
                        "id": str(uuid4()),
                        "portfolio_id": args.portfolio_id,
                        "detail": json.dumps(receipt),
                    },
                )
                await session.commit()
            else:
                await session.rollback()
    finally:
        await engine.dispose()
    print(json.dumps(receipt, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist the reviewed assignment")
    parser.add_argument("--portfolio-id", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--empleado-id", action="append", required=True)
    parser.add_argument("--tournament-id", action="append", required=True)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
