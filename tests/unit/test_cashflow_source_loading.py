"""Behavioral acceptance checks for complete, side-effect-free source scans."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import ProgrammingError

from samchat.cashflow.service import load_cashflow_sources


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return self.rows

    def first(self):
        return next(iter(self.rows), None)


class SourceSession:
    """The fake enforces SELECT-only and records transaction ownership."""

    no_autoflush = nullcontext()

    def __init__(
        self, bank=(), documents=(), fail_bank=False, parent=None, versions=()
    ):
        self.bank = list(bank)
        self.documents = list(documents)
        self.fail_bank = fail_bank
        self.parent = parent
        self.versions = list(versions)
        self.queries = []
        self.savepoints = []
        self.flush = AsyncMock(side_effect=AssertionError("Must not flush caller"))
        self.rollback = AsyncMock(
            side_effect=AssertionError("Must not rollback caller")
        )
        self.commit = AsyncMock(side_effect=AssertionError("Must not commit caller"))

    async def connection(self):
        return self

    async def begin_nested(self):
        nested = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        self.savepoints.append(nested)
        return nested

    async def execute(self, statement, params=None):
        query = str(statement)
        self.queries.append(query)
        assert query.lstrip().split()[0].upper() == "SELECT"
        if "FROM bank_movements t" in query:
            if self.fail_bank:
                raise ProgrammingError(query, params, RuntimeError("Unavailable"))
            return Rows(self.bank)
        if "FROM documentos t" in query:
            return Rows(self.documents)
        if "FROM budget_versions t" in query:
            return Rows(self.versions)
        return Rows([self.parent] if self.parent else [])

    async def scalar(self, statement, params=None):
        query = str(statement)
        self.queries.append(query)
        assert query.lstrip().split()[0].upper() == "SELECT"
        if "SELECT nombre" in query:
            return "Beneficiario acreditado"
        if "SELECT name" in query:
            return "Proyecto acreditado"
        return None


async def load(session, **kwargs):
    return await load_cashflow_sources(
        session,
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        forecast_end=date(2026, 3, 1),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_complete_bank_scan_does_not_truncate_at_500_or_5000():
    session = SourceSession(bank=[{"id": str(i)} for i in range(5001)])
    result = await load(session)
    assert len(result["bank_rows"]) == 5001
    assert result["source_status"]["bank"]["complete"] is True
    bank_query = next(q for q in session.queries if "FROM bank_movements t" in q)
    assert "LIMIT" not in bank_query.upper()
    assert "to_jsonb(t)->>'company_id' IS NULL" in bank_query
    # Legacy bank rows do not acquire a default MXN currency.
    assert all(row.get("currency") is None for row in result["bank_rows"])
    session.flush.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_payable_resolver_preserves_final_zero_and_missing_reimbursement():
    session = SourceSession(
        documents=[
            {"id": "zero", "monto_total": 0, "monto_solicitado": 100},
            {"id": "final", "monto_total": Decimal("123.45"), "monto_solicitado": 100},
            {
                "id": "reimbursement",
                "monto_total": None,
                "monto_solicitado": 100,
                "concepto_pago": "Reembolso de saldo a favor - I-001",
            },
            {"id": "missing", "monto_total": None, "monto_solicitado": None},
        ]
    )
    result = await load(session)
    amounts = {row["id"]: row["payable_amount"] for row in result["documents"]}
    assert amounts == {
        "zero": Decimal("0.00"),
        "final": Decimal("123.45"),
        "reimbursement": None,
        "missing": None,
    }


@pytest.mark.asyncio
async def test_unavailable_bank_rolls_back_only_its_savepoint_and_keeps_ap():
    session = SourceSession(fail_bank=True, documents=[{"id": "ap", "monto_total": 9}])
    result = await load(session)
    assert result["source_status"]["bank"]["available"] is False
    assert result["source_status"]["obligations"]["complete"] is True
    assert result["documents"][0]["payable_amount"] == Decimal("9.00")
    session.savepoints[0].rollback.assert_awaited_once()
    session.savepoints[1].commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_bank_parent_does_not_degrade_to_unavailable_or_global():
    session = SourceSession(
        bank=[{"id": "bank", "related_poliza_id": "foreign"}],
        parent={"company_id": "another-company"},
    )
    with pytest.raises(ValueError, match="cashflow_source_parent_scope_mismatch"):
        await load(session)
    session.savepoints[0].rollback.assert_awaited_once()
    assert len(session.savepoints) == 1


@pytest.mark.asyncio
async def test_tournament_cap_does_not_expose_global_bank_and_keeps_ap_predicate():
    session = SourceSession(bank=[{"id": "global"}])
    result = await load(session, tournament_ids=["allowed-tournament"])
    assert result["bank_rows"] == []
    assert result["source_status"]["bank"]["available"] is False
    assert not any("FROM bank_movements t" in q for q in session.queries)
    ap_query = next(q for q in session.queries if "FROM documentos t" in q)
    assert "ANY(CAST(:tournaments AS text[]))" in ap_query
    assert "t.fecha_pago IS NULL" in ap_query
    assert "'en_proceso_pago'" in ap_query
    assert "t.pagado_en IS NULL" in ap_query


@pytest.mark.asyncio
async def test_empty_tournament_cap_is_empty_complete_without_source_scans():
    session = SourceSession(bank=[{"id": "global"}], documents=[{"id": "global-ap"}])
    result = await load(session, tournament_ids=[])
    assert not session.queries
    assert not result["bank_rows"]
    assert not result["documents"]
    assert all(state["complete"] for state in result["source_status"].values())


@pytest.mark.asyncio
async def test_document_names_and_project_come_from_explicit_relationships():
    session = SourceSession(
        documents=[
            {
                "id": "ap",
                "monto_total": 15,
                "torneo_id": "tournament-uuid",
                "beneficiario_empleado_id": "employee-uuid",
            }
        ]
    )
    result = await load(session)
    document = result["documents"][0]
    assert document["party"] == "Beneficiario acreditado"
    assert document["project_id"] == "tournament-uuid"
    assert document["project_name"] == "Proyecto acreditado"
    assert document.get("currency") is None


@pytest.mark.asyncio
async def test_ar_credit_policy_and_unproven_legacy_coverage(monkeypatch):
    import samchat.ar.service as ar
    import samchat.budgets.service as budgets

    reader = AsyncMock(return_value={"issued_linked": [], "issued_unlinked": []})
    monkeypatch.setattr(ar, "build_ar_read_model", reader)
    monkeypatch.setattr(
        budgets, "get_budget_version", AsyncMock(return_value={"edition_year": 2026})
    )
    monkeypatch.setattr(budgets, "list_budget_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        budgets, "list_monthly_plan_for_lines", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        budgets,
        "list_budget_versions",
        AsyncMock(
            return_value=[
                {
                    "id": "selected",
                    "edition_year": 2026,
                    "version_name": "Selected",
                    "status": "approved",
                }
            ]
        ),
    )
    result = await load(
        SourceSession(versions=[{"id": "selected"}]),
        budget_version_id="selected",
        credit_days=45,
    )
    assert reader.await_args.kwargs["credit_days_default"] == 45
    assert reader.await_args.kwargs["ensure_schema"] is False
    assert result["source_status"]["ar"]["available"] is True
    assert result["source_status"]["ar"]["complete"] is False


@pytest.mark.asyncio
async def test_activated_company_owner_requires_context(monkeypatch):
    import sys
    from types import ModuleType

    module_name = "devnous.gastos.services.company_operation_scope"
    owner = ModuleType(module_name)

    def require_context(context, action):
        assert context is None and action == "read"
        raise ValueError("company_context_required")

    owner.company_id_for_operation = require_context
    monkeypatch.setitem(sys.modules, module_name, owner)
    session = SourceSession(bank=[{"id": "legacy"}])
    with pytest.raises(ValueError, match="company_context_required"):
        await load(session)
    assert not session.queries


@pytest.mark.asyncio
async def test_default_version_uses_canonical_selector_and_named_allowed_options(
    monkeypatch,
):
    import samchat.ar.service as ar
    import samchat.budgets.service as budgets

    versions = [
        {"id": "foreign", "status": "frozen", "version_name": "Foreign"},
        {"id": "approved", "status": "approved", "version_name": "Approved"},
        {"id": "draft", "status": "draft", "version_name": "Draft"},
    ]
    monkeypatch.setattr(
        budgets, "list_budget_versions", AsyncMock(return_value=versions)
    )
    monkeypatch.setattr(
        budgets, "get_budget_version", AsyncMock(return_value={"edition_year": 2026})
    )
    monkeypatch.setattr(budgets, "list_budget_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        budgets, "list_monthly_plan_for_lines", AsyncMock(return_value={})
    )
    monkeypatch.setattr(ar, "build_ar_read_model", AsyncMock(return_value={}))
    result = await load(SourceSession(versions=[{"id": "approved"}, {"id": "draft"}]))
    assert result["budget_version_id"] == "approved"
    assert {option["version_name"] for option in result["budget_version_options"]} == {
        "Approved",
        "Draft",
    }
    budgets.list_budget_versions.assert_awaited_once()
    assert budgets.list_budget_versions.await_args.kwargs == {
        "edition_year": 2026,
        "ensure_schema": False,
    }


@pytest.mark.asyncio
async def test_explicit_foreign_or_unknown_version_is_not_silently_replaced(
    monkeypatch,
):
    import samchat.budgets.service as budgets

    monkeypatch.setattr(
        budgets,
        "list_budget_versions",
        AsyncMock(
            return_value=[
                {"id": "foreign", "status": "frozen", "version_name": "Foreign"}
            ]
        ),
    )
    with pytest.raises(ValueError, match="budget_version_selection_denied"):
        await load(SourceSession(), budget_version_id="foreign")


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_existing_budget_lines_without_monthly_plan_are_not_zero(
    monkeypatch, partial
):
    import samchat.ar.service as ar
    import samchat.budgets.service as budgets
    from samchat.cashflow.service import build_cashflow_report

    monkeypatch.setattr(
        budgets,
        "list_budget_versions",
        AsyncMock(
            return_value=[
                {"id": "approved", "status": "approved", "version_name": "Approved"}
            ]
        ),
    )
    monkeypatch.setattr(
        budgets, "get_budget_version", AsyncMock(return_value={"edition_year": 2026})
    )
    lines = [{"id": "income-line", "line_direction": "income", "currency": "MXN"}]
    if partial:
        lines.append(
            {"id": "missing-line", "line_direction": "income", "currency": "MXN"}
        )
    monkeypatch.setattr(budgets, "list_budget_lines", AsyncMock(return_value=lines))
    plans = {"income-line": {1: {"expected_income_amount": "100"}}} if partial else {}
    monkeypatch.setattr(
        budgets, "list_monthly_plan_for_lines", AsyncMock(return_value=plans)
    )
    monkeypatch.setattr(ar, "build_ar_read_model", AsyncMock(return_value={}))
    loaded = await load(
        SourceSession(
            bank=[
                {
                    "id": "bank",
                    "fecha": "2026-01-05",
                    "signo": "+",
                    "importe": "100",
                    "currency": "MXN",
                }
            ],
            versions=[{"id": "approved"}],
        )
    )
    assert len(loaded["budget_plan"]) == (1 if partial else 0)
    assert loaded["source_status"]["budget"]["available"]
    assert not loaded["source_status"]["budget"]["complete"]
    assert "Falta plan mensual" in loaded["source_status"]["budget"]["reason"]
    report = build_cashflow_report(
        loaded,
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        cutoff=date(2026, 1, 31),
        forecast_end=date(2026, 2, 1),
    )
    assert report["summary"]["planned_budget_income"] is None
    assert report["summary"]["planned_budget_expense"] is None


@pytest.mark.asyncio
async def test_budget_edition_is_independent_from_bank_calendar_year(monkeypatch):
    import samchat.ar.service as ar
    import samchat.budgets.service as budgets

    version_reader = AsyncMock(
        return_value=[
            {
                "id": "approved-2025",
                "status": "approved",
                "version_name": "Budget 2025",
                "edition_year": 2025,
            }
        ]
    )
    monkeypatch.setattr(budgets, "list_budget_versions", version_reader)
    monkeypatch.setattr(
        budgets, "get_budget_version", AsyncMock(return_value={"edition_year": 2025})
    )
    monkeypatch.setattr(budgets, "list_budget_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        budgets, "list_monthly_plan_for_lines", AsyncMock(return_value={})
    )
    monkeypatch.setattr(ar, "build_ar_read_model", AsyncMock(return_value={}))
    loaded = await load(
        SourceSession(versions=[{"id": "approved-2025"}]),
        edition_year=2025,
        budget_version_id="approved-2025",
    )
    assert loaded["budget_version_id"] == "approved-2025"
    assert loaded["budget_version_options"][0]["version_name"] == "Budget 2025"
    assert version_reader.await_args.kwargs["edition_year"] == 2025


@pytest.mark.asyncio
async def test_loader_declares_current_document_state_date():
    result = await load(SourceSession())
    assert result["state_as_of"] == date.today().isoformat()
