"""Private proposal store framing, integrity and transaction boundaries."""

import base64
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from devnous.copa_telmex.draft_versioning import draft_content_hash
from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_review_proposal_store import (
    PostgresReviewProposalStore,
    ProposalAuthorityUnavailable,
    ProposalConflict,
    _canonical,
    _decode,
)


class FakeConnection:
    def __init__(self, value=None, error=None, nested=False):
        self.value, self.error, self.nested = value, error, nested
        self.committed = False

    def is_in_transaction(self):
        return self.nested

    @asynccontextmanager
    async def transaction(self):
        yield
        self.committed = True

    async def fetchval(self, query, *args):
        self.query, self.args = query, args
        if self.error:
            raise self.error
        return self.value


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pytest.mark.parametrize(
    "raw",
    [b'{"x":1,"x":2}', b'{ "x":1}', b'{"x":NaN}', b"[]", b"{}\n", None, b"\xff\xff"],
)
def test_noncanonical_bytes_denied(raw):
    with pytest.raises(CaptureDenied):
        _canonical(raw)


def test_decode_internal_multiline_base64():
    assert _decode(json.dumps({"review_bytes": "YQ==\n", "signed_review": None})) == {
        "review_bytes": b"a",
        "signed_review": None,
    }
    assert _decode(None) is None
    with pytest.raises(CaptureDenied):
        _decode([])
    with pytest.raises(CaptureDenied):
        _decode({"scope_bytes": 3})


@pytest.mark.asyncio
async def test_load_context_recomputes_raw_hash_and_returns_after_commit():
    raw = {"extraction": {"team": "exact"}}
    conn = FakeConnection(
        {
            "backend_pin": "test",
            "raw_snapshot": raw,
            "draft": {"content_hash": draft_content_hash(raw)},
            "existing_photo_records": [],
        }
    )
    store = PostgresReviewProposalStore(FakePool(conn), backend_pin="test")
    assert await store.load_context(uuid4(), uuid4()) == conn.value
    assert conn.committed is True
    conn.value["raw_snapshot"]["extraction"] = {"team": "forged"}
    with pytest.raises(CaptureDenied, match="raw draft hash"):
        await store.load_context(uuid4(), uuid4())
    conn.value["draft"]["content_hash"] = draft_content_hash(conn.value["raw_snapshot"])
    del conn.value["existing_photo_records"]
    with pytest.raises(CaptureDenied, match="photo context"):
        await store.load_context(uuid4(), uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["nested", "pin", "infrastructure", "conflict", "authority", "integrity"]
)
async def test_private_failures_do_not_become_enrollment(kind):
    class Failure(Exception):
        pass

    exc = Failure("private")
    exc.sqlstate = "40001" if kind == "conflict" else "42501"
    exc.message = (
        "CTT review authority unavailable"
        if kind == "authority"
        else "CTT review source assets unavailable"
    )
    conn = FakeConnection(
        {"backend_pin": "wrong"},
        error=(
            exc
            if kind in {"infrastructure", "conflict", "authority", "integrity"}
            else None
        ),
        nested=kind == "nested",
    )
    if kind == "infrastructure":
        exc.sqlstate = "08006"
    expected = (
        ProposalConflict
        if kind == "conflict"
        else ProposalAuthorityUnavailable if kind == "authority" else CaptureDenied
    )
    store = PostgresReviewProposalStore(FakePool(conn), backend_pin="expected")
    with pytest.raises(expected) as caught:
        await store.load_context(uuid4(), uuid4())
    if kind in {"integrity", "infrastructure"}:
        assert type(caught.value) is CaptureDenied
    assert not conn.committed


@pytest.mark.asyncio
async def test_fixed_private_api_frames_and_proof_recovery():
    ids = [uuid4() for _ in range(3)]
    conn = FakeConnection({"signed_review": base64.b64encode(b"proof").decode()})
    store = PostgresReviewProposalStore(FakePool(conn))
    assert await store.load_signed_proof(*ids) == b"proof"
    conn.value = {"signed_review": None}
    with pytest.raises(CaptureDenied):
        await store.load_signed_proof(*ids)
    conn.value = {}
    await store.resolve_source_scope(ids[0])
    assert "resolve_review_source_scope" in conn.query
    await store.cancel(*ids)
    assert "cancel_review_proposal" in conn.query
    await store.project_capture_rows(*ids, {}, [])
    assert "project_review_capture_rows" in conn.query
    await store.register_payload(*ids, b"{}", b"{}", uuid4(), b"{}", b"{}", b"{}")
    assert "register_proposal_capture_payload" in conn.query
    await store.create_challenge(*ids, b"x" * 32, 1, 2)
    assert "create_proposal_challenge" in conn.query
    with pytest.raises(CaptureDenied):
        await store.create_challenge(*ids, b"x" * 32, True, 2)
    conn.value = None
    assert await store.read_proposal(*ids) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    ["source", "register", "challenge", "projection", "status", "gate_context"],
)
async def test_required_database_responses_cannot_be_missing(monkeypatch, operation):
    from types import SimpleNamespace
    import devnous.copa_telmex.staff_review_proposal_store as module

    ids = [uuid4() for _ in range(3)]
    store = PostgresReviewProposalStore(FakePool(FakeConnection(None)))
    monkeypatch.setattr(
        module,
        "parse_canonical_scope",
        lambda _: SimpleNamespace(draft_content_hash=draft_content_hash({})),
    )
    with pytest.raises(CaptureDenied, match="required response"):
        if operation == "source":
            await store.resolve_source_scope(ids[0])
        elif operation == "register":
            await store.register_proposal(
                ids[0],
                ids[1],
                None,
                ids[2],
                b"{}",
                b"{}",
                b"{}",
                b"{}",
                b"{}",
                b"{}",
                uuid4(),
            )
        elif operation == "challenge":
            await store.create_challenge(*ids, b"x" * 32, 1, 2)
        elif operation == "projection":
            await store.project_capture_rows(*ids, {}, [])
        elif operation == "gate_context":
            await store.load_finality_gate_context(ids[2])
        else:
            await store.status(*ids)
