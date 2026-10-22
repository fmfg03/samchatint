"""Direct SQL attacks against the actual no-CFDI owner migration."""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "no_cfdi_schema_fixture",
    ROOT / "tests/acceptance/test_amex_sat_budget_direct_postgres.py",
)
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
direct_pg = owner.direct_pg
FORWARD = ROOT / "database/migrations/20261021_amex_direct_no_cfdi.sql"
ROLLBACK = ROOT / "database/migrations/20261021_amex_direct_no_cfdi_rollback.sql"


async def source(pg, profile="debit_negative"):
    amount = "-116.00" if profile == "debit_negative" else "116.00"
    result = await owner._direct_import(
        pg,
        f"Date,Description,Amount\n2026-10-02,NO FISCAL RECEIPT,{amount}\n".encode(),
        profile,
    )
    return await pg.conn.fetchrow(
        "SELECT * FROM amex_direct_items WHERE import_id=$1", result.statement.id
    )


async def append(pg, item, *, action="declare", event=True, pointer=True, **changes):
    """Construct real evidence directly, independently of the decision service."""
    c = pg.conn
    old = (
        await c.fetchrow(
            "SELECT * FROM amex_direct_no_cfdi_decisions WHERE id=$1",
            item["no_cfdi_decision_id"],
        )
        if item["no_cfdi_decision_id"]
        else None
    )
    decision = {
        "id": uuid4(),
        "item_id": item["id"],
        "import_id": item["import_id"],
        "imported_expense_id": item["imported_expense_id"],
        "context_id": item["context_id"],
        "context_version": item["context_version"],
        "revision": old["revision"] + 1 if old else 1,
        "action": action,
        "previous_decision_id": old["id"] if old else None,
        "treatment": "full_amount_expense_no_tax",
        "reason": "Explicit bank evidence",
        "actor_id": pg.actor.id,
        "request_id": str(uuid4()),
        "idempotency_key": uuid4().hex * 2,
        "payload_hash": "a" * 64,
        "economic_source_hash": await c.fetchval(
            "SELECT amex_direct_no_cfdi_source_hash($1)", item["id"]
        ),
        "versions_json": json.dumps(
            {
                "confirmed": True,
                "expected_versions": {str(item["id"]): item["source_version"]},
            }
        ),
    }
    if action == "revoke" and old:
        for key in ("context_id", "context_version", "economic_source_hash"):
            decision[key] = old[key]
    decision.update(changes)
    cols = list(decision)
    await c.execute(
        "INSERT INTO amex_direct_no_cfdi_decisions("
        + ",".join(cols)
        + ") VALUES("
        + ",".join(f"${i}" for i in range(1, len(cols) + 1))
        + ")",
        *(decision[k] for k in cols),
    )
    if pointer:
        await c.execute(
            (
                "UPDATE amex_direct_items SET no_cfdi_dec"
                "ision_id=$1,source_version=source_versio"
                "n+1 WHERE id=$2"
            ),
            decision["id"],
            item["id"],
        )
    if event:
        payload = {
            "no_cfdi_decision_id": str(decision["id"]),
            "confirmed": True,
            "reason": decision["reason"],
            "request_payload_hash": decision["payload_hash"],
        }
        await c.execute(
            (
                "INSERT INTO amex_direct_events(id,item_i"
                "d,action,actor_id,source_version,source_"
                "hash,payload_hash,payload_json) SELECT $"
                "1,id,$2,$3,source_version,source_hash,$4"
                ",$5 FROM amex_direct_items WHERE id=$6"
            ),
            uuid4(),
            "no_cfdi_" + action,
            pg.actor.id,
            "b" * 64,
            json.dumps(payload),
            item["id"],
        )
    await c.execute("SET CONSTRAINTS ALL IMMEDIATE")
    await c.execute("SET CONSTRAINTS ALL DEFERRED")
    return decision


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["debit_negative", "debit_positive"])
async def test_signed_source_hash_and_append_only_declare_revoke(direct_pg, profile):
    pg = direct_pg
    item = await source(pg, profile)
    async with pg.conn.transaction():
        first = await append(pg, item)
    with pytest.raises(asyncpg.CheckViolationError, match="immutable"):
        await pg.conn.execute(
            "UPDATE amex_direct_no_cfdi_decisions SET reason='changed' WHERE id=$1",
            first["id"],
        )
    with pytest.raises(asyncpg.CheckViolationError, match="immutable"):
        await pg.conn.execute(
            "DELETE FROM amex_direct_no_cfdi_decisions WHERE id=$1", first["id"]
        )
    with pytest.raises(asyncpg.CheckViolationError, match="revocation"):
        await pg.conn.execute(
            "UPDATE expense_reports SET cfdi_uuid_manual='fabricated' WHERE id=$1",
            item["imported_expense_id"],
        )
    current = await pg.conn.fetchrow(
        "SELECT * FROM amex_direct_items WHERE id=$1", item["id"]
    )
    async with pg.conn.transaction():
        second = await append(pg, current, action="revoke")
    assert second["previous_decision_id"] == first["id"]
    await pg.conn.execute(
        "UPDATE expense_reports SET cfdi_uuid_manual='new evidence' WHERE id=$1",
        item["imported_expense_id"],
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_no_cfdi_decisions")
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["hash", "context", "actor", "confirmed", "event", "pointer", "revision"]
)
async def test_forged_receipt_or_missing_seal_rolls_back_entire_transaction(
    direct_pg, damage
):
    pg = direct_pg
    item = await source(pg)
    changes = {}
    options = {}
    if damage == "hash":
        changes["economic_source_hash"] = "f" * 64
    if damage == "context":
        changes["context_version"] = 999
    if damage == "actor":
        changes["actor_id"] = pg.owner.id
    if damage == "confirmed":
        changes["versions_json"] = '{"confirmed":false}'
    if damage == "event":
        options["event"] = False
    if damage == "pointer":
        options["pointer"] = False
    if damage == "revision":
        changes["revision"] = 2
    with pytest.raises(asyncpg.CheckViolationError):
        async with pg.conn.transaction():
            await append(pg, item, **options, **changes)
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_no_cfdi_decisions")
        == 0
    )
    assert (
        await pg.conn.fetchval(
            "SELECT no_cfdi_decision_id FROM amex_direct_items WHERE id=$1", item["id"]
        )
        is None
    )


@pytest.mark.asyncio
async def test_current_head_cannot_be_cleared_or_reassigned(direct_pg):
    pg = direct_pg
    item = await source(pg)
    async with pg.conn.transaction():
        await append(pg, item)
    with pytest.raises(asyncpg.CheckViolationError, match="next append-only"):
        await pg.conn.execute(
            "UPDATE amex_direct_items SET no_cfdi_decision_id=NULL WHERE id=$1",
            item["id"],
        )


@pytest.mark.asyncio
async def test_empty_owner_rollback_roundtrip_and_history_refusal(direct_pg):
    pg = direct_pg
    # Roll back newer owner layers before this historical migration.
    sign_migrations = ROOT / "database/migrations"
    await pg.conn.execute(
        (
            sign_migrations / "20261022_amex_direct_sign_correction_rollback.sql"
        ).read_text()
    )
    await pg.conn.execute(ROLLBACK.read_text())
    assert (
        await pg.conn.fetchval("SELECT to_regclass('amex_direct_no_cfdi_decisions')")
        is None
    )
    await pg.conn.execute(FORWARD.read_text())
    await pg.conn.execute(
        (sign_migrations / "20261022_amex_direct_sign_correction.sql").read_text()
    )
    from devnous.gastos.services.amex_direct_schema_service import (
        inspect_amex_direct_schema,
    )

    async with pg.factory() as session:
        result = await inspect_amex_direct_schema(session)
        assert result["ready"], result
    item = await source(pg)
    async with pg.conn.transaction():
        await append(pg, item)
    with pytest.raises(asyncpg.RaiseError, match="evidence prevents rollback"):
        await pg.conn.execute(ROLLBACK.read_text())
    await pg.conn.execute("ROLLBACK")
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_no_cfdi_decisions")
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["reason", "treatment", "amount", "account", "tax"])
async def test_forged_frozen_snapshot_or_journal_is_rejected(
    direct_pg, monkeypatch, damage
):
    """Tamper proposed SQL INSERT rows; keep all database guards enabled."""
    import copy

    from sqlalchemy import event
    from sqlalchemy.exc import DBAPIError

    from devnous.gastos.models import AccountingPolizaLine, AmexDirectCutItem

    pg = direct_pg
    item = await source(pg)
    async with pg.conn.transaction():
        await append(pg, item)
    view = await owner._direct_current(pg, item["import_id"])
    view = await owner._assign_all_items(pg, view)
    cut_args = await owner._cut_args(pg, view)
    target = (
        AccountingPolizaLine
        if damage in {"amount", "account", "tax"}
        else AmexDirectCutItem
    )

    def corrupt(mapper, connection, row):
        if target is AmexDirectCutItem:
            row.snapshot_json = copy.deepcopy(row.snapshot_json)
            row.snapshot_json["no_cfdi_decision"][damage] = "fabricated frozen evidence"
        elif row.debe:
            if damage == "amount":
                row.debe += 1
            elif damage == "account":
                row.cuenta_contable_id = pg.liability.id
            else:
                row.raw_row_json = {**row.raw_row_json, "movement": "debe_iva"}

    event.listen(target, "before_insert", corrupt)
    try:
        with pytest.raises(DBAPIError):
            await owner._confirm(pg, cut_args)
    finally:
        event.remove(target, "before_insert", corrupt)
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_accounting_cuts") == 0
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 0
    )


@pytest.mark.asyncio
async def test_merchant_rfc_alone_preserved_without_fabricating_invoice(direct_pg):
    pg = direct_pg
    item = await source(pg)
    await pg.conn.execute(
        "UPDATE amex_direct_items SET issuer_rfc='AAA010101AAA' WHERE id=$1", item["id"]
    )
    item = await pg.conn.fetchrow(
        "SELECT * FROM amex_direct_items WHERE id=$1", item["id"]
    )
    async with pg.conn.transaction():
        await append(pg, item)
    assert (
        await pg.conn.fetchval(
            "SELECT issuer_rfc FROM amex_direct_items WHERE id=$1", item["id"]
        )
        == "AAA010101AAA"
    )
    assert (
        await pg.conn.fetchval(
            "SELECT cfdi_id FROM amex_direct_items WHERE id=$1", item["id"]
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "duplicate_debit",
        "duplicate_credit",
        "duplicate_header",
        "root_extra",
        "root_missing",
        "orphan_root_extra",
        "orphan_header",
        "concept",
    ],
)
async def test_frozen_movements_require_exact_multisets_before_recognition(
    direct_pg, damage
):
    """Reject duplicate/missing frozen movements with the real ledger intact."""
    import copy
    from decimal import Decimal

    from sqlalchemy import event
    from sqlalchemy.exc import DBAPIError

    from devnous.gastos.models import AmexDirectAccountingCut, AmexDirectCutItem

    pg = direct_pg
    item = await source(pg)
    async with pg.conn.transaction():
        await append(pg, item)
    view = await owner._direct_current(pg, item["import_id"])
    view = await owner._assign_all_items(pg, view)
    cut_args = await owner._cut_args(pg, view)

    def corrupt(mapper, connection, row):
        row.snapshot_json = copy.deepcopy(row.snapshot_json)
        snapshot = row.snapshot_json
        if damage in {"duplicate_debit", "duplicate_credit", "concept"}:
            parts = (
                snapshot["partidas"]
                if isinstance(row, AmexDirectAccountingCut)
                else [snapshot]
            )
            side = "debe" if damage in {"duplicate_debit", "concept"} else "haber"
            for part in parts:
                movement = next(
                    line for line in part["journal_lines"] if Decimal(line[side]) > 0
                )
                if damage == "concept":
                    movement["concepto"] = "forged frozen concept"
                else:
                    part["journal_lines"] = [
                        copy.deepcopy(movement),
                        copy.deepcopy(movement),
                    ]
            if damage == "concept" and isinstance(row, AmexDirectAccountingCut):
                movement = next(
                    line for line in snapshot["lines"] if Decimal(line["debe"]) > 0
                )
                movement["concepto"] = "forged frozen concept"
        elif isinstance(row, AmexDirectAccountingCut):
            if damage == "duplicate_header":
                snapshot["partidas"].append(copy.deepcopy(snapshot["partidas"][0]))
            elif damage == "orphan_header":
                orphan = copy.deepcopy(snapshot["partidas"][0])
                orphan["direct_item_id"] = str(uuid4())
                snapshot["partidas"].append(orphan)
            elif damage == "orphan_root_extra":
                movement = copy.deepcopy(snapshot["lines"][0])
                for key in ("direct_item_id", "expense_id", "no_cfdi_decision_id"):
                    movement["raw_row_json"].pop(key)
                snapshot["lines"].append(movement)
            elif damage == "root_extra":
                snapshot["lines"].append(copy.deepcopy(snapshot["lines"][0]))
            else:
                snapshot["lines"].pop()

    for model in (AmexDirectAccountingCut, AmexDirectCutItem):
        event.listen(model, "before_insert", corrupt)
    try:
        with pytest.raises(DBAPIError, match="exact full amount"):
            await owner._confirm(pg, cut_args)
    finally:
        for model in (AmexDirectAccountingCut, AmexDirectCutItem):
            event.remove(model, "before_insert", corrupt)
    for table in (
        "amex_direct_accounting_cuts",
        "amex_direct_cut_items",
        "accounting_polizas",
        "accounting_poliza_lines",
        "amex_recognition_consumptions",
        "amex_recognition_representations",
    ):
        assert await pg.conn.fetchval("SELECT count(*) FROM " + table) == 0
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_no_cfdi_decisions")
        == 1
    )
