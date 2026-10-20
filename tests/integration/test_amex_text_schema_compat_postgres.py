"""TEXT production columns retain exact schema and real R2/R3 accounting gates."""

import importlib.util
import re
from pathlib import Path

import pytest

from devnous.gastos.services.amex_direct_schema_service import (
    inspect_amex_direct_schema,
)

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "amex_text_compat_owner",
    ROOT / "tests/integration/test_amex_settlement_postgres.py",
)
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
direct_pg = owner.direct_pg
VIEWS = ("amex_expense_account_credit_sources", "amex_direct_prior_sources")


async def install_text_source_columns(pg):
    """Use real owner SQL over the legacy TEXT schema, rather than ORM VARCHAR."""
    for name in VIEWS:
        await pg.conn.execute(f"DROP VIEW {name}")
    await pg.conn.execute(
        "ALTER TABLE documentos ALTER COLUMN tipo TYPE TEXT; "
        "ALTER TABLE documentos ALTER COLUMN estado TYPE TEXT; "
        "ALTER TABLE expense_reports ALTER COLUMN estado_gasto TYPE TEXT"
    )
    migration = (
        ROOT / "database/migrations/20261020_amex_expense_account_settlements.sql"
    ).read_text()
    for name in VIEWS:
        statement = re.search(rf"CREATE VIEW {name} AS\b.*?;", migration, re.S)
        assert statement is not None
        await pg.conn.execute(statement.group())
    async with pg.factory() as session:
        assert (await inspect_amex_direct_schema(session))["ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["settlement", "prior"])
async def test_real_text_schema_historical_r2_r3_keep_original_accounting(
    direct_pg, kind
):
    pg = direct_pg
    await install_text_source_columns(pg)
    producer = (
        owner.historical_credit if kind == "settlement" else owner.historical_prior
    )
    current, row, expense, *_ = await producer(pg, origin="user_upload")
    await owner.select_credit(pg, current, row)
    budget_before = await pg.conn.fetchval(
        "SELECT count(*) FROM budget_movement_assignments"
    )
    polizas_before = await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas")
    result, preview = await owner.confirm_selected(pg, current, key="text-" + kind)
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments")
        == budget_before
    )
    if kind == "prior":
        assert result.status == "completed" and result.poliza is None
        assert (
            await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas")
            == polizas_before
        )
        assert preview.progress["completion_only"] is True
    else:
        assert result.status == "recognized"
        lines = await pg.conn.fetch(
            "SELECT cuenta_contable_id,debe,haber FROM accounting_poliza_lines "
            "WHERE poliza_id=$1 ORDER BY line_no",
            result.poliza.id,
        )
        assert len(lines) == 2
        assert lines[0]["cuenta_contable_id"] == row["origin_account_id"]
        assert lines[0]["debe"] == 116 and lines[1]["haber"] == 116
        assert (
            await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas")
            == polizas_before + 1
        )
        assert (
            await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
            == 0
        )
        for fmt in ("xlsx", "csv", "zip"):
            async with pg.factory() as session:
                body, filename, _ = await owner.accounting.export_direct_cut(
                    session,
                    actor_id=pg.actor.id,
                    import_id=current.statement.id,
                    format=fmt,
                )
            assert body and filename.endswith("." + fmt)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", VIEWS)
async def test_text_schema_still_rejects_relaxed_approval_predicate(direct_pg, name):
    pg = direct_pg
    await install_text_source_columns(pg)
    original = await pg.conn.fetchval("SELECT pg_get_viewdef($1::regclass,true)", name)
    altered = original.replace("d.estado = 'aprobado'::text", "true")
    assert altered != original
    await pg.conn.execute(f"CREATE OR REPLACE VIEW {name} AS " + altered)
    async with pg.factory() as session:
        result = await inspect_amex_direct_schema(session)
    assert result["ready"] is False
    assert name + ": incompatible settlement view definition" in result["schema_errors"]
