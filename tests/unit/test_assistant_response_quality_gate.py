from types import SimpleNamespace
from unittest.mock import patch

import pytest

from samchat.assistant.conversation_service import run_message_turn_with_pending
from samchat.assistant.response_quality_gate import evaluate_response_quality
from samchat.assistant.router import _maybe_append_export_prompt


BROKEN_SMALL_CAPS = (
    "ᴍᴇɴᴛ ᴛʜᴇ ᴛᴇɴᴏᴜɴ ᴛʜᴇ ᴄᴏɴᴛᴀʙʟɪᴛʏ ᴘᴀᴄᴋ ᴘʟᴀʏᴇᴅ ᴛᴏ ᴛʜᴇ "
    "ᴛᴏʀɴᴇᴇ. ᴛʜᴇ ᴛᴏʀɴᴇᴇ ᴛʜᴇɴ ᴛʜᴇ ᴄᴏɴᴛᴀʙʟɪᴛʏ ᴘʟᴀʏᴇᴅ ᴛᴏ ᴛʜᴇ "
    "ᴛᴏʀɴᴇᴇ. ᴛʜᴇ ᴛᴏʀɴᴇᴇ ᴛʜᴇɴ ᴛʜᴇ ᴄᴏɴᴛᴀʙʟɪᴛʏ ᴘʟᴀʏᴇᴅ ᴛᴏ ᴛʜᴇ "
    "ᴛᴏʀɴᴇᴇ."
)


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1


async def _pending_none(**_kwargs):
    return None


async def _provider_must_not_be_called(**_kwargs):  # pragma: no cover - sentinel
    raise AssertionError("provider path should not be called")


async def _provider_returns_bad_text(**_kwargs):
    return SimpleNamespace(
        assistant_message=BROKEN_SMALL_CAPS,
        tool_trace=[{"tool": "provider", "result": {"ok": True}}],
        run_id="run-bad",
        pending_confirmation=None,
        preview_render=None,
    )


def test_quality_gate_blocks_unicode_repeated_fallback() -> None:
    verdict = evaluate_response_quality(BROKEN_SMALL_CAPS)

    assert verdict.ok is False
    assert verdict.reason in {"unicode_gibberish", "repeated_text_loop"}


def test_quality_gate_allows_normal_executive_spanish_answer() -> None:
    verdict = evaluate_response_quality(
        "Sí hay contabilidad cargada. Hay 21 documentos, 22 gastos listos para COI "
        "y 0 pólizas descuadradas en el snapshot revisado. No ejecuté cambios."
    )

    assert verdict.ok is True


@pytest.mark.asyncio
async def test_conversation_blocks_bad_provider_output_before_display() -> None:
    response = await run_message_turn_with_pending(
        raw_message="tenemos algo raro?",
        conversation=SimpleNamespace(id="conv-quality", updated_at=None),
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
        assistant_turn=_provider_returns_bad_text,
        maybe_append_export_prompt=_maybe_append_export_prompt,
    )

    assert BROKEN_SMALL_CAPS not in response.assistant_message
    assert "no pasó el control de calidad" in response.assistant_message
    assert any("assistant_response_quality_gate" in item for item in response.tool_trace)


@pytest.mark.asyncio
async def test_accounting_status_uses_canonical_read_before_provider() -> None:
    async def adapter(_session, **kwargs):
        assert kwargs["intent"] == "finance.platform"
        return {
            "ok": True,
            "read_only": True,
            "intent": "finance.platform",
            "source_function": "test.finance.snapshot",
            "payload": {
                "summary": {"documents": 3, "expenses": 2, "polizas": 1},
                "accounting_close_center": {
                    "coi_ready_expenses_count": 2,
                    "pending_coi_expenses_count": 0,
                    "unbalanced_count": 0,
                },
                "tax_readiness": {"diot_blockers_count": 0, "status": "ready"},
                "payment_run": {"payable_count": 0, "payable_total": 0},
                "period": {"year": 2026, "month": 8},
                "action_queue": {"open_count": 0, "high_count": 0},
                "cash_control_center": {"payment_pressure": "low"},
                "finance_brief": {},
            },
            "source_notes": ["test snapshot"],
            "safety_labels": ["finance_platform_read_only"],
        }

    with patch("samchat.assistant.conversation_service.run_finance_read_adapter", new=adapter):
        response = await run_message_turn_with_pending(
            raw_message="tenemos contabilidad cargada?",
            conversation=SimpleNamespace(id="conv-accounting", updated_at=None),
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
        )

    assert "Sí hay información financiera/contable cargada" in response.assistant_message
    assert "Documentos: 3" in response.assistant_message
    assert any("assistant_finance_platform_read" in item for item in response.tool_trace)
