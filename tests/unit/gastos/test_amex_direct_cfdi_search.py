"""Bounded fiscal picker and single-selection external observation negatives."""

from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_direct_cfdi_search_service as search
from devnous.gastos.services import amex_direct_status_service as status
from devnous.gastos.services.amex_direct_common import (
    DirectAccessError,
    DirectConflict,
    digest,
)


@pytest.fixture
def setup_search(monkeypatch):
    item = NS(id=uuid4(), source_version=2, accounting_status="pending")
    context = NS(id=uuid4(), version=1, receiver_rfc="AAA010101AAA")
    state = NS(source_version=3)
    statement = NS(id=uuid4())
    session = NS(execute=AsyncMock(), get=AsyncMock(return_value=NS(id=uuid4())))
    monkeypatch.setattr(
        search,
        "load_statement",
        AsyncMock(return_value=(statement, state, context, {})),
    )
    monkeypatch.setattr(search, "load_items", AsyncMock(return_value=[item]))
    monkeypatch.setattr(
        search,
        "effective_source_for_item",
        AsyncMock(
            return_value=NS(
                movement_kind="debit",
                imported_expense_id=uuid4(),
                source_json={"row_number": 2},
            )
        ),
    )
    monkeypatch.setattr(search, "current_status_receipt", AsyncMock(return_value=None))
    monkeypatch.setattr(search, "_available", AsyncMock(return_value=True))
    identity = {
        "uuid": str(uuid4()),
        "rfc_receptor": context.receiver_rfc,
        "rfc_emisor": "BBB010101BBB",
        "total": "116.00",
        "xml_hash": "xml",
    }
    monkeypatch.setattr(search, "cfdi_identity", MagicMock(return_value=identity))
    args = dict(
        actor_id=uuid4(), import_id=statement.id, item_id=item.id, navigation={}
    )
    return NS(
        session=session,
        args=args,
        item=item,
        context=context,
        state=state,
        identity=identity,
    )


def set_records(session, rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session.execute.return_value = result


@pytest.mark.asyncio
async def test_empty_valid_page_advances_examined_cursor_without_unbounded_scan(
    setup_search,
):
    w = setup_search
    rows = [NS(id=uuid4(), fecha=None) for _ in range(26)]
    set_records(w.session, rows)
    search.cfdi_identity.side_effect = DirectConflict("canonical_sat_xml_required")
    result = await search.search_direct_cfdis(w.session, **w.args)
    assert result["candidates"] == [] and result["has_more"] is True
    position = w.args["navigation"][search.CURSOR_KEY][result["cursor"]]
    assert position["id"] == str(rows[24].id) and position["date"] is None
    assert search.cfdi_identity.call_count == 25
    query = str(w.session.execute.await_args.args[0])
    assert "LIMIT" in query and "NULLS LAST" in query


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["q", "version", "item", "context"])
async def test_cursor_rejects_changed_bound_context(setup_search, mutation):
    w = setup_search
    rows = [
        NS(id=uuid4(), fecha=datetime(2026, 9, 1), emisor_nombre="Proveedor")
        for _ in range(26)
    ]
    set_records(w.session, rows)
    result = await search.search_direct_cfdis(w.session, **w.args)
    w.args["cursor"] = result["cursor"]
    if mutation == "q":
        w.args["q"] = "otro"
    elif mutation == "version":
        w.state.source_version += 1
    elif mutation == "item":
        w.item.source_version += 1
    else:
        w.context.version += 1
    w.session.execute.reset_mock()
    with pytest.raises(DirectConflict, match="invalid_cfdi_search_cursor"):
        await search.search_direct_cfdis(w.session, **w.args)
    w.session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_candidate_not_exposed_even_if_database_row_is_returned(
    setup_search,
):
    w = setup_search
    set_records(w.session, [NS(id=uuid4(), fecha=None)])
    search.cfdi_identity.return_value = {**w.identity, "rfc_receptor": "FOREIGN"}
    result = await search.search_direct_cfdis(w.session, **w.args)
    assert result["candidates"] == []
    search._available.assert_not_awaited()


@pytest.mark.asyncio
async def test_receiver_scoped_literal_query_and_private_business_values(setup_search):
    w = setup_search
    set_records(w.session, [NS(id=uuid4(), fecha=None, emisor_nombre="Proveedor")])
    search._available.return_value = False
    result = await search.search_direct_cfdis(w.session, **w.args, q="%_\\")
    query = w.session.execute.await_args.args[0]
    parameters = query.compile().params
    assert "AAA010101AAA" in parameters.values()
    assert "%\\%\\_\\\\%" in parameters.values()
    assert result["candidates"][0]["available"] is False
    assert "xml" not in result["candidates"][0]
    assert not result["has_more"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"q": "x" * 121},
        {"amount": Decimal("NaN")},
        {"amount": Decimal("-1")},
        {"page_size": 51},
        {"cursor": "forged"},
    ],
)
async def test_invalid_filters_fail_before_catalog_read(setup_search, extra):
    w = setup_search
    with pytest.raises(DirectConflict):
        await search.search_direct_cfdis(w.session, **w.args, **extra)
    w.session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_denial_precedes_source_and_catalog_reads(setup_search):
    w = setup_search
    search.load_statement.side_effect = DirectAccessError("explicit_action_denied")
    with pytest.raises(DirectAccessError):
        await search.search_direct_cfdis(w.session, **w.args)
    search.load_items.assert_not_awaited()
    w.session.execute.assert_not_awaited()


@pytest.fixture
def selected_status(monkeypatch):
    item = NS(id=uuid4(), source_version=2, accounting_status="pending")
    ctx = NS(id=uuid4(), version=1, receiver_rfc="AAA010101AAA")
    state = NS(source_version=3, progress_json={})
    cfdi = NS(id=uuid4(), receptor_rfc=ctx.receiver_rfc)
    session = NS(get=AsyncMock(return_value=cfdi), add=MagicMock(), flush=AsyncMock())
    from devnous.gastos.models import AmexStatementDirectState

    async def get(model, *args, **kwargs):
        return state if model is AmexStatementDirectState else cfdi

    session.get.side_effect = get
    active = []

    @asynccontextmanager
    async def transaction():
        active.append(True)
        try:
            yield session
        finally:
            active.pop()

    factory = NS(begin=transaction)
    monkeypatch.setattr(
        status, "load_statement", AsyncMock(return_value=(NS(), state, ctx, {}))
    )
    monkeypatch.setattr(status, "load_items", AsyncMock(return_value=[item]))
    monkeypatch.setattr(status, "require_unfrozen", AsyncMock(), raising=False)
    from devnous.gastos.services import amex_direct_common as common
    from devnous.gastos.services import (
        amex_direct_effective_source_service as effective,
    )

    monkeypatch.setattr(common, "require_unfrozen", AsyncMock())
    monkeypatch.setattr(
        effective,
        "effective_source_for_item",
        AsyncMock(
            return_value=NS(
                movement_kind="debit",
                source_json={"proof": "source"},
                imported_expense_id=uuid4(),
            )
        ),
    )
    identity = {
        "uuid": str(uuid4()).upper(),
        "rfc_receptor": ctx.receiver_rfc,
        "rfc_emisor": "BBB010101BBB",
        "total": "116.00",
        "xml_hash": "original",
    }
    monkeypatch.setattr(status, "cfdi_identity", MagicMock(return_value=identity))
    monkeypatch.setattr(status, "current_status_receipt", AsyncMock(return_value=None))

    async def consult(**kw):
        assert not active
        return {"status": "vigente", "request": kw}

    service = NS(consult_status=AsyncMock(side_effect=consult))
    args = dict(
        actor_id=uuid4(),
        import_id=uuid4(),
        item_id=item.id,
        cfdi_id=cfdi.id,
        expected_source_version=3,
        expected_item_version=2,
        expected_identity_hash=digest(identity),
        request_key="selection",
        consented=True,
        status_service=service,
    )
    return NS(
        factory=factory, session=session, service=service, args=args, identity=identity
    )


@pytest.mark.asyncio
async def test_single_choice_observes_without_transaction_alias_or_reconcile(
    selected_status,
):
    w = selected_status
    result = await status.consult_selected_direct_cfdi_status(w.factory, **w.args)
    assert result.status == "observed" and result.progress["status"] == "vigente"
    w.service.consult_status.assert_awaited_once()
    w.session.add.assert_called_once()
    assert w.session.add.call_args.args[0].xml_hash == "original"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["permission", "source", "xml"])
async def test_selected_observation_revalidates_after_soap(selected_status, change):
    w = selected_status

    async def consult(**kw):
        if change == "permission":
            status.load_statement.side_effect = DirectAccessError(
                "explicit_action_denied"
            )
        elif change == "source":
            statement, state, ctx, cfg = status.load_statement.return_value
            state.source_version += 1
        else:
            status.cfdi_identity.return_value = {**w.identity, "xml_hash": "changed"}
        return {"status": "vigente", "request": kw}

    w.service.consult_status.side_effect = consult
    with pytest.raises((DirectAccessError, DirectConflict)):
        await status.consult_selected_direct_cfdi_status(w.factory, **w.args)
    w.session.add.assert_not_called()


@pytest.mark.asyncio
async def test_selected_requires_explicit_consent(selected_status):
    w = selected_status
    w.args["consented"] = False
    result = await status.consult_selected_direct_cfdi_status(w.factory, **w.args)
    assert result.reason == "sat_consent_required"
    status.load_statement.assert_not_awaited()
    w.service.consult_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", ["vigente", "cancelado"])
async def test_selected_reuses_only_fresh_exact_receipt(selected_status, observed):
    w = selected_status
    status.current_status_receipt.return_value = NS(
        id=uuid4(), observed_status=observed
    )
    result = await status.consult_selected_direct_cfdi_status(w.factory, **w.args)
    assert result.progress["status"] == observed and result.progress["cached"]
    w.service.consult_status.assert_not_awaited()
    w.session.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [{"status": "vigente"}, {"status": "vigente", "request": {"uuid": str(uuid4())}}],
)
async def test_selected_rejects_unproven_external_request(selected_status, response):
    w = selected_status
    w.service.consult_status.side_effect = None
    w.service.consult_status.return_value = response
    result = await status.consult_selected_direct_cfdi_status(w.factory, **w.args)
    assert result.reason == "invalid_observed_request"
    w.session.add.assert_not_called()


@pytest.mark.asyncio
async def test_selected_vigencia_denial_prevents_soap(selected_status):
    w = selected_status
    status.load_statement.side_effect = DirectAccessError("explicit_action_denied")
    with pytest.raises(DirectAccessError):
        await status.consult_selected_direct_cfdi_status(w.factory, **w.args)
    w.service.consult_status.assert_not_awaited()
    assert status.load_statement.await_args.args[3] == "vigencia"


@pytest.mark.asyncio
async def test_selected_foreign_receiver_never_calls_soap(selected_status):
    w = selected_status
    w.session.get.return_value.receptor_rfc = "FOREIGN"
    with pytest.raises(DirectConflict, match="cfdi_missing"):
        await status.consult_selected_direct_cfdi_status(w.factory, **w.args)
    w.service.consult_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_cursor_can_continue_through_null_date_page(setup_search):
    w = setup_search
    set_records(
        w.session,
        [NS(id=uuid4(), fecha=None, emisor_nombre="Proveedor") for _ in range(26)],
    )
    result = await search.search_direct_cfdis(w.session, **w.args)
    w.args["cursor"] = result["cursor"]
    set_records(w.session, [])
    result = await search.search_direct_cfdis(w.session, **w.args)
    assert result["candidates"] == [] and not result["has_more"]
    query = str(w.session.execute.await_args.args[0])
    assert "fecha IS NULL" in query and "cfdi_reports.id >" in query
