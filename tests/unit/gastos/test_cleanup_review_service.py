"""Explicit human review must not change fiscal readiness or financial fields."""

import json
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.gastos.services import cleanup_review_service as reviews


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class Session:
    def __init__(self, expense, events=()):
        self.expense = expense
        self.events = list(events)
        self.added = []

    async def execute(self, statement):
        if "FOR UPDATE" in str(statement):
            return Rows([self.expense] if self.expense else [])
        return Rows(self.events)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        pass


def event(expense_id, action, **details):
    return SimpleNamespace(
        id=uuid4(),
        entidad_id=expense_id,
        accion=action,
        comentario=json.dumps({"batch": reviews.BATCH, **details}),
        fecha=datetime(2026, 10, 30, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_request_is_audited_and_leaves_financial_fields_untouched():
    expense = SimpleNamespace(id=uuid4(), estado_gasto="activo", cuenta="5300-020-005")
    session = Session(expense)
    before = vars(expense).copy()
    assert await reviews.request_review(session, expense.id, uuid4()) is True
    assert vars(expense) == before
    assert len(session.added) == 1
    assert session.added[0].accion == reviews.REQUESTED
    assert session.added[0].aprobador_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ["cleanup_review_requested", "cleanup_review_confirmed"]
)
async def test_repeated_activation_does_not_duplicate_or_reopen(action):
    expense = SimpleNamespace(id=uuid4(), estado_gasto="activo")
    session = Session(expense, [event(expense.id, action)])
    assert await reviews.request_review(session, expense.id, uuid4()) is False
    assert session.added == []


@pytest.mark.asyncio
async def test_confirm_is_explicit_audited_and_idempotent():
    expense = SimpleNamespace(id=uuid4(), estado_gasto="activo")
    actor = uuid4()
    session = Session(expense, [event(expense.id, reviews.REQUESTED)])
    assert await reviews.confirm_review(session, expense.id, actor) is True
    assert session.added[0].accion == reviews.CONFIRMED
    assert session.added[0].aprobador_id == actor
    assert session.added[0].fecha > session.events[0].fecha
    session.events = [session.added[0]]
    assert await reviews.confirm_review(session, expense.id, actor) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", [None, UTC, timezone(timedelta(hours=-6))])
async def test_confirmation_orders_persisted_timestamps_in_utc(zone):
    expense = SimpleNamespace(id=uuid4(), estado_gasto="activo")
    previous = event(expense.id, reviews.REQUESTED)
    previous.fecha = datetime(2099, 1, 1, tzinfo=zone)
    session = Session(expense, [previous])
    await reviews.confirm_review(session, expense.id, uuid4())
    timestamp = session.added[0].fecha
    assert timestamp.tzinfo is UTC
    prior_utc = previous.fecha if zone else previous.fecha.replace(tzinfo=UTC)
    assert timestamp > prior_utc


@pytest.mark.asyncio
async def test_unrequested_expense_cannot_be_confirmed():
    expense = SimpleNamespace(id=uuid4(), estado_gasto="activo")
    session = Session(expense)
    with pytest.raises(ValueError, match="revisión solicitada"):
        await reviews.confirm_review(session, expense.id, uuid4())
    assert session.added == []


@pytest.mark.asyncio
async def test_cancelled_expense_cannot_be_requested():
    expense = SimpleNamespace(id=uuid4(), estado_gasto="cancelado")
    with pytest.raises(ValueError, match="activo"):
        await reviews.request_review(Session(expense), expense.id, uuid4())


@pytest.mark.asyncio
async def test_batch_read_distinguishes_pending_from_confirmed():
    first, second = uuid4(), uuid4()
    session = Session(
        None, [event(first, reviews.REQUESTED), event(second, reviews.CONFIRMED)]
    )
    states = await reviews.load_review_states(session, [first, second])
    assert states[first].pending
    assert not states[second].pending
    assert states[first].label == "La Merced · revisión contable solicitada"


@pytest.mark.asyncio
async def test_empty_batch_does_not_query():
    assert await reviews.load_review_states(None, []) == {}


def test_marker_is_escaped_and_explicit_confirmation_has_csrf():
    state = reviews.ReviewState(pending=True)
    html = reviews.review_controls(uuid4(), state, "<bad>")
    assert "La Merced · revisión contable solicitada" in html
    assert "Confirmar revisión" in html
    assert 'method="POST"' in html
    assert "&lt;bad&gt;" in html
    assert "<bad>" not in html
    assert (
        reviews.review_controls(uuid4(), reviews.ReviewState(pending=False), "x") == ""
    )


@pytest.mark.asyncio
async def test_invalid_audit_is_not_silently_treated_as_complete():
    row = event(uuid4(), reviews.REQUESTED)
    row.comentario = "{"
    with pytest.raises(ValueError, match="Historial"):
        await reviews.load_review_states(Session(None, [row]), [row.entidad_id])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "issues,review_pending,expected",
    [
        ([], True, True),
        ([], False, False),
        (["Falta cuenta de IVA"], False, True),
        (["Falta cuenta de IVA"], True, True),
    ],
)
async def test_queue_includes_requested_ready_expenses_and_keeps_technical_issues(
    monkeypatch, issues, review_pending, expected
):
    from devnous.gastos.services import expense_accounting_cleanup_service as cleanup

    expense = SimpleNamespace(id=uuid4(), estado_gasto="activo")

    async def query(_statement):
        return Rows([expense])

    async def states(_session, _ids):
        return {expense.id: reviews.ReviewState(pending=review_pending)}

    async def preview(_session, _expense):
        return {"status": "Listo COI" if not issues else "Pendiente", "issues": issues}

    session = SimpleNamespace(execute=query)
    monkeypatch.setattr(cleanup, "load_review_states", states)
    monkeypatch.setattr(cleanup, "build_cleanup_preview", preview)
    monkeypatch.setattr(
        cleanup, "cleanup_expense_matches_filters", lambda *a, **kw: True
    )
    assert bool(await cleanup.load_cleanup_expenses(session)) is expected
    review_only = await cleanup.load_cleanup_expenses(session, issue_type="review")
    assert bool(review_only) is review_pending


@pytest.mark.asyncio
async def test_review_does_not_change_coi_readiness(monkeypatch):
    from devnous.gastos.services import expense_accounting_cleanup_service as cleanup

    expense = SimpleNamespace(
        cuenta_contable_id=uuid4(),
        contra_cuenta_contable_id=uuid4(),
        cfdi_report_id=None,
        cuenta_contable=SimpleNamespace(codigo="5300-020-005", nombre="NO DEDUCIBLE"),
        _cleanup_review_state=reviews.ReviewState(pending=True),
    )

    async def preview(*a, **kw):
        return {"taxes": {}}

    monkeypatch.setattr(cleanup, "build_expense_accounting_preview", preview)
    monkeypatch.setattr(cleanup, "resolve_effective_budget_concept", lambda e: None)
    state = await cleanup.build_cleanup_preview(None, expense)
    assert state["status"] == "Listo COI"
    assert state["issues"] == []
