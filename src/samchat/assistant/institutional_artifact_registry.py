"""Institutional artifact registry for SamChat assistant planning.

The registry is a read-only map of business intelligence artifacts that already
exist in SamChat. It does not execute those artifacts; it tells the assistant
which institutional projection should be used for a class of question, what
source evidence it depends on, and whether it is currently exposed as a tool.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Optional

AuthorityLevel = Literal["read_only", "preview_only", "write_requires_approval"]
ArtifactStatus = Literal["wired", "available_not_wired", "partial", "planned"]
ArtifactDomain = Literal[
    "finance",
    "accounting",
    "operations",
    "tournament",
    "budget",
    "owner_pack",
    "institutional_memory",
    "cross_domain",
]


@dataclass(frozen=True)
class InstitutionalArtifactSpec:
    artifact_id: str
    domain: ArtifactDomain
    name: str
    purpose: str
    module_path: str
    entrypoint: str
    status: ArtifactStatus
    authority_level: AuthorityLevel = "read_only"
    assistant_tool: Optional[str] = None
    canonical_action: Optional[str] = None
    evidence_sources: tuple[str, ...] = field(default_factory=tuple)
    input_contract: tuple[str, ...] = field(default_factory=tuple)
    output_contract: tuple[str, ...] = field(default_factory=tuple)
    answers: tuple[str, ...] = field(default_factory=tuple)
    next_wiring_step: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ARTIFACTS: tuple[InstitutionalArtifactSpec, ...] = (
    InstitutionalArtifactSpec(
        artifact_id="finance.platform_snapshot",
        domain="finance",
        name="Finance Platform Snapshot",
        purpose="Read-only control projection over cash, payments, COI, DIOT/CFDI and finance brief.",
        module_path="samchat.finance_platform.service",
        entrypoint="build_finance_platform_snapshot",
        status="wired",
        assistant_tool="finance_strategy_snapshot",
        evidence_sources=("gastos", "documentos", "cfdi_reports", "polizas", "payment_run"),
        input_contract=("finance_source_snapshot",),
        output_contract=("cash_control_center", "accounting_close_center", "tax_readiness", "payment_run", "finance_brief"),
        answers=(
            "Como va finanzas?",
            "Que bloquea COI/DIOT?",
            "Que pagos o CFDI requieren atencion?",
        ),
    ),
    InstitutionalArtifactSpec(
        artifact_id="finance.closeout_diagnostics",
        domain="accounting",
        name="Finance Closeout Diagnostics",
        purpose="Explains whether an accounting/finance close is blocked by unbalanced policies, COI gaps or missing CFDI.",
        module_path="samchat.assistant.closeout_diagnostics",
        entrypoint="build_finance_closeout_diagnostics",
        status="wired",
        assistant_tool="finance_closeout_diagnostics",
        evidence_sources=("finance_platform.accounting_close_center", "finance_platform.tax_readiness"),
        input_contract=("year?", "month?", "scope", "include_medium"),
        output_contract=("status", "blockers", "source_summary", "safety_summary"),
        answers=(
            "Se puede cerrar la contabilidad?",
            "Por que no se puede cerrar el mes?",
            "Cuantas polizas no cuadran?",
        ),
    ),
    InstitutionalArtifactSpec(
        artifact_id="sports.platform_snapshot",
        domain="operations",
        name="Sports Platform Snapshot",
        purpose="Operational command projection for tournaments, teams, rosters, incidents, matchday and sponsor/media readiness.",
        module_path="samchat.sports_platform.service",
        entrypoint="build_sports_platform_snapshot",
        status="available_not_wired",
        canonical_action="operations.tournament_soul_snapshot",
        evidence_sources=("tournament_soul_snapshot", "teams", "players", "matches", "incidents", "media"),
        input_contract=("tournament_snapshot",),
        output_contract=("command_center", "roster_intelligence", "incident_center", "action_queue", "one_click_ops_brief"),
        answers=(
            "Que esta atorado en operaciones?",
            "Que equipos/jugadores tienen riesgo documental?",
            "Que falta para el dia de partido?",
        ),
        next_wiring_step="Expose a read-only assistant tool that returns the sports platform command summary for one tournament.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="sports.director_general_entity_dossier",
        domain="owner_pack",
        name="Director General Entity Dossier",
        purpose="Builds per-entity folders for owner requests: operations, finance and readiness.",
        module_path="samchat.sports_platform.director_general_dossier",
        entrypoint="build_director_general_entity_dossier",
        status="partial",
        canonical_action="operations.folder_planner_snapshot",
        evidence_sources=("tournament_soul_snapshot", "entity operations", "entity finance bridge"),
        input_contract=("tournament_snapshot",),
        output_contract=("entity_folders", "readiness", "missing_fields"),
        answers=(
            "Dame la carpeta por entidad del torneo.",
            "Que informacion falta para entregar el paquete del duenio?",
        ),
        next_wiring_step="Use owner_entity_dossier_audit wrapper before exposing live read tool; do not expose raw DG dossier.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="assistant.owner_entity_dossier_audit",
        domain="owner_pack",
        name="Owner Entity Dossier Audit",
        purpose="Assistant-facing wrapper that audits DG entity dossiers against Owner Pack expectations before wiring.",
        module_path="samchat.assistant.owner_entity_dossier_audit",
        entrypoint="build_owner_entity_dossier_audit_from_snapshot",
        status="available_not_wired",
        evidence_sources=("director_general_entity_dossier", "owner_pack_inventory", "tournament_soul_snapshot"),
        input_contract=("tournament_snapshot", "entity_name?"),
        output_contract=("decision", "supported_fields", "missing_fields", "redundancy_notes", "recommended_next_steps"),
        answers=(
            "Tiene sentido cablear el expediente por entidad?",
            "Que datos del paquete del duenio ya estan soportados por entidad?",
            "Que falta antes de presentar la carpeta como completa?",
        ),
        next_wiring_step="Add a live loader from tournament_soul_snapshot and expose as read-only assistant tool.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="tournament.soul_snapshot",
        domain="tournament",
        name="Tournament SOUL Snapshot",
        purpose="Canonical tournament operational case with compliance, entity folders, national phase, marketing, finance bridge and risk register.",
        module_path="samchat.tournaments_v2.services.soul_service",
        entrypoint="build_tournament_soul_snapshot",
        status="wired",
        canonical_action="operations.tournament_soul_snapshot",
        evidence_sources=("tournaments_v2", "entities", "teams", "players", "documents", "matches"),
        input_contract=("tournament_key", "include_finance?"),
        output_contract=("soul", "compliance", "entity_folder_seeds", "risk_register", "pending_actions"),
        answers=(
            "Cual es el estado institucional del torneo?",
            "Que entidades tienen expedientes incompletos?",
            "Que riesgos operativos hay?",
        ),
    ),
    InstitutionalArtifactSpec(
        artifact_id="accounting.historical_snapshot",
        domain="institutional_memory",
        name="Historical Accounting Snapshot",
        purpose="Historical COI accounting memory for trial balances, policies, quality flags and comparisons.",
        module_path="samchat.accounting_historical.service",
        entrypoint="load_historical_accounting_snapshot",
        status="available_not_wired",
        evidence_sources=("COI backups", "historical trial balances", "historical policies"),
        input_contract=("fiscal_year", "company_code"),
        output_contract=("accounts", "policies", "trial_balance", "quality_flags", "source_files"),
        answers=(
            "Como se contabilizo algo parecido antes?",
            "Que cuenta se uso historicamente para este proveedor/concepto?",
            "Que cambio contra el anio pasado?",
        ),
        next_wiring_step="Expose a read-only historical accounting precedent query for the assistant.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="budget.snapshot",
        domain="budget",
        name="Budget Snapshot",
        purpose="Budget artifact and operational budget projection with alerts, comparisons, actuals and forecast context.",
        module_path="samchat.budgets.service",
        entrypoint="build_budget_snapshot",
        status="wired",
        canonical_action="budgets.snapshot",
        evidence_sources=("budget_versions", "budget_lines", "expenses", "income_cfdi_links", "commitments"),
        input_contract=("edition_year", "tournament_id?", "scope?"),
        output_contract=("summary", "lines", "alerts", "actuals", "forecast"),
        answers=(
            "Como va el presupuesto?",
            "Que partidas estan excedidas?",
            "Como construir el presupuesto anual desde historico?",
        ),
    ),
    InstitutionalArtifactSpec(
        artifact_id="sam_inbox.payload",
        domain="cross_domain",
        name="Sam Inbox Payload",
        purpose="Unified attention queue from finance platform, CFDI, operations and direction snapshots.",
        module_path="samchat.sam_inbox.service",
        entrypoint="build_sam_inbox_payload",
        status="available_not_wired",
        evidence_sources=("finance_platform", "CFDI pending", "tournament operations", "direction snapshot"),
        input_contract=("employee", "period?", "tournament filters?"),
        output_contract=("items", "finance_brief", "operation_items", "direction_items"),
        answers=(
            "Que requiere atencion hoy?",
            "Que pendientes son de finanzas, operacion o direccion?",
        ),
        next_wiring_step="Expose as assistant read tool for daily operational briefing.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="expense.accounting_preview",
        domain="accounting",
        name="Expense Accounting Preview",
        purpose="Builds accounting preview for an expense before posting, including CFDI and accounting account context.",
        module_path="devnous.gastos.services.expense_accounting_service",
        entrypoint="build_expense_accounting_preview",
        status="wired",
        canonical_action="accounting.build_expense_preview",
        authority_level="preview_only",
        evidence_sources=("expense", "CFDI", "cuenta_contable", "budget/project context"),
        input_contract=("expense_id"),
        output_contract=("preview", "debe_haber", "tax_breakdown", "warnings"),
        answers=(
            "Como quedaria la prepoliza de este gasto?",
            "Que cuenta contable usaria este comprobante?",
        ),
    ),
)


def list_institutional_artifacts(
    *,
    domain: Optional[str] = None,
    status: Optional[str] = None,
    wired_only: bool = False,
) -> list[InstitutionalArtifactSpec]:
    specs: Iterable[InstitutionalArtifactSpec] = ARTIFACTS
    if domain:
        specs = [item for item in specs if item.domain == domain]
    if status:
        specs = [item for item in specs if item.status == status]
    if wired_only:
        specs = [item for item in specs if item.status == "wired"]
    return list(specs)


def get_institutional_artifact(artifact_id: str) -> Optional[InstitutionalArtifactSpec]:
    wanted = (artifact_id or "").strip()
    return next((item for item in ARTIFACTS if item.artifact_id == wanted), None)


def build_institutional_artifact_registry_report() -> dict[str, Any]:
    by_domain: dict[str, int] = {}
    by_status: dict[str, int] = {}
    wired: list[str] = []
    not_wired: list[str] = []
    for item in ARTIFACTS:
        by_domain[item.domain] = by_domain.get(item.domain, 0) + 1
        by_status[item.status] = by_status.get(item.status, 0) + 1
        if item.status == "wired":
            wired.append(item.artifact_id)
        else:
            not_wired.append(item.artifact_id)
    return {
        "registry_id": "samchat_institutional_artifact_registry_v1",
        "read_only": True,
        "artifact_count": len(ARTIFACTS),
        "by_domain": by_domain,
        "by_status": by_status,
        "wired_artifacts": sorted(wired),
        "not_wired_artifacts": sorted(not_wired),
        "artifacts": [item.to_dict() for item in ARTIFACTS],
    }


__all__ = [
    "ARTIFACTS",
    "InstitutionalArtifactSpec",
    "build_institutional_artifact_registry_report",
    "get_institutional_artifact",
    "list_institutional_artifacts",
]
