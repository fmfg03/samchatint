#!/usr/bin/env python3
"""Preview or atomically apply the approved Liga and Homeless weekly plans.

Preview is read-only. Apply requires the exact independently reviewed preview
digest, an explicit source commit and Juan Pablo's active budget authority.
This command never changes annual budgets, legacy schedules or other projects.
The caller is responsible for owner-run migration and verified backups first.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import devnous.gastos  # noqa: E402,F401 - preserve application import order
from devnous.gastos.routes.admin_routes import _budget_access_map  # noqa: E402
from samchat.budgets import service as budgets  # noqa: E402
from samchat.budgets.tournament_weekly_overlay import (  # noqa: E402
    apply_tournament_overlay,
    effective_plans_for_lines,
    preview_tournament_overlay,
)
from apply_provisional_approval_policy import database_url, save_json  # noqa: E402

VERSION = "64d963a7-61b1-402a-b58a-7efcb96cd71a"
ACTOR = "e3d13040-2360-420f-98a1-516440ef63c3"
TARGETS = (
    {
        "name": "Liga",
        "id": "16793f0e-4697-4a28-8f42-b563da8cc7d8",
        "week": 40,
        "line_count": 93,
        "total": "3686827.60",
    },
    {
        "name": "Homeless",
        "id": "c3b247f1-e3fc-4f4a-8995-56a5880f86a6",
        "week": 52,
        "line_count": 77,
        "total": "30384939.78",
    },
)
WRITE_RULES = {
    "budget_tournament_weekly_revisions": {"insert", "update"},
    "budget_tournament_weekly_lines": {"insert"},
    "budget_version_audit_log": {"insert"},
}
CODE_PATHS = (
    "scripts/apply_tournament_weekly_overlay.py",
    "src/samchat/budgets/tournament_weekly_overlay.py",
    "src/samchat/budgets/service.py",
    "src/samchat/budgets/distribution.py",
)


class AtomicSession(AsyncSession):
    """Require a single outer transaction for the two approved tournaments."""

    async def commit(self) -> None:
        raise ValueError("Nested commits are forbidden")


def digest(value: Any) -> str:
    raw = json.dumps(
        value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def source_identity() -> dict[str, Any]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    files = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in CODE_PATHS
    }
    return {"commit": revision, "code_sha256": digest(files), "files": files}


def require_serving_source(expected_commit: str) -> None:
    """Reject a checkout or stale release when effecting production state."""
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit or ""):
        raise ValueError("Apply requires the full reviewed commit")
    if source_identity()["commit"] != expected_commit:
        raise ValueError("Source commit differs from the reviewed commit")
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", *CODE_PATHS],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *CODE_PATHS],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    raw = subprocess.run(
        [
            "systemctl",
            "show",
            "samchat-gastos.service",
            "-p",
            "WorkingDirectory",
            "-p",
            "MainPID",
            "-p",
            "ActiveState",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    unit = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    if unit.get("ActiveState") != "active" or int(unit.get("MainPID") or 0) < 1:
        raise ValueError("Production service is not active")
    if Path(unit["WorkingDirectory"]).resolve() != ROOT.resolve():
        raise ValueError("This source is not the selected production release")
    if Path(f"/proc/{unit['MainPID']}/cwd").resolve() != ROOT.resolve():
        raise ValueError("The running service does not use this release")


async def authorized_actor(session: AsyncSession) -> None:
    record = (
        (
            await session.execute(
                text(
                    """SELECT activo,rol,departamento,correo
        FROM empleados WHERE id=CAST(:id AS uuid)"""
                ),
                {"id": ACTOR},
            )
        )
        .mappings()
        .first()
    )
    if record is None or not record["activo"]:
        raise PermissionError("The authorized employee is not active")
    access = _budget_access_map(SimpleNamespace(**record))
    if any(not access.get(key) for key in ("read", "create", "line_update", "approve")):
        raise PermissionError("The employee lacks required budget permissions")


async def baseline(session: AsyncSession) -> dict[str, list[dict]]:
    """Preserve the whole source version and all original weekly records."""
    queries = {
        "version": "SELECT to_jsonb(v) AS row FROM budget_versions v WHERE v.id=CAST(:id AS uuid) ORDER BY v.id",
        "lines": "SELECT to_jsonb(l) AS row FROM budget_lines l WHERE l.budget_version_id=CAST(:id AS uuid) ORDER BY l.id",
        "plans": "SELECT to_jsonb(p) AS row FROM budget_line_monthly_plan p JOIN budget_lines l ON l.id=p.budget_line_id WHERE l.budget_version_id=CAST(:id AS uuid) ORDER BY p.budget_line_id,p.month_number,p.id",
        "allocations": "SELECT to_jsonb(a) AS row FROM budget_line_monthly_allocations a JOIN budget_lines l ON l.id=a.budget_line_id WHERE l.budget_version_id=CAST(:id AS uuid) ORDER BY a.budget_line_id,a.month_number,a.id",
    }
    return {
        name: (await session.execute(text(sql), {"id": VERSION})).scalars().all()
        for name, sql in queries.items()
    }


async def build_plan(session: AsyncSession, source: dict[str, Any]) -> dict[str, Any]:
    await authorized_actor(session)
    plans = []
    for target in TARGETS:
        plan = await preview_tournament_overlay(
            session, version_id=VERSION, tournament_id=target["id"], week=target["week"]
        )
        if (
            plan["edition_year"] != 2026
            or plan["line_count"] != target["line_count"]
            or Decimal(plan["total"]) != Decimal(target["total"])
        ):
            raise ValueError(f"{target['name']} differs from the approved scope")
        plans.append({"target": target, "preview": plan})
    return {
        "source": source,
        "actor_id": ACTOR,
        "version_id": VERSION,
        "targets": plans,
    }


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


async def verify_effective(session: AsyncSession, plan: dict[str, Any]) -> dict:
    """Read back every line; check isolation, cents and income preservation."""
    lines = await budgets.list_budget_lines(
        session, version_id=VERSION, limit=5000, ensure_schema=False
    )
    ids = [str(row["id"]) for row in lines]
    baseline_plans = await budgets.list_monthly_plan_for_lines(
        session, line_ids=ids, ensure_schema=False
    )
    effective, conflicts = await effective_plans_for_lines(
        session, version_id=VERSION, line_ids=ids, baseline_plans=baseline_plans
    )
    target_lines = {}
    for item in plan["targets"]:
        if item["target"]["id"] in conflicts:
            raise ValueError("Applied target has a distribution conflict")
        for row in item["preview"]["lines"]:
            line_id = str(row["source_line_id"])
            if line_id in target_lines:
                raise ValueError("Duplicate financial line in the reviewed plans")
            target_lines[line_id] = (item["target"], row)
    for row in lines:
        line_id = str(row["id"])
        old = baseline_plans.get(line_id, {})
        now = effective.get(line_id, {})
        if line_id not in target_lines:
            if now != old:
                raise ValueError("A plan outside the approved scope changed")
            continue
        target, source = target_lines[line_id]
        week = target["week"]
        if week not in now or _money(now[week].get("budget_expense_amount")) != _money(
            source["budget_amount"]
        ):
            raise ValueError("A line is not scheduled at its approved week and amount")
        if sum(
            (_money(v.get("budget_expense_amount")) for v in now.values()), Decimal(0)
        ) != _money(source["budget_amount"]):
            raise ValueError("A line's weekly total does not reconcile")
        for old_week in set(old) | set(now):
            if _money(old.get(old_week, {}).get("expected_income_amount")) != _money(
                now.get(old_week, {}).get("expected_income_amount")
            ):
                raise ValueError("Expected income changed")
    if set(target_lines) - set(ids):
        raise ValueError("A reviewed line is no longer in the source version")
    return {
        "target_line_count": len(target_lines),
        "other_lines_preserved": True,
        "expected_income_preserved": True,
        "target_conflicts": False,
    }


def install_sql_guard(engine, *, applying: bool) -> None:
    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def guard(conn, cursor, statement, parameters, context, executemany):
        del conn, cursor, parameters, context, executemany
        sql = statement.strip()
        first = sql.split()[0].lower() if sql else ""
        if first in {"insert", "update", "delete"}:
            found = re.match(
                r"(?is)^(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]+)(?:\s|\()",
                sql,
            )
            if (
                not applying
                or not found
                or first not in WRITE_RULES.get(found[1].lower(), set())
            ):
                raise ValueError("Mutation outside the authorized weekly scope")
        elif first not in {"select", "set", "show"}:
            raise ValueError("DDL and unapproved SQL are forbidden")
        if not applying and re.search(r"\bFOR\s+(?:UPDATE|SHARE)\b", sql, re.I):
            raise ValueError("Preview cannot acquire write locks")


async def run(args: argparse.Namespace) -> dict:
    source = source_identity()
    if args.apply:
        if not args.expected_plan_sha256 or not args.expected_commit:
            raise ValueError("Apply requires reviewed plan SHA-256 and source commit")
        require_serving_source(args.expected_commit)
    engine = create_async_engine(database_url(args), hide_parameters=True)
    install_sql_guard(engine, applying=args.apply)
    try:
        async with AtomicSession(engine) as session:
            async with session.begin():
                isolation = (
                    "SERIALIZABLE" if args.apply else "REPEATABLE READ READ ONLY"
                )
                await session.execute(
                    text(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
                )
                await session.execute(text("SET LOCAL statement_timeout='60s'"))
                plan = await build_plan(session, source)
                plan_hash = digest(plan)
                before = await baseline(session)
                preview_filename = (
                    "fresh-preview.json" if args.apply else "preview.json"
                )
                save_json(
                    args.output_dir,
                    preview_filename,
                    {"status": "PREVIEW", "plan_sha256": plan_hash, "plan": plan},
                )
                if not args.apply:
                    return {
                        "status": "PREVIEW",
                        "plan_sha256": plan_hash,
                        "source_commit": source["commit"],
                    }
                if plan_hash != args.expected_plan_sha256:
                    raise ValueError("Plan drifted; inspect a fresh preview")
                if not all(item["preview"]["schema_ready"] for item in plan["targets"]):
                    raise ValueError("Owner-run migration has not been applied")
                save_json(args.output_dir, "before.json", before)
                results = []
                for item in plan["targets"]:
                    target = item["target"]
                    results.append(
                        await apply_tournament_overlay(
                            session,
                            version_id=VERSION,
                            tournament_id=target["id"],
                            week=target["week"],
                            expected_plan_hash=item["preview"]["plan_hash"],
                            actor_empleado_id=ACTOR,
                        )
                    )
                verification = await verify_effective(session, plan)
                if await baseline(session) != before:
                    raise ValueError("Original financial state or schedules changed")
                save_json(
                    args.output_dir,
                    "precommit.json",
                    {
                        "status": "VERIFIED_PENDING_COMMIT",
                        "plan_sha256": plan_hash,
                        "results": results,
                        "verification": verification,
                    },
                )
        # Persist an explicit marker before the independent read-back so that an
        # interrupted/failed verification is never mistaken for a rollback.
        save_json(
            args.output_dir,
            "committed-unverified.json",
            {
                "status": "COMMITTED_VERIFICATION_PENDING",
                "plan_sha256": plan_hash,
                "source_commit": source["commit"],
                "results": results,
            },
        )
        # New, independent read transaction after the outer transaction committed.
        async with AsyncSession(engine) as session:
            async with session.begin():
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                after = await baseline(session)
                if after != before:
                    raise ValueError(
                        "Committed original source state differs; inspect concurrent activity"
                    )
                verification = await verify_effective(session, plan)
                for item in results:
                    audit = (
                        await session.execute(
                            text(
                                """SELECT count(*) FROM budget_version_audit_log
                        WHERE budget_version_id=CAST(:version AS uuid)
                          AND event_type='tournament_weekly_distribution_applied'
                          AND payload->>'revision_id'=:revision"""
                            ),
                            {"version": VERSION, "revision": item["revision_id"]},
                        )
                    ).scalar_one()
                    if audit != 1:
                        raise ValueError(
                            "The committed revision has no unique audit event"
                        )
        receipt = {
            "status": "COMMITTED_VERIFIED",
            "plan_sha256": plan_hash,
            "source_commit": source["commit"],
            "actor_id": ACTOR,
            "results": results,
            "verification": verification,
            "before_sha256": digest(before),
            "after_sha256": digest(after),
        }
        save_json(args.output_dir, "receipt.json", receipt)
        return receipt
    finally:
        await engine.dispose()


def read_receipt(path: Path, expected_hash: str) -> dict:
    """Only a digest-bound receipt produced by the successful apply is usable."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash or ""):
        raise ValueError("The full receipt SHA-256 is required")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("The receipt does not match its reviewed SHA-256")
    receipt = json.loads(raw)
    if (
        receipt.get("status") != "COMMITTED_VERIFIED"
        or receipt.get("actor_id") != ACTOR
    ):
        raise ValueError("The receipt is not a verified authorized application")
    actual = receipt.get("results")
    if not isinstance(actual, list) or len(actual) != len(TARGETS):
        raise ValueError("Receipt scope is invalid")
    by_tournament = {str(item.get("tournament_id")): item for item in actual}
    if len(by_tournament) != len(TARGETS):
        raise ValueError("Receipt contains a duplicate tournament")
    for target in TARGETS:
        item = by_tournament.get(target["id"])
        if (
            not item
            or item.get("version_id") != VERSION
            or item.get("edition_year") != 2026
            or item.get("week") != target["week"]
            or item.get("line_count") != target["line_count"]
            or Decimal(str(item.get("total"))) != Decimal(target["total"])
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("plan_hash") or ""))
        ):
            raise ValueError("Receipt differs from the authorized scope")
        UUID(str(item.get("revision_id")))
    return receipt


async def rollback_plan(
    session: AsyncSession, source: dict, receipt: dict, receipt_hash: str
) -> dict:
    await authorized_actor(session)
    by_tournament = {item["tournament_id"]: item for item in receipt["results"]}
    targets = []
    for target in TARGETS:
        item = by_tournament[target["id"]]
        # A revision which already existed before this apply is not ours to undo.
        if item.get("idempotent") is True:
            targets.append({"target": target, "skip": "preexisting_idempotent"})
            continue
        current = (
            (
                await session.execute(
                    text(
                        """SELECT id::text AS id,
            tournament_id::text AS tournament_id, financial_version_id::text AS version_id,
            previous_effective_revision_id::text AS previous_revision_id, plan_hash, is_current
            FROM budget_tournament_weekly_revisions WHERE id=CAST(:id AS uuid)"""
                    ),
                    {"id": item["revision_id"]},
                )
            )
            .mappings()
            .first()
        )
        if (
            not current
            or current["is_current"] is not True
            or current["tournament_id"] != target["id"]
            or current["version_id"] != VERSION
            or current["plan_hash"] != item["plan_hash"]
        ):
            raise ValueError(
                "The applied revision is no longer current; inspect history"
            )
        targets.append({"target": target, "current": dict(current)})
    return {
        "source": source,
        "actor_id": ACTOR,
        "version_id": VERSION,
        "receipt_sha256": receipt_hash,
        "targets": targets,
    }


async def verify_rollback(session: AsyncSession, plan: dict) -> dict:
    """Ensure previous revisions are restored and all effective plans reconcile."""
    lines = await budgets.list_budget_lines(
        session, version_id=VERSION, limit=5000, ensure_schema=False
    )
    ids = [str(row["id"]) for row in lines]
    raw = await budgets.list_monthly_plan_for_lines(
        session, line_ids=ids, ensure_schema=False
    )
    effective, conflicts = await effective_plans_for_lines(
        session, version_id=VERSION, line_ids=ids, baseline_plans=raw
    )
    for item in plan["targets"]:
        if item.get("skip"):
            continue
        target, previous = item["target"], item["current"]["previous_revision_id"]
        restored = (
            await session.execute(
                text(
                    """SELECT id::text FROM
            budget_tournament_weekly_revisions WHERE edition_year=2026
            AND tournament_id=CAST(:tid AS uuid) AND is_current"""
                ),
                {"tid": target["id"]},
            )
        ).scalar_one_or_none()
        if restored != previous or target["id"] in conflicts:
            raise ValueError("The previous revision did not restore cleanly")
        if previous is None:
            for row in lines:
                if str(row.get("tournament_id") or "") == target["id"]:
                    lid = str(row["id"])
                    if effective.get(lid, {}) != raw.get(lid, {}):
                        raise ValueError("The original plan was not restored")
    return {"previous_revisions_restored": True, "financial_history_preserved": True}


async def verify_deactivation_audits(session: AsyncSession, results: list) -> None:
    for item in results:
        audit = (
            await session.execute(
                text(
                    """SELECT count(*) FROM budget_version_audit_log
            WHERE budget_version_id=CAST(:version AS uuid)
              AND event_type='tournament_weekly_distribution_deactivated'
              AND payload->>'revision_id'=:revision
              AND actor_empleado_id=CAST(:actor AS uuid)"""
                ),
                {"version": VERSION, "revision": item["revision_id"], "actor": ACTOR},
            )
        ).scalar_one()
        if audit != 1:
            raise ValueError("The deactivated revision has no unique audit event")


async def deactivate_run(args: argparse.Namespace) -> dict:
    from samchat.budgets.tournament_weekly_overlay import deactivate_tournament_overlay

    if not args.receipt_file or not args.expected_receipt_sha256:
        raise ValueError("Deactivation requires a verified receipt and its SHA-256")
    receipt = read_receipt(args.receipt_file, args.expected_receipt_sha256)
    source = source_identity()
    if args.apply:
        if not args.expected_plan_sha256 or not args.expected_commit:
            raise ValueError(
                "Deactivation requires reviewed plan SHA-256 and source commit"
            )
        require_serving_source(args.expected_commit)
    engine = create_async_engine(database_url(args), hide_parameters=True)
    install_sql_guard(engine, applying=args.apply)
    try:
        async with AtomicSession(engine) as session:
            async with session.begin():
                isolation = (
                    "SERIALIZABLE" if args.apply else "REPEATABLE READ READ ONLY"
                )
                await session.execute(
                    text(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
                )
                await session.execute(text("SET LOCAL statement_timeout='60s'"))
                plan = await rollback_plan(
                    session, source, receipt, args.expected_receipt_sha256
                )
                plan_hash = digest(plan)
                before = await baseline(session)
                filename = (
                    "deactivation-fresh-preview.json"
                    if args.apply
                    else "deactivation-preview.json"
                )
                save_json(
                    args.output_dir,
                    filename,
                    {
                        "status": "DEACTIVATION_PREVIEW",
                        "plan_sha256": plan_hash,
                        "plan": plan,
                    },
                )
                if not args.apply:
                    return {
                        "status": "DEACTIVATION_PREVIEW",
                        "plan_sha256": plan_hash,
                        "source_commit": source["commit"],
                    }
                if plan_hash != args.expected_plan_sha256:
                    raise ValueError(
                        "Deactivation plan changed; inspect a fresh preview"
                    )
                save_json(args.output_dir, "deactivation-before.json", before)
                results = []
                for item in plan["targets"]:
                    if item.get("skip"):
                        continue
                    current, target = item["current"], item["target"]
                    results.append(
                        await deactivate_tournament_overlay(
                            session,
                            version_id=VERSION,
                            tournament_id=target["id"],
                            expected_revision_id=current["id"],
                            expected_plan_hash=current["plan_hash"],
                            actor_empleado_id=ACTOR,
                        )
                    )
                verification = await verify_rollback(session, plan)
                await verify_deactivation_audits(session, results)
                if await baseline(session) != before:
                    raise ValueError("Original financial state or schedules changed")
                save_json(
                    args.output_dir,
                    "deactivation-precommit.json",
                    {
                        "status": "VERIFIED_PENDING_COMMIT",
                        "plan_sha256": plan_hash,
                        "results": results,
                        "verification": verification,
                    },
                )
        save_json(
            args.output_dir,
            "deactivation-committed-unverified.json",
            {
                "status": "COMMITTED_VERIFICATION_PENDING",
                "plan_sha256": plan_hash,
                "source_commit": source["commit"],
                "results": results,
            },
        )
        async with AsyncSession(engine) as session:
            async with session.begin():
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                after = await baseline(session)
                if after != before:
                    raise ValueError(
                        "Original source changed; inspect concurrent activity"
                    )
                verification = await verify_rollback(session, plan)
                await verify_deactivation_audits(session, results)
        result = {
            "status": "DEACTIVATED_VERIFIED",
            "plan_sha256": plan_hash,
            "source_commit": source["commit"],
            "actor_id": ACTOR,
            "results": results,
            "verification": verification,
            "before_sha256": digest(before),
            "after_sha256": digest(after),
        }
        save_json(args.output_dir, "deactivation-receipt.json", result)
        return result
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--expected-commit")
    parser.add_argument("--deactivate", action="store_true")
    parser.add_argument("--receipt-file", type=Path)
    parser.add_argument("--expected-receipt-sha256")
    args = parser.parse_args()
    try:
        operation = deactivate_run(args) if args.deactivate else run(args)
        print(json.dumps(asyncio.run(operation), ensure_ascii=False, default=str))
    except Exception as exc:
        # Business validation errors are controlled text; do not expose
        # connection URLs, SQL parameters or employee data from driver errors.
        detail = f": {exc}" if isinstance(exc, (ValueError, PermissionError)) else ""
        print(f"Operation stopped: {type(exc).__name__}{detail}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
