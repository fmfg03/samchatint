from __future__ import annotations

import json
from pathlib import Path

import pytest

from samchat.assistant.operational_case import EvidenceRef, OperationalCase, load_operational_cases
from samchat.assistant.specialist_agents import AgentArtifact, default_agent_contracts, default_agents
from samchat.assistant.specialist_harness import run_regression, run_task
from samchat.assistant.specialist_orchestrator import route_cross_agent_task
from samchat.assistant.specialist_task import AgentVisibleTask, RubricCriterion, SamchatTask, load_task


BENCHMARK_ROOT = Path(__file__).resolve().parents[2] / "benchmarks" / "assistant" / "rqf_052"
CASES_PATH = BENCHMARK_ROOT / "cases" / "operational_cases.json"
TASKS_DIR = BENCHMARK_ROOT / "tasks"


def test_operational_case_rejects_missing_evidence_reference() -> None:
    case = OperationalCase(
        case_id="EXP-BAD",
        case_type="expense_report",
        title="Bad case",
        summary="Has a fact pointer to missing evidence.",
        facts={"amount_evidence_id": "EV-MISSING"},
        evidence=(EvidenceRef("EV-OK", "document", "samchat://ok", "Valid evidence"),),
    )

    with pytest.raises(ValueError, match="missing evidence"):
        case.validate()


def test_agent_contracts_are_explicit_and_write_disabled() -> None:
    contracts = default_agent_contracts()

    assert set(contracts) == {"institutional_knowledge", "evidence_verifier", "finance"}
    assert contracts["institutional_knowledge"].authority_boundary == "READ_ONLY"
    assert contracts["evidence_verifier"].authority_boundary == "READ_ONLY"
    assert contracts["finance"].authority_boundary == "PROPOSE_ONLY"
    for contract in contracts.values():
        assert not any(tool.startswith(("write_", "mutate_", "delete_")) for tool in contract.tools_allowed)


def test_task_schema_requires_tools_for_specialist() -> None:
    task = SamchatTask(
        task_id="BAD-TOOLS",
        title="Missing tools",
        agent="finance",
        case_type="expense_report",
        instructions="Should fail schema validation.",
        authority_boundary="PROPOSE_ONLY",
        allowed_tools=(),
        criteria=(RubricCriterion("C1", "irrelevant", {}),),
    )

    with pytest.raises(ValueError, match="allowed_tools are required"):
        task.validate()


def test_harness_runs_all_rqf052_benchmarks_all_pass(tmp_path: Path) -> None:
    task_paths = sorted(TASKS_DIR.glob("*.json"))

    report = run_regression(task_paths, CASES_PATH, tmp_path / "regression")

    assert report["n_tasks"] == 10
    assert report["n_passed"] == 10
    assert report["all_pass"] is True
    assert (tmp_path / "regression" / "regression_report.json").exists()
    for child in report["reports"]:
        output = Path(child["workspace_dir"]) / "output"
        assert (output / "result.json").exists()
        assert (output / "scores.json").exists()
        visible_task = json.loads((Path(child["workspace_dir"]) / "source" / "samchat_task.json").read_text(encoding="utf-8"))
        assert "criteria" not in visible_task


def test_tool_permission_is_enforced(tmp_path: Path) -> None:
    original = load_task(TASKS_DIR / "fin-001.json")
    unsafe = SamchatTask(
        task_id="FIN-UNSAFE",
        title=original.title,
        agent=original.agent,
        case_type=original.case_type,
        instructions=original.instructions,
        authority_boundary=original.authority_boundary,
        allowed_tools=original.allowed_tools + ("mutate_database",),
        criteria=original.criteria,
        tags=original.tags,
    )
    cases = load_operational_cases(CASES_PATH)

    with pytest.raises(PermissionError, match="unauthorized tools"):
        run_task(unsafe, cases, tmp_path / "unsafe")


def test_orchestrator_routes_knowledge_verifier_finance_without_side_effects() -> None:
    task = load_task(TASKS_DIR / "orch-001.json")
    cases = load_operational_cases(CASES_PATH)

    plan = route_cross_agent_task(task, cases)

    assert plan.route == ("institutional_knowledge", "evidence_verifier", "finance")
    assert plan.authority_boundary == "PROPOSE_ONLY"
    assert not plan.side_effects_detected
    assert {result.agent_id for result in plan.child_results} == set(plan.route)


def test_orchestrator_task_harness_report_is_all_pass(tmp_path: Path) -> None:
    task = load_task(TASKS_DIR / "orch-001.json")
    cases = load_operational_cases(CASES_PATH)

    report = run_task(task, cases, tmp_path / "orchestrator")

    assert report.all_pass is True
    assert report.result.authority_boundary == "PROPOSE_ONLY"
    assert report.result.artifacts[0].content["route"] == ["institutional_knowledge", "evidence_verifier", "finance"]
    assert "EXP-AMEX-REF28" in report.result.artifacts[0].content["case_ids"]


def test_agents_do_not_use_rubric_ground_truth_to_select_cases(tmp_path: Path) -> None:
    original = load_task(TASKS_DIR / "know-001.json")
    poisoned = SamchatTask(
        task_id="KNOW-POISONED",
        title=original.title,
        agent=original.agent,
        case_type=original.case_type,
        instructions=original.instructions,
        authority_boundary=original.authority_boundary,
        allowed_tools=original.allowed_tools,
        criteria=(
            RubricCriterion(
                "C-POISON",
                "Poisoned rubric must not drive retrieval",
                "Evaluator-only ground truth",
                {"requires_case_ids": ["CASE-THAT-DOES-NOT-EXIST"]},
            ),
        ),
        tags=original.tags,
    )
    cases = load_operational_cases(CASES_PATH)

    report = run_task(poisoned, cases, tmp_path / "poisoned-knowledge")

    artifact = report.result.artifacts[0]
    assert "TEAM-ESTRELLAS-2026-DOCS" in artifact.content["case_ids"]
    assert "CASE-THAT-DOES-NOT-EXIST" not in artifact.content["case_ids"]
    assert report.all_pass is False


def test_finance_agent_does_not_use_rubric_ground_truth_to_select_cases(tmp_path: Path) -> None:
    original = load_task(TASKS_DIR / "fin-001.json")
    poisoned = SamchatTask(
        task_id="FIN-POISONED",
        title=original.title,
        agent=original.agent,
        case_type=original.case_type,
        instructions=original.instructions,
        authority_boundary=original.authority_boundary,
        allowed_tools=original.allowed_tools,
        criteria=(
            RubricCriterion(
                "C-POISON",
                "Poisoned rubric must not drive finance selection",
                "Evaluator-only ground truth",
                {"requires_case_ids": ["BUDGET-DCC-2026"]},
            ),
        ),
        tags=original.tags,
    )
    cases = load_operational_cases(CASES_PATH)

    report = run_task(poisoned, cases, tmp_path / "poisoned-finance")

    proposals = report.result.artifacts[0].content["proposals"]
    assert [proposal["case_id"] for proposal in proposals] == ["EXP-AMEX-REF28"]
    assert report.all_pass is False


def test_agent_visible_task_has_no_rubric_or_criteria() -> None:
    task = load_task(TASKS_DIR / "know-001.json")
    visible = task.to_agent_visible()

    assert isinstance(visible, AgentVisibleTask)
    assert not hasattr(visible, "criteria")


def test_rubric_independence_changing_only_rubric_does_not_change_agent_artifact(tmp_path: Path) -> None:
    original = load_task(TASKS_DIR / "know-001.json")
    mutated = SamchatTask(
        task_id=original.task_id,
        title=original.title,
        agent=original.agent,
        case_type=original.case_type,
        instructions=original.instructions,
        authority_boundary=original.authority_boundary,
        allowed_tools=original.allowed_tools,
        criteria=(
            RubricCriterion(
                "C-MUTATED",
                "Mutated private rubric",
                "Evaluator-only mutation",
                {"requires_case_ids": ["CASE-THAT-DOES-NOT-EXIST"], "requires_text": ["impossible"]},
            ),
        ),
        tags=original.tags,
    )
    cases = load_operational_cases(CASES_PATH)

    original_result = run_task(original, cases, tmp_path / "original").result
    mutated_result = run_task(mutated, cases, tmp_path / "mutated").result

    assert original_result.artifacts[0].to_dict() == mutated_result.artifacts[0].to_dict()


def test_verifier_handoff_blocks_unverified_claims_from_finance() -> None:
    cases = (
        OperationalCase(
            case_id="EXP-HANDOFF",
            case_type="expense_report",
            title="AMEX handoff test",
            summary="Expense with one supported amount and one unsupported account claim.",
            facts={"amount": 100.0, "amount_evidence_id": "EV-HANDOFF", "counterparty_account": "2120-999-999"},
            evidence=(EvidenceRef("EV-HANDOFF", "expense_report", "samchat://handoff", "Amount only"),),
        ).validate(),
    )
    agents = default_agents()
    knowledge_task = AgentVisibleTask(
        task_id="HANDOFF:knowledge",
        title="Find AMEX handoff test",
        agent="institutional_knowledge",
        case_type="expense_report",
        instructions="Find AMEX handoff test.",
        authority_boundary="READ_ONLY",
        allowed_tools=agents["institutional_knowledge"].contract.tools_allowed,
    ).validate()
    verifier_task = AgentVisibleTask(
        task_id="HANDOFF:verifier",
        title="Verify AMEX handoff test",
        agent="evidence_verifier",
        case_type="expense_report",
        instructions="Verify AMEX handoff test.",
        authority_boundary="READ_ONLY",
        allowed_tools=agents["evidence_verifier"].contract.tools_allowed,
    ).validate()
    finance_task = AgentVisibleTask(
        task_id="HANDOFF:finance",
        title="Prepare AMEX handoff proposal",
        agent="finance",
        case_type="expense_report",
        instructions="Prepare AMEX handoff proposal.",
        authority_boundary="PROPOSE_ONLY",
        allowed_tools=agents["finance"].contract.tools_allowed,
    ).validate()

    knowledge = agents["institutional_knowledge"].run(knowledge_task, cases)
    verified = agents["evidence_verifier"].run(verifier_task, cases, knowledge.artifacts)
    proposal = agents["finance"].run(finance_task, cases, verified.artifacts)

    assert verified.artifacts[0].content["supported_facts"][0]["fact_key"] == "amount"
    assert any(item["fact_key"] == "counterparty_account" for item in verified.artifacts[0].content["unverified_facts"])
    assert proposal.artifacts[0].content["proposals"][0]["amount"] == 100.0
    assert proposal.artifacts[0].content["proposals"][0]["credit_account"] is None


def test_provenance_is_fail_closed_without_explicit_fact_binding() -> None:
    cases = (
        OperationalCase(
            case_id="PERSON-UNBOUND",
            case_type="player_validation",
            title="Unbound person fact",
            summary="A fact exists in the case but has no explicit fact-to-evidence binding.",
            facts={"birthplace": "CDMX"},
            evidence=(EvidenceRef("EV-PERSON", "document", "samchat://person", "Generic person document"),),
        ).validate(),
    )
    verifier = default_agents()["evidence_verifier"]
    task = AgentVisibleTask(
        task_id="VERIFY-UNBOUND",
        title="Verify birthplace",
        agent="evidence_verifier",
        case_type="player_validation",
        instructions="Verify birthplace.",
        authority_boundary="READ_ONLY",
        allowed_tools=verifier.contract.tools_allowed,
    ).validate()

    result = verifier.run(task, cases)

    assert result.artifacts[0].content["supported_facts"] == []
    assert result.artifacts[0].content["unverified_facts"][0]["fact_key"] == "birthplace"
    assert result.artifacts[0].evidence_refs == ()


def test_verifier_claim_integrity_requires_exact_value_and_fact_evidence_binding() -> None:
    cases = (
        OperationalCase(
            case_id="EXP-CLAIM-INTEGRITY",
            case_type="expense_report",
            title="Claim integrity case",
            summary="Contains separately bound amount and supplier facts.",
            facts={
                "amount": 100.0,
                "amount_evidence_id": "EV-001",
                "supplier": "ACME",
                "supplier_evidence_id": "EV-002",
            },
            evidence=(
                EvidenceRef("EV-001", "invoice", "samchat://claim-integrity/invoice", "Invoice proving amount"),
                EvidenceRef("EV-002", "supplier", "samchat://claim-integrity/supplier", "Supplier record proving ACME"),
            ),
        ).validate(),
    )
    verifier = default_agents()["evidence_verifier"]
    task = AgentVisibleTask(
        task_id="VERIFIER-CLAIM-INTEGRITY",
        title="Verify claim integrity",
        agent="evidence_verifier",
        case_type="expense_report",
        instructions="Verify claim integrity.",
        authority_boundary="READ_ONLY",
        allowed_tools=verifier.contract.tools_allowed,
    ).validate()
    claims = AgentArtifact(
        artifact_type="precedent_set",
        title="Claims with one correct and three malformed variants",
        content={
            "claims": [
                {"case_id": "EXP-CLAIM-INTEGRITY", "fact_key": "amount", "value": 100.0, "evidence_id": "EV-001"},
                {"case_id": "EXP-CLAIM-INTEGRITY", "fact_key": "amount", "value": 900000.0, "evidence_id": "EV-001"},
                {"case_id": "EXP-CLAIM-INTEGRITY", "fact_key": "amount", "value": 100.0, "evidence_id": "EV-002"},
                {"case_id": "EXP-CLAIM-INTEGRITY", "fact_key": "missing_fact", "value": "x", "evidence_id": "EV-001"},
            ]
        },
        evidence_refs=("EV-001", "EV-002"),
        authority_boundary="READ_ONLY",
    )

    result = verifier.run(task, cases, (claims,))
    content = result.artifacts[0].content

    assert content["supported_facts"] == [
        {
            "case_id": "EXP-CLAIM-INTEGRITY",
            "fact_key": "amount",
            "value": 100.0,
            "evidence_id": "EV-001",
            "status": "supported",
        }
    ]
    assert len(content["unverified_facts"]) == 3
    assert {item["fact_key"] for item in content["unverified_facts"]} == {"amount", "missing_fact"}
    assert result.artifacts[0].evidence_refs == ("EV-001",)
