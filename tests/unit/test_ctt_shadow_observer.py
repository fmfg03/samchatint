from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from devnous.tournaments.core.ctt_canary import CttCanaryMode
from devnous.tournaments.instances.copa_telmex import (
    ctt_shadow_observer as observer_module,
)
from devnous.tournaments.instances.copa_telmex.ctt_shadow_observer import (
    MAX_PAGE_BYTES,
    MAX_PENDING_CHATS,
    CttRegistrationShadowObserver,
)


class _FakeReport:
    def model_dump_json(self) -> str:
        return json.dumps({"accepted": True, "no_database_write": True})


def _jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 900), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_two_pages_finalize_in_background_and_clear_memory(tmp_path) -> None:
    observer = CttRegistrationShadowObserver(
        enabled=True,
        api_key="test-key",
        layout_path=tmp_path / "layout.json",
    )
    seen = []

    async def fake_execute(payloads):
        seen.append(tuple(payloads))
        return _FakeReport()

    observer._execute = fake_execute

    assert await observer.capture_page(99, b"front") is True
    assert await observer.capture_page(99, b"back") is True
    assert observer.pending_chat_count == 1
    assert await observer.finalize(99) is True
    assert observer.pending_chat_count == 0
    assert observer.buffered_bytes == 0

    await observer.drain()

    assert seen == [(b"front", b"back")]


@pytest.mark.asyncio
async def test_one_page_is_discarded_without_provider_work(tmp_path) -> None:
    observer = CttRegistrationShadowObserver(
        enabled=True,
        api_key="test-key",
        layout_path=tmp_path / "layout.json",
    )
    called = False

    async def fake_execute(_payloads):
        nonlocal called
        called = True
        return _FakeReport()

    observer._execute = fake_execute

    await observer.capture_page(99, b"front")
    assert await observer.finalize(99) is False
    await observer.drain()

    assert called is False
    assert observer.pending_chat_count == 0


@pytest.mark.asyncio
async def test_third_page_auto_finalizes_exact_document(tmp_path) -> None:
    observer = CttRegistrationShadowObserver(
        enabled=True,
        api_key="test-key",
        layout_path=tmp_path / "layout.json",
    )
    seen = []

    async def fake_execute(payloads):
        seen.append(tuple(payloads))
        return _FakeReport()

    observer._execute = fake_execute

    await observer.capture_page(99, b"page-1")
    await observer.capture_page(99, b"page-2")
    await observer.capture_page(99, b"page-3")
    await observer.drain()

    assert seen == [(b"page-1", b"page-2", b"page-3")]
    assert observer.pending_chat_count == 0


@pytest.mark.asyncio
async def test_disabled_and_oversize_pages_never_enter_memory(tmp_path) -> None:
    disabled = CttRegistrationShadowObserver(enabled=False)
    enabled = CttRegistrationShadowObserver(
        enabled=True,
        api_key="test-key",
        layout_path=tmp_path / "layout.json",
    )

    assert await disabled.capture_page(99, b"front") is False
    assert await enabled.capture_page(99, b"x" * (MAX_PAGE_BYTES + 1)) is False
    assert disabled.pending_chat_count == 0
    assert enabled.pending_chat_count == 0


def test_environment_is_off_by_default_and_active_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("CTT_RESPONSES_ROLLOUT", raising=False)
    assert CttRegistrationShadowObserver.from_environment().enabled is False

    monkeypatch.setenv("CTT_RESPONSES_ROLLOUT", "active")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert CttRegistrationShadowObserver.from_environment().enabled is False


def test_shadow_environment_requires_key_and_layout(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CTT_RESPONSES_ROLLOUT", "shadow")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert CttRegistrationShadowObserver.from_environment().enabled is False

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CTT_LAYOUT_PATH", str(tmp_path / "missing.json"))
    assert CttRegistrationShadowObserver.from_environment().enabled is False


def test_valid_shadow_environment_enables_bounded_policy(monkeypatch, tmp_path) -> None:
    layout_path = tmp_path / "layout.json"
    layout_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CTT_RESPONSES_ROLLOUT", "shadow")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CTT_LAYOUT_PATH", str(layout_path))
    monkeypatch.setenv("CTT_SHADOW_MINIMUM_PLAYERS", "30")
    monkeypatch.setenv("CTT_RESPONSES_MODEL", "test-model")

    observer = CttRegistrationShadowObserver.from_environment()

    assert observer.enabled is True
    assert observer.minimum_players == 25
    assert observer.model == "test-model"


@pytest.mark.asyncio
async def test_pending_chat_boundary_is_fail_closed(tmp_path) -> None:
    observer = CttRegistrationShadowObserver(
        enabled=True,
        api_key="test-key",
        layout_path=tmp_path / "layout.json",
    )

    for chat_id in range(MAX_PENDING_CHATS):
        assert await observer.capture_page(chat_id, b"front") is True

    assert await observer.capture_page(MAX_PENDING_CHATS, b"front") is False
    assert observer.pending_chat_count == MAX_PENDING_CHATS
    await observer.close()
    assert observer.pending_chat_count == 0


@pytest.mark.asyncio
async def test_global_byte_boundary_rejects_excess_payload(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(observer_module, "MAX_TOTAL_BUFFER_BYTES", 5)
    observer = CttRegistrationShadowObserver(
        enabled=True,
        api_key="test-key",
        layout_path=tmp_path / "layout.json",
    )

    assert await observer.capture_page(1, b"1234") is True
    assert await observer.capture_page(2, b"12") is False
    assert observer.buffered_bytes == 4


@pytest.mark.asyncio
async def test_stale_single_page_is_expired_before_next_capture(
    monkeypatch,
    tmp_path,
) -> None:
    now = 1000.0
    monkeypatch.setattr(observer_module.time, "monotonic", lambda: now)
    observer = CttRegistrationShadowObserver(
        enabled=True,
        api_key="test-key",
        layout_path=tmp_path / "layout.json",
    )
    await observer.capture_page(1, b"stale")

    now += observer_module.MAX_BUFFER_AGE_SECONDS + 1
    assert await observer.capture_page(2, b"fresh") is True

    assert observer.pending_chat_count == 1
    assert observer.buffered_bytes == len(b"fresh")


@pytest.mark.asyncio
async def test_execute_uses_ephemeral_cache_and_closes_provider(
    monkeypatch,
    tmp_path,
) -> None:
    layout_path = tmp_path / "layout.json"
    layout_path.write_text("{}", encoding="utf-8")
    observer = CttRegistrationShadowObserver(
        enabled=True,
        api_key="test-key",
        layout_path=layout_path,
        model="test-model",
    )
    cache_roots: list[Path] = []

    class FakeClient:
        closed = False

        async def close(self):
            self.closed = True

    extractor = SimpleNamespace(client=FakeClient(), model="test-model")
    monkeypatch.setattr(
        observer_module.CttResponsesExtractor,
        "from_api_key",
        lambda api_key, *, model: extractor,
    )

    def fake_cache(root):
        root = Path(root)
        cache_roots.append(root)
        assert root.is_dir()
        return ("cache", root)

    monkeypatch.setattr(observer_module, "CttDraftCache", fake_cache)
    monkeypatch.setattr(
        observer_module,
        "CttCachedResponsesExtractor",
        lambda base, cache, attempts: (base, cache, attempts),
    )

    class FakeRunner:
        def __init__(self, cached, *, mode, policy):
            assert cached[2] == 1
            assert mode == CttCanaryMode.SHADOW
            assert policy.minimum_players == 16

        async def run(self, images, layout, *, document_sha256):
            assert len(images) == 2
            assert layout == {}
            assert len(document_sha256) == 64
            assert cache_roots[0].is_dir()
            return SimpleNamespace(report=_FakeReport())

    monkeypatch.setattr(observer_module, "CttCanaryRunner", FakeRunner)

    report = await observer._execute((_jpeg_bytes(), _jpeg_bytes()))

    assert isinstance(report, _FakeReport)
    assert extractor.client.closed is True
    assert cache_roots and not cache_roots[0].exists()
