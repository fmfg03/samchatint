import asyncio
import time
from types import SimpleNamespace

import pytest

import samchat.assistant.router as assistant_router


class _SlowAnthropicMessages:
    def create(self, **_kwargs):
        time.sleep(0.2)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])


class _SlowOpenAICompletions:
    def create(self, **_kwargs):
        time.sleep(0.2)
        message = SimpleNamespace(content="ok")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


async def _measure_event_loop_delay(coro, *, sleep_delay: float = 0.02) -> float:
    ticks = []

    async def ticker():
        await asyncio.sleep(sleep_delay)
        ticks.append(time.perf_counter())

    started_at = time.perf_counter()
    await asyncio.gather(coro, ticker())
    assert ticks
    return ticks[0] - started_at


def _patch_provider_selection(monkeypatch, provider: str) -> None:
    monkeypatch.setattr(
        assistant_router,
        "_assistant_provider_order",
        lambda *_args, **_kwargs: [provider],
    )
    monkeypatch.setattr(
        assistant_router,
        "_assistant_model",
        lambda *_args, **_kwargs: "fake-model",
    )


@pytest.mark.asyncio
async def test_assistant_text_response_anthropic_does_not_block_event_loop(monkeypatch):
    _patch_provider_selection(monkeypatch, "anthropic")
    monkeypatch.setattr(
        assistant_router,
        "_get_anthropic_client",
        lambda: SimpleNamespace(messages=_SlowAnthropicMessages()),
    )

    delay = await _measure_event_loop_delay(
        assistant_router._assistant_text_response(
            prompt_user="hola",
            history_messages=[],
            mode=None,
            route_info={"route": "test"},
            openai_api_key=None,
            max_tokens=100,
            system_prompts=["system"],
        )
    )

    assert delay < 0.08


@pytest.mark.asyncio
async def test_assistant_text_response_openai_does_not_block_event_loop(monkeypatch):
    _patch_provider_selection(monkeypatch, "openai")
    monkeypatch.setattr(
        assistant_router,
        "_get_openai_client",
        lambda _api_key=None: SimpleNamespace(
            chat=SimpleNamespace(completions=_SlowOpenAICompletions())
        ),
    )

    delay = await _measure_event_loop_delay(
        assistant_router._assistant_text_response(
            prompt_user="hola",
            history_messages=[],
            mode=None,
            route_info={"route": "test"},
            openai_api_key=None,
            max_tokens=100,
            system_prompts=["system"],
        )
    )

    assert delay < 0.08
