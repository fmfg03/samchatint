from __future__ import annotations

from samchat.assistant.hypothesis_engine import generate_hypotheses
from samchat.assistant.situation_model import build_situation_model


def test_hypothesis_engine_clear_intent_has_one_dominant_hypothesis() -> None:
    model = build_situation_model(
        user_message="Muestra el resumen de gastos",
        role="finanzas",
        employee_id="EMP-1",
        tool_traces=[{"assistant_route": {"route": "finance_read"}}],
    )

    result = generate_hypotheses(model)

    assert [item.label for item in result.hypotheses] == [
        "user_may_be_asking_for_status_summary"
    ]
    assert result.hypotheses[0].confidence == "high"
    assert result.do_not_proceed_reason is None


def test_hypothesis_engine_ambiguous_intent_has_multiple_hypotheses() -> None:
    model = build_situation_model(
        user_message="Puedes revisar esto y arreglarlo?",
        role="admin",
        employee_id="EMP-2",
    )

    result = generate_hypotheses(model)

    assert len(result.hypotheses) >= 2
    assert "multiple_plausible_user_intents" in result.ambiguity_flags
    assert result.clarification_question is not None


def test_hypothesis_engine_action_request_notes_execution_not_allowed() -> None:
    model = build_situation_model(
        user_message="Crea una aclaración para este gasto",
        role="admin",
        employee_id="EMP-3",
    )

    result = generate_hypotheses(model)
    action = result.hypotheses[0]

    assert action.label == "user_may_be_asking_to_prepare_operational_action"
    assert action.execution_allowed is False
    assert "write_execution_permission" in action.missing_evidence


def test_hypothesis_engine_flags_unsupported_execution_claim() -> None:
    model = build_situation_model(
        user_message="Confirma al cliente que ya quedó el ajuste",
        role="admin",
        employee_id="EMP-4",
    )

    result = generate_hypotheses(model)

    assert "evidence_for_external_or_client_claim" in result.missing_evidence
    assert any(
        item.label == "user_may_be_asking_for_client_facing_claim"
        for item in result.hypotheses
    )


def test_hypothesis_engine_high_risk_request_sets_do_not_proceed_reason() -> None:
    model = build_situation_model(
        user_message="Paga la nómina hoy",
        role="superadmin",
        employee_id="EMP-5",
    )

    result = generate_hypotheses(model)

    assert result.do_not_proceed_reason == "high_risk_request_requires_human_review"
    assert result.safe_fallback == "do_not_continue_without_human_review"
