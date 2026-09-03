"""Conversational renderer for Owner Pack readiness reports.

The readiness report is the source of truth. This module only renders it into
owner-facing Spanish so the assistant does not leak raw tool JSON or imply that
folders/actions were executed.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from .business_diff_preview import NOT_EXECUTED
from .owner_pack_readiness import (
    OWNER_PACK_NEEDS_TARGET,
    OWNER_PACK_PARTIAL_LIVE_EVIDENCE,
    OWNER_PACK_READY_FOR_REVIEW,
    OwnerPackReadinessReport,
)


OWNER_PACK_READINESS_ANSWER_ONLY = "owner_pack_readiness_answer_only"
ANSWER_TONE_OWNER_READINESS = "owner_readiness_brief"


@dataclass(frozen=True)
class OwnerPackReadinessConversationalAnswer:
    """Human-readable, no-write answer derived from a readiness report."""

    answer_id: str
    source_readiness_id: str
    status: str
    headline: str
    short_answer: str
    detail_lines: list[str] = field(default_factory=list)
    evidence_lines: list[str] = field(default_factory=list)
    missing_lines: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    next_questions: list[str] = field(default_factory=list)
    rendered_text: str = ""
    safety_summary: dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_PACK_READINESS_ANSWER_ONLY
    tone: str = ANSWER_TONE_OWNER_READINESS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _answer_id(report: OwnerPackReadinessReport) -> str:
    key = f"{report.readiness_id}|{report.status}|{report.readiness_score}|{report.target}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"opra_{digest}"


def _target_line(report: OwnerPackReadinessReport) -> str | None:
    target = report.target or {}
    target_bits: list[str] = []
    if target.get("tournament_slug"):
        target_bits.append(f"torneo={target['tournament_slug']}")
    if target.get("entity_name"):
        target_bits.append(f"entidad={target['entity_name']}")
    if target.get("scope"):
        target_bits.append(f"scope={target['scope']}")
    if not target_bits:
        return None
    return "Objetivo revisado: " + " - ".join(target_bits)


def _surface_lines(report: OwnerPackReadinessReport) -> list[str]:
    lines: list[str] = []
    for surface in report.surfaces[:6]:
        label = {
            "Entity folder": "Carpeta por entidad",
            "National phase": "Fase nacional",
        }.get(surface.label, surface.label)
        lines.append(
            f"{label}: {surface.status} "
            f"({surface.supported_field_count}/{surface.field_count} campos respaldados)"
        )
    return lines


def _missing_lines(report: OwnerPackReadinessReport) -> list[str]:
    if report.missing_evidence:
        return list(report.missing_evidence[:12])
    if report.status == OWNER_PACK_NEEDS_TARGET:
        return ["Falta indicar la entidad/operador objetivo."]
    return ["Sin faltantes detectados en el alcance solicitado."]


def _short_answer(report: OwnerPackReadinessReport) -> str:
    if report.status == OWNER_PACK_READY_FOR_REVIEW:
        return "El Owner Pack puede presentarse como vista segura para revision humana."
    if report.status == OWNER_PACK_PARTIAL_LIVE_EVIDENCE:
        return (
            "El Owner Pack ya tiene avance y evidencia viva parcial, pero todavia "
            "no debe presentarse como completo."
        )
    if report.status == OWNER_PACK_NEEDS_TARGET:
        return (
            "El Owner Pack esta preparado como contrato, pero falta indicar el "
            "objetivo exacto antes de evaluar evidencia viva."
        )
    return (
        "El Owner Pack esta preparado como contrato, pero falta "
        "evidencia viva para declararlo listo."
    )


def _headline_for_report(report: OwnerPackReadinessReport) -> str:
    headline = str(report.headline or "").strip()
    if not headline or "readiness" in headline.casefold():
        return "Estado ejecutivo del Owner Pack"
    return headline


def _executive_visible_text(value: str) -> str:
    replacements = {
        "preview read-only": "vista segura",
        "schema read-only": "contrato preparado",
        "contrato read-only": "contrato preparado",
        "diagnostico read-only": "diagnostico para revisión",
        "read-only": "segura",
        "Readiness": "Estado",
        "readiness": "cobertura",
        "Entity folder": "Carpeta por entidad",
        "National phase": "Fase nacional",
    }
    text = str(value or "")
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _rendered_text(
    *,
    headline: str,
    summary: str,
    status_line: str,
    target_line: str | None,
    detail_lines: Sequence[str],
    evidence_lines: Sequence[str],
    missing_lines: Sequence[str],
    next_actions: Sequence[str],
    next_questions: Sequence[str],
    short_answer: str,
) -> str:
    lines: list[str] = [
        _executive_visible_text(headline),
        "",
        _executive_visible_text(summary),
        "",
        _executive_visible_text(status_line),
        "",
        _executive_visible_text(short_answer),
    ]
    if target_line:
        lines.extend(["", _executive_visible_text(target_line)])

    lines.extend(["", "Secciones revisadas:"])
    lines.extend(f"- {_executive_visible_text(line)}" for line in detail_lines)

    lines.extend(["", "Evidencia encontrada:"])
    if evidence_lines:
        lines.extend(f"- {_executive_visible_text(line)}" for line in evidence_lines[:8])
    else:
        lines.append("- Aun no hay evidencia viva suficiente; solo contrato preparado.")

    lines.extend(["", "Faltantes para poder contestar sin inventar:"])
    lines.extend(f"- {_executive_visible_text(line)}" for line in missing_lines)

    if next_actions:
        lines.extend(["", "Siguiente paso seguro:"])
        lines.extend(f"- {_executive_visible_text(line)}" for line in next_actions[:4])
    if next_questions:
        lines.extend(["", "Pregunta mínima para avanzar:"])
        lines.extend(f"- {_executive_visible_text(line)}" for line in next_questions[:3])

    lines.extend(
        [
            "",
            "Límite de la vista: esto no crea carpetas, no modifica datos, "
            "no manda mensajes y no autoriza nada. Es diagnostico para revisión; "
            "cualquier salida durable requiere aprobacion humana.",
        ]
    )
    return "\n".join(lines)


def render_owner_pack_readiness_answer(
    report: OwnerPackReadinessReport,
) -> OwnerPackReadinessConversationalAnswer:
    """Render a readiness report into owner-facing Spanish."""

    headline = _headline_for_report(report)
    status_line = f"Estado: {report.status} - cobertura {report.readiness_score}%"
    detail_lines = _surface_lines(report)
    evidence_lines = list(report.evidence_found[:8])
    missing_lines = _missing_lines(report)
    short_answer = _short_answer(report)
    target_line = _target_line(report)
    rendered = _rendered_text(
        headline=headline,
        summary=report.summary,
        status_line=status_line,
        target_line=target_line,
        detail_lines=detail_lines,
        evidence_lines=evidence_lines,
        missing_lines=missing_lines,
        next_actions=report.next_actions,
        next_questions=report.next_questions,
        short_answer=short_answer,
    )
    safety = dict(report.safety_summary or {})
    safety.update(
        {
            "renderer_read_only": True,
            "renderer_infers_missing_values": False,
            "source_readiness_status": report.status,
        }
    )
    return OwnerPackReadinessConversationalAnswer(
        answer_id=_answer_id(report),
        source_readiness_id=report.readiness_id,
        status=report.status,
        headline=headline,
        short_answer=short_answer,
        detail_lines=detail_lines,
        evidence_lines=evidence_lines,
        missing_lines=missing_lines,
        next_actions=list(report.next_actions),
        next_questions=list(report.next_questions),
        rendered_text=rendered,
        safety_summary=safety,
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_PACK_READINESS_ANSWER_ONLY,
        tone=ANSWER_TONE_OWNER_READINESS,
    )


__all__ = [
    "ANSWER_TONE_OWNER_READINESS",
    "OWNER_PACK_READINESS_ANSWER_ONLY",
    "OwnerPackReadinessConversationalAnswer",
    "render_owner_pack_readiness_answer",
]
