"""Read-only multi-candidate execution for executive assistant turns.

A broad SamChat question can legitimately need more than one read-only surface
before the assistant can answer like an operator. This module does not execute
business tools by itself; it receives already-executed candidate responses,
checks each one against the WorkFrame sufficiency gate, and returns a single
human-facing answer plus an audit trace. Writes remain impossible here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .response_sufficiency import (
    ResponseSufficiencyResult,
    evaluate_response_sufficiency,
    render_sufficiency_gap_answer,
)
from .work_frame import WorkFrame, normalize_work_text


@dataclass(frozen=True)
class ReadOnlyCandidateResponse:
    """One read-only candidate answer produced by an existing deterministic path."""

    tool: str
    assistant_message: str
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    label: str = ""

    def to_trace(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "label": self.label or self.tool,
            "message_preview": _preview(self.assistant_message),
            "trace_count": len(self.tool_trace),
        }


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: ReadOnlyCandidateResponse
    sufficiency: ResponseSufficiencyResult

    def to_trace(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_trace(),
            "sufficiency": self.sufficiency.to_dict(),
        }


@dataclass(frozen=True)
class MultiCandidateSelection:
    selected: ReadOnlyCandidateResponse | None
    evaluations: list[CandidateEvaluation]
    rendered_message: str
    selected_tool: str
    reason: str
    read_only: bool = True
    writes_attempted: bool = False

    def trace(self) -> dict[str, Any]:
        return {
            "tool": "assistant.multi_candidate_readonly",
            "assistant_multi_candidate_readonly": {
                "stage": "read_only_candidate_execution_and_selection",
                "candidate_count": len(self.evaluations),
                "selected_tool": self.selected_tool,
                "reason": self.reason,
                "read_only": self.read_only,
                "writes_attempted": self.writes_attempted,
            },
            "result": {
                "selected_tool": self.selected_tool,
                "reason": self.reason,
                "candidate_count": len(self.evaluations),
                "evaluations": [item.to_trace() for item in self.evaluations],
                "read_only": self.read_only,
                "writes_attempted": self.writes_attempted,
            },
        }


def _preview(message: str, *, limit: int = 240) -> str:
    clean = " ".join(str(message or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _is_gap_answer(message: str) -> bool:
    text = normalize_work_text(message)
    return any(
        token in text
        for token in (
            "no tengo evidencia suficiente",
            "no hay dato soportado",
            "no pude consultar",
            "no encontre evidencia",
            "no encontr? evidencia",
            "falta evidencia",
        )
    )


def _score_evaluation(evaluation: CandidateEvaluation) -> tuple[int, int, int]:
    """Higher is better; deterministic and intentionally simple."""

    message = evaluation.candidate.assistant_message or ""
    text = normalize_work_text(message)
    supported_signal = any(
        token in text
        for token in (
            "fuente",
            "fuentes",
            "ruta",
            "evidencia",
            "snapshot",
            "datos encontrados",
            "payment run",
            "contabilidad",
            "owner pack",
        )
    )
    gap_penalty = 1 if _is_gap_answer(message) else 0
    return (
        1 if evaluation.sufficiency.ok else 0,
        1 if supported_signal else 0,
        -gap_penalty,
    )


def _render_combined_answer(
    *,
    evaluations: Sequence[CandidateEvaluation],
    selected: CandidateEvaluation,
) -> str:
    """Render one executive answer while preserving the selected candidate text."""

    ok_evaluations = [item for item in evaluations if item.sufficiency.ok]
    if len(ok_evaluations) <= 1:
        return selected.candidate.assistant_message

    lines: list[str] = [selected.candidate.assistant_message.strip()]
    already = normalize_work_text("\n".join(lines))
    for item in ok_evaluations:
        if item is selected:
            continue
        snippet = _preview(item.candidate.assistant_message, limit=320)
        if not snippet or normalize_work_text(snippet) in already:
            continue
        lines.extend(
            [
                "",
                f"Lectura complementaria ({item.candidate.label or item.candidate.tool}):",
                snippet,
            ]
        )
        already = normalize_work_text("\n".join(lines))
    if "read-only" not in already and "frontera de autoridad" not in already:
        lines.extend(
            [
                "",
                "Frontera de autoridad: respuesta de lectura; no ejecute cambios ni asumi datos sin evidencia.",
            ]
        )
    return "\n".join(lines)


def evaluate_readonly_candidates(
    *,
    work_frame: WorkFrame,
    candidates: Iterable[ReadOnlyCandidateResponse],
) -> MultiCandidateSelection:
    """Evaluate candidate answers and pick a sufficient read-only result."""

    evaluations: list[CandidateEvaluation] = []
    for candidate in candidates:
        sufficiency = evaluate_response_sufficiency(
            work_frame=work_frame,
            assistant_message=candidate.assistant_message,
            tool_trace=candidate.tool_trace,
        )
        evaluations.append(CandidateEvaluation(candidate=candidate, sufficiency=sufficiency))

    accepted = [item for item in evaluations if item.sufficiency.ok]
    if accepted:
        selected = max(accepted, key=_score_evaluation)
        return MultiCandidateSelection(
            selected=selected.candidate,
            evaluations=evaluations,
            rendered_message=_render_combined_answer(
                evaluations=evaluations,
                selected=selected,
            ),
            selected_tool=selected.candidate.tool,
            reason="selected_highest_scoring_sufficient_read_only_candidate",
        )

    synthetic = ResponseSufficiencyResult(
        ok=False,
        reason="no_read_only_candidate_satisfied_work_frame",
        action="replace_with_gap_answer",
        tool="assistant.multi_candidate_readonly",
        diagnostics={
            "candidate_count": len(evaluations),
            "candidate_tools": [item.candidate.tool for item in evaluations],
            "work_frame_domain": work_frame.domain,
            "work_frame_task_kind": work_frame.task_kind,
        },
    )
    return MultiCandidateSelection(
        selected=None,
        evaluations=evaluations,
        rendered_message=render_sufficiency_gap_answer(
            work_frame=work_frame,
            result=synthetic,
        ),
        selected_tool="",
        reason="no_candidate_sufficient",
    )


__all__ = [
    "CandidateEvaluation",
    "MultiCandidateSelection",
    "ReadOnlyCandidateResponse",
    "evaluate_readonly_candidates",
]
