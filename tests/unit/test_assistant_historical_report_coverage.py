"""Selected historical sources never restore superseded periods through fallbacks."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from samchat.assistant import tools


class Result:
    def __init__(self, rows=(), count=0):
        self.rows = rows
        self.count = count

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar_one(self):
        return self.count

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Run(tuple):
    def __new__(cls, metadata):
        obj = super().__new__(cls, ("current-run", "Company"))
        obj.coverage_metadata = metadata
        return obj


class Session:
    def __init__(self, metadata=None, present=True, admitted=False):
        self.metadata = metadata or {}
        self.present = present
        self.admitted = admitted
        self.calls = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "FROM accounting_import_runs air" in sql:
            return Result([Run(self.metadata)] if self.present else [])
        if params and "import_run_id" in params:
            assert params["import_run_id"] == "current-run"
            if "COUNT(*)" in sql:
                return Result(count=int(self.admitted))
            assert "FROM historical_policy_lines l" in sql
            return Result(
                [
                    SimpleNamespace(
                        policy_date=date(2026, 9, 1),
                        fiscal_month=9,
                        policy_type="Dr",
                        policy_number="1",
                        policy_id_natural="p",
                        account_code_raw="5300",
                        account_name_raw="Gastos",
                        line_concept="Explicit source",
                        concept_raw="Policy",
                        debit_amount=10,
                        credit_amount=0,
                    )
                ]
            )
        # Existing live ledger retains priority; these tests simulate its absence.
        return Result()


@pytest.fixture(autouse=True)
def reject_superseded_fallbacks(monkeypatch):
    movement = AsyncMock(side_effect=AssertionError("Superseded movements read"))
    static = lambda **kwargs: (_ for _ in ()).throw(AssertionError("Q1 static read"))
    monkeypatch.setattr(tools, "_load_historical_accounting_movements_report", movement)
    monkeypatch.setattr(tools, "_load_static_balance_report", static)
    return movement


@pytest.mark.asyncio
@pytest.mark.parametrize("report_type", ["diario", "mayor", "balanza"])
@pytest.mark.parametrize(
    "company,month,withheld",
    [
        ("01", 3, True),
        ("GOAT", 8, True),
        ("01", 9, False),
    ],
)
async def test_withheld_and_missing_periods_do_not_restore_old_sources(
    company, month, withheld, report_type
):
    metadata = {
        "balance_months": [1, 2, 4, 5, 6, 7],
        "policy_months": [1, 2, 4, 5, 6, 7],
        "withheld_months": (
            [{"month_num": month, "reason": "Needs source reconciliation"}]
            if withheld
            else []
        ),
    }
    session = Session(metadata)
    result = await tools.finance_accounting_report(
        session, report_type=report_type, year=2026, month=month, company_code=company
    )
    assert result["source"] == "historical_tables"
    assert result["coverage"]["status"] == ("withheld" if withheld else "unavailable")
    assert result["coverage"]["import_run_id"] == "current-run"
    assert result["summary"]["debe"] is None
    assert result["summary"]["haber"] is None
    assert result["rows"] == []
    assert "No disponible" in result["artifact_markdown"]
    latest_params = next(
        p for sql, p in session.calls if "FROM accounting_import_runs air" in sql
    )
    assert latest_params == {"fiscal_year": 2026, "company_code": company}


@pytest.mark.asyncio
@pytest.mark.parametrize("report_type", ["diario", "mayor", "balanza"])
async def test_empty_filter_on_current_run_never_falls_back(report_type):
    result = await tools.finance_accounting_report(
        Session({"balance_months": [1], "policy_months": [1]}),
        report_type=report_type,
        year=2026,
        month=1,
        q="nonexistent",
    )
    assert result["coverage"]["status"] == "no_matching_rows"
    assert result["summary"]["debe"] is None
    assert result["filters"]["q"] == "nonexistent"


@pytest.mark.asyncio
async def test_legacy_current_run_empty_stays_authoritative_without_invented_coverage():
    result = await tools.finance_accounting_report(
        Session(), report_type="balanza", year=2026, month=3
    )
    assert result["coverage"]["available_months"] is None
    assert result["coverage"]["status"] == "no_matching_rows"


@pytest.mark.asyncio
async def test_source_absent_still_allows_legacy_fallback(monkeypatch):
    legacy = {"source": "legacy"}
    reader = AsyncMock(return_value=legacy)
    monkeypatch.setattr(tools, "_load_historical_accounting_movements_report", reader)
    result = await tools.finance_accounting_report(
        Session(present=False), report_type="diario", year=2026, month=3
    )
    assert result is legacy
    reader.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("month", [9, None])
async def test_admitted_reports_keep_amounts_and_disclose_partial_annual_coverage(
    month,
):
    metadata = {
        "policy_months": [1, 2, 4, 5, 6, 7, 8, 9],
        "withheld_months": [{"month_num": 3, "reason": "Needs reconciliation"}],
    }
    result = await tools.finance_accounting_report(
        Session(metadata, admitted=True), report_type="diario", year=2026, month=month
    )
    assert result["summary"]["debe"] == 10
    assert result["coverage"]["withheld_months"] == metadata["withheld_months"]
    if month is None:
        assert result["coverage"]["status"] == "partial"
        assert "Cobertura parcial" in result["artifact_markdown"]
    else:
        assert result["coverage"]["status"] == "available"
