"""Navigable read-only dashboard model for Owner Pack readiness.

Slice 2B is intentionally presentation-facing but side-effect free: it reshapes
readiness evidence into cards a human can scan quickly. It does not query new
systems, create folders, write files, or grant authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .business_diff_preview import NOT_EXECUTED
from .owner_pack_readiness import (
    OWNER_PACK_NEEDS_TARGET,
    OWNER_PACK_READINESS_ONLY,
    OwnerPackReadinessReport,
    OwnerPackReadinessSurface,
)

OWNER_PACK_READINESS_DASHBOARD_ONLY = "owner_pack_readiness_dashboard_only"
OWNER_PACK_DASHBOARD_SECTIONS = (
    "tournament_context",
    "entity_folder",
    "national_phase_folder",
    "marketing_activation_report",
)
_OWNER_PACK_SURFACE_ORDER = {
    surface_id: index for index, surface_id in enumerate(OWNER_PACK_DASHBOARD_SECTIONS)
}
_OWNER_PACK_SURFACE_LABELS = {
    "tournament_context": "Torneo / contexto",
    "entity_folder": "Entity folder",
    "national_phase_folder": "National phase",
    "marketing_activation_report": "Marketing",
}


@dataclass(frozen=True)
class OwnerPackReadinessDashboardCard:
    section_id: str
    label: str
    status: str
    coverage_score: int
    field_count: int = 0
    supported_field_count: int = 0
    missing_field_count: int = 0
    available_sources: list[str] = field(default_factory=list)
    missing_items: list[str] = field(default_factory=list)
    next_action: str = ""
    next_questions: list[str] = field(default_factory=list)
    source_anchor: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerPackReadinessDashboard:
    dashboard_id: str
    headline: str
    summary: str
    target: dict[str, Any] = field(default_factory=dict)
    overall_status: str = ""
    coverage_score: int = 0
    cards: list[OwnerPackReadinessDashboardCard] = field(default_factory=list)
    next_questions: list[str] = field(default_factory=list)
    source_reports: list[str] = field(default_factory=list)
    safety_summary: dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_PACK_READINESS_DASHBOARD_ONLY
    source_readiness_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cards"] = [card.to_dict() for card in self.cards]
        return payload


def _limit(items: Sequence[str], *, max_items: int = 8) -> list[str]:
    clean = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in clean:
            clean.append(text)
        if len(clean) >= max_items:
            break
    return clean


def _target_value(target: Mapping[str, Any], key: str) -> str:
    return str(target.get(key) or "").strip()


def _tournament_context_card(report: OwnerPackReadinessReport) -> OwnerPackReadinessDashboardCard:
    tournament_slug = _target_value(report.target, "tournament_slug")
    scope = _target_value(report.target, "scope") or "all"
    entity_name = _target_value(report.target, "entity_name")
    missing = [] if tournament_slug else ["Torneo objetivo"]
    sources = [f"torneo: {tournament_slug}"] if tournament_slug else []
    if scope:
        sources.append(f"scope: {scope}")
    if entity_name:
        sources.append(f"entidad: {entity_name}")
    return OwnerPackReadinessDashboardCard(
        section_id="tournament_context",
        label=_OWNER_PACK_SURFACE_LABELS["tournament_context"],
        status="target_supplied" if tournament_slug else OWNER_PACK_NEEDS_TARGET,
        coverage_score=100 if tournament_slug else 0,
        field_count=1,
        supported_field_count=1 if tournament_slug else 0,
        missing_field_count=len(missing),
        available_sources=sources,
        missing_items=missing,
        next_action=(
            "Usar este torneo como contexto para evaluar carpetas y superficies."
            if tournament_slug
            else "Indicar el torneo que el dueno quiere revisar."
        ),
        next_questions=[] if tournament_slug else ["De que torneo quieres revisar el Owner Pack?"],
        source_anchor="target",
    )


def _surface_card(surface: OwnerPackReadinessSurface) -> OwnerPackReadinessDashboardCard:
    sources = [*surface.workspace_files_found, *surface.evidence_found]
    return OwnerPackReadinessDashboardCard(
        section_id=surface.surface_id,
        label=_OWNER_PACK_SURFACE_LABELS.get(surface.surface_id, surface.label),
        status=surface.status,
        coverage_score=int(surface.readiness_score or 0),
        field_count=int(surface.field_count or 0),
        supported_field_count=int(surface.supported_field_count or 0),
        missing_field_count=int(surface.missing_field_count or 0),
        available_sources=_limit(sources),
        missing_items=_limit(surface.missing_evidence),
        next_action=surface.next_action,
        next_questions=[],
        source_anchor=f"surface:{surface.surface_id}",
    )


def _not_evaluated_surface_card(section_id: str) -> OwnerPackReadinessDashboardCard:
    label = _OWNER_PACK_SURFACE_LABELS.get(section_id, section_id)
    return OwnerPackReadinessDashboardCard(
        section_id=section_id,
        label=label,
        status="not_evaluated_in_scope",
        coverage_score=0,
        field_count=0,
        supported_field_count=0,
        missing_field_count=0,
        available_sources=[],
        missing_items=["No evaluado en el scope actual"],
        next_action=f"Cambiar scope o seleccionar {label} para revisar esta superficie.",
        next_questions=[f"Quieres revisar {label}?"],
        source_anchor=f"surface:{section_id}",
    )


def build_owner_pack_readiness_dashboard(
    report: OwnerPackReadinessReport,
) -> OwnerPackReadinessDashboard:
    """Build a minimal navigable Owner Pack readiness dashboard from a report."""

    surface_cards_by_id = {
        surface.surface_id: _surface_card(surface)
        for surface in report.surfaces
        if surface.surface_id in _OWNER_PACK_SURFACE_ORDER
    }
    cards = []
    for section_id in OWNER_PACK_DASHBOARD_SECTIONS:
        if section_id == "tournament_context":
            cards.append(_tournament_context_card(report))
        else:
            cards.append(
                surface_cards_by_id.get(section_id)
                or _not_evaluated_surface_card(section_id)
            )
    missing_by_card = sum(card.missing_field_count for card in cards)
    supported_by_card = sum(card.supported_field_count for card in cards)
    total_by_card = sum(card.field_count for card in cards)
    coverage = round((supported_by_card / total_by_card) * 100) if total_by_card else 0

    summary = report.summary
    if missing_by_card:
        summary = (
            f"{summary} Vista navegable: {supported_by_card}/{total_by_card} campos "
            f"soportados y {missing_by_card} faltantes visibles."
        )

    return OwnerPackReadinessDashboard(
        dashboard_id="owner_pack_readiness_dashboard_v1",
        headline="Owner Pack Readiness Dashboard",
        summary=summary,
        target=dict(report.target or {}),
        overall_status=report.status,
        coverage_score=coverage,
        cards=cards,
        next_questions=_limit(report.next_questions, max_items=6),
        source_reports=list(report.source_reports),
        safety_summary={
            **dict(report.safety_summary or {}),
            "read_only_dashboard": True,
            "writes_enabled": False,
            "source_readiness_id": report.readiness_id,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_PACK_READINESS_DASHBOARD_ONLY,
        source_readiness_id=report.readiness_id,
    )


def owner_pack_readiness_dashboard_contains_execution_claim(
    dashboard: OwnerPackReadinessDashboard,
) -> bool:
    payload = dashboard.to_dict()
    text = str(payload).lower().replace(NOT_EXECUTED, "")
    unsafe_terms = (
        "creado",
        "actualizado",
        "ejecutado",
        "enviado",
        "publicado",
        "created",
        "updated",
        "executed",
        "published",
        "sent notification",
    )
    return any(term in text for term in unsafe_terms)


__all__ = [
    "OWNER_PACK_DASHBOARD_SECTIONS",
    "OWNER_PACK_READINESS_DASHBOARD_ONLY",
    "OwnerPackReadinessDashboard",
    "OwnerPackReadinessDashboardCard",
    "build_owner_pack_readiness_dashboard",
    "owner_pack_readiness_dashboard_contains_execution_claim",
]
