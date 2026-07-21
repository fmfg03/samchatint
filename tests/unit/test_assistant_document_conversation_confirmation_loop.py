from samchat.assistant.action_router import supported_actions
from samchat.assistant.document_conversation import (
    handle_document_confirmation_command,
    parse_document_confirmation_command,
    render_document_intake_for_conversation,
)
from samchat.assistant.document_intake import build_document_intake_result


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


def test_cfdi_proposal_renders_confirmation_instruction() -> None:
    intake = _cfdi_intake_without_missing()
    rendered = render_document_intake_for_conversation(intake)
    action = next(
        item
        for item in intake["proposed_actions"]
        if item["canonical_action"] == "receipts.link_expense_to_cfdi"
    )

    assert "Documento detectado: cfdi_invoice" in rendered
    assert "Resumen:" in rendered
    assert "Vincular CFDI" in rendered
    assert "receipts.link_expense_to_cfdi" not in rendered
    assert action["action_id"] in rendered
    assert f"CONFIRMAR accion {action['action_id']}" in rendered


def test_confirmation_command_parser_accepts_spanish_and_english_forms() -> None:
    assert parse_document_confirmation_command(
        "CONFIRMAR accion docact_123"
    ).to_dict() == {
        "action": "confirm",
        "proposed_action_id": "docact_123",
        "raw_text": "CONFIRMAR accion docact_123",
    }
    assert (
        parse_document_confirmation_command("CONFIRM action docact_123").action
        == "confirm"
    )
    assert (
        parse_document_confirmation_command("cancelar accion docact_123").action
        == "cancel"
    )
    assert (
        parse_document_confirmation_command("cancel action docact_123").action
        == "cancel"
    )
    assert parse_document_confirmation_command("confirmo esto") is None


def test_cfdi_write_confirmation_while_writes_disabled_blocks_without_executor_call() -> (
    None
):
    intake = _cfdi_intake_without_missing()
    action = next(
        item
        for item in intake["proposed_actions"]
        if item["canonical_action"] == "receipts.link_expense_to_cfdi"
    )
    calls = []

    def executor(canonical_action, payload):  # pragma: no cover - should not be called
        calls.append((canonical_action, payload))
        return {"summary": "unexpected"}

    result = handle_document_confirmation_command(
        text=f"CONFIRMAR accion {action['action_id']}",
        intake_result=intake,
        supported_actions=supported_actions(),
        writes_enabled=False,
        action_router_executor=executor,
    )

    assert result.confirmed is True
    assert result.executed is False
    assert result.status == "blocked"
    assert result.blocked_reason == "writes_disabled"
    assert "no se ejecuto ningun write" in result.message
    assert calls == []


def test_read_only_accounting_preview_confirmation_uses_action_router_executor() -> (
    None
):
    intake = _accounting_intake_without_missing()
    action = next(
        item
        for item in intake["proposed_actions"]
        if item["canonical_action"] == "executive.accounting_report"
    )
    calls = []

    def executor(canonical_action, payload):
        calls.append((canonical_action, payload))
        return {"summary": "preview contable listo"}

    result = handle_document_confirmation_command(
        text=f"CONFIRM action {action['action_id']}",
        intake_result=intake,
        supported_actions=supported_actions(),
        writes_enabled=False,
        action_router_executor=executor,
    )

    assert result.executed is True
    assert result.status == "executed"
    assert result.confirmation["safety"]["used_action_router"] is True
    assert result.message.endswith("preview contable listo")
    assert calls == [("executive.accounting_report", action["payload_preview"])]


def test_cancel_proposed_action_does_not_execute() -> None:
    intake = _cfdi_intake_without_missing()
    action = intake["proposed_actions"][0]

    result = handle_document_confirmation_command(
        text=f"cancelar accion {action['action_id']}",
        intake_result=intake,
        supported_actions=supported_actions(),
        writes_enabled=True,
        action_router_executor=lambda *_: {"summary": "unexpected"},
    )

    assert result.canceled is True
    assert result.executed is False
    assert result.status == "canceled"
    assert "No se ejecuto" in result.message


def test_wrong_action_id_fails_closed() -> None:
    intake = _cfdi_intake_without_missing()

    result = handle_document_confirmation_command(
        text="CONFIRMAR accion docact_wrong",
        intake_result=intake,
        supported_actions=supported_actions(),
        writes_enabled=True,
        action_router_executor=lambda *_: {"summary": "unexpected"},
    )

    assert result.executed is False
    assert result.status == "rejected"
    assert result.blocked_reason == "unknown_proposed_action_id"


def test_missing_fields_prevent_confirmation_and_ask_for_fields() -> None:
    intake = build_document_intake_result(
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
    action = intake["proposed_actions"][0]

    result = handle_document_confirmation_command(
        text=f"CONFIRMAR accion {action['action_id']}",
        intake_result=intake,
        supported_actions=supported_actions(),
        writes_enabled=True,
        action_router_executor=lambda *_: {"summary": "unexpected"},
    )

    assert result.executed is False
    assert result.status == "needs_clarification"
    assert result.blocked_reason == "missing_required_fields"
    assert "Faltan datos" in result.message
    assert "tournament" in result.message
