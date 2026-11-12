"""Machine provenance and transport boundaries never create human authority."""

import base64
import hashlib
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.staff_batch_contracts import digest
from devnous.copa_telmex.staff_batch_operations_channel import (
    BatchOperationsUnavailable,
    decode_operation,
    encode_operation,
    operation_request_mac,
    operation_response_mac,
)
from devnous.copa_telmex.staff_batch_store import BatchDenied
from devnous.copa_telmex.staff_batch_verification import (
    BatchPrivateVerifier,
    normalize_observation,
)


def test_native_date_normalization_does_not_repair_identity():
    assert normalize_observation("  María  López ", field="name") == "María López"
    assert normalize_observation("01/12/2000", field="birth_date") == "2000-12-01"
    with pytest.raises(BatchDenied):
        normalize_observation("12/1/2000", field="birth_date")
    with pytest.raises(BatchDenied):
        normalize_observation("31/02/2000", field="birth_date")


@pytest.mark.parametrize(
    "extra", ["approved", "principal_id", "verified_by_human", "policy_path"]
)
def test_transport_rejects_caller_authority(extra):
    with pytest.raises(BatchOperationsUnavailable):
        decode_operation(
            "catalog",
            b'{"account_id":"'
            + str(uuid4()).encode()
            + b'","'
            + extra.encode()
            + b'":true}',
        )


def test_mac_binds_operation_body_response_and_nonce():
    account, run, nonce = uuid4(), uuid4(), uuid4()
    body = encode_operation("view", account_id=account, run_id=run, offset=0, limit=50)
    assert decode_operation("view", body)["run_id"] == run
    key = b"x" * 32
    mac = operation_request_mac(key, "view", nonce, 123, body)
    assert mac != operation_request_mac(key, "view", nonce, 123, body + b" ")
    assert mac != operation_request_mac(key, "member", nonce, 123, body)
    assert operation_response_mac(
        key, "view", nonce, body, 200, b"{}"
    ) != operation_response_mac(key, "view", nonce, body, 503, b"{}")


@pytest.mark.asyncio
async def test_private_attestation_requires_independent_agreeing_readings():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )

    class Store:
        async def verifier_context(self, *args):
            return {
                "public_key": base64.b64encode(public).decode(),
                "not_after_seconds": 2000,
            }

    pages = [{"pdf_sha256": "a" * 64, "pdf_page": 1}]
    evidence = {
        "expected_fields": ["name"],
        "proposed_fields": {"name": "María"},
        "required_fields": ["name"],
        "source_binding_hash": digest(pages),
        "readings": [
            {"method": "first", "input_hash": "1" * 64, "fields": {"name": "María"}},
            {"method": "second", "input_hash": "2" * 64, "fields": {"name": "María"}},
        ],
    }

    class Reader:
        async def verification_input(self, member):
            return evidence

    verifier = BatchPrivateVerifier(
        source_reader=Reader(),
        store=Store(),
        signing_key=key,
        kid="test",
        profile_hash="b" * 64,
        clock=lambda: 1000,
    )
    member = {
        "status": "ready",
        "document_id": "c" * 64,
        "manifest_sha256": "d" * 64,
        "policy_hash": "e" * 64,
        "scope_hash": "f" * 64,
        "payload_hash": "0" * 64,
        "source_pages": pages,
    }
    run = {"profile_hash": "b" * 64, "members": [member]}
    proofs = await verifier.attest_members(run, {"qualification": "candidate"})
    raw = base64.b64decode(proofs[0]["proof_base64"])
    assert hashlib.sha256(raw).hexdigest() == proofs[0]["proof_hash"]
    assert proofs[0]["body"]["verified_by_human"] is False
    assert proofs[0]["body"]["human_edits_hash"] == digest([])
    evidence["readings"][1]["fields"]["name"] = "Mario"
    with pytest.raises(BatchDenied):
        await verifier.attest_members(run, {"qualification": "candidate"})
    evidence["readings"][1]["fields"]["name"] = "María"
    evidence["readings"][1]["input_hash"] = "1" * 64
    with pytest.raises(BatchDenied):
        await verifier.attest_members(run, {"qualification": "candidate"})


@pytest.mark.asyncio
async def test_private_channel_authenticates_response_and_rejects_replay():
    import time

    import httpx

    from devnous.copa_telmex.staff_batch_operations_channel import (
        PREFIX,
        BatchOperationsClient,
        create_batch_operations_app,
    )
    from devnous.copa_telmex.staff_batch_workflow import BatchReviewWorkflowService
    from devnous.copa_telmex.staff_capture_transport_store import (
        PostgresTransportNonces,
    )
    from devnous.copa_telmex.staff_review_channel import (
        MAC_HEADER,
        NONCE_HEADER,
        TIME_HEADER,
    )

    class Nonces(PostgresTransportNonces):
        def __init__(self):
            self.used = set()

        async def __call__(self, nonce, expires):
            if nonce in self.used:
                return False
            self.used.add(nonce)
            return True

    class Store:
        async def catalog(self, account):
            return {"runs": [], "source_manifests": []}

    class Provider:
        def prepare_run(self, *args):
            pass

        def analyze(self, *args):
            pass

        def qualify(self, *args):
            pass

        def source_page(self, *args):
            pass

    class Verifier:
        def validate_channel_key(self, key):
            pass

    service = BatchReviewWorkflowService(
        store=Store(), source_provider=Provider(), verifier=Verifier()
    )
    key = b"x" * 32
    app = create_batch_operations_app(
        service=service, channel_key=key, consume_nonce=Nonces()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), trust_env=False
    ) as http:
        client = BatchOperationsClient(
            base_url="http://127.0.0.1:12345", channel_key=key, http_client=http
        )
        assert await client.catalog(uuid4()) == {"runs": [], "source_manifests": []}
        body = encode_operation("catalog", account_id=uuid4())
        nonce, now = uuid4(), int(time.time())
        headers = {
            "content-type": "application/json",
            NONCE_HEADER: str(nonce),
            TIME_HEADER: str(now),
            MAC_HEADER: operation_request_mac(key, "catalog", nonce, now, body),
        }
        first = await http.post(
            "http://127.0.0.1:12345" + PREFIX + "catalog", content=body, headers=headers
        )
        second = await http.post(
            "http://127.0.0.1:12345" + PREFIX + "catalog", content=body, headers=headers
        )
        assert first.status_code == 200
        assert second.status_code == 503
    calls = []

    async def lost(request):
        calls.append(request)
        raise httpx.ReadTimeout("uncertain")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lost), trust_env=False
    ) as http:
        client = BatchOperationsClient(
            base_url="http://127.0.0.1:12345", channel_key=key, http_client=http
        )
        with pytest.raises(BatchOperationsUnavailable):
            await client.catalog(uuid4())
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_pending_human_date_proposal_retains_raw_error_and_machine_origin():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )

    class Store:
        async def verifier_context(self, *args):
            return {
                "public_key": base64.b64encode(public).decode(),
                "not_after_seconds": 2000,
            }

    pages = [{"pdf_sha256": "a" * 64, "pdf_page": 1}]
    field = "players.1.birth_date"
    evidence = {
        "expected_fields": [field],
        "proposed_fields": {field: "2000-12-01"},
        "required_fields": [field],
        "source_binding_hash": digest(pages),
        "readings": [
            {
                "method": "first",
                "input_hash": "1" * 64,
                "fields": {field: "not a date"},
            },
            {"method": "second", "input_hash": "2" * 64, "fields": {field: None}},
        ],
    }

    class Reader:
        async def verification_input(self, member):
            return evidence

    principal = str(uuid4())
    edits = [
        {
            "correction_id": str(uuid4()),
            "principal_id": principal,
            "expected_version": 2,
            "edits": [{"field_id": field, "value": "01/12/2000"}],
            "source_pages": pages,
        }
    ]
    member = {
        "status": "ready",
        "document_id": "c" * 64,
        "manifest_sha256": "d" * 64,
        "policy_hash": "e" * 64,
        "scope_hash": "f" * 64,
        "payload_hash": "0" * 64,
        "source_pages": pages,
        "human_edits": edits,
    }
    verifier = BatchPrivateVerifier(
        source_reader=Reader(),
        store=Store(),
        signing_key=key,
        kid="test",
        profile_hash="b" * 64,
        clock=lambda: 1000,
    )
    run = {"profile_hash": "b" * 64, "principal_id": principal, "members": [member]}
    proof = (await verifier.attest_members(run, {"qualification": "candidate"}))[0]
    assert proof["body"]["human_edits_hash"] == digest(edits)
    assert proof["body"]["verified_by_human"] is False
    assert evidence["readings"][0]["fields"][field] == "not a date"
    edits[0]["principal_id"] = str(uuid4())
    with pytest.raises(BatchDenied):
        await verifier.attest_members(run, {"qualification": "candidate"})
