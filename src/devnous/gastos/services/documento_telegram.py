"""
Telegram notifications and query helpers for document workflow (SOLICITUD / INFORME).

Keeps message builders and DB helpers out of the webhook route and the Telegram adapter.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from ..models import Documento, Empleado, ExpenseReport
from ..utils.mexico_city_dates import format_mexico_city_datetime
from .amex_expense_service import (
    calculate_informe_expense_totals,
    describe_informe_balance,
    sum_paid_solicitud_amounts,
)
from .cuenta_settlement_service import compute_cuenta_saldo_adjustments
from .telegram_notify import schedule_fire_and_forget
from .telegram_outbox_service import (
    deliver_telegram_notification,
    outbox_entry_exists,
)

logger = logging.getLogger(__name__)

# Callback data prefixes (keep total length <= 64; UUID is 36 chars)
CB_DETAIL_APPROVER = "gd:"  # approver detail + action buttons
CB_APPROVE = "ga:"
CB_REJECT = "gr:"
CB_VIEW_REQUESTER = "gv:"  # read-only for document owner

APPROVER_QUEUE_ROLES = frozenset({"finanzas", "admin", "superadmin", "super_admin"})
SUPERADMIN_ROLES = frozenset({"superadmin", "super_admin"})
TEMP_APPROVAL_ALERT_ACTOR_EMAIL = "otrujillo@plataformasports.com"

_notify_engine = None
_notify_session_maker: Optional[async_sessionmaker[AsyncSession]] = None


def _normalize_async_db_url(db_url: str) -> str:
    if db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
        return db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return db_url


def _resolve_expenses_db_url() -> str:
    return (
        os.getenv("EXPENSES_DATABASE_URL")
        or os.getenv("TELEGRAM_AUTH_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("POSTGRESQL_URL")
        or ""
    ).strip()


def get_notification_session_maker() -> Optional[async_sessionmaker[AsyncSession]]:
    """Lazy async session maker for post-commit notification tasks."""
    global _notify_engine, _notify_session_maker
    if _notify_session_maker is not None:
        return _notify_session_maker
    url = _resolve_expenses_db_url()
    if not url:
        logger.warning("No database URL for document Telegram notifications")
        return None
    _notify_engine = create_async_engine(
        _normalize_async_db_url(url),
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    _notify_session_maker = async_sessionmaker(
        _notify_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return _notify_session_maker


def schedule_document_workflow_telegram_notifications(
    *,
    documento_id: str,
    action: str,
    actor_id: str,
    comentario: Optional[str] = None,
) -> None:
    """Fire-and-forget hook after workflow commit."""
    schedule_fire_and_forget(
        run_document_workflow_telegram_notifications(
            documento_id=documento_id,
            action=action,
            actor_id=actor_id,
            comentario=comentario,
        )
    )


def schedule_solicitud_payment_telegram_notifications(
    *,
    documento_id: str,
    actor_id: str,
) -> None:
    """Fire-and-forget hook after SOLICITUD payment registration."""
    schedule_fire_and_forget(
        run_solicitud_payment_telegram_notifications(
            documento_id=documento_id,
            actor_id=actor_id,
        )
    )


def schedule_solicitud_pending_payment_telegram_notifications(
    *,
    documento_id: str,
) -> None:
    """Fire-and-forget hook when a SOLICITUD enters the pending-payments queue."""
    schedule_fire_and_forget(
        run_solicitud_pending_payment_telegram_notifications(
            documento_id=documento_id,
        )
    )


async def run_solicitud_pending_payment_telegram_notifications(
    *,
    documento_id: str,
) -> None:
    session_maker = get_notification_session_maker()
    if not session_maker:
        return
    try:
        doc_uuid = UUID(str(documento_id))
    except ValueError:
        logger.warning(
            "Invalid UUID for solicitud pending payment telegram notification",
            extra={"documento_id": documento_id},
        )
        return

    async with session_maker() as session:
        documento = await load_documento_for_telegram(session, doc_uuid)
        if documento is None:
            return
        try:
            await notify_finance_pending_payment_on_solicitud_approve(
                session, documento
            )
        except Exception:
            logger.exception(
                "Finance pending payment Telegram alert failed",
                extra={"documento_id": documento_id},
            )


async def run_solicitud_payment_telegram_notifications(
    *,
    documento_id: str,
    actor_id: str,
) -> None:
    session_maker = get_notification_session_maker()
    if not session_maker:
        return
    try:
        doc_uuid = UUID(str(documento_id))
        actor_uuid = UUID(str(actor_id))
    except ValueError:
        logger.warning(
            "Invalid UUID for solicitud payment telegram notification",
            extra={"documento_id": documento_id},
        )
        return

    async with session_maker() as session:
        documento = await load_documento_for_telegram(session, doc_uuid)
        if documento is None:
            return
        actor = await session.get(Empleado, actor_uuid)
        await notify_solicitud_transferencia_paid(session, documento, actor=actor)


async def run_document_workflow_telegram_notifications(
    *,
    documento_id: str,
    action: str,
    actor_id: str,
    comentario: Optional[str] = None,
) -> None:
    normalized = (action or "").strip().lower()
    session_maker = get_notification_session_maker()
    if not session_maker:
        return
    try:
        doc_uuid = UUID(str(documento_id))
        actor_uuid = UUID(str(actor_id))
    except ValueError:
        logger.warning("Invalid UUID for telegram notification", extra={"documento_id": documento_id})
        return

    async with session_maker() as session:
        documento = await load_documento_for_telegram(session, doc_uuid)
        if documento is None:
            return
        actor = await session.get(Empleado, actor_uuid)

        if normalized == "send":
            await notify_assigned_approver_new_request(session, documento)
        elif normalized == "approve":
            await notify_requester_decision(
                session, documento, approved=True, actor=actor
            )
        elif normalized == "reject":
            await notify_requester_decision(
                session, documento, approved=False, actor=actor, comentario=comentario
            )

        if (
            documento.tipo == "SOLICITUD"
            and documento.estado == "aprobado"
        ):
            try:
                await notify_finance_pending_payment_on_solicitud_approve(
                    session, documento
                )
            except Exception:
                logger.exception(
                    "Finance pending payment Telegram alert failed",
                    extra={"documento_id": documento_id},
                )


def _sum_active_expense_amounts(expenses: Sequence[ExpenseReport]) -> float:
    total_decimal = sum(
        (Decimal(str(exp.gasto_cantidad or 0)) for exp in expenses),
        Decimal("0"),
    )
    return float(total_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def load_informe_active_expenses(
    session: AsyncSession,
    documento: Documento,
) -> List[ExpenseReport]:
    """Load active expenses linked to an INFORME (read-only)."""
    expense_conditions = [
        ExpenseReport.documento_id == documento.id,
        ExpenseReport.informe_documento_id == documento.id,
    ]
    if documento.cuenta_gastos_id:
        expense_conditions.append(ExpenseReport.cuenta_gastos_id == documento.cuenta_gastos_id)

    expenses_result = await session.execute(
        select(ExpenseReport).where(
            and_(
                or_(*expense_conditions),
                ExpenseReport.estado_gasto != "cancelado",
            )
        )
    )
    return list(expenses_result.scalars().all())


async def compute_informe_total_readonly(session: AsyncSession, documento: Documento) -> float:
    """Derive INFORME total from active expenses (no DB writes)."""
    active_expenses = await load_informe_active_expenses(session, documento)
    return _sum_active_expense_amounts(active_expenses)


def _sum_requested_document_amounts(documentos: Sequence[Documento]) -> float:
    total_decimal = sum(
        (Decimal(str(doc.monto_solicitado or 0)) for doc in documentos),
        Decimal("0"),
    )
    return float(total_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def _load_informe_solicitudes(
    session: AsyncSession,
    documento: Documento,
) -> List[Documento]:
    if not documento.cuenta_gastos_id:
        return []

    solicitudes_result = await session.execute(
        select(Documento).where(
            and_(
                Documento.cuenta_gastos_id == documento.cuenta_gastos_id,
                Documento.tipo == "SOLICITUD",
            )
        )
    )
    return list(solicitudes_result.scalars().all())


def escape_markdown_light(text: Optional[str]) -> str:
    """Escape a few characters for Telegram classic Markdown."""
    if not text:
        return ""
    out = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        out = out.replace(ch, "\\" + ch)
    return out


def _fmt_mxn(amount: Any) -> str:
    try:
        if amount is None:
            return "—"
        return f"${float(amount):,.2f} MXN"
    except (TypeError, ValueError):
        return "—"


def _fmt_dt(value: Optional[datetime]) -> str:
    if value is None:
        return "—"
    try:
        return format_mexico_city_datetime(value)
    except Exception:
        return str(value)


def concepto_resumen(documento: Documento) -> str:
    parts: List[str] = []
    if documento.concepto_pago:
        parts.append(str(documento.concepto_pago).strip())
    if documento.notas:
        parts.append(str(documento.notas).strip())
    merged = " · ".join(p for p in parts if p)
    return merged or "—"


def proveedor_label(documento: Documento) -> str:
    prov = getattr(documento, "proveedor_cliente", None)
    if prov is not None and getattr(prov, "nombre", None):
        return str(prov.nombre).strip()
    return "—"


def _bold_field_line(label: str, value: str) -> str:
    """Telegram classic Markdown: bold label and bold value on one line."""
    return f"*{escape_markdown_light(label)}* *{escape_markdown_light(value)}*"


def _leading_identity_lines(documento: Documento) -> List[str]:
    beneficiario_heading = (
        "Beneficiario / tercero" if documento.tipo == "INFORME" else "Beneficiario"
    )
    return [
        _bold_field_line(beneficiario_heading, beneficiario_label(documento)),
        _bold_field_line("Proveedor", proveedor_label(documento)),
    ]


def beneficiario_label(documento: Documento) -> str:
    ben = getattr(documento, "beneficiario_empleado", None)
    if ben is not None and getattr(ben, "nombre", None):
        return str(ben.nombre)
    prov = getattr(documento, "proveedor_cliente", None)
    if prov is not None and getattr(prov, "nombre", None):
        return str(prov.nombre)
    return "—"


async def build_documento_telegram_context(
    session: AsyncSession,
    documento: Documento,
) -> Dict[str, Any]:
    """Numeric / saldo context for Spanish copy."""
    ctx: Dict[str, Any] = {
        "monto_line": "—",
        "saldo_line": None,
    }
    solicitante = documento.empleado.nombre if documento.empleado else "—"
    ctx["solicitante"] = solicitante

    if documento.tipo == "INFORME":
        expenses = await load_informe_active_expenses(session, documento)
        expense_totals = calculate_informe_expense_totals(expenses)
        solicitudes = await _load_informe_solicitudes(session, documento)
        monto_solicitado = _sum_requested_document_amounts(solicitudes)
        monto_entregado = sum_paid_solicitud_amounts(solicitudes)
        settled_amount = 0.0
        if documento.cuenta_gastos_id:
            settled_amount, _ = await compute_cuenta_saldo_adjustments(
                session,
                documento.cuenta_gastos_id,
            )
        balance_amount, owner_lbl, note = describe_informe_balance(
            employee_paid=expense_totals.employee_paid,
            monto_entregado=monto_entregado,
            settled_amount=settled_amount,
        )
        ctx["monto_solicitado"] = _fmt_mxn(monto_solicitado)
        ctx["monto_gastado"] = _fmt_mxn(expense_totals.employee_paid)
        ctx["saldo_line"] = f"{_fmt_mxn(balance_amount)} · {owner_lbl} · {note}"
    elif documento.tipo == "SOLICITUD":
        ctx["monto_line"] = _fmt_mxn(documento.monto_solicitado)
        ro = (documento.referencia_operaciones or "").strip()
        ctx["referencia_operaciones"] = ro or None
    else:
        ctx["monto_line"] = f"Monto total: {_fmt_mxn(documento.monto_total)}"

    return ctx


def format_documento_resumen_es(
    documento: Documento,
    *,
    context: Dict[str, Any],
    include_actions_hint: bool = False,
) -> str:
    ref = escape_markdown_light(documento.numero_referencia)
    tipo = escape_markdown_light(documento.tipo)
    estado = escape_markdown_light(documento.estado)
    sol = escape_markdown_light(str(context.get("solicitante") or "—"))
    concepto = escape_markdown_light(concepto_resumen(documento))
    saldo_line = context.get("saldo_line")
    saldo_txt = escape_markdown_light(str(saldo_line)) if saldo_line else None

    if documento.tipo == "SOLICITUD":
        ro = escape_markdown_light(str(context.get("referencia_operaciones") or "—"))
        monto_val = escape_markdown_light(str(context.get("monto_line") or "—"))
        lines = [
            *_leading_identity_lines(documento),
            f"*Documento* `{ref}` · *Tipo* {tipo}",
            f"*Estado* {estado}",
            f"*Solicitante* {sol}",
            f"*Concepto / notas* {concepto}",
            f"*Referencia Operaciones* {ro}",
            f"*Monto solicitado* {monto_val}",
        ]
    elif documento.tipo == "INFORME":
        monto_sol = escape_markdown_light(str(context.get("monto_solicitado") or "—"))
        monto_gas = escape_markdown_light(str(context.get("monto_gastado") or "—"))
        saldo_val = escape_markdown_light(str(context.get("saldo_line") or "—"))
        lines = [
            *_leading_identity_lines(documento),
            f"*Documento* `{ref}` · *Tipo* {tipo}",
            f"*Estado* {estado}",
            f"*Solicitante* {sol}",
            f"*Concepto / notas* {concepto}",
            f"*Monto solicitado* {monto_sol}",
            f"*Monto gastado* {monto_gas}",
            f"*Saldo* {saldo_val}",
        ]
    else:
        monto_line = escape_markdown_light(str(context.get("monto_line") or "—"))
        lines = [
            *_leading_identity_lines(documento),
            f"*Documento* `{ref}` · *Tipo* {tipo}",
            f"*Estado* {estado}",
            f"*Solicitante* {sol}",
            f"*Concepto / notas* {concepto}",
            monto_line,
        ]
        if saldo_txt:
            lines.append(saldo_txt)
    lines.append(f"*Enviado* {_fmt_dt(documento.enviado_en)}")
    lines.append(f"*Aprobado* {_fmt_dt(documento.aprobado_en)}")
    if include_actions_hint:
        lines.append("")
        lines.append("Usa los botones de abajo o el comando /pendientes.")
    return "\n".join(lines)


def approval_inline_keyboard(documento_id: UUID) -> Dict[str, Any]:
    sid = str(documento_id)
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Aprobar", "callback_data": f"{CB_APPROVE}{sid}"},
                {"text": "⛔ Rechazar", "callback_data": f"{CB_REJECT}{sid}"},
            ]
        ]
    }


def list_detail_callback_data(documento_id: UUID) -> str:
    return f"{CB_DETAIL_APPROVER}{documento_id}"


def requester_view_callback_data(documento_id: UUID) -> str:
    return f"{CB_VIEW_REQUESTER}{documento_id}"


def parse_documento_callback(data: str) -> Optional[Tuple[str, UUID]]:
    if not data or len(data) < 39:
        return None
    for prefix in (CB_DETAIL_APPROVER, CB_APPROVE, CB_REJECT, CB_VIEW_REQUESTER):
        if data.startswith(prefix):
            try:
                return prefix, UUID(data[len(prefix) :])
            except ValueError:
                return None
    return None


async def load_documento_for_telegram(
    session: AsyncSession,
    documento_id: UUID,
) -> Optional[Documento]:
    result = await session.execute(
        select(Documento)
        .options(
            selectinload(Documento.empleado).selectinload(Empleado.aprobador),
            selectinload(Documento.beneficiario_empleado),
            selectinload(Documento.proveedor_cliente),
        )
        .where(Documento.id == documento_id)
    )
    return result.scalar_one_or_none()


def approver_can_see_document_in_queue(empleado: Empleado, documento: Documento) -> bool:
    if empleado.rol not in APPROVER_QUEUE_ROLES:
        return False
    if documento.estado != "enviado":
        return False
    if empleado.rol in SUPERADMIN_ROLES:
        return True
    owner = documento.empleado
    if owner is None:
        return False
    return owner.aprobador_id == empleado.id


def requester_can_view_document(empleado: Empleado, documento: Documento) -> bool:
    return documento.empleado_id == empleado.id


async def query_pending_documentos_for_approver(
    session: AsyncSession,
    empleado: Empleado,
    *,
    limit: int = 30,
) -> List[Documento]:
    if empleado.rol not in APPROVER_QUEUE_ROLES:
        return []
    base_opts = (
        selectinload(Documento.empleado),
        selectinload(Documento.beneficiario_empleado),
        selectinload(Documento.proveedor_cliente),
    )
    if empleado.rol in SUPERADMIN_ROLES:
        result = await session.execute(
            select(Documento)
            .options(*base_opts)
            .where(Documento.estado == "enviado")
            .order_by(Documento.enviado_en.desc().nulls_last(), Documento.creado_en.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    result = await session.execute(
        select(Documento)
        .options(*base_opts)
        .join(Empleado, Documento.empleado_id == Empleado.id)
        .where(
            and_(
                Documento.estado == "enviado",
                Empleado.aprobador_id == empleado.id,
            )
        )
        .order_by(Documento.enviado_en.desc().nulls_last(), Documento.creado_en.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def query_documentos_for_requester(
    session: AsyncSession,
    empleado_id: UUID,
    *,
    limit: int = 15,
) -> List[Documento]:
    result = await session.execute(
        select(Documento)
        .options(
            selectinload(Documento.empleado),
            selectinload(Documento.beneficiario_empleado),
            selectinload(Documento.proveedor_cliente),
        )
        .where(Documento.empleado_id == empleado_id)
        .order_by(Documento.creado_en.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def find_documento_by_referencia_for_requester(
    session: AsyncSession,
    *,
    empleado_id: UUID,
    referencia: str,
) -> Optional[Documento]:
    ref = (referencia or "").strip()
    if not ref:
        return None
    result = await session.execute(
        select(Documento)
        .options(
            selectinload(Documento.empleado),
            selectinload(Documento.beneficiario_empleado),
            selectinload(Documento.proveedor_cliente),
        )
        .where(
            and_(
                Documento.empleado_id == empleado_id,
                Documento.numero_referencia == ref,
            )
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def notify_assigned_approver_new_request(
    session: AsyncSession,
    documento: Documento,
) -> None:
    """Notify only the assigned approver (mirrors web inbox)."""
    owner = documento.empleado
    if owner is None or not owner.aprobador_id:
        return
    approver = owner.aprobador
    if approver is None:
        return

    ctx = await build_documento_telegram_context(session, documento)
    body = format_documento_resumen_es(documento, context=ctx, include_actions_hint=True)
    header = "📥 *Nueva solicitud de aprobación*"
    text = header + "\n\n" + body
    chat_id = (
        int(approver.telegram_user_id)
        if approver.telegram_user_id is not None
        else None
    )
    await deliver_telegram_notification(
        session,
        notification_type="workflow_send_approver",
        header_text=header,
        text=text,
        chat_id=chat_id,
        documento_id=documento.id,
        recipient_empleado_id=approver.id,
        reply_markup=approval_inline_keyboard(documento.id) if chat_id else None,
    )


async def notify_requester_decision(
    session: AsyncSession,
    documento: Documento,
    *,
    approved: bool,
    actor: Optional[Empleado],
    comentario: Optional[str] = None,
) -> None:
    owner = documento.empleado
    if owner is None:
        return

    ctx = await build_documento_telegram_context(session, documento)
    body = format_documento_resumen_es(
        documento, context=ctx, include_actions_hint=False
    )
    actor_name = escape_markdown_light(actor.nombre) if actor else "—"
    if approved:
        header = "✅ *Tu documento fue aprobado*"
        extra = f"*Aprobador* {actor_name}\n"
        notification_type = "workflow_approve_requester"
    else:
        header = "⛔ *Tu documento fue rechazado*"
        extra = f"*Aprobador* {actor_name}\n"
        notification_type = "workflow_reject_requester"
        if comentario:
            extra += f"*Comentario* {escape_markdown_light(comentario)}\n"

    text = header + "\n\n" + extra + "\n" + body
    chat_id = int(owner.telegram_user_id) if owner.telegram_user_id is not None else None
    await deliver_telegram_notification(
        session,
        notification_type=notification_type,
        header_text=header,
        text=text,
        chat_id=chat_id,
        documento_id=documento.id,
        recipient_empleado_id=owner.id,
    )


def _is_odilon_trujillo_actor(actor: Optional[Empleado]) -> bool:
    if actor is None:
        return False
    correo = str(getattr(actor, "correo", "") or "").strip().lower()
    if correo == TEMP_APPROVAL_ALERT_ACTOR_EMAIL:
        return True
    nombre = str(getattr(actor, "nombre", "") or "").strip().lower()
    return "odilon" in nombre and "trujillo" in nombre


async def notify_finance_pending_payment_on_solicitud_approve(
    session: AsyncSession,
    documento: Documento,
) -> int:
    """Notify Finance users that an approved SOLICITUD awaits payment registration."""
    if documento.tipo != "SOLICITUD" or documento.estado != "aprobado":
        return 0

    result = await session.execute(
        select(Empleado).where(
            Empleado.rol == "finanzas",
            Empleado.activo.is_(True),
        )
    )
    recipients = list(result.scalars().all())
    if not recipients:
        logger.info(
            "Finance pending payment alert skipped; no active Finance users"
        )
        return 0

    ctx = await build_documento_telegram_context(session, documento)
    body = format_documento_resumen_es(
        documento, context=ctx, include_actions_hint=False
    )
    header = "*Solicitud aprobada URGENTE - pendiente de pago*" if bool(
        getattr(documento, "pago_urgente", False)
    ) else "📥 *Solicitud aprobada — pendiente de pago*"
    text = header + "\n\n" + body

    sent = 0
    seen_chat_ids: set[int] = set()
    for recipient in recipients:
        if await outbox_entry_exists(
            session,
            notification_type="finance_pending_payment",
            documento_id=documento.id,
            recipient_empleado_id=recipient.id,
        ):
            continue
        chat_id = (
            int(recipient.telegram_user_id)
            if recipient.telegram_user_id is not None
            else None
        )
        if chat_id is not None and chat_id in seen_chat_ids:
            continue
        if chat_id is not None:
            seen_chat_ids.add(chat_id)
        if await deliver_telegram_notification(
            session,
            notification_type="finance_pending_payment",
            header_text=header,
            text=text,
            chat_id=chat_id,
            documento_id=documento.id,
            recipient_empleado_id=recipient.id,
        ):
            sent += 1
    return sent


async def ensure_finance_pending_payment_notifications(
    session: AsyncSession,
    documento: Documento,
) -> int:
    """Backfill Finance pending-payment alerts for approved SOLICITUD rows.

    Idempotent: skips recipients that already have an outbox row.
    """
    return await notify_finance_pending_payment_on_solicitud_approve(
        session, documento
    )


async def notify_solicitud_transferencia_paid(
    session: AsyncSession,
    documento: Documento,
    *,
    actor: Optional[Empleado] = None,
) -> int:
    """Notify the requester and assigned approver that a SOLICITUD was paid."""
    if documento.tipo != "SOLICITUD" or documento.estado != "pagado":
        return 0

    ctx = await build_documento_telegram_context(session, documento)
    body = format_documento_resumen_es(
        documento, context=ctx, include_actions_hint=False
    )
    registrar = escape_markdown_light(actor.nombre) if actor else "—"
    extra = f"*Registrado por* {registrar}\n\n"

    sent = 0
    seen_chat_ids: set[int] = set()
    owner = documento.empleado

    if owner is not None:
        chat_id = (
            int(owner.telegram_user_id)
            if owner.telegram_user_id is not None
            else None
        )
        header = "✅ *Tu solicitud de transferencia fue pagada*"
        text = header + "\n\n" + extra + body
        if chat_id is not None:
            seen_chat_ids.add(chat_id)
        if await deliver_telegram_notification(
            session,
            notification_type="solicitud_paid_requester",
            header_text=header,
            text=text,
            chat_id=chat_id,
            documento_id=documento.id,
            recipient_empleado_id=owner.id,
        ):
            sent += 1

    approver = owner.aprobador if owner is not None else None
    if approver is not None:
        chat_id = (
            int(approver.telegram_user_id)
            if approver.telegram_user_id is not None
            else None
        )
        if chat_id is not None and chat_id in seen_chat_ids:
            return sent
        header = "✅ *Solicitud de transferencia pagada*"
        text = header + "\n\n" + extra + body
        if await deliver_telegram_notification(
            session,
            notification_type="solicitud_paid_approver",
            header_text=header,
            text=text,
            chat_id=chat_id,
            documento_id=documento.id,
            recipient_empleado_id=approver.id,
        ):
            sent += 1

    return sent


async def notify_finance_when_odilon_approves(
    session: AsyncSession,
    documento: Documento,
    actor: Optional[Empleado],
) -> int:
    """
    Temporary operational alert until formal multi-step approvals exist.

    When Odilon approves a request, alert active Finance users with Telegram linked.
    """
    if not _is_odilon_trujillo_actor(actor):
        return 0

    result = await session.execute(
        select(Empleado).where(
            Empleado.rol == "finanzas",
            Empleado.activo.is_(True),
            Empleado.telegram_user_id.isnot(None),
        )
    )
    recipients = list(result.scalars().all())
    if not recipients:
        logger.info(
            "Temporary Odilon approval alert skipped; no Finance Telegram IDs"
        )
        return 0

    ctx = await build_documento_telegram_context(session, documento)
    body = format_documento_resumen_es(
        documento, context=ctx, include_actions_hint=False
    )
    actor_name = escape_markdown_light(getattr(actor, "nombre", "") or "Odilon")
    header = "🔔 *Odilon aprobó una solicitud*"
    text = header + "\n\n" + f"*Aprobador* {actor_name}\n\n" + body
    sent = 0
    seen_chat_ids: set[int] = set()
    for recipient in recipients:
        chat_id = int(recipient.telegram_user_id)
        if chat_id in seen_chat_ids:
            continue
        seen_chat_ids.add(chat_id)
        if await deliver_telegram_notification(
            session,
            notification_type="finance_odilon_approve",
            header_text=header,
            text=text,
            chat_id=chat_id,
            documento_id=documento.id,
            recipient_empleado_id=recipient.id,
        ):
            sent += 1
    return sent


def telegram_command_base(text: str) -> Tuple[str, str]:
    """Return (command_lower, rest) supporting /cmd@BotName."""
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return raw.lower(), ""
    parts = raw.split(None, 1)
    cmd0 = parts[0]
    if "@" in cmd0:
        cmd0 = cmd0.split("@", 1)[0]
    base = cmd0.lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    return base, rest


def validate_callback_payload_length(data: str) -> bool:
    return len(data.encode("utf-8")) <= 64


# --- Dev sanity: UUID callbacks must fit in Telegram's limit ---
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def assert_callback_data_well_formed(data: str) -> bool:
    parsed = parse_documento_callback(data)
    if not parsed:
        return False
    prefix, uuid_val = parsed
    return bool(_UUID_RE.match(str(uuid_val))) and validate_callback_payload_length(
        data
    )
