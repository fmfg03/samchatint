from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Aprobacion, Documento, Empleado, Reembolso, Tournament
from .cfdi_expense_link_service import link_expense_to_cfdi_if_manual_uuid_set
from .expense_service import create_expense_from_data

logger = logging.getLogger(__name__)


FINANCE_ROLES = {"finanzas", "admin", "superadmin", "super_admin"}
REIMBURSEMENT_ROLES = {"finanzas", "admin", "superadmin", "super_admin"}


class DocumentoPaymentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DocumentoPaymentPermissionError(DocumentoPaymentError):
    pass


class DocumentoPaymentValidationError(DocumentoPaymentError):
    pass


@dataclass(slots=True)
class DocumentoPagoResult:
    documento: Documento
    aprobacion: Aprobacion
    expense: Any | None = None


@dataclass(slots=True)
class DocumentoReembolsoResult:
    documento: Documento
    reembolso: Reembolso


def _to_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


async def _load_documento_for_payment(
    session: AsyncSession,
    documento_id: UUID,
) -> Optional[Documento]:
    result = await session.execute(
        select(Documento)
        .options(
            selectinload(Documento.empleado),
            selectinload(Documento.proveedor_cliente),
            selectinload(Documento.beneficiario_empleado),
            selectinload(Documento.torneo),
        )
        .where(Documento.id == documento_id)
    )
    return result.scalar_one_or_none()


async def _load_documento_basic(
    session: AsyncSession,
    documento_id: UUID,
) -> Optional[Documento]:
    result = await session.execute(select(Documento).where(Documento.id == documento_id))
    return result.scalar_one_or_none()


async def _load_actor(session: AsyncSession, actor_id: UUID) -> Optional[Empleado]:
    return await session.get(Empleado, actor_id)


def _schedule_solicitud_paid_telegram_notifications(
    *,
    documento_id: UUID,
    actor_id: UUID,
) -> None:
    try:
        from .documento_telegram import schedule_solicitud_payment_telegram_notifications

        schedule_solicitud_payment_telegram_notifications(
            documento_id=str(documento_id),
            actor_id=str(actor_id),
        )
    except Exception:
        logger.exception(
            "Failed to schedule Telegram notifications for solicitud payment"
        )


async def register_document_payment(
    session: AsyncSession,
    *,
    documento_id: UUID | str,
    actor_id: UUID | str,
) -> DocumentoPagoResult:
    documento_uuid = _to_uuid(documento_id)
    actor_uuid = _to_uuid(actor_id)

    documento = await _load_documento_for_payment(session, documento_uuid)
    if documento is None:
        raise DocumentoPaymentValidationError("documento_not_found", "Documento not found")

    actor = await _load_actor(session, actor_uuid)
    if actor is None:
        raise DocumentoPaymentValidationError("actor_not_found", "Actor not found")
    if (actor.rol or "").strip().lower() not in FINANCE_ROLES:
        raise DocumentoPaymentPermissionError(
            "insufficient_role",
            "Access denied. Insufficient permissions.",
        )

    if documento.tipo != "SOLICITUD":
        raise DocumentoPaymentValidationError(
            "invalid_tipo",
            "Solo se pueden registrar pagos en documentos de tipo SOLICITUD.",
        )
    if documento.estado != "aprobado":
        raise DocumentoPaymentValidationError(
            "invalid_estado",
            "El documento debe estar aprobado antes de registrar el pago.",
        )
    if documento.gasto_generado_id:
        raise DocumentoPaymentValidationError(
            "already_paid",
            "Este documento ya tiene un gasto generado. No se puede registrar el pago nuevamente.",
        )
    if not documento.monto_solicitado or documento.monto_solicitado <= 0:
        raise DocumentoPaymentValidationError(
            "invalid_monto",
            "El documento debe tener un monto solicitado válido.",
        )

    has_proveedor = documento.proveedor_cliente_id is not None
    has_beneficiario = documento.beneficiario_empleado_id is not None
    if not has_proveedor and not has_beneficiario:
        raise DocumentoPaymentValidationError(
            "missing_beneficiary",
            "El documento debe tener un proveedor/cliente o un beneficiario empleado asociado.",
        )
    if has_proveedor and has_beneficiario:
        if documento.cuenta_gastos_id:
            documento.estado = "pagado"
            documento.pagado_en = datetime.utcnow()
            aprobacion = Aprobacion(
                tipo_entidad="documento",
                entidad_id=documento.id,
                aprobador_id=actor.id,
                accion="pagar",
                comentario="Solicitud de transferencia marcada como pagada.",
                fecha=datetime.utcnow(),
            )
            session.add(aprobacion)
            await session.commit()
            await session.refresh(documento)
            await session.refresh(aprobacion)
            _schedule_solicitud_paid_telegram_notifications(
                documento_id=documento.id,
                actor_id=actor.id,
            )
            return DocumentoPagoResult(
                documento=documento,
                aprobacion=aprobacion,
                expense=None,
            )
        raise DocumentoPaymentValidationError(
            "ambiguous_beneficiary",
            "El documento no puede tener tanto proveedor/cliente como beneficiario empleado. Debe tener exactamente uno.",
        )

    empleado = documento.empleado
    if not empleado:
        raise DocumentoPaymentValidationError(
            "missing_empleado",
            "No se encontró el empleado asociado al documento.",
        )

    beneficiario_empleado = None
    if has_beneficiario:
        beneficiario_empleado = documento.beneficiario_empleado
        if not beneficiario_empleado:
            raise DocumentoPaymentValidationError(
                "missing_beneficiario",
                "No se encontró el empleado beneficiario asociado al documento.",
            )

    proyecto = ""
    if documento.torneo:
        proyecto = documento.torneo.name
    elif documento.torneo_id:
        torneo_result = await session.execute(
            select(Tournament).where(Tournament.id == documento.torneo_id)
        )
        torneo = torneo_result.scalar_one_or_none()
        if torneo:
            proyecto = torneo.name

    fecha_pago = documento.fecha_pago if documento.fecha_pago else date.today()
    fecha_pago_dt = datetime.combine(fecha_pago, datetime.min.time())
    metodo_pago = documento.metodo_pago if documento.metodo_pago else "TRANSFERENCIA"

    if has_proveedor:
        proveedor_nombre = (
            documento.proveedor_cliente.nombre if documento.proveedor_cliente else "Proveedor"
        )
        concepto = (
            documento.concepto_pago
            if documento.concepto_pago
            else "Pago de solicitud de transferencia"
        )
        concepto_full = f"Pago a proveedor: {proveedor_nombre} - {concepto}"
        expense = await create_expense_from_data(
            session=session,
            concepto=concepto_full,
            gasto_cantidad=float(
                Decimal(str(documento.monto_solicitado)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            ),
            fecha=fecha_pago_dt,
            empleado_id=documento.empleado_id,
            proyecto=proyecto,
            tipo_gasto="manual",
            nombre_enviador=empleado.nombre,
            metodo_pago=metodo_pago,
            origen="solicitud_terceros",
            departamento=(
                empleado.departamento
                if hasattr(empleado, "departamento") and empleado.departamento
                else "Operaciones"
            ),
            tournament_id=str(documento.torneo_id) if documento.torneo_id else None,
            categorias=list(getattr(documento, "categorias", None) or []),
            edicion=getattr(documento, "edicion", None),
            currency=getattr(documento, "currency", None) or "MXN",
            budget_concept_id=getattr(documento, "budget_concept_id", None),
        )
        flow_type = "terceros"
    else:
        concepto = (
            documento.concepto_pago if documento.concepto_pago else "Reembolso personal"
        )
        concepto_full = f"Reembolso personal - {concepto}"
        expense = await create_expense_from_data(
            session=session,
            concepto=concepto_full,
            gasto_cantidad=float(
                Decimal(str(documento.monto_solicitado)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            ),
            fecha=fecha_pago_dt,
            empleado_id=beneficiario_empleado.id,
            proyecto=proyecto,
            tipo_gasto="manual",
            nombre_enviador=beneficiario_empleado.nombre,
            metodo_pago=metodo_pago,
            origen="solicitud_personal",
            departamento=(
                beneficiario_empleado.departamento
                if hasattr(beneficiario_empleado, "departamento")
                and beneficiario_empleado.departamento
                else "Operaciones"
            ),
            tournament_id=str(documento.torneo_id) if documento.torneo_id else None,
            categorias=list(getattr(documento, "categorias", None) or []),
            edicion=getattr(documento, "edicion", None),
            currency=getattr(documento, "currency", None) or "MXN",
            budget_concept_id=getattr(documento, "budget_concept_id", None),
        )
        flow_type = "personal"

    expense.documento_id = documento.id
    documento.estado = "pagado"
    documento.pagado_en = datetime.utcnow()
    documento.gasto_generado_id = expense.id

    # Propagate CFDI capture from the solicitud (if any) to the generated expense so the
    # standard cfdi_uuid_manual → cfdi_report_id linking pipeline can auto-match when the
    # CFDI appears (Tocino webhook, CFDI CSV import, or later direct load).
    if getattr(documento, "cfdi_uuid_manual", None):
        expense.cfdi_uuid_manual = documento.cfdi_uuid_manual
        if documento.cfdi_report_id and not expense.cfdi_report_id:
            expense.cfdi_report_id = documento.cfdi_report_id
        await link_expense_to_cfdi_if_manual_uuid_set(
            session, expense, clear_report_if_no_match=False
        )
        if expense.cfdi_report_id and not documento.cfdi_report_id:
            documento.cfdi_report_id = expense.cfdi_report_id

    aprobacion = Aprobacion(
        tipo_entidad="documento",
        entidad_id=documento.id,
        aprobador_id=actor.id,
        accion="pagar",
        comentario=(
            f"Pago registrado y gasto generado automáticamente ({flow_type}). "
            f"Gasto: {expense.numero_referencia}"
        ),
        fecha=datetime.utcnow(),
    )
    session.add(aprobacion)
    await session.commit()
    await session.refresh(documento)
    await session.refresh(aprobacion)
    _schedule_solicitud_paid_telegram_notifications(
        documento_id=documento.id,
        actor_id=actor.id,
    )
    return DocumentoPagoResult(documento=documento, aprobacion=aprobacion, expense=expense)


async def register_document_reembolso(
    session: AsyncSession,
    *,
    documento_id: UUID | str,
    actor_id: UUID | str,
    monto: float | str,
    moneda: str,
    metodo_pago: Optional[str] = None,
    fecha_pago: Optional[str] = None,
    estado: str = "pagado",
) -> DocumentoReembolsoResult:
    documento_uuid = _to_uuid(documento_id)
    actor_uuid = _to_uuid(actor_id)

    documento = await _load_documento_basic(session, documento_uuid)
    if documento is None:
        raise DocumentoPaymentValidationError("documento_not_found", "Documento not found")

    actor = await _load_actor(session, actor_uuid)
    if actor is None:
        raise DocumentoPaymentValidationError("actor_not_found", "Actor not found")

    if documento.empleado_id != actor.id and (actor.rol or "").strip().lower() not in REIMBURSEMENT_ROLES:
        raise DocumentoPaymentPermissionError("access_denied", "Acceso denegado")

    if documento.tipo != "INFORME":
        raise DocumentoPaymentValidationError(
            "invalid_tipo",
            "Solo se pueden registrar reembolsos en documentos de tipo INFORME.",
        )
    if documento.estado != "aprobado":
        raise DocumentoPaymentValidationError(
            "invalid_estado",
            "El documento debe estar aprobado antes de registrar un reembolso.",
        )

    try:
        monto_decimal = float(monto)
    except ValueError as exc:
        raise DocumentoPaymentValidationError(
            "invalid_monto",
            "El monto ingresado no es válido.",
        ) from exc
    if monto_decimal <= 0:
        raise DocumentoPaymentValidationError(
            "invalid_monto",
            "El monto debe ser mayor a cero.",
        )

    fecha_pago_dt = None
    if fecha_pago:
        try:
            fecha_pago_dt = datetime.strptime(fecha_pago, "%Y-%m-%d")
        except ValueError:
            fecha_pago_dt = None

    if documento.monto_total is not None and documento.monto_total > 0:
        total_reembolsos_result = await session.execute(
            select(func.coalesce(func.sum(Reembolso.monto), 0)).where(
                Reembolso.documento_id == documento_uuid
            )
        )
        total_reembolsos_existentes = float(total_reembolsos_result.scalar_one() or 0)
        if total_reembolsos_existentes + monto_decimal > float(documento.monto_total):
            raise DocumentoPaymentValidationError(
                "exceso_monto",
                "El total de reembolsos excede el monto total del documento.",
            )

    reembolso = Reembolso(
        empleado_id=documento.empleado_id,
        documento_id=documento_uuid,
        monto=monto_decimal,
        moneda=moneda,
        metodo_pago=metodo_pago.strip() if metodo_pago else None,
        fecha_pago=fecha_pago_dt,
        estado=estado,
        tipo="reembolso",
        creado_en=datetime.utcnow(),
    )
    session.add(reembolso)

    # Note: v1.0.24 intentionally stops flipping `documento.estado` from here. Settlement
    # direction and saldo awareness now live in `cuenta_settlement_service`, and the INFORME
    # documento estado is driven exclusively by its own workflow. This legacy path remains
    # only for data-only backfill on pre-cuenta INFORMEs and is not exposed in the UI.

    await session.commit()
    await session.refresh(documento)
    await session.refresh(reembolso)
    return DocumentoReembolsoResult(documento=documento, reembolso=reembolso)


async def get_pending_document_payment_overview(
    session: AsyncSession,
    *,
    actor_id: UUID | str,
) -> Dict[str, Any]:
    actor_uuid = _to_uuid(actor_id)
    actor = await _load_actor(session, actor_uuid)
    if actor is None:
        raise DocumentoPaymentValidationError("actor_not_found", "Actor not found")
    if (actor.rol or "").strip().lower() not in FINANCE_ROLES:
        raise DocumentoPaymentPermissionError(
            "insufficient_role",
            "Access denied. Insufficient permissions.",
        )

    result = await session.execute(
        select(Documento)
        .options(
            selectinload(Documento.empleado),
            selectinload(Documento.proveedor_cliente),
            selectinload(Documento.beneficiario_empleado),
        )
        .where(and_(Documento.estado == "aprobado", Documento.tipo == "SOLICITUD"))
        .order_by(Documento.aprobado_en.desc().nulls_last(), Documento.creado_en.desc())
    )
    documentos = result.scalars().all()

    rows = []
    total_pendiente = 0.0
    solicitud_terceros = 0
    solicitud_personal = 0
    for documento in documentos:
        monto = float(documento.monto_total or documento.monto_solicitado or 0)
        total_pendiente += monto
        tipo_solicitud = "—"
        beneficiario_nombre = "—"
        if documento.proveedor_cliente_id and documento.proveedor_cliente:
            tipo_solicitud = "Terceros"
            beneficiario_nombre = documento.proveedor_cliente.nombre
            solicitud_terceros += 1
        elif documento.beneficiario_empleado_id and documento.beneficiario_empleado:
            tipo_solicitud = "Personal"
            beneficiario_nombre = documento.beneficiario_empleado.nombre
            solicitud_personal += 1
        rows.append(
            {
                "documento_id": str(documento.id),
                "numero_referencia": documento.numero_referencia,
                "empleado_nombre": documento.empleado.nombre if documento.empleado else None,
                "tipo": documento.tipo,
                "tipo_solicitud": tipo_solicitud,
                "beneficiario_nombre": beneficiario_nombre,
                "estado": documento.estado,
                "monto_pendiente": round(monto, 2),
                "aprobado_en": documento.aprobado_en.isoformat() if documento.aprobado_en else None,
            }
        )

    return {
        "summary": {
            "pending_count": len(rows),
            "total_pendiente": round(total_pendiente, 2),
            "solicitud_terceros": solicitud_terceros,
            "solicitud_personal": solicitud_personal,
        },
        "documentos": rows,
    }
