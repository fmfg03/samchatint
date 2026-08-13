"""Renderer contract for specialist business previews.

This module defines the shape that UI/chat surfaces can render without knowing
agent internals. It is deliberately deterministic and read-only: it only formats
an already-built SpecialistBusinessDiffPreview.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Tuple

from .specialist_business_diff import SpecialistBusinessDiffPreview


SECTION_SUMMARY = "summary"
SECTION_PROPOSED_CHANGES = "proposed_changes"
SECTION_EVIDENCE = "evidence"
SECTION_MISSING_EVIDENCE = "missing_evidence"
SECTION_STEPS = "steps"
SECTION_CHECKS = "checks"
SECTION_AUTHORITY = "authority"


@dataclass(frozen=True)
class SpecialistPreviewSection:
    section_id: str
    title: str
    items: Tuple[Mapping[str, Any], ...]
    status: str = "info"

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["items"] = [dict(item) for item in self.items]
        return payload


@dataclass(frozen=True)
class SpecialistPreviewRender:
    preview_id: str
    task_id: str
    title: str
    preview_type: str
    sections: Tuple[SpecialistPreviewSection, ...]
    primary_action_label: str
    primary_action_enabled: bool
    blocked_reason: str
    execution_status: str
    audit_language: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preview_id": self.preview_id,
            "task_id": self.task_id,
            "title": self.title,
            "preview_type": self.preview_type,
            "sections": [section.to_dict() for section in self.sections],
            "primary_action_label": self.primary_action_label,
            "primary_action_enabled": self.primary_action_enabled,
            "blocked_reason": self.blocked_reason,
            "execution_status": self.execution_status,
            "audit_language": self.audit_language,
        }


def _summary_items(preview: SpecialistBusinessDiffPreview) -> Tuple[Mapping[str, Any], ...]:
    return (
        {"label": "Tipo", "value": preview.preview_type},
        {"label": "Tarea", "value": preview.task_id},
        {"label": "Objetivo", "value": preview.title},
    )


def _target_items(preview: SpecialistBusinessDiffPreview) -> Tuple[Mapping[str, Any], ...]:
    return tuple(
        {"label": str(key), "value": value}
        for key, value in sorted(dict(preview.target).items())
    )


def _change_items(preview: SpecialistBusinessDiffPreview) -> Tuple[Mapping[str, Any], ...]:
    return tuple(
        {
            "field": change.field,
            "value": change.proposed_value,
            "source": change.source,
            "evidence_id": change.evidence_id,
            "status": change.status,
            "reason": change.reason,
        }
        for change in preview.proposed_changes
    )


def _value_items(values: Tuple[str, ...], *, key: str) -> Tuple[Mapping[str, Any], ...]:
    return tuple({key: value} for value in values)


def render_specialist_business_preview(
    preview: SpecialistBusinessDiffPreview,
) -> SpecialistPreviewRender:
    missing_status = "warning" if preview.missing_evidence else "ok"
    sections = (
        SpecialistPreviewSection(
            section_id=SECTION_SUMMARY,
            title="Resumen",
            items=_summary_items(preview) + _target_items(preview),
        ),
        SpecialistPreviewSection(
            section_id=SECTION_PROPOSED_CHANGES,
            title="Cambios propuestos",
            items=_change_items(preview),
            status="ok" if preview.proposed_changes else "empty",
        ),
        SpecialistPreviewSection(
            section_id=SECTION_EVIDENCE,
            title="Evidencia encontrada",
            items=_value_items(preview.found_evidence, key="evidence_id"),
            status="ok" if preview.found_evidence else "empty",
        ),
        SpecialistPreviewSection(
            section_id=SECTION_MISSING_EVIDENCE,
            title="Evidencia faltante",
            items=_value_items(preview.missing_evidence, key="missing"),
            status=missing_status,
        ),
        SpecialistPreviewSection(
            section_id=SECTION_STEPS,
            title="Pasos propuestos",
            items=_value_items(preview.steps, key="step"),
            status="ok" if preview.steps else "empty",
        ),
        SpecialistPreviewSection(
            section_id=SECTION_CHECKS,
            title="Checks",
            items=_value_items(preview.checks, key="check"),
            status="ok" if preview.checks else "empty",
        ),
        SpecialistPreviewSection(
            section_id=SECTION_AUTHORITY,
            title="Autoridad",
            items=(
                {"label": "Aprobacion requerida", "value": preview.approval_required},
                {"label": "Ejecucion", "value": preview.execution_status},
                {"label": "Bloqueo", "value": preview.blocked_reason},
                {"label": "Writes intentados", "value": preview.writes_attempted},
                {"label": "Side effects", "value": preview.side_effects_detected},
            ),
            status="blocked" if preview.approval_required else "ok",
        ),
    )
    return SpecialistPreviewRender(
        preview_id=preview.preview_id,
        task_id=preview.task_id,
        title=preview.title,
        preview_type=preview.preview_type,
        sections=sections,
        primary_action_label="Aprobar y ejecutar",
        primary_action_enabled=False,
        blocked_reason=preview.blocked_reason,
        execution_status=preview.execution_status,
        audit_language=preview.audit_language,
    )


def render_specialist_business_preview_markdown(
    preview: SpecialistBusinessDiffPreview,
) -> str:
    rendered = render_specialist_business_preview(preview)
    lines = [
        f"# {rendered.title}",
        "",
        f"Tipo: {rendered.preview_type}",
        f"Ejecucion: {rendered.execution_status}",
        f"Accion principal habilitada: {rendered.primary_action_enabled}",
        "",
    ]
    for section in rendered.sections:
        lines.extend([f"## {section.title}", ""])
        if not section.items:
            lines.extend(["- none", ""])
            continue
        for item in section.items:
            if "field" in item:
                lines.append(
                    "- "
                    f"{item.get('field')}: {item.get('value')} "
                    f"[{item.get('status')}; evidence={item.get('evidence_id')}]"
                )
            elif len(item) == 1:
                key, value = next(iter(item.items()))
                lines.append(f"- {key}: {value}")
            else:
                label = item.get("label") or item.get("key") or "item"
                lines.append(f"- {label}: {item.get('value')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
