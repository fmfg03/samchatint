"""Final executive rendering for WorkFrame-governed turns."""

from __future__ import annotations

from typing import Iterable, Mapping, Any

from .response_sufficiency import ResponseSufficiencyResult, render_sufficiency_gap_answer
from .work_frame import WorkFrame, normalize_work_text

_CONTROLLED_SURFACES = (
    "document_intake_live_wiring",
    "receipt_workflow_draft",
    "analyst_workbench_live_wiring",
)


def _has_controlled_surface(tool_trace: Iterable[Mapping[str, Any]] | None) -> bool:
    return any(any(key in trace for key in _CONTROLLED_SURFACES) for trace in tool_trace or [])


def _looks_like_raw_tool_payload(message: str) -> bool:
    stripped = (message or "").lstrip()
    return stripped.startswith('{"name"') or (stripped.startswith("{") and '"arguments"' in stripped[:240])


def render_work_turn_answer(
    *,
    current_message: str,
    work_frame: WorkFrame,
    sufficiency: ResponseSufficiencyResult,
    tool_trace: Iterable[Mapping[str, Any]] | None,
) -> tuple[str, bool]:
    """Return final human-facing answer plus whether it was materially rendered.

    This layer is intentionally conservative: it does not rewrite deterministic
    document/analyst surfaces, and it does not invent evidence. It normalizes the
    final answer boundary and blocks raw tool-call payloads.
    """

    if not sufficiency.ok:
        return render_sufficiency_gap_answer(work_frame=work_frame, result=sufficiency), True

    message = str(current_message or "").strip()
    if _has_controlled_surface(tool_trace):
        return message, False

    if _looks_like_raw_tool_payload(message):
        return render_sufficiency_gap_answer(work_frame=work_frame, result=sufficiency), True

    normalized = normalize_work_text(message)
    if work_frame.domain in {"owner", "finance", "mixed"} and "frontera de autoridad" not in normalized and "read-only" not in normalized:
        suffix = "\n\nFrontera de autoridad: respuesta de lectura; no ejecuté cambios ni asumí datos sin evidencia."
        return message + suffix, True

    return message, False
