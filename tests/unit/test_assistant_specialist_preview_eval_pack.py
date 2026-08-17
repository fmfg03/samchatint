from __future__ import annotations

from dataclasses import replace

from samchat.assistant.samchat_task_schema import RubricCriterion
from samchat.assistant.specialist_benchmarks import build_seed_benchmarks
from samchat.assistant.specialist_harness import FAIL, PASS
from samchat.assistant.specialist_preview_eval_pack import (
    compact_preview_eval_pack_dict,
    evaluate_specialist_preview_result,
    render_specialist_preview_eval_pack_markdown,
    run_specialist_preview_eval_pack,
)


def test_specialist_preview_eval_pack_scores_all_seed_previews() -> None:
    result = run_specialist_preview_eval_pack()

    assert result.status == PASS
    assert result.total == 10
    assert result.passed == 10
    assert result.failed == 0
    assert result.criteria_total == 100
    assert result.criteria_passed == 100
    assert result.criteria_failed == 0
    assert len(result.task_ids) == 10
    assert result.quality_status_counts["supported"] == 8
    assert result.quality_status_counts["partial"] == 2
    assert result.missing_evidence_count == 2


def test_specialist_preview_eval_pack_keeps_authority_and_gate_inert() -> None:
    result = run_specialist_preview_eval_pack()

    for item in result.results:
        assert item.status == PASS
        assert item.evidence_quality_gate["authority"] == "read_only_evidence_gate"
        assert item.evidence_quality_gate["safe_to_execute"] is False
        assert item.evidence_quality_gate["primary_action_enabled"] is False
        assert item.evidence_quality_gate["writes_attempted"] is False
        assert all(criterion.status == PASS for criterion in item.criteria)


def test_specialist_preview_eval_pack_surfaces_expected_missing_evidence() -> None:
    result = run_specialist_preview_eval_pack()
    by_task = {item.task_id: item for item in result.results}

    assert by_task["SAMCHAT-FIN-AMEX-001"].quality_status == "partial"
    assert by_task["SAMCHAT-FIN-AMEX-001"].missing_evidence_count == 1
    assert (
        "expense-amex-ref-28:pending_user_note"
        in by_task["SAMCHAT-FIN-AMEX-001"].evidence_quality_gate["missing_evidence"]
    )
    assert by_task["SAMCHAT-OWNER-DCC-001"].quality_status == "partial"
    assert by_task["SAMCHAT-CXC-COLLECTION-001"].quality_status == "supported"


def test_specialist_preview_eval_pack_markdown_and_compact_report() -> None:
    result = run_specialist_preview_eval_pack()
    markdown = render_specialist_preview_eval_pack_markdown(result)
    compact = compact_preview_eval_pack_dict(result)

    assert "SamChat Specialist Preview Eval Pack" in markdown
    assert "Previews: 10/10 passed" in markdown
    assert "supported: 8" in markdown
    assert "partial: 2" in markdown
    assert "blocked from execution" in markdown
    assert compact["status"] == PASS
    assert compact["total"] == 10
    assert compact["criteria_total"] == 100
    assert "results" not in compact


def test_specialist_preview_eval_pack_fails_when_workflow_benchmark_fails() -> None:
    benchmark = build_seed_benchmarks()[0]
    criterion = benchmark.task.criteria[0]
    checks = dict(criterion.checks)
    checks["expected_proposal_items"] = [
        {"case_id": "expense-amex-ref-28", "fact_key": "amount", "value": 999.99}
    ]
    mutated = replace(
        benchmark,
        task=replace(
            benchmark.task,
            criteria=(
                RubricCriterion(
                    criterion_id=criterion.criterion_id,
                    description=criterion.description,
                    checks=checks,
                ),
            ),
        ),
    )

    result = evaluate_specialist_preview_result(mutated)

    assert result.status == FAIL
    assert result.criteria[0].status == FAIL
    assert "workflow_status:FAIL" == result.criteria[0].reason
