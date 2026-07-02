from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .cognitive_pipeline import CognitiveDecisionEnvelope
from .proposed_actions import APPROVAL_REQUIRED
from .response_strategy import ASK_CLARIFYING_QUESTION, CREATE_INERT_PROPOSAL


@dataclass(frozen=True)
class PlannedInertAction:
    decision: str
    reason: str
    action_type: str
    title: str
    payload: Dict[str, Any]
    evidence_summary: List[str] = field(default_factory=list)
    missing_approvals: List[str] = field(default_factory=list)
    blocked_capabilities: List[str] = field(default_factory=list)
    next_human_step: str = "review_and_decide"
    approval_boundary: str = APPROVAL_REQUIRED
    execution_status: str = "not_executed"
    writes_required: bool = False
    writes_enabled: bool = False
    handler_invoked: bool = False
    external_notification_enqueued: bool = False
    side_effects_detected: int = 0

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def plan_inert_action(
    *,
    envelope: CognitiveDecisionEnvelope,
) -> PlannedInertAction:
    if envelope.final_response_mode != CREATE_INERT_PROPOSAL:
        return PlannedInertAction(
            decision=ASK_CLARIFYING_QUESTION,
            reason="strategy_not_proposal",
            action_type="clarification",
            title="Clarify before planning",
            payload={"missing_information": list(envelope.missing_information)},
            evidence_summary=list(envelope.evidence_summary),
            blocked_capabilities=list(envelope.denied_capabilities),
            next_human_step="provide_missing_context_or_choose_readonly_answer",
        )

    missing_evidence = [
        item for item in envelope.missing_information if "evidence" in item
    ]
    if missing_evidence and not envelope.evidence_summary:
        return PlannedInertAction(
            decision=ASK_CLARIFYING_QUESTION,
            reason="missing_evidence_blocks_proposal",
            action_type="clarification",
            title="Need evidence before proposal",
            payload={"missing_information": missing_evidence},
            evidence_summary=[],
            blocked_capabilities=list(envelope.denied_capabilities),
            next_human_step="attach_or_select_supporting_evidence",
        )

    return PlannedInertAction(
        decision=CREATE_INERT_PROPOSAL,
        reason="cognitive_strategy_selected_inert_proposal",
        action_type="operator_follow_up",
        title="Prepare operator follow-up for review",
        payload={
            "intent_category": envelope.situation_model_summary.get("intent_category"),
            "risk_level": envelope.risk_level,
            "requested_outcome": envelope.situation_model_summary.get("requested_outcome"),
        },
        evidence_summary=list(envelope.evidence_summary),
        missing_approvals=["human_approval_required"],
        blocked_capabilities=list(envelope.denied_capabilities),
        next_human_step="review_proposal_and_approve_or_reject_outside_runtime",
        writes_required=True,
        writes_enabled=False,
    )
