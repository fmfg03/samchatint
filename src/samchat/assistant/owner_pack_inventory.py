"""Owner-pack field/source inventory for read-only assistant planning.

This module turns the owner request into a deterministic inventory of the
folder/dashboard surfaces SamChat is prepared to build. It does not inspect live
records and it does not claim that any folder has been generated; it only states
which fields exist in the contract, what evidence family should support each
field, and which canonical SamChat surfaces are expected to provide that
evidence once live data is connected.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Mapping, Sequence

from .business_diff_preview import NOT_EXECUTED
from .owner_folder_builder import (
    ACTIVATION_REPORT_PROPOSAL,
    ACTIVATION_SECTION_FIELDS,
    ENTITY_FOLDER_PROPOSAL,
    ENTITY_SECTION_FIELDS,
    FIELD_LABELS,
    FOLDER_BUILD_PLAN_PROPOSAL,
    NATIONAL_PHASE_FOLDER_PROPOSAL,
    NATIONAL_PHASE_SECTION_FIELDS,
    PLAN_SECTION_FIELDS,
)
from .owner_pack_status import SURFACE_LABELS


OWNER_PACK_INVENTORY_ONLY = "owner_pack_inventory_only"
OWNER_PACK_FIELD_SCHEMA_PREPARED = "schema_prepared"
OWNER_PACK_SOURCE_NOT_QUERIED = "live_source_not_queried"

SURFACE_FOLDER_TYPES = {
    "entity_folder": ENTITY_FOLDER_PROPOSAL,
    "national_phase_folder": NATIONAL_PHASE_FOLDER_PROPOSAL,
    "marketing_activation_report": ACTIVATION_REPORT_PROPOSAL,
    "work_plan_or_query": FOLDER_BUILD_PLAN_PROPOSAL,
}

SURFACE_SCHEMAS = {
    "entity_folder": ENTITY_SECTION_FIELDS,
    "national_phase_folder": NATIONAL_PHASE_SECTION_FIELDS,
    "marketing_activation_report": ACTIVATION_SECTION_FIELDS,
    "work_plan_or_query": PLAN_SECTION_FIELDS,
}

FIELD_EVIDENCE_TYPES = {
    "entity_name": "entity",
    "tournament": "tournament",
    "expected_teams": "team",
    "real_teams": "team",
    "players_by_category_age_gender": "player",
    "round_progression": "team",
    "state_phase_operations": "tournament",
    "operator_payments": "finance",
    "equipment_costs": "finance",
    "visit_results": "document",
    "photographic_evidence": "media",
    "tournament_category": "tournament",
    "host_city": "tournament",
    "opening_and_final_dates": "tournament",
    "contracted_hotels_bed_nights": "document",
    "contracted_meals": "document",
    "sports_venue_and_fields": "tournament",
    "medical_services_description": "medical/event_incident",
    "accidents_with_transfers": "medical/event_incident",
    "staff_travel_costs": "finance",
    "hotel_payments": "finance",
    "provider_payments": "finance",
    "medical_and_insurance_costs": "finance",
    "brand_activation_evidence": "marketing",
    "brand_activation_activities": "marketing",
    "physical_supplier_attendance": "marketing",
    "sponsor_visitors": "marketing",
    "activation_result": "marketing",
    "folder_scope": "canon",
    "source_inventory": "canon",
    "missing_evidence_inventory": "memory",
    "approval_boundary": "authority_preview",
}

EVIDENCE_SOURCE_ROUTES = {
    "authority_preview": ["assistant authority preview / approval receipt"],
    "canon": ["docs/assistant/owner-ai-needs.md", "docs/assistant/product-canon.md"],
    "document": ["documentos vinculados", "CFDI", "solicitudes", "informes de gastos"],
    "entity": ["padron de entidades/operadores", "contactos de entidad"],
    "finance": ["src/devnous/gastos", "payment run", "polizas", "CFDI/SAT"],
    "marketing": ["activaciones", "patrocinadores", "materialidad fotografica"],
    "media": ["archivos/materialidades", "fotografias vinculadas"],
    "medical/event_incident": ["incidencias medicas", "accidentes", "servicios medicos"],
    "memory": ["case memory", "expedientes previos", "decisiones registradas"],
    "player": ["base de jugadores", "cedulas OCR", "documentacion de jugadores"],
    "team": ["equipos", "rosters", "rondas/calendarios"],
    "tournament": ["src/devnous/tournaments", "torneos", "calendarios", "fases", "SOUL Wizard fases/fechas/actividades"],
}

PREFERRED_SURFACE_ORDER = (
    "entity_folder",
    "national_phase_folder",
    "marketing_activation_report",
    "work_plan_or_query",
)


@dataclass(frozen=True)
class OwnerPackInventoryField:
    field: str
    label: str
    section_id: str
    section_title: str
    evidence_type: str
    canonical_sources: List[str] = field(default_factory=list)
    status: str = OWNER_PACK_FIELD_SCHEMA_PREPARED
    live_query_status: str = OWNER_PACK_SOURCE_NOT_QUERIED
    value_required_from_live_data: bool = True

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerPackInventorySection:
    section_id: str
    title: str
    fields: List[OwnerPackInventoryField] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["fields"] = [field_item.to_dict() for field_item in self.fields]
        return payload


@dataclass(frozen=True)
class OwnerPackInventorySurface:
    surface_id: str
    label: str
    folder_type: str
    sections: List[OwnerPackInventorySection] = field(default_factory=list)
    field_count: int = 0
    evidence_types: List[str] = field(default_factory=list)
    canonical_sources: List[str] = field(default_factory=list)
    next_action: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["sections"] = [section.to_dict() for section in self.sections]
        return payload


@dataclass(frozen=True)
class OwnerPackInventoryReport:
    inventory_id: str
    headline: str
    summary: str
    surfaces: List[OwnerPackInventorySurface] = field(default_factory=list)
    surface_count: int = 0
    field_count: int = 0
    evidence_types: List[str] = field(default_factory=list)
    canonical_sources: List[str] = field(default_factory=list)
    safety_summary: Dict[str, object] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_PACK_INVENTORY_ONLY

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["surfaces"] = [surface.to_dict() for surface in self.surfaces]
        return payload


def _field_label(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())


def _sources_for_evidence_type(evidence_type: str) -> List[str]:
    return list(EVIDENCE_SOURCE_ROUTES.get(evidence_type, [evidence_type]))


def _surface_next_action(surface_id: str) -> str:
    if surface_id == "entity_folder":
        return "Conectar entidad, equipos, jugadores, documentos, finanzas y materialidad por torneo."
    if surface_id == "national_phase_folder":
        return "Conectar sede, hoteles, alimentos, canchas, medicos, accidentes, proveedores y pagos."
    if surface_id == "marketing_activation_report":
        return "Conectar proveedores presentes, visitantes, actividades, resultados y fotografias."
    return "Usar como plan read-only: mostrar fuentes esperadas, faltantes y frontera de aprobacion."


def build_owner_pack_surface_inventory(surface_id: str) -> OwnerPackInventorySurface:
    schema = SURFACE_SCHEMAS.get(surface_id)
    if schema is None:
        raise ValueError(f"unknown owner pack surface: {surface_id}")

    sections: List[OwnerPackInventorySection] = []
    all_evidence_types: set[str] = set()
    all_sources: set[str] = set()
    field_count = 0

    for section_id, section_spec in schema.items():
        fields: List[OwnerPackInventoryField] = []
        for field_name in section_spec["fields"]:
            field_key = str(field_name)
            evidence_type = FIELD_EVIDENCE_TYPES.get(field_key, "document")
            sources = _sources_for_evidence_type(evidence_type)
            all_evidence_types.add(evidence_type)
            all_sources.update(sources)
            field_count += 1
            fields.append(
                OwnerPackInventoryField(
                    field=field_key,
                    label=_field_label(field_key),
                    section_id=section_id,
                    section_title=str(section_spec["title"]),
                    evidence_type=evidence_type,
                    canonical_sources=sources,
                )
            )
        sections.append(
            OwnerPackInventorySection(
                section_id=section_id,
                title=str(section_spec["title"]),
                fields=fields,
            )
        )

    return OwnerPackInventorySurface(
        surface_id=surface_id,
        label=SURFACE_LABELS.get(surface_id, surface_id),
        folder_type=SURFACE_FOLDER_TYPES[surface_id],
        sections=sections,
        field_count=field_count,
        evidence_types=sorted(all_evidence_types),
        canonical_sources=sorted(all_sources),
        next_action=_surface_next_action(surface_id),
    )


def build_owner_pack_inventory_report(
    *,
    scopes: Sequence[str] | None = None,
) -> OwnerPackInventoryReport:
    requested = tuple(scopes or PREFERRED_SURFACE_ORDER)
    surfaces = [build_owner_pack_surface_inventory(scope) for scope in requested]
    evidence_types = sorted({item for surface in surfaces for item in surface.evidence_types})
    canonical_sources = sorted({item for surface in surfaces for item in surface.canonical_sources})
    field_count = sum(surface.field_count for surface in surfaces)
    return OwnerPackInventoryReport(
        inventory_id="owner_pack_inventory_v1",
        headline="Inventario del Owner Pack preparado",
        summary=(
            "SamChat tiene el contrato de campos y fuentes para las carpetas/tableros "
            "del dueno; los valores deben venir de evidencia viva antes de presentarse "
            "como informacion completa."
        ),
        surfaces=surfaces,
        surface_count=len(surfaces),
        field_count=field_count,
        evidence_types=evidence_types,
        canonical_sources=canonical_sources,
        safety_summary={
            "writes_enabled": False,
            "live_queries_executed": 0,
            "approval_required_for_durable_outputs": True,
            "values_are_schema_placeholders": True,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_PACK_INVENTORY_ONLY,
    )


def owner_pack_inventory_contains_execution_claim(
    report: OwnerPackInventoryReport,
) -> bool:
    payload = report.to_dict()
    text = str(payload).lower()
    text = text.replace(NOT_EXECUTED, "")
    text = re.sub(r"(entity|national_phase|activation)_folder_proposal", "", text)
    unsafe_terms = (
        "creado",
        "actualizado",
        "ejecutado",
        "publicado",
        "consultado en vivo",
        "created successfully",
        "updated successfully",
        "executed successfully",
        "published successfully",
        "live query executed",
    )
    return any(term in text for term in unsafe_terms)


__all__ = [
    "OWNER_PACK_FIELD_SCHEMA_PREPARED",
    "OWNER_PACK_INVENTORY_ONLY",
    "OWNER_PACK_SOURCE_NOT_QUERIED",
    "OwnerPackInventoryField",
    "OwnerPackInventoryReport",
    "OwnerPackInventorySection",
    "OwnerPackInventorySurface",
    "build_owner_pack_inventory_report",
    "build_owner_pack_surface_inventory",
    "owner_pack_inventory_contains_execution_claim",
]
