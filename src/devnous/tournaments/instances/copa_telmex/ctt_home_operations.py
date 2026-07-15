"""Build the bounded registration-review operations snapshot shown on Home."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional

HOME_RECENT_REVIEW_LIMIT = 6

_STATE_PRESENTATION = {
    "ready": {
        "label": "Lista para capturar",
        "action_label": "Continuar captura",
    },
    "blocked": {
        "label": "Bloqueada",
        "action_label": "Resolver bloqueos",
    },
    "processing": {
        "label": "Procesando",
        "action_label": "Ver progreso",
    },
    "rejected": {
        "label": "Rechazada",
        "action_label": "Corregir expediente",
    },
    "committed": {
        "label": "Capturada",
        "action_label": "Ver revisión",
    },
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _latest_review_timestamp(record: Mapping[str, Any]) -> Optional[datetime]:
    candidates = [
        _as_utc(record.get("updated_at")),
        _as_utc(record.get("draft_updated_at")),
        _as_utc(record.get("started_at")),
    ]
    timestamps = [value for value in candidates if value is not None]
    return max(timestamps) if timestamps else None


def _relative_recency(value: Optional[datetime], *, now: datetime) -> str:
    if value is None:
        return "Sin fecha"
    seconds = max(0, int((now - value).total_seconds()))
    if seconds < 60:
        return "Ahora"
    if seconds < 3600:
        return f"Hace {seconds // 60} min"
    if seconds < 86400:
        return f"Hace {seconds // 3600} h"
    if seconds < 604800:
        return f"Hace {seconds // 86400} d"
    return value.strftime("%d/%m/%Y")


def classify_review_operational_state(
    status: Any,
    *,
    ready_to_commit: Any,
    blocking_issue_count: Any = 0,
) -> str:
    """Return the operator-facing state without trusting the coarse DB status."""

    normalized_status = str(status or "").strip().lower()
    if normalized_status == "committed":
        return "committed"
    if normalized_status == "rejected":
        return "rejected"
    if normalized_status in {"uploaded", "processing"}:
        return "processing"

    if ready_to_commit is True and _safe_int(blocking_issue_count) == 0:
        return "ready"
    return "blocked"


def build_home_operations_snapshot(
    review_records: Iterable[Any],
    *,
    now: Optional[datetime] = None,
    recent_limit: int = HOME_RECENT_REVIEW_LIMIT,
) -> Dict[str, Any]:
    """Summarize the non-committed queue and its most recent continuation links."""

    current_time = _as_utc(now) or datetime.now(timezone.utc)
    rows = []
    for raw_record in review_records:
        record = _mapping(raw_record)
        blocking_count = _safe_int(record.get("blocking_issue_count"))
        state = classify_review_operational_state(
            record.get("status"),
            ready_to_commit=record.get("ready_to_commit"),
            blocking_issue_count=blocking_count,
        )
        if state == "committed":
            continue

        session_id = str(record.get("id") or "").strip()
        if not session_id:
            continue
        updated_at = _latest_review_timestamp(record)
        presentation = _STATE_PRESENTATION[state]
        issue_count = max(
            _safe_int(record.get("issue_count")),
            blocking_count,
        )
        folio = str(record.get("intake_folio") or "").strip() or None
        rows.append(
            {
                "id": session_id,
                "reference": folio or f"Expediente {session_id[:8]}",
                "state": state,
                "state_label": presentation["label"],
                "action_label": presentation["action_label"],
                "tournament_slug": (
                    str(record.get("tournament_slug") or "").strip()
                    or "Torneo sin definir"
                ),
                "player_count": _safe_int(record.get("player_count")),
                "issue_count": issue_count,
                "blocking_issue_count": blocking_count,
                "updated_at_iso": updated_at.isoformat() if updated_at else "",
                "updated_at_display": (
                    updated_at.strftime("%d/%m/%Y %H:%M UTC")
                    if updated_at
                    else "Sin fecha"
                ),
                "recency": _relative_recency(updated_at, now=current_time),
                "review_url": f"/registration-review/{session_id}",
                "_updated_at": updated_at,
            }
        )

    rows.sort(
        key=lambda item: item["_updated_at"]
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    counts = {
        state: sum(1 for item in rows if item["state"] == state)
        for state in ("ready", "blocked", "processing", "rejected")
    }
    bounded_limit = max(1, min(int(recent_limit or HOME_RECENT_REVIEW_LIMIT), 12))
    recent_rows = []
    for item in rows[:bounded_limit]:
        public_item = dict(item)
        public_item.pop("_updated_at", None)
        recent_rows.append(public_item)

    return {
        "pending_count": len(rows),
        "ready_count": counts["ready"],
        "blocked_count": counts["blocked"],
        "processing_count": counts["processing"],
        "rejected_count": counts["rejected"],
        "recent": recent_rows,
    }
