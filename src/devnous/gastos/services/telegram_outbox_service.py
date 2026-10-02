"""Persistent outbox for Telegram document notifications."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Adjunto, Documento, Empleado, TelegramNotificationOutbox
from .telegram_notify import schedule_fire_and_forget, send_telegram_message

logger = logging.getLogger(__name__)

NOTIFICATION_TYPE_LABELS: Dict[str, str] = {
    "workflow_send_approver": "Nueva solicitud de aprobación",
    "budget_control_pending": "Control Presupuestal pendiente",
    "workflow_notification_monitor_alert": "Alerta monitor Telegram",
    "workflow_approve_requester": "Documento aprobado (solicitante)",
    "workflow_reject_requester": "Documento rechazado (solicitante)",
    "finance_pending_payment": "Solicitud aprobada — pendiente de pago",
    "solicitud_paid_requester": "Solicitud pagada (solicitante)",
    "solicitud_paid_approver": "Solicitud pagada (aprobador)",
    "finance_odilon_approve": "Odilon aprobó (finanzas)",
    "beneficiary_onboarding_area_review": "Alta de beneficiario (área)",
    "beneficiary_onboarding_final_review": "Alta de beneficiario (revisión final)",
    "beneficiary_onboarding_decision": "Alta de beneficiario (decisión)",
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
    now = _outbox_now(notification_type)
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


async def find_outbox_entry(
    session: AsyncSession,
    *,
    notification_type: str,
    documento_id: Optional[UUID],
    recipient_empleado_id: Optional[UUID],
) -> Optional[TelegramNotificationOutbox]:
    """Return the latest outbox row for the logical notification key."""
    filters = [TelegramNotificationOutbox.notification_type == notification_type]
    if documento_id is None:
        filters.append(TelegramNotificationOutbox.documento_id.is_(None))
    else:
        filters.append(TelegramNotificationOutbox.documento_id == documento_id)
    if recipient_empleado_id is None:
        filters.append(TelegramNotificationOutbox.recipient_empleado_id.is_(None))
    else:
        filters.append(
            TelegramNotificationOutbox.recipient_empleado_id == recipient_empleado_id
        )
    result = await session.execute(
        select(TelegramNotificationOutbox)
        .where(and_(*filters))
        .order_by(TelegramNotificationOutbox.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def mark_outbox_entry(
    session: AsyncSession,
    entry: TelegramNotificationOutbox,
    *,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    entry.status = status
    entry.error_message = (error_message or "").strip() or None
    entry.updated_at = _outbox_now(getattr(entry, "notification_type", ""))
    if status == "sent":
        entry.sent_at = _outbox_now(getattr(entry, "notification_type", ""))
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
        entry.next_retry_at = _outbox_now(
            getattr(entry, "notification_type", "")
        ) + timedelta(seconds=OUTBOX_RETRY_DELAY_SECONDS)
        await session.flush()
        schedule_outbox_retry(entry.id)
    else:
        entry.next_retry_at = None


def _outbox_now(notification_type: str) -> datetime:
    """Keep finance timestamps timezone-aware without changing other send paths."""
    if notification_type == "finance_pending_payment":
        return datetime.now(timezone.utc)
    return datetime.utcnow()


def _as_utc(value: datetime) -> datetime:
    """Interpret legacy naive timestamps as UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def _lock_finance_entry(
    session: AsyncSession, entry_id: UUID
) -> Optional[TelegramNotificationOutbox]:
    result = await session.execute(
        select(TelegramNotificationOutbox)
        .where(
            TelegramNotificationOutbox.id == entry_id,
            TelegramNotificationOutbox.notification_type == "finance_pending_payment",
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _finance_skip_reason(
    session: AsyncSession, entry: TelegramNotificationOutbox
) -> Optional[str]:
    result = await session.execute(
        select(
            Documento.tipo,
            Documento.estado,
            Documento.fecha_pago_efectiva,
            Documento.pagado_en,
        ).where(Documento.id == entry.documento_id)
    )
    documento = result.one_or_none()
    if (
        documento is None
        or documento.tipo != "SOLICITUD"
        or documento.estado != "aprobado"
        or documento.fecha_pago_efectiva is not None
        or documento.pagado_en is not None
    ):
        return "Solicitud ya no elegible para aviso de pago pendiente"
    proof = await session.execute(
        select(Adjunto.id)
        .where(
            Adjunto.documento_id == entry.documento_id,
            Adjunto.categoria == "comprobante_pago",
        )
        .limit(1)
    )
    if proof.scalar_one_or_none() is not None:
        return "Solicitud con evidencia de pago"
    result = await session.execute(
        select(Empleado.activo, Empleado.rol, Empleado.telegram_user_id).where(
            Empleado.id == entry.recipient_empleado_id
        )
    )
    recipient = result.one_or_none()
    if recipient is None or not recipient.activo or recipient.rol != "finanzas":
        return "Destinatario ya no es Finanzas activo"
    if recipient.telegram_user_id is None:
        entry.telegram_chat_id = None
        return "Sin telegram_user_id vinculado"
    entry.telegram_chat_id = int(recipient.telegram_user_id)
    return None


async def _send_finance_outbox_entry(
    session: AsyncSession,
    entry_id: UUID,
    *,
    mode: str = "pending",
    stale_before: Optional[datetime] = None,
    retry_due_before: Optional[datetime] = None,
    force_resend: bool = False,
    text: Optional[str] = None,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> str:
    """Hold the canonical row lock through validation, HTTP, and result commit."""
    force_resend = force_resend and mode == "delivery"
    entry = await _lock_finance_entry(session, entry_id)
    if entry is None:
        await session.commit()
        return "busy"
    if entry.status == "sent" and not force_resend:
        await session.commit()
        return "already_sent"
    if mode == "recovery":
        if entry.status == "failed":
            if (
                entry.next_retry_at is None
                or retry_due_before is None
                or _as_utc(entry.next_retry_at) > _as_utc(retry_due_before)
            ):
                await session.commit()
                return "skipped"
            mode = "retry"
        else:
            mode = "pending"
    allowed = {"failed"} if mode == "retry" else {"pending"}
    if mode == "delivery":
        allowed |= {"failed", "skipped"}
        if force_resend:
            allowed.add("sent")
    if entry.status not in allowed:
        await session.commit()
        return "skipped"
    if stale_before is not None and mode != "retry":
        activity = entry.updated_at or entry.created_at
        if _as_utc(activity) >= _as_utc(stale_before):
            await session.commit()
            return "skipped"
    if mode == "retry":
        retry_clock = _as_utc(retry_due_before or datetime.now(timezone.utc))
        if (
            entry.next_retry_at is not None
            and _as_utc(entry.next_retry_at) > retry_clock
        ):
            await session.commit()
            return "skipped"
        if int(entry.retry_count or 0) >= 1:
            await session.commit()
            return "skipped"
    original_status = entry.status
    original_sent_at = getattr(entry, "sent_at", None)
    original_updated_at = entry.updated_at
    resend_ready = False
    try:
        document_lock = await session.execute(
            select(Documento.id)
            .where(Documento.id == entry.documento_id)
            .with_for_update(skip_locked=True)
        )
        if document_lock.scalar_one_or_none() is None:
            # Missing documents are ineligible; occupied ones are retried later.
            exists = await session.execute(
                select(Documento.id).where(Documento.id == entry.documento_id)
            )
            if exists.scalar_one_or_none() is not None:
                if mode == "retry":
                    entry.next_retry_at = _outbox_now(
                        entry.notification_type
                    ) + timedelta(seconds=OUTBOX_RETRY_DELAY_SECONDS)
                await session.commit()
                return "busy"
        reason = await _finance_skip_reason(session, entry)
        if reason is None and not force_resend:
            duplicate = await session.execute(
                select(TelegramNotificationOutbox.id)
                .where(
                    TelegramNotificationOutbox.notification_type
                    == "finance_pending_payment",
                    TelegramNotificationOutbox.documento_id == entry.documento_id,
                    TelegramNotificationOutbox.id != entry.id,
                    TelegramNotificationOutbox.telegram_chat_id
                    == entry.telegram_chat_id,
                    TelegramNotificationOutbox.status == "sent",
                )
                .limit(1)
            )
            if duplicate.scalar_one_or_none() is not None:
                reason = "Aviso ya enviado al mismo chat para esta solicitud"
        if reason:
            if force_resend and entry.status == "sent":
                # Preserve the historical delivery receipt when a manual resend
                # is no longer eligible; audit the current rejection separately.
                entry.error_message = reason
                entry.updated_at = _outbox_now(entry.notification_type)
            else:
                await mark_outbox_entry(
                    session, entry, status="skipped", error_message=reason
                )
                entry.next_retry_at = None
            await session.commit()
            return "skipped"
        resend_ready = True
        if force_resend:
            entry.retry_count = 0
            entry.sent_at = None
            entry.next_retry_at = None
        if mode == "retry":
            entry.retry_count = 1
        # Finance bodies always reflect the locked document's current data.
        message = await rebuild_outbox_message_text(session, entry)
        if not message:
            raise ValueError("message rebuild failed")
        ok = await send_telegram_message(
            int(entry.telegram_chat_id), message, reply_markup=reply_markup
        )
    except Exception:
        # Rebuild can fail in SQL; reacquire after rollback before persisting failure.
        await session.rollback()
        entry = await _lock_finance_entry(session, entry_id)
        if entry is None:
            await session.commit()
            return "busy"
        if entry.status == "sent" and (
            not force_resend
            or original_status != "sent"
            or entry.sent_at != original_sent_at
            or entry.updated_at != original_updated_at
        ):
            # Rollback released our locks. A newer delivery receipt belongs to
            # the competing sender and must survive our failed resend attempt.
            await session.commit()
            return "already_sent"
        if force_resend and entry.status == "sent" and not resend_ready:
            entry.error_message = "Error al validar elegibilidad del reenvío"
            entry.updated_at = _outbox_now(entry.notification_type)
            await session.commit()
            return "failed"
        if force_resend:
            entry.retry_count = 0
            entry.sent_at = None
            entry.next_retry_at = None
        if mode == "retry":
            entry.retry_count = 1
        await _mark_outbox_failed(session, entry, "Error al reconstruir o enviar aviso")
        await session.commit()
        return "failed"
    if ok:
        await mark_outbox_entry(session, entry, status="sent")
    else:
        await _mark_outbox_failed(session, entry, "Telegram API no confirmó entrega")
    await session.commit()
    return "sent" if ok else "failed"


async def recover_stale_finance_pending_notifications(
    session: AsyncSession,
    *,
    older_than_minutes: int = 5,
    limit: int = 100,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Recover a bounded oldest-first batch of interrupted finance deliveries."""
    current_time = _as_utc(now or datetime.now(timezone.utc))
    cutoff = current_time - timedelta(minutes=max(5, older_than_minutes))
    activity = func.coalesce(
        TelegramNotificationOutbox.updated_at, TelegramNotificationOutbox.created_at
    )
    result = await session.execute(
        select(TelegramNotificationOutbox.id)
        .where(
            TelegramNotificationOutbox.notification_type == "finance_pending_payment",
            or_(
                and_(
                    TelegramNotificationOutbox.status == "pending",
                    activity < cutoff,
                ),
                and_(
                    TelegramNotificationOutbox.status == "failed",
                    TelegramNotificationOutbox.retry_count == 0,
                    TelegramNotificationOutbox.next_retry_at.isnot(None),
                    TelegramNotificationOutbox.next_retry_at <= current_time,
                ),
            ),
        )
        .order_by(activity.asc(), TelegramNotificationOutbox.id.asc())
        .limit(max(1, min(limit, 500)))
    )
    entry_ids = list(result.scalars().all())
    await session.commit()
    stats = dict(reviewed=0, recovered=0, skipped=0, busy=0, failed=0)
    for entry_id in entry_ids:
        stats["reviewed"] += 1
        try:
            outcome = await _send_finance_outbox_entry(
                session,
                entry_id,
                mode="recovery",
                stale_before=cutoff,
                retry_due_before=current_time,
            )
        except Exception:
            await session.rollback()
            outcome = "failed"
            logger.warning("Finance notification recovery failed for one row")
        key = "recovered" if outcome == "sent" else outcome
        stats["skipped" if key == "already_sent" else key] += 1
    logger.info("Finance notification recovery: %s", stats)
    return stats


async def _send_outbox_entry(
    session: AsyncSession, entry: TelegramNotificationOutbox
) -> bool:
    if entry.notification_type == "finance_pending_payment":
        return await _send_finance_outbox_entry(session, entry.id) == "sent"
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
        if entry.notification_type == "finance_pending_payment":
            outcome = await _send_finance_outbox_entry(session, entry.id, mode="retry")
            if outcome == "busy":
                # Contention is not a send attempt. Reuse the existing two-hour
                # delay and recheck status rather than losing the scheduled retry.
                schedule_outbox_retry(entry_id)
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
    force_resend: bool = False,
) -> bool:
    """Persist outbox row, send via Telegram, update delivery status.

    Idempotency is intentionally enforced before creating a row. For the same
    notification/document/recipient key, a prior ``sent`` or ``pending`` row is
    reused instead of creating duplicates. Failed rows are retried in place.
    """
    existing = await find_outbox_entry(
        session,
        notification_type=notification_type,
        documento_id=documento_id,
        recipient_empleado_id=recipient_empleado_id,
    )
    if notification_type == "finance_pending_payment":
        if existing is None:
            try:
                existing = await create_outbox_entry(
                    session,
                    notification_type=notification_type,
                    status="pending",
                    header_text=header_text,
                    body_text=text,
                    documento_id=documento_id,
                    recipient_empleado_id=recipient_empleado_id,
                    telegram_chat_id=chat_id,
                )
                entry_id = existing.id
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await find_outbox_entry(
                    session,
                    notification_type=notification_type,
                    documento_id=documento_id,
                    recipient_empleado_id=recipient_empleado_id,
                )
                if existing is None:
                    raise
                entry_id = existing.id
        else:
            entry_id = existing.id
        outcome = await _send_finance_outbox_entry(
            session,
            entry_id,
            mode="delivery",
            text=text,
            reply_markup=reply_markup,
            force_resend=force_resend,
        )
        return outcome in {"sent", "already_sent"}
    if existing is not None and existing.status == "sent" and not force_resend:
        return True
    if chat_id is None:
        if existing is None:
            try:
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
            except IntegrityError:
                await session.rollback()
                existing = await find_outbox_entry(
                    session,
                    notification_type=notification_type,
                    documento_id=documento_id,
                    recipient_empleado_id=recipient_empleado_id,
                )
                if existing is not None and existing.status == "sent":
                    return True
        else:
            existing.status = "skipped"
            existing.header_text = (header_text or "").strip() or None
            existing.body_preview = _preview(text)
            existing.telegram_chat_id = None
            existing.error_message = "Sin telegram_user_id vinculado"
            existing.updated_at = datetime.utcnow()
        await session.commit()
        return False

    if existing is None:
        try:
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
        except IntegrityError:
            await session.rollback()
            entry = await find_outbox_entry(
                session,
                notification_type=notification_type,
                documento_id=documento_id,
                recipient_empleado_id=recipient_empleado_id,
            )
            if entry is None:
                raise
            if entry.status == "sent":
                return True
    else:
        entry = existing
        if force_resend:
            entry.retry_count = 0
            entry.sent_at = None
            entry.next_retry_at = None
        entry.status = "pending"
        entry.header_text = (header_text or "").strip() or None
        entry.body_preview = _preview(text)
        entry.telegram_chat_id = int(chat_id)
        entry.error_message = None
        entry.updated_at = datetime.utcnow()
        entry.next_retry_at = None
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
        try:
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
        except IntegrityError:
            await session.rollback()
            logger.info(
                "Finance pending-payment outbox already exists "
                "for document %s recipient %s",
                documento.id,
                recipient.id,
            )
            continue
        created += 1
    if created:
        await session.commit()
    return created


async def _load_documento_for_outbox(
    session: AsyncSession, documento_id: UUID, *, refresh: bool = False
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
        .execution_options(populate_existing=refresh)
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

    documento = await _load_documento_for_outbox(
        session,
        entry.documento_id,
        refresh=entry.notification_type == "finance_pending_payment",
    )
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
    stats = {
        "attempted": 0,
        "sent": 0,
        "failed": 0,
        "skipped_rebuild": 0,
        "busy": 0,
        "skipped": 0,
        "already_sent": 0,
    }

    for entry in entries:
        if entry.notification_type == "finance_pending_payment":
            outcome = await _send_finance_outbox_entry(session, entry.id)
            stats[outcome] += 1
            if outcome in {"sent", "failed"}:
                stats["attempted"] += 1
            continue
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
