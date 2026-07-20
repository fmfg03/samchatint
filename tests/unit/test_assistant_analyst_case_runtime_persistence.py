from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Tuple

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from devnous.copa_telmex.models import Base
import samchat.assistant.analyst_case_persistence as case_persistence
from samchat.assistant.analyst_case import (
    CASE_STATUS_CLOSED,
    CASE_STATUS_REVIEWED,
)
from samchat.assistant.analyst_case_models import (
    AnalystCaseRecord,
    AnalystCaseVersionRecord,
)
from samchat.assistant.analyst_case_store import AnalystCaseStore
from samchat.assistant.analyst_case_persistence import (
    analyst_case_persistence_enabled,
    persist_analyst_case,
)
from samchat.assistant.analyst_intent import (
    AnalystIntent,
    detect_analyst_intent,
)
from samchat.assistant.analyst_workbench import (
    AnalystEvidence,
    AnalystWorkbenchResult,
    run_analyst_workbench,
)


class _AsyncNestedTransaction:
    def __init__(self, sync_session: Session) -> None:
        self.sync_session = sync_session
        self.transaction: Any = None

    async def __aenter__(self) -> Any:
        self.transaction = self.sync_session.begin_nested()
        return self.transaction

    async def __aexit__(
        self,
        exc_type: Any,
        _exc: Any,
        _traceback: Any,
    ) -> bool:
        if exc_type is None:
            self.transaction.commit()
        else:
            self.transaction.rollback()
        return False


class _AsyncSessionAdapter:
    def __init__(self, sync_session: Session) -> None:
        self.sync_session = sync_session

    def begin_nested(self) -> _AsyncNestedTransaction:
        return _AsyncNestedTransaction(self.sync_session)

    async def run_sync(
        self,
        operation: Callable[[Session], Any],
    ) -> Any:
        return operation(self.sync_session)


class _FailingAsyncSession(_AsyncSessionAdapter):
    async def run_sync(
        self,
        operation: Callable[[Session], Any],
    ) -> Any:
        operation(self.sync_session)
        self.sync_session.flush()
        raise RuntimeError("database detail must not enter the trace")


class _DormantSession:
    def __getattr__(
        self,
        name: str,
    ) -> Any:  # pragma: no cover - assertion helper
        raise AssertionError(f"disabled persistence touched session.{name}")


@pytest.fixture()
def sync_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            AnalystCaseRecord.__table__,
            AnalystCaseVersionRecord.__table__,
        ],
    )
    Session = sessionmaker(bind=engine)
    current = Session()
    try:
        yield current
    finally:
        current.close()
        engine.dispose()


async def _intent_and_result(
    *,
    question: str,
    with_evidence: bool = True,
) -> Tuple[AnalystIntent, AnalystWorkbenchResult]:
    intent = detect_analyst_intent(question)
    assert intent is not None
    evidence = []
    if with_evidence:
        evidence = [
            AnalystEvidence(
                source_type="document",
                label="contrato.pdf",
                summary=(
                    "Contrato con obligaciones, responsables, fechas y "
                    "riesgos suficientes para el analisis."
                ),
            )
        ]
    result = await run_analyst_workbench(
        intent=intent,
        evidence=evidence,
    )
    return intent, result


def test_case_persistence_flag_defaults_and_invalid_values_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        raising=False,
    )
    assert analyst_case_persistence_enabled() is False

    monkeypatch.setenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "definitely",
    )
    assert analyst_case_persistence_enabled() is False

    monkeypatch.setenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "true",
    )
    assert analyst_case_persistence_enabled() is True


@pytest.mark.asyncio
async def test_disabled_persistence_does_not_touch_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        raising=False,
    )
    intent, result = await _intent_and_result(
        question="Explica este contrato con contexto suficiente",
    )

    persisted = await persist_analyst_case(
        session=_DormantSession(),
        conversation_id="conv-1",
        current_empleado=SimpleNamespace(id="emp-1", rol="finanzas"),
        question=intent.raw_text,
        intent=intent,
        result=result,
    )

    assert persisted.enabled is False
    assert persisted.outcome == "skipped"


@pytest.mark.asyncio
async def test_case_construction_failure_is_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "true",
    )
    intent, result = await _intent_and_result(
        question="Explica este contrato con contexto suficiente",
    )

    def fail_build(**_kwargs: Any) -> Any:
        raise ValueError("case contents must not enter the response")

    monkeypatch.setattr(
        case_persistence,
        "build_analyst_case",
        fail_build,
    )
    persisted = await persist_analyst_case(
        session=_DormantSession(),
        conversation_id="conv-1",
        current_empleado=SimpleNamespace(id="emp-1", rol="finanzas"),
        question=intent.raw_text,
        intent=intent,
        result=result,
    )

    assert persisted.outcome == "failed"
    assert "case contents" not in str(persisted.trace())


@pytest.mark.asyncio
async def test_creates_then_reuses_one_complete_case(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
) -> None:
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "true",
    )
    intent, result = await _intent_and_result(
        question="Explica este contrato con contexto suficiente",
    )
    result = replace(
        result,
        suggested_routes=[
            {
                "route_id": "documents.review",
                "label": "Revisar documento",
                "execution_status": "executed",
                "writes_enabled": True,
            }
        ],
    )
    async_session = _AsyncSessionAdapter(sync_session)
    kwargs = {
        "session": async_session,
        "conversation_id": "conv-1",
        "current_empleado": SimpleNamespace(
            id="emp-1",
            rol="finanzas",
        ),
        "question": intent.raw_text,
        "intent": intent,
        "result": result,
    }

    created = await persist_analyst_case(**kwargs)
    reused = await persist_analyst_case(**kwargs)
    sync_session.commit()

    assert created.outcome == "created"
    assert reused.outcome == "reused"
    assert reused.case_id == created.case_id
    assert created.status == "analyzed"
    assert created.version_number == 1
    assert created.trace()["product_case_write"] is True
    assert reused.trace()["product_case_write"] is False
    assert sync_session.scalar(
        select(func.count()).select_from(AnalystCaseRecord)
    ) == 1
    assert sync_session.scalar(
        select(func.count()).select_from(AnalystCaseVersionRecord)
    ) == 1
    record = sync_session.get(AnalystCaseRecord, created.case_id)
    assert record.user_id == "emp-1"
    assert record.role == "finanzas"
    assert record.current_answer == result.answer
    assert record.evidence == result.evidence
    assert record.next_questions == result.next_questions
    assert record.caveats == result.caveats
    assert record.suggested_routes[0]["execution_status"] == "not_executed"
    assert record.suggested_routes[0]["writes_enabled"] is False
    assert record.versions[0].answer_contract == result.answer_contract


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result_status", "expected_status"),
    (
        ("needs_context", "waiting_context"),
        ("provider_unavailable", "waiting_context"),
    ),
)
async def test_context_limited_results_are_persisted_for_resume(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
    result_status: str,
    expected_status: str,
) -> None:
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "true",
    )
    intent, result = await _intent_and_result(
        question="Explica este contrato",
        with_evidence=False,
    )
    result = replace(result, status=result_status)

    persisted = await persist_analyst_case(
        session=_AsyncSessionAdapter(sync_session),
        conversation_id="conv-context",
        current_empleado=SimpleNamespace(id="emp-2", rol="direccion"),
        question=intent.raw_text,
        intent=intent,
        result=result,
    )

    assert persisted.outcome == "created"
    assert persisted.status == expected_status


@pytest.mark.asyncio
async def test_identical_analysis_in_another_conversation_creates_new_case(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
) -> None:
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "true",
    )
    intent, result = await _intent_and_result(
        question="Explica este contrato con contexto suficiente",
    )
    shared = {
        "session": _AsyncSessionAdapter(sync_session),
        "current_empleado": SimpleNamespace(id="emp-1", rol="finanzas"),
        "question": intent.raw_text,
        "intent": intent,
        "result": result,
    }

    first = await persist_analyst_case(
        conversation_id="conv-1",
        **shared,
    )
    second = await persist_analyst_case(
        conversation_id="conv-2",
        **shared,
    )

    assert first.outcome == "created"
    assert second.outcome == "created"
    assert second.case_id != first.case_id
    assert sync_session.scalar(
        select(func.count()).select_from(AnalystCaseRecord)
    ) == 2


@pytest.mark.asyncio
async def test_changed_analysis_in_same_conversation_creates_new_case(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
) -> None:
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "true",
    )
    intent, result = await _intent_and_result(
        question="Explica este contrato con contexto suficiente",
    )
    shared = {
        "session": _AsyncSessionAdapter(sync_session),
        "conversation_id": "conv-1",
        "current_empleado": SimpleNamespace(id="emp-1", rol="finanzas"),
        "question": intent.raw_text,
        "intent": intent,
    }

    first = await persist_analyst_case(result=result, **shared)
    changed = await persist_analyst_case(
        result=replace(result, answer=f"{result.answer} Nueva evidencia."),
        **shared,
    )

    assert first.outcome == "created"
    assert changed.outcome == "created"
    assert changed.case_id != first.case_id


@pytest.mark.asyncio
async def test_terminal_transition_keeps_successor_identity_stable(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
) -> None:
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "true",
    )
    intent, result = await _intent_and_result(
        question="Explica este contrato con contexto suficiente",
    )
    kwargs = {
        "session": _AsyncSessionAdapter(sync_session),
        "conversation_id": "conv-1",
        "current_empleado": SimpleNamespace(id="emp-1", rol="finanzas"),
        "question": intent.raw_text,
        "intent": intent,
        "result": result,
    }
    first = await persist_analyst_case(**kwargs)
    AnalystCaseStore(sync_session).update_case(
        first.case_id,
        status=CASE_STATUS_REVIEWED,
        updated_by="emp-1",
    )

    successor = await persist_analyst_case(**kwargs)
    AnalystCaseStore(sync_session).update_case(
        first.case_id,
        status=CASE_STATUS_CLOSED,
        closed_by="emp-1",
    )
    retry = await persist_analyst_case(**kwargs)

    assert successor.outcome == "created"
    assert successor.case_id != first.case_id
    assert successor.status == "analyzed"
    assert retry.outcome == "reused"
    assert retry.case_id == successor.case_id
    assert sync_session.scalar(
        select(func.count()).select_from(AnalystCaseRecord)
    ) == 2


@pytest.mark.asyncio
async def test_missing_owner_or_non_analyst_result_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
) -> None:
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "true",
    )
    intent, result = await _intent_and_result(
        question="Explica este contrato con contexto suficiente",
    )
    async_session = _AsyncSessionAdapter(sync_session)

    missing_owner = await persist_analyst_case(
        session=async_session,
        conversation_id="conv-1",
        current_empleado=SimpleNamespace(id="", rol="finanzas"),
        question=intent.raw_text,
        intent=intent,
        result=result,
    )
    operational = await persist_analyst_case(
        session=async_session,
        conversation_id="conv-1",
        current_empleado=SimpleNamespace(id="emp-1", rol="finanzas"),
        question=intent.raw_text,
        intent=intent,
        result=replace(result, status="routed_to_operational"),
    )

    assert missing_owner.outcome == "skipped"
    assert operational.outcome == "skipped"
    assert sync_session.scalar(
        select(func.count()).select_from(AnalystCaseRecord)
    ) == 0


@pytest.mark.asyncio
async def test_failure_rolls_back_savepoint_and_returns_redacted_trace(
    monkeypatch: pytest.MonkeyPatch,
    sync_session: Session,
) -> None:
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "true",
    )
    intent, result = await _intent_and_result(
        question="Explica este contrato con contexto suficiente",
    )

    persisted = await persist_analyst_case(
        session=_FailingAsyncSession(sync_session),
        conversation_id="conv-failure",
        current_empleado=SimpleNamespace(id="emp-1", rol="finanzas"),
        question=intent.raw_text,
        intent=intent,
        result=result,
    )

    assert persisted.outcome == "failed"
    assert persisted.case_id is None
    assert "database detail" not in str(persisted.trace())
    assert persisted.trace()["operational_writes"] is False
    assert persisted.trace()["actions_executed"] == []
    assert sync_session.scalar(
        select(func.count()).select_from(AnalystCaseRecord)
    ) == 0
    assert sync_session.scalar(
        select(func.count()).select_from(AnalystCaseVersionRecord)
    ) == 0
    assert sync_session.scalar(select(text("1"))) == 1
