from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .cognitive_pipeline import CognitiveDecisionEnvelope


@dataclass(frozen=True)
class CognitiveAuditTrace:
    stages_completed: List[str]
    decision_labels: Dict[str, Any]
    evidence_count: int
    missing_info_count: int
    risk_level: str
    selected_strategy: str
    self_critique_result: str
    write_status: str = "disabled"
    side_effects_detected: int = 0
    write_handlers_invoked: int = 0
    provider_timeout_count: int = 0
    action_execution_claimed: bool = False
    safe_to_persist: bool = True

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def _provider_timeout_count(tool_traces: Optional[Iterable[Mapping[str, Any]]]) -> int:
    count = 0
    for trace in tool_traces or []:
        if not isinstance(trace, Mapping):
            continue
        text = repr(trace).lower()
        if "provider_timeout" in text or "timeout" in text:
            count += 1
    return count


def build_cognitive_audit_trace(
    *,
    envelope: CognitiveDecisionEnvelope,
    tool_traces: Optional[Iterable[Mapping[str, Any]]] = None,
) -> CognitiveAuditTrace:
    return CognitiveAuditTrace(
        stages_completed=[
            "situation_model",
            "hypothesis_engine",
            "decision_rubric",
            "response_strategy",
            "self_critique",
        ],
        decision_labels={
            "selected_option": envelope.selected_option,
            "final_response_mode": envelope.final_response_mode,
            "top_hypothesis_labels": [
                str(item.get("label"))
                for item in envelope.top_hypotheses
                if item.get("label")
            ],
        },
        evidence_count=len(envelope.evidence_summary),
        missing_info_count=len(envelope.missing_information),
        risk_level=envelope.risk_level,
        selected_strategy=envelope.final_response_mode,
        self_critique_result=str(envelope.self_critique_status.get("decision") or ""),
        provider_timeout_count=_provider_timeout_count(tool_traces),
    )
