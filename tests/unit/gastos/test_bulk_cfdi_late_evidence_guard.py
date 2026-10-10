from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services import cfdi_expense_link_service as links
from devnous.gastos.services import cfdi_ingestion_service as ingestion
from devnous.gastos.services import documento_service as documents


class Result:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None


@pytest.mark.parametrize("kind", ["documento", "expense"])
@pytest.mark.parametrize(
    "case", ["legacy", "own", "related", "foreign", "shared", "over"]
)
async def test_bulk_preserves_evidence_and_only_accepts_confirmed_balance(
    monkeypatch, kind, case
):
    identifier, owner, report_id = uuid4(), uuid4(), uuid4()
    fiscal_uuid = str(uuid4()).upper()
    candidate = SimpleNamespace(
        id=identifier,
        cfdi_report_id=None,
        cfdi_uuid_manual=fiscal_uuid.lower(),
        cfdi_compartido_confirmado=case in {"own", "related", "shared", "over"},
        monto_solicitado=400,
        gasto_cantidad=420,
        propina_no_deducible=20,
        documento_id=(
            owner if kind == "expense" and case in {"own", "related"} else None
        ),
        solicitud_documento_id=None,
        informe_documento_id=None,
    )
    owners = [] if case == "legacy" else [identifier if case == "own" else owner]
    if kind == "expense" and case == "own":
        owners = [owner]
    if kind == "documento" and case == "related":
        owners = [identifier]
    results = [Result([identifier]), Result([candidate])]
    if kind == "expense" and case in {"own", "related"}:
        results.append(Result([owner]))
    results.append(Result(owners))
    session = SimpleNamespace(execute=AsyncMock(side_effect=results), flush=AsyncMock())

    @asynccontextmanager
    async def nested():
        yield

    session.begin_nested = nested
    lock = AsyncMock()
    reserve = AsyncMock(return_value=None if case == "legacy" else object())
    balance = AsyncMock()
    if case == "over":
        balance.side_effect = documents.SolicitudValidationError(
            "cfdi_amount_exceeds_remaining", "Saldo insuficiente"
        )
    monkeypatch.setattr(ingestion, "lock_cfdi_identity", lock)
    monkeypatch.setattr(ingestion, "find_blocking_cfdi_evidence", reserve)
    monkeypatch.setattr(documents, "validate_shared_cfdi_payment_amount", balance)
    monkeypatch.setattr(
        links,
        "find_cfdi_report_by_fiscal_uuid",
        AsyncMock(return_value=SimpleNamespace(id=report_id)),
    )
    method = (
        links.bulk_link_pending_documentos_to_cfdi_reports
        if kind == "documento"
        else links.bulk_link_pending_expenses_to_cfdi_reports
    )
    expected = case in {"legacy", "shared"}
    assert await method(session) == int(expected)
    assert candidate.cfdi_report_id == (report_id if expected else None)
    assert candidate.cfdi_uuid_manual == fiscal_uuid.lower()
    lock.assert_awaited_once_with(session, fiscal_uuid)
    if case in {"shared", "over"}:
        assert balance.await_args.kwargs["requested_amount"] == 400
        assert balance.await_args.kwargs["exclude_documento_id"] == (
            identifier if kind == "documento" else None
        )
        assert balance.await_args.kwargs["exclude_expense_id"] == (
            identifier if kind == "expense" else None
        )
    else:
        balance.assert_not_awaited()
    assert session.flush.await_count == int(expected)


@pytest.mark.parametrize("kind", ["documento", "expense"])
async def test_bulk_rechecks_already_linked_candidate_without_reallocation(kind):
    candidate = SimpleNamespace(cfdi_report_id=uuid4())
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[Result([uuid4()]), Result([candidate])]),
        flush=AsyncMock(),
    )

    @asynccontextmanager
    async def nested():
        yield

    session.begin_nested = nested
    method = (
        links.bulk_link_pending_documentos_to_cfdi_reports
        if kind == "documento"
        else links.bulk_link_pending_expenses_to_cfdi_reports
    )
    assert await method(session) == 0
    session.flush.assert_not_awaited()
