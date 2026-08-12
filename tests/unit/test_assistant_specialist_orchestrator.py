from __future__ import annotations

from dataclasses import replace

import pytest

from samchat.assistant.samchat_task_schema import SamchatVisibleTask
from samchat.assistant.specialist_benchmarks import build_seed_benchmarks, run_seed_benchmark
from samchat.assistant.specialist_harness import PASS
from samchat.assistant.specialist_orchestrator import (
    UNSUPPORTED_ROUTE,
    VERIFIED_FINANCE_PREVIEW_ROUTE,
    UnsupportedSpecialistRouteError,
    run_specialist_orchestrator,
    select_specialist_route,
)


def test_orchestrator_runs_canonical_knowledge_verifier_finance_trace() -> None:
    benchmark = build_seed_benchmarks()[0]
    result = run_specialist_orchestrator(
        task=benchmark.task.agent_visible(),
        cases=benchmark.cases,
    )

    assert result.status == PASS
    assert result.route == VERIFIED_FINANCE_PREVIEW_ROUTE
    assert result.execution_allowed is False
    assert result.authority_boundary == "human_approval_required"
    assert result.side_effects_detected == 0
    assert [step.agent_id for step in result.steps] == [
        "institutional_knowledge_v0",
        "evidence_verifier_v0",
        "finance_v0",
    ]
    assert [step.artifact_type for step in result.steps] == [
        "precedent_pack",
        "verification_report",
        "finance_proposal",
    ]


def test_seed_benchmarks_are_now_executed_through_orchestrator() -> None:
    for benchmark in build_seed_benchmarks():
        result = run_seed_benchmark(benchmark)
        payload = result.to_dict()

        assert result.status == PASS
        assert result.orchestrator.status == PASS
        assert result.orchestrator.route == VERIFIED_FINANCE_PREVIEW_ROUTE
        assert payload["orchestrator"]["route"] == VERIFIED_FINANCE_PREVIEW_ROUTE
        assert "workflow" not in payload["orchestrator"]
        assert payload["orchestrator"]["execution_allowed"] is False
        assert len(payload["orchestrator"]["steps"]) == 3


def test_orchestrator_selection_is_visible_task_only() -> None:
    benchmark = build_seed_benchmarks()[0]
    visible = benchmark.task.agent_visible()
    mutated_private_rubric = replace(
        benchmark.task,
        criteria=(replace(benchmark.task.criteria[0], description="changed privately"),),
    )

    assert not hasattr(visible, "criteria")
    assert select_specialist_route(visible) == VERIFIED_FINANCE_PREVIEW_ROUTE
    assert select_specialist_route(mutated_private_rubric.agent_visible()) == (
        VERIFIED_FINANCE_PREVIEW_ROUTE
    )


def test_orchestrator_trace_preserves_finance_capability_without_execution() -> None:
    benchmark = next(
        item
        for item in build_seed_benchmarks()
        if item.task.task_id == "SAMCHAT-CXC-COLLECTION-001"
    )
    result = run_specialist_orchestrator(
        task=benchmark.task.agent_visible(),
        cases=benchmark.cases,
    )

    assert result.workflow.finance.content["finance_capability"] == (
        "accounts_receivable_collection"
    )
    assert result.workflow.finance.content["domain_summary"][
        "accounts_receivable_account"
    ] == "1150-001-001"
    assert result.execution_allowed is False


def test_orchestrator_fails_closed_for_unsupported_routes() -> None:
    task = SamchatVisibleTask(
        task_id="UNSUPPORTED-001",
        title="Unsupported route",
        agent_type="executive_reporting",
        case_type="tournament",
        instructions="Prepare a dashboard.",
        expected_output_artifacts=("executive_brief",),
    )

    assert select_specialist_route(task) == UNSUPPORTED_ROUTE
    with pytest.raises(UnsupportedSpecialistRouteError):
        run_specialist_orchestrator(task=task, cases=())
