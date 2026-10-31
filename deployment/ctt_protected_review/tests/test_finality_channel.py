"""Real Ed25519 finality through transport; DB/source doubles are explicit.

These are protocol tests, not accreditation or production source admission.
"""

import asyncio
import base64
import copy
import time
from contextlib import nullcontext
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from deployment.ctt_protected_review import finality_channel as rpc
from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_finality_effect import (
    canonical,
    freeze_finality_effect,
    parse,
)
from devnous.copa_telmex.staff_capture_transport_store import PostgresTransportNonces
from devnous.copa_telmex.staff_review_proposal_store import PostgresReviewProposalStore
from test_staff_capture_finality_v2 import final as _final, ctx as _ctx
from test_staff_capture_gate_v2 import pub

KEY = b"z" * 32
OPS = b"o" * 32
final = _final
ctx = _ctx


class TestProtectedStore(PostgresReviewProposalStore):
    __test__ = False

    def __init__(self, value):
        self.value = value
        self.calls = 0

    async def load_finality_gate_context(self, attempt):
        self.calls += 1
        return copy.deepcopy(self.value)


class TestNonces(PostgresTransportNonces):
    __test__ = False

    def __init__(self):
        self.values = set()

    async def __call__(self, nonce, expires):
        if nonce in self.values:
            return False
        self.values.add(nonce)
        return True


def build(final, monkeypatch):
    case = final
    f = case["ctx"]
    args = case["args"]
    pending = parse(args["pending_effect_bytes"])
    protected = dict(
        database_identity=case["context"].database_identity,
        database_oid=case["context"].database_oid,
        scope_bytes=rpc.canonical_scope_bytes(f.scope),
        review_bytes=f.review,
        pending_effect_bytes=args["pending_effect_bytes"],
        signed_review=f.signed,
        preauthorization_bytes=canonical(args["preauthorization"]),
        evidence_bytes=b"{}",
        validation_bytes=canonical(
            {
                "incident_policy": {
                    "player_results": [
                        {
                            "player_index": r["roster_index"],
                            "eligibility_status": "CLEARED",
                            "incidents": [],
                        }
                        for r in pending["players"]
                    ]
                }
            }
        ),
        source_context=dict(
            tenant_id=f.scope.tenant_id,
            edition_id=str(f.scope.edition_id),
            roster_slug=f.scope.tournament_slug,
            tournament_id=str(uuid4()),
            edition_year=2026,
            credential_id_hex="abcd",
        ),
    )
    store = TestProtectedStore(protected)
    server = rpc.FinalityGateRPCServer(
        gate=f.gate,
        store=store,
        private_source_root="/tmp/isolated-rpc-fixture",
        database_identity=case["context"].database_identity,
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    monkeypatch.setattr(server, "_snapshot", lambda *args: nullcontext(f.snapshot))
    app = rpc.mount_finality_rpc(
        FastAPI(),
        service=server,
        channel_key=KEY,
        operations_channel_key=OPS,
        consume_nonce=TestNonces(),
    )

    def bridge(request):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app)
            ) as http:
                response = await http.request(
                    request.method,
                    str(request.url),
                    headers=request.headers,
                    content=request.content,
                )
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=response.content,
                    request=request,
                )

        return asyncio.run(send())

    http = httpx.Client(transport=httpx.MockTransport(bridge), trust_env=False)
    client = rpc.FinalityRPCClient(
        base_url="http://127.0.0.1:8023",
        channel_key=KEY,
        http_client=http,
        effect_key=b"e" * 32,
        review_key=b"r" * 32,
        gate_public_keys={"gate": pub(f.signer)},
        receipt_public_keys={"bus": pub(f.bus)},
        assertion_public_keys={"issuer": pub(f.issuer)},
    )
    return client, store, app, server


def test_real_signatures_bundles_and_capability_roundtrip(final, monkeypatch):
    client, store, _, _ = build(final, monkeypatch)
    bundle = client.finalize_rows(
        **final["args"], observed_pending_rows=final["observed"]
    )
    active = copy.deepcopy(final["active"])
    for record in bundle.records:
        row = (
            active["players"][0]
            if record["kind"] == "PLAYER"
            else active["staff"][record["slot"] - 1]["row"]
        )
        row["finality_receipt_id"] = record["receipt"]["receipt_id"]
    effect = freeze_finality_effect(
        **{
            **final["freeze"],
            "verified_finalities": bundle,
            "active_projection_db_derived": active,
        }
    )
    envelope = client.authorize_finality_effect(
        **final["args"],
        finality_effect_bytes=effect,
        verified_finalities=bundle,
        nonce=final["context"].nonce,
        issued_at=final["context"].issued_at,
        expires_at=final["context"].expires_at,
    )
    assert parse(envelope)["body"]["review_attempt_id"] == str(final["ctx"].attempt)
    assert store.calls == 2
    assert not hasattr(client, "_signer")
    client.close()
    assert client._http.is_closed


@pytest.mark.parametrize(
    "field",
    [
        "scope_bytes",
        "review_bytes",
        "pending_effect_bytes",
        "signed_review",
        "preauthorization_bytes",
    ],
)
def test_gate_uses_protected_bytes_not_writer_replacements(final, monkeypatch, field):
    client, store, _, _ = build(final, monkeypatch)
    store.value[field] = b"{}"
    with pytest.raises(CaptureDenied):
        client.finalize_rows(**final["args"], observed_pending_rows=final["observed"])


@pytest.mark.parametrize(
    "field", ["database_identity", "database_oid", "source_context", "validation_bytes"]
)
def test_protected_state_is_revalidated(final, monkeypatch, field):
    client, store, _, _ = build(final, monkeypatch)
    if field == "database_identity":
        store.value[field] = "another-database"
    if field == "database_oid":
        store.value[field] += 1
    if field == "source_context":
        store.value[field]["tenant_id"] = "other"
    if field == "validation_bytes":
        store.value[field] = canonical(
            {"blockers": [{}], "incident_policy": {"player_results": []}}
        )
    with pytest.raises(CaptureDenied):
        client.finalize_rows(**final["args"], observed_pending_rows=final["observed"])


@pytest.mark.parametrize(
    "attack",
    [
        "cas",
        "count",
        "oid_bool",
        "time_float",
        "nonce",
        "extra",
        "duplicate",
        "noncanonical",
        "scope",
        "oversize",
    ],
)
def test_writer_wire_has_no_authority_flags_and_preserves_types(final, attack):
    raw = rpc.encode(
        "finalize_rows",
        **final["args"],
        observed_pending_rows_bytes=canonical(final["observed"]),
    )
    value = rpc.wire_json(raw, rpc.MAX_WIRE_BYTES)
    if attack == "cas":
        value["context"]["cas_succeeded"] = True
    if attack == "count":
        value["context"]["blocking_incident_count"] = 0
    if attack == "oid_bool":
        value["context"]["database_oid"] = True
    if attack == "time_float":
        value["context"]["issued_at"] = float(value["context"]["issued_at"])
    if attack == "nonce":
        value["context"]["nonce"] = "bad"
    if attack == "extra":
        value["verified"] = True
    if attack == "scope":
        value["scope_bytes"] = base64.b64encode(b"{}").decode()
    raw = rpc.wire_canonical(value)
    if attack == "duplicate":
        raw = b'{"context":{},"context":{}}'
    if attack == "noncanonical":
        raw += b"\n"
    if attack == "oversize":
        raw = b"x" * (rpc.MAX_WIRE_BYTES + 1)
    with pytest.raises(CaptureDenied):
        rpc.decode("finalize_rows", raw)


@pytest.mark.parametrize(
    "attack", ["bad_mac", "ops_key", "replay", "expired", "extra_header"]
)
def test_request_authentication_precedes_store_access(final, monkeypatch, attack):
    client, store, app, _ = build(final, monkeypatch)
    raw = rpc.encode(
        "finalize_rows",
        **final["args"],
        observed_pending_rows_bytes=canonical(final["observed"]),
    )
    nonce, now = uuid4(), int(time.time())
    if attack == "expired":
        now -= 100
    key = OPS if attack == "ops_key" else KEY
    headers = {
        "Content-Type": "application/json",
        rpc.NONCE: str(nonce),
        rpc.TIME: str(now),
        rpc.MAC: rpc.request_mac(key, "finalize_rows", nonce, now, raw),
    }
    if attack == "bad_mac":
        headers[rpc.MAC] = "bad"
    if attack == "extra_header":
        headers = list(headers.items()) + [(rpc.MAC, "duplicate")]
    with client._http as http:
        if attack == "replay":
            assert (
                http.post(
                    client._url + rpc.PREFIX + "finalize_rows",
                    content=raw,
                    headers=headers,
                ).status_code
                == 200
            )
            store.calls = 0
        response = http.post(
            client._url + rpc.PREFIX + "finalize_rows", content=raw, headers=headers
        )
    assert response.status_code == 503
    assert store.calls == 0


@pytest.mark.parametrize("binding", ["nonce", "operation", "request", "status", "body"])
def test_response_cannot_cross_context_even_with_real_mac(final, monkeypatch, binding):
    client, _, _, _ = build(final, monkeypatch)

    def respond(request):
        nonce = rpc.identifier(request.headers[rpc.NONCE])
        operation = "finalize_rows"
        original = request.content
        body = rpc.wire_canonical(
            {"result": base64.b64encode(final["bundle"].payload_bytes).decode()}
        )
        status = 200
        if binding == "nonce":
            nonce = uuid4()
        if binding == "operation":
            operation = "authorize_finality_effect"
        if binding == "request":
            original = b"{}"
        if binding == "status":
            status = 503
        signature = rpc.response_mac(KEY, operation, nonce, original, status, body)
        if binding == "body":
            body = rpc.wire_canonical({"result": "e30="})
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "application/json", rpc.RESPONSE_MAC: signature},
        )

    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
    with pytest.raises(CaptureDenied):
        client.finalize_rows(**final["args"], observed_pending_rows=final["observed"])
    client.close()


@pytest.mark.parametrize("bad", [b"r" * 32, b"e" * 32, b"i" * 32, OPS])
def test_no_key_reuse_with_signer_or_public_operations(final, monkeypatch, bad):
    _, _, _, server = build(final, monkeypatch)
    with pytest.raises(CaptureDenied):
        rpc.mount_finality_rpc(
            FastAPI(),
            service=server,
            channel_key=bad,
            operations_channel_key=OPS,
            consume_nonce=TestNonces(),
        )


def test_gate_snapshot_opens_its_own_sealed_source_copy(final, monkeypatch, tmp_path):
    from dataclasses import replace
    from devnous.copa_telmex.batch_admission import validate_manifest

    _, store, _, server = build(final, monkeypatch)
    # Exercise the actual method, not the transport fixture's snapshot double.
    monkeypatch.delattr(server, "_snapshot")
    manifest = copy.deepcopy(final["ctx"].manifest)
    manifest["edition"]["roster_slug"] = final["ctx"].scope.tournament_slug
    scope = replace(
        final["ctx"].scope,
        manifest_sha256=validate_manifest(manifest)["manifest_sha256"],
    )
    root = tmp_path / "cas"
    root.mkdir(mode=0o700)
    for name in ("manifests", "pdfs", "pages"):
        (root / name).mkdir(mode=0o700)
    files = {
        root / "manifests" / (scope.manifest_sha256 + ".json"): canonical(manifest),
        root / "pdfs" / (scope.pdf_sha256 + ".pdf"): final["ctx"].pdf,
    }
    for page, raw in zip(scope.source_pages, final["ctx"].images):
        files[root / "pages" / (page.image_sha256[7:] + ".png")] = raw
    for path, raw in files.items():
        path.write_bytes(raw)
        path.chmod(0o400)
    server._root = root
    source = store.value["source_context"]
    source.update(
        tournament_id=manifest["edition"]["tournament_id"],
        edition_year=manifest["edition"]["edition_year"],
    )
    with server._snapshot(scope, final["ctx"].attempt, source) as snapshot:
        snapshot.verify_unchanged()
        assert snapshot.scope_bytes == rpc.canonical_scope_bytes(scope)
    pdfpath = root / "pdfs" / (scope.pdf_sha256 + ".pdf")
    pdfpath.chmod(0o600)
    pdfpath.write_bytes(b"changed")
    with pytest.raises(CaptureDenied):
        with server._snapshot(scope, final["ctx"].attempt, source):
            pass


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8023",
        "http://example.org:8023",
        "http://127.0.0.1",
        "http://u:p@127.0.0.1:8023",
        "http://127.0.0.1:8023/path",
    ],
)
def test_client_rejects_untrusted_endpoint(final, monkeypatch, url):
    client, _, _, _ = build(final, monkeypatch)
    with pytest.raises(CaptureDenied):
        rpc.FinalityRPCClient(
            base_url=url,
            channel_key=KEY,
            http_client=client._http,
            effect_key=b"e" * 32,
            review_key=b"r" * 32,
            gate_public_keys={},
            receipt_public_keys={},
            assertion_public_keys={},
        )
    client.close()


@pytest.mark.parametrize("operation", ["finalize_rows", "authorize_finality_effect"])
def test_transport_mac_never_substitutes_for_bus_or_gate_signature(
    final, monkeypatch, operation
):
    client, _, _, _ = build(final, monkeypatch)

    def respond(request):
        nonce = rpc.identifier(request.headers[rpc.NONCE])
        if operation == "finalize_rows":
            document = parse(final["bundle"].payload_bytes)
            document["records"][0]["receipt"]["receipt_id"] = "forged"
        else:
            document = parse(final["envelope"])
            document["signature"] = base64.b64encode(b"x" * 64).decode()
        body = rpc.wire_canonical(
            {"result": base64.b64encode(canonical(document)).decode()}
        )
        return httpx.Response(
            200,
            content=body,
            headers={
                "Content-Type": "application/json",
                rpc.RESPONSE_MAC: rpc.response_mac(
                    KEY, operation, nonce, request.content, 200, body
                ),
            },
        )

    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
    with pytest.raises(CaptureDenied):
        if operation == "finalize_rows":
            client.finalize_rows(
                **final["args"], observed_pending_rows=final["observed"]
            )
        else:
            client.authorize_finality_effect(**final["signing"])
    client.close()
