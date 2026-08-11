"""Telegram notifications for validated AMEX reconciliations.

RQF-AMEX-005: when Finance validates an AMEX reconciliation period, Benjamin
must receive an authorization notification and LAO/FGV/FGN must receive an
awareness notification. This service intentionally uses the existing Telegram
outbox so delivery is auditable and idempotent per period/recipient.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Literal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Empleado, ExpenseReport
from .amex_expense_service import FINANCE_AMEX_ROLES
from .telegram_outbox_service import deliver_telegram_notification

AMEX_VALIDATION_AUTHORIZER_MATCHERS = ("benjamin", "benjamín")
AMEX_VALIDATION_AWARENESS_MATCHERS = (
    "luis angel",
    "luis ángel",
    "lao",
    "federico gonzalez",
    "federico gonzález",
    "fgv",
    "fgn",
)


class AmexReconciliationNotificationError(ValueError):
    pass


@dataclass(frozen=True)
class AmexReconciliationPeriodSummary:
    year: int
    month: int
    charge_count: int
    linked_charge_count: int
    pending_charge_count: int
    total_amount: Decimal
    linked_amount: Decimal
    pending_amount: Decimal

    @property
    def period_label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


@dataclass(frozen=True)
class AmexReconciliationNotificationResult:
    summary: AmexReconciliationPeriodSummary
    authorization_recipients: list[Empleado]
    awareness_recipients: list[Empleado]
    delivered: int
    skipped: int


def _normalize_text(value: Any) -> str:
    raw = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", raw)
    return " ".join(
        "".join(ch for ch in normalized if not unicodedata.combining(ch)).split()
    )


def _employee_haystack(employee: Empleado) -> str:
    return _normalize_text(
        " ".join(
            str(getattr(employee, attr, "") or "")
            for attr in ("nombre", "correo", "departamento")
        )
    )


def _matches_any(employee: Empleado, matchers: Iterable[str]) -> bool:
    haystack = _employee_haystack(employee)
    if not haystack:
        return False
    for matcher in matchers:
        token = _normalize_text(matcher)
        if token and token in haystack:
            return True
    return False


def _mxn(amount: Any) -> str:
    try:
        value = Decimal(str(amount or 0)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError, ValueError):
        value = Decimal("0.00")
    return f"${value:,.2f} MXN"


def _period_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    if month < 1 or month > 12:
        raise AmexReconciliationNotificationError("Mes AMEX inválido.")
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end


async def build_amex_reconciliation_period_summary(
    session: AsyncSession,
    *,
    year: int,
    month: int,
) -> AmexReconciliationPeriodSummary:
    start_dt, end_dt = _period_bounds(year, month)
    result = await session.execute(
        select(ExpenseReport).where(
            and_(
                ExpenseReport.origen == "amex_batch",
                ExpenseReport.estado_gasto == "activo",
                ExpenseReport.fecha >= start_dt,
                ExpenseReport.fecha < end_dt,
            )
        )
    )
    expenses = list(result.scalars().all())
    total = Decimal("0.00")
    linked = Decimal("0.00")
    pending = Decimal("0.00")
    linked_count = 0
    pending_count = 0
    for expense in expenses:
        amount = Decimal(str(getattr(expense, "gasto_cantidad", 0) or 0))
        total += amount
        if getattr(expense, "cfdi_report_id", None):
            linked += amount
            linked_count += 1
        else:
            pending += amount
            pending_count += 1
    return AmexReconciliationPeriodSummary(
        year=year,
        month=month,
        charge_count=len(expenses),
        linked_charge_count=linked_count,
        pending_charge_count=pending_count,
        total_amount=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        linked_amount=linked.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        pending_amount=pending.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    )


async def resolve_amex_reconciliation_notification_recipients(
    session: AsyncSession,
) -> tuple[list[Empleado], list[Empleado]]:
    result = await session.execute(
        select(Empleado)
        .where(Empleado.activo.is_(True))
        .order_by(Empleado.nombre.asc())
    )
    employees = list(result.scalars().all())
    authorizers: list[Empleado] = []
    awareness: list[Empleado] = []
    seen_authorizers: set[Any] = set()
    seen_awareness: set[Any] = set()
    for employee in employees:
        if _matches_any(employee, AMEX_VALIDATION_AUTHORIZER_MATCHERS):
            if employee.id not in seen_authorizers:
                authorizers.append(employee)
                seen_authorizers.add(employee.id)
        if _matches_any(employee, AMEX_VALIDATION_AWARENESS_MATCHERS):
            if (
                employee.id not in seen_authorizers
                and employee.id not in seen_awareness
            ):
                awareness.append(employee)
                seen_awareness.add(employee.id)
    return authorizers, awareness


def amex_reconciliation_notification_type(
    *,
    year: int,
    month: int,
    kind: Literal["authorization", "awareness"],
) -> str:
    return f"amex_reconciliation_validation_{kind}_{year:04d}_{month:02d}"


def _module_url(year: int, month: int) -> str:
    base = (
        os.getenv("APP_URL") or os.getenv("SAMCHAT_APP_URL") or "https://sam.chat"
    ).rstrip("/")
    return f"{base}/admin/gastos/amex/conciliacion?year={year:04d}&month={month:02d}"


def build_amex_reconciliation_validation_message(
    *,
    summary: AmexReconciliationPeriodSummary,
    actor: Empleado,
    kind: Literal["authorization", "awareness"],
) -> tuple[str, str]:
    if kind == "authorization":
        header = "Conciliación AMEX validada — autorización requerida"
        intro = (
            "Finanzas validó la conciliación AMEX y requiere autorización de Benjamín."
        )
    else:
        header = "Conciliación AMEX validada — para conocimiento"
        intro = "Finanzas validó la conciliación AMEX; se envía para conocimiento de Dirección."
    actor_name = (
        getattr(actor, "nombre", None) or getattr(actor, "correo", None) or "Finanzas"
    )
    body = "\n".join(
        [
            f"*{header}*",
            "",
            intro,
            f"*Periodo* {summary.period_label}",
            f"*Validó* {actor_name}",
            f"*Cargos AMEX* {summary.charge_count}",
            f"*Total AMEX* {_mxn(summary.total_amount)}",
            f"*Con CFDI vinculado* {summary.linked_charge_count} · {_mxn(summary.linked_amount)}",
            f"*Pendiente CFDI* {summary.pending_charge_count} · {_mxn(summary.pending_amount)}",
            f"*Módulo* {_module_url(summary.year, summary.month)}",
        ]
    )
    return header, body


async def notify_amex_reconciliation_validated(
    session: AsyncSession,
    *,
    year: int,
    month: int,
    actor: Empleado,
) -> AmexReconciliationNotificationResult:
    role = (getattr(actor, "rol", "") or "").strip().lower()
    if role not in FINANCE_AMEX_ROLES:
        raise AmexReconciliationNotificationError(
            "Solo Finanzas, Admin o Superadmin puede validar conciliaciones AMEX."
        )
    summary = await build_amex_reconciliation_period_summary(
        session,
        year=year,
        month=month,
    )
    if summary.charge_count <= 0:
        raise AmexReconciliationNotificationError(
            "No hay cargos AMEX activos para el periodo seleccionado."
        )
    authorizers, awareness = await resolve_amex_reconciliation_notification_recipients(
        session
    )
    delivered = 0
    skipped = 0
    for kind, recipients in (("authorization", authorizers), ("awareness", awareness)):
        header, body = build_amex_reconciliation_validation_message(
            summary=summary,
            actor=actor,
            kind=kind,
        )
        notification_type = amex_reconciliation_notification_type(
            year=year,
            month=month,
            kind=kind,
        )
        for recipient in recipients:
            ok = await deliver_telegram_notification(
                session,
                notification_type=notification_type,
                header_text=header,
                text=body,
                chat_id=(
                    int(recipient.telegram_user_id)
                    if recipient.telegram_user_id is not None
                    else None
                ),
                documento_id=None,
                recipient_empleado_id=recipient.id,
            )
            if ok:
                delivered += 1
            else:
                skipped += 1
    return AmexReconciliationNotificationResult(
        summary=summary,
        authorization_recipients=authorizers,
        awareness_recipients=awareness,
        delivered=delivered,
        skipped=skipped,
    )
