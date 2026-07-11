from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from .hypothesis_engine import HypothesisSet
from .situation_model import SituationModel


ANSWER = "answer"
CLARIFICATION = "clarification"
PROPOSED_ACTION = "proposed_action"
REFUSAL_OR_BLOCK = "refusal_or_block"


@dataclass(frozen=True)
class ResponseOptionScore:
    option: str
    user_value: int
    evidence_strength: int
    reversibility: int
    operational_risk: int
    policy_write_risk: int
    need_for_clarification: int
    urgency: int
    role_capability_fit: int
    total: int
    reason: str

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DecisionRubricResult:
    ranked_options: List[ResponseOptionScore]
    recommended_result: str
    reason: str

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def _evidence_strength(model: SituationModel, hypotheses: HypothesisSet) -> int:
    score = 1
    if model.evidence:
        score += 2
    if not hypotheses.missing_evidence:
        score += 1
    if not model.missing_information:
        score += 1
    return min(score, 5)


def _role_fit(model: SituationModel) -> int:
    if model.role is None:
        return 1
    if model.role in {"empleado", "user", "coordinador", "finanzas", "admin", "superadmin"}:
        return 4
    return 1


def _urgency(model: SituationModel) -> int:
    lowered = model.requested_outcome.lower()
    if any(term in lowered for term in ("hoy", "urgent", "urgente", "ahora")):
        return 4
    return 2


def _option_score(
    option: str,
    *,
    model: SituationModel,
    hypotheses: HypothesisSet,
    evidence_strength: Optional[int] = None,
) -> ResponseOptionScore:
    evidence = _evidence_strength(model, hypotheses) if evidence_strength is None else evidence_strength
    role_fit = _role_fit(model)
    urgency = _urgency(model)
    high_risk = model.risk_level == "high" or bool(hypotheses.do_not_proceed_reason)
    substantive_ambiguity = [
        flag for flag in hypotheses.ambiguity_flags if flag != "action_language_present"
    ]
    ambiguous = bool(substantive_ambiguity or model.missing_information)
    action = model.appears_action_oriented

    if option == ANSWER:
        user_value = 4 if model.appears_read_only else 2
        operational_risk = 1 if not action else 3
        policy_write_risk = 1 if not action else 4
        need_for_clarification = 4 if ambiguous else 1
        reversibility = 5
        total = (
            user_value
            + evidence
            + reversibility
            + role_fit
            + urgency
            - operational_risk
            - policy_write_risk
            - need_for_clarification
        )
        reason = "best_when_readonly_low_risk_and_supported"
    elif option == CLARIFICATION:
        user_value = 3 if ambiguous else 1
        operational_risk = 1
        policy_write_risk = 1
        need_for_clarification = 5 if ambiguous else 1
        reversibility = 5
        total = (
            user_value
            + reversibility
            + urgency
            + role_fit
            + need_for_clarification
            - operational_risk
            - policy_write_risk
        )
        if action and not ambiguous:
            total -= 4
        if high_risk:
            total -= 6
        reason = "best_when_intent_or_context_is_missing"
    elif option == PROPOSED_ACTION:
        user_value = 4 if action else 1
        operational_risk = 3 if not high_risk else 5
        policy_write_risk = 3 if action else 1
        need_for_clarification = 3 if ambiguous else 1
        reversibility = 4
        total = (
            user_value
            + evidence
            + reversibility
            + urgency
            + role_fit
            - operational_risk
            - policy_write_risk
            - need_for_clarification
        )
        if high_risk:
            total -= 6
        reason = "best_when_action_intent_exists_but_only_inert_proposal_is_allowed"
    else:
        user_value = 2 if high_risk else 1
        operational_risk = 1
        policy_write_risk = 1
        need_for_clarification = 2 if ambiguous else 1
        reversibility = 5
        total = (
            user_value
            + reversibility
            + urgency
            + (5 if high_risk else 1)
            - operational_risk
            - policy_write_risk
            - need_for_clarification
        )
        if high_risk:
            total += 10
        reason = "best_when_request_is_unsafe_or_execution_is_unsupported"

    return ResponseOptionScore(
        option=option,
        user_value=user_value,
        evidence_strength=evidence,
        reversibility=reversibility,
        operational_risk=operational_risk,
        policy_write_risk=policy_write_risk,
        need_for_clarification=need_for_clarification,
        urgency=urgency,
        role_capability_fit=role_fit,
        total=total,
        reason=reason,
    )


def evaluate_response_options(
    *,
    model: SituationModel,
    hypotheses: HypothesisSet,
) -> DecisionRubricResult:
    options = [
        _option_score(ANSWER, model=model, hypotheses=hypotheses),
        _option_score(CLARIFICATION, model=model, hypotheses=hypotheses),
        _option_score(PROPOSED_ACTION, model=model, hypotheses=hypotheses),
        _option_score(REFUSAL_OR_BLOCK, model=model, hypotheses=hypotheses),
    ]
    ranked = sorted(
        options,
        key=lambda item: (
            item.total,
            item.evidence_strength,
            item.reversibility,
            -item.operational_risk,
            item.option,
        ),
        reverse=True,
    )
    best = ranked[0]
    return DecisionRubricResult(
        ranked_options=ranked,
        recommended_result=best.option,
        reason=best.reason,
    )
