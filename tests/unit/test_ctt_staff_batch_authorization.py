"""Typed batch proofs never impersonate individual review assertions."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.staff_batch_authorization import (
    CONSENT_SCHEMA,
    MEMBER_SCHEMA,
    VERIFICATION_SCHEMA,
    BatchAuthorizationDenied,
    BatchDecisionVerifier,
    BatchReviewScope,
    BatchSourcePage,
    batch_scope_hash,
    canonical_batch_scope_bytes,
    parse_batch_scope,
    sign_batch_proof,
    verify_batch_proof,
)
from devnous.copa_telmex.staff_batch_contracts import canonical
from devnous.copa_telmex.staff_signed_assertion import verify_assertion


@pytest.fixture
def signer():
    key = Ed25519PrivateKey.generate()
    return key, {
        "fixture": key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    }


def child():
    return dict(
        schema=MEMBER_SCHEMA,
        kid="fixture",
        **{
            name: str(uuid4())
            for name in (
                "authorization_id",
                "consent_id",
                "member_id",
                "attempt_id",
                "operation_id",
                "principal_id",
                "worker_id",
                "nonce",
            )
        },
        **{
            name: "a" * 64
            for name in (
                "scope_hash",
                "root_hash",
                "qualification_hash",
                "verification_hash",
            )
        },
        authorization_generation=1,
        lease_epoch=1,
        dispatch_generation=1,
        issued_at=1000,
        expires_at=1300,
    )


def test_child_roundtrip_and_no_individual_counter_fields(signer):
    key, keys = signer
    body = child()
    raw = sign_batch_proof(body, signing_key=key)
    proof = BatchDecisionVerifier(keys, clock=lambda: 1100).verify(raw)
    assert str(proof.attempt_id) == body["attempt_id"]
    assert not hasattr(proof, "credential_id")
    assert not hasattr(proof, "prior_counter")
    with pytest.raises(Exception):
        verify_assertion(raw, public_keys=keys)


@pytest.mark.parametrize(
    "change",
    [
        {"expires_at": 1301},
        {"expires_at": 1000},
        {"lease_epoch": True},
        {"authorization_generation": 0},
        {"dispatch_generation": 0},
        {"counter_before": 0},
        {"credential_id": "ZmFrZQ=="},
        {"scope_hash": "sha256:" + "a" * 64},
        {"nonce": str(uuid4()).upper()},
    ],
)
def test_invalid_child_shapes_rejected(signer, change):
    body = child()
    body.update(change)
    with pytest.raises(BatchAuthorizationDenied):
        sign_batch_proof(body, signing_key=signer[0])


@pytest.mark.parametrize("now", [999, 1300, 1301])
def test_child_time_boundary(signer, now):
    raw = sign_batch_proof(child(), signing_key=signer[0])
    with pytest.raises(BatchAuthorizationDenied):
        verify_batch_proof(raw, schema=MEMBER_SCHEMA, public_keys=signer[1], now=now)


def test_wrong_signer_and_domain_and_tampering(signer):
    raw = sign_batch_proof(child(), signing_key=signer[0])
    other = Ed25519PrivateKey.generate()
    wrong = {
        "fixture": other.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    }
    for data, schema, keys in [
        (raw, MEMBER_SCHEMA, wrong),
        (raw, CONSENT_SCHEMA, signer[1]),
        (raw, VERIFICATION_SCHEMA, signer[1]),
    ]:
        with pytest.raises(BatchAuthorizationDenied):
            verify_batch_proof(data, schema=schema, public_keys=keys, now=1100)
    obj = json.loads(raw)
    obj["body"]["member_id"] = str(uuid4())
    with pytest.raises(BatchAuthorizationDenied):
        verify_batch_proof(
            canonical(obj), schema=MEMBER_SCHEMA, public_keys=signer[1], now=1100
        )


def scope():
    return BatchReviewScope(
        tenant_id="sample",
        tournament_slug="sample",
        **{
            name: uuid4()
            for name in (
                "edition_id",
                "session_id",
                "draft_id",
                "team_id",
                "actor_id",
                "operation_id",
                "run_id",
                "member_id",
            )
        },
        draft_version=1,
        draft_content_hash="a" * 64,
        manifest_sha256="b" * 64,
        document_id="c" * 64,
        source_pages=(BatchSourcePage(1, "d" * 64, 4, uuid4(), "e" * 64, 612, 792),),
        actor_role="coordinador",
        authority_binding="hmac-sha256:" + "f" * 64,
        staff_and_changes_binding="hmac-sha256:" + "f" * 64,
        field_evidence_binding="hmac-sha256:" + "f" * 64,
        policy_hash="1" * 64,
    )


def test_scope_roundtrip_binds_each_original_pdf_and_geometry():
    value = scope()
    raw = canonical_batch_scope_bytes(value)
    assert parse_batch_scope(raw) == value
    changed = replace(
        value, source_pages=(replace(value.source_pages[0], pdf_sha256="2" * 64),)
    )
    assert batch_scope_hash(value) != batch_scope_hash(changed)
    assert not hasattr(value, "pdf_sha256")
    with pytest.raises(BatchAuthorizationDenied):
        parse_batch_scope(raw + b" ")


@pytest.mark.parametrize(
    "field,value",
    [
        ("width", True),
        ("height", 0),
        ("pdf_page", 0),
        ("pdf_sha256", "sha256:" + "d" * 64),
    ],
)
def test_invalid_source_shapes(field, value):
    page = scope().source_pages[0]
    with pytest.raises(BatchAuthorizationDenied):
        replace(page, **{field: value})


def test_finality_dispatch_preserves_individual_service_and_rejects_unknown_scope():
    from test_ctt_staff_capture_source_check import sample

    from devnous.copa_telmex.staff_batch_authorization import BatchAwareFinalityService

    class Service:
        def __init__(self):
            self.calls = []

        def finalize_rows(self, **kwargs):
            self.calls.append(kwargs["scope"])
            return "rows"

        def authorize_finality_effect(self, **kwargs):
            self.calls.append(kwargs["scope"])
            return b"proof"

    individual, batch = Service(), Service()
    routed = BatchAwareFinalityService(individual=individual, batch=batch)
    individual_scope = sample()[1]
    assert routed.finalize_rows(scope=individual_scope) == "rows"
    assert routed.authorize_finality_effect(scope=scope()) == b"proof"
    assert len(individual.calls) == len(batch.calls) == 1
    with pytest.raises(BatchAuthorizationDenied):
        routed.finalize_rows(scope=object())


@pytest.mark.parametrize("source_valid", [False, True])
def test_jit_child_is_never_issued_before_source_integrity(source_valid):
    import asyncio
    from contextlib import contextmanager
    from types import SimpleNamespace
    from devnous.copa_telmex.staff_batch_authorization import BatchCaptureAdapter
    from devnous.copa_telmex.staff_capture_executor import CaptureDenied

    events = []
    value = scope()

    class Store:
        async def private_capture_context(self, attempt):
            events.append("private_inputs")
            return dict(
                scope_bytes=canonical_batch_scope_bytes(value),
                review_bytes=b"{}",
                evidence_bytes=b"{}",
                parent_proof_bytes=b"{}",
                verification_proof_bytes=b"{}",
                root_bytes=b"{}",
            )

    class Source:
        @contextmanager
        def open_verified_snapshot(self, raw):
            events.append("source_open")

            def verify():
                events.append("source_verified")
                if not source_valid:
                    raise ValueError("source changed")

            yield SimpleNamespace(verify_unchanged=verify)

    async def issue():
        assert events == ["private_inputs", "source_open", "source_verified"]
        events.append("issue_child")
        # Deliberately invalid: this test checks ordering and must never create
        # an accepted authority proof or reach materialization.
        return b"invalid-proof"

    adapter = BatchCaptureAdapter(
        store=Store(),
        gate=object(),
        source_adapter=Source(),
        capture_client=object(),
        public_keys={},
        issuer_public_keys={},
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
    )
    with pytest.raises(CaptureDenied):
        asyncio.run(
            adapter.prepare_and_capture(
                {"attempt_id": str(uuid4())}, proof_factory=issue
            )
        )
    assert ("issue_child" in events) is source_valid
