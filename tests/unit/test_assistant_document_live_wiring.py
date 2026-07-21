import json
from types import SimpleNamespace

import pytest

from samchat.assistant.action_router import supported_actions
from samchat.assistant.conversation_service import (
    run_conversation_turn,
    run_message_turn_with_pending,
)
from samchat.assistant.document_intake import build_document_intake_result
from samchat.assistant.receipt_workflow_draft import DRAFT_KEY


def _marker(intake: dict) -> str:
    return (
        "DOCUMENT_INTAKE_RESULT JSON:\n"
        f"{json.dumps(intake, ensure_ascii=False, sort_keys=True)}\n\n"
        "Archivo procesado."
    )


def _cfdi_intake_without_missing() -> dict:
    result = build_document_intake_result(
        conversation_id="conv",
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


def _roster_intake_with_missing() -> dict:
    return build_document_intake_result(
        conversation_id="conv",
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


def _accounting_intake_without_missing() -> dict:
    result = build_document_intake_result(
        conversation_id="conv",
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


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return self._rows


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


async def _should_not_call_provider(**_kwargs):  # pragma: no cover - failure path
    raise AssertionError("provider path should not be called")


def _append_noop(message, _trace):
    return message


@pytest.mark.asyncio
async def test_upload_context_with_document_intake_returns_deterministic_proposal_text():
    intake = _cfdi_intake_without_missing()
    session = _FakeSession()
    conversation = SimpleNamespace(id="conv-id", updated_at=None)

    response = await run_conversation_turn(
        raw_message=_marker(intake),
        conversation=conversation,
        current_empleado=SimpleNamespace(id="emp-1"),
        session=session,
        request=None,
        tournament_key=None,
        bi_year=None,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        assistant_turn=_should_not_call_provider,
        maybe_append_export_prompt=_append_noop,
    )

    assert "Documento detectado: cfdi_invoice" in response.assistant_message
    assert "proposed_action_id:" in response.assistant_message
    assert "CONFIRMAR accion" in response.assistant_message
    assert (
        response.tool_trace[0]["document_intake_live_wiring"]["provider_called"]
        is False
    )
    assert session.commits == 1


@pytest.mark.asyncio
async def test_confirmation_command_for_write_like_action_blocks_when_writes_disabled(
    monkeypatch,
):
    monkeypatch.setenv("ASSISTANT_AGENT_WRITES_ENABLED", "false")
    intake = _cfdi_intake_without_missing()
    action = next(
        item
        for item in intake["proposed_actions"]
        if item["canonical_action"] == "receipts.link_expense_to_cfdi"
    )
    session = _FakeSession(latest_contents=[_marker(intake)])
    calls = []

    async def executor(
        canonical_action, payload
    ):  # pragma: no cover - should not be called
        calls.append((canonical_action, payload))
        return {"summary": "unexpected"}

    response = await run_message_turn_with_pending(
        raw_message=f"CONFIRMAR accion {action['action_id']}",
        conversation=SimpleNamespace(id="conv-id", updated_at=None),
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
        confirm_pending_run=_should_not_call_provider,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=_should_not_call_provider,
        assistant_turn=_should_not_call_provider,
        maybe_append_export_prompt=_append_noop,
        document_action_router_executor=executor,
    )

    assert "no se ejecuto ningun write" in response.assistant_message
    assert (
        response.tool_trace[0]["document_confirmation_live_wiring"]["executed"] is False
    )
    assert (
        response.tool_trace[0]["document_confirmation_live_wiring"]["blocked_reason"]
        == "writes_disabled"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_cancel_command_returns_canceled_without_execution():
    intake = _cfdi_intake_without_missing()
    action = intake["proposed_actions"][0]
    session = _FakeSession(latest_contents=[_marker(intake)])

    response = await run_message_turn_with_pending(
        raw_message=f"cancelar accion {action['action_id']}",
        conversation=SimpleNamespace(id="conv-id", updated_at=None),
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
        confirm_pending_run=_should_not_call_provider,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=_should_not_call_provider,
        assistant_turn=_should_not_call_provider,
        maybe_append_export_prompt=_append_noop,
    )

    assert "cancelada" in response.assistant_message
    assert (
        response.tool_trace[0]["document_confirmation_live_wiring"]["status"]
        == "canceled"
    )
    assert (
        response.tool_trace[0]["document_confirmation_live_wiring"]["executed"] is False
    )


@pytest.mark.asyncio
async def test_missing_fields_prevent_confirmation():
    intake = _roster_intake_with_missing()
    action = intake["proposed_actions"][0]
    session = _FakeSession(latest_contents=[_marker(intake)])

    response = await run_message_turn_with_pending(
        raw_message=f"CONFIRM action {action['action_id']}",
        conversation=SimpleNamespace(id="conv-id", updated_at=None),
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
        confirm_pending_run=_should_not_call_provider,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=_should_not_call_provider,
        assistant_turn=_should_not_call_provider,
        maybe_append_export_prompt=_append_noop,
    )

    assert "Faltan datos" in response.assistant_message
    assert (
        response.tool_trace[0]["document_confirmation_live_wiring"]["status"]
        == "needs_clarification"
    )
    assert (
        response.tool_trace[0]["document_confirmation_live_wiring"]["executed"] is False
    )


@pytest.mark.asyncio
async def test_wrong_action_id_fails_closed_without_provider_or_executor():
    intake = _cfdi_intake_without_missing()
    session = _FakeSession(latest_contents=[_marker(intake)])
    calls = []

    async def executor(
        canonical_action, payload
    ):  # pragma: no cover - should not be called
        calls.append((canonical_action, payload))
        return {"summary": "unexpected"}

    response = await run_message_turn_with_pending(
        raw_message="CONFIRMAR accion docact_wrong",
        conversation=SimpleNamespace(id="conv-id", updated_at=None),
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
        confirm_pending_run=_should_not_call_provider,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=_should_not_call_provider,
        assistant_turn=_should_not_call_provider,
        maybe_append_export_prompt=_append_noop,
        document_action_router_executor=executor,
    )

    assert "No encontre una accion propuesta" in response.assistant_message
    assert (
        response.tool_trace[0]["document_confirmation_live_wiring"]["status"]
        == "rejected"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_read_only_preview_uses_mocked_action_router_executor(monkeypatch):
    monkeypatch.setenv("ASSISTANT_AGENT_WRITES_ENABLED", "false")
    intake = _accounting_intake_without_missing()
    action = next(
        item
        for item in intake["proposed_actions"]
        if item["canonical_action"] == "executive.accounting_report"
    )
    session = _FakeSession(latest_contents=[_marker(intake)])
    calls = []

    async def executor(canonical_action, payload):
        calls.append((canonical_action, payload))
        return {"summary": "preview contable listo"}

    response = await run_message_turn_with_pending(
        raw_message=f"CONFIRM action {action['action_id']}",
        conversation=SimpleNamespace(id="conv-id", updated_at=None),
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
        confirm_pending_run=_should_not_call_provider,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=_should_not_call_provider,
        assistant_turn=_should_not_call_provider,
        maybe_append_export_prompt=_append_noop,
        document_action_router_executor=executor,
    )

    assert "preview contable listo" in response.assistant_message
    assert (
        response.tool_trace[0]["document_confirmation_live_wiring"]["executed"] is True
    )
    assert calls == [("executive.accounting_report", action["payload_preview"])]


@pytest.mark.asyncio
async def test_confirmation_without_available_intake_fails_closed():
    session = _FakeSession(latest_contents=[])

    response = await run_message_turn_with_pending(
        raw_message="CONFIRMAR accion docact_missing",
        conversation=SimpleNamespace(id="conv-id", updated_at=None),
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
        confirm_pending_run=_should_not_call_provider,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=_should_not_call_provider,
        assistant_turn=_should_not_call_provider,
        maybe_append_export_prompt=_append_noop,
    )

    assert "No encontre una accion documental propuesta" in response.assistant_message
    assert (
        response.tool_trace[0]["document_confirmation_live_wiring"]["blocked_reason"]
        == "document_intake_context_missing"
    )


@pytest.mark.asyncio
async def test_active_receipt_draft_blocks_provider_fallback_for_unknown_input():
    conversation = SimpleNamespace(
        id="conv-id",
        updated_at=None,
        metadata_={
            DRAFT_KEY: {
                "draft_id": "receiptdraft-docint-1",
                "intake_id": "docint-1",
                "registry_hash": "registry-1",
                "evidence_sha256": "a" * 64,
                "media_id": "media-1",
                "amount": "1.00",
                "date": "2026-07-21",
                "concept": "WITNESS STAGE 3 NO PAGAR",
                "currency": "MXN",
                "tournament_id": "11111111-1111-1111-1111-111111111111",
                "tournament_name": "Copa Telmex",
                "payment_subject_type": "personal",
                "account_type": "local",
                "payment_method": None,
            }
        },
    )

    response = await run_message_turn_with_pending(
        raw_message="no entiendo",
        conversation=conversation,
        current_empleado=SimpleNamespace(id="emp-1"),
        session=_FakeSession(),
        request=None,
        tournament_key=None,
        bi_year=2026,
        bi_scope="copa-telmex",
        bi_segment=None,
        assistant_mode="ahorro",
        openai_api_key=None,
        latest_pending_run_for_conversation=_pending_none,
        is_explicit_approval_message=lambda _text: False,
        is_explicit_rejection_message=lambda _text: False,
        confirm_pending_run=_should_not_call_provider,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=_should_not_call_provider,
        assistant_turn=_should_not_call_provider,
        maybe_append_export_prompt=_append_noop,
    )

    assert "No reconoci un dato aplicable" in response.assistant_message
    assert "pago" in response.assistant_message
    assert response.tool_trace[0]["receipt_workflow_draft"]["writes_attempted"] is False
