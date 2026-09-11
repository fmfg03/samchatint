"""Route approved INFORME reimbursement requests into Payment Run."""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from ..models import (
    Aprobacion,
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
    ProveedorCliente,
)
from .amex_expense_service import (
    compute_informe_saldo,
    employee_paid_sql_condition,
)
from .cuenta_settlement_service import compute_cuenta_saldo_adjustments
from .documento_semantics import (
    effective_account_beneficiary_id,
    effective_account_provider_beneficiary_id,
    reimbursement_concept_from_cuenta,
)
from .documento_service import (
    SolicitudValidationError,
    build_solicitud_personal_payload,
    create_solicitud_personal_document,
)
from .documento_workflow_service import (
    approve_reimbursement_solicitud_for_approved_informe,
)
from .payment_schedule_service import ensure_fecha_pago_for_approved_solicitud

logger = logging.getLogger(__name__)

_EMPLOYEE_PROVIDER_NAME_SIMILARITY_THRESHOLD = 0.65


@dataclass(slots=True)
class InformeReimbursementRoutingResult:
    created: bool = False
    promoted: bool = False
    solicitud_id: Optional[UUID] = None
    warning: Optional[str] = None

    @property
    def changed(self) -> bool:
        return self.created or self.promoted


@dataclass(frozen=True, slots=True)
class InformeReimbursementPaymentReadiness:
    """Read-only explanation of an informe reimbursement's Payment Run state."""

    status: str
    detail: str
    solicitud_id: Optional[UUID] = None
    can_reconcile: bool = False


def should_apply_informe_reimbursement_reconciliation(
    *,
    apply: bool,
    readiness: InformeReimbursementPaymentReadiness,
) -> bool:
    """Keep dry-runs from entering the reimbursement mutation branch."""
    return apply and readiness.can_reconcile


def classify_informe_reimbursement_payment_readiness(
    *,
    informe: Documento,
    solicitud: Optional[Documento],
    has_approval_record: bool,
) -> InformeReimbursementPaymentReadiness:
    """Classify a reimbursement without changing documents or approval history."""
    if not getattr(informe, "budget_concept_id", None):
        return InformeReimbursementPaymentReadiness(
            status="blocked_missing_budget_concept",
            detail="Bloqueado: el informe no tiene concepto presupuestal.",
            solicitud_id=getattr(solicitud, "id", None),
        )
    if not has_approval_record:
        return InformeReimbursementPaymentReadiness(
            status="blocked_missing_approval_audit",
            detail="Bloqueado: falta el registro de aprobación del informe.",
            solicitud_id=getattr(solicitud, "id", None),
        )
    if solicitud is None:
        return InformeReimbursementPaymentReadiness(
            status="missing_solicitud",
            detail="Falta generar la solicitud de reembolso para Programación de Pagos.",
            can_reconcile=True,
        )
    if solicitud.estado == "aprobado" and not getattr(solicitud, "pagado_en", None):
        return InformeReimbursementPaymentReadiness(
            status="ready_for_payment_run",
            detail="Lista para Programación de Pagos.",
            solicitud_id=solicitud.id,
        )
    if solicitud.estado in {"borrador", "control_presupuestal", "enviado"}:
        return InformeReimbursementPaymentReadiness(
            status="awaiting_promotion",
            detail=(
                "Solicitud de reembolso pendiente de promoción "
                f"(estado: {solicitud.estado})."
            ),
            solicitud_id=solicitud.id,
            can_reconcile=True,
        )
    return InformeReimbursementPaymentReadiness(
        status="not_payment_run_eligible",
        detail=(
            "La solicitud de reembolso no es elegible para Programación de Pagos "
            f"(estado: {solicitud.estado})."
        ),
        solicitud_id=solicitud.id,
    )


async def get_informe_reimbursement_payment_readiness(
    session: AsyncSession,
    *,
    informe_doc: Documento,
) -> InformeReimbursementPaymentReadiness:
    """Return the Payment Run readiness for an approved informe, read-only."""
    solicitud = None
    if getattr(informe_doc, "cuenta_gastos_id", None):
        result = await session.execute(
            select(Documento)
            .where(
                Documento.cuenta_gastos_id == informe_doc.cuenta_gastos_id,
                Documento.tipo == "SOLICITUD",
                Documento.concepto_pago.like("Reembolso de saldo a favor%"),
                Documento.estado.notin_(["rechazado", "cancelado"]),
            )
            .order_by(Documento.creado_en.asc())
            .limit(1)
        )
        solicitud = result.scalar_one_or_none()
    approval_result = await session.execute(
        select(Aprobacion.id)
        .where(
            Aprobacion.tipo_entidad == "documento",
            Aprobacion.entidad_id == informe_doc.id,
            Aprobacion.accion == "aprobar",
        )
        .limit(1)
    )
    readiness = classify_informe_reimbursement_payment_readiness(
        informe=informe_doc,
        solicitud=solicitud,
        has_approval_record=approval_result.scalar_one_or_none() is not None,
    )
    if readiness.status == "missing_solicitud":
        cuenta = await session.get(CuentaDeGastos, informe_doc.cuenta_gastos_id)
        if cuenta is None:
            return InformeReimbursementPaymentReadiness(
                status="blocked_missing_cuenta",
                detail="Bloqueado: el informe no tiene una cuenta de gastos disponible.",
            )
        _, warning = await _resolve_reimbursement_provider_id(session, cuenta=cuenta)
        if warning:
            return InformeReimbursementPaymentReadiness(
                status="blocked_missing_bank_account",
                detail=warning,
            )
    if readiness.status == "ready_for_payment_run":
        closure_result = await session.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM payment_run_closure_items "
                "WHERE documento_id = :documento_id"
                ")"
            ),
            {"documento_id": solicitud.id},
        )
        if closure_result.scalar_one():
            return InformeReimbursementPaymentReadiness(
                status="not_payment_run_eligible",
                detail="La solicitud ya pertenece a un corte de Programación de Pagos.",
                solicitud_id=solicitud.id,
            )
    return readiness


def _schedule_pending_payment_notification(documento_id: UUID) -> None:
    try:
        from .documento_telegram import (
            schedule_solicitud_pending_payment_telegram_notifications,
        )

        schedule_solicitud_pending_payment_telegram_notifications(
            documento_id=str(documento_id),
        )
    except Exception:
        logger.exception(
            "Failed to schedule Finance pending payment Telegram notifications",
            extra={"documento_id": str(documento_id)},
        )


def _normalize_name_for_similarity(name: Optional[str]) -> str:
    value = unicodedata.normalize("NFKD", name or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.casefold().split())


def _token_set_jaccard(a: str, b: str) -> float:
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def _employee_provider_name_similarity(
    empleado_nombre: Optional[str],
    proveedor_nombre: Optional[str],
) -> float:
    norm_emp = _normalize_name_for_similarity(empleado_nombre)
    norm_prov = _normalize_name_for_similarity(proveedor_nombre)
    if not norm_emp or not norm_prov:
        return 0.0
    if norm_emp == norm_prov:
        return 1.0
    ratio = SequenceMatcher(None, norm_emp, norm_prov).ratio()
    jaccard = _token_set_jaccard(norm_emp, norm_prov)
    return 0.7 * ratio + 0.3 * jaccard


async def _get_matching_bank_accounts_for_empleado(
    session: AsyncSession,
    empleado: Optional[Empleado],
    threshold: float = _EMPLOYEE_PROVIDER_NAME_SIMILARITY_THRESHOLD,
    limit: int = 50,
) -> list[tuple[ProveedorCliente, float]]:
    empleado_nombre = empleado.nombre if empleado else None
    empleado_id = getattr(empleado, "id", None)
    if not empleado_nombre or empleado_id is None:
        return []

    exact_result = await session.execute(
        select(ProveedorCliente).where(
            and_(
                ProveedorCliente.activo.is_(True),
                ProveedorCliente.tipo == "empleado",
                ProveedorCliente.empleado_id == empleado_id,
                or_(
                    ProveedorCliente.banco.isnot(None),
                    ProveedorCliente.cuenta_clabe.isnot(None),
                    ProveedorCliente.cuenta_bancaria.isnot(None),
                ),
            )
        )
    )
    exact_accounts = list(exact_result.scalars().all())
    if exact_accounts:
        exact_accounts.sort(key=lambda account: (account.nombre or "").lower())
        return [(account, 1.0) for account in exact_accounts[:limit]]

    proveedores_result = await session.execute(
        select(ProveedorCliente).where(
            and_(
                ProveedorCliente.activo.is_(True),
                ProveedorCliente.tipo != "empleado",
                or_(
                    ProveedorCliente.banco.isnot(None),
                    ProveedorCliente.cuenta_clabe.isnot(None),
                    ProveedorCliente.cuenta_bancaria.isnot(None),
                ),
            )
        )
    )

    scored: list[tuple[ProveedorCliente, float]] = []
    for proveedor in proveedores_result.scalars().all():
        score = _employee_provider_name_similarity(
            empleado_nombre,
            proveedor.nombre,
        )
        if score >= threshold:
            scored.append((proveedor, score))

    scored.sort(key=lambda item: (-item[1], (item[0].nombre or "").lower()))
    return scored[:limit]


async def _compute_cuenta_saldo_context(
    session: AsyncSession,
    cuenta_id: UUID,
) -> dict[str, Any]:
    gastos_row = await session.execute(
        select(func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0)).where(
            ExpenseReport.cuenta_gastos_id == cuenta_id,
            ExpenseReport.estado_gasto != "cancelado",
            employee_paid_sql_condition(),
        )
    )
    total_pagado_empleado = float(gastos_row.scalar_one() or 0)

    solicitado_row = await session.execute(
        select(func.coalesce(func.sum(Documento.monto_solicitado), 0)).where(
            Documento.cuenta_gastos_id == cuenta_id,
            Documento.tipo == "SOLICITUD",
            Documento.estado == "pagado",
        )
    )
    monto_entregado = float(solicitado_row.scalar_one() or 0)

    settled_amount, active_count = await compute_cuenta_saldo_adjustments(
        session, cuenta_id
    )
    saldo_breakdown = compute_informe_saldo(
        employee_paid=total_pagado_empleado,
        monto_entregado=monto_entregado,
        settled_amount=settled_amount,
    )
    return {
        "active_settlement_count": active_count,
        "saldo_raw": saldo_breakdown.saldo,
    }


async def _find_existing_reimbursement_solicitud(
    session: AsyncSession,
    *,
    cuenta: CuentaDeGastos,
) -> Optional[Documento]:
    provider_beneficiary_id = effective_account_provider_beneficiary_id(cuenta)
    beneficiary_id = effective_account_beneficiary_id(cuenta)
    filters = [
        Documento.cuenta_gastos_id == cuenta.id,
        Documento.tipo == "SOLICITUD",
        Documento.concepto_pago.like("Reembolso de saldo a favor%"),
        Documento.estado.notin_(["rechazado", "cancelado"]),
    ]
    if provider_beneficiary_id is not None:
        filters.append(
            or_(
                Documento.beneficiario_proveedor_cliente_id
                == provider_beneficiary_id,
                Documento.proveedor_cliente_id == provider_beneficiary_id,
            )
        )
    else:
        filters.append(Documento.beneficiario_empleado_id == beneficiary_id)

    result = await session.execute(select(Documento).where(*filters).limit(1))
    return result.scalar_one_or_none()


async def _resolve_reimbursement_provider_id(
    session: AsyncSession,
    *,
    cuenta: CuentaDeGastos,
) -> tuple[Optional[UUID], Optional[str]]:
    provider_beneficiary_id = effective_account_provider_beneficiary_id(cuenta)
    if provider_beneficiary_id is not None:
        provider_result = await session.execute(
            select(ProveedorCliente).where(
                and_(
                    ProveedorCliente.id == provider_beneficiary_id,
                    ProveedorCliente.activo.is_(True),
                )
            )
        )
        if provider_result.scalar_one_or_none() is None:
            return (
                None,
                "No se generó la solicitud de reembolso porque la cuenta "
                "bancaria del operador regional no está activa.",
            )
        return provider_beneficiary_id, None

    beneficiary_id = effective_account_beneficiary_id(cuenta)
    beneficiary_result = await session.execute(
        select(Empleado).where(Empleado.id == beneficiary_id)
    )
    beneficiary_empleado = beneficiary_result.scalar_one_or_none()
    matches = await _get_matching_bank_accounts_for_empleado(
        session=session,
        empleado=beneficiary_empleado,
    )
    if len(matches) != 1:
        return (
            None,
            "No se generó la solicitud de reembolso porque el beneficiario "
            "no tiene una única cuenta bancaria activa seleccionable.",
        )
    return matches[0][0].id, None


async def ensure_approved_informe_reimbursement_for_payment_run(
    session: AsyncSession,
    *,
    informe_doc: Documento,
    actor_id: UUID,
    request_context: Optional[dict[str, Any]] = None,
) -> InformeReimbursementRoutingResult:
    """Create or promote a reimbursement SOLICITUD for an approved INFORME."""
    if (
        informe_doc.tipo != "INFORME"
        or informe_doc.estado != "aprobado"
        or not informe_doc.cuenta_gastos_id
    ):
        return InformeReimbursementRoutingResult()

    cuenta_result = await session.execute(
        select(CuentaDeGastos)
        .where(CuentaDeGastos.id == informe_doc.cuenta_gastos_id)
        .options(
            undefer(CuentaDeGastos.torneo_id),
            undefer(CuentaDeGastos.fase),
        )
    )
    cuenta = cuenta_result.scalar_one_or_none()
    if cuenta is None:
        return InformeReimbursementRoutingResult(
            warning=(
                "No se pudo generar la solicitud de reembolso: informe sin "
                "cuenta "
                "vinculada."
            )
        )

    saldo_ctx = await _compute_cuenta_saldo_context(session, cuenta.id)
    saldo_raw = float(saldo_ctx.get("saldo_raw") or 0)
    if saldo_raw >= -0.005:
        return InformeReimbursementRoutingResult()

    existing_reembolso = await _find_existing_reimbursement_solicitud(
        session, cuenta=cuenta
    )
    if existing_reembolso is not None:
        if existing_reembolso.estado in {"borrador", "control_presupuestal", "enviado"}:
            aprobacion = await approve_reimbursement_solicitud_for_approved_informe(
                session,
                existing_reembolso,
                comentario=(
                    "Auto-aprobada al aprobar el informe "
                    "(saldo a favor del empleado)."
                ),
            )
            if aprobacion is None:
                return InformeReimbursementRoutingResult(
                    solicitud_id=existing_reembolso.id,
                    warning=(
                        "La solicitud de reembolso ya existía, pero no pudo "
                        "aprobarse automáticamente desde el informe vinculado."
                    ),
                )
            await session.commit()
            _schedule_pending_payment_notification(existing_reembolso.id)
            logger.info(
                "Promoted reimbursement solicitud %s for approved informe %s",
                existing_reembolso.id,
                informe_doc.id,
            )
            return InformeReimbursementRoutingResult(
                promoted=True,
                solicitud_id=existing_reembolso.id,
            )

        if existing_reembolso.estado == "aprobado":
            if ensure_fecha_pago_for_approved_solicitud(existing_reembolso):
                await session.commit()
                _schedule_pending_payment_notification(existing_reembolso.id)
            return InformeReimbursementRoutingResult(
                solicitud_id=existing_reembolso.id,
            )

        return InformeReimbursementRoutingResult(
            solicitud_id=existing_reembolso.id,
        )

    proveedor_uuid, warning = await _resolve_reimbursement_provider_id(
        session, cuenta=cuenta
    )
    if warning:
        return InformeReimbursementRoutingResult(warning=warning)

    try:
        payload = build_solicitud_personal_payload(
            cuenta_id=cuenta.id,
            empleado_id=cuenta.empleado_id,
            monto_solicitado=abs(saldo_raw),
            concepto_pago=reimbursement_concept_from_cuenta(cuenta),
            proveedor_cliente_id=str(proveedor_uuid),
            budget_concept_id=(
                str(informe_doc.budget_concept_id)
                if informe_doc.budget_concept_id
                else None
            ),
            allow_closed_cuenta=True,
        )
        new_solicitud = await create_solicitud_personal_document(
            session,
            payload,
        )
        aprobacion = await approve_reimbursement_solicitud_for_approved_informe(
            session,
            new_solicitud,
            comentario=(
                "Auto-aprobada al aprobar el informe "
                "(saldo a favor del empleado)."
            ),
        )
        if aprobacion is None:
            await session.rollback()
            return InformeReimbursementRoutingResult(
                warning=(
                    "No se pudo aprobar automáticamente la solicitud de "
                    "reembolso desde el informe vinculado."
                )
            )
        await session.commit()
        _schedule_pending_payment_notification(new_solicitud.id)
    except SolicitudValidationError as exc:
        await session.rollback()
        return InformeReimbursementRoutingResult(
            warning=f"No se pudo generar la solicitud de reembolso: {str(exc)}"
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "Failed to auto-create reimbursement solicitud on "
            "informe approval",
            extra={
                "cuenta_id": str(cuenta.id),
                "documento_id": str(informe_doc.id),
            },
        )
        return InformeReimbursementRoutingResult(
            warning=(
                "No se pudo generar automáticamente la solicitud de reembolso."
            )
        )

    logger.info(
        "Auto-created reimbursement solicitud %s for approved informe %s",
        new_solicitud.id,
        informe_doc.id,
    )
    return InformeReimbursementRoutingResult(
        created=True,
        solicitud_id=new_solicitud.id,
    )
