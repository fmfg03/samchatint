"""Private review orchestration; the browser never supplies authority rows.

All database calls use the protected proposal service role. Source inspection
does not consume a challenge; only the independently signed review followed by
the existing trusted writer can produce a committed result.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

from .staff_capture_authorization import verify_capture_authorization
from .staff_capture_edition import TrustedEditionBinding
from .staff_capture_effect import (
    DATABASE_DERIVED_FIELDS,
    LATE_AUTHORITY_FIELDS,
    freeze_capture_effect,
)
from .staff_capture_executor import CaptureDenied, LockedAttempt
from .staff_capture_private_sources import PrivateAttemptSourceProvider
from .staff_capture_source_adapter import SourceCheckAdapter
from .staff_capture_transport import CaptureTransportUncertain
from .staff_review_challenge import (
    canonical_scope_bytes,
    parse_canonical_scope,
    scope_hash,
)


class ReviewWorkflowDenied(ValueError):
    """A bounded public error code, without source values or credentials."""

    def __init__(self, code="REVIEW_BLOCKED"):
        self.code = code
        super().__init__(code)


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _id(value):
    if isinstance(value, UUID):
        return value
    if type(value) is not str or str(UUID(value)) != value:
        raise ReviewWorkflowDenied("SCOPE_DENIED")
    return UUID(value)


class ReviewWorkflowService:
    """Configured private services, never callbacks from a public request."""

    def __init__(
        self,
        *,
        store,
        builder,
        enrollment,
        issuer,
        business_gate,
        capture_client,
        private_source_root,
        review_key,
        effect_key,
        gate_public_keys,
        policy_artifact,
        expected_policy_hash,
        max_source_bytes,
        max_asset_bytes,
        max_manifest_bytes,
        reserved_channel_secrets=(),
        clock=time.time,
    ):
        minimum = policy_artifact.get("minimum_roster_size")
        maximum = policy_artifact.get("maximum_roster_size")
        if (
            type(minimum) is not int
            or not 16 <= minimum <= 35
            or type(maximum) is not int
            or not minimum <= maximum <= 35
            or policy_artifact.get("policy_hash") != expected_policy_hash
            or type(review_key) is not bytes
            or len(review_key) < 32
            or type(effect_key) is not bytes
            or len(effect_key) < 32
            or review_key == effect_key
            or any(
                type(n) is not int or n < 1
                for n in (max_source_bytes, max_asset_bytes, max_manifest_bytes)
            )
        ):
            raise ValueError("invalid protected review configuration")
        self._store, self._builder, self._enrollment = store, builder, enrollment
        self._issuer, self._gate, self._capture = issuer, business_gate, capture_client
        self._source_root = Path(private_source_root)
        if not self._source_root.is_absolute():
            raise ValueError("private source root must be absolute")
        self._review_key, self._effect_key = review_key, effect_key
        self._gate_keys = dict(gate_public_keys)
        self._limits = max_source_bytes, max_asset_bytes, max_manifest_bytes
        self._clock = clock
        secrets_to_separate = [review_key, effect_key, *reserved_channel_secrets]
        for component in (issuer, capture_client, business_gate):
            for name in ("_key", "_identity_key", "_review_key", "_effect_key"):
                candidate = getattr(component, name, None)
                if type(candidate) is bytes:
                    secrets_to_separate.append(candidate)
        self._reserved_channel_hashes = tuple(
            hashlib.sha256(key).digest() for key in secrets_to_separate
        )

    def validate_channel_key(self, key):
        """Reject reuse of known authority or adjacent transport key domains."""
        if type(key) is not bytes or len(key) < 32:
            raise ValueError("invalid operations channel key")
        fingerprint = hashlib.sha256(key).digest()
        if any(
            hmac.compare_digest(fingerprint, used)
            for used in self._reserved_channel_hashes
        ):
            raise ValueError("separate operations channel key required")

    @contextmanager
    def _snapshot(self, context, scope, attempt_id, challenge_id=None):
        """Source-only inspection uses no fabricated approval or receipt."""
        binding = TrustedEditionBinding(
            scope.tenant_id,
            scope.edition_id,
            _id(context["tournament_id"]),
            context["edition_year"],
            scope.tournament_slug,
        )
        attempt = LockedAttempt(
            _id(attempt_id),
            _id(challenge_id or attempt_id),
            scope.actor_id,
            bytes.fromhex(context["credential_id_hex"]),
            scope_hash(scope),
            canonical_scope_bytes(scope),
            binding,
        )
        source, asset, manifest = self._limits
        provider = PrivateAttemptSourceProvider(
            attempt,
            self._source_root,
            max_manifest_bytes=manifest,
        )
        adapter = SourceCheckAdapter(
            provider,
            max_source_bytes=source,
            max_asset_bytes=asset,
            max_manifest_bytes=manifest,
        )
        with adapter.open_verified_snapshot(attempt) as snapshot:
            yield snapshot

    async def view(self, account_id, session_id):
        account_id, session_id = _id(account_id), _id(session_id)
        # The source resolver does not accredit an account or disclose names.
        await self._store.resolve_source_scope(session_id)
        try:
            context = await self._store.load_context(account_id, session_id)
        except Exception as error:
            if getattr(error, "code", None) not in {
                "ENROLLMENT_REQUIRED",
                "ACCOUNT_NOT_BOUND",
                "SCOPE_DENIED",
            }:
                raise
            return {
                "session_id": str(session_id),
                "proposal_id": None,
                "state": "enrollment_required",
                "ready_to_approve": False,
                "team": {},
                "players": [],
                "staff": [],
                "pages": [],
                "blockers": [
                    {
                        "code": "ENROLLMENT_REQUIRED",
                        "message": (
                            "Falta acreditar la passkey y la facultad de revisión."
                        ),
                    }
                ],
                "passkey": {"status": "not_enrolled"},
            }
        proposal_id = context.get("head_proposal_id")
        if proposal_id:
            proposal = await self._store.read_proposal(
                account_id, session_id, _id(proposal_id)
            )
            preview = json.loads(proposal["preview_bytes"])
            preview.update(session_id=str(session_id), proposal_id=str(proposal_id))
            preview["passkey"] = {"status": "active"}
            if context.get("current_attempt_id"):
                state = await self.status(
                    account_id, session_id, _id(context["current_attempt_id"])
                )
                preview["attempt_id"] = state["attempt_id"]
                preview["capture_state"] = state["state"]
                if state["state"] == "committed":
                    preview.update(state)
            return preview
        # Preparing the initial proposal is explicit; a GET never writes.
        proposal_id = uuid4()
        scope = self._builder.initial_scope(context, proposal_id)
        with self._snapshot(context, scope, proposal_id) as snapshot:
            prepared = self._builder.build(
                context, {}, snapshot, proposal_id, stage_photos=False
            )
        preview = json.loads(prepared.preview_bytes)
        preview.update(
            session_id=str(session_id), proposal_id=None, state="unpublished"
        )
        preview["ready_to_approve"] = False
        preview["passkey"] = {"status": "active"}
        return preview

    async def prepare(self, account_id, session_id, expected_proposal_id, changes):
        account_id, session_id = _id(account_id), _id(session_id)
        context = await self._store.load_context(account_id, session_id)
        expected = _id(expected_proposal_id) if expected_proposal_id else None
        head = context.get("head_proposal_id")
        if (str(expected) if expected else None) != head:
            raise ReviewWorkflowDenied("STALE_PROPOSAL")
        proposal_id = uuid4()
        # A new correction is based on the last private view, not an app write.
        if expected:
            previous = await self._store.read_proposal(account_id, session_id, expected)
            context = {
                **context,
                "previous_preview": json.loads(previous["preview_bytes"]),
            }
        scope = self._builder.initial_scope(context, proposal_id)
        with self._snapshot(context, scope, proposal_id) as snapshot:
            prepared = self._builder.build(context, changes, snapshot, proposal_id)
        await self._store.register_proposal(
            account_id,
            session_id,
            expected,
            proposal_id,
            canonical_scope_bytes(prepared.scope),
            prepared.review_bytes,
            prepared.evidence_bytes,
            prepared.preview_bytes,
            prepared.validation_bytes,
            canonical(context["raw_snapshot"]),
            prepared.capture_nonce,
        )
        return await self.view(account_id, session_id)

    async def challenge(self, account_id, session_id, proposal_id, confirmed):
        if confirmed is not True:
            raise ReviewWorkflowDenied("REVIEW_BLOCKED")
        account_id, session_id, proposal_id = map(
            _id, (account_id, session_id, proposal_id)
        )
        context = await self._store.load_context(account_id, session_id)
        proposal = await self._store.read_proposal(account_id, session_id, proposal_id)
        preview = json.loads(proposal["preview_bytes"])
        if preview.get("ready_to_approve") is not True:
            raise ReviewWorkflowDenied("REVIEW_BLOCKED")
        scope = parse_canonical_scope(proposal["scope_bytes"])
        with self._snapshot(context, scope, proposal_id) as snapshot:
            snapshot.verify_unchanged()
        now = int(self._clock())
        faculty_until = context.get("faculty_valid_until_seconds")
        if type(faculty_until) is not int or faculty_until <= now:
            raise ReviewWorkflowDenied("SCOPE_DENIED")
        expires = min(now + 300, faculty_until)
        challenge = secrets.token_bytes(32)
        attempt = await self._store.create_challenge(
            account_id,
            session_id,
            proposal_id,
            challenge,
            now,
            expires,
        )

        def encode(raw):
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

        return {
            "attempt_id": str(attempt["attempt_id"]),
            "options": {
                "publicKey": {
                    "challenge": encode(challenge),
                    "timeout": (expires - now) * 1000,
                    "rpId": "sam.chat",
                    "userVerification": "required",
                    "allowCredentials": [
                        {
                            "type": "public-key",
                            "id": encode(bytes.fromhex(context["credential_id_hex"])),
                        }
                    ],
                }
            },
        }

    async def status(self, account_id, session_id, attempt_id):
        account_id, session_id, attempt_id = map(
            _id, (account_id, session_id, attempt_id)
        )
        status = await self._store.status(account_id, session_id, attempt_id)
        result = {"session_id": str(session_id), "attempt_id": str(attempt_id)}
        if status.get("committed") is True:
            if status.get("session_status") != "committed" or not status.get(
                "result_id"
            ):
                raise ReviewWorkflowDenied("REVIEW_BLOCKED")
            return {
                **result,
                "state": "committed",
                "result_id": str(_id(status["result_id"])),
            }
        if status.get("is_current") is False:
            return {**result, "state": "stale"}
        # PostgreSQL evaluates expiry while holding the shared capture lock.
        # Comparing a later application clock could misclassify an in-flight
        # transaction that acquired the exclusive lock after the read.
        if status.get("expired") is True:
            return {**result, "state": "expired"}
        return {
            **result,
            "state": (
                "uncertain"
                if status.get("payload_admitted")
                or type(status.get("signed_review")) is bytes
                else "awaiting_assertion"
            ),
        }

    async def assert_and_capture(self, account_id, session_id, attempt_id, assertion):
        account_id, session_id, attempt_id = map(
            _id, (account_id, session_id, attempt_id)
        )
        state = await self.status(account_id, session_id, attempt_id)
        if state["state"] == "committed":
            return state
        if state["state"] in {"expired", "stale"}:
            raise ReviewWorkflowDenied(
                "REVIEW_EXPIRED" if state["state"] == "expired" else "STALE_PROPOSAL"
            )
        context = await self._store.load_context(account_id, session_id)
        stored = await self._store.status(account_id, session_id, attempt_id)
        if stored.get("is_current") is not True:
            raise ReviewWorkflowDenied("STALE_PROPOSAL")
        proposal = await self._store.read_proposal(
            account_id, session_id, _id(stored["proposal_id"])
        )
        if stored.get("payload_admitted"):
            signed_review = stored["signed_review"]
            if type(signed_review) is not bytes:
                raise ReviewWorkflowDenied("REVIEW_BLOCKED")
        else:
            signed_review = stored.get("signed_review")
            if type(signed_review) is not bytes:
                if type(assertion) is dict:
                    assertion = canonical(assertion)
                if type(assertion) is not bytes or not 0 < len(assertion) <= 65_536:
                    raise ReviewWorkflowDenied("REVIEW_BLOCKED")
                signed_review = await self._issuer.issue(attempt_id, assertion)
            await self._admit_effect(
                context,
                proposal,
                stored,
                account_id,
                session_id,
                attempt_id,
                signed_review,
            )
        try:
            await self._capture.execute(attempt_id, signed_review, nonce=uuid4())
        except CaptureTransportUncertain:
            return {
                "state": "uncertain",
                "session_id": str(session_id),
                "attempt_id": str(attempt_id),
            }
        # Socket acknowledgment alone is insufficient: read official completion.
        return await self.status(account_id, session_id, attempt_id)

    async def _admit_effect(
        self, context, proposal, attempt, account, session, attempt_id, signed
    ):
        scope = parse_canonical_scope(proposal["scope_bytes"])
        issued, expires = attempt["issued_at"], attempt["expires_at"]
        plan = json.loads(proposal["review_bytes"])
        with self._snapshot(
            context, scope, attempt_id, attempt["challenge_id"]
        ) as snapshot:
            preauthorization = await asyncio.to_thread(
                self._gate.preauthorize,
                scope=scope,
                attempt_id=attempt_id,
                review_bytes=proposal["review_bytes"],
                evidence_bytes=proposal["evidence_bytes"],
                signed_review=signed,
                snapshot=snapshot,
                issued_at=issued,
                expires_at=expires,
            )
        team, players, staff = (
            deepcopy(plan["team"]),
            deepcopy(plan["players"]),
            deepcopy(plan["staff"]),
        )
        people = players + [item["row"] for item in staff if item["row"] is not None]
        for row in people:
            for field in LATE_AUTHORITY_FIELDS:
                row[field] = preauthorization["authority_metadata"][row["id"]][field]
        projected = await self._store.project_capture_rows(
            account, session, attempt_id, team, players
        )
        if len(projected["players"]) != len(players):
            raise CaptureDenied("capture denied")
        for row, derived in [(team, projected["team"])] + list(
            zip(players, projected["players"])
        ):
            if derived["id"] != row["id"] or set(derived) != {
                "id",
                *DATABASE_DERIVED_FIELDS,
            }:
                raise CaptureDenied("capture denied")
            row.update({field: derived[field] for field in DATABASE_DERIVED_FIELDS})
        sources = plan["player_sources"]
        refs = [
            f"page:{item['pdf_page']}:jugador_{item['source_slot']}" for item in sources
        ]
        effect = freeze_capture_effect(
            scope=scope,
            team=team,
            players=players,
            player_source_refs=refs,
            staff=staff,
        )
        nonce = _id(proposal["capture_nonce"])
        envelope = await asyncio.to_thread(
            self._gate.authorize_effect,
            scope=scope,
            attempt_id=attempt_id,
            nonce=nonce,
            review_bytes=proposal["review_bytes"],
            effect_bytes=effect,
            signed_review=signed,
            preauthorization=preauthorization,
            issued_at=issued,
            expires_at=expires,
        )
        verify_capture_authorization(
            envelope=envelope,
            public_keys=self._gate_keys,
            scope=scope,
            attempt_id=attempt_id,
            nonce=nonce,
            review_bytes=proposal["review_bytes"],
            effect_bytes=effect,
            review_key=self._review_key,
            effect_key=self._effect_key,
            now_seconds=int(self._clock()),
        )
        await self._store.register_payload(
            account,
            session,
            attempt_id,
            effect,
            envelope,
            nonce,
            canonical(preauthorization),
            proposal["evidence_bytes"],
            signed,
        )

    async def source_page(self, account_id, session_id, proposal_id, page_index):
        account_id, session_id, proposal_id = map(
            _id, (account_id, session_id, proposal_id)
        )
        context = await self._store.load_context(account_id, session_id)
        proposal = await self._store.read_proposal(account_id, session_id, proposal_id)
        scope = parse_canonical_scope(proposal["scope_bytes"])
        if type(page_index) is not int:
            raise ReviewWorkflowDenied("SOURCE_UNAVAILABLE")
        page = next((p for p in scope.source_pages if p.page_index == page_index), None)
        if page is None:
            raise ReviewWorkflowDenied("SOURCE_UNAVAILABLE")
        with self._snapshot(context, scope, proposal_id) as snapshot:
            return snapshot.page_assets[page.asset_id]

    async def enrollment_begin(self, account_id, session_id):
        source = await self._store.resolve_source_scope(_id(session_id))
        return self._enrollment_view(
            await self._enrollment.begin(
                _id(account_id), source["tenant_id"], _id(source["edition_id"])
            )
        )

    async def enrollment_complete(
        self, account_id, session_id, enrollment_id, registration
    ):
        source = await self._store.resolve_source_scope(_id(session_id))
        pending = await self._enrollment.status(_id(account_id), _id(enrollment_id))
        self._enrollment_scope(pending, source)
        result = await self._enrollment.complete(
            _id(account_id), _id(enrollment_id), registration
        )
        self._enrollment_scope(result, source)
        return self._enrollment_view(result)

    async def enrollment_status(self, account_id, session_id, enrollment_id):
        source = await self._store.resolve_source_scope(_id(session_id))
        result = await self._enrollment.status(_id(account_id), _id(enrollment_id))
        self._enrollment_scope(result, source)
        return self._enrollment_view(result)

    @staticmethod
    def _enrollment_scope(result, source):
        if result.get("tenant_id") != source["tenant_id"] or str(
            result.get("edition_id")
        ) != str(source["edition_id"]):
            raise ReviewWorkflowDenied("SCOPE_DENIED")

    @staticmethod
    def _enrollment_view(result):
        states = {
            "AWAITING_REGISTRATION": "ready",
            "PENDING_OPERATOR_REVIEW": "pending_activation",
            "ACTIVE": "active",
            "EXPIRED": "not_enrolled",
            "REVOKED": "not_enrolled",
        }
        if (
            result.get("status") not in states
            or result.get("grants_faculty") is not False
        ):
            raise ReviewWorkflowDenied("ENROLLMENT_REQUIRED")
        return {**result, "state": states[result["status"]]}
