from __future__ import annotations

from samchat.assistant.cognitive_pipeline import (
    CognitiveRuntimeInput,
    run_cognitive_pipeline,
)
from samchat.assistant.cognitive_trace import build_cognitive_audit_trace


def test_cognitive_trace_serializes_safe_counts_and_labels() -> None:
    envelope = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Muestra el resumen de gastos",
            role="finanzas",
            employee_id="EMP-1",
            tool_traces=[{"assistant_route": {"route": "finance_read"}}],
        )
    )

    trace = build_cognitive_audit_trace(envelope=envelope).to_trace()

    assert trace["stages_completed"] == [
        "situation_model",
        "hypothesis_engine",
        "decision_rubric",
        "response_strategy",
        "self_critique",
    ]
    assert trace["write_status"] == "disabled"
    assert trace["side_effects_detected"] == 0
    assert trace["write_handlers_invoked"] == 0
    assert trace["safe_to_persist"] is True


def test_cognitive_trace_counts_provider_timeouts_without_success_claim() -> None:
    envelope = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Muestra el estado",
            role="admin",
            employee_id="EMP-2",
            tool_traces=[{"provider_timeout": {"count": 1}}],
        )
    )

    trace = build_cognitive_audit_trace(
        envelope=envelope,
        tool_traces=[{"provider_timeout": {"count": 1}}],
    )

    assert trace.provider_timeout_count == 1
    assert trace.action_execution_claimed is False


def test_cognitive_trace_does_not_include_raw_reasoning_fields() -> None:
    envelope = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Puedes revisar esto y arreglarlo?",
            role="admin",
            employee_id="EMP-3",
        )
    )

    trace = build_cognitive_audit_trace(envelope=envelope).to_trace()
    serialized_keys = set(trace.keys())

    assert "raw_reasoning" not in serialized_keys
    assert "hidden_reasoning" not in serialized_keys
    assert trace["action_execution_claimed"] is False
