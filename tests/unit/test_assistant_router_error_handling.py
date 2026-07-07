from __future__ import annotations

import uuid
from io import BytesIO
from types import SimpleNamespace
from urllib import error as urllib_error
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import samchat.assistant.router as assistant_router


def _empleado():
    return SimpleNamespace(id=uuid.uuid4(), rol="admin", nombre="Test User")


def _http_error(body: bytes, *, code: int = 403) -> urllib_error.HTTPError:
    return urllib_error.HTTPError(
        url="https://example.invalid/private",
        code=code,
        msg="Forbidden",
        hdrs={},
        fp=BytesIO(body),
    )


def test_supabase_auth_error_detail_does_not_expose_remote_body(monkeypatch):
    monkeypatch.setattr(
        assistant_router.urllib_request,
        "urlopen",
        MagicMock(
            side_effect=_http_error(
                b'{"error":"invalid token","access_token":"SECRET_REMOTE_TOKEN"}',
                code=401,
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        assistant_router._sync_fetch_json(
            "https://example.invalid/auth",
            headers={"Authorization": "Bearer local-token"},
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Supabase auth rejected token"
    assert "SECRET_REMOTE_TOKEN" not in exc_info.value.detail


def test_supabase_storage_error_detail_does_not_expose_remote_body(monkeypatch):
    monkeypatch.setattr(
        assistant_router.urllib_request,
        "urlopen",
        MagicMock(
            side_effect=_http_error(
                b'{"signed_url":"https://storage.example/SECRET_OBJECT"}',
                code=403,
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        assistant_router._sync_fetch_bytes(
            "https://example.invalid/storage",
            headers={"Authorization": "Bearer local-token"},
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "No se pudo descargar archivo privado"
    assert "SECRET_OBJECT" not in exc_info.value.detail


def test_sendgrid_error_detail_does_not_expose_remote_body(monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "test-sendgrid-key")
    monkeypatch.setattr(
        assistant_router.urllib_request,
        "urlopen",
        MagicMock(
            side_effect=_http_error(
                b'{"message":"rejected","email":"private@example.com","token":"SECRET"}',
                code=400,
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        assistant_router._sync_sendgrid_request(
            {
                "personalizations": [{"to": [{"email": "private@example.com"}]}],
                "from": {"email": "sender@example.com"},
                "subject": "test",
                "content": [{"type": "text/plain", "value": "hello"}],
            }
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "SendGrid rejected request"
    assert "private@example.com" not in exc_info.value.detail
    assert "SECRET" not in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_conversation_rolls_back_on_unexpected_commit_error(monkeypatch):
    empleado = _empleado()
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock(side_effect=RuntimeError("db exploded"))
    monkeypatch.setattr(
        assistant_router,
        "_find_conversation_by_external_session_id",
        AsyncMock(return_value=None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await assistant_router.create_conversation(
            payload=assistant_router.ConversationCreateRequest(title="Nueva"),
            request=SimpleNamespace(),
            current_empleado=empleado,
            session=session,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Unexpected processing error"
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_auth_bridge_supabase_rolls_back_on_unexpected_persist_error(monkeypatch):
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock(side_effect=RuntimeError("db exploded"))
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=execute_result)
    monkeypatch.setattr(
        assistant_router,
        "_load_supabase_user",
        AsyncMock(return_value={"id": "supa-1", "email": "user@example.com"}),
    )
    monkeypatch.setattr(
        assistant_router,
        "_load_supabase_roles",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        assistant_router,
        "_supabase_rpc_has_role",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        assistant_router,
        "_derive_empleado_role",
        lambda **_kwargs: "empleado",
    )

    with pytest.raises(HTTPException) as exc_info:
        await assistant_router.assistant_auth_bridge_supabase(
            payload=assistant_router.SupabaseBridgeRequest(access_token="tok"),
            request=SimpleNamespace(session={}),
            authorization=None,
            session=session,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Unexpected processing error"
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_tournaments_create_hides_unexpected_supabase_error(monkeypatch):
    empleado = _empleado()
    monkeypatch.setattr(
        assistant_router,
        "_supabase_rest_mutate",
        AsyncMock(side_effect=RuntimeError("supabase exploded")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await assistant_router.admin_tournaments_create(
            payload=assistant_router.AdminTournamentSaveRequest(
                tournament={"name": "Demo", "slug": "demo"},
                config={},
            ),
            current_empleado=empleado,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Unexpected processing error"


@pytest.mark.asyncio
async def test_admin_email_campaigns_schedule_hides_unexpected_supabase_error(
    monkeypatch,
):
    empleado = _empleado()
    monkeypatch.setattr(
        assistant_router,
        "_supabase_rest_mutate",
        AsyncMock(side_effect=RuntimeError("supabase exploded")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await assistant_router.admin_email_campaigns_schedule(
            payload=assistant_router.EmailScheduleRequest(
                recipients=[
                    assistant_router.EmailRecipientRequest(email="test@example.com")
                ],
                subject="Hola",
                html_content="<p>Test</p>",
                tournament_id=str(uuid.uuid4()),
                scheduled_at=assistant_router.datetime.utcnow()
                + assistant_router.timedelta(hours=1),
            ),
            current_empleado=empleado,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Unexpected processing error"


@pytest.mark.asyncio
async def test_create_solicitud_from_commitment_keeps_value_error_as_400(monkeypatch):
    empleado = _empleado()
    session = AsyncMock()
    monkeypatch.setattr(
        assistant_router,
        "execute_canonical_action",
        AsyncMock(side_effect=ValueError("bad input")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await assistant_router.admin_tournament_operational_commitment_create_solicitud(
            tournament_id=str(uuid.uuid4()),
            commitment_id=str(uuid.uuid4()),
            payload=assistant_router.TournamentCommitmentSolicitudRequest(
                proveedor_cliente_id=str(uuid.uuid4())
            ),
            current_empleado=empleado,
            session=session,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "bad input"


@pytest.mark.asyncio
async def test_create_solicitud_from_commitment_rolls_back_unexpected_error(monkeypatch):
    empleado = _empleado()
    session = AsyncMock()
    monkeypatch.setattr(
        assistant_router,
        "execute_canonical_action",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await assistant_router.admin_tournament_operational_commitment_create_solicitud(
            tournament_id=str(uuid.uuid4()),
            commitment_id=str(uuid.uuid4()),
            payload=assistant_router.TournamentCommitmentSolicitudRequest(
                proveedor_cliente_id=str(uuid.uuid4())
            ),
            current_empleado=empleado,
            session=session,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Unexpected processing error"
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_message_rolls_back_on_unexpected_turn_error(monkeypatch):
    empleado = _empleado()
    session = AsyncMock()
    conversation = SimpleNamespace(id=uuid.uuid4(), metadata_={}, tournament_key=None)
    monkeypatch.setattr(assistant_router, "_enforce_rate_limit", lambda **_kwargs: None)
    monkeypatch.setattr(
        assistant_router,
        "_load_conversation",
        AsyncMock(return_value=conversation),
    )
    monkeypatch.setattr(
        assistant_router,
        "run_message_turn_with_pending",
        AsyncMock(side_effect=RuntimeError("turn exploded")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await assistant_router.create_message(
            payload=assistant_router.MessageCreateRequest(message="hola"),
            request=SimpleNamespace(),
            conversation_id=str(uuid.uuid4()),
            openai_api_key=None,
            current_empleado=empleado,
            session=session,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Unexpected processing error"
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_write_rolls_back_on_unexpected_confirm_error(monkeypatch):
    empleado = _empleado()
    session = AsyncMock()
    conversation = SimpleNamespace(id=uuid.uuid4())
    run = SimpleNamespace(status="pending_confirmation")
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = run
    monkeypatch.setattr(assistant_router, "_enforce_rate_limit", lambda **_kwargs: None)
    monkeypatch.setattr(
        assistant_router,
        "_load_conversation",
        AsyncMock(return_value=conversation),
    )
    session.execute = AsyncMock(return_value=execute_result)
    monkeypatch.setattr(
        assistant_router,
        "_confirm_pending_run",
        AsyncMock(side_effect=RuntimeError("confirm exploded")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await assistant_router.confirm_write(
            payload=assistant_router.ConfirmRequest(run_id=str(uuid.uuid4())),
            conversation_id=str(uuid.uuid4()),
            openai_api_key=None,
            current_empleado=empleado,
            session=session,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Unexpected processing error"
    session.rollback.assert_awaited_once()
