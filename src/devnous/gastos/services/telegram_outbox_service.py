"""Persistent outbox for Telegram document notifications."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Documento, Empleado, TelegramNotificationOutbox
from .telegram_notify import schedule_fire_and_forget, send_telegram_message

logger = logging.getLogger(__name__)

NOTIFICATION_TYPE_LABELS: Dict[str, str] = {
    "workflow_send_approver": "Nueva solicitud de aprobación",
    "workflow_approve_requester": "Documento aprobado (solicitante)",
    "workflow_reject_requester": "Documento rechazado (solicitante)",
    "finance_pending_payment": "Solicitud aprobada — pendiente de pago",
    "solicitud_paid_requester": "Solicitud pagada (solicitante)",
    "solicitud_paid_approver": "Solicitud pagada (aprobador)",
    "finance_odilon_approve": "Odilon aprobó (finanzas)",
}

OUTBOX_CONSOLE_ROLES = frozenset({"finanzas", "admin", "superadmin", "super_admin"})
BODY_PREVIEW_MAX = 240
OUTBOX_RETRY_DELAY_SECONDS = 2 * 60 * 60


def notification_type_label(notification_type: str) -> str:
    return NOTIFICATION_TYPE_LABELS.get(
        notification_type,
        notification_type.replace("_", " ").strip().capitalize(),
    )


def _preview(text: str) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= BODY_PREVIEW_MAX:
        return compact
    return compact[: BODY_PREVIEW_MAX - 1] + "…"


async def create_outbox_entry(
    session: AsyncSession,
    *,
    notification_type: str,
    status: str,
    header_text: str,
    body_text: str,
    documento_id: Optional[UUID] = None,
    recipient_empleado_id: Optional[UUID] = None,
    telegram_chat_id: Optional[int] = None,
    error_message: Optional[str] = None,
) -> TelegramNotificationOutbox:
    now = datetime.utcnow()
    entry = TelegramNotificationOutbox(
        notification_type=notification_type,
        status=status,
        documento_id=documento_id,
        recipient_empleado_id=recipient_empleado_id,
        telegram_chat_id=telegram_chat_id,
        header_text=(header_text or "").strip() or None,
        body_preview=_preview(body_text),
        error_message=(error_message or "").strip() or None,
        created_at=now,
        updated_at=now,
        sent_at=now if status == "sent" else None,
        retry_count=0,
        next_retry_at=None,
    )
    session.add(entry)
    await session.flush()
    return entry


async def mark_outbox_entry(
    session: AsyncSession,
    entry: TelegramNotificationOutbox,
    *,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    entry.status = status
    entry.error_message = (error_message or "").strip() or None
    entry.updated_at = datetime.utcnow()
    if status == "sent":
        entry.sent_at = datetime.utcnow()
        entry.next_retry_at = None


def schedule_outbox_retry(entry_id: UUID) -> None:
    schedule_fire_and_forget(_execute_outbox_retry(entry_id))


async def _mark_outbox_failed(
    session: AsyncSession,
    entry: TelegramNotificationOutbox,
    error_message: str,
) -> None:
    retry_count = int(getattr(entry, "retry_count", 0) or 0)
    await mark_outbox_entry(
        session,
        entry,
        status="failed",
        error_message=error_message,
    )
    if retry_count == 0 and entry.telegram_chat_id is not None:
        entry.next_retry_at = datetime.utcnow() + timedelta(
            seconds=OUTBOX_RETRY_DELAY_SECONDS
        )
        await session.flush()
        schedule_outbox_retry(entry.id)
    else:
        entry.next_retry_at = None


async def _send_outbox_entry(session: AsyncSession, entry: TelegramNotificationOutbox) -> bool:
    text = await rebuild_outbox_message_text(session, entry)
    if not text:
        await _mark_outbox_failed(
            session,
            entry,
            error_message="No se pudo reconstruir el mensaje",
        )
        await session.commit()
        return False

    reply_markup = None
    if (
        entry.notification_type == "workflow_send_approver"
        and entry.documento_id is not None
    ):
        from .documento_telegram import approval_inline_keyboard

        reply_markup = approval_inline_keyboard(entry.documento_id)

    ok = await send_telegram_message(
        int(entry.telegram_chat_id),
        text,
        reply_markup=reply_markup,
    )
    if ok:
        await mark_outbox_entry(session, entry, status="sent")
    else:
        await _mark_outbox_failed(
            session,
            entry,
            error_message="Telegram API no confirmó entrega",
        )
    await session.commit()
    return ok


async def _execute_outbox_retry(entry_id: UUID) -> None:
    await asyncio.sleep(OUTBOX_RETRY_DELAY_SECONDS)
    from .documento_telegram import get_notification_session_maker

    session_maker = get_notification_session_maker()
    if not session_maker:
        logger.warning("Outbox retry skipped; no notification session maker")
        return

    async with session_maker() as session:
        entry = await session.get(TelegramNotificationOutbox, entry_id)
        if entry is None:
            return
        if entry.status != "failed":
            return
        if int(entry.retry_count or 0) >= 1:
            return
        if entry.telegram_chat_id is None:
            return
        entry.retry_count = 1
        await session.flush()
        await _send_outbox_entry(session, entry)


async def deliver_telegram_notification(
    session: AsyncSession,
    *,
    notification_type: str,
    header_text: str,
    text: str,
    chat_id: Optional[int],
    documento_id: Optional[UUID] = None,
    recipient_empleado_id: Optional[UUID] = None,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> bool:
    """Persist outbox row, send via Telegram, update delivery status."""
    if chat_id is None:
        await create_outbox_entry(
            session,
            notification_type=notification_type,
            status="skipped",
            header_text=header_text,
            body_text=text,
            documento_id=documento_id,
            recipient_empleado_id=recipient_empleado_id,
            error_message="Sin telegram_user_id vinculado",
        )
        await session.commit()
        return False

    entry = await create_outbox_entry(
        session,
        notification_type=notification_type,
        status="pending",
        header_text=header_text,
        body_text=text,
        documento_id=documento_id,
        recipient_empleado_id=recipient_empleado_id,
        telegram_chat_id=int(chat_id),
    )
    await session.commit()

    ok = await send_telegram_message(
        int(chat_id),
        text,
        reply_markup=reply_markup,
    )
    if ok:
        await mark_outbox_entry(session, entry, status="sent")
    else:
        await _mark_outbox_failed(
            session,
            entry,
            error_message="Telegram API no confirmó entrega",
        )
    await session.commit()
    return ok


async def outbox_entry_exists(
    session: AsyncSession,
    *,
    notification_type: str,
    documento_id: UUID,
    recipient_empleado_id: UUID,
    statuses: Sequence[str] = ("pending", "sent"),
) -> bool:
    result = await session.execute(
        select(TelegramNotificationOutbox.id).where(
            and_(
                TelegramNotificationOutbox.notification_type == notification_type,
                TelegramNotificationOutbox.documento_id == documento_id,
                TelegramNotificationOutbox.recipient_empleado_id
                == recipient_empleado_id,
                TelegramNotificationOutbox.status.in_(tuple(statuses)),
            )
        )
    )
    return result.scalar_one_or_none() is not None


async def enqueue_finance_pending_payment_outbox(
    session: AsyncSession,
    documento: Documento,
    *,
    header_text: str,
    body_text: str,
) -> int:
    """Create pending outbox rows for finance users (no Telegram send)."""
    if documento.tipo != "SOLICITUD":
        return 0

    result = await session.execute(
        select(Empleado).where(
            Empleado.rol == "finanzas",
            Empleado.activo.is_(True),
        )
    )
    recipients = list(result.scalars().all())
    created = 0
    text = f"{header_text}\n\n{body_text}"
    for recipient in recipients:
        exists = await outbox_entry_exists(
            session,
            notification_type="finance_pending_payment",
            documento_id=documento.id,
            recipient_empleado_id=recipient.id,
        )
        if exists:
            continue
        chat_id = (
            int(recipient.telegram_user_id)
            if recipient.telegram_user_id is not None
            else None
        )
        status = "pending" if chat_id is not None else "skipped"
        error = None if chat_id is not None else "Sin telegram_user_id vinculado"
        await create_outbox_entry(
            session,
            notification_type="finance_pending_payment",
            status=status,
            header_text=header_text,
            body_text=text,
            documento_id=documento.id,
            recipient_empleado_id=recipient.id,
            telegram_chat_id=chat_id,
            error_message=error,
        )
        created += 1
    if created:
        await session.commit()
    return created


async def _load_documento_for_outbox(
    session: AsyncSession, documento_id: UUID
) -> Optional[Documento]:
    result = await session.execute(
        select(Documento)
        .options(
            selectinload(Documento.empleado),
            selectinload(Documento.beneficiario_empleado),
            selectinload(Documento.proveedor_cliente),
        )
        .where(Documento.id == documento_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def rebuild_outbox_message_text(
    session: AsyncSession,
    entry: TelegramNotificationOutbox,
) -> Optional[str]:
    """Rebuild full Telegram body for a queued outbox row."""
    from .documento_telegram import (
        build_documento_telegram_context,
        format_documento_resumen_es,
    )

    header = (entry.header_text or "").strip()
    if not entry.documento_id:
        if header and entry.body_preview:
            return f"{header}\n\n{entry.body_preview}"
        return entry.body_preview

    documento = await _load_documento_for_outbox(session, entry.documento_id)
    if documento is None:
        return None

    ctx = await build_documento_telegram_context(session, documento)
    include_actions = entry.notification_type == "workflow_send_approver"
    body = format_documento_resumen_es(
        documento,
        context=ctx,
        include_actions_hint=include_actions,
    )

    if entry.notification_type == "finance_odilon_approve":
        return header + "\n\n" + body if header else body

    if header:
        return f"{header}\n\n{body}"
    return body


async def flush_pending_outbox_notifications(
    session: AsyncSession,
    *,
    documento_id: Optional[UUID] = None,
    limit: int = 50,
) -> Dict[str, int]:
    """Send pending outbox rows that have a Telegram chat id."""
    stmt = (
        select(TelegramNotificationOutbox)
        .where(
            TelegramNotificationOutbox.status == "pending",
            TelegramNotificationOutbox.telegram_chat_id.isnot(None),
        )
        .order_by(TelegramNotificationOutbox.created_at.asc())
        .limit(limit)
    )
    if documento_id is not None:
        stmt = stmt.where(TelegramNotificationOutbox.documento_id == documento_id)

    result = await session.execute(stmt)
    entries = list(result.scalars().all())
    stats = {"attempted": 0, "sent": 0, "failed": 0, "skipped_rebuild": 0}

    for entry in entries:
        stats["attempted"] += 1
        ok = await _send_outbox_entry(session, entry)
        if ok:
            stats["sent"] += 1
        elif entry.error_message == "No se pudo reconstruir el mensaje":
            stats["skipped_rebuild"] += 1
        else:
            stats["failed"] += 1

    return stats


async def list_outbox_for_console(
    session: AsyncSession,
    viewer: Empleado,
    *,
    limit: int = 40,
) -> List[TelegramNotificationOutbox]:
    stmt = (
        select(TelegramNotificationOutbox)
        .options(
            selectinload(TelegramNotificationOutbox.documento),
            selectinload(TelegramNotificationOutbox.recipient_empleado),
        )
        .outerjoin(Documento, TelegramNotificationOutbox.documento_id == Documento.id)
        .order_by(TelegramNotificationOutbox.created_at.desc())
        .limit(limit)
    )
    role = (viewer.rol or "").strip().lower()
    if role not in OUTBOX_CONSOLE_ROLES:
        stmt = stmt.where(
            or_(
                TelegramNotificationOutbox.recipient_empleado_id == viewer.id,
                Documento.empleado_id == viewer.id,
            )
        )
    result = await session.execute(stmt)
    return list(result.scalars().unique().all())
