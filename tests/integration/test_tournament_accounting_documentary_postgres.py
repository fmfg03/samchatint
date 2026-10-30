"""Real JSONB/current-run selection inside PostgreSQL READ ONLY transactions."""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Reuse the exact opt-in disposable PostgreSQL fixture; it creates/drops only its
# own UUID database and rejects DDL after owner schema bootstrap.
from test_historical_explicit_manifest_postgres import database  # noqa: F401

# Match the live application import order for the existing gastos/budgets cycle.
import devnous.gastos  # noqa: F401
from samchat.client_executive import accounting_bridge as bridge


@pytest.mark.asyncio
async def test_documentary_real_jsonb_current_selected_source_no_ddl(
    database,  # noqa: F811
):
    spec = importlib.util.spec_from_file_location(
        "documentary_fixtures",
        Path(__file__).parents[1] / "unit/test_tournament_accounting_documentary.py",
    )
    fixture_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture_module)
    data = fixture_module.data.__wrapped__()
    run_id, old_run = str(uuid4()), str(uuid4())
    ids = {}

    def uid(value):
        return ids.setdefault(value, str(uuid4()))

    async with AsyncSession(database) as session:
        for run, finished in ((old_run, "2026-01-01"), (run_id, "2026-10-28")):
            await session.execute(
                text(
                    """
                INSERT INTO accounting_import_runs
                  (id,source_type,filename,source_sha256,mode,status,started_at,finished_at)
                VALUES (:id,'historical_accounting','source','eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
                  'apply','completed',CAST(:finished AS timestamptz),CAST(:finished AS timestamptz))
            """
                ),
                {
                    "id": run,
                    "finished": datetime.fromisoformat(finished).replace(
                        tzinfo=timezone.utc
                    ),
                },
            )
        for source in data["sources"]:
            params = {
                **source,
                "id": uid(source["id"]),
                "run": run_id,
                "metadata": json.dumps(source["metadata"]),
            }
            await session.execute(
                text(
                    """
                INSERT INTO historical_accounting_source_files
                (id,import_run_id,fiscal_year,company_code,company_label,source_family,
                 source_format,source_filename,source_path,source_sha256,validation_status,metadata)
                VALUES (:id,:run,:fiscal_year,:company_code,:company_label,:source_family,
                  'csv','source.csv','source.csv',:source_sha256,:validation_status,CAST(:metadata AS jsonb))
            """
                ),
                params,
            )
        # An older completed source retains March; the selected import excludes it.
        old_source = str(uuid4())
        await session.execute(
            text(
                """
            INSERT INTO historical_accounting_source_files
            (id,import_run_id,fiscal_year,company_code,company_label,source_family,
             source_format,source_filename,source_path,source_sha256,validation_status)
            VALUES (:id,:run,2026,'01','PSP1705058S4','balanza','csv','old.csv','old.csv',
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','OK')
        """
            ),
            {"id": old_source, "run": old_run},
        )
        for row in data["balances"]:
            params = {
                **row,
                "id": str(uuid4()),
                "run": run_id,
                "source": uid("balanza"),
                "period": f'2026-{row["fiscal_month"]:02d}',
            }
            await session.execute(
                text(
                    """
                INSERT INTO historical_trial_balance_rows
                (id,import_run_id,source_file_id,fiscal_year,fiscal_month,period_key,
                 company_code,company_label,account_code_raw,account_name_raw,is_detail,
                 debits,credits,closing_balance)
                VALUES (:id,:run,:source,2026,:fiscal_month,:period,'01','PSP1705058S4',
                  :account_code,'Documentary account',true,:debits,:credits,:closing_balance)
            """
                ),
                params,
            )
        await session.execute(
            text(
                """
            INSERT INTO historical_trial_balance_rows
            (id,import_run_id,source_file_id,fiscal_year,fiscal_month,period_key,
             company_code,company_label,account_code_raw,account_name_raw,is_detail,debits)
            VALUES (:id,:run,:source,2026,3,'2026-03','01','PSP1705058S4',
              '5300-010-001','Old March',true,999999)
        """
            ),
            {"id": str(uuid4()), "run": old_run, "source": old_source},
        )
        for header in data["headers"]:
            params = {
                **header,
                "id": uid(header["id"]),
                "run": run_id,
                "source": uid("poliza_header"),
                "period": f'2026-{header["fiscal_month"]:02d}',
                "flags": json.dumps(header["quality_flags"]),
            }
            await session.execute(
                text(
                    """
                INSERT INTO historical_policy_headers
                (id,import_run_id,source_file_id,fiscal_year,fiscal_month,period_key,
                 company_code,company_label,policy_id_natural,policy_type,policy_number,
                 line_count,total_debits,total_credits,is_balanced,quality_flags)
                VALUES (:id,:run,:source,2026,:fiscal_month,:period,'01','PSP1705058S4',
                  :policy_id_natural,'Dr','1',:line_count,:total_debits,:total_credits,
                  true,CAST(:flags AS jsonb))
            """
                ),
                params,
            )
        for number, line in enumerate(data["lines"]):
            params = {
                **line,
                "id": uid(line["id"]),
                "run": run_id,
                "source": uid("poliza_line"),
                "header": uid(line["policy_header_id"]),
                "number": number,
                "period": f'2026-{line["fiscal_month"]:02d}',
            }
            await session.execute(
                text(
                    """
                INSERT INTO historical_policy_lines
                (id,import_run_id,source_file_id,policy_header_id,fiscal_year,fiscal_month,
                 period_key,company_code,company_label,policy_id_natural,line_number,
                 account_code_raw,account_name_raw,debit_amount,credit_amount)
                VALUES (:id,:run,:source,:header,2026,:fiscal_month,:period,'01','PSP1705058S4',
                  :policy_id_natural,:number,:account_code,'Source account',:debit_amount,:credit_amount)
            """
                ),
                params,
            )
        await session.commit()
    async with AsyncSession(database) as session:
        await session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        results = [
            await bridge.build_tournament_documentary_accounting(
                session, tournament_id=tournament, edition_year=2026
            )
            for tournament in (
                fixture_module.COPA,
                fixture_module.LIGA,
                fixture_module.HWC,
            )
        ]
        assert all(r["source_status"] == "partial" for r in results)
        assert all(r["import_run_id"] == run_id for r in results)
        accounts = [a for r in results for a in r["accounts"]]
        assert len(accounts) == 85
        assert sum(bool(a["policy_periods"]) for a in accounts) == 39
        assert all(
            3 not in [p["month"] for p in a["trial_balance_periods"]] for a in accounts
        )
        assert all(a["currency"] is a["tax_basis"] is None for a in accounts)
        assert accounts[0]["policy_periods"][0]["quality_flags"] == [
            "missing_summary_marker"
        ]
        flags = (
            await session.execute(
                text("SELECT quality_flags FROM historical_policy_headers LIMIT 1")
            )
        ).scalar_one()
        assert flags == ["missing_summary_marker"]
        await session.rollback()


@pytest.mark.asyncio
async def test_actual_restored_multitournament_home_uses_real_batch_readers(
    monkeypatch,
):
    """Financial owners and authorization run unmocked on the opt-in restore."""
    import os
    from unittest.mock import AsyncMock

    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine

    from samchat.client_executive import home, service
    from samchat.client_executive.read_batches import ReadBatch

    dsn = (
        "postgresql+asyncpg://postgres@/samchat_dashboard_closeout_restore_20261030"
        "?host=/tmp/samchat-dashboard-closeout-restore-proxy-20261030&port=55483"
    )
    if os.environ.get("SAMCHAT_DASHBOARD_RESTORE_TEST_URL") != dsn:
        pytest.skip("Requires the exact private dashboard acceptance restore")
    engine = create_async_engine(dsn)
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def readonly_guard(conn, cursor, statement, parameters, context, executemany):
        assert statement.lstrip().split()[0].upper() in {
            "SELECT",
            "WITH",
            "SET",
            "SAVEPOINT",
            "RELEASE",
            "ROLLBACK",
        }
        statements.append(statement)

    # Operational stores are independent of this financial transport regression.
    # Budget, documentary, fact, scope and receipt owners are the real readers.
    monkeypatch.setattr(
        service,
        "dispatch_registration_snapshot",
        AsyncMock(return_value={"available": False}),
    )
    monkeypatch.setattr(
        service, "_build_operational_summaries", AsyncMock(return_value={})
    )
    expected = {
        "16793f0e-4697-4a28-8f42-b563da8cc7d8": ("3686827.60", 27),
        "67c556df-f953-4f99-8cab-87ed4d111b05": ("17471291.92", 29),
        "c3b247f1-e3fc-4f4a-8995-56a5880f86a6": ("30384939.78", 29),
    }
    try:
        async with AsyncSession(engine) as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            result, scope = await home.build_home(
                session,
                actor="e3d13040-2360-420f-98a1-516440ef63c3",
                superadmin=True,
                year=2026,
                tournament_ids=list(expected),
            )
            assert len(scope["selected"]) == len(result["tournaments"]) == 3
            for row in result["tournaments"]:
                budget, count = expected[row["id"]]
                assert row["values"]["budget"] == budget
                assert row["accounting"] is not None
                documentary = row["accounting"]["documentary"]
                assert documentary["source_status"] == "partial"
                assert len(documentary["accounts"]) == count
                assert documentary["coverage"]["trial_balance_months"] == [1, 2, 5, 8]
            assert any("__direction_batch" in q for q in statements)
            # Exercise the actual JSONB scalar result used by audit-proof owners,
            # including a cache hit: mutation cannot rewrite cached evidence.
            batch = ReadBatch(session)
            query = text(
                "SELECT jsonb_build_object('proofs', jsonb_build_array(jsonb_build_object('authorized', false))) AS payload"
            )
            try:
                from samchat.budgets import service as budgets

                actions = budgets._closeout_actions()
                audit_payloads = (
                    (
                        await session.execute(
                            text(
                                "SELECT payload FROM budget_version_audit_log WHERE event_type=:event AND budget_version_id=CAST(:version AS uuid)"
                            ),
                            {
                                "event": budgets._CLOSEOUT_EVENT,
                                "version": actions["version_id"],
                            },
                        )
                    )
                    .scalars()
                    .all()
                )
                movements = [
                    proof["movement"]
                    for audit in audit_payloads
                    for proof in audit.get("proofs", [])
                ]
                confirmed = await budgets._closeout_assignment_proofs(
                    batch, actions["version_id"], movements
                )
                assert len(confirmed) == 16
                assert set(confirmed) == {
                    d["movement_key"] for d in actions["movements"]
                }
                before = len(statements)
                proof = (await batch.execute(query)).scalars().all()
                assert proof == [{"proofs": [{"authorized": False}]}]
                proof[0]["proofs"][0]["authorized"] = True
                assert (await batch.execute(query)).scalars().all() == [
                    {"proofs": [{"authorized": False}]}
                ]
                assert len(statements) - before == 3  # savepoint, SELECT, release
            finally:
                await batch.close()
            await session.rollback()
    finally:
        await engine.dispose()
