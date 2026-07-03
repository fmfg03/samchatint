from __future__ import annotations

from types import SimpleNamespace

import pytest

from samchat.assistant.conversation_service import run_message_turn_with_pending
from samchat.assistant.router import _assistant_classify_request, _assistant_route_system_prompt


async def _none_pending(**_kwargs):
    return None


async def _provider_must_not_be_called(**_kwargs):  # pragma: no cover - sentinel
    raise AssertionError("provider path was called for deterministic request")


def _append_noop(message, _trace):
    return message


@pytest.mark.asyncio
async def test_deterministic_request_route_wins_without_provider_call():
    route = _assistant_classify_request("Compara gasto 2026 vs 2025 por concepto")

    assert route["route"] == "reporting"
    assert route["domain"] == "finance"
    assert "Consulta analitica" in _assistant_route_system_prompt(route)

    async def build_response(**kwargs):
        return SimpleNamespace(
            assistant_message="deterministic response",
            tool_trace=[
                {
                    "deterministic_pending": {
                        "provider_called": False,
                        "source": "request-router-integration",
                    }
                }
            ],
            pending_confirmation=None,
        )

    response = await run_message_turn_with_pending(
        raw_message="Compara gasto 2026 vs 2025 por concepto",
        conversation=SimpleNamespace(id="conv-1"),
        current_empleado=SimpleNamespace(id="emp-1"),
        session=SimpleNamespace(),
        request=None,
        tournament_key=None,
        bi_year=None,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        latest_pending_run_for_conversation=_none_pending,
        is_explicit_approval_message=lambda _text: False,
        is_explicit_rejection_message=lambda _text: False,
        confirm_pending_run=_provider_must_not_be_called,
        deterministic_pending_builders=[
            lambda **_kwargs: (
                "assistant_canonical_query",
                {"action": "executive.realtime_report"},
                "deterministic request",
            )
        ],
        build_deterministic_pending_response=build_response,
        assistant_turn=_provider_must_not_be_called,
        maybe_append_export_prompt=_append_noop,
    )

    assert response.tool_trace[0]["deterministic_pending"]["provider_called"] is False
