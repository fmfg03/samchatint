"""AMEX fee/interest classification helpers.

RQF-AMEX-004: fees and interest charged in AMEX statements must be easy to
classify to the P1218 budget line without editing each expense manually.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Aprobacion,
    BudgetConcept,
    CuentaContable,
    CuentaDeGastos,
    Empleado,
    ExpenseReport,
)
from .amex_expense_service import FINANCE_AMEX_ROLES

P1218_TOKEN = "P1218"
AMEX_FEE_INTEREST_CONCEPT = "Comisiones e intereses AMEX"


class AmexFeeInterestError(ValueError):
    pass


@dataclass(frozen=True)
class AmexP1218ClassificationResult:
    expenses: list[ExpenseReport]
    budget_concept: BudgetConcept
    cuenta_contable: CuentaContable


async def find_p1218_budget_concept(
    session: AsyncSession,
    cuenta: CuentaDeGastos,
) -> BudgetConcept | None:
    """Find the active P1218 expense budget concept for the report tournament."""
    torneo_id = getattr(cuenta, "torneo_id", None)
    if not torneo_id:
        return None
    token = f"%{P1218_TOKEN}%"
    result = await session.execute(
        select(BudgetConcept)
        .where(
            and_(
                BudgetConcept.active.is_(True),
                BudgetConcept.budget_direction == "expense",
                BudgetConcept.tournament_id == torneo_id,
                or_(
                    func.upper(BudgetConcept.concept_key).like(token),
                    func.upper(BudgetConcept.concept_name).like(token),
                ),
            )
        )
        .order_by(BudgetConcept.concept_key.asc(), BudgetConcept.concept_name.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def apply_amex_fee_interest_p1218(
    session: AsyncSession,
    *,
    cuenta_id: UUID,
    expense_ids: Iterable[UUID],
    actor: Empleado,
) -> AmexP1218ClassificationResult:
    """Classify selected AMEX charges as fees/interest in budget line P1218."""
    role = (actor.rol or "").strip().lower()
    if role not in FINANCE_AMEX_ROLES:
        raise AmexFeeInterestError(
            "Solo Finanzas, Admin o Superadmin puede clasificar comisiones AMEX."
        )

    unique_ids = list(dict.fromkeys(expense_ids))
    if not unique_ids:
        raise AmexFeeInterestError("Selecciona al menos un gasto activo.")

    cuenta_result = await session.execute(
        select(CuentaDeGastos).where(CuentaDeGastos.id == cuenta_id)
    )
    cuenta = cuenta_result.scalar_one_or_none()
    if not cuenta:
        raise AmexFeeInterestError("Informe de gastos no encontrado.")

    budget_concept = await find_p1218_budget_concept(session, cuenta)
    if not budget_concept:
        raise AmexFeeInterestError(
            "No encontré la partida P1218 activa para el torneo de este informe."
        )
    if not budget_concept.cuenta_contable_id:
        raise AmexFeeInterestError(
            "La partida P1218 no tiene cuenta contable asignada."
        )

    cuenta_contable_result = await session.execute(
        select(CuentaContable).where(
            and_(
                CuentaContable.id == budget_concept.cuenta_contable_id,
                CuentaContable.activo.is_(True),
            )
        )
    )
    cuenta_contable = cuenta_contable_result.scalar_one_or_none()
    if not cuenta_contable:
        raise AmexFeeInterestError(
            "La cuenta contable de P1218 está inactiva o no existe."
        )

    expenses_result = await session.execute(
        select(ExpenseReport).where(ExpenseReport.id.in_(unique_ids)).with_for_update()
    )
    expenses = list(expenses_result.scalars().all())
    if len(expenses) != len(unique_ids):
        raise AmexFeeInterestError("Uno o más gastos seleccionados no existen.")
    if any(expense.cuenta_gastos_id != cuenta_id for expense in expenses):
        raise AmexFeeInterestError(
            "Uno o más gastos no pertenecen al Informe de Gastos."
        )
    if any(expense.estado_gasto == "cancelado" for expense in expenses):
        raise AmexFeeInterestError("No se pueden modificar gastos cancelados.")

    now = datetime.now(timezone.utc)
    for expense in expenses:
        previous_concept = expense.concepto
        previous_budget = expense.budget_concept_id
        previous_account = expense.cuenta_contable_id
        previous_amex = expense.pagado_con_amex_empresa
        expense.pagado_con_amex_empresa = True
        expense.concepto = AMEX_FEE_INTEREST_CONCEPT
        expense.budget_concept_id = budget_concept.id
        expense.cuenta_contable_id = cuenta_contable.id
        session.add(
            Aprobacion(
                tipo_entidad="gasto",
                entidad_id=expense.id,
                aprobador_id=actor.id,
                accion="editar",
                comentario=(
                    "Clasificado como comisión/interés AMEX P1218: "
                    f"concepto {previous_concept!r} -> {AMEX_FEE_INTEREST_CONCEPT!r}; "
                    f"partida {previous_budget or '(vacío)'} -> {budget_concept.id}; "
                    f"cuenta {previous_account or '(vacío)'} -> {cuenta_contable.codigo}; "
                    f"AMEX {previous_amex} -> True."
                ),
                fecha=now,
            )
        )

    return AmexP1218ClassificationResult(
        expenses=expenses,
        budget_concept=budget_concept,
        cuenta_contable=cuenta_contable,
    )
