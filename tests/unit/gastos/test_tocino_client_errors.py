import pytest
import requests

from devnous.gastos.services.tocino_client import (
    TOCINO_ERROR_API_UNAVAILABLE,
    TOCINO_ERROR_AUTH,
    TOCINO_ERROR_BAD_RESPONSE,
    TOCINO_ERROR_RATE_LIMITED,
    TOCINO_ERROR_VALIDATION,
    TocinoAPIError,
    TocinoClient,
    classify_tocino_status,
)


class _Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {"X-Request-ID": "req-test"}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_classify_tocino_status_groups_operational_failures():
    assert classify_tocino_status(0) == TOCINO_ERROR_API_UNAVAILABLE
    assert classify_tocino_status(401) == TOCINO_ERROR_AUTH
    assert classify_tocino_status(403) == TOCINO_ERROR_AUTH
    assert classify_tocino_status(422) == TOCINO_ERROR_VALIDATION
    assert classify_tocino_status(429) == TOCINO_ERROR_RATE_LIMITED
    assert classify_tocino_status(503) == TOCINO_ERROR_API_UNAVAILABLE


@pytest.mark.parametrize(
    ("status_code", "payload", "expected_category", "expected_message"),
    [
        (401, {"message": "bad key"}, TOCINO_ERROR_AUTH, "credenciales"),
        (422, {"field": ["required"]}, TOCINO_ERROR_VALIDATION, "rechaz? los datos"),
        (503, {"message": "maintenance"}, TOCINO_ERROR_API_UNAVAILABLE, "ca?da"),
    ],
)
def test_submit_ticket_raises_classified_error(monkeypatch, status_code, payload, expected_category, expected_message):
    def fake_post(*args, **kwargs):
        return _Response(status_code, payload=payload, text=str(payload))

    monkeypatch.setattr(requests, "post", fake_post)
    client = TocinoClient(api_key="secret-test", base_url="https://tocino.test")

    with pytest.raises(TocinoAPIError) as exc_info:
        client.submit_ticket({"filename": "ticket.jpg"})

    exc = exc_info.value
    assert exc.category == expected_category
    assert expected_message in exc.user_message
    assert "secret-test" not in exc.user_message


def test_submit_ticket_timeout_is_api_unavailable(monkeypatch):
    def fake_post(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(requests, "post", fake_post)
    client = TocinoClient(api_key="secret-test", base_url="https://tocino.test")

    with pytest.raises(TocinoAPIError) as exc_info:
        client.submit_ticket({"filename": "ticket.jpg"})

    assert exc_info.value.status_code == 0
    assert exc_info.value.category == TOCINO_ERROR_API_UNAVAILABLE
    assert exc_info.value.retryable is True


def test_check_invoice_status_invalid_json_is_bad_response(monkeypatch):
    def fake_get(*args, **kwargs):
        return _Response(200, payload=ValueError("not json"), text="not json")

    monkeypatch.setattr(requests, "get", fake_get)
    client = TocinoClient(api_key="secret-test", base_url="https://tocino.test")

    with pytest.raises(TocinoAPIError) as exc_info:
        client.check_invoice_status("T-1")

    assert exc_info.value.category == TOCINO_ERROR_BAD_RESPONSE
