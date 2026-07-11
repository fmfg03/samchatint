from types import SimpleNamespace

import pytest

from samchat.assistant.analyst_intent import detect_analyst_intent
from samchat.assistant.analyst_workbench import (
    AnalystEvidence,
    run_analyst_workbench,
)
from samchat.assistant.conversation_service import (
    run_message_turn_with_pending,
)
from samchat.assistant.router import _maybe_append_export_prompt


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1

    async def execute(self, _stmt):
        raise AssertionError("no conversation evidence should be needed")


async def _pending_none(**_kwargs):
    return None


async def _provider_must_not_be_called(**_kwargs):  # pragma: no cover
    raise AssertionError("provider path should not be called")


async def _finance_rows(_intent):
    return [
        {"year": 2025, "concepto": "Uniformes", "amount": 100},
        {"year": 2026, "concepto": "Uniformes", "amount": 200},
    ]


@pytest.mark.asyncio
async def test_provider_raising_returns_provider_unavailable_in_workbench():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = [
        AnalystEvidence(
            source_type="uploaded_file",
            label="contrato.pdf",
            summary="Contrato con obligaciones y penalizaciones.",
        )
    ]

    async def provider_raises(_intent, _evidence):
        raise RuntimeError("provider blocked")

    result = await run_analyst_workbench(
        intent=intent,
        evidence=evidence,
        provider_allowed=True,
        provider_fn=provider_raises,
    )

    assert result.status == "provider_unavailable"
    assert result.provider_called is True
    assert result.actions_executed == []


@pytest.mark.asyncio
async def test_operational_request_bypasses_analyst_and_provider():
    response = await run_message_turn_with_pending(
        raw_message="Compara gasto 2026 vs 2025 por concepto",
        conversation=SimpleNamespace(id="conv-provider", updated_at=None),
        current_empleado=SimpleNamespace(id="emp-1"),
        session=_FakeSession(),
        request=None,
        tournament_key=None,
        bi_year=None,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        latest_pending_run_for_conversation=_pending_none,
        is_explicit_approval_message=lambda _text: False,
        is_explicit_rejection_message=lambda _text: False,
        confirm_pending_run=_provider_must_not_be_called,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=_provider_must_not_be_called,
        assistant_turn=_provider_must_not_be_called,
        maybe_append_export_prompt=_maybe_append_export_prompt,
        finance_rows_provider=_finance_rows,
    )

    assert "Comparación de gasto por concepto" in response.assistant_message
    assert response.tool_trace[0].get("request_intelligence_live_wiring")
    assert "analyst_workbench_live_wiring" not in response.tool_trace[0]
    assert (
        response.tool_trace[0]["request_intelligence_live_wiring"][
            "provider_called"
        ]
        is False
    )
