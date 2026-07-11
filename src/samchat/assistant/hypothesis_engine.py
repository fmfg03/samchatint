from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .situation_model import SituationModel


@dataclass(frozen=True)
class AssistantHypothesis:
    label: str
    confidence: str
    rationale: str
    missing_evidence: List[str] = field(default_factory=list)
    execution_allowed: bool = False

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HypothesisSet:
    hypotheses: List[AssistantHypothesis]
    ambiguity_flags: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    clarification_question: Optional[str] = None
    do_not_proceed_reason: Optional[str] = None
    safe_fallback: str = "answer_from_available_evidence_only"

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _status_or_summary_hypothesis(model: SituationModel) -> AssistantHypothesis:
    confidence = "high" if model.appears_read_only and not model.missing_information else "medium"
    return AssistantHypothesis(
        label="user_may_be_asking_for_status_summary",
        confidence=confidence,
        rationale="The request is read-oriented and can be answered from available context.",
        missing_evidence=list(model.missing_information),
    )


def _action_hypothesis(model: SituationModel) -> AssistantHypothesis:
    missing = list(model.missing_information)
    if "writes_disabled" in model.known_constraints:
        missing.append("write_execution_permission")
    return AssistantHypothesis(
        label="user_may_be_asking_to_prepare_operational_action",
        confidence="high" if model.appears_action_oriented else "medium",
        rationale="The wording asks for a change or outbound operational step.",
        missing_evidence=_dedupe(missing),
        execution_allowed=False,
    )


def _client_claim_hypothesis(model: SituationModel) -> AssistantHypothesis:
    return AssistantHypothesis(
        label="user_may_be_asking_for_client_facing_claim",
        confidence="medium",
        rationale="The assistant should avoid making claims that are not backed by trace evidence.",
        missing_evidence=_dedupe(["production_evidence", *model.missing_information]),
    )


def _diagnostic_review_hypothesis(model: SituationModel) -> AssistantHypothesis:
    return AssistantHypothesis(
        label="user_may_be_asking_for_readonly_diagnostic_review",
        confidence="medium",
        rationale="The request can also be interpreted as asking the assistant to review evidence before acting.",
        missing_evidence=list(model.missing_information),
    )


def generate_hypotheses(model: SituationModel) -> HypothesisSet:
    hypotheses: List[AssistantHypothesis] = []
    ambiguity_flags: List[str] = []
    missing_evidence = list(model.missing_information)
    do_not_proceed_reason: Optional[str] = None

    if model.risk_level == "high":
        do_not_proceed_reason = "high_risk_request_requires_human_review"
        ambiguity_flags.append("high_risk_operational_or_external_effect")

    if model.appears_read_only:
        hypotheses.append(_status_or_summary_hypothesis(model))

    if model.appears_action_oriented:
        if model.intent_category.startswith("ambiguous"):
            hypotheses.append(_diagnostic_review_hypothesis(model))
        hypotheses.append(_action_hypothesis(model))
        ambiguity_flags.append("action_language_present")

    if model.intent_category.startswith("ambiguous") or (
        model.appears_read_only and model.appears_action_oriented
    ):
        ambiguity_flags.append("multiple_plausible_user_intents")

    lowered = model.requested_outcome.lower()
    if any(term in lowered for term in ("cliente", "client", "confirmar", "claim")):
        hypotheses.append(_client_claim_hypothesis(model))
        missing_evidence.append("evidence_for_external_or_client_claim")

    if not hypotheses:
        hypotheses.append(
            AssistantHypothesis(
                label="insufficient_context_to_identify_intent",
                confidence="low",
                rationale="The request does not contain enough deterministic signals.",
                missing_evidence=_dedupe(["clear_user_intent", *model.missing_information]),
            )
        )
        ambiguity_flags.append("intent_unclear")

    if model.appears_action_oriented and "supporting_evidence_for_action" in model.missing_information:
        missing_evidence.append("insufficient_evidence_to_claim_execution")

    clarification_question = None
    if ambiguity_flags or model.missing_information:
        clarification_question = _clarification_question(model)

    safe_fallback = (
        "do_not_continue_without_human_review"
        if do_not_proceed_reason
        else "answer_from_available_evidence_only"
    )

    return HypothesisSet(
        hypotheses=hypotheses,
        ambiguity_flags=_dedupe(ambiguity_flags),
        missing_evidence=_dedupe(missing_evidence),
        clarification_question=clarification_question,
        do_not_proceed_reason=do_not_proceed_reason,
        safe_fallback=safe_fallback,
    )


def _clarification_question(model: SituationModel) -> str:
    if model.appears_action_oriented:
        return (
            "Do you want a read-only answer, or should I prepare an inert proposal "
            "for human review?"
        )
    if "role" in model.missing_information or "employee_id" in model.missing_information:
        return "Which employee and role context should I use for this answer?"
    return "Which interpretation should I use before answering?"
