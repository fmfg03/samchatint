"""Read-only Owner Entity Folder Workspace composition.

This module composes the existing live entity dossier and Owner Pack readiness
artifacts into one assistant-facing workspace. It is intentionally inert: it
only prepares cards, sections, evidence, missing fields, questions and a preview
boundary. It does not create folders, export files, notify anyone or mutate
business state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .business_diff_preview import NOT_EXECUTED
from .owner_entity_dossier_live import (
    OwnerEntityDossierLiveReport,
    build_owner_entity_dossier_live_from_tournament_source,
)
from .owner_pack_readiness import (
    OWNER_PACK_NEEDS_TARGET,
    OWNER_PACK_PARTIAL_LIVE_EVIDENCE,
    OWNER_PACK_READY_FOR_REVIEW,
    OwnerPackReadinessReport,
    build_owner_pack_readiness_from_scope,
)
from .owner_pack_status import OwnerPackStatusReport
from .soul_wizard import build_soul_wizard_owner_pack_bridge

OWNER_ENTITY_FOLDER_WORKSPACE_ONLY = "owner_entity_folder_workspace_only"
WORKSPACE_READY_FOR_REVIEW = "ready_for_readonly_review"
WORKSPACE_PARTIAL = "partial_live_evidence"
WORKSPACE_NEEDS_TARGET = "needs_target_context"
WORKSPACE_NO_LIVE_EVIDENCE = "no_live_evidence"

_OPERATIONS_KEYWORDS = (
    "operation",
    "operacion",
    "operaciones",
    "team",
    "equipo",
    "jugador",
    "categoria",
    "genero",
    "rama",
    "ronda",
    "fase estatal",
    "fase nacional",
    "torneo",
    "municipio",
    "estado",
    "viaje",
    "clasificacion",
    "contacto",
    "responsable",
)

_FINANCE_KEYWORDS = (
    "finance",
    "finanza",
    "pago",
    "pagos",
    "monto",
    "ayuda",
    "transfer",
    "costo",
    "gasto",
    "gastos",
    "proveedor",
    "hotel",
    "hospedaje",
    "seguro",
    "medico",
    "uniforme",
    "balon",
    "equipamiento",
    "utileria",
)


@dataclass(frozen=True)
class OwnerEntityFolderWorkspaceCard:
    card_id: str
    title: str
    status: str
    summary: str
    items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerEntityFolderWorkspaceSection:
    section_id: str
    title: str
    status: str
    supported: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerEntityFolderWorkspace:
    workspace_id: str
    headline: str
    summary: str
    status: str
    target: dict[str, Any] = field(default_factory=dict)
    workspace_cards: list[OwnerEntityFolderWorkspaceCard] = field(default_factory=list)
    folder_sections: list[OwnerEntityFolderWorkspaceSection] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    non_claims: list[str] = field(default_factory=list)
    next_questions: list[str] = field(default_factory=list)
    preview: dict[str, Any] = field(default_factory=dict)
    source_reports: list[str] = field(default_factory=list)
    safety_summary: dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_ENTITY_FOLDER_WORKSPACE_ONLY

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["workspace_cards"] = [card.to_dict() for card in self.workspace_cards]
        payload["folder_sections"] = [section.to_dict() for section in self.folder_sections]
        return payload


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        raw = value.to_dict()
        return dict(raw) if isinstance(raw, Mapping) else {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _dedupe_str(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _safe_str(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _matches_any(text: Any, keywords: Sequence[str]) -> bool:
    normalized = _safe_str(text).casefold()
    return any(keyword.casefold() in normalized for keyword in keywords)


def _section_status(supported: Sequence[str], missing: Sequence[str]) -> str:
    if supported and missing:
        return WORKSPACE_PARTIAL
    if supported:
        return "supported"
    if missing:
        return "missing"
    return WORKSPACE_NO_LIVE_EVIDENCE


def _bucket_section(
    *,
    section_id: str,
    title: str,
    keywords: Sequence[str],
    base_sections: Sequence[OwnerEntityFolderWorkspaceSection],
    global_evidence: Sequence[str],
    global_missing: Sequence[str],
) -> OwnerEntityFolderWorkspaceSection:
    supported: list[str] = []
    missing: list[str] = []
    evidence: list[str] = []
    for section in base_sections:
        for item in section.supported:
            if _matches_any(item, keywords):
                supported.append(item)
        for item in section.missing:
            if _matches_any(item, keywords):
                missing.append(item)
        for item in section.evidence:
            if _matches_any(item, keywords):
                evidence.append(item)

    for item in global_evidence:
        if _matches_any(item, keywords):
            supported.append(item)
            evidence.append(item)
    for item in global_missing:
        if _matches_any(item, keywords):
            missing.append(item)

    supported = _dedupe_str(supported)
    missing = _dedupe_str(missing)
    evidence = _dedupe_str(evidence)
    return OwnerEntityFolderWorkspaceSection(
        section_id=section_id,
        title=title,
        status=_section_status(supported, missing),
        supported=supported,
        missing=missing,
        evidence=evidence,
    )


def _operational_folder_sections(
    *,
    base_sections: Sequence[OwnerEntityFolderWorkspaceSection],
    evidence: Sequence[str],
    missing: Sequence[str],
) -> list[OwnerEntityFolderWorkspaceSection]:
    """Return owner-facing folder drawers before raw diagnostic sections.

    This is a conservative re-bucketing of already-discovered fields. It does
    not infer new facts; it only makes the workspace legible as Operaciones and
    Finanzas for human review.
    """

    operations = _bucket_section(
        section_id="operations",
        title="Operaciones",
        keywords=_OPERATIONS_KEYWORDS,
        base_sections=base_sections,
        global_evidence=evidence,
        global_missing=missing,
    )
    finance = _bucket_section(
        section_id="finance",
        title="Finanzas",
        keywords=_FINANCE_KEYWORDS,
        base_sections=base_sections,
        global_evidence=evidence,
        global_missing=missing,
    )
    return [operations, finance]


def _workspace_id(target: Mapping[str, Any], evidence: Sequence[str], missing: Sequence[str]) -> str:
    canonical = json.dumps(
        {"target": dict(target), "evidence": list(evidence), "missing": list(missing)},
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )
    return "oefw_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _status(readiness: Mapping[str, Any], dossier: Mapping[str, Any]) -> str:
    readiness_status = _safe_str(readiness.get("status"))
    dossier_status = _safe_str(dossier.get("status"))
    if readiness_status == OWNER_PACK_NEEDS_TARGET:
        return WORKSPACE_NEEDS_TARGET
    if readiness_status == OWNER_PACK_READY_FOR_REVIEW and dossier_status in {"usable", "partial_aggregate_only", "partial"}:
        return WORKSPACE_READY_FOR_REVIEW
    if readiness_status == OWNER_PACK_PARTIAL_LIVE_EVIDENCE or dossier_status.startswith("partial"):
        return WORKSPACE_PARTIAL
    return WORKSPACE_NO_LIVE_EVIDENCE


def _readiness_sections(readiness: Mapping[str, Any]) -> list[OwnerEntityFolderWorkspaceSection]:
    sections: list[OwnerEntityFolderWorkspaceSection] = []
    for surface in _as_list(readiness.get("surfaces")):
        surface_dict = _as_dict(surface)
        sections.append(
            OwnerEntityFolderWorkspaceSection(
                section_id=_safe_str(surface_dict.get("surface_id")) or "owner_pack_surface",
                title=_safe_str(surface_dict.get("label")) or "Owner Pack",
                status=_safe_str(surface_dict.get("status")) or WORKSPACE_NO_LIVE_EVIDENCE,
                supported=_dedupe_str(surface_dict.get("evidence_found") or []),
                missing=_dedupe_str(surface_dict.get("missing_evidence") or []),
                evidence=_dedupe_str(surface_dict.get("workspace_files_found") or []),
            )
        )
    return sections


def _dossier_sections(dossier: Mapping[str, Any]) -> list[OwnerEntityFolderWorkspaceSection]:
    audit = _as_dict(dossier.get("audit"))
    sections: list[OwnerEntityFolderWorkspaceSection] = []
    for entity in _as_list(audit.get("entities")):
        entity_dict = _as_dict(entity)
        sections.append(
            OwnerEntityFolderWorkspaceSection(
                section_id="dossier_entity",
                title=_safe_str(entity_dict.get("entity_name")) or "Entidad",
                status=_safe_str(entity_dict.get("readiness_status")) or _safe_str(dossier.get("status")),
                supported=_dedupe_str(entity_dict.get("supported_fields") or []),
                missing=_dedupe_str(entity_dict.get("missing_fields") or []),
                evidence=_dedupe_str(entity_dict.get("overlap_with_owner_pack") or []),
            )
        )
    return sections


def _cards(
    *,
    status: str,
    readiness: Mapping[str, Any],
    dossier: Mapping[str, Any],
    evidence: Sequence[str],
    missing: Sequence[str],
    next_questions: Sequence[str],
) -> list[OwnerEntityFolderWorkspaceCard]:
    return [
        OwnerEntityFolderWorkspaceCard(
            card_id="status",
            title="Estado de la carpeta",
            status=status,
            summary=_safe_str(readiness.get("summary")) or _safe_str(dossier.get("summary")),
            items=[
                f"Readiness: {_safe_str(readiness.get('status')) or 'sin status'}",
                f"Dossier: {_safe_str(dossier.get('status')) or 'sin status'}",
            ],
        ),
        OwnerEntityFolderWorkspaceCard(
            card_id="evidence",
            title="Evidencia encontrada",
            status="supported" if evidence else "missing",
            summary=f"{len(evidence)} evidencias/fuentes detectadas.",
            items=list(evidence)[:12],
        ),
        OwnerEntityFolderWorkspaceCard(
            card_id="missing",
            title="Faltantes bloqueantes",
            status="missing" if missing else "clear",
            summary=f"{len(missing)} faltantes antes de prometer carpeta completa.",
            items=list(missing)[:12],
        ),
        OwnerEntityFolderWorkspaceCard(
            card_id="questions",
            title="Siguientes preguntas",
            status="open" if next_questions else "clear",
            summary="Preguntas minimas para avanzar sin inventar datos.",
            items=list(next_questions)[:8],
        ),
    ]


def _wizard_card(bridge: Mapping[str, Any]) -> OwnerEntityFolderWorkspaceCard:
    phase_count = int(bridge.get("phase_count") or 0)
    activity_count = int(bridge.get("activity_count") or 0)
    status = _safe_str(bridge.get("status")) or WORKSPACE_NO_LIVE_EVIDENCE
    items = [
        f"Fases capturadas: {phase_count}",
        f"Actividades capturadas: {activity_count}",
    ]
    missing_paths = _dedupe_str(bridge.get("missing_paths") or [])
    if missing_paths:
        items.append("Faltantes: " + ", ".join(missing_paths[:4]))
    return OwnerEntityFolderWorkspaceCard(
        card_id="soul_wizard_plan",
        title="Plan SOUL del torneo",
        status=status,
        summary="Fases, fechas y actividades del Wizard disponibles como contexto Owner Pack.",
        items=items,
    )


def _wizard_section(bridge: Mapping[str, Any]) -> OwnerEntityFolderWorkspaceSection:
    phases = [_as_dict(item) for item in _as_list(bridge.get("phases"))]
    supported = []
    missing = []
    evidence = []
    for phase in phases:
        label = _safe_str(phase.get("name")) or _safe_str(phase.get("phase_id")) or "Fase"
        if phase.get("ready_for_owner_pack"):
            supported.append(label)
        else:
            missing.append(label)
        start_date = _safe_str(phase.get("start_date"))
        end_date = _safe_str(phase.get("end_date"))
        activity_count = int(phase.get("activity_count") or 0)
        evidence.append(f"{label}: {start_date or 'sin inicio'} a {end_date or 'sin cierre'}; {activity_count} actividad(es)")
    return OwnerEntityFolderWorkspaceSection(
        section_id="soul_wizard_plan",
        title="Plan SOUL: fases, fechas y actividades",
        status=_safe_str(bridge.get("status")) or WORKSPACE_NO_LIVE_EVIDENCE,
        supported=_dedupe_str(supported),
        missing=_dedupe_str(missing + list(bridge.get("missing_paths") or [])),
        evidence=_dedupe_str(evidence),
    )


def _preview(status: str, missing: Sequence[str]) -> dict[str, Any]:
    return {
        "preview_type": "owner_entity_folder_review",
        "status": status,
        "approval_required_for_durable_output": True,
        "allowed_next_action": "human_review_readonly_workspace",
        "blocked_actions": [
            "create_folder",
            "export_folder",
            "publish_owner_pack",
            "notify_external_parties",
            "mutate_tournament_or_finance_state",
        ],
        "missing_blocks_complete_claim": bool(missing),
        "execution_status": NOT_EXECUTED,
    }


def build_owner_entity_folder_workspace(
    *,
    readiness_report: OwnerPackReadinessReport | Mapping[str, Any],
    dossier_report: OwnerEntityDossierLiveReport | Mapping[str, Any] | None = None,
    target: Optional[Mapping[str, Any]] = None,
    soul_wizard_payload: Optional[Mapping[str, Any]] = None,
) -> OwnerEntityFolderWorkspace:
    """Compose Owner Pack readiness and entity dossier into an inert workspace."""

    readiness = _as_dict(readiness_report)
    dossier = _as_dict(dossier_report)
    wizard_bridge = build_soul_wizard_owner_pack_bridge(soul_wizard_payload) if soul_wizard_payload else {}
    target_payload = dict(target or readiness.get("target") or {})
    unavailable_components = [
        f"Fuente no disponible: {item}"
        for item in (dossier.get("source_summary", {}).get("unavailable_components", []) or [])
    ]
    wizard_evidence = []
    if wizard_bridge:
        wizard_evidence.append(
            "SOUL Wizard: "
            f"{wizard_bridge.get('phase_count') or 0} fase(s), "
            f"{wizard_bridge.get('activity_count') or 0} actividad(es)"
        )
    evidence = _dedupe_str(list(readiness.get("evidence_found") or []) + wizard_evidence)
    missing = _dedupe_str(
        list(readiness.get("missing_evidence") or [])
        + list(dossier.get("missing_evidence") or [])
        + unavailable_components
        + [f"SOUL Wizard: {item}" for item in (wizard_bridge.get("missing_paths") or [])]
    )
    non_claims = _dedupe_str(
        list(dossier.get("non_claims") or [])
        + list(wizard_bridge.get("non_claims") or [])
        + [
            "No afirma que la carpeta este completa si hay faltantes de evidencia.",
            "No crea, exporta ni publica carpetas del dueno.",
            "No convierte memoria, precedente o agregados en autoridad operativa.",
        ]
    )
    next_questions = _dedupe_str(readiness.get("next_questions") or [])
    status = _status(readiness, dossier)
    if status == WORKSPACE_READY_FOR_REVIEW and missing:
        status = WORKSPACE_PARTIAL
    diagnostic_sections = _readiness_sections(readiness) + _dossier_sections(dossier)
    sections = _operational_folder_sections(
        base_sections=diagnostic_sections,
        evidence=evidence,
        missing=missing,
    ) + diagnostic_sections
    if wizard_bridge:
        sections.append(_wizard_section(wizard_bridge))
    workspace_id = _workspace_id(target_payload, evidence, missing)
    summary = "Workspace read-only de carpeta por entidad preparado para revision humana."
    if status == WORKSPACE_PARTIAL:
        summary = "Workspace read-only con evidencia parcial; faltantes bloquean cualquier claim completo."
    elif status == WORKSPACE_NEEDS_TARGET:
        summary = "Falta definir entidad objetivo antes de evaluar carpeta por entidad."
    elif status == WORKSPACE_NO_LIVE_EVIDENCE:
        summary = "No hay evidencia viva suficiente; solo se puede mostrar contrato/faltantes."

    return OwnerEntityFolderWorkspace(
        workspace_id=workspace_id,
        headline="Owner Entity Folder Workspace",
        summary=summary,
        status=status,
        target=target_payload,
        workspace_cards=_cards(
            status=status,
            readiness=readiness,
            dossier=dossier,
            evidence=evidence,
            missing=missing,
            next_questions=next_questions,
        )
        + ([_wizard_card(wizard_bridge)] if wizard_bridge else []),
        folder_sections=sections,
        evidence=evidence,
        missing_fields=missing,
        non_claims=non_claims,
        next_questions=next_questions,
        preview=_preview(status, missing),
        source_reports=_dedupe_str(
            list(readiness.get("source_reports") or [])
            + [_safe_str(dossier.get("report_id")), _safe_str(dossier.get("audit", {}).get("audit_id"))]
            + [_safe_str(wizard_bridge.get("bridge_version")), _safe_str(wizard_bridge.get("draft_hash"))]
        ),
        safety_summary={
            "read_only": True,
            "writes_enabled": False,
            "write_handlers_invoked": 0,
            "approval_required_for_durable_output": True,
            "memory_or_precedent_grants_authority": False,
            "soul_wizard_context_accepted": bool(wizard_bridge),
            "soul_wizard_creates_live_operations": False,
            "complete_claim_allowed": status == WORKSPACE_READY_FOR_REVIEW and not missing,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_ENTITY_FOLDER_WORKSPACE_ONLY,
    )


def build_owner_entity_folder_workspace_from_tournament_source(
    source: Any,
    *,
    status_report: OwnerPackStatusReport,
    entity_name: Optional[str] = None,
    root_dir: Any = None,
    soul_wizard_payload: Optional[Mapping[str, Any]] = None,
) -> OwnerEntityFolderWorkspace:
    """Build the workspace from the conservative local tournament source."""

    project = getattr(source, "project", None)
    link = getattr(source, "operations_link", None)
    tournament_name = _safe_str(getattr(project, "name", ""))
    tournament_slug = _safe_str(getattr(link, "operations_tournament_slug", "")) or tournament_name
    dossier = build_owner_entity_dossier_live_from_tournament_source(
        source,
        entity_name=entity_name,
    )
    readiness = build_owner_pack_readiness_from_scope(
        status_report=status_report,
        scope="entity_folder",
        tournament_slug=tournament_slug,
        entity_name=entity_name,
        root_dir=root_dir,
    )
    return build_owner_entity_folder_workspace(
        readiness_report=readiness,
        dossier_report=dossier,
        target={
            "scope": "entity_folder",
            "tournament_name": tournament_name or None,
            "tournament_slug": tournament_slug or None,
            "entity_name": entity_name or None,
        },
        soul_wizard_payload=soul_wizard_payload,
    )


def owner_entity_folder_workspace_contains_execution_claim(
    workspace: OwnerEntityFolderWorkspace,
) -> bool:
    payload = workspace.to_dict()
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
    "OWNER_ENTITY_FOLDER_WORKSPACE_ONLY",
    "WORKSPACE_NEEDS_TARGET",
    "WORKSPACE_NO_LIVE_EVIDENCE",
    "WORKSPACE_PARTIAL",
    "WORKSPACE_READY_FOR_REVIEW",
    "OwnerEntityFolderWorkspace",
    "OwnerEntityFolderWorkspaceCard",
    "OwnerEntityFolderWorkspaceSection",
    "build_owner_entity_folder_workspace",
    "build_owner_entity_folder_workspace_from_tournament_source",
    "owner_entity_folder_workspace_contains_execution_claim",
]
