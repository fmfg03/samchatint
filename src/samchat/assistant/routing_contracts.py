"""Deterministic routing contracts for high-value assistant questions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .request_intent import normalize_request_text


@dataclass(frozen=True)
class AssistantRoutingContractCase:
    case_id: str
    prompt: str
    expected_tool: str
    expected_intent: str | None = None
    must_bypass_provider: bool = True
    must_be_read_only: bool = True
    must_be_exportable: bool = False
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["required_terms"] = list(self.required_terms)
        payload["forbidden_terms"] = list(self.forbidden_terms)
        return payload


@dataclass(frozen=True)
class AssistantRoutingVerdict:
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


def _walk(value: Any):
    if isinstance(value, Mapping):
        for item in value.values():
            yield item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield item
            yield from _walk(item)


def _trace_provider_called(tool_trace: Iterable[Mapping[str, Any]] | None) -> bool:
    for item in tool_trace or []:
        if item.get("provider_called") is True:
            return True
        for value in _walk(item):
            if isinstance(value, Mapping) and value.get("provider_called") is True:
                return True
    return False


def _trace_has_write(tool_trace: Iterable[Mapping[str, Any]] | None) -> bool:
    for item in tool_trace or []:
        if item.get("writes_attempted") or item.get("operational_writes"):
            return True
        result = item.get("result")
        if isinstance(result, Mapping):
            if result.get("writes_attempted") or result.get("side_effects_detected"):
                return True
        for value in _walk(item):
            if isinstance(value, Mapping) and (
                value.get("writes_attempted") or value.get("operational_writes")
            ):
                return True
    return False


def _trace_intents(tool_trace: Iterable[Mapping[str, Any]] | None) -> tuple[str, ...]:
    intents: list[str] = []
    for item in tool_trace or []:
        for value in _walk(item):
            if isinstance(value, Mapping):
                intent = str(value.get("intent") or "").strip()
                if intent and intent not in intents:
                    intents.append(intent)
    return tuple(intents)


def _trace_exportable(tool_trace: Iterable[Mapping[str, Any]] | None) -> bool:
    for item in tool_trace or []:
        result = item.get("result")
        if not isinstance(result, Mapping):
            continue
        payload = result.get("payload")
        if isinstance(payload, Mapping) and payload.get("report_type") and payload.get("rows"):
            return True
        rows = result.get("rows")
        if isinstance(rows, list) and rows:
            return True
    return False


def _missing_terms(text: str, required_terms: Sequence[str]) -> list[str]:
    normalized = normalize_request_text(text)
    return [
        term
        for term in required_terms
        if normalize_request_text(term) not in normalized
    ]


def _present_forbidden_terms(text: str, forbidden_terms: Sequence[str]) -> list[str]:
    normalized = normalize_request_text(text)
    return [
        term
        for term in forbidden_terms
        if normalize_request_text(term) in normalized
    ]


def evaluate_routing_contract_case(
    *,
    case: AssistantRoutingContractCase,
    assistant_message: str,
    tool_trace: Iterable[Mapping[str, Any]] | None,
) -> AssistantRoutingVerdict:
    trace_items = list(tool_trace or [])
    tools = _trace_tools(trace_items)
    intents = _trace_intents(trace_items)
    failures: list[str] = []

    if case.expected_tool not in tools:
        failures.append(f"missing_tool:{case.expected_tool}")
    if case.expected_intent and case.expected_intent not in intents:
        failures.append(f"missing_intent:{case.expected_intent}")
    if case.must_bypass_provider and _trace_provider_called(trace_items):
        failures.append("provider_called")
    if case.must_be_read_only and _trace_has_write(trace_items):
        failures.append("write_or_side_effect_detected")
    if case.must_be_exportable and not _trace_exportable(trace_items):
        failures.append("not_exportable")
    failures.extend(
        f"missing_term:{term}"
        for term in _missing_terms(assistant_message, case.required_terms)
    )
    failures.extend(
        f"forbidden_term:{term}"
        for term in _present_forbidden_terms(assistant_message, case.forbidden_terms)
    )

    return AssistantRoutingVerdict(
        case_id=case.case_id,
        ok=not failures,
        failures=tuple(failures),
        diagnostics={
            "prompt": case.prompt,
            "tools": list(tools),
            "intents": list(intents),
            "provider_called": _trace_provider_called(trace_items),
            "write_detected": _trace_has_write(trace_items),
            "exportable": _trace_exportable(trace_items),
        },
    )


def assistant_routing_contract_cases() -> tuple[AssistantRoutingContractCase, ...]:
    return (
        AssistantRoutingContractCase(
            case_id="ROUTE-FIN-CASHFLOW-STATEMENT-001",
            prompt="Dame el flujo de efectivo de Copa Telmex a junio",
            expected_tool="assistant_finance_read",
            expected_intent="cashflow.statement",
            must_be_exportable=True,
            required_terms=("Flujo de Efectivo",),
            forbidden_terms=("Cashflow Planning read-only",),
        ),
        AssistantRoutingContractCase(
            case_id="ROUTE-FIN-CASHFLOW-SUMMARY-001",
            prompt="Dame el cashflow planning técnico de Copa Telmex",
            expected_tool="assistant_finance_read",
            expected_intent="cashflow.summary",
            required_terms=("Cashflow Planning read-only",),
            forbidden_terms=("Flujo de Efectivo",),
        ),
        AssistantRoutingContractCase(
            case_id="ROUTE-FIN-BUDGET-VS-ACTUAL-001",
            prompt="Genera presupuesto vs real de Copa Telmex a junio",
            expected_tool="assistant_finance_read",
            expected_intent="budget.vs_actual",
            must_be_exportable=True,
            required_terms=("Presupuesto vs Real",),
            forbidden_terms=("Presupuesto read-only",),
        ),
        AssistantRoutingContractCase(
            case_id="ROUTE-FIN-BUDGET-SNAPSHOT-001",
            prompt="Dame el snapshot de presupuesto de Copa Telmex",
            expected_tool="assistant_finance_read",
            expected_intent="budget.snapshot",
            required_terms=("Presupuesto read-only",),
            forbidden_terms=("Presupuesto vs Real",),
        ),
        AssistantRoutingContractCase(
            case_id="ROUTE-FIN-AR-SUMMARY-001",
            prompt="Cuánto falta por cobrar de Copa Telmex",
            expected_tool="assistant_finance_read",
            expected_intent="ar.summary",
            required_terms=("CxC AR read-only",),
            forbidden_terms=("Cashflow Planning",),
        ),
        AssistantRoutingContractCase(
            case_id="ROUTE-FIN-AR-PREMATCHING-001",
            prompt="Revisa el pre matching de cuentas por cobrar",
            expected_tool="assistant_finance_read",
            expected_intent="ar.prematching",
            required_terms=("Pre-matching AR read-only",),
            forbidden_terms=("Cashflow Planning",),
        ),
        AssistantRoutingContractCase(
            case_id="ROUTE-OWNER-READINESS-001",
            prompt="Qué falta para el Owner Pack de Jalisco",
            expected_tool="assistant_owner_pack_readiness",
            required_terms=("Estado ejecutivo del Owner Pack",),
            forbidden_terms=("assistant_owner_pack_readiness",),
        ),
        AssistantRoutingContractCase(
            case_id="ROUTE-OWNER-FOLDER-WORKSPACE-001",
            prompt="Abre el workspace de carpeta por entidad CDMX",
            expected_tool="assistant_owner_entity_folder_workspace",
            required_terms=("Owner Entity Folder Workspace",),
            forbidden_terms=("db_write_universal",),
        ),
        AssistantRoutingContractCase(
            case_id="ROUTE-OWNER-VARIABLE-001",
            prompt="Cuántos equipos reales tenemos por categoría",
            expected_tool="assistant_owner_variable_query",
            required_terms=("equipos",),
            forbidden_terms=("supongo",),
        ),
        AssistantRoutingContractCase(
            case_id="ROUTE-FIN-ACCOUNTING-QA-001",
            prompt="Tenemos contabilidad cargada?",
            expected_tool="assistant_finance_accounting_qa",
            required_terms=("información financiera",),
            forbidden_terms=("Owner Pack",),
        ),
        AssistantRoutingContractCase(
            case_id="ROUTE-FIN-CFDI-GAPS-001",
            prompt="Qué CFDIs faltan vincular?",
            expected_tool="assistant_finance_accounting_qa",
            required_terms=("CFDI",),
            forbidden_terms=("Owner Pack",),
        ),
        AssistantRoutingContractCase(
            case_id="ROUTE-FIN-YOY-COMPARISON-001",
            prompt="Compara gasto 2026 vs 2025 por concepto",
            expected_tool="finance.read_only_comparison",
            must_be_exportable=True,
            required_terms=("Comparación",),
            forbidden_terms=("assistant_finance_read",),
        ),
    )


__all__ = [
    "AssistantRoutingContractCase",
    "AssistantRoutingVerdict",
    "assistant_routing_contract_cases",
    "evaluate_routing_contract_case",
]
