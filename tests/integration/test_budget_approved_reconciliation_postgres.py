"""Real SERIALIZABLE transaction/race acceptance on an owned disposable DB.

Only the snapshot SQL seam is narrowed to these fixture records; projection,
actor/governance checks, source locks, BMA writes and audit use their real owners.
The full canonical snapshot is separately exercised on the restored-source run.
"""

import asyncio
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import devnous.gastos  # noqa: F401
from samchat.budgets import reconciliation as owner
from samchat.budgets import service

spec = importlib.util.spec_from_file_location(
    "approved_pg_server_fixture",
    Path(__file__).with_name("test_tournament_weekly_overlay_postgres.py"),
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
V = fixture.VERSION
A = fixture.ACTOR
T = fixture.LIGA
L = fixture.LIGA_COST
C = "40000000-0000-4000-8000-000000000001"
D = "60000000-0000-4000-8000-000000000001"
E = "70000000-0000-4000-8000-000000000001"
P = "80000000-0000-4000-8000-000000000001"
POSTING = "90000000-0000-4000-8000-000000000001"
OLD = "90000000-0000-4000-8000-000000000002"
KW = {
    "budget_version_id": V,
    "edition_year": 2026,
    "actor_empleado_id": A,
    "reason": "Asignación revisada",
    "assignments": [
        {
            "tournament_id": T,
            "movement_key": "accounting:" + POSTING,
            "budget_line_id": L,
        }
    ],
}


@pytest_asyncio.fixture
async def database(monkeypatch):
    server = fixture._test_server()
    admin = await asyncpg.connect(server.replace("+asyncpg", ""))
    name = "approved_reconciliation_test_" + uuid4().hex
    await admin.execute(
        f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8'"
    )
    engine = create_async_engine(server.rsplit("/", 1)[0] + "/" + name)
    try:
        async with engine.begin() as conn:
            raw = await conn.get_raw_connection()
            await raw.driver_connection.execute(fixture.SCHEMA)
            await raw.driver_connection.execute(
                (
                    "\n              ALTER TABLE budget_concepts ADD act"
                    "ive boolean DEFAULT true, ADD metadata jsonb DEFAU"
                    "LT '{}';\n              CREATE TABLE analyst_case_v"
                    "ersions(case_id text,version_number int,answer_con"
                    "tract jsonb);\n              CREATE TABLE documento"
                    "s(id uuid PRIMARY KEY,estado text,monto numeric);\n"
                    "              CREATE TABLE expense_reports(id uuid"
                    " PRIMARY KEY,documento_id uuid,monto numeric);\n   "
                    "           CREATE TABLE accounting_polizas(id uuid"
                    " PRIMARY KEY,fecha_poliza date);\n              CRE"
                    "ATE TABLE accounting_poliza_lines(id uuid PRIMARY "
                    "KEY,poliza_id uuid,source_metadata jsonb,debe nume"
                    "ric,haber numeric);\n              CREATE TABLE bud"
                    "get_movement_assignments(id uuid PRIMARY KEY,budge"
                    "t_version_id uuid REFERENCES budget_versions(id),b"
                    "udget_concept_id uuid REFERENCES budget_concepts(i"
                    "d),budget_line_id uuid REFERENCES budget_lines(id)"
                    ",movement_key text,assigned_by_empleado_id uuid RE"
                    "FERENCES empleados(id),created_at timestamptz DEFA"
                    "ULT now(),updated_at timestamptz DEFAULT now(),UNI"
                    "QUE(budget_version_id,movement_key));\n            "
                )
            )
            await conn.execute(
                text(
                    (
                        "INSERT INTO empleados(id,nombre,rol,activo) VALUES"
                        "(CAST(:a AS uuid),'Approver','superadmin',true)"
                    )
                ),
                {"a": A},
            )
            await conn.execute(
                text("INSERT INTO tournaments VALUES(CAST(:t AS uuid),'Liga',true)"),
                {"t": T},
            )
            await conn.execute(
                text(
                    (
                        "INSERT INTO budget_versions(id,edition_year,versio"
                        "n_name,status,source,notes) VALUES(CAST(:v AS uuid"
                        "),2026,'Approved','approved','ui_manual','Borrador"
                        "')"
                    )
                ),
                {"v": V},
            )
            await conn.execute(
                text(
                    (
                        "INSERT INTO budget_concepts(id,concept_name) VALUE"
                        "S(CAST(:c AS uuid),'Hotel')"
                    )
                ),
                {"c": C},
            )
            await conn.execute(
                text(
                    (
                        "INSERT INTO budget_lines(id,budget_version_id,tour"
                        "nament_id,tournament_name,phase,concept_name,budge"
                        "t_concept_id,budget_amount) VALUES(CAST(:l AS uuid"
                        "),CAST(:v AS uuid),CAST(:t AS uuid),'Liga','Fase E"
                        "statal','Hotel',CAST(:c AS uuid),500)"
                    )
                ),
                {"l": L, "v": V, "t": T, "c": C},
            )
            await conn.execute(
                text("INSERT INTO documentos VALUES(CAST(:d AS uuid),'pagado',105.75)"),
                {"d": D},
            )
            await conn.execute(
                text(
                    (
                        "INSERT INTO expense_reports VALUES(CAST(:e AS uuid"
                        "),CAST(:d AS uuid),100.25)"
                    )
                ),
                {"e": E, "d": D},
            )
            await conn.execute(
                text(
                    (
                        "INSERT INTO accounting_polizas VALUES(CAST(:p AS u"
                        "uid),'2026-08-20')"
                    )
                ),
                {"p": P},
            )
            for pid, debe, haber in [
                (POSTING, 100.25, 0),
                (OLD, 5.5, 0),
                ("90000000-0000-4000-8000-000000000003", 0, 105.75),
            ]:
                await conn.execute(
                    text(
                        (
                            "INSERT INTO accounting_poliza_lines VALUES(CAST(:i"
                            "d AS uuid),CAST(:p AS uuid),CAST(:m AS jsonb),:d,:"
                            "h)"
                        )
                    ),
                    {
                        "id": pid,
                        "p": P,
                        "m": json.dumps(
                            {
                                "concept_key": C,
                                "document_id": D,
                                "expense_id": E,
                                "effective_phase": "Estatal",
                                "kind": "ledger_expense",
                                "origen": "proveedor_aprobacion",
                            }
                        ),
                        "d": debe,
                        "h": haber,
                    },
                )
            await conn.execute(
                text(
                    (
                        "INSERT INTO budget_movement_assignments(id,budget_"
                        "version_id,budget_concept_id,budget_line_id,moveme"
                        "nt_key,assigned_by_empleado_id) VALUES(gen_random_"
                        "uuid(),CAST(:v AS uuid),CAST(:c AS uuid),CAST(:l A"
                        "S uuid),:key,CAST(:a AS uuid))"
                    )
                ),
                {"v": V, "c": C, "l": L, "key": "accounting:" + OLD, "a": A},
            )

        async def snapshot(session, **kwargs):
            rows = (
                (
                    await session.execute(
                        text(
                            (
                                "SELECT id::text,poliza_id::text,source_metadata,de"
                                "be,haber FROM accounting_poliza_lines WHERE debe>0"
                                " ORDER BY id"
                            )
                        )
                    )
                )
                .mappings()
                .all()
            )
            movements = [
                {
                    "accounting_line_id": r["id"],
                    "poliza_id": r["poliza_id"],
                    **r["source_metadata"],
                    "amount": float(r["debe"] - r["haber"]),
                    "month_number": 34,
                }
                for r in rows
            ]
            context = await service._movement_assignment_projection_context(
                session, budget_version_id=kwargs["version_id"], movements=movements
            )
            store = service._monthly_actual_store()
            service._project_explicit_movement_assignments(
                movements=movements, store=store, **context
            )
            return {
                "movements": movements,
                "monthly": {k: dict(v) for k, v in store.items()},
            }

        monkeypatch.setattr(service, "build_budget_actuals_snapshot", snapshot)
        yield engine
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def preview(engine, **changes):
    async with AsyncSession(engine) as s:
        await s.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY"))
        return await owner.preview_approved_movement_reconciliation(
            s, **{**KW, **changes}
        )


async def apply(engine, plan, **changes):
    async with AsyncSession(engine) as s:
        async with s.begin():
            await s.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
            return await owner.apply_approved_movement_reconciliation(
                s, expected_plan_sha256=plan["plan_sha256"], **{**KW, **changes}
            )


async def protected(engine):
    async with AsyncSession(engine) as s:
        return {
            t: list(
                (
                    await s.execute(
                        text("SELECT to_jsonb(t) FROM " + t + " t ORDER BY id")
                    )
                ).scalars()
            )
            for t in [
                "budget_versions",
                "budget_lines",
                "budget_concepts",
                "documentos",
                "expense_reports",
                "accounting_polizas",
                "accounting_poliza_lines",
            ]
        }


async def counts(engine):
    async with AsyncSession(engine) as s:
        return tuple(
            [
                await s.scalar(text("SELECT count(*) FROM " + table))
                for table in ["budget_movement_assignments", "budget_version_audit_log"]
            ]
        )


@pytest.mark.asyncio
async def test_real_transaction_actor_projection_and_replay(database):
    before = await protected(database)
    plan = await preview(database)
    assert await counts(database) == (1, 0)
    applied = await apply(database, plan)
    assert applied["status"] == "APPLIED"
    assert await counts(database) == (2, 1)
    assert await protected(database) == before
    replay = await apply(database, plan)
    assert (
        replay["status"] == "ALREADY_APPLIED"
        and replay["audit_id"] == applied["audit_id"]
    )
    async with AsyncSession(database) as s:
        await s.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY"))
        old = await owner.recover_approved_movement_reconciliation(s, **KW)
    assert old["audit_id"] == applied["audit_id"] and await counts(database) == (2, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE expense_reports SET monto=999",
        "UPDATE budget_lines SET phase='Nacional'",
        "UPDATE budget_concepts SET active=false",
    ],
)
async def test_changed_context_cannot_be_confirmed(database, mutation):
    p = await preview(database)
    async with database.begin() as c:
        await c.execute(text(mutation))
    with pytest.raises(ValueError):
        await apply(database, p)
    assert await counts(database) == (1, 0)


@pytest.mark.asyncio
async def test_failure_between_binding_and_audit_rolls_back_all(database):
    p = await preview(database)
    before = await protected(database)

    def fault(conn, cursor, statement, params, ctx, many):
        if "INSERT INTO budget_version_audit_log" in statement:
            raise RuntimeError("injected audit failure")

    event.listen(database.sync_engine, "before_cursor_execute", fault)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            await apply(database, p)
    finally:
        event.remove(database.sync_engine, "before_cursor_execute", fault)
    assert await counts(database) == (1, 0) and await protected(database) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,mutation,wait_sql",
    [
        ("expense_reports", "monto=999", "SELECT id FROM expense_reports"),
        ("budget_lines", "budget_amount=999", "SELECT id FROM budget_lines"),
    ],
)
async def test_source_or_target_update_while_waiting_aborts_serializable(
    database, table, mutation, wait_sql
):
    p = await preview(database)
    waiting = asyncio.Event()

    def observe(conn, cursor, sql, params, ctx, many):
        if sql.startswith(wait_sql) and "FOR SHARE" in sql:
            waiting.set()

    event.listen(database.sync_engine, "before_cursor_execute", observe)
    async with AsyncSession(database) as other:
        await other.execute(text("UPDATE " + table + " SET " + mutation))
        task = asyncio.create_task(apply(database, p))
        try:
            await asyncio.wait_for(waiting.wait(), 5)
            await other.commit()
            with pytest.raises(DBAPIError):
                await asyncio.wait_for(task, 10)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            event.remove(database.sync_engine, "before_cursor_execute", observe)
    assert await counts(database) == (1, 0)


@pytest.mark.asyncio
async def test_generic_stale_draft_writer_cannot_overwrite_after_approval(database):
    async with database.begin() as c:
        await c.execute(text("UPDATE budget_versions SET status='draft'"))
    waiting = asyncio.Event()

    def observe(conn, cursor, sql, params, ctx, many):
        if sql.startswith("SELECT id FROM budget_versions") and "FOR UPDATE" in sql:
            waiting.set()

    async def legacy():
        async with AsyncSession(database) as s:
            async with s.begin():
                await service.assign_budget_movement_to_line(
                    s,
                    budget_version_id=V,
                    budget_concept_id=C,
                    budget_line_id=L,
                    movement_key="accounting:" + POSTING,
                    actor_empleado_id=A,
                    ensure_schema=False,
                )

    event.listen(database.sync_engine, "before_cursor_execute", observe)
    async with AsyncSession(database) as approver:
        await approver.execute(text("UPDATE budget_versions SET status='approved'"))
        task = asyncio.create_task(legacy())
        try:
            await asyncio.wait_for(waiting.wait(), 5)
            await approver.commit()
            with pytest.raises(ValueError, match="Partida inválida"):
                await asyncio.wait_for(task, 10)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            event.remove(database.sync_engine, "before_cursor_execute", observe)
    assert await counts(database) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("different_intention", [False, True])
async def test_concurrent_confirmations_never_overwrite_or_duplicate(
    database, different_intention
):
    p = await preview(database)
    other_reason = "Otra decisión" if different_intention else KW["reason"]
    q = await preview(database, reason=other_reason)
    outcomes = await asyncio.gather(
        apply(database, p),
        apply(database, q, reason=other_reason),
        return_exceptions=True,
    )
    assert any(isinstance(x, dict) and x["status"] == "APPLIED" for x in outcomes)
    assert all(isinstance(x, (dict, DBAPIError, ValueError)) for x in outcomes)
    assert await counts(database) == (2, 1)


@pytest.mark.asyncio
async def test_real_postprojection_failure_rolls_back_without_an_audit(
    database, monkeypatch
):
    plan = await preview(database)
    before = await protected(database)
    reader = service.build_budget_actuals_snapshot

    async def damaged(session, **kwargs):
        result = await reader(session, **kwargs)
        exists = await session.scalar(
            text(
                "SELECT count(*) FROM budget_movement_assignments "
                "WHERE movement_key=:key"
            ),
            {"key": "accounting:" + POSTING},
        )
        if exists:
            result["movements"][0]["amount"] += 1
        return result

    monkeypatch.setattr(service, "build_budget_actuals_snapshot", damaged)
    with pytest.raises(owner.ReconciliationConflict, match="proyección real"):
        await apply(database, plan)
    assert await counts(database) == (1, 0) and await protected(database) == before
