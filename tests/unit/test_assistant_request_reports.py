import pytest

from samchat.assistant.request_intent import detect_request_intent
from samchat.assistant.request_reports import run_read_only_report
from samchat.assistant.request_response import render_request_report
from samchat.assistant.request_router import route_request


async def _finance_rows(_intent):
    return [
        {"year": 2025, "concepto": "Uniformes", "amount": 1000},
        {"year": 2026, "concepto": "Uniformes", "amount": 1250},
    ]


async def _empty_rows(_intent):
    return []


async def _unavailable_rows(_intent):
    raise RuntimeError("source unavailable")


@pytest.mark.asyncio
async def test_finance_comparison_mocked_read_only_data_returns_rows():
    intent = detect_request_intent("Compara gasto 2026 vs 2025 por concepto")
    route = route_request(intent)

    result = await run_read_only_report(
        intent=intent,
        route=route,
        finance_rows_provider=_finance_rows,
    )

    assert result.status == "success"
    assert result.exportable is True
    assert result.provider_called is False
    assert result.actions_executed == []
    assert result.rows == [
        {
            "concepto": "Uniformes",
            "diferencia": 250,
            "variacion_pct": 25,
            "gasto_2025": 1000,
            "gasto_2026": 1250,
        }
    ]

    rendered = render_request_report(intent=intent, route=route, result=result)
    assert "Comparación de gasto por concepto, 2026 vs 2025" in rendered
    assert (
        "| Uniformes | $1,000.00 | $1,250.00 | $250.00 | 25.00% |"
        in rendered
    )


@pytest.mark.asyncio
async def test_empty_finance_data_is_not_exportable():
    intent = detect_request_intent("Compara gasto 2026 vs 2025 por concepto")
    route = route_request(intent)

    result = await run_read_only_report(
        intent=intent,
        route=route,
        finance_rows_provider=_empty_rows,
    )

    assert result.status == "empty"
    assert result.exportable is False
    assert result.rows == []
    assert result.provider_called is False


@pytest.mark.asyncio
async def test_unavailable_finance_source_fails_closed_without_provider():
    intent = detect_request_intent("Compara gasto 2026 vs 2025 por concepto")
    route = route_request(intent)

    result = await run_read_only_report(
        intent=intent,
        route=route,
        finance_rows_provider=_unavailable_rows,
    )

    assert result.status == "data_source_unavailable"
    assert result.exportable is False
    assert result.provider_called is False


@pytest.mark.asyncio
async def test_cfdi_request_uses_injected_read_only_action_executor():
    intent = detect_request_intent("Qué CFDIs están pendientes")
    route = route_request(intent)
    calls = []

    async def executor(action, payload):
        calls.append((action, payload))
        return {
            "summary": "CFDIs pendientes",
            "data": {
                "title": "CFDIs pendientes",
                "rows": [{"uuid": "A", "status": "pending"}],
            },
        }

    result = await run_read_only_report(
        intent=intent,
        route=route,
        action_executor=executor,
    )

    assert calls == [
        ("receipts.cfdi_matching_overview", {"view": "pending", "limit": 50})
    ]
    assert result.status == "success"
    assert result.rows == [{"uuid": "A", "status": "pending"}]
    assert result.exportable is True


@pytest.mark.asyncio
async def test_missing_read_only_executor_returns_unavailable_not_provider():
    intent = detect_request_intent("Qué pagos vencen esta semana")
    route = route_request(intent)

    result = await run_read_only_report(intent=intent, route=route)

    assert result.status == "data_source_unavailable"
    assert result.exportable is False
    assert result.provider_called is False
