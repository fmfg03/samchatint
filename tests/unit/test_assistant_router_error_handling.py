from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import samchat.assistant.router as assistant_router


def _empleado():
    return SimpleNamespace(id=uuid.uuid4(), rol="admin", nombre="Test User")


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
async def test_create_solicitud_from_commitment_rolls_back_unexpected_error(
    monkeypatch,
):
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


@pytest.mark.asyncio
async def test_confirm_write_returns_receipt_validation_error_without_500(monkeypatch):
    empleado = _empleado()
    session = AsyncMock()
    conversation = SimpleNamespace(id=uuid.uuid4())
    run = SimpleNamespace(
        id=uuid.uuid4(),
        status="pending_confirmation",
        pending_tool_args={"action": "expenses.create_personal_receipt_workflow"},
    )
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
        AsyncMock(side_effect=ValueError("Su usuario no tiene departamento asignado.")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await assistant_router.confirm_write(
            payload=assistant_router.ConfirmRequest(run_id=str(uuid.uuid4())),
            conversation_id=str(uuid.uuid4()),
            openai_api_key=None,
            current_empleado=empleado,
            session=session,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Su usuario no tiene departamento asignado."
    session.rollback.assert_awaited_once()
