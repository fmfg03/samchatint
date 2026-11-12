"""Explicit composition of private batch services; imports activate nothing.

The caller supplies separately admitted pools, exact policies/profiles, keys
and sealed directories. Factories perform no SQL, model invocation, enrollment,
source admission, socket binding or task startup. Operational grants and human
consent remain mandatory after composition.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .staff_batch_authorization import BatchCaptureAdapter, BatchReviewScope
from .staff_batch_contracts import digest
from .staff_batch_operations_channel import create_batch_operations_app
from .staff_batch_plan_builder import BatchPlanBuilder
from .staff_batch_source_adapter import PrivateBatchSourceProvider, SourceCheckAdapterV2
from .staff_batch_source_service import (
    BatchCatalogSourceProvider,
    PrivateBatchAnalysisCache,
)
from .staff_batch_store import PostgresBatchReviewStore
from .staff_batch_verification import BatchPrivateVerifier
from .staff_batch_workflow import BatchReviewWorkflowService
from .staff_capture_transport import CaptureUnixClient
from .staff_capture_transport_store import PostgresTransportNonces
from .staff_photo_store import PrivateReviewPhotoStore


def _private_bytes(key):
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def _public_bytes(key):
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def _root(value):
    value = Path(value)
    if not value.is_absolute() or ".." in value.parts:
        raise ValueError("absolute private batch root required")
    return value


class BatchPolicyFinalityService:
    """Select an already configured gate by the signed exact policy hash."""

    def __init__(self, gates):
        from staff_capture_gate_v3 import StaffCaptureGateV3

        if not gates or any(
            type(gate) is not StaffCaptureGateV3 or gate._policy_hash != "sha256:" + key
            for key, gate in gates.items()
        ):
            raise ValueError("exact private batch gate pins required")
        self._gates = MappingProxyType(dict(gates))

    def _gate(self, scope):
        if type(scope) is not BatchReviewScope or scope.policy_hash not in self._gates:
            raise ValueError("batch finality scope denied")
        return self._gates[scope.policy_hash]

    def finalize_rows(self, **kwargs):
        return self._gate(kwargs["scope"]).finalize_rows(**kwargs)

    def authorize_finality_effect(self, **kwargs):
        return self._gate(kwargs["scope"]).authorize_finality_effect(**kwargs)


class _PolicyCaptureAdapter:
    def __init__(self, store, adapters):
        self._store, self._adapters = store, MappingProxyType(dict(adapters))

    async def recover(self, attempt_id):
        return await self._store.private_result(attempt_id)

    async def prepare_and_capture(self, job, proof=None, *, proof_factory=None):
        import base64
        from uuid import UUID

        from .staff_batch_authorization import parse_batch_scope

        context = await self._store.private_capture_context(
            UUID(str(job["attempt_id"]))
        )
        raw = context["scope_bytes"]
        if type(raw) is str:
            raw = base64.b64decode(raw, validate=True)
        scope = parse_batch_scope(raw)
        if scope.policy_hash not in self._adapters:
            raise ValueError("batch policy unavailable")
        # The selected adapter reloads private inputs and rechecks the proof,
        # source and policy. This lookup itself creates no authority.
        return await self._adapters[scope.policy_hash].prepare_and_capture(
            job, proof, proof_factory=proof_factory
        )


@dataclass(frozen=True)
class BatchPrivateRuntime:
    """Explicit process lifecycle; the caller owns pools and transport clients."""

    service: BatchReviewWorkflowService
    app: object
    source_adapter: SourceCheckAdapterV2
    finality_service: BatchPolicyFinalityService
    issuer: object | None

    def start(self):
        self.service.start()

    async def aclose(self):
        await self.service.aclose()


def assemble_batch_review_runtime(
    *,
    enabled=False,
    capture_enabled=False,
    retained_analysis_only=False,
    workflow_pool=None,
    verifier_pool=None,
    worker_pool=None,
    nonce_pool=None,
    backend_pin=None,
    private_source_root=None,
    private_photo_root=None,
    private_analysis_root=None,
    profiles=None,
    profile_hash=None,
    business_gates=None,
    verifier_kid=None,
    verifier_signing_key=None,
    issuer_client=None,
    issuer_public_keys=None,
    channel_key=None,
    review_key=None,
    effect_key=None,
    gate_public_keys=None,
    capture_client=None,
    analysis_producer=None,
    worker_id=None,
    max_source_bytes=512 * 1024 * 1024,
    max_asset_bytes=16 * 1024 * 1024,
    max_manifest_bytes=8 * 1024 * 1024,
):
    """Assemble the real workflow/provider/verifier/issuer/worker chain.

    Missing external analysis is allowed only when the caller explicitly
    selects replay of retained observations. That mode still requires source
    verification, qualification, current batch faculty and genuine parent UV.
    Neither enabling flag admits a source, capability or production credential.
    """
    if any(
        type(flag) is not bool
        for flag in (enabled, capture_enabled, retained_analysis_only)
    ):
        raise ValueError("explicit batch flags required")
    if not enabled:
        if capture_enabled:
            raise ValueError("batch capture requires enabled runtime")
        return None
    from .staff_batch_issuer_channel import BatchIssuerClient

    gates = BatchPolicyFinalityService(business_gates)
    if (
        not isinstance(verifier_signing_key, Ed25519PrivateKey)
        or type(backend_pin) is not str
        or not backend_pin
        or type(profiles) is not dict
        or set(profiles) != {profile_hash}
        or digest(profiles[profile_hash]) != profile_hash
        or any(
            type(v) is not int or v < 1
            for v in (max_source_bytes, max_asset_bytes, max_manifest_bytes)
        )
    ):
        raise ValueError("complete private batch verification required")
    if analysis_producer is None and not retained_analysis_only:
        raise ValueError(
            "admitted analysis producer or explicit retained replay required"
        )
    if analysis_producer is not None and not callable(
        getattr(analysis_producer, "analyze", None)
    ):
        raise ValueError("concrete budget-bound analysis producer required")
    pools = [workflow_pool, verifier_pool, nonce_pool]
    if capture_enabled:
        pools += [worker_pool]
        if (
            type(issuer_client) is not BatchIssuerClient
            or not issuer_public_keys
            or issuer_client._public_keys != dict(issuer_public_keys)
            or not isinstance(capture_client, CaptureUnixClient)
        ):
            raise ValueError("complete private batch capture required")
    elif any(
        v is not None
        for v in (
            worker_pool,
            issuer_client,
            capture_client,
        )
    ):
        raise ValueError("capture dependencies require explicit capture enablement")
    if any(pool is None for pool in pools) or len({id(pool) for pool in pools}) != len(
        pools
    ):
        raise ValueError("separate private batch service pools required")
    keys = (channel_key, review_key, effect_key)
    if (
        any(type(k) is not bytes or not 32 <= len(k) <= 64 for k in keys)
        or len(set(keys)) != 3
    ):
        raise ValueError("separate batch channel and binding keys required")
    reserved = [review_key, effect_key, _private_bytes(verifier_signing_key)]
    issuer_keys = dict(issuer_public_keys or {})
    if capture_enabled:
        reserved.extend((issuer_client._key, capture_client._key))
    verifier_keys = {verifier_kid: _public_bytes(verifier_signing_key)}
    for key, gate in gates._gates.items():
        if gate._review_key != review_key or gate._effect_key != effect_key:
            raise ValueError("batch gate binding keys differ")
        if any(
            gate._verifier_keys.get(kid) != pub for kid, pub in verifier_keys.items()
        ):
            raise ValueError("batch verifier pin differs")
        if capture_enabled and any(
            gate._assertion_keys.get(kid) != pub for kid, pub in issuer_keys.items()
        ):
            raise ValueError("batch issuer pin differs")
        if not gate_public_keys or gate_public_keys.get(gate._kid) != _public_bytes(
            gate._signer
        ):
            raise ValueError("batch gate signer pin differs")
        reserved.extend((_private_bytes(gate._signer), gate._identity_key))
    if any(hmac.compare_digest(channel_key, value) for value in reserved):
        raise ValueError("batch channel key overlaps authority material")
    logging.getLogger("fido2.server").setLevel(logging.WARNING)
    store = PostgresBatchReviewStore(workflow_pool, backend_pin=backend_pin)
    verifier_store = PostgresBatchReviewStore(verifier_pool, backend_pin=backend_pin)
    adapter = SourceCheckAdapterV2(
        PrivateBatchSourceProvider(
            _root(private_source_root), max_manifest_bytes=max_manifest_bytes
        ),
        max_source_bytes=max_source_bytes,
        max_asset_bytes=max_asset_bytes,
        max_manifest_bytes=max_manifest_bytes,
    )
    photos = PrivateReviewPhotoStore(_root(private_photo_root))
    builders = {
        key: BatchPlanBuilder(
            review_key=review_key,
            policy_artifact=gate._policy,
            expected_policy_hash=gate._policy_hash,
            photo_store=photos,
            gate=gate,
        )
        for key, gate in gates._gates.items()
    }
    sources = BatchCatalogSourceProvider(
        store=store,
        source_adapter=adapter,
        profiles=profiles,
        builders=builders,
        analysis_cache=PrivateBatchAnalysisCache(_root(private_analysis_root)),
        analysis_producer=analysis_producer,
        analysis_budget_store=verifier_store,
    )
    verifier = BatchPrivateVerifier(
        source_reader=sources,
        store=verifier_store,
        signing_key=verifier_signing_key,
        kid=verifier_kid,
        profile_hash=profile_hash,
    )
    issuer = None
    capture = None
    workers = None
    if capture_enabled:
        issuer = issuer_client
        workers = PostgresBatchReviewStore(worker_pool, backend_pin=backend_pin)
        capture = _PolicyCaptureAdapter(
            store,
            {
                key: BatchCaptureAdapter(
                    store=store,
                    gate=gate,
                    source_adapter=adapter,
                    capture_client=capture_client,
                    public_keys=gate_public_keys,
                    issuer_public_keys=issuer_keys,
                    review_key=review_key,
                    effect_key=effect_key,
                )
                for key, gate in gates._gates.items()
            },
        )
    service = BatchReviewWorkflowService(
        store=store,
        source_provider=sources,
        verifier=verifier,
        issuer=issuer,
        capture_adapter=capture,
        worker_store=workers,
        capture_enabled=capture_enabled,
        worker_id=worker_id,
        reserved_channel_secrets=tuple(reserved),
    )
    app = create_batch_operations_app(
        service=service,
        channel_key=channel_key,
        consume_nonce=PostgresTransportNonces(nonce_pool),
    )
    return BatchPrivateRuntime(service, app, adapter, gates, issuer)
