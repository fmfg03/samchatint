"""Conversational renderer for Owner Pack variable query reports.

The resolver decides what is supported, partial, missing, conflicting or
unmapped. This module only renders that evidence into owner-facing Spanish. It
must not infer facts, execute actions or upgrade missing evidence into claims.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from .business_diff_preview import NOT_EXECUTED
from .owner_variable_query import (
    OWNER_VARIABLE_CONFLICT,
    OWNER_VARIABLE_MISSING,
    OWNER_VARIABLE_PARTIAL,
    OWNER_VARIABLE_QUERY_ONLY,
    OWNER_VARIABLE_SUPPORTED,
    OWNER_VARIABLE_UNMAPPED,
    OwnerVariableQueryReport,
    OwnerVariableResolution,
)

OWNER_VARIABLE_ANSWER_ONLY = "owner_variable_answer_only"
ANSWER_TONE_OWNER_BRIEF = "owner_brief"


@dataclass(frozen=True)
class OwnerVariableConversationalAnswer:
    """Human-readable, no-write answer derived from a variable query report."""

    answer_id: str
    source_query_id: str
    status: str
    headline: str
    short_answer: str
    detail_lines: list[str] = field(default_factory=list)
    evidence_lines: list[str] = field(default_factory=list)
    missing_lines: list[str] = field(default_factory=list)
    conflict_lines: list[str] = field(default_factory=list)
    next_questions: list[str] = field(default_factory=list)
    rendered_text: str = ""
    safety_summary: dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_VARIABLE_ANSWER_ONLY
    tone: str = ANSWER_TONE_OWNER_BRIEF

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _answer_id(report: OwnerVariableQueryReport) -> str:
    key = f"{report.query_id}|{report.question}|{report.status}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"ova_{digest}"


def _value_text(value: Any) -> str:
    if value is None or value == "":
        return "sin valor"
    if isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value]
        return ", ".join(values) if values else "sin valor"
    if isinstance(value, dict):
        pieces: list[str] = []
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            pieces.append(f"{key}: {_value_text(item)}")
        return "; ".join(pieces) if pieces else "sin valor"
    return str(value)


def _shorten(text: str, *, limit: int = 220) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "?"


def _status_headline(status: str) -> str:
    if status == OWNER_VARIABLE_SUPPORTED:
        return "Si tengo ese dato soportado por evidencia."
    if status == OWNER_VARIABLE_PARTIAL:
        return "Tengo una respuesta parcial; falta evidencia para cerrarla."
    if status == OWNER_VARIABLE_CONFLICT:
        return "Hay fuentes en conflicto; no conviene usar el dato todavia."
    if status == OWNER_VARIABLE_MISSING:
        return "Se que variable necesitas, pero no tengo evidencia viva suficiente."
    if status == OWNER_VARIABLE_UNMAPPED:
        return "No pude mapear la pregunta a una variable del Owner Pack."
    return "Revise la pregunta contra el Owner Pack."


def _supported_line(resolution: OwnerVariableResolution) -> str:
    return f"{resolution.label}: {_shorten(_value_text(resolution.value))}"


def _missing_line(resolution: OwnerVariableResolution) -> str:
    reason = resolution.missing_reason or "sin evidencia viva suficiente"
    return f"{resolution.label}: falta evidencia ({reason})."


def _conflict_line(resolution: OwnerVariableResolution) -> str:
    values = "; ".join(_shorten(_value_text(value), limit=120) for value in resolution.conflict_values)
    return f"{resolution.label}: fuentes en conflicto -> {values or 'sin valores comparables'}."


def _evidence_lines(resolutions: Sequence[OwnerVariableResolution]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for resolution in resolutions:
        for evidence in resolution.evidence:
            text = f"{resolution.label}: {evidence}"
            key = text.casefold()
            if key not in seen:
                seen.add(key)
                lines.append(text)
    return lines


def _canonical_source_lines(resolutions: Sequence[OwnerVariableResolution]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for resolution in resolutions:
        if resolution.evidence:
            continue
        for source in resolution.canonical_sources:
            text = f"Fuente esperada para {resolution.label}: {source}"
            key = text.casefold()
            if key not in seen:
                seen.add(key)
                lines.append(text)
    return lines


def _build_rendered_text(
    *,
    headline: str,
    short_answer: str,
    detail_lines: Sequence[str],
    evidence_lines: Sequence[str],
    missing_lines: Sequence[str],
    conflict_lines: Sequence[str],
    next_questions: Sequence[str],
) -> str:
    sections: list[str] = [headline, "", short_answer]
    if detail_lines:
        sections.extend(["", "Datos encontrados:"])
        sections.extend(f"- {line}" for line in detail_lines)
    if conflict_lines:
        sections.extend(["", "Conflictos:"])
        sections.extend(f"- {line}" for line in conflict_lines)
    if missing_lines:
        sections.extend(["", "Faltantes:"])
        sections.extend(f"- {line}" for line in missing_lines)
    if evidence_lines:
        sections.extend(["", "Evidencia:"])
        sections.extend(f"- {line}" for line in evidence_lines[:8])
    if next_questions:
        sections.extend(["", "Siguiente pregunta sugerida:"])
        sections.append(f"- {next_questions[0]}")
    sections.extend(["", "No ejecute cambios; esto es solo lectura."])
    return "\n".join(sections)


def render_owner_variable_query_answer(
    report: OwnerVariableQueryReport,
) -> OwnerVariableConversationalAnswer:
    """Render a variable query report into owner-facing Spanish."""

    supported = [item for item in report.resolutions if item.status == OWNER_VARIABLE_SUPPORTED]
    partial = [item for item in report.resolutions if item.status == OWNER_VARIABLE_PARTIAL]
    missing = [item for item in report.resolutions if item.status == OWNER_VARIABLE_MISSING]
    conflicts = [item for item in report.resolutions if item.status == OWNER_VARIABLE_CONFLICT]

    detail_lines = [_supported_line(item) for item in supported]
    if partial:
        detail_lines.extend(
            f"{item.label}: evidencia parcial; todavia no lo afirmo como cerrado."
            for item in partial
        )
    missing_lines = [_missing_line(item) for item in missing]
    conflict_lines = [_conflict_line(item) for item in conflicts]
    evidence_lines = _evidence_lines(report.resolutions)
    if not evidence_lines and (missing or partial):
        evidence_lines = _canonical_source_lines(report.resolutions)[:8]

    headline = _status_headline(report.status)
    if report.status == OWNER_VARIABLE_UNMAPPED:
        short_answer = (
            "No encontre una variable canonica que corresponda a esa pregunta. "
            "Prefiero pedir precision antes que inventar una respuesta."
        )
    elif supported and not missing and not conflicts and not partial:
        labels = ", ".join(item.label for item in supported)
        short_answer = f"La respuesta esta soportada para: {labels}."
    elif conflicts:
        short_answer = (
            "Hay mas de una fuente con valores distintos. Necesito conciliacion humana "
            "antes de presentar esto como dato del Owner Pack."
        )
    elif supported or partial:
        short_answer = (
            "Puedo contestar una parte, pero no todo el alcance de la pregunta esta "
            "cerrado con evidencia."
        )
    else:
        short_answer = (
            "La pregunta si corresponde al Owner Pack, pero hoy no hay evidencia viva "
            "suficiente para responderla."
        )

    rendered_text = _build_rendered_text(
        headline=headline,
        short_answer=short_answer,
        detail_lines=detail_lines,
        evidence_lines=evidence_lines,
        missing_lines=missing_lines,
        conflict_lines=conflict_lines,
        next_questions=report.next_questions,
    )

    safety = dict(report.safety_summary or {})
    safety.update(
        {
            "renderer_read_only": True,
            "renderer_infers_missing_values": False,
            "source_query_status": report.status,
        }
    )

    return OwnerVariableConversationalAnswer(
        answer_id=_answer_id(report),
        source_query_id=report.query_id,
        status=report.status,
        headline=headline,
        short_answer=short_answer,
        detail_lines=detail_lines,
        evidence_lines=list(evidence_lines),
        missing_lines=missing_lines,
        conflict_lines=conflict_lines,
        next_questions=list(report.next_questions),
        rendered_text=rendered_text,
        safety_summary=safety,
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_VARIABLE_ANSWER_ONLY,
        tone=ANSWER_TONE_OWNER_BRIEF,
    )


__all__ = [
    "ANSWER_TONE_OWNER_BRIEF",
    "OWNER_VARIABLE_ANSWER_ONLY",
    "OwnerVariableConversationalAnswer",
    "render_owner_variable_query_answer",
]
