import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

import samchat.assistant.conversation_service as conversation_service
from samchat.assistant.bi_scope import (
    ASSISTANT_BI_SCOPES,
    bi_scope_terms,
    text_matches_bi_scope,
)
from samchat.assistant.receipt_workflow_draft import ReceiptDraftAdvance
from samchat.assistant.router import (
    AssistantAlertsRequest,
    AssistantExecutiveDashboardRequest,
    MessageCreateRequest,
)


@pytest.mark.parametrize("scope", ASSISTANT_BI_SCOPES)
def test_assistant_request_contracts_accept_every_visible_bi_scope(scope: str) -> None:
    assert MessageCreateRequest(message="hola", bi_scope=scope).bi_scope == scope
    assert AssistantExecutiveDashboardRequest(bi_scope=scope).bi_scope == scope
    assert AssistantAlertsRequest(bi_scope=scope).bi_scope == scope


@pytest.mark.parametrize(
    "request_type,payload",
    [
        (MessageCreateRequest, {"message": "hola", "bi_scope": "desconocido"}),
        (AssistantExecutiveDashboardRequest, {"bi_scope": "desconocido"}),
        (AssistantAlertsRequest, {"bi_scope": "desconocido"}),
    ],
)
def test_assistant_request_contracts_reject_unknown_bi_scope(
    request_type, payload
) -> None:
    with pytest.raises(ValidationError):
        request_type(**payload)


def test_bi_scope_matching_normalizes_accents_and_hyphens() -> None:
    assert text_matches_bi_scope("Copa Telmex Telcel de Fútbol", "copa-telmex")
    assert text_matches_bi_scope("copa-club-america-2026", "copa-america")
    assert text_matches_bi_scope("Liga Telmex Telcel de Béisbol", "beisbol")
    assert not text_matches_bi_scope("Liga Telmex Telcel de Béisbol", "copa-telmex")
    assert not text_matches_bi_scope("Futbolito Bimbo", "copa-telmex")


def test_unknown_bi_scope_has_no_query_terms() -> None:
    assert bi_scope_terms("desconocido") == []


@pytest.mark.asyncio
async def test_message_turn_forwards_bi_context_to_receipt_draft(monkeypatch) -> None:
    captured = {}

    async def fake_advance_receipt_draft(**kwargs):
        captured.update(kwargs)
        return ReceiptDraftAdvance(message="preview gobernado")

    async def no_pending_run(**_kwargs):
        return None

    async def must_not_execute(**_kwargs):
        raise AssertionError("unexpected fallback execution")

    monkeypatch.setattr(
        conversation_service,
        "advance_receipt_draft",
        fake_advance_receipt_draft,
    )
    session = AsyncMock()
    session.add = MagicMock()
    conversation = SimpleNamespace(id=uuid.uuid4(), updated_at=None)
    employee = SimpleNamespace(id=uuid.uuid4())

    result = await conversation_service.run_message_turn_with_pending(
        raw_message="Es personal",
        conversation=conversation,
        current_empleado=employee,
        session=session,
        request=SimpleNamespace(),
        tournament_key=None,
        bi_year=2026,
        bi_scope="copa-telmex",
        bi_segment=None,
        assistant_mode="ahorro",
        openai_api_key=None,
        latest_pending_run_for_conversation=no_pending_run,
        is_explicit_approval_message=lambda _message: False,
        is_explicit_rejection_message=lambda _message: False,
        confirm_pending_run=must_not_execute,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=must_not_execute,
        assistant_turn=must_not_execute,
        maybe_append_export_prompt=lambda message, _trace: message,
    )

    assert captured["bi_year"] == 2026
    assert captured["bi_scope"] == "copa-telmex"
    assert result.assistant_message == "preview gobernado"
    assert result.tool_trace[0]["receipt_workflow_draft"]["writes_attempted"] is False
