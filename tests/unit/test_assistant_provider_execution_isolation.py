import asyncio
import time
from types import SimpleNamespace

import pytest
from samchat.assistant.provider_execution import (
    PROVIDER_TIMEOUT_REASON,
    execute_anthropic_provider,
)


class DummyMessages:
    def __init__(self, *, delay: float, response: object) -> None:
        self.delay = delay
        self.response = response

    def create(self, **kwargs):
        time.sleep(self.delay)
        return self.response


class DummyClient:
    def __init__(self, *, delay: float, response: object) -> None:
        self.messages = DummyMessages(delay=delay, response=response)


class DummySession:
    def __init__(self) -> None:
        self.added = []
        self.commits = 0

    def add(self, item) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        self.commits += 1


class DummyMessage:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class DummyRun:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class DummyResponse:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


async def _history_messages(*args, **kwargs):
    return []


async def _run_read_tool(*args, **kwargs):
    raise AssertionError("read tool should not be invoked")


def _base_kwargs(*, client: DummyClient, tool_trace: list):
    return {
        "model": "claude-test",
        "normalized_mode": "balanceado",
        "route_info": {"route": "finance", "domain": "finance"},
        "raw_message": "hola",
        "conversation": SimpleNamespace(id="conversation-1"),
        "current_empleado": SimpleNamespace(id="employee-1", rol="finanzas"),
        "session": DummySession(),
        "tool_trace": tool_trace,
        "tool_defs": [],
        "max_tokens": 100,
        "retrieval_sources": [],
        "response_cache_enabled": False,
        "cache_key": "cache-key",
        "tournament_key_default": None,
        "bi_year": None,
        "bi_scope": None,
        "write_tools": set(),
        "route_prompt": "route",
        "language_prompt": "lang",
        "hermes_profile_prompt": None,
        "workspace_context": None,
        "module_key_default": None,
        "module_label_default": None,
        "module_context_default": None,
        "retrieval_context": None,
        "assistant_system_prompt": lambda: "system",
        "history_messages": _history_messages,
        "get_anthropic_client": lambda: client,
        "tool_defs_anthropic": lambda defs: [],
        "anthropic_text_from_blocks": lambda blocks: "respuesta",
        "anthropic_message_from_blocks": lambda blocks: [],
        "run_read_tool": _run_read_tool,
        "ensure_citations": lambda text, sources: text,
        "tool_trace_has_write_intent": lambda trace: False,
        "assistant_response_cache_set": lambda **kwargs: None,
        "pending_confirmation_cls": object,
        "assistant_run_cls": DummyRun,
        "assistant_message_cls": DummyMessage,
        "message_response_cls": DummyResponse,
        "tool_policy_evaluator": None,
    }


@pytest.mark.asyncio
async def test_anthropic_provider_call_does_not_block_event_loop(monkeypatch):
    monkeypatch.setenv("ASSISTANT_AGENT_PROVIDER_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("ASSISTANT_AGENT_RUNTIME_TOTAL_BUDGET_SECONDS", "5")
    monkeypatch.setenv("ASSISTANT_AGENT_PROVIDER_MAX_CONCURRENCY", "1")
    tool_trace = []
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="respuesta")])
    client = DummyClient(delay=0.25, response=response)
    ticks = 0

    async def ticker():
        nonlocal ticks
        deadline = time.monotonic() + 0.22
        while time.monotonic() < deadline:
            await asyncio.sleep(0.03)
            ticks += 1

    result, _ = await asyncio.gather(
        execute_anthropic_provider(
            **_base_kwargs(client=client, tool_trace=tool_trace)
        ),
        ticker(),
    )

    assert result.assistant_message == "respuesta"
    assert ticks >= 3
    assert any(
        step.get("provider_call", {}).get("provider") == "anthropic"
        for step in tool_trace
    )


@pytest.mark.asyncio
async def test_anthropic_provider_timeout_is_controlled_and_traced(
    monkeypatch,
):
    monkeypatch.setenv("ASSISTANT_AGENT_PROVIDER_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setenv("ASSISTANT_AGENT_RUNTIME_TOTAL_BUDGET_SECONDS", "1")
    monkeypatch.setenv("ASSISTANT_AGENT_PROVIDER_MAX_CONCURRENCY", "1")
    tool_trace = []
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="late")])
    client = DummyClient(delay=0.2, response=response)

    result = await execute_anthropic_provider(
        **_base_kwargs(client=client, tool_trace=tool_trace)
    )

    assert result.pending_confirmation is None
    assert "tardó demasiado" in result.assistant_message
    assert any(
        step.get("provider_error", {}).get("reason") == PROVIDER_TIMEOUT_REASON
        for step in tool_trace
    )
