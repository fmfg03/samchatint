"""Owner entity dossier audit wrapper.

This module evaluates the existing Director General entity dossier against the
Owner Pack contract before exposing it as an assistant-facing answer. It is
read-only and does not load live data by itself; callers pass a canonical
tournament snapshot and receive a human-facing audit of what is supported,
missing, duplicated, or not ready to claim.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from samchat.sports_platform import build_director_general_entity_dossier

from .business_diff_preview import NOT_EXECUTED
from .owner_pack_inventory import build_owner_pack_surface_inventory

OWNER_ENTITY_DOSSIER_AUDIT_ONLY = "owner_entity_dossier_audit_only"
OWNER_DOSSIER_READY_PARTIAL = "partial"
OWNER_DOSSIER_READY_NEEDS_DATA = "needs_data"
OWNER_DOSSIER_READY_USABLE = "usable"


@dataclass(frozen=True)
class OwnerEntityDossierEntityAudit:
    entity_name: str
    readiness_status: str
    readiness_score: int
    supported_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    overlap_with_owner_pack: list[str] = field(default_factory=list)
    improvement_notes: list[str] = field(default_factory=list)
    source_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerEntityDossierAuditReport:
    audit_id: str
    headline: str
    summary: str
    decision: str
    tournament: dict[str, Any]
    entities: list[OwnerEntityDossierEntityAudit] = field(default_factory=list)
    entity_count: int = 0
    usable_entity_count: int = 0
    missing_evidence: list[str] = field(default_factory=list)
    redundancy_notes: list[str] = field(default_factory=list)
    recommended_next_steps: list[str] = field(default_factory=list)
    non_claims: list[str] = field(default_factory=list)
    safety_summary: dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_ENTITY_DOSSIER_AUDIT_ONLY

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entities"] = [item.to_dict() for item in self.entities]
        return payload


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _owner_entity_fields() -> set[str]:
    surface = build_owner_pack_surface_inventory("entity_folder")
    return {field.field for section in surface.sections for field in section.fields}


def _operation_supported_fields(operations: Mapping[str, Any]) -> list[str]:
    mapping = {
        "entity_name": operations.get("entity_name"),
        "real_teams": operations.get("real_teams_by_category_gender"),
        "players_by_category_age_gender": operations.get("players_by_category_age_gender"),
        "document_metrics": operations.get("document_metrics"),
        "entity_contacts": operations.get("entity_contacts"),
    }
    return sorted(key for key, value in mapping.items() if _has_value(value))


def _missing_from_dossier(entity: Mapping[str, Any]) -> list[str]:
    operations = entity.get("operations") or {}
    finance = entity.get("finance") or {}
    missing = list(operations.get("pending_fields") or [])
    missing.extend(finance.get("pending_fields") or [])
    return sorted({_safe_str(item) for item in missing if _safe_str(item)})


def _improvement_notes(entity: Mapping[str, Any]) -> list[str]:
    operations = entity.get("operations") or {}
    finance = entity.get("finance") or {}
    notes: list[str] = []
    if (finance.get("source_status") or "") == "pending_finance_entity_bridge":
        notes.append(
            "Cruzar la entidad contra solicitudes, informes, CFDI, pagos y presupuesto antes de prometer carpeta financiera."
        )
    if not operations.get("expected_teams_by_category_gender"):
        notes.append("Agregar fuente para equipos esperados por categoria/genero; hoy solo hay equipos reales.")
    if not operations.get("round_advancement"):
        notes.append("Agregar fuente de rondas/avance antes de afirmar equipos que superan cada fase.")
    if not operations.get("national_phase_qualifiers"):
        notes.append("Agregar fuente de clasificados a nacional antes de cerrar carpeta de entidad.")
    if not operations.get("final_classification"):
        notes.append("Agregar tabla de clasificacion final por equipo cuando exista resultado final.")
    return notes


def _entity_audit(entity: Mapping[str, Any], owner_fields: set[str]) -> OwnerEntityDossierEntityAudit:
    operations = entity.get("operations") or {}
    readiness = entity.get("readiness") or {}
    supported = _operation_supported_fields(operations)
    overlap = sorted(
        {
            "entity_name" if field == "entity_name" else field
            for field in ("entity_name", "real_teams", "players_by_category_age_gender")
            if field in owner_fields and field in supported
        }
    )
    return OwnerEntityDossierEntityAudit(
        entity_name=_safe_str(entity.get("entity_name")) or "Sin entidad",
        readiness_status=_safe_str(readiness.get("status")) or OWNER_DOSSIER_READY_NEEDS_DATA,
        readiness_score=_safe_int(readiness.get("score")),
        supported_fields=supported,
        missing_fields=_missing_from_dossier(entity),
        overlap_with_owner_pack=overlap,
        improvement_notes=_improvement_notes(entity),
        source_summary={
            "teams_count": ((operations.get("summary") or {}).get("teams_count")),
            "players_count": ((operations.get("summary") or {}).get("players_count")),
            "contacts_count": len(operations.get("entity_contacts") or []),
            "finance_source_status": ((entity.get("finance") or {}).get("source_status")),
        },
    )


def build_owner_entity_dossier_audit_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    entity_name: Optional[str] = None,
) -> OwnerEntityDossierAuditReport:
    dossier = build_director_general_entity_dossier(dict(snapshot))
    owner_fields = _owner_entity_fields()
    raw_entities = list(dossier.get("entities") or [])
    if entity_name:
        wanted = entity_name.casefold().strip()
        raw_entities = [
            item
            for item in raw_entities
            if wanted in _safe_str(item.get("entity_name")).casefold()
        ]

    audits = [_entity_audit(item, owner_fields) for item in raw_entities]
    missing = sorted({field for item in audits for field in item.missing_fields})
    usable = sum(1 for item in audits if item.readiness_status == OWNER_DOSSIER_READY_USABLE)
    if not audits:
        decision = "do_not_wire_directly"
        headline = "No hay entidades para auditar"
        summary = "El snapshot no contiene entidades compatibles con el expediente DG."
    elif usable == len(audits) and not missing:
        decision = "wire_read_only_candidate"
        headline = "Expediente DG listo como candidato read-only"
        summary = "El expediente por entidad tiene datos suficientes y no reporta faltantes cr?ticos."
    else:
        decision = "wrap_before_wiring"
        headline = "Expediente DG ?til, pero requiere wrapper antes de cablearse"
        summary = (
            "La estructura por entidad existe y aporta datos reales, pero todav?a hay "
            "faltantes de evidencia y solapes con Owner Pack que deben explicarse al usuario."
        )

    return OwnerEntityDossierAuditReport(
        audit_id="owner_entity_dossier_audit_v1",
        headline=headline,
        summary=summary,
        decision=decision,
        tournament=dict(dossier.get("tournament") or {}),
        entities=audits,
        entity_count=len(audits),
        usable_entity_count=usable,
        missing_evidence=missing,
        redundancy_notes=[
            "Se solapa con Owner Pack entity_folder: no debe presentarse como carpeta nueva separada.",
            "Usar Director General dossier como fuente estructural interna y Owner Pack como contrato conversacional.",
        ],
        recommended_next_steps=[
            "Conservar build_director_general_entity_dossier como fuente read-only interna.",
            "Crear respuesta de asistente que combine DG dossier, Owner Pack inventory y evidencia viva disponible.",
            "No prometer datos financieros por entidad hasta conectar finance_entity_bridge real.",
        ],
        non_claims=list(dossier.get("non_claims") or []),
        safety_summary={
            "read_only": True,
            "writes_enabled": False,
            "executes_dossier_only": True,
            "approval_required_for_durable_folder": True,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_ENTITY_DOSSIER_AUDIT_ONLY,
    )


__all__ = [
    "OWNER_ENTITY_DOSSIER_AUDIT_ONLY",
    "OwnerEntityDossierAuditReport",
    "OwnerEntityDossierEntityAudit",
    "build_owner_entity_dossier_audit_from_snapshot",
]
