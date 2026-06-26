"""
Support ticket routes for the expense management web app.

Two surfaces are provided:

1. End-user surface (``/soporte``)
   - List the empleado's own tickets.
   - Open a new ticket (``/soporte/nuevo``).
   - View / append a comment to an existing ticket (``/soporte/{id}``).

2. Superadmin dashboard (``/admin/soporte``)
   - List every ticket with status filters and counters.
   - View any ticket, change its status / assignee / priority,
     reply with a staff comment, write a resolution note.

The system is intentionally small (one table for tickets, one for comments)
so it can be deployed without external services. Both surfaces share the
same data layer in :mod:`devnous.gastos.services.support_ticket_service`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Optional
from uuid import UUID as UUIDType

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    Documento,
    Empleado,
    SUPPORT_TICKET_CATEGORIES,
    SUPPORT_TICKET_PRIORITIES,
    SUPPORT_TICKET_STATUSES,
    TelegramNotificationOutbox,
)
from ..services.support_ticket_service import (
    SupportTicketError,
    SupportTicketNotFoundError,
    SupportTicketPermissionError,
    SupportTicketValidationError,
    add_ticket_comment,
    create_ticket,
    get_ticket_for_user,
    list_admin_tickets,
    list_my_tickets,
    list_staff_empleados,
    summarize_admin_tickets,
    update_ticket_admin_fields,
)
from .dependencies import get_current_empleado, get_db_session

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Constants and presentation helpers
# ---------------------------------------------------------------------------

SUPERADMIN_ROLES: frozenset[str] = frozenset({"superadmin", "super_admin"})
STAFF_ROLES: frozenset[str] = frozenset({"superadmin", "super_admin", "admin"})

CATEGORY_LABELS: dict[str, str] = {
    "bug": "Falla / error",
    "duda": "Duda de uso",
    "solicitud": "Solicitud / mejora",
    "acceso": "Acceso o permisos",
    "otro": "Otro",
}
PRIORITY_LABELS: dict[str, str] = {
    "baja": "Baja",
    "normal": "Normal",
    "alta": "Alta",
    "urgente": "Urgente",
}
STATUS_LABELS: dict[str, str] = {
    "abierto": "Abierto",
    "en_revision": "En revisión",
    "en_progreso": "En progreso",
    "resuelto": "Resuelto",
    "cerrado": "Cerrado",
}

# Color tokens reused by both surfaces. Kept inline so the system has no
# extra CSS dependency on top of the existing inline-styled dashboard.
_STATUS_COLOR: dict[str, tuple[str, str]] = {
    "abierto": ("#1d4ed8", "#dbeafe"),
    "en_revision": ("#9a3412", "#fed7aa"),
    "en_progreso": ("#0f766e", "#ccfbf1"),
    "resuelto": ("#166534", "#bbf7d0"),
    "cerrado": ("#475569", "#e2e8f0"),
}
_PRIORITY_COLOR: dict[str, tuple[str, str]] = {
    "baja": ("#475569", "#e2e8f0"),
    "normal": ("#1d4ed8", "#dbeafe"),
    "alta": ("#9a3412", "#fed7aa"),
    "urgente": ("#7f1d1d", "#fecaca"),
}


def _format_dt(value: Optional[datetime]) -> str:
    if not value:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M")


def _badge(text: str, fg: str, bg: str) -> str:
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:999px;'
        f"font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;"
        f'color:{fg};background:{bg};">{escape(text)}</span>'
    )


def _status_badge(estado: str) -> str:
    fg, bg = _STATUS_COLOR.get(estado, ("#0f172a", "#e2e8f0"))
    label = STATUS_LABELS.get(estado, estado.replace("_", " ").title())
    return _badge(label, fg, bg)


def _priority_badge(prioridad: str) -> str:
    fg, bg = _PRIORITY_COLOR.get(prioridad, ("#0f172a", "#e2e8f0"))
    label = PRIORITY_LABELS.get(prioridad, prioridad.title())
    return _badge(label, fg, bg)


def _category_label(categoria: str) -> str:
    return CATEGORY_LABELS.get(categoria, categoria.title())


def _is_superadmin(empleado: Empleado) -> bool:
    return (getattr(empleado, "rol", "") or "").strip().lower() in SUPERADMIN_ROLES


def _ensure_superadmin(empleado: Empleado) -> None:
    if not _is_superadmin(empleado):
        raise HTTPException(
            status_code=403,
            detail="Solo los superadmin pueden abrir el panel de soporte.",
        )


def _safe_uuid(raw: str, *, field: str = "id") -> UUIDType:
    try:
        return UUIDType(str(raw))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"{field} inválido") from exc


def _multiline_to_html(value: str) -> str:
    """Escape and preserve line breaks for plain-text blocks."""
    return escape(value or "").replace("\n", "<br>")


# ---------------------------------------------------------------------------
# System status dashboard (superadmin, /admin/soporte/estado-sistema)
# ---------------------------------------------------------------------------

APPROVAL_AGING_DAYS = 2
PAYMENT_AGING_DAYS = 3
TELEGRAM_FAILURE_HOURS = 72

SOLICITUD_PIPELINE_ESTADOS = (
    "borrador",
    "enviado",
    "aprobado",
    "pagado",
    "rechazado",
    "cerrado",
)

ROLE_COLUMNS = ["empleado", "coordinador", "finanzas", "admin", "superadmin"]

ROUTE_MANIFEST: list[tuple[str, str, str, frozenset[str]]] = [
    ("panel", "/panel", "Panel", frozenset({"*"})),
    ("informes", "/informes-de-gastos", "Informes de gastos", frozenset({"*"})),
    (
        "informe_crear",
        "/informes-de-gastos/crear",
        "Crear informe",
        frozenset({"*"}),
    ),
    (
        "gastos_terceros",
        "/gastos-terceros",
        "Solicitudes de transferencia",
        frozenset({"*"}),
    ),
    (
        "sol_terceros",
        "/documentos/nueva-solicitud-terceros",
        "Nueva solicitud terceros",
        frozenset({"*"}),
    ),
    (
        "sol_personal",
        "/documentos/nueva-solicitud-personal",
        "Nueva solicitud personal",
        frozenset({"*"}),
    ),
    ("informes_de_gastos", "/informes-de-gastos", "Informes de gastos", frozenset({"*"})),
    (
        "pendientes",
        "/documentos/pendientes",
        "Aprobaciones pendientes",
        frozenset({"finanzas", "admin", "superadmin", "super_admin"}),
    ),
    (
        "pendientes_pago",
        "/documentos/pendientes-pago",
        "Pagos pendientes",
        frozenset({"finanzas", "admin", "superadmin", "super_admin"}),
    ),
    (
        "historial_aprob",
        "/documentos/historial-aprobador",
        "Historial aprobaciones",
        frozenset({"finanzas", "admin", "superadmin", "super_admin"}),
    ),
    (
        "todos_docs",
        "/documentos/todos",
        "Todos los documentos",
        frozenset({"coordinador", "finanzas", "admin", "superadmin", "super_admin"}),
    ),
    (
        "admin_gastos",
        "/admin/gastos",
        "Vista contable",
        frozenset({"finanzas", "admin", "superadmin", "super_admin"}),
    ),
    (
        "presupuestos",
        "/admin/presupuestos",
        "Presupuestos",
        frozenset({"finanzas", "admin", "superadmin", "super_admin"}),
    ),
    (
        "sin_cuenta",
        "/admin/gastos/sin-cuenta-contable",
        "Limpieza contable",
        frozenset({"finanzas", "admin", "superadmin", "super_admin"}),
    ),
    (
        "torneos",
        "/admin/torneos",
        "Torneos y proyectos",
        frozenset({"finanzas", "admin", "superadmin", "super_admin"}),
    ),
    (
        "empleados",
        "/admin/empleados",
        "Empleados",
        frozenset({"finanzas", "admin", "superadmin", "super_admin"}),
    ),
    (
        "soporte_admin",
        "/admin/soporte",
        "Panel de soporte",
        frozenset({"superadmin", "super_admin"}),
    ),
]


@dataclass(frozen=True)
class FlowBlocker:
    blocker_id: str
    severity: str
    label: str
    count: int
    fix_hint_url: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_dt(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _days_waiting(since: Optional[datetime], *, now: Optional[datetime] = None) -> int:
    aware = _aware_dt(since)
    if aware is None:
        return 0
    reference = now or _utc_now()
    delta = reference - aware
    return max(0, delta.days)


def _is_approval_stale(
    enviado_en: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    threshold_days: int = APPROVAL_AGING_DAYS,
) -> bool:
    aware = _aware_dt(enviado_en)
    if aware is None:
        return False
    reference = now or _utc_now()
    return (reference - aware) > timedelta(days=threshold_days)


def _is_payment_stale(
    aprobado_en: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    threshold_days: int = PAYMENT_AGING_DAYS,
) -> bool:
    aware = _aware_dt(aprobado_en)
    if aware is None:
        return False
    reference = now or _utc_now()
    return (reference - aware) > timedelta(days=threshold_days)


def _route_visible_for_role(allowed_roles: frozenset[str], role_column: str) -> bool:
    if "*" in allowed_roles:
        return True
    if role_column in allowed_roles:
        return True
    if role_column == "superadmin" and "super_admin" in allowed_roles:
        return True
    return False


def _role_visibility_matrix(
    manifest: list[tuple[str, str, str, frozenset[str]]] = ROUTE_MANIFEST,
    role_columns: list[str] = ROLE_COLUMNS,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for route_id, path, label, allowed in manifest:
        visibility = {
            role: _route_visible_for_role(allowed, role) for role in role_columns
        }
        rows.append(
            {
                "route_id": route_id,
                "path": path,
                "label": label,
                "visibility": visibility,
            }
        )
    return rows


def _severity_badge(severity: str) -> str:
    palette = {
        "error": ("#991b1b", "#fecaca"),
        "warning": ("#92400e", "#fef3c7"),
    }
    fg, bg = palette.get(severity, ("#475569", "#e2e8f0"))
    label = {"error": "Error", "warning": "Advertencia"}.get(
        severity, severity.title()
    )
    return _badge(label, fg, bg)


def _admin_soporte_tab_nav(active: str) -> str:
    tabs = [
        ("tickets", "/admin/soporte", "Tickets"),
        ("estado", "/admin/soporte/estado-sistema", "Estado del sistema"),
    ]
    parts: list[str] = []
    for key, href, label in tabs:
        is_active = key == active
        style = (
            "padding:8px 14px;border-radius:10px;font-size:13px;font-weight:700;"
            "text-decoration:none;"
        )
        if is_active:
            style += "background:#0f766e;color:#fff;"
        else:
            style += "background:#fff;color:#0f766e;border:1px solid #99f6e4;"
        parts.append(f'<a href="{href}" style="{style}">{escape(label)}</a>')
    return (
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">'
        + "".join(parts)
        + "</div>"
    )


async def _solicitud_pipeline_counts(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Documento.estado, func.count())
            .where(Documento.tipo == "SOLICITUD")
            .group_by(Documento.estado)
        )
    ).all()
    counts = {str(estado): int(total) for estado, total in rows}
    return {estado: counts.get(estado, 0) for estado in SOLICITUD_PIPELINE_ESTADOS}


async def _solicitud_approval_aging(
    session: AsyncSession,
    *,
    limit: int = 10,
    now: Optional[datetime] = None,
) -> tuple[int, list[Documento]]:
    reference = now or _utc_now()
    cutoff = reference - timedelta(days=APPROVAL_AGING_DAYS)
    conditions = (
        Documento.tipo == "SOLICITUD",
        Documento.estado == "enviado",
        Documento.enviado_en.isnot(None),
        Documento.enviado_en < cutoff,
    )
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(Documento).where(*conditions)
            )
        ).scalar_one()
        or 0
    )
    result = await session.execute(
        select(Documento)
        .options(selectinload(Documento.empleado))
        .where(*conditions)
        .order_by(Documento.enviado_en.asc())
        .limit(limit)
    )
    return total, list(result.scalars().all())


async def _solicitud_payment_aging(
    session: AsyncSession,
    *,
    limit: int = 10,
    now: Optional[datetime] = None,
) -> tuple[int, list[Documento]]:
    reference = now or _utc_now()
    cutoff = reference - timedelta(days=PAYMENT_AGING_DAYS)
    conditions = (
        Documento.tipo == "SOLICITUD",
        Documento.estado == "aprobado",
        Documento.aprobado_en.isnot(None),
        Documento.aprobado_en < cutoff,
    )
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(Documento).where(*conditions)
            )
        ).scalar_one()
        or 0
    )
    result = await session.execute(
        select(Documento)
        .options(selectinload(Documento.empleado))
        .where(*conditions)
        .order_by(Documento.aprobado_en.asc())
        .limit(limit)
    )
    return total, list(result.scalars().all())


async def _telegram_notification_failures(
    session: AsyncSession,
    *,
    limit: int = 10,
    now: Optional[datetime] = None,
) -> tuple[dict[str, int], list[TelegramNotificationOutbox]]:
    reference = now or _utc_now()
    cutoff = reference - timedelta(hours=TELEGRAM_FAILURE_HOURS)
    grouped = (
        await session.execute(
            select(TelegramNotificationOutbox.notification_type, func.count())
            .where(
                TelegramNotificationOutbox.status.in_(("failed", "skipped")),
                TelegramNotificationOutbox.created_at >= cutoff,
            )
            .group_by(TelegramNotificationOutbox.notification_type)
            .order_by(func.count().desc())
        )
    ).all()
    by_type = {str(ntype): int(total) for ntype, total in grouped}
    result = await session.execute(
        select(TelegramNotificationOutbox)
        .options(
            selectinload(TelegramNotificationOutbox.documento),
            selectinload(TelegramNotificationOutbox.recipient_empleado),
        )
        .where(
            TelegramNotificationOutbox.status.in_(("failed", "skipped")),
            TelegramNotificationOutbox.created_at >= cutoff,
        )
        .order_by(TelegramNotificationOutbox.created_at.desc())
        .limit(limit)
    )
    return by_type, list(result.scalars().all())


def _blocker_is_active(blocker: FlowBlocker) -> bool:
    if blocker.blocker_id == "BLK-NOTIF-INFORME":
        return True
    if blocker.blocker_id == "BLK-FIN-001":
        return blocker.count == 0
    return blocker.count > 0


async def _flow_blockers(session: AsyncSession) -> list[FlowBlocker]:
    missing_approver = int(
        (
            await session.execute(
                select(func.count()).where(
                    Empleado.activo.is_(True),
                    Empleado.aprobador_id.is_(None),
                )
            )
        ).scalar_one()
        or 0
    )
    finanzas_count = int(
        (
            await session.execute(
                select(func.count()).where(
                    Empleado.activo.is_(True),
                    Empleado.rol == "finanzas",
                )
            )
        ).scalar_one()
        or 0
    )
    finanzas_sin_telegram = int(
        (
            await session.execute(
                select(func.count()).where(
                    Empleado.activo.is_(True),
                    Empleado.rol == "finanzas",
                    Empleado.telegram_user_id.is_(None),
                )
            )
        ).scalar_one()
        or 0
    )
    blockers = [
        FlowBlocker(
            blocker_id="BLK-APR-001",
            severity="error",
            label="Empleados activos sin aprobador asignado",
            count=missing_approver,
            fix_hint_url="/admin/empleados",
        ),
        FlowBlocker(
            blocker_id="BLK-FIN-001",
            severity="error",
            label="Usuarios activos con rol finanzas",
            count=finanzas_count,
            fix_hint_url="/admin/empleados",
        ),
        FlowBlocker(
            blocker_id="BLK-TG-FIN",
            severity="warning",
            label="Usuarios finanzas sin Telegram vinculado",
            count=finanzas_sin_telegram,
            fix_hint_url="/panel/mi-telegram",
        ),
        FlowBlocker(
            blocker_id="BLK-NOTIF-INFORME",
            severity="warning",
            label="Informe de gastos: reembolso sin notificación Telegram (gap conocido)",
            count=0,
            fix_hint_url="",
        ),
    ]
    return blockers


# ---------------------------------------------------------------------------
# User-facing routes
# ---------------------------------------------------------------------------


@router.get("/soporte", response_class=HTMLResponse)
async def support_my_tickets(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    creado: Optional[str] = Query(None),
) -> str:
    """List the current empleado's tickets and link to the create form."""
    # Imported here to avoid an import cycle with user_routes (which imports
    # several private renderers from itself; user_routes is a very large
    # module and we don't want support routes to depend on its global state
    # at import time).
    from .user_routes import (  # noqa: WPS433 (intentional local import)
        _render_workspace_hero,
        _workspace_shell_styles,
        render_top_navigation,
    )

    nav = render_top_navigation(current_empleado, "soporte")
    tickets = await list_my_tickets(session, empleado_id=current_empleado.id)

    success_banner = ""
    if creado == "1":
        success_banner = """
        <div role="status" style="margin:0 0 16px;padding:14px 16px;border-radius:12px;
            background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46;font-weight:600;">
            Ticket creado. Te avisaremos por correo cuando haya respuesta.
        </div>
        """

    if tickets:
        rows_html = ""
        for ticket in tickets:
            status_html = _status_badge(ticket.estado)
            priority_html = _priority_badge(ticket.prioridad)
            cat_label = escape(_category_label(ticket.categoria))
            rows_html += f"""
            <tr>
                <td style="padding:10px 12px;font-weight:600;">
                    <a href="/soporte/{ticket.id}" style="color:#0f766e;text-decoration:none;">
                        {escape(ticket.asunto)}
                    </a>
                </td>
                <td style="padding:10px 12px;color:#475569;">{cat_label}</td>
                <td style="padding:10px 12px;">{status_html}</td>
                <td style="padding:10px 12px;">{priority_html}</td>
                <td style="padding:10px 12px;color:#475569;font-variant-numeric:tabular-nums;">
                    {escape(_format_dt(ticket.created_at))}
                </td>
            </tr>
            """
        tickets_section = f"""
        <section class="surface" style="padding:18px;">
            <div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;font-size:14px;">
                    <thead>
                        <tr style="text-align:left;color:#475569;font-size:12px;
                            text-transform:uppercase;letter-spacing:.06em;">
                            <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Asunto</th>
                            <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Categoría</th>
                            <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Estado</th>
                            <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Prioridad</th>
                            <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Creado</th>
                        </tr>
                    </thead>
                    <tbody>{rows_html}</tbody>
                </table>
            </div>
        </section>
        """
    else:
        tickets_section = """
        <section class="surface" style="padding:24px;text-align:center;color:#475569;">
            <div style="font-size:48px;line-height:1;margin-bottom:8px;">📨</div>
            <div style="font-size:18px;font-weight:700;color:#0f172a;margin-bottom:4px;">
                Aún no abres ningún ticket
            </div>
            <p style="margin:0 0 16px;">
                Si necesitas ayuda con la plataforma de gastos, puedes abrir un ticket
                y el equipo de soporte lo atenderá lo antes posible.
            </p>
            <a href="/soporte/nuevo" class="button primary">Abrir mi primer ticket</a>
        </section>
        """

    hero = _render_workspace_hero(
        eyebrow="Soporte",
        title="Mis tickets de soporte",
        description=(
            "Abre un ticket cuando necesites ayuda con la plataforma. "
            "Aquí puedes ver el estado y la respuesta del equipo."
        ),
        actions_html='<a href="/soporte/nuevo" class="button primary">Abrir nuevo ticket</a>',
        side_html=f"""
            <div class="eyebrow">Resumen</div>
            <div style="font-size:20px;font-weight:800;color:var(--shell-ink);margin-bottom:6px;">
                {len(tickets)} ticket{'s' if len(tickets) != 1 else ''}
            </div>
            <div class="section-note">Visible solo para ti y para el equipo de soporte.</div>
        """,
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Mis tickets de soporte - sam.chat</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta charset="utf-8">
        <style>{_workspace_shell_styles("1080px")}</style>
    </head>
    <body>
        <div class="container">
            {nav}
            {success_banner}
            {hero}
            <div class="stack">
                {tickets_section}
            </div>
        </div>
    </body>
    </html>
    """


@router.get("/soporte/nuevo", response_class=HTMLResponse)
async def support_new_ticket_form(
    request: Request,
    current_empleado: Empleado = Depends(get_current_empleado),
    error: Optional[str] = Query(None),
) -> str:
    from .user_routes import (  # noqa: WPS433
        _render_workspace_hero,
        _workspace_shell_styles,
        render_top_navigation,
    )

    nav = render_top_navigation(current_empleado, "soporte")
    error_banner = ""
    if error:
        error_banner = f"""
        <div role="alert" style="margin:0 0 16px;padding:14px 16px;border-radius:12px;
            background:#fef2f2;border:1px solid #fecaca;color:#991b1b;font-weight:600;">
            {escape(error)}
        </div>
        """

    category_options = "".join(
        f'<option value="{escape(value)}">{escape(CATEGORY_LABELS[value])}</option>'
        for value in SUPPORT_TICKET_CATEGORIES
    )
    priority_options = "".join(
        (
            f'<option value="{escape(value)}"'
            + (' selected' if value == 'normal' else '')
            + f'>{escape(PRIORITY_LABELS[value])}</option>'
        )
        for value in SUPPORT_TICKET_PRIORITIES
    )

    contact_default = escape(getattr(current_empleado, "correo", "") or "")

    hero = _render_workspace_hero(
        eyebrow="Soporte",
        title="Abrir nuevo ticket",
        description=(
            "Cuéntanos qué pasa con la plataforma. Cuanto más detalle, "
            "más rápido lo podremos resolver."
        ),
        actions_html='<a href="/soporte" class="button secondary">Cancelar</a>',
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Nuevo ticket de soporte - sam.chat</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta charset="utf-8">
        <style>
            {_workspace_shell_styles("780px")}
            .ticket-form label {{
                display:block;
                font-weight:700;
                margin:14px 0 6px;
                color:var(--shell-ink);
            }}
            .ticket-form input,
            .ticket-form select,
            .ticket-form textarea {{
                width:100%;
                box-sizing:border-box;
                padding:12px;
                border-radius:10px;
                border:1px solid var(--shell-line);
                font-size:15px;
                font-family:inherit;
            }}
            .ticket-form textarea {{ min-height:160px; resize:vertical; }}
            .ticket-form .row {{
                display:grid;
                grid-template-columns:1fr 1fr;
                gap:14px;
            }}
            @media (max-width:640px) {{
                .ticket-form .row {{ grid-template-columns:1fr; }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {nav}
            {error_banner}
            {hero}
            <section class="surface ticket-form" style="padding:22px;">
                <form method="POST" action="/soporte/nuevo">
                    <label for="asunto">Asunto</label>
                    <input id="asunto" name="asunto" type="text" maxlength="200"
                        required placeholder="Resumen breve del problema o solicitud">

                    <div class="row">
                        <div>
                            <label for="categoria">Categoría</label>
                            <select id="categoria" name="categoria" required>
                                {category_options}
                            </select>
                        </div>
                        <div>
                            <label for="prioridad">Prioridad</label>
                            <select id="prioridad" name="prioridad" required>
                                {priority_options}
                            </select>
                        </div>
                    </div>

                    <label for="descripcion">Descripción</label>
                    <textarea id="descripcion" name="descripcion" required
                        placeholder="Pasos para reproducir, qué esperabas que pasara, qué pasó en cambio. Incluye URLs si aplica."></textarea>

                    <div class="row">
                        <div>
                            <label for="page_url">URL relacionada (opcional)</label>
                            <input id="page_url" name="page_url" type="url"
                                placeholder="https://sam.chat/...">
                        </div>
                        <div>
                            <label for="contact_email">Correo de contacto</label>
                            <input id="contact_email" name="contact_email" type="email"
                                value="{contact_default}" placeholder="tu correo">
                        </div>
                    </div>

                    <div style="margin-top:22px;display:flex;gap:10px;flex-wrap:wrap;">
                        <button type="submit" class="button primary">Enviar ticket</button>
                        <a href="/soporte" class="button secondary">Cancelar</a>
                    </div>
                </form>
            </section>
        </div>
    </body>
    </html>
    """


@router.post("/soporte/nuevo")
async def support_new_ticket_submit(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    asunto: str = Form(...),
    descripcion: str = Form(...),
    categoria: str = Form("otro"),
    prioridad: str = Form("normal"),
    page_url: Optional[str] = Form(None),
    contact_email: Optional[str] = Form(None),
) -> RedirectResponse:
    try:
        ticket = await create_ticket(
            session,
            requester_empleado_id=current_empleado.id,
            asunto=asunto,
            descripcion=descripcion,
            categoria=categoria,
            prioridad=prioridad,
            page_url=page_url,
            contact_email=contact_email,
        )
    except SupportTicketValidationError as exc:
        from urllib.parse import quote as _quote

        return RedirectResponse(
            url=f"/soporte/nuevo?error={_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error creating support ticket",
            extra={
                "requester_empleado_id": str(current_empleado.id),
                "categoria": categoria,
                "prioridad": prioridad,
            },
        )
        from urllib.parse import quote as _quote

        return RedirectResponse(
            url=(
                "/soporte/nuevo?error="
                + _quote(
                    "Ocurrió un error al procesar la operación. Intente nuevamente."
                )
            ),
            status_code=303,
        )
    logger.info(
        "support_ticket.created id=%s requester=%s categoria=%s prioridad=%s",
        ticket.id,
        current_empleado.id,
        ticket.categoria,
        ticket.prioridad,
    )
    return RedirectResponse(url="/soporte?creado=1", status_code=303)


@router.get("/soporte/{ticket_id}", response_class=HTMLResponse)
async def support_ticket_detail(
    ticket_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    error: Optional[str] = Query(None),
    ok: Optional[str] = Query(None),
) -> str:
    from .user_routes import (  # noqa: WPS433
        _render_workspace_hero,
        _workspace_shell_styles,
        render_top_navigation,
    )

    ticket_uuid = _safe_uuid(ticket_id, field="ticket_id")
    try:
        ticket = await get_ticket_for_user(
            session,
            ticket_id=ticket_uuid,
            empleado=current_empleado,
            include_staff=_is_superadmin(current_empleado),
        )
    except SupportTicketNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SupportTicketPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    nav = render_top_navigation(current_empleado, "soporte")

    banner = ""
    if error:
        banner = f"""
        <div role="alert" style="margin:0 0 16px;padding:14px 16px;border-radius:12px;
            background:#fef2f2;border:1px solid #fecaca;color:#991b1b;font-weight:600;">
            {escape(error)}
        </div>
        """
    elif ok == "comentado":
        banner = """
        <div role="status" style="margin:0 0 16px;padding:14px 16px;border-radius:12px;
            background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46;font-weight:600;">
            Comentario agregado.
        </div>
        """

    requester_name = escape(
        getattr(ticket.requester, "nombre", "") or "—"
    )
    assignee_name = escape(
        getattr(ticket.assignee, "nombre", "") or "Sin asignar"
    )

    page_url_html = "—"
    if ticket.page_url:
        page_url_html = (
            f'<a href="{escape(ticket.page_url)}" target="_blank" rel="noopener" '
            f'style="color:#1d4ed8;text-decoration:none;">{escape(ticket.page_url)}</a>'
        )

    contact_email_html = escape(ticket.contact_email or "—")

    comments_html = ""
    if ticket.comments:
        comment_blocks = []
        for comment in ticket.comments:
            author_name = escape(
                getattr(comment.author, "nombre", None) or "Sistema"
            )
            role_label = {
                "requester": "Usuario",
                "staff": "Soporte",
                "system": "Sistema",
            }.get(comment.author_role, comment.author_role.title())
            role_color = {
                "requester": "#1d4ed8",
                "staff": "#0f766e",
                "system": "#64748b",
            }.get(comment.author_role, "#64748b")
            comment_blocks.append(f"""
            <article style="border:1px solid #e2e8f0;border-radius:14px;padding:14px 16px;
                background:#ffffff;margin-bottom:10px;">
                <header style="display:flex;justify-content:space-between;
                    align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px;">
                    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                        <strong style="color:#0f172a;">{author_name}</strong>
                        <span style="font-size:11px;font-weight:700;letter-spacing:.06em;
                            text-transform:uppercase;color:{role_color};">
                            {escape(role_label)}
                        </span>
                    </div>
                    <span style="font-size:12px;color:#64748b;font-variant-numeric:tabular-nums;">
                        {escape(_format_dt(comment.created_at))}
                    </span>
                </header>
                <div style="color:#0f172a;line-height:1.55;">
                    {_multiline_to_html(comment.body)}
                </div>
            </article>
            """)
        comments_html = "".join(comment_blocks)
    else:
        comments_html = """
        <div style="padding:14px;color:#64748b;text-align:center;
            background:#f8fafc;border-radius:12px;border:1px dashed #cbd5e1;">
            Aún no hay comentarios. El equipo de soporte responderá pronto.
        </div>
        """

    resolution_html = ""
    if ticket.resolution_note:
        resolution_html = f"""
        <section class="surface" style="padding:18px;border-left:4px solid #15803d;">
            <div class="eyebrow" style="color:#15803d;">Resolución</div>
            <div style="margin-top:6px;color:#0f172a;line-height:1.55;">
                {_multiline_to_html(ticket.resolution_note)}
            </div>
            <div style="margin-top:8px;font-size:12px;color:#64748b;">
                Resuelto el {escape(_format_dt(ticket.resolved_at))}
            </div>
        </section>
        """

    side_html = f"""
        <div class="eyebrow">Detalles</div>
        <dl style="margin:8px 0 0;display:grid;grid-template-columns:auto 1fr;
            gap:6px 12px;font-size:13px;">
            <dt style="color:#64748b;">Estado</dt>
            <dd style="margin:0;">{_status_badge(ticket.estado)}</dd>
            <dt style="color:#64748b;">Prioridad</dt>
            <dd style="margin:0;">{_priority_badge(ticket.prioridad)}</dd>
            <dt style="color:#64748b;">Categoría</dt>
            <dd style="margin:0;color:#0f172a;">{escape(_category_label(ticket.categoria))}</dd>
            <dt style="color:#64748b;">Solicitante</dt>
            <dd style="margin:0;color:#0f172a;">{requester_name}</dd>
            <dt style="color:#64748b;">Asignado a</dt>
            <dd style="margin:0;color:#0f172a;">{assignee_name}</dd>
            <dt style="color:#64748b;">Contacto</dt>
            <dd style="margin:0;color:#0f172a;word-break:break-all;">{contact_email_html}</dd>
            <dt style="color:#64748b;">URL relacionada</dt>
            <dd style="margin:0;color:#0f172a;word-break:break-all;">{page_url_html}</dd>
            <dt style="color:#64748b;">Creado</dt>
            <dd style="margin:0;color:#0f172a;">{escape(_format_dt(ticket.created_at))}</dd>
            <dt style="color:#64748b;">Actualizado</dt>
            <dd style="margin:0;color:#0f172a;">{escape(_format_dt(ticket.updated_at))}</dd>
        </dl>
    """

    hero = _render_workspace_hero(
        eyebrow=f"Ticket #{str(ticket.id)[:8]}",
        title=ticket.asunto,
        description=(
            "Sigue la conversación con el equipo de soporte y agrega información "
            "si lo necesitas."
        ),
        actions_html='<a href="/soporte" class="button secondary">Volver a mis tickets</a>',
        side_html=side_html,
    )

    description_block = f"""
    <section class="surface" style="padding:18px;">
        <div class="eyebrow">Descripción inicial</div>
        <div style="margin-top:6px;color:#0f172a;line-height:1.55;">
            {_multiline_to_html(ticket.descripcion)}
        </div>
    </section>
    """

    can_comment = ticket.estado != "cerrado"
    comment_form_html = ""
    if can_comment:
        comment_form_html = """
        <section class="surface" style="padding:18px;">
            <div class="eyebrow">Agregar comentario</div>
            <form method="POST" action="/soporte/{ticket_id}/comentarios" style="margin-top:10px;">
                <textarea name="body" required placeholder="Escribe aquí..."
                    style="width:100%;min-height:120px;padding:12px;border-radius:10px;
                    border:1px solid var(--shell-line);font-family:inherit;font-size:14px;
                    box-sizing:border-box;resize:vertical;"></textarea>
                <div style="margin-top:12px;">
                    <button type="submit" class="button primary">Enviar comentario</button>
                </div>
            </form>
        </section>
        """.replace("{ticket_id}", str(ticket.id))
    else:
        comment_form_html = """
        <section class="surface" style="padding:14px 18px;color:#64748b;
            text-align:center;background:#f8fafc;border:1px dashed #cbd5e1;">
            Este ticket está cerrado. Si necesitas ayuda con un tema relacionado,
            <a href="/soporte/nuevo" style="color:#0f766e;">abre uno nuevo</a>.
        </section>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Ticket - sam.chat</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta charset="utf-8">
        <style>{_workspace_shell_styles("980px")}</style>
    </head>
    <body>
        <div class="container">
            {nav}
            {banner}
            {hero}
            <div class="stack">
                {description_block}
                {resolution_html}
                <section class="surface" style="padding:18px;">
                    <div class="eyebrow" style="margin-bottom:10px;">Conversación</div>
                    {comments_html}
                </section>
                {comment_form_html}
            </div>
        </div>
    </body>
    </html>
    """


@router.post("/soporte/{ticket_id}/comentarios")
async def support_ticket_add_comment(
    ticket_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    body: str = Form(...),
) -> RedirectResponse:
    ticket_uuid = _safe_uuid(ticket_id, field="ticket_id")
    try:
        await add_ticket_comment(
            session,
            ticket_id=ticket_uuid,
            empleado=current_empleado,
            body=body,
            is_staff=_is_superadmin(current_empleado),
        )
    except SupportTicketNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SupportTicketPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SupportTicketValidationError as exc:
        from urllib.parse import quote as _quote

        return RedirectResponse(
            url=f"/soporte/{ticket_id}?error={_quote(str(exc))}",
            status_code=303,
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error adding support ticket comment",
            extra={
                "ticket_id": ticket_id,
                "empleado_id": str(current_empleado.id),
                "is_staff": _is_superadmin(current_empleado),
            },
        )
        from urllib.parse import quote as _quote

        return RedirectResponse(
            url=(
                f"/soporte/{ticket_id}?error="
                + _quote(
                    "Ocurrió un error al procesar la operación. Intente nuevamente."
                )
            ),
            status_code=303,
        )

    target = "/soporte" if not _is_superadmin(current_empleado) else "/admin/soporte"
    suffix = f"/{ticket_id}?ok=comentado"
    return RedirectResponse(url=f"{target}{suffix}", status_code=303)


# ---------------------------------------------------------------------------
# Superadmin dashboard routes
# ---------------------------------------------------------------------------


@router.get("/admin/soporte", response_class=HTMLResponse)
async def support_admin_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    estado: Optional[str] = Query(None),
    prioridad: Optional[str] = Query(None),
    categoria: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
) -> str:
    _ensure_superadmin(current_empleado)
    from .user_routes import (  # noqa: WPS433
        _render_workspace_hero,
        _workspace_shell_styles,
        render_top_navigation,
    )

    estado_filter = (estado or "").strip().lower() or None
    prioridad_filter = (prioridad or "").strip().lower() or None
    categoria_filter = (categoria or "").strip().lower() or None

    if estado_filter and estado_filter not in SUPPORT_TICKET_STATUSES:
        estado_filter = None
    if prioridad_filter and prioridad_filter not in SUPPORT_TICKET_PRIORITIES:
        prioridad_filter = None
    if categoria_filter and categoria_filter not in SUPPORT_TICKET_CATEGORIES:
        categoria_filter = None

    summary = await summarize_admin_tickets(session)
    tickets = await list_admin_tickets(
        session,
        estado=estado_filter,
        prioridad=prioridad_filter,
        categoria=categoria_filter,
        search=q,
    )

    nav = render_top_navigation(current_empleado, "soporte")

    counters = [
        ("Abiertos", summary.open_count, "#1d4ed8"),
        ("En revisión", summary.by_estado.get("en_revision", 0), "#9a3412"),
        ("En progreso", summary.by_estado.get("en_progreso", 0), "#0f766e"),
        ("Resueltos (7d)", summary.resolved_last_7d, "#15803d"),
        ("Total", summary.total, "#334155"),
    ]
    counters_html = "".join(
        f"""
        <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;
            padding:14px 16px;min-width:160px;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;
                color:#64748b;font-weight:700;">{escape(label)}</div>
            <div style="font-size:28px;font-weight:800;color:{color};
                font-variant-numeric:tabular-nums;line-height:1.1;margin-top:4px;">
                {value}
            </div>
        </div>
        """
        for label, value, color in counters
    )

    estado_options = ['<option value="">Todos los estados</option>'] + [
        (
            f'<option value="{escape(value)}"'
            + (' selected' if estado_filter == value else '')
            + f'>{escape(STATUS_LABELS[value])}</option>'
        )
        for value in SUPPORT_TICKET_STATUSES
    ]
    prioridad_options = ['<option value="">Todas las prioridades</option>'] + [
        (
            f'<option value="{escape(value)}"'
            + (' selected' if prioridad_filter == value else '')
            + f'>{escape(PRIORITY_LABELS[value])}</option>'
        )
        for value in SUPPORT_TICKET_PRIORITIES
    ]
    categoria_options = ['<option value="">Todas las categorías</option>'] + [
        (
            f'<option value="{escape(value)}"'
            + (' selected' if categoria_filter == value else '')
            + f'>{escape(CATEGORY_LABELS[value])}</option>'
        )
        for value in SUPPORT_TICKET_CATEGORIES
    ]

    rows_html = ""
    for ticket in tickets:
        requester_name = escape(getattr(ticket.requester, "nombre", "") or "—")
        assignee_name = escape(getattr(ticket.assignee, "nombre", "") or "—")
        rows_html += f"""
        <tr>
            <td style="padding:10px 12px;font-family:monospace;color:#64748b;">
                {escape(str(ticket.id)[:8])}
            </td>
            <td style="padding:10px 12px;font-weight:600;">
                <a href="/admin/soporte/{ticket.id}" style="color:#0f766e;text-decoration:none;">
                    {escape(ticket.asunto)}
                </a>
            </td>
            <td style="padding:10px 12px;color:#475569;">{requester_name}</td>
            <td style="padding:10px 12px;color:#475569;">
                {escape(_category_label(ticket.categoria))}
            </td>
            <td style="padding:10px 12px;">{_status_badge(ticket.estado)}</td>
            <td style="padding:10px 12px;">{_priority_badge(ticket.prioridad)}</td>
            <td style="padding:10px 12px;color:#475569;">{assignee_name}</td>
            <td style="padding:10px 12px;color:#475569;font-variant-numeric:tabular-nums;">
                {escape(_format_dt(ticket.created_at))}
            </td>
        </tr>
        """

    if not rows_html:
        rows_html = """
        <tr>
            <td colspan="8" style="padding:24px;text-align:center;color:#64748b;">
                No hay tickets con esos filtros.
            </td>
        </tr>
        """

    table_html = f"""
    <section class="surface" style="padding:18px;">
        <form method="GET" action="/admin/soporte" style="display:flex;gap:10px;
            flex-wrap:wrap;align-items:center;margin-bottom:14px;">
            <select name="estado" style="padding:8px 10px;border-radius:10px;
                border:1px solid var(--shell-line);font-size:13px;">
                {''.join(estado_options)}
            </select>
            <select name="prioridad" style="padding:8px 10px;border-radius:10px;
                border:1px solid var(--shell-line);font-size:13px;">
                {''.join(prioridad_options)}
            </select>
            <select name="categoria" style="padding:8px 10px;border-radius:10px;
                border:1px solid var(--shell-line);font-size:13px;">
                {''.join(categoria_options)}
            </select>
            <input type="text" name="q" value="{escape(q or '')}"
                placeholder="Buscar por asunto o descripción"
                style="flex:1;min-width:200px;padding:8px 12px;border-radius:10px;
                border:1px solid var(--shell-line);font-size:13px;">
            <button type="submit" class="button primary">Filtrar</button>
            <a href="/admin/soporte" class="button secondary">Limpiar</a>
        </form>
        <div style="overflow-x:auto;">
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <thead>
                    <tr style="text-align:left;color:#475569;font-size:11px;
                        text-transform:uppercase;letter-spacing:.06em;">
                        <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">ID</th>
                        <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Asunto</th>
                        <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Solicitante</th>
                        <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Categoría</th>
                        <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Estado</th>
                        <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Prioridad</th>
                        <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Asignado</th>
                        <th style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">Creado</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    </section>
    """

    hero = _render_workspace_hero(
        eyebrow="Soporte",
        title="Panel de tickets de soporte",
        description=(
            f"{summary.open_count} abierto{'s' if summary.open_count != 1 else ''}"
            f" de {summary.total} total. Triage, asignación y respuesta del equipo."
        ),
        actions_html=(
            '<a href="/admin/soporte?estado=abierto" class="button primary">Ver solo abiertos</a>'
            '<a href="/admin/soporte/estado-sistema" class="button secondary">Estado del sistema</a>'
        ),
        side_html="""
            <div class="eyebrow">Agenda</div>
            <div style="margin-top:6px;font-size:13px;color:#475569;line-height:1.55;">
                Atiende primero los tickets con prioridad <strong>alta</strong> o
                <strong>urgente</strong>. Los resueltos hace más de 7 días se
                pueden cerrar para mantener la bandeja limpia.
            </div>
        """,
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Panel de soporte - sam.chat</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta charset="utf-8">
        <style>{_workspace_shell_styles("1320px")}</style>
    </head>
    <body>
        <div class="container">
            {nav}
            {_admin_soporte_tab_nav("tickets")}
            {hero}
            <section class="surface" style="padding:14px 18px;">
                <div class="eyebrow" style="margin-bottom:10px;">Resumen</div>
                <div style="display:flex;gap:10px;flex-wrap:wrap;">{counters_html}</div>
            </section>
            {table_html}
        </div>
    </body>
    </html>
    """


@router.get("/admin/soporte/estado-sistema", response_class=HTMLResponse)
async def support_system_status(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
) -> str:
    _ensure_superadmin(current_empleado)
    from .user_routes import (  # noqa: WPS433
        _render_workspace_hero,
        _workspace_shell_styles,
        render_top_navigation,
    )

    pipeline = await _solicitud_pipeline_counts(session)
    approval_total, approval_rows = await _solicitud_approval_aging(session)
    payment_total, payment_rows = await _solicitud_payment_aging(session)
    telegram_by_type, telegram_rows = await _telegram_notification_failures(session)
    blockers = await _flow_blockers(session)
    matrix_rows = _role_visibility_matrix()

    nav = render_top_navigation(current_empleado, "soporte")

    pipeline_labels = {
        "borrador": "Borrador",
        "enviado": "Enviado",
        "aprobado": "Aprobado",
        "pagado": "Pagado",
        "rechazado": "Rechazado",
        "cerrado": "Cerrado",
    }
    pipeline_colors = {
        "borrador": "#475569",
        "enviado": "#1d4ed8",
        "aprobado": "#0f766e",
        "pagado": "#15803d",
        "rechazado": "#991b1b",
        "cerrado": "#64748b",
    }
    pipeline_counters = [
        (
            pipeline_labels.get(estado, estado.title()),
            pipeline.get(estado, 0),
            pipeline_colors.get(estado, "#334155"),
        )
        for estado in SOLICITUD_PIPELINE_ESTADOS
    ]
    pipeline_counters_html = "".join(
        f"""
        <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;
            padding:14px 16px;min-width:140px;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;
                color:#64748b;font-weight:700;">{escape(label)}</div>
            <div style="font-size:28px;font-weight:800;color:{color};
                font-variant-numeric:tabular-nums;line-height:1.1;margin-top:4px;">
                {value}
            </div>
        </div>
        """
        for label, value, color in pipeline_counters
    )

    approval_rows_html = ""
    for doc in approval_rows:
        requester = escape(str(getattr(doc.empleado, "nombre", None) or "—"))
        days = _days_waiting(doc.enviado_en)
        approval_rows_html += f"""
        <tr>
            <td style="padding:8px 10px;font-family:monospace;">{escape(str(doc.numero_referencia))}</td>
            <td style="padding:8px 10px;">{requester}</td>
            <td style="padding:8px 10px;">{days} d</td>
        </tr>
        """

    payment_rows_html = ""
    for doc in payment_rows:
        requester = escape(str(getattr(doc.empleado, "nombre", None) or "—"))
        days = _days_waiting(doc.aprobado_en)
        payment_rows_html += f"""
        <tr>
            <td style="padding:8px 10px;font-family:monospace;">{escape(str(doc.numero_referencia))}</td>
            <td style="padding:8px 10px;">{requester}</td>
            <td style="padding:8px 10px;">{days} d</td>
        </tr>
        """

    telegram_summary_html = ""
    if telegram_by_type:
        for ntype, total in telegram_by_type.items():
            telegram_summary_html += (
                f"<li><strong>{escape(ntype)}</strong>: {total}</li>"
            )
    else:
        telegram_summary_html = "<li>Sin fallos ni omitidos en la ventana.</li>"

    telegram_rows_html = ""
    for telegram_row in telegram_rows:
        doc_ref = "—"
        if telegram_row.documento is not None:
            doc_ref = escape(str(telegram_row.documento.numero_referencia or "—"))
        recipient = escape(
            str(getattr(telegram_row.recipient_empleado, "nombre", None) or "—")
        )
        err_preview = str(telegram_row.error_message or "—")[:120]
        telegram_rows_html += f"""
        <tr>
            <td style="padding:8px 10px;">{escape(str(telegram_row.notification_type or "—"))}</td>
            <td style="padding:8px 10px;">{escape(str(telegram_row.status or "—"))}</td>
            <td style="padding:8px 10px;font-family:monospace;">{doc_ref}</td>
            <td style="padding:8px 10px;">{recipient}</td>
            <td style="padding:8px 10px;font-size:12px;color:#64748b;">
                {escape(err_preview)}
            </td>
        </tr>
        """

    blockers_html = ""
    for blocker in blockers:
        active = _blocker_is_active(blocker)
        hint = ""
        if blocker.fix_hint_url:
            hint = (
                f'<a href="{escape(blocker.fix_hint_url)}" style="color:#0f766e;">'
                f"Ver →</a>"
            )
        elif blocker.blocker_id == "BLK-NOTIF-INFORME":
            hint = "<span style='color:#64748b;'>Solo documentación</span>"
        status_cell = _severity_badge(blocker.severity) if active else _badge(
            "OK", "#166534", "#bbf7d0"
        )
        blockers_html += f"""
        <tr>
            <td style="padding:8px 10px;font-family:monospace;">{escape(blocker.blocker_id)}</td>
            <td style="padding:8px 10px;">{status_cell}</td>
            <td style="padding:8px 10px;">{escape(blocker.label)}</td>
            <td style="padding:8px 10px;">{blocker.count}</td>
            <td style="padding:8px 10px;">{hint}</td>
        </tr>
        """

    matrix_header = "".join(
        f'<th style="padding:8px;text-align:center;">{escape(role)}</th>'
        for role in ROLE_COLUMNS
    )
    matrix_body = ""
    for matrix_row in matrix_rows:
        cells = "".join(
            (
                '<td style="padding:8px;text-align:center;">✅</td>'
                if matrix_row["visibility"][role]
                else '<td style="padding:8px;text-align:center;">⛔</td>'
            )
            for role in ROLE_COLUMNS
        )
        matrix_body += f"""
        <tr>
            <td style="padding:8px 10px;">
                <div style="font-weight:600;">{escape(matrix_row["label"])}</div>
                <div style="font-size:11px;color:#64748b;font-family:monospace;">
                    {escape(matrix_row["path"])}
                </div>
            </td>
            {cells}
        </tr>
        """

    hero = _render_workspace_hero(
        eyebrow="Soporte",
        title="Estado del sistema",
        description=(
            "Pipeline de solicitudes de transferencia, bloqueos de configuración "
            "y visibilidad de páginas por rol."
        ),
        actions_html=(
            '<a href="/admin/soporte" class="button secondary">Volver a tickets</a>'
        ),
        side_html=f"""
            <div class="eyebrow">Umbrales</div>
            <div style="margin-top:6px;font-size:13px;color:#475569;line-height:1.55;">
                Aprobación pendiente &gt; {APPROVAL_AGING_DAYS} días.
                Pago pendiente &gt; {PAYMENT_AGING_DAYS} días.
                Telegram: ventana de {TELEGRAM_FAILURE_HOURS} h.
            </div>
        """,
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Estado del sistema - sam.chat</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta charset="utf-8">
        <style>{_workspace_shell_styles("1320px")}</style>
    </head>
    <body>
        <div class="container">
            {nav}
            {_admin_soporte_tab_nav("estado")}
            {hero}
            <section class="surface" style="padding:14px 18px;margin-bottom:14px;">
                <div class="eyebrow" style="margin-bottom:10px;">Solicitudes de transferencia</div>
                <div style="display:flex;gap:10px;flex-wrap:wrap;">{pipeline_counters_html}</div>
            </section>
            <section class="surface" style="padding:14px 18px;margin-bottom:14px;">
                <div class="section-head" style="margin-bottom:12px;">
                    <h2 style="margin:0;font-size:18px;">Envejecimiento</h2>
                    <div class="section-note">
                        {approval_total} en aprobación &gt; {APPROVAL_AGING_DAYS} d ·
                        {payment_total} en pago &gt; {PAYMENT_AGING_DAYS} d
                    </div>
                </div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;">
                    <div>
                        <h3 style="font-size:14px;margin:0 0 8px;">Aprobación pendiente</h3>
                        <table style="width:100%;border-collapse:collapse;font-size:13px;">
                            <thead><tr style="color:#64748b;font-size:11px;text-transform:uppercase;">
                                <th style="padding:6px 8px;text-align:left;">Ref</th>
                                <th style="padding:6px 8px;text-align:left;">Solicitante</th>
                                <th style="padding:6px 8px;text-align:left;">Espera</th>
                            </tr></thead>
                            <tbody>{approval_rows_html or '<tr><td colspan="3" style="padding:8px;color:#64748b;">Sin casos</td></tr>'}</tbody>
                        </table>
                    </div>
                    <div>
                        <h3 style="font-size:14px;margin:0 0 8px;">Pago pendiente</h3>
                        <table style="width:100%;border-collapse:collapse;font-size:13px;">
                            <thead><tr style="color:#64748b;font-size:11px;text-transform:uppercase;">
                                <th style="padding:6px 8px;text-align:left;">Ref</th>
                                <th style="padding:6px 8px;text-align:left;">Solicitante</th>
                                <th style="padding:6px 8px;text-align:left;">Espera</th>
                            </tr></thead>
                            <tbody>{payment_rows_html or '<tr><td colspan="3" style="padding:8px;color:#64748b;">Sin casos</td></tr>'}</tbody>
                        </table>
                    </div>
                </div>
            </section>
            <section class="surface" style="padding:14px 18px;margin-bottom:14px;">
                <div class="section-head" style="margin-bottom:12px;">
                    <h2 style="margin:0;font-size:18px;">Telegram (últimas {TELEGRAM_FAILURE_HOURS} h)</h2>
                </div>
                <ul style="margin:0 0 12px;padding-left:18px;font-size:13px;">{telegram_summary_html}</ul>
                <table style="width:100%;border-collapse:collapse;font-size:13px;">
                    <thead><tr style="color:#64748b;font-size:11px;text-transform:uppercase;">
                        <th style="padding:6px 8px;text-align:left;">Tipo</th>
                        <th style="padding:6px 8px;text-align:left;">Estado</th>
                        <th style="padding:6px 8px;text-align:left;">Documento</th>
                        <th style="padding:6px 8px;text-align:left;">Destinatario</th>
                        <th style="padding:6px 8px;text-align:left;">Error</th>
                    </tr></thead>
                    <tbody>{telegram_rows_html or '<tr><td colspan="5" style="padding:8px;color:#64748b;">Sin registros</td></tr>'}</tbody>
                </table>
            </section>
            <section class="surface" style="padding:14px 18px;margin-bottom:14px;">
                <div class="section-head" style="margin-bottom:12px;">
                    <h2 style="margin:0;font-size:18px;">Bloqueos del flujo</h2>
                </div>
                <table style="width:100%;border-collapse:collapse;font-size:13px;">
                    <thead><tr style="color:#64748b;font-size:11px;text-transform:uppercase;">
                        <th style="padding:6px 8px;text-align:left;">ID</th>
                        <th style="padding:6px 8px;text-align:left;">Severidad</th>
                        <th style="padding:6px 8px;text-align:left;">Descripción</th>
                        <th style="padding:6px 8px;text-align:left;">Cantidad</th>
                        <th style="padding:6px 8px;text-align:left;">Acción</th>
                    </tr></thead>
                    <tbody>{blockers_html}</tbody>
                </table>
            </section>
            <section class="surface" style="padding:14px 18px;">
                <div class="section-head" style="margin-bottom:12px;">
                    <h2 style="margin:0;font-size:18px;">Visibilidad por rol</h2>
                    <div class="section-note" style="margin-top:6px;">
                        Matriz de visibilidad esperada por rol (no ejecuta las páginas).
                        La verificación de estado en vivo por rol es Tier B.
                    </div>
                </div>
                <!-- TODO (Tier B): live per-role probe status -->
                <div style="overflow-x:auto;">
                    <table style="width:100%;border-collapse:collapse;font-size:13px;">
                        <thead><tr style="color:#64748b;font-size:11px;text-transform:uppercase;">
                            <th style="padding:8px;text-align:left;">Ruta</th>
                            {matrix_header}
                        </tr></thead>
                        <tbody>{matrix_body}</tbody>
                    </table>
                </div>
            </section>
        </div>
    </body>
    </html>
    """


@router.get("/admin/soporte/{ticket_id}", response_class=HTMLResponse)
async def support_admin_ticket_detail(
    ticket_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    error: Optional[str] = Query(None),
    ok: Optional[str] = Query(None),
) -> str:
    _ensure_superadmin(current_empleado)
    from .user_routes import (  # noqa: WPS433
        _render_workspace_hero,
        _workspace_shell_styles,
        render_top_navigation,
    )

    ticket_uuid = _safe_uuid(ticket_id, field="ticket_id")
    try:
        ticket = await get_ticket_for_user(
            session,
            ticket_id=ticket_uuid,
            empleado=current_empleado,
            include_staff=True,
        )
    except SupportTicketNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    nav = render_top_navigation(current_empleado, "soporte")

    banner = ""
    if error:
        banner = f"""
        <div role="alert" style="margin:0 0 16px;padding:14px 16px;border-radius:12px;
            background:#fef2f2;border:1px solid #fecaca;color:#991b1b;font-weight:600;">
            {escape(error)}
        </div>
        """
    elif ok == "actualizado":
        banner = """
        <div role="status" style="margin:0 0 16px;padding:14px 16px;border-radius:12px;
            background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46;font-weight:600;">
            Ticket actualizado.
        </div>
        """
    elif ok == "comentado":
        banner = """
        <div role="status" style="margin:0 0 16px;padding:14px 16px;border-radius:12px;
            background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46;font-weight:600;">
            Comentario publicado.
        </div>
        """

    staff_options_rows = await list_staff_empleados(session)

    estado_options_html = "".join(
        (
            f'<option value="{escape(value)}"'
            + (' selected' if value == ticket.estado else '')
            + f'>{escape(STATUS_LABELS[value])}</option>'
        )
        for value in SUPPORT_TICKET_STATUSES
    )
    prioridad_options_html = "".join(
        (
            f'<option value="{escape(value)}"'
            + (' selected' if value == ticket.prioridad else '')
            + f'>{escape(PRIORITY_LABELS[value])}</option>'
        )
        for value in SUPPORT_TICKET_PRIORITIES
    )
    categoria_options_html = "".join(
        (
            f'<option value="{escape(value)}"'
            + (' selected' if value == ticket.categoria else '')
            + f'>{escape(CATEGORY_LABELS[value])}</option>'
        )
        for value in SUPPORT_TICKET_CATEGORIES
    )
    assignee_options = [
        '<option value="">Sin asignar</option>'
    ]
    for staff in staff_options_rows:
        selected = (
            ' selected'
            if ticket.assigned_to_empleado_id == staff.id
            else ''
        )
        assignee_options.append(
            f'<option value="{escape(str(staff.id))}"{selected}>'
            f'{escape(staff.nombre or staff.correo or str(staff.id))}'
            f'</option>'
        )
    assignee_options_html = "".join(assignee_options)

    requester_name = escape(getattr(ticket.requester, "nombre", "") or "—")
    requester_email = escape(getattr(ticket.requester, "correo", "") or "—")

    page_url_html = "—"
    if ticket.page_url:
        page_url_html = (
            f'<a href="{escape(ticket.page_url)}" target="_blank" rel="noopener" '
            f'style="color:#1d4ed8;">{escape(ticket.page_url)}</a>'
        )

    contact_email_html = escape(ticket.contact_email or "—")

    comments_html = ""
    for comment in ticket.comments:
        author_name = escape(
            getattr(comment.author, "nombre", None) or "Sistema"
        )
        role_label = {
            "requester": "Usuario",
            "staff": "Soporte",
            "system": "Sistema",
        }.get(comment.author_role, comment.author_role.title())
        role_color = {
            "requester": "#1d4ed8",
            "staff": "#0f766e",
            "system": "#64748b",
        }.get(comment.author_role, "#64748b")
        comments_html += f"""
        <article style="border:1px solid #e2e8f0;border-radius:14px;padding:14px 16px;
            background:#ffffff;margin-bottom:10px;">
            <header style="display:flex;justify-content:space-between;
                align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px;">
                <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                    <strong style="color:#0f172a;">{author_name}</strong>
                    <span style="font-size:11px;font-weight:700;letter-spacing:.06em;
                        text-transform:uppercase;color:{role_color};">{escape(role_label)}</span>
                </div>
                <span style="font-size:12px;color:#64748b;font-variant-numeric:tabular-nums;">
                    {escape(_format_dt(comment.created_at))}
                </span>
            </header>
            <div style="color:#0f172a;line-height:1.55;">
                {_multiline_to_html(comment.body)}
            </div>
        </article>
        """
    if not comments_html:
        comments_html = """
        <div style="padding:14px;color:#64748b;text-align:center;background:#f8fafc;
            border-radius:12px;border:1px dashed #cbd5e1;">
            Sin comentarios todavía.
        </div>
        """

    side_html = f"""
        <div class="eyebrow">Detalles</div>
        <dl style="margin:8px 0 0;display:grid;grid-template-columns:auto 1fr;
            gap:6px 12px;font-size:13px;">
            <dt style="color:#64748b;">ID</dt>
            <dd style="margin:0;font-family:monospace;color:#0f172a;">
                {escape(str(ticket.id))}
            </dd>
            <dt style="color:#64748b;">Solicitante</dt>
            <dd style="margin:0;color:#0f172a;">{requester_name}</dd>
            <dt style="color:#64748b;">Correo</dt>
            <dd style="margin:0;color:#0f172a;word-break:break-all;">{requester_email}</dd>
            <dt style="color:#64748b;">Contacto</dt>
            <dd style="margin:0;color:#0f172a;word-break:break-all;">{contact_email_html}</dd>
            <dt style="color:#64748b;">URL</dt>
            <dd style="margin:0;color:#0f172a;word-break:break-all;">{page_url_html}</dd>
            <dt style="color:#64748b;">Creado</dt>
            <dd style="margin:0;color:#0f172a;">{escape(_format_dt(ticket.created_at))}</dd>
            <dt style="color:#64748b;">Actualizado</dt>
            <dd style="margin:0;color:#0f172a;">{escape(_format_dt(ticket.updated_at))}</dd>
        </dl>
    """

    hero = _render_workspace_hero(
        eyebrow=f"Ticket #{str(ticket.id)[:8]}",
        title=ticket.asunto,
        description=(
            "Triage, asignación y respuesta del equipo. Los cambios quedan "
            "registrados en el hilo del ticket."
        ),
        actions_html='<a href="/admin/soporte" class="button secondary">Volver al panel</a>',
        side_html=side_html,
    )

    description_block = f"""
    <section class="surface" style="padding:18px;">
        <div class="eyebrow">Descripción inicial</div>
        <div style="margin-top:6px;color:#0f172a;line-height:1.55;">
            {_multiline_to_html(ticket.descripcion)}
        </div>
    </section>
    """

    triage_form = f"""
    <section class="surface" style="padding:18px;">
        <div class="eyebrow" style="margin-bottom:10px;">Triage</div>
        <form method="POST" action="/admin/soporte/{ticket.id}/actualizar"
            style="display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));
            gap:14px;align-items:flex-end;">
            <div>
                <label style="display:block;font-size:12px;font-weight:700;
                    text-transform:uppercase;letter-spacing:.06em;color:#475569;
                    margin-bottom:4px;">Estado</label>
                <select name="estado" style="width:100%;padding:10px;border-radius:10px;
                    border:1px solid var(--shell-line);">{estado_options_html}</select>
            </div>
            <div>
                <label style="display:block;font-size:12px;font-weight:700;
                    text-transform:uppercase;letter-spacing:.06em;color:#475569;
                    margin-bottom:4px;">Prioridad</label>
                <select name="prioridad" style="width:100%;padding:10px;border-radius:10px;
                    border:1px solid var(--shell-line);">{prioridad_options_html}</select>
            </div>
            <div>
                <label style="display:block;font-size:12px;font-weight:700;
                    text-transform:uppercase;letter-spacing:.06em;color:#475569;
                    margin-bottom:4px;">Categoría</label>
                <select name="categoria" style="width:100%;padding:10px;border-radius:10px;
                    border:1px solid var(--shell-line);">{categoria_options_html}</select>
            </div>
            <div>
                <label style="display:block;font-size:12px;font-weight:700;
                    text-transform:uppercase;letter-spacing:.06em;color:#475569;
                    margin-bottom:4px;">Asignar a</label>
                <select name="assigned_to_empleado_id" style="width:100%;padding:10px;
                    border-radius:10px;border:1px solid var(--shell-line);">
                    {assignee_options_html}
                </select>
            </div>
            <div style="grid-column:1/-1;">
                <label style="display:block;font-size:12px;font-weight:700;
                    text-transform:uppercase;letter-spacing:.06em;color:#475569;
                    margin-bottom:4px;">Nota de resolución (opcional)</label>
                <textarea name="resolution_note" rows="3" placeholder="Resumen de la solución..."
                    style="width:100%;padding:10px;border-radius:10px;border:1px solid var(--shell-line);
                    box-sizing:border-box;font-family:inherit;font-size:14px;resize:vertical;"
                >{escape(ticket.resolution_note or "")}</textarea>
            </div>
            <div style="grid-column:1/-1;">
                <button type="submit" class="button primary">Guardar cambios</button>
            </div>
        </form>
    </section>
    """

    reply_form = f"""
    <section class="surface" style="padding:18px;">
        <div class="eyebrow" style="margin-bottom:10px;">Responder al usuario</div>
        <form method="POST" action="/soporte/{ticket.id}/comentarios">
            <textarea name="body" required placeholder="Escribe la respuesta de soporte..."
                style="width:100%;min-height:140px;padding:12px;border-radius:10px;
                border:1px solid var(--shell-line);font-family:inherit;font-size:14px;
                box-sizing:border-box;resize:vertical;"></textarea>
            <div style="margin-top:12px;">
                <button type="submit" class="button primary">Enviar comentario</button>
            </div>
        </form>
    </section>
    """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Ticket {escape(str(ticket.id)[:8])} - sam.chat</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta charset="utf-8">
        <style>{_workspace_shell_styles("1100px")}</style>
    </head>
    <body>
        <div class="container">
            {nav}
            {banner}
            {hero}
            <div class="stack">
                {description_block}
                {triage_form}
                <section class="surface" style="padding:18px;">
                    <div class="eyebrow" style="margin-bottom:10px;">Conversación</div>
                    {comments_html}
                </section>
                {reply_form}
            </div>
        </div>
    </body>
    </html>
    """


@router.post("/admin/soporte/{ticket_id}/actualizar")
async def support_admin_update_ticket(
    ticket_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = Depends(get_current_empleado),
    estado: str = Form(...),
    prioridad: str = Form(...),
    categoria: str = Form(...),
    assigned_to_empleado_id: Optional[str] = Form(None),
    resolution_note: Optional[str] = Form(None),
) -> RedirectResponse:
    _ensure_superadmin(current_empleado)
    ticket_uuid = _safe_uuid(ticket_id, field="ticket_id")

    assignee_uuid: Optional[UUIDType] = None
    if assigned_to_empleado_id:
        try:
            assignee_uuid = UUIDType(assigned_to_empleado_id)
        except ValueError:
            from urllib.parse import quote as _quote

            return RedirectResponse(
                url=f"/admin/soporte/{ticket_id}?error={_quote('Asignado inválido')}",
                status_code=303,
            )

    try:
        await update_ticket_admin_fields(
            session,
            ticket_id=ticket_uuid,
            actor=current_empleado,
            estado=estado,
            prioridad=prioridad,
            categoria=categoria,
            assigned_to_empleado_id=assignee_uuid,
            resolution_note=resolution_note,
        )
    except SupportTicketNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SupportTicketValidationError as exc:
        from urllib.parse import quote as _quote

        return RedirectResponse(
            url=f"/admin/soporte/{ticket_id}?error={_quote(str(exc))}",
            status_code=303,
        )
    except SupportTicketError:
        await session.rollback()
        logger.exception("support_ticket.update_failed id=%s", ticket_id)
        from urllib.parse import quote as _quote

        return RedirectResponse(
            url=(
                f"/admin/soporte/{ticket_id}?error="
                + _quote(
                    "Ocurrió un error al procesar la operación. Intente nuevamente."
                )
            ),
            status_code=303,
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "Unexpected error updating support ticket",
            extra={
                "ticket_id": ticket_id,
                "actor_id": str(current_empleado.id),
            },
        )
        from urllib.parse import quote as _quote

        return RedirectResponse(
            url=(
                f"/admin/soporte/{ticket_id}?error="
                + _quote(
                    "Ocurrió un error al procesar la operación. Intente nuevamente."
                )
            ),
            status_code=303,
        )

    return RedirectResponse(
        url=f"/admin/soporte/{ticket_id}?ok=actualizado",
        status_code=303,
    )


__all__ = ["router"]
