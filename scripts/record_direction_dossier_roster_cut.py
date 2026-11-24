#!/usr/bin/env python3
"""Observe one edition's current team roster, with optional daily cut recording."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from uuid import UUID

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import devnous.gastos  # noqa: F401 - initialize the owning runtime first
from samchat.client_executive.dossier_registry import record_observed_roster_cut

DATABASE_ENV = "DIRECTION_DOSSIER_CUT_DATABASE_URL"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tournament-id", type=UUID, required=True)
    parser.add_argument("--edition-year", type=int, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Append today's complete observation. Default prints a read-only preview.",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict:
    raw = os.environ.get(DATABASE_ENV)
    if not raw:
        raise SystemExit(f"Configure {DATABASE_ENV}.")
    try:
        configured = make_url(raw)
        if configured.get_backend_name() != "postgresql":
            raise ValueError("This command requires PostgreSQL.")
        url = configured.set(drivername="postgresql+asyncpg")
    except (ValueError, ArgumentError):
        raise SystemExit(
            f"Invalid PostgreSQL configuration in {DATABASE_ENV}."
        ) from None

    engine = create_async_engine(url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as session:
            return await record_observed_roster_cut(
                session,
                tournament_id=str(args.tournament_id),
                edition_year=args.edition_year,
                apply=args.apply,
            )
    except (OSError, SQLAlchemyError):
        return {"status": "unavailable", "edition_year": args.edition_year}
    finally:
        await engine.dispose()


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if not 1900 <= args.edition_year <= 2200:
        parser.error("edition-year must be between 1900 and 2200")
    result = asyncio.run(_run(args))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"ready", "recorded", "already_recorded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
