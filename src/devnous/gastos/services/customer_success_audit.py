from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


SUPERADMIN_ROLES = frozenset({"superadmin", "super_admin"})
AUDIT_DEFAULT_LIMIT = 100
AUDIT_MAX_LIMIT = 500


@dataclass(frozen=True, slots=True)
class AuditRequestContext:
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_path: Optional[str] = None
    request_method: Optional[str] = None


def is_superadmin_role(role: Optional[str]) -> bool:
    return (str(role or "").strip().lower()) in SUPERADMIN_ROLES


def _truncate(value: Optional[Any], max_len: int) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return normalized[:max_len]


def audit_context_from_request(request: Any) -> AuditRequestContext:
    headers = getattr(request, "headers", {}) or {}
    forwarded_for = _truncate(headers.get("x-forwarded-for"), 300)
    ip_address = None
    if forwarded_for:
        ip_address = forwarded_for.split(",", 1)[0].strip() or None
    ip_address = ip_address or _truncate(headers.get("x-real-ip"), 120)
    if not ip_address:
        client = getattr(request, "client", None)
        ip_address = _truncate(getattr(client, "host", None), 120)
    return AuditRequestContext(
        ip_address=_truncate(ip_address, 120),
        user_agent=_truncate(headers.get("user-agent"), 500),
        request_path=_truncate(
            str(getattr(getattr(request, "url", None), "path", "")), 600
        ),
        request_method=_truncate(getattr(request, "method", None), 20),
    )


async def ensure_customer_success_audit_schema(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS customer_success_audit_events (
                id UUID PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                action TEXT NOT NULL,
                surface TEXT NOT NULL DEFAULT 'web',
                actor_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
                target_empleado_id UUID NULL REFERENCES empleados(id)
                    ON DELETE SET NULL,
                documento_id UUID NULL REFERENCES documentos(id) ON DELETE SET NULL,
                entity_type TEXT NULL,
                entity_id TEXT NULL,
                documento_referencia TEXT NULL,
                ip_address TEXT NULL,
                user_agent TEXT NULL,
                request_path TEXT NULL,
                request_method TEXT NULL,
                summary TEXT NULL,
                metadata_json JSONB NULL
            )
            """
        )
    )
    index_statements = [
        (
            "CREATE INDEX IF NOT EXISTS ix_cs_audit_created_at "
            "ON customer_success_audit_events(created_at DESC)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS ix_cs_audit_action "
            "ON customer_success_audit_events(action)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS ix_cs_audit_actor "
            "ON customer_success_audit_events(actor_empleado_id, created_at DESC)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS ix_cs_audit_target "
            "ON customer_success_audit_events(target_empleado_id, created_at DESC)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS ix_cs_audit_documento "
            "ON customer_success_audit_events(documento_id)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS ix_cs_audit_ip "
            "ON customer_success_audit_events(ip_address)"
        ),
    ]
    for statement in index_statements:
        await session.execute(text(statement))


def _uuid_or_none(value: Optional[Any]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return str(UUID(raw))
    except (TypeError, ValueError):
        return None


async def record_customer_success_audit_event(
    session: AsyncSession,
    *,
    action: str,
    actor_empleado_id: Optional[Any] = None,
    target_empleado_id: Optional[Any] = None,
    documento_id: Optional[Any] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[Any] = None,
    documento_referencia: Optional[str] = None,
    surface: str = "web",
    request: Optional[Any] = None,
    request_context: Optional[AuditRequestContext] = None,
    summary: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    commit: bool = False,
) -> None:
    normalized_action = _truncate(action, 120)
    if not normalized_action:
        return
    ctx = request_context or (
        audit_context_from_request(request)
        if request is not None
        else AuditRequestContext()
    )
    try:
        await ensure_customer_success_audit_schema(session)
        await session.execute(
            text(
                """
                INSERT INTO customer_success_audit_events (
                    id, created_at, action, surface, actor_empleado_id,
                    target_empleado_id, documento_id, entity_type, entity_id,
                    documento_referencia, ip_address, user_agent, request_path,
                    request_method, summary, metadata_json
                ) VALUES (
                    :id, NOW(), :action, :surface, :actor_empleado_id,
                    :target_empleado_id, :documento_id, :entity_type, :entity_id,
                    :documento_referencia, :ip_address, :user_agent, :request_path,
                    :request_method, :summary, CAST(:metadata_json AS JSONB)
                )
                """
            ),
            {
                "id": str(uuid4()),
                "action": normalized_action,
                "surface": _truncate(surface, 40) or "web",
                "actor_empleado_id": _uuid_or_none(actor_empleado_id),
                "target_empleado_id": _uuid_or_none(target_empleado_id),
                "documento_id": _uuid_or_none(documento_id),
                "entity_type": _truncate(entity_type, 80),
                "entity_id": _truncate(entity_id, 160),
                "documento_referencia": _truncate(documento_referencia, 200),
                "ip_address": ctx.ip_address,
                "user_agent": ctx.user_agent,
                "request_path": ctx.request_path,
                "request_method": ctx.request_method,
                "summary": _truncate(summary, 1000),
                "metadata_json": json.dumps(metadata or {}, default=str),
            },
        )
        if commit:
            await session.commit()
    except Exception:
        logger.exception("Failed to record customer success audit event")
        try:
            await session.rollback()
        except Exception:
            logger.exception("Failed to rollback audit event failure")


def _parse_date_start(value: Optional[str]) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    return datetime.combine(date.fromisoformat(raw), time.min).replace(
        tzinfo=timezone.utc
    )


def _parse_date_end(value: Optional[str]) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    return datetime.combine(date.fromisoformat(raw), time.max).replace(
        tzinfo=timezone.utc
    )


async def build_customer_success_audit_report(
    session: AsyncSession,
    *,
    actor_empleado_id: Optional[str] = None,
    target_empleado_id: Optional[str] = None,
    action: Optional[str] = None,
    documento_referencia: Optional[str] = None,
    ip_address: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = AUDIT_DEFAULT_LIMIT,
) -> dict[str, Any]:
    await ensure_customer_success_audit_schema(session)
    row_limit = max(10, min(int(limit or AUDIT_DEFAULT_LIMIT), AUDIT_MAX_LIMIT))
    params: dict[str, Any] = {"row_limit": row_limit}
    filters = ["1=1"]
    actor_uuid = _uuid_or_none(actor_empleado_id)
    if actor_uuid:
        params["actor_empleado_id"] = actor_uuid
        filters.append("e.actor_empleado_id = :actor_empleado_id")
    target_uuid = _uuid_or_none(target_empleado_id)
    if target_uuid:
        params["target_empleado_id"] = target_uuid
        filters.append("e.target_empleado_id = :target_empleado_id")
    if action:
        params["action"] = f"%{str(action).strip()}%"
        filters.append("e.action ILIKE :action")
    if documento_referencia:
        params["documento_referencia"] = f"%{str(documento_referencia).strip()}%"
        filters.append(
            "("
            "e.documento_referencia ILIKE :documento_referencia "
            "OR d.numero_referencia ILIKE :documento_referencia"
            ")"
        )
    if ip_address:
        params["ip_address"] = f"%{str(ip_address).strip()}%"
        filters.append("e.ip_address ILIKE :ip_address")
    start_dt = _parse_date_start(date_from)
    if start_dt:
        params["date_from"] = start_dt
        filters.append("e.created_at >= :date_from")
    end_dt = _parse_date_end(date_to)
    if end_dt:
        params["date_to"] = end_dt
        filters.append("e.created_at <= :date_to")
    where_sql = " AND ".join(filters)

    event_rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    e.id, e.created_at, e.action, e.surface,
                    e.actor_empleado_id,
                    actor.nombre AS actor_nombre,
                    actor.correo AS actor_correo,
                    e.target_empleado_id,
                    target.nombre AS target_nombre,
                    target.correo AS target_correo,
                    e.documento_id,
                    COALESCE(
                        e.documento_referencia,
                        d.numero_referencia
                    ) AS documento_referencia,
                    d.tipo AS documento_tipo, d.estado AS documento_estado,
                    e.ip_address, e.user_agent, e.request_method, e.request_path,
                    e.summary, e.metadata_json
                FROM customer_success_audit_events e
                LEFT JOIN empleados actor ON actor.id = e.actor_empleado_id
                LEFT JOIN empleados target ON target.id = e.target_empleado_id
                LEFT JOIN documentos d ON d.id = e.documento_id
                WHERE {where_sql}
                ORDER BY e.created_at DESC
                LIMIT :row_limit
                """
            ),
            params,
        )
    ).mappings().all()

    documento_ids = [
        str(row["documento_id"])
        for row in event_rows
        if row.get("documento_id")
    ]
    telegram_by_documento: dict[str, list[dict[str, Any]]] = {}
    if documento_ids:
        outbox_rows = (
            await session.execute(
                text(
                    """
                    SELECT
                        o.documento_id, o.notification_type, o.status,
                        o.created_at, o.sent_at, o.error_message,
                        o.telegram_chat_id, r.nombre AS recipient_nombre
                    FROM telegram_notification_outbox o
                    LEFT JOIN empleados r ON r.id = o.recipient_empleado_id
                    WHERE o.documento_id = ANY(CAST(:documento_ids AS UUID[]))
                    ORDER BY o.created_at DESC
                    """
                ),
                {"documento_ids": documento_ids},
            )
        ).mappings().all()
        for row in outbox_rows:
            telegram_by_documento.setdefault(
                str(row["documento_id"]), []
            ).append(dict(row))

    return {
        "events": [dict(row) for row in event_rows],
        "telegram_by_documento": telegram_by_documento,
        "limit": row_limit,
    }
