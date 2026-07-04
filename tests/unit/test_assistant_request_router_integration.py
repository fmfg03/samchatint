from types import SimpleNamespace

import pytest

from samchat.assistant.conversation_service import run_message_turn_with_pending
from samchat.assistant.router import _maybe_append_export_prompt


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1


async def _pending_none(**_kwargs):
    return None


async def _provider_must_not_be_called(**_kwargs):  # pragma: no cover
    raise AssertionError("provider path should not be called")


async def _finance_rows(_intent):
    return [
        {"year": 2025, "concepto": "Uniformes", "amount": 1000},
        {"year": 2026, "concepto": "Uniformes", "amount": 1250},
    ]


async def _empty_finance_rows(_intent):
    return []


async def _run_message(raw_message, *, finance_rows_provider=None, executor=None):
    return await run_message_turn_with_pending(
        raw_message=raw_message,
        conversation=SimpleNamespace(id="conv-request", updated_at=None),
        current_empleado=SimpleNamespace(id="emp-1"),
        session=_FakeSession(),
        request=None,
        tournament_key=None,
        bi_year=None,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        latest_pending_run_for_conversation=_pending_none,
        is_explicit_approval_message=lambda _text: False,
        is_explicit_rejection_message=lambda _text: False,
        confirm_pending_run=_provider_must_not_be_called,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=_provider_must_not_be_called,
        assistant_turn=_provider_must_not_be_called,
        maybe_append_export_prompt=_maybe_append_export_prompt,
        document_action_router_executor=executor,
        finance_rows_provider=finance_rows_provider,
    )


@pytest.mark.asyncio
async def test_deterministic_finance_request_bypasses_provider_and_offers_export():
    response = await _run_message(
        "Compara gasto 2026 vs 2025 por concepto",
        finance_rows_provider=_finance_rows,
    )

    assert "Comparación de gasto por concepto, 2026 vs 2025" in response.assistant_message
    assert "Uniformes" in response.assistant_message
    assert "¿Quieres que te lo exporte ahora? Responde Excel (CSV) o PDF." in response.assistant_message
    trace = response.tool_trace[0]["request_intelligence_live_wiring"]
    assert trace["domain"] == "finance"
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False


@pytest.mark.asyncio
async def test_empty_finance_request_has_no_export_prompt_or_provider_fallback():
    response = await _run_message(
        "gasto por concepto 2026 vs 2025",
        finance_rows_provider=_empty_finance_rows,
    )

    assert "No encontré datos suficientes" in response.assistant_message
    assert "¿Quieres que te lo exporte" not in response.assistant_message
    assert response.tool_trace[0]["request_intelligence_live_wiring"]["status"] == "empty"


@pytest.mark.asyncio
async def test_cfdi_request_uses_read_only_executor_not_provider():
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

    response = await _run_message("Qué CFDIs están pendientes", executor=executor)

    assert calls == [("receipts.cfdi_matching_overview", {"view": "pending", "limit": 50})]
    assert "CFDIs pendientes" in response.assistant_message
    assert "¿Quieres que te lo exporte ahora? Responde Excel (CSV) o PDF." in response.assistant_message
    assert response.tool_trace[0]["request_intelligence_live_wiring"]["provider_called"] is False


@pytest.mark.asyncio
async def test_payments_request_without_executor_fails_closed_no_provider():
    response = await _run_message("Qué pagos vencen esta semana")

    assert "executor read-only disponible" in response.assistant_message
    assert "¿Quieres que te lo exporte" not in response.assistant_message
    trace = response.tool_trace[0]["request_intelligence_live_wiring"]
    assert trace["domain"] == "payments"
    assert trace["status"] == "data_source_unavailable"
    assert trace["provider_called"] is False


@pytest.mark.asyncio
async def test_no_write_or_adapter_execution_for_read_only_request():
    write_calls = []

    async def executor(action, payload):
        write_calls.append((action, payload))
        assert action == "receipts.cfdi_matching_overview"
        return {"data": {"rows": []}, "summary": "Sin datos"}

    response = await _run_message("Facturas sin vincular", executor=executor)

    assert write_calls == [("receipts.cfdi_matching_overview", {"view": "unlinked", "limit": 50})]
    trace = response.tool_trace[0]["request_intelligence_live_wiring"]
    assert trace["actions_executed"] == []
    assert trace["writes_attempted"] is False
