"""Real JSONB/current-run selection inside PostgreSQL READ ONLY transactions."""

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Match the live application import order for the existing gastos/budgets cycle.
import devnous.gastos  # noqa: F401
from samchat.client_executive import accounting_bridge as bridge

# Reuse the exact opt-in disposable PostgreSQL fixture; it creates/drops only its
# own UUID database and rejects DDL after owner schema bootstrap.
from test_historical_explicit_manifest_postgres import database  # noqa: F401


@pytest.mark.asyncio
async def test_documentary_real_jsonb_current_selected_source_no_ddl(database):  # noqa: F811
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
