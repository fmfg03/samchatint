from types import SimpleNamespace

import pytest

from samchat.assistant.conversation_service import (
    run_message_turn_with_pending,
)
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


async def _provider_must_not_be_called(
    **_kwargs,
):  # pragma: no cover - sentinel
    raise AssertionError("provider path should not be called")


async def _finance_rows(_intent):
    return [
        {"year": 2025, "concepto": "Hospedaje", "amount": 1000},
        {"year": 2026, "concepto": "Hospedaje", "amount": 1500},
    ]


async def _empty_finance_rows(_intent):
    return []


async def _unavailable_finance_rows(_intent):
    raise RuntimeError("unavailable")


async def _run_finance_message(raw_message, rows_provider):
    return await run_message_turn_with_pending(
        raw_message=raw_message,
        conversation=SimpleNamespace(id="conv-finance", updated_at=None),
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
        finance_rows_provider=rows_provider,
    )


@pytest.mark.asyncio
async def test_finance_comparison_offers_export_on_rows():
    response = await _run_finance_message(
        "Compara gasto 2026 vs 2025 por concepto",
        _finance_rows,
    )

    assert (
        "Comparación de gasto por concepto, 2026 vs 2025"
        in response.assistant_message
    )
    assert (
        "| Hospedaje | $1,000.00 | $1,500.00 | $500.00 | 50.00% |"
        in response.assistant_message
    )
    assert (
        "¿Quieres que te lo exporte ahora? Responde Excel (CSV) o PDF."
        in response.assistant_message
    )
    trace = response.tool_trace[0]["finance_query_live_wiring"]
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False
    assert trace["status"] == "success"
    assert response.tool_trace[0]["result"]["rows"]


@pytest.mark.asyncio
async def test_empty_finance_comparison_skips_export_and_provider():
    response = await _run_finance_message(
        "gasto por concepto 2026 vs 2025",
        _empty_finance_rows,
    )

    assert "No encontré datos suficientes" in response.assistant_message
    assert "¿Quieres que te lo exporte" not in response.assistant_message
    trace = response.tool_trace[0]["finance_query_live_wiring"]
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False
    assert trace["status"] == "empty"
    assert "rows" not in response.tool_trace[0]["result"]


@pytest.mark.asyncio
async def test_unavailable_finance_source_skips_export_and_provider():
    response = await _run_finance_message(
        "variación de gastos por concepto entre 2025 y 2026",
        _unavailable_finance_rows,
    )

    assert (
        "fuente de datos financiera disponible"
        in response.assistant_message
    )
    assert "¿Quieres que te lo exporte" not in response.assistant_message
    trace = response.tool_trace[0]["finance_query_live_wiring"]
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False
    assert trace["status"] == "unavailable"


def test_timeout_response_still_never_offers_export_prompt():
    stale_exportable_trace = [
        {
            "tool": "finance.read_only_comparison",
            "result": {"rows": [{"concepto": "Hospedaje", "amount": 100}]},
        }
    ]
    message = (
        "El proveedor del asistente tardó demasiado en responder. "
        "No ejecuté acciones ni cambios; intenta de nuevo con una "
        "consulta más corta."
    )

    assert (
        _maybe_append_export_prompt(message, stale_exportable_trace)
        == message
    )
