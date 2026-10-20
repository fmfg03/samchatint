"""Declared bank-only costs use the real full amount, never fiscal estimation."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_direct_accounting_service as accounting
from devnous.gastos.services import amex_direct_budget_service as budget
from devnous.gastos.services import expense_accounting_service as fiscal
from devnous.gastos.services.amex_direct_common import DirectConflict


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


@pytest.fixture
def world(monkeypatch):
    from devnous.gastos.services import amex_direct_no_cfdi_service as owner

    context_id, statement_id = uuid4(), uuid4()
    context = NS(id=context_id)
    statement = NS(id=statement_id)
    items, expenses, sources, heads = [], {}, {}, {}
    for _ in range(2):
        item = NS(
            id=uuid4(),
            import_id=statement_id,
            context_id=context_id,
            no_cfdi_decision_id=uuid4(),
            tournament_id=None,
            project_code="ADMIN",
            budget_version_id=None,
            budget_assignment_id=None,
            budget_line_id=None,
            budget_concept_id=uuid4(),
        )
        expense = NS(
            id=uuid4(),
            estado_gasto="activo",
            fecha=datetime(2026, 10, 1),
            proyecto="ADMIN",
        )
        items.append(item)
        expenses[expense.id] = expense
        sources[item.id] = NS(
            imported_expense_id=expense.id,
            raw_signed_amount=Decimal("-116.00"),
            source_json={"description": "Cargo sin comprobante"},
        )
        heads[item.no_cfdi_decision_id] = NS(
            id=item.no_cfdi_decision_id,
            item_id=item.id,
            import_id=item.import_id,
            action="declare",
        )

    async def execute(query):
        sql = str(query)
        if "FROM amex_direct_items" in sql:
            return Rows(items)
        if "FROM amex_statement_imports" in sql:
            return Rows([statement])
        if "FROM amex_statement_direct_contexts" in sql:
            return Rows([context])
        raise AssertionError("unexpected source query")

    async def get(model, identity):
        if model.__name__ == "AmexStatementDirectState":
            return NS(context_id=context_id)
        if model.__name__ == "ExpenseReport":
            return expenses[identity]
        raise AssertionError("unexpected source model")

    session = NS(
        scalar=AsyncMock(return_value=True),
        execute=AsyncMock(side_effect=execute),
        get=AsyncMock(side_effect=get),
    )
    monkeypatch.setattr(budget, "configured_scopes", lambda: [{}])
    monkeypatch.setattr(
        budget,
        "effective_source_for_item",
        AsyncMock(side_effect=lambda session, item: sources[item.id]),
    )
    monkeypatch.setattr(
        accounting, "covered_source_proof", AsyncMock(return_value=None)
    )
    matched = AsyncMock(return_value=None)
    monkeypatch.setattr(accounting, "validate_direct_match", matched)
    preview = AsyncMock(return_value={"taxes": {"base_gasto": "100.00"}})
    monkeypatch.setattr(fiscal, "build_expense_accounting_preview", preview)
    loaded = AsyncMock(return_value=heads)
    active = Mock(side_effect=lambda item, values: values[item.no_cfdi_decision_id])
    validate = Mock(return_value={})
    monkeypatch.setattr(owner, "load_decision_heads", loaded)
    monkeypatch.setattr(owner, "active_decision", active)
    monkeypatch.setattr(owner, "validate_decision", validate)
    return NS(
        session=session,
        items=items,
        heads=heads,
        loaded=loaded,
        active=active,
        validate=validate,
        matched=matched,
        preview=preview,
    )


@pytest.mark.asyncio
async def test_no_cfdi_cost_is_full_bank_amount_and_loads_context_in_batches(world):
    rows = await budget.list_budget_direct_movements(world.session, 2026, None, None)
    assert len(rows) == 2
    assert all(row["amount"] == Decimal("116.00") for row in rows)
    assert all(row["kind"] == "commitment" and row["reason"] is None for row in rows)
    assert len({row["movement_key"] for row in rows}) == 2
    assert all(row["source_kind"] == "amex_direct" for row in rows)
    world.loaded.assert_awaited_once()
    assert world.session.execute.await_count == 3
    assert world.validate.call_count == 2
    world.matched.assert_not_awaited()
    world.preview.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_declaration_keeps_amount_unknown_without_tax_fallback(world):
    world.validate.side_effect = DirectConflict("no_cfdi_source_changed")
    rows = await budget.list_budget_direct_movements(world.session, 2026, None, None)
    assert all(row["amount"] is None for row in rows)
    assert all(row["kind"] == "pending_fiscal_evidence" for row in rows)
    assert all(row["reason"] == "no_cfdi_source_changed" for row in rows)
    world.matched.assert_not_awaited()
    world.preview.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_historical_head", [False, True])
async def test_revoked_or_absent_decision_preserves_cfdi_fiscal_base(
    world, has_historical_head
):
    world.active.side_effect = None
    world.active.return_value = None
    if not has_historical_head:
        for item in world.items:
            item.no_cfdi_decision_id = None
    rows = await budget.list_budget_direct_movements(world.session, 2026, None, None)
    assert all(row["amount"] == Decimal("100.00") for row in rows)
    assert world.matched.await_count == 2
    assert world.preview.await_count == 2
    world.validate.assert_not_called()
    assert world.session.execute.await_count == (3 if has_historical_head else 1)
