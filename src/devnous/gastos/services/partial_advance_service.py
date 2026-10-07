"""Partial paid-advance comprobaciones reuse canonical document authorization.

The cuenta/primary INFORME remains the case. Each lot is an INFORME with its
own immutable approval and accounting identity, linked by informe_origen_id.
Submission and its lot membership commit together through the canonical workflow.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from ..models import Aprobacion, CuentaDeGastos, Documento, Empleado, ExpenseReport
from .amex_expense_service import is_company_amex_expense
from .cuenta_settlement_service import (
    _sum_active_gastos,
    _sum_requested_solicitudes,
    sum_active_advance_returns,
)


class PartialAdvanceError(ValueError):
    pass


async def validate_partial_case(
    session: AsyncSession, cuenta: CuentaDeGastos, informe: Documento
) -> None:
    if cuenta.estado != "abierta" or informe.estado != "borrador":
        raise PartialAdvanceError(
            "La comprobación parcial requiere un informe abierto en borrador."
        )
    if await _sum_requested_solicitudes(session, cuenta.id) <= 0:
        raise PartialAdvanceError(
            "La comprobación parcial requiere un anticipo efectivamente pagado."
        )
    from .employee_debtor_accounting_service import (
        _event_poliza_number,
        _existing_event_poliza,
    )

    advances = (
        (
            await session.execute(
                select(Documento).where(
                    Documento.cuenta_gastos_id == cuenta.id,
                    Documento.tipo == "SOLICITUD",
                    Documento.estado == "pagado",
                )
            )
        )
        .scalars()
        .all()
    )
    for advance in advances:
        if (
            await _existing_event_poliza(
                session,
                origen="deudores_anticipo",
                numero_poliza=_event_poliza_number("DEU-PAY", advance.id),
                legacy_numero_poliza=f"DEU-PAG-{str(advance.id)[:8]}",
            )
            is None
        ):
            raise PartialAdvanceError(
                "El anticipo pagado no tiene póliza en el auxiliar; Contabilidad debe conciliarlo antes de habilitar comprobaciones parciales."
            )
    if (
        await session.execute(
            select(func.count(Aprobacion.id)).where(
                Aprobacion.entidad_id == informe.id,
                Aprobacion.tipo_entidad == "documento",
                Aprobacion.accion == "aprobar",
            )
        )
    ).scalar_one():
        raise PartialAdvanceError(
            "No se puede convertir un informe ya aprobado en comprobación parcial."
        )
    expenses = (
        (
            await session.execute(
                select(ExpenseReport).where(
                    ExpenseReport.cuenta_gastos_id == cuenta.id,
                    ExpenseReport.estado_gasto != "cancelado",
                )
            )
        )
        .scalars()
        .all()
    )
    if any(is_company_amex_expense(e) for e in expenses):
        raise PartialAdvanceError(
            "Los informes AMEX conservan su corte contable; no admiten lotes de anticipo."
        )


async def submit_partial_advance_lot(
    session: AsyncSession,
    *,
    cuenta_id: UUID,
    actor: Empleado,
    submission_id: UUID,
    expected_expense_ids: set[UUID],
    expected_total: Decimal,
    motivo: str,
) -> Documento:
    from .documento_workflow_service import transition_documento_workflow

    motivo = (motivo or "").strip()
    if not motivo or len(motivo) > 2000:
        raise PartialAdvanceError(
            "Explica el motivo de la comprobación parcial (máximo 2000 caracteres)."
        )
    cuenta = (
        await session.execute(
            select(CuentaDeGastos)
            .where(CuentaDeGastos.id == cuenta_id)
            .options(undefer(CuentaDeGastos.torneo_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if cuenta is None:
        raise PartialAdvanceError("Informe no encontrado.")
    if actor.id != cuenta.empleado_id:
        raise PartialAdvanceError(
            "El solicitante debe confirmar personalmente el motivo y enviar la comprobación parcial."
        )
    informe = (
        await session.execute(
            select(Documento)
            .where(Documento.cuenta_gastos_id == cuenta.id, Documento.tipo == "INFORME")
            .options(undefer(Documento.fase))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if informe is None:
        raise PartialAdvanceError("Falta el documento principal del informe.")
    existing = (
        await session.execute(
            select(Documento).where(
                Documento.informe_origen_id == informe.id,
                Documento.client_submission_id == submission_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    await validate_partial_case(session, cuenta, informe)
    # Explicitly assigned lines cannot be swept into a later approval/export.
    expenses = list(
        (
            await session.execute(
                select(ExpenseReport)
                .where(
                    ExpenseReport.cuenta_gastos_id == cuenta.id,
                    ExpenseReport.estado_gasto != "cancelado",
                    or_(
                        ExpenseReport.informe_documento_id.is_(None),
                        ExpenseReport.informe_documento_id == informe.id,
                    ),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if not expenses:
        raise PartialAdvanceError("No hay gastos nuevos para enviar a aprobación.")
    total = sum(
        (Decimal(str(e.gasto_cantidad or 0)) for e in expenses), Decimal(0)
    ).quantize(Decimal("0.01"))
    if {e.id for e in expenses} != expected_expense_ids or total != expected_total:
        raise PartialAdvanceError(
            "Los gastos cambiaron desde que abriste el informe; recarga y revisa el lote."
        )
    paid = await _sum_requested_solicitudes(session, cuenta.id)
    returned = await sum_active_advance_returns(session, cuenta.id)
    if await _sum_active_gastos(session, cuenta.id) + returned > paid:
        raise PartialAdvanceError(
            "La comprobación supera el anticipo disponible; revisa la devolución y el saldo."
        )
    count = (
        await session.execute(
            select(func.count(Documento.id)).where(
                Documento.informe_origen_id == informe.id
            )
        )
    ).scalar_one()
    lot = Documento(
        id=uuid4(),
        tipo="INFORME",
        estado="borrador",
        empleado_id=informe.empleado_id,
        numero_referencia=f"{informe.numero_referencia}-C{int(count) + 1}",
        informe_origen_id=informe.id,
        client_submission_id=submission_id,
        motivo_comprobacion_parcial=motivo,
        referencia_base=informe.referencia_base,
        referencia_operaciones=informe.referencia_operaciones,
        beneficiario_empleado_id=informe.beneficiario_empleado_id,
        beneficiario_proveedor_cliente_id=informe.beneficiario_proveedor_cliente_id,
        beneficiario_alterno_tipo=informe.beneficiario_alterno_tipo,
        torneo_id=informe.torneo_id,
        fase=informe.fase,
        categorias=informe.categorias,
        edicion=informe.edicion,
        currency=informe.currency,
        budget_concept_id=None,
        monto_total=sum(
            (Decimal(str(e.gasto_cantidad or 0)) for e in expenses), Decimal(0)
        ),
        notas=f"Comprobación parcial de {informe.numero_referencia}. El informe original permanece abierto.",
    )
    session.add(lot)
    await session.flush()
    for expense in expenses:
        expense.informe_documento_id = lot.id
        expense.documento_id = lot.id
    cuenta.comprobacion_parcial = True
    await session.flush()
    result = await transition_documento_workflow(
        session,
        documento_id=lot.id,
        actor_id=actor.id,
        action="send",
        comentario=f"Comprobación parcial de {informe.numero_referencia}",
    )
    return result.documento


async def finalize_partial_advance(
    session: AsyncSession, *, cuenta: CuentaDeGastos, informe: Documento
) -> None:
    from .employee_debtor_accounting_service import build_cuenta_debtor_auxiliary

    await session.execute(
        select(CuentaDeGastos).where(CuentaDeGastos.id == cuenta.id).with_for_update()
    )
    if not cuenta.comprobacion_parcial:
        raise PartialAdvanceError("El informe no utiliza comprobación parcial.")
    if cuenta.estado != "abierta" or informe.estado != "borrador":
        raise PartialAdvanceError(
            "El informe ya está cerrado o no admite cierre parcial."
        )
    pending = (
        await session.execute(
            select(func.count(Documento.id)).where(
                Documento.informe_origen_id == informe.id,
                Documento.estado != "aprobado",
            )
        )
    ).scalar_one()
    unbatched = (
        await session.execute(
            select(func.count(ExpenseReport.id)).where(
                ExpenseReport.cuenta_gastos_id == cuenta.id,
                ExpenseReport.estado_gasto != "cancelado",
                or_(
                    ExpenseReport.informe_documento_id.is_(None),
                    ExpenseReport.informe_documento_id == informe.id,
                ),
            )
        )
    ).scalar_one()
    auxiliary = await build_cuenta_debtor_auxiliary(session, cuenta_id=cuenta.id)
    captured_balance = (
        await _sum_requested_solicitudes(session, cuenta.id)
        - await _sum_active_gastos(session, cuenta.id)
        - await sum_active_advance_returns(session, cuenta.id)
    )
    if (
        pending
        or unbatched
        or captured_balance != 0
        or not auxiliary["lines"]
        or Decimal(str(auxiliary["saldo"])) != 0
    ):
        raise PartialAdvanceError(
            "Para cerrar: aprueba todos los lotes y comprueba o devuelve todo el saldo contable."
        )
    from ..utils.mexico_city_dates import utc_now

    cuenta.estado = "cerrada"
    cuenta.closed_at = utc_now()
    # Container closure is not another accounting approval or export.
    informe.estado = "cerrado"
