"""Versioned batch wire proofs; signature validity alone grants no SQL authority.

The verifier, issuer, source operator and transactional writer remain separate
admitted capabilities. A member proof is never a WebAuthn assertion and carries
no invented credential counter. All new hashes use unprefixed SHA-256; the
established review/effect HMAC bindings retain their explicit prefix.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .staff_batch_contracts import canonical, digest, strict_loads

SCOPE_SCHEMA = "ctt.staff-review.batch-scope.v3"
CONSENT_SCHEMA = "ctt.batch-consent.v1"
MEMBER_SCHEMA = "ctt.batch.member-authorization.v1"
VERIFICATION_SCHEMA = "ctt.batch.verification.v1"
CAPTURE_SCHEMA = "ctt.batch.capture-authorization.v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAC = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")
_KID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class BatchAuthorizationDenied(ValueError):
    """Bounded failure without input, identifiers, person data or key material."""


def deny():
    raise BatchAuthorizationDenied("batch authorization denied") from None


def _uuid(value):
    try:
        result = UUID(value) if type(value) is str else value
        if type(result) is not UUID or result.int == 0:
            deny()
        if type(value) is str and str(result) != value:
            deny()
        return result
    except (ValueError, TypeError, AttributeError):
        deny()


def _sha(value):
    if type(value) is not str or _SHA.fullmatch(value) is None:
        deny()
    return value


def _number(value, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= 2**63 - 1:
        deny()
    return value


def _base64(value, *, maximum=16384):
    try:
        if type(value) is not str or len(value) > maximum * 2:
            deny()
        raw = base64.b64decode(value, validate=True)
        if not 1 <= len(raw) <= maximum or base64.b64encode(raw).decode() != value:
            deny()
        return raw
    except (ValueError, TypeError):
        deny()


@dataclass(frozen=True, repr=False)
class BatchSourcePage:
    page_index: int
    pdf_sha256: str
    pdf_page: int
    asset_id: UUID
    image_sha256: str
    width: int
    height: int

    def __post_init__(self):
        _number(self.page_index)
        _number(self.pdf_page)
        _uuid(self.asset_id)
        _sha(self.pdf_sha256)
        _sha(self.image_sha256)
        if (
            any(
                type(v) is not int or not 1 <= v <= 8000
                for v in (self.width, self.height)
            )
            or self.width * self.height > 25_000_000
        ):
            deny()

    def wire(self):
        return dict(
            page_index=self.page_index,
            pdf_sha256=self.pdf_sha256,
            pdf_page=self.pdf_page,
            asset_id=str(self.asset_id),
            image_sha256=self.image_sha256,
            width=self.width,
            height=self.height,
        )


@dataclass(frozen=True, repr=False)
class BatchReviewScope:
    tenant_id: str
    tournament_slug: str
    edition_id: UUID
    session_id: UUID
    draft_id: UUID
    draft_version: int
    draft_content_hash: str
    team_id: UUID
    manifest_sha256: str
    document_id: str
    source_pages: tuple[BatchSourcePage, ...]
    actor_id: UUID
    actor_role: str
    authority_binding: str
    staff_and_changes_binding: str
    policy_hash: str
    operation_id: UUID
    field_evidence_binding: str
    run_id: UUID
    member_id: UUID
    purpose: str = "capture_staff_batch_v1"

    def __post_init__(self):
        for value in (
            self.edition_id,
            self.session_id,
            self.draft_id,
            self.team_id,
            self.actor_id,
            self.operation_id,
            self.run_id,
            self.member_id,
        ):
            if type(value) is not UUID:
                deny()
            _uuid(value)
        for value in (self.tenant_id, self.tournament_slug, self.actor_role):
            if type(value) is not str or not _SLUG.fullmatch(value):
                deny()
        for value in (
            self.draft_content_hash,
            self.manifest_sha256,
            self.document_id,
            self.policy_hash,
        ):
            _sha(value)
        for value in (
            self.authority_binding,
            self.staff_and_changes_binding,
            self.field_evidence_binding,
        ):
            if type(value) is not str or not _MAC.fullmatch(value):
                deny()
        _number(self.draft_version)
        if (
            self.purpose != "capture_staff_batch_v1"
            or type(self.source_pages) is not tuple
            or not 1 <= len(self.source_pages) <= 4
        ):
            deny()
        if any(type(p) is not BatchSourcePage for p in self.source_pages):
            deny()
        if [p.page_index for p in self.source_pages] != list(
            range(1, len(self.source_pages) + 1)
        ):
            deny()
        if len({p.asset_id for p in self.source_pages}) != len(
            self.source_pages
        ) or len({(p.pdf_sha256, p.pdf_page) for p in self.source_pages}) != len(
            self.source_pages
        ):
            deny()


def batch_scope_value(scope: BatchReviewScope) -> dict:
    if type(scope) is not BatchReviewScope:
        deny()
    return {
        "schema": SCOPE_SCHEMA,
        **{
            k: (
                str(v)
                if type(v) is UUID
                else [p.wire() for p in v] if k == "source_pages" else v
            )
            for k, v in scope.__dict__.items()
        },
    }


def canonical_batch_scope_bytes(scope: BatchReviewScope) -> bytes:
    return canonical(batch_scope_value(scope))


def batch_scope_hash(scope: BatchReviewScope) -> str:
    return digest(batch_scope_value(scope))


def parse_batch_scope(raw: bytes) -> BatchReviewScope:
    try:
        obj = strict_loads(raw, limit=65536)
        keys = set(BatchReviewScope.__dataclass_fields__) | {"schema"}
        if (
            type(obj) is not dict
            or set(obj) != keys
            or obj["schema"] != SCOPE_SCHEMA
            or canonical(obj) != raw
        ):
            deny()
        data = {k: v for k, v in obj.items() if k != "schema"}
        for name in (
            "edition_id",
            "session_id",
            "draft_id",
            "team_id",
            "actor_id",
            "operation_id",
            "run_id",
            "member_id",
        ):
            data[name] = _uuid(data[name])
        if type(data["source_pages"]) is not list:
            deny()
        pages = []
        for page in data["source_pages"]:
            if type(page) is not dict or set(page) != set(
                BatchSourcePage.__dataclass_fields__
            ):
                deny()
            pages.append(
                BatchSourcePage(**(page | {"asset_id": _uuid(page["asset_id"])}))
            )
        data["source_pages"] = tuple(pages)
        return BatchReviewScope(**data)
    except Exception:
        deny()


_CONSENT_FIELDS = frozenset(
    {
        "schema",
        "kid",
        "consent_id",
        "challenge_id",
        "principal_id",
        "credential_id",
        "root_hash",
        "qualification_hash",
        "capability_version",
        "credential_version",
        "principal_version",
        "account_version",
        "service_version",
        "counter_before",
        "counter_after",
        "auth_profile",
        "backup_eligible",
        "backup_state",
        "auth_policy_hash",
        "issued_at",
        "expires_at",
    }
)
_MEMBER_FIELDS = frozenset(
    {
        "schema",
        "kid",
        "authorization_id",
        "consent_id",
        "member_id",
        "attempt_id",
        "operation_id",
        "principal_id",
        "scope_hash",
        "root_hash",
        "qualification_hash",
        "verification_hash",
        "authorization_generation",
        "worker_id",
        "lease_epoch",
        "dispatch_generation",
        "nonce",
        "issued_at",
        "expires_at",
    }
)
_VERIFICATION_FIELDS = frozenset(
    {
        "schema",
        "kid",
        "document_id",
        "manifest_sha256",
        "policy_hash",
        "profile_hash",
        "scope_hash",
        "payload_hash",
        "source_binding_hash",
        "qualification_hash",
        "checks_hash",
        "human_edits_hash",
        "issued_at",
        "expires_at",
        "verified_by_human",
    }
)
_CAPTURE_FIELDS = frozenset(
    {
        "schema",
        "kid",
        "scope_hash",
        "attempt_id",
        "authorization_id",
        "nonce",
        "member_proof_hash",
        "review_plan_binding",
        "effect_binding",
        "issued_at",
        "expires_at",
        "authority_metadata",
    }
)
_FIELDS = {
    CONSENT_SCHEMA: _CONSENT_FIELDS,
    MEMBER_SCHEMA: _MEMBER_FIELDS,
    VERIFICATION_SCHEMA: _VERIFICATION_FIELDS,
    CAPTURE_SCHEMA: _CAPTURE_FIELDS,
}


def _validate_body(body: Mapping[str, Any], schema: str):
    if (
        type(body) is not dict
        or schema not in _FIELDS
        or set(body) != _FIELDS[schema]
        or body["schema"] != schema
    ):
        deny()
    if type(body["kid"]) is not str or not _KID.fullmatch(body["kid"]):
        deny()
    _number(body["issued_at"], zero=True)
    _number(body["expires_at"])
    limit = 3600 if schema in (CONSENT_SCHEMA, VERIFICATION_SCHEMA) else 300
    if not 0 < body["expires_at"] - body["issued_at"] <= limit:
        deny()
    for name in (
        "consent_id",
        "challenge_id",
        "principal_id",
        "authorization_id",
        "member_id",
        "attempt_id",
        "operation_id",
        "worker_id",
        "nonce",
    ):
        if name in body:
            _uuid(body[name])
    for name in (
        "root_hash",
        "qualification_hash",
        "scope_hash",
        "verification_hash",
        "document_id",
        "manifest_sha256",
        "policy_hash",
        "profile_hash",
        "payload_hash",
        "source_binding_hash",
        "checks_hash",
        "human_edits_hash",
        "member_proof_hash",
    ):
        if name in body:
            _sha(body[name])
    for name in (
        "capability_version",
        "credential_version",
        "principal_version",
        "account_version",
        "service_version",
        "authorization_generation",
        "lease_epoch",
        "dispatch_generation",
    ):
        if name in body:
            _number(body[name])
    for name in ("review_plan_binding", "effect_binding", "finality_effect_binding"):
        if name in body and (
            type(body[name]) is not str or not _MAC.fullmatch(body[name])
        ):
            deny()
    if schema == CONSENT_SCHEMA:
        _base64(body["credential_id"], maximum=1024)
        before, after = body["counter_before"], body["counter_after"]
        if any(type(v) is not int or not 0 <= v <= 0xFFFFFFFF for v in (before, after)):
            deny()
        if (
            type(body["backup_eligible"]) is not bool
            or type(body["backup_state"]) is not bool
            or (body["backup_state"] and not body["backup_eligible"])
        ):
            deny()
        if body["auth_profile"] == "platform-passkey-v1":
            from .staff_signed_assertion import PLATFORM_POLICY_HASH

            if body["auth_policy_hash"] != PLATFORM_POLICY_HASH or not (
                before == after == 0 or after > before
            ):
                deny()
        elif body["auth_profile"] == "device-counter-v1":
            if (
                body["auth_policy_hash"] is not None
                or not after > before
                or body["backup_eligible"]
                or body["backup_state"]
            ):
                deny()
        else:
            deny()
    if schema == VERIFICATION_SCHEMA and body["verified_by_human"] is not False:
        deny()
    if schema == CAPTURE_SCHEMA:
        metadata = body["authority_metadata"]
        if type(metadata) is not dict or not metadata or len(metadata) > 37:
            deny()
        for row_id, values in metadata.items():
            _uuid(row_id)
            if type(values) is not dict:
                deny()


def sign_batch_proof(
    body: Mapping[str, Any], *, signing_key: Ed25519PrivateKey
) -> bytes:
    """Sign only a typed candidate; callers establish admitted authority first."""
    try:
        data = dict(body)
        _validate_body(data, data.get("schema"))
        signature = signing_key.sign(
            data["schema"].encode("ascii") + b"\0" + canonical(data)
        )
        return canonical(
            {"body": data, "signature": base64.b64encode(signature).decode()}
        )
    except Exception:
        deny()


def verify_batch_proof(
    raw: bytes, *, schema: str, public_keys: Mapping[str, bytes], now: int
) -> dict:
    """Authenticate strict canonical bytes; SQL still checks current admission."""
    try:
        obj = strict_loads(raw, limit=262144)
        if (
            type(obj) is not dict
            or set(obj) != {"body", "signature"}
            or canonical(obj) != raw
        ):
            deny()
        body = obj["body"]
        _validate_body(body, schema)
        if type(now) is not int or not body["issued_at"] <= now < body["expires_at"]:
            deny()
        signature = _base64(obj["signature"], maximum=64)
        if len(signature) != 64:
            deny()
        key = public_keys[body["kid"]]
        if type(key) is not bytes or len(key) != 32:
            deny()
        Ed25519PublicKey.from_public_bytes(key).verify(
            signature, schema.encode("ascii") + b"\0" + canonical(body)
        )
        return dict(body)
    except Exception:
        deny()


@dataclass(frozen=True, repr=False)
class VerifiedBatchDecision:
    """Typed child evidence, with no WebAuthn credential/counter impersonation."""

    attempt_id: UUID
    authorization_id: UUID
    consent_id: UUID
    member_id: UUID
    operation_id: UUID
    principal_id: UUID
    worker_id: UUID
    lease_epoch: int
    dispatch_generation: int
    authorization_generation: int
    scope_hash: str
    expires_at: int
    proof_sha256: str
    proof_bytes: bytes = field(repr=False, compare=False)


class BatchDecisionVerifier:
    def __init__(self, public_keys: Mapping[str, bytes], *, clock=None):
        import time

        self._keys = MappingProxyType(dict(public_keys))
        self._clock = clock or time.time

    def verify(self, raw: bytes) -> VerifiedBatchDecision:
        body = verify_batch_proof(
            raw, schema=MEMBER_SCHEMA, public_keys=self._keys, now=int(self._clock())
        )
        names = (
            "attempt_id",
            "authorization_id",
            "consent_id",
            "member_id",
            "operation_id",
            "principal_id",
            "worker_id",
        )
        return VerifiedBatchDecision(
            **{name: UUID(body[name]) for name in names},
            **{
                name: body[name]
                for name in (
                    "lease_epoch",
                    "dispatch_generation",
                    "authorization_generation",
                    "scope_hash",
                    "expires_at",
                )
            },
            proof_sha256=hashlib.sha256(raw).hexdigest(),
            proof_bytes=raw,
        )


class BatchAwareDecisionVerifier:
    """Explicit, fail-closed routing; individual parsing remains unchanged."""

    def __init__(self, individual_verifier, batch_verifier: BatchDecisionVerifier):
        if (
            not callable(getattr(individual_verifier, "verify", None))
            or type(batch_verifier) is not BatchDecisionVerifier
        ):
            deny()
        self._individual, self._batch = individual_verifier, batch_verifier

    def verify(self, raw: bytes):
        value = strict_loads(raw, limit=262144)
        if type(value) is not dict or type(value.get("body")) is not dict:
            deny()
        if value["body"].get("schema") == MEMBER_SCHEMA:
            return self._batch.verify(raw)
        return self._individual.verify(raw)


class BatchCaptureAdapter:
    """Prepare the exact late effect, dispatch privately, read canonical result.

    The store, gate, source adapter and transport are trusted configuration.
    Job dictionaries choose only a stable attempt; immutable inputs are loaded
    through the private store. No browser input supplies rows or authority.
    """

    def __init__(
        self,
        *,
        store,
        gate,
        source_adapter,
        capture_client,
        public_keys,
        issuer_public_keys,
        review_key,
        effect_key,
        clock=None,
    ):
        import time

        self._store, self._gate, self._source = store, gate, source_adapter
        self._capture = capture_client
        self._keys = MappingProxyType(dict(public_keys))
        self._issuer_keys = MappingProxyType(dict(issuer_public_keys))
        self._review_key, self._effect_key = review_key, effect_key
        self._clock = clock or time.time

    async def recover(self, attempt_id: UUID) -> dict:
        return await self._store.private_result(attempt_id)

    async def prepare_and_capture(
        self, job: dict, child_proof: bytes | None = None, *, proof_factory=None
    ) -> dict:
        import asyncio
        from copy import deepcopy

        from .staff_capture_authorization import verify_capture_authorization
        from .staff_capture_effect import (
            DATABASE_DERIVED_FIELDS,
            LATE_AUTHORITY_FIELDS,
            freeze_capture_batch_effect,
        )
        from .staff_capture_executor import CaptureDenied
        from .staff_capture_transport import CaptureTransportUncertain

        try:
            attempt_id = UUID(job["attempt_id"])
            if (child_proof is None) == (proof_factory is None) or (
                proof_factory is not None and not callable(proof_factory)
            ):
                deny()
            loaded = await self._store.private_capture_context(attempt_id)

            def private_bytes(key):
                value = loaded[key]
                return (
                    value
                    if type(value) is bytes
                    else _base64(value, maximum=8 * 1024 * 1024)
                )

            scope_raw, review, evidence = (
                private_bytes(key)
                for key in ("scope_bytes", "review_bytes", "evidence_bytes")
            )
            scope = parse_batch_scope(scope_raw)
            context = {
                "parent_consent": private_bytes("parent_proof_bytes"),
                "verification": private_bytes("verification_proof_bytes"),
                "root_bytes": private_bytes("root_bytes"),
            }
            with self._source.open_verified_snapshot(scope_raw) as snapshot:
                snapshot.verify_unchanged()
                # Source CAS/hash preparation precedes the five-minute child.
                # The closure resolves the current lease/generation only now.
                if proof_factory is not None:
                    child_proof = await proof_factory()
                child = verify_batch_proof(
                    child_proof,
                    schema=MEMBER_SCHEMA,
                    public_keys=self._issuer_keys,
                    now=int(self._clock()),
                )
                if child["attempt_id"] != str(attempt_id):
                    deny()
                if (
                    batch_scope_hash(scope) != child["scope_hash"]
                    or str(scope.member_id) != child["member_id"]
                ):
                    deny()
                issued, expires = child["issued_at"], child["expires_at"]
                preauth = await asyncio.to_thread(
                    self._gate.preauthorize,
                    scope=scope,
                    attempt_id=attempt_id,
                    review_bytes=review,
                    evidence_bytes=evidence,
                    signed_review=child_proof,
                    snapshot=snapshot,
                    issued_at=issued,
                    expires_at=expires,
                    **context,
                )
                snapshot.verify_unchanged()
            plan = strict_loads(review, limit=4 * 1024 * 1024)
            team, players, staff = (
                deepcopy(plan[name]) for name in ("team", "players", "staff")
            )
            for row in players + [
                item["row"] for item in staff if item["row"] is not None
            ]:
                for name in LATE_AUTHORITY_FIELDS:
                    row[name] = preauth["authority_metadata"][row["id"]][name]
            projected = await self._store.project_rows(attempt_id, team, players)
            if len(projected["players"]) != len(players):
                deny()
            for row, derived in [
                (team, projected["team"]),
                *zip(players, projected["players"]),
            ]:
                if (
                    set(derived) != {"id", *DATABASE_DERIVED_FIELDS}
                    or derived["id"] != row["id"]
                ):
                    deny()
                row.update({name: derived[name] for name in DATABASE_DERIVED_FIELDS})
            effect = freeze_capture_batch_effect(
                scope=scope,
                team=team,
                players=players,
                staff=staff,
                player_source_refs=[
                    f"page:{item['page_index']}:jugador_{item['source_slot']}"
                    for item in plan["player_sources"]
                ],
            )
            nonce = UUID(child["nonce"])
            envelope = await asyncio.to_thread(
                self._gate.authorize_effect,
                scope=scope,
                attempt_id=attempt_id,
                nonce=nonce,
                review_bytes=review,
                effect_bytes=effect,
                signed_review=child_proof,
                preauthorization=preauth,
                issued_at=issued,
                expires_at=expires,
                **context,
            )
            verify_capture_authorization(
                envelope=envelope,
                public_keys=self._keys,
                scope=scope,
                attempt_id=attempt_id,
                nonce=nonce,
                review_bytes=review,
                effect_bytes=effect,
                review_key=self._review_key,
                effect_key=self._effect_key,
                member_proof_bytes=child_proof,
                now_seconds=int(self._clock()),
            )
            await self._store.stage_capture_payload(
                attempt_id,
                UUID(child["authorization_id"]),
                review,
                effect,
                evidence,
                canonical(preauth),
                envelope,
                nonce,
            )
            try:
                await self._capture.execute(attempt_id, child_proof, nonce=uuid4())
            except CaptureTransportUncertain:
                return await self.recover(attempt_id)
            return await self.recover(attempt_id)
        except Exception as exc:
            raise CaptureDenied("capture denied") from exc


class BatchAwareFinalityService:
    """Route exact scope types without changing individual finality semantics."""

    def __init__(self, *, individual, batch):
        for service in (individual, batch):
            if any(
                not callable(getattr(service, method, None))
                for method in ("finalize_rows", "authorize_finality_effect")
            ):
                deny()
        self._individual, self._batch = individual, batch

    def _service(self, scope):
        from .staff_review_challenge import ReviewScope

        if type(scope) is BatchReviewScope:
            return self._batch
        if type(scope) is ReviewScope:
            return self._individual
        deny()

    def finalize_rows(self, **kwargs):
        return self._service(kwargs["scope"]).finalize_rows(**kwargs)

    def authorize_finality_effect(self, **kwargs):
        return self._service(kwargs["scope"]).authorize_finality_effect(**kwargs)
