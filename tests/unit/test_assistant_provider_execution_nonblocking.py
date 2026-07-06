import asyncio
import time
from types import SimpleNamespace

import pytest

from samchat.assistant.provider_execution import (
    execute_anthropic_provider,
    execute_openai_provider,
)


class _FakeSession:
    def __init__(self) -> None:
        self.added = []
        self.commits = 0

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1


class _FakeMessage:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _FakeRun:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _FakeResponse:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _SlowAnthropicMessages:
    def create(self, **_kwargs):
        time.sleep(0.2)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])


class _SlowOpenAICompletions:
    def create(self, **_kwargs):
        time.sleep(0.2)
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


async def _history_messages(*_args, **_kwargs):
    return []


async def _run_read_tool(*_args, **_kwargs):  # pragma: no cover - sentinel
    raise AssertionError("read tools must not be called for no-tool provider response")


async def _measure_event_loop_delay(coro, *, sleep_delay: float = 0.02) -> float:
    ticks = []

    async def ticker():
        await asyncio.sleep(sleep_delay)
        ticks.append(time.perf_counter())

    started_at = time.perf_counter()
    await asyncio.gather(coro, ticker())
    assert ticks
    return ticks[0] - started_at


def _common_kwargs() -> dict:
    return {
        "model": "fake-model",
        "normalized_mode": "normal",
        "route_info": {"route": "test"},
        "raw_message": "hola",
        "conversation": SimpleNamespace(id="conversation-1", updated_at=None),
        "current_empleado": SimpleNamespace(id="empleado-1", rol="admin"),
        "session": _FakeSession(),
        "tool_trace": [],
        "tool_defs": [],
        "max_tokens": 100,
        "retrieval_sources": [],
        "response_cache_enabled": False,
        "cache_key": "cache-key",
        "tournament_key_default": None,
        "bi_year": None,
        "bi_scope": None,
        "write_tools": set(),
        "run_read_tool": _run_read_tool,
        "ensure_citations": lambda text, _sources: text,
        "tool_trace_has_write_intent": lambda _trace: False,
        "assistant_response_cache_set": lambda **_kwargs: None,
        "pending_confirmation_cls": SimpleNamespace,
        "assistant_run_cls": _FakeRun,
        "assistant_message_cls": _FakeMessage,
        "message_response_cls": _FakeResponse,
    }


@pytest.mark.asyncio
async def test_anthropic_provider_call_does_not_block_event_loop():
    kwargs = _common_kwargs()
    kwargs.update(
        {
            "route_prompt": "route",
            "language_prompt": "language",
            "hermes_profile_prompt": None,
            "workspace_context": None,
            "module_key_default": None,
            "module_label_default": None,
            "module_context_default": None,
            "retrieval_context": None,
            "assistant_system_prompt": lambda: "system",
            "history_messages": _history_messages,
            "get_anthropic_client": lambda: SimpleNamespace(
                messages=_SlowAnthropicMessages()
            ),
            "tool_defs_anthropic": lambda tools: tools,
            "anthropic_text_from_blocks": lambda _blocks: "ok",
            "anthropic_message_from_blocks": lambda _blocks: [],
        }
    )

    delay = await _measure_event_loop_delay(execute_anthropic_provider(**kwargs))

    assert delay < 0.08


@pytest.mark.asyncio
async def test_openai_provider_call_does_not_block_event_loop():
    kwargs = _common_kwargs()
    kwargs.update(
        {
            "messages": [{"role": "user", "content": "hola"}],
            "openai_api_key": None,
            "get_openai_client": lambda _api_key: SimpleNamespace(
                chat=SimpleNamespace(
                    completions=_SlowOpenAICompletions(),
                )
            ),
        }
    )

    delay = await _measure_event_loop_delay(execute_openai_provider(**kwargs))

    assert delay < 0.08
