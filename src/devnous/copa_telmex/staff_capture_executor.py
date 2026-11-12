"""Candidate transaction coordinator for reviewed staff capture.

Private store and transport adapters are available for isolated verification.
Their protected admissions, identity accreditation and operational isolation
must be established before activation. A browser or application database role
must not supply the store. This module grants no privileges or endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncContextManager, ContextManager, Protocol
from uuid import UUID

from .staff_capture_edition import TrustedEditionBinding

if TYPE_CHECKING:
    from .staff_capture_source_adapter import SourceSnapshot


class CaptureDenied(Exception):
    """The protected attempt or decision was not accepted."""


@dataclass(frozen=True)
class VerifiedDecision:
    """Authenticated decision; never constructed by HTTP."""

    attempt_id: UUID
    challenge_id: UUID
    principal_id: UUID
    credential_id: bytes = field(repr=False)
    scope_hash: str
    prior_counter: int
    new_counter: int
    expires_at: int | None = None
    proof_sha256: str | None = field(default=None, repr=False)
    auth_profile: str = "device-counter-v1"
    auth_policy_hash: str | None = None
    backup_eligible: bool = False
    backup_state: bool = False
    proof_bytes: bytes = field(default=b"", repr=False, compare=False)


@dataclass(frozen=True)
class LockedAttempt:
    """Protected attempt loaded and locked within the writer transaction."""

    attempt_id: UUID
    challenge_id: UUID
    principal_id: UUID
    credential_id: bytes = field(repr=False)
    scope_hash: str
    scope_bytes: bytes = field(default=b"", repr=False)
    edition_binding: TrustedEditionBinding | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CaptureResult:
    """Opaque result of an atomic, idempotent capture."""

    attempt_id: UUID
    result_id: UUID


class DecisionVerifier(Protocol):
    def verify(self, signed_decision: bytes) -> VerifiedDecision:
        """Verify the complete signed gate decision and return its binding."""
        ...


class CaptureTransaction(Protocol):
    async def assert_prepared_transactions_disabled(self) -> None:
        """Fail unless the server disallows prepared transactions."""
        ...

    async def lock_attempt(self, attempt_id: UUID) -> LockedAttempt:
        """Lock protected attempt, draft, sources, policy and authority."""
        ...

    async def completed_result(self, attempt_id: UUID) -> CaptureResult | None:
        """Return the durable result for the locked attempt, if any."""
        ...

    async def validate_decision(
        self, decision: VerifiedDecision, attempt: LockedAttempt
    ) -> None:
        """Revalidate expiry, reviewer authority and receipts under locks."""
        ...

    def open_source(self, attempt: LockedAttempt) -> ContextManager[SourceSnapshot]:
        """Retain verified private sources throughout capture."""
        ...

    async def consume(self, decision: VerifiedDecision) -> None:
        """Consume on this connection under authority locks."""
        ...

    async def capture(
        self, attempt: LockedAttempt, source: SourceSnapshot
    ) -> CaptureResult:
        """Write the exact protected effect and its idempotency result."""
        ...


class CaptureStore(Protocol):
    def transaction(self) -> AsyncContextManager[CaptureTransaction]:
        """Open one trusted connection with deferred checks at commit."""
        ...


class StaffCaptureExecutor:
    """Coordinate a single effect within a trusted, configured transaction.

    The store is part of the trusted executor and must not allow arbitrary SQL
    or advance deferred constraints. Its context commits only on normal exit.
    All revalidation and writes must happen inside its single transaction.
    """

    def __init__(self, store: CaptureStore, verifier: DecisionVerifier):
        self._store = store
        self._verifier = verifier

    async def execute(self, attempt_id: UUID, signed_decision: bytes) -> CaptureResult:
        try:
            return await self._execute(attempt_id, signed_decision)
        except Exception:
            raise CaptureDenied("capture denied") from None

    async def _execute(self, attempt_id: UUID, signed_decision: bytes) -> CaptureResult:
        if not isinstance(attempt_id, UUID) or not isinstance(signed_decision, bytes):
            raise CaptureDenied("capture denied")
        if not signed_decision or len(signed_decision) > 65536:
            raise CaptureDenied("capture denied")
        decision = self._verifier.verify(signed_decision)
        from .staff_batch_authorization import VerifiedBatchDecision

        if type(decision) is VerifiedBatchDecision:
            return await self._execute_batch(attempt_id, decision)
        if (
            not isinstance(decision, VerifiedDecision)
            or decision.attempt_id != attempt_id
        ):
            raise CaptureDenied("capture denied")
        async with self._store.transaction() as tx:
            await tx.assert_prepared_transactions_disabled()
            attempt = await tx.lock_attempt(attempt_id)
            if (
                not isinstance(attempt, LockedAttempt)
                or attempt.attempt_id != decision.attempt_id
                or attempt.challenge_id != decision.challenge_id
                or attempt.principal_id != decision.principal_id
                or attempt.credential_id != decision.credential_id
                or attempt.scope_hash != decision.scope_hash
            ):
                raise CaptureDenied("capture denied")
            existing = await tx.completed_result(attempt_id)
            if existing is not None:
                if (
                    not isinstance(existing, CaptureResult)
                    or existing.attempt_id != attempt_id
                ):
                    raise CaptureDenied("capture denied")
                return existing
            await tx.validate_decision(decision, attempt)
            with tx.open_source(attempt) as source:
                source.verify_unchanged()
                await tx.consume(decision)
                result = await tx.capture(attempt, source)
                source.verify_unchanged()
                if (
                    not isinstance(result, CaptureResult)
                    or result.attempt_id != attempt_id
                ):
                    raise CaptureDenied("capture denied")
                return result

    async def _execute_batch(self, attempt_id, decision):
        from .staff_batch_authorization import VerifiedBatchDecision

        if (
            type(decision) is not VerifiedBatchDecision
            or decision.attempt_id != attempt_id
        ):
            raise CaptureDenied("capture denied")
        async with self._store.transaction() as tx:
            await tx.assert_prepared_transactions_disabled()
            attempt = await tx.lock_batch_attempt(decision)
            existing = await tx.completed_result(attempt_id)
            if existing is not None:
                if (
                    type(existing) is not CaptureResult
                    or existing.attempt_id != attempt_id
                ):
                    raise CaptureDenied("capture denied")
                return existing
            await tx.validate_batch_decision(decision, attempt)
            with tx.open_batch_source(attempt) as source:
                source.verify_unchanged()
                await tx.consume_batch(decision)
                result = await tx.capture_batch(attempt, source)
                source.verify_unchanged()
                if type(result) is not CaptureResult or result.attempt_id != attempt_id:
                    raise CaptureDenied("capture denied")
                return result
