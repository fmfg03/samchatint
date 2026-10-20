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
    issuer_similarity,
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
            await conn.execute(
                text(
                    """CREATE TABLE cfdi_reports(
              id uuid PRIMARY KEY, cfdi_uuid text, emisor_rfc text, fecha timestamp,
              total double precision, emisor_nombre text,
              descripcion_concepto_principal text, origen text, receptor_rfc text,
              moneda text, tipo_de_comprobante text)"""
                )
            )
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
        text(
            """INSERT INTO cfdi_reports VALUES
      (:id,:uuid,:issuer,CAST(:stamp AS timestamp),:amount,:name,:description,
       'sat',:receiver,'MXN','I')"""
        ),
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
    expected = issuer_similarity("MANSION MÉXICO", "LA MANSIÓN México")
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


@pytest.mark.asyncio
async def test_suntory_named_issuer_beats_bakery_concept_and_airline_location(
    catalog_pg,
):
    conn = catalog_pg
    target = await insert(
        conn,
        name="RESTAURANTES SUNTORY",
        amount=3814,
        stamp="2026-10-06",
        description="Consumo de alimentos y bebidas",
    )
    await insert(
        conn,
        name="PASTELERIA Y CONFITERIA DE MEXICO",
        amount=637,
        stamp="2026-09-17",
        issuer="PAST010101AAA",
        description="Consumo restaurante",
    )
    await insert(
        conn,
        name="AEROVIAS DE MEXICO",
        amount=4396,
        stamp="2026-06-24",
        issuer="AERO010101AAA",
        description="Transporte aereo",
    )
    rows = await rank(
        conn,
        amount="4386.10",
        descriptor="RESTAURANTE SUNTORY SA  MEXICO",
    )
    assert rows[0]["cfdi_id"] == target
    assert rows[0]["amount_delta"] == Decimal("-572.10")
    assert rows[0]["days_delta"] == 7
    assert len(rows) == 3  # Nonmatching alternatives remain discoverable.
    assert await conn.scalar(text("SELECT count(*) FROM cfdi_reports")) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "descriptor,name,description",
    [
        ("RESTAURANTE SUNTORY SA MEXICO", "RESTAURANTES SUNTORY", "Alimentos"),
        ("LA MANSIÓN CDMX", "MANSION SA DE CV", "Restaurante"),
        ("MÉXICO SA DE CV RESTAURANTE", "RESTAURANTES DE MEXICO", "SUNTORY"),
        ("SUNTORY", None, "SUNTORY"),
        ("SUNTORY", "", "SUNTORY"),
        ("", "SUNTORY", "Alimentos"),
    ],
)
async def test_sql_python_named_issuer_similarity_parity(
    catalog_pg, descriptor, name, description
):
    conn = catalog_pg
    await insert(conn, name=name, description=description)
    rows = await rank(conn, descriptor=descriptor)
    assert len(rows) == 1
    expected = issuer_similarity(descriptor, name)
    assert abs(rows[0]["similarity"] - expected) < Decimal("0.000000000000000001")


@pytest.mark.asyncio
async def test_issuer_name_score_is_not_diluted_by_large_concepts(catalog_pg):
    conn = catalog_pg
    target = await insert(
        conn,
        name="RESTAURANTES SUNTORY",
        amount=3814,
        description=" ".join(f"detalle{n}" for n in range(500)),
    )
    await insert(
        conn,
        name="PASTELERIA Y CONFITERIA DE MEXICO",
        amount=3814,
        description="SUNTORY",
        issuer="OTRO010101AAA",
    )
    rows = await rank(conn, descriptor="RESTAURANTE SUNTORY SA MEXICO")
    assert rows[0]["cfdi_id"] == target
    assert rows[0]["similarity"] == 1
    assert rows[1]["similarity"] == 0


@pytest.mark.asyncio
async def test_explicit_identity_and_exact_amount_keep_priority_over_name(catalog_pg):
    conn = catalog_pg
    target = await insert(conn, name="RESTAURANTES SUNTORY", amount=3814)
    exact = await insert(conn, name="Other", amount=4386.10, issuer="OTHER010101A")
    explicit_uuid = str(uuid4())
    explicit = await insert(
        conn,
        name="Another",
        amount=50,
        fiscal_uuid=explicit_uuid,
        issuer="EXPL010101AAA",
    )
    rows = await rank(
        conn,
        amount="4386.10",
        descriptor="RESTAURANTE SUNTORY SA MEXICO",
        declared_uuid=explicit_uuid.upper(),
    )
    assert [r["cfdi_id"] for r in rows] == [explicit, exact, target]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [None, "RESTAURANTES DE MEXICO SA DE CV"])
async def test_missing_or_generic_names_use_fallback_without_hiding_rows(
    catalog_pg, name
):
    conn = catalog_pg
    target = await insert(
        conn, name=name, description="SUNTORY alimentos", issuer="FALL010101AAA"
    )
    await insert(
        conn, name=name, description="Transporte aereo", issuer="AERO010101AAA"
    )
    rows = await rank(conn, descriptor="SUNTORY RESTAURANTE MEXICO")
    assert rows[0]["cfdi_id"] == target
    assert all(row["similarity"] == 0 for row in rows)
    expected = issuer_similarity(
        "SUNTORY RESTAURANTE MEXICO", "FALL010101AAA SUNTORY alimentos"
    )
    assert abs(rows[0]["fallback_similarity"] - expected) < Decimal(
        "0.000000000000000001"
    )
    assert len(rows) == 2
