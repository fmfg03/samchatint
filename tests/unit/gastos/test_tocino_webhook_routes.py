from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import webhook_handler


@pytest.fixture(autouse=True)
def _clear_runtime_env(monkeypatch):
    for name in ("SAMCHAT_ENV", "ENVIRONMENT", "APP_ENV", "FASTAPI_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("TOCINO_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("TOCINO_WEBHOOK_SIGNATURE_HEADER", raising=False)


@pytest.mark.asyncio
async def test_receive_tocino_webhook_rejects_missing_secret_in_production(monkeypatch):
    monkeypatch.setenv("SAMCHAT_ENV", "production")
    payload = {"ticket_id": "T-1", "nova_request_id": "N-1", "status": "finalizado"}
    session = AsyncMock()
    request = SimpleNamespace(
        headers={},
        body=AsyncMock(return_value=json.dumps(payload).encode("utf-8")),
    )
    apply_mock = AsyncMock()
    monkeypatch.setattr(webhook_handler, "apply_tocino_payload_to_db", apply_mock)

    with pytest.raises(HTTPException) as exc_info:
        await webhook_handler.receive_tocino_webhook(
            request=request,
            typeform_signature=None,
            session=session,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Webhook signature verification is not configured"
    apply_mock.assert_not_called()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_receive_tocino_webhook_accepts_configured_secret(monkeypatch):
    monkeypatch.setenv("SAMCHAT_ENV", "production")
    monkeypatch.setenv("TOCINO_WEBHOOK_SECRET", "test-webhook-secret")
    payload = {"ticket_id": "T-1", "nova_request_id": "N-1", "status": "finalizado"}
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(
        b"test-webhook-secret",
        body,
        hashlib.sha256,
    ).hexdigest()
    session = AsyncMock()
    request = SimpleNamespace(
        headers={"typeform-signature": signature},
        body=AsyncMock(return_value=body),
    )
    monkeypatch.setattr(
        webhook_handler,
        "apply_tocino_payload_to_db",
        AsyncMock(
            return_value={
                "status": "success",
                "nova_request_id": "N-1",
                "estado_factura": "completada",
                "synced_to_expenses": True,
            }
        ),
    )

    result = await webhook_handler.receive_tocino_webhook(
        request=request,
        typeform_signature=None,
        session=session,
    )

    assert result["status"] == "success"
    assert result["nova_request_id"] == "N-1"
