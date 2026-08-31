"""Read-only AR pre-matching over candidate bank inflows."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import text

from .collection_matches import list_ar_collection_matches
from .service import build_ar_read_model


FORBIDDEN_CONFIRMED_STATUSES = {"matched_collected", "collected", "paid"}


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _norm(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", _safe_str(value).upper())


def _words(value: Any) -> set[str]:
    return {
        token
        for token in re.split(r"[^A-Z0-9]+", _safe_str(value).upper())
        if len(token) >= 4
    }


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return _safe_str(value) or None


def _date_distance_days(left: Any, right: Any) -> Optional[int]:
    if not left or not right:
        return None
    left_date = left.date() if isinstance(left, datetime) else left
    if isinstance(left_date, str):
        try:
            left_date = datetime.fromisoformat(left_date[:10]).date()
        except ValueError:
            return None
    right_date = right.date() if isinstance(right, datetime) else right
    if isinstance(right_date, str):
        try:
            right_date = datetime.fromisoformat(right_date[:10]).date()
        except ValueError:
            return None
    if not isinstance(left_date, date) or not isinstance(right_date, date):
        return None
    return abs((left_date - right_date).days)


async def list_candidate_bank_inflows(
    session: Any,
    *,
    year: Optional[int] = None,
    month: Optional[int] = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Load bank inflows as candidate evidence, not collection proof."""

    row_limit = max(1, min(int(limit or 500), 5000))
    filters = [
        "signo = '+'",
        "COALESCE(NULLIF(TRIM(conciliacion_estado), ''), 'unmatched') "
        "IN ('unmatched', 'candidate', 'pending', 'review')",
    ]
    params: dict[str, Any] = {"limit": row_limit}
    if year is not None:
        filters.append("EXTRACT(YEAR FROM fecha) = :year")
        params["year"] = int(year)
    if month is not None:
        filters.append("EXTRACT(MONTH FROM fecha) = :month")
        params["month"] = int(month)
    rows = (
        (
            await session.execute(
                text(
                    f"""
                    SELECT id, fecha, importe, rfc_ordenante, nombre_ordenante,
                           descripcion, concepto_banco, referencia_bancaria,
                           clave_rastreo, conciliacion_estado
                    FROM bank_movements
                    WHERE {' AND '.join(filters)}
                    ORDER BY fecha DESC NULLS LAST, created_at DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _ar_items_from_read_model(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for source_key in ("issued_linked", "issued_unlinked"):
        for item in payload.get(source_key) or []:
            amount = item.get("issued_amount") or item.get("linked_income_amount")
            items.append(
                {
                    "ar_item_id": _safe_str(item.get("ar_item_id")),
                    "source": source_key,
                    "payer_rfc": _safe_str(item.get("payer_rfc")) or None,
                    "payer_name": _safe_str(item.get("payer_name")) or None,
                    "amount": _safe_float(amount),
                    "issued_date": item.get("recognized_income_date")
                    or item.get("issued_date"),
                    "collection_status": "collection_unknown",
                }
            )
    return items


def _score_candidate(
    ar_item: dict[str, Any],
    movement: dict[str, Any],
    *,
    tolerance: float,
) -> Optional[dict[str, Any]]:
    ar_amount = _safe_float(ar_item.get("amount"))
    bank_amount = _safe_float(movement.get("importe"))
    amount_delta = round(abs(ar_amount - bank_amount), 2)
    amount_match = amount_delta <= tolerance
    payer_rfc = _norm(ar_item.get("payer_rfc"))
    bank_rfc = _norm(movement.get("rfc_ordenante"))
    rfc_match = bool(payer_rfc and bank_rfc and payer_rfc == bank_rfc)
    payer_words = _words(ar_item.get("payer_name"))
    bank_text = " ".join(
        [
            _safe_str(movement.get("nombre_ordenante")),
            _safe_str(movement.get("descripcion")),
            _safe_str(movement.get("concepto_banco")),
        ]
    )
    name_overlap = sorted(payer_words.intersection(_words(bank_text)))
    identity_match = rfc_match or bool(name_overlap)
    days_distance = _date_distance_days(
        ar_item.get("issued_date"),
        movement.get("fecha"),
    )

    if not (amount_match or identity_match):
        return None

    signals = []
    if amount_match:
        signals.append("amount")
    if rfc_match:
        signals.append("rfc")
    if name_overlap:
        signals.append("name")
    if days_distance is not None and days_distance <= 45:
        signals.append("date_window")

    status = "manual_match_required"
    reason = "requires_manual_review"
    if amount_match and identity_match:
        status = "candidate_match"
        reason = "amount_and_identity_candidate"
    elif amount_match:
        reason = "amount_only"
    elif identity_match:
        reason = "identity_only"

    return {
        "bank_movement_id": _safe_str(movement.get("id")),
        "bank_amount": bank_amount,
        "bank_date": _iso(movement.get("fecha")),
        "bank_rfc": _safe_str(movement.get("rfc_ordenante")) or None,
        "bank_name": _safe_str(movement.get("nombre_ordenante")) or None,
        "bank_description": _safe_str(movement.get("descripcion"))
        or _safe_str(movement.get("concepto_banco"))
        or None,
        "amount_delta": amount_delta,
        "signals": signals,
        "score": len(signals),
        "status": status,
        "reason": reason,
    }


async def build_ar_matching_workbench(
    session: Any,
    *,
    budget_version_id: str,
    tournament_id: Optional[str] = None,
    tournament_code: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    tolerance: float = 1.0,
    limit: int = 500,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Build read-only AR pre-matching candidates without collection authority."""

    row_limit = max(1, min(int(limit or 500), 5000))
    safe_tolerance = max(0.0, min(float(tolerance or 0), 100000.0))
    ar_payload = await build_ar_read_model(
        session,
        budget_version_id=budget_version_id,
        tournament_id=tournament_id,
        tournament_code=tournament_code,
        limit=row_limit,
        ensure_schema=ensure_schema,
    )
    bank_inflows = await list_candidate_bank_inflows(
        session,
        year=year,
        month=month,
        limit=row_limit,
    )
    accepted_matches = await list_ar_collection_matches(
        session,
        budget_version_id=budget_version_id,
        include_reversed=False,
        ensure_schema=ensure_schema,
    )
    accepted_item_ids = {
        _safe_str(match.get("ar_item_id")) for match in accepted_matches
    }
    ar_items = _ar_items_from_read_model(ar_payload)
    items: list[dict[str, Any]] = []
    used_bank_ids: set[str] = set()

    for ar_item in ar_items:
        if _safe_str(ar_item.get("ar_item_id")) in accepted_item_ids:
            continue
        candidates = [
            candidate
            for movement in bank_inflows
            if (
                candidate := _score_candidate(
                    ar_item,
                    movement,
                    tolerance=safe_tolerance,
                )
            )
        ]
        candidates = sorted(
            candidates,
            key=lambda item: (-int(item.get("score") or 0), item["amount_delta"]),
        )[:5]
        for candidate in candidates:
            used_bank_ids.add(candidate["bank_movement_id"])

        status = "collection_unknown"
        reason = "no_candidate_bank_evidence"
        if not ar_item.get("payer_rfc") and not ar_item.get("payer_name"):
            status = "payer_gap"
            reason = "missing_payer_identity"
        elif len(candidates) == 1:
            status = candidates[0]["status"]
            reason = candidates[0]["reason"]
        elif len(candidates) > 1:
            status = "manual_match_required"
            reason = "multiple_candidate_bank_inflows"

        items.append(
            {
                **ar_item,
                "status": status,
                "reason": reason,
                "candidate_evidence": candidates,
            }
        )

    unmatched = []
    for movement in bank_inflows:
        movement_id = _safe_str(movement.get("id"))
        if movement_id in used_bank_ids:
            continue
        unmatched.append(
            {
                "status": "unmatched_bank_inflow",
                "bank_movement_id": movement_id,
                "bank_amount": _safe_float(movement.get("importe")),
                "bank_date": _iso(movement.get("fecha")),
                "bank_rfc": _safe_str(movement.get("rfc_ordenante")) or None,
                "bank_name": _safe_str(movement.get("nombre_ordenante")) or None,
                "reason": "no_ar_item_candidate",
            }
        )

    statuses = [item["status"] for item in items] + [
        item["status"] for item in unmatched
    ]
    forbidden = sorted(set(statuses).intersection(FORBIDDEN_CONFIRMED_STATUSES))

    return {
        "ok": True,
        "read_only": True,
        "budget_version_id": _safe_str(budget_version_id),
        "tournament_id": _safe_str(tournament_id) or None,
        "tournament_code": _safe_str(tournament_code) or None,
        "collection_authority": False,
        "tolerance": safe_tolerance,
        "summary": {
            "ar_item_count": len(items),
            "accepted_match_count": len(accepted_matches),
            "candidate_match_count": statuses.count("candidate_match"),
            "manual_match_required_count": statuses.count("manual_match_required"),
            "collection_unknown_count": statuses.count("collection_unknown"),
            "payer_gap_count": statuses.count("payer_gap"),
            "unmatched_bank_inflow_count": statuses.count("unmatched_bank_inflow"),
            "forbidden_status_count": len(forbidden),
        },
        "items": items,
        "accepted_matches": accepted_matches,
        "unmatched_bank_inflows": unmatched,
        "source_notes": [
            "bank_movements is candidate evidence only",
            "candidate_match is not collection proof",
        ],
    }
