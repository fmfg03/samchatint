"""
Service layer for the support ticket subsystem.

Routes import from this module so the SQL/auth logic can be unit-tested
in isolation. All public functions operate on the active ``AsyncSession``
provided by the caller and commit before returning so HTTP handlers don't
need to manage transactions themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    Empleado,
    SUPPORT_TICKET_CATEGORIES,
    SUPPORT_TICKET_OPEN_STATUSES,
    SUPPORT_TICKET_PRIORITIES,
    SUPPORT_TICKET_STATUSES,
    SupportTicket,
    SupportTicketComment,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SupportTicketError(Exception):
    """Base error for the support ticket subsystem."""


class SupportTicketNotFoundError(SupportTicketError):
    """Raised when a ticket id does not exist."""


class SupportTicketPermissionError(SupportTicketError):
    """Raised when an empleado tries to access a ticket they don't own."""


class SupportTicketValidationError(SupportTicketError):
    """Raised when input would create an invalid ticket / comment."""


# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------


SUPERADMIN_ROLES: frozenset[str] = frozenset({"superadmin", "super_admin"})
STAFF_ROLES: frozenset[str] = frozenset({"superadmin", "super_admin", "admin"})

MAX_ASUNTO_LEN = 200
MAX_DESCRIPCION_LEN = 8000
MAX_COMMENT_LEN = 8000
MAX_RESOLUTION_LEN = 4000
MAX_PAGE_URL_LEN = 600
MAX_CONTACT_EMAIL_LEN = 200


@dataclass
class AdminTicketSummary:
    """Aggregated counters used in the superadmin dashboard hero."""

    total: int = 0
    open_count: int = 0
    resolved_last_7d: int = 0
    by_estado: dict[str, int] = field(default_factory=dict)
    by_prioridad: dict[str, int] = field(default_factory=dict)


def _normalize_role(empleado: Empleado) -> str:
    return (getattr(empleado, "rol", "") or "").strip().lower()


def _is_staff(empleado: Empleado) -> bool:
    return _normalize_role(empleado) in STAFF_ROLES


def _truncate(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned[:limit]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_ticket_input(
    *,
    asunto: str,
    descripcion: str,
    categoria: str,
    prioridad: str,
) -> tuple[str, str, str, str]:
    """Normalize and validate user-submitted ticket fields.

    Returns a tuple of ``(asunto, descripcion, categoria, prioridad)`` ready
    to persist. Raises :class:`SupportTicketValidationError` on bad input.
    """
    asunto_clean = (asunto or "").strip()
    if not asunto_clean:
        raise SupportTicketValidationError("El asunto es obligatorio.")
    if len(asunto_clean) > MAX_ASUNTO_LEN:
        raise SupportTicketValidationError(
            f"El asunto no puede tener más de {MAX_ASUNTO_LEN} caracteres."
        )

    descripcion_clean = (descripcion or "").strip()
    if not descripcion_clean:
        raise SupportTicketValidationError("La descripción es obligatoria.")
    if len(descripcion_clean) > MAX_DESCRIPCION_LEN:
        raise SupportTicketValidationError(
            f"La descripción no puede tener más de {MAX_DESCRIPCION_LEN} caracteres."
        )

    categoria_norm = (categoria or "").strip().lower()
    if categoria_norm not in SUPPORT_TICKET_CATEGORIES:
        raise SupportTicketValidationError("Categoría inválida.")

    prioridad_norm = (prioridad or "").strip().lower()
    if prioridad_norm not in SUPPORT_TICKET_PRIORITIES:
        raise SupportTicketValidationError("Prioridad inválida.")

    return asunto_clean, descripcion_clean, categoria_norm, prioridad_norm


def validate_comment_body(body: str) -> str:
    cleaned = (body or "").strip()
    if not cleaned:
        raise SupportTicketValidationError("El comentario no puede estar vacío.")
    if len(cleaned) > MAX_COMMENT_LEN:
        raise SupportTicketValidationError(
            f"El comentario no puede tener más de {MAX_COMMENT_LEN} caracteres."
        )
    return cleaned


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def list_my_tickets(
    session: AsyncSession,
    *,
    empleado_id: UUID,
) -> Sequence[SupportTicket]:
    result = await session.execute(
        select(SupportTicket)
        .where(SupportTicket.requester_empleado_id == empleado_id)
        .order_by(SupportTicket.created_at.desc())
    )
    return result.scalars().all()


async def list_admin_tickets(
    session: AsyncSession,
    *,
    estado: Optional[str] = None,
    prioridad: Optional[str] = None,
    categoria: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
) -> Sequence[SupportTicket]:
    query = (
        select(SupportTicket)
        .options(
            selectinload(SupportTicket.requester),
            selectinload(SupportTicket.assignee),
        )
        .order_by(
            SupportTicket.created_at.desc(),
        )
        .limit(limit)
    )
    conditions = []
    if estado and estado in SUPPORT_TICKET_STATUSES:
        conditions.append(SupportTicket.estado == estado)
    if prioridad and prioridad in SUPPORT_TICKET_PRIORITIES:
        conditions.append(SupportTicket.prioridad == prioridad)
    if categoria and categoria in SUPPORT_TICKET_CATEGORIES:
        conditions.append(SupportTicket.categoria == categoria)
    if search:
        like = f"%{search.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(SupportTicket.asunto).like(like),
                func.lower(SupportTicket.descripcion).like(like),
            )
        )
    if conditions:
        query = query.where(and_(*conditions))
    result = await session.execute(query)
    return result.scalars().all()


async def summarize_admin_tickets(session: AsyncSession) -> AdminTicketSummary:
    total = await session.scalar(select(func.count(SupportTicket.id)))
    summary = AdminTicketSummary(total=int(total or 0))

    estado_rows = await session.execute(
        select(SupportTicket.estado, func.count(SupportTicket.id)).group_by(
            SupportTicket.estado
        )
    )
    for estado, count in estado_rows.all():
        summary.by_estado[estado] = int(count or 0)
        if estado in SUPPORT_TICKET_OPEN_STATUSES:
            summary.open_count += int(count or 0)

    prioridad_rows = await session.execute(
        select(SupportTicket.prioridad, func.count(SupportTicket.id)).group_by(
            SupportTicket.prioridad
        )
    )
    for prioridad, count in prioridad_rows.all():
        summary.by_prioridad[prioridad] = int(count or 0)

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    resolved = await session.scalar(
        select(func.count(SupportTicket.id)).where(
            and_(
                SupportTicket.estado.in_(("resuelto", "cerrado")),
                SupportTicket.resolved_at.is_not(None),
                SupportTicket.resolved_at >= cutoff,
            )
        )
    )
    summary.resolved_last_7d = int(resolved or 0)

    return summary


async def list_staff_empleados(session: AsyncSession) -> Sequence[Empleado]:
    """Return active empleados eligible for ticket assignment."""
    result = await session.execute(
        select(Empleado)
        .where(
            and_(
                Empleado.activo.is_(True),
                func.lower(Empleado.rol).in_(STAFF_ROLES),
            )
        )
        .order_by(Empleado.nombre.asc())
    )
    return result.scalars().all()


async def get_ticket_for_user(
    session: AsyncSession,
    *,
    ticket_id: UUID,
    empleado: Empleado,
    include_staff: bool = False,
) -> SupportTicket:
    """Return ticket if the empleado is the requester or staff."""
    result = await session.execute(
        select(SupportTicket)
        .options(
            selectinload(SupportTicket.requester),
            selectinload(SupportTicket.assignee),
            selectinload(SupportTicket.comments).selectinload(
                SupportTicketComment.author
            ),
        )
        .where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise SupportTicketNotFoundError("Ticket no encontrado.")
    if include_staff and _is_staff(empleado):
        return ticket
    if ticket.requester_empleado_id == getattr(empleado, "id", None):
        return ticket
    raise SupportTicketPermissionError("No tienes acceso a este ticket.")


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


async def create_ticket(
    session: AsyncSession,
    *,
    requester_empleado_id: UUID,
    asunto: str,
    descripcion: str,
    categoria: str = "otro",
    prioridad: str = "normal",
    page_url: Optional[str] = None,
    contact_email: Optional[str] = None,
) -> SupportTicket:
    asunto_v, descripcion_v, categoria_v, prioridad_v = validate_ticket_input(
        asunto=asunto,
        descripcion=descripcion,
        categoria=categoria,
        prioridad=prioridad,
    )

    ticket = SupportTicket(
        requester_empleado_id=requester_empleado_id,
        asunto=asunto_v,
        descripcion=descripcion_v,
        categoria=categoria_v,
        prioridad=prioridad_v,
        estado="abierto",
        page_url=_truncate(page_url, MAX_PAGE_URL_LEN),
        contact_email=_truncate(contact_email, MAX_CONTACT_EMAIL_LEN),
    )
    session.add(ticket)
    await session.flush()
    await session.commit()
    await session.refresh(ticket)
    return ticket


async def add_ticket_comment(
    session: AsyncSession,
    *,
    ticket_id: UUID,
    empleado: Empleado,
    body: str,
    is_staff: Optional[bool] = None,
) -> SupportTicketComment:
    cleaned = validate_comment_body(body)

    ticket = await get_ticket_for_user(
        session,
        ticket_id=ticket_id,
        empleado=empleado,
        include_staff=True,
    )

    if is_staff is None:
        is_staff = _is_staff(empleado)

    is_requester = ticket.requester_empleado_id == getattr(empleado, "id", None)
    if not is_staff and not is_requester:
        # Defensive: get_ticket_for_user already enforces this, but keeps the
        # invariant explicit for callers that might pass include_staff=True.
        raise SupportTicketPermissionError("No tienes acceso a este ticket.")

    role_value = "staff" if is_staff and not is_requester else "requester"
    if is_staff and is_requester:
        # Superadmin commenting on their own ticket should still appear as
        # the requester voice; that matches user intuition.
        role_value = "requester"

    comment = SupportTicketComment(
        ticket_id=ticket.id,
        author_empleado_id=getattr(empleado, "id", None),
        author_role=role_value,
        body=cleaned,
    )
    session.add(comment)

    # When staff replies on an open ticket move it to "en_revision" so the
    # admin board reflects activity without requiring a manual triage step.
    if role_value == "staff" and ticket.estado == "abierto":
        ticket.estado = "en_revision"

    ticket.updated_at = datetime.utcnow()

    await session.flush()
    await session.commit()
    await session.refresh(comment)
    return comment


async def update_ticket_admin_fields(
    session: AsyncSession,
    *,
    ticket_id: UUID,
    actor: Empleado,
    estado: str,
    prioridad: str,
    categoria: str,
    assigned_to_empleado_id: Optional[UUID] = None,
    resolution_note: Optional[str] = None,
) -> SupportTicket:
    if not _is_staff(actor):
        raise SupportTicketPermissionError(
            "Solo el equipo de soporte puede modificar tickets."
        )

    estado_norm = (estado or "").strip().lower()
    prioridad_norm = (prioridad or "").strip().lower()
    categoria_norm = (categoria or "").strip().lower()
    if estado_norm not in SUPPORT_TICKET_STATUSES:
        raise SupportTicketValidationError("Estado inválido.")
    if prioridad_norm not in SUPPORT_TICKET_PRIORITIES:
        raise SupportTicketValidationError("Prioridad inválida.")
    if categoria_norm not in SUPPORT_TICKET_CATEGORIES:
        raise SupportTicketValidationError("Categoría inválida.")

    cleaned_resolution = _truncate(resolution_note, MAX_RESOLUTION_LEN)

    result = await session.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise SupportTicketNotFoundError("Ticket no encontrado.")

    changes: List[str] = []
    if ticket.estado != estado_norm:
        changes.append(f"Estado: {ticket.estado} → {estado_norm}")
        ticket.estado = estado_norm
    if ticket.prioridad != prioridad_norm:
        changes.append(f"Prioridad: {ticket.prioridad} → {prioridad_norm}")
        ticket.prioridad = prioridad_norm
    if ticket.categoria != categoria_norm:
        changes.append(f"Categoría: {ticket.categoria} → {categoria_norm}")
        ticket.categoria = categoria_norm

    new_assignee = assigned_to_empleado_id
    if ticket.assigned_to_empleado_id != new_assignee:
        before = (
            str(ticket.assigned_to_empleado_id)
            if ticket.assigned_to_empleado_id
            else "(sin asignar)"
        )
        after = str(new_assignee) if new_assignee else "(sin asignar)"
        changes.append(f"Asignado: {before} → {after}")
        ticket.assigned_to_empleado_id = new_assignee

    previous_resolution = ticket.resolution_note or ""
    if (cleaned_resolution or "") != previous_resolution:
        ticket.resolution_note = cleaned_resolution
        changes.append("Nota de resolución actualizada.")

    if estado_norm in ("resuelto", "cerrado"):
        if ticket.resolved_at is None:
            ticket.resolved_at = datetime.now(timezone.utc)
    else:
        ticket.resolved_at = None

    ticket.updated_at = datetime.utcnow()

    if changes:
        actor_name = getattr(actor, "nombre", None) or "Sistema"
        log_body = f"Actualización de soporte ({actor_name}): " + "; ".join(changes)
        session.add(
            SupportTicketComment(
                ticket_id=ticket.id,
                author_empleado_id=getattr(actor, "id", None),
                author_role="system",
                body=log_body,
            )
        )

    await session.flush()
    await session.commit()
    await session.refresh(ticket)
    return ticket


__all__ = [
    "AdminTicketSummary",
    "SupportTicketError",
    "SupportTicketNotFoundError",
    "SupportTicketPermissionError",
    "SupportTicketValidationError",
    "add_ticket_comment",
    "create_ticket",
    "get_ticket_for_user",
    "list_admin_tickets",
    "list_my_tickets",
    "list_staff_empleados",
    "summarize_admin_tickets",
    "update_ticket_admin_fields",
    "validate_comment_body",
    "validate_ticket_input",
]
