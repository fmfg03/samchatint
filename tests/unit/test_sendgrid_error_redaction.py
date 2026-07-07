from __future__ import annotations

from io import BytesIO
from urllib import error as urllib_error

import pytest

from devnous.gastos.services import empleado_onboarding_email
from devnous.utils import sendgrid_client


def _http_error(body: bytes, *, code: int = 400) -> urllib_error.HTTPError:
    return urllib_error.HTTPError(
        url="https://api.sendgrid.com/v3/mail/send",
        code=code,
        msg="Bad Request",
        hdrs={},
        fp=BytesIO(body),
    )


def test_sendgrid_http_error_detail_is_generic(monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-sendgrid-key")
    monkeypatch.setattr(
        sendgrid_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _http_error(
                b'{"errors":[{"message":"SECRET_SENDGRID_TOKEN user@example.com"}]}'
            )
        ),
    )

    ok, status, detail = sendgrid_client.send_sendgrid_mail_sync({"ok": True})

    assert ok is False
    assert status == 400
    assert detail == "SendGrid request failed"
    assert "SECRET_SENDGRID_TOKEN" not in detail
    assert "user@example.com" not in detail


def test_sendgrid_url_error_detail_is_generic(monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-sendgrid-key")
    monkeypatch.setattr(
        sendgrid_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib_error.URLError("network leak SECRET_URL_TOKEN")
        ),
    )

    ok, status, detail = sendgrid_client.send_sendgrid_mail_sync({"ok": True})

    assert ok is False
    assert status == 0
    assert detail == "SendGrid unreachable"
    assert "SECRET_URL_TOKEN" not in detail


@pytest.mark.asyncio
async def test_initial_password_email_failure_note_does_not_expose_sendgrid_detail(
    monkeypatch,
):
    async def _failed_send(_payload):
        return False, 400, "SECRET_SENDGRID_TOKEN user@example.com"

    monkeypatch.setattr(
        empleado_onboarding_email,
        "send_sendgrid_mail_async",
        _failed_send,
    )

    ok, note = await empleado_onboarding_email.send_initial_password_email(
        to_email="user@example.com",
        nombre="User",
        plain_password="temporary-password",
    )

    assert ok is False
    assert "SECRET_SENDGRID_TOKEN" not in note
    assert "user@example.com" not in note
    assert "sin detalle" not in note.lower()
