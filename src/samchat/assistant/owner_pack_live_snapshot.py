"""Read-only live snapshots for Owner Pack folder surfaces.

The inventory/status contracts say what SamChat is prepared to show. This module
performs the first conservative live-data connection: read existing tournament
intelligence workspace JSON files and mark each Owner Pack field as supported or
missing. It never creates folders, never writes files, and never treats missing
workspace artifacts as facts.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .business_diff_preview import NOT_EXECUTED
from .owner_pack_inventory import (
    OWNER_PACK_FIELD_SCHEMA_PREPARED,
    OWNER_PACK_INVENTORY_ONLY,
    OWNER_PACK_SOURCE_NOT_QUERIED,
    OwnerPackInventoryField,
    build_owner_pack_surface_inventory,
)


OWNER_PACK_LIVE_SNAPSHOT_ONLY = "owner_pack_live_snapshot_only"
OWNER_PACK_LIVE_SUPPORTED = "supported_by_live_workspace"
OWNER_PACK_LIVE_MISSING = "missing_live_workspace_evidence"
OWNER_PACK_WORKSPACE_SOURCE = "tournament_intelligence_workspace"

ENTITY_WORKSPACE_FILES = ("operations.json", "finance.json")
NATIONAL_WORKSPACE_FILES = ("operations.json", "finance.json", "marketing.json")
MARKETING_WORKSPACE_FILES = ("marketing.json",)

FIELD_WORKSPACE_KEYS = {
    "entity_name": ("operations.entity_name", "finance.entity_name"),
    "tournament": ("operations.tournament_slug",),
    "expected_teams": ("operations.expected_teams_by_category_gender",),
    "real_teams": ("operations.real_teams_by_category_gender",),
    "players_by_category_age_gender": ("operations.players_by_category_age_gender",),
    "round_progression": ("operations.teams_advancing_each_round",),
    "state_phase_operations": ("operations.state_phase_description",),
    "operator_payments": ("finance.operator_transfers",),
    "equipment_costs": ("finance.equipment_costs",),
    "visit_results": ("finance.visit_reports",),
    "photographic_evidence": ("marketing.photo_evidence",),
    "tournament_category": ("operations.tournament_category_dates_city",),
    "host_city": ("operations.tournament_category_dates_city",),
    "opening_and_final_dates": ("operations.tournament_category_dates_city",),
    "contracted_hotels_bed_nights": ("operations.hotels_and_bed_nights",),
    "contracted_meals": ("operations.meals_breakdown",),
    "sports_venue_and_fields": (
        "operations.sports_facility",
        "operations.field_types_and_count",
    ),
    "medical_services_description": ("operations.medical_services_description",),
    "accidents_with_transfers": ("operations.accidents_with_transfer",),
    "staff_travel_costs": ("finance.ps_travel_costs",),
    "hotel_payments": ("finance.hotel_payments_advance_settlement",),
    "provider_payments": ("finance.supplier_payments",),
    "medical_and_insurance_costs": (
        "finance.medical_service_costs",
        "finance.insurance_costs",
    ),
    "brand_activation_evidence": ("marketing.photo_evidence",),
    "brand_activation_activities": ("marketing.activities_and_results",),
    "physical_supplier_attendance": (
        "marketing.onsite_brand_activation_providers",
    ),
    "sponsor_visitors": ("marketing.sponsor_visitors",),
    "activation_result": ("marketing.activities_and_results",),
}


@dataclass(frozen=True)
class OwnerPackLiveFieldSnapshot:
    field: str
    label: str
    section_id: str
    evidence_type: str
    status: str
    value: Any = None
    source_paths: List[str] = field(default_factory=list)
    source_files: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerPackLiveSurfaceSnapshot:
    surface_id: str
    label: str
    target: Dict[str, Any]
    workspace_root: str
    workspace_files_checked: List[str] = field(default_factory=list)
    workspace_files_found: List[str] = field(default_factory=list)
    fields: List[OwnerPackLiveFieldSnapshot] = field(default_factory=list)
    supported_field_count: int = 0
    missing_field_count: int = 0
    live_lookup_performed: bool = True
    source: str = OWNER_PACK_WORKSPACE_SOURCE
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_PACK_LIVE_SNAPSHOT_ONLY

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["fields"] = [item.to_dict() for item in self.fields]
        return payload


@dataclass(frozen=True)
class OwnerPackLiveSnapshotReport:
    snapshot_id: str
    headline: str
    summary: str
    surfaces: List[OwnerPackLiveSurfaceSnapshot] = field(default_factory=list)
    supported_field_count: int = 0
    missing_field_count: int = 0
    safety_summary: Dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_PACK_LIVE_SNAPSHOT_ONLY

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["surfaces"] = [surface.to_dict() for surface in self.surfaces]
        return payload


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower())
    return normalized.strip("-") or "sin-nombre"


def _workspace_root(root_dir: str | Path | None) -> Path:
    if root_dir is not None:
        return Path(root_dir)
    return Path(os.getenv("TOURNAMENT_AI_WORKSPACE_ROOT", "reports/tournaments_ai"))


def _workspace_dir(
    *,
    root_dir: str | Path | None,
    tournament_slug: str,
    surface_id: str,
    entity_name: Optional[str],
) -> Path:
    root = _workspace_root(root_dir)
    tournament_dir = root / _slugify(tournament_slug)
    if surface_id == "entity_folder":
        return tournament_dir / "entities" / _slugify(entity_name or "")
    if surface_id in {"national_phase_folder", "marketing_activation_report"}:
        return tournament_dir / "national"
    return tournament_dir


def _file_names_for_surface(surface_id: str) -> Sequence[str]:
    if surface_id == "entity_folder":
        return ENTITY_WORKSPACE_FILES
    if surface_id == "marketing_activation_report":
        return MARKETING_WORKSPACE_FILES
    if surface_id == "national_phase_folder":
        return NATIONAL_WORKSPACE_FILES
    return ()


def _read_json(path: Path) -> Mapping[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _load_workspace_payloads(surface_dir: Path, file_names: Sequence[str]) -> Dict[str, Mapping[str, Any]]:
    payloads: Dict[str, Mapping[str, Any]] = {}
    for file_name in file_names:
        payload = _read_json(surface_dir / file_name)
        if payload is not None:
            payloads[file_name.removesuffix(".json")] = payload
    return payloads


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _get_path(payloads: Mapping[str, Any], path: str) -> Any:
    current: Any = payloads
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        return None
    return current


def _first_supported_value(
    payloads: Mapping[str, Any],
    paths: Sequence[str],
) -> tuple[Any, List[str]]:
    found_paths: List[str] = []
    values: List[Any] = []
    for path in paths:
        value = _get_path(payloads, path)
        if _has_value(value):
            found_paths.append(path)
            values.append(value)
    if not values:
        return None, []
    if len(values) == 1:
        return values[0], found_paths
    return values, found_paths


def _source_files_for_paths(surface_dir: Path, paths: Sequence[str]) -> List[str]:
    files = sorted({str(surface_dir / f"{path.split('.', 1)[0]}.json") for path in paths})
    return files


def _field_snapshot(
    field_contract: OwnerPackInventoryField,
    *,
    surface_dir: Path,
    payloads: Mapping[str, Any],
) -> OwnerPackLiveFieldSnapshot:
    paths = FIELD_WORKSPACE_KEYS.get(field_contract.field, ())
    if not paths:
        return OwnerPackLiveFieldSnapshot(
            field=field_contract.field,
            label=field_contract.label,
            section_id=field_contract.section_id,
            evidence_type=field_contract.evidence_type,
            status=OWNER_PACK_SOURCE_NOT_QUERIED,
            reason="No hay binding workspace v0 para este campo; requiere conector posterior.",
        )
    value, source_paths = _first_supported_value(payloads, paths)
    if source_paths:
        return OwnerPackLiveFieldSnapshot(
            field=field_contract.field,
            label=field_contract.label,
            section_id=field_contract.section_id,
            evidence_type=field_contract.evidence_type,
            status=OWNER_PACK_LIVE_SUPPORTED,
            value=value,
            source_paths=source_paths,
            source_files=_source_files_for_paths(surface_dir, source_paths),
            reason="Valor leido de workspace de inteligencia de torneos.",
        )
    return OwnerPackLiveFieldSnapshot(
        field=field_contract.field,
        label=field_contract.label,
        section_id=field_contract.section_id,
        evidence_type=field_contract.evidence_type,
        status=OWNER_PACK_LIVE_MISSING,
        source_paths=list(paths),
        source_files=_source_files_for_paths(surface_dir, paths),
        reason="No se encontro evidencia viva en los JSON esperados del workspace.",
    )


def build_owner_pack_live_surface_snapshot(
    *,
    surface_id: str,
    tournament_slug: str,
    entity_name: Optional[str] = None,
    root_dir: str | Path | None = None,
) -> OwnerPackLiveSurfaceSnapshot:
    inventory = build_owner_pack_surface_inventory(surface_id)
    surface_dir = _workspace_dir(
        root_dir=root_dir,
        tournament_slug=tournament_slug,
        surface_id=surface_id,
        entity_name=entity_name,
    )
    file_names = _file_names_for_surface(surface_id)
    checked = [str(surface_dir / name) for name in file_names]
    payloads = _load_workspace_payloads(surface_dir, file_names)
    found = [str(surface_dir / f"{name}.json") for name in sorted(payloads)]
    fields = [
        _field_snapshot(field_contract, surface_dir=surface_dir, payloads=payloads)
        for section in inventory.sections
        for field_contract in section.fields
        if field_contract.status == OWNER_PACK_FIELD_SCHEMA_PREPARED
    ]
    supported_count = sum(1 for item in fields if item.status == OWNER_PACK_LIVE_SUPPORTED)
    missing_count = sum(1 for item in fields if item.status != OWNER_PACK_LIVE_SUPPORTED)
    return OwnerPackLiveSurfaceSnapshot(
        surface_id=surface_id,
        label=inventory.label,
        target={
            "tournament_slug": tournament_slug,
            "entity_name": entity_name,
        },
        workspace_root=str(_workspace_root(root_dir)),
        workspace_files_checked=checked,
        workspace_files_found=found,
        fields=fields,
        supported_field_count=supported_count,
        missing_field_count=missing_count,
        live_lookup_performed=True,
        source=OWNER_PACK_WORKSPACE_SOURCE,
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_PACK_LIVE_SNAPSHOT_ONLY,
    )


def build_owner_pack_live_snapshot_report(
    *,
    surface_id: str,
    tournament_slug: str,
    entity_name: Optional[str] = None,
    root_dir: str | Path | None = None,
) -> OwnerPackLiveSnapshotReport:
    surface = build_owner_pack_live_surface_snapshot(
        surface_id=surface_id,
        tournament_slug=tournament_slug,
        entity_name=entity_name,
        root_dir=root_dir,
    )
    summary = (
        "Se encontro evidencia viva parcial en el workspace; los faltantes siguen marcados."
        if surface.supported_field_count
        else "No se encontro evidencia viva para esta superficie en el workspace configurado."
    )
    return OwnerPackLiveSnapshotReport(
        snapshot_id="owner_pack_live_snapshot_v1",
        headline="Snapshot vivo read-only del Owner Pack",
        summary=summary,
        surfaces=[surface],
        supported_field_count=surface.supported_field_count,
        missing_field_count=surface.missing_field_count,
        safety_summary={
            "writes_enabled": False,
            "live_lookup_performed": True,
            "source": OWNER_PACK_WORKSPACE_SOURCE,
            "approval_required_for_durable_outputs": True,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_PACK_LIVE_SNAPSHOT_ONLY,
    )


__all__ = [
    "OWNER_PACK_LIVE_MISSING",
    "OWNER_PACK_LIVE_SNAPSHOT_ONLY",
    "OWNER_PACK_LIVE_SUPPORTED",
    "OWNER_PACK_WORKSPACE_SOURCE",
    "OwnerPackLiveFieldSnapshot",
    "OwnerPackLiveSnapshotReport",
    "OwnerPackLiveSurfaceSnapshot",
    "build_owner_pack_live_snapshot_report",
    "build_owner_pack_live_surface_snapshot",
]
