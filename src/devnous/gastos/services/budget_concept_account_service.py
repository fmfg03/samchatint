"""Budget concept → cuenta contable propagation and cleanup display helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import BudgetConcept, CuentaContable, Documento, ExpenseReport
from samchat.budgets.service import validate_active_cuenta_contable_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupAccountingDisplay:
    """Resolved partida/cuenta labels for the accounting cleanup bandeja."""

    partida_name: Optional[str]
    partida_from_document: bool
    assigned_cuenta: Optional[CuentaContable]
    mapped_cuenta: Optional[CuentaContable]


def resolve_effective_budget_concept(
    expense: ExpenseReport,
) -> Optional[BudgetConcept]:
    """Return the expense partida, falling back to linked documento records."""

    if expense.budget_concept:
        return expense.budget_concept
    for doc in (
        expense.documento,
        expense.informe_documento,
        expense.solicitud_documento,
    ):
        concept = getattr(doc, "budget_concept", None)
        if concept:
            return concept
    return None


def build_cleanup_accounting_display(
    expense: ExpenseReport,
) -> CleanupAccountingDisplay:
    """Build display metadata for partida/cuenta columns on sin-cuenta-contable."""

    effective_concept = resolve_effective_budget_concept(expense)
    partida_from_document = bool(
        not expense.budget_concept and effective_concept is not None
    )
    mapped_cuenta = None
    if (
        expense.cuenta_contable is None
        and effective_concept is not None
        and effective_concept.cuenta_contable is not None
    ):
        mapped_cuenta = effective_concept.cuenta_contable
    return CleanupAccountingDisplay(
        partida_name=(
            effective_concept.concept_name if effective_concept is not None else None
        ),
        partida_from_document=partida_from_document,
        assigned_cuenta=expense.cuenta_contable,
        mapped_cuenta=mapped_cuenta,
    )


async def apply_budget_concept_cuenta_mapping(
    session: AsyncSession,
    expense: ExpenseReport,
    *,
    budget_concept_id: Optional[UUID] = None,
) -> bool:
    """Set expense.cuenta_contable_id from catalog mapping when not already assigned."""

    if expense.cuenta_contable_id is not None:
        return False

    concept_id = budget_concept_id or expense.budget_concept_id
    if not concept_id:
        return False

    result = await session.execute(
        select(BudgetConcept).where(BudgetConcept.id == concept_id)
    )
    concept = result.scalar_one_or_none()
    if concept is None or concept.cuenta_contable_id is None:
        return False

    try:
        validated_id = await validate_active_cuenta_contable_id(
            session, str(concept.cuenta_contable_id)
        )
        expense.cuenta_contable_id = UUID(validated_id)
        return True
    except ValueError:
        logger.warning(
            "Skipping inactive/missing cuenta for budget concept %s on expense %s",
            concept_id,
            getattr(expense, "id", None),
        )
        return False


async def backfill_expense_budget_concept_accounts(
    session: AsyncSession,
    *,
    apply: bool = False,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Copy partida from linked documentos and apply catalog cuenta mappings."""

    cap = max(1, int(limit)) if limit else None
    partida_sql = text(
        """
        WITH candidates AS (
            SELECT DISTINCT ON (er.id)
                er.id AS expense_id,
                d.budget_concept_id AS doc_budget_concept_id
            FROM expense_reports er
            JOIN documentos d ON (
                d.id = er.documento_id
                OR d.id = er.informe_documento_id
                OR d.id = er.solicitud_documento_id
            )
            WHERE er.estado_gasto = 'activo'
              AND er.budget_concept_id IS NULL
              AND d.budget_concept_id IS NOT NULL
            ORDER BY er.id, d.creado_en DESC NULLS LAST
        )
        SELECT expense_id, doc_budget_concept_id
        FROM candidates
        """
        + (f" LIMIT {cap}" if cap else "")
    )
    partida_rows = (await session.execute(partida_sql)).mappings().all()

    cuenta_sql = text(
        """
        SELECT er.id AS expense_id, bc.cuenta_contable_id
        FROM expense_reports er
        JOIN budget_concepts bc ON bc.id = er.budget_concept_id
        JOIN cuentas_contables cc ON cc.id = bc.cuenta_contable_id AND cc.activo = TRUE
        WHERE er.estado_gasto = 'activo'
          AND er.cuenta_contable_id IS NULL
          AND bc.cuenta_contable_id IS NOT NULL
        """
        + (f" LIMIT {cap}" if cap else "")
    )
    cuenta_rows = (await session.execute(cuenta_sql)).mappings().all()

    partida_updates = 0
    cuenta_updates = 0

    if apply:
        for row in partida_rows:
            expense = await session.get(ExpenseReport, row["expense_id"])
            if expense is None or expense.budget_concept_id is not None:
                continue
            expense.budget_concept_id = row["doc_budget_concept_id"]
            partida_updates += 1

        await session.flush()

        refreshed_cuenta_rows = (await session.execute(cuenta_sql)).mappings().all()
        for row in refreshed_cuenta_rows:
            expense = await session.get(ExpenseReport, row["expense_id"])
            if expense is None or expense.cuenta_contable_id is not None:
                continue
            expense.cuenta_contable_id = row["cuenta_contable_id"]
            cuenta_updates += 1

        await session.commit()

    return {
        "ok": True,
        "apply": apply,
        "partida_candidates": len(partida_rows),
        "cuenta_candidates": len(cuenta_rows),
        "partida_updated": partida_updates if apply else 0,
        "cuenta_updated": cuenta_updates if apply else 0,
    }


def cleanup_expense_loader_options() -> list:
    """Eager-load options for sin-cuenta-contable display fallbacks."""

    doc_budget = (
        selectinload(ExpenseReport.documento)
        .selectinload(Documento.budget_concept)
        .selectinload(BudgetConcept.cuenta_contable)
    )
    informe_budget = (
        selectinload(ExpenseReport.informe_documento)
        .selectinload(Documento.budget_concept)
        .selectinload(BudgetConcept.cuenta_contable)
    )
    solicitud_budget = (
        selectinload(ExpenseReport.solicitud_documento)
        .selectinload(Documento.budget_concept)
        .selectinload(BudgetConcept.cuenta_contable)
    )
    expense_budget = (
        selectinload(ExpenseReport.budget_concept).selectinload(
            BudgetConcept.cuenta_contable
        )
    )
    return [doc_budget, informe_budget, solicitud_budget, expense_budget]


__all__ = [
    "CleanupAccountingDisplay",
    "apply_budget_concept_cuenta_mapping",
    "backfill_expense_budget_concept_accounts",
    "build_cleanup_accounting_display",
    "cleanup_expense_loader_options",
    "resolve_effective_budget_concept",
]
