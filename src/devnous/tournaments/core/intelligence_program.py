"""
AI program scaffolding for tournament operations, finance and marketing.

This module provides:
- Agent-like validators for each domain.
- Workflow/checklist templates.
- Persistent folder/report generation per tournament and entity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return normalized.strip("-") or "sin-nombre"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@dataclass
class EntityOperationsRecord:
    entity_name: str
    ps_owner_name: Optional[str] = None
    entity_contact_name: Optional[str] = None
    entity_contact_phone: Optional[str] = None
    entity_contact_email: Optional[str] = None
    entity_contact_birthdate: Optional[str] = None
    partner_name: Optional[str] = None
    partner_birthdate: Optional[str] = None
    expected_teams_by_category_gender: List[Dict[str, Any]] = field(default_factory=list)
    real_teams_by_category_gender: List[Dict[str, Any]] = field(default_factory=list)
    players_by_category_age_gender: List[Dict[str, Any]] = field(default_factory=list)
    teams_advancing_each_round: List[Dict[str, Any]] = field(default_factory=list)
    state_phase_description: Optional[str] = None
    national_phase_qualified_teams: List[str] = field(default_factory=list)
    uniform_delivery_date_place: Optional[str] = None
    national_travel_dates: Dict[str, str] = field(default_factory=dict)
    final_ranking_by_team: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class EntityFinanceRecord:
    entity_name: str
    operator_transfers: List[Dict[str, Any]] = field(default_factory=list)
    equipment_costs: List[Dict[str, Any]] = field(default_factory=list)
    visit_reports: List[Dict[str, Any]] = field(default_factory=list)
    visit_expenses: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class NationalOperationsRecord:
    tournament_category_dates_city: Optional[str] = None
    hotels_and_bed_nights: List[Dict[str, Any]] = field(default_factory=list)
    meals_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    sports_facility: Optional[str] = None
    field_types_and_count: List[Dict[str, Any]] = field(default_factory=list)
    medical_services_description: Optional[str] = None
    accidents_with_transfer: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class NationalFinanceRecord:
    ps_travel_costs: List[Dict[str, Any]] = field(default_factory=list)
    hotel_payments_advance_settlement: List[Dict[str, Any]] = field(default_factory=list)
    supplier_payments: List[Dict[str, Any]] = field(default_factory=list)
    medical_service_costs: List[Dict[str, Any]] = field(default_factory=list)
    insurance_costs: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class NationalMarketingRecord:
    onsite_brand_activation_providers: List[Dict[str, Any]] = field(default_factory=list)
    sponsor_visitors: List[Dict[str, Any]] = field(default_factory=list)
    activities_and_results: List[Dict[str, Any]] = field(default_factory=list)
    photo_evidence: List[Dict[str, Any]] = field(default_factory=list)


class DomainIntakeAgent:
    """Validates and normalizes domain payloads for persistence/reporting."""

    required_fields: Sequence[str] = ()
    agent_name: str = "domain-intake-agent"

    def validate(self, payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        for field_name in self.required_fields:
            if payload.get(field_name) in (None, "", [], {}):
                errors.append(f"Missing required field: {field_name}")
        return (len(errors) == 0, errors)

    def normalize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return payload

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self.normalize(payload)
        valid, errors = self.validate(normalized)
        return {
            "agent": self.agent_name,
            "valid": valid,
            "errors": errors,
            "normalized_payload": normalized,
            "processed_at": _now_iso(),
        }


class EntityOperationsAgent(DomainIntakeAgent):
    agent_name = "entity-operations-agent"
    required_fields = ("entity_name",)


class EntityFinanceAgent(DomainIntakeAgent):
    agent_name = "entity-finance-agent"
    required_fields = ("entity_name",)


class NationalMarketingAgent(DomainIntakeAgent):
    agent_name = "national-marketing-agent"
    required_fields = ()


class TournamentIntelligenceWorkspace:
    """
    Creates and maintains AI folders/flows/reports for tournament operations.
    """

    def __init__(self, root_dir: str = "reports/tournaments_ai"):
        self.root = Path(root_dir)

    def bootstrap_tournament(self, tournament_slug: str) -> Dict[str, str]:
        tournament_dir = self.root / _slugify(tournament_slug)
        paths = {
            "tournament_dir": str(tournament_dir),
            "entities_dir": str(tournament_dir / "entities"),
            "national_dir": str(tournament_dir / "national"),
            "flows_dir": str(tournament_dir / "flows"),
        }
        for p in paths.values():
            Path(p).mkdir(parents=True, exist_ok=True)

        _write_text(tournament_dir / "flows" / "entity_workflow.yaml", self._entity_flow_template())
        _write_text(tournament_dir / "flows" / "national_workflow.yaml", self._national_flow_template())
        return paths

    def upsert_entity(
        self,
        tournament_slug: str,
        entity_ops: EntityOperationsRecord,
        entity_fin: Optional[EntityFinanceRecord] = None,
    ) -> Dict[str, str]:
        tournament_dir = self.root / _slugify(tournament_slug)
        entity_dir = tournament_dir / "entities" / _slugify(entity_ops.entity_name)
        entity_dir.mkdir(parents=True, exist_ok=True)

        ops_payload = asdict(entity_ops)
        ops_payload["updated_at"] = _now_iso()
        _write_json(entity_dir / "operations.json", ops_payload)
        _write_text(entity_dir / "operaciones_reporte.md", self._render_entity_operations_report(ops_payload))

        if entity_fin:
            fin_payload = asdict(entity_fin)
            fin_payload["updated_at"] = _now_iso()
            _write_json(entity_dir / "finance.json", fin_payload)
            _write_text(entity_dir / "finanzas_reporte.md", self._render_entity_finance_report(fin_payload))

        return {
            "entity_dir": str(entity_dir),
            "operations_json": str(entity_dir / "operations.json"),
            "operations_report": str(entity_dir / "operaciones_reporte.md"),
            "finance_json": str(entity_dir / "finance.json"),
            "finance_report": str(entity_dir / "finanzas_reporte.md"),
        }

    def upsert_national_phase(
        self,
        tournament_slug: str,
        operations: NationalOperationsRecord,
        finance: NationalFinanceRecord,
        marketing: NationalMarketingRecord,
    ) -> Dict[str, str]:
        tournament_dir = self.root / _slugify(tournament_slug)
        national_dir = tournament_dir / "national"
        national_dir.mkdir(parents=True, exist_ok=True)

        ops_payload = asdict(operations)
        fin_payload = asdict(finance)
        mkt_payload = asdict(marketing)
        ops_payload["updated_at"] = _now_iso()
        fin_payload["updated_at"] = _now_iso()
        mkt_payload["updated_at"] = _now_iso()

        _write_json(national_dir / "operations.json", ops_payload)
        _write_json(national_dir / "finance.json", fin_payload)
        _write_json(national_dir / "marketing.json", mkt_payload)

        _write_text(
            national_dir / "nacional_operaciones_reporte.md",
            self._render_national_operations_report(ops_payload),
        )
        _write_text(
            national_dir / "nacional_finanzas_reporte.md",
            self._render_national_finance_report(fin_payload),
        )
        _write_text(
            national_dir / "nacional_mercadotecnia_reporte.md",
            self._render_national_marketing_report(mkt_payload),
        )
        return {
            "national_dir": str(national_dir),
            "operations_json": str(national_dir / "operations.json"),
            "finance_json": str(national_dir / "finance.json"),
            "marketing_json": str(national_dir / "marketing.json"),
        }

    def _entity_flow_template(self) -> str:
        return """version: 1
scope: entity
steps:
  - id: capture_operations_baseline
    owner: entity-operations-agent
    outputs: [operations.json]
  - id: capture_finance_baseline
    owner: entity-finance-agent
    outputs: [finance.json]
  - id: daily_updates
    owner: entity-operations-agent
    frequency: daily
    outputs: [operations.json, operaciones_reporte.md]
  - id: weekly_finance_updates
    owner: entity-finance-agent
    frequency: weekly
    outputs: [finance.json, finanzas_reporte.md]
"""

    def _national_flow_template(self) -> str:
        return """version: 1
scope: national
steps:
  - id: capture_national_operations
    owner: entity-operations-agent
    outputs: [operations.json, nacional_operaciones_reporte.md]
  - id: capture_national_finance
    owner: entity-finance-agent
    outputs: [finance.json, nacional_finanzas_reporte.md]
  - id: capture_national_marketing
    owner: national-marketing-agent
    outputs: [marketing.json, nacional_mercadotecnia_reporte.md]
  - id: evidence_collection
    owner: national-marketing-agent
    frequency: daily
    outputs: [marketing.json]
"""

    def _render_entity_operations_report(self, data: Dict[str, Any]) -> str:
        return (
            "# Reporte Operaciones por Entidad\n\n"
            f"- Fecha de actualizacion: {data.get('updated_at', '')}\n"
            f"- Entidad: {data.get('entity_name', '')}\n"
            f"- Responsable PS: {data.get('ps_owner_name', '')}\n"
            f"- Responsable Entidad: {data.get('entity_contact_name', '')}\n"
            f"- Telefono: {data.get('entity_contact_phone', '')}\n"
            f"- Correo: {data.get('entity_contact_email', '')}\n"
            f"- Fecha de nacimiento: {data.get('entity_contact_birthdate', '')}\n"
            f"- Pareja: {data.get('partner_name', '')} ({data.get('partner_birthdate', '')})\n\n"
            "## Equipos esperados por categoria/genero\n"
            f"{json.dumps(data.get('expected_teams_by_category_gender', []), indent=2, ensure_ascii=True)}\n\n"
            "## Equipos reales por categoria/genero\n"
            f"{json.dumps(data.get('real_teams_by_category_gender', []), indent=2, ensure_ascii=True)}\n\n"
            "## Jugadores por categoria/edad/genero\n"
            f"{json.dumps(data.get('players_by_category_age_gender', []), indent=2, ensure_ascii=True)}\n\n"
            "## Equipos que superan cada ronda\n"
            f"{json.dumps(data.get('teams_advancing_each_round', []), indent=2, ensure_ascii=True)}\n\n"
            f"## Organizacion fase estatal\n{data.get('state_phase_description', '')}\n\n"
            "## Equipos que pasan a fase nacional\n"
            f"{json.dumps(data.get('national_phase_qualified_teams', []), indent=2, ensure_ascii=True)}\n\n"
            f"## Entrega de uniformes (fecha/lugar)\n{data.get('uniform_delivery_date_place', '')}\n\n"
            "## Viajes al nacional (ida/vuelta)\n"
            f"{json.dumps(data.get('national_travel_dates', {}), indent=2, ensure_ascii=True)}\n\n"
            "## Clasificacion final por equipo\n"
            f"{json.dumps(data.get('final_ranking_by_team', []), indent=2, ensure_ascii=True)}\n"
        )

    def _render_entity_finance_report(self, data: Dict[str, Any]) -> str:
        return (
            "# Reporte Finanzas por Entidad\n\n"
            f"- Fecha de actualizacion: {data.get('updated_at', '')}\n"
            f"- Entidad: {data.get('entity_name', '')}\n\n"
            "## Transferencias al operador\n"
            f"{json.dumps(data.get('operator_transfers', []), indent=2, ensure_ascii=True)}\n\n"
            "## Costo de uniformes, balones y equipamiento/utileria\n"
            f"{json.dumps(data.get('equipment_costs', []), indent=2, ensure_ascii=True)}\n\n"
            "## Informes de visitas (AZ, CL)\n"
            f"{json.dumps(data.get('visit_reports', []), indent=2, ensure_ascii=True)}\n\n"
            "## Monto de gastos por visita\n"
            f"{json.dumps(data.get('visit_expenses', []), indent=2, ensure_ascii=True)}\n"
        )

    def _render_national_operations_report(self, data: Dict[str, Any]) -> str:
        return (
            "# Reporte Nacional - Operaciones\n\n"
            f"- Fecha de actualizacion: {data.get('updated_at', '')}\n"
            f"- Torneo/categoria/fechas/ciudad: {data.get('tournament_category_dates_city', '')}\n\n"
            "## Hoteles y camas-noche\n"
            f"{json.dumps(data.get('hotels_and_bed_nights', []), indent=2, ensure_ascii=True)}\n\n"
            "## Alimentos (desayuno/comida/box lunch/cena)\n"
            f"{json.dumps(data.get('meals_breakdown', []), indent=2, ensure_ascii=True)}\n\n"
            f"## Unidad deportiva\n{data.get('sports_facility', '')}\n\n"
            "## Numero y tipos de canchas\n"
            f"{json.dumps(data.get('field_types_and_count', []), indent=2, ensure_ascii=True)}\n\n"
            f"## Servicios medicos en sede\n{data.get('medical_services_description', '')}\n\n"
            "## Accidentes con traslado\n"
            f"{json.dumps(data.get('accidents_with_transfer', []), indent=2, ensure_ascii=True)}\n"
        )

    def _render_national_finance_report(self, data: Dict[str, Any]) -> str:
        return (
            "# Reporte Nacional - Finanzas\n\n"
            f"- Fecha de actualizacion: {data.get('updated_at', '')}\n\n"
            "## Viajes de personal PS a finales\n"
            f"{json.dumps(data.get('ps_travel_costs', []), indent=2, ensure_ascii=True)}\n\n"
            "## Pagos a hoteles (anticipos/liquidaciones/servicio)\n"
            f"{json.dumps(data.get('hotel_payments_advance_settlement', []), indent=2, ensure_ascii=True)}\n\n"
            "## Pagos a proveedores diversos\n"
            f"{json.dumps(data.get('supplier_payments', []), indent=2, ensure_ascii=True)}\n\n"
            "## Costos de servicios medicos\n"
            f"{json.dumps(data.get('medical_service_costs', []), indent=2, ensure_ascii=True)}\n\n"
            "## Costo de seguros\n"
            f"{json.dumps(data.get('insurance_costs', []), indent=2, ensure_ascii=True)}\n"
        )

    def _render_national_marketing_report(self, data: Dict[str, Any]) -> str:
        return (
            "# Reporte Nacional - Mercadotecnia\n\n"
            f"- Fecha de actualizacion: {data.get('updated_at', '')}\n\n"
            "## Proveedores en activacion de marcas\n"
            f"{json.dumps(data.get('onsite_brand_activation_providers', []), indent=2, ensure_ascii=True)}\n\n"
            "## Visitantes involucrados con patrocinador\n"
            f"{json.dumps(data.get('sponsor_visitors', []), indent=2, ensure_ascii=True)}\n\n"
            "## Actividades realizadas y resultados\n"
            f"{json.dumps(data.get('activities_and_results', []), indent=2, ensure_ascii=True)}\n\n"
            "## Evidencia fotografica\n"
            f"{json.dumps(data.get('photo_evidence', []), indent=2, ensure_ascii=True)}\n"
        )
