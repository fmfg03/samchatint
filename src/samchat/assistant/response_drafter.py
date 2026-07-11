from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .action_planner import PlannedInertAction, plan_inert_action
from .cognitive_pipeline import CognitiveDecisionEnvelope
from .response_strategy import (
    ANSWER_NOW,
    ASK_CLARIFYING_QUESTION,
    CREATE_INERT_PROPOSAL,
    ESCALATE_TO_HUMAN_REVIEW,
    REFUSE_OR_BLOCK,
)
from .self_critique import BLOCKED, NEEDS_REVISION, critique_assistant_draft


@dataclass(frozen=True)
class DraftedAssistantResponse:
    final_text: Optional[str]
    draft_text: str
    response_mode: str
    release_decision: str
    critique_passed: bool
    required_edits: List[str] = field(default_factory=list)
    proposal: Optional[Dict[str, Any]] = None

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def _answer_text(envelope: CognitiveDecisionEnvelope) -> str:
    if envelope.missing_information or envelope.evidence_summary == ["no_tool_trace_evidence"]:
        missing = envelope.missing_information or ["supporting_evidence"]
        return (
            "From the available read-only evidence, I can give a partial answer. "
            f"Missing context: {', '.join(missing)}."
        )
    return "From the available read-only evidence, this can be answered now."


def _clarification_text(envelope: CognitiveDecisionEnvelope) -> str:
    if envelope.missing_information:
        return f"Please clarify: {', '.join(envelope.missing_information)}."
    return "Please confirm whether you want a read-only answer or an inert proposal."


def _proposal_text(plan: PlannedInertAction) -> str:
    return (
        "I prepared an inert proposal for human review. "
        f"Status: not executed. Next step: {plan.next_human_step}."
    )


def _block_text(envelope: CognitiveDecisionEnvelope) -> str:
    return (
        "I cannot proceed with that request under the current safety boundary. "
        f"Risk level: {envelope.risk_level}."
    )


def draft_response_from_cognitive_envelope(
    *,
    envelope: CognitiveDecisionEnvelope,
    unsafe_draft_override: Optional[str] = None,
) -> DraftedAssistantResponse:
    proposal: Optional[PlannedInertAction] = None
    if unsafe_draft_override is not None:
        draft = unsafe_draft_override
    elif envelope.final_response_mode == ANSWER_NOW:
        draft = _answer_text(envelope)
    elif envelope.final_response_mode == ASK_CLARIFYING_QUESTION:
        draft = _clarification_text(envelope)
    elif envelope.final_response_mode == CREATE_INERT_PROPOSAL:
        proposal = plan_inert_action(envelope=envelope)
        draft = _proposal_text(proposal)
    elif envelope.final_response_mode == REFUSE_OR_BLOCK:
        draft = _block_text(envelope)
    elif envelope.final_response_mode == ESCALATE_TO_HUMAN_REVIEW:
        draft = "This needs human review before the assistant continues."
    else:
        draft = "This request needs review before the assistant answers."

    critique = critique_assistant_draft(
        draft=draft,
        evidence=[],
        proposal_boundary=envelope.proposal_boundary,
        missing_evidence=[],
    )
    final_text = None if critique.final_release_decision in {NEEDS_REVISION, BLOCKED} else draft

    return DraftedAssistantResponse(
        final_text=final_text,
        draft_text=draft,
        response_mode=envelope.final_response_mode,
        release_decision=critique.final_release_decision,
        critique_passed=critique.passed,
        required_edits=list(critique.required_edits),
        proposal=proposal.to_trace() if proposal is not None else None,
    )
