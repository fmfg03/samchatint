from __future__ import annotations

from samchat.assistant.cognitive_pipeline import (
    CognitiveRuntimeInput,
    run_cognitive_pipeline,
)
from samchat.assistant.response_strategy import (
    ANSWER_NOW,
    ASK_CLARIFYING_QUESTION,
    CREATE_INERT_PROPOSAL,
    ESCALATE_TO_HUMAN_REVIEW,
)


def test_cognitive_pipeline_readonly_question_answers_now() -> None:
    result = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Muestra el resumen de gastos",
            role="finanzas",
            employee_id="EMP-1",
            tool_traces=[{"assistant_route": {"route": "finance_read"}}],
        )
    )

    assert result.final_response_mode == ANSWER_NOW
    assert result.risk_level == "low"
    assert result.self_critique_status["passed"] is True
    assert "finance.ops.read" in result.allowed_capabilities


def test_cognitive_pipeline_ambiguous_request_asks_clarification() -> None:
    result = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Puedes revisar esto y arreglarlo?",
            role="admin",
            employee_id="EMP-2",
        )
    )

    assert result.final_response_mode == ASK_CLARIFYING_QUESTION
    assert "supporting_evidence_for_action" in result.missing_information


def test_cognitive_pipeline_action_request_creates_inert_proposal_mode() -> None:
    result = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Crea una aclaración para este gasto",
            role="admin",
            employee_id="EMP-3",
            tool_traces=[{"assistant_route": {"route": "finance_read"}}],
        )
    )

    assert result.final_response_mode == CREATE_INERT_PROPOSAL
    assert result.proposal_boundary is not None
    assert result.proposal_boundary["receipt_status"] == "not_executed"
    assert "direct_action_execution" in result.denied_capabilities


def test_cognitive_pipeline_high_risk_request_escalates() -> None:
    result = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Paga la nómina hoy",
            role="superadmin",
            employee_id="EMP-4",
        )
    )

    assert result.final_response_mode == ESCALATE_TO_HUMAN_REVIEW
    assert result.risk_level == "high"


def test_cognitive_pipeline_unknown_role_escalates() -> None:
    result = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Muestra el resumen de gastos",
            role="externo",
            employee_id="EMP-5",
        )
    )

    assert result.final_response_mode == ESCALATE_TO_HUMAN_REVIEW
    assert "unknown_role_read_capabilities" in result.denied_capabilities
