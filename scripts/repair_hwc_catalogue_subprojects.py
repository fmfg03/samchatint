#!/usr/bin/env python3
"""Hash-bound HWC catalogue scope repair through existing budget owners.

Preview is read-only. Production application requires the exact serving release;
restore application accepts one fixed disposable database only. No schema writes.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from contextvars import ContextVar
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from import_historical_explicit import digest, save  # noqa: E402
from repair_tournament_dashboard_closeout import verify_effective  # noqa: E402

import devnous.gastos  # noqa: E402,F401
from samchat.budgets import service  # noqa: E402
from samchat.budgets import tournament_weekly_overlay as overlay  # noqa: E402

ACTOR = "e3d13040-2360-420f-98a1-516440ef63c3"
VERSION = "64d963a7-61b1-402a-b58a-7efcb96cd71a"
HWC = "c3b247f1-e3fc-4f4a-8995-56a5880f86a6"
ZERO_LINE = "0728a2f8-a64d-49fe-b53b-c2e93762b9c2"
CONFIG = "config/hwc_catalogue_subprojects_20261105.json"
CONFIG_SHA = "7635fc4d97811303b3ebc1ea5c68f9bf05cbb8376cef4161eace4466a9a88564"
SPEC = "docs/roadmap/hwc-catalogue-subprojects-20261105-spec.md"
ARTIFACTS = Path("/root/samchat/tests/artifacts/hwc-subproject-20261105")
PRODUCTION_ENV = Path("/etc/samchat/samchat.env")
RESTORE_DB = "samchat_hwc_subproject_restore_20261105"
RESTORE_URL = (
    "postgresql+asyncpg://postgres@/"
    + RESTORE_DB
    + "?host=/tmp/samchat-hwc-subproject-restore-proxy-20261105&port=55484"
)
EVENT = "hwc_catalogue_subprojects_20261105"
CODE = (
    "scripts/repair_hwc_catalogue_subprojects.py",
    "scripts/repair_tournament_dashboard_closeout.py",
    "scripts/import_historical_explicit.py",
    "scripts/apply_provisional_approval_policy.py",
    "src/samchat/budgets/service.py",
    "src/samchat/budgets/tournament_weekly_overlay.py",
    "src/samchat/budgets/distribution.py",
    "src/devnous/gastos/services/tournament_phase_service.py",
    "src/devnous/gastos/routes/admin_routes.py",
    CONFIG,
    SPEC,
)
PROTECTED = (
    "documentos",
    "expense_reports",
    "accounting_polizas",
    "accounting_poliza_lines",
    "cuentas_contables",
    "budget_cfdi_income_links",
    "budget_movement_assignments",
    "budget_line_monthly_plan",
    "budget_line_monthly_allocations",
    "tournaments",
)
TABLES = (
    *PROTECTED,
    "budget_concepts",
    "budget_versions",
    "budget_lines",
    "budget_tournament_weekly_revisions",
    "budget_tournament_weekly_lines",
    "budget_approvals",
    "budget_version_audit_log",
)
WRITE_TABLES = frozenset(TABLES) - frozenset(PROTECTED)
APPROVED_CONCEPT_IDS = frozenset(
    r["concept_id"] for r in json.loads((ROOT / CONFIG).read_text())["scope_updates"]
)
SCOPE = ContextVar("hwc_catalogue_owner_scope", default=None)


def inputs():
    """Bind approved source bytes and exact identities, never editable lineage."""
    raw = (ROOT / CONFIG).read_bytes()
    if hashlib.sha256(raw).hexdigest() != CONFIG_SHA:
        raise ValueError("Approved repair configuration changed")
    data = json.loads(raw)
    approval = data["approval"]
    if (
        approval != json.loads((ARTIFACTS / "approval.json").read_text())
        or approval["status"] != "STORY_AND_SPEC_APPROVED"
        or approval["answer"] != "continua"
        or hashlib.sha256((ROOT / SPEC).read_bytes()).hexdigest()
        != approval["spec_sha256"]
        or hashlib.sha256((ARTIFACTS / "plan.json").read_bytes()).hexdigest()
        != approval["plan_sha256"]
        or hashlib.sha256(Path(data["source"]).read_bytes()).hexdigest()
        != data["source_sha256"]
        or data["source_sha256"] != approval["source_sha256"]
    ):
        raise ValueError("Approved specification, plan or original source changed")
    ids = [r["concept_id"] for r in data["scope_updates"]]
    if (
        len(ids) != 74
        or len(set(ids)) != 74
        or data["tournament_id"] != HWC
        or data["version_id"] != VERSION
        or data["actor_empleado_id"] != ACTOR
        or len(data["zero_line_phase_updates"]) != 1
        or data["zero_line_phase_updates"][0]["line_id"] != ZERO_LINE
        or Decimal(data["zero_line_phase_updates"][0]["amount"]) != 0
        or any(
            not r["scope_after"] or r["active"] is not True
            for r in data["scope_updates"]
        )
    ):
        raise ValueError("Exact approved seventy-four scopes and zero line required")
    return data


def source_identity():
    return {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "files": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in CODE},
    }


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


def sql_guard(statement, applying, *, parameters=None, owner_scope=None):
    """Reject schema/operational writes; row scopes supplement final exact diff."""
    sql = re.sub(
        r"^(?:(?:/\*.*?\*/|--[^\n]*(?:\n|$))\s*)+", "", statement.strip(), flags=re.S
    ).strip()
    if ";" in sql.rstrip(";") or re.search(
        r"\b(CREATE|ALTER|DROP|TRUNCATE|DELETE|GRANT|REVOKE|CALL)\b", sql, re.I
    ):
        raise ValueError("DDL, deletion or compound SQL forbidden")
    if re.match(r"WITH\b", sql, re.I) and re.search(r"\b(INSERT|UPDATE)\b", sql, re.I):
        raise ValueError("Mutating CTE forbidden")
    match = re.match(r"(INSERT\s+INTO|UPDATE)\s+([a-z_]+)\b", sql, re.I)
    if match:
        table = match[2].lower()
        if not applying or table not in WRITE_TABLES:
            raise ValueError("Write outside approved budget owners")
        if table == "budget_concepts" and (
            match[1].upper() != "UPDATE"
            or owner_scope not in APPROVED_CONCEPT_IDS
            or (parameters or {}).get("concept_id") != owner_scope
        ):
            raise ValueError("Catalogue write outside exact canonical owner scope")
        if table == "budget_lines" and (
            match[1].upper() != "UPDATE"
            or str((parameters or {}).get("line_id")) != ZERO_LINE
        ):
            raise ValueError("Only the approved zero line may change")
    elif not re.match(
        r"(?:SELECT|WITH|SET|SHOW|SAVEPOINT|RELEASE|ROLLBACK)\b", sql, re.I
    ):
        raise ValueError("Unsupported SQL")
    if not applying and (
        re.search(r"\bFOR\s+(UPDATE|SHARE)\b", sql, re.I)
        or re.search(r"pg_advisory_(?:xact_)?lock", sql, re.I)
    ):
        raise ValueError("Preview cannot lock business rows")


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


def scope_metadata(concept, label, tournament):
    return service._merge_budget_concept_scope_metadata(
        concept.get("metadata") or {},
        [label],
        tournament_etapas=tournament.get("etapas") or [],
        tournament_categorias=tournament.get("categorias") or [],
    )


def _current_revision(state):
    current = [
        r
        for r in state["budget_tournament_weekly_revisions"]
        if r["tournament_id"] == HWC and r["is_current"]
    ]
    if (
        len(current) != 1
        or current[0]["week_number"] != 52
        or current[0]["financial_root_version_id"] != VERSION
    ):
        raise ValueError("Exact current HWC week52 revision required")
    return current[0]


def validate_state(state, data):
    """Accept the disclosed initial state or the completely repaired state only."""
    tournament = next(r for r in state["tournaments"] if r["id"] == HWC)
    version = next(r for r in state["budget_versions"] if r["id"] == VERSION)
    if version["status"] != "approved" or "Borrador" not in (
        version.get("notes") or ""
    ):
        raise ValueError("Approved Borrador financial source required")
    concepts = {r["id"]: r for r in state["budget_concepts"]}
    expected_active = {r["concept_id"] for r in data["scope_updates"]} | {
        r["id"] for r in data["untouched_active_concepts"]
    }
    if {
        r["id"] for r in concepts.values() if r["tournament_id"] == HWC and r["active"]
    } != expected_active:
        raise ValueError("HWC catalogue activity or identity drifted")
    repaired = []
    for target in data["scope_updates"]:
        current = concepts.get(target["concept_id"])
        if (
            not current
            or current["tournament_id"] != HWC
            or current["active"] is not True
            or current["concept_name"] != target["concept_name"]
        ):
            raise ValueError("Approved catalogue identity changed")
        metadata = current.get("metadata") or {}
        wanted = scope_metadata(current, target["scope_after"], tournament)
        complete = all(
            metadata.get(k) == wanted[k] for k in service._BUDGET_SCOPE_METADATA_KEYS
        )
        initial = metadata.get("scope_mode") == "global" and all(
            not metadata.get(k)
            for k in service._BUDGET_SCOPE_METADATA_KEYS
            if k != "scope_mode"
        )
        if not initial and not complete:
            raise ValueError(
                "Catalogue scope changed outside the approved initial state"
            )
        repaired.append(complete)
    target = data["zero_line_phase_updates"][0]
    line = next(r for r in state["budget_lines"] if r["id"] == ZERO_LINE)
    if (
        line["tournament_id"] != HWC
        or line["budget_version_id"] != VERSION
        or line["budget_concept_id"] != target["budget_concept_id"]
        or Decimal(str(line["budget_amount"])) != 0
    ):
        raise ValueError("Zero line financial identity drifted")
    if line["phase"] not in {target["phase_before"], target["phase_after"]}:
        raise ValueError("Zero line phase drifted")
    completed = all(repaired) and line["phase"] == target["phase_after"]
    if not completed and (any(repaired) or line["phase"] != target["phase_before"]):
        raise ValueError("Partial prior repair requires independent review")
    _current_revision(state)
    return completed


def preservation(before, after, data):
    """Permit scope keys, one zero-line phase and one HWC revision only."""
    for table in PROTECTED:
        if before[table] != after[table]:
            raise ValueError("Protected rows changed: " + table)
    tournament = next(r for r in before["tournaments"] if r["id"] == HWC)
    targets = {r["concept_id"]: r for r in data["scope_updates"]}
    old = {r["id"]: r for r in before["budget_concepts"]}
    new = {r["id"]: r for r in after["budget_concepts"]}
    if old.keys() != new.keys():
        raise ValueError("Catalogue identities changed")
    for key, row in old.items():
        expected, actual = dict(row), dict(new[key])
        if key in targets:
            expected["metadata"] = scope_metadata(
                row, targets[key]["scope_after"], tournament
            )
            expected.pop("updated_at", None)
            actual.pop("updated_at", None)
        if expected != actual:
            raise ValueError("Unexpected catalogue mutation: " + key)
    old_lines = {r["id"]: r for r in before["budget_lines"]}
    new_lines = {r["id"]: r for r in after["budget_lines"]}
    if old_lines.keys() != new_lines.keys():
        raise ValueError("Financial line identities changed")
    for key, row in old_lines.items():
        expected, actual = dict(row), dict(new_lines[key])
        if key == ZERO_LINE:
            expected["phase"] = data["zero_line_phase_updates"][0]["phase_after"]
            expected.pop("updated_at", None)
            actual.pop("updated_at", None)
        if expected != actual:
            raise ValueError("Unexpected financial field mutation: " + key)
    versions_before = {r["id"]: dict(r) for r in before["budget_versions"]}
    versions_after = {r["id"]: dict(r) for r in after["budget_versions"]}
    for versions in (versions_before, versions_after):
        versions[VERSION].pop("updated_at", None)
    if (
        versions_before != versions_after
        or versions_after[VERSION]["status"] != "approved"
    ):
        raise ValueError("Version or Borrador changed")
    for table in (
        "budget_approvals",
        "budget_version_audit_log",
        "budget_tournament_weekly_lines",
    ):
        if Counter(map(digest, before[table])) - Counter(map(digest, after[table])):
            raise ValueError("Existing history changed: " + table)
    old_revisions = {r["id"]: r for r in before["budget_tournament_weekly_revisions"]}
    new_revisions = {r["id"]: r for r in after["budget_tournament_weekly_revisions"]}
    if not old_revisions.keys() <= new_revisions.keys():
        raise ValueError("Weekly history removed")
    added = [r for key, r in new_revisions.items() if key not in old_revisions]
    for key, row in old_revisions.items():
        expected, actual = dict(row), dict(new_revisions[key])
        if added and row["tournament_id"] == HWC and row["is_current"]:
            if (
                actual.get("is_current") is not False
                or not actual.get("deactivated_at")
                or actual.get("deactivated_by_empleado_id") != ACTOR
                or actual.get("restored_revision_id") is not None
            ):
                raise ValueError("Unexpected HWC retirement")
            for field in (
                "is_current",
                "deactivated_at",
                "deactivated_by_empleado_id",
                "restored_revision_id",
            ):
                expected.pop(field, None)
                actual.pop(field, None)
        if expected != actual:
            raise ValueError("Unrelated weekly revision changed")
    if added and (
        len(added) != 1
        or added[0]["tournament_id"] != HWC
        or added[0]["week_number"] != 52
    ):
        raise ValueError("Only one HWC week52 renewal is permitted")
    if added:
        renewed_id = added[0]["id"]
        old_line_keys = {
            (r["revision_id"], r["financial_line_id"])
            for r in before["budget_tournament_weekly_lines"]
        }
        new_weekly = [
            r
            for r in after["budget_tournament_weekly_lines"]
            if (r["revision_id"], r["financial_line_id"]) not in old_line_keys
        ]
        hwc_line_ids = {
            r["id"]
            for r in before["budget_lines"]
            if r["budget_version_id"] == VERSION
            and r.get("tournament_id") == HWC
            and r.get("line_direction", "expense") == "expense"
        }
        if (
            len(new_weekly) != len(hwc_line_ids)
            or {r["financial_line_id"] for r in new_weekly} != hwc_line_ids
            or any(r["revision_id"] != renewed_id for r in new_weekly)
        ):
            raise ValueError("New weekly history escaped the exact HWC population")
    allowed_events = {
        EVENT,
        "status_changed",
        "line_updated",
        "tournament_weekly_distribution_applied",
        "tournament_weekly_distribution_deactivated",
    }
    for table in ("budget_approvals", "budget_version_audit_log"):
        prior_ids = {r["id"] for r in before[table]}
        for row in after[table]:
            if row["id"] in prior_ids:
                continue
            if row["budget_version_id"] != VERSION:
                raise ValueError("New approval or audit escaped financial version")
            if table == "budget_approvals":
                if (
                    row["approved_by_empleado_id"] != ACTOR
                    or row["approval_status"] != "approved"
                ):
                    raise ValueError("Unexpected budget approval")
            elif (
                row["actor_empleado_id"] != ACTOR
                or row["event_type"] not in allowed_events
            ):
                raise ValueError("Unexpected budget audit event")


async def build_plan(session, source, data):
    await overlay._require_budget_actor(session, ACTOR)
    await overlay._version(session, VERSION, require_selected=True)
    state = await snapshot(session)
    completed = validate_state(state, data)
    return {
        "source": source,
        "actor": ACTOR,
        "config_sha256": CONFIG_SHA,
        "before": state,
        "effective_before": await verify_effective(session, state),
        "completed": completed,
        "scope_count": 74,
        "zero_line_count": 1,
    }


async def apply_owners(session, plan, data, *, fail_after=None):
    if plan["completed"]:
        return {"idempotent": True, "revisions": []}

    def fault(index):
        if fail_after == index:
            raise RuntimeError("Injected restored HWC repair failure")

    for index, target in enumerate(data["scope_updates"], 1):
        token = SCOPE.set(target["concept_id"])
        try:
            await service.update_budget_concept(
                session,
                concept_id=target["concept_id"],
                scope_labels=[target["scope_after"]],
                actor_empleado_id=ACTOR,
                ensure_schema=False,
                commit=False,
            )
        finally:
            SCOPE.reset(token)
        fault(index)
    revision = _current_revision(plan["before"])
    await overlay.deactivate_tournament_overlay(
        session,
        version_id=VERSION,
        tournament_id=HWC,
        expected_revision_id=revision["id"],
        expected_plan_hash=revision["plan_hash"],
        actor_empleado_id=ACTOR,
    )
    fault(75)
    await service.transition_budget_version(
        session,
        version_id=VERSION,
        new_status="reforecast",
        actor_empleado_id=ACTOR,
        ensure_schema=False,
    )
    fault(76)
    await service.update_budget_line(
        session,
        line_id=ZERO_LINE,
        tournament_id=HWC,
        updates={"phase": data["zero_line_phase_updates"][0]["phase_after"]},
        actor_empleado_id=ACTOR,
        ensure_schema=False,
        commit=True,
    )
    fault(77)
    await service.transition_budget_version(
        session,
        version_id=VERSION,
        new_status="approved",
        actor_empleado_id=ACTOR,
        ensure_schema=False,
    )
    fault(78)
    preview = await overlay.preview_tournament_overlay(
        session, version_id=VERSION, tournament_id=HWC, week=52
    )
    renewed = await overlay.apply_tournament_overlay(
        session,
        version_id=VERSION,
        tournament_id=HWC,
        week=52,
        expected_plan_hash=preview["plan_hash"],
        actor_empleado_id=ACTOR,
    )
    fault(79)
    old = {r["id"]: r for r in plan["before"]["budget_concepts"]}
    await service._audit_budget_event(
        session,
        budget_version_id=VERSION,
        event_type=EVENT,
        actor_empleado_id=ACTOR,
        payload={
            "plan_sha256": digest(plan),
            "config_sha256": CONFIG_SHA,
            "source_sha256": data["source_sha256"],
            "scope_updates": [
                {
                    **target,
                    "metadata_before": old[target["concept_id"]].get("metadata") or {},
                }
                for target in data["scope_updates"]
            ],
            "zero_line_phase_updates": data["zero_line_phase_updates"],
            "previous_revision_id": revision["id"],
            "renewed_revision_id": renewed["revision_id"],
            "historical_scope_gaps": data["historical_scope_gaps"],
        },
    )
    fault(80)
    return {"idempotent": False, "revisions": [renewed]}


async def verify(session, plan, data):
    after = await snapshot(session)
    preservation(plan["before"], after, data)
    if (
        not validate_state(after, data)
        or await verify_effective(session, after) != plan["effective_before"]
    ):
        raise ValueError("Final classifications or weekly expense/income changed")
    audits = [
        row
        for row in after["budget_version_audit_log"]
        if row["event_type"] == EVENT
        and row["budget_version_id"] == VERSION
        and row["actor_empleado_id"] == ACTOR
        and row["payload"].get("config_sha256") == CONFIG_SHA
        and row["payload"].get("source_sha256") == data["source_sha256"]
        and {r["concept_id"] for r in row["payload"].get("scope_updates", [])}
        == {r["concept_id"] for r in data["scope_updates"]}
        and row["payload"].get("zero_line_phase_updates")
        == data["zero_line_phase_updates"]
    ]
    if len(audits) != 1:
        raise ValueError("Exact canonical repair audit required")
    return {
        "snapshot_sha256": digest(after),
        "scope_count": 74,
        "zero_line_count": 1,
        "protected_preserved": True,
        "effective_plans_and_income_preserved": True,
        "Borrador_preserved": True,
        "historical_scope_gaps_not_reclassified": data["historical_scope_gaps"],
    }


async def run(args):
    if not __debug__ or args.actor_id != ACTOR:
        raise ValueError("Named actor and enabled checks required")
    if args.fail_after is not None and (
        not (args.restore and args.apply) or not 1 <= args.fail_after <= 80
    ):
        raise ValueError("Failure injection requires disposable restore apply")
    data, source = inputs(), source_identity()
    if args.apply and (
        not args.expected_hash or args.expected_commit != source["commit"]
    ):
        raise ValueError("Reviewed plan hash and exact commit required")
    if args.apply and not args.restore:
        require_serving(args.expected_commit)
    if args.restore:
        target_url, backend = RESTORE_URL, {
            "mode": "restore",
            "database": RESTORE_DB,
            "socket": "/tmp/samchat-hwc-subproject-restore-proxy-20261105",
            "port": 55484,
        }
    else:
        target_url, backend = production_backend(args)

    def require_same_backend():
        if not args.restore:
            current_url, current_backend = production_backend(args)
            if current_url != target_url or current_backend != backend:
                raise ValueError("Serving backend changed; prepare a fresh preview")

    engine = create_async_engine(target_url, hide_parameters=True)
    target_db = RESTORE_DB if args.restore else "devnous_db"
    if engine.url.database != target_db:
        raise ValueError("Unexpected database")

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def guard(conn, cursor, statement, parameters, context, many):
        values = context.compiled_parameters[0] if context.compiled_parameters else {}
        sql_guard(statement, args.apply, parameters=values, owner_scope=SCOPE.get())

    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
                        + ("" if args.apply else " READ ONLY")
                    )
                )
                await connection.execute(text("SET LOCAL lock_timeout='5s'"))
                await connection.execute(text("SET LOCAL statement_timeout='120s'"))
                if (
                    await connection.scalar(text("SELECT current_database()"))
                    != target_db
                ):
                    raise ValueError("Unexpected connected database")
                async with AsyncSession(
                    bind=connection, join_transaction_mode="create_savepoint"
                ) as session:
                    if args.apply:
                        # Same order as catalogue and tournament scope writers.
                        await session.execute(
                            text(
                                "SELECT id FROM tournaments "
                                "WHERE id=CAST(:tid AS uuid) FOR SHARE"
                            ),
                            {"tid": HWC},
                        )
                        await session.execute(
                            text(
                                "SELECT pg_advisory_xact_lock("
                                "hashtextextended(:scope,0))"
                            ),
                            {"scope": f"budget-weekly:2026:{HWC}"},
                        )
                        for table, predicate in (
                            ("budget_versions", "id=CAST(:version AS uuid)"),
                            (
                                "budget_lines",
                                "budget_version_id=CAST(:version AS uuid)",
                            ),
                            (
                                "budget_tournament_weekly_revisions",
                                "financial_root_version_id=CAST(:version AS uuid)",
                            ),
                        ):
                            await session.execute(
                                text(
                                    "SELECT id FROM "
                                    + table
                                    + " WHERE "
                                    + predicate
                                    + " ORDER BY id FOR UPDATE"
                                ),
                                {"version": VERSION},
                            )
                        await session.execute(
                            text(
                                "SELECT id FROM budget_concepts "
                                "WHERE id=ANY(CAST(:ids AS uuid[])) "
                                "ORDER BY id FOR UPDATE"
                            ),
                            {
                                "ids": sorted(
                                    r["concept_id"] for r in data["scope_updates"]
                                )
                            },
                        )
                    plan = await build_plan(session, source, data)
                    plan["backend"] = backend
                    require_same_backend()
                    ph = digest(plan)
                    save(args.output_dir, "preview-" + ph + ".json", plan)
                    if not args.apply:
                        return {
                            "status": "PREVIEW",
                            "hash": ph,
                            "completed": plan["completed"],
                            "scope_count": 74,
                            "zero_line_count": 1,
                        }
                    if ph != args.expected_hash:
                        raise ValueError("Plan drifted; prepare a fresh preview")
                    result = await apply_owners(
                        session, plan, data, fail_after=args.fail_after
                    )
                    checked = await verify(session, plan, data)
                    if inputs() != data or source_identity() != source:
                        raise ValueError("Source/code changed before commit")
                    require_same_backend()
                    save(
                        args.output_dir,
                        "pending-" + ph + ".json",
                        {"hash": ph, "result": result, "verification": checked},
                    )
                    await session.commit()
        save(
            args.output_dir, "committed-" + ph + ".json", {"hash": ph, "result": result}
        )
        async with AsyncSession(engine) as session:
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            checked = await verify(session, plan, data)
        receipt = {
            "status": "COMMITTED_VERIFIED",
            "hash": ph,
            "source": source,
            "actor": ACTOR,
            "result": result,
            "verification": checked,
        }
        save(args.output_dir, "receipt-" + ph + ".json", receipt)
        return receipt
    finally:
        await engine.dispose()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--env-file", type=Path, default=PRODUCTION_ENV)
    p.add_argument("--actor-id", default=ACTOR)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--expected-hash")
    p.add_argument("--expected-commit")
    p.add_argument("--restore", action="store_true")
    p.add_argument("--fail-after", type=int)
    return p


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(parser().parse_args())), default=str))
