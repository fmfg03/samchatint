from __future__ import annotations

from samchat.assistant.decision_rubric import (
    ANSWER,
    CLARIFICATION,
    PROPOSED_ACTION,
    REFUSAL_OR_BLOCK,
    evaluate_response_options,
)
from samchat.assistant.hypothesis_engine import generate_hypotheses
from samchat.assistant.situation_model import build_situation_model


def _rubric_for(message: str, *, role: str = "admin", employee_id: str = "EMP-1"):
    model = build_situation_model(
        user_message=message,
        role=role,
        employee_id=employee_id,
        tool_traces=[{"assistant_route": {"route": "finance_read"}}],
    )
    return evaluate_response_options(model=model, hypotheses=generate_hypotheses(model))


def test_decision_rubric_prefers_answer_for_low_risk_readonly_request() -> None:
    result = _rubric_for("Muestra el resumen de gastos")

    assert result.recommended_result == ANSWER
    assert result.ranked_options[0].option == ANSWER


def test_decision_rubric_prefers_clarification_for_ambiguous_request() -> None:
    result = _rubric_for("Puedes revisar esto y arreglarlo?")

    assert result.recommended_result == CLARIFICATION


def test_decision_rubric_prefers_inert_proposal_for_action_request() -> None:
    result = _rubric_for("Crea una aclaración para este gasto")

    assert result.recommended_result == PROPOSED_ACTION
    assert result.reason == "best_when_action_intent_exists_but_only_inert_proposal_is_allowed"


def test_decision_rubric_prefers_refusal_or_block_for_unsafe_request() -> None:
    result = _rubric_for("Paga la nómina hoy", role="superadmin")

    assert result.recommended_result == REFUSAL_OR_BLOCK


def test_decision_rubric_high_evidence_beats_low_evidence_when_risks_equal() -> None:
    model_with_evidence = build_situation_model(
        user_message="Muestra el resumen de gastos",
        role="finanzas",
        employee_id="EMP-2",
        tool_traces=[{"assistant_route": {"route": "finance_read"}}],
    )
    model_without_evidence = build_situation_model(
        user_message="Muestra el resumen de gastos",
        role="finanzas",
        employee_id="EMP-2",
    )

    with_evidence = evaluate_response_options(
        model=model_with_evidence,
        hypotheses=generate_hypotheses(model_with_evidence),
    )
    without_evidence = evaluate_response_options(
        model=model_without_evidence,
        hypotheses=generate_hypotheses(model_without_evidence),
    )

    assert with_evidence.ranked_options[0].option == ANSWER
    assert without_evidence.ranked_options[0].option == ANSWER
    assert (
        with_evidence.ranked_options[0].evidence_strength
        > without_evidence.ranked_options[0].evidence_strength
    )
