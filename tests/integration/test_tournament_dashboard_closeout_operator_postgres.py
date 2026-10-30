"""Real PostgreSQL containment of owner commits; disposable local/CI fixture only."""

from pathlib import Path
import importlib.util
from unittest.mock import AsyncMock

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
    "closeout_pg_fixture",
    Path(__file__).with_name("test_tournament_weekly_overlay_postgres.py"),
)
database = fixture.database
cli = load(
    "closeout_pg_operator", ROOT / "scripts/repair_tournament_dashboard_closeout.py"
)


async def financial_snapshot(database):
    async with AsyncSession(database) as session:
        return {
            table: await cli.rows(session, table)
            for table in (
                "budget_versions",
                "budget_lines",
                "budget_line_monthly_plan",
                "budget_tournament_weekly_revisions",
                "budget_tournament_weekly_lines",
                "budget_version_audit_log",
                "budget_approvals",
            )
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["apply", "fail_after_first_commit", "approval_guard"])
async def test_outer_transaction_contains_owner_commits_and_normal_approval(
    database, monkeypatch, mode
):
    monkeypatch.setattr(cli, "VERSION", fixture.VERSION)
    monkeypatch.setattr(cli, "ACTOR", fixture.ACTOR)
    monkeypatch.setattr(cli, "LIGA", fixture.LIGA)
    monkeypatch.setattr(cli, "HWC", fixture.HOMELESS)
    monkeypatch.setattr(cli, "WEEKS", {fixture.LIGA: 40, fixture.HOMELESS: 52})
    # This test exercises the account/overlay owners with a two-line fixture.
    # Exact sixteen real decision proofs are covered separately and in restore.
    monkeypatch.setattr(
        cli.service, "confirm_tournament_closeout_decisions", AsyncMock()
    )
    async with AsyncSession(database) as session:
        await session.execute(
            text(
                "UPDATE budget_lines SET variance_amount=budget_amount-reference_amount"
            )
        )
        if mode == "approval_guard":
            await session.execute(
                text(
                    "UPDATE budget_line_monthly_plan SET budget_expense_amount=1 WHERE budget_expense_amount>0"
                )
            )
        for tid, week in cli.WEEKS.items():
            plan = await cli.overlay.preview_tournament_overlay(
                session, version_id=fixture.VERSION, tournament_id=tid, week=week
            )
            await cli.overlay.apply_tournament_overlay(
                session,
                version_id=fixture.VERSION,
                tournament_id=tid,
                week=week,
                expected_plan_hash=plan["plan_hash"],
                actor_empleado_id=fixture.ACTOR,
            )
        await session.commit()
    before = await financial_snapshot(database)
    plan = {"completed": False, "before": before}
    data = {
        "accounts": [
            {"id": lid, "proposed_account": "5300-001-002", "tournament_id": tid}
            for lid, tid in [
                (fixture.LIGA_COST, fixture.LIGA),
                (fixture.HOMELESS_COST, fixture.HOMELESS),
            ]
        ],
        "movements": [],
    }

    async def execute():
        async with database.connect() as conn:
            async with conn.begin():
                await conn.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint"
                ) as session:
                    result = await cli.apply_owners(
                        session,
                        plan,
                        data,
                        fail_after=1 if mode == "fail_after_first_commit" else None,
                    )
                    await session.commit()
                    return result

    if mode == "apply":
        result = await execute()
        assert not result["idempotent"]
        after = await financial_snapshot(database)
        assert (
            next(r for r in after["budget_versions"] if r["id"] == fixture.VERSION)[
                "status"
            ]
            == "approved"
        )
        assert len(after["budget_tournament_weekly_revisions"]) == 4
        assert {
            r["tournament_id"]: r["week_number"]
            for r in after["budget_tournament_weekly_revisions"]
            if r["is_current"]
        } == cli.WEEKS
        for row in after["budget_lines"]:
            original = next(r for r in before["budget_lines"] if r["id"] == row["id"])
            assert row["budget_amount"] == original["budget_amount"]
            assert row["phase"] == original["phase"]
            assert row["budget_concept_id"] == original["budget_concept_id"]
    else:
        error, message = (
            (RuntimeError, "Injected restored")
            if mode == "fail_after_first_commit"
            else (ValueError, "no concilia")
        )
        with pytest.raises(error, match=message):
            await execute()
        assert await financial_snapshot(database) == before


@pytest.mark.asyncio
async def test_real_sixteen_confirmations_preserve_policies_and_reject_policy_drift(
    database, monkeypatch
):
    """Exercise persistent proof with real rows; only snapshot discovery is a seam."""
    from uuid import uuid4

    actions = cli.service._closeout_actions()
    actor = cli.service._CLOSEOUT_ACTOR
    movements = []
    async with database.begin() as conn:
        await conn.execute(
            text("ALTER TABLE budget_concepts ADD COLUMN active boolean DEFAULT true")
        )
        for sql in (
            "CREATE TABLE accounting_polizas(id uuid PRIMARY KEY,status text)",
            "CREATE TABLE accounting_poliza_lines(id uuid PRIMARY KEY,poliza_id uuid,debe numeric,haber numeric)",
            "CREATE TABLE documentos(id uuid PRIMARY KEY,estado text)",
            "CREATE TABLE expense_reports(id uuid PRIMARY KEY,monto numeric)",
            "CREATE TABLE budget_movement_assignments(id uuid PRIMARY KEY,budget_version_id uuid,budget_concept_id uuid,budget_line_id uuid,movement_key text,assigned_by_empleado_id uuid,created_at timestamptz,updated_at timestamptz,UNIQUE(budget_version_id,movement_key))",
        ):
            await conn.execute(text(sql))
        await conn.execute(
            text(
                "INSERT INTO empleados(id,nombre,rol,activo) VALUES(CAST(:id AS uuid),'Approved actor','superadmin',true)"
            ),
            {"id": actor},
        )
        await conn.execute(
            text(
                "INSERT INTO budget_versions(id,edition_year,version_name,status,source) VALUES(CAST(:id AS uuid),2026,'Approved root','reforecast','operational_singleton')"
            ),
            {"id": actions["version_id"]},
        )
        for decision in actions["movements"]:
            for cid, active in [
                (decision["source_concept_id"], False),
                (decision["target_concept_id"], True),
            ]:
                await conn.execute(
                    text(
                        "INSERT INTO budget_concepts(id,concept_name,active) VALUES(CAST(:id AS uuid),:id,:active) ON CONFLICT(id) DO NOTHING"
                    ),
                    {"id": cid, "active": active},
                )
            await conn.execute(
                text(
                    "INSERT INTO budget_lines(id,budget_version_id,tournament_id,tournament_name,phase,concept_name,budget_concept_id,budget_amount) VALUES(CAST(:id AS uuid),CAST(:version AS uuid),CAST(:tid AS uuid),'Tournament',:phase,'Target',CAST(:concept AS uuid),100) ON CONFLICT(id) DO NOTHING"
                ),
                {
                    "id": decision["target_line_id"],
                    "version": actions["version_id"],
                    "tid": decision["tournament_id"],
                    "phase": decision["effective_phase"],
                    "concept": decision["target_concept_id"],
                },
            )
            pid = str(uuid4())
            await conn.execute(
                text(
                    "INSERT INTO accounting_polizas VALUES(CAST(:id AS uuid),'balanced')"
                ),
                {"id": pid},
            )
            await conn.execute(
                text(
                    "INSERT INTO accounting_poliza_lines VALUES(CAST(:id AS uuid),CAST(:pid AS uuid),:amount,0)"
                ),
                {
                    "id": decision["movement_key"].split(":")[1],
                    "pid": pid,
                    "amount": float(decision["amount"]),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO documentos VALUES(CAST(:id AS uuid),'pagado') ON CONFLICT(id) DO NOTHING"
                ),
                {"id": decision["document_id"]},
            )
            movements.append(
                {
                    "accounting_line_id": decision["movement_key"].split(":")[1],
                    "poliza_id": pid,
                    "document_id": decision["document_id"],
                    "expense_id": None,
                    "concept_key": decision["source_concept_id"],
                    "effective_phase": decision["effective_phase"],
                    "amount": float(decision["amount"]),
                    "cuenta_codigo": decision["source_account_code"],
                    "kind": "ledger_expense",
                    "month_number": 40,
                    "document_state": "pagado",
                }
            )
        liga = [d for d in actions["movements"] if d.get("prior_authority_evidence")]
        prior = liga[0]["prior_authority_evidence"]
        import json

        await conn.execute(
            text(
                "INSERT INTO budget_version_audit_log(id,budget_version_id,event_type,payload) VALUES(CAST(:id AS uuid),CAST(:version AS uuid),'tournament_upload_four_conflicts_reconciled',CAST(:payload AS jsonb))"
            ),
            {
                "id": prior["audit_id"],
                "version": actions["version_id"],
                "payload": json.dumps(
                    {
                        "plan_sha256": prior["plan_sha256"],
                        "canonical_id": liga[0]["target_concept_id"],
                        "document_ids": [d["document_id"] for d in liga],
                    }
                ),
            },
        )

    async def snapshot(*args, **kwargs):
        tid = kwargs["tournament_id"]
        keys = {
            d["movement_key"].split(":")[1]
            for d in actions["movements"]
            if d["tournament_id"] == tid
        }
        return {
            "movements": [dict(m) for m in movements if m["accounting_line_id"] in keys]
        }

    monkeypatch.setattr(cli.service, "build_budget_actuals_snapshot", snapshot)
    async with AsyncSession(database) as session:
        before_policies = await cli.rows(session, "accounting_polizas")
        before_lines = await cli.rows(session, "accounting_poliza_lines")
        first = actions["movements"][0]
        with pytest.raises(ValueError, match="no corresponde"):
            await cli.service.assign_budget_movement_to_line(
                session,
                budget_version_id=actions["version_id"],
                budget_concept_id=first["target_concept_id"],
                budget_line_id=first["target_line_id"],
                movement_key=first["movement_key"],
                actor_empleado_id=actor,
                ensure_schema=False,
            )
        await cli.service.confirm_tournament_closeout_decisions(
            session, actor_empleado_id=actor
        )
        for decision in actions["movements"]:
            await cli.service.assign_budget_movement_to_line(
                session,
                budget_version_id=actions["version_id"],
                budget_concept_id=decision["target_concept_id"],
                budget_line_id=decision["target_line_id"],
                movement_key=decision["movement_key"],
                actor_empleado_id=actor,
                ensure_schema=False,
            )
        proofs = await cli.service._closeout_assignment_proofs(
            session, actions["version_id"], movements
        )
        assert proofs == {
            d["movement_key"]: d["target_line_id"] for d in actions["movements"]
        }
        projected = [dict(m) for m in movements]
        await cli.service._apply_explicit_movement_assignments(
            session,
            budget_version_id=actions["version_id"],
            movements=projected,
            store=cli.service._monthly_actual_store(),
        )
        assert {
            cli.service.budget_movement_key(m): m["budget_line_id"] for m in projected
        } == proofs
        assert await cli.rows(session, "accounting_polizas") == before_policies
        assert await cli.rows(session, "accounting_poliza_lines") == before_lines
        await session.execute(
            text(
                "UPDATE accounting_poliza_lines SET debe=debe+1 WHERE id=CAST(:id AS uuid)"
            ),
            {"id": movements[0]["accounting_line_id"]},
        )
        proofs = await cli.service._closeout_assignment_proofs(
            session, actions["version_id"], movements
        )
        assert first["movement_key"] not in proofs
        assert len(proofs) == 15
