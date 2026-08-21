"""Accepted AR collection matches and audit trail."""

from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text


ACCEPTED_STATUS = "accepted_collection_match"
REVERSED_STATUS = "match_reversed"


class ARCollectionMatchError(ValueError):
    """Raised when an AR collection match violates authority rules."""


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ar_amount(ar_item: dict[str, Any]) -> float:
    return _safe_float(
        ar_item.get("issued_amount")
        or ar_item.get("linked_income_amount")
        or ar_item.get("amount")
        or ar_item.get("expected_income_amount")
    )


def _snapshot(row: dict[str, Any] | None) -> dict[str, Any]:
    return dict(row or {})


async def ensure_ar_collection_match_schema(session: Any) -> None:
    """Create AR collection match tables and indexes idempotently."""

    statements = [
        """
        CREATE TABLE IF NOT EXISTS ar_collection_matches (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ar_item_id TEXT NOT NULL,
            budget_version_id UUID NOT NULL,
            budget_line_id UUID NULL,
            cfdi_report_id UUID NULL,
            bank_movement_id UUID NOT NULL,
            accepted_amount NUMERIC(14,2) NOT NULL,
            collection_date TIMESTAMPTZ NULL,
            payer_rfc TEXT NULL,
            payer_name TEXT NULL,
            status TEXT NOT NULL,
            acceptance_reason TEXT NOT NULL,
            accepted_by_empleado_id UUID NULL,
            accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reversed_by_empleado_id UUID NULL,
            reversed_at TIMESTAMPTZ NULL,
            reversal_reason TEXT NULL,
            evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ar_collection_matches_active_ar_item
        ON ar_collection_matches(ar_item_id)
        WHERE status = 'accepted_collection_match'
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ar_collection_matches_active_bank
        ON ar_collection_matches(bank_movement_id)
        WHERE status = 'accepted_collection_match'
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_ar_collection_matches_budget_version
        ON ar_collection_matches(budget_version_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_ar_collection_matches_cfdi_report
        ON ar_collection_matches(cfdi_report_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS ar_collection_match_audit_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            collection_match_id UUID NOT NULL REFERENCES ar_collection_matches(id),
            action TEXT NOT NULL,
            actor_empleado_id UUID NULL,
            before_state JSONB NOT NULL DEFAULT '{}'::jsonb,
            after_state JSONB NOT NULL DEFAULT '{}'::jsonb,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_ar_collection_match_audit_match
        ON ar_collection_match_audit_log(collection_match_id, created_at DESC)
        """,
    ]
    for statement in statements:
        await session.execute(text(statement))


async def _load_bank_movement(session: Any, bank_movement_id: str) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    """
                    SELECT id, signo, importe, fecha, rfc_ordenante,
                           nombre_ordenante, descripcion, concepto_banco,
                           conciliacion_estado
                    FROM bank_movements
                    WHERE id = CAST(:bank_movement_id AS uuid)
                    LIMIT 1
                    """
                ),
                {"bank_movement_id": bank_movement_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row or {})


async def _find_active_match(
    session: Any,
    *,
    ar_item_id: Optional[str] = None,
    bank_movement_id: Optional[str] = None,
) -> dict[str, Any]:
    filters = ["status = :status"]
    params: dict[str, Any] = {"status": ACCEPTED_STATUS}
    if ar_item_id:
        filters.append("ar_item_id = :ar_item_id")
        params["ar_item_id"] = ar_item_id
    if bank_movement_id:
        filters.append("bank_movement_id = CAST(:bank_movement_id AS uuid)")
        params["bank_movement_id"] = bank_movement_id
    row = (
        (
            await session.execute(
                text(
                    f"""
                    SELECT *
                    FROM ar_collection_matches
                    WHERE {' AND '.join(filters)}
                    LIMIT 1
                    """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    return dict(row or {})


async def _insert_match(
    session: Any,
    *,
    ar_item: dict[str, Any],
    bank_movement: dict[str, Any],
    actor_empleado_id: Optional[str],
    acceptance_reason: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    params = {
        "ar_item_id": _safe_str(ar_item.get("ar_item_id")),
        "budget_version_id": _safe_str(ar_item.get("budget_version_id")),
        "budget_line_id": _safe_str(ar_item.get("budget_line_id")) or None,
        "cfdi_report_id": _safe_str(ar_item.get("cfdi_report_id")) or None,
        "bank_movement_id": _safe_str(bank_movement.get("id")),
        "accepted_amount": _ar_amount(ar_item),
        "collection_date": bank_movement.get("fecha"),
        "payer_rfc": _safe_str(ar_item.get("payer_rfc")) or None,
        "payer_name": _safe_str(ar_item.get("payer_name")) or None,
        "status": ACCEPTED_STATUS,
        "acceptance_reason": acceptance_reason,
        "accepted_by_empleado_id": actor_empleado_id,
        "evidence": json.dumps(evidence or {}, default=str),
    }
    row = (
        (
            await session.execute(
                text(
                    """
                    INSERT INTO ar_collection_matches (
                        ar_item_id, budget_version_id, budget_line_id,
                        cfdi_report_id, bank_movement_id, accepted_amount,
                        collection_date, payer_rfc, payer_name, status,
                        acceptance_reason, accepted_by_empleado_id, evidence
                    )
                    VALUES (
                        :ar_item_id, CAST(:budget_version_id AS uuid),
                        CAST(:budget_line_id AS uuid),
                        CAST(:cfdi_report_id AS uuid),
                        CAST(:bank_movement_id AS uuid), :accepted_amount,
                        :collection_date, :payer_rfc, :payer_name, :status,
                        :acceptance_reason, CAST(:accepted_by_empleado_id AS uuid),
                        CAST(:evidence AS jsonb)
                    )
                    RETURNING *
                    """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    return dict(row or params)


async def _load_match(session: Any, match_id: str) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    """
                    SELECT *
                    FROM ar_collection_matches
                    WHERE id = CAST(:match_id AS uuid)
                    LIMIT 1
                    """
                ),
                {"match_id": match_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row or {})


async def _update_match_reversed(
    session: Any,
    *,
    match_id: str,
    actor_empleado_id: Optional[str],
    reversal_reason: str,
) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    """
                    UPDATE ar_collection_matches
                    SET status = :status,
                        reversed_by_empleado_id = CAST(:actor AS uuid),
                        reversed_at = NOW(),
                        reversal_reason = :reason,
                        updated_at = NOW()
                    WHERE id = CAST(:match_id AS uuid)
                    RETURNING *
                    """
                ),
                {
                    "status": REVERSED_STATUS,
                    "actor": actor_empleado_id,
                    "reason": reversal_reason,
                    "match_id": match_id,
                },
            )
        )
        .mappings()
        .first()
    )
    return dict(row or {})


async def _audit_match_event(
    session: Any,
    *,
    collection_match_id: str,
    action: str,
    actor_empleado_id: Optional[str],
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    details: dict[str, Any],
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO ar_collection_match_audit_log (
                collection_match_id, action, actor_empleado_id,
                before_state, after_state, details
            )
            VALUES (
                CAST(:collection_match_id AS uuid), :action,
                CAST(:actor_empleado_id AS uuid),
                CAST(:before_state AS jsonb),
                CAST(:after_state AS jsonb),
                CAST(:details AS jsonb)
            )
            """
        ),
        {
            "collection_match_id": collection_match_id,
            "action": action,
            "actor_empleado_id": actor_empleado_id,
            "before_state": json.dumps(before_state or {}, default=str),
            "after_state": json.dumps(after_state or {}, default=str),
            "details": json.dumps(details or {}, default=str),
        },
    )


def _identity_matches(
    ar_item: dict[str, Any],
    bank_movement: dict[str, Any],
) -> bool:
    ar_rfc = _norm(ar_item.get("payer_rfc"))
    bank_rfc = _norm(bank_movement.get("rfc_ordenante"))
    if ar_rfc and bank_rfc and ar_rfc == bank_rfc:
        return True
    ar_words = _words(ar_item.get("payer_name"))
    bank_words = _words(
        " ".join(
            [
                _safe_str(bank_movement.get("nombre_ordenante")),
                _safe_str(bank_movement.get("descripcion")),
                _safe_str(bank_movement.get("concepto_banco")),
            ]
        )
    )
    return bool(ar_words.intersection(bank_words))


def _validate_acceptance(
    *,
    ar_item: dict[str, Any],
    bank_movement: dict[str, Any],
    acceptance_reason: str,
    tolerance: float,
) -> None:
    if not _safe_str(ar_item.get("ar_item_id")):
        raise ARCollectionMatchError("missing_ar_item_id")
    if not _safe_str(ar_item.get("budget_version_id")):
        raise ARCollectionMatchError("missing_budget_version_id")
    if not bank_movement:
        raise ARCollectionMatchError("bank_movement_not_found")
    if _safe_str(bank_movement.get("signo")) != "+":
        raise ARCollectionMatchError("bank_movement_not_inflow")
    ar_amount = _ar_amount(ar_item)
    bank_amount = _safe_float(bank_movement.get("importe"))
    if ar_amount <= 0 or bank_amount <= 0:
        raise ARCollectionMatchError("invalid_amount")
    if abs(ar_amount - bank_amount) > tolerance:
        raise ARCollectionMatchError("amount_incompatible")
    if not _identity_matches(ar_item, bank_movement) and not acceptance_reason:
        raise ARCollectionMatchError("manual_reason_required")


async def list_ar_collection_matches(
    session: Any,
    *,
    budget_version_id: str,
    include_reversed: bool = False,
) -> list[dict[str, Any]]:
    """List AR collection matches for a budget version."""

    await ensure_ar_collection_match_schema(session)
    status_filter = "" if include_reversed else "AND status = :status"
    params: dict[str, Any] = {
        "budget_version_id": budget_version_id,
        "status": ACCEPTED_STATUS,
    }
    rows = (
        (
            await session.execute(
                text(
                    f"""
                    SELECT *
                    FROM ar_collection_matches
                    WHERE budget_version_id = CAST(:budget_version_id AS uuid)
                    {status_filter}
                    ORDER BY accepted_at DESC
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def accept_ar_collection_match(
    session: Any,
    *,
    ar_item: dict[str, Any],
    bank_movement_id: str,
    actor_empleado_id: Optional[str],
    acceptance_reason: str,
    tolerance: float = 1.0,
    evidence: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Accept a one-to-one AR collection match without touching bank state."""

    await ensure_ar_collection_match_schema(session)
    clean_reason = _safe_str(acceptance_reason)
    safe_tolerance = max(0.0, min(float(tolerance or 0), 100000.0))
    bank_movement = await _load_bank_movement(session, bank_movement_id)
    _validate_acceptance(
        ar_item=ar_item,
        bank_movement=bank_movement,
        acceptance_reason=clean_reason,
        tolerance=safe_tolerance,
    )
    active_ar = await _find_active_match(
        session,
        ar_item_id=_safe_str(ar_item.get("ar_item_id")),
    )
    if active_ar:
        raise ARCollectionMatchError("active_match_exists_for_ar_item")
    active_bank = await _find_active_match(
        session,
        bank_movement_id=_safe_str(bank_movement.get("id")),
    )
    if active_bank:
        raise ARCollectionMatchError("active_match_exists_for_bank_movement")

    evidence_payload = {
        "accepted_at": _now_iso(),
        "bank_movement": _snapshot(bank_movement),
        "ar_item": _snapshot(ar_item),
        **(evidence or {}),
    }
    row = await _insert_match(
        session,
        ar_item=ar_item,
        bank_movement=bank_movement,
        actor_empleado_id=actor_empleado_id,
        acceptance_reason=clean_reason,
        evidence=evidence_payload,
    )
    await _audit_match_event(
        session,
        collection_match_id=_safe_str(row.get("id")),
        action="accept_collection_match",
        actor_empleado_id=actor_empleado_id,
        before_state={},
        after_state=row,
        details={"reason": clean_reason, "tolerance": safe_tolerance},
    )
    return row


async def reverse_ar_collection_match(
    session: Any,
    *,
    match_id: str,
    actor_empleado_id: Optional[str],
    reversal_reason: str,
) -> dict[str, Any]:
    """Reverse an accepted AR collection match without deleting history."""

    await ensure_ar_collection_match_schema(session)
    clean_reason = _safe_str(reversal_reason)
    if not clean_reason:
        raise ARCollectionMatchError("reversal_reason_required")
    before = await _load_match(session, match_id)
    if not before:
        raise ARCollectionMatchError("collection_match_not_found")
    if _safe_str(before.get("status")) != ACCEPTED_STATUS:
        raise ARCollectionMatchError("collection_match_not_active")
    after = await _update_match_reversed(
        session,
        match_id=match_id,
        actor_empleado_id=actor_empleado_id,
        reversal_reason=clean_reason,
    )
    await _audit_match_event(
        session,
        collection_match_id=_safe_str(after.get("id") or before.get("id")),
        action="reverse_collection_match",
        actor_empleado_id=actor_empleado_id,
        before_state=before,
        after_state=after,
        details={"reason": clean_reason},
    )
    return after
