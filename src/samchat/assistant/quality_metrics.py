from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Mapping, Optional


@dataclass(frozen=True)
class ReadOnlyUtilityMetrics:
    useful_answer_present: bool
    source_or_tool_trace_present: bool
    provider_timeout_count: int
    tool_success_count: int
    tool_failure_count: int
    runtime_allowed_count: int
    runtime_denied_count: int
    write_handlers_invoked: int
    side_effects_detected: int
    bounded_duration_seconds: Optional[float] = None
    healthz_status: Optional[int] = None
    readyz_status: Optional[int] = None

    def to_trace(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["readonly_safe"] = (
            self.write_handlers_invoked == 0 and self.side_effects_detected == 0
        )
        return payload


def _decision_from_trace(item: Mapping[str, Any]) -> Optional[str]:
    if "decision" in item:
        return str(item.get("decision") or "")
    if "assistant_policy" in item and isinstance(item["assistant_policy"], Mapping):
        return str(item["assistant_policy"].get("decision") or "")
    if "assistant_agent_runtime_activation" in item:
        activation = item["assistant_agent_runtime_activation"]
        if isinstance(activation, Mapping):
            return str(activation.get("decision") or "")
    return None


def _side_effect_count_from_trace(item: Mapping[str, Any]) -> int:
    count = item.get("side_effects_detected")
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        return count
    return int(item.get("side_effect_detected") is True)


def evaluate_readonly_utility_metrics(
    *,
    assistant_message: str,
    tool_trace: Iterable[Mapping[str, Any]],
    bounded_duration_seconds: Optional[float] = None,
    healthz_status: Optional[int] = None,
    readyz_status: Optional[int] = None,
) -> ReadOnlyUtilityMetrics:
    trace_items = list(tool_trace or [])
    provider_timeout_count = 0
    tool_success_count = 0
    tool_failure_count = 0
    runtime_allowed_count = 0
    runtime_denied_count = 0
    write_handlers_invoked = 0
    side_effects_detected = 0

    for item in trace_items:
        if "provider_error" in item:
            error = item.get("provider_error") or {}
            if isinstance(error, Mapping) and error.get("reason") == "PROVIDER_TIMEOUT":
                provider_timeout_count += 1
        if "tool" in item and "result" in item:
            result = item.get("result")
            if isinstance(result, Mapping) and result.get("error"):
                tool_failure_count += 1
            else:
                tool_success_count += 1
        decision = _decision_from_trace(item)
        if decision in {"allow", "RUNTIME_ALLOWED_EMPLOYEE_ID"}:
            runtime_allowed_count += 1
        elif decision:
            runtime_denied_count += 1
        if item.get("handler_invoked") is True and item.get("operation_type") == "write":
            write_handlers_invoked += 1
        side_effects_detected += _side_effect_count_from_trace(item)

    return ReadOnlyUtilityMetrics(
        useful_answer_present=bool((assistant_message or "").strip()),
        source_or_tool_trace_present=bool(trace_items),
        provider_timeout_count=provider_timeout_count,
        tool_success_count=tool_success_count,
        tool_failure_count=tool_failure_count,
        runtime_allowed_count=runtime_allowed_count,
        runtime_denied_count=runtime_denied_count,
        write_handlers_invoked=write_handlers_invoked,
        side_effects_detected=side_effects_detected,
        bounded_duration_seconds=bounded_duration_seconds,
        healthz_status=healthz_status,
        readyz_status=readyz_status,
    )
