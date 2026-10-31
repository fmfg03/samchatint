"""Authenticated writer attestations to the separately accredited finality gate.

No signer key enters the writer. Wire context contains no CAS/incident authority
flags. The gate resolves immutable protected inputs, current source context and
incident inventory independently, and opens its own retained-source snapshot.
"""

import asyncio
import base64
import hmac
import ipaddress
import time
from typing import NoReturn
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from devnous.copa_telmex.staff_capture_edition import TrustedEditionBinding
from devnous.copa_telmex.staff_capture_executor import CaptureDenied, LockedAttempt
from devnous.copa_telmex.staff_capture_finality_authorization import (
    verify_capture_finality_authorization,
)
from devnous.copa_telmex.staff_capture_finality_effect import (
    FinalityContext,
    VerifiedFinalityBundle,
    canonical,
    parse,
    pending_rows,
    verify_finality_bundle,
)
from devnous.copa_telmex.staff_capture_finalizing_writer import _blocking_incident_count
from devnous.copa_telmex.staff_capture_private_sources import (
    PrivateAttemptSourceProvider,
)
from devnous.copa_telmex.staff_capture_source_adapter import SourceCheckAdapter
from devnous.copa_telmex.staff_capture_transport_store import PostgresTransportNonces
from devnous.copa_telmex.staff_review_challenge import (
    canonical_scope_bytes,
    parse_canonical_scope,
    scope_hash,
)
from devnous.copa_telmex.staff_review_operations_channel import (
    _canonical as wire_canonical,
    _json as wire_json,
    _mac as channel_mac,
)
from devnous.copa_telmex.staff_review_proposal_store import PostgresReviewProposalStore

PREFIX = "/v2/staff-review/finality/"
OPERATIONS = frozenset({"finalize_rows", "authorize_finality_effect"})
MAX_WIRE_BYTES = 8_388_608
MAX_FIELD_BYTES = 4_194_304
TIME = "X-CTT-Finality-Time"
NONCE = "X-CTT-Finality-Nonce"
MAC = "X-CTT-Finality-MAC"
RESPONSE_MAC = "X-CTT-Finality-Response-MAC"
_BASE = {
    "context",
    "scope_bytes",
    "review_attempt_id",
    "review_bytes",
    "pending_effect_bytes",
    "signed_review",
    "preauthorization_bytes",
}
_CONTEXT = {
    "database_identity",
    "database_oid",
    "pg_top_xid",
    "issued_at",
    "expires_at",
    "nonce",
}


def deny() -> NoReturn:
    raise CaptureDenied("finality channel unavailable") from None


def binary(value):
    try:
        raw = base64.b64decode(value, validate=True)
        if (
            not 0 < len(raw) <= MAX_FIELD_BYTES
            or base64.b64encode(raw).decode() != value
        ):
            deny()
        return raw
    except Exception:
        deny()


def identifier(value):
    try:
        result = UUID(value)
        if str(result) != value:
            deny()
        return result
    except Exception:
        deny()


def request_mac(key, operation, nonce, timestamp, raw):
    if (
        operation not in OPERATIONS
        or type(timestamp) is not int
        or not 0 < timestamp <= 253402300799
        or not isinstance(nonce, UUID)
        or type(raw) is not bytes
        or not 0 < len(raw) <= MAX_WIRE_BYTES
    ):
        deny()
    return channel_mac(
        key,
        b"samchat.ctt.finality-rpc.request.v1\0",
        b"POST",
        (PREFIX + operation).encode(),
        str(nonce).encode(),
        str(timestamp).encode(),
        raw,
    )


def response_mac(key, operation, nonce, request, status, raw):
    import hashlib

    if (
        operation not in OPERATIONS
        or not isinstance(nonce, UUID)
        or type(status) is not int
        or status not in (200, 503)
        or type(raw) is not bytes
        or not 0 < len(raw) <= MAX_WIRE_BYTES
    ):
        deny()
    return channel_mac(
        key,
        b"samchat.ctt.finality-rpc.response.v1\0",
        (PREFIX + operation).encode(),
        str(nonce).encode(),
        hashlib.sha256(request).digest(),
        str(status).encode(),
        raw,
    )


def decode(operation, raw):
    try:
        value = wire_json(raw, MAX_WIRE_BYTES)
        additional = (
            {"observed_pending_rows_bytes"}
            if operation == "finalize_rows"
            else {"finality_effect_bytes", "verified_finalities_bytes"}
        )
        if operation not in OPERATIONS or set(value) != _BASE | additional:
            deny()
        context = value["context"]
        if type(context) is not dict or set(context) != _CONTEXT:
            deny()
        if type(context["database_identity"]) is not str:
            deny()
        for key in ("database_oid", "pg_top_xid", "issued_at", "expires_at"):
            if type(context[key]) is not int or context[key] <= 0:
                deny()
        context["nonce"] = identifier(context["nonce"])
        value["review_attempt_id"] = identifier(value["review_attempt_id"])
        for key in _BASE | additional:
            if key.endswith("_bytes") or key == "signed_review":
                value[key] = binary(value[key])
        parse_canonical_scope(value["scope_bytes"])
        return value
    except Exception:
        deny()


def encode(
    operation,
    *,
    protected_context,
    scope,
    review_attempt_id,
    review_bytes,
    pending_effect_bytes,
    signed_review,
    preauthorization,
    **extra,
):
    try:
        if type(protected_context) is not FinalityContext:
            deny()
        protected_context.verify(scope, review_attempt_id)
        value = {
            "context": {key: getattr(protected_context, key) for key in _CONTEXT},
            "scope_bytes": canonical_scope_bytes(scope),
            "review_attempt_id": str(review_attempt_id),
            "review_bytes": review_bytes,
            "pending_effect_bytes": pending_effect_bytes,
            "signed_review": signed_review,
            "preauthorization_bytes": canonical(preauthorization),
            **extra,
        }
        value["context"]["nonce"] = str(value["context"]["nonce"])
        for key in tuple(value):
            if key.endswith("_bytes") or key == "signed_review":
                if (
                    type(value[key]) is not bytes
                    or not 0 < len(value[key]) <= MAX_FIELD_BYTES
                ):
                    deny()
                value[key] = base64.b64encode(value[key]).decode()
        raw = wire_canonical(value)
        decode(operation, raw)
        return raw
    except Exception:
        deny()


class FinalityGateRPCServer:
    """Private gate side: independently resolve protected inputs and source CAS."""

    def __init__(
        self,
        *,
        gate,
        store,
        private_source_root,
        database_identity,
        max_source_bytes,
        max_asset_bytes,
        max_manifest_bytes,
    ):
        from staff_capture_gate_v2 import StaffCaptureGateV2

        if (
            not isinstance(gate, StaffCaptureGateV2)
            or not isinstance(store, PostgresReviewProposalStore)
            or type(database_identity) is not str
            or not database_identity
            or any(
                type(n) is not int or n < 1
                for n in (max_source_bytes, max_asset_bytes, max_manifest_bytes)
            )
        ):
            deny()
        self._gate, self._store = gate, store
        self._root, self._identity = Path(private_source_root), database_identity
        if not self._root.is_absolute() or ".." in self._root.parts:
            deny()
        self._limits = max_source_bytes, max_asset_bytes, max_manifest_bytes

    @contextmanager
    def _snapshot(self, scope, attempt, source_context):
        source, asset, manifest = self._limits
        binding = TrustedEditionBinding(
            scope.tenant_id,
            scope.edition_id,
            UUID(source_context["tournament_id"]),
            source_context["edition_year"],
            scope.tournament_slug,
        )
        locked = LockedAttempt(
            attempt,
            attempt,
            scope.actor_id,
            bytes.fromhex(source_context["credential_id_hex"]),
            scope_hash(scope),
            canonical_scope_bytes(scope),
            binding,
        )
        provider = PrivateAttemptSourceProvider(
            locked, self._root, max_manifest_bytes=manifest
        )
        adapter = SourceCheckAdapter(
            provider,
            max_source_bytes=source,
            max_asset_bytes=asset,
            max_manifest_bytes=manifest,
        )
        with adapter.open_verified_snapshot(locked) as snapshot:
            yield snapshot

    async def invoke(self, operation, value):
        """The only admitted source of CAS/incident status is protected SQL+CAS."""
        protected = await self._store.load_finality_gate_context(
            value["review_attempt_id"]
        )
        scope = parse_canonical_scope(value["scope_bytes"])
        expected = {
            "scope_bytes",
            "review_bytes",
            "pending_effect_bytes",
            "signed_review",
            "preauthorization_bytes",
        }
        if not protected or any(
            type(protected.get(key)) is not bytes or protected[key] != value[key]
            for key in expected
        ):
            deny()
        attested = value["context"]
        if (
            protected["database_identity"] != self._identity
            or attested["database_identity"] != self._identity
            or type(protected["database_oid"]) is not int
            or attested["database_oid"] != protected["database_oid"]
        ):
            deny()
        pending = parse(value["pending_effect_bytes"])
        incidents = _blocking_incident_count(
            parse(protected["validation_bytes"]),
            [row["roster_index"] for row in pending["players"]],
        )
        if incidents != 0:
            deny()
        source = protected["source_context"]
        if (
            source["tenant_id"] != scope.tenant_id
            or source["edition_id"] != str(scope.edition_id)
            or source["roster_slug"] != scope.tournament_slug
        ):
            deny()
        observed = pending_rows(pending)
        if operation == "finalize_rows" and canonical(
            parse(value["observed_pending_rows_bytes"])
        ) != canonical(observed):
            deny()
        with self._snapshot(scope, value["review_attempt_id"], source) as snapshot:
            context = FinalityContext(
                **attested,
                review_attempt_id=value["review_attempt_id"],
                snapshot=snapshot,
                blocking_incident_count=incidents,
                cas_succeeded=True,
            )
            context.verify(scope, value["review_attempt_id"])
            kwargs = dict(
                protected_context=context,
                scope=scope,
                review_attempt_id=value["review_attempt_id"],
                review_bytes=value["review_bytes"],
                pending_effect_bytes=value["pending_effect_bytes"],
                signed_review=value["signed_review"],
                preauthorization=parse(value["preauthorization_bytes"]),
            )
            if operation == "finalize_rows":
                bundle = await asyncio.to_thread(
                    self._gate.finalize_rows, **kwargs, observed_pending_rows=observed
                )
                return bundle.payload_bytes
            return await asyncio.to_thread(
                self._gate.authorize_finality_effect,
                **kwargs,
                finality_effect_bytes=value["finality_effect_bytes"],
                verified_finalities=VerifiedFinalityBundle(
                    value["verified_finalities_bytes"]
                ),
            )


def mount_finality_rpc(
    app,
    *,
    service,
    channel_key,
    operations_channel_key,
    consume_nonce,
    max_clock_skew=30,
    timeout_seconds=90,
    max_inflight=4,
):
    """Mount two fixed private RPCs; never expose with public operations key."""
    from fastapi import Request, Response, HTTPException

    if (
        not isinstance(service, FinalityGateRPCServer)
        or not isinstance(consume_nonce, PostgresTransportNonces)
        or type(max_clock_skew) is not int
        or not 1 <= max_clock_skew <= 60
        or type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 120
        or type(max_inflight) is not int
        or not 1 <= max_inflight <= 64
    ):
        deny()
    gate = service._gate
    forbidden = (
        operations_channel_key,
        gate._review_key,
        gate._effect_key,
        gate._identity_key,
        gate._signer.private_bytes_raw(),
    )
    if (
        type(channel_key) is not bytes
        or not 32 <= len(channel_key) <= 64
        or any(hmac.compare_digest(channel_key, key) for key in forbidden)
    ):
        deny()
    semaphore = asyncio.Semaphore(max_inflight)

    @app.post(PREFIX + "{operation}")
    async def invoke(operation: str, request: Request):
        if operation not in OPERATIONS or semaphore.locked():
            raise HTTPException(503, "finality unavailable")
        async with semaphore:
            try:
                async with asyncio.timeout(timeout_seconds):
                    if (
                        any(
                            len(request.headers.getlist(name)) != 1
                            for name in (TIME, NONCE, MAC, "content-type")
                        )
                        or request.headers["content-type"] != "application/json"
                        or request.headers.get("content-encoding", "identity")
                        != "identity"
                    ):
                        deny()
                    chunks, size = [], 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_WIRE_BYTES:
                            deny()
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    nonce = identifier(request.headers[NONCE])
                    timestamp_text = request.headers[TIME]
                    if (
                        not timestamp_text.isascii()
                        or not timestamp_text.isdecimal()
                        or len(timestamp_text) > 12
                    ):
                        deny()
                    timestamp = int(timestamp_text)
                    if (
                        str(timestamp) != timestamp_text
                        or abs(int(time.time()) - timestamp) > max_clock_skew
                        or not hmac.compare_digest(
                            request_mac(channel_key, operation, nonce, timestamp, raw),
                            request.headers[MAC],
                        )
                    ):
                        deny()
                    value = decode(operation, raw)
                    if (
                        await consume_nonce(nonce, timestamp + max_clock_skew)
                        is not True
                    ):
                        deny()
                    try:
                        result = await service.invoke(operation, value)
                        body = wire_canonical(
                            {"result": base64.b64encode(result).decode()}
                        )
                        status = 200
                    except Exception:
                        body, status = (
                            wire_canonical({"error": "FINALITY_UNAVAILABLE"}),
                            503,
                        )
                    return Response(
                        body,
                        status_code=status,
                        media_type="application/json",
                        headers={
                            RESPONSE_MAC: response_mac(
                                channel_key, operation, nonce, raw, status, body
                            ),
                            "Cache-Control": "no-store",
                        },
                    )
            except Exception:
                raise HTTPException(503, "finality unavailable") from None

    return app


class FinalityRPCClient:
    """Synchronous interface used by writer's asyncio.to_thread, no signer key."""

    def __init__(
        self,
        *,
        base_url,
        channel_key,
        http_client,
        effect_key,
        review_key,
        gate_public_keys,
        receipt_public_keys,
        assertion_public_keys,
        timeout_seconds=90,
    ):
        try:
            parsed = urlsplit(base_url)
            if (
                parsed.scheme != "http"
                or not ipaddress.ip_address(parsed.hostname).is_loopback
                or parsed.port is None
                or parsed.path not in ("", "/")
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or getattr(http_client, "trust_env", None) is not False
                or type(timeout_seconds) is not int
                or not 1 <= timeout_seconds <= 120
            ):
                deny()
            channel_mac(channel_key, b"configuration")
            if channel_key in (effect_key, review_key):
                deny()
            self._url, self._key, self._http = (
                base_url.rstrip("/"),
                channel_key,
                http_client,
            )
            self._effect_key, self._review_key = effect_key, review_key
            self._gate_keys, self._receipt_keys, self._assertion_keys = (
                gate_public_keys,
                receipt_public_keys,
                assertion_public_keys,
            )
            self._timeout = timeout_seconds
        except Exception:
            deny()

    def _call(self, operation, raw):
        try:
            nonce, timestamp = uuid4(), int(time.time())
            headers = {
                "Content-Type": "application/json",
                NONCE: str(nonce),
                TIME: str(timestamp),
                MAC: request_mac(self._key, operation, nonce, timestamp, raw),
            }
            with self._http.stream(
                "POST",
                self._url + PREFIX + operation,
                content=raw,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                if (
                    response.status_code not in (200, 503)
                    or response.headers.get("content-type") != "application/json"
                    or response.headers.get("content-encoding", "identity")
                    != "identity"
                    or len(response.headers.get_list(RESPONSE_MAC)) != 1
                ):
                    deny()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_WIRE_BYTES:
                        deny()
                    chunks.append(chunk)
                result = b"".join(chunks)
                if not hmac.compare_digest(
                    response_mac(
                        self._key, operation, nonce, raw, response.status_code, result
                    ),
                    response.headers[RESPONSE_MAC],
                ):
                    deny()
                obj = wire_json(result, MAX_WIRE_BYTES)
                if response.status_code != 200 or set(obj) != {"result"}:
                    deny()
                return binary(obj["result"])
        except Exception:
            deny()

    def finalize_rows(self, *, observed_pending_rows, **kwargs):
        raw = encode(
            "finalize_rows",
            **kwargs,
            observed_pending_rows_bytes=canonical(observed_pending_rows),
        )
        bundle = VerifiedFinalityBundle(self._call("finalize_rows", raw))
        verify_finality_bundle(
            bundle=bundle,
            scope=kwargs["scope"],
            context=kwargs["protected_context"],
            pending_effect_bytes=kwargs["pending_effect_bytes"],
            preauthorization=kwargs["preauthorization"],
            effect_key=self._effect_key,
            receipt_public_keys=self._receipt_keys,
        )
        return bundle

    def authorize_finality_effect(
        self,
        *,
        finality_effect_bytes,
        verified_finalities,
        nonce=None,
        issued_at=None,
        expires_at=None,
        **kwargs,
    ):
        context = kwargs["protected_context"]
        if (
            (nonce is not None and nonce != context.nonce)
            or (
                issued_at is not None
                and (type(issued_at) is not int or issued_at != context.issued_at)
            )
            or (
                expires_at is not None
                and (type(expires_at) is not int or expires_at != context.expires_at)
            )
        ):
            deny()
        raw = encode(
            "authorize_finality_effect",
            **kwargs,
            finality_effect_bytes=finality_effect_bytes,
            verified_finalities_bytes=verified_finalities.payload_bytes,
        )
        envelope = self._call("authorize_finality_effect", raw)
        verify_capture_finality_authorization(
            envelope=envelope,
            public_keys=self._gate_keys,
            scope=kwargs["scope"],
            protected_context=context,
            attempt_id=kwargs["review_attempt_id"],
            nonce=context.nonce,
            review_bytes=kwargs["review_bytes"],
            pending_effect_bytes=kwargs["pending_effect_bytes"],
            finality_effect_bytes=finality_effect_bytes,
            signed_review=kwargs["signed_review"],
            preauthorization=kwargs["preauthorization"],
            verified_finalities=verified_finalities,
            review_key=self._review_key,
            effect_key=self._effect_key,
            receipt_public_keys=self._receipt_keys,
            assertion_public_keys=self._assertion_keys,
            now_seconds=int(time.time()),
        )
        return envelope

    def close(self):
        self._http.close()
