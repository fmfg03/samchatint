from datetime import datetime, timezone

import pytest

from samchat.assistant.analyst_case import (
    CASE_STATUS_CLOSED,
    CASE_STATUS_REVIEWED,
    build_analyst_case,
)
from samchat.assistant.analyst_case_store import (
    AnalystCaseStoreError,
    deterministic_changed_fields,
    validate_case_actor_requirements,
)
from samchat.assistant.analyst_intent import detect_analyst_intent
from samchat.assistant.analyst_workbench import (
    AnalystEvidence,
    run_analyst_workbench,
)


CREATED_AT = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


class FakeAnalystCaseStore:
    def __init__(self):
        self.cases = {}

    def create_case(self, case):
        self.cases[case.case_id] = case
        return case

    def get_case(self, case_id):
        return self.cases.get(case_id)


@pytest.mark.asyncio
async def test_fake_store_can_save_and_resume_case_without_external_writes():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    result = await run_analyst_workbench(
        intent=intent,
        evidence=[
            AnalystEvidence(
                source_type="uploaded_file",
                label="contrato.pdf",
                summary="Contrato con responsable faltante.",
            )
        ],
    )
    case = build_analyst_case(
        user_id="emp-1",
        role="direccion",
        question="Qué riesgos ves en este contrato",
        intent=intent,
        result=result,
        created_at=CREATED_AT,
    )

    store = FakeAnalystCaseStore()
    store.create_case(case)
    resumed = store.get_case(case.case_id)

    assert resumed == case
    assert resumed.writes_policy["operational_writes_allowed"] is False
    assert resumed.versions[0].changed_fields == ["case_created"]


def test_changed_fields_are_deterministic_and_sorted():
    first = {
        "status": "open",
        "current_answer": "a",
        "next_questions": ["q1"],
    }
    second = {
        "status": "reviewed",
        "current_answer": "b",
        "next_questions": ["q1"],
    }

    assert deterministic_changed_fields(first, second) == [
        "current_answer",
        "status",
    ]


def test_reviewed_requires_updated_by():
    with pytest.raises(AnalystCaseStoreError, match="updated_by"):
        validate_case_actor_requirements(
            status=CASE_STATUS_REVIEWED,
            updated_by=None,
            closed_by=None,
        )


def test_closed_requires_closed_by():
    with pytest.raises(AnalystCaseStoreError, match="closed_by"):
        validate_case_actor_requirements(
            status=CASE_STATUS_CLOSED,
            updated_by="reviewer-1",
            closed_by=None,
        )
