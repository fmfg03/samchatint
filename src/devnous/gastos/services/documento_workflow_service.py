from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Aprobacion, CuentaDeGastos, Documento, Empleado, ExpenseReport
from ..utils.mexico_city_dates import utc_now
from .payment_schedule_service import assign_fecha_pago_on_solicitud_approval
from .customer_success_audit import (
    AuditRequestContext,
    record_customer_success_audit_event,
)

logger = logging.getLogger(__name__)

APPROVER_ROLES = {"finanzas", "admin", "superadmin", "super_admin"}
FINANCE_ADMIN_ROLES = {"finanzas", "admin"}


class DocumentoWorkflowError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DocumentoWorkflowPermissionError(DocumentoWorkflowError):
    pass


class DocumentoWorkflowValidationError(DocumentoWorkflowError):
    pass


@dataclass(slots=True)
class DocumentoWorkflowResult:
    documento: Documento
    aprobacion: Aprobacion


async def _load_documento(
    session: AsyncSession, documento_id: UUID
) -> Optional[Documento]:
    result = await session.execute(
        select(Documento)
        .options(selectinload(Documento.empleado))
        .where(Documento.id == documento_id)
    )
    return result.scalar_one_or_none()


async def _load_actor(session: AsyncSession, actor_id: UUID) -> Optional[Empleado]:
    return await session.get(Empleado, actor_id)


async def _informe_documento_for_cuenta(
    session: AsyncSession, cuenta_gastos_id: UUID
) -> Optional[Documento]:
    result = await session.execute(
        select(Documento)
        .where(
            Documento.cuenta_gastos_id == cuenta_gastos_id,
            Documento.tipo == "INFORME",
        )
        .order_by(Documento.creado_en.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _solicitud_linked_informe_is_approved(
    session: AsyncSession, documento: Documento
) -> bool:
    if documento.tipo != "SOLICITUD" or documento.cuenta_gastos_id is None:
        return False
    informe = await _informe_documento_for_cuenta(session, documento.cuenta_gastos_id)
    return informe is not None and informe.estado == "aprobado"


async def _linked_informe_approval_actor_id(
    session: AsyncSession, documento: Documento
) -> Optional[UUID]:
    if documento.tipo != "SOLICITUD" or documento.cuenta_gastos_id is None:
        return None
    informe = await _informe_documento_for_cuenta(session, documento.cuenta_gastos_id)
    if informe is None or informe.estado != "aprobado":
        return None

    result = await session.execute(
        select(Aprobacion.aprobador_id)
        .where(
            Aprobacion.tipo_entidad == "documento",
            Aprobacion.entidad_id == informe.id,
            Aprobacion.accion == "aprobar",
        )
        .order_by(Aprobacion.fecha.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _auto_approve_solicitud_with_approved_informe(
    *,
    documento: Documento,
    aprobador_id: UUID,
    now: datetime,
    comentario: Optional[str] = None,
) -> Aprobacion:
    documento.estado = "aprobado"
    documento.aprobado_en = now
    assign_fecha_pago_on_solicitud_approval(documento)
    return Aprobacion(
        tipo_entidad="documento",
        entidad_id=documento.id,
        aprobador_id=aprobador_id,
        accion="aprobar",
        comentario=(
            comentario
            or "Auto-aprobada: el informe de gastos vinculado ya estaba aprobado."
        ),
        fecha=now,
    )


async def promote_solicitudes_ready_for_payment(
    session: AsyncSession,
    *,
    actor_id: Optional[UUID | str] = None,
) -> int:
    """Promote enviado solicitudes when their linked informe is approved."""
    result = await session.execute(
        select(Documento)
        .where(
            Documento.tipo == "SOLICITUD",
            Documento.estado == "enviado",
            Documento.cuenta_gastos_id.isnot(None),
        )
    )
    solicitudes = result.scalars().all()
    if not solicitudes:
        return 0

    del actor_id  # retained for API compatibility; approvals inherit informe actor

    now = utc_now()
    promoted = 0
    promoted_ids: list[UUID] = []
    for documento in solicitudes:
        aprobador_id = await _linked_informe_approval_actor_id(session, documento)
        if aprobador_id is None:
            continue
        session.add(
            _auto_approve_solicitud_with_approved_informe(
                documento=documento,
                aprobador_id=aprobador_id,
                now=now,
            )
        )
        promoted += 1
        promoted_ids.append(documento.id)

    if promoted:
        await session.commit()
        try:
            from .documento_telegram import (
                schedule_solicitud_pending_payment_telegram_notifications,
            )

            for documento_id in promoted_ids:
                schedule_solicitud_pending_payment_telegram_notifications(
                    documento_id=str(documento_id),
                )
        except Exception:
            logger.exception(
                "Failed to schedule Finance pending payment Telegram notifications"
            )
    return promoted


async def _count_active_document_expenses(
    session: AsyncSession,
    *,
    documento_id: UUID,
    cuenta_gastos_id: Optional[UUID] = None,
) -> int:
    expense_links = [
        ExpenseReport.documento_id == documento_id,
        ExpenseReport.informe_documento_id == documento_id,
    ]
    if cuenta_gastos_id:
        expense_links.append(ExpenseReport.cuenta_gastos_id == cuenta_gastos_id)
    result = await session.execute(
        select(func.count(ExpenseReport.id)).where(
            and_(
                or_(*expense_links),
                ExpenseReport.estado_gasto != "cancelado",
            )
        )
    )
    return int(result.scalar_one() or 0)


async def _reopen_informe_de_gastos_on_reject(
    session: AsyncSession, documento: Documento
) -> None:
    """Reopen a rejected INFORME so the employee can edit and resubmit it.

    Closing an informe sets its Cuenta de Gastos to ``cerrada`` and sends the
    INFORME documento to approval. When that documento is rejected we return both
    to their editable state (cuenta ``abierta``, documento ``borrador``) so the
    existing close/edit flow can run again. The rejection itself stays recorded
    in the Aprobacion history. No-op for SOLICITUD or documents without a linked
    cuenta.
    """
    if documento.tipo != "INFORME" or documento.cuenta_gastos_id is None:
        return
    cuenta = await session.get(CuentaDeGastos, documento.cuenta_gastos_id)
    if cuenta is not None and cuenta.estado == "cerrada":
        cuenta.estado = "abierta"
        cuenta.closed_at = None
    documento.estado = "borrador"
    documento.enviado_en = None


async def transition_documento_workflow(
    session: AsyncSession,
    *,
    documento_id: UUID | str,
    actor_id: UUID | str,
    action: str,
    comentario: Optional[str] = None,
    surface: str = "web",
    request_context: Optional[AuditRequestContext] = None,
) -> DocumentoWorkflowResult:
    normalized_action = (action or "").strip().lower()
    if normalized_action not in {"send", "approve", "reject", "cancel", "withdraw"}:
        raise DocumentoWorkflowValidationError(
            "invalid_action",
            "action must be one of: send, approve, reject, cancel, withdraw",
        )

    documento_uuid = UUID(str(documento_id))
    actor_uuid = UUID(str(actor_id))

    documento = await _load_documento(session, documento_uuid)
    if documento is None:
        raise DocumentoWorkflowValidationError(
            "documento_not_found", "Documento not found"
        )

    actor = await _load_actor(session, actor_uuid)
    if actor is None:
        raise DocumentoWorkflowValidationError("actor_not_found", "Actor not found")

    now = utc_now()
    comentario_normalizado = (comentario or "").strip() or None
    auto_aprobacion: Optional[Aprobacion] = None

    if normalized_action == "send":
        if documento.empleado_id != actor.id:
            raise DocumentoWorkflowPermissionError("owner_mismatch", "Access denied")
        if documento.estado != "borrador":
            raise DocumentoWorkflowValidationError(
                "invalid_estado",
                "El documento solo puede enviarse cuando está en estado 'borrador'.",
            )
        if documento.tipo == "INFORME":
            gastos_count = await _count_active_document_expenses(
                session,
                documento_id=documento_uuid,
                cuenta_gastos_id=documento.cuenta_gastos_id,
            )
            if gastos_count == 0:
                raise DocumentoWorkflowValidationError(
                    "no_gastos",
                    "El documento tipo INFORME debe tener al menos un gasto activo "
                    "antes de poder enviarse.",
                )
        documento.estado = "enviado"
        documento.enviado_en = now
        aprobacion_accion = "enviar"
        informe_aprobador_id = await _linked_informe_approval_actor_id(
            session, documento
        )
        if informe_aprobador_id is not None:
            auto_aprobacion = _auto_approve_solicitud_with_approved_informe(
                documento=documento,
                aprobador_id=informe_aprobador_id,
                now=now,
            )

    elif normalized_action == "approve":
        if documento.estado != "enviado":
            raise DocumentoWorkflowValidationError(
                "invalid_estado",
                "El documento solo puede aprobarse cuando está en estado 'enviado'.",
            )
        if actor.rol not in APPROVER_ROLES:
            raise DocumentoWorkflowPermissionError(
                "insufficient_role",
                "Access denied. Insufficient permissions.",
            )
        propietario = documento.empleado
        if propietario is not None and propietario.aprobador_id:
            es_aprobador_asignado = propietario.aprobador_id == actor.id
            es_finanzas_o_admin = actor.rol in FINANCE_ADMIN_ROLES
            if not (es_aprobador_asignado or es_finanzas_o_admin):
                raise DocumentoWorkflowValidationError(
                    "not_assigned_approver",
                    "No eres el aprobador asignado para este empleado. Contacta a "
                    "finanzas o administración.",
                )
        if documento.tipo == "INFORME":
            gastos_count = await _count_active_document_expenses(
                session,
                documento_id=documento_uuid,
                cuenta_gastos_id=documento.cuenta_gastos_id,
            )
            if gastos_count == 0:
                raise DocumentoWorkflowValidationError(
                    "no_gastos",
                    "El documento tipo INFORME debe tener al menos un gasto activo "
                    "antes de poder aprobarse.",
                )
        documento.estado = "aprobado"
        documento.aprobado_en = now
        assign_fecha_pago_on_solicitud_approval(documento)
        aprobacion_accion = "aprobar"

    elif normalized_action == "reject":
        if documento.estado != "enviado":
            raise DocumentoWorkflowValidationError(
                "invalid_estado",
                "El documento solo puede rechazarse cuando está en estado 'enviado'.",
            )
        if actor.rol not in APPROVER_ROLES:
            raise DocumentoWorkflowPermissionError(
                "insufficient_role",
                "Access denied. Insufficient permissions.",
            )
        propietario = documento.empleado
        if propietario is not None and propietario.aprobador_id:
            es_aprobador_asignado = propietario.aprobador_id == actor.id
            es_finanzas_o_admin = actor.rol in FINANCE_ADMIN_ROLES
            if not (es_aprobador_asignado or es_finanzas_o_admin):
                raise DocumentoWorkflowValidationError(
                    "not_assigned_approver",
                    "No eres el aprobador asignado para este empleado. Contacta a "
                    "finanzas o administración.",
                )
        documento.estado = "rechazado"
        aprobacion_accion = "rechazar"
        await _reopen_informe_de_gastos_on_reject(session, documento)

    elif normalized_action == "withdraw":
        if documento.tipo != "SOLICITUD":
            raise DocumentoWorkflowValidationError(
                "invalid_tipo",
                "Solo las solicitudes pueden retirarse desde este flujo.",
            )
        if documento.empleado_id != actor.id:
            raise DocumentoWorkflowPermissionError(
                "owner_mismatch", "Access denied"
            )
        if documento.estado != "enviado":
            raise DocumentoWorkflowValidationError(
                "invalid_estado",
                "La solicitud solo puede retirarse mientras está en revisión.",
            )
        documento.estado = "borrador"
        documento.enviado_en = None
        aprobacion_accion = "editar"
        if comentario_normalizado:
            comentario_normalizado = (
                f"Retirada por el solicitante: {comentario_normalizado}"
            )
        else:
            comentario_normalizado = "Retirada por el solicitante para edición."

    else:
        if documento.tipo != "SOLICITUD":
            raise DocumentoWorkflowValidationError(
                "invalid_tipo",
                "Solo las solicitudes pueden cancelarse desde este flujo.",
            )
        if documento.empleado_id != actor.id:
            raise DocumentoWorkflowPermissionError("owner_mismatch", "Access denied")
        if documento.estado != "borrador":
            raise DocumentoWorkflowValidationError(
                "invalid_estado",
                "La solicitud solo puede cancelarse en borrador.",
            )
        documento.estado = "rechazado"
        aprobacion_accion = "cancelar"

    aprobacion = Aprobacion(
        tipo_entidad="documento",
        entidad_id=documento_uuid,
        aprobador_id=actor.id,
        accion=aprobacion_accion,
        comentario=comentario_normalizado,
        fecha=now,
    )
    session.add(aprobacion)
    if auto_aprobacion is not None:
        session.add(auto_aprobacion)
    await session.commit()
    await session.refresh(documento)
    await session.refresh(aprobacion)

    action_labels = {
        "send": "documento.sent",
        "approve": "documento.approved",
        "reject": "documento.rejected",
        "cancel": "documento.cancelled",
        "withdraw": "documento.withdrawn",
    }
    await record_customer_success_audit_event(
        session,
        action=action_labels.get(normalized_action, f"documento.{normalized_action}"),
        actor_empleado_id=actor.id,
        target_empleado_id=documento.empleado_id,
        documento_id=documento.id,
        documento_referencia=documento.numero_referencia,
        entity_type="documento",
        entity_id=documento.id,
        surface=surface,
        request_context=request_context,
        summary=(
            f"{actor.nombre} ejecutó {normalized_action} sobre "
            f"{documento.numero_referencia}"
        ),
        metadata={
            "documento_tipo": documento.tipo,
            "documento_estado": documento.estado,
            "aprobacion_id": str(aprobacion.id),
            "comentario": comentario_normalizado,
            "auto_approved": auto_aprobacion is not None,
        },
        commit=True,
    )

    try:
        from .documento_telegram import (
            schedule_document_workflow_telegram_notifications,
        )

        schedule_document_workflow_telegram_notifications(
            documento_id=str(documento_uuid),
            action=normalized_action,
            actor_id=str(actor.id),
            comentario=comentario_normalizado,
        )
    except Exception:
        logger.exception(
            "Failed to schedule Telegram notifications for document workflow"
        )

    return DocumentoWorkflowResult(documento=documento, aprobacion=aprobacion)
