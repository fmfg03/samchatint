"""Real HTTP/MAC/Ed25519 RPC protocol with explicit synthetic issuer results.

Genuine UV and SQL retention are exercised by the independent PostgreSQL/RPC
acceptance cohort. These endpoint tests never claim reviewer accreditation.
"""

import asyncio
import base64
import time
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

from devnous.copa_telmex import staff_batch_issuer_channel as rpc
from devnous.copa_telmex.staff_batch_authorization import (
    CONSENT_SCHEMA,
    MEMBER_SCHEMA,
    sign_batch_proof,
)
from devnous.copa_telmex.staff_batch_contracts import canonical
from devnous.copa_telmex.staff_capture_transport_store import PostgresTransportNonces


class Nonces(PostgresTransportNonces):
    def __init__(self):
        self.used = set()

    async def __call__(self, nonce, expiry):
        if nonce in self.used:
            return False
        self.used.add(nonce)
        return True


@pytest.fixture
def channel(monkeypatch):
    from staff_batch_issuer_v3 import PostgresBatchAssertionIssuer

    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    issuer = PostgresBatchAssertionIssuer(
        object(),
        signing_kid="fixture",
        signing_key=key,
        verifier_public_keys={"verifier": b"v" * 32},
    )
    calls = []

    async def consent(challenge, assertion):
        calls.append(("consent", challenge, assertion))
        now = int(time.time())
        body = dict(
            schema=CONSENT_SCHEMA,
            kid="fixture",
            consent_id=str(uuid4()),
            challenge_id=str(challenge),
            principal_id=str(uuid4()),
            credential_id=base64.b64encode(b"synthetic-credential").decode(),
            root_hash="a" * 64,
            qualification_hash="b" * 64,
            capability_version=1,
            credential_version=1,
            principal_version=1,
            account_version=1,
            service_version=1,
            counter_before=4,
            counter_after=5,
            auth_profile="device-counter-v1",
            backup_eligible=False,
            backup_state=False,
            auth_policy_hash=None,
            issued_at=now,
            expires_at=now + 600,
        )
        return sign_batch_proof(body, signing_key=key)

    async def child(consent_id, member, worker, epoch):
        calls.append(("child", consent_id, member, worker, epoch))
        now = int(time.time())
        body = dict(
            schema=MEMBER_SCHEMA,
            kid="fixture",
            authorization_id=str(uuid4()),
            consent_id=str(consent_id),
            member_id=str(member),
            attempt_id=str(uuid4()),
            operation_id=str(uuid4()),
            principal_id=str(uuid4()),
            scope_hash="a" * 64,
            root_hash="b" * 64,
            qualification_hash="c" * 64,
            verification_hash="d" * 64,
            authorization_generation=1,
            worker_id=str(worker),
            lease_epoch=epoch,
            dispatch_generation=1,
            nonce=str(uuid4()),
            issued_at=now,
            expires_at=now + 180,
        )
        return sign_batch_proof(body, signing_key=key)

    monkeypatch.setattr(issuer, "issue_consent", consent)
    monkeypatch.setattr(issuer, "issue_child", child)
    nonces = Nonces()
    app = rpc.mount_batch_issuer_rpc(
        FastAPI(), issuer=issuer, channel_key=b"c" * 32, consume_nonce=nonces
    )
    return app, {"fixture": pub}, calls, issuer


def test_real_http_typed_consent_and_child(channel):
    app, pins, calls, *_ = channel

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), trust_env=False
        ) as http:
            client = rpc.BatchIssuerClient(
                url="http://127.0.0.1:8022",
                channel_key=b"c" * 32,
                http_client=http,
                issuer_public_keys=pins,
            )
            assert not hasattr(client, "_pool") and not hasattr(client, "_signing_key")
            parent = await client.issue_consent(uuid4(), b'{"synthetic":true}')
            child = await client.issue_child(uuid4(), uuid4(), uuid4(), 2)
            assert (
                b"ctt.batch-consent.v1" in parent
                and b"ctt.batch.member-authorization.v1" in child
            )

    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize(
    "fault", ["mac", "operation", "extra", "epoch", "old_schema", "nonce_replay"]
)
def test_bad_or_mixed_requests_do_not_reach_issuer(channel, fault):
    app, pins, calls, *_ = channel
    operation = "child"
    stamp = int(time.time())
    nonce = uuid4()
    body = dict(
        schema="ctt.batch.issuer-request.v1",
        operation=operation,
        consent_id=str(uuid4()),
        member_id=str(uuid4()),
        worker_id=str(uuid4()),
        lease_epoch=1,
    )
    if fault == "operation":
        body["operation"] = "consent"
    elif fault == "extra":
        body["verified_by_human"] = True
    elif fault == "epoch":
        body["lease_epoch"] = True
    elif fault == "old_schema":
        body["schema"] = "ctt.review-assertion.v2"
    raw = canonical(body)
    headers = {
        "content-type": "application/json",
        rpc.TIME: str(stamp),
        rpc.NONCE: str(nonce),
        rpc.MAC: rpc.request_mac(b"c" * 32, operation, nonce, stamp, raw),
    }
    if fault == "mac":
        headers[rpc.MAC] = "0" * 64

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), trust_env=False
        ) as http:
            result = await http.post(
                "http://127.0.0.1" + rpc.PREFIX + operation,
                content=raw,
                headers=headers,
            )
            if fault == "nonce_replay":
                assert result.status_code == 200
                result = await http.post(
                    "http://127.0.0.1" + rpc.PREFIX + operation,
                    content=raw,
                    headers=headers,
                )
            assert result.status_code == 503

    asyncio.run(run())
    assert len(calls) == (1 if fault == "nonce_replay" else 0)


def test_wrong_issuer_pin_rejects_authenticated_response(channel):
    app, pins, calls, *_ = channel

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), trust_env=False
        ) as http:
            client = rpc.BatchIssuerClient(
                url="http://127.0.0.1:8022",
                channel_key=b"c" * 32,
                http_client=http,
                issuer_public_keys={"fixture": b"x" * 32},
            )
            with pytest.raises(rpc.BatchIssuerUnavailable):
                await client.issue_consent(uuid4(), b"{}")

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["proof_kind", "request_binding"])
def test_authentic_response_cannot_mix_proof_kind_or_request(
    channel, monkeypatch, fault
):
    app, pins, calls, issuer = channel
    original = issuer.issue_consent

    async def wrong(challenge, assertion):
        if fault == "proof_kind":
            return await issuer.issue_child(uuid4(), uuid4(), uuid4(), 1)
        return await original(uuid4(), assertion)

    monkeypatch.setattr(issuer, "issue_consent", wrong)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), trust_env=False
        ) as http:
            client = rpc.BatchIssuerClient(
                url="http://127.0.0.1:8022",
                channel_key=b"c" * 32,
                http_client=http,
                issuer_public_keys=pins,
            )
            with pytest.raises(rpc.BatchIssuerUnavailable):
                await client.issue_consent(uuid4(), b"{}")

    asyncio.run(run())
