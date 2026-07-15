from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from devnous.tournaments.instances.copa_telmex.ctt_home_operations import (
    build_home_operations_snapshot,
    classify_review_operational_state,
)


def _record(
    session_id: str,
    *,
    status: str = "ready",
    values: Optional[Dict[str, Any]] = None,
    updated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": session_id,
        "status": status,
        "tournament_slug": "copa_telmex",
        "started_at": updated_at,
        "updated_at": updated_at,
        "draft_updated_at": updated_at,
    }
    payload.update(values or {})
    return payload


def test_operational_state_uses_validation_instead_of_ready_db_status() -> None:
    assert classify_review_operational_state("ready", ready_to_commit=True) == "ready"
    assert (
        classify_review_operational_state("ready", ready_to_commit=False) == "blocked"
    )
    assert (
        classify_review_operational_state(
            "ready",
            ready_to_commit=True,
            blocking_issue_count=1,
        )
        == "blocked"
    )
    assert (
        classify_review_operational_state("processing", ready_to_commit=False)
        == "processing"
    )
    assert (
        classify_review_operational_state("rejected", ready_to_commit=False)
        == "rejected"
    )


def test_snapshot_counts_states_orders_recency_and_excludes_committed() -> None:
    now = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)
    sessions = [
        _record(
            "ready-session",
            values={
                "ready_to_commit": True,
                "player_count": 16,
                "intake_folio": "REG-2026-READY001",
            },
            updated_at=now - timedelta(minutes=5),
        ),
        _record(
            "blocked-session",
            values={
                "ready_to_commit": False,
                "blocking_issue_count": 1,
                "player_count": 15,
            },
            updated_at=now - timedelta(hours=2),
        ),
        _record(
            "processing-session",
            status="processing",
            updated_at=now - timedelta(seconds=20),
        ),
        _record(
            "rejected-session",
            status="rejected",
            updated_at=now - timedelta(days=2),
        ),
        _record(
            "committed-session",
            status="committed",
            values={"ready_to_commit": True},
            updated_at=now,
        ),
    ]

    snapshot = build_home_operations_snapshot(sessions, now=now)

    assert snapshot["pending_count"] == 4
    assert snapshot["ready_count"] == 1
    assert snapshot["blocked_count"] == 1
    assert snapshot["processing_count"] == 1
    assert snapshot["rejected_count"] == 1
    assert [item["id"] for item in snapshot["recent"]] == [
        "processing-session",
        "ready-session",
        "blocked-session",
        "rejected-session",
    ]
    assert snapshot["recent"][0]["recency"] == "Ahora"
    assert snapshot["recent"][1]["reference"] == "REG-2026-READY001"
    assert snapshot["recent"][1]["action_label"] == "Continuar captura"
    assert snapshot["recent"][2]["blocking_issue_count"] == 1


def test_snapshot_bounds_recent_rows_and_handles_naive_database_timestamps() -> None:
    now = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)
    sessions = [
        _record(
            f"session-{index}",
            values={"ready_to_commit": False},
            updated_at=datetime(2026, 7, 15, 19, index),
        )
        for index in range(10)
    ]

    snapshot = build_home_operations_snapshot(sessions, now=now, recent_limit=3)

    assert len(snapshot["recent"]) == 3
    assert [item["id"] for item in snapshot["recent"]] == [
        "session-9",
        "session-8",
        "session-7",
    ]
    assert snapshot["recent"][0]["recency"] == "Hace 51 min"
