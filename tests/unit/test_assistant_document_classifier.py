from samchat.assistant.document_classifier import (
    ACCOUNTING_BALANCE,
    CFDI_INVOICE,
    PAYMENT_PROOF,
    ROSTER,
    TOURNAMENT_OPS,
    UNKNOWN_OR_GENERIC,
    classify_document,
)


def test_classifies_accounting_balance_from_headers() -> None:
    result = classify_document(
        file_name="BALANZA MAYO 2026.xlsx",
        file_kind="spreadsheet",
        records=[
            {
                "Cuenta": "1120",
                "Descripcion de la cuenta": "Banco",
                "Total de cargos": "100",
                "Total de abonos": "100",
                "Saldo final": "0",
            }
        ],
    )

    assert result.detected_document_type == ACCOUNTING_BALANCE
    assert 0.0 <= result.confidence <= 1.0
    assert "accounting_balance_markers" in result.signals


def test_classifies_roster_from_player_columns() -> None:
    result = classify_document(
        file_name="roster.xlsx",
        file_kind="spreadsheet",
        records=[
            {
                "Equipo": "Tigres",
                "Categoria": "Sub-17",
                "Nombre": "Ana",
                "Apellido": "Lopez",
                "CURP": "LOPA090101MDFABC09",
            }
        ],
    )

    assert result.detected_document_type == ROSTER
    assert result.confidence >= 0.7


def test_classifies_cfdi_xml() -> None:
    result = classify_document(
        file_name="factura.xml",
        file_kind="text",
        text="<cfdi:Comprobante Total='100'><tfd:TimbreFiscalDigital UUID='abc' /></cfdi:Comprobante>",
    )

    assert result.detected_document_type == CFDI_INVOICE


def test_classifies_payment_proof() -> None:
    result = classify_document(
        file_name="spei.txt",
        file_kind="text",
        text="Comprobante de pago SPEI\nClave de rastreo ABC123\nBeneficiario CLUB",
    )

    assert result.detected_document_type == PAYMENT_PROOF


def test_classifies_tournament_document() -> None:
    result = classify_document(
        file_name="fixture.txt",
        file_kind="text",
        text="Torneo: Nacional\nSede: Monterrey\nFixture semifinal 2026-05-12",
    )

    assert result.detected_document_type == TOURNAMENT_OPS


def test_unknown_document_falls_back_safely() -> None:
    result = classify_document(
        file_name="nota.txt",
        file_kind="text",
        text="Documento libre sin marcadores operativos.",
    )

    assert result.detected_document_type == UNKNOWN_OR_GENERIC
    assert 0.0 <= result.confidence <= 1.0
