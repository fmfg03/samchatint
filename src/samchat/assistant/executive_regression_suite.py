"""Executive regression suite for SamChat assistant answers.

These checks turn real owner/finance questions into deterministic expectations.
They do not call providers and they do not execute business actions; they verify
that a rendered answer used the right read-only surface, stayed inside the
authority boundary, and avoided known wrong interpretations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .response_quality_gate import evaluate_response_quality
from .response_sufficiency import evaluate_response_sufficiency
from .work_frame import WorkFrame, build_work_frame, normalize_work_text


@dataclass(frozen=True)
class ExecutiveRegressionCase:
    case_id: str
    question: str
    expected_domain: str
    expected_task_kind: str
    expected_answer_class: str
    expected_source_class: str
    expected_tools: tuple[str, ...] = ()
    required_answer_terms: tuple[str, ...] = ()
    forbidden_answer_terms: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    allow_gap_answer: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["expected_tools"] = list(self.expected_tools)
        payload["required_answer_terms"] = list(self.required_answer_terms)
        payload["forbidden_answer_terms"] = list(self.forbidden_answer_terms)
        payload["forbidden_tools"] = list(self.forbidden_tools)
        return payload


@dataclass(frozen=True)
class ExecutiveRegressionVerdict:
    case_id: str
    ok: bool
    failures: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "ok": self.ok,
            "failures": list(self.failures),
            "diagnostics": dict(self.diagnostics),
        }


def _trace_tools(tool_trace: Iterable[Mapping[str, Any]] | None) -> tuple[str, ...]:
    tools: list[str] = []
    for item in tool_trace or []:
        tool = str(item.get("tool") or "").strip()
        if tool and tool not in tools:
            tools.append(tool)
        for key in item.keys():
            if key.startswith("assistant_") and key not in tools:
                tools.append(key)
    return tuple(tools)


def _trace_has_write(tool_trace: Iterable[Mapping[str, Any]] | None) -> bool:
    for item in tool_trace or []:
        if item.get("writes_attempted") or item.get("operational_writes"):
            return True
        if item.get("handler_invoked") is True and item.get("operation_type") == "write":
            return True
        result = item.get("result")
        if isinstance(result, Mapping):
            if result.get("writes_attempted") or result.get("side_effects_detected"):
                return True
    return False


def _contains_all(answer: str, required_terms: Sequence[str]) -> list[str]:
    normalized = normalize_work_text(answer)
    return [term for term in required_terms if normalize_work_text(term) not in normalized]


def _contains_any(answer: str, forbidden_terms: Sequence[str]) -> list[str]:
    normalized = normalize_work_text(answer)
    return [term for term in forbidden_terms if normalize_work_text(term) in normalized]


def evaluate_executive_regression_case(
    *,
    case: ExecutiveRegressionCase,
    assistant_message: str,
    tool_trace: Iterable[Mapping[str, Any]] | None,
    work_frame: WorkFrame | None = None,
) -> ExecutiveRegressionVerdict:
    """Validate one executive answer against its stable product contract."""

    frame = work_frame or build_work_frame(case.question)
    trace_items = list(tool_trace or [])
    tools = _trace_tools(trace_items)
    failures: list[str] = []

    if frame.domain != case.expected_domain:
        failures.append(f"domain:{frame.domain}!={case.expected_domain}")
    if frame.task_kind != case.expected_task_kind:
        failures.append(f"task_kind:{frame.task_kind}!={case.expected_task_kind}")

    quality = evaluate_response_quality(assistant_message)
    if not quality.ok:
        failures.append(f"quality:{quality.reason}")

    sufficiency = evaluate_response_sufficiency(
        work_frame=frame,
        assistant_message=assistant_message,
        tool_trace=trace_items,
    )
    if not sufficiency.ok:
        failures.append(f"sufficiency:{sufficiency.reason}")

    missing_terms = _contains_all(assistant_message, case.required_answer_terms)
    failures.extend(f"missing_term:{term}" for term in missing_terms)

    forbidden_terms = _contains_any(assistant_message, case.forbidden_answer_terms)
    failures.extend(f"forbidden_term:{term}" for term in forbidden_terms)

    for expected_tool in case.expected_tools:
        if expected_tool not in tools:
            failures.append(f"missing_tool:{expected_tool}")
    for forbidden_tool in case.forbidden_tools:
        if forbidden_tool in tools:
            failures.append(f"forbidden_tool:{forbidden_tool}")

    if _trace_has_write(trace_items):
        failures.append("write_or_side_effect_detected")

    if not case.allow_gap_answer and "no tengo evidencia suficiente" in normalize_work_text(assistant_message):
        failures.append("unexpected_gap_answer")

    return ExecutiveRegressionVerdict(
        case_id=case.case_id,
        ok=not failures,
        failures=tuple(failures),
        diagnostics={
            "question": case.question,
            "expected_answer_class": case.expected_answer_class,
            "expected_source_class": case.expected_source_class,
            "work_frame": frame.to_dict(),
            "tools": list(tools),
            "quality": quality.reason,
            "sufficiency": sufficiency.to_dict(),
        },
    )


def executive_regression_cases() -> tuple[ExecutiveRegressionCase, ...]:
    """Return the current real-question suite for assistant demo hardening."""

    return (
        ExecutiveRegressionCase(
            case_id="OWNER-READINESS-001",
            question="tenemos listo el pack del dueño?",
            expected_domain="owner",
            expected_task_kind="readiness",
            expected_answer_class="readiness_or_explicit_gap",
            expected_source_class="owner_pack_readiness",
            expected_tools=("assistant_owner_pack_readiness",),
            required_answer_terms=("Owner Pack", "Readiness", "Frontera de autoridad"),
            forbidden_answer_terms=('"name":', "assistant_owner_pack_readiness", "tenemos datos cargados"),
            allow_gap_answer=True,
            notes="Must render an executive readiness answer, not a raw tool call.",
        ),
        ExecutiveRegressionCase(
            case_id="OWNER-PAYMENT-EVIDENCE-001",
            question="¿Qué evidencia tenemos de pagos hechos en agosto?",
            expected_domain="owner",
            expected_task_kind="evidence",
            expected_answer_class="supported_evidence_or_explicit_gap",
            expected_source_class="owner_variable_or_payment_evidence",
            expected_tools=("assistant_owner_variable_query",),
            required_answer_terms=("evidencia", "No ejecut"),
            forbidden_answer_terms=("Hay 0 solicitudes pendientes", "Pagos pendientes"),
            forbidden_tools=("receipts.pending_payment_overview",),
            allow_gap_answer=True,
            notes="Historical payment evidence must not be answered with a pending-payment queue.",
        ),
        ExecutiveRegressionCase(
            case_id="FIN-ACCOUNTING-LOADED-001",
            question="tenemos contabilidad cargada?",
            expected_domain="finance",
            expected_task_kind="status",
            expected_answer_class="finance_status",
            expected_source_class="finance.platform_snapshot",
            expected_tools=("assistant_finance_accounting_qa",),
            required_answer_terms=("información financiera", "Fuente", "finance.platform_snapshot", "No ejecut"),
            forbidden_answer_terms=("ᴍᴇɴᴛ", "played to the tornee", '"name":'),
        ),
        ExecutiveRegressionCase(
            case_id="FIN-PAYMENT-RUN-001",
            question="¿Qué está en payment run?",
            expected_domain="finance",
            expected_task_kind="status",
            expected_answer_class="payment_run_status",
            expected_source_class="finance.platform_snapshot.payment_run",
            expected_tools=("assistant_finance_accounting_qa",),
            required_answer_terms=("Payment Run", "Ruta sugerida", "/admin/finanzas/payment-run", "No ejecut"),
            forbidden_answer_terms=("Owner Pack", '"name":'),
        ),
        ExecutiveRegressionCase(
            case_id="FIN-CLOSE-BLOCKERS-001",
            question="¿Por qué no puedo cerrar contabilidad?",
            expected_domain="finance",
            expected_task_kind="diagnostic",
            expected_answer_class="closeout_blockers",
            expected_source_class="finance.closeout_diagnostics",
            expected_tools=("assistant_finance_accounting_qa",),
            required_answer_terms=("contabilidad", "bloque", "Fuente", "No ejecut"),
            forbidden_answer_terms=("Owner Pack", "acciones ejecutadas", '"name":'),
            allow_gap_answer=True,
        ),
        ExecutiveRegressionCase(
            case_id="FIN-CFDI-GAPS-001",
            question="¿Qué CFDIs faltan vincular?",
            expected_domain="finance",
            expected_task_kind="evidence",
            expected_answer_class="cfdi_gap_status",
            expected_source_class="finance.platform_snapshot.tax_readiness",
            expected_tools=("assistant_finance_accounting_qa",),
            required_answer_terms=("CFDI", "Evidencia", "Ruta sugerida", "No ejecut"),
            forbidden_answer_terms=("Owner Pack", '"name":'),
            allow_gap_answer=True,
        ),
        ExecutiveRegressionCase(
            case_id="OWNER-TEAMS-BY-CATEGORY-001",
            question="¿Cuántos equipos reales tenemos por categoría?",
            expected_domain="owner",
            expected_task_kind="evidence",
            expected_answer_class="owner_variable_answer_or_gap",
            expected_source_class="owner_variable_source",
            expected_tools=("assistant_owner_variable_query",),
            required_answer_terms=("equipos", "evidencia"),
            forbidden_answer_terms=("inventé", "supongo", '"name":'),
            allow_gap_answer=True,
        ),
    )


__all__ = [
    "ExecutiveRegressionCase",
    "ExecutiveRegressionVerdict",
    "evaluate_executive_regression_case",
    "executive_regression_cases",
]
