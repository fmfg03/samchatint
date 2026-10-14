"""Fictitious rows in PGlite or the explicitly configured disposable CI database."""

import asyncio
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from threading import Thread
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from samchat.budgets import distribution as dist, service

SOURCE = "10000000-0000-4000-8000-000000000001"
ACTOR = "10000000-0000-4000-8000-000000000002"
L1 = "20000000-0000-4000-8000-000000000001"
L2 = "20000000-0000-4000-8000-000000000002"
L3 = "20000000-0000-4000-8000-000000000003"
T1 = "30000000-0000-4000-8000-000000000001"
T2 = "30000000-0000-4000-8000-000000000002"


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class Session:
    def __init__(self, module):
        self.process = subprocess.Popen(
            [
                shutil.which("node"),
                str(Path(__file__).with_name("budget_distribution_pg.mjs")),
                module,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert json.loads(self.process.stdout.readline()) == {"ready": True}
        self.in_tx = False
        self.fail_on = None

    def query(self, statement, parameters=None):
        statement = (
            str(statement.compile(dialect=postgresql.dialect()))
            if hasattr(statement, "compile")
            else str(statement)
        )
        names = []

        def bind(match):
            names.append(match.group(1))
            return "$" + str(len(names))

        statement = re.sub(r"%\((\w+)\)s", bind, statement)
        self.process.stdin.write(
            json.dumps(
                {
                    "sql": statement,
                    "parameters": [(parameters or {})[name] for name in names],
                },
                default=str,
            )
            + "\n"
        )
        self.process.stdin.flush()
        response = json.loads(self.process.stdout.readline())
        if "error" in response:
            raise RuntimeError(response["error"])
        for row in response["rows"]:
            for field, value in row.items():
                if field.endswith("_at") and isinstance(value, str):
                    row[field] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return Result(response["rows"])

    async def execute(self, statement, parameters=None):
        if not self.in_tx:
            self.query("BEGIN")
            self.in_tx = True
        if self.fail_on and self.fail_on in str(statement):
            raise RuntimeError("synthetic injected failure")
        return self.query(statement, parameters)

    async def commit(self):
        if self.in_tx:
            self.query("COMMIT")
            self.in_tx = False

    async def rollback(self):
        if self.in_tx:
            self.query("ROLLBACK")
            self.in_tx = False

    async def flush(self):
        # All fixture statements are executed immediately by PostgreSQL.
        pass

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=15)
        self.process.stdout.close()
        self.process.stderr.close()


class NativeSession(Session):
    """Same fixture contract over asyncpg, in a unique throwaway schema."""

    def __init__(self, dsn):
        self.loop = asyncio.new_event_loop()
        self.thread = Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.in_tx = False
        self.fail_on = None
        self.schema = "test_budget_distribution_" + uuid4().hex

        async def connect():
            import asyncpg

            connection = await asyncpg.connect(dsn.replace("+asyncpg", ""))
            await connection.set_type_codec(
                "jsonb",
                schema="pg_catalog",
                format="text",
                encoder=lambda value: (
                    value if isinstance(value, str) else json.dumps(value)
                ),
                decoder=json.loads,
            )
            return connection

        self.connection = self.run(connect())
        self.query('CREATE SCHEMA "' + self.schema + '"')
        self.query('SET search_path TO "' + self.schema + '"')

    def run(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=30)

    def query(self, statement, parameters=None):
        statement = (
            str(statement.compile(dialect=postgresql.dialect()))
            if hasattr(statement, "compile")
            else str(statement)
        )
        names = []

        def bind(match):
            names.append(match.group(1))
            return "$" + str(len(names))

        statement = re.sub(r"%\((\w+)\)s", bind, statement)
        values = [(parameters or {})[name] for name in names]
        # These fixture numeric binds can be strings in the PGlite adapter.
        values = [
            (
                Decimal(value)
                if isinstance(value, str) and name in {"amount", "a_amount", "b_amount"}
                else value
            )
            for name, value in zip(names, values)
        ]
        rows = self.run(self.connection.fetch(statement, *values))
        return Result(
            [
                {
                    key: str(value) if isinstance(value, (Decimal, UUID)) else value
                    for key, value in dict(row).items()
                }
                for row in rows
            ]
        )

    def close(self):
        if self.in_tx:
            self.query("ROLLBACK")
        self.query('DROP SCHEMA "' + self.schema + '" CASCADE')
        self.run(self.connection.close())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        self.loop.close()


@pytest.fixture
def pg(monkeypatch):
    module = os.environ.get("SAMCHAT_TEST_PGLITE_PATH")
    ci_dsn = os.environ.get("TEST_AMEX_DATABASE_URL")
    disposable_ci_dsn = (
        "postgresql+asyncpg://test_user:test_password@127.0.0.1:55471/devnous_test"
    )
    if module and Path(module).is_file() and shutil.which("node"):
        session = Session(module)
    elif os.environ.get("GITHUB_ACTIONS") == "true" and ci_dsn == disposable_ci_dsn:
        session = NativeSession(ci_dsn)
    else:
        pytest.skip(
            "Explicit PGlite module or exact disposable GitHub CI database required"
        )
    statements = [
        "CREATE TABLE budget_versions(id uuid PRIMARY KEY,edition_year int,version_name text,status text,source text,notes text,artifact_path text,created_by_empleado_id uuid,created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now())",
        "CREATE TABLE budget_lines(id uuid PRIMARY KEY,budget_version_id uuid REFERENCES budget_versions(id),metadata jsonb DEFAULT '{}',created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now(),"
        + ",".join(
            field
            + (
                " numeric(18,2)"
                if field in {"budget_amount", "reference_amount", "variance_amount"}
                else (
                    " uuid"
                    if field in {"tournament_id", "budget_concept_id"}
                    else " text"
                )
            )
            for field in dist.PROTECTED
        )
        + ")",
        "CREATE TABLE budget_line_monthly_plan(id uuid PRIMARY KEY,budget_line_id uuid REFERENCES budget_lines(id),month_number int,budget_expense_amount numeric(18,2),expected_income_amount numeric(18,2),metadata jsonb,created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now(),UNIQUE(budget_line_id,month_number))",
        "CREATE TABLE budget_line_monthly_allocations(id uuid PRIMARY KEY,budget_line_id uuid REFERENCES budget_lines(id),month_number int,allocated_amount numeric(18,2),metadata jsonb,created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now(),UNIQUE(budget_line_id,month_number))",
        "CREATE TABLE budget_approvals(id uuid PRIMARY KEY,budget_version_id uuid REFERENCES budget_versions(id),approved_by_empleado_id uuid,approval_status text,approval_note text,approved_at timestamptz,created_at timestamptz DEFAULT now())",
        "CREATE TABLE budget_version_audit_log(id uuid PRIMARY KEY,budget_version_id uuid REFERENCES budget_versions(id),event_type text,actor_empleado_id uuid,from_status text,to_status text,note text,payload jsonb,created_at timestamptz DEFAULT now())",
    ]
    for sql in statements:
        session.query(sql)
    session.query(
        text(
            "INSERT INTO budget_versions(id,edition_year,version_name,status,source) VALUES (:id,2026,'Ficticio anual','frozen','ui_manual')"
        ),
        {"id": SOURCE},
    )
    for line, tournament, name, direction, amount in (
        (L1, T1, "Material ficticio", "expense", 520),
        (L2, T1, "Ingreso ficticio", "income", 200),
        (L3, T2, "Transporte ficticio", "expense", 70),
    ):
        session.query(
            text(
                "INSERT INTO budget_lines(id,budget_version_id,tournament_id,tournament_code,tournament_name,sport,phase,entity_name,concept_name,line_direction,account_code_suggested,budget_amount,reference_amount,variance_amount,metadata) VALUES (:id,:version,:t,'FIC','Torneo ficticio','deporte ficticio','Fase ficticia','Entidad ficticia',:name,:direction,:account,:amount,0,:amount,CAST(:metadata AS jsonb))"
            ),
            {
                "id": line,
                "version": SOURCE,
                "t": tournament,
                "name": name,
                "direction": direction,
                "account": "5100-001",
                "amount": amount,
                "metadata": json.dumps(
                    {"upload_source": {"file": "fiction.xlsx", "row": 2}}
                ),
            },
        )
    session.query(
        text(
            "INSERT INTO budget_line_monthly_plan VALUES(gen_random_uuid(),:line,1,0,200,'{}',now(),now())"
        ),
        {"line": L2},
    )
    monkeypatch.setattr(service, "ensure_budget_schema", AsyncMock())
    yield session
    session.close()


async def rows(pg, version):
    return (
        await pg.execute(
            text(
                "SELECT * FROM budget_lines WHERE budget_version_id=CAST(:id AS uuid) ORDER BY id"
            ),
            {"id": version},
        )
    ).all()


@pytest.mark.asyncio
async def test_revision_full_cycle_preserves_frozen_history_and_income(pg):
    original = await rows(pg, SOURCE)
    target = await dist.create_revision(
        pg, source_version_id=SOURCE, actor_empleado_id=ACTOR
    )
    for status in ("reforecast", "closed"):
        with pytest.raises(ValueError, match="fuente de una revisión"):
            await service.transition_budget_version(
                pg, version_id=SOURCE, new_status=status, actor_empleado_id=ACTOR
            )
        await pg.rollback()
    replay = await dist.create_revision(
        pg, source_version_id=SOURCE, actor_empleado_id=ACTOR
    )
    assert replay["id"] == target["id"]
    copies = await rows(pg, target["id"])
    assert len(copies) == 3
    for clone in copies:
        origin = next(
            r
            for r in original
            if r["id"] == clone["metadata"]["distribution_source_line_id"]
        )
        assert all(clone[k] == origin[k] for k in dist.PROTECTED)
        assert clone["metadata"]["upload_source"] == origin["metadata"]["upload_source"]
    versions = await service.list_budget_versions(
        pg, edition_year=2026, ensure_schema=False
    )
    assert dist.select_version(versions)["id"] == SOURCE
    await service.transition_budget_version(
        pg, version_id=target["id"], new_status="submitted", actor_empleado_id=ACTOR
    )
    with pytest.raises(ValueError, match="Completa y concilia"):
        await service.transition_budget_version(
            pg, version_id=target["id"], new_status="approved", actor_empleado_id=ACTOR
        )
    await pg.rollback()
    await service.transition_budget_version(
        pg, version_id=target["id"], new_status="draft", actor_empleado_id=ACTOR
    )
    for clone in copies:
        if clone["line_direction"] == "expense":
            with pytest.raises(ValueError, match="sólo distribuir"):
                await service.update_budget_line(
                    pg,
                    line_id=clone["id"],
                    updates={"budget_amount": float(clone["budget_amount"]) + 1},
                    ensure_schema=False,
                )
            await pg.rollback()
            await service.replace_budget_line_monthly_plan(
                pg,
                budget_line_id=clone["id"],
                plan={1: {"budget_expense_amount": clone["budget_amount"]}},
                ensure_schema=False,
                actor_empleado_id=ACTOR,
            )
            await pg.commit()
    await service.transition_budget_version(
        pg, version_id=target["id"], new_status="submitted", actor_empleado_id=ACTOR
    )
    approved = await service.transition_budget_version(
        pg, version_id=target["id"], new_status="approved", actor_empleado_id=ACTOR
    )
    assert approved["distribution_source_version_id"] == SOURCE
    for status in ("reforecast", "closed"):
        with pytest.raises(ValueError, match="distribución aprobada conserva"):
            await service.transition_budget_version(
                pg, version_id=target["id"], new_status=status, actor_empleado_id=ACTOR
            )
        await pg.rollback()
    assert (
        await service._select_budget_version(pg, edition_year=2026, ensure_schema=False)
    )["id"] == target["id"]
    assert (await service.resolve_definitive_budget_version(pg, edition_year=2026))[
        "id"
    ] == target["id"]
    assert await rows(pg, SOURCE) == original
    assert (await dist.revision_context(pg, SOURCE))["status"] == "frozen"

    with pytest.raises(ValueError, match="fuente de una revisión"):
        await service.transition_budget_version(
            pg, version_id=SOURCE, new_status="reforecast", actor_empleado_id=ACTOR
        )
    await pg.rollback()
    with pytest.raises(ValueError, match="Only draft or reforecast"):
        await service.replace_budget_line_monthly_plan(
            pg,
            budget_line_id=copies[0]["id"],
            plan={1: {"budget_expense_amount": 0}},
            ensure_schema=False,
        )
    await pg.rollback()


@pytest.mark.asyncio
async def test_multiple_approvals_do_not_multiply_authorized_amounts(pg):
    for _ in range(2):
        pg.query(
            text(
                "INSERT INTO budget_approvals(id,budget_version_id,approval_status,approved_at) VALUES(gen_random_uuid(),:id,'approved',now())"
            ),
            {"id": SOURCE},
        )
    versions = await service.list_budget_versions(
        pg, edition_year=2026, ensure_schema=False
    )
    assert versions[0]["line_count"] == 3 and versions[0]["budget_total"] == 790


@pytest.mark.asyncio
async def test_authorized_amount_and_plan_rollback_together_on_invalid_period(pg):
    pg.query(
        text("UPDATE budget_versions SET status='draft' WHERE id=:id"), {"id": SOURCE}
    )
    await service.update_budget_line(
        pg,
        line_id=L1,
        updates={"budget_amount": 600},
        commit=False,
        ensure_schema=False,
    )
    with pytest.raises(ValueError, match="Semana fuera"):
        await service.replace_budget_line_monthly_plan(
            pg,
            budget_line_id=L1,
            plan={53: {"budget_expense_amount": 600}},
            ensure_schema=False,
        )
    await pg.rollback()
    restored = next(row for row in await rows(pg, SOURCE) if row["id"] == L1)
    assert restored["budget_amount"] == "520.00"


@pytest.mark.asyncio
async def test_standard_annual_can_be_approved_without_plan_but_not_mismatched_plan(pg):
    pg.query(
        text("UPDATE budget_versions SET status='submitted' WHERE id=:id"),
        {"id": SOURCE},
    )
    pg.query(
        text(
            "INSERT INTO budget_line_monthly_plan VALUES(gen_random_uuid(),:line,1,500,0,'{}',now(),now())"
        ),
        {"line": L1},
    )
    with pytest.raises(ValueError, match="no concilia"):
        await service.transition_budget_version(
            pg, version_id=SOURCE, new_status="approved", actor_empleado_id=ACTOR
        )
    await pg.rollback()
    assert (await dist.revision_context(pg, SOURCE))["status"] == "submitted"
    await pg.rollback()
    pg.query(
        text("DELETE FROM budget_line_monthly_plan WHERE budget_line_id=:id"),
        {"id": L1},
    )
    approved = await service.transition_budget_version(
        pg, version_id=SOURCE, new_status="approved", actor_empleado_id=ACTOR
    )
    assert approved["status"] == "approved"
    plan = await service.list_monthly_plan_for_lines(
        pg, line_ids=[L1], ensure_schema=False
    )
    assert plan == {}


@pytest.mark.asyncio
async def test_expense_revision_preserves_legacy_income_in_same_plan(pg):
    pg.query(
        text(
            "INSERT INTO budget_line_monthly_plan VALUES(gen_random_uuid(),:line,1,200,40,'{}',now(),now())"
        ),
        {"line": L1},
    )
    target = await dist.create_revision(
        pg, source_version_id=SOURCE, actor_empleado_id=ACTOR
    )
    clone = next(
        row
        for row in await rows(pg, target["id"])
        if row["metadata"]["distribution_source_line_id"] == L1
    )
    await service.replace_budget_line_monthly_plan(
        pg,
        budget_line_id=clone["id"],
        plan={3: {"budget_expense_amount": 520}},
        ensure_schema=False,
    )
    plan = (
        await service.list_monthly_plan_for_lines(
            pg, line_ids=[clone["id"]], ensure_schema=False
        )
    )[clone["id"]]
    assert (
        plan[1]["expected_income_amount"] == 40
        and plan[1]["budget_expense_amount"] == 0
    )
    assert plan[3]["budget_expense_amount"] == 520
    await pg.commit()
    with pytest.raises(ValueError, match="no cambia ingresos"):
        await service.replace_budget_line_monthly_plan(
            pg,
            budget_line_id=clone["id"],
            plan={1: {"budget_expense_amount": 520, "expected_income_amount": 0}},
            ensure_schema=False,
        )
    await pg.rollback()


@pytest.mark.asyncio
async def test_clone_failure_rolls_back_every_insert(pg):
    pg.fail_on = "INSERT INTO budget_line_monthly_allocations"
    with pytest.raises(RuntimeError, match="synthetic injected"):
        await dist.create_revision(
            pg, source_version_id=SOURCE, actor_empleado_id=ACTOR
        )
    assert pg.query("SELECT COUNT(*) AS n FROM budget_versions").first()["n"] == 1
    assert pg.query("SELECT COUNT(*) AS n FROM budget_lines").first()["n"] == 3
    assert (
        pg.query("SELECT COUNT(*) AS n FROM budget_version_audit_log").first()["n"] == 0
    )


@pytest.mark.asyncio
async def test_approval_rejects_swapped_amounts_even_with_same_total(pg):
    target = await dist.create_revision(
        pg, source_version_id=SOURCE, actor_empleado_id=ACTOR
    )
    copies = await rows(pg, target["id"])
    expenses = [r for r in copies if r["line_direction"] == "expense"]
    await pg.execute(
        text(
            "UPDATE budget_lines SET budget_amount=CASE WHEN id=CAST(:a AS uuid) THEN CAST(:b_amount AS numeric) ELSE CAST(:a_amount AS numeric) END WHERE id IN(CAST(:a AS uuid),CAST(:b AS uuid))"
        ),
        {
            "a": expenses[0]["id"],
            "b": expenses[1]["id"],
            "a_amount": expenses[0]["budget_amount"],
            "b_amount": expenses[1]["budget_amount"],
        },
    )
    await pg.commit()
    await service.transition_budget_version(
        pg, version_id=target["id"], new_status="submitted", actor_empleado_id=ACTOR
    )
    with pytest.raises(ValueError, match="alteró una partida"):
        await service.transition_budget_version(
            pg, version_id=target["id"], new_status="approved", actor_empleado_id=ACTOR
        )
    await pg.rollback()
    assert (await dist.revision_context(pg, SOURCE))["status"] == "frozen"
