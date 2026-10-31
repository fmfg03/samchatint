"""Contract tests for the non-operational transaction coordinator."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.copa_telmex.staff_capture_executor import (
    CaptureDenied,
    CaptureResult,
    LockedAttempt,
    StaffCaptureExecutor,
    VerifiedDecision,
)


class Verifier:
    def __init__(self, decision):
        self.decision = decision

    def verify(self, signed):
        if signed != b"valid":
            raise CaptureDenied("capture denied")
        return self.decision


class Store:
    def __init__(self, attempt):
        self.attempt = attempt
        self.lock = asyncio.Lock()
        self.result = None
        self.consumed = 0
        self.captured = 0
        self.fail_capture = False
        self.fail_source = False
        self.fail_decision = False
        self.fail_commit = False
        self.prepared_disabled = True

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            old = self.result, self.consumed, self.captured
            try:
                yield self
                if self.fail_commit:
                    raise RuntimeError("commit failed")
            except BaseException:
                self.result, self.consumed, self.captured = old
                raise

    async def assert_prepared_transactions_disabled(self):
        if not self.prepared_disabled:
            raise CaptureDenied("capture denied")

    async def lock_attempt(self, attempt_id):
        return self.attempt

    async def completed_result(self, attempt_id):
        return self.result

    async def validate_decision(self, decision, attempt):
        if self.fail_decision:
            raise CaptureDenied("capture denied")

    @contextmanager
    def open_source(self, attempt):
        if self.fail_source:
            raise CaptureDenied("capture denied")
        yield SimpleNamespace(verify_unchanged=lambda: None)

    async def consume(self, decision):
        self.consumed += 1

    async def capture(self, attempt, source):
        assert callable(source.verify_unchanged)
        self.captured += 1
        if self.fail_capture:
            raise RuntimeError("capture failed")
        self.result = CaptureResult(attempt.attempt_id, uuid4())
        return self.result


def fixture():
    attempt = LockedAttempt(uuid4(), uuid4(), uuid4(), b"key", "scope")
    decision = VerifiedDecision(
        attempt.attempt_id,
        attempt.challenge_id,
        attempt.principal_id,
        attempt.credential_id,
        attempt.scope_hash,
        4,
        5,
    )
    store = Store(attempt)
    return attempt, store, StaffCaptureExecutor(store, Verifier(decision))


def test_success_and_concurrent_retries_are_single_effect():
    async def run():
        attempt, store, executor = fixture()
        results = await asyncio.gather(
            *(executor.execute(attempt.attempt_id, b"valid") for _ in range(8))
        )
        assert len(set(results)) == 1
        assert (store.consumed, store.captured) == (1, 1)

    asyncio.run(run())


@pytest.mark.parametrize(
    "field",
    [
        "attempt_id",
        "challenge_id",
        "principal_id",
        "credential_id",
        "scope_hash",
    ],
)
def test_changed_protected_binding_is_denied(field):
    async def run():
        attempt, store, executor = fixture()
        changed = {
            "attempt_id": uuid4(),
            "challenge_id": uuid4(),
            "principal_id": uuid4(),
            "credential_id": b"other",
            "scope_hash": "other",
        }
        store.attempt = replace(attempt, **{field: changed[field]})
        with pytest.raises(CaptureDenied):
            await executor.execute(attempt.attempt_id, b"valid")
        assert (store.consumed, store.captured) == (0, 0)

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["capture", "commit", "source", "decision", "prepared"]
)
def test_failure_rolls_back_and_retry_works(failure):
    async def run():
        attempt, store, executor = fixture()
        if failure == "prepared":
            store.prepared_disabled = False
        else:
            setattr(store, "fail_" + failure, True)
        with pytest.raises(CaptureDenied):
            await executor.execute(attempt.attempt_id, b"valid")
        assert store.result is None
        assert (store.consumed, store.captured) == (0, 0)
        store.fail_capture = store.fail_commit = store.fail_source = False
        store.fail_decision = False
        store.prepared_disabled = True
        assert (
            await executor.execute(attempt.attempt_id, b"valid")
        ).attempt_id == attempt.attempt_id

    asyncio.run(run())


def test_invalid_signature_does_not_enter_transaction():
    async def run():
        attempt, store, executor = fixture()
        with pytest.raises(CaptureDenied):
            await executor.execute(attempt.attempt_id, b"invalid")
        assert (store.consumed, store.captured) == (0, 0)

    asyncio.run(run())
