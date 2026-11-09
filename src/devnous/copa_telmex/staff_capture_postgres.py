"""Concrete candidate store; no operational pool, keys or grants are created.

Payload admission and gate accreditation remain independent prerequisites.
The enclosing executor returns only after this store commits its transaction.
"""

from contextlib import asynccontextmanager
import hashlib
import json
import re
import time
from types import MappingProxyType
from uuid import uuid4

from .staff_capture_anchor_reader import load_locked_anchor
from .staff_capture_authorization import verify_capture_authorization
from .staff_capture_executor import CaptureDenied, CaptureResult
from .staff_capture_persistence import PendingCaptureWriter
from .staff_capture_finalizing_writer import FinalizingCaptureWriter
from .staff_review_challenge import parse_canonical_scope
from .staff_signed_assertion import PLATFORM_POLICY_HASH, PLATFORM_PROFILE


class PostgresCaptureStore:
    def __init__(
        self,
        pool,
        source_adapter_factory,
        *,
        public_keys,
        review_key,
        effect_key,
        photo_store=None,
        finality_service=None,
        receipt_public_keys=None,
        assertion_public_keys=None,
        database_identity=None,
    ):
        self._pool, self._source_factory = pool, source_adapter_factory
        if not callable(source_adapter_factory):
            raise CaptureDenied("capture denied")
        self._keys = MappingProxyType(dict(public_keys))
        self._review_key, self._effect_key = review_key, effect_key
        self._final_writer = None
        self._writer = PendingCaptureWriter(
            public_keys=self._keys,
            review_key=review_key,
            effect_key=effect_key,
            photo_store=photo_store,
        )
        if finality_service is not None:
            self._final_writer = FinalizingCaptureWriter(
                public_keys=self._keys,
                review_key=review_key,
                effect_key=effect_key,
                photo_store=photo_store,
                finality_service=finality_service,
                receipt_public_keys=receipt_public_keys,
                assertion_public_keys=assertion_public_keys,
                database_identity=database_identity,
            )
        else:
            if any(
                value is not None
                for value in (
                    receipt_public_keys,
                    assertion_public_keys,
                    database_identity,
                )
            ):
                raise CaptureDenied("capture denied")

    @asynccontextmanager
    async def transaction(self):
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                yield _CaptureTransaction(connection, self)


class _CaptureTransaction:
    def __init__(self, connection, store):
        self._conn, self._store = connection, store
        self._attempt = self._payload = self._decision = None

    async def assert_prepared_transactions_disabled(self):
        if await self._conn.fetchval("SHOW max_prepared_transactions") != "0":
            raise CaptureDenied("capture denied")

    async def lock_attempt(self, attempt_id):
        self._attempt = await load_locked_anchor(self._conn, attempt_id)
        self._payload = await self._conn.fetchrow(
            "SELECT * FROM ctt_staff_private.load_capture_payload($1)",
            attempt_id,
        )
        if self._payload is None:
            raise CaptureDenied("capture denied")
        scope = parse_canonical_scope(self._attempt.scope_bytes)
        if json.loads(self._payload["effect_bytes"])["team"]["id"] != str(
            scope.team_id
        ):
            raise CaptureDenied("capture denied")
        return self._attempt

    async def completed_result(self, attempt_id):
        result = await self._conn.fetchval(
            "SELECT ctt_staff_private.get_capture_result($1)", attempt_id
        )
        return CaptureResult(attempt_id, result) if result is not None else None

    def _authorization(self):
        if self._attempt is None or self._payload is None:
            raise CaptureDenied("capture denied")
        return dict(
            envelope=self._payload["gate_envelope"],
            scope=parse_canonical_scope(self._attempt.scope_bytes),
            attempt_id=self._attempt.attempt_id,
            nonce=self._payload["nonce"],
            review_bytes=self._payload["review_bytes"],
            effect_bytes=self._payload["effect_bytes"],
        )

    async def validate_decision(self, decision, attempt):
        payload = self._payload
        if payload is None:
            raise CaptureDenied("capture denied")
        if attempt != self._attempt or (
            decision.attempt_id != attempt.attempt_id
            or decision.challenge_id != attempt.challenge_id
            or decision.principal_id != attempt.principal_id
            or decision.credential_id != attempt.credential_id
            or decision.scope_hash != attempt.scope_hash
            or decision.expires_at != payload["expires_at_seconds"]
            or not isinstance(decision.proof_sha256, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", decision.proof_sha256) is None
        ):
            raise CaptureDenied("capture denied")
        verify_capture_authorization(
            **self._authorization(),
            public_keys=self._store._keys,
            review_key=self._store._review_key,
            effect_key=self._store._effect_key,
            now_seconds=int(time.time()),
        )
        # Verify the envelope before reading its claims. Both expiration
        # checks must share the same precommit SQL guard, not just the
        # earlier application-clock verification.
        gate_expiry = json.loads(payload["gate_envelope"])["body"]["expires_at"]
        if (
            gate_expiry != payload["expires_at_seconds"]
            or gate_expiry != decision.expires_at
        ):
            raise CaptureDenied("capture denied")
        self._decision = decision

    def open_source(self, attempt):
        if attempt != self._attempt or self._decision is None:
            raise CaptureDenied("capture denied")
        return self._store._source_factory(attempt).open_verified_snapshot(attempt)

    async def consume(self, decision):
        if decision != self._decision:
            raise CaptureDenied("capture denied")
        arguments = (
            decision.challenge_id,
            decision.principal_id,
            decision.credential_id,
            decision.scope_hash,
            decision.prior_counter,
            decision.new_counter,
        )
        if decision.auth_profile == "device-counter-v1":
            if (
                decision.auth_policy_hash is not None
                or decision.backup_eligible is not False
                or decision.backup_state is not False
            ):
                raise CaptureDenied("capture denied")
            consumed = await self._conn.fetchval(
                "SELECT ctt_staff_private.consume_review_challenge("
                "$1,$2,$3,$4,$5,$6)",
                *arguments,
            )
        elif decision.auth_profile == PLATFORM_PROFILE:
            if (
                decision.auth_policy_hash != PLATFORM_POLICY_HASH
                or type(decision.backup_eligible) is not bool
                or type(decision.backup_state) is not bool
                or (decision.backup_state and not decision.backup_eligible)
            ):
                raise CaptureDenied("capture denied")
            consumed = await self._conn.fetchval(
                "SELECT ctt_staff_private.consume_review_challenge_v2("
                "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                *arguments,
                decision.auth_profile,
                decision.backup_eligible,
                decision.backup_state,
                decision.auth_policy_hash,
            )
        else:
            raise CaptureDenied("capture denied")
        if consumed is not True:
            raise CaptureDenied("capture denied")
        await self._conn.execute(
            "SELECT ctt_staff_private.record_signed_assertion_guard($1,$2,$3)",
            decision.challenge_id,
            decision.proof_sha256,
            decision.expires_at,
        )

    async def capture(self, attempt, source):
        if attempt != self._attempt or self._decision is None:
            raise CaptureDenied("capture denied")
        source.verify_unchanged()
        if self._store._final_writer is not None:
            signed_review = self._decision.proof_bytes
            if (
                type(signed_review) is not bytes
                or not signed_review
                or "sha256:" + hashlib.sha256(signed_review).hexdigest()
                != self._decision.proof_sha256
            ):
                raise CaptureDenied("capture denied")
            tokens = await self._store._final_writer.persist_and_finalize(
                self._conn,
                source=source,
                signed_review=signed_review,
                **self._authorization(),
            )
            token = tokens.pending_authorization_hash
        else:
            token = await self._store._writer.persist(
                self._conn, **self._authorization()
            )
        source.verify_unchanged()
        result_id = uuid4()
        await self._conn.execute(
            "SELECT ctt_staff_private.record_capture_result($1,$2,$3)",
            attempt.attempt_id,
            result_id,
            token,
        )
        return CaptureResult(attempt.attempt_id, result_id)
