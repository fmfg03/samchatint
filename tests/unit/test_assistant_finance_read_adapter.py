from __future__ import annotations

import inspect
import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

import pytest

from samchat.assistant import finance_read_adapter
from samchat.assistant.finance_read_adapter import run_finance_read_adapter


def _jsonb_trace_processor(driver):
    from sqlalchemy.dialects.postgresql import JSONB, psycopg, psycopg2

    from devnous.gastos.models import AssistantRun

    trace_type = AssistantRun.__table__.c.tool_trace.type
    assert isinstance(trace_type, JSONB)
    pg_dialect = {"psycopg2": psycopg2, "psycopg": psycopg}[driver].dialect()
    processor = trace_type.bind_processor(pg_dialect)
    if processor is None:
        assert pg_dialect.supports_native_json_serialization
    return processor


def _strict_jsonb_trace_roundtrip(trace, processor):
    # Native JSON drivers accept the Python value; other drivers accept the
    # type's serialized value. Both must still satisfy PostgreSQL JSON syntax.
    bound = (
        json.dumps(trace, allow_nan=False) if processor is None else processor(trace)
    )
    assert isinstance(bound, (str, bytes, bytearray))

    def reject_nonfinite_json(token):
        raise ValueError(f"Non-finite JSON constant: {token}")

    return json.loads(bound, parse_constant=reject_nonfinite_json)


@pytest.mark.parametrize("uses_processor", [False, True], ids=["native", "encoded"])
def test_jsonb_trace_requires_strict_json_on_both_binding_paths(uses_processor):
    processor = json.dumps if uses_processor else None
    trace = [{"result": {"amount": 100.1, "opening": None, "count": 0, "ok": True}}]
    assert _strict_jsonb_trace_roundtrip(trace, processor) == trace
    for amount in (float("nan"), float("inf"), float("-inf")):
        invalid_trace = [{"result": {"amount": amount}}]
        with pytest.raises(ValueError):
            _strict_jsonb_trace_roundtrip(invalid_trace, processor)


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
@pytest.mark.parametrize("intent", ["cashflow.summary", "cashflow.statement"])
@pytest.mark.parametrize("driver", ["psycopg2", "psycopg"])
async def test_cashflow_results_cross_provider_and_jsonb_boundaries(intent, driver):
    from samchat.cashflow.service import build_cashflow_report

    source_payload = build_cashflow_report(
        {
            "source_status": {
                key: {"available": True, "complete": True}
                for key in ("bank", "ar", "obligations", "budget")
            },
            "bank_rows": [
                {
                    "id": "mxn-bank-movement",
                    "fecha": "2026-01-10",
                    "currency": "MXN",
                    "signo": "+",
                    "importe": "100.10",
                },
                {
                    "id": "usd-bank-movement",
                    "fecha": "2026-01-10",
                    "currency": "USD",
                    "signo": "+",
                    "importe": "40.20",
                },
            ],
            "documents": [],
            "ar_payload": {},
            "budget_plan": [],
        },
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        cutoff=date(2026, 1, 31),
        forecast_end=date(2026, 2, 1),
    )
    with pytest.raises(TypeError, match="Decimal"):
        json.dumps(source_payload)
    source = AsyncMock(return_value=source_payload)
    with patch.object(
        finance_read_adapter, "build_cashflow_planning_read_model", source
    ):
        result = await run_finance_read_adapter(
            object(), intent=intent, year=2026, month=1
        )

    # Providers use plain json.dumps. The ORM's actual JSONB column may encode
    # the same trace or delegate serialization to a native JSON driver.
    decoded = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
    trace = [{"tool": "assistant_finance_read", "result": result}]
    bind_trace = _jsonb_trace_processor(driver)
    assert _strict_jsonb_trace_roundtrip(trace, bind_trace)[0]["result"] == decoded
    payload = decoded["payload"]
    if intent == "cashflow.summary":
        assert payload["summary_by_currency"]["MXN"]["actual_cash_in"] == 100.1
        assert payload["summary_by_currency"]["USD"]["actual_cash_in"] == 40.2
        assert payload["summary_by_currency"]["MXN"]["actual_cash_out"] == 0
        assert type(payload["summary_by_currency"]["MXN"]["actual_cash_out"]) is int
        assert payload["summary_by_currency"]["MXN"]["opening_balance"] is None
        assert {row["currency"]: row["amount"] for row in payload["movements"]} == {
            "MXN": 100.1,
            "USD": 40.2,
        }
        assert payload["details"]["rows"] == payload["movements"]
    else:
        reports = payload["currency_reports"]
        assert reports["MXN"]["rows"][1]["months"][0] == 0.1001
        assert reports["USD"]["rows"][1]["months"][0] == 0.0402
        assert reports["MXN"]["summary"]["saldo_inicial"] is None
        assert reports["MXN"]["rows"][0]["months"][0] is None
    # Encoding at the assistant boundary never mutates exact source arithmetic.
    assert source_payload["summary_by_currency"]["MXN"]["actual_cash_in"] == Decimal(
        "100.10"
    )
    assert isinstance(source_payload["movements"][0]["amount"], Decimal)


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["cashflow.summary", "cashflow.statement"])
@pytest.mark.parametrize("driver", ["psycopg2", "psycopg"])
@pytest.mark.parametrize(
    "amount",
    [
        pytest.param(Decimal("NaN"), id="decimal-nan"),
        pytest.param(Decimal("Infinity"), id="decimal-positive-infinity"),
        pytest.param(Decimal("-Infinity"), id="decimal-negative-infinity"),
        pytest.param(float("nan"), id="float-nan"),
        pytest.param(float("inf"), id="float-positive-infinity"),
        pytest.param(float("-inf"), id="float-negative-infinity"),
        pytest.param(Decimal("9" * 400 + ".01"), id="decimal-overflows-wire-float"),
    ],
)
async def test_cashflow_nonfinite_source_values_remain_unavailable_in_json(
    intent, driver, amount
):
    from math import isfinite

    from samchat.cashflow.service import build_cashflow_report

    source_payload = build_cashflow_report(
        {
            "source_status": {
                key: {"available": True, "complete": True}
                for key in ("bank", "ar", "obligations", "budget")
            },
            "bank_rows": [
                {
                    "id": "mxn-bank-movement",
                    "fecha": "2026-01-10",
                    "currency": "MXN",
                    "signo": "+",
                    "importe": "100.10",
                }
            ],
            "documents": [],
            "ar_payload": {},
            "budget_plan": [
                {
                    "budget_line_id": "budget-source",
                    "year": 2026,
                    "month": 1,
                    "currency": "MXN",
                    "direction": "income",
                    "amount": amount,
                }
            ],
        },
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        cutoff=date(2026, 1, 31),
        forecast_end=date(2026, 2, 1),
    )
    original_amount = source_payload["budget_plan"][0]["amount"]
    assert not isfinite(float(original_amount))
    if not isinstance(amount, Decimal) or not amount.is_finite():
        assert (
            source_payload["summary_by_currency"]["MXN"]["planned_budget_income"]
            is None
        )
    with patch.object(
        finance_read_adapter,
        "build_cashflow_planning_read_model",
        AsyncMock(return_value=source_payload),
    ):
        result = await run_finance_read_adapter(
            object(), intent=intent, year=2026, month=1
        )

    def reject_nonfinite_json(token):
        raise AssertionError(f"Non-finite JSON constant: {token}")

    decoded = json.loads(
        json.dumps(result, ensure_ascii=False, allow_nan=False),
        parse_constant=reject_nonfinite_json,
    )
    trace = [{"tool": "assistant_finance_read", "result": result}]
    bind_trace = _jsonb_trace_processor(driver)
    assert _strict_jsonb_trace_roundtrip(trace, bind_trace)[0]["result"] == decoded
    assert decoded["read_only"] is True
    if intent == "cashflow.summary":
        assert decoded["payload"]["budget_plan"][0]["amount"] is None
        assert decoded["payload"]["summary"]["actual_cash_in"] == 100.1
        assert type(decoded["payload"]["period"]["year"]) is int
        assert decoded["payload"]["details"]["total"] == 1
    else:
        report = decoded["payload"]["currency_reports"]["MXN"]
        assert report["rows"][1]["months"][0] == 0.1001
        assert report["summary"]["saldo_inicial"] is None
    assert source_payload["budget_plan"][0]["amount"] is original_amount
    assert not isfinite(float(original_amount))


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
        "finance.vat_paid",
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
