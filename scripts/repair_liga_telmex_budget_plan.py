#!/usr/bin/env python3
"""Restore the audited Liga Telmex budget schedule.

Dry-run is the default. Pass ``--apply`` to persist the guarded repair.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from samchat.budgets.service import (  # noqa: E402
    list_monthly_plan_for_lines,
    replace_budget_line_monthly_plan,
)

VERSION_ID = "64d963a7-61b1-402a-b58a-7efcb96cd71a"
LINE_ID = "fc7de465-f6f7-4714-bc19-85ac51d2ab0d"
EXPECTED_VERSION_NAME = "Presupuesto operativo 2026"
EXPECTED_TOURNAMENT_NAME = "Liga Telmex Telcel de Béisbol"
EXPECTED_CONCEPT_NAME = "Envío de material"
EXPECTED_TOTAL = 6295.18


def _load_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        raise SystemExit(f"env_file_not_found={env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _database_url() -> str:
    value = (os.getenv("DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


def _target_plan() -> dict[int, dict[str, float]]:
    return {
        week: {
            "budget_expense_amount": 121.12 if week == 52 else 121.06,
            "expected_income_amount": 0.0,
        }
        for week in range(1, 53)
    }


def _expense_values(plan: dict[int, dict[str, Any]]) -> dict[int, float]:
    return {
        week: round(float(values.get("budget_expense_amount") or 0), 2)
        for week, values in plan.items()
    }


async def _run(args: argparse.Namespace) -> int:
    _load_env_file(args.env_file)
    engine = create_async_engine(_database_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    receipt: dict[str, Any] = {"apply": args.apply, "line_id": LINE_ID}
    try:
        async with maker() as session:
            row = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT
                            l.id::text AS line_id,
                            l.budget_amount,
                            l.concept_name,
                            l.tournament_name,
                            v.id::text AS version_id,
                            v.version_name,
                            v.edition_year,
                            v.status
                        FROM budget_lines l
                        JOIN budget_versions v ON v.id = l.budget_version_id
                        WHERE l.id = CAST(:line_id AS uuid)
                        """
                        ),
                        {"line_id": LINE_ID},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise RuntimeError("target_line_not_found")
            guards = {
                "version_id": str(row["version_id"]) == VERSION_ID,
                "version_name": row["version_name"] == EXPECTED_VERSION_NAME,
                "edition_year": int(row["edition_year"]) == 2026,
                "version_status": row["status"] in {"draft", "reforecast"},
                "tournament_name": row["tournament_name"] == EXPECTED_TOURNAMENT_NAME,
                "concept_name": row["concept_name"] == EXPECTED_CONCEPT_NAME,
                "budget_amount": round(float(row["budget_amount"]), 2)
                == EXPECTED_TOTAL,
            }
            receipt["guards"] = guards
            if not all(guards.values()):
                raise RuntimeError("target_guard_failed")

            current = (
                await list_monthly_plan_for_lines(session, line_ids=[LINE_ID])
            ).get(LINE_ID, {})
            current_values = _expense_values(current)
            target = _target_plan()
            target_values = _expense_values(target)
            receipt["before_total"] = round(sum(current_values.values()), 2)
            receipt["target_total"] = round(sum(target_values.values()), 2)

            if current_values == target_values:
                receipt["status"] = "already_repaired"
                await session.rollback()
            elif current_values and any(current_values.values()):
                raise RuntimeError("unexpected_nonzero_schedule")
            elif args.apply:
                await replace_budget_line_monthly_plan(
                    session,
                    budget_line_id=LINE_ID,
                    plan=target,
                    actor_empleado_id=None,
                )
                await session.commit()
                receipt["status"] = "repaired"
            else:
                receipt["status"] = "ready_to_repair"
                await session.rollback()
    finally:
        await engine.dispose()

    print(json.dumps(receipt, indent=2, ensure_ascii=False, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Persist repair")
    parser.add_argument("--env-file")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
