from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import samchat.assistant.router as assistant_router
from devnous.tournaments.core.telegram_adapter import TelegramAdapter


class _SessionContext:
    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def __init__(self, session):
        self.session = session


@pytest.mark.asyncio
async def test_telegram_confirmation_uses_server_provider_credentials(monkeypatch):
    adapter = object.__new__(TelegramAdapter)
    adapter._assistant_domain_mode_by_chat = {}
    adapter._assistant_pending_run_by_chat = {(42, "empresa"): "pending-run"}
    adapter._assistant_get_or_create_conversation_id = AsyncMock(return_value="conv-42")
    session = SimpleNamespace()
    adapter._assistant_session_maker = lambda: lambda: _SessionContext(session)
    adapter._assistant_mode = lambda _chat_id: "ahorro"
    confirm_write = AsyncMock(
        return_value=SimpleNamespace(
            pending_confirmation=None,
            assistant_message="Confirmada.",
        )
    )
    monkeypatch.setattr(assistant_router, "confirm_write", confirm_write)

    result = await adapter._assistant_confirm_pending(
        chat_id=42,
        empleado=SimpleNamespace(id="empleado-1"),
        approve=True,
    )

    assert result == "Confirmada."
    assert adapter._assistant_pending_run_by_chat == {}
    kwargs = confirm_write.await_args.kwargs
    assert kwargs["payload"].run_id == "pending-run"
    assert kwargs["payload"].approve is True
    assert kwargs["payload"].assistant_mode == "ahorro"
    assert kwargs["conversation_id"] == "conv-42"
    assert kwargs["current_empleado"].id == "empleado-1"
    assert kwargs["session"] is session
    assert "openai_api_key" not in kwargs
    assert "request" not in kwargs
