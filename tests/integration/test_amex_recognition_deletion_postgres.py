"""Recognized economic identity survives direct SQL deletion attempts."""

from uuid import uuid4

import asyncpg
import pytest

from tests.integration.test_amex_approved_informe_schema_postgres import (
    seed,
)

pytest_plugins = ["tests.integration.test_amex_approved_informe_schema_postgres"]


async def unposted_report(conn, item):
    """Create a real report source without any posting, bridge or cut witness."""
    expense, consumption = uuid4(), uuid4()
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO expense_reports(id,origen) VALUES($1,'informe_quick_entry')",
            expense,
        )
        await conn.execute(
            """INSERT INTO amex_recognition_consumptions(
                id,source_kind,binding_mode,source_report_expense_id,
                card_account_id,amount,currency,economic_date,
                liability_account_id,liability_code,actor_id)
               SELECT $1,'report','report',$2,card_account_id,amount,currency,
                      economic_date,liability_account_id,liability_code,actor_id
               FROM amex_recognition_consumptions WHERE id=$3""",
            consumption,
            expense,
            item.targets[0],
        )
        await conn.execute(
            """INSERT INTO amex_recognition_representations(
                   id,consumption_id,expense_id,role) VALUES($1,$2,$3,'report')""",
            uuid4(),
            consumption,
            expense,
        )
    return consumption


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity", ["unposted_report", "posted_report", "posted_legacy", "cut_legacy"]
)
@pytest.mark.parametrize(
    "operation", ["consumption", "representation_then_consumption"]
)
async def test_recognized_identity_cannot_be_deleted(schema, identity, operation):
    conn = schema.conn
    item = await seed(
        schema, (100,), mode="legacy", reference_header=identity == "cut_legacy"
    )
    if identity == "unposted_report":
        target = await unposted_report(conn, item)
    elif identity == "posted_report":
        target = item.targets[0]
    else:
        target = item.header
        if identity == "posted_legacy":
            await conn.execute(
                "UPDATE amex_recognition_consumptions SET accounting_poliza_id=$1 WHERE id=$2",
                item.poliza,
                target,
            )
    expected = (
        "representation"
        if operation == "representation_then_consumption"
        else "identity"
    )
    with pytest.raises(
        asyncpg.CheckViolationError, match=f"Recognized AMEX {expected}"
    ):
        async with conn.transaction():
            if operation == "representation_then_consumption":
                await conn.execute(
                    "DELETE FROM amex_recognition_representations WHERE consumption_id=$1",
                    target,
                )
            await conn.execute(
                "DELETE FROM amex_recognition_consumptions WHERE id=$1", target
            )
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM amex_recognition_consumptions WHERE id=$1", target
        )
        == 1
    )
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM amex_recognition_representations WHERE consumption_id=$1",
            target,
        )
        == 1
    )


@pytest.mark.asyncio
async def test_unposted_unreferenced_legacy_identity_remains_removable(schema):
    item = await seed(schema, (100,), mode="legacy")
    async with schema.conn.transaction():
        await schema.conn.execute(
            "DELETE FROM amex_recognition_representations WHERE consumption_id=$1",
            item.header,
        )
        await schema.conn.execute(
            "DELETE FROM amex_recognition_consumptions WHERE id=$1", item.header
        )
    assert (
        await schema.conn.fetchval(
            "SELECT count(*) FROM amex_recognition_consumptions WHERE id=$1",
            item.header,
        )
        == 0
    )
