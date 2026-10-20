"""Budget detail preserves missing fiscal evidence and the assignment owner."""

from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from devnous.gastos.routes.admin_budget_ui import _render_budget_movement_details
from devnous.gastos.services import amex_direct_budget_service as budget
from samchat.budgets import service as budget_owner


def test_direct_pending_base_is_not_zero_or_a_legacy_assignment_form():
    html = _render_budget_movement_details(
        [{"id": "line"}],
        [
            {
                "expense_id": "expense",
                "source_kind": "amex_direct",
                "concept_key": "category",
                "budget_line_id": "line",
                "kind": "pending_fiscal_evidence",
                "amount": None,
            }
        ],
        version_id="version",
        tournament_key="project",
        edition_year=2026,
        phase_filter=None,
        can_edit=True,
    )
    assert "Pendiente fiscal" in html
    assert "Base fiscal pendiente de evidencia" in html
    assert "$0.00" not in html
    assert "/documentos/control-presupuestal/amex-directo" in html
    assert "assign-existing" not in html


def test_evidenced_zero_stays_zero():
    html = _render_budget_movement_details(
        [],
        [{"concept_key": "category", "kind": "pending_accounting", "amount": 0}],
        version_id="version",
        tournament_key="project",
        edition_year=2026,
        phase_filter=None,
        can_edit=False,
    )
    assert "$0.00" in html


@pytest.fixture
def direct_budget_scope(monkeypatch):
    context_id, parent_id = uuid4(), uuid4()
    item = NS(
        id=uuid4(),
        budget_concept_id=uuid4(),
        tournament_id=uuid4(),
        project_code=None,
        edition_year=2026,
        context_id=context_id,
        source_version=1,
        source_hash="original",
        budget_status="sent",
        budget_line_id=None,
    )
    session = NS(
        no_autoflush=nullcontext(),
        get=AsyncMock(return_value=item),
        execute=AsyncMock(),
        flush=AsyncMock(),
        add=MagicMock(),
    )
    monkeypatch.setattr(
        budget, "resolve_item_import", AsyncMock(return_value=parent_id)
    )
    monkeypatch.setattr(
        budget,
        "load_statement",
        AsyncMock(return_value=(NS(), NS(source_version=1), NS(id=context_id), {})),
    )
    monkeypatch.setattr(budget, "_scope", MagicMock())
    monkeypatch.setattr(budget, "require_unfrozen", AsyncMock())
    expense = NS(id=uuid4())
    monkeypatch.setattr(
        budget,
        "effective_source_for_item",
        AsyncMock(return_value=NS(imported_expense_id=expense.id)),
    )
    monkeypatch.setattr(budget, "load_expenses", AsyncMock(return_value=[expense]))
    return NS(session=session, item=item)


def financial_line(item, *, status="approved", source="manual"):
    return {
        "id": uuid4(),
        "budget_concept_id": item.budget_concept_id,
        "tournament_id": item.tournament_id,
        "tournament_code": None,
        "edition_year": 2026,
        "line_direction": "expense",
        "version_status": status,
        "version_source": source,
        "version_name": "Presupuesto",
        "concept_name": "Gasto",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["approved", "frozen"])
async def test_direct_picker_offers_financial_roots_not_distribution_copies(
    direct_budget_scope, status
):
    w = direct_budget_scope
    root = financial_line(w.item, status=status)
    clone = financial_line(w.item, status=status, source="distribution_revision")
    w.session.execute.return_value = NS(mappings=lambda: NS(all=lambda: [root, clone]))
    result = await budget.list_direct_budget_lines(
        w.session, actor_id=uuid4(), item_id=w.item.id
    )
    assert [row["id"] for row in result] == [str(root["id"])]
    assert "v.source AS version_source" in str(w.session.execute.await_args.args[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["approved", "frozen"])
async def test_forged_distribution_assignment_rejected_before_budget_write(
    direct_budget_scope, monkeypatch, status
):
    w = direct_budget_scope
    clone = financial_line(w.item, status=status, source="distribution_revision")
    w.session.execute.side_effect = [
        NS(scalar_one_or_none=lambda: None),
        NS(mappings=lambda: NS(first=lambda: clone)),
    ]
    assign = AsyncMock()
    monkeypatch.setattr(budget_owner, "assign_budget_movement_to_line", assign)
    event = AsyncMock()
    monkeypatch.setattr(budget, "record_event", event)
    original = vars(w.item).copy()
    result = await budget.assign_direct_budget_line(
        w.session,
        actor_id=uuid4(),
        item_id=w.item.id,
        budget_line_id=clone["id"],
        expected_source_version=1,
        expected_source_hash="original",
        idempotency_key="forged-clone",
    )
    assert result.status == "pending"
    assert result.reason == "budget_line_context_mismatch"
    assign.assert_not_awaited()
    event.assert_not_awaited()
    w.session.flush.assert_not_awaited()
    w.session.add.assert_not_called()
    assert vars(w.item) == original
    line_sql = str(w.session.execute.await_args_list[1].args[0])
    assert "v.source AS version_source" in line_sql
    assert "FOR UPDATE OF l,v" in line_sql


@pytest.mark.parametrize(
    "status,allow_draft,allowed",
    [
        ("approved", False, True),
        ("frozen", False, True),
        ("draft", False, False),
        ("reforecast", False, False),
        ("draft", True, True),
        ("reforecast", True, True),
        ("submitted", True, False),
        ("closed", True, False),
    ],
)
def test_ordinary_direct_budget_status_policy_unchanged(
    direct_budget_scope, status, allow_draft, allowed
):
    item = direct_budget_scope.item
    line = financial_line(item, status=status)
    assert (
        budget._line_matches(item, line, {"allow_draft_reforecast": allow_draft})
        is allowed
    )
    line["version_source"] = "distribution_revision"
    assert not budget._line_matches(item, line, {"allow_draft_reforecast": allow_draft})
