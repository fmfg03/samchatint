from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional
from uuid import uuid4


PROPOSED_ACTION_STATUS = "proposed"
APPROVAL_REQUIRED = "human_approval_required"
WRITES_DISABLED_DECISION = "writes_disabled_fail_closed"


@dataclass(frozen=True)
class ProposedAction:
    proposal_id: str
    action_type: str
    title: str
    payload: Dict[str, Any]
    approval_boundary: str
    status: str = PROPOSED_ACTION_STATUS
    source_trace_ref: Optional[str] = None
    execution_claimed: bool = False
    handler_invoked: bool = False
    external_notification_enqueued: bool = False
    side_effects_detected: int = 0

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def create_proposed_action(
    *,
    action_type: str,
    title: str,
    payload: Mapping[str, Any],
    source_trace_ref: Optional[str] = None,
) -> ProposedAction:
    return ProposedAction(
        proposal_id=f"proposal:{uuid4()}",
        action_type=(action_type or "").strip() or "operational_checklist",
        title=(title or "").strip() or "Propuesta del asistente",
        payload=dict(payload or {}),
        approval_boundary=APPROVAL_REQUIRED,
        source_trace_ref=source_trace_ref,
    )


def proposal_execution_attempt_trace(
    *,
    proposal: ProposedAction,
    writes_enabled: bool,
) -> Dict[str, Any]:
    if not writes_enabled:
        return {
            "proposal_id": proposal.proposal_id,
            "decision": "deny",
            "reason": "writes_disabled",
            "status": proposal.status,
            "handler_invoked": False,
            "side_effects_detected": 0,
            "external_notification_enqueued": False,
            "audit_language": "prepared",
        }
    return {
        "proposal_id": proposal.proposal_id,
        "decision": "pending",
        "reason": "approval_required",
        "status": proposal.status,
        "handler_invoked": False,
        "side_effects_detected": 0,
        "external_notification_enqueued": False,
        "audit_language": "proposed",
    }


def approval_shell_decision(
    *,
    proposal: ProposedAction,
    approved_by: Optional[str],
    writes_enabled: bool,
) -> Dict[str, Any]:
    approver_present = bool((approved_by or "").strip())
    if not writes_enabled:
        return {
            "proposal_id": proposal.proposal_id,
            "decision": "deny",
            "reason": WRITES_DISABLED_DECISION,
            "approver_present": approver_present,
            "handler_invoked": False,
            "side_effects_detected": 0,
            "external_notification_enqueued": False,
            "audit_language": "prepared",
            "receipt_status": "not_executed",
        }
    if not approver_present:
        return {
            "proposal_id": proposal.proposal_id,
            "decision": "deny",
            "reason": "approval_missing",
            "approver_present": False,
            "handler_invoked": False,
            "side_effects_detected": 0,
            "external_notification_enqueued": False,
            "audit_language": "proposed",
            "receipt_status": "not_executed",
        }
    return {
        "proposal_id": proposal.proposal_id,
        "decision": "pending",
        "reason": "execution_boundary_not_implemented",
        "approver_present": True,
        "handler_invoked": False,
        "side_effects_detected": 0,
        "external_notification_enqueued": False,
        "audit_language": "proposed",
        "receipt_status": "not_executed",
    }
