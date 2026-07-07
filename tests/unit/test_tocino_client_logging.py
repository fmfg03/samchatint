import pytest

from devnous.gastos.services import tocino_client
from devnous.gastos.services.tocino_client import (
    TocinoAPIError,
    TocinoClient,
    redact_tocino_payload_for_log,
)


class _FakeResponse:
    def __init__(self, *, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {"X-Request-ID": "req-1"}

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


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


def test_submit_ticket_error_does_not_expose_remote_body(monkeypatch):
    remote_body = (
        '{"message":"RFC REAL010101ABC rejected",'
        '"token":"SECRET_TOCINO_TOKEN","taxpayer":"REAL TAXPAYER"}'
    )
    monkeypatch.setattr(
        tocino_client.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(
            status_code=422,
            payload={"message": "RFC REAL010101ABC rejected"},
            text=remote_body,
        ),
    )
    client = TocinoClient(api_key="test-key", base_url="https://tocino.example")

    with pytest.raises(TocinoAPIError) as exc_info:
        client.submit_ticket({"ticket_id": "T-1"})

    exc = exc_info.value
    assert exc.status_code == 422
    assert str(exc) == "Tocino API request failed"
    assert exc.response_text is None
    assert "REAL010101ABC" not in str(exc)
    assert "SECRET_TOCINO_TOKEN" not in str(exc)


def test_check_invoice_status_error_does_not_expose_remote_body(monkeypatch):
    monkeypatch.setattr(
        tocino_client.requests,
        "get",
        lambda *_args, **_kwargs: _FakeResponse(
            status_code=500,
            payload={
                "detail": "internal traceback for RFC REAL010101ABC",
                "token": "SECRET_STATUS_TOKEN",
            },
            text='{"detail":"internal traceback for RFC REAL010101ABC"}',
        ),
    )
    client = TocinoClient(api_key="test-key", base_url="https://tocino.example")

    with pytest.raises(TocinoAPIError) as exc_info:
        client.check_invoice_status("T-1")

    exc = exc_info.value
    assert exc.status_code == 500
    assert str(exc) == "Tocino status check failed"
    assert exc.response_text is None
    assert "REAL010101ABC" not in str(exc)
    assert "SECRET_STATUS_TOKEN" not in str(exc)
