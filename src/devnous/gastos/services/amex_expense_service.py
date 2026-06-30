"""Company AMEX marking and reimbursement-saldo helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Aprobacion, Empleado, ExpenseReport

FINANCE_AMEX_ROLES = frozenset({"finanzas", "admin", "superadmin", "super_admin"})


class AmexExpenseError(ValueError):
    """Base error for AMEX expense marking."""


class AmexExpensePermissionError(AmexExpenseError):
    """Actor is not authorized to mark company AMEX expenses."""


class AmexExpenseValidationError(AmexExpenseError):
    """Selected expenses do not satisfy the bulk-action contract."""


MONEY_QUANT = Decimal("0.01")


@dataclass(frozen=True)
class InformeExpenseTotals:
    total_reported: float
    company_amex: float
    employee_paid: float


@dataclass(frozen=True)
class InformeSaldoBreakdown:
    """Signed saldo between employee and company for a Cuenta de Gastos.

    Uses only employee out-of-pocket expenses (AMEX excluded) and company
    transfers already marked ``pagado`` on linked SOLICITUD rows.
    """

    monto_entregado: float
    employee_paid: float
    saldo_gross: float
    settled_amount: float
    saldo: float
    settlement_tipo: Optional[str]


def _quantize_money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def sum_paid_solicitud_amounts(documentos: Iterable[object]) -> float:
    """Sum ``monto_solicitado`` for SOLICITUD rows the company already paid."""
    total = sum(
        (
            _quantize_money(getattr(doc, "monto_solicitado", 0) or 0)
            for doc in documentos
            if (getattr(doc, "estado", None) or "").strip().lower() == "pagado"
        ),
        Decimal("0"),
    )
    return float(total)


def compute_informe_saldo(
    *,
    employee_paid: float,
    monto_entregado: float,
    settled_amount: float = 0.0,
) -> InformeSaldoBreakdown:
    """Compute signed saldo and settlement direction.

    Convention (matches finance docs):
    - ``saldo_gross = monto_entregado - employee_paid``
    - ``saldo > 0`` → employee owes company → ``devolucion``
    - ``saldo < 0`` → company owes employee → ``reembolso``
    """
    gross = _quantize_money(monto_entregado) - _quantize_money(employee_paid)
    settled = _quantize_money(settled_amount)
    if gross > 0:
        net = max(gross - settled, Decimal("0.00"))
    elif gross < 0:
        net = min(gross + settled, Decimal("0.00"))
    else:
        net = Decimal("0.00")

    if net > 0:
        tipo = "devolucion"
    elif net < 0:
        tipo = "reembolso"
    else:
        tipo = None

    return InformeSaldoBreakdown(
        monto_entregado=float(_quantize_money(monto_entregado)),
        employee_paid=float(_quantize_money(employee_paid)),
        saldo_gross=float(gross),
        settled_amount=float(settled),
        saldo=float(net),
        settlement_tipo=tipo,
    )


def describe_informe_balance(
    *,
    employee_paid: float,
    monto_entregado: float,
    settled_amount: float = 0.0,
) -> tuple[float, str, str]:
    """Return absolute balance amount, owner label, and supporting note."""
    breakdown = compute_informe_saldo(
        employee_paid=employee_paid,
        monto_entregado=monto_entregado,
        settled_amount=settled_amount,
    )
    balance_amount = abs(breakdown.saldo)
    if breakdown.saldo > 0:
        return (
            balance_amount,
            "A pagar por el empleado",
            "La empresa entregó más de lo que el empleado pagó de su bolsillo.",
        )
    if breakdown.saldo < 0:
        return (
            balance_amount,
            "A favor del empleado",
            "El empleado pagó de su bolsillo más de lo que la empresa entregó.",
        )
    return balance_amount, "Saldado", "Entregado y gastos de bolsillo coinciden."


def is_company_amex_expense(expense: ExpenseReport) -> bool:
    explicit = getattr(expense, "pagado_con_amex_empresa", None)
    if explicit is not None:
        return bool(explicit)
    return (getattr(expense, "origen", None) or "").strip().lower() == "amex_batch"


def company_amex_sql_condition():
    return or_(
        ExpenseReport.pagado_con_amex_empresa.is_(True),
        and_(
            ExpenseReport.pagado_con_amex_empresa.is_(None),
            ExpenseReport.origen == "amex_batch",
        ),
    )


def employee_paid_sql_condition():
    return or_(
        ExpenseReport.pagado_con_amex_empresa.is_(False),
        and_(
            ExpenseReport.pagado_con_amex_empresa.is_(None),
            func.coalesce(ExpenseReport.origen, "") != "amex_batch",
        ),
    )


def calculate_informe_expense_totals(
    expenses: Iterable[ExpenseReport],
) -> InformeExpenseTotals:
    active = [
        expense
        for expense in expenses
        if getattr(expense, "estado_gasto", None) != "cancelado"
    ]
    total_reported = sum(float(expense.gasto_cantidad or 0) for expense in active)
    company_amex = sum(
        float(expense.gasto_cantidad or 0)
        for expense in active
        if is_company_amex_expense(expense)
    )
    return InformeExpenseTotals(
        total_reported=total_reported,
        company_amex=company_amex,
        employee_paid=total_reported - company_amex,
    )


async def set_company_amex_status(
    session: AsyncSession,
    *,
    cuenta_id: UUID,
    expense_ids: Iterable[UUID],
    mark_as_amex: bool,
    actor: Empleado,
) -> List[ExpenseReport]:
    role = (actor.rol or "").strip().lower()
    if role not in FINANCE_AMEX_ROLES:
        raise AmexExpensePermissionError(
            "Solo Finanzas, Admin o Superadmin puede actualizar gastos AMEX."
        )

    unique_ids = list(dict.fromkeys(expense_ids))
    if not unique_ids:
        raise AmexExpenseValidationError("Selecciona al menos un gasto activo.")

    result = await session.execute(
        select(ExpenseReport)
        .where(ExpenseReport.id.in_(unique_ids))
        .with_for_update()
    )
    expenses = list(result.scalars().all())
    if len(expenses) != len(unique_ids):
        raise AmexExpenseValidationError("Uno o más gastos seleccionados no existen.")
    if any(expense.cuenta_gastos_id != cuenta_id for expense in expenses):
        raise AmexExpenseValidationError(
            "Uno o más gastos no pertenecen al Informe de Gastos."
        )
    if any(expense.estado_gasto == "cancelado" for expense in expenses):
        raise AmexExpenseValidationError("No se pueden modificar gastos cancelados.")

    changed: List[ExpenseReport] = []
    now = datetime.now(timezone.utc)
    for expense in expenses:
        previous = is_company_amex_expense(expense)
        if previous == mark_as_amex:
            continue
        expense.pagado_con_amex_empresa = mark_as_amex
        changed.append(expense)
        session.add(
            Aprobacion(
                tipo_entidad="gasto",
                entidad_id=expense.id,
                aprobador_id=actor.id,
                accion="editar",
                comentario=(
                    "Pago AMEX empresa actualizado por Finanzas: "
                    f"{previous} -> {mark_as_amex}."
                ),
                fecha=now,
            )
        )
    return changed
