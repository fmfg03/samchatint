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
from .specialist_agents import SpecialistWorkflowResult, run_specialist_workflow
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "workflow": self.workflow.to_dict(),
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
    workflow = run_specialist_workflow(
        task=benchmark.task.agent_visible(),
        cases=benchmark.cases,
    )
    criteria = tuple(
        _evaluate_workflow_criterion(workflow, criterion)
        for criterion in benchmark.task.criteria
    )
    status = PASS if all(item.status == PASS for item in criteria) else FAIL
    return WorkflowBenchmarkResult(
        task_id=benchmark.task.task_id,
        status=status,
        workflow=workflow,
        criteria=criteria,
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


def build_seed_benchmarks() -> Tuple[SpecialistBenchmark, ...]:
    return (
        _expense_amex_benchmark(),
        _owner_entity_folder_benchmark(),
        _supplier_history_benchmark(),
    )
