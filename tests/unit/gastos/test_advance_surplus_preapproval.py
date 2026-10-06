"""Paid advances must be returned before their expense report is submitted."""

from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import Headers, UploadFile

from devnous.gastos.routes import user_routes
from devnous.gastos.services import cuenta_settlement_service as settlement
from devnous.gastos.services import documento_workflow_service as workflow


def scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar_one.return_value = value
    return result


@pytest.fixture
def setup_return(monkeypatch):
    actor = SimpleNamespace(id=uuid4(), rol="empleado", nombre="Solicitante")
    cuenta = SimpleNamespace(
        id=uuid4(),
        empleado_id=actor.id,
        referencia_base="TEST",
        estado="abierta",
        comprobacion_parcial=False,
    )
    informe = SimpleNamespace(
        id=uuid4(), tipo="INFORME", estado="borrador", cuenta_gastos_id=cuenta.id
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=scalar_result(cuenta)),
        add=MagicMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        rollback=AsyncMock(),
    )
    monkeypatch.setattr(settlement, "_load_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        settlement, "_load_informe_documento", AsyncMock(return_value=informe)
    )
    monkeypatch.setattr(
        settlement, "_sum_active_gastos", AsyncMock(return_value=Decimal("800"))
    )
    monkeypatch.setattr(
        settlement,
        "_sum_requested_solicitudes",
        AsyncMock(return_value=Decimal("1000")),
    )
    monkeypatch.setattr(
        settlement, "_sum_active_settlements", AsyncMock(return_value=(Decimal(0), 0))
    )
    monkeypatch.setattr(
        settlement, "sum_active_advance_returns", AsyncMock(return_value=Decimal(200))
    )
    monkeypatch.setattr(settlement, "ensure_debtor_settlement_posting", AsyncMock())
    monkeypatch.setattr(settlement, "create_adjunto_record", AsyncMock())
    return session, actor, cuenta, informe


async def register(setup_return, **overrides):
    session, actor, cuenta, _ = setup_return
    values = dict(
        cuenta_id=cuenta.id,
        actor_id=actor.id,
        monto="200.00",
        fecha_pago="2026-09-30",
        comprobante_bytes=b"%PDF-1.4 test receipt",
        comprobante_filename="deposito.pdf",
        comprobante_mime="application/pdf",
    )
    values.update(overrides)
    return await settlement.register_cuenta_settlement(session, **values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["borrador", "control_presupuestal", "enviado", "aprobado"]
)
async def test_paid_advance_return_preserves_report_approval_state(setup_return, state):
    session, _, _, informe = setup_return
    informe.estado = state
    result = await register(setup_return)
    assert result.tipo == "devolucion"
    assert result.reembolso.monto == Decimal("200.00")
    assert result.reembolso.estado == "pagado"
    assert result.saldo_after == 0
    assert informe.estado == state
    session.commit.assert_awaited_once()
    settlement.ensure_debtor_settlement_posting.assert_awaited_once()
    settlement.create_adjunto_record.assert_awaited_once()
    assert "FOR UPDATE" in str(session.execute.call_args.args[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"monto": "199.99"}, "saldo_changed"),
        ({"saldo_snapshot": "201"}, "saldo_changed"),
        ({"comprobante_bytes": None}, "missing_comprobante"),
        ({"fecha_pago": None}, "missing_fecha_pago"),
        ({"fecha_pago": "   "}, "missing_fecha_pago"),
        ({"fecha_pago": "invalid"}, "invalid_fecha_pago"),
    ],
)
async def test_invalid_return_never_writes(setup_return, overrides, code):
    with pytest.raises(settlement.CuentaSettlementValidationError) as exc:
        await register(setup_return, **overrides)
    assert exc.value.code == code
    setup_return[0].add.assert_not_called()
    setup_return[0].commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_return_is_blocked(setup_return, monkeypatch):
    monkeypatch.setattr(
        settlement, "_sum_active_settlements", AsyncMock(return_value=(Decimal(200), 1))
    )
    with pytest.raises(settlement.CuentaSettlementValidationError) as exc:
        await register(setup_return)
    assert exc.value.code == "active_settlement_exists"
    setup_return[0].commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_owner_cannot_return_another_employees_advance(setup_return):
    setup_return[1].id = uuid4()
    with pytest.raises(settlement.CuentaSettlementPermissionError):
        await register(setup_return)
    setup_return[0].commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unpaid_advance_cannot_be_returned(setup_return, monkeypatch):
    monkeypatch.setattr(
        settlement, "_sum_requested_solicitudes", AsyncMock(return_value=Decimal(0))
    )
    # No money was delivered; this is a reimbursement direction, never a return.
    with pytest.raises(settlement.CuentaSettlementPermissionError):
        await register(setup_return)
    setup_return[0].commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("settled,blocked", [(0, True), (200, False)])
async def test_return_removes_submission_block_and_cancellation_restores_it(
    setup_return, monkeypatch, settled, blocked
):
    session, _, cuenta, _ = setup_return
    monkeypatch.setattr(
        settlement,
        "_sum_active_settlements",
        AsyncMock(return_value=(Decimal(settled), int(bool(settled)))),
    )
    if blocked:
        with pytest.raises(settlement.CuentaSettlementValidationError) as exc:
            await settlement.validate_cuenta_surplus_is_returned(session, cuenta.id)
        assert exc.value.code == "surplus_return_required"
    else:
        await settlement.validate_cuenta_surplus_is_returned(session, cuenta.id)


@pytest.mark.asyncio
async def test_cancellation_reopens_surplus_for_a_new_return(setup_return, monkeypatch):
    session, actor, cuenta, _ = setup_return
    actor.rol = "finanzas"
    old = SimpleNamespace(id=uuid4(), estado="pagado", monto=Decimal(200))
    session.execute.side_effect = [scalar_result(cuenta), scalar_result(old)]
    cancelled = await settlement.cancel_cuenta_settlement(
        session,
        cuenta_id=cuenta.id,
        reembolso_id=old.id,
        actor_id=actor.id,
        motivo="Depósito cancelado",
    )
    assert cancelled.estado == "cancelado"
    assert cancelled.cancelado_por_id == actor.id
    session.execute.side_effect = None
    session.execute.return_value = scalar_result(cuenta)
    with pytest.raises(settlement.CuentaSettlementValidationError) as exc:
        await settlement.validate_cuenta_surplus_is_returned(session, cuenta.id)
    assert exc.value.code == "surplus_return_required"
    result = await register(setup_return)
    assert result.tipo == "devolucion"


@pytest.mark.asyncio
async def test_closing_report_with_surplus_cannot_send_or_reserve_cfdis(
    setup_return, monkeypatch
):
    session, actor, cuenta, informe = setup_return
    monkeypatch.setattr(
        user_routes, "_count_active_cuenta_expenses", AsyncMock(return_value=1)
    )
    reserve = AsyncMock()
    monkeypatch.setattr(user_routes, "reserve_documento_cfdis_or_raise", reserve)
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as exc:
        await user_routes._sync_informe_documento_to_enviado(
            session, cuenta=cuenta, informe_doc=informe, actor=actor
        )
    assert exc.value.code == "surplus_return_required"
    assert informe.estado == "borrador"
    reserve.assert_not_awaited()
    session.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("action,state", [("send", "borrador"), ("approve", "enviado")])
async def test_document_workflow_cannot_bypass_pending_return(
    setup_return, monkeypatch, action, state
):
    session, actor, _, informe = setup_return
    informe.empleado_id = actor.id
    informe.estado = state
    monkeypatch.setattr(workflow, "_load_documento", AsyncMock(return_value=informe))
    monkeypatch.setattr(workflow, "_load_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        workflow, "documento_financial_terminal_reason", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        workflow, "documento_has_approval", AsyncMock(return_value=False)
    )
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as exc:
        await workflow.transition_documento_workflow(
            session, documento_id=informe.id, actor_id=actor.id, action=action
        )
    assert exc.value.code == "surplus_return_required"
    assert informe.estado == state
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "stale,active,saldo,copy",
    [
        (True, True, 0, "cancelar la devolución"),
        (False, True, 0, "Liquidación en curso"),
        (False, False, 0, "Saldado"),
        (False, False, 200, ""),
    ],
)
def test_stale_return_is_visibly_blocked_instead_of_labelled_settled(
    stale, active, saldo, copy
):
    label, html = user_routes._cuenta_settlement_status_html(
        stale_return=stale, has_active_settlement=active, saldo=saldo
    )
    if stale:
        assert label == "Devolución debe recalcularse"
        assert "Saldado" not in html
    assert copy in html


@pytest.mark.asyncio
@pytest.mark.parametrize("gastos,stale", [(800, False), (900, True), (1200, True)])
async def test_report_context_detects_changed_expenses_after_return(
    monkeypatch, gastos, stale
):
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[scalar_result(gastos), scalar_result(1000)]),
        get=AsyncMock(return_value=SimpleNamespace(comprobacion_parcial=False)),
    )
    monkeypatch.setattr(
        user_routes,
        "compute_cuenta_saldo_adjustments",
        AsyncMock(return_value=(200, 1)),
    )
    monkeypatch.setattr(
        user_routes, "sum_active_advance_returns", AsyncMock(return_value=Decimal(200))
    )
    context = await user_routes._compute_cuenta_saldo_context(session, uuid4())
    assert context["advance_return_stale"] is stale


@pytest.mark.asyncio
async def test_preapproval_devolution_is_not_a_terminal_informe_payment():
    informe = SimpleNamespace(
        id=uuid4(), tipo="INFORME", estado="borrador", cuenta_gastos_id=uuid4()
    )
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[scalar_result(0), scalar_result(0)])
    )
    assert await workflow.documento_financial_terminal_reason(session, informe) is None
    sql = str(
        session.execute.call_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "!= 'devolucion'" in sql


@pytest.mark.parametrize("state", ["borrador", "control_presupuestal", "enviado"])
def test_reimbursement_still_requires_approved_informe(state):
    with pytest.raises(settlement.CuentaSettlementValidationError):
        settlement.validate_settlement_eligibility(
            informe_estado=state, tipo="reembolso", monto_entregado=0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "surplus,active,state,visible",
    [
        (200, 0, "borrador", True),
        (0, 0, "borrador", False),
        (0, 1, "borrador", False),
        (200, 0, "cancelado", False),
    ],
)
async def test_return_form_is_available_before_approval_only_for_pending_surplus(
    setup_return, monkeypatch, surplus, active, state, visible
):
    session, actor, cuenta, informe = setup_return
    informe.estado = state
    monkeypatch.setattr(user_routes, "_can_access_reembolso_cuenta", lambda *_: True)
    monkeypatch.setattr(user_routes, "_can_submit_settlement", lambda *_: True)
    monkeypatch.setattr(
        user_routes, "_informe_documento_for_cuenta", AsyncMock(return_value=informe)
    )
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_: "")
    monkeypatch.setattr(user_routes, "_gastos_workspace_nav_html", lambda *_: "")
    monkeypatch.setattr(
        user_routes,
        "_compute_cuenta_saldo_context",
        AsyncMock(
            return_value={
                "tipo": "devolucion" if surplus else None,
                "saldo_raw": surplus,
                "active_settlement_count": active,
                "total_pagado_empleado": 800,
                "monto_entregado": 1000,
            }
        ),
    )
    html = await user_routes.saldar_cuenta_form(
        cuenta.id,
        request=SimpleNamespace(query_params={}),
        session=session,
        current_empleado=actor,
    )
    assert ('enctype="multipart/form-data"' in html) is visible
    if visible:
        assert 'name="monto"' in html
        assert 'max="200.00"' in html
        assert 'name="allow_partial" value="1"' in html
        assert 'name="fecha_pago"' in html
        assert 'name="comprobante"' in html


@pytest.mark.asyncio
async def test_read_only_user_cannot_open_return_form(setup_return, monkeypatch):
    session, actor, cuenta, _ = setup_return
    monkeypatch.setattr(user_routes, "_can_access_reembolso_cuenta", lambda *_: False)
    with pytest.raises(HTTPException) as exc:
        await user_routes.saldar_cuenta_form(
            cuenta.id,
            request=SimpleNamespace(query_params={}),
            session=session,
            current_empleado=actor,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_report_can_go_to_approval_after_return(setup_return, monkeypatch):
    monkeypatch.setattr(
        user_routes, "prepare_document_authorization_route", AsyncMock()
    )
    session, actor, cuenta, informe = setup_return
    informe.budget_concept_id = uuid4()
    monkeypatch.setattr(
        settlement, "_sum_active_settlements", AsyncMock(return_value=(Decimal(200), 1))
    )
    monkeypatch.setattr(
        user_routes, "_count_active_cuenta_expenses", AsyncMock(return_value=1)
    )
    monkeypatch.setattr(user_routes, "reserve_documento_cfdis_or_raise", AsyncMock())
    monkeypatch.setattr(
        user_routes,
        "_informe_has_unassigned_budget_lines",
        AsyncMock(return_value=False),
    )
    assert (
        await user_routes._sync_informe_documento_to_enviado(
            session, cuenta=cuenta, informe_doc=informe, actor=actor
        )
        is True
    )
    assert informe.estado == "enviado"
    assert session.add.call_args.args[0].accion == "enviar"


@pytest.mark.asyncio
async def test_return_post_registers_before_informe_approval(setup_return, monkeypatch):
    session, actor, cuenta, informe = setup_return
    monkeypatch.setattr(user_routes, "_can_access_reembolso_cuenta", lambda *_: True)
    monkeypatch.setattr(
        user_routes, "_informe_documento_for_cuenta", AsyncMock(return_value=informe)
    )
    receipt = UploadFile(
        filename="deposito.pdf",
        file=BytesIO(b"%PDF-1.4 test receipt"),
        headers=Headers({"content-type": "application/pdf"}),
    )
    response = await user_routes.saldar_cuenta_submit(
        cuenta.id,
        request=SimpleNamespace(),
        session=session,
        current_empleado=actor,
        monto="200",
        saldo_snapshot="200",
        moneda="MXN",
        metodo_pago="transferencia",
        fecha_pago="2026-09-30",
        referencia_pago="TEST",
        notas=None,
        comprobante=receipt,
    )
    assert response.status_code == 303
    assert "success=saldada" in response.headers["location"]
    assert "Devoluci" in response.headers["location"]
    assert informe.estado == "borrador"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reimbursement_balance_does_not_block_submission(
    setup_return, monkeypatch
):
    monkeypatch.setattr(
        settlement, "_sum_active_gastos", AsyncMock(return_value=Decimal(1200))
    )
    await settlement.validate_cuenta_surplus_is_returned(
        setup_return[0], setup_return[2].id
    )


@pytest.mark.asyncio
async def test_missing_account_blocks_submission(setup_return):
    setup_return[0].execute.return_value = scalar_result(None)
    with pytest.raises(settlement.CuentaSettlementValidationError) as exc:
        await settlement.validate_cuenta_surplus_is_returned(
            setup_return[0], setup_return[2].id
        )
    assert exc.value.code == "cuenta_not_found"


@pytest.mark.parametrize(
    "state,tipo,paid,code",
    [
        ("borrador", "devolucion", 0, "advance_not_paid"),
        ("cancelado", "devolucion", 1000, "informe_not_approved"),
        ("borrador", None, 1000, "saldo_zero"),
    ],
)
def test_ineligible_returns_are_blocked(state, tipo, paid, code):
    with pytest.raises(settlement.CuentaSettlementValidationError) as exc:
        settlement.validate_settlement_eligibility(
            informe_estado=state, tipo=tipo, monto_entregado=paid
        )
    assert exc.value.code == code


def test_finance_reimbursement_remains_available_after_approval():
    settlement.validate_settlement_eligibility(
        informe_estado="aprobado", tipo="reembolso", monto_entregado=0
    )


@pytest.mark.parametrize(
    "changes,visible",
    [
        ({}, True),
        ({"informe_estado": "control_presupuestal"}, True),
        ({"informe_estado": "enviado"}, True),
        ({"informe_estado": "aprobado"}, True),
        ({"informe_estado": "cancelado"}, False),
        ({"monto_entregado": 0}, False),
        ({"saldo": 0}, False),
        ({"saldo": -200}, False),
        ({"has_active_settlement": True}, False),
        ({"can_submit": False}, False),
    ],
)
def test_return_button_is_visible_during_comprobacion_only_when_eligible(
    changes, visible
):
    cuenta_id = uuid4()
    facts = dict(
        cuenta_id=cuenta_id,
        informe_estado="borrador",
        saldo=200,
        monto_entregado=1000,
        has_active_settlement=False,
        can_submit=True,
    )
    facts.update(changes)
    html = user_routes._devolucion_sobrante_action_html(**facts)
    assert bool(html) is visible
    if visible:
        assert "Registrar devolución de sobrantes" in html
        assert f"/informes-de-gastos/{cuenta_id}/saldar" in html


@pytest.mark.asyncio
async def test_other_document_types_do_not_use_report_surplus_guard():
    session = SimpleNamespace(execute=AsyncMock())
    await workflow.validate_informe_surplus_before_submission(
        session, SimpleNamespace(tipo="SOLICITUD")
    )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["register", "cancel"])
async def test_missing_actor_cannot_write_a_settlement(
    setup_return, monkeypatch, operation
):
    monkeypatch.setattr(settlement, "_load_actor", AsyncMock(return_value=None))
    with pytest.raises(settlement.CuentaSettlementValidationError) as exc:
        if operation == "register":
            await register(setup_return)
        else:
            await settlement.cancel_cuenta_settlement(
                setup_return[0],
                cuenta_id=setup_return[2].id,
                reembolso_id=uuid4(),
                actor_id=setup_return[1].id,
                motivo="Error",
            )
    assert exc.value.code == "actor_not_found"
    setup_return[0].commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_duplicate_rolls_back_without_creating_proof(setup_return):
    setup_return[0].flush.side_effect = IntegrityError(
        None, None, Exception("duplicate")
    )
    with pytest.raises(settlement.CuentaSettlementValidationError) as exc:
        await register(setup_return)
    assert exc.value.code == "active_settlement_exists"
    setup_return[0].rollback.assert_awaited_once()
    setup_return[0].commit.assert_not_awaited()
    settlement.create_adjunto_record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tipo,state", [("SOLICITUD", "borrador"), ("INFORME", "aprobado")]
)
async def test_terminal_financial_guard_is_preserved_for_other_paid_documents(
    tipo, state
):
    document = SimpleNamespace(
        id=uuid4(), tipo=tipo, estado=state, cuenta_gastos_id=uuid4()
    )
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[scalar_result(0), scalar_result(1)])
    )
    assert (
        await workflow.documento_financial_terminal_reason(session, document)
        == "reembolso/devolucion registrado"
    )
    sql = str(
        session.execute.call_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "!= 'devolucion'" not in sql


@pytest.mark.parametrize(
    "gross,returned,stale",
    [
        (200, 200, False),
        (100, 200, True),
        (300, 200, True),
        (-200, 200, True),
        (0, 200, True),
        (100, 0, False),
    ],
)
def test_changed_expenses_cannot_make_a_stale_return_look_settled(
    gross, returned, stale
):
    assert (
        settlement.advance_return_is_stale(saldo_gross=gross, returned_amount=returned)
        is stale
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("gastos", [700, 900, 1200])
async def test_submission_blocks_stale_return_after_expenses_change(
    setup_return, monkeypatch, gastos
):
    monkeypatch.setattr(
        settlement, "_sum_active_gastos", AsyncMock(return_value=Decimal(gastos))
    )
    monkeypatch.setattr(
        settlement, "_sum_active_settlements", AsyncMock(return_value=(Decimal(200), 1))
    )
    with pytest.raises(settlement.CuentaSettlementValidationError) as exc:
        await settlement.validate_cuenta_surplus_is_returned(
            setup_return[0], setup_return[2].id
        )
    assert exc.value.code == "settlement_balance_changed"
    assert "cancelar" in exc.value.message


@pytest.mark.asyncio
async def test_return_totals_select_only_non_cancelled_devoluciones():
    session = SimpleNamespace(
        execute=AsyncMock(return_value=scalar_result(Decimal("200")))
    )
    assert await settlement.sum_active_advance_returns(session, uuid4()) == Decimal(
        "200.00"
    )
    sql = str(
        session.execute.call_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "reembolsos.tipo = 'devolucion'" in sql
    assert "reembolsos.estado != 'cancelado'" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("expense_assignment", [False, True])
@pytest.mark.parametrize("returned", [False, True])
async def test_budget_release_cannot_bypass_surplus_guard(
    setup_return, monkeypatch, expense_assignment, returned
):
    monkeypatch.setattr(
        user_routes, "prepare_document_authorization_route", AsyncMock()
    )
    session, actor, cuenta, informe = setup_return
    informe.estado = "control_presupuestal"
    informe.cuenta_gastos = None
    informe.torneo_id = None
    informe.fase = None
    concept = uuid4()
    expense = SimpleNamespace(
        id=uuid4(),
        numero_referencia="TEST",
        budget_concept_id=None,
        cuenta_contable_id=None,
        contra_cuenta_contable_id=None,
    )
    session.get = AsyncMock(return_value=expense)
    session.execute.side_effect = [scalar_result(informe), scalar_result(cuenta)]
    monkeypatch.setattr(
        user_routes,
        "resolve_budget_concept",
        AsyncMock(return_value={"id": str(concept), "concept_name": "Transporte"}),
    )
    monkeypatch.setattr(
        user_routes, "_informe_documento_for_expense", AsyncMock(return_value=informe)
    )
    monkeypatch.setattr(
        user_routes, "_informe_budget_assignment_complete", AsyncMock(return_value=True)
    )
    if returned:
        monkeypatch.setattr(
            settlement,
            "_sum_active_settlements",
            AsyncMock(return_value=(Decimal(200), 1)),
        )

    async def release():
        if expense_assignment:
            return await user_routes._apply_control_presupuestal_expense_assignment(
                session,
                expense_id=expense.id,
                budget_concept_id=str(concept),
                actor=actor,
            )
        return await user_routes._apply_control_presupuestal_assignment(
            session,
            documento_id=informe.id,
            budget_concept_id=str(concept),
            actor=actor,
        )

    if returned:
        await release()
        assert informe.estado == "enviado"
    else:
        with pytest.raises(workflow.DocumentoWorkflowValidationError) as exc:
            await release()
        assert exc.value.code == "surplus_return_required"
        assert informe.estado == "control_presupuestal"
    session.commit.assert_not_awaited()
