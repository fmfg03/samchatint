"""Read-only Owner Pack readiness aggregation for the assistant.

This module composes the existing Owner Pack status, inventory and live
workspace snapshot reports into one assistant-facing readiness answer. It does
not query databases, write files, generate folders, send notifications or grant
authority. It only states what is prepared, what evidence exists, what is
missing, and which human-safe next step should happen next.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .business_diff_preview import NOT_EXECUTED
from .owner_pack_inventory import (
    OwnerPackInventoryReport,
    OwnerPackInventorySurface,
    build_owner_pack_inventory_report,
)
from .owner_pack_live_snapshot import (
    OwnerPackLiveSnapshotReport,
    build_owner_pack_live_snapshot_report,
)
from .owner_pack_status import OwnerPackStatusReport


OWNER_PACK_READINESS_ONLY = "owner_pack_readiness_only"
OWNER_PACK_READY_FOR_REVIEW = "ready_for_readonly_review"
OWNER_PACK_PARTIAL_LIVE_EVIDENCE = "partial_live_evidence"
OWNER_PACK_SCHEMA_ONLY = "schema_only_no_live_evidence"
OWNER_PACK_NEEDS_TARGET = "needs_target_context"
OWNER_PACK_NO_CONTRACT = "no_contract_for_surface"

LIVE_SURFACES = (
    "entity_folder",
    "national_phase_folder",
    "marketing_activation_report",
)


@dataclass(frozen=True)
class OwnerPackReadinessSurface:
    surface_id: str
    label: str
    status: str
    readiness_score: int
    field_count: int
    supported_field_count: int = 0
    missing_field_count: int = 0
    contract_prepared: bool = True
    live_lookup_performed: bool = False
    workspace_files_found: List[str] = field(default_factory=list)
    evidence_found: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    next_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerPackReadinessReport:
    readiness_id: str
    headline: str
    summary: str
    target: Dict[str, Any] = field(default_factory=dict)
    status: str = OWNER_PACK_SCHEMA_ONLY
    readiness_score: int = 0
    surfaces: List[OwnerPackReadinessSurface] = field(default_factory=list)
    evidence_found: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    next_actions: List[str] = field(default_factory=list)
    next_questions: List[str] = field(default_factory=list)
    source_reports: List[str] = field(default_factory=list)
    safety_summary: Dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_PACK_READINESS_ONLY

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["surfaces"] = [surface.to_dict() for surface in self.surfaces]
        return payload


def _surface_by_id(
    surfaces: Iterable[Any],
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for surface in surfaces:
        surface_id = getattr(surface, "surface_id", None)
        if surface_id:
            result[str(surface_id)] = surface
    return result


def _supported_evidence_from_live(report: OwnerPackLiveSnapshotReport) -> List[str]:
    found: List[str] = []
    for surface in report.surfaces:
        for field_item in surface.fields:
            if field_item.status == "supported_by_live_workspace":
                source = ", ".join(field_item.source_paths or field_item.source_files)
                found.append(f"{field_item.label}: {source or field_item.evidence_type}")
    return sorted(set(found))


def _missing_evidence_from_live(report: OwnerPackLiveSnapshotReport) -> List[str]:
    missing: List[str] = []
    for surface in report.surfaces:
        for field_item in surface.fields:
            if field_item.status != "supported_by_live_workspace":
                missing.append(f"{field_item.label} ({field_item.evidence_type})")
    return sorted(set(missing))


def _status_for_counts(
    *,
    field_count: int,
    supported_count: int,
    missing_count: int,
    live_lookup_performed: bool,
    needs_target: bool = False,
) -> tuple[str, int]:
    if needs_target:
        return OWNER_PACK_NEEDS_TARGET, 0
    if field_count <= 0:
        return OWNER_PACK_NO_CONTRACT, 0
    if not live_lookup_performed:
        return OWNER_PACK_SCHEMA_ONLY, 0
    score = round((supported_count / field_count) * 100) if field_count else 0
    if missing_count == 0 and supported_count > 0:
        return OWNER_PACK_READY_FOR_REVIEW, 100
    return OWNER_PACK_PARTIAL_LIVE_EVIDENCE, max(0, min(99, score))


def _default_next_action(status: str, label: str) -> str:
    if status == OWNER_PACK_READY_FOR_REVIEW:
        return f"Revisar {label} con usuario y preparar preview durable solo con aprobacion."
    if status == OWNER_PACK_PARTIAL_LIVE_EVIDENCE:
        return f"Completar evidencia faltante de {label} antes de presentarla como carpeta completa."
    if status == OWNER_PACK_NEEDS_TARGET:
        return f"Pedir entidad/persona objetivo antes de evaluar {label}."
    if status == OWNER_PACK_NO_CONTRACT:
        return f"Definir contrato de campos para {label} antes de consultar evidencia."
    return f"Conectar evidencia viva de {label}; hoy solo existe contrato/schema."


def _surface_from_inventory(
    inventory_surface: OwnerPackInventorySurface,
    *,
    status_surface: Any = None,
    live_report: Optional[OwnerPackLiveSnapshotReport] = None,
    needs_target: bool = False,
) -> OwnerPackReadinessSurface:
    field_count = inventory_surface.field_count
    supported_count = 0
    missing_count = field_count
    files_found: List[str] = []
    evidence_found: List[str] = []
    missing_evidence: List[str] = []
    live_lookup = live_report is not None

    if live_report and live_report.surfaces:
        live_surface = live_report.surfaces[0]
        supported_count = live_surface.supported_field_count
        missing_count = live_surface.missing_field_count
        files_found = list(live_surface.workspace_files_found)
        evidence_found = _supported_evidence_from_live(live_report)
        missing_evidence = _missing_evidence_from_live(live_report)
    elif status_surface is not None:
        missing_evidence = list(getattr(status_surface, "missing_evidence", []) or [])

    status, score = _status_for_counts(
        field_count=field_count,
        supported_count=supported_count,
        missing_count=missing_count,
        live_lookup_performed=live_lookup,
        needs_target=needs_target,
    )
    next_action = getattr(status_surface, "next_action", "") if status_surface else ""
    if status != OWNER_PACK_SCHEMA_ONLY or not next_action:
        next_action = _default_next_action(status, inventory_surface.label)

    return OwnerPackReadinessSurface(
        surface_id=inventory_surface.surface_id,
        label=inventory_surface.label,
        status=status,
        readiness_score=score,
        field_count=field_count,
        supported_field_count=supported_count,
        missing_field_count=missing_count,
        contract_prepared=True,
        live_lookup_performed=live_lookup,
        workspace_files_found=files_found,
        evidence_found=evidence_found,
        missing_evidence=missing_evidence,
        next_action=next_action,
    )


def _global_status(surfaces: Sequence[OwnerPackReadinessSurface]) -> str:
    if not surfaces:
        return OWNER_PACK_NO_CONTRACT
    statuses = {surface.status for surface in surfaces}
    if statuses == {OWNER_PACK_READY_FOR_REVIEW}:
        return OWNER_PACK_READY_FOR_REVIEW
    if OWNER_PACK_PARTIAL_LIVE_EVIDENCE in statuses or OWNER_PACK_READY_FOR_REVIEW in statuses:
        return OWNER_PACK_PARTIAL_LIVE_EVIDENCE
    if OWNER_PACK_NEEDS_TARGET in statuses:
        return OWNER_PACK_NEEDS_TARGET
    return OWNER_PACK_SCHEMA_ONLY


def _next_questions(
    *,
    target: Mapping[str, Any],
    surfaces: Sequence[OwnerPackReadinessSurface],
) -> List[str]:
    questions: List[str] = []
    if not target.get("tournament_slug"):
        questions.append("De que torneo quieres revisar el Owner Pack?")
    if any(surface.status == OWNER_PACK_NEEDS_TARGET for surface in surfaces):
        questions.append("Que entidad/operador debemos revisar para la carpeta por entidad?")
    if any(surface.missing_evidence for surface in surfaces):
        questions.append("Quieres que priorice los faltantes bloqueantes o que muestre fuentes por seccion?")
    if not questions:
        questions.append("Quieres que prepare el preview durable para aprobacion humana?")
    return questions


def build_owner_pack_readiness_report(
    *,
    status_report: OwnerPackStatusReport,
    inventory_report: OwnerPackInventoryReport,
    live_reports: Sequence[OwnerPackLiveSnapshotReport] = (),
    target: Optional[Mapping[str, Any]] = None,
    missing_target_surfaces: Sequence[str] = (),
) -> OwnerPackReadinessReport:
    """Compose existing Owner Pack reports into one assistant readiness answer."""

    target_payload = dict(target or {})
    status_by_surface = _surface_by_id(status_report.surfaces)
    live_by_surface: Dict[str, OwnerPackLiveSnapshotReport] = {}
    for report in live_reports:
        if report.surfaces:
            live_by_surface[report.surfaces[0].surface_id] = report

    surfaces: List[OwnerPackReadinessSurface] = []
    for inventory_surface in inventory_report.surfaces:
        surfaces.append(
            _surface_from_inventory(
                inventory_surface,
                status_surface=status_by_surface.get(inventory_surface.surface_id),
                live_report=live_by_surface.get(inventory_surface.surface_id),
                needs_target=inventory_surface.surface_id in set(missing_target_surfaces),
            )
        )

    evidence_found = sorted({item for surface in surfaces for item in surface.evidence_found})
    missing_evidence = sorted({item for surface in surfaces for item in surface.missing_evidence})
    total_fields = sum(surface.field_count for surface in surfaces)
    total_supported = sum(surface.supported_field_count for surface in surfaces)
    status = _global_status(surfaces)
    score = round((total_supported / total_fields) * 100) if total_fields else 0
    if status == OWNER_PACK_READY_FOR_REVIEW:
        score = 100
    elif status in {OWNER_PACK_SCHEMA_ONLY, OWNER_PACK_NEEDS_TARGET} and not total_supported:
        score = 0

    summary = (
        "Owner Pack esta preparado como contrato read-only, pero falta evidencia viva para declararlo completo."
    )
    if status == OWNER_PACK_READY_FOR_REVIEW:
        summary = "Owner Pack tiene evidencia viva para las superficies solicitadas y queda listo para revision humana read-only."
    elif status == OWNER_PACK_PARTIAL_LIVE_EVIDENCE:
        summary = "Owner Pack tiene evidencia viva parcial; los faltantes siguen bloqueando cualquier claim completo."
    elif status == OWNER_PACK_NEEDS_TARGET:
        summary = "Owner Pack requiere contexto objetivo adicional antes de evaluar evidencia viva."

    return OwnerPackReadinessReport(
        readiness_id="owner_pack_readiness_v1",
        headline="Readiness del Owner Pack del dueno",
        summary=summary,
        target=target_payload,
        status=status,
        readiness_score=score,
        surfaces=surfaces,
        evidence_found=evidence_found,
        missing_evidence=missing_evidence,
        next_actions=[surface.next_action for surface in surfaces],
        next_questions=_next_questions(target=target_payload, surfaces=surfaces),
        source_reports=[
            status_report.status_id,
            inventory_report.inventory_id,
            *[report.snapshot_id for report in live_reports],
        ],
        safety_summary={
            "writes_enabled": False,
            "write_handlers_invoked": 0,
            "approval_required_for_durable_outputs": True,
            "memory_or_precedent_grants_authority": False,
            "complete_claim_allowed": status == OWNER_PACK_READY_FOR_REVIEW,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_PACK_READINESS_ONLY,
    )


def build_owner_pack_readiness_from_scope(
    *,
    status_report: OwnerPackStatusReport,
    scope: str = "all",
    tournament_slug: str = "",
    entity_name: str | None = None,
    root_dir: Any = None,
    extra_live_reports: Sequence[OwnerPackLiveSnapshotReport] = (),
) -> OwnerPackReadinessReport:
    """Build readiness for a requested scope, optionally using live workspace evidence."""

    requested_scope = (scope or "all").strip() or "all"
    scopes = None if requested_scope == "all" else (requested_scope,)
    inventory = build_owner_pack_inventory_report(scopes=scopes)
    live_reports: List[OwnerPackLiveSnapshotReport] = list(extra_live_reports or [])
    missing_targets: List[str] = []
    target = {
        "scope": requested_scope,
        "tournament_slug": tournament_slug or None,
        "entity_name": entity_name or None,
    }

    if tournament_slug:
        for surface in inventory.surfaces:
            if surface.surface_id not in LIVE_SURFACES:
                continue
            if surface.surface_id == "entity_folder" and not entity_name:
                missing_targets.append(surface.surface_id)
                continue
            if surface.surface_id in {
                report.surfaces[0].surface_id
                for report in live_reports
                if report.surfaces
            }:
                continue
            live_reports.append(
                build_owner_pack_live_snapshot_report(
                    surface_id=surface.surface_id,
                    tournament_slug=tournament_slug,
                    entity_name=entity_name,
                    root_dir=root_dir,
                )
            )
    elif requested_scope in LIVE_SURFACES or requested_scope == "all":
        # Without a tournament we can only report schema readiness.
        pass

    return build_owner_pack_readiness_report(
        status_report=status_report,
        inventory_report=inventory,
        live_reports=live_reports,
        target=target,
        missing_target_surfaces=missing_targets,
    )


def owner_pack_readiness_contains_execution_claim(report: OwnerPackReadinessReport) -> bool:
    payload = report.to_dict()
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
    "OWNER_PACK_NEEDS_TARGET",
    "OWNER_PACK_NO_CONTRACT",
    "OWNER_PACK_PARTIAL_LIVE_EVIDENCE",
    "OWNER_PACK_READINESS_ONLY",
    "OWNER_PACK_READY_FOR_REVIEW",
    "OWNER_PACK_SCHEMA_ONLY",
    "OwnerPackReadinessReport",
    "OwnerPackReadinessSurface",
    "build_owner_pack_readiness_from_scope",
    "build_owner_pack_readiness_report",
    "owner_pack_readiness_contains_execution_claim",
]
