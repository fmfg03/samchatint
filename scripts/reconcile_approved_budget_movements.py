#!/usr/bin/env python3
"""Fixed, hash-bound reconciliation of sixteen already-classified movements.

Production application requires separate human approval of the final diff and
exact batch. Preview is read-only; restore mode targets one disposable database.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from uuid import UUID

from dotenv import dotenv_values
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from import_historical_explicit import digest, save  # noqa: E402

import devnous.gastos  # noqa: E402,F401

ARTIFACTS = Path("/root/samchat/tests/artifacts/dashboard-technical-closeout-20261106")
CONFIG = "config/budget_approved_reconciliation_20261106.json"
CONFIG_SHA = "882467b95bbaf2b1fa890e6b323cbdee6c73964215a201410154a6fd9ff7993c"
CSV_SHA = "cc54537c8f838a8953f1d7420702f1ff153b56593d0e6fe46656936ab9540284"
STORY = "docs/roadmap/budget-approved-reconciliation-20261106-story.md"
SPEC = "docs/roadmap/budget-approved-reconciliation-20261106-spec.md"
ACTOR = "e3d13040-2360-420f-98a1-516440ef63c3"
VERSION = "64d963a7-61b1-402a-b58a-7efcb96cd71a"
EVENT = "approved_movement_reconciled"
PRODUCTION_ENV = Path("/etc/samchat/samchat.env")
FINAL_APPROVAL = "aprobacion-final-lote.json"
RESTORE_DB = "samchat_approved_reconciliation_restore_20261106"
RESTORE_URL = (
    "postgresql+asyncpg://postgres@/"
    + RESTORE_DB
    + "?host=/tmp/samchat-approved-reconciliation-restore-20261106&port=55485"
)
CODE = (
    "scripts/reconcile_approved_budget_movements.py",
    "scripts/import_historical_explicit.py",
    "src/samchat/budgets/reconciliation.py",
    "src/samchat/budgets/service.py",
    "src/samchat/budgets/tournament_weekly_overlay.py",
    "src/samchat/budgets/distribution.py",
    "src/devnous/gastos/routes/admin_routes.py",
    "src/devnous/gastos/routes/admin_budget_routes.py",
    "src/devnous/gastos/routes/admin_budget_ui.py",
    "src/devnous/gastos/services/tournament_authority_service.py",
    "src/devnous/gastos/services/tournament_phase_service.py",
    CONFIG,
    STORY,
    SPEC,
)
PROTECTED = (
    "documentos",
    "expense_reports",
    "accounting_polizas",
    "accounting_poliza_lines",
    "cuentas_contables",
    "tournaments",
    "budget_concepts",
    "budget_versions",
    "budget_lines",
    "budget_line_monthly_plan",
    "budget_line_monthly_allocations",
    "budget_tournament_weekly_revisions",
    "budget_tournament_weekly_lines",
    "budget_approvals",
    "budget_cfdi_income_links",
    "payment_run_closures",
    "payment_run_closure_items",
)
TABLES = (*PROTECTED, "budget_movement_assignments", "budget_version_audit_log")


def core_module():
    from samchat.budgets import reconciliation

    return reconciliation


def _file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assignments(data):
    return [
        {k: row[k] for k in ("tournament_id", "movement_key", "budget_line_id")}
        for row in data["assignments"]
    ]


def inputs():
    """Require original approved story/spec and the exact reviewed candidate CSV."""
    if _file_sha(ROOT / CONFIG) != CONFIG_SHA:
        raise ValueError("Fixed reconciliation configuration changed")
    data = json.loads((ROOT / CONFIG).read_text())
    story = json.loads((ARTIFACTS / "aprobacion-historia.json").read_text())
    spec = json.loads((ARTIFACTS / "aprobacion-especificacion.json").read_text())
    if (
        story.get("answer") != "Aprobar historia de reparación"
        or spec.get("answer") != "aprobado"
        or _file_sha(ROOT / STORY) != data["story_sha256"]
        or _file_sha(ARTIFACTS / "historia.md") != data["story_sha256"]
        or story.get("approved_sha256") != data["story_sha256"]
        or _file_sha(ROOT / SPEC) != data["spec_sha256"]
        or _file_sha(ARTIFACTS / "especificacion.md") != data["spec_sha256"]
        or spec.get("spec_sha256") != data["spec_sha256"]
        or _file_sha(ARTIFACTS / "16-propuestas-nuevas.csv") != CSV_SHA
        or data["csv_sha256"] != CSV_SHA
        or data["budget_version_id"] != VERSION
        or data["actor_empleado_id"] != ACTOR
        or data["edition_year"] != 2026
    ):
        raise ValueError("Approved source, specification or named actor changed")
    with (ARTIFACTS / "16-propuestas-nuevas.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    selected = data["assignments"]
    if len(selected) != 16 or len({r["movement_key"] for r in selected}) != 16:
        raise ValueError("Exactly sixteen distinct movements required")
    projects = Counter(r["tournament_id"] for r in selected)
    if projects != {
        "16793f0e-4697-4a28-8f42-b563da8cc7d8": 6,
        "67c556df-f953-4f99-8cab-87ed4d111b05": 10,
    }:
        raise ValueError("Fixed Liga/Copa scope required")
    csv_rows = {r["movement_key"]: r for r in rows}
    for selected_row in selected:
        source = csv_rows.get(selected_row["movement_key"], {})
        if any(
            selected_row[key] != source.get(csv_key)
            for key, csv_key in (
                ("budget_line_id", "budget_line_id"),
                ("budget_concept_id", "concept_id"),
                ("phase", "phase"),
                ("kind", "kind"),
                ("document_id", "document_id"),
            )
        ):
            raise ValueError("Fixed movement identity differs from original CSV")
        for key in ("budget_line_id", "budget_concept_id", "tournament_id"):
            UUID(selected_row[key])
        if not re.fullmatch(
            r"(?:accounting|expense):[0-9a-f-]{36}", selected_row["movement_key"]
        ):
            raise ValueError("Specific canonical movement key required")
        UUID(selected_row["movement_key"].split(":", 1)[1])
        if selected_row.get("expense_id") != (source.get("expense_id") or None):
            raise ValueError("Specific expense identity differs from original CSV")
    return data


def source_identity():
    def git(ref):
        return subprocess.check_output(
            ["git", "rev-parse", ref], cwd=ROOT, text=True
        ).strip()

    return {
        "commit": git("HEAD"),
        "tree": git("HEAD^{tree}"),
        "files": {name: _file_sha(ROOT / name) for name in CODE},
    }


def require_final_approval(data, source):
    """An external final receipt is never replaced by a manifest boolean."""
    try:
        approval = json.loads((ARTIFACTS / FINAL_APPROVAL).read_text())
    except (OSError, ValueError):
        raise ValueError(
            "Separate final diff and exact-batch approval required"
        ) from None
    expected = {
        "status": "FINAL_DIFF_AND_BATCH_APPROVED",
        "action": "apply_fixed_16_approved_budget_reconciliations",
        "config_sha256": CONFIG_SHA,
        "csv_sha256": CSV_SHA,
        "spec_sha256": data["spec_sha256"],
        "story_sha256": data["story_sha256"],
        "assignments_sha256": digest(assignments(data)),
        "actor_empleado_id": ACTOR,
        "reviewed_code_files": source["files"],
    }
    whole_source = (
        approval.get("reviewed_commit") == source["commit"]
        or approval.get("reviewed_tree_sha") == source["tree"]
    )
    if (
        not whole_source
        or not approval.get("answer")
        or not re.fullmatch("[0-9a-f]{64}", str(approval.get("final_diff_sha256", "")))
        or any(approval.get(k) != v for k, v in expected.items())
    ):
        raise ValueError("Final approval does not match this exact batch and source")
    return approval


def require_serving(expected_commit):
    if not re.fullmatch("[a-f0-9]{40}", expected_commit or "") or (
        source_identity()["commit"] != expected_commit
    ):
        raise ValueError("Exact reviewed serving commit required")
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", *CODE],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=ROOT, check=True)
    result = subprocess.check_output(
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
        text=True,
    )
    unit = dict(line.split("=", 1) for line in result.splitlines())
    if (
        unit["ActiveState"] != "active"
        or Path(unit["WorkingDirectory"]).resolve() != ROOT
        or Path("/proc", unit["MainPID"], "cwd").resolve() != ROOT
    ):
        raise ValueError("Operator is not the active serving source")


def serving_environment():
    """Read the active service environment in memory without exporting secrets."""

    def unit():
        try:
            result = subprocess.check_output(
                [
                    "systemctl",
                    "show",
                    "samchat-gastos.service",
                    "-p",
                    "MainPID",
                    "-p",
                    "ActiveState",
                ],
                text=True,
            )
            values = dict(line.split("=", 1) for line in result.splitlines())
            pid = values["MainPID"]
            if values["ActiveState"] != "active" or not pid.isdigit() or int(pid) <= 0:
                raise ValueError()
            return pid
        except Exception:
            raise ValueError("Active serving process unavailable") from None

    pid = unit()
    try:
        entries = Path("/proc", pid, "environ").read_bytes().split(b"\0")
        environment = {}
        for entry in entries:
            if b"=" not in entry:
                continue
            key, value = entry.split(b"=", 1)
            if key in (b"DATABASE_URL", b"POSTGRESQL_URL", b"SAMCHAT_ENV_FILE"):
                name = key.decode()
                if name in environment:
                    raise ValueError()
                environment[name] = value.decode()
    except Exception:
        raise ValueError("Serving database environment unavailable") from None
    if unit() != pid:
        raise ValueError("Serving process changed during backend validation")
    return pid, environment


def normalized_production_url(value):
    """Normalize only the two approved PostgreSQL driver prefixes in memory."""
    try:
        url = make_url(value)
        if (
            url.drivername not in ("postgresql", "postgresql+asyncpg")
            or url.database != "devnous_db"
            or not url.host
            or not url.username
            or any(
                key in url.query
                for key in ("host", "port", "dbname", "user", "password", "service")
            )
        ):
            raise ValueError()
        return url.set(drivername="postgresql+asyncpg", port=url.port or 5432)
    except Exception:
        raise ValueError("Invalid serving database binding") from None


def production_backend(args):
    """Bind file credentials and endpoint to the running service before connect."""
    if str(args.env_file) != str(PRODUCTION_ENV):
        raise ValueError("Canonical production environment file required")
    pid, environment = serving_environment()
    if (
        environment.get("SAMCHAT_ENV_FILE") != str(PRODUCTION_ENV)
        or not environment.get("DATABASE_URL")
        or not environment.get("POSTGRESQL_URL")
    ):
        raise ValueError("Serving database environment incomplete")
    try:
        file_url = dotenv_values(PRODUCTION_ENV).get("DATABASE_URL")
    except Exception:
        raise ValueError("Canonical database environment unavailable") from None
    url = normalized_production_url(environment["DATABASE_URL"])
    if url != normalized_production_url(file_url) or url != normalized_production_url(
        environment["POSTGRESQL_URL"]
    ):
        raise ValueError("Database does not match the active serving backend")
    return url, {
        "mode": "production",
        "service": "samchat-gastos.service",
        "main_pid": pid,
        "host": url.host,
        "port": url.port,
        "database": url.database,
        "environment_file": str(PRODUCTION_ENV),
    }


def sql_guard(statement, applying, *, parameters=None):
    """Only canonical inserts for the sixteen keys and one audit are allowed."""
    sql = re.sub(
        r"^(?:(?:/\*.*?\*/|--[^\n]*(?:\n|$))\s*)+", "", statement.strip(), flags=re.S
    ).strip()
    if ";" in sql.rstrip(";") or re.search(
        r"\b(CREATE|ALTER|DROP|TRUNCATE|DELETE|GRANT|REVOKE|CALL)\b", sql, re.I
    ):
        raise ValueError("DDL, deletion and compound SQL forbidden")
    if re.match(r"WITH\b", sql, re.I) and re.search(r"\b(INSERT|UPDATE)\b", sql, re.I):
        raise ValueError("Mutating CTE forbidden")
    match = re.match(r"INSERT\s+INTO\s+([a-z_]+)\b", sql, re.I)
    if match:
        params = parameters or {}
        if not applying:
            raise ValueError("Read-only preview cannot write")
        if re.search(r"\bDO\s+UPDATE\b", sql, re.I):
            raise ValueError("Reconciliation may not update an existing assignment")
        table = match[1].lower()
        if table == "budget_movement_assignments":
            wanted = {r["movement_key"]: r for r in FIXED_ASSIGNMENTS}
            row = wanted.get(params.get("movement_key"))
            if (
                not row
                or str(params.get("budget_version_id")) != VERSION
                or str(params.get("budget_line_id")) != row["budget_line_id"]
                or str(params.get("budget_concept_id")) != row["budget_concept_id"]
                or str(params.get("assigned_by_empleado_id")) != ACTOR
            ):
                raise ValueError("Assignment insert outside fixed reviewed scope")
        elif table == "budget_version_audit_log":
            if (
                str(params.get("budget_version_id")) != VERSION
                or params.get("event_type") != EVENT
                or str(params.get("actor_empleado_id")) != ACTOR
                or params.get("from_status") != "approved"
                or params.get("to_status") != "approved"
            ):
                raise ValueError("Audit insert outside fixed reconciliation owner")
            try:
                payload = json.loads(params["payload"])
                wanted = {
                    r["movement_key"]: r["budget_line_id"] for r in FIXED_ASSIGNMENTS
                }
                actual = {
                    r["movement_key"]: r["budget_line_id"]
                    for r in payload["assignments"]
                }
                if (
                    actual != wanted
                    or len(payload["assignments"]) != 16
                    or payload.get("actor_empleado_id") != ACTOR
                    or payload.get("reason") != FIXED_REASON
                    or not re.fullmatch("[0-9a-f]{64}", payload.get("plan_sha256", ""))
                ):
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    "Audit payload outside fixed reconciliation batch"
                ) from None
        else:
            raise ValueError("Write outside canonical reconciliation tables")
    elif not re.match(
        r"(?:SELECT|WITH|SET|SHOW|SAVEPOINT|RELEASE|ROLLBACK)\b", sql, re.I
    ):
        raise ValueError("Unsupported or mutating SQL")
    if not applying and (
        re.search(r"\bFOR\s+(UPDATE|SHARE)\b", sql, re.I)
        or re.search(r"pg_advisory_(?:xact_)?lock", sql, re.I)
    ):
        raise ValueError("Read-only preview cannot lock business rows")


async def snapshot(session):
    return {
        table: list(
            (
                await session.execute(
                    text(
                        "SELECT to_jsonb(t) FROM "
                        + table
                        + " t ORDER BY to_jsonb(t)::text"
                    )
                )
            ).scalars()
        )
        for table in TABLES
    }


def _desired(data):
    return {r["movement_key"]: r for r in data["assignments"]}


def completed_core_hash(state, data):
    wanted = _desired(data)
    rows = [
        r
        for r in state["budget_movement_assignments"]
        if r["budget_version_id"] == VERSION and r["movement_key"] in wanted
    ]
    if not rows:
        return None
    if len(rows) != 16 or any(
        any(
            str(r.get(k)) != str(wanted[r["movement_key"]][k])
            for k in ("budget_line_id", "budget_concept_id")
        )
        or r.get("assigned_by_empleado_id") != ACTOR
        for r in rows
    ):
        raise ValueError("Partial or conflicting fixed assignment batch")
    matches = []
    for audit in state["budget_version_audit_log"]:
        p = audit.get("payload") or {}
        records = p.get("assignments") or []
        if (
            audit.get("budget_version_id") == VERSION
            and audit.get("event_type") == EVENT
            and audit.get("actor_empleado_id") == ACTOR
            and p.get("reason") == data["reason"]
            and len(records) == 16
            and {x.get("movement_key"): x.get("budget_line_id") for x in records}
            == {k: x["budget_line_id"] for k, x in wanted.items()}
            and all(
                x.get("assignment_id")
                == next(r["id"] for r in rows if r["movement_key"] == x["movement_key"])
                for x in records
            )
            and re.fullmatch("[0-9a-f]{64}", str(p.get("plan_sha256", "")))
        ):
            matches.append(p["plan_sha256"])
    if len(matches) != 1:
        raise ValueError("Exact canonical batch audit required for completed state")
    return matches[0]


def verify_preservation(before, after, data, *, completed):
    for table in PROTECTED:
        if before[table] != after[table]:
            raise ValueError("Protected financial source changed: " + table)
    old = {r["id"]: r for r in before["budget_movement_assignments"]}
    new = {r["id"]: r for r in after["budget_movement_assignments"]}
    if any(new.get(k) != v for k, v in old.items()):
        raise ValueError("Previously existing assignment changed")
    delta = [v for k, v in new.items() if k not in old]
    if len(delta) != (0 if completed else 16):
        raise ValueError("Unexpected assignment insert count")
    old_audits = Counter(digest(r) for r in before["budget_version_audit_log"])
    new_audits = Counter(digest(r) for r in after["budget_version_audit_log"])
    if old_audits - new_audits or sum((new_audits - old_audits).values()) != (
        0 if completed else 1
    ):
        raise ValueError("Audit history changed or unexpected audit inserted")
    plan_sha = completed_core_hash(after, data)
    return {
        "protected_preserved": True,
        "old_assignments_preserved": True,
        "assignment_count": 16,
        "new_assignment_count": len(delta),
        "core_plan_sha256": plan_sha,
    }


async def build_plan(session, data, source, backend):
    core = core_module()
    await core.require_reconciliation_actor(session, ACTOR, lock=False)
    before = await snapshot(session)
    existing = completed_core_hash(before, data)
    if existing:
        recovered = await core.recover_approved_movement_reconciliation(
            session,
            budget_version_id=VERSION,
            edition_year=2026,
            assignments=assignments(data),
            actor_empleado_id=ACTOR,
            reason=data["reason"],
            expected_plan_sha256=existing,
        )
        if not recovered or recovered["plan_sha256"] != existing:
            raise ValueError("Canonical recovery could not verify the completed batch")
        core_plan = {"plan_sha256": existing, "completed": True, "receipt": recovered}
    else:
        core_plan = await core.preview_approved_movement_reconciliation(
            session,
            budget_version_id=VERSION,
            edition_year=2026,
            assignments=assignments(data),
            actor_empleado_id=ACTOR,
            reason=data["reason"],
        )
        actual = {r["movement_key"]: r for r in core_plan["assignments"]}
        if set(actual) != set(_desired(data)) or any(
            any(
                actual[r["movement_key"]].get(k) != r[k]
                for k in (
                    "budget_line_id",
                    "budget_concept_id",
                    "tournament_id",
                    "phase",
                    "kind",
                )
            )
            for r in data["assignments"]
        ):
            raise ValueError(
                "Current canonical classification differs from fixed batch"
            )
    return {
        "operator_schema": data["schema_version"],
        "config_sha256": CONFIG_SHA,
        "csv_sha256": CSV_SHA,
        "source": source,
        "backend": backend,
        "before": before,
        "completed": bool(existing),
        "core_plan": core_plan,
    }


async def run(args):
    if not __debug__ or args.actor_id != ACTOR:
        raise ValueError("Named authorized actor and enabled checks required")
    if args.fail_after_insert is not None and (
        not (args.restore and args.apply) or not 1 <= args.fail_after_insert <= 17
    ):
        raise ValueError("Failure injection requires the fixed disposable restore")
    data, source = inputs(), source_identity()
    if args.apply and (
        not args.expected_hash or args.expected_commit != source["commit"]
    ):
        raise ValueError("Reviewed plan hash and exact commit required")
    if args.apply and not args.restore:
        require_final_approval(data, source)
        require_serving(args.expected_commit)
    if args.restore:
        url, backend = RESTORE_URL, {
            "mode": "restore",
            "database": RESTORE_DB,
            "socket": "/tmp/samchat-approved-reconciliation-restore-20261106",
            "port": 55485,
        }
    else:
        url, backend = production_backend(args)

    def recheck_source():
        if source_identity() != source or inputs() != data:
            raise ValueError("Source or approved inputs changed during operation")
        if not args.restore:
            if production_backend(args) != (url, backend):
                raise ValueError("Serving backend changed during operation")
            if args.apply:
                require_final_approval(data, source)
                require_serving(source["commit"])

    engine = create_async_engine(url, hide_parameters=True)
    write_scope = False
    insert_count = 0

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def guard(c, cu, statement, parameters, context, many):
        for bound in context.compiled_parameters or [{}]:
            sql_guard(statement, write_scope, parameters=bound)

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def fault(c, cu, statement, parameters, context, many):
        nonlocal insert_count
        if write_scope and re.match(r"\s*INSERT\s+INTO", statement, re.I):
            insert_count += 1
            if args.fail_after_insert == insert_count:
                raise RuntimeError("Injected disposable reconciliation failure")

    async def setup(connection, *, readonly):
        await connection.execute(
            text(
                "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
                + (" READ ONLY" if readonly else " READ WRITE")
            )
        )
        await connection.execute(text("SET LOCAL lock_timeout='5s'"))
        await connection.execute(text("SET LOCAL statement_timeout='120s'"))
        expected_db = RESTORE_DB if args.restore else "devnous_db"
        if await connection.scalar(text("SELECT current_database()")) != expected_db:
            raise ValueError("Unexpected connected database")

    try:
        # The core's public preview has a strict READ ONLY contract, even when
        # the CLI intends to apply after reviewing this fresh candidate.
        async with engine.connect() as connection:
            async with connection.begin():
                await setup(connection, readonly=True)
                async with AsyncSession(
                    bind=connection, join_transaction_mode="create_savepoint"
                ) as session:
                    plan = await build_plan(session, data, source, backend)
                    recheck_source()
                    ph = digest(plan)
                    save(args.output_dir, "preview-" + ph + ".json", plan)
        if not args.apply:
            return {
                "status": "PREVIEW",
                "hash": ph,
                "completed": plan["completed"],
                "assignment_count": 16,
                "core_plan_sha256": plan["core_plan"]["plan_sha256"],
            }
        if ph != args.expected_hash:
            raise ValueError("Plan drifted; prepare a fresh preview")
        async with engine.connect() as connection:
            async with connection.begin():
                await setup(connection, readonly=False)
                write_scope = True
                async with AsyncSession(
                    bind=connection, join_transaction_mode="create_savepoint"
                ) as session:
                    if await snapshot(session) != plan["before"]:
                        raise ValueError(
                            "Source changed between preview and application"
                        )
                    recheck_source()
                    receipt = (
                        await core_module().apply_approved_movement_reconciliation(
                            session,
                            budget_version_id=VERSION,
                            edition_year=2026,
                            assignments=assignments(data),
                            actor_empleado_id=ACTOR,
                            reason=data["reason"],
                            expected_plan_sha256=plan["core_plan"]["plan_sha256"],
                        )
                    )
                    if args.fail_after_insert is not None:
                        raise RuntimeError(
                            "Requested disposable failure point was not reached"
                        )
                    checked = verify_preservation(
                        plan["before"],
                        await snapshot(session),
                        data,
                        completed=plan["completed"],
                    )
                    recheck_source()
                    save(
                        args.output_dir,
                        "pending-" + ph + ".json",
                        {
                            "hash": ph,
                            "core": receipt,
                            "verification": checked,
                        },
                    )
                    # Only release this session's savepoint. The enclosing
                    # connection transaction owns the atomic batch commit.
                    await session.commit()
        write_scope = False
        save(
            args.output_dir,
            "committed-" + ph + ".json",
            {
                "hash": ph,
                "core": receipt,
            },
        )
        async with AsyncSession(engine) as session:
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            checked = verify_preservation(
                plan["before"],
                await snapshot(session),
                data,
                completed=plan["completed"],
            )
        result = {
            "status": "COMMITTED_VERIFIED",
            "hash": ph,
            "source": source,
            "actor_empleado_id": ACTOR,
            "core": receipt,
            "verification": checked,
        }
        save(args.output_dir, "receipt-" + ph + ".json", result)
        return result
    finally:
        await engine.dispose()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--env-file", type=Path, default=PRODUCTION_ENV)
    p.add_argument("--actor-id", default=ACTOR)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--restore", action="store_true")
    p.add_argument("--expected-hash")
    p.add_argument("--expected-commit")
    p.add_argument("--fail-after-insert", type=int)
    return p


_FIXED_CONFIG = json.loads((ROOT / CONFIG).read_text())
FIXED_ASSIGNMENTS = _FIXED_CONFIG["assignments"]
FIXED_REASON = _FIXED_CONFIG["reason"]

if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(parser().parse_args())), default=str))
