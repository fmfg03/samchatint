"""Acceptance checks against a disposable PostgreSQL database only.

The opt-in DSN is deliberately pinned to a throwaway local instance. The same
suite also supports the exact disposable GitHub CI instance used by this repo.
No production URL is accepted, even if it is passed in the environment.
"""

import asyncio
from decimal import Decimal
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import devnous.gastos  # noqa: F401  # Initialize in the application startup order.
from samchat.budgets import distribution, service, tournament_weekly_overlay as overlay


VERSION = "10000000-0000-4000-8000-000000000001"
ACTOR = "10000000-0000-4000-8000-000000000002"
DENIED = "10000000-0000-4000-8000-000000000003"
INACTIVE = "10000000-0000-4000-8000-000000000004"
LIGA = "30000000-0000-4000-8000-000000000001"
HOMELESS = "30000000-0000-4000-8000-000000000002"
COPA = "30000000-0000-4000-8000-000000000003"
LIGA_COST = "20000000-0000-4000-8000-000000000001"
LIGA_ZERO = "20000000-0000-4000-8000-000000000002"
LIGA_INCOME = "20000000-0000-4000-8000-000000000003"
HOMELESS_COST = "20000000-0000-4000-8000-000000000004"
COPA_UNKNOWN = "20000000-0000-4000-8000-000000000005"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "database/migrations/20261027_tournament_weekly_overlay.sql"
)


def _test_server():
    local = "postgresql+asyncpg://postgres@127.0.0.1:55479/postgres"
    ci = "postgresql+asyncpg://test_user:test_password@127.0.0.1:55471/devnous_test"
    configured = os.getenv("SAMCHAT_WEEKLY_TEST_DATABASE_URL")
    if configured == local:
        return configured
    if (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("TEST_AMEX_DATABASE_URL") == ci
    ):
        return ci
    pytest.skip("Requires the exact disposable weekly-test PostgreSQL server")


SCHEMA = """
CREATE TABLE empleados(id uuid PRIMARY KEY,nombre text NOT NULL,rol text NOT NULL,activo boolean NOT NULL,departamento text,correo text);
CREATE TABLE tournaments(id uuid PRIMARY KEY,name text NOT NULL,active boolean NOT NULL);
CREATE TABLE budget_versions(id uuid PRIMARY KEY,edition_year int NOT NULL,version_name text NOT NULL,status text NOT NULL,source text NOT NULL,notes text,artifact_path text,created_by_empleado_id uuid,created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now());
CREATE TABLE budget_lines(id uuid PRIMARY KEY,budget_version_id uuid REFERENCES budget_versions(id),metadata jsonb NOT NULL DEFAULT '{}',created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now(),
  tournament_id uuid, tournament_code text,tournament_name text NOT NULL,sport text,phase text,entity_name text,concept_name text NOT NULL,budget_concept_id uuid,line_direction text NOT NULL DEFAULT 'expense',account_code_suggested text,account_code_final text,budget_amount numeric(18,2) NOT NULL,reference_amount numeric(18,2) DEFAULT 0,variance_amount numeric(18,2) DEFAULT 0,priority text,owner_name text,criteria_note text,observations text);
CREATE TABLE budget_concepts(id uuid PRIMARY KEY,concept_name text);
CREATE TABLE budget_line_monthly_plan(id uuid PRIMARY KEY,budget_line_id uuid REFERENCES budget_lines(id),month_number int,budget_expense_amount numeric(18,2),expected_income_amount numeric(18,2),metadata jsonb NOT NULL DEFAULT '{}',created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now(),UNIQUE(budget_line_id,month_number));
CREATE TABLE budget_line_monthly_allocations(id uuid PRIMARY KEY,budget_line_id uuid REFERENCES budget_lines(id),month_number int,allocated_amount numeric(18,2),metadata jsonb NOT NULL DEFAULT '{}',created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now(),UNIQUE(budget_line_id,month_number));
CREATE TABLE budget_approvals(id uuid PRIMARY KEY,budget_version_id uuid REFERENCES budget_versions(id),approved_by_empleado_id uuid,approval_status text,approval_note text,approved_at timestamptz,created_at timestamptz DEFAULT now());
CREATE TABLE budget_version_audit_log(id uuid PRIMARY KEY,budget_version_id uuid REFERENCES budget_versions(id),event_type text,actor_empleado_id uuid,from_status text,to_status text,note text,payload jsonb,created_at timestamptz DEFAULT now());
"""


@pytest_asyncio.fixture
async def database():
    server = _test_server()
    admin_dsn = server.replace("+asyncpg", "")
    name = "weekly_overlay_test_" + uuid4().hex
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    target = server.rsplit("/", 1)[0] + "/" + name
    engine = create_async_engine(target)
    try:
        async with engine.begin() as conn:
            raw = await conn.get_raw_connection()
            await raw.driver_connection.execute(SCHEMA)
            # Execute the actual owner-run migration against the isolated DB.
            migration = (
                MIGRATION.read_text().replace("BEGIN;", "", 1).rsplit("COMMIT;", 1)[0]
            )
            await raw.driver_connection.execute(migration)
            await conn.execute(
                text(
                    """INSERT INTO empleados(id,nombre,rol,activo) VALUES
              (CAST(:a AS uuid),'Authorized','superadmin',true),
              (CAST(:d AS uuid),'Denied','empleado',true),
              (CAST(:i AS uuid),'Inactive','superadmin',false)"""
                ),
                {"a": ACTOR, "d": DENIED, "i": INACTIVE},
            )
            for tournament, name_label in [
                (LIGA, "Liga"),
                (HOMELESS, "Homeless"),
                (COPA, "Copa"),
            ]:
                await conn.execute(
                    text(
                        "INSERT INTO tournaments VALUES (CAST(:id AS uuid),:name,true)"
                    ),
                    {"id": tournament, "name": name_label},
                )
            await conn.execute(
                text(
                    "INSERT INTO budget_versions(id,edition_year,version_name,status,source) VALUES (CAST(:id AS uuid),2026,'Annual approved','approved','ui_manual')"
                ),
                {"id": VERSION},
            )
            for lid, tid, name_label, direction, amount in [
                (LIGA_COST, LIGA, "Liga material", "expense", "584640.00"),
                (LIGA_ZERO, LIGA, "Liga zero", "expense", "0.00"),
                (LIGA_INCOME, LIGA, "Liga income", "income", "50.00"),
                (HOMELESS_COST, HOMELESS, "Homeless transport", "expense", "99.91"),
                (COPA_UNKNOWN, COPA, "Copa unknown", "expense", "0.00"),
            ]:
                await conn.execute(
                    text(
                        """INSERT INTO budget_lines
                  (id,budget_version_id,tournament_id,tournament_name,phase,concept_name,line_direction,account_code_final,budget_amount)
                  VALUES(CAST(:id AS uuid),CAST(:version AS uuid),CAST(:tid AS uuid),:tname,'Fase',:name,:direction,'5300-001-001',CAST(:amount AS numeric))"""
                    ),
                    {
                        "id": lid,
                        "version": VERSION,
                        "tid": tid,
                        "tname": {
                            "Liga material": "Liga",
                            "Liga zero": "Liga",
                            "Liga income": "Liga",
                            "Homeless transport": "Homeless",
                            "Copa unknown": "Copa",
                        }[name_label],
                        "name": name_label,
                        "direction": direction,
                        "amount": amount,
                    },
                )
            for lid, week, expense, income in [
                (LIGA_COST, 40, "584640.00", "11.00"),
                (LIGA_ZERO, 4, "0.00", "2.00"),
                (LIGA_INCOME, 4, "0.00", "50.00"),
            ]:
                await conn.execute(
                    text(
                        "INSERT INTO budget_line_monthly_plan(id,budget_line_id,month_number,budget_expense_amount,expected_income_amount) VALUES(gen_random_uuid(),CAST(:id AS uuid),:week,CAST(:expense AS numeric),CAST(:income AS numeric))"
                    ),
                    {"id": lid, "week": week, "expense": expense, "income": income},
                )
        yield engine
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


async def preview(session, tournament=LIGA, week=40, version=VERSION):
    return await overlay.preview_tournament_overlay(
        session, version_id=version, tournament_id=tournament, week=week
    )


async def apply(session, plan, actor=ACTOR):
    return await overlay.apply_tournament_overlay(
        session,
        version_id=plan["version_id"],
        tournament_id=plan["tournament_id"],
        week=plan["week"],
        expected_plan_hash=plan["plan_hash"],
        actor_empleado_id=actor,
    )


async def scalar(session, sql, **params):
    return (await session.execute(text(sql), params)).scalar_one()


@pytest.mark.asyncio
async def test_preview_is_read_only_and_exact_to_the_cent(database):
    async with AsyncSession(database) as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        plan = await preview(session)
        assert plan["week"] == 40
        assert Decimal(str(plan["total"])) == Decimal("584640.00")
        assert len(plan["lines"]) == 2
        assert {str(r["source_line_id"]) for r in plan["lines"]} == {
            LIGA_COST,
            LIGA_ZERO,
        }
        assert (
            await scalar(
                session, "SELECT count(*) FROM budget_tournament_weekly_revisions"
            )
            == 0
        )
        assert (
            await scalar(session, "SELECT count(*) FROM budget_tournament_weekly_lines")
            == 0
        )
        assert (
            await scalar(session, "SELECT count(*) FROM budget_version_audit_log") == 0
        )


@pytest.mark.asyncio
async def test_effective_scope_preserves_legacy_history_income_and_unknown_zero(
    database,
):
    async with AsyncSession(database) as session:
        plan = await preview(session)
        before = await service.list_monthly_plan_for_lines(
            session,
            line_ids=[LIGA_COST, LIGA_ZERO, LIGA_INCOME, HOMELESS_COST, COPA_UNKNOWN],
            ensure_schema=False,
        )
        await apply(session, plan)
        await session.commit()
        raw = await service.list_monthly_plan_for_lines(
            session,
            line_ids=[LIGA_COST, LIGA_ZERO, LIGA_INCOME, HOMELESS_COST, COPA_UNKNOWN],
            ensure_schema=False,
        )
        assert raw == before
        effective, conflicts = await overlay.effective_plans_for_lines(
            session,
            version_id=VERSION,
            line_ids=[LIGA_COST, LIGA_ZERO, LIGA_INCOME, HOMELESS_COST, COPA_UNKNOWN],
            baseline_plans=raw,
        )
        assert not conflicts
        assert Decimal(
            str(effective[LIGA_COST][40]["budget_expense_amount"])
        ) == Decimal("584640.00")
        assert Decimal(
            str(effective[LIGA_COST][40]["expected_income_amount"])
        ) == Decimal("11.00")
        assert Decimal(
            str(effective[LIGA_ZERO][40]["budget_expense_amount"])
        ) == Decimal("0.00")
        assert Decimal(
            str(effective[LIGA_ZERO][4]["expected_income_amount"])
        ) == Decimal("2.00")
        assert effective[LIGA_INCOME] == raw[LIGA_INCOME]
        assert COPA_UNKNOWN not in effective and HOMELESS_COST not in effective
        assert (
            distribution.schedule_state({"budget_amount": "0"}, effective[LIGA_ZERO])[
                "status"
            ]
            == "complete"
        )
        assert (
            distribution.schedule_state(
                {"budget_amount": "0"}, effective.get(COPA_UNKNOWN, {})
            )["status"]
            == "missing"
        )


@pytest.mark.asyncio
async def test_changed_plan_after_preview_cannot_be_applied(database):
    async with AsyncSession(database) as session:
        plan = await preview(session)
        await session.execute(
            text(
                "UPDATE budget_lines SET budget_amount=584641 WHERE id=CAST(:id AS uuid)"
            ),
            {"id": LIGA_COST},
        )
        await session.commit()
        with pytest.raises((ValueError, PermissionError)):
            await apply(session, plan)
        await session.rollback()
        assert (
            await scalar(
                session, "SELECT count(*) FROM budget_tournament_weekly_revisions"
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [DENIED, INACTIVE])
async def test_ineligible_actor_cannot_apply(database, actor):
    async with AsyncSession(database) as session:
        plan = await preview(session)
        with pytest.raises((ValueError, PermissionError)):
            await apply(session, plan, actor=actor)
        await session.rollback()
        assert (
            await scalar(
                session, "SELECT count(*) FROM budget_tournament_weekly_revisions"
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["amount", "added_line", "removed_line", "phase", "account"]
)
async def test_target_drift_fails_closed_without_contaminating_other_tournaments(
    database, mutation
):
    async with AsyncSession(database) as session:
        plan = await preview(session)
        await apply(session, plan)
        await session.commit()
        if mutation == "added_line":
            await session.execute(
                text(
                    "INSERT INTO budget_lines(id,budget_version_id,tournament_id,tournament_name,concept_name,budget_amount) VALUES(gen_random_uuid(),CAST(:v AS uuid),CAST(:t AS uuid),'Liga','Additional',1)"
                ),
                {"v": VERSION, "t": LIGA},
            )
        elif mutation == "removed_line":
            await session.execute(
                text(
                    "UPDATE budget_lines SET tournament_id=CAST(:tid AS uuid) WHERE id=CAST(:id AS uuid)"
                ),
                {"tid": COPA, "id": LIGA_ZERO},
            )
        else:
            statements = {
                "amount": "budget_amount=2",
                "phase": "phase='Otro'",
                "account": "account_code_final='5300-002-002'",
            }
            await session.execute(
                text(
                    "UPDATE budget_lines SET "
                    + statements[mutation]
                    + " WHERE id=CAST(:id AS uuid)"
                ),
                {"id": LIGA_ZERO},
            )
        await session.commit()
        baseline = await service.list_monthly_plan_for_lines(
            session,
            line_ids=[LIGA_COST, LIGA_ZERO, LIGA_INCOME, HOMELESS_COST, COPA_UNKNOWN],
            ensure_schema=False,
        )
        plans, conflicts = await overlay.effective_plans_for_lines(
            session,
            version_id=VERSION,
            line_ids=[LIGA_COST, LIGA_ZERO, LIGA_INCOME, HOMELESS_COST, COPA_UNKNOWN],
            baseline_plans=baseline,
        )
        assert LIGA in conflicts
        # Expense is no longer authoritative, but expected income is retained.
        assert plans[LIGA_COST][40]["expected_income_amount"] == 11.0
        assert plans[LIGA_COST][40]["budget_expense_amount"] == 0.0
        if mutation != "removed_line":
            assert plans[LIGA_ZERO][4]["expected_income_amount"] == 2.0
        assert plans[LIGA_INCOME] == baseline[LIGA_INCOME]
        assert HOMELESS_COST not in plans and COPA_UNKNOWN not in plans


@pytest.mark.asyncio
async def test_another_project_update_does_not_invalidate_liga(database):
    async with AsyncSession(database) as session:
        await apply(session, await preview(session))
        await session.commit()
        await session.execute(
            text(
                "UPDATE budget_lines SET budget_amount=123 WHERE id=CAST(:id AS uuid)"
            ),
            {"id": COPA_UNKNOWN},
        )
        await session.commit()
        raw = await service.list_monthly_plan_for_lines(
            session, line_ids=[LIGA_COST, LIGA_ZERO], ensure_schema=False
        )
        plans, conflicts = await overlay.effective_plans_for_lines(
            session,
            version_id=VERSION,
            line_ids=[LIGA_COST, LIGA_ZERO],
            baseline_plans=raw,
        )
        assert not conflicts
        assert Decimal(str(plans[LIGA_COST][40]["budget_expense_amount"])) == Decimal(
            "584640.00"
        )


@pytest.mark.asyncio
async def test_replay_does_not_duplicate_revision_or_financial_amount(database):
    async with AsyncSession(database) as session:
        plan = await preview(session)
        await apply(session, plan)
        await session.commit()
        await apply(session, plan)
        await session.commit()
        assert (
            await scalar(
                session, "SELECT count(*) FROM budget_tournament_weekly_revisions"
            )
            == 1
        )
        assert (
            await scalar(session, "SELECT count(*) FROM budget_tournament_weekly_lines")
            == 2
        )
        assert await scalar(
            session, "SELECT sum(amount) FROM budget_tournament_weekly_lines"
        ) == Decimal("584640.00")
        assert await scalar(
            session,
            "SELECT sum(budget_amount) FROM budget_lines WHERE tournament_id=CAST(:id AS uuid) AND line_direction='expense'",
            id=LIGA,
        ) == Decimal("584640.00")
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM budget_version_audit_log WHERE event_type='tournament_weekly_distribution_applied'",
            )
            == 1
        )
        assert (
            str(
                await scalar(
                    session,
                    "SELECT actor_empleado_id FROM budget_version_audit_log WHERE event_type='tournament_weekly_distribution_applied'",
                )
            )
            == ACTOR
        )


@pytest.mark.asyncio
async def test_changed_income_in_legacy_plan_fails_closed_but_income_survives(database):
    async with AsyncSession(database) as session:
        await apply(session, await preview(session))
        await session.commit()
        await session.execute(
            text(
                "UPDATE budget_line_monthly_plan SET expected_income_amount=3 WHERE budget_line_id=CAST(:id AS uuid)"
            ),
            {"id": LIGA_ZERO},
        )
        await session.commit()
        raw = await service.list_monthly_plan_for_lines(
            session, line_ids=[LIGA_COST, LIGA_ZERO], ensure_schema=False
        )
        plans, conflicts = await overlay.effective_plans_for_lines(
            session,
            version_id=VERSION,
            line_ids=[LIGA_COST, LIGA_ZERO],
            baseline_plans=raw,
        )
        assert LIGA in conflicts
        assert plans[LIGA_ZERO][4]["expected_income_amount"] == 3.0
        assert plans[LIGA_COST][40]["budget_expense_amount"] == 0.0


@pytest.mark.asyncio
async def test_stale_preview_cannot_overwrite_a_newer_revision(database):
    async with AsyncSession(database) as session:
        older = await preview(session, week=40)
        newer = await preview(session, week=41)
        await apply(session, newer)
        await session.commit()
        with pytest.raises((ValueError, PermissionError)):
            await apply(session, older)
        await session.rollback()
        assert (
            await scalar(
                session,
                "SELECT week_number FROM budget_tournament_weekly_revisions WHERE is_current",
            )
            == 41
        )
        assert (
            await scalar(
                session, "SELECT count(*) FROM budget_tournament_weekly_revisions"
            )
            == 1
        )


@pytest.mark.asyncio
async def test_simultaneous_same_preview_is_idempotent(database):
    async with AsyncSession(database) as session:
        plan = await preview(session)

    async def runner():
        async with AsyncSession(database) as session:
            response = await apply(session, plan)
            await session.commit()
            return response

    first, second = await asyncio.gather(runner(), runner())
    assert first["revision_id"] == second["revision_id"]
    assert sorted((first["idempotent"], second["idempotent"])) == [False, True]
    async with AsyncSession(database) as session:
        assert (
            await scalar(
                session, "SELECT count(*) FROM budget_tournament_weekly_revisions"
            )
            == 1
        )


@pytest.mark.asyncio
async def test_audited_deactivation_restores_raw_plan_without_deleting_history(
    database,
):
    async with AsyncSession(database) as session:
        before = await service.list_monthly_plan_for_lines(
            session, line_ids=[LIGA_COST, LIGA_ZERO], ensure_schema=False
        )
        applied = await apply(session, await preview(session))
        await session.commit()
        rollback = await overlay.deactivate_tournament_overlay(
            session,
            version_id=VERSION,
            tournament_id=LIGA,
            expected_revision_id=applied["revision_id"],
            expected_plan_hash=applied["plan_hash"],
            actor_empleado_id=ACTOR,
        )
        assert rollback["idempotent"] is False
        await session.commit()
        baseline = await service.list_monthly_plan_for_lines(
            session, line_ids=[LIGA_COST, LIGA_ZERO], ensure_schema=False
        )
        plans, conflicts = await overlay.effective_plans_for_lines(
            session,
            version_id=VERSION,
            line_ids=[LIGA_COST, LIGA_ZERO],
            baseline_plans=baseline,
        )
        assert not conflicts and plans == before
        assert (
            await scalar(
                session, "SELECT count(*) FROM budget_tournament_weekly_revisions"
            )
            == 1
        )
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM budget_tournament_weekly_revisions WHERE is_current",
            )
            == 0
        )
        assert (
            await scalar(session, "SELECT count(*) FROM budget_tournament_weekly_lines")
            == 2
        )
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM budget_version_audit_log WHERE event_type='tournament_weekly_distribution_deactivated'",
            )
            == 1
        )
        # The exact rollback receipt may be replayed safely.
        replay = await overlay.deactivate_tournament_overlay(
            session,
            version_id=VERSION,
            tournament_id=LIGA,
            expected_revision_id=applied["revision_id"],
            expected_plan_hash=applied["plan_hash"],
            actor_empleado_id=ACTOR,
        )
        await session.commit()
        assert replay["idempotent"] is True
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM budget_version_audit_log WHERE event_type='tournament_weekly_distribution_deactivated'",
            )
            == 1
        )


@pytest.mark.asyncio
async def test_rollback_restores_immediately_prior_revision_and_detects_stale_fingerprint(
    database,
):
    async with AsyncSession(database) as session:
        first = await apply(session, await preview(session, week=40))
        await session.commit()
        second = await apply(session, await preview(session, week=41))
        await session.commit()
        # A rollback must not pretend the older snapshot still matches changed finance.
        await session.execute(
            text(
                "UPDATE budget_lines SET budget_amount=584641 WHERE id=CAST(:id AS uuid)"
            ),
            {"id": LIGA_COST},
        )
        await session.commit()
        await overlay.deactivate_tournament_overlay(
            session,
            version_id=VERSION,
            tournament_id=LIGA,
            expected_revision_id=second["revision_id"],
            expected_plan_hash=second["plan_hash"],
            actor_empleado_id=ACTOR,
        )
        await session.commit()
        assert (
            str(
                await scalar(
                    session,
                    "SELECT id FROM budget_tournament_weekly_revisions WHERE is_current",
                )
            )
            == first["revision_id"]
        )
        raw = await service.list_monthly_plan_for_lines(
            session, line_ids=[LIGA_COST, LIGA_ZERO], ensure_schema=False
        )
        plans, conflicts = await overlay.effective_plans_for_lines(
            session,
            version_id=VERSION,
            line_ids=[LIGA_COST, LIGA_ZERO],
            baseline_plans=raw,
        )
        assert (
            LIGA in conflicts and plans[LIGA_COST][40]["expected_income_amount"] == 11.0
        )
        assert (
            await scalar(
                session, "SELECT count(*) FROM budget_tournament_weekly_revisions"
            )
            == 2
        )
        assert (
            await scalar(session, "SELECT count(*) FROM budget_tournament_weekly_lines")
            == 4
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["other_revision", "bad_hash", "denied", "inactive"])
async def test_rollback_rejects_stale_receipt_or_unauthorized_actor(database, case):
    async with AsyncSession(database) as session:
        first = await apply(session, await preview(session, week=40))
        await session.commit()
        current = first
        if case == "other_revision":
            current = await apply(session, await preview(session, week=41))
            await session.commit()
        supplied_hash = "0" * 64 if case == "bad_hash" else first["plan_hash"]
        actor = {"denied": DENIED, "inactive": INACTIVE}.get(case, ACTOR)
        with pytest.raises((ValueError, PermissionError)):
            await overlay.deactivate_tournament_overlay(
                session,
                version_id=VERSION,
                tournament_id=LIGA,
                expected_revision_id=first["revision_id"],
                expected_plan_hash=supplied_hash,
                actor_empleado_id=actor,
            )
        await session.rollback()
        assert (
            str(
                await scalar(
                    session,
                    "SELECT id FROM budget_tournament_weekly_revisions WHERE is_current",
                )
            )
            == current["revision_id"]
        )
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM budget_version_audit_log WHERE event_type='tournament_weekly_distribution_deactivated'",
            )
            == 0
        )


@pytest.mark.asyncio
async def test_copa_financial_reforecast_can_change_without_invalidating_liga(
    database, monkeypatch
):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(service, "ensure_budget_schema", AsyncMock())
    async with AsyncSession(database) as session:
        await apply(session, await preview(session))
        await session.commit()
        await service.transition_budget_version(
            session,
            version_id=VERSION,
            new_status="reforecast",
            actor_empleado_id=ACTOR,
        )
        await session.execute(
            text(
                "UPDATE budget_lines SET budget_amount=123 WHERE id=CAST(:id AS uuid)"
            ),
            {"id": COPA_UNKNOWN},
        )
        await session.commit()
        # A later valid approval can be selected without changing Liga's source lines.
        await service.transition_budget_version(
            session, version_id=VERSION, new_status="submitted", actor_empleado_id=ACTOR
        )
        await service.transition_budget_version(
            session, version_id=VERSION, new_status="approved", actor_empleado_id=ACTOR
        )
        raw = await service.list_monthly_plan_for_lines(
            session, line_ids=[LIGA_COST, LIGA_ZERO], ensure_schema=False
        )
        plans, conflicts = await overlay.effective_plans_for_lines(
            session,
            version_id=VERSION,
            line_ids=[LIGA_COST, LIGA_ZERO],
            baseline_plans=raw,
        )
        assert not conflicts and Decimal(
            str(plans[LIGA_COST][40]["budget_expense_amount"])
        ) == Decimal("584640.00")


@pytest.mark.asyncio
async def test_new_financial_version_without_lineage_never_inherits_by_name(database):
    successor = "10000000-0000-4000-8000-000000000010"
    new_line = "20000000-0000-4000-8000-000000000010"
    async with AsyncSession(database) as session:
        await apply(session, await preview(session))
        await session.commit()
        await session.execute(
            text(
                "UPDATE budget_versions SET status='closed' WHERE id=CAST(:id AS uuid)"
            ),
            {"id": VERSION},
        )
        await session.execute(
            text(
                "INSERT INTO budget_versions(id,edition_year,version_name,status,source) VALUES(CAST(:id AS uuid),2026,'Successor','approved','ui_manual')"
            ),
            {"id": successor},
        )
        await session.execute(
            text(
                """INSERT INTO budget_lines
          (id,budget_version_id,tournament_id,tournament_name,phase,concept_name,line_direction,account_code_final,budget_amount)
          SELECT CAST(:new AS uuid),CAST(:version AS uuid),tournament_id,tournament_name,phase,concept_name,line_direction,account_code_final,budget_amount
          FROM budget_lines WHERE id=CAST(:old AS uuid)"""
            ),
            {"new": new_line, "version": successor, "old": LIGA_COST},
        )
        await session.commit()
        plans, conflicts = await overlay.effective_plans_for_lines(
            session, version_id=successor, line_ids=[new_line], baseline_plans={}
        )
        assert LIGA in conflicts
        assert new_line not in plans


@pytest.mark.asyncio
async def test_independent_version_cannot_spoof_financial_lineage_via_metadata(
    database,
):
    """Copied metadata is not proof of a lineage from the authorized root."""
    successor = "10000000-0000-4000-8000-000000000010"
    pairs = [
        (LIGA_COST, "20000000-0000-4000-8000-000000000010"),
        (LIGA_ZERO, "20000000-0000-4000-8000-000000000011"),
    ]
    async with AsyncSession(database) as session:
        await apply(session, await preview(session))
        await session.commit()
        await session.execute(
            text(
                "UPDATE budget_versions SET status='closed' WHERE id=CAST(:id AS uuid)"
            ),
            {"id": VERSION},
        )
        await session.execute(
            text(
                """INSERT INTO budget_versions(id,edition_year,version_name,status,source)
                 VALUES(CAST(:id AS uuid),2026,'Independent','approved','ui_manual')"""
            ),
            {"id": successor},
        )
        for original, clone in pairs:
            await session.execute(
                text(
                    """INSERT INTO budget_lines
                  (id,budget_version_id,tournament_id,tournament_name,phase,concept_name,
                   line_direction,account_code_final,budget_amount,metadata)
                  SELECT CAST(:new AS uuid),CAST(:version AS uuid),tournament_id,tournament_name,
                   phase,concept_name,line_direction,account_code_final,budget_amount,
                   jsonb_build_object('distribution_financial_line_id',CAST(id AS text),
                                      'distribution_financial_version_id',CAST(:source AS text))
                  FROM budget_lines WHERE id=CAST(:old AS uuid)"""
                ),
                {
                    "new": clone,
                    "version": successor,
                    "source": VERSION,
                    "old": original,
                },
            )
            await session.execute(
                text(
                    """INSERT INTO budget_line_monthly_plan
                  (id,budget_line_id,month_number,budget_expense_amount,expected_income_amount,metadata)
                  SELECT gen_random_uuid(),CAST(:new AS uuid),month_number,
                         budget_expense_amount,expected_income_amount,metadata
                  FROM budget_line_monthly_plan WHERE budget_line_id=CAST(:old AS uuid)"""
                ),
                {"new": clone, "old": original},
            )
        await session.commit()
        clones = [clone for _, clone in pairs]
        raw = await service.list_monthly_plan_for_lines(
            session, line_ids=clones, ensure_schema=False
        )
        plans, conflicts = await overlay.effective_plans_for_lines(
            session, version_id=successor, line_ids=clones, baseline_plans=raw
        )
        assert LIGA in conflicts
        # Expense values cannot be inherited; genuine raw income is retained.
        assert plans[pairs[0][1]][40]["budget_expense_amount"] == 0.0
        assert plans[pairs[0][1]][40]["expected_income_amount"] == 11.0
        assert plans[pairs[1][1]][4]["expected_income_amount"] == 2.0


@pytest.mark.asyncio
async def test_weekly_values_agree_in_direction_budget_matrix_and_cashflow(
    database, monkeypatch
):
    from datetime import date
    from unittest.mock import AsyncMock
    from devnous.gastos.routes.admin_budget_ui import _sum_budget_line_periods
    from samchat.cashflow import service as cashflow
    from samchat.client_executive.home_ui import render_budget_distribution

    monkeypatch.setattr(service, "ensure_budget_schema", AsyncMock())

    liga = [
        {
            "id": LIGA_COST,
            "concept_name": "Liga material",
            "budget_amount": "584640.00",
            "line_direction": "expense",
        },
        {
            "id": LIGA_ZERO,
            "concept_name": "Liga zero",
            "budget_amount": "0.00",
            "line_direction": "expense",
        },
    ]
    homeless = [
        {
            "id": HOMELESS_COST,
            "concept_name": "Homeless transport",
            "budget_amount": "99.91",
            "line_direction": "expense",
        }
    ]
    income = {
        "id": LIGA_INCOME,
        "concept_name": "Liga income",
        "budget_amount": "50.00",
        "line_direction": "income",
    }
    async with AsyncSession(database) as session:
        await apply(session, await preview(session, LIGA, 40))
        await apply(session, await preview(session, HOMELESS, 52))
        await session.commit()
        line_ids = [r["id"] for r in liga + homeless + [income]]
        baseline = await service.list_monthly_plan_for_lines(
            session, line_ids=line_ids, ensure_schema=False
        )
        effective, conflicts = await overlay.effective_plans_for_lines(
            session, version_id=VERSION, line_ids=line_ids, baseline_plans=baseline
        )
        assert not conflicts
        liga_projection = distribution.period_projection(
            liga, effective, 2026, date(2026, 9, 30)
        )
        homeless_projection = distribution.period_projection(
            homeless, effective, 2026, date(2026, 12, 31)
        )
        assert (
            liga_projection["status"] == "complete"
            and homeless_projection["status"] == "complete"
        )
        assert sum(
            Decimal(r["budget_month"]) for r in liga_projection["rows"]
        ) == Decimal("584640.00")
        assert Decimal(homeless_projection["rows"][0]["budget_month"]) == Decimal(
            "99.91"
        )
        html = render_budget_distribution(
            [
                {"name": "Liga", "budget_distribution": liga_projection},
                {"name": "Homeless", "budget_distribution": homeless_projection},
            ]
        )
        assert "584,640.00" in html and "99.91" in html and "Conciliada" in html
        matrix = _sum_budget_line_periods(
            liga + homeless,
            plan_map=effective,
            actuals_map={},
            edition_year=2026,
            period="weekly",
            budget_view="expenses",
        )
        assert matrix[39]["budget"] == 584640.0 and matrix[51]["budget"] == 99.91
        assert matrix[39]["plan_known"] and matrix[51]["plan_known"]
        monkeypatch.setattr(
            cashflow,
            "list_budget_lines",
            AsyncMock(return_value=liga + homeless + [income]),
        )
        buckets = {
            f"2026-{m:02d}": {"planned_budget_income": 0, "planned_budget_expense": 0}
            for m in (1, 9, 12)
        }
        notes = await cashflow._apply_budget_plan(
            session,
            budget_version_id=VERSION,
            buckets_by_key=buckets,
            budget_year=2026,
            limit=5000,
        )
        assert not notes
        assert buckets["2026-09"]["planned_budget_expense"] == 584640.0
        assert buckets["2026-12"]["planned_budget_expense"] == 99.91
        assert buckets["2026-01"]["planned_budget_income"] == 50.0


@pytest.mark.asyncio
async def test_cashflow_marks_conflicting_expense_unavailable_but_keeps_income(
    database, monkeypatch
):
    from unittest.mock import AsyncMock
    from samchat.cashflow import service as cashflow

    monkeypatch.setattr(service, "ensure_budget_schema", AsyncMock())
    lines = [
        {"id": LIGA_COST, "line_direction": "expense"},
        {"id": LIGA_ZERO, "line_direction": "expense"},
        {"id": LIGA_INCOME, "line_direction": "income"},
    ]
    async with AsyncSession(database) as session:
        await apply(session, await preview(session))
        await session.commit()
        await session.execute(
            text("UPDATE budget_lines SET phase='Cambiada' WHERE id=CAST(:id AS uuid)"),
            {"id": LIGA_ZERO},
        )
        await session.commit()
        monkeypatch.setattr(
            cashflow, "list_budget_lines", AsyncMock(return_value=lines)
        )
        buckets = {
            f"2026-{m:02d}": {"planned_budget_income": 0, "planned_budget_expense": 0}
            for m in (1, 9, 12)
        }
        notes = await cashflow._apply_budget_plan(
            session,
            budget_version_id=VERSION,
            buckets_by_key=buckets,
            budget_year=2026,
            limit=5000,
        )
        assert notes == ["budget_distribution_requires_reconciliation"]
        assert all(b["planned_budget_expense"] is None for b in buckets.values())
        assert buckets["2026-01"]["planned_budget_income"] == 50.0


@pytest.mark.asyncio
async def test_cashflow_overlay_failure_does_not_present_expense_as_zero(monkeypatch):
    from unittest.mock import AsyncMock
    from samchat.cashflow import service as cashflow

    lines = [
        {"id": LIGA_COST, "line_direction": "expense"},
        {"id": LIGA_INCOME, "line_direction": "income"},
    ]
    baseline = {
        LIGA_INCOME: {4: {"budget_expense_amount": 0, "expected_income_amount": 50}}
    }
    monkeypatch.setattr(cashflow, "list_budget_lines", AsyncMock(return_value=lines))
    monkeypatch.setattr(cashflow, "_budget_edition_year", AsyncMock(return_value=2026))
    monkeypatch.setattr(
        cashflow, "list_monthly_plan_for_lines", AsyncMock(return_value=baseline)
    )
    monkeypatch.setattr(
        overlay,
        "effective_plans_for_lines",
        AsyncMock(side_effect=ValueError("invalid overlay")),
    )
    buckets = {"2026-01": {"planned_budget_income": 0, "planned_budget_expense": 0}}
    notes = await cashflow._apply_budget_plan(
        object(),
        budget_version_id=VERSION,
        buckets_by_key=buckets,
        budget_year=2026,
        limit=5000,
    )
    assert notes == ["budget_plan_unavailable:ValueError"]
    assert buckets["2026-01"]["planned_budget_expense"] is None
    assert buckets["2026-01"]["planned_budget_income"] == 50.0


@pytest.mark.asyncio
async def test_cashflow_missing_version_marks_both_plans_unavailable(database):
    from samchat.cashflow import service as cashflow

    buckets = {"2026-01": {"planned_budget_income": 0, "planned_budget_expense": 0}}
    async with AsyncSession(database) as session:
        notes = await cashflow._apply_budget_plan(
            session,
            budget_version_id="10000000-0000-4000-8000-000000000999",
            buckets_by_key=buckets,
            budget_year=2026,
            limit=5000,
        )
    assert notes == ["budget_plan_year_missing"]
    assert buckets["2026-01"]["planned_budget_income"] is None
    assert buckets["2026-01"]["planned_budget_expense"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("horizon_year", [None, 2027])
async def test_cashflow_uses_budget_version_edition_instead_of_horizon(
    database, monkeypatch, horizon_year
):
    """Week 23 starts in June 2026, but in May 2027."""
    from unittest.mock import AsyncMock
    from samchat.cashflow import service as cashflow

    lines = [
        {"id": LIGA_COST, "line_direction": "expense"},
        {"id": LIGA_ZERO, "line_direction": "expense"},
        {"id": LIGA_INCOME, "line_direction": "income"},
    ]
    monkeypatch.setattr(service, "ensure_budget_schema", AsyncMock())
    monkeypatch.setattr(cashflow, "list_budget_lines", AsyncMock(return_value=lines))
    async with AsyncSession(database) as session:
        await apply(session, await preview(session, week=23))
        await session.commit()
        buckets = {
            month: {"planned_budget_income": 0, "planned_budget_expense": 0}
            for month in ("2026-01", "2026-06", "2027-05")
        }
        notes = await cashflow._apply_budget_plan(
            session,
            budget_version_id=VERSION,
            buckets_by_key=buckets,
            budget_year=horizon_year,
            limit=5000,
        )
        assert not notes
        assert buckets["2026-06"]["planned_budget_expense"] == 584640.0
        assert buckets["2027-05"]["planned_budget_expense"] == 0.0
        assert buckets["2026-01"]["planned_budget_income"] == 50.0


@pytest.mark.asyncio
async def test_direction_reads_the_overlay_and_marks_zero_with_income_as_conflicted(
    database, monkeypatch
):
    from datetime import date
    from unittest.mock import AsyncMock
    from samchat.client_executive.home_ui import render_budget_distribution

    class Aggregate:
        def mappings(self):
            return self

        def first(self):
            return {"shared_count": 0, "currency_count": 0, "missing_amount_count": 0}

    class SessionProxy:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        async def execute(self, statement, params=None):
            sql = str(statement)
            if "shared_count" in sql or (
                "missing_amount_count" in sql and "FROM documentos d" in sql
            ):
                return Aggregate()
            return await self.wrapped.execute(statement, params)

    monkeypatch.setattr(
        service, "_build_budget_scope_filters", lambda **kw: (["TRUE"], ["TRUE"], {})
    )
    monkeypatch.setattr(
        service,
        "_build_budget_finance_breakdowns",
        AsyncMock(return_value={"by_concept": [], "by_provider": []}),
    )
    monkeypatch.setattr(
        service,
        "_build_budget_finance_comparison",
        AsyncMock(
            return_value={
                "requested_total": 0,
                "committed_total": 0,
                "paid_total": 0,
                "actual_total": 0,
                "pending_to_pay_total": 0,
                "overdue_open_total": 0,
                "due_next_30_total": 0,
            }
        ),
    )
    monkeypatch.setattr(
        service, "build_executive_monthly_actuals", AsyncMock(return_value={})
    )
    async with AsyncSession(database) as session:
        await apply(session, await preview(session))
        await session.commit()
        proxy = SessionProxy(session)

        async def snapshot():
            return await service.build_budget_snapshot(
                proxy,
                tournament_id=LIGA,
                edition_year=2026,
                version_id=VERSION,
                ensure_schema=False,
                strict_tournament_scope=True,
                executive_read=True,
                date_to=date(2026, 9, 30),
            )

        before = await snapshot()
        assert before["budget_distribution"]["status"] == "complete"
        before_zero = next(
            r
            for r in before["budget_distribution"]["rows"]
            if r["line_id"] == LIGA_ZERO
        )
        assert (
            before_zero["status"] == "complete"
            and Decimal(before_zero["budget_month"]) == 0
        )
        await session.execute(
            text("UPDATE budget_lines SET phase='Cambiada' WHERE id=CAST(:id AS uuid)"),
            {"id": LIGA_ZERO},
        )
        await session.commit()
        after = await snapshot()
        zero = next(
            r for r in after["budget_distribution"]["rows"] if r["line_id"] == LIGA_ZERO
        )
        assert (
            zero["status"] == "conflict"
            and zero["budget_month"] is None
            and zero["monthly"] is None
        )
        assert after["budget_distribution"]["status"] == "pending"
        html = render_budget_distribution(
            [{"name": "Liga", "budget_distribution": after["budget_distribution"]}]
        )
        assert "Distribución requiere conciliación" in html


@pytest.mark.asyncio
async def test_admin_tournament_route_uses_effective_plan_and_suppresses_conflicts(
    database, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from fastapi import APIRouter
    from devnous.gastos.routes import admin_budget_routes as routes

    class EmptyResult:
        def scalar_one_or_none(self):
            return None

        def scalars(self):
            return self

        def all(self):
            return []

    class SessionProxy:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        async def execute(self, statement, params=None):
            entities = getattr(statement, "column_descriptions", None)
            if entities and entities[0].get("entity") in (
                routes.Tournament,
                routes.CuentaContable,
            ):
                return EmptyResult()
            return await self.wrapped.execute(statement, params)

    expense = [
        {
            "id": LIGA_COST,
            "tournament_id": LIGA,
            "concept_name": "Material",
            "budget_amount": 584640.0,
            "phase": "Fase",
            "line_direction": "expense",
        },
        {
            "id": LIGA_ZERO,
            "tournament_id": LIGA,
            "concept_name": "Zero",
            "budget_amount": 0.0,
            "phase": "Fase",
            "line_direction": "expense",
        },
    ]
    income = [
        {
            "id": LIGA_INCOME,
            "tournament_id": LIGA,
            "concept_name": "Ingreso",
            "budget_amount": 50.0,
            "phase": "Fase",
            "line_direction": "income",
        }
    ]
    version = {
        "id": VERSION,
        "edition_year": 2026,
        "status": "approved",
        "source": "ui_manual",
        "version_name": "Annual approved",
    }
    monkeypatch.setattr(routes, "ensure_budget_schema", AsyncMock())
    monkeypatch.setattr(
        routes,
        "resolve_budget_tournament_context",
        AsyncMock(
            return_value={
                "tournament_id": LIGA,
                "tournament_code": "LIGA",
                "tournament_name": "Liga",
            }
        ),
    )
    monkeypatch.setattr(
        routes, "list_budget_versions", AsyncMock(return_value=[version])
    )
    monkeypatch.setattr(
        routes,
        "list_budget_lines",
        AsyncMock(
            side_effect=lambda session, **kw: (
                expense if kw["line_direction"] == "expense" else income
            )
        ),
    )
    monkeypatch.setattr(
        routes,
        "attach_cuenta_contable_to_budget_lines",
        AsyncMock(side_effect=lambda session, lines: lines),
    )
    monkeypatch.setattr(
        routes,
        "build_budget_actuals_snapshot",
        AsyncMock(return_value={"monthly": {}, "movements": []}),
    )
    monkeypatch.setattr(
        routes,
        "build_budget_monthly_plan_rollups",
        AsyncMock(
            return_value={
                "budget_expense_total": 584640.0,
                "expected_income_total": 50.0,
            }
        ),
    )
    matrix = Mock(
        side_effect=lambda lines, **kw: "<div>" + kw["budget_view"] + "</div>"
    )
    monkeypatch.setattr(routes, "render_budget_partida_matrix", matrix)
    monkeypatch.setattr(
        routes, "render_budget_executive_dashboard", lambda *args, **kw: ""
    )
    router = APIRouter()
    routes.register_presupuestos_routes(router)
    endpoint = next(
        r.endpoint
        for r in router.routes
        if r.path == "/admin/presupuestos/torneo/{tournament_key}"
        and "GET" in r.methods
    )
    user = SimpleNamespace(
        id=ACTOR,
        rol="superadmin",
        activo=True,
        nombre="Ficticio",
        departamento=None,
        correo=None,
    )
    async with AsyncSession(database) as session:
        await apply(session, await preview(session))
        await session.commit()

        async def invoke():
            return await endpoint(
                tournament_key=LIGA,
                session=SessionProxy(session),
                current_empleado=user,
                edition_year=2026,
                version_id=VERSION,
                budget_view="expenses",
                phase_filter=None,
                show_committed=1,
                show_yoy=0,
                budget_period="weekly",
                success_msg=None,
                error_msg=None,
            )

        # The raw weekly reader normally ensures schema; the fixture has it.
        monkeypatch.setattr(service, "ensure_budget_schema", AsyncMock())
        response = await invoke()
        assert response.status_code == 200
        expense_map = matrix.call_args_list[0].kwargs["plan_map"]
        assert expense_map[LIGA_COST][40]["budget_expense_amount"] == 584640.0
        assert expense_map[LIGA_ZERO][40]["budget_expense_amount"] == 0.0
        assert "requiere conciliación" not in response.body.decode()
        await session.execute(
            text("UPDATE budget_lines SET phase='Cambiada' WHERE id=CAST(:id AS uuid)"),
            {"id": LIGA_ZERO},
        )
        await session.commit()
        matrix.reset_mock()
        response = await invoke()
        assert "distribución requiere conciliación" in response.body.decode()
        assert LIGA_COST not in matrix.call_args_list[0].kwargs["plan_map"]
        assert LIGA_ZERO not in matrix.call_args_list[0].kwargs["plan_map"]
        assert (
            matrix.call_args_list[1].kwargs["plan_map"][LIGA_INCOME][4][
                "expected_income_amount"
            ]
            == 50.0
        )


@pytest.mark.asyncio
async def test_schema_absence_preserves_baseline_and_blocks_writes(database):
    async with AsyncSession(database) as session:
        await session.execute(text("DROP TABLE budget_tournament_weekly_lines"))
        await session.execute(text("DROP TABLE budget_tournament_weekly_revisions"))
        await session.commit()
        baseline = await service.list_monthly_plan_for_lines(
            session, line_ids=[LIGA_COST, LIGA_ZERO], ensure_schema=False
        )
        plans, conflicts = await overlay.effective_plans_for_lines(
            session,
            version_id=VERSION,
            line_ids=[LIGA_COST, LIGA_ZERO],
            baseline_plans=baseline,
        )
        assert plans == baseline and not conflicts
        candidate = await preview(session)
        assert candidate["schema_ready"] is False
        with pytest.raises((ValueError, RuntimeError)):
            await apply(session, candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt_lineage", [False, True])
async def test_canonical_distribution_clone_requires_real_lineage(
    database, corrupt_lineage
):
    """A genuine successor remains valid; a forged line is a conflict."""
    successor = "10000000-0000-4000-8000-000000000010"
    pairs = [
        (LIGA_COST, "20000000-0000-4000-8000-000000000010"),
        (LIGA_ZERO, "20000000-0000-4000-8000-000000000011"),
        (LIGA_INCOME, "20000000-0000-4000-8000-000000000012"),
        (HOMELESS_COST, "20000000-0000-4000-8000-000000000013"),
        (COPA_UNKNOWN, "20000000-0000-4000-8000-000000000014"),
    ]
    async with AsyncSession(database) as session:
        await apply(session, await preview(session))
        await session.commit()
        await session.execute(
            text(
                """INSERT INTO budget_versions
          (id,edition_year,version_name,status,source) VALUES
          (CAST(:id AS uuid),2026,'Validated successor','approved','distribution_revision')"""
            ),
            {"id": successor},
        )
        await session.execute(
            text(
                """INSERT INTO budget_version_audit_log
          (id,budget_version_id,event_type,payload) VALUES(gen_random_uuid(),CAST(:id AS uuid),
          'distribution_revision_created',jsonb_build_object('source_version_id',CAST(:source AS text)))"""
            ),
            {"id": successor, "source": VERSION},
        )
        for original, clone in pairs:
            fake_root = COPA if corrupt_lineage and original == LIGA_ZERO else original
            await session.execute(
                text(
                    """INSERT INTO budget_lines
              (id,budget_version_id,tournament_id,tournament_code,tournament_name,sport,phase,
               entity_name,concept_name,budget_concept_id,line_direction,account_code_suggested,
               account_code_final,budget_amount,reference_amount,variance_amount,priority,
               owner_name,criteria_note,observations,metadata)
              SELECT CAST(:new AS uuid),CAST(:version AS uuid),tournament_id,tournament_code,
               tournament_name,sport,phase,entity_name,concept_name,budget_concept_id,line_direction,
               account_code_suggested,account_code_final,budget_amount,reference_amount,variance_amount,
               priority,owner_name,criteria_note,observations,
               metadata || jsonb_build_object('distribution_source_line_id',CAST(id AS text),
                 'distribution_financial_version_id',CAST(:source AS text),
                 'distribution_financial_line_id',CAST(:root_line AS text))
              FROM budget_lines WHERE id=CAST(:old AS uuid)"""
                ),
                {
                    "new": clone,
                    "version": successor,
                    "source": VERSION,
                    "root_line": fake_root,
                    "old": original,
                },
            )
            await session.execute(
                text(
                    """INSERT INTO budget_line_monthly_plan
              (id,budget_line_id,month_number,budget_expense_amount,expected_income_amount,metadata)
              SELECT gen_random_uuid(),CAST(:new AS uuid),month_number,budget_expense_amount,
                expected_income_amount,metadata FROM budget_line_monthly_plan WHERE budget_line_id=CAST(:old AS uuid)"""
                ),
                {"new": clone, "old": original},
            )
        await session.commit()
        target = [clone for _, clone in pairs[:2]]
        raw = await service.list_monthly_plan_for_lines(
            session, line_ids=target, ensure_schema=False
        )
        plans, conflicts = await overlay.effective_plans_for_lines(
            session, version_id=successor, line_ids=target, baseline_plans=raw
        )
        if corrupt_lineage:
            assert (
                LIGA in conflicts
                and plans[target[0]][40]["budget_expense_amount"] == 0.0
            )
        else:
            assert not conflicts
            assert plans[target[0]][40]["budget_expense_amount"] == 584640.0
            assert plans[target[1]][40]["budget_expense_amount"] == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", [None, "amount", "account", "root"])
async def test_reapproval_validates_current_overlay_over_zero_legacy(database, tamper):
    async with AsyncSession(database) as session:
        await session.execute(
            text(
                "UPDATE budget_line_monthly_plan SET budget_expense_amount=0 WHERE budget_line_id=CAST(:id AS uuid)"
            ),
            {"id": LIGA_COST},
        )
        await apply(session, await preview(session))
        await session.commit()
        await service.transition_budget_version(
            session,
            version_id=VERSION,
            new_status="reforecast",
            actor_empleado_id=ACTOR,
            ensure_schema=False,
        )
        if tamper == "amount":
            await session.execute(
                text(
                    "UPDATE budget_lines SET budget_amount=budget_amount+1 WHERE id=CAST(:id AS uuid)"
                ),
                {"id": LIGA_COST},
            )
        elif tamper == "account":
            await session.execute(
                text(
                    "UPDATE budget_lines SET account_code_final='unexpected' WHERE id=CAST(:id AS uuid)"
                ),
                {"id": LIGA_COST},
            )
        elif tamper == "root":
            await session.execute(
                text(
                    "INSERT INTO budget_versions(id,edition_year,version_name,status,source) VALUES ('10000000-0000-4000-8000-000000000999',2025,'Unrelated','closed','ui_manual')"
                )
            )
            await session.execute(
                text(
                    "UPDATE budget_tournament_weekly_revisions SET financial_root_version_id=CAST(:id AS uuid) WHERE tournament_id=CAST(:tid AS uuid)"
                ),
                {"id": "10000000-0000-4000-8000-000000000999", "tid": LIGA},
            )
        if tamper:
            with pytest.raises(ValueError, match="conflictos"):
                await service.transition_budget_version(
                    session,
                    version_id=VERSION,
                    new_status="approved",
                    actor_empleado_id=ACTOR,
                    ensure_schema=False,
                )
        else:
            await service.transition_budget_version(
                session,
                version_id=VERSION,
                new_status="approved",
                actor_empleado_id=ACTOR,
                ensure_schema=False,
            )
            raw = await service.list_monthly_plan_for_lines(
                session, line_ids=[LIGA_COST], ensure_schema=False
            )
            assert all(v["budget_expense_amount"] == 0 for v in raw[LIGA_COST].values())
