"""Real V3 HTTP/MAC/Ed25519 transport; protected SQL read is an explicit double."""

import asyncio
import copy
import json
import time
from contextlib import nullcontext

import httpx
import pytest
from deployment.ctt_protected_review import finality_channel as rpc
from fastapi import FastAPI
from test_finality_channel import TestNonces
from test_staff_capture_finality_v2 import final as _final
from test_staff_capture_gate_v2 import pub
from test_staff_capture_gate_v3 import batch as _batch

from devnous.copa_telmex.staff_batch_authorization import canonical_batch_scope_bytes
from devnous.copa_telmex.staff_batch_runtime import BatchPolicyFinalityService
from devnous.copa_telmex.staff_batch_store import PostgresBatchReviewStore
from devnous.copa_telmex.staff_capture_effect import freeze_capture_batch_effect
from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_finality_effect import (
    BatchFinalityContext,
    canonical,
    freeze_finality_effect,
    pending_rows,
)

batch = _batch
final = _final


@pytest.fixture
def ctx(tmp_path):
    from test_staff_capture_gate_v2 import Context

    root = tmp_path / "individual"
    root.mkdir()
    value = Context(root)
    yield value
    value.journal.close()
    value.snapshot.pdf.close()


KEY = b"z" * 32


@pytest.fixture
def wire(batch, monkeypatch):
    gate, args, c, child, *_ = batch
    preauth = gate.preauthorize(
        **args, evidence_bytes=c.evidence_bytes, snapshot=c.batch_snapshot
    )
    plan = json.loads(args["review_bytes"])
    team, players, staff = (
        copy.deepcopy(plan[name]) for name in ("team", "players", "staff")
    )
    for row in players + [s["row"] for s in staff if s["row"] is not None]:
        row.update(preauth["authority_metadata"][row["id"]])
    for row in [team] + players:
        row.update(c.generated[row["id"]])
    pending = freeze_capture_batch_effect(
        scope=args["scope"],
        team=team,
        players=players,
        staff=staff,
        player_source_refs=[
            f"page:{p['page_index']}:jugador_{p['source_slot']}"
            for p in plan["player_sources"]
        ],
    )
    context = BatchFinalityContext(
        "isolated-database",
        16384,
        497,
        args["attempt_id"],
        int(time.time()),
        args["expires_at"],
        __import__("uuid").uuid4(),
        c.batch_snapshot,
        0,
        True,
    )
    request = dict(
        protected_context=context,
        scope=args["scope"],
        review_attempt_id=args["attempt_id"],
        review_bytes=args["review_bytes"],
        pending_effect_bytes=pending,
        signed_review=args["signed_review"],
        preauthorization=preauth,
    )
    protected = dict(
        database_identity=context.database_identity,
        database_oid=context.database_oid,
        scope_bytes=canonical_batch_scope_bytes(args["scope"]),
        review_bytes=args["review_bytes"],
        pending_effect_bytes=pending,
        signed_review=args["signed_review"],
        preauthorization_bytes=canonical(preauth),
        evidence_bytes=c.evidence_bytes,
        validation_bytes=canonical(
            {
                "incident_policy": {
                    "player_results": [
                        {
                            "player_index": r["roster_index"],
                            "eligibility_status": "CLEARED",
                            "incidents": [],
                        }
                        for r in players
                    ]
                }
            }
        ),
        source_context=dict(
            tenant_id=args["scope"].tenant_id,
            edition_id=str(args["scope"].edition_id),
            roster_slug=args["scope"].tournament_slug,
        ),
    )
    store = PostgresBatchReviewStore(object(), backend_pin="fixture")
    calls = []

    async def load(attempt):
        calls.append(attempt)
        return copy.deepcopy(protected)

    monkeypatch.setattr(store, "load_finality_gate_context", load)
    server = rpc.BatchFinalityGateRPCServer(
        gate=BatchPolicyFinalityService({args["scope"].policy_hash: gate}),
        store=store,
        private_source_root="/tmp/isolated-v3-rpc",
        database_identity=context.database_identity,
        max_source_bytes=1000000,
        max_asset_bytes=1000000,
        max_manifest_bytes=1000000,
    )
    monkeypatch.setattr(server, "_snapshot", lambda *a: nullcontext(c.batch_snapshot))
    app = rpc.mount_finality_rpc(
        FastAPI(),
        service=server,
        channel_key=KEY,
        operations_channel_key=b"o" * 32,
        consume_nonce=TestNonces(),
    )

    def bridge(req):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app)
            ) as client:
                response = await client.request(
                    req.method, str(req.url), headers=req.headers, content=req.content
                )
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=response.content,
                    request=req,
                )

        return asyncio.run(send())

    http = httpx.Client(transport=httpx.MockTransport(bridge), trust_env=False)
    client = rpc.BatchFinalityRPCClient(
        base_url="http://127.0.0.1:8023",
        channel_key=KEY,
        http_client=http,
        effect_key=b"e" * 32,
        review_key=b"r" * 32,
        gate_public_keys={"gate": pub(c.signer)},
        receipt_public_keys={"bus": pub(c.bus)},
        assertion_public_keys={"issuer": pub(c.issuer)},
    )
    yield dict(
        client=client,
        app=app,
        request=request,
        context=context,
        args=args,
        c=c,
        protected=protected,
        calls=calls,
    )
    client.close()


def test_real_http_pending_to_separate_batch_finality(wire):
    from datetime import datetime, timezone

    req = wire["request"]
    client = wire["client"]
    observed = pending_rows(json.loads(req["pending_effect_bytes"]))
    bundle = client.finalize_rows(**req, observed_pending_rows=observed)
    active = copy.deepcopy(observed)
    for record in bundle.records:
        row = (
            active["players"][0]
            if record["kind"] == "PLAYER"
            else active["staff"][record["slot"] - 1]["row"]
        )
        updated = datetime.fromtimestamp(wire["context"].issued_at, timezone.utc)
        row.update(
            governance_state="ACTIVE",
            finality_receipt_id=record["receipt"]["receipt_id"],
            updated_at=(
                updated.replace(tzinfo=None) if record["kind"] == "PLAYER" else updated
            ).isoformat(),
        )
        if record["kind"] == "PLAYER":
            row.update(
                postcommit_revision=1, postcommit_snapshot_hash="sha256:" + "f" * 64
            )
    effect = freeze_finality_effect(
        scope=req["scope"],
        protected_context=wire["context"],
        pending_effect_bytes=req["pending_effect_bytes"],
        observed_pending_rows=observed,
        verified_finalities=bundle,
        active_projection_db_derived=active,
        effect_key=b"e" * 32,
        preauthorization=req["preauthorization"],
        receipt_public_keys={"bus": pub(wire["c"].bus)},
    )
    proof = client.authorize_finality_effect(
        **req, finality_effect_bytes=effect, verified_finalities=bundle
    )
    assert (
        json.loads(proof)["body"]["schema"] == "ctt.capture.finality-authorization.v3"
    )
    assert len(wire["calls"]) == 2
    assert not hasattr(client, "_signer")


def test_individual_and_batch_wire_contexts_are_not_interchangeable(wire, final):
    raw = rpc.encode(
        "finalize_rows",
        _batch=True,
        **wire["request"],
        observed_pending_rows_bytes=canonical(
            pending_rows(json.loads(wire["request"]["pending_effect_bytes"]))
        ),
    )
    with pytest.raises(CaptureDenied):
        rpc.decode("finalize_rows", raw)
    with pytest.raises(CaptureDenied):
        rpc.encode(
            "finalize_rows", **wire["request"], observed_pending_rows_bytes=b"{}"
        )
    with pytest.raises(CaptureDenied):
        rpc.encode(
            "finalize_rows",
            _batch=True,
            **final["args"],
            observed_pending_rows_bytes=canonical(final["observed"]),
        )


@pytest.mark.parametrize("fault", ["wrong_domain", "authority_flag", "source_change"])
def test_bad_mac_forged_flags_or_stale_private_input_denied(wire, fault):
    observed = pending_rows(json.loads(wire["request"]["pending_effect_bytes"]))
    if fault == "source_change":
        wire["protected"]["review_bytes"] = b"{}"
        with pytest.raises(CaptureDenied):
            wire["client"].finalize_rows(
                **wire["request"], observed_pending_rows=observed
            )
        return
    raw = rpc.encode(
        "finalize_rows",
        _batch=True,
        **wire["request"],
        observed_pending_rows_bytes=canonical(observed),
    )
    if fault == "authority_flag":
        value = json.loads(raw)
        value["context"]["cas_succeeded"] = True
        raw = canonical(value)
    nonce = __import__("uuid").uuid4()
    stamp = int(time.time())
    headers = {
        "content-type": "application/json",
        rpc.NONCE: str(nonce),
        rpc.TIME: str(stamp),
        rpc.MAC: rpc.request_mac(
            KEY, "finalize_rows", nonce, stamp, raw, _batch=fault != "wrong_domain"
        ),
    }

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=wire["app"])
        ) as client:
            return await client.post(
                "http://127.0.0.1" + rpc.BATCH_PREFIX + "finalize_rows",
                content=raw,
                headers=headers,
            )

    assert asyncio.run(send()).status_code == 503
    assert wire["calls"] == []
