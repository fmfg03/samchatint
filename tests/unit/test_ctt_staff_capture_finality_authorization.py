"""Malformed capabilities fail closed before any persistence operation."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_finality_authorization import (
    verify_capture_finality_authorization,
)


@pytest.mark.parametrize(
    "envelope",
    [
        b"{}",
        b'{"body":{},"signature":""}',
        b'{"body":{},"body":{},"signature":""}',
        b'{"body":[],"signature":""}',
        b"not-json",
    ],
)
def test_malformed_finality_capability_is_generic_and_never_returns_a_token(envelope):
    attempt, nonce = uuid4(), uuid4()
    with pytest.raises(CaptureDenied) as denied:
        verify_capture_finality_authorization(
            envelope=envelope,
            public_keys={},
            scope=None,
            protected_context=SimpleNamespace(review_attempt_id=attempt, nonce=nonce),
            attempt_id=attempt,
            nonce=nonce,
            review_bytes=b"PRIVATE_SENTINEL",
            pending_effect_bytes=b"PRIVATE_SENTINEL",
            finality_effect_bytes=b"PRIVATE_SENTINEL",
            signed_review=b"PRIVATE_SENTINEL",
            preauthorization={},
            verified_finalities=None,
            review_key=b"r" * 32,
            effect_key=b"e" * 32,
            receipt_public_keys={},
            assertion_public_keys={},
            now_seconds=1,
        )
    assert str(denied.value) == "capture finality denied"
    assert denied.value.__suppress_context__ is True
