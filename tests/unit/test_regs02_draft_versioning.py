from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.copa_telmex.draft_versioning import (
    DraftVersionConflict,
    append_draft_version,
    draft_content_hash,
)
from devnous.copa_telmex.models import (
    RegistrationReviewDraft,
    RegistrationReviewSession,
)
from devnous.copa_telmex.registration_governance import RegistrationGovernanceDenied


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, current=None):
        self.current = current
        self.added = []
        self.executions = 0

    async def execute(self, _query):
        self.executions += 1
        return _Result(None if self.executions == 1 else self.current)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


class _Gate:
    async def authorize_draft_version(self, payload):
        return {
            "authorized": True,
            "draft_decision": {
                "decision": "AUTHORIZE_DRAFT_VERSION",
                "decision_id": "sha256:" + "d" * 64,
                "new_draft_id": payload["new_draft_id"],
                "new_content_hash": payload["new_content_hash"],
            },
            "draft_receipt": {
                "verified": True,
                "receipt_id": "sha256:" + "e" * 64,
            },
        }


@pytest.mark.asyncio
async def test_append_creates_successor_without_mutating_predecessor():
    review_session = RegistrationReviewSession(id=uuid4())
    current = RegistrationReviewDraft(
        id=uuid4(),
        session_id=review_session.id,
        draft_version=3,
        content_hash="sha256:" + "a" * 64,
        mutation_type="previous",
        mutation_operation_id="previous-operation",
        mutation_decision_id="sha256:" + "b" * 64,
        mutation_receipt_id="sha256:" + "c" * 64,
        validation={"state": "old"},
        needs_review=True,
    )
    db = _Session(current)

    successor = await append_draft_version(
        db,
        review_session,
        mutation_type="operator_edit",
        actor_id="operator-1",
        expected_draft=current,
        governance_client=_Gate(),
        validation={"state": "new"},
        needs_review=False,
    )

    assert current.validation == {"state": "old"}
    assert current.needs_review is True
    assert successor.id != current.id
    assert successor.draft_version == 4
    assert successor.predecessor_draft_id == current.id
    assert successor.predecessor_content_hash == current.content_hash
    assert successor.validation == {"state": "new"}
    assert successor.content_hash == draft_content_hash(
        {
            "ocr_raw": None,
            "extraction": None,
            "validation": {"state": "new"},
            "review_edits": None,
            "layout_regions": None,
            "overall_confidence": 0.0,
            "needs_review": False,
        }
    )
    assert db.added == [successor]


@pytest.mark.asyncio
async def test_stale_expected_version_is_rejected_before_gate_or_insert():
    review_session = RegistrationReviewSession(id=uuid4())
    expected = SimpleNamespace(
        id=uuid4(), draft_version=2, content_hash="sha256:" + "1" * 64
    )
    current = SimpleNamespace(
        id=uuid4(), draft_version=3, content_hash="sha256:" + "2" * 64
    )
    db = _Session(current)

    with pytest.raises(DraftVersionConflict):
        await append_draft_version(
            db,
            review_session,
            mutation_type="operator_edit",
            expected_draft=expected,
            governance_client=_Gate(),
            validation={},
        )
    assert db.added == []


@pytest.mark.asyncio
async def test_evidence_failure_leaves_database_without_successor():
    class _ClosedGate:
        async def authorize_draft_version(self, _payload):
            raise RegistrationGovernanceDenied(
                "EVIDENCE_WRITE_FAILED_FAIL_CLOSED", "Evidence Bus unavailable"
            )

    review_session = RegistrationReviewSession(id=uuid4())
    db = _Session()
    with pytest.raises(RegistrationGovernanceDenied):
        await append_draft_version(
            db,
            review_session,
            mutation_type="telegram_upload_created",
            governance_client=_ClosedGate(),
            extraction={"team": {"name": "A"}},
        )
    assert db.added == []
