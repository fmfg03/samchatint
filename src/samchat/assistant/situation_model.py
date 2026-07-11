from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .policy import normalize_role


READONLY_KEYWORDS = (
    "consulta",
    "consultar",
    "muestra",
    "ver",
    "status",
    "estado",
    "resumen",
    "summary",
    "lista",
    "list",
    "cuanto",
    "cuánto",
    "dime",
)
ACTION_KEYWORDS = (
    "crea",
    "crear",
    "actualiza",
    "actualizar",
    "envia",
    "envía",
    "manda",
    "paga",
    "pagar",
    "registra",
    "registrar",
    "aprueba",
    "aprobar",
    "arregla",
    "arreglar",
    "elimina",
    "eliminar",
    "notifica",
    "notificar",
)
HIGH_RISK_KEYWORDS = (
    "pago",
    "payment",
    "nomina",
    "nómina",
    "payroll",
    "contabilidad",
    "accounting",
    "produccion",
    "producción",
    "live db",
    "webhook",
    "telegram",
    "whatsapp",
    "ocr",
    "delete",
    "elimina",
)


@dataclass(frozen=True)
class SituationModel:
    intent_category: str
    requested_outcome: str
    actor_employee_id: Optional[str]
    role: Optional[str]
    known_constraints: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    missing_information: List[str] = field(default_factory=list)
    risk_level: str = "medium"
    appears_read_only: bool = False
    appears_action_oriented: bool = False
    may_require_approval: bool = False
    may_require_future_write_execution: bool = False
    recommended_next_cognitive_step: str = "generate_hypotheses"

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def _normalized_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


def _compact_evidence(tool_traces: Optional[Iterable[Mapping[str, Any]]]) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    for index, trace in enumerate(tool_traces or []):
        if not isinstance(trace, Mapping):
            evidence.append({"index": index, "kind": "unstructured_trace"})
            continue
        keys = sorted(str(key) for key in trace.keys())
        item: Dict[str, Any] = {"index": index, "keys": keys}
        for key in (
            "assistant_route",
            "assistant_policy",
            "assistant_agent_runtime",
            "assistant_agent_runtime_activation",
            "assistant_proposed_action",
        ):
            if key in trace:
                item["kind"] = key
                value = trace.get(key)
                if isinstance(value, Mapping):
                    item["summary"] = {
                        str(k): value.get(k)
                        for k in ("route", "decision", "reason", "status", "tool_name")
                        if k in value
                    }
                break
        else:
            item["kind"] = "tool_trace"
        evidence.append(item)
    return evidence


def _intent_category(text: str, *, action_oriented: bool, read_only: bool) -> str:
    lowered = text.lower()
    if not text:
        return "unknown"
    if "?" in text or read_only:
        if action_oriented:
            return "ambiguous_operational_request"
        return "read_only_question"
    if action_oriented:
        return "operational_action_request"
    if any(word in lowered for word in ("puedes", "can you", "ayuda", "help")):
        return "ambiguous_assistance_request"
    return "unknown"


def build_situation_model(
    *,
    user_message: str,
    role: Optional[str] = None,
    employee_id: Optional[Any] = None,
    constraints: Optional[Iterable[str]] = None,
    tool_traces: Optional[Iterable[Mapping[str, Any]]] = None,
) -> SituationModel:
    text = _normalized_text(user_message)
    normalized_role = normalize_role(role) if role is not None else None
    employee_key = str(employee_id).strip() if employee_id is not None else ""
    read_only = _contains_any(text, READONLY_KEYWORDS)
    action_oriented = _contains_any(text, ACTION_KEYWORDS)
    high_risk = _contains_any(text, HIGH_RISK_KEYWORDS)
    evidence = _compact_evidence(tool_traces)

    missing: List[str] = []
    if normalized_role is None:
        missing.append("role")
    if not employee_key:
        missing.append("employee_id")
    if not text:
        missing.append("user_intent")
    if action_oriented and not evidence:
        missing.append("supporting_evidence_for_action")

    known_constraints = [str(item).strip() for item in constraints or [] if str(item).strip()]
    known_constraints.extend(
        [
            "writes_disabled",
            "external_side_effects_disallowed",
            "provider_calls_disallowed",
        ]
    )

    if high_risk:
        risk_level = "high"
    elif action_oriented or missing:
        risk_level = "medium"
    else:
        risk_level = "low"

    if high_risk:
        next_step = "generate_hypotheses_with_high_risk_guard"
    elif missing or (action_oriented and read_only):
        next_step = "generate_hypotheses_and_clarify"
    else:
        next_step = "generate_hypotheses"

    return SituationModel(
        intent_category=_intent_category(
            text,
            action_oriented=action_oriented,
            read_only=read_only,
        ),
        requested_outcome=text,
        actor_employee_id=employee_key or None,
        role=normalized_role,
        known_constraints=known_constraints,
        evidence=evidence,
        missing_information=missing,
        risk_level=risk_level,
        appears_read_only=read_only and not action_oriented,
        appears_action_oriented=action_oriented,
        may_require_approval=action_oriented or high_risk,
        may_require_future_write_execution=action_oriented,
        recommended_next_cognitive_step=next_step,
    )
