"""Deterministic v0 specialist agents for the SamChat RQF-052 harness.

These agents are deliberately small and non-LLM. Their purpose is to lock the
handoff semantics before any probabilistic provider is allowed into the loop:
precedents inform, verifier validates, finance proposes only from supported
facts, and nobody executes external side effects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .operational_case import OperationalCase
from .samchat_task_schema import SamchatVisibleTask
from .specialist_contract import (
    AgentContract,
    build_initial_specialist_contracts,
    contracts_by_agent_id,
)
from .specialist_harness import FAIL, PASS, SpecialistArtifact


SUPPORTED = "supported"
UNVERIFIED = "unverified"


FINANCE_CAPABILITY_AMEX = "amex_expense_reconciliation"
FINANCE_CAPABILITY_CXC = "accounts_receivable_collection"
FINANCE_CAPABILITY_BUDGET = "budget_reforecast_preview"
FINANCE_CAPABILITY_MONEY_REQUEST = "money_request_preview"
FINANCE_CAPABILITY_TOURNAMENT = "tournament_financial_context"
FINANCE_CAPABILITY_OPERATIONS = "operations_context_preview"
FINANCE_CAPABILITY_SUPPLIER = "supplier_financial_precedent"
FINANCE_CAPABILITY_GENERAL = "general_finance_preview"


@dataclass(frozen=True)
class Claim:
    case_id: str
    fact_key: str
    value: Any
    evidence_id: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "Claim":
        raw_evidence_id = payload.get("evidence_id")
        evidence_id = str(raw_evidence_id) if raw_evidence_id is not None else None
        return cls(
            case_id=str(payload.get("case_id", "")),
            fact_key=str(payload.get("fact_key", "")),
            value=payload.get("value"),
            evidence_id=evidence_id,
        )


@dataclass(frozen=True)
class VerifiedClaim:
    claim: Claim
    status: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["claim"] = self.claim.to_dict()
        return payload


@dataclass(frozen=True)
class SpecialistWorkflowResult:
    knowledge: SpecialistArtifact
    verification: SpecialistArtifact
    finance: SpecialistArtifact
    status: str
    side_effects_detected: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "knowledge": self.knowledge.to_dict(),
            "verification": self.verification.to_dict(),
            "finance": self.finance.to_dict(),
            "side_effects_detected": self.side_effects_detected,
        }


def _contracts() -> Mapping[str, AgentContract]:
    return contracts_by_agent_id(build_initial_specialist_contracts())


class InstitutionalKnowledgeAgent:
    """Find case facts that can become candidate claims.

    The agent sees only the visible task and supplied cases. It never reads the
    private rubric and it does not infer facts that are absent from a case.
    """

    contract = _contracts()["institutional_knowledge_v0"]

    def run(
        self,
        task: SamchatVisibleTask,
        cases: Tuple[OperationalCase, ...],
    ) -> SpecialistArtifact:
        allowed_case_ids = set(task.allowed_case_ids or ())
        selected_cases = [
            case
            for case in cases
            if case.case_type == task.case_type
            and (not allowed_case_ids or case.case_id in allowed_case_ids)
        ]
        claims: List[Dict[str, Any]] = []
        missing_evidence: List[Dict[str, str]] = []
        for case in selected_cases:
            for fact_key, value in case.facts.items():
                if fact_key.endswith("_evidence_id"):
                    continue
                evidence_id = case.fact_evidence_id(fact_key)
                if evidence_id:
                    claims.append(
                        Claim(
                            case_id=case.case_id,
                            fact_key=fact_key,
                            value=value,
                            evidence_id=evidence_id,
                        ).to_dict()
                    )
                else:
                    missing_evidence.append(
                        {"case_id": case.case_id, "fact_key": fact_key}
                    )
        return SpecialistArtifact(
            artifact_type="precedent_pack",
            content={
                "case_ids": [case.case_id for case in selected_cases],
                "claims": claims,
                "missing_evidence": missing_evidence,
            },
            provenance=tuple(case.case_id for case in selected_cases),
        )


class EvidenceVerifierAgent:
    """Verify candidate claims against exact case fact/evidence bindings."""

    contract = _contracts()["evidence_verifier_v0"]

    def run(
        self,
        task: SamchatVisibleTask,
        cases: Tuple[OperationalCase, ...],
        agent_artifact: Optional[SpecialistArtifact] = None,
    ) -> SpecialistArtifact:
        case_by_id = {case.case_id: case for case in cases}
        source_claims = tuple((agent_artifact.content or {}).get("claims") or ())
        verified: List[Dict[str, Any]] = []
        unsupported: List[str] = []
        for raw_claim in source_claims:
            claim = Claim.from_mapping(raw_claim)
            case = case_by_id.get(claim.case_id)
            if case is None:
                result = VerifiedClaim(
                    claim=claim,
                    status=UNVERIFIED,
                    reason="case_not_found",
                )
            elif case.supports_fact_claim(
                fact_key=claim.fact_key,
                value=claim.value,
                evidence_id=claim.evidence_id,
            ):
                result = VerifiedClaim(
                    claim=claim,
                    status=SUPPORTED,
                    reason="exact_fact_value_and_evidence_binding",
                )
            else:
                result = VerifiedClaim(
                    claim=claim,
                    status=UNVERIFIED,
                    reason="fact_value_or_evidence_binding_mismatch",
                )
            verified.append(result.to_dict())
            if result.status != SUPPORTED:
                unsupported.append(
                    f"{claim.case_id}:{claim.fact_key}:{result.reason}"
                )
        return SpecialistArtifact(
            artifact_type="verification_report",
            content={"verified_claims": verified},
            provenance=tuple(sorted(case_by_id)),
            unsupported_claims=tuple(unsupported),
        )


def finance_capability_for_task(task: SamchatVisibleTask) -> str:
    """Classify the visible finance proposal surface for a task.

    This is intentionally based only on task-visible metadata. It lets the
    orchestrator and report explain what kind of finance work is being proposed
    without reading evaluator-only rubric data.
    """

    tags = set(task.tags or ())
    if "amex" in tags:
        return FINANCE_CAPABILITY_AMEX
    if "cxc" in tags or "collection" in tags:
        return FINANCE_CAPABILITY_CXC
    if "budget" in tags:
        return FINANCE_CAPABILITY_BUDGET
    if "money_request" in tags or "reimbursement" in tags:
        return FINANCE_CAPABILITY_MONEY_REQUEST
    if "tournament" in tags or "owner_pack" in tags:
        return FINANCE_CAPABILITY_TOURNAMENT
    if "team" in tags or "player" in tags or "document_incident" in tags:
        return FINANCE_CAPABILITY_OPERATIONS
    if "supplier" in tags:
        return FINANCE_CAPABILITY_SUPPLIER
    return FINANCE_CAPABILITY_GENERAL


def _claim_value_by_key(claims: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for item in claims:
        claim = item.get("claim") or {}
        fact_key = claim.get("fact_key")
        if isinstance(fact_key, str) and fact_key:
            values[fact_key] = claim.get("value")
    return values


def finance_domain_summary(
    *,
    task: SamchatVisibleTask,
    supported_claims: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    capability = finance_capability_for_task(task)
    values = _claim_value_by_key(supported_claims)
    base: Dict[str, Any] = {
        "capability": capability,
        "case_type": task.case_type,
        "tags": list(task.tags),
    }
    if capability == FINANCE_CAPABILITY_AMEX:
        base.update(
            {
                "summary_label": "AMEX comprobacion preview",
                "amount": values.get("amount"),
                "supplier": values.get("supplier"),
                "expense_account": values.get("account"),
                "operaciones_ref": values.get("operaciones_ref"),
                "system_ref": values.get("system_ref"),
                "card_label": values.get("amex_card_label"),
            }
        )
    elif capability == FINANCE_CAPABILITY_CXC:
        base.update(
            {
                "summary_label": "CxC collection preview",
                "amount": values.get("amount"),
                "customer": values.get("supplier"),
                "accounts_receivable_account": values.get("account"),
                "collection_status": values.get("collection_status"),
            }
        )
    elif capability == FINANCE_CAPABILITY_BUDGET:
        base.update(
            {
                "summary_label": "Budget preview",
                "amount": values.get("amount"),
                "budget_account": values.get("account"),
                "budget_line": values.get("budget_line"),
            }
        )
    elif capability == FINANCE_CAPABILITY_MONEY_REQUEST:
        base.update(
            {
                "summary_label": "Money request preview",
                "amount": values.get("amount"),
                "beneficiary_or_supplier": values.get("supplier"),
                "account": values.get("account"),
                "operaciones_ref": values.get("operaciones_ref"),
                "system_ref": values.get("system_ref"),
            }
        )
    elif capability == FINANCE_CAPABILITY_TOURNAMENT:
        base.update(
            {
                "summary_label": "Tournament financial context preview",
                "amount": values.get("amount"),
                "counterparty": values.get("supplier"),
                "account": values.get("account"),
                "tournament": values.get("tournament"),
                "budget_line": values.get("budget_line"),
            }
        )
    elif capability == FINANCE_CAPABILITY_OPERATIONS:
        base.update(
            {
                "summary_label": "Operations context preview",
                "subject": values.get("supplier"),
                "amount": values.get("amount"),
                "account": values.get("account"),
            }
        )
    elif capability == FINANCE_CAPABILITY_SUPPLIER:
        base.update(
            {
                "summary_label": "Supplier financial precedent preview",
                "amount": values.get("amount"),
                "supplier": values.get("supplier"),
                "account": values.get("account"),
                "local_tax": values.get("local_tax"),
                "decision": values.get("decision"),
            }
        )
    else:
        base.update(
            {
                "summary_label": "General finance preview",
                "amount": values.get("amount"),
                "counterparty": values.get("supplier"),
                "account": values.get("account"),
            }
        )
    return base


class FinanceAgent:
    """Produce finance proposals only from supported verified claims."""

    contract = _contracts()["finance_v0"]

    def run(
        self,
        task: SamchatVisibleTask,
        cases: Tuple[OperationalCase, ...],
        verified_artifact: Optional[SpecialistArtifact] = None,
    ) -> SpecialistArtifact:
        verified_claims = tuple(
            (verified_artifact.content or {}).get("verified_claims") or ()
        )
        supported_claims = [
            item
            for item in verified_claims
            if item.get("status") == SUPPORTED
        ]
        rejected_claims = [
            item
            for item in verified_claims
            if item.get("status") != SUPPORTED
        ]
        proposal_items = []
        for item in supported_claims:
            claim = item.get("claim") or {}
            if claim.get("fact_key") not in {"amount", "account", "supplier"}:
                continue
            proposal_items.append(
                {
                    "case_id": claim.get("case_id"),
                    "fact_key": claim.get("fact_key"),
                    "value": claim.get("value"),
                    "evidence_id": claim.get("evidence_id"),
                    "proposal_status": "preview_only",
                }
            )
        domain_summary = finance_domain_summary(
            task=task,
            supported_claims=supported_claims,
        )
        return SpecialistArtifact(
            artifact_type="finance_proposal",
            content={
                "proposal_items": proposal_items,
                "rejected_claims": rejected_claims,
                "finance_capability": domain_summary["capability"],
                "domain_summary": domain_summary,
                "authority_boundary": "human_approval_required",
                "execution_allowed": False,
            },
            provenance=tuple(
                str(item.get("claim", {}).get("case_id"))
                for item in supported_claims
                if item.get("claim", {}).get("case_id")
            ),
        )


def run_specialist_workflow(
    *,
    task: SamchatVisibleTask,
    cases: Iterable[OperationalCase],
    knowledge_agent: Optional[InstitutionalKnowledgeAgent] = None,
    verifier_agent: Optional[EvidenceVerifierAgent] = None,
    finance_agent: Optional[FinanceAgent] = None,
) -> SpecialistWorkflowResult:
    """Run the first canonical handoff: Knowledge -> Verifier -> Finance."""

    case_tuple = tuple(case.validate() for case in cases)
    knowledge = (knowledge_agent or InstitutionalKnowledgeAgent()).run(
        task,
        case_tuple,
    )
    verification = (verifier_agent or EvidenceVerifierAgent()).run(
        task,
        case_tuple,
        agent_artifact=knowledge,
    )
    finance = (finance_agent or FinanceAgent()).run(
        task,
        case_tuple,
        verified_artifact=verification,
    )
    side_effects = (
        knowledge.side_effects_detected
        + verification.side_effects_detected
        + finance.side_effects_detected
    )
    status = PASS if side_effects == 0 and not verification.unsupported_claims else FAIL
    return SpecialistWorkflowResult(
        knowledge=knowledge,
        verification=verification,
        finance=finance,
        status=status,
        side_effects_detected=side_effects,
    )
