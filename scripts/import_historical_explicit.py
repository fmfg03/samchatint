#!/usr/bin/env python3
"""Fresh readonly previews and guarded, versioned explicit historical imports."""
from __future__ import annotations

import argparse
import asyncio
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
import devnous.gastos  # noqa: E402,F401
from samchat.accounting_historical import service  # noqa: E402
from apply_provisional_approval_policy import database_url  # noqa: E402

ACTOR = "e3d13040-2360-420f-98a1-516440ef63c3"
APPROVED_SCOPES = {
    (2026, "01"): ("PSP1705058S4", {3, 4, 6, 7}),
    (2026, "GOAT"): ("GDM210323RIA", {8}),
    (2026, "02"): ("PMD0608162M2", set()),
}
RESTORE_DB = "samchat_accounting_restore_20261028"
RESTORE_URL = (
    "postgresql+asyncpg://postgres@/"
    + RESTORE_DB
    + "?host=/tmp/samchat-accounting-restore-proxy-20261028&port=55482"
)
HIST = (
    "historical_accounting_source_files",
    "historical_trial_balance_rows",
    "historical_policy_headers",
    "historical_policy_lines",
)
PROTECTED = (
    "documentos",
    "expense_reports",
    "budget_versions",
    "budget_lines",
    "budget_concepts",
    "budget_line_monthly_plan",
    "budget_line_monthly_allocations",
    "budget_tournament_weekly_revisions",
    "budget_tournament_weekly_lines",
    "budget_movement_assignments",
    "budget_cfdi_income_links",
    "cuentas_contables",
)
CODE = (
    "scripts/import_historical_explicit.py",
    "scripts/normalize_historical_xlsx.py",
    "scripts/apply_provisional_approval_policy.py",
    "src/samchat/accounting_historical/service.py",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def save(directory, name, value):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    payload = json.dumps(
        value, sort_keys=True, default=str, ensure_ascii=False, indent=2
    ).encode()
    path = directory / name
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("Evidence already exists with different content")
    else:
        with path.open("xb") as stream:
            stream.write(payload)
        path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


def source_identity():
    return {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "files": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in CODE},
    }


def require_serving(commit):
    if (
        not re.fullmatch("[0-9a-f]{40}", commit or "")
        or source_identity()["commit"] != commit
    ):
        raise ValueError("Reviewed commit required")
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", *CODE],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *CODE], cwd=ROOT, check=True
    )
    raw = subprocess.check_output(
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
    unit = dict(x.split("=", 1) for x in raw.splitlines())
    if (
        unit["ActiveState"] != "active"
        or Path(unit["WorkingDirectory"]).resolve() != ROOT.resolve()
        or Path("/proc/" + unit["MainPID"] + "/cwd").resolve() != ROOT.resolve()
    ):
        raise ValueError("This code is not the active serving source")


def manifest_configs(paths):
    configs = []
    for path in paths:
        path = path.resolve()
        data = json.loads(path.read_text())
        configs.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "fiscal_year": data["fiscal_year"],
                "company_code": data["company_code"],
                "company_label": data["company_label"],
            }
        )
    keys = [(x["fiscal_year"], x["company_code"]) for x in configs]
    if len(keys) != len(set(keys)) or not keys:
        raise ValueError("Select each company/year exactly once")
    return sorted(configs, key=lambda x: (x["fiscal_year"], x["company_code"]))


def require_approved_admission(configs):
    """This operator admits only the company/month profiles authorized by the user."""
    for config in configs:
        approved = APPROVED_SCOPES.get((config["fiscal_year"], config["company_code"]))
        manifest = json.loads(Path(config["path"]).read_text())
        withheld = manifest.get("withheld_months", [])
        if not isinstance(withheld, list) or any(
            type(item.get("month_num")) is not int for item in withheld
        ):
            raise ValueError("Manifest scope is outside the approved admission")
        if (
            not approved
            or config["company_label"] != approved[0]
            or {item["month_num"] for item in withheld} != approved[1]
        ):
            raise ValueError("Manifest scope is outside the approved admission")


def reference_path(manifest_path, item):
    path = Path(item["path"])
    return path if path.is_absolute() else Path(manifest_path).parent / path


def recheck_inputs(configs):
    """Pin manifest and every referenced original/derived file through commit."""
    for config in configs:
        payload = Path(config["path"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != config["sha256"]:
            raise ValueError("Manifest changed while preparing import")
        manifest = json.loads(payload)
        files = list(manifest["originals"]) + list(manifest["derived"].values())
        for key in ("validation_report", "full_validation_report"):
            if manifest.get(key):
                files.append(manifest[key])
        files.extend(item["evidence"] for item in manifest.get("withheld_months", []))
        for item in files:
            if (
                hashlib.sha256(
                    reference_path(config["path"], item).read_bytes()
                ).hexdigest()
                != item["sha256"]
            ):
                raise ValueError("Source changed while preparing import")


async def authorize(s, actor):
    actor = str(UUID(actor))
    row = (
        (
            await s.execute(
                text("SELECT activo,rol FROM empleados WHERE id=CAST(:id AS uuid)"),
                {"id": actor},
            )
        )
        .mappings()
        .first()
    )
    if (
        not row
        or row["activo"] is not True
        or str(row["rol"]).lower() not in {"superadmin", "super_admin"}
    ):
        raise PermissionError("An active superadmin actor is required")


async def preservation(s, exclude=()):
    result = {}
    for table in (*HIST, "accounting_import_runs", *PROTECTED):
        where = ""
        if table in HIST:
            where = " WHERE NOT (import_run_id=ANY(CAST(:exclude AS uuid[]))) OR import_run_id IS NULL"
        elif table == "accounting_import_runs":
            where = " WHERE NOT (id=ANY(CAST(:exclude AS uuid[])))"
        sql = (
            "SELECT count(*) AS count,encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,'' ORDER BY to_jsonb(t)::text),''),'UTF8')),'hex') AS sha256 FROM "
            + table
            + " t"
            + where
        )
        result[table] = dict(
            (await s.execute(text(sql), {"exclude": list(exclude)})).mappings().one()
        )
    return result


async def build_plan(s, configs, source, actor):
    await authorize(s, actor)
    recheck_inputs(configs)
    before = await preservation(s)
    companies = []
    for config in configs:
        summary = await service.import_historical_accounting_pilot(
            s,
            fiscal_year=config["fiscal_year"],
            company_code=config["company_code"],
            company_label=config["company_label"],
            source_kind="normalized_manifest",
            source_path=config["path"],
            started_by_empleado_id=actor,
            apply_changes=False,
            ensure_schema=False,
        )
        selected = await service._find_latest_applied_import_run(
            s, fiscal_year=config["fiscal_year"], company_code=config["company_code"]
        )
        companies.append(
            {"manifest": config, "preview": summary, "selected_before": selected}
        )
    recheck_inputs(configs)
    blockers = [
        {
            "company_code": c["manifest"]["company_code"],
            "issues": c["preview"]["summary"]
            .get("source_validation", {})
            .get("material_reconciliation_issues", []),
        }
        for c in companies
    ]
    blockers = [b for b in blockers if b["issues"]]
    return {
        "source": source,
        "actor": actor,
        "companies": companies,
        "preservation": before,
        "blockers": blockers,
    }


async def verify(s, plan, results):
    checked = []
    for entry, result in zip(plan["companies"], results):
        config = entry["manifest"]
        expected = entry["preview"]["summary"]
        selected = await service._find_latest_applied_import_run(
            s, fiscal_year=config["fiscal_year"], company_code=config["company_code"]
        )
        if (
            not selected
            or selected["source_sha256"] != expected["source_bundle_sha256"]
        ):
            raise ValueError(
                "Selected source does not match the reviewed complete bundle"
            )
        rid = selected["id"]
        stats = {}
        for table, name in [
            ("historical_trial_balance_rows", "trial_balance_rows"),
            ("historical_policy_headers", "policy_headers"),
            ("historical_policy_lines", "policy_lines"),
        ]:
            rows = (
                (
                    await s.execute(
                        text(
                            f"SELECT fiscal_year,company_code,company_label,period_key,count(*) AS count FROM {table} WHERE import_run_id=CAST(:id AS uuid) GROUP BY fiscal_year,company_code,company_label,period_key ORDER BY period_key"
                        ),
                        {"id": rid},
                    )
                )
                .mappings()
                .all()
            )
            if sum(r["count"] for r in rows) != expected[name] or any(
                r["fiscal_year"] != config["fiscal_year"]
                or r["company_code"] != config["company_code"]
                or r["company_label"] != config["company_label"]
                for r in rows
            ):
                raise ValueError(
                    "Imported rows differ from the reviewed identity/counts"
                )
            manifest = json.loads(Path(config["path"]).read_text())
            family = {
                "trial_balance_rows": "trial_balance",
                "policy_headers": "policy_headers",
                "policy_lines": "policy_lines",
            }[name]
            with reference_path(config["path"], manifest["derived"][family]).open(
                newline="", encoding="utf-8-sig"
            ) as stream:
                source_rows = list(csv.DictReader(stream))
            fields = {
                "trial_balance_rows": [
                    ("opening_balance", "saldo_inicial"),
                    ("debits", "cargos"),
                    ("credits", "abonos"),
                    ("closing_balance", "saldo_final"),
                ],
                "policy_headers": [
                    ("total_debits", "total_debe"),
                    ("total_credits", "total_haber"),
                ],
                "policy_lines": [("debit_amount", "debe"), ("credit_amount", "haber")],
            }[name]
            expected_periods = sorted(
                {
                    f"{config['fiscal_year']}-{int(row['month_num']):02d}"
                    for row in source_rows
                }
            )
            if sorted(r["period_key"] for r in rows) != expected_periods:
                raise ValueError(
                    "Imported period coverage differs from reviewed source"
                )
            withheld = {
                int(item["month_num"]) for item in manifest.get("withheld_months", [])
            }
            if any(int(period[-2:]) in withheld for period in expected_periods):
                raise ValueError("A withheld month was imported")
            amounts = {}
            for db_field, source_field in fields:
                actual = await s.scalar(
                    text(
                        f"SELECT coalesce(sum({db_field}),0) FROM {table} WHERE import_run_id=CAST(:id AS uuid)"
                    ),
                    {"id": rid},
                )
                expected_total = sum(
                    (Decimal(row[source_field]) for row in source_rows), Decimal(0)
                )
                if Decimal(str(actual)) != expected_total:
                    raise ValueError(
                        "Imported monetary total differs from reviewed source"
                    )
                amounts[db_field] = str(expected_total)
            stats[name] = {
                "periods": [dict(r) for r in rows],
                "coverage_months": expected_periods,
                "totals": amounts,
            }
        inventories = []
        for inventory in (
            await service.list_historical_accounting_companies(
                s, fiscal_year=config["fiscal_year"]
            ),
            await service.list_historical_accounting_coverage(
                s, year_from=config["fiscal_year"], year_to=config["fiscal_year"]
            ),
        ):
            current = [
                item
                for item in inventory
                if item["company_code"] == config["company_code"]
            ]
            if len(current) != 1:
                raise ValueError(
                    "Current inventory does not resolve the selected company"
                )
            item = current[0]
            if (
                item["trial_balance_periods"]
                != len(stats["trial_balance_rows"]["periods"])
                or item["policy_periods"] != len(stats["policy_lines"]["periods"])
                or item["policy_headers"] != expected["policy_headers"]
                or item["policy_lines"] != expected["policy_lines"]
            ):
                raise ValueError(
                    "Inventory reintroduces historical or withheld periods"
                )
            inventories.append(item)
        checked.append(
            {
                "company_code": config["company_code"],
                "import_run_id": rid,
                "source_sha256": selected["source_sha256"],
                "rows": stats,
                "inventory_readback": inventories,
                "withheld_months": manifest.get("withheld_months", []),
                "scope_status": (
                    "partial_with_disclosed_gaps"
                    if manifest.get("withheld_months")
                    else "reviewed_bundle"
                ),
                "source_validation": expected.get("source_validation", {}),
            }
        )
    return checked


def sql_guard(statement, applying):
    sql = statement.strip()
    first = sql.split()[0].lower() if sql else ""
    if first in {"insert", "update", "delete"}:
        match = re.match(r"(?is)^INSERT\s+INTO\s+([a-z_]+)(?:\s|\()", sql)
        if (
            not applying
            or not match
            or match[1] not in {*HIST, "accounting_import_runs"}
        ):
            raise ValueError("Write outside historical import scope")
    elif first not in {"select", "set", "show", "savepoint", "release", "rollback"}:
        raise ValueError("DDL or unapproved SQL is forbidden")
    if not applying and re.search(r"\bFOR\s+(UPDATE|SHARE)\b", sql, re.I):
        raise ValueError("Readonly preview cannot acquire write locks")


async def run(args):
    if sys.flags.optimize:
        raise ValueError("Optimized Python is not supported")
    actor = str(UUID(args.actor_id))
    if actor != ACTOR:
        raise PermissionError(
            "This operation requires the specifically authorized actor"
        )
    source = source_identity()
    if args.fail_after_import and (not args.restore or not args.apply):
        raise ValueError("Fault injection is restricted to restored apply tests")
    if args.apply:
        if not args.expected_hash or not args.expected_commit:
            raise ValueError("Apply requires preview hash and reviewed commit")
        if args.restore:
            if args.expected_commit != source["commit"]:
                raise ValueError("Restore source commit mismatch")
        else:
            require_serving(args.expected_commit)
    configs = manifest_configs(args.manifest)
    url = (
        RESTORE_URL
        if args.restore
        else database_url(SimpleNamespace(env_file=args.env_file))
    )
    engine = create_async_engine(url, hide_parameters=True)

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def guard(conn, cursor, statement, parameters, context, executemany):
        sql_guard(statement, args.apply)

    try:
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text(
                        "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
                        if args.apply
                        else "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
                )
                await conn.execute(text("SET LOCAL lock_timeout='5s'"))
                await conn.execute(text("SET LOCAL statement_timeout='120s'"))
                if await conn.scalar(text("SELECT current_database()")) != (
                    RESTORE_DB if args.restore else "devnous_db"
                ):
                    raise ValueError("Unexpected database")
                if args.apply:
                    for c in configs:
                        await conn.execute(
                            text(
                                "SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"
                            ),
                            {
                                "scope": f"historical-accounting:{c['fiscal_year']}:{c['company_code']}"
                            },
                        )
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint"
                ) as s:
                    plan = await build_plan(s, configs, source, actor)
                    ph = digest(plan)
                    save(
                        args.output_dir,
                        "preview-" + ph + ".json",
                        {
                            "status": (
                                "PREVIEW_BLOCKED" if plan["blockers"] else "PREVIEW"
                            ),
                            "hash": ph,
                            "plan": plan,
                        },
                    )
                    if not args.apply:
                        return {
                            "status": (
                                "PREVIEW_BLOCKED" if plan["blockers"] else "PREVIEW"
                            ),
                            "hash": ph,
                            "blocked_companies": [
                                b["company_code"] for b in plan["blockers"]
                            ],
                        }
                    if ph != args.expected_hash:
                        raise ValueError("Plan drifted; inspect a fresh preview")
                    if plan["blockers"]:
                        raise ValueError(
                            "Material source reconciliation issues require resolution"
                        )
                    require_approved_admission(configs)
                    before_ids = set(
                        (
                            await s.execute(
                                text("SELECT id::text FROM accounting_import_runs")
                            )
                        )
                        .scalars()
                        .all()
                    )
                    results = []
                    for n, c in enumerate(configs, 1):
                        results.append(
                            await service.import_historical_accounting_pilot(
                                s,
                                fiscal_year=c["fiscal_year"],
                                company_code=c["company_code"],
                                company_label=c["company_label"],
                                source_kind="normalized_manifest",
                                source_path=c["path"],
                                started_by_empleado_id=actor,
                                apply_changes=True,
                                ensure_schema=False,
                            )
                        )
                        if args.fail_after_import == n:
                            raise RuntimeError("Injected restored import failure")
                    all_ids = set(
                        (
                            await s.execute(
                                text("SELECT id::text FROM accounting_import_runs")
                            )
                        )
                        .scalars()
                        .all()
                    )
                    new_ids = sorted(all_ids - before_ids)
                    if await preservation(s, new_ids) != plan["preservation"]:
                        raise ValueError("Existing rows or operational state changed")
                    checked = await verify(s, plan, results)
                    recheck_inputs(configs)
                    if source_identity() != source:
                        raise ValueError("Source changed during import")
                    if not args.restore:
                        require_serving(args.expected_commit)
                    save(
                        args.output_dir,
                        "pending-" + ph + ".json",
                        {
                            "hash": ph,
                            "new_import_ids": new_ids,
                            "verification": checked,
                            "plan": plan,
                        },
                    )
                    await s.commit()
        save(
            args.output_dir,
            "committed-" + ph + ".json",
            {"hash": ph, "new_import_ids": new_ids, "verification": checked},
        )
        async with AsyncSession(engine) as s:
            await s.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            checked = await verify(s, plan, results)
            if await preservation(s, new_ids) != plan["preservation"]:
                raise ValueError(
                    "Postcommit preservation differs; investigate before retry"
                )
        receipt = {
            "status": "COMMITTED_VERIFIED",
            "hash": ph,
            "actor": actor,
            "source": source,
            "new_import_ids": new_ids,
            "verification": checked,
        }
        save(args.output_dir, "receipt-" + ph + ".json", receipt)
        return receipt
    finally:
        await engine.dispose()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, action="append", required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--env-file", type=Path, default=Path("/etc/samchat/samchat.env"))
    p.add_argument("--actor-id", default=ACTOR)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--expected-hash")
    p.add_argument("--expected-commit")
    p.add_argument("--restore", action="store_true")
    p.add_argument("--fail-after-import", type=int)
    return p


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(parser().parse_args())), default=str))
