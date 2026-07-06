from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import samchat.assistant.router as assistant_router


class _SlowRAGStore:
    def __init__(self, *, delay: float = 0.2):
        self.delay = delay

    def search(self, **_kwargs):
        time.sleep(self.delay)
        return []

    def ingest(self, **_kwargs):
        time.sleep(self.delay)
        return {"indexed_files": 0, "indexed_chunks": 0}


async def _measure_event_loop_delay(coro, *, sleep_delay: float = 0.02) -> float:
    ticks = []

    async def ticker():
        await asyncio.sleep(sleep_delay)
        ticks.append(time.perf_counter())

    started_at = time.perf_counter()
    await asyncio.gather(coro, ticker())
    assert ticks
    return ticks[0] - started_at


def _admin_empleado():
    return SimpleNamespace(id="empleado-1", rol="superadmin")


@pytest.mark.asyncio
async def test_rag_search_does_not_block_event_loop(monkeypatch):
    monkeypatch.setattr(assistant_router, "get_rag_store", lambda: _SlowRAGStore())

    delay = await _measure_event_loop_delay(
        assistant_router.rag_search(
            payload=assistant_router.RAGSearchRequest(query="estado de gastos"),
            current_empleado=_admin_empleado(),
        )
    )

    assert delay < 0.08


@pytest.mark.asyncio
async def test_hybrid_retrieval_does_not_block_event_loop_on_rag_search(monkeypatch):
    monkeypatch.setattr(assistant_router, "get_rag_store", lambda: _SlowRAGStore())
    monkeypatch.setattr(
        assistant_router,
        "_retrieve_sql_snippets",
        lambda **_kwargs: asyncio.sleep(0, result=[]),
    )
    monkeypatch.setattr(
        assistant_router,
        "_retrieve_memory_snippets",
        lambda **_kwargs: asyncio.sleep(0, result=[]),
    )

    delay = await _measure_event_loop_delay(
        assistant_router._build_hybrid_retrieval(
            session=SimpleNamespace(),
            query="estado de gastos",
            empleado_id=None,
        )
    )

    assert delay < 0.08


@pytest.mark.asyncio
async def test_rag_ingest_does_not_block_event_loop(monkeypatch):
    monkeypatch.setattr(assistant_router, "get_rag_store", lambda: _SlowRAGStore())

    delay = await _measure_event_loop_delay(
        assistant_router.rag_ingest(
            payload=assistant_router.RAGIngestRequest(paths=["docs"], max_files=1),
            current_empleado=_admin_empleado(),
        )
    )

    assert delay < 0.08
