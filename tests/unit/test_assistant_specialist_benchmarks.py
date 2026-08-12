from __future__ import annotations

from dataclasses import replace

from samchat.assistant.samchat_task_schema import RubricCriterion
from samchat.assistant.specialist_benchmarks import (
    build_seed_benchmarks,
    run_seed_benchmark,
    run_seed_benchmarks,
)
from samchat.assistant.specialist_harness import FAIL, PASS


def test_seed_benchmarks_cover_initial_platforma_case_types() -> None:
    benchmarks = build_seed_benchmarks()
    task_ids = [benchmark.task.task_id for benchmark in benchmarks]

    assert len(benchmarks) == 10
    assert len(set(task_ids)) == 10
    assert set(task_ids) == {
        "SAMCHAT-FIN-AMEX-001",
        "SAMCHAT-OWNER-DCC-001",
        "SAMCHAT-SUPPLIER-HOTEL-001",
        "SAMCHAT-TEAM-REG-001",
        "SAMCHAT-PLAYER-ELIG-001",
        "SAMCHAT-DOC-INCIDENT-001",
        "SAMCHAT-MONEY-REQ-001",
        "SAMCHAT-BUDGET-2027-001",
        "SAMCHAT-TOURNAMENT-2027-001",
        "SAMCHAT-CXC-COLLECTION-001",
    }
    assert {case.case_type for benchmark in benchmarks for case in benchmark.cases} == {
        "budget",
        "document_incident",
        "expense_report",
        "money_request",
        "player_validation",
        "supplier",
        "team",
        "tournament",
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
    assert summary["total"] == 10
    assert summary["passed"] == 10
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


def test_expanded_seed_benchmarks_all_have_verified_proposal_items() -> None:
    for benchmark in build_seed_benchmarks():
        result = run_seed_benchmark(benchmark)
        proposal_items = result.workflow.finance.content["proposal_items"]
        keys = {item["fact_key"] for item in proposal_items}

        assert result.status == PASS
        assert {"amount", "supplier", "account"}.issubset(keys)
        assert result.workflow.verification.unsupported_claims == ()
        assert result.workflow.finance.content["execution_allowed"] is False


def test_cxc_collection_seed_preserves_accounts_receivable_account() -> None:
    benchmark = next(
        item
        for item in build_seed_benchmarks()
        if item.task.task_id == "SAMCHAT-CXC-COLLECTION-001"
    )
    result = run_seed_benchmark(benchmark)

    proposal = {
        (item["fact_key"], item["value"])
        for item in result.workflow.finance.content["proposal_items"]
    }
    assert result.status == PASS
    assert ("amount", 1972903.00) in proposal
    assert ("supplier", "BIMBO BIM011108DJ5") in proposal
    assert ("account", "1150-001-001") in proposal


def test_seed_benchmarks_expose_domain_specific_finance_capabilities() -> None:
    capabilities = {}
    summaries = {}
    for benchmark in build_seed_benchmarks():
        result = run_seed_benchmark(benchmark)
        content = result.workflow.finance.content
        capabilities[benchmark.task.task_id] = content["finance_capability"]
        summaries[benchmark.task.task_id] = content["domain_summary"]

    assert capabilities["SAMCHAT-FIN-AMEX-001"] == "amex_expense_reconciliation"
    assert summaries["SAMCHAT-FIN-AMEX-001"]["card_label"] == "AMEX FGV 45007"
    assert summaries["SAMCHAT-FIN-AMEX-001"]["operaciones_ref"] == "28"
    assert capabilities["SAMCHAT-CXC-COLLECTION-001"] == (
        "accounts_receivable_collection"
    )
    assert summaries["SAMCHAT-CXC-COLLECTION-001"][
        "accounts_receivable_account"
    ] == "1150-001-001"
    assert capabilities["SAMCHAT-BUDGET-2027-001"] == "budget_reforecast_preview"
    assert capabilities["SAMCHAT-MONEY-REQ-001"] == "money_request_preview"
    assert capabilities["SAMCHAT-SUPPLIER-HOTEL-001"] == (
        "supplier_financial_precedent"
    )
    assert summaries["SAMCHAT-SUPPLIER-HOTEL-001"]["local_tax"] == "ISH"
    assert capabilities["SAMCHAT-TEAM-REG-001"] == "operations_context_preview"
    assert capabilities["SAMCHAT-PLAYER-ELIG-001"] == "operations_context_preview"
    assert capabilities["SAMCHAT-DOC-INCIDENT-001"] == "operations_context_preview"


def test_domain_specific_action_previews_are_preview_only() -> None:
    previews = {}
    for benchmark in build_seed_benchmarks():
        result = run_seed_benchmark(benchmark)
        preview = result.workflow.finance.content["action_preview"]
        previews[benchmark.task.task_id] = preview

        assert preview["execution_allowed"] is False
        assert preview["requires_human_approval"] is True
        assert preview["authority_boundary"] == "human_approval_required"
        assert preview["steps"]
        assert preview["checks"]

    assert previews["SAMCHAT-FIN-AMEX-001"]["preview_type"] == (
        "amex_expense_reconciliation"
    )
    assert "prepare_amex_reconciliation_preview" in previews[
        "SAMCHAT-FIN-AMEX-001"
    ]["steps"]
    assert "card_label:AMEX FGV 45007" in previews["SAMCHAT-FIN-AMEX-001"][
        "checks"
    ]
    assert previews["SAMCHAT-CXC-COLLECTION-001"]["preview_type"] == (
        "accounts_receivable_collection"
    )
    assert "prepare_accounts_receivable_entry_preview" in previews[
        "SAMCHAT-CXC-COLLECTION-001"
    ]["steps"]
    assert "ar_account:1150-001-001" in previews["SAMCHAT-CXC-COLLECTION-001"][
        "checks"
    ]
    assert "prepare_budget_line_preview" in previews["SAMCHAT-BUDGET-2027-001"][
        "steps"
    ]
    assert "prepare_payment_request_preview" in previews["SAMCHAT-MONEY-REQ-001"][
        "steps"
    ]


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
