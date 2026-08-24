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
ConnectionDecision = Literal[
    "connect_now",
    "keep_internal",
    "merge_with_another",
    "obsolete",
    "needs_data_first",
]
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


@dataclass(frozen=True)
class InstitutionalArtifactConnectionDecision:
    artifact_id: str
    decision: ConnectionDecision
    rationale: str
    merge_target: Optional[str] = None
    data_prerequisites: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DECISION_LABELS: dict[ConnectionDecision, str] = {
    "connect_now": "conectar ahora",
    "keep_internal": "mantener interno",
    "merge_with_another": "fusionar con otro",
    "obsolete": "obsoleto",
    "needs_data_first": "necesita datos antes",
}


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
        status="partial",
        canonical_action="operations.tournament_soul_snapshot",
        evidence_sources=("tournament_soul_snapshot", "teams", "players", "matches", "incidents", "media"),
        input_contract=("tournament_snapshot",),
        output_contract=("command_center", "roster_intelligence", "incident_center", "action_queue", "one_click_ops_brief"),
        answers=(
            "Que esta atorado en operaciones?",
            "Que equipos/jugadores tienen riesgo documental?",
            "Que falta para el dia de partido?",
        ),
        next_wiring_step="Use sports_platform_audit wrapper first; expose only a narrowed operations status, not the raw snapshot.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="assistant.sports_operations_status",
        domain="operations",
        name="Sports Operations Status",
        purpose="Narrow assistant-safe read-only wrapper over Sports Platform: mission, incidents, roster risk, matchday state and action queue.",
        module_path="samchat.assistant.sports_operations_status",
        entrypoint="build_sports_operations_status_from_tournament_source",
        status="wired",
        assistant_tool="assistant_sports_operations_status",
        evidence_sources=("local_tournament_db", "sports.platform_snapshot", "tournament.soul_snapshot", "sports_platform_audit"),
        input_contract=("tournament_snapshot", "focus?", "max_actions?", "soul_wizard_payload?"),
        output_contract=("operational_status", "priorities", "top_actions", "roster_summary", "incident_summary", "matchday_summary", "wizard_alignment", "safety_summary"),
        answers=(
            "Que esta atorado en operaciones?",
            "Que acciones operativas tienen prioridad hoy?",
            "Que equipos, cedulas o incidentes requieren atencion?",
        ),
        next_wiring_step="Expand live local source coverage for schedule, communications and media; preserve SOUL Wizard alignment so creation drafts and operations status stay in one story.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="assistant.sports_platform_audit",
        domain="operations",
        name="Sports Platform Audit",
        purpose="Classifies Sports Platform modules before assistant wiring: assistant-ready summaries, internal sources and commercial/demo surfaces.",
        module_path="samchat.assistant.sports_platform_audit",
        entrypoint="build_sports_platform_audit_from_snapshot",
        status="available_not_wired",
        evidence_sources=("sports.platform_snapshot", "tournament.soul_snapshot"),
        input_contract=("tournament_snapshot", "focus?"),
        output_contract=("decision", "assistant_ready_modules", "internal_source_modules", "commercial_or_demo_modules", "recommended_next_steps"),
        answers=(
            "Tiene sentido cablear Sports Platform al asistente?",
            "Que modulos operativos estan listos para consulta read-only?",
            "Que partes son solo demo/comerciales y no deben exponerse crudas?",
        ),
        next_wiring_step="Create a narrowed live operations status tool using mission_control, action_queue, incident_center and roster_intelligence.",
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
        artifact_id="assistant.owner_entity_dossier_live",
        domain="owner_pack",
        name="Owner Entity Dossier Live",
        purpose="Read-only live local wrapper for DG/Owner entity folders with supported evidence, missing fields and aggregate-only non-claims.",
        module_path="samchat.assistant.owner_entity_dossier_live",
        entrypoint="build_owner_entity_dossier_live_from_tournament_source",
        status="wired",
        assistant_tool="assistant_owner_entity_dossier_live",
        evidence_sources=("local_tournament_db", "owner_entity_dossier_audit", "director_general_entity_dossier"),
        input_contract=("tournament_id xor tournament_name", "entity_name?"),
        output_contract=("status", "source_summary", "audit", "missing_evidence", "non_claims", "safety_summary"),
        answers=(
            "Que carpeta de entidad puedo mostrarle al Director General?",
            "Que evidencia viva existe para esta entidad?",
            "Que falta para completar el expediente de una entidad?",
        ),
        next_wiring_step="Improve local per-entity source coverage for contacts, finances, uniforms, travel and classification before claiming complete folders.",
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
        artifact_id="assistant.owner_pack_readiness",
        domain="owner_pack",
        name="Owner Pack Readiness",
        purpose="Composes owner status, inventory and live workspace evidence into one read-only readiness answer for the assistant.",
        module_path="samchat.assistant.owner_pack_readiness",
        entrypoint="build_owner_pack_readiness_from_scope",
        status="wired",
        assistant_tool="assistant_owner_pack_readiness",
        evidence_sources=("owner_pack_status", "owner_pack_inventory", "owner_pack_live_snapshot", "tournament SOUL/workspace"),
        input_contract=("scope", "tournament_slug?", "entity_name?"),
        output_contract=("status", "readiness_score", "surfaces", "evidence_found", "missing_evidence", "next_questions", "safety_summary"),
        answers=(
            "Que falta para contestarle al Director General?",
            "Que tan listo esta el Owner Pack de este torneo?",
            "Que evidencia hay y que falta para una carpeta de entidad?",
        ),
    ),
    InstitutionalArtifactSpec(
        artifact_id="assistant.soul_wizard_contract",
        domain="tournament",
        name="SOUL Wizard Draft and Activation Preview",
        purpose="Read-only/preview-only tournament creation workspace: stepper contract, clone-from-existing draft, phases, dates, activities and activation diff before any operational write.",
        module_path="samchat.assistant.soul_wizard",
        entrypoint="build_soul_wizard_payload",
        status="wired",
        canonical_action="assistant.soul_wizard_review",
        evidence_sources=("operator input", "optional source tournament snapshot", "tournament.soul_snapshot"),
        input_contract=("tournament_name", "edition_year", "categories", "branches", "phases", "activities", "source_tournament_snapshot?"),
        output_contract=("contract", "draft", "readiness", "owner_pack_bridge", "clone_metadata", "activation_diff", "validation_issues"),
        answers=(
            "Que datos faltan para crear el SOUL del torneo?",
            "Como deberia capturar Operaciones un torneo paso a paso?",
            "Que cambia si clono este torneo?",
            "El borrador del torneo esta listo para revision?",
        ),
        next_wiring_step="Use SOUL owner_pack_bridge as context substrate for Owner Entity Folder Workspace; keep activation writes behind explicit review/approval.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="assistant.soul_wizard_owner_pack_bridge",
        domain="owner_pack",
        name="SOUL Wizard Owner Pack Bridge",
        purpose="Read-only bridge that converts SOUL Wizard phases, dates and activities into Owner Pack planning context without creating folders or live operations.",
        module_path="samchat.assistant.soul_wizard",
        entrypoint="build_soul_wizard_owner_pack_bridge",
        status="wired",
        authority_level="read_only",
        canonical_action="assistant.soul_wizard_review",
        evidence_sources=("assistant.soul_wizard_contract", "operator input", "optional source tournament snapshot"),
        input_contract=("soul_wizard_payload or draft",),
        output_contract=("tournament", "phases", "activity_count", "owner_pack_support", "missing_paths", "non_claims"),
        answers=(
            "Que fases, fechas y actividades del Wizard alimentan el Owner Pack?",
            "Que le falta al borrador del torneo para servir como expediente del duenio?",
            "Puedo usar este SOUL como contexto de carpeta sin crear operaciones?",
        ),
        next_wiring_step="Feed this bridge into Owner Entity Folder Workspace cards before enabling any folder export or tournament activation.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="assistant.owner_variable_query",
        domain="owner_pack",
        name="Owner Pack Variable Query Resolver",
        purpose="Maps owner natural-language questions to canonical Owner Pack variables and reports supported, partial, missing or conflicting evidence without guessing.",
        module_path="samchat.assistant.owner_variable_query",
        entrypoint="build_owner_variable_query_report",
        status="wired",
        authority_level="read_only",
        assistant_tool="assistant_owner_variable_query",
        evidence_sources=("owner_pack_inventory", "owner_pack_live_evidence", "assistant.soul_wizard_owner_pack_bridge"),
        input_contract=("question", "tournament_id?", "tournament_name?", "entity_name?", "soul_wizard_payload?"),
        output_contract=("status", "candidates", "resolutions", "evidence", "missing_reason", "conflict_values", "safety_summary"),
        answers=(
            "Que sabe SamChat sobre esta variable del Owner Pack?",
            "Hay evidencia para contestarle al duenio esta pregunta?",
            "Que falta para responder una pregunta del Director General sin inventar?",
        ),
        next_wiring_step="Use variable query results inside conversational owner answers; keep unresolved variables as missing evidence, never as inferred facts.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="assistant.owner_variable_answer",
        domain="owner_pack",
        name="Owner Variable Conversational Answer",
        purpose="Renders supported, partial, missing, conflicting or unmapped Owner Pack variable query reports into owner-facing Spanish without creating new factual claims.",
        status="wired",
        module_path="samchat.assistant.owner_variable_answer",
        entrypoint="render_owner_variable_query_answer",
        authority_level="read_only",
        assistant_tool="assistant_owner_variable_query",
        evidence_sources=(
            "assistant.owner_variable_query",
        ),
        input_contract=(
            "OwnerVariableQueryReport from assistant.owner_variable_query",
        ),
        output_contract=(
            "headline",
            "short_answer",
            "detail_lines",
            "evidence_lines",
            "missing_lines",
            "conflict_lines",
            "rendered_text",
            "safety_summary",
        ),
        next_wiring_step="Use conversation_answer.rendered_text as the owner-facing assistant reply while preserving structured resolver payload for audit.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="assistant.owner_entity_folder_workspace",
        domain="owner_pack",
        name="Owner Entity Folder Workspace",
        purpose="Composed assistant workspace that turns SOUL, live entity dossiers, owner readiness and finance/operations evidence into an owner-facing folder preview.",
        module_path="samchat.assistant.owner_entity_folder_workspace",
        entrypoint="build_owner_entity_folder_workspace",
        status="wired",
        authority_level="preview_only",
        assistant_tool="assistant_owner_entity_folder_workspace",
        evidence_sources=(
            "assistant.owner_entity_dossier_live",
            "assistant.owner_pack_readiness",
            "tournament.soul_snapshot",
            "assistant.sports_operations_status",
            "finance.platform_snapshot",
        ),
        input_contract=("tournament_id xor tournament_name", "entity_name?", "scope?", "soul_wizard_payload?"),
        output_contract=("workspace_cards", "folder_sections", "soul_wizard_plan", "evidence", "missing_fields", "non_claims", "next_questions", "preview"),
        answers=(
            "Prepara la carpeta de una entidad para el Director General.",
            "Que informacion falta para la carpeta de esta entidad?",
            "Muestrame el expediente operativo y financiero de esta entidad.",
        ),
        next_wiring_step="Use workspace cards as the assistant preview surface; no folder write, export or publication without explicit approval.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="assistant.owner_pack_export_preview",
        domain="owner_pack",
        name="Owner Pack Export / Print Preview",
        purpose="Read-only package preview for the owner pack: HTML/print, PDF preview and Excel-like index with evidence, missing fields and non-claims.",
        module_path="samchat.assistant.owner_pack_export_preview",
        entrypoint="build_owner_pack_export_preview",
        status="wired",
        authority_level="preview_only",
        assistant_tool="assistant_owner_pack_export_preview",
        evidence_sources=(
            "assistant.owner_pack_readiness",
            "assistant.owner_entity_folder_workspace",
            "assistant.owner_variable_query",
            "assistant.soul_wizard_owner_pack_bridge",
        ),
        input_contract=("dashboard/readiness", "workspace?", "variable_answers?", "soul_bridge?"),
        output_contract=("html_preview", "pdf_preview", "excel_index", "evidence_links", "missing_items", "non_claims", "safety_summary"),
        answers=(
            "Genera una vista previa del Owner Pack.",
            "Que contiene el paquete revisable y que falta?",
            "Prepara el Owner Pack para imprimir sin publicarlo.",
        ),
        next_wiring_step="Use only as read-only preview/export surface; publication and writes remain behind explicit authority boundary.",
    ),
    InstitutionalArtifactSpec(
        artifact_id="assistant.owner_operator_workflow",
        domain="owner_pack",
        name="Owner Operator Workflow Preview",
        purpose=(
            "Legacy owner/operator workflow that assesses owner needs and "
            "builds a proposed folder structure. It is retained as an "
            "internal benchmark/helper after Slice 7; conversation runtime "
            "must use readiness or entity folder workspace wrappers."
        ),
        module_path="samchat.assistant.owner_operator_workflow",
        entrypoint="run_owner_operator_workflow",
        status="available_not_wired",
        authority_level="preview_only",
        assistant_tool=None,
        evidence_sources=(
            "owner_needs_eval",
            "business_diff_preview",
            "owner_folder_builder",
            "owner_folder_revision",
            "owner_response_pack",
        ),
        input_contract=(
            "owner context request",
            "owner needs prompt",
            "requested_revision?",
        ),
        output_contract=(
            "assessment",
            "preview",
            "folder_proposal",
            "revision?",
            "response_pack",
            "safety_summary",
            "execution_status",
            "writes_attempted",
            "side_effects_detected",
        ),
        answers=(
            "Que necesita Direccion del Owner Pack?",
            "Prepara una respuesta o estructura propuesta para el duenio.",
            "Que evidencia falta antes de cerrar la carpeta?",
        ),
        next_wiring_step=(
            "Merged into assistant.owner_pack_readiness and "
            "assistant.owner_entity_folder_workspace; do not expose as a "
            "conversation tool."
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
        artifact_id="assistant.historical_accounting_precedent",
        domain="institutional_memory",
        name="Historical Accounting Precedent Query",
        purpose="Read-only query over historical accounting lines to find precedent account candidates for similar concepts, providers, projects or accounts.",
        module_path="samchat.assistant.historical_accounting_precedent",
        entrypoint="query_historical_accounting_precedents",
        status="wired",
        assistant_tool="assistant_historical_accounting_precedent",
        evidence_sources=("historical_policy_lines", "historical_policy_headers", "accounting_import_runs"),
        input_contract=("query?", "company_code", "fiscal_year?", "account_code?", "limit"),
        output_contract=("status", "source_summary", "candidates", "non_claims", "safety_summary"),
        answers=(
            "Que cuenta usamos historicamente para algo parecido?",
            "Hay precedentes contables para este proveedor o concepto?",
            "Que evidencia historica respalda esta clasificacion?",
        ),
        next_wiring_step="Use candidates as evidence in accounting cleanup previews; never auto-assign accounts without finance approval.",
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


ARTIFACT_CONNECTION_DECISIONS: tuple[InstitutionalArtifactConnectionDecision, ...] = (
    InstitutionalArtifactConnectionDecision(
        artifact_id="finance.platform_snapshot",
        decision="connect_now",
        rationale="Ya esta cableado como snapshot ejecutivo de finanzas; debe seguir disponible como fuente read-only para preguntas de caja, pagos, COI, DIOT y CFDI.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="finance.closeout_diagnostics",
        decision="connect_now",
        rationale="Ya responde bloqueos de cierre contable con fuente controlada; es central para preguntas tipo 'por que no cierra contabilidad'.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="sports.platform_snapshot",
        decision="merge_with_another",
        rationale="Es una proyeccion operacional cruda y amplia; no conviene exponerla directa al asistente cuando ya existe un wrapper mas seguro.",
        merge_target="assistant.sports_operations_status",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.sports_operations_status",
        decision="connect_now",
        rationale="Es el wrapper read-only estrecho para estado operativo; puede alimentar respuestas y Owner Pack sin mostrar superficies internas crudas.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.sports_platform_audit",
        decision="merge_with_another",
        rationale="Su valor principal fue auditar que partes de Sports Platform eran seguras; en runtime debe quedar absorbido por el status operacional y pruebas de regresion.",
        merge_target="assistant.sports_operations_status",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="sports.director_general_entity_dossier",
        decision="merge_with_another",
        rationale="El dossier DG crudo debe convertirse en fuente del workspace del duenio, no en superficie directa, para preservar non-claims y faltantes.",
        merge_target="assistant.owner_entity_folder_workspace",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.owner_entity_dossier_audit",
        decision="keep_internal",
        rationale="Sirve como control de calidad y evidencia de cobertura, pero no como herramienta conversacional principal para el usuario final.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.owner_entity_dossier_live",
        decision="connect_now",
        rationale="Ya es la envoltura viva read-only del expediente por entidad y debe alimentar readiness, carpetas y respuestas con evidencia.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="tournament.soul_snapshot",
        decision="needs_data_first",
        rationale="El concepto es canonico, pero la cobertura real por torneo aun es irregular; no debe prometerse un SOUL completo por torneo sin datos cargados.",
        data_prerequisites=("crear/cargar SOUL por torneo activo", "validar fases, fechas y actividades", "mapear entidades y equipos reales"),
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.owner_pack_readiness",
        decision="connect_now",
        rationale="Es la respuesta navegable minima del Owner Pack: cobertura, faltantes, fuentes y siguientes preguntas.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.soul_wizard_contract",
        decision="connect_now",
        rationale="Debe seguir conectado como borrador/preview para crear torneos paso a paso sin writes automaticos.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.soul_wizard_owner_pack_bridge",
        decision="connect_now",
        rationale="Une el wizard con el Owner Pack y permite que fases, fechas y actividades alimenten carpetas sin activar operaciones reales.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.owner_variable_query",
        decision="connect_now",
        rationale="Es el resolver de variables concretas del duenio; debe contestar con evidencia o declarar dato faltante sin inventar.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.owner_variable_answer",
        decision="connect_now",
        rationale="Convierte los resultados estructurados en respuesta ejecutiva humana, preservando faltantes y non-claims.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.owner_entity_folder_workspace",
        decision="connect_now",
        rationale="Es la superficie de carpeta operacional read-only que el duenio puede entender: operaciones, finanzas, evidencia, faltantes y preguntas.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.owner_pack_export_preview",
        decision="connect_now",
        rationale="Es la superficie segura de export/impresion del Owner Pack: consume readiness/workspace, muestra faltantes y non-claims, y no publica ni escribe sin autoridad.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.owner_operator_workflow",
        decision="merge_with_another",
        rationale="Se traslapa con readiness, variable answers y folder workspace; debe mantenerse como preview de conversacion y consolidarse en el flujo Owner Pack.",
        merge_target="assistant.owner_pack_readiness",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="accounting.historical_snapshot",
        decision="needs_data_first",
        rationale="La memoria historica contable es valiosa, pero antes de exponerla hace falta confirmar fuentes COI, calidad y cobertura por ejercicio/empresa.",
        data_prerequisites=("confirmar dumps COI disponibles", "validar calidad de polizas historicas", "definir company_code y ejercicios canonicos"),
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="assistant.historical_accounting_precedent",
        decision="connect_now",
        rationale="Ya es el wrapper read-only adecuado para precedentes contables; informa, pero no asigna automaticamente cuentas ni autoridad.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="budget.snapshot",
        decision="connect_now",
        rationale="Es fuente canonica de presupuesto, alertas, comparaciones, actuals y forecast para preguntas ejecutivas y de control presupuestal.",
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="sam_inbox.payload",
        decision="needs_data_first",
        rationale="La bandeja unificada puede ser muy util, pero antes de conectarla hay que evitar duplicados, revisar permisos y confirmar fuentes vivas por usuario.",
        data_prerequisites=("deduplicar pendientes cross-domain", "atar visibilidad a actor/rol", "validar fuentes de finance/operaciones/direccion"),
    ),
    InstitutionalArtifactConnectionDecision(
        artifact_id="expense.accounting_preview",
        decision="connect_now",
        rationale="Debe permanecer como preview contable de gasto; es util para limpieza y COI siempre que siga sin postear efectos reales sin aprobacion.",
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


def get_institutional_artifact_connection_decision(
    artifact_id: str,
) -> Optional[InstitutionalArtifactConnectionDecision]:
    wanted = (artifact_id or "").strip()
    return next((item for item in ARTIFACT_CONNECTION_DECISIONS if item.artifact_id == wanted), None)


def build_institutional_artifact_connection_review() -> dict[str, Any]:
    by_decision: dict[str, int] = {key: 0 for key in DECISION_LABELS}
    decisions_by_id = {item.artifact_id: item for item in ARTIFACT_CONNECTION_DECISIONS}
    items: list[dict[str, Any]] = []
    for artifact in ARTIFACTS:
        review = decisions_by_id.get(artifact.artifact_id)
        if review is None:
            raise ValueError(f"Missing connection decision for artifact {artifact.artifact_id}")
        by_decision[review.decision] = by_decision.get(review.decision, 0) + 1
        item = artifact.to_dict()
        item["connection_review"] = review.to_dict()
        item["connection_review"]["label"] = DECISION_LABELS[review.decision]
        items.append(item)

    def _ids_for(decision: ConnectionDecision) -> list[str]:
        return sorted(item.artifact_id for item in ARTIFACT_CONNECTION_DECISIONS if item.decision == decision)

    return {
        "review_id": "samchat_institutional_artifact_connection_review_v1",
        "read_only": True,
        "artifact_count": len(ARTIFACTS),
        "decision_labels": dict(DECISION_LABELS),
        "by_decision": by_decision,
        "safe_to_wire_now": _ids_for("connect_now"),
        "internal_only": _ids_for("keep_internal"),
        "merge_queue": _ids_for("merge_with_another"),
        "obsolete": _ids_for("obsolete"),
        "needs_data_first": _ids_for("needs_data_first"),
        "items": items,
        "non_claims": (
            "This review does not connect new runtime tools.",
            "A connect_now verdict means the artifact is suitable for assistant exposure under its existing authority level, not that writes are authorized.",
            "A needs_data_first verdict means SamChat must report missing evidence rather than infer facts.",
        ),
    }


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
    "ARTIFACT_CONNECTION_DECISIONS",
    "DECISION_LABELS",
    "InstitutionalArtifactConnectionDecision",
    "InstitutionalArtifactSpec",
    "build_institutional_artifact_connection_review",
    "build_institutional_artifact_registry_report",
    "get_institutional_artifact",
    "get_institutional_artifact_connection_decision",
    "list_institutional_artifacts",
]
