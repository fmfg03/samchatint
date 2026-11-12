"""Actual private composition stays dormant and rejects incomplete authority."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.staff_batch_contracts import canonical, digest
from devnous.copa_telmex.staff_batch_issuer_channel import BatchIssuerClient
from devnous.copa_telmex.staff_batch_runtime import assemble_batch_review_runtime
from devnous.copa_telmex.staff_capture_transport import CaptureUnixClient


class DormantPool:
    def acquire(self):
        pytest.fail("assembly must not access a database")


class DormantProducer:
    async def analyze(self, *args, **kwargs):
        pytest.fail("assembly must not call a model or reserve budget")


def public(key):
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def configuration(tmp_path):
    from staff_capture_gate_v3 import StaffCaptureGateV3

    gate_key, issuer_key, verifier_key, bus_key = [
        Ed25519PrivateKey.generate() for _ in range(4)
    ]
    policy = dict(
        workflow_id="samchat_registration_identity_v2",
        policy_id="samchat_registration_identity_policy_v2",
        policy_version="2.0.0",
        effective_sal="SAL-3",
        field_order=["full_name", "birth_date", "curp"],
        minimum_effective_pixels=12000,
        allowed_roles=["coordinador"],
        minimum_roster_size=16,
        maximum_roster_size=35,
        required_staff_slots=[1],
    )
    policy["policy_hash"] = (
        "sha256:" + __import__("hashlib").sha256(canonical(policy)).hexdigest()
    )

    def publish(event):
        pytest.fail("assembly must not publish evidence")

    gate = StaffCaptureGateV3(
        identity_key=b"i" * 32,
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
        signing_kid="gate",
        signing_key=gate_key,
        assertion_public_keys={"issuer": public(issuer_key)},
        verifier_public_keys={"verifier": public(verifier_key)},
        receipt_public_keys={"bus": public(bus_key)},
        policy_artifact=policy,
        expected_policy_hash=policy["policy_hash"],
        publisher=publish,
        max_page_bytes=1000000,
    )
    profile = {"schema": "ctt.batch.test-profile.v1", "name": "retained-fixture"}
    return dict(
        enabled=True,
        capture_enabled=True,
        retained_analysis_only=True,
        workflow_pool=DormantPool(),
        verifier_pool=DormantPool(),
        worker_pool=DormantPool(),
        nonce_pool=DormantPool(),
        backend_pin="isolated-batch",
        private_source_root=tmp_path / "source",
        private_photo_root=tmp_path / "photos",
        private_analysis_root=tmp_path / "analysis",
        profiles={digest(profile): profile},
        profile_hash=digest(profile),
        business_gates={policy["policy_hash"][7:]: gate},
        verifier_kid="verifier",
        verifier_signing_key=verifier_key,
        issuer_client=BatchIssuerClient(
            url="http://127.0.0.1:8022",
            channel_key=b"u" * 32,
            http_client=SimpleNamespace(trust_env=False),
            issuer_public_keys={"issuer": public(issuer_key)},
        ),
        issuer_public_keys={"issuer": public(issuer_key)},
        channel_key=b"c" * 32,
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
        gate_public_keys={"gate": public(gate_key)},
        capture_client=CaptureUnixClient(
            path=tmp_path / "capture.sock", key=b"t" * 32, server_uid=os.getuid()
        ),
    )


def test_disabled_factory_needs_no_keys_pools_or_runtime_dependency():
    assert assemble_batch_review_runtime() is None
    with pytest.raises(ValueError):
        assemble_batch_review_runtime(capture_enabled=True)


def test_real_private_chain_assembles_without_io_or_starting_tasks(tmp_path):
    args = configuration(tmp_path)
    runtime = assemble_batch_review_runtime(**args)
    assert runtime.service.capture_enabled is True
    assert runtime.service.store.pool is args["workflow_pool"]
    assert runtime.service.verifier._store.pool is args["verifier_pool"]
    assert runtime.service.issuer is args["issuer_client"]
    assert not hasattr(runtime.service.issuer, "_pool")
    assert not hasattr(runtime.service.issuer, "_signing_key")
    assert runtime.service.worker_store.pool is args["worker_pool"]
    assert runtime.service._tasks == []
    assert list(tmp_path.iterdir()) == []


def test_analysis_producer_injection_is_dormant_at_assembly(tmp_path):
    args = configuration(tmp_path)
    producer = DormantProducer()
    args.update(analysis_producer=producer, retained_analysis_only=False)
    runtime = assemble_batch_review_runtime(**args)
    assert runtime.service.sources.analysis_producer is producer


@pytest.mark.parametrize(
    "fault",
    [
        "same_pool",
        "no_issuer",
        "no_worker",
        "no_source",
        "no_producer",
        "profile",
        "gate_pin",
        "verifier_pin",
        "issuer_pin",
        "channel",
        "capture_client",
        "disabled_capture_dependencies",
        "bool",
    ],
)
def test_partial_or_ambiguous_configuration_denied(tmp_path, fault):
    args = configuration(tmp_path)
    if fault == "same_pool":
        args["verifier_pool"] = args["workflow_pool"]
    elif fault == "no_issuer":
        args["issuer_client"] = None
    elif fault == "no_worker":
        args["worker_pool"] = None
    elif fault == "no_source":
        args["private_source_root"] = Path("relative")
    elif fault == "no_producer":
        args["retained_analysis_only"] = False
    elif fault == "profile":
        args["profile_hash"] = "f" * 64
    elif fault == "gate_pin":
        args["gate_public_keys"] = {"gate": b"x" * 32}
    elif fault == "verifier_pin":
        args["verifier_signing_key"] = Ed25519PrivateKey.generate()
    elif fault == "issuer_pin":
        args["issuer_public_keys"] = {"issuer": b"x" * 32}
    elif fault == "channel":
        args["channel_key"] = args["review_key"]
    elif fault == "capture_client":
        args["capture_client"] = object()
    elif fault == "disabled_capture_dependencies":
        args["capture_enabled"] = False
    else:
        args["enabled"] = 1
    with pytest.raises(ValueError):
        assemble_batch_review_runtime(**args)


def test_workflow_factory_rejects_any_issuer_private_material(tmp_path):
    import inspect

    args = configuration(tmp_path)
    for field in ("issuer_pool", "issuer_signing_key", "issuer_kid"):
        assert field not in inspect.signature(assemble_batch_review_runtime).parameters
        with pytest.raises(TypeError):
            assemble_batch_review_runtime(**args, **{field: object()})
