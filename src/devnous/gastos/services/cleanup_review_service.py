"""Explicit cleanup review, separate from fiscal readiness and financial state.

The existing Aprobacion journal stores requests and confirmations. Writers lock
the expense before checking the journal; callers own the transaction and auth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Iterable, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Aprobacion, ExpenseReport

BATCH = "la-merced-octubre-2026-20261030"
LABEL = "La Merced · revisión contable solicitada"
REQUESTED = "cleanup_review_requested"
CONFIRMED = "cleanup_review_confirmed"


@dataclass(frozen=True)
class ReviewState:
    """Last explicit human-review event; never a fiscal-readiness status."""

    pending: bool
    label: str = LABEL
    fecha: datetime | None = None


async def load_review_states(
    session: AsyncSession, expense_ids: Iterable[UUID]
) -> dict[UUID, ReviewState]:
    """Read the latest event for each requested expense in one query."""
    ids = list(expense_ids)
    if not ids:
        return {}
    result = await session.execute(
        select(Aprobacion)
        .where(
            Aprobacion.tipo_entidad == "gasto",
            Aprobacion.entidad_id.in_(ids),
            Aprobacion.accion.in_([REQUESTED, CONFIRMED]),
        )
        .order_by(Aprobacion.fecha.desc(), Aprobacion.id.desc())
    )
    states: dict[UUID, ReviewState] = {}
    for event in result.scalars().all():
        expense_id = cast(UUID, event.entidad_id)
        if expense_id in states:
            continue
        try:
            details = json.loads(str(event.comentario or "{}"))
        except (ValueError, TypeError):
            raise ValueError("Historial de revisión contable inválido.") from None
        if not isinstance(details, dict) or details.get("batch") != BATCH:
            raise ValueError("Historial de revisión contable inválido.")
        states[expense_id] = ReviewState(
            pending=str(event.accion) == REQUESTED,
            fecha=cast(datetime, event.fecha),
        )
    return states


async def _locked_expense(session: AsyncSession, expense_id: UUID) -> ExpenseReport:
    result = await session.execute(
        select(ExpenseReport).where(ExpenseReport.id == expense_id).with_for_update()
    )
    expense = result.scalar_one_or_none()
    if expense is None or expense.estado_gasto != "activo":
        raise ValueError("La revisión requiere un gasto activo existente.")
    return expense


def _event(expense_id: UUID, actor_id: UUID, action: str, previous=None) -> Aprobacion:
    now = datetime.now(UTC)
    if previous is not None and previous.fecha is not None:
        prior = previous.fecha
        prior = (
            prior.replace(tzinfo=UTC) if prior.tzinfo is None else prior.astimezone(UTC)
        )
        now = max(now, prior + timedelta(microseconds=1))
    return Aprobacion(
        tipo_entidad="gasto",
        entidad_id=expense_id,
        aprobador_id=actor_id,
        accion=action,
        fecha=now,
        comentario=json.dumps(
            {
                "batch": BATCH,
                "marker": LABEL,
                "reason": (
                    "Revisión solicitada por el responsable para pagos semanales."
                    if action == REQUESTED
                    else "Contabilidad confirmó explícitamente la revisión."
                ),
            },
            ensure_ascii=False,
        ),
    )


async def request_review(
    session: AsyncSession, expense_id: UUID, actor_id: UUID
) -> bool:
    """Request once; a repeated batch cannot reopen a confirmed review."""
    await _locked_expense(session, expense_id)
    states = await load_review_states(session, [expense_id])
    if expense_id in states:
        return False
    session.add(_event(expense_id, actor_id, REQUESTED))
    await session.flush()
    return True


async def confirm_review(
    session: AsyncSession, expense_id: UUID, actor_id: UUID
) -> bool:
    """Audit a human confirmation, without editing any financial field."""
    await _locked_expense(session, expense_id)
    previous = (await load_review_states(session, [expense_id])).get(expense_id)
    if previous is None:
        raise ValueError("El gasto no tiene una revisión solicitada.")
    if not previous.pending:
        return False
    session.add(_event(expense_id, actor_id, CONFIRMED, previous))
    await session.flush()
    return True


def review_controls(expense_id: UUID, state: ReviewState, csrf_token: str) -> str:
    """Render a separate explicit action; saving COI choices never confirms it."""
    if not state.pending:
        return ""
    return (
        f'<div class="cleanup-pill cleanup-pill-warn">{escape(state.label)}</div>'
        '<form method="POST" '
        f'action="/admin/gastos/{expense_id}/confirmar-revision-contable">'
        '<input type="hidden" name="csrf_token" '
        f'value="{escape(csrf_token, quote=True)}">'
        '<label><input type="checkbox" name="review_confirmed" value="yes" required> '
        "He revisado el CFDI, las cuentas y los impuestos de esta partida.</label>"
        '<button type="submit" class="button secondary">Confirmar revisión</button>'
        "</form>"
    )
