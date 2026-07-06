from devnous.gastos.services.tocino_client import redact_tocino_payload_for_log


def test_tocino_payload_log_redacts_sensitive_taxpayer_fields():
    payload = {
        "tax_id": "REAL010101ABC",
        "taxpayer": "REAL TAXPAYER SA DE CV",
        "taxpayer_name": "REAL",
        "taxpayer_last_name": "TAXPAYER",
        "taxpayer_second_last_name": "SA",
        "street_address_1": "PRIVATE STREET",
        "street_address_2": "PRIVATE COLONY",
        "ext_num": "123",
        "int_num": "4",
        "postal_code": "01234",
        "card_last_digits": "1234",
        "csf_pdf": "base64-csf",
        "file": "base64-receipt",
        "filename": "receipt.jpg",
        "cfdi_use_code": "G03",
        "payment_form": "Tarjeta",
    }

    redacted = redact_tocino_payload_for_log(payload)

    assert "file" not in redacted
    assert redacted["tax_id"] == "[REDACTED]"
    assert redacted["taxpayer"] == "[REDACTED]"
    assert redacted["street_address_1"] == "[REDACTED]"
    assert redacted["postal_code"] == "[REDACTED]"
    assert redacted["card_last_digits"] == "[REDACTED]"
    assert redacted["csf_pdf"] == "[REDACTED]"
    assert redacted["filename"] == "receipt.jpg"
    assert redacted["cfdi_use_code"] == "G03"
    assert redacted["payment_form"] == "Tarjeta"
    assert "REAL010101ABC" not in redacted.values()
    assert "REAL TAXPAYER SA DE CV" not in redacted.values()
    assert "base64-receipt" not in redacted.values()

