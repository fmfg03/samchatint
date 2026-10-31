"""Finality transaction domains and retained-source context have no defaults."""

from dataclasses import replace
from io import BytesIO
import time
from uuid import uuid4

import pytest

from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_finality_effect import (
    FinalityContext,
    bind_finality_effect,
    canonical,
    database_transaction_id,
    finality_attempt_id,
    parse,
)
from devnous.copa_telmex.staff_capture_source_adapter import SourceSnapshot
from devnous.copa_telmex.staff_review_challenge import canonical_scope_bytes
from test_ctt_staff_capture_source_adapter import candidate


def test_transaction_identity_never_reuses_another_database_or_transaction():
    identities = {
        database_transaction_id("isolated-a", 16384, 500),
        database_transaction_id("isolated-b", 16384, 500),
        database_transaction_id("isolated-a", 16385, 500),
        database_transaction_id("isolated-a", 16384, 501),
    }
    assert len(identities) == 4
    attempt = uuid4()
    assert len({finality_attempt_id(attempt, value) for value in identities}) == 4
    transaction = identities.pop()
    assert finality_attempt_id(attempt, transaction) != finality_attempt_id(
        uuid4(), transaction
    )


@pytest.mark.parametrize(
    "identity,oid,xid",
    [
        ("", 1, 1),
        ("../database", 1, 1),
        ("a\0b", 1, 1),
        ("valid", True, 1),
        ("valid", 2**32, 1),
        ("valid", 1, 0),
        ("valid", 1, True),
        ("valid", 1, 2**64),
    ],
)
def test_untrusted_database_identifiers_cannot_form_a_context(identity, oid, xid):
    with pytest.raises(CaptureDenied):
        database_transaction_id(identity, oid, xid)


@pytest.mark.parametrize(
    "raw",
    [b'{"x":1,"x":2}', b'{ "x":1}', b'{"x":NaN}', b"[]", b"null", b"\xff", b'{"x":'],
)
def test_noncanonical_or_ambiguous_private_json_is_rejected(raw):
    with pytest.raises(CaptureDenied):
        parse(raw)


def test_domain_binding_and_key_validation():
    raw = canonical({"private": "synthetic"})
    assert parse(raw) == {"private": "synthetic"}
    assert bind_finality_effect(raw, b"k" * 32) != bind_finality_effect(raw, b"j" * 32)
    for key in (b"short", None, "k" * 32):
        with pytest.raises(CaptureDenied):
            bind_finality_effect(raw, key)


def test_context_requires_intact_retained_source_and_explicit_cas_and_incidents():
    locked, scope, manifest, pdf, images = candidate()
    snapshot = SourceSnapshot(
        canonical(manifest),
        BytesIO(pdf),
        {page.asset_id: image for page, image in zip(scope.source_pages, images)},
        canonical_scope_bytes(scope),
        1_000_000,
    )
    now = int(time.time())
    context = FinalityContext(
        "isolated",
        16384,
        500,
        locked.attempt_id,
        now,
        now + 60,
        uuid4(),
        snapshot,
        0,
        True,
    )
    context.verify(scope, locked.attempt_id)
    for changed in (
        replace(context, cas_succeeded=False),
        replace(context, blocking_incident_count=True),
        replace(context, blocking_incident_count=1),
        replace(context, expires_at=now),
        replace(context, issued_at=now + 2),
        replace(context, nonce="client-nonce"),
    ):
        with pytest.raises(CaptureDenied):
            changed.verify(scope, locked.attempt_id)
    with pytest.raises(CaptureDenied):
        context.verify(scope, uuid4())
    snapshot.pdf.seek(0)
    snapshot.pdf.write(b"corrupt")
    with pytest.raises(CaptureDenied):
        context.verify(scope, locked.attempt_id)
    snapshot.pdf.close()
