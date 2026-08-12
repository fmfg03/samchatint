from __future__ import annotations

from samchat.assistant.operational_case import EvidenceRef, OperationalCase
from samchat.assistant.samchat_task_schema import RubricCriterion, SamchatTask
from samchat.assistant.specialist_agents import (
    EvidenceVerifierAgent,
    FinanceAgent,
    InstitutionalKnowledgeAgent,
    run_specialist_workflow,
)
from samchat.assistant.specialist_harness import FAIL, PASS, SpecialistArtifact


def _case() -> OperationalCase:
    return OperationalCase(
        case_id="expense-amex-001",
        case_type="expense_report",
        title="AMEX Leon scouting",
        facts={
            "amount": 128.0,
            "amount_evidence_id": "EV-AMOUNT",
            "supplier": "HOTEL LEON",
            "supplier_evidence_id": "EV-SUPPLIER",
            "account": "5300-006-007",
            "account_evidence_id": "EV-ACCOUNT",
            "unsupported_note": "captured without source",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-AMOUNT",
                source_type="cfdi",
                title="CFDI total hospedaje",
            ),
            EvidenceRef(
                evidence_id="EV-SUPPLIER",
                source_type="cfdi",
                title="CFDI emisor hotel",
            ),
            EvidenceRef(
                evidence_id="EV-ACCOUNT",
                source_type="catalog",
                title="Cuenta contable sugerida",
            ),
        ),
    )


def _task() -> SamchatTask:
    return SamchatTask(
        task_id="FIN-AMEX-001",
        title="Prepare AMEX finance proposal from verified evidence",
        agent_type="finance",
        case_type="expense_report",
        instructions=(
            "Encuentra precedentes de comprobacion AMEX y prepara propuesta "
            "contable solo con hechos verificados."
        ),
        allowed_case_ids=("expense-amex-001",),
        allowed_tools=("case_search", "evidence_lookup", "finance_preview"),
        expected_output_artifacts=(
            "precedent_pack",
            "verification_report",
            "finance_proposal",
        ),
        criteria=(
            RubricCriterion(
                criterion_id="C1",
                description="Finance proposal is preview only",
                checks={"requires_artifact_keys": ["proposal_items"]},
            ),
        ),
    )


def _visible_task():
    return _task().agent_visible()


def test_knowledge_agent_emits_only_case_facts_with_explicit_evidence() -> None:
    artifact = InstitutionalKnowledgeAgent().run(_visible_task(), (_case(),))

    fact_keys = {claim["fact_key"] for claim in artifact.content["claims"]}
    assert {"amount", "supplier", "account"}.issubset(fact_keys)
    assert "unsupported_note" not in fact_keys
    assert artifact.content["missing_evidence"] == [
        {"case_id": "expense-amex-001", "fact_key": "unsupported_note"}
    ]
    assert artifact.side_effects_detected == 0


def test_verifier_handoff_rejects_adulterated_value_and_wrong_evidence() -> None:
    knowledge = SpecialistArtifact(
        artifact_type="precedent_pack",
        content={
            "claims": [
                {
                    "case_id": "expense-amex-001",
                    "fact_key": "amount",
                    "value": 128.0,
                    "evidence_id": "EV-AMOUNT",
                },
                {
                    "case_id": "expense-amex-001",
                    "fact_key": "amount",
                    "value": 900000.0,
                    "evidence_id": "EV-AMOUNT",
                },
                {
                    "case_id": "expense-amex-001",
                    "fact_key": "amount",
                    "value": 128.0,
                    "evidence_id": "EV-SUPPLIER",
                },
            ]
        },
    )

    artifact = EvidenceVerifierAgent().run(
        _visible_task(),
        (_case(),),
        agent_artifact=knowledge,
    )

    statuses = [item["status"] for item in artifact.content["verified_claims"]]
    assert statuses == ["supported", "unverified", "unverified"]
    assert len(artifact.unsupported_claims) == 2
    assert "fact_value_or_evidence_binding_mismatch" in artifact.unsupported_claims[0]


def test_finance_agent_consumes_only_supported_verified_claims() -> None:
    verification = SpecialistArtifact(
        artifact_type="verification_report",
        content={
            "verified_claims": [
                {
                    "claim": {
                        "case_id": "expense-amex-001",
                        "fact_key": "amount",
                        "value": 128.0,
                        "evidence_id": "EV-AMOUNT",
                    },
                    "status": "supported",
                    "reason": "exact_fact_value_and_evidence_binding",
                },
                {
                    "claim": {
                        "case_id": "expense-amex-001",
                        "fact_key": "amount",
                        "value": 900000.0,
                        "evidence_id": "EV-AMOUNT",
                    },
                    "status": "unverified",
                    "reason": "fact_value_or_evidence_binding_mismatch",
                },
            ]
        },
    )

    artifact = FinanceAgent().run(
        _visible_task(),
        (_case(),),
        verified_artifact=verification,
    )

    assert artifact.content["execution_allowed"] is False
    assert artifact.content["authority_boundary"] == "human_approval_required"
    assert artifact.content["proposal_items"] == [
        {
            "case_id": "expense-amex-001",
            "fact_key": "amount",
            "value": 128.0,
            "evidence_id": "EV-AMOUNT",
            "proposal_status": "preview_only",
        }
    ]
    assert artifact.content["rejected_claims"][0]["claim"]["value"] == 900000.0


def test_specialist_workflow_composes_knowledge_verifier_finance() -> None:
    result = run_specialist_workflow(task=_visible_task(), cases=(_case(),))

    assert result.status == PASS
    assert result.side_effects_detected == 0
    assert result.knowledge.artifact_type == "precedent_pack"
    assert result.verification.artifact_type == "verification_report"
    assert result.finance.artifact_type == "finance_proposal"
    proposal_keys = {
        item["fact_key"] for item in result.finance.content["proposal_items"]
    }
    assert proposal_keys == {"amount", "supplier", "account"}


def test_workflow_fails_closed_when_verifier_rejects_knowledge_claim() -> None:
    class TamperingKnowledgeAgent(InstitutionalKnowledgeAgent):
        def run(self, task, cases):
            return SpecialistArtifact(
                artifact_type="precedent_pack",
                content={
                    "claims": [
                        {
                            "case_id": "expense-amex-001",
                            "fact_key": "amount",
                            "value": 900000.0,
                            "evidence_id": "EV-AMOUNT",
                        }
                    ]
                },
            )

    result = run_specialist_workflow(
        task=_visible_task(),
        cases=(_case(),),
        knowledge_agent=TamperingKnowledgeAgent(),
    )

    assert result.status == FAIL
    assert result.verification.unsupported_claims
    assert result.finance.content["proposal_items"] == []
    assert result.finance.content["rejected_claims"][0]["claim"]["value"] == 900000.0
