from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from samchat.assistant.specialist_harness import PASS
from samchat.assistant.specialist_report import (
    build_benchmark_report,
    compact_report_dict,
    render_benchmark_report_markdown,
)


ROOT = Path(__file__).resolve().parents[2]


def test_benchmark_report_summarizes_all_seed_tasks() -> None:
    report = build_benchmark_report()
    payload = report.to_dict(include_results=False)

    assert report.status == PASS
    assert report.total == 10
    assert report.passed == 10
    assert report.failed == 0
    assert report.criteria_total == 10
    assert report.criteria_passed == 10
    assert report.criteria_failed == 0
    assert report.side_effects_detected == 0
    assert len(report.task_ids) == 10
    assert set(report.case_types) == {
        "budget",
        "document_incident",
        "expense_report",
        "money_request",
        "player_validation",
        "supplier",
        "team",
        "tournament",
    }
    assert "amex_expense_reconciliation" in report.finance_capabilities
    assert "accounts_receivable_collection" in report.finance_capabilities
    assert "budget_reforecast_preview" in report.finance_capabilities
    assert payload["finance_capabilities"] == list(report.finance_capabilities)
    assert "amex_expense_reconciliation" in report.business_preview_types
    assert report.business_preview_missing_evidence_count == 2
    assert payload["business_preview_missing_evidence_count"] == 2
    assert payload["status"] == PASS
    assert "results" not in payload


def test_benchmark_report_classifies_missing_evidence_as_gap() -> None:
    report = build_benchmark_report()
    gaps = {(gap.task_id, gap.gap_type, gap.detail) for gap in report.gaps}

    assert (
        "SAMCHAT-FIN-AMEX-001",
        "missing_evidence",
        "expense-amex-ref-28:pending_user_note",
    ) in gaps
    assert (
        "SAMCHAT-OWNER-DCC-001",
        "missing_evidence",
        "tournament-dcc-entity-bimbo:missing_contact_birthdate",
    ) in gaps
    assert not [gap for gap in report.gaps if gap.gap_type == "failed_criterion"]
    assert not [gap for gap in report.gaps if gap.gap_type == "unsupported_claim"]


def test_render_benchmark_report_markdown_is_human_readable() -> None:
    report = build_benchmark_report()
    markdown = render_benchmark_report_markdown(report)

    assert "# SamChat Specialist Benchmark Report" in markdown
    assert "Status: PASS" in markdown
    assert "Benchmarks: 10/10 passed" in markdown
    assert "SAMCHAT-CXC-COLLECTION-001: PASS" in markdown
    assert "## Finance capabilities" in markdown
    assert "## Business preview types" in markdown
    assert "amex_expense_reconciliation" in markdown
    assert "human_approval_required" in markdown


def test_compact_report_dict_omits_heavy_results() -> None:
    compact = compact_report_dict(build_benchmark_report())

    assert compact["total"] == 10
    assert compact["status"] == PASS
    assert "results" not in compact
    assert compact["side_effects_detected"] == 0
    assert "finance_capabilities" in compact
    assert "business_preview_types" in compact


def test_specialist_benchmark_cli_outputs_json_and_markdown() -> None:
    json_run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_assistant_specialist_benchmarks.py"),
            "--format",
            "json",
            "--compact",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    payload = json.loads(json_run.stdout)
    assert payload["status"] == PASS
    assert payload["total"] == 10
    assert payload["side_effects_detected"] == 0

    md_run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_assistant_specialist_benchmarks.py"),
            "--format",
            "markdown",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert "SamChat Specialist Benchmark Report" in md_run.stdout
    assert "Benchmarks: 10/10 passed" in md_run.stdout
