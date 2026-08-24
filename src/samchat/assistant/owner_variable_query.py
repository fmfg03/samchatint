"""Read-only Owner Pack variable query resolver.

This module maps natural-language owner questions to canonical Owner Pack
fields and resolves whether SamChat has supported, partial, missing or
conflicting evidence. It is intentionally fail-closed: unmatched or unsupported
questions produce no factual claim.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .business_diff_preview import NOT_EXECUTED
from .owner_folder_builder import FIELD_LABELS
from .owner_pack_inventory import (
    FIELD_EVIDENCE_TYPES,
    build_owner_pack_inventory_report,
)
from .owner_pack_live_snapshot import (
    OWNER_PACK_LIVE_SUPPORTED,
    OwnerPackLiveFieldSnapshot,
    OwnerPackLiveSnapshotReport,
)
from .soul_wizard import build_soul_wizard_owner_pack_bridge

OWNER_VARIABLE_QUERY_ONLY = "owner_variable_query_only"
OWNER_VARIABLE_SUPPORTED = "supported"
OWNER_VARIABLE_PARTIAL = "partial"
OWNER_VARIABLE_MISSING = "missing"
OWNER_VARIABLE_CONFLICT = "conflict"
OWNER_VARIABLE_UNMAPPED = "unmapped"

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "entity_name": ("entidad", "estado", "operador"),
    "tournament": ("torneo", "copa", "campeonato"),
    "expected_teams": ("equipos esperados", "numero de equipos esperados", "estimado equipos"),
    "real_teams": ("equipos reales", "equipos participantes", "equipos inscritos", "numero de equipos reales"),
    "players_by_category_age_gender": ("jugadores", "edad", "genero", "categoria", "jugadores por categoria"),
    "round_progression": ("ronda", "pasan de ronda", "superan ronda", "clasificacion"),
    "state_phase_operations": ("fase estatal", "organiza la fase", "cuotas", "arbitraje", "transporte estatal"),
    "operator_payments": (
        "ayuda",
        "apoyo",
        "apoyos",
        "operador",
        "transferido",
        "pagos sucesivos",
        "primera ayuda",
        "pagos hechos",
        "pagos realizados",
        "pagos efectuados",
        "pagos o apoyos",
        "evidencia de pagos",
        "evidencia de apoyos",
        "pagos pendientes",
        "pendientes de pago",
        "entidades con pagos",
    ),
    "equipment_costs": ("uniformes", "balones", "equipamiento", "utilera"),
    "visit_results": ("visitas", "responsables", "az", "cl", "resultado visita"),
    "photographic_evidence": ("fotografias", "fotos", "materialidad"),
    "tournament_category": ("categoria", "rama", "torneo categoria"),
    "host_city": ("ciudad", "sede", "lugar"),
    "opening_and_final_dates": ("fecha", "inauguracion", "clausura", "final", "finales", "duracion"),
    "contracted_hotels_bed_nights": ("hotel", "hoteles", "camas noche", "hospedaje"),
    "contracted_meals": ("alimentos", "desayunos", "comidas", "box lunch", "cenas"),
    "sports_venue_and_fields": ("unidad deportiva", "cancha", "canchas", "campo", "campos"),
    "medical_services_description": ("medico", "servicio medico", "ambulancia", "curacion"),
    "accidents_with_transfers": ("accidentes", "traslado", "lesionados"),
    "staff_travel_costs": ("viajes personal", "viajes ps", "costo viaje"),
    "hotel_payments": ("pagos hotel", "anticipos hotel", "liquidaciones hotel", "salones"),
    "provider_payments": ("proveedores", "pagos proveedores", "proveedor diverso"),
    "medical_and_insurance_costs": ("seguros", "costo seguro", "costo medico"),
    "brand_activation_evidence": ("activacion", "marca", "fotografias activacion"),
    "brand_activation_activities": ("actividades", "resultado activacion"),
    "physical_supplier_attendance": ("proveedores asisten", "asisten fisicamente"),
    "sponsor_visitors": ("visitantes", "patrocinador", "sponsor"),
    "activation_result": ("resultado", "informe actividades", "resultado actividad"),
}


@dataclass(frozen=True)
class OwnerVariableCandidate:
    field: str
    label: str
    evidence_type: str
    matched_aliases: list[str] = field(default_factory=list)
    score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerVariableResolution:
    field: str
    label: str
    status: str
    value: Any = None
    evidence: list[str] = field(default_factory=list)
    missing_reason: str = ""
    conflict_values: list[Any] = field(default_factory=list)
    canonical_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerVariableQueryReport:
    query_id: str
    question: str
    status: str
    answer: str
    candidates: list[OwnerVariableCandidate] = field(default_factory=list)
    resolutions: list[OwnerVariableResolution] = field(default_factory=list)
    next_questions: list[str] = field(default_factory=list)
    safety_summary: dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_VARIABLE_QUERY_ONLY

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidates"] = [item.to_dict() for item in self.candidates]
        payload["resolutions"] = [item.to_dict() for item in self.resolutions]
        return payload


def _normalize(value: Any) -> str:
    text = str(value or "").casefold()
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def _dedupe(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _inventory_field_sources() -> dict[str, list[str]]:
    sources: dict[str, list[str]] = {}
    report = build_owner_pack_inventory_report()
    for surface in report.surfaces:
        for section in surface.sections:
            for item in section.fields:
                sources.setdefault(item.field, [])
                sources[item.field].extend(item.canonical_sources)
    return {field: sorted(set(values)) for field, values in sources.items()}


def _candidate_fields(question: str) -> list[OwnerVariableCandidate]:
    normalized_question = _normalize(question)
    sources = _inventory_field_sources()
    candidates: list[OwnerVariableCandidate] = []
    for field_name, aliases in FIELD_ALIASES.items():
        matched = [alias for alias in aliases if _normalize(alias) and _normalize(alias) in normalized_question]
        label = FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())
        if matched:
            candidates.append(
                OwnerVariableCandidate(
                    field=field_name,
                    label=label,
                    evidence_type=FIELD_EVIDENCE_TYPES.get(field_name, "document"),
                    matched_aliases=matched,
                    score=sum(len(item.split()) for item in matched),
                )
            )
    candidates.sort(key=lambda item: (-item.score, item.field))
    return candidates[:5]


def _field_snapshots(live_reports: Sequence[OwnerPackLiveSnapshotReport]) -> dict[str, list[OwnerPackLiveFieldSnapshot]]:
    result: dict[str, list[OwnerPackLiveFieldSnapshot]] = {}
    for report in live_reports:
        for surface in report.surfaces:
            for item in surface.fields:
                result.setdefault(item.field, []).append(item)
    return result


def _wizard_resolution(field_name: str, soul_wizard_payload: Optional[Mapping[str, Any]]) -> OwnerVariableResolution | None:
    if not soul_wizard_payload:
        return None
    bridge = build_soul_wizard_owner_pack_bridge(soul_wizard_payload)
    support = bridge.get("owner_pack_support") or {}
    if field_name not in set(support.get("supported_fields") or []):
        return None
    label = FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())
    sources = _inventory_field_sources().get(field_name, [])
    missing = list(bridge.get("missing_paths") or [])
    status = OWNER_VARIABLE_SUPPORTED if bridge.get("status") == "ready_for_review" else OWNER_VARIABLE_PARTIAL
    return OwnerVariableResolution(
        field=field_name,
        label=label,
        status=status,
        value={
            "tournament": bridge.get("tournament"),
            "phases": bridge.get("phases"),
            "phase_count": bridge.get("phase_count"),
            "activity_count": bridge.get("activity_count"),
        } if status == OWNER_VARIABLE_SUPPORTED else None,
        evidence=[f"SOUL Wizard bridge: {bridge.get('bridge_version')}"] if status == OWNER_VARIABLE_SUPPORTED else [],
        missing_reason=", ".join(missing) if missing else "",
        canonical_sources=sources,
    )


def _resolve_field(
    candidate: OwnerVariableCandidate,
    *,
    live_by_field: Mapping[str, list[OwnerPackLiveFieldSnapshot]],
    soul_wizard_payload: Optional[Mapping[str, Any]],
    source_routes: Mapping[str, list[str]],
) -> OwnerVariableResolution:
    wizard = _wizard_resolution(candidate.field, soul_wizard_payload)
    if wizard and wizard.status == OWNER_VARIABLE_SUPPORTED:
        return wizard

    snapshots = live_by_field.get(candidate.field, [])
    supported = [item for item in snapshots if item.status == OWNER_PACK_LIVE_SUPPORTED]
    if supported:
        distinct_values: dict[str, Any] = {_json_key(item.value): item.value for item in supported}
        evidence = []
        for item in supported:
            evidence.extend(item.source_paths or item.source_files)
        if len(distinct_values) > 1:
            return OwnerVariableResolution(
                field=candidate.field,
                label=candidate.label,
                status=OWNER_VARIABLE_CONFLICT,
                conflict_values=list(distinct_values.values()),
                evidence=_dedupe(evidence),
                canonical_sources=source_routes.get(candidate.field, []),
            )
        return OwnerVariableResolution(
            field=candidate.field,
            label=candidate.label,
            status=OWNER_VARIABLE_SUPPORTED,
            value=next(iter(distinct_values.values())),
            evidence=_dedupe(evidence),
            canonical_sources=source_routes.get(candidate.field, []),
        )

    if wizard:
        return wizard

    missing_reason = "no_supported_live_evidence_for_field"
    if snapshots:
        missing_reason = snapshots[0].reason or missing_reason
    return OwnerVariableResolution(
        field=candidate.field,
        label=candidate.label,
        status=OWNER_VARIABLE_MISSING,
        missing_reason=missing_reason,
        canonical_sources=source_routes.get(candidate.field, []),
    )


def _global_status(resolutions: Sequence[OwnerVariableResolution], candidates: Sequence[OwnerVariableCandidate]) -> str:
    if not candidates:
        return OWNER_VARIABLE_UNMAPPED
    statuses = {item.status for item in resolutions}
    if OWNER_VARIABLE_CONFLICT in statuses:
        return OWNER_VARIABLE_CONFLICT
    if statuses == {OWNER_VARIABLE_SUPPORTED}:
        return OWNER_VARIABLE_SUPPORTED
    if OWNER_VARIABLE_SUPPORTED in statuses or OWNER_VARIABLE_PARTIAL in statuses:
        return OWNER_VARIABLE_PARTIAL
    return OWNER_VARIABLE_MISSING


def _answer(status: str, resolutions: Sequence[OwnerVariableResolution]) -> str:
    if status == OWNER_VARIABLE_UNMAPPED:
        return "No pude mapear la pregunta a una variable canonica del Owner Pack; no genero dato inventado."
    if status == OWNER_VARIABLE_SUPPORTED:
        names = ", ".join(item.label for item in resolutions)
        return f"La variable esta soportada por evidencia viva/canonica: {names}."
    if status == OWNER_VARIABLE_PARTIAL:
        return "Hay evidencia parcial; algunos campos o desglose siguen pendientes antes de afirmar respuesta completa."
    if status == OWNER_VARIABLE_CONFLICT:
        return "Hay conflicto entre fuentes para al menos una variable; requiere revision humana antes de usar el dato."
    return "La variable existe en el contrato del Owner Pack, pero no encontre evidencia viva suficiente para contestarla."


def build_owner_variable_query_report(
    *,
    question: str,
    live_reports: Sequence[OwnerPackLiveSnapshotReport] = (),
    soul_wizard_payload: Optional[Mapping[str, Any]] = None,
) -> OwnerVariableQueryReport:
    """Resolve a natural-language owner question against Owner Pack variables."""

    candidates = _candidate_fields(question)
    resolved_candidates = [
        candidate
        for candidate in candidates
        if candidate.field not in {"tournament", "entity_name"}
    ] or candidates
    source_routes = _inventory_field_sources()
    live_by_field = _field_snapshots(live_reports)
    resolutions = [
        _resolve_field(
            candidate,
            live_by_field=live_by_field,
            soul_wizard_payload=soul_wizard_payload,
            source_routes=source_routes,
        )
        for candidate in resolved_candidates
    ]
    status = _global_status(resolutions, resolved_candidates)
    next_questions = []
    if status == OWNER_VARIABLE_UNMAPPED:
        next_questions.append("Quieres preguntar por equipos, jugadores, fases, pagos, hoteles, sede, alimentos o activaciones?")
    elif any(item.status in {OWNER_VARIABLE_MISSING, OWNER_VARIABLE_PARTIAL} for item in resolutions):
        next_questions.append("Quieres que muestre las fuentes esperadas y los faltantes para cargar evidencia?")
    elif status == OWNER_VARIABLE_CONFLICT:
        next_questions.append("Quieres que liste las fuentes en conflicto para conciliacion manual?")

    return OwnerVariableQueryReport(
        query_id="owner_variable_query_v1",
        question=question,
        status=status,
        answer=_answer(status, resolutions),
        candidates=candidates,
        resolutions=resolutions,
        next_questions=next_questions,
        safety_summary={
            "read_only": True,
            "writes_enabled": False,
            "write_handlers_invoked": 0,
            "fallback_to_guessing": False,
            "unmapped_questions_generate_claims": False,
            "memory_or_precedent_grants_authority": False,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_VARIABLE_QUERY_ONLY,
    )


__all__ = [
    "OWNER_VARIABLE_CONFLICT",
    "OWNER_VARIABLE_MISSING",
    "OWNER_VARIABLE_PARTIAL",
    "OWNER_VARIABLE_QUERY_ONLY",
    "OWNER_VARIABLE_SUPPORTED",
    "OWNER_VARIABLE_UNMAPPED",
    "OwnerVariableCandidate",
    "OwnerVariableQueryReport",
    "OwnerVariableResolution",
    "build_owner_variable_query_report",
]
