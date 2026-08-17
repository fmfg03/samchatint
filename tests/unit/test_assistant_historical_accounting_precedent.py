from __future__ import annotations

from datetime import date

import pytest

from samchat.assistant.historical_accounting_precedent import (
    HISTORICAL_ACCOUNTING_PRECEDENT_ONLY,
    query_historical_accounting_precedents,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Row:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeSession:
    def __init__(self, *, latest=True, rows=None):
        self.latest = latest
        self.rows = rows if rows is not None else []
        self.calls = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "FROM accounting_import_runs" in sql:
            if not self.latest:
                return _Result([])
            return _Result([
                _Row(id="run-1", fiscal_year=2025, company_label="Plataforma Sports")
            ])
        return _Result(self.rows)


@pytest.mark.asyncio
async def test_historical_accounting_precedent_returns_candidates_without_assigning_account() -> None:
    session = _FakeSession(
        rows=[
            _Row(
                account_code="5300-006-007",
                account_name="CAFETERIA",
                match_count=8,
                policy_count=5,
                debit_total=12000.50,
                credit_total=0,
                last_policy_date=date(2025, 7, 31),
                sample_concepts=["Consumo alimentos", "Cafe Serena"],
                sample_policies=["Dr-123", "Eg-455"],
            )
        ]
    )

    report = await query_historical_accounting_precedents(
        session,
        query="consumo alimentos cafeteria",
        company_code="1",
        fiscal_year=2025,
    )

    assert report.report_id == "historical_accounting_precedent_v1"
    assert report.status == "precedents_found"
    assert report.audit_language == HISTORICAL_ACCOUNTING_PRECEDENT_ONLY
    assert report.company_code == "01"
    assert report.fiscal_year == 2025
    assert report.candidates[0].account_code == "5300-006-007"
    assert report.candidates[0].confidence == "high"
    assert report.candidates[0].last_policy_date == "2025-07-31"
    assert report.safety_summary["read_only"] is True
    assert report.safety_summary["account_assignment_performed"] is False
    assert report.writes_attempted == 0
    assert any("No asigna" in claim for claim in report.non_claims)


@pytest.mark.asyncio
async def test_historical_accounting_precedent_fails_closed_without_historical_source() -> None:
    report = await query_historical_accounting_precedents(
        _FakeSession(latest=False),
        query="hotel leon",
        company_code="01",
        fiscal_year=2024,
    )

    assert report.status == "no_historical_source"
    assert report.candidates == []
    assert report.safety_summary["writes_enabled"] is False
    assert "No hay importacion historica" in report.summary


@pytest.mark.asyncio
async def test_historical_accounting_precedent_fails_closed_without_matches() -> None:
    report = await query_historical_accounting_precedents(
        _FakeSession(rows=[]),
        query="concepto raro",
    )

    assert report.status == "no_precedent_found"
    assert report.candidates == []
    assert report.safety_summary["account_assignment_performed"] is False


@pytest.mark.asyncio
async def test_historical_accounting_precedent_requires_query_or_account_code() -> None:
    report = await query_historical_accounting_precedents(_FakeSession(), query="")

    assert report.status == "invalid_query"
    assert report.candidates == []
    assert "query o account_code" in report.summary


@pytest.mark.asyncio
async def test_historical_accounting_precedent_supports_account_filter() -> None:
    session = _FakeSession(rows=[])

    await query_historical_accounting_precedents(
        session,
        query="",
        account_code="1170-002-001",
    )

    _, params = session.calls[-1]
    assert params["account_code"] == "1170-002-001"

@pytest.mark.asyncio
async def test_router_runs_historical_accounting_precedent_read_tool(monkeypatch) -> None:
    import samchat.assistant.router as assistant_router

    captured = {}

    async def fake_query(session, **kwargs):
        captured.update(kwargs)
        return await query_historical_accounting_precedents(
            _FakeSession(
                rows=[
                    _Row(
                        account_code="5300-006-007",
                        account_name="CAFETERIA",
                        match_count=3,
                        policy_count=2,
                        debit_total=100,
                        credit_total=0,
                        last_policy_date=None,
                        sample_concepts=["Cafe"],
                        sample_policies=["Dr-1"],
                    )
                ]
            ),
            **kwargs,
        )

    monkeypatch.setattr(assistant_router, "query_historical_accounting_precedents", fake_query)

    result = await assistant_router._run_read_tool(
        "assistant_historical_accounting_precedent",
        {"query": "cafeteria", "company_code": "1", "fiscal_year": 2025, "limit": 3},
        gastos_session=object(),
        tournament_key_default=None,
        current_role="admin",
    )

    assert result["report_id"] == "historical_accounting_precedent_v1"
    assert result["status"] == "precedents_found"
    assert result["candidates"][0]["account_code"] == "5300-006-007"
    assert result["safety_summary"]["account_assignment_performed"] is False
    assert captured["company_code"] == "1"
    assert captured["limit"] == 3
