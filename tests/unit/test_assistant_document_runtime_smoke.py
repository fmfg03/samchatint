import json
from types import SimpleNamespace

import pytest

from samchat.assistant.action_router import supported_actions
from samchat.assistant.conversation_service import (
    run_conversation_turn,
    run_message_turn_with_pending,
)
from samchat.assistant.document_intake import build_document_intake_result


def _document_marker(intake: dict) -> str:
    return (
        "DOCUMENT_INTAKE_RESULT JSON:\n"
        f"{json.dumps(intake, ensure_ascii=False, sort_keys=True)}\n\n"
        "Archivo procesado por intake documental."
    )


def _cfdi_intake() -> dict:
    result = build_document_intake_result(
        conversation_id="conv-runtime",
        file_name="factura.xml",
        file_kind="text",
        text=(
            "<cfdi:Comprobante xmlns:cfdi='http://www.sat.gob.mx/cfd/4' "
            "xmlns:tfd='http://www.sat.gob.mx/TimbreFiscalDigital' "
            "Fecha='2026-05-12T10:00:00' Total='45000.00' Moneda='MXN'>"
            "<cfdi:Emisor Rfc='AAA010101AAA' Nombre='Proveedor SA'/>"
            "<cfdi:Receptor Rfc='BBB010101BBB'/>"
            "<cfdi:Complemento><tfd:TimbreFiscalDigital "
            "UUID='123E4567-E89B-12D3-A456-426614174000'/></cfdi:Complemento>"
            "</cfdi:Comprobante>"
        ),
        user_context={"expense_or_document_candidate": "expense-1"},
        supported_actions=supported_actions(),
    ).to_dict()
    result["entities"]["expense_or_document_candidate"] = "expense-1"
    result["missing_fields"] = []
    return result


def _accounting_intake() -> dict:
    result = build_document_intake_result(
        conversation_id="conv-runtime",
        file_name="BALANZA MAYO 2026.csv",
        file_kind="spreadsheet",
        records=[
            {
                "Cuenta": "1000",
                "Descripcion de la cuenta": "Banco",
                "Total de cargos": "500.00",
                "Total de abonos": "500.00",
                "Saldo final": "100.00",
            }
        ],
        user_context={"company": "Empresa X", "project": "Proyecto Y"},
        supported_actions=supported_actions(),
    ).to_dict()
    result["missing_fields"] = []
    return result


def _roster_intake_missing_fields() -> dict:
    return build_document_intake_result(
        conversation_id="conv-runtime",
        file_name="roster.csv",
        file_kind="spreadsheet",
        records=[
            {
                "Equipo": "Tigres",
                "Categoria": "Sub-17",
                "Nombre": "Ana",
                "Apellido": "Lopez",
            }
        ],
        supported_actions=supported_actions(),
    ).to_dict()


class _FakeScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarRows(self._rows)


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
        return _FakeExecuteResult(
            [SimpleNamespace(content=content) for content in self.latest_contents]
        )


@pytest.fixture(autouse=True)
def _disabled_runtime_flags(monkeypatch):
    monkeypatch.setenv("ASSISTANT_AGENT_RUNTIME_ENABLED", "false")
    monkeypatch.setenv("ASSISTANT_AGENT_WRITES_ENABLED", "false")
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_ENABLED", "false")


async def _provider_must_not_be_called(**_kwargs):  # pragma: no cover - sentinel
    raise AssertionError("provider client path was called")


async def _pending_none(**_kwargs):
    return None


def _append_noop(message, _trace):
    return message


async def _run_message(raw_message, session, *, executor=None):
    return await run_message_turn_with_pending(
        raw_message=raw_message,
        conversation=SimpleNamespace(id="conv-runtime", updated_at=None),
        current_empleado=SimpleNamespace(id="emp-1"),
        session=session,
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
        maybe_append_export_prompt=_append_noop,
        document_action_router_executor=executor,
    )


@pytest.mark.asyncio
async def test_upload_derived_intake_renders_without_provider_call():
    intake = _cfdi_intake()
    response = await run_conversation_turn(
        raw_message=_document_marker(intake),
        conversation=SimpleNamespace(id="conv-runtime", updated_at=None),
        current_empleado=SimpleNamespace(id="emp-1"),
        session=_FakeSession(),
        request=None,
        tournament_key=None,
        bi_year=None,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        assistant_turn=_provider_must_not_be_called,
        maybe_append_export_prompt=_append_noop,
    )

    assert "Documento detectado: cfdi_invoice" in response.assistant_message
    assert "proposed_action_id:" in response.assistant_message
    assert "CONFIRMAR accion" in response.assistant_message
    assert "cancelar accion" in response.assistant_message
    assert response.tool_trace[0]["document_intake_live_wiring"] == {
        "stage": "upload_render",
        "detected_document_type": "cfdi_invoice",
        "proposed_action_count": len(intake["proposed_actions"]),
        "missing_field_count": 0,
        "provider_called": False,
    }


@pytest.mark.asyncio
async def test_confirm_write_like_action_blocks_without_provider_or_adapter_call():
    intake = _cfdi_intake()
    action = next(
        item for item in intake["proposed_actions"]
        if item["canonical_action"] == "receipts.link_expense_to_cfdi"
    )
    adapter_calls = []

    async def adapter_must_not_be_called(canonical_action, payload):  # pragma: no cover
        adapter_calls.append((canonical_action, payload))
        raise AssertionError("write adapter path was called")

    response = await _run_message(
        f"CONFIRMAR accion {action['action_id']}",
        _FakeSession(latest_contents=[_document_marker(intake)]),
        executor=adapter_must_not_be_called,
    )

    trace = response.tool_trace[0]["document_confirmation_live_wiring"]
    assert "no se ejecuto ningun write" in response.assistant_message
    assert trace["status"] == "blocked"
    assert trace["blocked_reason"] == "writes_disabled"
    assert trace["executed"] is False
    assert trace["provider_called"] is False
    assert adapter_calls == []


@pytest.mark.asyncio
async def test_cancel_action_bypasses_provider_and_action_router():
    intake = _cfdi_intake()
    action = intake["proposed_actions"][0]
    action_router_calls = []

    async def action_router_must_not_be_called(canonical_action, payload):  # pragma: no cover
        action_router_calls.append((canonical_action, payload))
        raise AssertionError("action_router should not run for cancel")

    response = await _run_message(
        f"cancel action {action['action_id']}",
        _FakeSession(latest_contents=[_document_marker(intake)]),
        executor=action_router_must_not_be_called,
    )

    trace = response.tool_trace[0]["document_confirmation_live_wiring"]
    assert "cancelada" in response.assistant_message
    assert trace["status"] == "canceled"
    assert trace["executed"] is False
    assert trace["provider_called"] is False
    assert action_router_calls == []


@pytest.mark.asyncio
async def test_read_only_preview_uses_injected_action_router_not_provider():
    intake = _accounting_intake()
    action = next(
        item for item in intake["proposed_actions"]
        if item["canonical_action"] == "executive.accounting_report"
    )
    action_router_calls = []

    async def read_only_executor(canonical_action, payload):
        action_router_calls.append((canonical_action, payload))
        return {"summary": "preview contable generado"}

    response = await _run_message(
        f"CONFIRM action {action['action_id']}",
        _FakeSession(latest_contents=[_document_marker(intake)]),
        executor=read_only_executor,
    )

    trace = response.tool_trace[0]["document_confirmation_live_wiring"]
    assert "preview contable generado" in response.assistant_message
    assert trace["status"] == "executed"
    assert trace["executed"] is True
    assert trace["provider_called"] is False
    assert action_router_calls == [("executive.accounting_report", action["payload_preview"])]


@pytest.mark.asyncio
async def test_missing_fields_return_clarification_without_provider_call():
    intake = _roster_intake_missing_fields()
    action = intake["proposed_actions"][0]

    response = await _run_message(
        f"CONFIRMAR accion {action['action_id']}",
        _FakeSession(latest_contents=[_document_marker(intake)]),
    )

    trace = response.tool_trace[0]["document_confirmation_live_wiring"]
    assert "Faltan datos" in response.assistant_message
    assert trace["status"] == "needs_clarification"
    assert trace["blocked_reason"] == "missing_required_fields"
    assert trace["provider_called"] is False


@pytest.mark.asyncio
async def test_confirmation_without_stored_intake_fails_closed_without_provider_call():
    response = await _run_message(
        "CONFIRMAR accion docact_missing",
        _FakeSession(latest_contents=[]),
    )

    trace = response.tool_trace[0]["document_confirmation_live_wiring"]
    assert "No encontre una accion documental propuesta" in response.assistant_message
    assert trace["status"] == "rejected"
    assert trace["blocked_reason"] == "document_intake_context_missing"
    assert trace["provider_called"] is False
