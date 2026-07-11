from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .decision_rubric import (
    ANSWER,
    CLARIFICATION,
    PROPOSED_ACTION,
    REFUSAL_OR_BLOCK,
    DecisionRubricResult,
)
from .hypothesis_engine import HypothesisSet
from .policy import READONLY_ROLE_CAPABILITIES, readonly_capabilities_for_role
from .proposed_actions import APPROVAL_REQUIRED, PROPOSED_ACTION_STATUS
from .situation_model import SituationModel


ANSWER_NOW = "ANSWER_NOW"
ASK_CLARIFYING_QUESTION = "ASK_CLARIFYING_QUESTION"
CREATE_INERT_PROPOSAL = "CREATE_INERT_PROPOSAL"
REFUSE_OR_BLOCK = "REFUSE_OR_BLOCK"
ESCALATE_TO_HUMAN_REVIEW = "ESCALATE_TO_HUMAN_REVIEW"


@dataclass(frozen=True)
class ResponseStrategy:
    strategy: str
    reason: str
    evidence_summary: List[str] = field(default_factory=list)
    missing_info: List[str] = field(default_factory=list)
    allowed_capabilities: List[str] = field(default_factory=list)
    denied_capabilities: List[str] = field(default_factory=list)
    proposal_boundary: Optional[Dict[str, Any]] = None
    wording_constraints: List[str] = field(default_factory=list)

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def _evidence_summary(model: SituationModel) -> List[str]:
    if not model.evidence:
        return ["no_tool_trace_evidence"]
    return [
        f"{item.get('kind', 'tool_trace')}:{item.get('index')}"
        for item in model.evidence
    ]


def _denied_capabilities(model: SituationModel) -> List[str]:
    denied = ["write_execution", "external_notifications", "provider_live_calls"]
    if model.role not in READONLY_ROLE_CAPABILITIES:
        denied.append("unknown_role_read_capabilities")
    if model.appears_action_oriented:
        denied.append("direct_action_execution")
    return denied


def _wording_constraints(model: SituationModel) -> List[str]:
    constraints = [
        "do_not_claim_action_completed",
        "do_not_claim_write_execution",
        "state_evidence_limits",
    ]
    if model.appears_action_oriented:
        constraints.append("describe_only_inert_proposal_or_next_review_step")
    if model.risk_level == "high":
        constraints.append("avoid_operational_instructions_for_high_risk_action")
    return constraints


def select_response_strategy(
    *,
    model: SituationModel,
    hypotheses: HypothesisSet,
    rubric: DecisionRubricResult,
) -> ResponseStrategy:
    allowed = sorted(readonly_capabilities_for_role(model.role))
    denied = _denied_capabilities(model)
    missing = sorted(set(model.missing_information + hypotheses.missing_evidence))
    evidence = _evidence_summary(model)
    wording = _wording_constraints(model)

    if model.role not in READONLY_ROLE_CAPABILITIES:
        return ResponseStrategy(
            strategy=ESCALATE_TO_HUMAN_REVIEW,
            reason="unknown_role_requires_human_review",
            evidence_summary=evidence,
            missing_info=missing,
            allowed_capabilities=allowed,
            denied_capabilities=denied,
            wording_constraints=wording,
        )

    if hypotheses.do_not_proceed_reason and model.risk_level == "high":
        return ResponseStrategy(
            strategy=ESCALATE_TO_HUMAN_REVIEW,
            reason=hypotheses.do_not_proceed_reason,
            evidence_summary=evidence,
            missing_info=missing,
            allowed_capabilities=allowed,
            denied_capabilities=denied,
            wording_constraints=wording,
        )

    if rubric.recommended_result == ANSWER:
        return ResponseStrategy(
            strategy=ANSWER_NOW,
            reason=rubric.reason,
            evidence_summary=evidence,
            missing_info=missing,
            allowed_capabilities=allowed,
            denied_capabilities=denied,
            wording_constraints=wording,
        )

    if rubric.recommended_result == CLARIFICATION:
        return ResponseStrategy(
            strategy=ASK_CLARIFYING_QUESTION,
            reason=hypotheses.clarification_question or rubric.reason,
            evidence_summary=evidence,
            missing_info=missing,
            allowed_capabilities=allowed,
            denied_capabilities=denied,
            wording_constraints=wording,
        )

    if rubric.recommended_result == PROPOSED_ACTION:
        return ResponseStrategy(
            strategy=CREATE_INERT_PROPOSAL,
            reason=rubric.reason,
            evidence_summary=evidence,
            missing_info=missing,
            allowed_capabilities=allowed,
            denied_capabilities=denied,
            proposal_boundary={
                "status": PROPOSED_ACTION_STATUS,
                "approval_boundary": APPROVAL_REQUIRED,
                "handler_invoked": False,
                "side_effects_detected": 0,
                "receipt_status": "not_executed",
            },
            wording_constraints=wording,
        )

    if rubric.recommended_result == REFUSAL_OR_BLOCK:
        return ResponseStrategy(
            strategy=REFUSE_OR_BLOCK,
            reason=rubric.reason,
            evidence_summary=evidence,
            missing_info=missing,
            allowed_capabilities=allowed,
            denied_capabilities=denied,
            wording_constraints=wording,
        )

    return ResponseStrategy(
        strategy=ESCALATE_TO_HUMAN_REVIEW,
        reason="no_safe_strategy_selected",
        evidence_summary=evidence,
        missing_info=missing,
        allowed_capabilities=allowed,
        denied_capabilities=denied,
        wording_constraints=wording,
    )
