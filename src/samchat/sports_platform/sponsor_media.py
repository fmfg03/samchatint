"""Sponsor/media proof and approval helpers for the sports platform snapshot."""

from __future__ import annotations

from typing import Any

CLIENT_AUTHORIZATION_STATUS = "not_authorized_by_fundacion_telmex"
DEFAULT_AUDIT_TIMESTAMP = "deterministic_timestamp"

APPROVAL_STATES = (
    "draft",
    "automated_review",
    "ops_review",
    "sponsor_review",
    "changes_requested",
    "rejected",
    "approved",
    "ready_for_manual_distribution",
)

SPONSOR_CONTENT_REQUIRED_REVIEWS = ("ops_review", "sponsor_review")


def _safe_str(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _record_id(row: dict[str, Any], fallback: str) -> str:
    return _safe_str(row.get("id") or row.get("obligation_id"), fallback)


def _has_review(workflow: dict[str, Any], state: str) -> bool:
    return any(
        event.get("to_state") == state
        for event in workflow.get("audit_trail") or []
    )


def _audit_event(
    *,
    from_state: str,
    to_state: str,
    actor_role: str,
    action: str,
    comment: str,
    timestamp: str,
) -> dict[str, Any]:
    return {
        "from_state": from_state,
        "to_state": to_state,
        "actor_role": actor_role,
        "action": action,
        "comment": comment,
        "timestamp": timestamp,
    }


def build_sponsor_proof_package(
    *,
    sponsor: dict[str, Any],
    event: dict[str, Any],
    obligations: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    approval_state: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic proof-of-performance package from structured data."""

    sponsor_id = _safe_str(sponsor.get("id") or sponsor.get("sponsor_id"), "sponsor")
    event_id = _safe_str(
        event.get("id")
        or event.get("event_id")
        or event.get("match_id")
        or event.get("tournament_id"),
        "event",
    )
    obligation_ids = [
        _record_id(obligation, f"obligation_{index + 1}")
        for index, obligation in enumerate(obligations)
    ]
    linked_obligation_ids = {
        _safe_str(item.get("linked_obligation_id"))
        for item in evidence_items
        if _safe_str(item.get("linked_obligation_id"))
    }
    fulfilled_ids = [
        obligation_id
        for obligation_id in obligation_ids
        if obligation_id in linked_obligation_ids
    ]
    missing_ids = [
        obligation_id
        for obligation_id in obligation_ids
        if obligation_id not in linked_obligation_ids
    ]
    total = len(obligation_ids)
    fulfilled = len(fulfilled_ids)
    missing = len(missing_ids)
    coverage_ratio = round(fulfilled / total, 4) if total else 0.0
    if missing:
        status = "missing_evidence"
    elif approval_state == "ready_for_manual_distribution":
        status = "approved_for_manual_distribution"
    else:
        status = "ready_for_review"
    return {
        "package_id": f"proof_pkg_{sponsor_id}_{event_id}",
        "sponsor_id": sponsor_id,
        "event_id": event_id,
        "tournament_id": event.get("tournament_id"),
        "status": status,
        "obligation_coverage": {
            "total_obligations": total,
            "fulfilled": fulfilled,
            "missing": missing,
            "coverage_ratio": coverage_ratio,
        },
        "evidence_index": [
            {
                "evidence_id": _safe_str(
                    item.get("id") or item.get("evidence_id"),
                    f"evidence_{index + 1}",
                ),
                "type": _safe_str(item.get("type"), "manual_note"),
                "linked_obligation_id": _safe_str(item.get("linked_obligation_id")),
                "source": _safe_str(item.get("source"), "manual_upload"),
                "requires_human_review": True,
            }
            for index, item in enumerate(evidence_items)
        ],
        "missing_evidence": [
            {"linked_obligation_id": obligation_id}
            for obligation_id in missing_ids
        ],
        "approval_required": True,
        "external_publishing_enabled": False,
        "manual_distribution_required": True,
        "client_authorization_status": CLIENT_AUTHORIZATION_STATUS,
        "evidence_detection_claim": "assistive_indexing_not_guaranteed_detection",
    }


def create_approval_workflow(
    *,
    asset_id: str,
    content_type: str = "sponsor",
) -> dict[str, Any]:
    """Create a formal sponsor/branding approval workflow envelope."""

    return {
        "asset_id": asset_id,
        "content_type": content_type,
        "state": "draft",
        "allowed_states": list(APPROVAL_STATES),
        "human_review_required": True,
        "external_publishing_enabled": False,
        "manual_distribution_required": True,
        "client_authorization_status": CLIENT_AUTHORIZATION_STATUS,
        "audit_trail": [],
    }


def transition_approval_workflow(
    workflow: dict[str, Any],
    *,
    to_state: str,
    actor_role: str,
    action: str,
    comment: str = "",
    timestamp: str = DEFAULT_AUDIT_TIMESTAMP,
) -> dict[str, Any]:
    """Transition a sponsor/branding workflow while enforcing approval rules."""

    if to_state not in APPROVAL_STATES:
        raise ValueError(f"Unsupported approval state: {to_state}")
    from_state = _safe_str(workflow.get("state"), "draft")
    if to_state in {"changes_requested", "rejected"} and not _safe_str(comment):
        raise ValueError("changes_requested and rejected require a comment")
    allowed_transitions = {
        "draft": {"automated_review"},
        "automated_review": {"ops_review", "changes_requested", "rejected"},
        "ops_review": {"sponsor_review", "changes_requested", "rejected"},
        "sponsor_review": {"approved", "changes_requested", "rejected"},
        "changes_requested": {"draft"},
        "rejected": set(),
        "approved": {"ready_for_manual_distribution"},
        "ready_for_manual_distribution": set(),
    }
    if to_state not in allowed_transitions.get(from_state, set()):
        raise ValueError(f"Invalid transition from {from_state} to {to_state}")
    next_workflow = {
        **workflow,
        "external_publishing_enabled": False,
        "manual_distribution_required": True,
        "client_authorization_status": CLIENT_AUTHORIZATION_STATUS,
    }
    if (
        to_state == "approved"
        and workflow.get("content_type") == "sponsor"
        and not all(
            _has_review(workflow, state)
            for state in SPONSOR_CONTENT_REQUIRED_REVIEWS
        )
    ):
        raise ValueError("Sponsor approval requires ops_review and sponsor_review")
    if to_state == "ready_for_manual_distribution" and from_state != "approved":
        raise ValueError("Manual distribution readiness requires approved state")
    next_workflow["state"] = to_state
    next_workflow["audit_trail"] = list(workflow.get("audit_trail") or []) + [
        _audit_event(
            from_state=from_state,
            to_state=to_state,
            actor_role=actor_role,
            action=action,
            comment=comment,
            timestamp=timestamp,
        )
    ]
    return next_workflow


def build_sponsor_media_v1_snapshot() -> dict[str, Any]:
    """Expose internal v1 sponsor proof and approval capabilities."""

    return {
        "proof_of_performance_v1": {
            "status": "internal_v1",
            "package_statuses": [
                "missing_evidence",
                "ready_for_review",
                "approved_for_manual_distribution",
            ],
            "approval_required": True,
            "external_publishing_enabled": False,
            "manual_distribution_required": True,
            "client_authorization_status": CLIENT_AUTHORIZATION_STATUS,
            "evidence_detection_claim": "assistive_indexing_not_guaranteed_detection",
        },
        "approval_workflow_v1": {
            "status": "state_machine_v1",
            "allowed_states": list(APPROVAL_STATES),
            "human_review_required": True,
            "required_sponsor_reviews": list(SPONSOR_CONTENT_REQUIRED_REVIEWS),
            "external_publishing_enabled": False,
            "manual_distribution_required": True,
            "client_authorization_status": CLIENT_AUTHORIZATION_STATUS,
        },
        "direct_social_publishing": {
            "status": "client_not_authorized",
            "reason": (
                "Fundacion Telmex requires human review and "
                "manual/human-supervised publishing."
            ),
            "external_publishing_enabled": False,
            "manual_distribution_required": True,
        },
    }
