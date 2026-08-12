from __future__ import annotations

from dataclasses import replace

from samchat.assistant.samchat_task_schema import RubricCriterion
from samchat.assistant.specialist_benchmarks import (
    build_seed_benchmarks,
    run_seed_benchmark,
    run_seed_benchmarks,
)
from samchat.assistant.specialist_harness import FAIL, PASS


def test_seed_benchmarks_cover_first_platforma_domains() -> None:
    benchmarks = build_seed_benchmarks()

    assert len(benchmarks) == 3
    assert {benchmark.task.task_id for benchmark in benchmarks} == {
        "SAMCHAT-FIN-AMEX-001",
        "SAMCHAT-OWNER-DCC-001",
        "SAMCHAT-SUPPLIER-HOTEL-001",
    }
    assert {benchmark.task.case_type for benchmark in benchmarks} == {
        "expense_report",
        "tournament",
        "supplier",
    }


def test_seed_benchmarks_do_not_expose_private_rubric_to_agent_payload() -> None:
    for benchmark in build_seed_benchmarks():
        visible = benchmark.task.agent_visible().to_dict()
        public = benchmark.to_dict(include_private_rubric=False)
        private = benchmark.to_dict(include_private_rubric=True)

        assert "criteria" not in visible
        assert "criteria" not in public["task"]
        assert "criteria" in private["task"]


def test_seed_benchmark_suite_is_all_pass_and_preview_only() -> None:
    summary = run_seed_benchmarks()

    assert summary["status"] == PASS
    assert summary["total"] == 3
    assert summary["passed"] == 3
    assert summary["failed"] == 0
    for result in summary["results"]:
        finance = result["workflow"]["finance"]
        assert finance["content"]["execution_allowed"] is False
        assert finance["content"]["authority_boundary"] == "human_approval_required"
        assert result["workflow"]["side_effects_detected"] == 0


def test_amex_seed_requires_verified_amount_supplier_and_account() -> None:
    benchmark = next(
        item
        for item in build_seed_benchmarks()
        if item.task.task_id == "SAMCHAT-FIN-AMEX-001"
    )
    result = run_seed_benchmark(benchmark)

    proposal = {
        (item["fact_key"], item["value"])
        for item in result.workflow.finance.content["proposal_items"]
    }
    assert result.status == PASS
    assert ("amount", 3067.43) in proposal
    assert ("supplier", "AEROLINEA TARIFA AEREA PNR LE8KXZ") in proposal
    assert ("account", "5300-006-007") in proposal
    assert result.workflow.knowledge.content["missing_evidence"] == [
        {"case_id": "expense-amex-ref-28", "fact_key": "pending_user_note"}
    ]


def test_owner_dcc_seed_preserves_cxc_tournament_context() -> None:
    benchmark = next(
        item
        for item in build_seed_benchmarks()
        if item.task.task_id == "SAMCHAT-OWNER-DCC-001"
    )
    result = run_seed_benchmark(benchmark)

    proposal = {
        (item["fact_key"], item["value"])
        for item in result.workflow.finance.content["proposal_items"]
    }
    assert result.status == PASS
    assert ("amount", 1972903.00) in proposal
    assert ("supplier", "BIMBO BIM011108DJ5") in proposal
    assert ("account", "4100-001-004") in proposal
    assert result.workflow.knowledge.content["missing_evidence"] == [
        {
            "case_id": "tournament-dcc-entity-bimbo",
            "fact_key": "missing_contact_birthdate",
        }
    ]


def test_supplier_hotel_seed_carries_lodging_tax_precedent_without_execution() -> None:
    benchmark = next(
        item
        for item in build_seed_benchmarks()
        if item.task.task_id == "SAMCHAT-SUPPLIER-HOTEL-001"
    )
    result = run_seed_benchmark(benchmark)

    proposal = {
        (item["fact_key"], item["value"])
        for item in result.workflow.finance.content["proposal_items"]
    }
    assert result.status == PASS
    assert ("amount", 128.00) in proposal
    assert ("supplier", "HOTEL LEON") in proposal
    assert ("account", "5300-006-010") in proposal
    assert result.workflow.finance.content["execution_allowed"] is False


def test_seed_benchmark_fails_when_required_expected_item_is_wrong() -> None:
    benchmark = build_seed_benchmarks()[0]
    criterion = benchmark.task.criteria[0]
    checks = dict(criterion.checks)
    checks["expected_proposal_items"] = [
        {"case_id": "expense-amex-ref-28", "fact_key": "amount", "value": 999.99}
    ]
    mutated_task = replace(
        benchmark.task,
        criteria=(
            RubricCriterion(
                criterion_id=criterion.criterion_id,
                description=criterion.description,
                checks=checks,
            ),
        ),
    )
    result = run_seed_benchmark(replace(benchmark, task=mutated_task))

    assert result.status == FAIL
    assert result.criteria[0].status == FAIL
    assert "missing_proposal_item" in result.criteria[0].reason
