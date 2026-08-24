from __future__ import annotations

import pytest

from samchat.assistant.soul_data_coverage import (
    INSUFFICIENT,
    PARTIAL,
    READY,
    SOURCE_MISSING,
    build_soul_data_coverage_report,
    evaluate_accounting_historical_sources,
    evaluate_sam_inbox_payload,
    evaluate_tournament_soul_snapshot,
    render_soul_data_coverage_answer,
)


def test_missing_sources_are_reported_as_needs_data_first() -> None:
    report = build_soul_data_coverage_report(
        soul_snapshot=None,
        sam_inbox_payload=None,
        historical_manifests=(),
    )

    assert report.status == INSUFFICIENT
    assert report.read_only is True
    assert report.tool_policy == "soul_data_coverage_only"
    assert {artifact.artifact_id: artifact.status for artifact in report.artifacts} == {
        "tournament.soul_snapshot": SOURCE_MISSING,
        "accounting.historical_snapshot": SOURCE_MISSING,
        "sam_inbox.payload": SOURCE_MISSING,
    }
    answer = render_soul_data_coverage_answer(report)
    assert "no est? listo" in answer
    assert "No ejecut? cambios" in answer


def test_complete_tournament_soul_snapshot_is_ready_for_base_questions() -> None:
    snapshot = {
        "tournament": {"name": "Copa Test"},
        "soul": {
            "operations": {
                "categories": ["Sub-13", "Sub-15"],
                "phases": [
                    {"name": "Estatal", "start_date": "2026-09-01", "activities": ["registro", "final"]},
                    {"name": "Nacional", "end_date": "2026-11-20", "activities": ["viaje"]},
                ],
            },
            "entity_folders_seed": {"entities": [{"name": "Jalisco"}]},
        },
    }

    coverage = evaluate_tournament_soul_snapshot(snapshot)

    assert coverage.status == READY
    assert coverage.score == 1.0
    assert coverage.findings == ()
    assert coverage.safety["can_answer_owner_pack"] is True


def test_incomplete_tournament_soul_snapshot_names_missing_data() -> None:
    coverage = evaluate_tournament_soul_snapshot({"tournament_name": "Liga Incompleta", "soul": {}})

    assert coverage.status == INSUFFICIENT
    codes = {finding.code for finding in coverage.findings}
    assert "missing_entities" in codes
    assert "missing_categories" in codes
    assert "missing_phases" in codes


def test_historical_accounting_sources_distinguish_missing_and_balance_only(tmp_path) -> None:
    trial_balance = tmp_path / "balanza.xlsx"
    trial_balance.write_text("ok", encoding="utf-8")
    missing_policy = tmp_path / "polizas.xlsx"

    coverage = evaluate_accounting_historical_sources(
        (
            {
                "label": "2025",
                "trial_balance": str(trial_balance),
                "policy_headers": str(missing_policy),
            },
        )
    )

    assert coverage.status == PARTIAL
    assert {finding.code for finding in coverage.findings} == {"unvalidated_historical_policy_source"}
    assert coverage.findings[0].severity == "warning"

    blocked = evaluate_accounting_historical_sources(({"label": "2024", "trial_balance": str(tmp_path / "missing.xlsx")},))
    assert blocked.status == INSUFFICIENT
    assert "missing_historical_trial_balance" in {finding.code for finding in blocked.findings}


def test_sam_inbox_detects_duplicates_and_permission_gap() -> None:
    coverage = evaluate_sam_inbox_payload(
        {
            "items": [{"id": "S-1"}, {"id": "S-1"}],
            "tabs": {"pending": 2},
            "source_health": {"finance": {"ok": True}},
        }
    )

    assert coverage.status == INSUFFICIENT
    codes = {finding.code for finding in coverage.findings}
    assert "inbox_duplicate_payloads" in codes
    assert "inbox_permission_unclear" in codes


@pytest.mark.asyncio
async def test_router_exposes_soul_data_coverage_as_read_only_tool() -> None:
    from samchat.assistant.router import (
        FINANCE_READ_TOOLS,
        READ_TOOLS,
        TOURNAMENT_READ_TOOLS,
        _run_read_tool,
    )

    assert "assistant_soul_data_coverage" in READ_TOOLS
    assert "assistant_soul_data_coverage" in FINANCE_READ_TOOLS
    assert "assistant_soul_data_coverage" in TOURNAMENT_READ_TOOLS

    payload = await _run_read_tool(
        "assistant_soul_data_coverage",
        {"include_live_soul": False},
        gastos_session=None,
        tournament_key_default=None,
        current_role="admin",
    )

    assert payload["read_only"] is True
    assert payload["tool_policy"] == "soul_data_coverage_only"
    assert payload["status"] == INSUFFICIENT
    assert "conversation_answer" in payload
    assert "No ejecut? cambios" in payload["conversation_answer"]
