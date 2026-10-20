"""Signed source and authority boundaries, independent of synthetic SOAP."""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_direct_common as common
from devnous.gastos.services import amex_direct_intake_service as intake


@pytest.mark.parametrize(
    "profile,amount,kind",
    [
        ("debit_negative", "-116.00", "debit"),
        ("debit_negative", "116.00", "credit"),
        ("debit_positive", "116.00", "debit"),
        ("debit_positive", "-116.00", "credit"),
        (None, "-116.00", "unknown"),
        ("other", "116.00", "unknown"),
        ("debit_negative", "0", "unknown"),
        ("debit_negative", "NaN", "unknown"),
        ("debit_negative", "Infinity", "unknown"),
        ("debit_negative", "-1.001", "unknown"),
        ("debit_negative", "not money", "unknown"),
        ("debit_negative", "", "unknown"),
    ],
)
def test_signed_rows_never_convert_credit_unknown_to_debit(profile, amount, kind):
    raw = f"Date,Description,Amount\n2026-10-02,Declared merchant,{amount}\n".encode()
    row = intake.parse_direct_csv(raw, profile, "MXN")[0]
    assert row["movement_kind"] == kind
    assert row["raw_amount"] == amount
    assert row["cells"]["Amount"] == amount
    assert (row["amount"] is not None) == (kind == "debit")
    if kind == "debit":
        assert row["raw_signed_amount"] == Decimal(amount)


@pytest.mark.parametrize(
    "debit,credit,kind",
    [
        ("116", "", "debit"),
        ("", "116", "credit"),
        ("116", "116", "unknown"),
        ("", "", "unknown"),
        ("-116", "", "unknown"),
        ("", "-116", "unknown"),
    ],
)
def test_separate_columns_require_one_explicit_positive_column(debit, credit, kind):
    row = intake.parse_direct_csv(
        (
            f"Date,Description,Debit,Credit\n" f"2026-10-02,Merchant,{debit},{credit}\n"
        ).encode(),
        "separate_columns",
        "MXN",
    )[0]
    assert row["movement_kind"] == kind
    assert row["amount"] == (Decimal("116") if kind == "debit" else None)


@pytest.mark.parametrize(
    "date,description,currency,uuid,expected",
    [
        ("bad", "Merchant", "MXN", "", "skipped"),
        ("2026-10-02", "", "MXN", "", "skipped"),
        ("2026-10-02", "Merchant", "USD", "", "unknown"),
        ("2026-10-02", "Merchant", "", "", "unknown"),
        ("2026-10-02", "Merchant", "MXN", "invalid", "unknown"),
    ],
)
def test_invalid_source_stays_explicit_without_zero(
    date, description, currency, uuid, expected
):
    raw = (
        "Date,Description,Amount,Currency,UUID\n"
        f"{date},{description},-116,{currency},{uuid}\n"
    ).encode()
    row = intake.parse_direct_csv(raw, "debit_negative", None)[0]
    assert row["movement_kind"] == expected
    assert row["amount"] is None
    assert row["reason"]


@pytest.mark.parametrize(
    "raw,reason",
    [
        (b"", "source_size_invalid"),
        (b"\xff", "invalid_utf8"),
        (b"Date,Description,Amount\n", "no_statement_rows"),
    ],
)
def test_source_is_bounded_and_not_reconstructed(raw, reason):
    with pytest.raises(common.DirectConflict, match=reason):
        intake.parse_direct_csv(raw, "debit_negative", "MXN")


def test_exact_signed_grouping_and_uuid_material_identity():
    uid = str(uuid4()).upper()
    row = intake.parse_direct_csv(
        (
            "Date,Description,Amount,UUID\n" f'2026-10-02,Merchant,"-1,234.56",{uid}\n'
        ).encode(),
        "debit_negative",
        "MXN",
    )[0]
    assert row["amount"] == Decimal("1234.56")
    assert row["raw_signed_amount"] == Decimal("-1234.56")
    assert row["declared_uuid"] == uid


@pytest.mark.parametrize("value", [None, "{}", "bad", "[{}]"])
def test_malformed_config_is_fail_closed(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", raising=False)
    else:
        monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", value)
    assert common.configured_scopes() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [None, SimpleNamespace(activo=False)])
async def test_inactive_actor_denied_before_any_source_or_catalog_query(actor):
    session = SimpleNamespace(get=AsyncMock(return_value=actor), execute=AsyncMock())
    with pytest.raises(common.DirectAccessError, match="active_actor_required"):
        await common.resolve_item_import(
            session, actor_id=uuid4(), action="ver", item_id=uuid4()
        )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("deny_action", ["ver", "editar", "confirmar"])
async def test_actual_and_read_and_edit_denials_all_precede_source_query(
    monkeypatch, deny_action
):
    actor = SimpleNamespace(activo=True, rol="finanzas")
    session = SimpleNamespace(get=AsyncMock(return_value=actor), execute=AsyncMock())

    async def decide(session, actor, tool, action):
        return False if action == deny_action else None

    monkeypatch.setattr(common, "explicit_tool_decision", decide)
    with pytest.raises(common.DirectAccessError, match="explicit_action_denied"):
        await common.load_statement(
            session, uuid4(), uuid4(), "confirmar", locked=True, finance=True
        )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_projection_disabled_config_has_no_sql(monkeypatch):
    from devnous.gastos.services.amex_direct_budget_service import (
        list_budget_direct_movements,
    )

    monkeypatch.delenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", raising=False)
    session = SimpleNamespace(execute=AsyncMock())
    assert await list_budget_direct_movements(session, 2026, None, None) == []
    session.execute.assert_not_awaited()


def test_configuration_requires_exact_single_entity_card_actor(monkeypatch):
    card, actor = uuid4(), uuid4()
    config = {
        "entity_code": "TEST",
        "company_code": "TEST",
        "receiver_rfc": "BBB010101BBB",
        "card_account_ids": [str(card)],
        "actor_scopes": [{"actor_id": str(actor)}],
    }
    monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps([config]))
    assert common.configuration_for(card, "TEST", "BBB010101BBB") == config
    assert common.configuration_for(card, "OTHER", "BBB010101BBB") is None
    assert common.actor_scope(config, uuid4()) is None
    monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps([config, config]))
    assert common.configuration_for(card, "TEST", "BBB010101BBB") is None


@pytest.mark.parametrize("value", ["NaN", "Infinity", None, "bad"])
def test_invalid_economic_amount_never_becomes_zero(value):
    with pytest.raises(common.DirectConflict, match="invalid_amount"):
        common.money(value)


@pytest.mark.parametrize("value", ["", "BBB010101", "not an RFC"])
def test_invalid_fiscal_receiver_is_not_inferred(value):
    with pytest.raises(common.DirectConflict, match="invalid_rfc"):
        common.normalize_rfc(value)


@pytest.mark.parametrize(
    "observed_request",
    [
        None,
        {},
        {"uuid": "broken"},
        {
            "uuid": "10000000-0000-4000-8000-000000000001",
            "rfc_emisor": "wrong",
            "rfc_receptor": "BBB010101BBB",
            "total": "116",
        },
    ],
)
def test_sat_observation_requires_exact_complete_request_identity(observed_request):
    from devnous.gastos.services.amex_direct_status_service import (
        _observed_request_matches,
    )

    identity = {
        "uuid": "10000000-0000-4000-8000-000000000001",
        "rfc_emisor": "AAA010101AAA",
        "rfc_receptor": "BBB010101BBB",
        "total": "116",
    }
    assert not _observed_request_matches({"request": observed_request}, identity)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proof,amount,currency,reason",
    [
        (False, "116", "MXN", "recognized_source_receipt_incomplete"),
        (True, "117", "MXN", "recognized_source_identity_changed"),
        (True, "116", "USD", "recognized_source_identity_changed"),
    ],
)
async def test_prior_recognition_requires_real_receipt_and_exact_economy(
    proof, amount, currency, reason
):
    from devnous.gastos.services.amex_direct_accounting_service import (
        covered_source_proof,
    )

    expense = SimpleNamespace(id=uuid4(), gasto_cantidad=Decimal("116"), currency="MXN")
    consumption = SimpleNamespace(
        id=uuid4(),
        accounting_poliza_id=uuid4(),
        binding_mode="direct",
        amount=amount,
        currency=currency,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: consumption),
                SimpleNamespace(first=lambda: ("real-receipt",) if proof else None),
            ]
        )
    )
    with pytest.raises(common.DirectConflict, match=reason):
        await covered_source_proof(session, expense)


@pytest.mark.asyncio
async def test_accounting_date_inside_statement_still_requires_budget_edition(
    monkeypatch,
):
    from datetime import date

    from devnous.gastos.services import amex_direct_accounting_service as accounting

    item = SimpleNamespace(id=uuid4(), movement_kind="debit", edition_year=2026)
    ready = common.DirectResult(
        "ready",
        statement=SimpleNamespace(
            period_start=date(2026, 12, 15), period_end=date(2027, 1, 15)
        ),
        source_key="source",
        source_version=7,
        items=[item],
        _sources={
            item.id: SimpleNamespace(
                movement_kind="debit",
                period_start=date(2026, 12, 15),
                period_end=date(2027, 1, 15),
            )
        },
    )
    monkeypatch.setattr(accounting, "_prepare", AsyncMock(return_value=ready))
    session = SimpleNamespace(execute=AsyncMock())
    result = await accounting.confirm_direct_accounting(
        session,
        actor_id=uuid4(),
        import_id=uuid4(),
        expected_source_key="source",
        expected_source_version=7,
        accounting_date=date(2027, 1, 5),
        confirmed=True,
        idempotency_key="edition",
    )
    assert result.reason == "accounting_date_outside_budget_edition"
    session.execute.assert_not_awaited()


def test_declared_period_preview_preserves_rows_and_reports_complete_coverage():
    from datetime import date

    raw = (
        b"Date,Description,Amount\n2026-08-31,Prior debit,-116.00\n"
        b"2026-09-01,Debit,-232.00\n2026-09-30,Credit,116.00\n"
        b"invalid,Unknown,-5.00\n"
    )
    coverage = intake.evaluate_direct_period(
        raw, "debit_negative", "MXN", date(2026, 9, 1), date(2026, 9, 30)
    )
    assert coverage["min_date"] == date(2026, 8, 31)
    assert coverage["max_date"] == date(2026, 9, 30)
    assert coverage["outside_rows"] == [
        {
            "row_number": 2,
            "date": date(2026, 8, 31),
            "reason": "row_outside_declared_period",
        }
    ]
    assert coverage["counts"] == {"debit": 2, "credit": 1, "unknown": 0, "skipped": 1}
    assert coverage["rows"] == intake.parse_direct_csv(raw, "debit_negative", "MXN")
    corrected = intake.evaluate_direct_period(
        raw, "debit_negative", "MXN", date(2026, 8, 31), date(2026, 9, 30)
    )
    assert corrected["outside_count"] == 0
