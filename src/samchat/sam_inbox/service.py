from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import Tournament
from devnous.gastos.services.cfdi_matching_service import get_cfdi_matching_overview
from devnous.gastos.services.documento_payment_service import (
    DocumentoPaymentPermissionError,
    DocumentoPaymentValidationError,
    get_pending_document_payment_overview,
)
from samchat.finance_platform import (
    build_finance_platform_snapshot,
    build_finance_source_snapshot,
)
from samchat.tournaments_v2.services import build_tournament_soul_snapshot

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
STATUS_RANK = {
    "needs_attention": 0,
    "blocked": 1,
    "pending": 2,
    "ready": 3,
    "info": 4,
    "done": 5,
}
DOMAIN_RANK = {"finanzas": 0, "operaciones": 1, "direccion": 2}
VALID_TABS = {"todo", "operaciones", "finanzas", "direccion"}


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _safe_href(value: Any) -> str | None:
    href = _safe_str(value)
    if not href:
        return None
    if href.startswith("/api/"):
        return None
    if href.startswith("/admin/") or href.startswith("/assistant"):
        return href
    return None


def _is_admin_role(role: Any) -> bool:
    return _safe_str(role).lower() in {"admin", "superadmin", "super_admin"}


def _item_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        SEVERITY_RANK.get(_safe_str(item.get("severity")).lower(), 9),
        STATUS_RANK.get(_safe_str(item.get("status")).lower(), 9),
        DOMAIN_RANK.get(_safe_str(item.get("domain")).lower(), 9),
        0 if _safe_href(item.get("href")) else 1,
        _safe_str(item.get("title")).lower(),
        _safe_str(item.get("item_id")).lower(),
    )


def _make_item(
    *,
    item_id: str,
    source_type: str,
    domain: str,
    module: str,
    status: str,
    severity: str,
    title: str,
    detail: str,
    href: str | None = None,
    owner_hint: str | None = None,
    tags: Optional[list[str]] = None,
    source_ref: Optional[dict[str, Any]] = None,
    prepared_action: Optional[dict[str, Any]] = None,
    timestamps: Optional[dict[str, Any]] = None,
    secondary_label: str | None = None,
    secondary_href: str | None = None,
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "source_type": source_type,
        "domain": domain,
        "module": module,
        "status": status,
        "severity": severity,
        "title": title,
        "detail": detail,
        "href": _safe_href(href),
        "owner_hint": owner_hint,
        "tags": tags or [],
        "source_ref": source_ref or {},
        "prepared_action": prepared_action or {},
        "timestamps": timestamps or {},
        "secondary_label": secondary_label,
        "secondary_href": _safe_href(secondary_href),
    }


async def _load_active_tournaments(
    session: AsyncSession, *, limit: int = 3
) -> list[Tournament]:
    stmt = (
        select(Tournament)
        .where(Tournament.active.is_(True))
        .order_by(Tournament.name.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _load_direction_sources(
    session: AsyncSession, *, year: int
) -> dict[str, Any]:
    from samchat.assistant.router import _build_automatic_alerts, _build_executive_dashboard

    dashboard = await _build_executive_dashboard(
        session=session,
        year=year,
        bi_scope=None,
        bi_segment=None,
    )
    alerts = await _build_automatic_alerts(
        session=session,
        year=year,
        bi_scope=None,
        bi_segment=None,
        spike_ratio=1.35,
    )
    return {"dashboard": dashboard, "alerts": alerts}


def _finance_items_from_platform(platform: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, action in enumerate(
        list((platform.get("action_queue") or {}).get("actions") or [])
    ):
        severity = _safe_str(action.get("severity")).lower() or "low"
        title = _safe_str(action.get("title")) or f"Acción financiera {index + 1}"
        raw_href = action.get("href")
        href = _safe_href(raw_href)
        if href is None and not _safe_str(raw_href):
            href = "/admin/finanzas"
        items.append(
            _make_item(
                item_id=f"finance-action:{index}:{title.lower()}",
                source_type="finance_action",
                domain="finanzas",
                module=_safe_str(action.get("module")) or "Finanzas",
                status="needs_attention" if severity in {"high", "medium"} else "info",
                severity=severity,
                title=title,
                detail=_safe_str(action.get("detail"))
                or "Revisar en el módulo canónico.",
                href=href,
                owner_hint=_safe_str(action.get("owner")) or "Finanzas",
                tags=["finanzas"],
                prepared_action={
                    "canonical_action": None,
                    "mode": "read_only",
                    "label": "Abrir módulo",
                },
                secondary_label="Preguntar a Sam",
                secondary_href="/assistant",
            )
        )
    return items


def _finance_items_from_pending_payments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in list(payload.get("documentos") or []):
        document_id = _safe_str(row.get("documento_id"))
        ref = _safe_str(row.get("numero_referencia")) or document_id
        beneficiary = _safe_str(row.get("beneficiario_nombre")) or "Beneficiario"
        amount = _safe_float(row.get("monto_pendiente"))
        items.append(
            _make_item(
                item_id=f"pending-payment:{document_id}",
                source_type="pending_payment",
                domain="finanzas",
                module="Pagos",
                status="pending",
                severity="high",
                title=f"Pagar solicitud {ref}",
                detail=f"{beneficiary} por ${amount:,.2f}.",
                href="/admin/finanzas",
                owner_hint="Finanzas",
                tags=["pago", "solicitud"],
                source_ref={"document_id": document_id},
                prepared_action={
                    "canonical_action": None,
                    "mode": "prepare_only",
                    "label": "Abrir módulo",
                },
                secondary_label="Preguntar a Sam",
                secondary_href="/assistant",
            )
        )
    return items


def _finance_items_from_cfdi(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in list(payload.get("pending_expenses") or []):
        expense_id = _safe_str(row.get("expense_id"))
        ref = _safe_str(row.get("numero_referencia")) or expense_id
        uuid_manual = _safe_str(row.get("cfdi_uuid_manual")) or "UUID pendiente"
        items.append(
            _make_item(
                item_id=f"cfdi-pending:{expense_id}",
                source_type="cfdi_matching",
                domain="finanzas",
                module="DIOT / CFDI",
                status="needs_attention",
                severity="medium",
                title=f"Completar CFDI de {ref}",
                detail=f"UUID manual capturado: {uuid_manual}.",
                href="/admin/gastos/cfdis/matching",
                owner_hint="Finanzas",
                tags=["cfdi", "diot"],
                source_ref={"expense_id": expense_id},
                prepared_action={
                    "canonical_action": None,
                    "mode": "prepare_only",
                    "label": "Abrir módulo",
                },
                secondary_label="Preguntar a Sam",
                secondary_href="/assistant",
            )
        )
    for row in list(payload.get("unlinked_cfdis") or []):
        cfdi_id = _safe_str(row.get("cfdi_report_id") or row.get("id"))
        uuid_text = _safe_str(row.get("cfdi_uuid")) or cfdi_id
        items.append(
            _make_item(
                item_id=f"cfdi-unlinked:{cfdi_id}",
                source_type="cfdi_matching",
                domain="finanzas",
                module="DIOT / CFDI",
                status="needs_attention",
                severity="medium",
                title=f"CFDI sin gasto {uuid_text}",
                detail="Existe CFDI importado sin gasto vinculado.",
                href="/admin/gastos/cfdis/matching",
                owner_hint="Finanzas",
                tags=["cfdi", "matching"],
                prepared_action={
                    "canonical_action": None,
                    "mode": "read_only",
                    "label": "Abrir módulo",
                },
                secondary_label="Preguntar a Sam",
                secondary_href="/assistant",
            )
        )
    return items


def _operations_items_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    soul = snapshot.get("soul") or {}
    tournament = (soul.get("tournament") or {}) if isinstance(soul, dict) else {}
    tournament_id = _safe_str(tournament.get("id"))
    tournament_name = _safe_str(tournament.get("name")) or "Torneo"
    for index, action in enumerate(list(soul.get("pending_actions") or [])):
        text = _safe_str(action)
        if not text:
            continue
        items.append(
            _make_item(
                item_id=f"ops-pending:{tournament_id}:{index}",
                source_type="tournament_pending",
                domain="operaciones",
                module="Operaciones",
                status="pending",
                severity="medium",
                title=f"{tournament_name}: {text}",
                detail="Pendiente operativo del snapshot canónico.",
                owner_hint="Operaciones",
                tags=["operaciones", "torneo"],
                source_ref={"tournament_id": tournament_id},
                prepared_action={
                    "canonical_action": None,
                    "mode": "prepare_only",
                    "label": "Preguntar a Sam",
                },
                secondary_label="Preguntar a Sam",
                secondary_href="/assistant",
            )
        )
    for index, risk in enumerate(list(soul.get("risks") or [])):
        code = _safe_str(risk.get("code")) or f"risk-{index}"
        severity = _safe_str(risk.get("severity")).lower() or "medium"
        message = _safe_str(risk.get("message")) or "Riesgo operativo detectado."
        items.append(
            _make_item(
                item_id=f"ops-risk:{tournament_id}:{code}",
                source_type="tournament_risk",
                domain="operaciones",
                module="Operaciones",
                status="needs_attention",
                severity=severity if severity in {"high", "medium", "low"} else "medium",
                title=f"{tournament_name}: {message}",
                detail=f"Código de riesgo: {code}.",
                owner_hint="Operaciones",
                tags=["operaciones", "riesgo"],
                source_ref={"tournament_id": tournament_id},
                prepared_action={
                    "canonical_action": None,
                    "mode": "read_only",
                    "label": "Revisar detalle",
                },
                secondary_label="Preguntar a Sam",
                secondary_href="/assistant",
            )
        )
    return items


def _direction_items_from_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, alert in enumerate(list(payload.get("alerts") or [])):
        severity = _safe_str(alert.get("severity")).lower() or "medium"
        items.append(
            _make_item(
                item_id=f"direction-alert:{index}:{_safe_str(alert.get('code'))}",
                source_type="executive_alert",
                domain="direccion",
                module="Dirección",
                status="needs_attention" if severity in {"high", "medium"} else "info",
                severity=severity if severity in {"high", "medium", "low"} else "medium",
                title=_safe_str(alert.get("title")) or "Alerta ejecutiva",
                detail=_safe_str(alert.get("detail")) or "Revisar señal ejecutiva.",
                href="/admin/finanzas",
                owner_hint="Dirección",
                tags=["direccion", "alerta"],
                prepared_action={
                    "canonical_action": None,
                    "mode": "read_only",
                    "label": "Abrir módulo",
                },
                secondary_label="Preguntar a Sam",
                secondary_href="/assistant",
            )
        )
    return items


def _direction_snapshot(
    *,
    finance_platform: dict[str, Any],
    executive_dashboard: dict[str, Any],
    executive_alerts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "finance_brief": finance_platform.get("finance_brief") or {},
        "cash_control_center": finance_platform.get("cash_control_center") or {},
        "executive_alerts": executive_alerts,
        "budget_signals": [],
        "limitations": [
            "Budget signals only appear when an existing shared read model exposes them globally.",
        ],
        "current_period": {
            "period": finance_platform.get("period") or {},
            "summary": finance_platform.get("summary") or {},
        },
        "ytd": {
            "year": executive_dashboard.get("year"),
            "total": executive_dashboard.get("total"),
            "count": executive_dashboard.get("count"),
            "yoy_pct": executive_dashboard.get("yoy_pct"),
            "run_rate_projection": executive_dashboard.get("run_rate_projection"),
        },
    }


def _tab_counts(items: list[dict[str, Any]], *, show_direction: bool) -> dict[str, int]:
    counts = {
        "todo": len(
            [
                item
                for item in items
                if _safe_str(item.get("severity")).lower() in {"high", "medium"}
                and _safe_str(item.get("status")).lower()
                in {"needs_attention", "pending", "blocked", "ready"}
            ]
        ),
        "operaciones": len(
            [item for item in items if item.get("domain") == "operaciones"]
        ),
        "finanzas": len([item for item in items if item.get("domain") == "finanzas"]),
    }
    if show_direction:
        counts["direccion"] = len(
            [item for item in items if item.get("domain") == "direccion"]
        )
    return counts


def _filter_items(
    items: list[dict[str, Any]],
    *,
    tab: str,
    severity: str | None,
    status: str | None,
    source_type: str | None,
    module: str | None,
) -> list[dict[str, Any]]:
    severity_norm = _safe_str(severity).lower()
    status_norm = _safe_str(status).lower()
    source_norm = _safe_str(source_type).lower()
    module_norm = _safe_str(module).lower()

    filtered = list(items)
    if tab == "todo":
        filtered = [
            item
            for item in filtered
            if _safe_str(item.get("severity")).lower() in {"high", "medium"}
            and _safe_str(item.get("status")).lower()
            in {"needs_attention", "pending", "blocked", "ready"}
        ]
    elif tab in {"operaciones", "finanzas", "direccion"}:
        filtered = [
            item
            for item in filtered
            if _safe_str(item.get("domain")).lower() == tab
        ]

    if severity_norm:
        filtered = [
            item
            for item in filtered
            if _safe_str(item.get("severity")).lower() == severity_norm
        ]
    if status_norm:
        filtered = [
            item
            for item in filtered
            if _safe_str(item.get("status")).lower() == status_norm
        ]
    if source_norm:
        filtered = [
            item
            for item in filtered
            if _safe_str(item.get("source_type")).lower() == source_norm
        ]
    if module_norm:
        filtered = [
            item
            for item in filtered
            if module_norm in _safe_str(item.get("module")).lower()
        ]

    return sorted(filtered, key=_item_sort_key)


async def build_sam_inbox_payload(
    session: AsyncSession,
    *,
    current_empleado: Any,
    tab: str = "todo",
    severity: str | None = None,
    status: str | None = None,
    source_type: str | None = None,
    module: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    active_tab = _safe_str(tab).lower()
    if active_tab not in VALID_TABS:
        active_tab = "todo"

    finance_source = await build_finance_source_snapshot(
        session,
        year=now.year,
        month=now.month,
        limit=300,
    )
    finance_platform = build_finance_platform_snapshot(finance_source)

    finance_items = _finance_items_from_platform(finance_platform)
    source_health: dict[str, dict[str, Any]] = {
        "finance_platform": {"ok": True},
    }

    try:
        pending_payment_payload = await get_pending_document_payment_overview(
            session,
            actor_id=getattr(current_empleado, "id", None),
        )
        finance_items.extend(
            _finance_items_from_pending_payments(pending_payment_payload)
        )
        source_health["pending_payments"] = {"ok": True}
    except (DocumentoPaymentPermissionError, DocumentoPaymentValidationError) as exc:
        source_health["pending_payments"] = {"ok": False, "message": str(exc)}

    try:
        cfdi_payload = await get_cfdi_matching_overview(session, view=None, limit=100)
        finance_items.extend(_finance_items_from_cfdi(cfdi_payload))
        source_health["cfdi_matching"] = {"ok": True}
    except Exception as exc:  # pragma: no cover - defensive guard
        source_health["cfdi_matching"] = {"ok": False, "message": str(exc)}

    operation_items: list[dict[str, Any]] = []
    try:
        tournaments = await _load_active_tournaments(session, limit=3)
        for tournament in tournaments:
            snapshot = await build_tournament_soul_snapshot(
                tournament_key="all",
                tournament_slug=str(tournament.id),
                include_media=False,
                include_communications=False,
                limit=120,
            )
            operation_items.extend(_operations_items_from_snapshot(snapshot))
        source_health["tournament_soul"] = {"ok": True, "count": len(operation_items)}
    except Exception as exc:
        source_health["tournament_soul"] = {"ok": False, "message": str(exc)}

    executive_dashboard: dict[str, Any] = {}
    executive_alerts: dict[str, Any] = {"alerts": []}
    direction_snapshot: dict[str, Any] = {}
    direction_items: list[dict[str, Any]] = []
    if _is_admin_role(getattr(current_empleado, "rol", None)):
        try:
            direction_sources = await _load_direction_sources(session, year=now.year)
            executive_dashboard = direction_sources.get("dashboard") or {}
            executive_alerts = direction_sources.get("alerts") or {"alerts": []}
            direction_snapshot = _direction_snapshot(
                finance_platform=finance_platform,
                executive_dashboard=executive_dashboard,
                executive_alerts=executive_alerts,
            )
            direction_items = _direction_items_from_alerts(executive_alerts)
            source_health["direccion"] = {"ok": True}
        except Exception as exc:
            source_health["direccion"] = {"ok": False, "message": str(exc)}

    items = finance_items + operation_items + direction_items
    counts = _tab_counts(
        items,
        show_direction=_is_admin_role(getattr(current_empleado, "rol", None)),
    )
    visible_items = _filter_items(
        items,
        tab=active_tab,
        severity=severity,
        status=status,
        source_type=source_type,
        module=module,
    )
    tabs = [
        {"key": "todo", "label": "Todo", "count": counts.get("todo", 0)},
        {
            "key": "operaciones",
            "label": "Operaciones",
            "count": counts.get("operaciones", 0),
        },
        {"key": "finanzas", "label": "Finanzas", "count": counts.get("finanzas", 0)},
    ]
    if _is_admin_role(getattr(current_empleado, "rol", None)):
        tabs.append(
            {
                "key": "direccion",
                "label": "Dirección",
                "count": counts.get("direccion", 0),
            }
        )

    return {
        "generated_at": now.isoformat(),
        "tab": active_tab,
        "tabs": tabs,
        "items": visible_items,
        "all_items": items,
        "direction": direction_snapshot,
        "source_health": source_health,
        "filters": {
            "severity": _safe_str(severity).lower(),
            "status": _safe_str(status).lower(),
            "source_type": _safe_str(source_type).lower(),
            "module": _safe_str(module),
        },
        "available_statuses": [
            "needs_attention",
            "pending",
            "ready",
            "done",
            "blocked",
            "info",
        ],
        "available_severities": ["high", "medium", "low"],
    }
