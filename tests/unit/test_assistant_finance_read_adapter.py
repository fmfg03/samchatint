from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, Mock, patch

import pytest

from samchat.assistant import finance_read_adapter
from samchat.assistant.finance_read_adapter import run_finance_read_adapter


@pytest.mark.asyncio
async def test_adapter_routes_ar_summary_to_canonical_read_model():
    source = AsyncMock(
        return_value={
            "read_only": True,
            "source_notes": ["AR model note"],
            "summary": {"matched_collected_count": 1},
        }
    )
    session = object()

    with patch(
        "samchat.assistant.finance_read_adapter.build_ar_read_model",
        new=source,
    ):
        result = await run_finance_read_adapter(
            session,
            intent="ar.summary",
            budget_version_id="version-1",
            tournament_id="tournament-1",
            tournament_code="COPA",
            limit=25,
        )

    source.assert_awaited_once_with(
        session,
        budget_version_id="version-1",
        tournament_id="tournament-1",
        tournament_code="COPA",
        limit=25,
    )
    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["intent"] == "ar.summary"
    assert result["source_function"] == "samchat.ar.service.build_ar_read_model"
    assert result["payload"]["summary"]["matched_collected_count"] == 1
    assert "matched_collected_is_only_collection_proof" in result["safety_labels"]
    assert "candidate_match_is_evidence_only" in result["safety_labels"]
    assert "matched_collected is the only AR collection proof" in result["source_notes"]


@pytest.mark.asyncio
async def test_adapter_routes_ar_prematching_to_canonical_workbench():
    source = AsyncMock(
        return_value={
            "read_only": True,
            "source_notes": ["candidate_match is not collection proof"],
            "summary": {"candidate_match_count": 2},
        }
    )
    session = object()

    with patch(
        "samchat.assistant.finance_read_adapter.build_ar_matching_workbench",
        new=source,
    ):
        result = await run_finance_read_adapter(
            session,
            intent="ar.prematching",
            budget_version_id="version-1",
            tournament_id="tournament-1",
            year=2026,
            month=1,
            limit=10,
        )

    source.assert_awaited_once_with(
        session,
        budget_version_id="version-1",
        tournament_id="tournament-1",
        tournament_code=None,
        year=2026,
        month=1,
        limit=10,
    )
    assert result["source_function"] == (
        "samchat.ar.matching.build_ar_matching_workbench"
    )
    assert result["read_only"] is True
    assert "candidate_match_is_evidence_only" in result["safety_labels"]
    assert "pre-matching has no AR collection authority" in result["source_notes"]


@pytest.mark.asyncio
async def test_adapter_routes_cashflow_summary_to_canonical_read_model():
    source = AsyncMock(
        return_value={
            "read_only": True,
            "source_notes": ["Cashflow model note"],
            "summary": {"forecast_net": 1000.0},
        }
    )
    session = object()

    with patch(
        "samchat.assistant.finance_read_adapter."
        "build_cashflow_planning_read_model",
        new=source,
    ):
        result = await run_finance_read_adapter(
            session,
            intent="cashflow.summary",
            budget_version_id="version-1",
            year=2026,
            month=2,
            horizon_months=6,
            limit=20,
        )

    source.assert_awaited_once_with(
        session,
        budget_version_id="version-1",
        year=2026,
        month=2,
        horizon_months=6,
        limit=20,
    )
    assert result["source_function"] == (
        "samchat.cashflow.service.build_cashflow_planning_read_model"
    )
    assert result["payload"]["summary"]["forecast_net"] == 1000.0
    assert "forecast_is_derived" in result["safety_labels"]
    assert "ar_candidates_not_counted_as_collected" in result["safety_labels"]
    assert "forecast_net is derived" in result["source_notes"]


@pytest.mark.asyncio
async def test_adapter_routes_cashflow_statement_to_template_report():
    source = AsyncMock(
        return_value={
            "read_only": True,
            "period": {"year": 2026, "month": 6},
            "monthly_buckets": [{"month": 1, "actual_cash_in": 1000}],
        }
    )
    session = object()

    with patch(
        "samchat.assistant.finance_read_adapter."
        "build_cashflow_planning_read_model",
        new=source,
    ):
        result = await run_finance_read_adapter(
            session,
            intent="cashflow.statement",
            budget_version_id="version-1",
            year=2026,
            month=6,
            horizon_months=3,
            limit=20,
        )

    source.assert_awaited_once_with(
        session,
        budget_version_id="version-1",
        year=2026,
        month=6,
        horizon_months=12,
        limit=20,
    )
    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["intent"] == "cashflow.statement"
    assert result["payload"]["report_type"] == "cashflow_statement"
    assert result["payload"]["title"] == "Flujo de Efectivo"
    assert "forecast_is_derived" in result["payload"]["safety_labels"]


@pytest.mark.asyncio
async def test_adapter_routes_budget_snapshot_to_canonical_budget_service():
    source = AsyncMock(
        return_value={
            "ok": True,
            "source": "budget_db",
            "summary": {"budget_total": 2500.0},
        }
    )
    session = object()

    with patch(
        "samchat.assistant.finance_read_adapter.build_budget_snapshot",
        new=source,
    ):
        result = await run_finance_read_adapter(
            session,
            intent="budget.snapshot",
            budget_version_id="version-1",
            tournament_id="tournament-1",
            tournament_code="COPA",
            year=2026,
        )

    source.assert_awaited_once_with(
        session,
        version_id="version-1",
        tournament_id="tournament-1",
        tournament_slug="COPA",
        edition_year=2026,
    )
    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["intent"] == "budget.snapshot"
    assert result["source_function"] == "samchat.budgets.service.build_budget_snapshot"
    assert result["payload"]["summary"]["budget_total"] == 2500.0
    assert "budget_snapshot_read_only" in result["safety_labels"]
    assert "budget_authority_stays_in_presupuestos" in result["safety_labels"]
    assert "budget authority stays in Presupuestos" in result["source_notes"]


@pytest.mark.asyncio
async def test_adapter_routes_budget_vs_actual_to_template_report():
    source = AsyncMock(
        return_value={
            "ok": True,
            "source": "budget_db",
            "summary": {"edition_year": 2026},
            "breakdowns": {
                "by_phase": [
                    {
                        "label": "Fase Nacional",
                        "budget_total": 8000,
                        "actual_total": 7000,
                    }
                ]
            },
        }
    )
    session = object()

    with patch(
        "samchat.assistant.finance_read_adapter.build_budget_snapshot",
        new=source,
    ):
        result = await run_finance_read_adapter(
            session,
            intent="budget.vs_actual",
            budget_version_id="version-1",
            tournament_id="tournament-1",
            tournament_code="CTT",
            year=2026,
            month=6,
        )

    source.assert_awaited_once_with(
        session,
        version_id="version-1",
        tournament_id="tournament-1",
        tournament_slug="CTT",
        edition_year=2026,
    )
    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["intent"] == "budget.vs_actual"
    assert result["payload"]["report_type"] == "budget_vs_actual"
    assert result["payload"]["summary"]["variance_accumulated_total"] == 1000.0
    assert "budget_snapshot_read_only" in result["safety_labels"]


@pytest.mark.asyncio
async def test_adapter_labels_budget_artifact_fallback():
    source = AsyncMock(
        return_value={
            "ok": True,
            "source": "budget_artifact",
            "summary": {"budget_total": 100.0},
        }
    )

    with patch(
        "samchat.assistant.finance_read_adapter.build_budget_snapshot",
        new=source,
    ):
        result = await run_finance_read_adapter(
            object(),
            intent="budget.snapshot",
            year=None,
        )

    source.assert_awaited_once()
    assert "artifact_snapshot_must_be_labeled" in result["safety_labels"]
    assert any("artifact fallback" in note for note in result["source_notes"])


@pytest.mark.asyncio
async def test_adapter_routes_finance_platform_to_canonical_platform_snapshot():
    session = object()
    source = AsyncMock(
        return_value={
            "period": {"year": 2026, "month": 3},
            "documents": [{"id": "doc-1"}],
        }
    )
    platform_builder = Mock(
        return_value={
            "ok": True,
            "read_only": True,
            "period": {"year": 2026, "month": 3},
            "summary": {"open_actions": 2},
        }
    )

    with (
        patch(
            "samchat.assistant.finance_read_adapter."
            "build_finance_source_snapshot",
            new=source,
        ),
        patch(
            "samchat.assistant.finance_read_adapter."
            "build_finance_platform_snapshot",
            new=platform_builder,
        ),
    ):
        result = await run_finance_read_adapter(
            session,
            intent="finance.platform",
            year=2026,
            month=3,
            limit=40,
        )

    source.assert_awaited_once_with(
        session,
        year=2026,
        month=3,
        limit=40,
    )
    platform_builder.assert_called_once_with(
        {
            "period": {"year": 2026, "month": 3},
            "documents": [{"id": "doc-1"}],
        }
    )
    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["intent"] == "finance.platform"
    assert result["payload"]["period"] == {"year": 2026, "month": 3}
    assert "build_finance_source_snapshot" in result["source_function"]
    assert "build_finance_platform_snapshot" in result["source_function"]
    assert "finance_platform_read_only" in result["safety_labels"]
    assert "payment_run_is_ap_not_ar" in result["safety_labels"]
    assert "payment_run is AP/payment-run, not AR collection" in result["source_notes"]


@pytest.mark.asyncio
async def test_adapter_returns_finance_export_guidance_without_generating_files():
    result = await run_finance_read_adapter(object(), intent="finance.exports")

    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["intent"] == "finance.exports"
    assert result["source_function"] == (
        "samchat.assistant.finance_read_adapter._finance_export_catalog"
    )
    exports = {item["id"]: item for item in result["payload"]["exports"]}
    assert exports["finance_platform_xlsx"]["owner"] == "Finance Platform"
    assert exports["finance_platform_xlsx"]["route"] == "/admin/finanzas/export.xlsx"
    assert exports["budget_review_xlsx"]["owner"] == "Presupuestos"
    assert exports["assistant_report_export"]["owner"] == "Assistant report flow"
    assert exports["legacy_cashflow_export"]["status"] == "legacy/reference"
    assert exports["legacy_cashflow_export"]["caveat"] == "Not Finance Spine authority."
    assert "finance_export_guidance_only" in result["safety_labels"]
    assert "no_direct_file_generation" in result["safety_labels"]
    assert "exports are executed by owning modules" in result["source_notes"]


@pytest.mark.asyncio
async def test_adapter_blocks_unsupported_intent_without_fallback():
    result = await run_finance_read_adapter(
        object(),
        intent="finance.sql",
        budget_version_id="version-1",
    )

    assert result["ok"] is False
    assert result["read_only"] is True
    assert result["error"]["code"] == "unsupported_finance_intent"
    assert result["allowed_intents"] == [
        "ar.summary",
        "ar.prematching",
        "cashflow.summary",
        "cashflow.statement",
        "budget.snapshot",
        "budget.vs_actual",
        "finance.platform",
        "finance.exports",
    ]
    assert "source_function" not in result
    assert "no_free_sql_recompute" in result["safety_labels"]


@pytest.mark.asyncio
async def test_adapter_requires_budget_version_for_ar_intents():
    result = await run_finance_read_adapter(object(), intent="ar.summary")

    assert result["ok"] is False
    assert result["read_only"] is True
    assert result["error"]["code"] == "missing_budget_version_id"
    assert "no_financial_effects" in result["safety_labels"]


def test_adapter_source_has_no_legacy_route_or_write_mechanisms():
    source = inspect.getsource(finance_read_adapter)

    assert source.count("/admin/contabilidad/cash-flow/export.xlsx") == 1
    assert "legacy/reference" in source
    assert "Not Finance Spine authority" in source
    assert "db_read_universal" not in source
    assert "db_write_universal" not in source
    assert "session.execute" not in source
    assert "text(" not in source
    assert "method=\"POST\"" not in source
    assert "pending_confirmation" not in source
    assert "confirm_write" not in source
    assert "generate_finance_platform_xlsx" not in source
    assert "openpyxl" not in source
    assert "requests" not in source
    assert "httpx" not in source
