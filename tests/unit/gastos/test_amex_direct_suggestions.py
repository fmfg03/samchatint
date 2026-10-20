"""Ranking explanations, scoped read orchestration and honest continuation."""

from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_direct_suggestion_service as owner
from devnous.gastos.services.amex_direct_common import DirectAccessError, DirectConflict


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("SUNTORY México", "suntory mexico", Decimal(1)),
        ("LA MANSIÓN", "mansion restaurante", Decimal(1) / 3),
        ("%_\\ ; DROP TABLE", "Suntory", Decimal(0)),
        ("", "", Decimal(0)),
    ],
)
def test_normalization_explains_token_overlap(left, right, expected):
    assert owner.merchant_similarity(left, right) == expected


@pytest.fixture
def world(monkeypatch):
    from devnous.gastos.services import amex_direct_fiscal_evidence_service as fiscal

    item = NS(
        id=uuid4(),
        source_version=2,
        source_hash="source",
        issuer_rfc=None,
        declared_uuid=None,
    )
    ctx = NS(
        id=uuid4(),
        version=1,
        receiver_rfc="BBB010101BBB",
        entity_code="sports",
        company_code="PS",
    )
    statement = NS(id=uuid4(), card_account_id=uuid4())
    state = NS(source_version=4)
    expense = NS(id=uuid4())
    source = NS(
        movement_kind="debit",
        imported_expense_id=expense.id,
        source_json={
            "amount": "3814.00",
            "date": "2026-09-29",
            "description": "SUNTORY",
            "row_number": 2,
        },
    )
    cfdi = NS(id=uuid4(), xml_raw="xml")
    identity = dict(
        uuid=str(uuid4()).upper(),
        total="3814.00",
        rfc_receptor=ctx.receiver_rfc,
        rfc_emisor="AAA010101AAA",
        xml_hash="hash",
    )
    session = NS(
        no_autoflush=nullcontext(),
        execute=AsyncMock(),
        add=MagicMock(),
        flush=AsyncMock(),
    )
    monkeypatch.setattr(
        owner, "load_statement", AsyncMock(return_value=(statement, state, ctx, {}))
    )
    monkeypatch.setattr(owner, "load_items", AsyncMock(return_value=[item]))
    monkeypatch.setattr(
        owner, "load_effective_sources", AsyncMock(return_value={item.id: source})
    )
    monkeypatch.setattr(owner, "cfdi_identity", MagicMock(return_value=identity))
    monkeypatch.setattr(
        owner,
        "parse_cfdi_xml",
        MagicMock(
            return_value={"fecha": datetime(2026, 10, 6), "emisor_nombre": "SUNTORY"}
        ),
    )
    monkeypatch.setattr(owner, "current_status_receipt", AsyncMock(return_value=None))
    monkeypatch.setattr(owner, "_available", AsyncMock(return_value=True))
    monkeypatch.setattr(
        fiscal,
        "read_direct_cfdi_fiscal_evidence_batch",
        AsyncMock(
            return_value={
                str(cfdi.id): {
                    "payment_method": "PPD",
                    "complement_status": "not_local",
                }
            }
        ),
    )
    args = dict(actor_id=uuid4(), import_id=statement.id, navigation={})

    def records(hits=None):
        def result(rows):
            r = MagicMock()
            r.scalars.return_value.all.return_value = rows
            return r

        ranking = MagicMock()
        ranking.mappings.return_value.all.return_value = hits or []
        session.execute.side_effect = [result([]), result([expense]), ranking] + (
            [result([cfdi])] if hits else []
        )

    hit = dict(
        item_id=item.id, cfdi_id=cfdi.id, identity_match=False, similarity=Decimal(1)
    )
    return NS(**locals())


@pytest.mark.asyncio
async def test_suntory_original_dates_decimal_delta_and_optional_ppd_complement(world):
    w = world
    w.records([w.hit])
    result = await owner.suggest_direct_statement_cfdis(w.session, **w.args)
    row = result["items"][0]
    candidate = row["candidates"][0]
    assert row["bank_date"] == "2026-09-29" and candidate["date"] == "2026-10-06"
    assert candidate["days_delta"] == 7 and candidate["amount_delta"] == "0.00"
    assert (
        candidate["payment_method"] == "PPD"
        and candidate["complement_status"] == "not_local"
    )
    assert candidate["provisional"] and candidate["available"]
    assert row["coverage_complete"] and not row["has_more"]
    w.session.add.assert_not_called()
    w.session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_denial_before_any_source_or_fiscal_read(world):
    w = world
    owner.load_statement.side_effect = DirectAccessError("denied")
    with pytest.raises(DirectAccessError):
        await owner.suggest_direct_statement_cfdis(w.session, **w.args)
    owner.load_items.assert_not_awaited()
    w.session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["context", "source", "item", "actor"])
async def test_bound_cursor_rejects_changes_before_catalog_read(world, change):
    w = world
    w.args["item_ids"] = [w.item.id]
    w.records([w.hit] * 4)
    result = await owner.suggest_direct_statement_cfdis(w.session, **w.args)
    w.args.update(cursor=result["items"][0]["cursor"], item_ids=[w.item.id])
    if change == "context":
        w.ctx.version += 1
    elif change == "source":
        w.state.source_version += 1
    elif change == "item":
        w.item.source_version += 1
    else:
        w.args["actor_id"] = uuid4()
    w.session.execute.reset_mock()
    with pytest.raises(DirectConflict, match="invalid_cfdi_suggestions_cursor"):
        await owner.suggest_direct_statement_cfdis(w.session, **w.args)
    w.session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_top_page_keeps_continuation_and_never_claims_no_invoices(world):
    w = world
    w.args["item_ids"] = [w.item.id]
    w.records([w.hit] * 4)
    owner.cfdi_identity.side_effect = DirectConflict("xml_conflict")
    result = await owner.suggest_direct_statement_cfdis(w.session, **w.args)
    row = result["items"][0]
    assert not row["candidates"] and row["has_more"] and not row["coverage_complete"]
    assert row["cursor"] and row["pending_reason"] == "candidate_search_incomplete"
    w.records([])
    result = await owner.suggest_direct_statement_cfdis(
        w.session, **w.args, cursor=row["cursor"]
    )
    assert (
        result["items"][0]["coverage_complete"] and not result["items"][0]["has_more"]
    )
    assert (
        w.session.execute.call_args_list[-1]
        .args[1]["items"]
        .find('"candidate_offset": 3')
        >= 0
    )


@pytest.mark.asyncio
async def test_foreign_xml_and_used_candidates_never_become_free_link(world):
    w = world
    w.records([w.hit])
    w.identity["rfc_receptor"] = "FOREIGN"
    result = await owner.suggest_direct_statement_cfdis(w.session, **w.args)
    assert result["items"][0]["candidates"] == []
    owner._available.assert_not_awaited()
    w.identity["rfc_receptor"] = w.ctx.receiver_rfc
    w.records([w.hit])
    owner._available.return_value = False
    result = await owner.suggest_direct_statement_cfdis(w.session, **w.args)
    assert result["items"][0]["candidates"][0]["available"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"page_size": 26},
        {"candidates_per_item": 4},
        {"cursor": "forged"},
        {"item_ids": [uuid4()]},
    ],
)
async def test_invalid_request_does_not_read_catalog(world, extra):
    with pytest.raises(DirectConflict):
        await owner.suggest_direct_statement_cfdis(world.session, **world.args, **extra)
    world.session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexplained_amount_difference_is_visible_and_never_tip(world):
    w = world
    w.records([w.hit])
    w.identity["total"] = "3700.00"
    result = await owner.suggest_direct_statement_cfdis(w.session, **w.args)
    candidate = result["items"][0]["candidates"][0]
    assert (
        candidate["amount_delta"] == "-114.00"
        and not candidate["reasons"]["exact_amount"]
    )
    assert "tip" not in candidate
