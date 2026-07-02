from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .decision_rubric import evaluate_response_options
from .hypothesis_engine import generate_hypotheses
from .response_strategy import ResponseStrategy, select_response_strategy
from .self_critique import critique_assistant_draft
from .situation_model import SituationModel, build_situation_model


@dataclass(frozen=True)
class CognitiveRuntimeInput:
    user_message: str
    role: Optional[str] = None
    employee_id: Optional[Any] = None
    constraints: List[str] = field(default_factory=list)
    tool_traces: List[Mapping[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class CognitiveDecisionEnvelope:
    situation_model_summary: Dict[str, Any]
    top_hypotheses: List[Dict[str, Any]]
    selected_option: str
    final_response_mode: str
    evidence_summary: List[str]
    missing_information: List[str]
    risk_level: str
    allowed_capabilities: List[str]
    denied_capabilities: List[str]
    proposal_boundary: Optional[Dict[str, Any]]
    self_critique_status: Dict[str, Any]
    reasoning_summary: List[str]

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def _situation_summary(model: SituationModel) -> Dict[str, Any]:
    return {
        "intent_category": model.intent_category,
        "requested_outcome": model.requested_outcome,
        "actor_employee_id_present": model.actor_employee_id is not None,
        "role": model.role,
        "risk_level": model.risk_level,
        "appears_read_only": model.appears_read_only,
        "appears_action_oriented": model.appears_action_oriented,
        "may_require_approval": model.may_require_approval,
        "may_require_future_write_execution": model.may_require_future_write_execution,
        "recommended_next_cognitive_step": model.recommended_next_cognitive_step,
    }


def _hypothesis_summaries(hypotheses: Iterable[Any], *, limit: int = 3) -> List[Dict[str, Any]]:
    summaries = []
    for hypothesis in list(hypotheses)[:limit]:
        summaries.append(
            {
                "label": hypothesis.label,
                "confidence": hypothesis.confidence,
                "missing_evidence_count": len(hypothesis.missing_evidence),
                "execution_allowed": hypothesis.execution_allowed,
            }
        )
    return summaries


def _draft_for_critique(strategy: ResponseStrategy) -> str:
    if strategy.strategy == "ANSWER_NOW":
        return "I can answer from the available read-only evidence."
    if strategy.strategy == "ASK_CLARIFYING_QUESTION":
        return strategy.reason
    if strategy.strategy == "CREATE_INERT_PROPOSAL":
        return "I can prepare an inert proposal for human review; it was not executed."
    if strategy.strategy == "REFUSE_OR_BLOCK":
        return "I cannot proceed with that request under the current safety boundary."
    return "This needs human review before the assistant continues."


def _reasoning_summary(
    *,
    model: SituationModel,
    selected_option: str,
    strategy: ResponseStrategy,
    critique_passed: bool,
) -> List[str]:
    return [
        f"intent:{model.intent_category}",
        f"risk:{model.risk_level}",
        f"selected_option:{selected_option}",
        f"response_mode:{strategy.strategy}",
        f"critique:{'passed' if critique_passed else 'needs_attention'}",
    ]


def run_cognitive_pipeline(
    request: CognitiveRuntimeInput,
) -> CognitiveDecisionEnvelope:
    model = build_situation_model(
        user_message=request.user_message,
        role=request.role,
        employee_id=request.employee_id,
        constraints=request.constraints,
        tool_traces=request.tool_traces,
    )
    hypotheses = generate_hypotheses(model)
    rubric = evaluate_response_options(model=model, hypotheses=hypotheses)
    strategy = select_response_strategy(
        model=model,
        hypotheses=hypotheses,
        rubric=rubric,
    )
    critique = critique_assistant_draft(
        draft=_draft_for_critique(strategy),
        evidence=model.evidence,
        proposal_boundary=strategy.proposal_boundary,
        missing_evidence=[],
    )

    return CognitiveDecisionEnvelope(
        situation_model_summary=_situation_summary(model),
        top_hypotheses=_hypothesis_summaries(hypotheses.hypotheses),
        selected_option=rubric.recommended_result,
        final_response_mode=strategy.strategy,
        evidence_summary=strategy.evidence_summary,
        missing_information=strategy.missing_info,
        risk_level=model.risk_level,
        allowed_capabilities=strategy.allowed_capabilities,
        denied_capabilities=strategy.denied_capabilities,
        proposal_boundary=strategy.proposal_boundary,
        self_critique_status={
            "passed": critique.passed,
            "decision": critique.final_release_decision,
            "issue_count": len(critique.issues),
        },
        reasoning_summary=_reasoning_summary(
            model=model,
            selected_option=rubric.recommended_result,
            strategy=strategy,
            critique_passed=critique.passed,
        ),
    )
