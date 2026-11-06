"""Real owner commits stay contained in the HWC repair's outer transaction."""

import copy
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load(
    "hwc_repair_pg_fixture",
    Path(__file__).with_name("test_tournament_weekly_overlay_postgres.py"),
)
database = fixture.database
cli = load(
    "hwc_repair_pg_operator", ROOT / "scripts/repair_hwc_catalogue_subprojects.py"
)


async def capture(database):
    async with AsyncSession(database) as session:
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
            for table in (
                "tournaments",
                "budget_concepts",
                "budget_versions",
                "budget_lines",
                "budget_line_monthly_plan",
                "budget_line_monthly_allocations",
                "budget_tournament_weekly_revisions",
                "budget_tournament_weekly_lines",
                "budget_approvals",
                "budget_version_audit_log",
            )
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["apply", "scope_failure", "after_internal_commit", "approval_guard"]
)
async def test_real_scope_phase_and_weekly_owners_are_atomic(
    database, monkeypatch, mode
):
    monkeypatch.setattr(cli, "ACTOR", fixture.ACTOR)
    monkeypatch.setattr(cli, "VERSION", fixture.VERSION)
    monkeypatch.setattr(cli, "HWC", fixture.HOMELESS)
    monkeypatch.setattr(cli, "ZERO_LINE", fixture.COPA_UNKNOWN)
    cid = str(uuid4())
    async with database.begin() as conn:
        for sql in (
            "ALTER TABLE tournaments ADD COLUMN etapas jsonb, "
            "ADD COLUMN categorias jsonb",
            "CREATE TABLE cuentas_contables(id uuid PRIMARY KEY,codigo text,"
            "nombre text,activo boolean)",
            "ALTER TABLE budget_concepts ADD COLUMN tournament_id uuid, "
            "ADD COLUMN tournament_code text, ADD COLUMN tournament_name text, "
            "ADD COLUMN concept_key text, "
            "ADD COLUMN budget_direction text DEFAULT 'expense', "
            "ADD COLUMN active boolean DEFAULT true, "
            "ADD COLUMN source text DEFAULT 'admin_ui', "
            "ADD COLUMN metadata jsonb DEFAULT '{}', "
            "ADD COLUMN cuenta_contable_id uuid, "
            "ADD COLUMN pasivo_cuenta_contable_id uuid, "
            "ADD COLUMN created_by_empleado_id uuid, "
            "ADD COLUMN created_at timestamptz DEFAULT now(), "
            "ADD COLUMN updated_at timestamptz DEFAULT now()",
        ):
            await conn.execute(text(sql))
        await conn.execute(
            text(
                "UPDATE tournaments SET "
                'etapas=\'["Fase","Preoperativos"]\'::jsonb,'
                "categorias='[]'::jsonb"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO budget_concepts(id,concept_name,concept_key,"
                "tournament_id,tournament_name,metadata) VALUES("
                "CAST(:id AS uuid),'Zero concept','zero',CAST(:tid AS uuid),"
                '\'Homeless\',\'{"scope_mode":"global",'
                '"private_provenance":"kept"}\')'
            ),
            {"id": cid, "tid": fixture.HOMELESS},
        )
        await conn.execute(
            text(
                "UPDATE budget_lines SET tournament_id=CAST(:tid AS uuid),"
                "budget_concept_id=CAST(:cid AS uuid),"
                "variance_amount=budget_amount-reference_amount "
                "WHERE id=CAST(:id AS uuid)"
            ),
            {"tid": fixture.HOMELESS, "cid": cid, "id": fixture.COPA_UNKNOWN},
        )
        await conn.execute(
            text(
                "INSERT INTO budget_line_monthly_plan(id,budget_line_id,"
                "month_number,budget_expense_amount,expected_income_amount) "
                "VALUES(gen_random_uuid(),CAST(:id AS uuid),52,99.91,0)"
            ),
            {"id": fixture.HOMELESS_COST},
        )
        # Only the deliberately injected schedule mismatch fails approval.
        await conn.execute(
            text(
                "UPDATE budget_lines SET variance_amount=budget_amount-reference_amount"
            )
        )
    async with AsyncSession(database) as session:
        for tid, week in [(fixture.LIGA, 40), (fixture.HOMELESS, 52)]:
            preview = await cli.overlay.preview_tournament_overlay(
                session, version_id=fixture.VERSION, tournament_id=tid, week=week
            )
            await cli.overlay.apply_tournament_overlay(
                session,
                version_id=fixture.VERSION,
                tournament_id=tid,
                week=week,
                expected_plan_hash=preview["plan_hash"],
                actor_empleado_id=fixture.ACTOR,
            )
        if mode == "approval_guard":
            await session.execute(
                text(
                    "UPDATE budget_line_monthly_plan SET budget_expense_amount=1 "
                    "WHERE budget_line_id=CAST(:id AS uuid)"
                ),
                {"id": fixture.HOMELESS_COST},
            )
        await session.commit()
    before = await capture(database)
    data = {
        "scope_updates": [{"concept_id": cid, "scope_after": "Preoperativos"}],
        "zero_line_phase_updates": [{"phase_after": "Preoperativos"}],
        "source_sha256": "f" * 64,
        "historical_scope_gaps": {"documents": 0, "expenses": 0},
    }
    plan = {"completed": False, "before": copy.deepcopy(before)}

    async def execute():
        async with database.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                )
                async with AsyncSession(
                    bind=connection, join_transaction_mode="create_savepoint"
                ) as session:
                    result = await cli.apply_owners(
                        session,
                        plan,
                        data,
                        fail_after={
                            "scope_failure": 1,
                            "after_internal_commit": 77,
                        }.get(mode),
                    )
                    await session.commit()
                    return result

    if mode != "apply":
        error, message = (
            (ValueError, "no concilia")
            if mode == "approval_guard"
            else (RuntimeError, "Injected restored")
        )
        with pytest.raises(error, match=message):
            await execute()
        assert await capture(database) == before
        return
    result = await execute()
    after = await capture(database)
    assert result["idempotent"] is False
    concept = next(r for r in after["budget_concepts"] if r["id"] == cid)
    assert concept["metadata"]["private_provenance"] == "kept"
    assert concept["metadata"]["applicable_phase_labels"] == ["Preoperativos"]
    assert (
        next(r for r in after["budget_lines"] if r["id"] == fixture.COPA_UNKNOWN)[
            "phase"
        ]
        == "Preoperativos"
    )
    for table in ("budget_line_monthly_plan", "budget_line_monthly_allocations"):
        assert after[table] == before[table]
    assert after["tournaments"] == before["tournaments"]
    assert (
        len(after["budget_tournament_weekly_revisions"])
        == len(before["budget_tournament_weekly_revisions"]) + 1
    )
    liga_before = [
        r
        for r in before["budget_tournament_weekly_revisions"]
        if r["tournament_id"] == fixture.LIGA
    ]
    assert [
        r
        for r in after["budget_tournament_weekly_revisions"]
        if r["tournament_id"] == fixture.LIGA
    ] == liga_before
    assert (
        next(r for r in after["budget_versions"] if r["id"] == fixture.VERSION)[
            "status"
        ]
        == "approved"
    )
    async with AsyncSession(database) as session:
        line_ids = [
            fixture.LIGA_COST,
            fixture.LIGA_ZERO,
            fixture.LIGA_INCOME,
            fixture.HOMELESS_COST,
            fixture.COPA_UNKNOWN,
        ]
        raw = await cli.service.list_monthly_plan_for_lines(
            session, line_ids=line_ids, ensure_schema=False
        )
        effective, conflicts = await cli.overlay.effective_plans_for_lines(
            session, version_id=fixture.VERSION, line_ids=line_ids, baseline_plans=raw
        )
        assert not conflicts
        assert (
            str(effective[fixture.HOMELESS_COST][52]["budget_expense_amount"])
            == "99.91"
        )
        assert any(
            r["event_type"] == cli.EVENT for r in after["budget_version_audit_log"]
        )
