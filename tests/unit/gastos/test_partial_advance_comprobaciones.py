"""Business regression: partial approvals post only new expenses, never twice."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles

from devnous.gastos.models import (
    AccountingPoliza,
    AccountingPolizaLine,
    Adjunto,
    Aprobacion,
    Base,
    CuentaContable,
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
)
from devnous.gastos.routes import user_routes
from devnous.gastos.services import documento_workflow_service as workflow
from devnous.gastos.services import employee_debtor_accounting_service as accounting
from devnous.gastos.services.cuenta_settlement_service import (
    CuentaSettlementValidationError,
    register_cuenta_settlement,
)
from devnous.gastos.services.partial_advance_service import (
    PartialAdvanceError,
    finalize_partial_advance,
    submit_partial_advance_lot,
)


@pytest.mark.asyncio
async def test_web_partial_form_shows_only_new_expenses_and_history(case):
    first = await add_expense(case, "13912.50")
    controls = dict(
        cuenta=case.cuenta,
        informe_doc=case.original,
        monto_entregado=32370,
        total_amex=0,
        is_owner=True,
        can_manage=True,
        cerrar_informe_form_html="ordinary-close",
    )
    actions, history, close = await user_routes._build_partial_comprobacion_controls(
        case.session, active_expenses=[first], **controls
    )
    assert 'name="motivo" required' in actions
    assert str(first.id) in actions
    assert "13912.50" in actions
    lot = await submit(case, [first])
    assert not user_routes._can_edit_cuenta_before_budget_assignment(
        case.cuenta, case.original
    )
    assert (
        "Abierto · comprobación parcial"
        in user_routes._render_informe_operational_status_badge(
            partial_mode=True, cuenta_estado="abierta"
        )
    )
    assert (
        "Comprobado y cerrado"
        in user_routes._render_informe_operational_status_badge(
            partial_mode=True, cuenta_estado="cerrada"
        )
    )
    second = await add_expense(case, "4000")
    actions, history, close = await user_routes._build_partial_comprobacion_controls(
        case.session, active_expenses=[first, second], **controls
    )
    assert str(first.id) not in actions
    assert str(second.id) in actions
    assert lot.numero_referencia in history
    assert "Cerrar informe saldado" in close
    controls.update(is_owner=False, can_manage=False)
    actions, history, close = await user_routes._build_partial_comprobacion_controls(
        case.session, active_expenses=[second], **controls
    )
    assert not actions and not close
    assert lot.numero_referencia in history


@pytest.mark.asyncio
async def test_partial_web_submission_validates_preview_and_owner(case):
    expense = await add_expense(case, "13912.50")
    arguments = dict(
        cuenta_id=case.cuenta.id,
        request=Request({"type": "http"}),
        session=case.session,
        current_empleado=case.requester,
        submission_id=str(uuid4()),
        expense_ids=str(expense.id),
        expected_total="13912.50",
        motivo="Faltan comprobantes restantes",
    )
    response = await user_routes.enviar_comprobacion_parcial(**arguments)
    assert response.status_code == 303
    assert "comprobacion_enviada" in response.headers["location"]
    response = await user_routes.enviar_comprobacion_parcial(**arguments)
    assert "comprobacion_enviada" in response.headers["location"]
    arguments.update(expected_total="NaN")
    response = await user_routes.enviar_comprobacion_parcial(**arguments)
    assert "error_msg" in response.headers["location"]
    await case.session.refresh(case.outsider)
    arguments.update(current_empleado=case.outsider, expected_total="13912.50")
    response = await user_routes.enviar_comprobacion_parcial(**arguments)
    assert "error_msg" in response.headers["location"]


@pytest.mark.asyncio
async def test_partial_web_closure_is_zero_only_and_audited_once(case):
    expense = await add_expense(case, "13912.50")
    lot = await submit(case, [expense])
    await approve(case, lot)
    arguments = dict(
        cuenta_id=case.cuenta.id,
        request=Request({"type": "http"}),
        session=case.session,
        current_empleado=case.requester,
        next=None,
        proveedor_cliente_id=None,
    )
    response = await user_routes.cerrar_cuenta_de_gastos(**arguments)
    assert "error_msg" in response.headers["location"]
    # Rollback expires ORM instances; reload before the next transaction.
    await case.session.refresh(case.cuenta)
    await case.session.refresh(case.original)
    await case.session.refresh(case.requester)
    await return_amount(case, "18457.50")
    await case.session.commit()
    response = await user_routes.cerrar_cuenta_de_gastos(**arguments)
    assert "success=cerrada" in response.headers["location"]
    assert case.cuenta.estado == "cerrada"
    assert case.original.estado == "cerrado"
    response = await user_routes.cerrar_cuenta_de_gastos(**arguments)
    assert "error_msg" in response.headers["location"]
    approvals = (
        (
            await case.session.execute(
                select(Aprobacion).where(
                    Aprobacion.accion == "cerrar_comprobacion_parcial"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(approvals) == 1


@pytest.mark.asyncio
async def test_approved_lot_cannot_be_edited_or_reclassified_even_by_finance(case):
    from devnous.gastos.services.expense_accounting_cleanup_service import (
        save_expense_cleanup,
    )

    expense = await add_expense(case, "13912.50")
    lot = await submit(case, [expense])
    await user_routes._ensure_can_mutate_informe_expense(
        case.session, expense, case.requester
    )
    await approve(case, lot)
    with pytest.raises(HTTPException) as error:
        await user_routes._ensure_can_mutate_informe_expense(
            case.session, expense, case.approver
        )
    assert error.value.status_code == 409
    with pytest.raises(ValueError, match="reversión"):
        await save_expense_cleanup(case.session, expense.id, iva=100)
    assert expense.gasto_cantidad == 13912.50


@pytest.mark.asyncio
async def test_telegram_context_reports_lot_amount_instead_of_full_advance(case):
    from devnous.gastos.services.documento_telegram import (
        build_documento_telegram_context,
    )

    expense = await add_expense(case, "13912.50")
    lot = await submit(case, [expense])
    lot.empleado = case.requester
    context = await build_documento_telegram_context(case.session, lot)
    assert "13,912.50" in context["monto_line"]
    assert context["saldo_line"] is None
    assert context["solicitante"] == "Carlos Lozano"


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kw):
    return "JSON"


@pytest_asyncio.fixture
async def case(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            text("CREATE TABLE documento_authorization_routes (documento_id TEXT)")
        )
    async with AsyncSession(engine, expire_on_commit=False) as session:
        requester = Empleado(id=uuid4(), nombre="Carlos Lozano", rol="empleado")
        approver = Empleado(id=uuid4(), nombre="Finanzas", rol="finanzas")
        outsider = Empleado(id=uuid4(), nombre="Otro empleado", rol="empleado")
        debtor = CuentaContable(
            id=uuid4(), codigo="1170-001-003", nombre=requester.nombre, tipo="deudor"
        )
        bank = CuentaContable(
            id=uuid4(), codigo="1120-001-001", nombre="Santander", tipo="banco"
        )
        expense_account = CuentaContable(
            id=uuid4(),
            codigo="6000-001-001",
            nombre="Sin requisitos fiscales",
            tipo="gasto",
        )
        cuenta = CuentaDeGastos(
            id=uuid4(),
            empleado_id=requester.id,
            referencia_base="575016",
            estado="abierta",
            tipo_cuenta="local",
        )
        original = Documento(
            id=uuid4(),
            empleado_id=requester.id,
            tipo="INFORME",
            numero_referencia="I-575016",
            estado="borrador",
            cuenta_gastos_id=cuenta.id,
        )
        advance = Documento(
            id=uuid4(),
            empleado_id=requester.id,
            tipo="SOLICITUD",
            numero_referencia="S-575016",
            estado="pagado",
            cuenta_gastos_id=cuenta.id,
            beneficiario_empleado_id=requester.id,
            monto_solicitado=Decimal("32370"),
        )
        session.add_all(
            [
                requester,
                approver,
                outsider,
                debtor,
                bank,
                expense_account,
                cuenta,
                original,
                advance,
            ]
        )
        await session.flush()
        from devnous.gastos.utils import receipt_bytes

        monkeypatch.setattr(
            receipt_bytes,
            "get_adjunto_columns",
            AsyncMock(return_value={c.name for c in Adjunto.__table__.columns}),
        )
        # These are external notification/audit/route surfaces, not accounting.
        monkeypatch.setattr(
            workflow, "record_customer_success_audit_event", AsyncMock()
        )
        monkeypatch.setattr(
            workflow, "prepare_document_authorization_route", AsyncMock()
        )
        monkeypatch.setattr(workflow, "invalidate_document_route", AsyncMock())
        monkeypatch.setattr(
            workflow, "documento_requires_budget_control", lambda _: False
        )
        from devnous.gastos.services import (
            authorization_profile_service,
            documento_telegram,
        )

        monkeypatch.setattr(
            authorization_profile_service,
            "build_authorization_route_hard_block",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            authorization_profile_service,
            "build_authorization_route_soft_warning",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            documento_telegram,
            "schedule_document_workflow_telegram_notifications",
            lambda **_: None,
        )
        monkeypatch.setattr(
            documento_telegram,
            "schedule_budget_control_telegram_notifications",
            lambda **_: None,
        )

        async def preview(_session, expense, **_kwargs):
            # No taxes in this fixture. The canonical journal builder remains real.
            return {
                "taxes": {
                    "base_gasto": expense.gasto_cantidad,
                    "neto_contrapartida": expense.gasto_cantidad,
                }
            }

        monkeypatch.setattr(accounting, "build_expense_accounting_preview", preview)
        result = await accounting.ensure_debtor_payment_posting_for_document(
            session,
            documento=advance,
            empleado=requester,
            fecha_pago=datetime(2026, 10, 1),
        )
        assert result.status == "created"
        await session.commit()
        ctx = SimpleNamespace(
            session=session,
            requester=requester,
            approver=approver,
            outsider=outsider,
            debtor=debtor,
            expense_account=expense_account,
            cuenta=cuenta,
            original=original,
        )

        async def auxiliary(_session, *, cuenta_id):
            # SQLite lacks PostgreSQL's JSONB @>; use the same persisted ledger
            # rows to verify final closure, never an assumed expected balance.
            lines = list(
                (await session.execute(select(AccountingPolizaLine))).scalars().all()
            )
            lines = [
                line
                for line in lines
                if line.raw_row_json.get("cuenta_gastos_id") == str(cuenta_id)
                and line.cuenta_codigo == debtor.codigo
            ]
            return {
                "lines": lines,
                "saldo": round(sum(line.debe - line.haber for line in lines), 2),
            }

        monkeypatch.setattr(accounting, "build_cuenta_debtor_auxiliary", auxiliary)
        yield ctx
    await engine.dispose()


async def add_expense(case, amount):
    expense = ExpenseReport(
        id=uuid4(),
        empleado_id=case.requester.id,
        proyecto="Prueba",
        concepto="Gasto comprobado",
        gasto_cantidad=float(amount),
        cuenta_gastos_id=case.cuenta.id,
        documento_id=case.original.id,
        informe_documento_id=case.original.id,
        cuenta_contable_id=case.expense_account.id,
        estado_gasto="activo",
        origen="manual",
        metodo_pago="Efectivo",
    )
    case.session.add(expense)
    await case.session.commit()
    return expense


async def submit(case, expenses, **kwargs):
    return await submit_partial_advance_lot(
        case.session,
        cuenta_id=case.cuenta.id,
        actor=case.requester,
        submission_id=kwargs.pop("submission_id", uuid4()),
        expected_expense_ids={e.id for e in expenses},
        expected_total=sum(
            (Decimal(str(e.gasto_cantidad)) for e in expenses), Decimal(0)
        ),
        motivo=kwargs.pop("motivo", "Faltan comprobantes de los gastos restantes."),
        **kwargs,
    )


async def approve(
    case, lot, comment="Revisé el motivo y autorizo únicamente este lote."
):
    return await workflow.transition_documento_workflow(
        case.session,
        documento_id=lot.id,
        actor_id=case.approver.id,
        action="approve",
        comentario=comment,
    )


async def debtor_balance(case):
    aux = await accounting.build_cuenta_debtor_auxiliary(
        case.session, cuenta_id=case.cuenta.id
    )
    return Decimal(str(aux["saldo"]))


async def return_amount(case, amount, key=None):
    return await register_cuenta_settlement(
        case.session,
        cuenta_id=case.cuenta.id,
        actor_id=case.requester.id,
        monto=amount,
        allow_partial=True,
        client_submission_id=key or uuid4(),
        fecha_pago="2026-10-06",
        comprobante_bytes=b"%PDF-1.4 test",
        comprobante_filename="devolucion.pdf",
        comprobante_mime="application/pdf",
    )


@pytest.mark.asyncio
async def test_carlos_second_comprobacion_and_final_closure_never_duplicate(case):
    first = await add_expense(case, "13912.50")
    key = uuid4()
    lot1 = await submit(case, [first], submission_id=key)
    assert await debtor_balance(case) == Decimal("32370")  # Sent is not posted.
    assert (await submit(case, [first], submission_id=key)).id == lot1.id
    await approve(case, lot1)
    assert await debtor_balance(case) == Decimal("18457.5")
    return_key = uuid4()
    returned = await return_amount(case, "9200", return_key)
    assert returned.saldo_after == 9257.5
    assert (
        await return_amount(case, "9200", return_key)
    ).reembolso.id == returned.reembolso.id
    assert await debtor_balance(case) == Decimal("9257.5")
    assert case.cuenta.estado == "abierta" and case.original.estado == "borrador"
    second = await add_expense(case, "4000")
    lot2 = await submit(case, [second])
    assert lot2.id != lot1.id
    await approve(case, lot2)
    assert await debtor_balance(case) == Decimal("5257.5")
    with pytest.raises(PartialAdvanceError):
        await finalize_partial_advance(
            case.session, cuenta=case.cuenta, informe=case.original
        )
    repeat = await accounting.ensure_debtor_comprobacion_posting_for_informe(
        case.session, informe_documento=lot2
    )
    assert repeat.status == "exists"
    assert await debtor_balance(case) == Decimal("5257.5")
    await return_amount(case, "5257.50")
    assert await debtor_balance(case) == 0
    await finalize_partial_advance(
        case.session, cuenta=case.cuenta, informe=case.original
    )
    await case.session.commit()
    assert case.cuenta.estado == "cerrada" and case.original.estado == "cerrado"
    policies = list(
        (
            await case.session.execute(
                select(AccountingPoliza).where(
                    AccountingPoliza.origen == "deudores_comprobacion"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(policies) == 2
    credits = list(
        (
            await case.session.execute(
                select(AccountingPolizaLine).where(
                    AccountingPolizaLine.cuenta_codigo == case.debtor.codigo
                )
            )
        )
        .scalars()
        .all()
    )
    expense_ids = [
        line.raw_row_json["expense_id"]
        for line in credits
        if "expense_id" in line.raw_row_json
    ]
    assert sorted(expense_ids) == sorted([str(first.id), str(second.id)])
    approvals = list(
        (
            await case.session.execute(
                select(Aprobacion).where(Aprobacion.accion == "aprobar")
            )
        )
        .scalars()
        .all()
    )
    assert len(approvals) == 2 and all(a.comentario for a in approvals)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["", "   ", "x" * 2001])
async def test_reason_required_before_any_lot_is_created(case, reason):
    expense = await add_expense(case, "100")
    with pytest.raises(PartialAdvanceError):
        await submit(case, [expense], motivo=reason)
    assert expense.informe_documento_id == case.original.id
    assert not case.cuenta.comprobacion_parcial


@pytest.mark.asyncio
async def test_approver_comment_required_and_unprivileged_actor_cannot_approve(case):
    expense = await add_expense(case, "100")
    lot = await submit(case, [expense])
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as failure:
        await approve(case, lot, " ")
    assert failure.value.code == "partial_approval_comment_required"
    assert await debtor_balance(case) == Decimal("32370")
    with pytest.raises(workflow.DocumentoWorkflowPermissionError):
        await workflow.transition_documento_workflow(
            case.session,
            documento_id=lot.id,
            actor_id=case.outsider.id,
            action="approve",
            comentario="No autorizado",
        )
    assert await debtor_balance(case) == Decimal("32370")


@pytest.mark.asyncio
async def test_stale_preview_and_over_return_do_not_write(case):
    expense = await add_expense(case, "13912.50")
    with pytest.raises(PartialAdvanceError, match="cambiaron"):
        await submit_partial_advance_lot(
            case.session,
            cuenta_id=case.cuenta.id,
            actor=case.requester,
            submission_id=uuid4(),
            expected_expense_ids=set(),
            expected_total=Decimal("13912.50"),
            motivo="Falta comprobar el resto",
        )
    with pytest.raises(CuentaSettlementValidationError):
        await return_amount(case, "20000")
    assert await debtor_balance(case) == Decimal("32370")


@pytest.mark.asyncio
async def test_rejection_keeps_case_open_and_requires_new_approval(case):
    expense = await add_expense(case, "100")
    lot = await submit(case, [expense])
    await workflow.transition_documento_workflow(
        case.session,
        documento_id=lot.id,
        actor_id=case.approver.id,
        action="reject",
        comentario="Corregir comprobante",
    )
    assert lot.estado == "borrador" and case.cuenta.estado == "abierta"
    assert await debtor_balance(case) == Decimal("32370")
    await workflow.transition_documento_workflow(
        case.session, documento_id=lot.id, actor_id=case.requester.id, action="send"
    )
    await approve(case, lot)
    assert await debtor_balance(case) == Decimal("32270")


@pytest.mark.asyncio
async def test_new_lot_enters_budget_control_and_cannot_be_approved_early(
    case, monkeypatch
):
    monkeypatch.setattr(workflow, "documento_requires_budget_control", lambda _: True)
    expense = await add_expense(case, "100")
    lot = await submit(case, [expense])
    assert lot.estado == "control_presupuestal"
    with pytest.raises(workflow.DocumentoWorkflowValidationError, match="enviado"):
        await approve(case, lot)
    assert await debtor_balance(case) == Decimal("32370")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem", ["closed", "approved", "unpaid", "amex", "missing_advance_policy"]
)
async def test_ineligible_original_case_cannot_be_converted(case, problem):
    expense = await add_expense(case, "100")
    if problem == "closed":
        case.cuenta.estado = "cerrada"
    elif problem == "approved":
        case.original.estado = "aprobado"
    elif problem == "unpaid":
        advance = (
            await case.session.execute(
                select(Documento).where(Documento.tipo == "SOLICITUD")
            )
        ).scalar_one()
        advance.estado = "aprobado"
    elif problem == "amex":
        expense.pagado_con_amex_empresa = True
    else:
        from sqlalchemy import delete

        await case.session.execute(delete(AccountingPolizaLine))
        await case.session.execute(delete(AccountingPoliza))
    await case.session.commit()
    with pytest.raises(PartialAdvanceError):
        await submit(case, [expense])
    assert expense.informe_documento_id == case.original.id


@pytest.mark.asyncio
async def test_no_new_expenses_cannot_make_an_empty_or_duplicate_lot(case):
    expense = await add_expense(case, "100")
    await submit(case, [expense])
    with pytest.raises(PartialAdvanceError, match="gastos nuevos"):
        await submit(case, [])


@pytest.mark.asyncio
async def test_original_cannot_be_approved_as_a_second_aggregate_policy(case):
    expense = await add_expense(case, "100")
    await submit(case, [expense])
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as failure:
        await workflow.transition_documento_workflow(
            case.session,
            documento_id=case.original.id,
            actor_id=case.requester.id,
            action="send",
        )
    assert failure.value.code == "partial_lot_required"


@pytest.mark.asyncio
async def test_only_requester_can_confirm_the_reason(case):
    expense = await add_expense(case, "100")
    with pytest.raises(PartialAdvanceError, match="solicitante"):
        await submit_partial_advance_lot(
            case.session,
            cuenta_id=case.cuenta.id,
            actor=case.approver,
            submission_id=uuid4(),
            expected_expense_ids={expense.id},
            expected_total=Decimal("100"),
            motivo="Motivo de otra persona",
        )


@pytest.mark.asyncio
async def test_missing_reason_or_closed_origin_blocks_lot_approval(case):
    expense = await add_expense(case, "100")
    lot = await submit(case, [expense])
    reason = lot.motivo_comprobacion_parcial
    lot.motivo_comprobacion_parcial = ""
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as failure:
        await approve(case, lot)
    assert failure.value.code == "partial_reason_required"
    lot.motivo_comprobacion_parcial = reason
    case.cuenta.estado = "cerrada"
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as failure:
        await approve(case, lot)
    assert failure.value.code == "invalid_partial_case"


@pytest.mark.asyncio
async def test_incomplete_accounting_does_not_create_an_approved_lot(case):
    expense = await add_expense(case, "100")
    expense.cuenta_contable_id = None
    expense.cuenta_contable = None
    await case.session.commit()
    lot = await submit(case, [expense])
    with pytest.raises(workflow.DocumentoWorkflowValidationError) as failure:
        await approve(case, lot)
    assert failure.value.code == "accounting_posting_pending"
    await case.session.rollback()
    policies = list(
        (
            await case.session.execute(
                select(AccountingPoliza).where(
                    AccountingPoliza.origen == "deudores_comprobacion"
                )
            )
        )
        .scalars()
        .all()
    )
    assert not policies
