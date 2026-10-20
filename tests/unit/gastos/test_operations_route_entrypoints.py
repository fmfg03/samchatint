"""Entry-point behavior for Operations folios after closing/classifying reports."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import user_routes


@pytest.mark.asyncio
@pytest.mark.parametrize("classified", [False, True])
async def test_close_prepares_route_without_skipping_budget_control(
    monkeypatch, classified
):
    report = SimpleNamespace(
        id=uuid4(),
        estado="borrador",
        tipo="INFORME",
        budget_concept_id=uuid4() if classified else None,
        enviado_en=None,
    )
    account = SimpleNamespace(id=uuid4())
    actor = SimpleNamespace(id=uuid4())
    session = SimpleNamespace(add=lambda value: None)
    monkeypatch.setattr(
        user_routes, "_count_active_cuenta_expenses", AsyncMock(return_value=1)
    )
    monkeypatch.setattr(
        user_routes, "validate_informe_surplus_before_submission", AsyncMock()
    )
    monkeypatch.setattr(user_routes, "reserve_documento_cfdis_or_raise", AsyncMock())
    monkeypatch.setattr(
        user_routes,
        "_informe_has_unassigned_budget_lines",
        AsyncMock(return_value=not classified),
    )
    prepare = AsyncMock()
    monkeypatch.setattr(user_routes, "prepare_document_authorization_route", prepare)
    assert await user_routes._sync_informe_documento_to_enviado(
        session, cuenta=account, informe_doc=report, actor=actor
    )
    prepare.assert_awaited_once_with(session, report)
    assert report.estado == ("enviado" if classified else "control_presupuestal")
    assert bool(report.enviado_en) == classified


@pytest.mark.asyncio
async def test_closing_an_already_submitted_report_does_not_regenerate_folio(
    monkeypatch,
):
    report = SimpleNamespace(estado="enviado", referencia_operaciones="190")
    prepare = AsyncMock()
    monkeypatch.setattr(user_routes, "prepare_document_authorization_route", prepare)
    assert not await user_routes._sync_informe_documento_to_enviado(
        SimpleNamespace(),
        cuenta=SimpleNamespace(),
        informe_doc=report,
        actor=SimpleNamespace(),
    )
    prepare.assert_not_awaited()
    assert report.referencia_operaciones == "190"


@pytest.mark.asyncio
async def test_classification_prepares_route_before_returning_for_notification(
    monkeypatch,
):
    concept_id = uuid4()
    report = SimpleNamespace(
        id=uuid4(),
        estado="control_presupuestal",
        tipo="INFORME",
        cuenta_gastos=None,
        cuenta_gastos_id=None,
        torneo_id=None,
        fase=None,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: report)
        ),
        add=lambda value: None,
    )
    monkeypatch.setattr(
        user_routes,
        "resolve_budget_concept",
        AsyncMock(return_value={"id": concept_id, "concept_name": "Food"}),
    )
    monkeypatch.setattr(
        user_routes, "validate_informe_surplus_before_submission", AsyncMock()
    )
    prepare = AsyncMock()
    monkeypatch.setattr(user_routes, "prepare_document_authorization_route", prepare)
    result, _ = await user_routes._apply_control_presupuestal_assignment(
        session,
        documento_id=report.id,
        budget_concept_id=str(concept_id),
        actor=SimpleNamespace(id=uuid4()),
    )
    assert result.estado == "enviado"
    assert result.budget_concept_id == concept_id
    prepare.assert_awaited_once_with(session, report)


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_expense_classification_prepares_route_only_when_report_is_complete(
    monkeypatch, complete
):
    concept_id = uuid4()
    report = SimpleNamespace(
        id=uuid4(),
        estado="control_presupuestal",
        tipo="INFORME",
        cuenta_gastos=None,
        cuenta_gastos_id=None,
        torneo_id=None,
        fase=None,
    )
    expense = SimpleNamespace(
        id=uuid4(), numero_referencia="G-1", budget_concept_id=None
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=expense),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: report)),
        add=lambda value: None,
    )
    monkeypatch.setattr(
        user_routes, "_informe_documento_for_expense", AsyncMock(return_value=report)
    )
    monkeypatch.setattr(
        user_routes,
        "resolve_budget_concept",
        AsyncMock(return_value={"id": concept_id, "concept_name": "Food"}),
    )
    monkeypatch.setattr(
        user_routes, "validate_informe_surplus_before_submission", AsyncMock()
    )
    monkeypatch.setattr(
        user_routes,
        "_informe_budget_assignment_complete",
        AsyncMock(return_value=complete),
    )
    prepare = AsyncMock()
    monkeypatch.setattr(user_routes, "prepare_document_authorization_route", prepare)
    result, _, released = (
        await user_routes._apply_control_presupuestal_expense_assignment(
            session,
            expense_id=expense.id,
            budget_concept_id=str(concept_id),
            actor=SimpleNamespace(id=uuid4()),
        )
    )
    assert released == complete
    assert result.estado == ("enviado" if complete else "control_presupuestal")
    assert prepare.await_count == int(complete)
