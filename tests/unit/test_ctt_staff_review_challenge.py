"""Synthetic prechecks: success is never approval or single-use consumption."""

from dataclasses import replace
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from devnous.copa_telmex.staff_review_challenge import (
    ChallengeCandidate,
    ChallengePrecheckError,
    ReviewScope,
    SourcePage,
    canonical_scope_bytes,
    create_challenge_candidate,
    scope_hash,
    precheck_challenge,
    parse_canonical_scope,
)

SHA = "sha256:" + "a" * 64
MAC = "hmac-sha256:" + "b" * 64
NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


@pytest.fixture
def scope():
    return ReviewScope(
        tenant_id="sample-tenant",
        tournament_slug="sample-edition",
        edition_id=uuid4(),
        session_id=uuid4(),
        draft_id=uuid4(),
        draft_version=3,
        draft_content_hash=SHA,
        team_id=uuid4(),
        manifest_sha256="a" * 64,
        document_id="b" * 64,
        pdf_sha256="c" * 64,
        source_pages=(
            SourcePage(1, uuid4(), SHA, 13),
            SourcePage(2, uuid4(), SHA, 14),
        ),
        actor_id=uuid4(),
        actor_role="coordinador",
        authority_binding=MAC,
        staff_and_changes_binding=MAC,
        decision_hash=SHA,
        review_receipt_id=SHA,
        policy_hash=SHA,
        operation_id=SHA,
    )


def test_random_challenge_and_five_minute_boundary(scope):
    first = create_challenge_candidate(scope, issued_at=NOW)
    second = create_challenge_candidate(scope, issued_at=NOW)
    assert len(first.challenge) == len(second.challenge) == 32
    assert first.challenge != second.challenge
    assert first.expires_at == NOW + timedelta(minutes=5)
    assert (
        precheck_challenge(
            first,
            presented_challenge=first.challenge,
            current_scope=scope,
            now=first.expires_at - timedelta(microseconds=1),
        ).identity_verified
        is False
    )
    with pytest.raises(ChallengePrecheckError):
        precheck_challenge(
            first,
            presented_challenge=first.challenge,
            current_scope=scope,
            now=first.expires_at,
        )


def test_no_replay_or_authority_is_claimed(scope):
    candidate = create_challenge_candidate(scope, issued_at=NOW)
    for _ in range(2):
        result = precheck_challenge(
            candidate,
            presented_challenge=candidate.challenge,
            current_scope=scope,
            now=NOW,
        )
        assert result.identity_verified is False
        assert result.replay_protection_verified is False
        assert result.materialization_authorized is False


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "other-tenant"},
        {"tournament_slug": "other-edition"},
        {"edition_id": uuid4()},
        {"session_id": uuid4()},
        {"draft_id": uuid4()},
        {"draft_version": 4},
        {"draft_content_hash": "sha256:" + "c" * 64},
        {"team_id": uuid4()},
        {"manifest_sha256": "d" * 64},
        {"document_id": "e" * 64},
        {"pdf_sha256": "f" * 64},
        {"actor_id": uuid4()},
        {"actor_role": "admin"},
        {"authority_binding": "hmac-sha256:" + "c" * 64},
        {"staff_and_changes_binding": "hmac-sha256:" + "d" * 64},
        {"decision_hash": "sha256:" + "d" * 64},
        {"review_receipt_id": "sha256:" + "9" * 64},
        {"policy_hash": "sha256:" + "e" * 64},
        {"operation_id": "sha256:" + "f" * 64},
        {"source_pages": (SourcePage(1, uuid4(), SHA, 13),)},
    ],
)
def test_each_server_scope_change_closes_candidate(scope, changes):
    candidate = create_challenge_candidate(scope, issued_at=NOW)
    with pytest.raises(ChallengePrecheckError):
        precheck_challenge(
            candidate,
            presented_challenge=candidate.challenge,
            current_scope=replace(scope, **changes),
            now=NOW,
        )


@pytest.mark.parametrize("wrong", [b"", b"x" * 31, b"x" * 32, b"x" * 33, None])
def test_wrong_or_malformed_challenge_closes(scope, wrong):
    candidate = create_challenge_candidate(scope, issued_at=NOW)
    with pytest.raises(ChallengePrecheckError):
        precheck_challenge(
            candidate, presented_challenge=wrong, current_scope=scope, now=NOW
        )


def test_scope_duplicate_sources_and_invalid_bindings_fail(scope):
    page = scope.source_pages[0]
    bad_sources = [
        (),
        (page, page),
        (page, SourcePage(2, page.asset_id, SHA, 14)),
        (SourcePage(2, uuid4(), SHA, 14), SourcePage(1, uuid4(), SHA, 13)),
        [page],
    ]
    for pages in bad_sources:
        with pytest.raises(ChallengePrecheckError):
            replace(scope, source_pages=pages)
    for changes in (
        {"draft_version": True},
        {"manifest_sha256": ""},
        {"document_id": None},
        {"pdf_sha256": "bad"},
        {"draft_content_hash": "invalid"},
        {"staff_and_changes_binding": SHA},
        {"review_receipt_id": None},
        {"review_receipt_id": ""},
        {"authority_binding": ""},
        {"actor_role": ""},
        {"purpose": "other"},
    ):
        with pytest.raises(ChallengePrecheckError):
            replace(scope, **changes)


def test_naive_future_and_overlong_times_rejected(scope):
    with pytest.raises(ChallengePrecheckError):
        create_challenge_candidate(scope, issued_at=NOW.replace(tzinfo=None))
    with pytest.raises(ChallengePrecheckError):
        ChallengeCandidate(scope, b"x" * 32, NOW, NOW + timedelta(minutes=6))
    candidate = create_challenge_candidate(scope, issued_at=NOW)
    with pytest.raises(ChallengePrecheckError):
        precheck_challenge(
            candidate,
            presented_challenge=candidate.challenge,
            current_scope=scope,
            now=NOW - timedelta(microseconds=1),
        )


def test_repr_and_error_do_not_reveal_inputs(scope):
    candidate = create_challenge_candidate(scope, issued_at=NOW)
    text = repr(candidate) + repr(scope) + repr(scope.source_pages[0])
    assert str(scope.actor_id) not in text
    assert str(scope.draft_id) not in text
    assert candidate.challenge.hex() not in text
    with pytest.raises(ChallengePrecheckError) as error:
        precheck_challenge(
            candidate,
            presented_challenge=b"private sentinel",
            current_scope=scope,
            now=NOW,
        )
    assert "private sentinel" not in str(error.value)
    assert str(scope.actor_id) not in str(error.value)


def test_canonical_scope_hash_covers_each_binding(scope):
    canonical = canonical_scope_bytes(scope)
    decoded = json.loads(canonical)
    assert decoded["schema"] == "ctt.staff-review.challenge-scope.v1"
    assert canonical == json.dumps(
        decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert scope_hash(scope) == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert scope_hash(scope) == scope_hash(replace(scope))
    changes = {
        "tenant_id": "other",
        "tournament_slug": "other",
        "edition_id": uuid4(),
        "session_id": uuid4(),
        "draft_id": uuid4(),
        "draft_version": 4,
        "draft_content_hash": "sha256:" + "1" * 64,
        "team_id": uuid4(),
        "manifest_sha256": "1" * 64,
        "document_id": "2" * 64,
        "pdf_sha256": "3" * 64,
        "actor_id": uuid4(),
        "actor_role": "other",
        "authority_binding": "hmac-sha256:" + "1" * 64,
        "staff_and_changes_binding": "hmac-sha256:" + "2" * 64,
        "decision_hash": "sha256:" + "2" * 64,
        "review_receipt_id": "sha256:" + "3" * 64,
        "policy_hash": "sha256:" + "4" * 64,
        "operation_id": "sha256:" + "5" * 64,
    }
    for key, value in changes.items():
        assert scope_hash(replace(scope, **{key: value})) != scope_hash(scope), key
    first = scope.source_pages[0]
    for page in (
        replace(first, page_index=2),
        replace(first, asset_id=uuid4()),
        replace(first, image_sha256="sha256:" + "5" * 64),
        replace(first, pdf_page=12),
    ):
        assert scope_hash(replace(scope, source_pages=(page,))) != scope_hash(
            replace(scope, source_pages=(first,))
        )
    assert scope_hash(replace(scope, source_pages=(first,))) != scope_hash(scope)


def test_pdf_physical_page_changes_and_invalid_pages_fail(scope):
    first, second = scope.source_pages
    candidate = create_challenge_candidate(scope, issued_at=NOW)
    changed = replace(scope, source_pages=(replace(first, pdf_page=12), second))
    with pytest.raises(ChallengePrecheckError):
        precheck_challenge(
            candidate,
            presented_challenge=candidate.challenge,
            current_scope=changed,
            now=NOW,
        )
    for value in (0, -1, True, None):
        with pytest.raises(ChallengePrecheckError):
            replace(first, pdf_page=value)
    with pytest.raises(ChallengePrecheckError):
        replace(
            scope,
            source_pages=(first, replace(second, pdf_page=first.pdf_page)),
        )
    with pytest.raises(ChallengePrecheckError):
        replace(scope, source_pages=(replace(first, pdf_page=15), second))


def test_canonical_scope_golden_vector(scope):
    synthetic_id = UUID("00000000-0000-0000-0000-000000000001")
    fixed = replace(
        scope,
        edition_id=synthetic_id,
        session_id=synthetic_id,
        draft_id=synthetic_id,
        team_id=synthetic_id,
        actor_id=synthetic_id,
        source_pages=(SourcePage(1, synthetic_id, SHA, 13),),
    )
    assert scope_hash(fixed) == (
        "sha256:d7d1ca327d7d732d220587ca9b61337fc7" "c2f9085618e056e6deb41e777c6063"
    )


def test_prereceipt_scope_has_no_future_authority_and_roundtrips(scope):
    pending = replace(
        scope, decision_hash=None, review_receipt_id=None, field_evidence_binding=MAC
    )
    raw = canonical_scope_bytes(pending)
    decoded = json.loads(raw)
    assert decoded["schema"] == "ctt.staff-review.challenge-scope.v2"
    assert "decision_hash" not in decoded
    assert "review_receipt_id" not in decoded
    assert decoded["operation_id"] == pending.operation_id
    assert parse_canonical_scope(raw) == pending
    candidate = create_challenge_candidate(pending, issued_at=NOW)
    assert (
        precheck_challenge(
            candidate,
            presented_challenge=candidate.challenge,
            current_scope=pending,
            now=NOW,
        ).materialization_authorized
        is False
    )


@pytest.mark.parametrize("field", ["decision_hash", "review_receipt_id"])
@pytest.mark.parametrize("value", [None, SHA])
def test_prereceipt_scope_rejects_even_null_future_keys(scope, field, value):
    pending = replace(
        scope, decision_hash=None, review_receipt_id=None, field_evidence_binding=MAC
    )
    body = json.loads(canonical_scope_bytes(pending))
    body[field] = value
    raw = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    with pytest.raises(ChallengePrecheckError):
        parse_canonical_scope(raw)


def test_prereceipt_scope_does_not_reinterpret_existing_v1(scope):
    pending = replace(
        scope, decision_hash=None, review_receipt_id=None, field_evidence_binding=MAC
    )
    body = json.loads(canonical_scope_bytes(pending))
    body["schema"] = "ctt.staff-review.challenge-scope.v1"
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ChallengePrecheckError):
        parse_canonical_scope(raw)
    assert scope_hash(pending) != scope_hash(scope)
    for field in ("decision_hash", "review_receipt_id"):
        with pytest.raises(ChallengePrecheckError):
            replace(scope, **{field: None})
