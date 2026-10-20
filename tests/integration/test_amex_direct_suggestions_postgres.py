"""Execute the actual ranking SQL against a disposable PostgreSQL catalog.

This fixture deliberately isolates the read query; unit tests separately cover
owner authorization/orchestration. It is not an authenticated Finance UAT.
"""

import json
import os
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from devnous.gastos.services.amex_direct_suggestion_service import (
    _RANK_SQL,
    merchant_similarity,
)


@pytest_asyncio.fixture
async def catalog_pg():
    raw = os.getenv("TEST_AMEX_DATABASE_URL")
    if not raw:
        pytest.skip("Set isolated TEST_AMEX_DATABASE_URL")
    url = make_url(raw)
    if (
        url.host not in {"localhost", "127.0.0.1"}
        or url.port != 55471
        or url.username != "test_user"
        or url.database != "devnous_test"
    ):
        pytest.fail("Requires disposable localhost:55471/test_user/devnous_test")
    engine = create_async_engine(url)
    schema = "suggestions_" + uuid4().hex
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path="{schema}"'))
            await conn.execute(text("""CREATE TABLE cfdi_reports(
              id uuid PRIMARY KEY, cfdi_uuid text, emisor_rfc text, fecha timestamp,
              total double precision, emisor_nombre text,
              descripcion_concepto_principal text, origen text, receptor_rfc text,
              moneda text, tipo_de_comprobante text)"""))
            yield conn
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def insert(
    conn,
    *,
    name="SUNTORY",
    amount=3814,
    stamp="2026-10-06",
    receiver="BBB010101BBB",
    fiscal_uuid=None,
    issuer="AAA010101AAA",
    description="Alimentos",
):
    identity = uuid4()
    await conn.execute(
        text("""INSERT INTO cfdi_reports VALUES
      (:id,:uuid,:issuer,CAST(:stamp AS timestamp),:amount,:name,:description,
       'sat',:receiver,'MXN','I')"""),
        dict(
            id=identity,
            uuid=fiscal_uuid or str(identity),
            issuer=issuer,
            stamp=datetime.fromisoformat(stamp) if stamp else None,
            amount=amount,
            name=name,
            description=description,
            receiver=receiver,
        ),
    )
    return identity


async def rank(conn, **overrides):
    item = dict(
        item_id=str(uuid4()),
        amount="3814.00",
        bank_date="2026-09-29",
        descriptor="SUNTORY",
        declared_uuid=None,
        issuer_rfc=None,
        candidate_offset=0,
    )
    item.update(overrides)
    result = await conn.execute(
        _RANK_SQL, dict(items=json.dumps([item]), receiver="BBB010101BBB", take=4)
    )
    return result.mappings().all()


@pytest.mark.asyncio
async def test_entire_catalog_ranked_before_limit_cross_month_and_receiver_scope(
    catalog_pg,
):
    conn = catalog_pg
    for n in range(60):
        await insert(conn, name="Otro proveedor", amount=100 + n, stamp="2026-09-29")
    winner = await insert(conn)
    await insert(conn, receiver="FOREIGN", name="SUNTORY", stamp="2026-09-29")
    rows = await rank(conn)
    assert rows[0]["cfdi_id"] == winner and rows[0]["days_delta"] == 7
    assert rows[0]["amount_delta"] == Decimal("0.00")
    assert rows[0]["catalog_count"] == 61 and len(rows) == 4


@pytest.mark.asyncio
async def test_distant_year_is_not_filtered_and_literal_descriptor_cannot_inject(
    catalog_pg,
):
    conn = catalog_pg
    identity = await insert(conn, stamp="2023-01-01")
    rows = await rank(conn, descriptor="%_' ; DROP TABLE cfdi_reports; --")
    assert rows[0]["cfdi_id"] == identity
    assert rows[0]["days_delta"] == (date(2023, 1, 1) - date(2026, 9, 29)).days
    assert await conn.scalar(text("SELECT count(*) FROM cfdi_reports")) == 1


@pytest.mark.asyncio
async def test_identity_exact_amount_similarity_relative_delta_date_uuid_order(
    catalog_pg,
):
    conn = catalog_pg
    explicit_uuid = str(uuid4())
    explicit = await insert(conn, name="Other", amount=50, fiscal_uuid=explicit_uuid)
    exact = await insert(conn, name="SUNTORY", amount=3814)
    near = await insert(conn, name="SUNTORY", amount=3813.99)
    rows = await rank(conn, declared_uuid=explicit_uuid.upper())
    assert [r["cfdi_id"] for r in rows] == [explicit, exact, near]
    rows = await rank(conn, issuer_rfc="AAA010101AAA")
    assert rows[0]["cfdi_id"] == exact and rows[1]["cfdi_id"] == near
    assert rows[1]["amount_delta"] == Decimal("-0.01")


@pytest.mark.asyncio
async def test_sql_python_similarity_parity_missing_dates_and_nonfinite_money(
    catalog_pg,
):
    conn = catalog_pg
    await insert(
        conn, name="LA MANSIÓN México", issuer="AAA010101AAA", description="Restaurante"
    )
    for invalid in [float("nan"), float("inf"), float("-inf"), -1, 0]:
        await insert(conn, amount=invalid)
    rows = await rank(conn, descriptor="MANSION MÉXICO", bank_date=None)
    assert len(rows) == 1 and rows[0]["days_delta"] is None
    expected = merchant_similarity(
        "MANSION MÉXICO", "LA MANSIÓN México AAA010101AAA Restaurante"
    )
    assert abs(rows[0]["similarity"] - expected) < Decimal("0.000000000000000001")


@pytest.mark.asyncio
async def test_pagination_stable_and_same_invoice_proposed_for_two_items_without_writes(
    catalog_pg,
):
    conn = catalog_pg
    for n in range(9):
        await insert(conn, fiscal_uuid=f"00000000-0000-4000-8000-{n:012d}")
    first = await rank(conn)
    second = await rank(conn, candidate_offset=4)
    assert not {r["cfdi_id"] for r in first} & {r["cfdi_id"] for r in second}
    assert [r["cfdi_id"] for r in first] == [r["cfdi_id"] for r in await rank(conn)]
    assert await conn.scalar(text("SELECT count(*) FROM cfdi_reports")) == 9
