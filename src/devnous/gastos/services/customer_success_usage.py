from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def customer_success_usage_area_for_path(path: Optional[str]) -> str:
    normalized = str(path or "").strip().lower()
    if not normalized:
        return "unknown"
    if normalized.startswith("/admin/customer-success"):
        return "customer_success"
    if normalized.startswith("/admin/presupuestos"):
        return "presupuestos"
    if normalized.startswith("/admin/contabilidad") or normalized.startswith("/api/contabilidad"):
        return "contabilidad"
    if normalized.startswith("/admin/nomina"):
        return "nomina"
    if normalized.startswith("/admin/gastos"):
        return "finanzas"
    if normalized.startswith("/admin/perfiles") or normalized.startswith("/admin/empleados"):
        return "administracion"
    if normalized.startswith("/panel/operaciones-console"):
        return "operaciones"
    if normalized.startswith("/panel/telegram"):
        return "telegram"
    if normalized.startswith("/panel"):
        return "panel"
    if normalized.startswith("/informes-de-gastos"):
        return "informes"
    if normalized.startswith("/documentos"):
        return "documentos"
    if normalized.startswith("/gastos"):
        return "gastos"
    return "other"


def render_customer_success_usage_tracker_script(
    *,
    endpoint_path: str = "/api/customer-success/heartbeat",
    heartbeat_ms: int = 60000,
) -> str:
    endpoint_json = json.dumps(endpoint_path)
    heartbeat_ms = max(15000, int(heartbeat_ms))
    return f"""
    <script>
        (function() {{
            const endpoint = {endpoint_json};
            const heartbeatMs = {heartbeat_ms};
            const storageKey = 'customer-success-session-key';
            let timer = null;

            function ensureSessionKey() {{
                try {{
                    let sessionKey = window.sessionStorage.getItem(storageKey);
                    if (!sessionKey) {{
                        sessionKey = 'cs-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
                        window.sessionStorage.setItem(storageKey, sessionKey);
                    }}
                    return sessionKey;
                }} catch (_error) {{
                    return 'cs-fallback-' + Date.now().toString(36);
                }}
            }}

            function payload() {{
                const context = window.__csContext || {{}};
                const query = new URLSearchParams(window.location.search || '');
                const tournamentId = context.tournament_id || document.body.dataset.csTournamentId || query.get('tournament_id') || '';
                const tournamentName = context.tournament_name || document.body.dataset.csTournamentName || '';
                const customerLabel = context.customer_label || document.body.dataset.csCustomer || window.location.hostname || '';
                return JSON.stringify({{
                    session_key: ensureSessionKey(),
                    page_path: window.location.pathname,
                    page_title: document.title || '',
                    page_url: window.location.pathname + window.location.search,
                    observed_at: new Date().toISOString(),
                    tournament_id: tournamentId,
                    tournament_name: tournamentName,
                    customer_label: customerLabel
                }});
            }}

            function sendHeartbeat() {{
                if (document.visibilityState === 'hidden') {{
                    return;
                }}
                try {{
                    if (navigator.sendBeacon) {{
                        const blob = new Blob([payload()], {{ type: 'application/json' }});
                        navigator.sendBeacon(endpoint, blob);
                        return;
                    }}
                }} catch (_error) {{}}

                fetch(endpoint, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: payload(),
                    credentials: 'same-origin',
                    keepalive: true
                }}).catch(function() {{}});
            }}

            function restartTimer() {{
                if (timer) {{
                    window.clearInterval(timer);
                }}
                timer = window.setInterval(sendHeartbeat, heartbeatMs);
            }}

            document.addEventListener('visibilitychange', function() {{
                if (document.visibilityState === 'visible') {{
                    sendHeartbeat();
                    restartTimer();
                }}
            }});

            window.addEventListener('focus', sendHeartbeat, {{ passive: true }});
            window.addEventListener('click', sendHeartbeat, {{ passive: true }});
            window.addEventListener('keydown', sendHeartbeat, {{ passive: true }});
            window.addEventListener('load', function() {{
                sendHeartbeat();
                restartTimer();
            }});
        }})();
    </script>
    """


async def ensure_customer_success_usage_schema(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS customer_success_usage_minutes (
                id UUID PRIMARY KEY,
                empleado_id UUID NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
                session_key TEXT NOT NULL,
                page_path TEXT NOT NULL,
                page_title TEXT NULL,
                page_url TEXT NULL,
                product_area TEXT NOT NULL,
                tracked_tournament_id TEXT NULL,
                tracked_tournament_name TEXT NULL,
                customer_label TEXT NULL,
                minute_bucket TIMESTAMPTZ NOT NULL,
                heartbeat_count INTEGER NOT NULL DEFAULT 1,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (empleado_id, session_key, page_path, minute_bucket)
            )
            """
        )
    )
    await session.execute(
        text(
            """
            ALTER TABLE customer_success_usage_minutes
            ADD COLUMN IF NOT EXISTS tracked_tournament_id TEXT NULL
            """
        )
    )
    await session.execute(
        text(
            """
            ALTER TABLE customer_success_usage_minutes
            ADD COLUMN IF NOT EXISTS tracked_tournament_name TEXT NULL
            """
        )
    )
    await session.execute(
        text(
            """
            ALTER TABLE customer_success_usage_minutes
            ADD COLUMN IF NOT EXISTS customer_label TEXT NULL
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_customer_success_usage_minutes_empleado
            ON customer_success_usage_minutes (empleado_id, minute_bucket DESC)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_customer_success_usage_minutes_tournament
            ON customer_success_usage_minutes (tracked_tournament_id, minute_bucket DESC)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_customer_success_usage_minutes_customer
            ON customer_success_usage_minutes (customer_label, minute_bucket DESC)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_customer_success_usage_minutes_area
            ON customer_success_usage_minutes (product_area, minute_bucket DESC)
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_customer_success_usage_minutes_path
            ON customer_success_usage_minutes (page_path, minute_bucket DESC)
            """
        )
    )


def _normalize_customer_label(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return normalized[:160]


def _normalize_tournament_id(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return normalized[:120]


def _normalize_tournament_name(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return normalized[:220]


def _extract_usage_context_from_page_url(page_url: Optional[str]) -> dict[str, Optional[str]]:
    parsed = urlparse(str(page_url or ""))
    query = parse_qs(parsed.query or "")
    tournament_id = _normalize_tournament_id((query.get("tournament_id") or [None])[0])
    customer_label = _normalize_customer_label((query.get("customer_label") or [None])[0])
    return {
        "tournament_id": tournament_id,
        "customer_label": customer_label,
    }


async def record_customer_success_usage_heartbeat(
    session: AsyncSession,
    *,
    empleado_id: str,
    session_key: str,
    page_path: str,
    page_title: Optional[str] = None,
    page_url: Optional[str] = None,
    tournament_id: Optional[str] = None,
    tournament_name: Optional[str] = None,
    customer_label: Optional[str] = None,
    observed_at: Optional[datetime] = None,
) -> dict[str, Any]:
    await ensure_customer_success_usage_schema(session)
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    minute_bucket = observed.replace(second=0, microsecond=0)
    product_area = customer_success_usage_area_for_path(page_path)
    url_context = _extract_usage_context_from_page_url(page_url)
    normalized_tournament_id = _normalize_tournament_id(tournament_id) or url_context.get("tournament_id")
    normalized_tournament_name = _normalize_tournament_name(tournament_name)
    normalized_customer_label = _normalize_customer_label(customer_label) or url_context.get("customer_label")
    await session.execute(
        text(
            """
            INSERT INTO customer_success_usage_minutes (
                id, empleado_id, session_key, page_path, page_title, page_url,
                product_area, tracked_tournament_id, tracked_tournament_name, customer_label,
                minute_bucket, heartbeat_count, first_seen_at,
                last_seen_at, created_at
            ) VALUES (
                :id, :empleado_id, :session_key, :page_path, :page_title, :page_url,
                :product_area, :tracked_tournament_id, :tracked_tournament_name, :customer_label,
                :minute_bucket, 1, :observed_at, :observed_at, NOW()
            )
            ON CONFLICT (empleado_id, session_key, page_path, minute_bucket)
            DO UPDATE SET
                heartbeat_count = customer_success_usage_minutes.heartbeat_count + 1,
                page_title = EXCLUDED.page_title,
                page_url = EXCLUDED.page_url,
                product_area = EXCLUDED.product_area,
                tracked_tournament_id = EXCLUDED.tracked_tournament_id,
                tracked_tournament_name = EXCLUDED.tracked_tournament_name,
                customer_label = EXCLUDED.customer_label,
                last_seen_at = EXCLUDED.last_seen_at
            """
        ),
        {
            "id": str(uuid4()),
            "empleado_id": empleado_id,
            "session_key": session_key,
            "page_path": page_path,
            "page_title": (page_title or "").strip() or None,
            "page_url": (page_url or "").strip() or None,
            "product_area": product_area,
            "tracked_tournament_id": normalized_tournament_id,
            "tracked_tournament_name": normalized_tournament_name,
            "customer_label": normalized_customer_label,
            "minute_bucket": minute_bucket,
            "observed_at": observed,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "product_area": product_area,
        "minute_bucket": minute_bucket.isoformat(),
        "tracked_tournament_id": normalized_tournament_id,
        "customer_label": normalized_customer_label,
    }


async def build_customer_success_usage_report(
    session: AsyncSession,
    *,
    days: int = 14,
    area: Optional[str] = None,
    tournament_id: Optional[str] = None,
    customer_label: Optional[str] = None,
    limit: int = 25,
) -> dict[str, Any]:
    await ensure_customer_success_usage_schema(session)
    days = max(1, min(int(days or 14), 180))
    limit = max(5, min(int(limit or 25), 200))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    params: dict[str, Any] = {
        "cutoff": cutoff,
        "row_limit": limit,
    }
    filters = ["u.minute_bucket >= :cutoff"]
    if area:
        params["area"] = str(area).strip().lower()
        filters.append("u.product_area = :area")
    if tournament_id:
        params["tracked_tournament_id"] = _normalize_tournament_id(tournament_id)
        filters.append("u.tracked_tournament_id = :tracked_tournament_id")
    if customer_label:
        params["customer_label"] = _normalize_customer_label(customer_label)
        filters.append("u.customer_label = :customer_label")
    where_sql = " AND ".join(filters)

    summary_row = (
        await session.execute(
            text(
                f"""
                SELECT
                    COUNT(DISTINCT u.empleado_id) AS active_users,
                    COUNT(DISTINCT u.session_key) AS tracked_sessions,
                    COUNT(*) AS total_active_minutes,
                    COALESCE(MAX(u.last_seen_at), NULL) AS last_seen_at
                FROM customer_success_usage_minutes u
                WHERE {where_sql}
                """
            ),
            params,
        )
    ).mappings().first() or {}

    user_rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    CAST(u.empleado_id AS text) AS empleado_id,
                    COALESCE(NULLIF(TRIM(e.nombre), ''), e.correo, 'Sin nombre') AS empleado_nombre,
                    COALESCE(NULLIF(TRIM(e.correo), ''), '—') AS correo,
                    COALESCE(NULLIF(TRIM(e.rol), ''), 'empleado') AS rol,
                    COUNT(*) AS active_minutes,
                    COUNT(DISTINCT u.session_key) AS session_count,
                    COUNT(DISTINCT u.product_area) AS area_count,
                    COUNT(DISTINCT u.page_path) AS page_count,
                    COUNT(DISTINCT COALESCE(NULLIF(TRIM(u.tracked_tournament_name), ''), NULLIF(TRIM(u.tracked_tournament_id), ''))) AS tournament_count,
                    COALESCE(MAX(NULLIF(TRIM(u.customer_label), '')), '—') AS customer_label,
                    COALESCE(MAX(u.last_seen_at), NULL) AS last_seen_at
                FROM customer_success_usage_minutes u
                JOIN empleados e ON e.id = u.empleado_id
                WHERE {where_sql}
                GROUP BY 1,2,3,4
                ORDER BY active_minutes DESC, last_seen_at DESC
                LIMIT :row_limit
                """
            ),
            params,
        )
    ).mappings().all()

    area_rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    u.product_area,
                    COUNT(*) AS active_minutes,
                    COUNT(DISTINCT u.empleado_id) AS active_users,
                    COUNT(DISTINCT u.page_path) AS page_count,
                    COUNT(DISTINCT COALESCE(NULLIF(TRIM(u.tracked_tournament_name), ''), NULLIF(TRIM(u.tracked_tournament_id), ''))) AS tournament_count,
                    COALESCE(MAX(u.last_seen_at), NULL) AS last_seen_at
                FROM customer_success_usage_minutes u
                WHERE {where_sql}
                GROUP BY 1
                ORDER BY active_minutes DESC, active_users DESC
                LIMIT :row_limit
                """
            ),
            params,
        )
    ).mappings().all()

    page_rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    u.page_path,
                    COALESCE(MAX(NULLIF(TRIM(u.page_title), '')), u.page_path) AS page_title,
                    COALESCE(MAX(u.product_area), 'unknown') AS product_area,
                    COALESCE(MAX(NULLIF(TRIM(u.tracked_tournament_name), '')), MAX(NULLIF(TRIM(u.tracked_tournament_id), '')), '—') AS tournament_name,
                    COALESCE(MAX(NULLIF(TRIM(u.customer_label), '')), '—') AS customer_label,
                    COUNT(*) AS active_minutes,
                    COUNT(DISTINCT u.empleado_id) AS active_users,
                    COALESCE(MAX(u.last_seen_at), NULL) AS last_seen_at
                FROM customer_success_usage_minutes u
                WHERE {where_sql}
                GROUP BY 1
                ORDER BY active_minutes DESC, active_users DESC
                LIMIT :row_limit
                """
            ),
            params,
        )
    ).mappings().all()

    tournament_rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    COALESCE(NULLIF(TRIM(u.tracked_tournament_name), ''), NULLIF(TRIM(u.tracked_tournament_id), ''), 'Sin torneo') AS tournament_name,
                    COALESCE(NULLIF(TRIM(u.tracked_tournament_id), ''), '—') AS tournament_id,
                    COALESCE(MAX(NULLIF(TRIM(u.customer_label), '')), '—') AS customer_label,
                    COUNT(*) AS active_minutes,
                    COUNT(DISTINCT u.empleado_id) AS active_users,
                    COUNT(DISTINCT u.page_path) AS page_count,
                    COALESCE(MAX(u.last_seen_at), NULL) AS last_seen_at
                FROM customer_success_usage_minutes u
                WHERE {where_sql}
                GROUP BY 1,2
                ORDER BY active_minutes DESC, active_users DESC
                LIMIT :row_limit
                """
            ),
            params,
        )
    ).mappings().all()

    cohort_rows = (
        await session.execute(
            text(
                f"""
                WITH filtered AS (
                    SELECT *
                    FROM customer_success_usage_minutes u
                    WHERE {where_sql}
                ),
                first_touch AS (
                    SELECT
                        empleado_id,
                        DATE(MIN(first_seen_at)) AS cohort_date,
                        MIN(first_seen_at) AS first_seen_at
                    FROM filtered
                    GROUP BY empleado_id
                )
                SELECT
                    CAST(f.cohort_date AS text) AS cohort_date,
                    COUNT(*) AS users_count,
                    ROUND(AVG(activity.active_minutes), 2) AS avg_active_minutes,
                    COALESCE(MAX(activity.last_seen_at), NULL) AS last_seen_at
                FROM first_touch f
                JOIN (
                    SELECT
                        empleado_id,
                        COUNT(*) AS active_minutes,
                        MAX(last_seen_at) AS last_seen_at
                    FROM filtered
                    GROUP BY empleado_id
                ) activity ON activity.empleado_id = f.empleado_id
                GROUP BY 1
                ORDER BY cohort_date DESC
                LIMIT :row_limit
                """
            ),
            params,
        )
    ).mappings().all()

    avg_active_minutes = 0.0
    active_users = int(summary_row.get("active_users") or 0)
    total_active_minutes = int(summary_row.get("total_active_minutes") or 0)
    if active_users > 0:
        avg_active_minutes = round(total_active_minutes / active_users, 2)

    return {
        "days": days,
        "area_filter": str(area or "").strip().lower() or None,
        "tournament_filter": _normalize_tournament_id(tournament_id),
        "customer_filter": _normalize_customer_label(customer_label),
        "summary": {
            "active_users": active_users,
            "tracked_sessions": int(summary_row.get("tracked_sessions") or 0),
            "total_active_minutes": total_active_minutes,
            "avg_active_minutes_per_user": avg_active_minutes,
            "last_seen_at": (
                summary_row.get("last_seen_at").isoformat()
                if summary_row.get("last_seen_at")
                else None
            ),
        },
        "users": [
            {
                "empleado_id": row.get("empleado_id"),
                "empleado_nombre": row.get("empleado_nombre"),
                "correo": row.get("correo"),
                "rol": row.get("rol"),
                "active_minutes": int(row.get("active_minutes") or 0),
                "active_hours": round(int(row.get("active_minutes") or 0) / 60, 2),
                "session_count": int(row.get("session_count") or 0),
                "area_count": int(row.get("area_count") or 0),
                "page_count": int(row.get("page_count") or 0),
                "tournament_count": int(row.get("tournament_count") or 0),
                "customer_label": row.get("customer_label"),
                "last_seen_at": row.get("last_seen_at").isoformat() if row.get("last_seen_at") else None,
            }
            for row in user_rows
        ],
        "areas": [
            {
                "product_area": row.get("product_area"),
                "active_minutes": int(row.get("active_minutes") or 0),
                "active_hours": round(int(row.get("active_minutes") or 0) / 60, 2),
                "active_users": int(row.get("active_users") or 0),
                "page_count": int(row.get("page_count") or 0),
                "tournament_count": int(row.get("tournament_count") or 0),
                "last_seen_at": row.get("last_seen_at").isoformat() if row.get("last_seen_at") else None,
            }
            for row in area_rows
        ],
        "pages": [
            {
                "page_path": row.get("page_path"),
                "page_title": row.get("page_title"),
                "product_area": row.get("product_area"),
                "tournament_name": row.get("tournament_name"),
                "customer_label": row.get("customer_label"),
                "active_minutes": int(row.get("active_minutes") or 0),
                "active_hours": round(int(row.get("active_minutes") or 0) / 60, 2),
                "active_users": int(row.get("active_users") or 0),
                "last_seen_at": row.get("last_seen_at").isoformat() if row.get("last_seen_at") else None,
            }
            for row in page_rows
        ],
        "tournaments": [
            {
                "tournament_name": row.get("tournament_name"),
                "tournament_id": row.get("tournament_id"),
                "customer_label": row.get("customer_label"),
                "active_minutes": int(row.get("active_minutes") or 0),
                "active_hours": round(int(row.get("active_minutes") or 0) / 60, 2),
                "active_users": int(row.get("active_users") or 0),
                "page_count": int(row.get("page_count") or 0),
                "last_seen_at": row.get("last_seen_at").isoformat() if row.get("last_seen_at") else None,
            }
            for row in tournament_rows
        ],
        "cohorts": [
            {
                "cohort_date": row.get("cohort_date"),
                "users_count": int(row.get("users_count") or 0),
                "avg_active_minutes": round(float(row.get("avg_active_minutes") or 0), 2),
                "avg_active_hours": round(float(row.get("avg_active_minutes") or 0) / 60, 2),
                "last_seen_at": row.get("last_seen_at").isoformat() if row.get("last_seen_at") else None,
            }
            for row in cohort_rows
        ],
    }


def customer_success_usage_csv_rows(
    report: dict[str, Any],
    *,
    view: str,
) -> tuple[list[str], list[list[Any]]]:
    view_norm = str(view or "users").strip().lower()
    if view_norm == "areas":
        header = ["product_area", "active_users", "active_minutes", "active_hours", "page_count", "tournament_count", "last_seen_at"]
        rows = [
            [
                item.get("product_area"),
                item.get("active_users"),
                item.get("active_minutes"),
                item.get("active_hours"),
                item.get("page_count"),
                item.get("tournament_count"),
                item.get("last_seen_at"),
            ]
            for item in (report.get("areas") or [])
        ]
        return header, rows
    if view_norm == "pages":
        header = ["page_path", "page_title", "product_area", "tournament_name", "customer_label", "active_users", "active_minutes", "active_hours", "last_seen_at"]
        rows = [
            [
                item.get("page_path"),
                item.get("page_title"),
                item.get("product_area"),
                item.get("tournament_name"),
                item.get("customer_label"),
                item.get("active_users"),
                item.get("active_minutes"),
                item.get("active_hours"),
                item.get("last_seen_at"),
            ]
            for item in (report.get("pages") or [])
        ]
        return header, rows
    if view_norm == "tournaments":
        header = ["tournament_id", "tournament_name", "customer_label", "active_users", "active_minutes", "active_hours", "page_count", "last_seen_at"]
        rows = [
            [
                item.get("tournament_id"),
                item.get("tournament_name"),
                item.get("customer_label"),
                item.get("active_users"),
                item.get("active_minutes"),
                item.get("active_hours"),
                item.get("page_count"),
                item.get("last_seen_at"),
            ]
            for item in (report.get("tournaments") or [])
        ]
        return header, rows
    if view_norm == "cohorts":
        header = ["cohort_date", "users_count", "avg_active_minutes", "avg_active_hours", "last_seen_at"]
        rows = [
            [
                item.get("cohort_date"),
                item.get("users_count"),
                item.get("avg_active_minutes"),
                item.get("avg_active_hours"),
                item.get("last_seen_at"),
            ]
            for item in (report.get("cohorts") or [])
        ]
        return header, rows

    header = ["empleado_id", "empleado_nombre", "correo", "rol", "customer_label", "active_minutes", "active_hours", "session_count", "area_count", "page_count", "tournament_count", "last_seen_at"]
    rows = [
        [
            item.get("empleado_id"),
            item.get("empleado_nombre"),
            item.get("correo"),
            item.get("rol"),
            item.get("customer_label"),
            item.get("active_minutes"),
            item.get("active_hours"),
            item.get("session_count"),
            item.get("area_count"),
            item.get("page_count"),
            item.get("tournament_count"),
            item.get("last_seen_at"),
        ]
        for item in (report.get("users") or [])
    ]
    return header, rows
