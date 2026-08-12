from __future__ import annotations

from dataclasses import replace

import pytest

from samchat.assistant.operational_case import EvidenceRef, OperationalCase
from samchat.assistant.samchat_task_schema import (
    RubricCriterion,
    SamchatTask,
    task_from_mapping,
)
from samchat.assistant.specialist_contract import (
    AgentContract,
    ToolPermission,
    build_initial_specialist_contracts,
)
from samchat.assistant.specialist_harness import (
    PASS,
    SpecialistArtifact,
    run_readonly_benchmark,
)


def _case() -> OperationalCase:
    return OperationalCase(
        case_id="expense-001",
        case_type="expense_report",
        title="Comprobacion AMEX julio",
        facts={
            "amount": 100,
            "amount_evidence_id": "EV-001",
            "supplier": "ACME",
            "supplier_evidence_id": "EV-002",
        },
        evidence=(
            EvidenceRef(
                evidence_id="EV-001",
                source_type="cfdi",
                title="Factura ACME importe",
            ),
            EvidenceRef(
                evidence_id="EV-002",
                source_type="receipt",
                title="Voucher proveedor",
            ),
        ),
    )


def _task() -> SamchatTask:
    return SamchatTask(
        task_id="PLAYER-ELIGIBILITY-017",
        title="Find precedent without executing writes",
        agent_type="institutional_knowledge",
        case_type="expense_report",
        instructions="Busca precedentes parecidos y reporta evidencia faltante.",
        allowed_case_ids=("expense-001",),
        allowed_tools=("case_search",),
        expected_output_artifacts=("precedent_pack",),
        criteria=(
            RubricCriterion(
                criterion_id="C1",
                description="Produces precedent pack",
                checks={"requires_artifact_keys": ["case_ids"]},
            ),
        ),
    )


class EchoVisibleTaskAgent:
    def __init__(self):
        self.contract = build_initial_specialist_contracts()[0]
        self.seen_task = None

    def run(self, task, cases):
        self.seen_task = task
        return SpecialistArtifact(
            artifact_type="precedent_pack",
            content={"case_ids": [case.case_id for case in cases]},
            provenance=tuple(case.case_id for case in cases),
        )


def test_initial_specialist_contracts_are_phase_one_safe() -> None:
    contracts = build_initial_specialist_contracts()

    assert {contract.agent_type for contract in contracts} == {
        "institutional_knowledge",
        "evidence_verifier",
        "finance",
    }
    for contract in contracts:
        assert contract.errors() == []
        assert contract.may_execute_external_side_effects is False
        for permission in contract.allowed_tools:
            assert permission.side_effects_allowed is False


def test_contract_rejects_write_side_effect_authority() -> None:
    contract = AgentContract(
        agent_id="bad_finance",
        agent_type="finance",
        description="Bad write contract",
        allowed_case_types=("expense_report",),
        allowed_tools=(
            ToolPermission(
                name="post_policy",
                mode="write",
                requires_human_approval=False,
                side_effects_allowed=True,
            ),
        ),
        may_execute_external_side_effects=True,
    )

    assert "agent_side_effects_forbidden_in_phase_one" in contract.errors()
    assert "tool_side_effects_forbidden_in_phase_one" in contract.errors()
    assert "write_tool_requires_human_approval" in contract.errors()


def test_samchat_task_private_rubric_is_not_agent_visible() -> None:
    task = _task().validate()
    visible = task.agent_visible()

    assert not hasattr(visible, "criteria")
    assert "criteria" not in visible.to_dict()
    assert "criteria" not in task.to_dict()
    assert "criteria" in task.to_dict(include_private_rubric=True)


def test_task_from_mapping_validates_required_phase_one_boundary() -> None:
    payload = {
        "task_id": "FIN-001",
        "title": "Finance preview",
        "agent_type": "finance",
        "case_type": "expense_report",
        "instructions": "Prepara una propuesta sin ejecutar.",
        "authority_boundary": "human_approval_required",
        "criteria": [
            {
                "criterion_id": "C1",
                "description": "No side effects",
                "checks": {"forbid_side_effects": True},
            }
        ],
    }

    assert task_from_mapping(payload).task_id == "FIN-001"
    bad = dict(payload)
    bad["authority_boundary"] = "agent_can_execute"
    with pytest.raises(ValueError) as exc_info:
        task_from_mapping(bad)
    assert "phase_one_requires_human_authority_boundary" in str(exc_info.value)


def test_operational_case_requires_exact_fact_value_and_evidence_binding() -> None:
    case = _case().validate()

    assert case.supports_fact_claim(
        fact_key="amount",
        value=100,
        evidence_id="EV-001",
    )
    assert not case.supports_fact_claim(
        fact_key="amount",
        value=900000,
        evidence_id="EV-001",
    )
    assert not case.supports_fact_claim(
        fact_key="amount",
        value=100,
        evidence_id="EV-002",
    )
    assert not case.supports_fact_claim(
        fact_key="missing_fact",
        value=100,
        evidence_id="EV-001",
    )


def test_operational_case_rejects_unbound_fact_evidence() -> None:
    broken = replace(
        _case(),
        facts={"amount": 100, "amount_evidence_id": "EV-MISSING"},
    )

    assert "unbound_fact_evidence:amount_evidence_id:EV-MISSING" in broken.errors()


def test_readonly_harness_keeps_rubric_private_and_scores_all_pass() -> None:
    agent = EchoVisibleTaskAgent()
    result = run_readonly_benchmark(
        task=_task(),
        agent=agent,
        cases=(_case(),),
    )

    assert result.status == PASS
    assert result.side_effects_detected == 0
    assert agent.seen_task is not None
    assert not hasattr(agent.seen_task, "criteria")
    assert result.artifact.content["case_ids"] == ["expense-001"]


def test_rubric_independence_visible_payload_does_not_change() -> None:
    task = _task()
    mutated = replace(
        task,
        criteria=(
            RubricCriterion(
                criterion_id="C1",
                description="Secretly mutated rubric",
                checks={"requires_artifact_keys": ["different_key"]},
            ),
        ),
    )

    assert task.agent_visible().to_dict() == mutated.agent_visible().to_dict()
