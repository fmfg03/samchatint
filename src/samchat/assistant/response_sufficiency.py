"""Response sufficiency gate for Claude-Code-like SamChat turns.

A selected tool is not enough. The assistant must also prove that the rendered
answer satisfies the WorkFrame. This module is deterministic, read-only and
fail-closed for known semantic mismatches.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .tool_adjudicator import adjudicate_tool_candidate
from .work_frame import WorkFrame, normalize_work_text


@dataclass(frozen=True)
class ResponseSufficiencyResult:
    ok: bool
    reason: str
    action: str
    tool: str
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _primary_tool(tool_trace: Iterable[Mapping[str, Any]] | None) -> str:
    for trace in tool_trace or []:
        tool = str(trace.get("tool") or "").strip()
        if tool and not tool.startswith("assistant."):
            return tool
    for trace in tool_trace or []:
        tool = str(trace.get("tool") or "").strip()
        if tool:
            return tool
    return ""


def _result_payload(tool_trace: Iterable[Mapping[str, Any]] | None) -> Mapping[str, Any]:
    for trace in tool_trace or []:
        result = trace.get("result")
        if isinstance(result, Mapping):
            return result
    return {}


def _deterministic_surface(tool_trace: Iterable[Mapping[str, Any]] | None) -> str:
    """Return controlled non-Q&A surfaces that already render safe output."""

    for trace in tool_trace or []:
        for key in (
            "document_intake_live_wiring",
            "receipt_workflow_draft",
            "analyst_workbench_live_wiring",
        ):
            if key in trace:
                return key
    return ""


def evaluate_response_sufficiency(
    *,
    work_frame: WorkFrame,
    assistant_message: str,
    tool_trace: Iterable[Mapping[str, Any]] | None,
) -> ResponseSufficiencyResult:
    """Evaluate whether the visible answer satisfies the current WorkFrame."""

    tool = _primary_tool(tool_trace)
    adjudication = adjudicate_tool_candidate(work_frame=work_frame, tool=tool)
    message = normalize_work_text(assistant_message)
    result = _result_payload(tool_trace)

    surface = _deterministic_surface(tool_trace)
    diagnostics = {
        "work_frame_domain": work_frame.domain,
        "work_frame_task_kind": work_frame.task_kind,
        "required_evidence": list(work_frame.required_evidence),
        "forbidden_interpretations": list(work_frame.forbidden_interpretations),
        "primary_tool": tool,
        "deterministic_surface": surface,
        "result_status": result.get("status") or result.get("question_type"),
    }

    if surface:
        return ResponseSufficiencyResult(
            ok=True,
            reason="controlled_deterministic_surface_already_rendered_safe_output",
            action="allow",
            tool=tool or surface,
            diagnostics=diagnostics,
        )

    if work_frame.needs_clarification and not tool:
        return ResponseSufficiencyResult(
            ok=True,
            reason="unknown_work_frame_provider_or_clarification_fallback_allowed",
            action="allow",
            tool=tool,
            diagnostics=diagnostics,
        )

    if not adjudication.accepted:
        return ResponseSufficiencyResult(
            ok=False,
            reason=adjudication.reason,
            action="replace_with_gap_answer",
            tool=tool,
            diagnostics=diagnostics,
        )

    if (
        "zero_pending_payments_as_evidence" in work_frame.forbidden_interpretations
        and (
            "pagos pendientes" in message
            or "solicitudes pendientes" in message
            or tool == "receipts.pending_payment_overview"
        )
        and "no hay dato soportado" not in message
    ):
        return ResponseSufficiencyResult(
            ok=False,
            reason="pending_payment_summary_does_not_answer_payment_evidence_question",
            action="replace_with_gap_answer",
            tool=tool,
            diagnostics=diagnostics,
        )

    if work_frame.domain == "owner" and work_frame.task_kind == "readiness":
        if "readiness" not in message and "faltantes" not in message:
            return ResponseSufficiencyResult(
                ok=False,
                reason="owner_readiness_answer_missing_readiness_or_gaps",
                action="replace_with_gap_answer",
                tool=tool,
                diagnostics=diagnostics,
            )

    if work_frame.domain == "owner" and work_frame.task_kind == "evidence":
        has_supported = any(
            token in message
            for token in (
                "si tengo ese dato",
                "sí tengo ese dato",
                "evidencia visible",
                "fuente",
                "fuentes",
            )
        )
        has_gap = any(
            token in message
            for token in (
                "no hay dato soportado",
                "no encontre evidencia",
                "no encontré evidencia",
                "no hay evidencia viva suficiente",
            )
        )
        if not (has_supported or has_gap):
            return ResponseSufficiencyResult(
                ok=False,
                reason="owner_evidence_answer_missing_supported_evidence_or_explicit_gap",
                action="replace_with_gap_answer",
                tool=tool,
                diagnostics=diagnostics,
            )

    if work_frame.domain == "finance" and work_frame.task_kind in {"status", "diagnostic", "evidence"}:
        if tool == "receipts.pending_payment_overview" and any(
            token in message for token in ("pagos pendientes", "solicitudes pendientes")
        ):
            return ResponseSufficiencyResult(
                ok=True,
                reason="pending_payment_answer_matches_pending_payment_work_frame",
                action="allow",
                tool=tool,
                diagnostics=diagnostics,
            )
        if tool == "receipts.cfdi_matching_overview" and any(
            token in message for token in ("cfdi", "cfdis", "factura", "facturas")
        ):
            return ResponseSufficiencyResult(
                ok=True,
                reason="cfdi_answer_matches_finance_evidence_work_frame",
                action="allow",
                tool=tool,
                diagnostics=diagnostics,
            )
        if not any(token in message for token in ("fuente", "ruta", "snapshot", "evidencia", "no pude consultar")):
            return ResponseSufficiencyResult(
                ok=False,
                reason="finance_answer_missing_source_or_route",
                action="replace_with_gap_answer",
                tool=tool,
                diagnostics=diagnostics,
            )

    return ResponseSufficiencyResult(
        ok=True,
        reason="answer_satisfies_known_work_frame_invariants",
        action="allow",
        tool=tool,
        diagnostics=diagnostics,
    )


def render_sufficiency_gap_answer(
    *,
    work_frame: WorkFrame,
    result: ResponseSufficiencyResult,
) -> str:
    """Render a safe executive answer when the selected path was insufficient."""

    evidence = ", ".join(work_frame.required_evidence) or "evidencia canónica"
    forbidden = ", ".join(work_frame.forbidden_interpretations) or "atajos sin soporte"
    return "\n".join(
        [
            "No tengo evidencia suficiente para contestar eso todavía.",
            "",
            f"Lo que entendí: {work_frame.interpreted_goal}",
            f"Necesito revisar: {evidence}.",
            f"No voy a usar como sustituto: {forbidden}.",
            "",
            "Siguiente paso: conectar o consultar la fuente correcta antes de darte una conclusión.",
            "No ejecuté cambios; esta respuesta es sólo lectura.",
        ]
    )


def build_response_sufficiency_trace(
    *,
    result: ResponseSufficiencyResult,
) -> dict[str, Any]:
    return {
        "tool": "assistant.response_sufficiency_gate",
        "assistant_response_sufficiency_gate": {
            "stage": "post_tool_work_frame_sufficiency_gate",
            **result.to_dict(),
        },
        "result": result.to_dict(),
    }


__all__ = [
    "ResponseSufficiencyResult",
    "evaluate_response_sufficiency",
    "render_sufficiency_gap_answer",
    "build_response_sufficiency_trace",
]
