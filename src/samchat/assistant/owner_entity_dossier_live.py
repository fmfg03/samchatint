"""Live local Owner Entity Dossier wrapper.

This module bridges the local tournament source into the Director General entity
folder audit. It is deliberately conservative: the current local source exposes
some tournament/team/player aggregates, but not a complete per-entity finance or
operations folder. The wrapper therefore reports source limits instead of
claiming a completed dossier.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from .business_diff_preview import NOT_EXECUTED
from .owner_entity_dossier_audit import (
    OWNER_ENTITY_DOSSIER_AUDIT_ONLY,
    OwnerEntityDossierAuditReport,
    build_owner_entity_dossier_audit_from_snapshot,
)

OWNER_ENTITY_DOSSIER_LIVE_ONLY = "owner_entity_dossier_live_only"


@dataclass(frozen=True)
class OwnerEntityDossierLiveReport:
    report_id: str
    headline: str
    summary: str
    status: str
    source_summary: dict[str, Any]
    audit: dict[str, Any]
    entity_name: Optional[str] = None
    missing_evidence: list[str] = field(default_factory=list)
    non_claims: list[str] = field(default_factory=list)
    recommended_next_steps: list[str] = field(default_factory=list)
    safety_summary: dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_ENTITY_DOSSIER_LIVE_ONLY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _safe_str(value)
        if not cleaned:
            continue
        folded = cleaned.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(cleaned)
    return result


def _snapshot_from_tournament_source(source: Any, *, entity_name: Optional[str]) -> dict[str, Any]:
    project = getattr(source, "project", None)
    operations = getattr(source, "observed_operations", None)
    link = getattr(source, "operations_link", None)
    project_name = _safe_str(getattr(project, "name", ""))
    project_id = _safe_str(getattr(project, "id", ""))
    slug = _safe_str(getattr(link, "operations_tournament_slug", ""))
    categories = list(getattr(operations, "categories", []) or getattr(project, "categorias", []) or [])
    branches = list(getattr(operations, "branches", []) or [])
    states = list(getattr(operations, "states", []) or [])
    municipalities = list(getattr(operations, "municipalities", []) or [])
    teams_count = _safe_int(getattr(operations, "teams_count", 0))
    players_count = _safe_int(getattr(operations, "players_count", 0))
    target_entity = _safe_str(entity_name) or (states[0] if states else "Operaciones")

    team_stub = {
        "team_id": slug or project_id or project_name,
        "team_name": "Agregado local del torneo",
        "category": categories[0] if categories else None,
        "branch": branches[0] if branches else None,
        "players_count": players_count,
        "documents_complete_players": 0,
        "documents_verified_players": 0,
        "primary_manager": {},
    }
    entity = {
        "entity_name": target_entity,
        "teams_count": teams_count,
        "players_count": players_count,
        "categories": categories,
        "branches": branches,
        "states": states,
        "municipalities": municipalities,
        "teams": [team_stub] if teams_count or players_count else [],
    }
    return {
        "ok": True,
        "tournaments": [
            {
                "id": project_id,
                "name": project_name,
                "slug": slug or project_id,
            }
        ],
        "soul": {
            "tournament": {
                "id": project_id,
                "name": project_name,
                "slug": slug or project_id,
            },
            "operations": {"entities": [entity]},
        },
    }


def _status_for_audit(audit: OwnerEntityDossierAuditReport, *, aggregate_only: bool) -> str:
    if audit.entity_count == 0:
        return "not_found"
    if aggregate_only:
        return "partial_aggregate_only"
    if audit.usable_entity_count == audit.entity_count and not audit.missing_evidence:
        return "usable"
    return "partial"


def build_owner_entity_dossier_live_from_tournament_source(
    source: Any,
    *,
    entity_name: Optional[str] = None,
) -> OwnerEntityDossierLiveReport:
    """Build a conservative live Owner Entity dossier audit from local DB source."""

    operations = getattr(source, "observed_operations", None)
    teams_count = _safe_int(getattr(operations, "teams_count", 0))
    players_count = _safe_int(getattr(operations, "players_count", 0))
    aggregate_only = bool(teams_count or players_count)
    snapshot = _snapshot_from_tournament_source(source, entity_name=entity_name)
    audit = build_owner_entity_dossier_audit_from_snapshot(snapshot, entity_name=entity_name)
    status = _status_for_audit(audit, aggregate_only=aggregate_only)
    unavailable = list(getattr(source, "unavailable_components", []) or [])
    missing = _dedupe(
        list(audit.missing_evidence)
        + [
            "Detalle financiero por entidad" if aggregate_only else "Equipos/jugadores por entidad",
            "Contacto completo de entidad",
            "Fechas, cuotas, viajes, uniformes y clasificacion final",
        ]
    )
    if audit.entity_count == 0:
        headline = "No hay expediente vivo para la entidad solicitada"
        summary = "La base local no contiene evidencia suficiente para armar esa carpeta de entidad."
    elif aggregate_only:
        headline = "Expediente de entidad disponible solo como agregado parcial"
        summary = "Hay evidencia local de torneo/equipos/jugadores, pero faltan datos por entidad y finanzas antes de prometer carpeta completa."
    else:
        headline = "Expediente de entidad sin evidencia operativa viva suficiente"
        summary = "La fuente local encontro el torneo, pero no expone equipos/jugadores para sostener la carpeta."

    return OwnerEntityDossierLiveReport(
        report_id="owner_entity_dossier_live_v1",
        headline=headline,
        summary=summary,
        status=status,
        source_summary={
            "source": "samchat_local_tournament_db",
            "source_hash": getattr(source, "source_hash", None),
            "schema_version": getattr(source, "schema_version", None),
            "operations_slug": getattr(getattr(source, "operations_link", None), "operations_tournament_slug", None),
            "teams_count": teams_count,
            "players_count": players_count,
            "aggregate_only": aggregate_only,
            "unavailable_components": unavailable,
            "domain_write_performed": bool(getattr(source, "domain_write_performed", False)),
        },
        audit=audit.to_dict(),
        entity_name=_safe_str(entity_name) or None,
        missing_evidence=missing,
        non_claims=list(audit.non_claims)
        + [
            "No convierte agregados del torneo en hechos comprobados por entidad.",
            "No crea ni actualiza carpetas, torneos, equipos, pagos o polizas.",
        ],
        recommended_next_steps=[
            "Completar fuente local de responsables/contactos por entidad.",
            "Conectar bridge financiero por entidad antes de presentar carpeta financiera.",
            "Usar SOUL Wizard para fases, fechas y actividades faltantes cuando el torneo aun esta en planeacion.",
        ],
        safety_summary={
            "read_only": True,
            "writes_enabled": False,
            "raw_dossier_exposed": False,
            "uses_local_db_source": True,
            "fallback_to_legacy_supabase": False,
            "audit_language": OWNER_ENTITY_DOSSIER_AUDIT_ONLY,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_ENTITY_DOSSIER_LIVE_ONLY,
    )


__all__ = [
    "OWNER_ENTITY_DOSSIER_LIVE_ONLY",
    "OwnerEntityDossierLiveReport",
    "build_owner_entity_dossier_live_from_tournament_source",
]
