"""Actual v2 event validation, Ed25519 receipts and durable journal via ASGI.

Only transport nonces use a test set. It conveys no business authority; the
journal independently validates the event and the signature with its pinned key.
No verified-flag callback substitutes for a receipt, source or capture proof.
"""

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import time
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import pytest

from deployment.ctt_protected_review import evidence_channel as rpc
from durable_event_store import DurableJSONLEventStore, verify_stored_receipt
from v2_fixtures import finality_event, roster_event

KEY = b"channel-domain-only-key-32-bytes!!"


class Nonces:
    """Transport replay test double; durable SQL is verified in its own suite."""

    def __init__(self):
        self.seen = set()

    async def __call__(self, nonce, expires):
        assert type(expires) is int
        if nonce in self.seen:
            return False
        self.seen.add(nonce)
        return True


def public(key):
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


@pytest.fixture
def case(tmp_path):
    signing_key = Ed25519PrivateKey.generate()
    keys = {"bus-test": public(signing_key)}
    journal = DurableJSONLEventStore(
        str(tmp_path / "events.jsonl"), v2_public_keys=keys
    )
    nonces = Nonces()
    app = rpc.create_evidence_app(
        journal=journal,
        signing_key=signing_key,
        signing_kid="bus-test",
        channel_key=KEY,
        consume_nonce=nonces,
    )
    yield SimpleNamespace(
        app=app,
        journal=journal,
        keys=keys,
        key=signing_key,
        nonces=nonces,
        path=tmp_path / "events.jsonl",
    )
    journal.close()


def headers(raw, *, nonce=None, timestamp=None):
    nonce = nonce or uuid4()
    timestamp = timestamp or int(time.time())
    return {
        "Content-Type": "application/json",
        rpc.NONCE_HEADER: str(nonce),
        rpc.TIME_HEADER: str(timestamp),
        rpc.MAC_HEADER: rpc.request_mac(KEY, nonce, timestamp, raw),
    }


async def send(case, raw, supplied=None, *, path=rpc.PATH):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=case.app), base_url="http://127.0.0.1:8024"
    ) as http:
        return await http.post(path, content=raw, headers=supplied or headers(raw))


@contextmanager
def publisher(case, mutate=None):
    """Sync client to the actual ASGI app on a dedicated bridge thread."""
    with ThreadPoolExecutor(max_workers=1) as executor:

        def bridge(request):
            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=case.app)
                ) as client:
                    result = await client.request(
                        request.method,
                        str(request.url),
                        headers=request.headers.multi_items(),
                        content=request.content,
                    )
                    return httpx.Response(
                        result.status_code,
                        headers=result.headers.multi_items(),
                        content=result.content,
                        request=request,
                    )

            response = executor.submit(asyncio.run, run()).result(timeout=10)
            return mutate(request, response) if mutate else response

        with httpx.Client(
            transport=httpx.MockTransport(bridge), trust_env=False
        ) as http:
            yield rpc.EvidencePublisher(
                base_url="http://127.0.0.1:8024", channel_key=KEY, http_client=http
            )


@pytest.mark.parametrize("factory", [roster_event, finality_event])
def test_actual_v2_event_signed_receipt_and_restart_idempotency(case, factory):
    event = factory()
    with publisher(case) as publish:
        first = publish(event)
        assert verify_stored_receipt(first, case.keys)
        assert first["alg"] == "Ed25519" and first["event_type"] == event["event_type"]
        second = publish(copy.deepcopy(event))
    assert first == second
    assert len(case.journal) == 1 and len(case.nonces.seen) == 2
    case.journal.close()
    case.journal = DurableJSONLEventStore(str(case.path), v2_public_keys=case.keys)
    case.app = rpc.create_evidence_app(
        journal=case.journal,
        signing_key=case.key,
        signing_kid="bus-test",
        channel_key=KEY,
        consume_nonce=case.nonces,
    )
    with publisher(case) as publish:
        assert publish(event) == first
    assert len(case.journal) == 1
    stored = json.loads(case.path.read_text().splitlines()[0])
    assert stored["entry"]["idempotent_v2"]["semantic_event"] == event


@pytest.mark.asyncio
async def test_health_and_request_replay_have_no_duplicate_publication(case):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=case.app), base_url="http://127.0.0.1:8024"
    ) as client:
        health = await client.get("/healthz")
        assert health.json() == {"status": "ok", "role": "ctt-evidence-v2"}
    raw = rpc._canonical(roster_event())
    signed = headers(raw)
    first = await send(case, raw, signed)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.headers[rpc.RESPONSE_HEADER] == rpc.response_mac(
        KEY, rpc.UUID(signed[rpc.NONCE_HEADER]), raw, 200, first.content
    )
    second = await send(case, raw, signed)
    assert second.status_code == 403 and len(case.journal) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack",
    [
        "mac",
        "body",
        "path",
        "time",
        "future",
        "time_format",
        "nonce_format",
        "content_type",
        "encoding",
        "invalid_json",
        "duplicate_json",
        "oversize",
        "event_schema",
        "event_operation",
        "empty",
        "duplicate_mac",
        "duplicate_time",
        "duplicate_nonce",
    ],
)
async def test_untrusted_request_never_gets_a_receipt(case, attack):
    raw = rpc._canonical(roster_event())
    signed = headers(raw)
    path = rpc.PATH
    if attack == "mac":
        signed[rpc.MAC_HEADER] = "0" * 64
    elif attack == "body":
        raw = raw.replace(b"synthetic", b"different")
    elif attack == "path":
        path += "/other"
    elif attack in {"time", "future"}:
        signed = headers(
            raw, timestamp=int(time.time()) + (60 if attack == "future" else -60)
        )
    elif attack == "time_format":
        signed[rpc.TIME_HEADER] = "0" + signed[rpc.TIME_HEADER]
    elif attack == "nonce_format":
        signed = headers(raw, nonce=rpc.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
        signed[rpc.NONCE_HEADER] = signed[rpc.NONCE_HEADER].upper()
    elif attack == "content_type":
        signed["Content-Type"] = "text/plain"
    elif attack == "encoding":
        signed["Content-Encoding"] = "gzip"
    elif attack == "invalid_json":
        raw = b'{"invalid":'
        signed = headers(raw)
    elif attack == "duplicate_json":
        raw = raw[:-1] + b',"event_type":"duplicate"}'
        signed = headers(raw)
    elif attack == "oversize":
        raw = b"x" * (rpc.MAX_BYTES + 1)
        signed = headers(raw)
    elif attack == "event_schema":
        event = roster_event()
        event["draft_version"] = True
        raw = rpc._canonical(event)
        signed = headers(raw)
    elif attack == "event_operation":
        event = roster_event()
        event["operation_id"] = "sha256:" + "0" * 64
        raw = rpc._canonical(event)
        signed = headers(raw)
    elif attack == "empty":
        raw = b""
        signed = headers(raw)
    elif attack.startswith("duplicate_"):
        header = {
            "duplicate_mac": rpc.MAC_HEADER,
            "duplicate_time": rpc.TIME_HEADER,
            "duplicate_nonce": rpc.NONCE_HEADER,
        }[attack]
        signed = list(signed.items()) + [(header, dict(signed)[header])]
    response = await send(case, raw, signed, path=path)
    assert response.status_code == (
        503 if attack == "event_operation" else 404 if attack == "path" else 403
    )
    assert len(case.journal) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["content-type", "content-encoding"])
async def test_duplicate_semantic_headers_are_rejected(case, header):
    raw = rpc._canonical(roster_event())
    signed = list(headers(raw).items())
    if header == "content-encoding":
        signed.append((header, "identity"))
    signed.append(
        (header, "application/json" if header == "content-type" else "identity")
    )
    response = await send(case, raw, signed)
    assert response.status_code == 403
    assert len(case.journal) == 0


def test_lost_response_reuses_identical_durable_receipt_on_explicit_retry(case):
    event = roster_event()
    lost: list[dict] = []

    def drop(request, response):
        if not lost:
            lost.append(response.json()["receipt"])
            assert len(case.journal) == 1
            raise httpx.ReadError("synthetic reply lost after fsync", request=request)
        return response

    with publisher(case, drop) as publish:
        with pytest.raises(rpc.EvidenceTransportUncertain):
            publish(event)
        recovered = publish(event)
    assert recovered == lost[0]
    assert verify_stored_receipt(recovered, case.keys)
    assert len(case.journal) == 1


def test_ambiguous_transport_loss_reaches_gate_retry_exception_contract(case):
    def drop(request, response):
        raise httpx.ReadError("synthetic reply lost", request=request)

    with publisher(case, drop) as publish:
        with pytest.raises((TimeoutError, ConnectionError, OSError)):
            publish(finality_event())
    assert len(case.journal) == 1


@pytest.mark.parametrize(
    "attack",
    [
        "mac",
        "body",
        "status",
        "duplicate_mac",
        "encoding",
        "oversize",
        "shape",
        "receipt_type",
        "wrong_nonce",
        "wrong_request",
        "signed_error",
    ],
)
def test_client_denies_corrupted_or_unbound_response(case, attack):
    def alter(request, response):
        raw = response.content
        status = response.status_code
        result_headers = httpx.Headers(response.headers)
        if attack == "mac":
            result_headers[rpc.RESPONSE_HEADER] = "0" * 64
        elif attack == "body":
            raw += b" "
        elif attack == "status":
            status = 503
        elif attack == "duplicate_mac":
            result_headers = httpx.Headers(
                list(response.headers.multi_items())
                + [(rpc.RESPONSE_HEADER, response.headers[rpc.RESPONSE_HEADER])]
            )
        elif attack == "encoding":
            result_headers["content-encoding"] = "gzip-unknown"
        elif attack == "oversize":
            raw = b"x" * (rpc.MAX_BYTES + 1)
        elif attack in {"shape", "receipt_type", "signed_error"}:
            payload: dict[str, object]
            if attack == "shape":
                payload = {"unexpected": True}
            elif attack == "receipt_type":
                payload = {"receipt": []}
            else:
                payload = {"error": "EVIDENCE_UNAVAILABLE"}
            raw = rpc._canonical(payload)
            status = 503 if attack == "signed_error" else 200
            result_headers[rpc.RESPONSE_HEADER] = rpc.response_mac(
                KEY,
                rpc.UUID(request.headers[rpc.NONCE_HEADER]),
                request.content,
                status,
                raw,
            )
        elif attack == "wrong_nonce":
            result_headers[rpc.RESPONSE_HEADER] = rpc.response_mac(
                KEY, uuid4(), request.content, status, raw
            )
        elif attack == "wrong_request":
            result_headers[rpc.RESPONSE_HEADER] = rpc.response_mac(
                KEY,
                rpc.UUID(request.headers[rpc.NONCE_HEADER]),
                b"other exact request",
                status,
                raw,
            )
        return httpx.Response(
            status, content=raw, headers=result_headers, request=request
        )

    with publisher(case, alter) as publish:
        with pytest.raises(rpc.EvidenceTransportUnavailable):
            publish(roster_event())
    assert len(case.journal) == 1


def test_actual_journal_signature_key_mismatch_produces_signed_failure(case):
    different = Ed25519PrivateKey.generate()
    case.app = rpc.create_evidence_app(
        journal=case.journal,
        signing_key=different,
        signing_kid="bus-test",
        channel_key=KEY,
        consume_nonce=case.nonces,
    )
    with publisher(case) as publish:
        with pytest.raises(rpc.EvidenceTransportUnavailable):
            publish(roster_event())
    assert len(case.journal) == 0


@pytest.mark.parametrize(
    "change", ["key", "signer", "kid", "nonce", "timeout", "same_key"]
)
def test_server_configuration_is_separate_and_bounded(case, change):
    options = dict(
        journal=case.journal,
        signing_key=case.key,
        signing_kid="bus-test",
        channel_key=KEY,
        consume_nonce=case.nonces,
    )
    if change == "key":
        options["channel_key"] = b"short"
    elif change == "signer":
        options["signing_key"] = object()
    elif change == "kid":
        options["signing_kid"] = ""
    elif change == "nonce":
        options["consume_nonce"] = None
    elif change == "timeout":
        options["timeout_seconds"] = True
    elif change == "same_key":
        options["channel_key"] = case.key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    with pytest.raises(rpc.EvidenceTransportUnavailable):
        rpc.create_evidence_app(**options)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8024",
        "http://remote.test:8024",
        "http://192.0.2.1:8024",
        "http://127.0.0.1",
        "http://127.0.0.1:8024/other",
        "http://127.0.0.1:8024/?x=1",
        "http://user:password@127.0.0.1:8024",
    ],
)
def test_client_requires_pinned_loopback_configuration(url):
    with httpx.Client(trust_env=False) as http:
        with pytest.raises(rpc.EvidenceTransportUnavailable):
            rpc.EvidencePublisher(base_url=url, channel_key=KEY, http_client=http)


def test_client_timeout_proxy_policy_and_local_size_bound(case):
    with httpx.Client(trust_env=True) as http:
        with pytest.raises(rpc.EvidenceTransportUnavailable):
            rpc.EvidencePublisher(
                base_url="http://127.0.0.1:8024", channel_key=KEY, http_client=http
            )
    with publisher(case) as publish:
        with pytest.raises(rpc.EvidenceTransportUnavailable, match="too large"):
            publish({"oversized": "x" * rpc.MAX_BYTES})
    assert len(case.journal) == 0


@pytest.mark.parametrize("field", ["key", "nonce", "timestamp"])
def test_request_mac_rejects_invalid_configuration(field):
    values = dict(key=KEY, nonce=uuid4(), timestamp=int(time.time()), body=b"{}")
    values[field] = {"key": b"short", "nonce": "not-uuid", "timestamp": True}[field]
    with pytest.raises(rpc.EvidenceTransportUnavailable):
        rpc.request_mac(**values)


def test_same_operation_altered_event_cannot_replace_journal_receipt(case):
    event = roster_event()
    with publisher(case) as publish:
        receipt = publish(event)
        changed = {**event, "timestamp": "2026-10-28T00:00:01Z"}
        with pytest.raises(rpc.EvidenceTransportUnavailable):
            publish(changed)
        assert publish(event) == receipt
    assert len(case.journal) == 1


@pytest.mark.asyncio
async def test_unclaimed_nonce_never_publishes(case):
    async def denied(nonce, expires):
        return False

    case.app = rpc.create_evidence_app(
        journal=case.journal,
        signing_key=case.key,
        signing_kid="bus-test",
        channel_key=KEY,
        consume_nonce=denied,
    )
    response = await send(case, rpc._canonical(roster_event()))
    assert response.status_code == 403 and len(case.journal) == 0


@pytest.mark.asyncio
async def test_nonce_timeout_has_no_journal_effect(case):
    async def stalled(nonce, expires):
        await asyncio.sleep(2)
        return True

    case.app = rpc.create_evidence_app(
        journal=case.journal,
        signing_key=case.key,
        signing_kid="bus-test",
        channel_key=KEY,
        consume_nonce=stalled,
        timeout_seconds=1,
    )
    response = await send(case, rpc._canonical(roster_event()))
    assert response.status_code == 403 and len(case.journal) == 0
