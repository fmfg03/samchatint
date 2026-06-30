"""Shared definitions for the Telegram Document Console V1."""

from __future__ import annotations

from html import escape
from typing import Any, Optional, Sequence

from .telegram_outbox_service import notification_type_label

TELEGRAM_DOCUMENT_CONSOLE_PATH = "/panel/telegram-console"
TELEGRAM_SELF_SERVICE_PATH = "/panel/mi-telegram"
TELEGRAM_STATUS_PATH = "/panel/telegram-status"

TELEGRAM_APPROVER_ROLES = frozenset({"finanzas", "admin", "superadmin", "super_admin"})


def is_telegram_document_approver_role(role: Optional[str]) -> bool:
    return (role or "").strip().lower() in TELEGRAM_APPROVER_ROLES


def telegram_console_commands_for_role(role: Optional[str]) -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = [
        ("/mis_solicitudes", "Consulta tus documentos recientes desde Telegram."),
        ("/solicitud REF", "Abre el detalle de un documento propio por referencia."),
    ]
    if is_telegram_document_approver_role(role):
        commands.insert(
            0,
            ("/pendientes", "Abre tu bandeja de aprobaciones con la misma visibilidad que la web."),
        )
    return commands


def telegram_console_action_cards(
    *,
    role: Optional[str],
    is_connected: bool,
) -> list[tuple[str, str, str]]:
    cards: list[tuple[str, str, str]] = [
        (
            TELEGRAM_STATUS_PATH,
            "Estado oficial del canal",
            "Lee el alcance canónico de Telegram, sus fronteras de producto y las superficies oficiales ya activas.",
        ),
        (
            TELEGRAM_SELF_SERVICE_PATH,
            "Vincular ID de Telegram" if not is_connected else "Actualizar ID de Telegram",
            "Guarda o corrige tu identificador numérico para habilitar notificaciones y flujos por bot.",
        ),
        (
            "/documentos/mis-documentos",
            "Mis documentos",
            "Abre la vista web base de tus solicitudes e informes para contrastar el mismo estado que verás en Telegram.",
        ),
    ]
    if is_telegram_document_approver_role(role):
        cards.insert(
            1,
            (
                "/documentos/pendientes",
                "Bandeja web de aprobaciones",
                "Revisa la misma cola que expone Telegram con `/pendientes` y callbacks de aprobar o rechazar.",
            ),
        )
    return cards


def telegram_console_bot_menu_lines() -> list[str]:
    return [
        f"• Consola web oficial: `{TELEGRAM_DOCUMENT_CONSOLE_PATH}` para ver el canal canónico",
        "• `/pendientes` bandeja del aprobador (misma visibilidad que la web)",
        "• `/mis_solicitudes` estado de tus documentos recientes",
        "• `/solicitud REF` detalle de un documento tuyo por referencia",
        f"• `/tgid` ver tu user_id/chat_id y luego vincularlo en `{TELEGRAM_SELF_SERVICE_PATH}`",
    ]


def telegram_console_bot_commands() -> list[dict[str, str]]:
    return [
        {"command": "pendientes", "description": "Bandeja Telegram de aprobaciones (aprobador)"},
        {"command": "mis_solicitudes", "description": "Mis documentos y estado por Telegram"},
        {"command": "solicitud", "description": "Detalle por referencia (/solicitud REF)"},
        {"command": "tgid", "description": "Ver tu user_id/chat_id para vincular Telegram"},
    ]


def telegram_channel_status_snapshot(
    *,
    role: Optional[str],
    is_connected: bool,
) -> dict[str, Any]:
    normalized_role = (role or "empleado").strip().lower() or "empleado"
    is_approver = is_telegram_document_approver_role(normalized_role)
    return {
        "channel": "Telegram",
        "status": "active",
        "official": True,
        "channel_type": "interno",
        "product_scope": "gastos/documentos/aprobaciones",
        "owner_surface": "Telegram Document Console",
        "owner_module": "devnous.gastos + tournaments.telegram_adapter",
        "role_mode": "approver" if is_approver else "requester",
        "employee_link_status": "connected" if is_connected else "pending_link",
        "boundaries": {
            "internal": "Telegram opera como canal interno de plataforma.",
            "external": "WhatsApp queda como canal externo hacia equipos y jugadores.",
        },
        "official_surfaces": [
            TELEGRAM_DOCUMENT_CONSOLE_PATH,
            TELEGRAM_SELF_SERVICE_PATH,
        ],
        "commands": [command for command, _ in telegram_console_commands_for_role(role)],
        "current_capabilities": [
            "consulta de documentos propios",
            "bandeja de aprobaciones por rol",
            "callbacks de aprobar/rechazar",
            "vinculación self-service de identidad Telegram",
        ],
        "out_of_scope": [
            "OCR de torneos",
            "assistant general multi-dominio",
            "bots externos a documentos",
        ],
        "governance_gaps": [
            "separar con más claridad el runtime interno del adapter de torneos",
            "integrar scopes/perfiles más finos por área y edición",
            "cerrar trazabilidad de canal al mismo nivel que web y assistant",
        ],
    }


OUTBOX_STATUS_LABELS = {
    "pending": "Pendiente",
    "sent": "Enviado",
    "failed": "Fallido",
    "skipped": "Omitido",
}

OUTBOX_STATUS_CSS = {
    "pending": "tg-outbox-status-pending",
    "sent": "tg-outbox-status-sent",
    "failed": "tg-outbox-status-failed",
    "skipped": "tg-outbox-status-skipped",
}


def render_telegram_outbox_status_chip(status: Optional[str]) -> str:
    key = (status or "").strip().lower()
    label = OUTBOX_STATUS_LABELS.get(key, key or "—")
    css = OUTBOX_STATUS_CSS.get(key, "tg-outbox-status-skipped")
    return f'<span class="tg-outbox-status {css}">{escape(label)}</span>'


def render_telegram_outbox_table_html(entries: Sequence[Any]) -> str:
    if not entries:
        return (
            '<div class="notice info">Sin notificaciones registradas todavía.</div>'
        )

    rows: list[str] = []
    for entry in entries:
        doc = getattr(entry, "documento", None)
        recipient = getattr(entry, "recipient_empleado", None)
        doc_ref = "—"
        doc_cell = doc_ref
        if doc is not None:
            ref = getattr(doc, "numero_referencia", None) or "—"
            doc_id = getattr(doc, "id", None)
            doc_ref = escape(str(ref))
            if doc_id:
                doc_cell = (
                    f'<a href="/documentos/{escape(str(doc_id))}">{doc_ref}</a>'
                )
            else:
                doc_cell = doc_ref

        recipient_name = (
            escape(getattr(recipient, "nombre", None) or "—")
            if recipient is not None
            else "—"
        )
        created = getattr(entry, "created_at", None)
        created_label = (
            created.strftime("%Y-%m-%d %H:%M") if created is not None else "—"
        )
        type_label = escape(
            notification_type_label(getattr(entry, "notification_type", "") or "")
        )
        header = escape(getattr(entry, "header_text", None) or "")
        preview = escape(getattr(entry, "body_preview", None) or "")
        status_chip = render_telegram_outbox_status_chip(getattr(entry, "status", ""))
        error = getattr(entry, "error_message", None)
        error_html = (
            f'<div class="tg-outbox-error">{escape(str(error))}</div>'
            if error
            else ""
        )
        rows.append(
            f"""
            <tr>
                <td>{created_label}</td>
                <td>{status_chip}</td>
                <td>{type_label}</td>
                <td>{doc_cell}</td>
                <td>{recipient_name}</td>
                <td>
                    <div class="tg-outbox-header">{header}</div>
                    <div class="tg-outbox-preview">{preview}</div>
                    {error_html}
                </td>
            </tr>
            """
        )

    return f"""
        <div class="table-shell">
            <table class="tg-outbox-table">
                <thead>
                    <tr>
                        <th>Creado</th>
                        <th>Estado</th>
                        <th>Tipo</th>
                        <th>Documento</th>
                        <th>Destinatario</th>
                        <th>Contenido</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(rows)}
                </tbody>
            </table>
        </div>
    """
