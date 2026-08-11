"""Create AMEX card payment requests for the existing Payment Run flow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import AmexCardAccount, Aprobacion, Documento, Empleado
from .amex_expense_service import FINANCE_AMEX_ROLES
from .documento_service import generate_documento_reference_number
from .payment_run_service import parse_payment_run_date, PaymentRunValidationError


class AmexCardPaymentError(ValueError):
    """User-facing validation error for AMEX payment scheduling."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AmexCardPaymentRequest:
    card_account_id: UUID
    amount: Decimal
    fecha_pago: date
    urgent: bool = False
    currency: str = "MXN"


@dataclass(frozen=True)
class AmexCardPaymentResult:
    documento: Documento
    card_account: AmexCardAccount


def _actor_can_schedule_amex_payment(actor: Empleado) -> bool:
    roles = {
        str(role or "").strip().lower()
        for role in (getattr(actor, "roles", None) or [])
    }
    role = str(getattr(actor, "rol", "") or "").strip().lower()
    if role:
        roles.add(role)
    return bool(roles.intersection(FINANCE_AMEX_ROLES))


def parse_amex_payment_amount(value: Any) -> Decimal:
    raw = str(value or "").replace(",", "").strip()
    if not raw:
        raise AmexCardPaymentError("missing_amount", "Captura el monto a pagar.")
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise AmexCardPaymentError(
            "invalid_amount", "El monto a pagar no es válido."
        ) from exc
    if amount <= Decimal("0.00"):
        raise AmexCardPaymentError(
            "invalid_amount", "El monto a pagar debe ser mayor a cero."
        )
    return amount


def parse_amex_payment_date(value: Any) -> date:
    try:
        return parse_payment_run_date(value)
    except PaymentRunValidationError as exc:
        raise AmexCardPaymentError("invalid_fecha_pago", str(exc)) from exc


async def load_active_amex_card_account(
    session: AsyncSession, card_account_id: UUID
) -> AmexCardAccount | None:
    result = await session.execute(
        select(AmexCardAccount)
        .options(selectinload(AmexCardAccount.liability_cuenta_contable))
        .where(
            AmexCardAccount.id == card_account_id,
            AmexCardAccount.active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def create_amex_card_payment_request(
    session: AsyncSession,
    *,
    actor: Empleado,
    request: AmexCardPaymentRequest,
) -> AmexCardPaymentResult:
    """Create an approved SOLICITUD so AMEX card payment enters Payment Run.

    This intentionally reuses Payment Run's canonical document queue. It does not
    mark the card as paid and it does not create accounting entries by itself.
    """
    if not _actor_can_schedule_amex_payment(actor):
        raise AmexCardPaymentError(
            "forbidden",
            "No tienes permiso para programar pagos de AMEX.",
        )

    card = await load_active_amex_card_account(session, request.card_account_id)
    if card is None:
        raise AmexCardPaymentError(
            "card_not_found",
            "La tarjeta AMEX no existe o no está activa.",
        )

    amount = Decimal(request.amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount <= Decimal("0.00"):
        raise AmexCardPaymentError(
            "invalid_amount", "El monto a pagar debe ser mayor a cero."
        )

    currency = str(request.currency or "MXN").strip().upper() or "MXN"
    if currency != "MXN":
        raise AmexCardPaymentError(
            "unsupported_currency",
            "Por ahora los pagos AMEX del Payment Run se programan en MXN.",
        )

    numero_referencia = await generate_documento_reference_number(
        session,
        "SOLICITUD",
        getattr(actor, "id"),
    )
    now = datetime.utcnow()
    liability = getattr(card, "liability_cuenta_contable", None)
    liability_code = getattr(liability, "codigo", None) or "sin cuenta pasivo"
    label = card.card_label or f"AMEX terminación {card.last4}"
    concept = f"Pago AMEX {label} ({card.last4})"

    documento = Documento(
        empleado_id=getattr(actor, "id"),
        tipo="SOLICITUD",
        numero_referencia=numero_referencia,
        estado="aprobado",
        monto_solicitado=float(amount),
        monto_total=float(amount),
        currency=currency,
        fecha_pago=request.fecha_pago,
        pago_urgente=bool(request.urgent),
        concepto_pago=concept,
        metodo_pago="AMEX",
        notas=(
            "Solicitud generada desde Conciliación AMEX para Payment Run. "
            f"Tarjeta: {label}; terminación: {card.last4}; cuenta pasivo: {liability_code}."
        ),
        enviado_en=now,
        aprobado_en=now,
    )
    session.add(documento)
    await session.flush()

    session.add(
        Aprobacion(
            tipo_entidad="documento",
            entidad_id=documento.id,
            aprobador_id=getattr(actor, "id"),
            accion="aprobar",
            comentario=(
                "Programación de pago AMEX enviada automáticamente a Payment Run "
                f"por {getattr(actor, 'nombre', 'Finanzas')}."
            ),
            fecha=now,
        )
    )
    await session.commit()
    await session.refresh(documento)
    return AmexCardPaymentResult(documento=documento, card_account=card)
