#!/usr/bin/env python3
"""Hash-bound, atomic execution of the approved 2026 tournament closeout.

Preview is READ ONLY. Writes use budget owners exclusively; production apply
requires the serving release. Restore mode has one fixed disposable database.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextvars import ContextVar
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
import devnous.gastos  # noqa: E402,F401
from samchat.budgets import service, tournament_weekly_overlay as overlay  # noqa: E402
from apply_provisional_approval_policy import database_url  # noqa: E402
from import_historical_explicit import digest, save  # noqa: E402

ACTOR = "e3d13040-2360-420f-98a1-516440ef63c3"
VERSION = "64d963a7-61b1-402a-b58a-7efcb96cd71a"
LIGA = "16793f0e-4697-4a28-8f42-b563da8cc7d8"
HWC = "c3b247f1-e3fc-4f4a-8995-56a5880f86a6"
COPA = "67c556df-f953-4f99-8cab-87ed4d111b05"
WEEKS = {LIGA: 40, HWC: 52, COPA: 52}
TOTALS = {LIGA: "3686827.60", HWC: "30384939.78", COPA: "17471291.92"}
ARTIFACTS = Path("/root/samchat/tests/artifacts/cierre-tableros-20261030")
PINS = {
    "approval.json": "c45b0b345fe73b9864fa3757b49cb27dc7ec943c36474d30e15b42ba75238e8b",
    "cuentas-finales-40.json": "8d56688b835e58b990f7ba38cd1619c81e059c79f1f3815b00e1fac7eb78c19e",
    "all-39-financial-review-enriched.json": "88d8eccbe8cd083b04ec82b320eb0c385eb26d3b9aee037bd1fae31baff67451",
    "weekly-overlays-before.json": "dd3f8cc09ce3ee9fffc7b7f62e91763bb5f205ef8114e69b5deb37971f1c92ad",
    "Revision_cierre_tableros_20261030.xlsx": "3c6c4c2254b46e7f804748e25736edbb4cdd036f2056376f804482008d6040f5",
}
ORIGINALS = {
    "/root/samchat/catalogo_presupuestal (3).xlsx": "75da03a5853199c3fab3db96699ed1950bb6b637b2702630b8bbcfb24e3d4eae",
    "/root/samchat/BGPSP.xls": "5e66cbf4825a985aebf6cfc7049639c0e259b09d76f5032d1cb58463f5955652",
}
CODE = (
    "scripts/repair_tournament_dashboard_closeout.py",
    "scripts/import_historical_explicit.py",
    "scripts/apply_provisional_approval_policy.py",
    "src/samchat/budgets/service.py",
    "src/samchat/budgets/tournament_weekly_overlay.py",
    "src/samchat/budgets/distribution.py",
    "config/tournament_dashboard_closeout_20261030_actions.json",
    "docs/roadmap/tournament-dashboard-closeout-20261030-spec.md",
    "docs/operations/tournament-dashboard-closeout-legacy-weeks-20261030.json",
    "docs/operations/tournament-dashboard-closeout-legacy-weeks-20261030.md",
)
RESTORE_DB = "samchat_dashboard_closeout_restore_20261030"
RESTORE_URL = (
    "postgresql+asyncpg://postgres@/"
    + RESTORE_DB
    + "?host=/tmp/samchat-dashboard-closeout-restore-proxy-20261030&port=55483"
)
PROTECTED = (
    "documentos",
    "expense_reports",
    "accounting_polizas",
    "accounting_poliza_lines",
    "budget_concepts",
    "cuentas_contables",
    "budget_cfdi_income_links",
)
WRITE_TABLES = {
    "budget_lines",
    "budget_versions",
    "budget_approvals",
    "budget_version_audit_log",
    "budget_tournament_weekly_revisions",
    "budget_tournament_weekly_lines",
    "budget_movement_assignments",
}
LEGACY_PATH = "docs/operations/tournament-dashboard-closeout-legacy-weeks-20261030.json"
LEGACY_SHA = "52a040cda18a9b8df7e2220a39677616229495f9d1eb8d0ac0c0edc22b9930c3"
LEGACY_IDS = frozenset(
    {
        "8a3fc932-1f22-40de-8880-097e26ba6777",
        "bfe9a652-d22d-41ac-b507-2b8c037072c0",
        "c0b8c328-cd2e-40a3-94e5-bafe6cd199b0",
        "d7f3a792-51e7-4b43-9c86-58d6e6045c01",
        "f6be74ea-e9ac-41f5-9700-b79babc45e54",
    }
)
CALENDAR_TABLES = {"budget_line_monthly_plan", "budget_line_monthly_allocations"}
LEGACY_SCOPE = ContextVar("closeout_legacy_owner_line", default=None)
EVENT = "tournament_dashboard_closeout_20261030"
LEGACY_EVENT = "tournament_closeout_legacy_calendar_regularized_20261030"


def legacy_plan():
    payload = (ROOT / LEGACY_PATH).read_bytes()
    if hashlib.sha256(payload).hexdigest() != LEGACY_SHA:
        raise ValueError("Approved five-calendar source changed")
    value = json.loads(payload)
    if {r["line_id"] for r in value["lines"]} != LEGACY_IDS or len(value["lines"]) != 5:
        raise ValueError("Exact five calendar identities required")
    return value


def inputs():
    """Read pinned bytes once; editable lineage metadata never grants authority."""
    values = {}
    for name, sha in PINS.items():
        payload = (ARTIFACTS / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != sha:
            raise ValueError("Approved evidence changed: " + name)
        if name.endswith(".json"):
            values[name] = json.loads(payload)
    for path, sha in ORIGINALS.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != sha:
            raise ValueError("Original source changed: " + path)
    approval = values["approval.json"]
    if (
        approval["status"] != "SPEC_AND_13_COPA_PROPOSALS_APPROVED"
        or approval["user_answer"] != "Aprobar plan y las 13 propuestas de Copa"
        or approval["workbook_sha256"] != PINS["Revision_cierre_tableros_20261030.xlsx"]
        or hashlib.sha256(
            (
                ROOT / "docs/roadmap/tournament-dashboard-closeout-20261030-spec.md"
            ).read_bytes()
        ).hexdigest()
        != approval["spec_sha256"]
    ):
        raise ValueError("Exact specification and Copa batch approval required")
    approved = service._closeout_actions()
    if approved["version_id"] != VERSION or approved["approval"] != approval:
        raise ValueError("Approved action identity differs")
    accounts = values["cuentas-finales-40.json"]
    rows = values["all-39-financial-review-enriched.json"]["rows"]
    movements = [
        r for r in rows if len(r.get("lineage_compatible_candidate_ids", [])) == 1
    ]
    if len(accounts) != 40 or len({r["id"] for r in accounts}) != 40:
        raise ValueError("Exact forty account destinations required")
    if len(movements) != 16 or sum(r["tournament_id"] == LIGA for r in movements) != 3:
        raise ValueError("Exact three Liga and thirteen Copa decisions required")
    for row in movements:
        if row["tournament_id"] not in {LIGA, COPA} or not row[
            "movement_key"
        ].startswith("accounting:"):
            raise ValueError("Outside approved accounting movements")
    by_key = {r["movement_key"]: r for r in approved["movements"]}
    if set(by_key) != {r["movement_key"] for r in movements}:
        raise ValueError("Approved sixteen movement keys differ")
    for row in movements:
        decision = by_key[row["movement_key"]]
        if (
            decision["target_line_id"] != row["lineage_compatible_candidate_ids"][0]
            or decision["source_concept_id"] != row["concept_id"]
        ):
            raise ValueError("Approved exact pair differs")
    if {
        (r["line_id"], r["target_account_code"], r["target_account_id"])
        for r in approved["accounts"]
    } != {(r["id"], r["proposed_account"], r["proposed_account_id"]) for r in accounts}:
        raise ValueError("Approved forty account pairs differ")
    return {
        "accounts": accounts,
        "legacy": legacy_plan(),
        "movements": movements,
        "overlays": values["weekly-overlays-before.json"],
    }


def source_identity():
    return {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "files": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in CODE},
    }


def require_serving(expected_commit):
    if (
        not re.fullmatch("[0-9a-f]{40}", expected_commit or "")
        or source_identity()["commit"] != expected_commit
    ):
        raise ValueError("Exact reviewed serving commit required")
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", *CODE],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *CODE], cwd=ROOT, check=True
    )
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


def sql_guard(statement, applying, *, budget_line_id=None, legacy_line=None):
    sql = statement.strip()
    sql = re.sub(
        r"^(?:(?:/\*.*?\*/|--[^\n]*(?:\n|$))\s*)+", "", sql, flags=re.S
    ).strip()
    calendar = re.match(
        r"DELETE\s+FROM\s+(budget_line_monthly_plan|budget_line_monthly_allocations)\s+WHERE\s+budget_line_id\s*=\s*\$1\s*$",
        sql,
        re.I,
    )
    if calendar:
        if (
            not applying
            or legacy_line not in LEGACY_IDS
            or str(budget_line_id) != legacy_line
        ):
            raise ValueError("Calendar replacement outside exact canonical owner scope")
        return
    if ";" in sql.rstrip(";") or re.search(
        r"\b(CREATE|ALTER|DROP|TRUNCATE|DELETE|GRANT|REVOKE|CALL)\b", sql, re.I
    ):
        raise ValueError("DDL, deletion or compound SQL forbidden")
    if re.match(r"WITH\b", sql, re.I) and re.search(r"\b(INSERT|UPDATE)\b", sql, re.I):
        raise ValueError("Mutating CTE forbidden")
    match = re.match(r"(?:INSERT\s+INTO|UPDATE)\s+([a-z_]+)\b", sql, re.I)
    if match:
        table = match[1].lower()
        if table in CALENDAR_TABLES:
            if (
                not applying
                or legacy_line not in LEGACY_IDS
                or str(budget_line_id) != legacy_line
            ):
                raise ValueError("Calendar write outside exact canonical owner scope")
        elif not applying or table not in WRITE_TABLES:
            raise ValueError("Write outside approved budget owners")
    elif not re.match(
        r"(?:SELECT|WITH|SET|SHOW|SAVEPOINT|RELEASE|ROLLBACK)\b", sql, re.I
    ):
        raise ValueError("Unsupported SQL")
    if not applying and re.search(r"\bFOR\s+(UPDATE|SHARE)\b", sql, re.I):
        raise ValueError("Preview cannot lock business rows")


async def rows(session, table):
    return list(
        (
            await session.execute(
                text(
                    "SELECT to_jsonb(t) AS row FROM "
                    + table
                    + " t ORDER BY to_jsonb(t)::text"
                )
            )
        )
        .scalars()
        .all()
    )


async def snapshot(session):
    result = {}
    for table in (
        *PROTECTED,
        *sorted(CALENDAR_TABLES),
        "budget_versions",
        "budget_lines",
        "budget_movement_assignments",
        "budget_tournament_weekly_revisions",
        "budget_tournament_weekly_lines",
        "budget_approvals",
        "budget_version_audit_log",
    ):
        result[table] = await rows(session, table)
    return result


def calendar_semantics(state, target, table):
    rows = [r for r in state.get(table, []) if r["budget_line_id"] == target["line_id"]]
    by_week = {str(r["month_number"]): r for r in rows}
    if len(rows) != 52 or len(by_week) != 52:
        raise ValueError("Calendar coverage changed")
    for week, expected in target["after_plan"].items():
        current = by_week[week]
        if table == "budget_line_monthly_plan":
            keys = (
                ("budget_expense_amount", "budget_expense_amount"),
                ("expected_income_amount", "expected_income_amount"),
            )
        else:
            keys = (("allocated_amount", "budget_expense_amount"),)
        if any(Decimal(str(current[a])) != Decimal(str(expected[b])) for a, b in keys):
            raise ValueError("Calendar week, amount or income changed")


def validate_legacy_source(state, legacy, completed):
    if {r["line_id"] for r in legacy["lines"]} != LEGACY_IDS or len(
        legacy["lines"]
    ) != 5:
        raise ValueError("Unexpected sixth or missing legacy calendar")
    indexed = {r["id"]: r for r in state["budget_lines"]}
    history = state["budget_tournament_weekly_lines"]
    for target in legacy["lines"]:
        original_weeks = {str(r["month_number"]): r for r in target["before_plan_rows"]}
        if set(target["after_plan"]) != {str(w) for w in range(1, 53)}:
            raise ValueError("Regularization must preserve all52 weeks")
        for week, values in target["after_plan"].items():
            amount = (
                Decimal(str(target["authorized_amount"]))
                if week == "40"
                else Decimal(0)
            )
            if Decimal(str(values["budget_expense_amount"])) != amount or Decimal(
                str(values["expected_income_amount"])
            ) != Decimal(str(original_weeks[week]["expected_income_amount"])):
                raise ValueError(
                    "Regularization changed week, amount or expected income"
                )
        line = indexed[target["line_id"]]
        if (
            line["tournament_id"] != LIGA
            or line["budget_version_id"] != VERSION
            or line["budget_concept_id"] != target["concept_id"]
            or line["phase"] != target["phase"]
            or Decimal(str(line["budget_amount"]))
            != Decimal(str(target["authorized_amount"]))
            or target["week"] != 40
        ):
            raise ValueError("Five-calendar financial source drifted")
        if target["effective_weekly_line_before"] not in history:
            raise ValueError("Approved week40 history is absent or changed")
        for table, before_key in (
            ("budget_line_monthly_plan", "before_plan_rows"),
            ("budget_line_monthly_allocations", "before_allocation_rows"),
        ):
            if completed:
                calendar_semantics(state, target, table)
            else:
                actual = [
                    r for r in state[table] if r["budget_line_id"] == target["line_id"]
                ]
                if Counter(digest(r) for r in actual) != Counter(
                    digest(r) for r in target[before_key]
                ):
                    raise ValueError("Five-calendar source rows drifted")


def preservation(before, after, data):
    """Prove the exact permitted diff, including retired concepts and paid states."""
    for table in PROTECTED:
        if before[table] != after[table]:
            raise ValueError("Protected rows changed: " + table)
    legacy = data.get("legacy")
    calendar_ids = {r["line_id"] for r in legacy["lines"]} if legacy else set()
    changed_calendars = False
    for table in CALENDAR_TABLES:
        old = before.get(table, [])
        new = after.get(table, [])
        if [r for r in old if r["budget_line_id"] not in calendar_ids] != [
            r for r in new if r["budget_line_id"] not in calendar_ids
        ]:
            raise ValueError("Unrelated calendar rows changed")
        changed_calendars |= old != new
        if legacy:
            for target in legacy["lines"]:
                calendar_semantics(after, target, table)
    if changed_calendars:
        audits = [
            r
            for r in after["budget_version_audit_log"]
            if r["event_type"] == LEGACY_EVENT
            and r["payload"].get("source_sha256") == LEGACY_SHA
        ]
        if not audits or not any(
            r["payload"].get("original_plan_rows")
            == [v for target in legacy["lines"] for v in target["before_plan_rows"]]
            and r["payload"].get("original_allocation_rows")
            == [
                v
                for target in legacy["lines"]
                for v in target["before_allocation_rows"]
            ]
            for r in audits
        ):
            raise ValueError("Original calendar history not preserved in audit")
    expected = {r["id"]: r["proposed_account"] for r in data["accounts"]}
    old = {r["id"]: r for r in before["budget_lines"]}
    new = {r["id"]: r for r in after["budget_lines"]}
    if set(old) != set(new):
        raise ValueError("Budget line identities changed")
    for key in old:
        wanted = dict(old[key])
        actual = dict(new[key])
        if key in expected:
            wanted["account_code_final"] = expected[key]
            wanted.pop("updated_at", None)
            actual.pop("updated_at", None)
        if wanted != actual:
            raise ValueError("Unexpected budget line change: " + key)
    old_versions = {r["id"]: dict(r) for r in before["budget_versions"]}
    new_versions = {r["id"]: dict(r) for r in after["budget_versions"]}
    for values in (old_versions, new_versions):
        values[VERSION].pop("updated_at", None)
    if old_versions != new_versions or new_versions[VERSION]["status"] != "approved":
        raise ValueError("Version, notes or approval changed unexpectedly")
    allowed = {r["movement_key"] for r in data["movements"]}
    for table in (
        "budget_approvals",
        "budget_version_audit_log",
        "budget_tournament_weekly_lines",
    ):
        current = Counter(digest(r) for r in after[table])
        previous = Counter(digest(r) for r in before[table])
        if any(current[key] < count for key, count in previous.items()):
            raise ValueError("Existing audit or weekly history changed: " + table)
    current = {r["id"]: r for r in after["budget_movement_assignments"]}
    for row in before["budget_movement_assignments"]:
        if row["budget_version_id"] != VERSION or row["movement_key"] not in allowed:
            if current.get(row["id"]) != row:
                raise ValueError("Unrelated assignment changed")

    old_ids = {r["id"] for r in before["budget_movement_assignments"]}
    if any(
        r["id"] not in old_ids
        and (r["budget_version_id"] != VERSION or r["movement_key"] not in allowed)
        for r in after["budget_movement_assignments"]
    ):
        raise ValueError("Unexpected new assignment")
    revisions = {r["id"]: r for r in after["budget_tournament_weekly_revisions"]}
    old_revision_ids = {r["id"] for r in before["budget_tournament_weekly_revisions"]}
    for row in before["budget_tournament_weekly_revisions"]:
        expected_revision = dict(row)
        actual_revision = dict(revisions.get(row["id"], {}))
        if (
            row["tournament_id"] in {LIGA, HWC}
            and row["is_current"]
            and actual_revision.get("deactivated_at")
        ):
            if (
                actual_revision.get("is_current") is not False
                or actual_revision.get("restored_revision_id") is not None
                or actual_revision.get("deactivated_by_empleado_id") != ACTOR
            ):
                raise ValueError("Unexpected overlay retirement")
            for name in (
                "is_current",
                "deactivated_at",
                "deactivated_by_empleado_id",
                "restored_revision_id",
            ):
                expected_revision.pop(name, None)
                actual_revision.pop(name, None)
        if expected_revision != actual_revision:
            raise ValueError("Existing overlay history changed")
    added = [r for r in revisions.values() if r["id"] not in old_revision_ids]
    if added and (
        len(added) != 2 or {r["tournament_id"] for r in added} != {LIGA, HWC}
    ):
        raise ValueError("Unexpected overlay additions")


async def current_movements(session):
    result = {}
    for tid in (LIGA, COPA):
        value = await service.build_budget_actuals_snapshot(
            session, edition_year=2026, version_id=VERSION, tournament_id=tid
        )
        for row in value["movements"]:
            key = service.budget_movement_key(row)
            if key in result:
                raise ValueError("Duplicate movement identity")
            result[key] = row
    return result


async def verify_effective(session, state):
    lines = [
        r
        for r in state["budget_lines"]
        if r["budget_version_id"] == VERSION
        and r.get("tournament_id") in WEEKS
        and r.get("line_direction", "expense") == "expense"
    ]
    plans = await service.list_monthly_plan_for_lines(
        session, line_ids=[r["id"] for r in lines], ensure_schema=False
    )
    plans, conflicts = await overlay.effective_plans_for_lines(
        session,
        version_id=VERSION,
        line_ids=[r["id"] for r in lines],
        baseline_plans=plans,
    )
    if conflicts:
        raise ValueError("Effective weekly planning conflicts")
    totals = {tid: Decimal(0) for tid in WEEKS}
    for row in lines:
        amount = Decimal(str(row["budget_amount"]))
        totals[row["tournament_id"]] += amount
        allocated = {
            int(w): Decimal(str(v.get("budget_expense_amount", 0)))
            for w, v in plans.get(row["id"], {}).items()
        }
        if sum(allocated.values()) != amount or any(
            v != 0 and w != WEEKS[row["tournament_id"]] for w, v in allocated.items()
        ):
            raise ValueError("Week/amount changed: " + row["id"])
    if {k: str(v.quantize(Decimal(".01"))) for k, v in totals.items()} != TOTALS:
        raise ValueError("Tournament authorized totals changed")
    return {
        row["id"]: {
            str(week): {
                key: str(
                    Decimal(
                        str(plans.get(row["id"], {}).get(week, {}).get(key, 0))
                    ).quantize(Decimal(".01"))
                )
                for key in ("budget_expense_amount", "expected_income_amount")
            }
            for week in range(1, 53)
        }
        for row in lines
    }


async def approval_blockers(session, state, legacy=None):
    """Simulate the normal approval reader after retiring only Liga/HWC overlays."""
    from samchat.budgets.distribution import schedule_state

    lines = [
        r
        for r in state["budget_lines"]
        if r["budget_version_id"] == VERSION
        and r.get("line_direction", "expense") == "expense"
    ]
    raw = await service.list_monthly_plan_for_lines(
        session, line_ids=[r["id"] for r in lines], ensure_schema=False
    )
    effective, conflicts = await overlay.effective_plans_for_lines(
        session,
        version_id=VERSION,
        line_ids=[r["id"] for r in lines],
        baseline_plans=raw,
    )
    result = []
    for line in lines:
        plan = (
            raw.get(line["id"], {})
            if line.get("tournament_id") in {LIGA, HWC}
            else effective.get(line["id"], {})
        )
        if legacy:
            repair = next(
                (r for r in legacy["lines"] if r["line_id"] == line["id"]), None
            )
            if repair:
                plan = {
                    int(week): dict(values)
                    for week, values in repair["after_plan"].items()
                }
        schedule = schedule_state(line, plan)
        if line["id"] in conflicts or schedule["status"] in {"invalid", "mismatch"}:
            result.append(
                {
                    "line_id": line["id"],
                    "tournament_id": line.get("tournament_id"),
                    "concept_name": line["concept_name"],
                    "phase": line.get("phase"),
                    "schedule": schedule,
                    "conflict": conflicts.get(line["id"]),
                }
            )
    return result


async def build_plan(session, source, data):
    await overlay._require_budget_actor(session, ACTOR)
    await overlay._version(session, VERSION, require_selected=True)
    state = await snapshot(session)
    indexed = {r["id"]: r for r in state["budget_lines"]}
    for target in data["accounts"]:
        row = indexed.get(target["id"])
        if not row or any(
            str(row.get(k)) != str(target.get(k))
            for k in (
                "budget_version_id",
                "tournament_id",
                "budget_concept_id",
                "phase",
                "concept_name",
            )
        ):
            raise ValueError("Account line scope drifted")
        if any(
            Decimal(str(row[k])) != Decimal(target[k])
            for k in ("budget_amount", "reference_amount", "variance_amount")
        ):
            raise ValueError("Account line economics drifted")
        if row["account_code_final"] not in {None, "", target["proposed_account"]}:
            raise ValueError("Account destination drifted")
        concept = next(
            (
                r
                for r in state["budget_concepts"]
                if r["id"] == target["budget_concept_id"]
            ),
            None,
        )
        if not concept or concept["active"] is not target["concept_active"]:
            raise ValueError("Concept activity drifted")
        if (
            target["concept_active"]
            and concept.get("cuenta_contable_id") != target["proposed_account_id"]
        ):
            raise ValueError("Active concept account mapping drifted")
        account = next(
            (
                r
                for r in state["cuentas_contables"]
                if r["id"] == target["proposed_account_id"]
            ),
            None,
        )
        if (
            not account
            or account.get("activo") is not True
            or account.get("codigo") != target["proposed_account"]
        ):
            raise ValueError("Destination account is inactive or changed")
    movements = await current_movements(session)
    for target in data["movements"]:
        row = movements.get(target["movement_key"])
        if (
            not row
            or str(row.get("concept_key")) != target["concept_id"]
            or Decimal(str(row["amount"])) != Decimal(str(target["amount"]))
        ):
            raise ValueError("Movement source drifted")
        line = indexed[target["lineage_compatible_candidate_ids"][0]]
        if (
            line["tournament_id"] != target["tournament_id"]
            or line["budget_version_id"] != VERSION
            or str(line["budget_concept_id"])
            != target["lineage_target_existing_lines"][0]["budget_concept_id"]
        ):
            raise ValueError("Movement destination drifted")
    effective_before = await verify_effective(session, state)
    completed = all(
        indexed[r["id"]]["account_code_final"] == r["proposed_account"]
        for r in data["accounts"]
    ) and all(
        movements[r["movement_key"]].get("budget_line_id")
        == r["lineage_compatible_candidate_ids"][0]
        for r in data["movements"]
    )
    validate_legacy_source(state, data["legacy"], completed)
    if not completed:
        for tid in (LIGA, HWC):
            revision = next(
                (
                    r
                    for r in state["budget_tournament_weekly_revisions"]
                    if r["tournament_id"] == tid and r["is_current"]
                ),
                None,
            )
            expected = next(
                r
                for r in data["overlays"]
                if r["week_number"] == WEEKS[tid]
                and r["name"].startswith("Liga" if tid == LIGA else "Homeless")
            )
            if (
                not revision
                or revision["id"] != expected["id"]
                or revision["plan_hash"] != expected["plan_hash"]
                or revision["previous_effective_revision_id"] is not None
            ):
                raise ValueError(
                    "Weekly revision changed; new reviewed preview required"
                )
    return {
        "source": source,
        "actor": ACTOR,
        "evidence": PINS,
        "originals": ORIGINALS,
        "before": state,
        "effective_before": effective_before,
        "legacy_calendar_repairs": [
            {
                "line_id": r["line_id"],
                "amount": r["authorized_amount"],
                "week": r["week"],
            }
            for r in data["legacy"]["lines"]
        ],
        "completed": completed,
        "account_count": 40,
        "movement_count": 16,
        "approval_blockers": (
            [] if completed else await approval_blockers(session, state, data["legacy"])
        ),
    }


async def apply_owners(
    session, plan, data, *, fail_after=None, fail_after_calendar=None
):
    """All effects use canonical owners; caller contains their internal commits."""
    if plan["completed"]:
        return {"idempotent": True, "revisions": []}
    revisions = []
    for tid in (LIGA, HWC):
        revision = next(
            r
            for r in plan["before"]["budget_tournament_weekly_revisions"]
            if r["tournament_id"] == tid and r["is_current"]
        )
        await overlay.deactivate_tournament_overlay(
            session,
            version_id=VERSION,
            tournament_id=tid,
            expected_revision_id=revision["id"],
            expected_plan_hash=revision["plan_hash"],
            actor_empleado_id=ACTOR,
        )
    await service.transition_budget_version(
        session,
        version_id=VERSION,
        new_status="reforecast",
        actor_empleado_id=ACTOR,
        ensure_schema=False,
    )
    for index, target in enumerate(data["accounts"], 1):
        await service.update_budget_line(
            session,
            line_id=target["id"],
            updates={"account_code_final": target["proposed_account"]},
            actor_empleado_id=ACTOR,
            tournament_id=target["tournament_id"],
            ensure_schema=False,
            commit=True,
        )
        if fail_after == index:
            raise RuntimeError("Injected restored closeout failure")
    legacy = data.get("legacy")
    if legacy:
        await service._audit_budget_event(
            session,
            budget_version_id=VERSION,
            event_type=LEGACY_EVENT,
            actor_empleado_id=ACTOR,
            payload={
                "source_sha256": LEGACY_SHA,
                "original_plan_rows": [
                    r for target in legacy["lines"] for r in target["before_plan_rows"]
                ],
                "original_allocation_rows": [
                    r
                    for target in legacy["lines"]
                    for r in target["before_allocation_rows"]
                ],
            },
        )
        for index, target in enumerate(legacy["lines"], 1):
            token = LEGACY_SCOPE.set(target["line_id"])
            try:
                await service.replace_budget_line_monthly_plan(
                    session,
                    budget_line_id=target["line_id"],
                    plan={int(w): dict(v) for w, v in target["after_plan"].items()},
                    actor_empleado_id=ACTOR,
                    ensure_schema=False,
                )
            finally:
                LEGACY_SCOPE.reset(token)
            if fail_after_calendar == index:
                raise RuntimeError("Injected restored calendar failure")
    await service.confirm_tournament_closeout_decisions(
        session, actor_empleado_id=ACTOR
    )
    for target in data["movements"]:
        line = next(
            r
            for r in plan["before"]["budget_lines"]
            if r["id"] == target["lineage_compatible_candidate_ids"][0]
        )
        await service.assign_budget_movement_to_line(
            session,
            budget_version_id=VERSION,
            budget_concept_id=line["budget_concept_id"],
            budget_line_id=line["id"],
            movement_key=target["movement_key"],
            actor_empleado_id=ACTOR,
            ensure_schema=False,
        )
    await service.transition_budget_version(
        session,
        version_id=VERSION,
        new_status="approved",
        actor_empleado_id=ACTOR,
        ensure_schema=False,
    )
    for tid in (LIGA, HWC):
        preview = await overlay.preview_tournament_overlay(
            session, version_id=VERSION, tournament_id=tid, week=WEEKS[tid]
        )
        revisions.append(
            await overlay.apply_tournament_overlay(
                session,
                version_id=VERSION,
                tournament_id=tid,
                week=WEEKS[tid],
                expected_plan_hash=preview["plan_hash"],
                actor_empleado_id=ACTOR,
            )
        )
    await service._audit_budget_event(
        session,
        budget_version_id=VERSION,
        event_type=EVENT,
        actor_empleado_id=ACTOR,
        payload={
            "plan_sha256": digest(plan),
            "workbook_sha256": PINS["Revision_cierre_tableros_20261030.xlsx"],
            "movement_keys": [r["movement_key"] for r in data["movements"]],
            "account_ids": [r["id"] for r in data["accounts"]],
        },
    )
    return {"idempotent": False, "revisions": revisions}


async def verify(session, plan, data):
    after = await snapshot(session)
    preservation(plan["before"], after, data)
    effective_after = await verify_effective(session, after)
    if effective_after != plan["effective_before"]:
        raise ValueError("Effective weekly expense or expected income changed")
    movements = await current_movements(session)
    for target in data["movements"]:
        if (
            movements[target["movement_key"]].get("budget_line_id")
            != target["lineage_compatible_candidate_ids"][0]
        ):
            raise ValueError("Assignment not visible in canonical projection")
    return {
        "snapshot_sha256": digest(after),
        "account_count": 40,
        "movement_count": 16,
        "weeks": WEEKS,
        "totals": TOTALS,
        "protected_preserved": True,
        "effective_plans_and_income_preserved": True,
        "regularized_legacy_calendar_count": 5,
    }


async def run(args):
    if not __debug__ or args.actor_id != ACTOR:
        raise ValueError("Named actor and enabled checks required")
    if (args.fail_after or args.fail_after_calendar) and not (
        args.restore and args.apply
    ):
        raise ValueError("Failure injection requires disposable restore apply")
    data, source = inputs(), source_identity()
    if args.apply and (
        not args.expected_hash or args.expected_commit != source["commit"]
    ):
        raise ValueError("Reviewed plan hash and exact commit required")
    if args.apply and not args.restore:
        require_serving(args.expected_commit)
    engine = create_async_engine(
        RESTORE_URL if args.restore else database_url(args), hide_parameters=True
    )
    if engine.url.database != (RESTORE_DB if args.restore else "devnous_db"):
        raise ValueError("Unexpected database")

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def guard(conn, cursor, statement, parameters, context, many):
        compiled = context.compiled_parameters[0] if context.compiled_parameters else {}
        sql_guard(
            statement,
            args.apply,
            budget_line_id=compiled.get("budget_line_id"),
            legacy_line=LEGACY_SCOPE.get(),
        )

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
                if await connection.scalar(text("SELECT current_database()")) != (
                    RESTORE_DB if args.restore else "devnous_db"
                ):
                    raise ValueError("Unexpected database identity")
                async with AsyncSession(
                    bind=connection, join_transaction_mode="create_savepoint"
                ) as session:
                    if args.apply:
                        for tid in sorted(WEEKS):
                            await session.execute(
                                text(
                                    "SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"
                                ),
                                {"scope": f"budget-weekly:2026:{tid}"},
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
                                "SELECT id FROM cuentas_contables WHERE id=ANY(CAST(:ids AS uuid[])) ORDER BY id FOR SHARE"
                            ),
                            {
                                "ids": sorted(
                                    {r["proposed_account_id"] for r in data["accounts"]}
                                )
                            },
                        )
                        await session.execute(
                            text(
                                "SELECT id FROM empleados WHERE id=CAST(:actor AS uuid) FOR SHARE"
                            ),
                            {"actor": ACTOR},
                        )
                        concepts = {r["budget_concept_id"] for r in data["accounts"]}
                        approved = service._closeout_actions()
                        concepts.update(
                            r["source_concept_id"] for r in approved["movements"]
                        )
                        concepts.update(
                            r["target_concept_id"] for r in approved["movements"]
                        )
                        await session.execute(
                            text(
                                "SELECT id FROM budget_concepts WHERE id=ANY(CAST(:ids AS uuid[])) ORDER BY id FOR SHARE"
                            ),
                            {"ids": sorted(concepts)},
                        )
                    plan = await build_plan(session, source, data)
                    ph = digest(plan)
                    save(args.output_dir, "preview-" + ph + ".json", plan)
                    if not args.apply:
                        return {
                            "status": (
                                "PREVIEW_BLOCKED"
                                if plan["approval_blockers"]
                                else "PREVIEW"
                            ),
                            "approval_blockers": plan["approval_blockers"],
                            "legacy_calendar_repairs": plan["legacy_calendar_repairs"],
                            "hash": ph,
                            "completed": plan["completed"],
                            "account_count": 40,
                            "movement_count": 16,
                        }
                    if ph != args.expected_hash:
                        raise ValueError("Plan drifted; prepare a fresh preview")
                    if plan["approval_blockers"]:
                        raise ValueError(
                            "Normal approval would reject legacy schedules; resolve the disclosed blockers before applying"
                        )
                    result = await apply_owners(
                        session,
                        plan,
                        data,
                        fail_after=args.fail_after,
                        fail_after_calendar=args.fail_after_calendar,
                    )
                    checked = await verify(session, plan, data)
                    if inputs() != data or source_identity() != source:
                        raise ValueError("Source/code changed before commit")
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
    p.add_argument("--env-file", type=Path, default=Path("/etc/samchat/samchat.env"))
    p.add_argument("--actor-id", default=ACTOR)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--expected-hash")
    p.add_argument("--expected-commit")
    p.add_argument("--restore", action="store_true")
    p.add_argument("--fail-after", type=int)
    p.add_argument("--fail-after-calendar", type=int)
    return p


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(parser().parse_args())), default=str))
