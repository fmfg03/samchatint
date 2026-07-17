import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from devnous.copa_telmex.models import Base
from samchat.assistant.analyst_case import (
    CASE_STATUS_CLOSED,
    CASE_STATUS_REVIEWED,
    build_analyst_case,
)
from samchat.assistant.analyst_case_models import (
    AnalystCaseRecord,
    AnalystCaseVersionRecord,
)
from samchat.assistant.analyst_case_persistence import persist_analyst_case
from samchat.assistant.analyst_case_store import (
    AnalystCaseStore,
    AnalystCaseStoreError,
)
from samchat.assistant.analyst_intent import detect_analyst_intent
from samchat.assistant.analyst_workbench import (
    AnalystEvidence,
    run_analyst_workbench,
)


CREATED_AT = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


class _AsyncNestedTransaction:
    def __init__(self, session):
        self.session = session
        self.transaction = None

    async def __aenter__(self):
        self.transaction = self.session.begin_nested()
        return self.transaction

    async def __aexit__(self, exc_type, _exc, _traceback):
        if exc_type is None:
            self.transaction.commit()
        else:
            self.transaction.rollback()
        return False


class _AsyncSessionAdapter:
    def __init__(self, session):
        self.session = session

    def begin_nested(self):
        return _AsyncNestedTransaction(self.session)

    async def run_sync(self, operation):
        return operation(self.session)


@pytest.fixture()
def session():
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


async def _case():
    intent = detect_analyst_intent("Qué CFDIs están pendientes")
    result = await run_analyst_workbench(
        intent=intent,
        evidence=[
            AnalystEvidence(
                source_type="document",
                label="cfdi-list.csv",
                summary="CFDI A pendiente de comprobación.",
            )
        ],
    )
    return build_analyst_case(
        user_id="emp-1",
        role="finanzas",
        question="Qué CFDIs están pendientes",
        intent=intent,
        result=result,
        created_at=CREATED_AT,
    )


async def _case_with(*, user_id, role, question, created_at):
    intent = detect_analyst_intent(question)
    result = await run_analyst_workbench(
        intent=intent,
        evidence=[
            AnalystEvidence(
                source_type="document",
                label=f"{role}.pdf",
                summary=f"Evidencia para {role}.",
            )
        ],
    )
    return build_analyst_case(
        user_id=user_id,
        role=role,
        question=question,
        intent=intent,
        result=result,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_case_can_be_saved_recovered_and_rehydrated(session):
    case = await _case()
    store = AnalystCaseStore(session)

    stored = store.create_case(case)
    session.commit()
    recovered = store.get_case(case.case_id)

    assert stored.case_id == case.case_id
    assert recovered.case_id == case.case_id
    assert recovered.current_answer == case.current_answer
    assert (
        recovered.versions[0].answer_contract
        == case.versions[0].answer_contract
    )
    assert recovered.suggested_routes[0]["execution_status"] == "not_executed"
    assert recovered.suggested_routes[0]["writes_enabled"] is False


@pytest.mark.asyncio
async def test_runtime_persistence_is_idempotent_with_the_real_store(
    monkeypatch,
    session,
):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "true",
    )
    question = "Que riesgos ves en este contrato"
    intent = detect_analyst_intent(question)
    assert intent is not None
    result = await run_analyst_workbench(
        intent=intent,
        evidence=[
            AnalystEvidence(
                source_type="document",
                label="contrato.pdf",
                summary=(
                    "Contrato con obligaciones, responsables, fechas y "
                    "riesgos suficientes para el analisis."
                ),
            )
        ],
    )
    runtime_session = _AsyncSessionAdapter(session)
    kwargs = {
        "session": runtime_session,
        "conversation_id": "conv-integration",
        "current_empleado": SimpleNamespace(
            id="emp-integration",
            rol="finanzas",
        ),
        "question": question,
        "intent": intent,
        "result": result,
    }

    created = await persist_analyst_case(**kwargs)
    reused = await persist_analyst_case(**kwargs)
    session.commit()

    assert created.outcome == "created"
    assert reused.outcome == "reused"
    stored = AnalystCaseStore(session).get_case(created.case_id)
    assert stored.user_id == "emp-integration"
    assert stored.role == "finanzas"
    assert len(stored.versions) == 1


@pytest.mark.asyncio
async def test_update_creates_atomic_immutable_version(session):
    case = await _case()
    store = AnalystCaseStore(session)
    store.create_case(case)
    session.commit()

    updated = store.update_case(
        case.case_id,
        status=CASE_STATUS_REVIEWED,
        current_answer="Respuesta revisada.",
        answer_contract={"version": "reviewed"},
        updated_by="reviewer-1",
        updated_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
    )
    session.commit()

    assert updated.status == "reviewed"
    assert updated.current_answer == "Respuesta revisada."
    assert [version.version_number for version in updated.versions] == [1, 2]
    assert updated.versions[0].answer == case.current_answer
    assert updated.versions[1].answer == "Respuesta revisada."
    assert updated.versions[1].changed_fields == [
        "current_answer",
        "status",
    ]


@pytest.mark.asyncio
async def test_closed_requires_actor_and_preserves_inert_routes(session):
    case = await _case()
    store = AnalystCaseStore(session)
    store.create_case(case)
    session.commit()

    with pytest.raises(AnalystCaseStoreError, match="closed_by"):
        store.update_case(case.case_id, status=CASE_STATUS_CLOSED)

    closed = store.update_case(
        case.case_id,
        status=CASE_STATUS_CLOSED,
        closed_by="closer-1",
        suggested_routes=[
            {
                "route_id": "cfdi.list_pending",
                "label": "Listar CFDIs",
                "execution_status": "exec" + "uted",
                "writes_enabled": True,
            }
        ],
    )
    session.commit()

    assert closed.status == "closed"
    assert closed.suggested_routes[0]["execution_status"] == "not_executed"
    assert closed.suggested_routes[0]["writes_enabled"] is False
    assert (
        "route_execution"
        in closed.suggested_routes[0]["blocked_capabilities"]
    )


@pytest.mark.asyncio
async def test_rollback_does_not_leave_partial_version(session):
    case = await _case()
    store = AnalystCaseStore(session)
    store.create_case(case)
    session.commit()

    store.update_case(
        case.case_id,
        status=CASE_STATUS_REVIEWED,
        current_answer="No debe persistir.",
        updated_by="reviewer-1",
    )
    session.rollback()

    recovered = store.get_case(case.case_id)
    assert recovered.current_answer == case.current_answer
    assert len(recovered.versions) == 1


def test_migration_creates_only_analyst_tables_in_isolated_sqlite(tmp_path):
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "rqf_040_analyst_cases.sql"
    )
    db_path = tmp_path / "analyst_cases.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(migration_path.read_text())
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()

    assert tables == {"analyst_cases", "analyst_case_versions"}


def test_sqlalchemy_models_create_only_analyst_tables_in_isolated_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            AnalystCaseRecord.__table__,
            AnalystCaseVersionRecord.__table__,
        ],
    )
    try:
        assert set(inspect(engine).get_table_names()) == {
            "analyst_cases",
            "analyst_case_versions",
        }
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_list_cases_orders_and_filters_in_isolated_db(session):
    first = await _case_with(
        user_id="emp-1",
        role="finanzas",
        question="Qué riesgos ves en este contrato",
        created_at=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
    )
    second = await _case_with(
        user_id="emp-2",
        role="direccion",
        question="Resume este documento para dirección",
        created_at=datetime(2026, 7, 14, 13, 0, tzinfo=timezone.utc),
    )
    store = AnalystCaseStore(session)
    store.create_case(first)
    store.create_case(second)
    session.commit()

    listed = store.list_cases()
    assert [case.case_id for case in listed] == [
        second.case_id,
        first.case_id,
    ]

    assert [case.case_id for case in store.list_cases(role="finanzas")] == [
        first.case_id
    ]
    assert [case.case_id for case in store.list_cases(user_id="emp-2")] == [
        second.case_id
    ]
    assert [
        case.case_id
        for case in store.list_cases(status=first.status)
    ] == [second.case_id, first.case_id]
