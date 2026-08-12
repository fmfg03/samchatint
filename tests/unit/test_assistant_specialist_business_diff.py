from __future__ import annotations

from samchat.assistant.specialist_benchmarks import (
    build_seed_benchmarks,
    run_seed_benchmark,
)
from samchat.assistant.specialist_business_diff import (
    NOT_EXECUTED,
    PREVIEW_ONLY,
    create_specialist_business_diff_preview,
    preview_contains_execution_claim,
    summarize_specialist_business_previews,
)


def test_specialist_business_preview_is_reviewable_and_inert() -> None:
    benchmark = next(
        item
        for item in build_seed_benchmarks()
        if item.task.task_id == "SAMCHAT-FIN-AMEX-001"
    )
    result = run_seed_benchmark(benchmark)
    preview = result.business_preview

    assert preview.preview_type == "amex_expense_reconciliation"
    assert preview.title == "AMEX comprobacion preview"
    assert preview.target["case_type"] == "expense_report"
    assert preview.approval_required is True
    assert preview.execution_status == NOT_EXECUTED
    assert preview.audit_language == PREVIEW_ONLY
    assert preview.writes_attempted == 0
    assert preview.side_effects_detected == 0
    assert "prepare_amex_reconciliation_preview" in preview.steps
    assert "card_label:AMEX FGV 45007" in preview.checks
    assert "EV-AMEX-STATEMENT" in preview.found_evidence
    assert "expense-amex-ref-28:pending_user_note" in preview.missing_evidence
    assert preview_contains_execution_claim(preview) is False


def test_specialist_business_preview_contains_verified_changes_only() -> None:
    benchmark = next(
        item
        for item in build_seed_benchmarks()
        if item.task.task_id == "SAMCHAT-CXC-COLLECTION-001"
    )
    result = run_seed_benchmark(benchmark)
    changes = {
        change.field: change for change in result.business_preview.proposed_changes
    }

    assert result.business_preview.preview_type == "accounts_receivable_collection"
    assert changes["amount"].proposed_value == 1972903.00
    assert changes["account"].proposed_value == "1150-001-001"
    assert changes["account"].evidence_id == "EV-CXC-POLICY"
    assert all(change.status == "supported" for change in changes.values())
    assert "prepare_accounts_receivable_entry_preview" in result.business_preview.steps


def test_specialist_business_preview_builder_matches_benchmark_preview() -> None:
    benchmark = build_seed_benchmarks()[0]
    result = run_seed_benchmark(benchmark)
    rebuilt = create_specialist_business_diff_preview(
        task=benchmark.task.agent_visible(),
        workflow=result.workflow,
    )

    assert rebuilt.to_dict() == result.business_preview.to_dict()


def test_specialist_business_preview_summary_detects_no_execution_claims() -> None:
    previews = [
        run_seed_benchmark(item).business_preview for item in build_seed_benchmarks()
    ]
    summary = summarize_specialist_business_previews(previews)

    assert summary["total"] == 10
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0
    assert summary["execution_claims_detected"] == 0
    assert "amex_expense_reconciliation" in summary["preview_types"]
    assert "accounts_receivable_collection" in summary["preview_types"]
    assert summary["missing_evidence_count"] == 2
