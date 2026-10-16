"""A direct fiscal posting remains in its actual assigned budget version."""

import importlib.util
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

import devnous.gastos  # noqa: F401 - initialize the production import order
from samchat.budgets.service import build_budget_actuals_snapshot

_path = (
    Path(__file__).resolve().parents[1]
    / "acceptance/test_amex_sat_budget_direct_postgres.py"
)
_spec = importlib.util.spec_from_file_location("direct_budget_versions_fixture", _path)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
direct_pg = _fixture.direct_pg


@pytest.mark.asyncio
async def test_posting_does_not_move_direct_cost_into_another_budget_version(direct_pg):
    pg = direct_pg
    current = await _fixture._assigned_statement(pg)
    other_version, other_line = uuid4(), uuid4()
    await pg.conn.execute(
        "INSERT INTO budget_versions(id,edition_year,version_name,status) "
        "VALUES($1,2026,'Other version same tournament','approved')",
        other_version,
    )
    await pg.conn.execute(
        "INSERT INTO budget_lines(id,budget_version_id,budget_concept_id,"
        "tournament_id,tournament_name,concept_name,line_direction,budget_amount) "
        "VALUES($1,$2,$3,$4,'Acceptance direct','Alimentos','expense',1000)",
        other_line,
        other_version,
        pg.concept.id,
        pg.tournament.id,
    )

    async def snapshot(version_id):
        async with pg.factory() as session:
            return await build_budget_actuals_snapshot(
                session,
                edition_year=2026,
                version_id=str(version_id),
                tournament_id=str(pg.tournament.id),
                tournament_name=pg.tournament.name,
            )

    expense_id = str(current.items[0].imported_expense_id)

    def source_movements(value):
        return [
            row for row in value["movements"] if row.get("expense_id") == expense_id
        ]

    assert len(source_movements(await snapshot(pg.version_id))) == 1
    assert source_movements(await snapshot(other_version)) == []
    result = await _fixture._confirm(pg, await _fixture._cut_args(pg, current))
    assert result.status == "recognized", result.reason
    assigned, unrelated = await snapshot(pg.version_id), await snapshot(other_version)
    actual = source_movements(assigned)
    assert len(actual) == 1
    assert actual[0]["kind"] == "ledger_expense"
    assert Decimal(str(actual[0]["amount"])) == Decimal("100")
    assert source_movements(unrelated) == []
    assert sum(
        Decimal(str(bucket["real_expense_cash"]))
        for months in unrelated["monthly"].values()
        for bucket in months.values()
    ) == Decimal("0")
