from pathlib import Path
from types import SimpleNamespace

import pytest

from devnous.copa_telmex import staff_batch_public_runtime as runtime
from devnous.copa_telmex.staff_review_operations_channel import (
    StaffReviewClientSettings,
)


def test_disabled_transport_reads_no_key_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled transport touched a resource")

    monkeypatch.setattr(runtime, "_read_key", forbidden)
    monkeypatch.setattr(runtime.httpx, "AsyncClient", forbidden)
    app = SimpleNamespace(state=SimpleNamespace())
    assert (
        runtime.configure_batch_review_client(app, StaffReviewClientSettings()) is None
    )
    assert app.state.ctt_batch_review_client is None


@pytest.mark.asyncio
async def test_dedicated_transport_closes_without_touching_individual_client(
    monkeypatch,
):
    monkeypatch.setattr(runtime, "_read_key", lambda path: b"synthetic-only" * 3)
    individual = object()
    app = SimpleNamespace(state=SimpleNamespace(ctt_staff_review_client=individual))
    client = runtime.configure_batch_review_client(
        app,
        StaffReviewClientSettings(
            enabled=True,
            base_url="http://127.0.0.1:9999",
            key_path=Path("/synthetic/key"),
        ),
    )
    transport = app.state.ctt_batch_review_transport
    assert client is not None
    await runtime.close_batch_review_client(app)
    assert transport.is_closed
    assert app.state.ctt_batch_review_client is None
    assert app.state.ctt_staff_review_client is individual


@pytest.mark.parametrize(
    "url", ["https://example.com", "http://10.0.0.1:80", "http://127.0.0.1:80/path"]
)
def test_invalid_remote_or_path_transport_never_opens_connection(monkeypatch, url):
    monkeypatch.setattr(runtime, "_read_key", lambda path: b"synthetic-only" * 3)
    app = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(Exception):
        runtime.configure_batch_review_client(
            app,
            StaffReviewClientSettings(
                enabled=True, base_url=url, key_path=Path("/synthetic/key")
            ),
        )
    assert app.state.ctt_batch_review_client is None
