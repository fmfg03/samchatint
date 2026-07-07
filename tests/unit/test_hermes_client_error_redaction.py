from __future__ import annotations

import pytest

from samchat.assistant import hermes_client
from samchat.assistant.hermes_client import (
    HermesSamchatAssistantClient,
    SamchatAssistantAPIError,
)


class _FakeResponse:
    def __init__(self, *, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


def _client() -> HermesSamchatAssistantClient:
    return HermesSamchatAssistantClient(
        base_url="https://assistant.example/api/assistant",
        service_token="test-service-token",
        actor_email="operator@example.com",
        timeout_seconds=1,
    )


def test_hermes_client_error_does_not_expose_remote_detail(monkeypatch):
    monkeypatch.setattr(
        hermes_client.requests,
        "request",
        lambda **_kwargs: _FakeResponse(
            status_code=500,
            payload={
                "detail": "traceback includes SECRET_ASSISTANT_TOKEN and user@example.com"
            },
            text='{"detail":"traceback includes SECRET_ASSISTANT_TOKEN"}',
        ),
    )

    with pytest.raises(SamchatAssistantAPIError) as exc_info:
        _client()._request("GET", "/conversations")

    exc = exc_info.value
    assert exc.status_code == 500
    assert str(exc) == "Samchat assistant request failed"
    assert exc.response_text is None
    assert "SECRET_ASSISTANT_TOKEN" not in str(exc)
    assert "user@example.com" not in str(exc)


def test_hermes_client_network_error_does_not_expose_request_exception(monkeypatch):
    monkeypatch.setattr(
        hermes_client.requests,
        "request",
        lambda **_kwargs: (_ for _ in ()).throw(
            hermes_client.requests.RequestException(
                "connect failed with token SECRET_REQUEST_TOKEN"
            )
        ),
    )

    with pytest.raises(SamchatAssistantAPIError) as exc_info:
        _client()._request("GET", "/conversations")

    exc = exc_info.value
    assert exc.status_code == 0
    assert str(exc) == "Samchat assistant unreachable"
    assert exc.response_text is None
    assert "SECRET_REQUEST_TOKEN" not in str(exc)
