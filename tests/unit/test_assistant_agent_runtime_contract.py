from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from samchat.assistant.agent_runtime import evaluate_runtime_tool_call
from samchat.assistant.provider_execution import execute_ollama_provider
from samchat.assistant.tool_registry import build_tool_registry


class _Record(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def _tool_defs():
    return [
        {"type": "function", "function": {"name": "db_read_universal"}},
        {"type": "function", "function": {"name": "finance_expense_create"}},
    ]


def _registry():
    return build_tool_registry(
        tool_defs=_tool_defs(),
        read_tools={"db_read_universal"},
        write_tools={"finance_expense_create"},
        finance_tools={"finance_expense_create"},
        tournament_tools=set(),
        dev_tools=set(),
    )


def _payloads(tool_name: str, args: dict):
    return [
        {
            "content": "",
            "done_reason": "tool_calls",
            "tool_calls": [{"function": {"name": tool_name, "arguments": args}}],
        },
        {"content": "respuesta final", "done_reason": "stop", "tool_calls": []},
    ]


async def _run_ollama_contract(
    *,
    tool_name: str,
    args: dict | None = None,
    role: str = "admin",
    tool_policy_evaluator=None,
    recorder: dict | None = None,
):
    payloads = list(_payloads(tool_name, args or {}))
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    run_read_tool = AsyncMock(return_value={"ok": True})
    tool_trace = []
    if recorder is not None:
        recorder["run_read_tool"] = run_read_tool
        recorder["session"] = session
        recorder["tool_trace"] = tool_trace

    async def ollama_chat(**_kwargs):
        return payloads.pop(0)

    result = await execute_ollama_provider(
        model="qwen3:4b",
        normalized_mode="ahorro",
        route_info={"route": "lookup_sql", "domain": "finance"},
        raw_message="consulta",
        conversation=SimpleNamespace(id=uuid.uuid4(), updated_at=None),
        current_empleado=SimpleNamespace(id=uuid.uuid4(), rol=role),
        session=session,
        tool_trace=tool_trace,
        tool_defs=_tool_defs(),
        max_tokens=256,
        retrieval_sources=[],
        response_cache_enabled=False,
        cache_key="cache",
        tournament_key_default=None,
        bi_year=None,
        bi_scope=None,
        messages=[{"role": "user", "content": "consulta"}],
        write_tools={"finance_expense_create"},
        get_model=lambda *_args, **_kwargs: "qwen3:4b",
        ollama_chat=ollama_chat,
        ollama_message_content=lambda payload: payload.get("content") or "",
        ollama_tool_calls=lambda payload: payload.get("tool_calls") or [],
        ollama_assistant_message=lambda payload: {
            "role": "assistant",
            "content": payload.get("content") or "",
        },
        run_read_tool=run_read_tool,
        ensure_citations=lambda answer, _sources: answer,
        tool_trace_has_write_intent=lambda _trace: False,
        assistant_response_cache_set=lambda **_kwargs: None,
        pending_confirmation_cls=_Record,
        assistant_run_cls=_Record,
        assistant_message_cls=_Record,
        message_response_cls=_Record,
        tool_policy_evaluator=tool_policy_evaluator,
    )
    return result, tool_trace, run_read_tool, session


@pytest.mark.asyncio
async def test_flag_off_provider_contract_preserves_legacy_read_behavior():
    result, tool_trace, run_read_tool, session = await _run_ollama_contract(
        tool_name="db_read_universal",
        args={"table": "expenses"},
        role="user",
        tool_policy_evaluator=None,
    )

    assert result.assistant_message == "respuesta final"
    run_read_tool.assert_awaited_once()
    assert not [item for item in tool_trace if "assistant_policy" in item]
    assert not [item for item in tool_trace if "blocked_tool" in item]
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_flag_on_unknown_tool_is_denied_without_side_effects():
    def evaluator(tool_name, args, role):
        return evaluate_runtime_tool_call(
            tool_name=tool_name,
            args=args,
            role=role,
            registry=_registry(),
        )

    recorder = {}
    with pytest.raises(HTTPException) as exc_info:
        await _run_ollama_contract(
            tool_name="unknown_tool",
            role="admin",
            tool_policy_evaluator=evaluator,
            recorder=recorder,
        )

    assert exc_info.value.status_code == 403
    assert "unknown_tool" in exc_info.value.detail
    assert recorder["run_read_tool"].await_count == 0
    assert recorder["session"].commit.await_count == 0


@pytest.mark.asyncio
async def test_flag_on_write_blocks_without_tool_side_effect(monkeypatch):
    monkeypatch.delenv("ASSISTANT_AGENT_RUNTIME_READONLY_ONLY", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_WRITES_ENABLED", raising=False)

    def evaluator(tool_name, args, role):
        return evaluate_runtime_tool_call(
            tool_name=tool_name,
            args=args,
            role=role,
            registry=_registry(),
        )

    recorder = {}
    with pytest.raises(HTTPException) as exc_info:
        await _run_ollama_contract(
            tool_name="finance_expense_create",
            args={"amount": 100},
            role="admin",
            tool_policy_evaluator=evaluator,
            recorder=recorder,
        )

    assert exc_info.value.status_code == 403
    assert "runtime_readonly_write_blocked" in exc_info.value.detail
    assert recorder["run_read_tool"].await_count == 0
    assert recorder["session"].commit.await_count == 0
    policy_items = [
        item["assistant_policy"]
        for item in recorder["tool_trace"]
        if "assistant_policy" in item
    ]
    assert policy_items[-1]["decision"] == "deny"
    assert policy_items[-1]["reason"] == "runtime_readonly_write_blocked"


@pytest.mark.asyncio
async def test_flag_on_read_requires_authorized_role_without_side_effects():
    def evaluator(tool_name, args, role):
        return evaluate_runtime_tool_call(
            tool_name=tool_name,
            args=args,
            role=role,
            registry=_registry(),
        )

    recorder = {}
    with pytest.raises(HTTPException) as exc_info:
        await _run_ollama_contract(
            tool_name="db_read_universal",
            args={"table": "expenses"},
            role="user",
            tool_policy_evaluator=evaluator,
            recorder=recorder,
        )

    assert exc_info.value.status_code == 403
    assert "role_not_allowed:user" in exc_info.value.detail
    assert recorder["run_read_tool"].await_count == 0
    assert recorder["session"].commit.await_count == 0


@pytest.mark.asyncio
async def test_flag_on_read_allowed_for_authorized_role():
    def evaluator(tool_name, args, role):
        return evaluate_runtime_tool_call(
            tool_name=tool_name,
            args=args,
            role=role,
            registry=_registry(),
        )

    result, tool_trace, run_read_tool, _session = await _run_ollama_contract(
        tool_name="db_read_universal",
        args={"table": "expenses"},
        role="admin",
        tool_policy_evaluator=evaluator,
    )

    assert result.assistant_message == "respuesta final"
    run_read_tool.assert_awaited_once()
    policy_items = [
        item["assistant_policy"] for item in tool_trace if "assistant_policy" in item
    ]
    assert policy_items[-1]["decision"] == "allow"
