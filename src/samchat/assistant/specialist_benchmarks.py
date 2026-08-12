"""Seed benchmarks for SamChat specialist-agent workflows.

These are intentionally tiny, deterministic fixtures. They are not production
truth; they are regression seeds that encode the first Plataforma-style cases
for the Harvey-inspired task -> case -> artifact -> rubric loop.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .operational_case import CaseRelationship, EvidenceRef, OperationalCase
from .samchat_task_schema import RubricCriterion, SamchatTask
from .specialist_agents import SpecialistWorkflowResult
from .specialist_business_diff import (
    SpecialistBusinessDiffPreview,
    create_specialist_business_diff_preview,
)
from .specialist_orchestrator import OrchestratorResult, run_specialist_orchestrator
from .specialist_harness import FAIL, PASS, CriterionResult


@dataclass(frozen=True)
class SpecialistBenchmark:
    task: SamchatTask
    cases: Tuple[OperationalCase, ...]

    def to_dict(self, *, include_private_rubric: bool = False) -> Dict[str, Any]:
        return {
            "task": self.task.to_dict(include_private_rubric=include_private_rubric),
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(frozen=True)
class WorkflowBenchmarkResult:
    task_id: str
    status: str
    workflow: SpecialistWorkflowResult
    criteria: Tuple[CriterionResult, ...]
    orchestrator: OrchestratorResult
    business_preview: SpecialistBusinessDiffPreview

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "workflow": self.workflow.to_dict(),
            "orchestrator": {
                key: value
                for key, value in self.orchestrator.to_dict().items()
                if key != "workflow"
            },
            "business_preview": self.business_preview.to_dict(),
            "criteria": [criterion.to_dict() for criterion in self.criteria],
        }


def _criterion_result(
    criterion: RubricCriterion,
    status: str,
    reason: str,
) -> CriterionResult:
    return CriterionResult(
        criterion_id=criterion.criterion_id,
        status=status,
        reason=reason,
    )


def _proposal_tuples(workflow: SpecialistWorkflowResult) -> set[tuple[str, str, Any]]:
    proposal_items = workflow.finance.content.get("proposal_items") or []
    return {
        (
            str(item.get("case_id")),
            str(item.get("fact_key")),
            item.get("value"),
        )
        for item in proposal_items
    }


def _evaluate_workflow_criterion(
    workflow: SpecialistWorkflowResult,
    criterion: RubricCriterion,
) -> CriterionResult:
    checks = dict(criterion.checks or {})

    if checks.get("workflow_status") and workflow.status != checks["workflow_status"]:
        return _criterion_result(
            criterion,
            FAIL,
            f"workflow_status:{workflow.status}",
        )

    if checks.get("execution_allowed") is not None:
        actual = workflow.finance.content.get("execution_allowed")
        if actual is not checks["execution_allowed"]:
            return _criterion_result(
                criterion,
                FAIL,
                f"execution_allowed:{actual}",
            )

    if checks.get("authority_boundary"):
        actual = workflow.finance.content.get("authority_boundary")
        if actual != checks["authority_boundary"]:
            return _criterion_result(
                criterion,
                FAIL,
                f"authority_boundary:{actual}",
            )

    expected_items = checks.get("expected_proposal_items") or []
    actual_items = _proposal_tuples(workflow)
    for item in expected_items:
        expected = (
            str(item.get("case_id")),
            str(item.get("fact_key")),
            item.get("value"),
        )
        if expected not in actual_items:
            return _criterion_result(
                criterion,
                FAIL,
                f"missing_proposal_item:{expected}",
            )

    forbidden_items = checks.get("forbidden_proposal_items") or []
    for item in forbidden_items:
        forbidden = (
            str(item.get("case_id")),
            str(item.get("fact_key")),
            item.get("value"),
        )
        if forbidden in actual_items:
            return _criterion_result(
                criterion,
                FAIL,
                f"forbidden_proposal_item:{forbidden}",
            )

    if checks.get("min_missing_evidence", 0):
        missing = workflow.knowledge.content.get("missing_evidence") or []
        if len(missing) < int(checks["min_missing_evidence"]):
            return _criterion_result(
                criterion,
                FAIL,
                f"missing_evidence_count:{len(missing)}",
            )

    if checks.get("requires_unsupported_claims", False):
        if not workflow.verification.unsupported_claims:
            return _criterion_result(
                criterion,
                FAIL,
                "unsupported_claims_absent",
            )

    if checks.get("forbid_unsupported_claims", False):
        if workflow.verification.unsupported_claims:
            return _criterion_result(
                criterion,
                FAIL,
                "unsupported_claims_present",
            )

    return _criterion_result(criterion, PASS, "criterion_satisfied")


def run_seed_benchmark(benchmark: SpecialistBenchmark) -> WorkflowBenchmarkResult:
    benchmark.task.validate()
    orchestrator = run_specialist_orchestrator(
        task=benchmark.task.agent_visible(),
        cases=benchmark.cases,
    )
    workflow = orchestrator.workflow
    criteria = tuple(
        _evaluate_workflow_criterion(workflow, criterion)
        for criterion in benchmark.task.criteria
    )
    business_preview = create_specialist_business_diff_preview(
        task=benchmark.task.agent_visible(),
        workflow=workflow,
    )
    status = (
        PASS
        if orchestrator.status == PASS and all(item.status == PASS for item in criteria)
        else FAIL
    )
    return WorkflowBenchmarkResult(
        task_id=benchmark.task.task_id,
        status=status,
        workflow=workflow,
        criteria=criteria,
        orchestrator=orchestrator,
        business_preview=business_preview,
    )


def run_seed_benchmarks(
    benchmarks: Iterable[SpecialistBenchmark] | None = None,
) -> Dict[str, Any]:
    selected = tuple(benchmarks or build_seed_benchmarks())
    results = tuple(run_seed_benchmark(benchmark) for benchmark in selected)
    return {
        "total": len(results),
        "passed": sum(1 for result in results if result.status == PASS),
        "failed": sum(1 for result in results if result.status != PASS),
        "status": PASS if all(result.status == PASS for result in results) else FAIL,
        "results": [result.to_dict() for result in results],
    }


def _finance_task(
    *,
    task_id: str,
    title: str,
    case_type: str,
    allowed_case_ids: Sequence[str],
    expected_items: Sequence[Mapping[str, Any]],
    min_missing_evidence: int = 0,
    tags: Sequence[str] = (),
) -> SamchatTask:
    checks: Dict[str, Any] = {
        "workflow_status": PASS,
        "execution_allowed": False,
        "authority_boundary": "human_approval_required",
        "expected_proposal_items": list(expected_items),
        "forbid_unsupported_claims": True,
    }
    if min_missing_evidence:
        checks["min_missing_evidence"] = min_missing_evidence
    return SamchatTask(
        task_id=task_id,
        title=title,
        agent_type="finance",
        case_type=case_type,
        instructions=(
            "Usa precedentes operativos para preparar una propuesta read-only. "
            "No ejecutes acciones reales y reporta evidencia faltante."
        ),
        allowed_case_ids=tuple(allowed_case_ids),
        allowed_tools=("case_search", "evidence_lookup", "finance_preview"),
        expected_output_artifacts=(
            "precedent_pack",
            "verification_report",
            "finance_proposal",
        ),
        criteria=(
            RubricCriterion(
                criterion_id=f"{task_id}-C1",
                description="Workflow produces only verified preview items.",
                checks=checks,
            ),
        ),
        tags=tuple(tags),
    )


def _expense_amex_benchmark() -> SpecialistBenchmark:
    case = OperationalCase(
        case_id="expense-amex-ref-28",
        case_type="expense_report",
        title="Referencia 28 Operaciones - comprobacion AMEX Odilon",
        facts={
            "amount": 3067.43,
            "amount_evidence_id": "EV-AMEX-CFDI-TOTAL",
            "supplier": "AEROLINEA TARIFA AEREA PNR LE8KXZ",
            "supplier_evidence_id": "EV-AMEX-CFDI-SUPPLIER",
            "account": "5300-006-007",
            "account_evidence_id": "EV-AMEX-ACCOUNT",
            "operaciones_ref": "28",
            "operaciones_ref_evidence_id": "EV-AMEX-REPORT",
            "system_ref": "I-991520",
            "system_ref_evidence_id": "EV-AMEX-REPORT",
            "amex_card_label": "AMEX FGV 45007",
            "amex_card_label_evidence_id": "EV-AMEX-STATEMENT",
            "pending_user_note": "requiere revision humana de soporte AMEX",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-AMEX-CFDI-TOTAL",
                source_type="cfdi",
                title="CFDI vinculado al gasto AMEX",
                excerpt="Total CFDI 3067.43 MXN",
            ),
            EvidenceRef(
                evidence_id="EV-AMEX-CFDI-SUPPLIER",
                source_type="cfdi",
                title="Emisor/concepto CFDI AMEX",
            ),
            EvidenceRef(
                evidence_id="EV-AMEX-ACCOUNT",
                source_type="catalog",
                title="Cuenta de gastos de viaje AMEX",
            ),
            EvidenceRef(
                evidence_id="EV-AMEX-REPORT",
                source_type="expense_report",
                title="Informe de gastos I-991520 REF 28",
            ),
            EvidenceRef(
                evidence_id="EV-AMEX-STATEMENT",
                source_type="amex_statement",
                title="Estado de cuenta AMEX FGV 45007",
            ),
        ),
    )
    task = _finance_task(
        task_id="SAMCHAT-FIN-AMEX-001",
        title="Prepare AMEX comprobacion preview from verified evidence",
        case_type="expense_report",
        allowed_case_ids=(case.case_id,),
        expected_items=(
            {"case_id": case.case_id, "fact_key": "amount", "value": 3067.43},
            {
                "case_id": case.case_id,
                "fact_key": "supplier",
                "value": "AEROLINEA TARIFA AEREA PNR LE8KXZ",
            },
            {"case_id": case.case_id, "fact_key": "account", "value": "5300-006-007"},
        ),
        min_missing_evidence=1,
        tags=("finance", "amex", "expense_report"),
    )
    return SpecialistBenchmark(task=task, cases=(case,))


def _owner_entity_folder_benchmark() -> SpecialistBenchmark:
    case = OperationalCase(
        case_id="tournament-dcc-entity-bimbo",
        case_type="tournament",
        title="DCC Nacional - carpeta entidad Bimbo",
        facts={
            "amount": 1972903.00,
            "amount_evidence_id": "EV-DCC-CFDI",
            "supplier": "BIMBO BIM011108DJ5",
            "supplier_evidence_id": "EV-DCC-CFDI",
            "account": "4100-001-004",
            "account_evidence_id": "EV-DCC-BUDGET-LINE",
            "tournament": "De la Calle a la Cancha",
            "tournament_evidence_id": "EV-DCC-TOURNAMENT",
            "budget_line": "DCC Nacional",
            "budget_line_evidence_id": "EV-DCC-BUDGET-LINE",
            "missing_contact_birthdate": "unknown",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-DCC-CFDI",
                source_type="cfdi",
                title="Factura emitida Bimbo folio 630",
                excerpt="UUID 669DBF39... Total 1,972,903.00",
            ),
            EvidenceRef(
                evidence_id="EV-DCC-BUDGET-LINE",
                source_type="budget",
                title="Partida presupuestal DCC Nacional 4100-001-004",
            ),
            EvidenceRef(
                evidence_id="EV-DCC-TOURNAMENT",
                source_type="tournament",
                title="Torneo De la Calle a la Cancha 2026",
            ),
        ),
    )
    task = _finance_task(
        task_id="SAMCHAT-OWNER-DCC-001",
        title="Prepare owner entity-folder financial preview for DCC",
        case_type="tournament",
        allowed_case_ids=(case.case_id,),
        expected_items=(
            {"case_id": case.case_id, "fact_key": "amount", "value": 1972903.00},
            {"case_id": case.case_id, "fact_key": "supplier", "value": "BIMBO BIM011108DJ5"},
            {"case_id": case.case_id, "fact_key": "account", "value": "4100-001-004"},
        ),
        min_missing_evidence=1,
        tags=("owner_pack", "tournament", "cxc"),
    )
    return SpecialistBenchmark(task=task, cases=(case,))


def _supplier_history_benchmark() -> SpecialistBenchmark:
    case = OperationalCase(
        case_id="supplier-hotel-leon-001",
        case_type="supplier",
        title="Proveedor hospedaje Leon - antecedente comprobacion",
        facts={
            "amount": 128.00,
            "amount_evidence_id": "EV-HOTEL-CFDI-TOTAL",
            "supplier": "HOTEL LEON",
            "supplier_evidence_id": "EV-HOTEL-CFDI-SUPPLIER",
            "account": "5300-006-010",
            "account_evidence_id": "EV-HOTEL-ACCOUNT",
            "local_tax": "ISH",
            "local_tax_evidence_id": "EV-HOTEL-CFDI-TAX",
            "decision": "Hospedaje requiere capturar ISH como impuesto local",
            "decision_evidence_id": "EV-HOTEL-RULE",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-HOTEL-CFDI-TOTAL",
                source_type="cfdi",
                title="CFDI hospedaje total 128.00",
            ),
            EvidenceRef(
                evidence_id="EV-HOTEL-CFDI-SUPPLIER",
                source_type="cfdi",
                title="CFDI emisor Hotel Leon",
            ),
            EvidenceRef(
                evidence_id="EV-HOTEL-CFDI-TAX",
                source_type="cfdi",
                title="Desglose fiscal ISH",
            ),
            EvidenceRef(
                evidence_id="EV-HOTEL-ACCOUNT",
                source_type="catalog",
                title="Cuenta contable hospedaje",
            ),
            EvidenceRef(
                evidence_id="EV-HOTEL-RULE",
                source_type="precedent",
                title="Regla operativa para impuesto sobre hospedaje",
            ),
        ),
        relationships=(
            CaseRelationship(
                relationship_type="similar_issue",
                target_case_id="expense-amex-ref-28",
            ),
        ),
    )
    task = _finance_task(
        task_id="SAMCHAT-SUPPLIER-HOTEL-001",
        title="Recover supplier precedent for lodging tax classification",
        case_type="supplier",
        allowed_case_ids=(case.case_id,),
        expected_items=(
            {"case_id": case.case_id, "fact_key": "amount", "value": 128.00},
            {"case_id": case.case_id, "fact_key": "supplier", "value": "HOTEL LEON"},
            {"case_id": case.case_id, "fact_key": "account", "value": "5300-006-010"},
        ),
        tags=("supplier", "lodging_tax", "precedent"),
    )
    return SpecialistBenchmark(task=task, cases=(case,))




def _team_registration_benchmark() -> SpecialistBenchmark:
    case = OperationalCase(
        case_id="team-aguiluchos-juvenil-001",
        case_type="team",
        title="Dep. Aguiluchos juvenil - registro parcial",
        facts={
            "amount": 0.0,
            "amount_evidence_id": "EV-TEAM-ROSTER",
            "supplier": "DEP. AGUILUCHOS",
            "supplier_evidence_id": "EV-TEAM-HEADER",
            "account": "OPERACIONES-REGISTRO",
            "account_evidence_id": "EV-TEAM-RULE",
            "team_name": "Dep. Aguiluchos",
            "team_name_evidence_id": "EV-TEAM-HEADER",
            "category": "Juvenil",
            "category_evidence_id": "EV-TEAM-HEADER",
            "validated_players": 14,
            "validated_players_evidence_id": "EV-TEAM-ROSTER",
            "missing_page_three": "not_required_until_page_two_full",
            "missing_page_three_evidence_id": "EV-TEAM-RULE",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-TEAM-HEADER",
                source_type="team",
                title="Cedula Dep. Aguiluchos encabezado",
            ),
            EvidenceRef(
                evidence_id="EV-TEAM-ROSTER",
                source_type="player",
                title="Roster jugadores Dep. Aguiluchos",
            ),
            EvidenceRef(
                evidence_id="EV-TEAM-RULE",
                source_type="canon",
                title="Regla tercera pagina solo si segunda llena",
            ),
        ),
    )
    task = _finance_task(
        task_id="SAMCHAT-TEAM-REG-001",
        title="Recover team registration facts for operations review",
        case_type="team",
        allowed_case_ids=(case.case_id,),
        expected_items=(
            {"case_id": case.case_id, "fact_key": "amount", "value": 0.0},
            {"case_id": case.case_id, "fact_key": "supplier", "value": "DEP. AGUILUCHOS"},
            {
                "case_id": case.case_id,
                "fact_key": "account",
                "value": "OPERACIONES-REGISTRO",
            },
        ),
        tags=("operations", "team", "registration"),
    )
    return SpecialistBenchmark(task=task, cases=(case,))


def _player_eligibility_benchmark() -> SpecialistBenchmark:
    case = OperationalCase(
        case_id="player-eligibility-axel-001",
        case_type="player_validation",
        title="Elegibilidad jugador Axel Antonio Soto Ramirez",
        facts={
            "amount": 0.0,
            "amount_evidence_id": "EV-PLAYER-DOCS",
            "supplier": "AXEL ANTONIO SOTO RAMIREZ",
            "supplier_evidence_id": "EV-PLAYER-CEDULA",
            "account": "OPERACIONES-ELEGIBILIDAD",
            "account_evidence_id": "EV-PLAYER-RULE",
            "player_name": "Axel Antonio Soto Ramirez",
            "player_name_evidence_id": "EV-PLAYER-CEDULA",
            "birthdate": "2011-08-18",
            "birthdate_evidence_id": "EV-PLAYER-CEDULA",
            "curp_status": "present",
            "curp_status_evidence_id": "EV-PLAYER-DOCS",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-PLAYER-CEDULA",
                source_type="player",
                title="Cedula jugador 1 Axel Antonio",
            ),
            EvidenceRef(
                evidence_id="EV-PLAYER-DOCS",
                source_type="document",
                title="Documentacion del jugador",
            ),
            EvidenceRef(
                evidence_id="EV-PLAYER-RULE",
                source_type="canon",
                title="Regla de elegibilidad por categoria",
            ),
        ),
    )
    task = _finance_task(
        task_id="SAMCHAT-PLAYER-ELIG-001",
        title="Verify player eligibility evidence without guessing person data",
        case_type="player_validation",
        allowed_case_ids=(case.case_id,),
        expected_items=(
            {"case_id": case.case_id, "fact_key": "amount", "value": 0.0},
            {
                "case_id": case.case_id,
                "fact_key": "supplier",
                "value": "AXEL ANTONIO SOTO RAMIREZ",
            },
            {
                "case_id": case.case_id,
                "fact_key": "account",
                "value": "OPERACIONES-ELEGIBILIDAD",
            },
        ),
        tags=("player", "eligibility", "no_guessing"),
    )
    return SpecialistBenchmark(task=task, cases=(case,))


def _document_incident_benchmark() -> SpecialistBenchmark:
    case = OperationalCase(
        case_id="doc-incident-curp-duplicate-001",
        case_type="document_incident",
        title="Incidente documental por posible CURP duplicada",
        facts={
            "amount": 0.0,
            "amount_evidence_id": "EV-INCIDENT-REPORT",
            "supplier": "CURP DUPLICADA",
            "supplier_evidence_id": "EV-INCIDENT-REPORT",
            "account": "OPERACIONES-INCIDENCIA",
            "account_evidence_id": "EV-INCIDENT-RULE",
            "incident_type": "duplicate_curp",
            "incident_type_evidence_id": "EV-INCIDENT-REPORT",
            "resolution_status": "requires_human_review",
            "resolution_status_evidence_id": "EV-INCIDENT-REVIEW",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-INCIDENT-REPORT",
                source_type="document_incident",
                title="Reporte de duplicidad CURP",
            ),
            EvidenceRef(
                evidence_id="EV-INCIDENT-REVIEW",
                source_type="event_incident",
                title="Revision operativa pendiente",
            ),
            EvidenceRef(
                evidence_id="EV-INCIDENT-RULE",
                source_type="canon",
                title="Regla de incidentes documentales",
            ),
        ),
    )
    task = _finance_task(
        task_id="SAMCHAT-DOC-INCIDENT-001",
        title="Recover document incident precedent without resolving automatically",
        case_type="document_incident",
        allowed_case_ids=(case.case_id,),
        expected_items=(
            {"case_id": case.case_id, "fact_key": "amount", "value": 0.0},
            {"case_id": case.case_id, "fact_key": "supplier", "value": "CURP DUPLICADA"},
            {
                "case_id": case.case_id,
                "fact_key": "account",
                "value": "OPERACIONES-INCIDENCIA",
            },
        ),
        tags=("document_incident", "curp", "human_review"),
    )
    return SpecialistBenchmark(task=task, cases=(case,))


def _money_request_benchmark() -> SpecialistBenchmark:
    case = OperationalCase(
        case_id="money-request-s2600071",
        case_type="money_request",
        title="Solicitud transferencia S-2600071 aprobada para reembolso",
        facts={
            "amount": 628.00,
            "amount_evidence_id": "EV-MONEY-REQUEST",
            "supplier": "BIBIANA RAQUEL ROMAN ARGUELLES",
            "supplier_evidence_id": "EV-MONEY-BENEFICIARY",
            "account": "1170-001-EMPLOYEE",
            "account_evidence_id": "EV-MONEY-ACCOUNT",
            "operaciones_ref": "9",
            "operaciones_ref_evidence_id": "EV-MONEY-REQUEST",
            "system_ref": "S-2600071",
            "system_ref_evidence_id": "EV-MONEY-REQUEST",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-MONEY-REQUEST",
                source_type="money_request",
                title="Solicitud S-2600071 REF 9",
            ),
            EvidenceRef(
                evidence_id="EV-MONEY-BENEFICIARY",
                source_type="employee",
                title="Empleado beneficiario del reembolso",
            ),
            EvidenceRef(
                evidence_id="EV-MONEY-ACCOUNT",
                source_type="catalog",
                title="Subcuenta empleado deudores",
            ),
        ),
    )
    task = _finance_task(
        task_id="SAMCHAT-MONEY-REQ-001",
        title="Prepare money request preview with references preserved",
        case_type="money_request",
        allowed_case_ids=(case.case_id,),
        expected_items=(
            {"case_id": case.case_id, "fact_key": "amount", "value": 628.00},
            {
                "case_id": case.case_id,
                "fact_key": "supplier",
                "value": "BIBIANA RAQUEL ROMAN ARGUELLES",
            },
            {
                "case_id": case.case_id,
                "fact_key": "account",
                "value": "1170-001-EMPLOYEE",
            },
        ),
        tags=("money_request", "reimbursement", "references"),
    )
    return SpecialistBenchmark(task=task, cases=(case,))


def _budget_reforecast_benchmark() -> SpecialistBenchmark:
    case = OperationalCase(
        case_id="budget-2027-dcc-draft-001",
        case_type="budget",
        title="Presupuesto 2027 DCC desde historico 2026",
        facts={
            "amount": 2030000.00,
            "amount_evidence_id": "EV-BUDGET-HISTORIC",
            "supplier": "DCC NACIONAL",
            "supplier_evidence_id": "EV-BUDGET-LINE",
            "account": "4100-001-004",
            "account_evidence_id": "EV-BUDGET-LINE",
            "base_year": "2026",
            "base_year_evidence_id": "EV-BUDGET-HISTORIC",
            "target_year": "2027",
            "target_year_evidence_id": "EV-BUDGET-ASSUMPTION",
            "approval_state": "draft_preview_only",
            "approval_state_evidence_id": "EV-BUDGET-RULE",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-BUDGET-HISTORIC",
                source_type="budget",
                title="Historico presupuesto DCC 2026",
            ),
            EvidenceRef(
                evidence_id="EV-BUDGET-LINE",
                source_type="budget",
                title="Linea DCC Nacional 4100-001-004",
            ),
            EvidenceRef(
                evidence_id="EV-BUDGET-ASSUMPTION",
                source_type="canon",
                title="Supuesto preliminar 2027",
            ),
            EvidenceRef(
                evidence_id="EV-BUDGET-RULE",
                source_type="canon",
                title="Presupuesto requiere aprobacion humana",
            ),
        ),
    )
    task = _finance_task(
        task_id="SAMCHAT-BUDGET-2027-001",
        title="Prepare annual budget preview from historical evidence",
        case_type="budget",
        allowed_case_ids=(case.case_id,),
        expected_items=(
            {"case_id": case.case_id, "fact_key": "amount", "value": 2030000.00},
            {"case_id": case.case_id, "fact_key": "supplier", "value": "DCC NACIONAL"},
            {"case_id": case.case_id, "fact_key": "account", "value": "4100-001-004"},
        ),
        tags=("budget", "annual", "preview"),
    )
    return SpecialistBenchmark(task=task, cases=(case,))


def _tournament_creation_benchmark() -> SpecialistBenchmark:
    case = OperationalCase(
        case_id="tournament-ctt-2027-draft-001",
        case_type="tournament",
        title="Copa Telmex 2027 draft from previous tournament",
        facts={
            "amount": 0.0,
            "amount_evidence_id": "EV-TOURNAMENT-BASE",
            "supplier": "COPA TELMEX TELCEL",
            "supplier_evidence_id": "EV-TOURNAMENT-BASE",
            "account": "OPERACIONES-TORNEO",
            "account_evidence_id": "EV-TOURNAMENT-RULE",
            "source_tournament": "Copa Telmex 2026",
            "source_tournament_evidence_id": "EV-TOURNAMENT-BASE",
            "new_category": "Sub-17",
            "new_category_evidence_id": "EV-TOURNAMENT-REQUEST",
            "approval_state": "draft_preview_only",
            "approval_state_evidence_id": "EV-TOURNAMENT-RULE",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-TOURNAMENT-BASE",
                source_type="tournament",
                title="Torneo base Copa Telmex 2026",
            ),
            EvidenceRef(
                evidence_id="EV-TOURNAMENT-REQUEST",
                source_type="owner_needs",
                title="Solicitud de crear torneo con categoria Sub-17",
            ),
            EvidenceRef(
                evidence_id="EV-TOURNAMENT-RULE",
                source_type="canon",
                title="Creacion de torneo requiere aprobacion",
            ),
        ),
    )
    task = _finance_task(
        task_id="SAMCHAT-TOURNAMENT-2027-001",
        title="Prepare tournament creation preview from prior tournament",
        case_type="tournament",
        allowed_case_ids=(case.case_id,),
        expected_items=(
            {"case_id": case.case_id, "fact_key": "amount", "value": 0.0},
            {"case_id": case.case_id, "fact_key": "supplier", "value": "COPA TELMEX TELCEL"},
            {
                "case_id": case.case_id,
                "fact_key": "account",
                "value": "OPERACIONES-TORNEO",
            },
        ),
        tags=("tournament", "creation", "preview"),
    )
    return SpecialistBenchmark(task=task, cases=(case,))



def _accounts_receivable_collection_benchmark() -> SpecialistBenchmark:
    case = OperationalCase(
        case_id="cxc-bimbo-669dbf39-001",
        case_type="money_request",
        title="CxC Bimbo factura 669DBF39 con cobro pendiente",
        facts={
            "amount": 1972903.00,
            "amount_evidence_id": "EV-CXC-CFDI",
            "supplier": "BIMBO BIM011108DJ5",
            "supplier_evidence_id": "EV-CXC-CFDI",
            "account": "1150-001-001",
            "account_evidence_id": "EV-CXC-POLICY",
            "income_account": "4100-001-004",
            "income_account_evidence_id": "EV-CXC-POLICY",
            "tax_account": "IVA_TRASLADADO",
            "tax_account_evidence_id": "EV-CXC-CFDI",
            "collection_counterparty": "1120-001-001",
            "collection_counterparty_evidence_id": "EV-CXC-BANK-RULE",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-CXC-CFDI",
                source_type="cfdi",
                title="Factura emitida Bimbo UUID 669DBF39",
                excerpt="Total 1,972,903.00; ingreso emitido por Plataforma",
            ),
            EvidenceRef(
                evidence_id="EV-CXC-POLICY",
                source_type="accounting_policy",
                title="Prep?liza CxC Debe 1150-001-001 Haber 4100-001-004 + IVA",
            ),
            EvidenceRef(
                evidence_id="EV-CXC-BANK-RULE",
                source_type="canon",
                title="Cobro posterior Debe bancos Haber CxC",
            ),
        ),
    )
    task = _finance_task(
        task_id="SAMCHAT-CXC-COLLECTION-001",
        title="Prepare CxC and collection preview from issued CFDI",
        case_type="money_request",
        allowed_case_ids=(case.case_id,),
        expected_items=(
            {"case_id": case.case_id, "fact_key": "amount", "value": 1972903.00},
            {"case_id": case.case_id, "fact_key": "supplier", "value": "BIMBO BIM011108DJ5"},
            {"case_id": case.case_id, "fact_key": "account", "value": "1150-001-001"},
        ),
        tags=("cxc", "collection", "cfdi"),
    )
    return SpecialistBenchmark(task=task, cases=(case,))


def build_seed_benchmarks() -> Tuple[SpecialistBenchmark, ...]:
    return (
        _expense_amex_benchmark(),
        _owner_entity_folder_benchmark(),
        _supplier_history_benchmark(),
        _team_registration_benchmark(),
        _player_eligibility_benchmark(),
        _document_incident_benchmark(),
        _money_request_benchmark(),
        _budget_reforecast_benchmark(),
        _tournament_creation_benchmark(),
        _accounts_receivable_collection_benchmark(),
    )
