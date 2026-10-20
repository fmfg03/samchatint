"""Real canonical envelopes/readers with local source doubles; no live data or LLM."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from samchat.assistant.conversation_context import contextual_read_frame
from samchat.assistant.conversation_service import finalize_contextual_response
from samchat.assistant.read_evidence import validate_read_evidence


def report():
    # finance_realtime_report's actual schema, not a generic ok/payload wrapper.
    return {
        "generated_at": "2026-10-01",
        "period": {"from": "2026-08-01", "to": "2026-08-31"},
        "filters": {},
        "totals": {"gasto_total": 42, "registros": 1, "moneda": "MXN"},
        "breakdown": {"items": [{"registros": 1, "monto": 42}]},
        "notes": ["Tiempo real sobre expense_reports/documentos en la BD de gastos."],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("canonical", [False, True])
async def test_real_router_and_adapter_envelopes(monkeypatch, canonical):
    from samchat.assistant import router

    # Execute the actual module-owned report, adapter and router. Only SQL rows
    # are doubled; no result envelope or report schema is hand-built here.
    session = AsyncMock()
    session.execute.side_effect = [
        SimpleNamespace(one=lambda: SimpleNamespace(n=1, m=42)),
        SimpleNamespace(all=lambda: []),
        SimpleNamespace(all=lambda: [SimpleNamespace(k="Operations", n=1, m=42)]),
    ]
    name = "assistant_canonical_query" if canonical else "finance_realtime_report"
    payload = {
        "date_from": "2026-08-01",
        "date_to": "2026-08-31",
        "budget_source": "none",
        "compare_years": 0,
    }
    args = (
        {"action": "executive.realtime_report", "context": {}, "payload": payload}
        if canonical
        else payload
    )
    result = await router._run_read_tool(
        name,
        args,
        gastos_session=session,
        current_role="admin",
        tournament_key_default=None,
    )
    assert session.execute.await_count == 3
    if canonical:
        assert set(result) == {"action", "status", "data", "context"}
        assert result["status"] == "completed"
    answer = "Se registraron 42 MXN. Fuente: reporte de gastos del periodo."
    safe, trace = finalize_contextual_response(
        answer,
        [{"tool": name, "result": result}],
        work_frame=contextual_read_frame("¿Cuánto gasto hubo en agosto?", []),
        maybe_append_export_prompt=lambda text, trace: text,
    )
    assert answer in safe
    assert any(
        s.get("assistant_response_sufficiency_gate", {}).get("ok") is True
        for s in trace
    )


@pytest.mark.asyncio
async def test_real_pending_document_reader_and_canonical_envelope(monkeypatch):
    from samchat.assistant import adapters, router

    # Real router/action adapter; source data is a local double.
    data = {
        "summary": {"pending_count": 1, "total_pendiente": 42},
        "documentos": [{"documento_id": "doc-42", "monto_pendiente": 42}],
    }
    monkeypatch.setattr(
        adapters, "get_pending_document_payment_overview", AsyncMock(return_value=data)
    )
    result = await router._run_read_tool(
        "assistant_canonical_query",
        {
            "action": "receipts.pending_payment_overview",
            "context": {"responsible_user_id": "u"},
        },
        gastos_session=AsyncMock(),
        tournament_key_default=None,
        current_role="admin",
    )
    assert validate_read_evidence("assistant_canonical_query", result) == (
        True,
        "receipts.pending_payment_overview",
    )
    # Pending evidence must not be accepted for historical payments.
    safe, _ = finalize_contextual_response(
        "Se pagaron 42 MXN. Fuente: solicitudes.",
        [{"tool": "assistant_canonical_query", "result": result}],
        work_frame=contextual_read_frame("¿Qué evidencia de pagos realizados hay?", []),
        maybe_append_export_prompt=lambda text, trace: text,
    )
    assert "Se pagaron 42" not in safe


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "failed",
        "missing_context",
        "write",
        "missing_period",
        "missing_count",
        "missing_currency",
        "missing_source",
        "truncated",
        "gap",
        "unavailable",
    ],
)
def test_invalid_canonical_envelope_never_proves_amount(fault):
    result = {
        "action": "executive.realtime_report",
        "status": "completed",
        "context": {},
        "data": report(),
    }
    if fault == "empty":
        result["data"] = {}
    elif fault == "failed":
        result["status"] = "failed"
    elif fault == "missing_context":
        del result["context"]
    elif fault == "write":
        result["action"] = "expenses.create_manual_expense"
    elif fault == "missing_period":
        del result["data"]["period"]
    elif fault == "missing_count":
        del result["data"]["totals"]["registros"]
    elif fault == "missing_currency":
        del result["data"]["totals"]["moneda"]
    elif fault == "missing_source":
        del result["data"]["notes"]
    elif fault == "truncated":
        result["data"]["source_status"] = {"expense_scan_truncated": True}
    elif fault == "gap":
        result["data"]["ok"] = False
    elif fault == "unavailable":
        result["data"]["coverage"] = "not_queried"
    assert validate_read_evidence("assistant_canonical_query", result)[0] is False
    answer, _ = finalize_contextual_response(
        "Se pagaron 42 MXN. Fuente: reporte.",
        [{"tool": "assistant_canonical_query", "result": result}],
        work_frame=contextual_read_frame("¿Cuánto gasto hubo?", []),
        maybe_append_export_prompt=lambda text, trace: text,
    )
    assert "Se pagaron 42" not in answer


def test_unmapped_tool_and_generic_nonempty_payload_are_not_evidence():
    assert not validate_read_evidence(
        "random_reader", {"ok": True, "payload": {"amount": 42}}
    )[0]
    assert not validate_read_evidence(
        "assistant_finance_read", {"ok": True, "payload": {"amount": 42}}
    )[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("truncated", [False, True])
async def test_finance_platform_preserves_actual_source_coverage(
    monkeypatch, truncated
):
    from samchat.assistant import finance_read_adapter as adapter

    source = {
        "period": {"year": 2026, "month": 8},
        "documents": [],
        "expenses": [],
        "polizas": [],
        "source_status": {
            "expense_scan_truncated": truncated,
            "expense_scan_limit": 100,
        },
    }
    monkeypatch.setattr(
        adapter, "build_finance_source_snapshot", AsyncMock(return_value=source)
    )
    result = await adapter.run_finance_read_adapter(
        AsyncMock(), intent="finance.platform", year=2026, month=8
    )
    assert result["source_status"] == source["source_status"]
    assert validate_read_evidence("assistant_finance_read", result)[0] is (
        not truncated
    )
    del result["source_status"]
    assert not validate_read_evidence("assistant_finance_read", result)[0]


@pytest.mark.parametrize(
    "reader,data",
    [
        ("finance_strategy_snapshot", report()),
        ("finance_planner_snapshot", {"source_strategy": report()}),
        (
            "finance_alerts_scan",
            {
                "period": {"from": "2026-08-01", "to": "2026-08-31"},
                "generated_at": "2026-10-01",
                "summary": {"weeks_analyzed": 4},
                "alerts": [],
                "notes": ["Weekly z-score"],
            },
        ),
        (
            "finance_accounting_report",
            {
                "period": {"year": 2026, "month": 8},
                "report_type": "estado_mes",
                "summary": {"polizas": 1},
                "rows": [],
            },
        ),
        (
            "finance_expense_workflow_status",
            {
                "expense_id": "e",
                "numero_referencia": "G-1",
                "stages": [{"name": "accounting"}],
                "expense": {"id": "e"},
            },
        ),
        (
            "expense.full_workflow_snapshot",
            {
                "expense": {"expense_id": "e"},
                "workflow": {
                    "expense_id": "e",
                    "numero_referencia": "G-1",
                    "stages": [{"name": "accounting"}],
                    "expense": {"id": "e"},
                },
            },
        ),
        (
            "accounting.build_expense_preview",
            {
                "expense": {"expense_id": "e", "numero_referencia": "G-1"},
                "preview": {
                    "taxes": {"base_gasto": 42},
                    "contra_account": {"codigo": "2100"},
                },
            },
        ),
        (
            "receipts.cfdi_matching_overview",
            {
                "limit": 100,
                "summary": {"pending_count": 0},
                "pending_expenses": [],
                "linked_expenses": [],
                "unlinked_cfdis": [],
            },
        ),
        (
            "operations.folder_planner_snapshot",
            {
                "ok": True,
                "sources": ["tournament_operational_commitments"],
                "filters": {"limit": 100},
                "commitments": [],
                "summary": {"commitments_count": 0},
            },
        ),
        (
            "operations.tournament_soul_snapshot",
            {
                "snapshot_type": "tournament_soul_service",
                "tournaments": [{"id": "t"}],
                "soul": {"version": 1},
            },
        ),
    ],
)
def test_other_module_owned_read_contracts(reader, data):
    from samchat.assistant.read_evidence import _ACTION_READERS

    assert validate_read_evidence(reader, data)[0]
    for action, mapped_reader in _ACTION_READERS.items():
        if mapped_reader == reader:
            assert validate_read_evidence(
                "assistant_canonical_query",
                {"action": action, "status": "completed", "data": data, "context": {}},
            )[0]
    assert not validate_read_evidence(reader, {})[0]
    assert not validate_read_evidence(reader, {**data, "ok": False})[0]
    assert not validate_read_evidence(
        reader, {**data, "source_status": {"currency_gap": True}}
    )[0]


@pytest.mark.parametrize(
    "intent,payload",
    [
        (
            "ar.summary",
            {
                "ok": True,
                "read_only": True,
                "budget_version_id": "v",
                "summary": {"expected_income_count": 0},
                "expected_income": [],
            },
        ),
        (
            "ar.matching",
            {
                "ok": True,
                "read_only": True,
                "budget_version_id": "v",
                "summary": {"ar_item_count": 0},
                "items": [],
            },
        ),
        (
            "cashflow.summary",
            {
                "ok": True,
                "read_only": True,
                "period": {"year": 2026},
                "summary": {"expected": 0},
                "monthly_buckets": [{"month": 8}],
                "source_notes": ["planning"],
            },
        ),
        (
            "cashflow.statement",
            {
                "ok": True,
                "read_only": True,
                "report_type": "cashflow_statement",
                "period": {"year": 2026},
                "columns": ["month"],
                "rows": [{"label": "Entradas"}],
                "source_notes": ["planning"],
            },
        ),
        (
            "budget.vs_actual",
            {
                "ok": True,
                "read_only": True,
                "report_type": "budget_vs_actual",
                "period": {"year": 2026},
                "columns": ["month"],
                "rows": [{"segment": "state"}],
                "source_notes": ["budget_db"],
            },
        ),
    ],
)
def test_finance_read_requires_intent_and_source_metadata(intent, payload):
    result = {
        "ok": True,
        "read_only": True,
        "intent": intent,
        "source_function": "canonical.module",
        "source_notes": ["module-owned"],
        "payload": payload,
    }
    assert validate_read_evidence("assistant_finance_read", result)[0]
    for field in ("intent", "source_function", "source_notes", "payload", "read_only"):
        incomplete = {k: v for k, v in result.items() if k != field}
        assert not validate_read_evidence("assistant_finance_read", incomplete)[0]
    assert not validate_read_evidence(
        "assistant_finance_read", {**result, "payload": {**payload, "ok": False}}
    )[0]


def test_all_canonical_read_actions_have_explicit_evidence_contracts():
    from samchat.assistant.action_router import supported_read_actions
    from samchat.assistant.read_evidence import _ACTION_READERS

    assert set(_ACTION_READERS) == set(supported_read_actions())


def test_real_registration_report_retains_nonfinancial_read_surface():
    from samchat.assistant.tournament_registration_reports import (
        build_registration_executive_reports,
    )

    result = build_registration_executive_reports(
        dataset={"tournaments": [{"id": "t", "name": "Tournament"}]},
        tournament_key="t",
        as_of_date="2026-08-31",
        municipality_denominators={"national_total": 1, "states": {}, "source": "test"},
    )
    envelope = {
        "action": "operations.tournament_registration_executive_reports",
        "status": "completed",
        "data": result,
        "context": {},
    }
    assert validate_read_evidence("assistant_canonical_query", envelope)[0]
    answer = "No se registraron equipos. Fuente: reporte de cédulas del torneo."
    safe, _ = finalize_contextual_response(
        answer,
        [{"tool": "assistant_canonical_query", "result": envelope}],
        work_frame=contextual_read_frame("¿Cuántos equipos tenemos en el torneo?", []),
        maybe_append_export_prompt=lambda text, trace: text,
    )
    assert answer in safe
