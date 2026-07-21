from samchat.assistant.action_router import supported_actions
from samchat.assistant.document_classifier import (
    ACCOUNTING_BALANCE,
    CFDI_INVOICE,
    EXPENSE_RECEIPT,
    PAYMENT_PROOF,
    ROSTER,
    UNKNOWN_OR_GENERIC,
)
from samchat.assistant.document_intake import build_document_intake_result


def test_intake_accounting_balance_extracts_period_totals_and_preview_action() -> None:
    result = build_document_intake_result(
        conversation_id="conv-1",
        file_name="BALANZA MAYO 2026.xlsx",
        file_kind="spreadsheet",
        records=[
            {
                "Cuenta": "1000",
                "Descripcion de la cuenta": "Banco",
                "Total de cargos": "500.00",
                "Total de abonos": "500.00",
                "Saldo final": "100.00",
            },
            {
                "Cuenta": "2000",
                "Descripcion de la cuenta": "Ingresos",
                "Total de cargos": "100.00",
                "Total de abonos": "100.00",
                "Saldo final": "0.00",
            },
        ],
        supported_actions=supported_actions(),
    )

    assert result.detected_document_type == ACCOUNTING_BALANCE
    assert result.entities["period"] == "2026-05"
    assert result.entities["account_count"] == 2
    assert result.entities["imbalance"] == "0.00"
    assert "company" in result.missing_fields
    assert (
        result.proposed_actions[0]["canonical_action"] == "executive.accounting_report"
    )
    assert result.safety["can_execute_without_confirmation"] is False


def test_intake_roster_extracts_players_and_requires_tournament() -> None:
    result = build_document_intake_result(
        conversation_id="conv-2",
        file_name="roster.xlsx",
        file_kind="spreadsheet",
        records=[
            {
                "Equipo": "Tigres",
                "Categoria": "Sub-17",
                "Nombre": "Ana",
                "Apellido": "Lopez",
                "CURP": "MALFORMADA",
            },
            {
                "Equipo": "Tigres",
                "Categoria": "Sub-17",
                "Nombre": "Luis",
                "Apellido": "Garcia",
                "CURP": "GALL090101HDFRRS09",
            },
        ],
        supported_actions=supported_actions(),
    )

    assert result.detected_document_type == ROSTER
    assert result.entities["team_name"] == "Tigres"
    assert result.entities["category"] == "Sub-17"
    assert result.entities["player_count"] == 2
    assert result.entities["invalid_curp_count"] == 1
    assert "tournament" in result.missing_fields
    assert any(
        action["canonical_action"] == "operations.verify_player_document"
        for action in result.proposed_actions
    )


def test_intake_cfdi_extracts_xml_fields_and_requires_candidate_choice() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
    Fecha="2026-05-12T10:00:00" Total="45000.00" Moneda="MXN">
  <cfdi:Emisor Rfc="AAA010101AAA" Nombre="Proveedor SA"/>
  <cfdi:Receptor Rfc="BBB010101BBB"/>
  <cfdi:Conceptos><cfdi:Concepto Descripcion="Servicios"/></cfdi:Conceptos>
  <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="123E4567-E89B-12D3-A456-426614174000"/></cfdi:Complemento>
</cfdi:Comprobante>
"""
    result = build_document_intake_result(
        conversation_id="conv-3",
        file_name="factura.xml",
        file_kind="text",
        text=xml,
        supported_actions=supported_actions(),
    )

    assert result.detected_document_type == CFDI_INVOICE
    assert result.entities["uuid"] == "123E4567-E89B-12D3-A456-426614174000"
    assert result.entities["issuer_rfc"] == "AAA010101AAA"
    assert result.entities["amount"] == "45000.00"
    assert "expense_or_document_candidate" in result.missing_fields
    assert any(
        action["canonical_action"] == "receipts.link_expense_to_cfdi"
        for action in result.proposed_actions
    )


def test_intake_payment_proof_extracts_reference_and_blocks_write() -> None:
    result = build_document_intake_result(
        conversation_id="conv-4",
        file_name="spei.txt",
        file_kind="text",
        text=(
            "Comprobante de pago SPEI\n"
            "Monto: $45,000.00\n"
            "Fecha: 2026-05-13\n"
            "Clave de rastreo: SPEI123ABC\n"
            "Beneficiario: Proveedor SA\n"
            "Concepto: Informe A"
        ),
        supported_actions=supported_actions(),
    )

    assert result.detected_document_type == PAYMENT_PROOF
    assert result.entities["amount"] == "45,000.00"
    assert result.entities["bank_reference"] == "SPEI123ABC"
    write_action = next(
        action
        for action in result.proposed_actions
        if action["canonical_action"] == "receipts.register_document_payment"
    )
    assert write_action["requires_confirmation"] is True
    assert write_action["write_blocked"] is True


def test_intake_unknown_document_has_no_write_proposals() -> None:
    result = build_document_intake_result(
        conversation_id="conv-5",
        file_name="generic.txt",
        file_kind="text",
        text="Notas generales sin workflow claro.",
        supported_actions=supported_actions(),
    )

    assert result.detected_document_type == UNKNOWN_OR_GENERIC
    assert result.proposed_actions == []
    assert result.safety["blocked_reason"] == "unsupported_document_type"


def test_intake_expense_receipt_is_distinct_from_payment_proof_and_binds_evidence() -> (
    None
):
    result = build_document_intake_result(
        conversation_id="conv-receipt",
        file_name="ticket.jpg",
        file_kind="image",
        text=(
            "Ticket de compra\nComercio: Papeleria Central\n"
            "Total: $1,250.00\nFecha: 2026-07-20\n"
            "Concepto: Material de oficina"
        ),
        evidence_sha256="a" * 64,
        supported_actions=supported_actions(),
    )

    assert result.detected_document_type == EXPENSE_RECEIPT
    assert result.entities["merchant"] == "Papeleria Central"
    assert result.entities["amount"] == "1,250.00"
    assert result.evidence_sha256 == "a" * 64
    assert "payment_subject_type" in result.missing_fields
    assert "bank_reference" not in result.entities


def test_intake_expense_receipt_accepts_markdown_emphasis_separators() -> None:
    result = build_document_intake_result(
        conversation_id="conv-markdown-receipt",
        file_name="ticket.png",
        file_kind="image",
        text=(
            "Ticket de compra\n"
            "Comercio ** Witness Controlado SamChat\n"
            "Total ** 1.00\n"
            "Fecha ** 2026-07-21\n"
            "Concepto ** WITNESS STAGE 3 NO PAGAR\n"
            "Moneda MXN"
        ),
        evidence_sha256="b" * 64,
        supported_actions=supported_actions(),
    )

    assert result.detected_document_type == EXPENSE_RECEIPT
    assert result.entities["merchant"] == "Witness Controlado SamChat"
    assert result.entities["amount"] == "1.00"
    assert result.entities["concept"] == "WITNESS STAGE 3 NO PAGAR"


def test_intake_result_serializes_compact_public_fields() -> None:
    result = build_document_intake_result(
        conversation_id="conv-6",
        file_name="pago.txt",
        file_kind="text",
        text="SPEI Monto: 100 Referencia: ABC Beneficiario: Club",
        supported_actions=supported_actions(),
    )
    payload = result.to_dict()

    assert "detected_document_type" in payload
    assert "proposed_actions" in payload
    assert "chain" not in result.to_json().lower()
