from types import SimpleNamespace

import pytest

from samchat.assistant.conversation_service import run_message_turn_with_pending
from samchat.assistant.router import _maybe_append_export_prompt


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, latest_contents=None):
        self.added = []
        self.commits = 0
        self.latest_contents = latest_contents or []

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1

    async def execute(self, _stmt):
        rows = [SimpleNamespace(content=content) for content in self.latest_contents]
        return _FakeExecuteResult(rows)


async def _pending_none(**_kwargs):
    return None


async def _provider_must_not_be_called(**_kwargs):  # pragma: no cover
    raise AssertionError("provider path should not be called")


async def _finance_rows(_intent):
    return [
        {"year": 2025, "concepto": "Hospedaje", "amount": 1000},
        {"year": 2026, "concepto": "Hospedaje", "amount": 1500},
    ]


async def _run_message(raw_message, *, session=None, finance_rows_provider=None, executor=None):
    return await run_message_turn_with_pending(
        raw_message=raw_message,
        conversation=SimpleNamespace(id="conv-analyst", updated_at=None),
        current_empleado=SimpleNamespace(id="emp-1"),
        session=session or _FakeSession(),
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
async def test_analyst_insufficient_context_returns_needs_context_no_provider():
    response = await _run_message("Explícame esta balanza")

    assert "Necesito contexto para analizar" in response.assistant_message
    assert "subas, pegues o selecciones" in response.assistant_message
    trace = response.tool_trace[0]["analyst_workbench_live_wiring"]
    assert trace["status"] == "needs_context"
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False


@pytest.mark.asyncio
async def test_analyst_uses_latest_document_context_without_provider():
    context = (
        "DOCUMENT_INTAKE_RESULT JSON:\n"
        '{"detected_document_type":"accounting_balance","summary":"Balanza mayo 2026 con descuadre cero","missing_fields":[]}\n\n'
        "Archivo procesado."
    )
    response = await _run_message(
        "Explícame esta balanza",
        session=_FakeSession(latest_contents=[context]),
    )

    assert "Explicación con el contexto disponible" in response.assistant_message
    assert "Balanza mayo 2026" in response.assistant_message
    trace = response.tool_trace[0]["analyst_workbench_live_wiring"]
    assert trace["status"] == "success"
    assert trace["provider_called"] is False


@pytest.mark.asyncio
async def test_operational_finance_route_wins_over_analyst():
    response = await _run_message(
        "Compara gasto 2026 vs 2025 por concepto",
        finance_rows_provider=_finance_rows,
    )

    assert "Comparación de gasto por concepto" in response.assistant_message
    assert response.tool_trace[0].get("request_intelligence_live_wiring")
    assert "analyst_workbench_live_wiring" not in response.tool_trace[0]


@pytest.mark.asyncio
async def test_cfdi_request_route_wins_over_analyst():
    calls = []

    async def executor(action, payload):
        calls.append((action, payload))
        return {"summary": "CFDI pendientes", "data": {"rows": [{"uuid": "A"}]}}

    response = await _run_message("Qué CFDIs están pendientes", executor=executor)

    assert calls == [("receipts.cfdi_matching_overview", {"view": "pending", "limit": 50})]
    assert response.tool_trace[0].get("request_intelligence_live_wiring")
    assert "analyst_workbench_live_wiring" not in response.tool_trace[0]


@pytest.mark.asyncio
async def test_document_confirmation_command_wins_over_analyst():
    response = await _run_message("CONFIRMAR accion abc123")

    assert "No encontre una accion documental propuesta" in response.assistant_message
    assert response.tool_trace[0].get("document_confirmation_live_wiring")
    assert "analyst_workbench_live_wiring" not in response.tool_trace[0]

@pytest.mark.asyncio
async def test_contract_risk_request_uses_analyst_without_provider():
    context = (
        "DOCUMENT_INTAKE_RESULT JSON:\n"
        '{"detected_document_type":"contract","summary":"Contrato con obligaciones de pago y entregables pendientes","missing_fields":[]}\n\n'
        "Archivo procesado."
    )
    response = await _run_message(
        "Qué riesgos ves en este contrato",
        session=_FakeSession(latest_contents=[context]),
    )

    assert "Riesgos visibles con el contexto disponible" in response.assistant_message
    assert "Contrato con obligaciones" in response.assistant_message
    trace = response.tool_trace[0]["analyst_workbench_live_wiring"]
    assert trace["status"] == "success"
    assert trace["provider_called"] is False
