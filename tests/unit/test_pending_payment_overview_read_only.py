from __future__ import annotations

import uuid

import pytest

import devnous.gastos.services.documento_payment_service as payment_service


class _Actor:
    id = uuid.uuid4()
    rol = "finanzas"


class _EmptyScalarResult:
    def all(self):
        return []


class _EmptyExecuteResult:
    def scalars(self):
        return _EmptyScalarResult()


class _ReadOnlySession:
    def __init__(self) -> None:
        self.commits = 0
        self.added = []
        self.executed = 0

    async def execute(self, _stmt):
        self.executed += 1
        return _EmptyExecuteResult()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_pending_payment_overview_does_not_promote_solicitudes(monkeypatch):
    async def load_actor(_session, _actor_id):
        return _Actor()

    async def promote_solicitudes_ready_for_payment(*_args, **_kwargs):
        raise AssertionError("read-only pending payment overview must not promote state")

    monkeypatch.setattr(payment_service, "_load_actor", load_actor)
    monkeypatch.setattr(
        payment_service,
        "promote_solicitudes_ready_for_payment",
        promote_solicitudes_ready_for_payment,
        raising=False,
    )

    session = _ReadOnlySession()

    result = await payment_service.get_pending_document_payment_overview(
        session,
        actor_id=_Actor.id,
    )

    assert result["summary"]["pending_count"] == 0
    assert session.executed == 1
    assert session.added == []
    assert session.commits == 0
