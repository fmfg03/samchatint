from __future__ import annotations

from samchat.assistant.situation_model import build_situation_model


def test_situation_model_for_clear_readonly_question() -> None:
    model = build_situation_model(
        user_message="Muestra el resumen de gastos de EMP-1",
        role="finanzas",
        employee_id="EMP-1",
        tool_traces=[{"assistant_route": {"route": "finance_read", "decision": "read"}}],
    )

    assert model.intent_category == "read_only_question"
    assert model.appears_read_only is True
    assert model.appears_action_oriented is False
    assert model.risk_level == "low"
    assert model.role == "finanzas"
    assert model.actor_employee_id == "EMP-1"
    assert model.evidence[0]["kind"] == "assistant_route"


def test_situation_model_for_ambiguous_request() -> None:
    model = build_situation_model(
        user_message="Puedes revisar esto y arreglarlo?",
        role="admin",
        employee_id="EMP-2",
    )

    assert model.intent_category == "ambiguous_operational_request"
    assert model.appears_action_oriented is True
    assert model.may_require_approval is True
    assert "supporting_evidence_for_action" in model.missing_information
    assert model.recommended_next_cognitive_step == "generate_hypotheses_and_clarify"


def test_situation_model_action_request_becomes_future_proposal_not_execution() -> None:
    model = build_situation_model(
        user_message="Actualiza el gasto sin CFDI",
        role="admin",
        employee_id="EMP-3",
    )

    assert model.intent_category == "operational_action_request"
    assert model.appears_action_oriented is True
    assert model.may_require_future_write_execution is True
    assert model.may_require_approval is True
    assert "writes_disabled" in model.known_constraints


def test_situation_model_marks_missing_role_and_employee_context() -> None:
    model = build_situation_model(user_message="Dime el estado de mis solicitudes")

    assert model.role is None
    assert model.actor_employee_id is None
    assert "role" in model.missing_information
    assert "employee_id" in model.missing_information
    assert model.risk_level == "medium"


def test_situation_model_marks_high_risk_request() -> None:
    model = build_situation_model(
        user_message="Paga la nómina y manda WhatsApp al equipo",
        role="super_admin",
        employee_id="EMP-4",
    )

    assert model.role == "superadmin"
    assert model.risk_level == "high"
    assert model.appears_action_oriented is True
    assert model.may_require_approval is True
    assert model.recommended_next_cognitive_step == "generate_hypotheses_with_high_risk_guard"
