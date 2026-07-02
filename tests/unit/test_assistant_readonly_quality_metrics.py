from __future__ import annotations

from samchat.assistant.quality_metrics import evaluate_readonly_utility_metrics


def test_readonly_utility_metrics_count_trace_quality_without_side_effects() -> None:
    metrics = evaluate_readonly_utility_metrics(
        assistant_message="Encontré 2 gastos pendientes.",
        tool_trace=[
            {"decision": "RUNTIME_ALLOWED_EMPLOYEE_ID"},
            {"provider_error": {"reason": "PROVIDER_TIMEOUT"}},
            {"tool": "finance_ops_query", "result": {"rows": []}},
            {"tool": "finance_alerts_scan", "result": {"error": "timeout"}},
        ],
        bounded_duration_seconds=1.25,
        healthz_status=200,
        readyz_status=200,
    )

    payload = metrics.to_trace()
    assert payload["useful_answer_present"] is True
    assert payload["source_or_tool_trace_present"] is True
    assert payload["provider_timeout_count"] == 1
    assert payload["tool_success_count"] == 1
    assert payload["tool_failure_count"] == 1
    assert payload["runtime_allowed_count"] == 1
    assert payload["runtime_denied_count"] == 0
    assert payload["write_handlers_invoked"] == 0
    assert payload["side_effects_detected"] == 0
    assert payload["readonly_safe"] is True
    assert payload["healthz_status"] == 200
    assert payload["readyz_status"] == 200


def test_readonly_utility_metrics_counts_denied_runtime_decisions() -> None:
    metrics = evaluate_readonly_utility_metrics(
        assistant_message="No puedo ejecutar esa herramienta.",
        tool_trace=[
            {"decision": "RUNTIME_SUBJECT_NOT_ALLOWED"},
            {
                "assistant_policy": {
                    "decision": "deny",
                    "reason": "role_not_allowed:user",
                }
            },
        ],
    )

    payload = metrics.to_trace()
    assert payload["runtime_allowed_count"] == 0
    assert payload["runtime_denied_count"] == 2
    assert payload["write_handlers_invoked"] == 0
    assert payload["side_effects_detected"] == 0
    assert payload["readonly_safe"] is True
