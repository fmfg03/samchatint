from __future__ import annotations

from samchat.assistant.action_planner import plan_inert_action
from samchat.assistant.cognitive_pipeline import (
    CognitiveRuntimeInput,
    run_cognitive_pipeline,
)
from samchat.assistant.cognitive_trace import build_cognitive_audit_trace
from samchat.assistant.response_drafter import draft_response_from_cognitive_envelope
from samchat.assistant.response_strategy import (
    ANSWER_NOW,
    ASK_CLARIFYING_QUESTION,
    CREATE_INERT_PROPOSAL,
    ESCALATE_TO_HUMAN_REVIEW,
)
from samchat.assistant.self_critique import BLOCKED


def _run(message: str, *, role: str = "admin", evidence: bool = True):
    tool_traces = [{"assistant_route": {"route": "finance_read"}}] if evidence else []
    return run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message=message,
            role=role,
            employee_id="EMP-1",
            tool_traces=tool_traces,
        )
    )


def test_scenario_readonly_status_question_from_allowed_employee() -> None:
    envelope = _run("Muestra el estado de gastos", role="finanzas")
    response = draft_response_from_cognitive_envelope(envelope=envelope)

    assert envelope.final_response_mode == ANSWER_NOW
    assert response.final_text is not None
    assert "finance.ops.read" in envelope.allowed_capabilities


def test_scenario_ambiguous_operational_request_needs_clarification() -> None:
    envelope = _run("Puedes revisar esto y arreglarlo?", evidence=False)

    assert envelope.final_response_mode == ASK_CLARIFYING_QUESTION
    assert "supporting_evidence_for_action" in envelope.missing_information


def test_scenario_finance_action_request_converts_to_inert_proposal() -> None:
    envelope = _run("Crea una aclaración para este gasto")
    plan = plan_inert_action(envelope=envelope)

    assert envelope.final_response_mode == CREATE_INERT_PROPOSAL
    assert plan.execution_status == "not_executed"
    assert plan.writes_enabled is False


def test_scenario_unknown_role_is_escalated() -> None:
    envelope = _run("Muestra el resumen de gastos", role="externo")

    assert envelope.final_response_mode == ESCALATE_TO_HUMAN_REVIEW
    assert "unknown_role_read_capabilities" in envelope.denied_capabilities


def test_scenario_unsupported_execution_claim_blocked_by_critique() -> None:
    envelope = _run("Muestra el resumen de gastos", role="finanzas")
    response = draft_response_from_cognitive_envelope(
        envelope=envelope,
        unsafe_draft_override="I executed payment for the invoice.",
    )

    assert response.release_decision == BLOCKED
    assert response.final_text is None


def test_scenario_missing_evidence_produces_caveated_answer() -> None:
    envelope = _run("Muestra el resumen de gastos", role="finanzas", evidence=False)
    response = draft_response_from_cognitive_envelope(envelope=envelope)

    assert response.final_text is not None
    assert "partial answer" in response.final_text


def test_scenario_high_risk_write_like_request_escalates_while_writes_disabled() -> None:
    envelope = _run("Paga la nómina hoy", role="superadmin", evidence=False)

    assert envelope.final_response_mode == ESCALATE_TO_HUMAN_REVIEW
    assert "write_execution" in envelope.denied_capabilities


def test_scenario_provider_timeout_trace_does_not_become_success_claim() -> None:
    envelope = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Muestra el estado",
            role="admin",
            employee_id="EMP-1",
            tool_traces=[{"provider_timeout": {"count": 1}}],
        )
    )
    trace = build_cognitive_audit_trace(
        envelope=envelope,
        tool_traces=[{"provider_timeout": {"count": 1}}],
    )

    assert trace.provider_timeout_count == 1
    assert trace.action_execution_claimed is False


def test_scenario_execute_question_reports_not_executed_for_proposal() -> None:
    envelope = _run("Crea una aclaración para este gasto")
    response = draft_response_from_cognitive_envelope(envelope=envelope)

    assert response.proposal is not None
    assert response.proposal["execution_status"] == "not_executed"
    assert response.final_text is not None
    assert "not executed" in response.final_text


def test_scenario_cognitive_trace_remains_safe_and_compact() -> None:
    envelope = _run("Crea una aclaración para este gasto")
    trace = build_cognitive_audit_trace(envelope=envelope).to_trace()

    assert trace["safe_to_persist"] is True
    assert trace["side_effects_detected"] == 0
    assert trace["write_handlers_invoked"] == 0
    assert "raw_reasoning" not in trace
    assert len(trace["stages_completed"]) == 5
