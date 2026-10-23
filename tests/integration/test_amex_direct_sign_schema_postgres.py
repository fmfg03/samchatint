"""Attack the owner-installed sign layer in disposable PostgreSQL only."""

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from devnous.gastos.services.amex_direct_schema_service import (
    inspect_amex_direct_schema,
)

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "sign_schema_fixture",
    ROOT / "tests/acceptance/test_amex_sat_budget_direct_postgres.py",
)
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
direct_pg = owner.direct_pg
FORWARD = ROOT / "database/migrations/20261022_amex_direct_sign_correction.sql"
ROLLBACK = (
    ROOT / "database/migrations/20261022_amex_direct_sign_correction_rollback.sql"
)


async def append_revision(pg, **changes):
    result = await owner._direct_import(
        pg,
        (
            b"Date,Description,Amount\n2026-10-02,GOOGLE,939.00\n"
            b"2026-10-03,AMAZON,-46.53\n"
        ),
        "debit_negative",
    )
    header = result.statement.id
    rows = await pg.conn.fetch(
        "SELECT * FROM amex_direct_items WHERE import_id=$1 ORDER BY id::text", header
    )
    file = await pg.conn.fetchrow(
        "SELECT * FROM amex_statement_source_files WHERE import_id=$1", header
    )
    state = await pg.conn.fetchrow(
        "SELECT * FROM amex_statement_direct_state WHERE import_id=$1", header
    )
    ids = [str(row["id"]) for row in rows]
    values = {
        "id": uuid4(),
        "import_id": header,
        "source_file_id": file["id"],
        "file_sha256": file["file_sha256"],
        "parser_version": "amex-direct-csv-v1",
        "original_profile": "debit_negative",
        "profile": "debit_positive",
        "context_id": state["context_id"],
        "context_version": state["context_version"],
        "expected_source_version": state["source_version"],
        "expected_count": len(ids),
        "expected_item_ids": ids,
        "original_counts": {"debit": 1, "credit": 1, "unknown": 0, "skipped": 0},
        "effective_counts": {"debit": 1, "credit": 1, "unknown": 0, "skipped": 0},
        "original_totals": {"debit": "46.53", "credit": "939.00"},
        "effective_totals": {"debit": "939.00", "credit": "46.53"},
        "actor_id": pg.actor.id,
        "reason": "Correct explicit bank sign declaration",
        "idempotency_key": "schema-attack",
        "request_hash": "a" * 64,
        "preview_hash": "b" * 64,
        "membership_digest": hashlib.sha256(",".join(ids).encode()).hexdigest(),
    }
    values.update(changes)
    columns = list(values)
    await pg.conn.execute(
        "INSERT INTO amex_direct_sign_revisions("
        + ",".join(columns)
        + ") VALUES("
        + ",".join(f"${i}" for i in range(1, len(columns) + 1))
        + ")",
        *(json.dumps(v) if isinstance(v, (list, dict)) else v for v in values.values()),
    )
    return values


@pytest.mark.asyncio
async def test_complete_owner_admission_and_empty_rollback(direct_pg):
    pg = direct_pg
    async with pg.factory() as session:
        assert (await inspect_amex_direct_schema(session))["ready"]
    # Unwind the newer empty account-policy layer before this historical owner.
    await direct_pg.conn.execute(
        (
            ROOT
            / "database/migrations/20261023_amex_direct_account_treatments_rollback.sql"
        ).read_text()
    )
    await pg.conn.execute(ROLLBACK.read_text())
    assert not await pg.conn.fetchval(
        "SELECT to_regclass('amex_direct_sign_revisions')"
    )
    await pg.conn.execute(FORWARD.read_text())
    await direct_pg.conn.execute(
        (
            ROOT / "database/migrations/20261023_amex_direct_account_treatments.sql"
        ).read_text()
    )
    async with pg.factory() as session:
        assert (await inspect_amex_direct_schema(session))["ready"]


@pytest.mark.asyncio
async def test_incomplete_revision_cannot_commit(direct_pg):
    pg = direct_pg
    async with pg.conn.transaction():
        with pytest.raises(
            asyncpg.CheckViolationError, match="complete exact membership"
        ):
            async with pg.conn.transaction():
                await append_revision(pg)
                await pg.conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_sign_revisions") == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing_row", "wrong_profile", "wrong_actor"])
async def test_revision_rejects_invalid_preimage(direct_pg, mutation):
    pg = direct_pg
    changes = {
        "missing_row": {"expected_count": 1},
        "wrong_profile": {
            "original_profile": "debit_positive",
            "profile": "debit_negative",
        },
        "wrong_actor": {"actor_id": pg.owner.id},
    }[mutation]
    async with pg.conn.transaction():
        with pytest.raises(asyncpg.CheckViolationError):
            async with pg.conn.transaction():
                await append_revision(pg, **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trigger", ["ad_sign_revision", "ad_sign_seal_row", "ad_sign_no_recovery"]
)
async def test_disabled_sign_guard_rejected_by_admission(direct_pg, trigger):
    pg = direct_pg
    table = {
        "ad_sign_revision": "amex_direct_sign_revisions",
        "ad_sign_seal_row": "amex_direct_sign_rows",
        "ad_sign_no_recovery": "amex_direct_coverage_revisions",
    }[trigger]
    await pg.conn.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    async with pg.factory() as session:
        result = await inspect_amex_direct_schema(session)
    assert not result["ready"]
    assert trigger in result["missing_triggers"]


@pytest.mark.asyncio
async def test_rollback_waits_for_confirm_and_preserves_committed_evidence(direct_pg):
    """The empty check cannot race a writer committing complete evidence."""
    pg = direct_pg
    module_spec = importlib.util.spec_from_file_location(
        "sign_rollback_acceptance",
        ROOT / "tests/integration/test_amex_direct_sign_correction_postgres.py",
    )
    acceptance = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(acceptance)
    view = await acceptance.imported(pg)
    request, planned = await acceptance.preview(pg, view)
    schema = await pg.conn.fetchval("SELECT current_schema()")
    # Reuse the fixture-validated endpoint and credentials for this second
    # independent connection; CI requires password authentication.
    database_url = pg.factory.kw["bind"].url
    rollback_conn = await asyncpg.connect(
        user=database_url.username,
        password=database_url.password,
        host=database_url.host,
        port=database_url.port,
        database=database_url.database,
        server_settings={"search_path": schema, "statement_timeout": "8000"},
    )
    rollback_task = None
    try:
        async with pg.factory.begin() as session:
            result = await acceptance.correction.confirm_direct_sign_correction(
                session,
                **request,
                preview_hash=planned.progress["preview_hash"],
                idempotency_key="concurrent-schema-rollback",
                confirmed=True,
            )
            assert result.status == "corrected", result.reason
            # Both owner scripts are one transaction here: a refusal in the
            # historical sign layer must also restore the newer empty layer.
            latest_rollback = (
                ROOT
                / "database/migrations/20261023_amex_direct_account_treatments_rollback.sql"
            ).read_text()

            def strip_transaction(sql):
                return sql.replace("\nBEGIN;\n", "\n", 1).rsplit("COMMIT;", 1)[0]

            rollback_sql = (
                "BEGIN;\n"
                + strip_transaction(latest_rollback)
                + strip_transaction(ROLLBACK.read_text())
                + "COMMIT;"
            )
            rollback_task = asyncio.create_task(rollback_conn.execute(rollback_sql))

            # Bounded witness of the actual PostgreSQL lock conflict, not timing.
            async def blocked():
                while True:
                    waiting = await pg.conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 "
                        "AND relation IN ('amex_direct_sign_revisions'::regclass, "
                        "'amex_direct_account_treatment_decisions'::regclass) "
                        "AND mode='AccessExclusiveLock' AND NOT granted)",
                        rollback_conn.get_server_pid(),
                    )
                    if waiting:
                        return
                    if rollback_task.done():
                        await rollback_task
                        pytest.fail("Rollback unexpectedly completed before commit")
                    await asyncio.sleep(0.02)

            await asyncio.wait_for(blocked(), timeout=3)
        with pytest.raises(asyncpg.RaiseError, match="evidence prevents rollback"):
            await asyncio.wait_for(rollback_task, timeout=5)
        await rollback_conn.execute("ROLLBACK")
        assert (
            await pg.conn.fetchval("SELECT count(*) FROM amex_direct_sign_revisions")
            == 1
        )
        assert (
            await pg.conn.fetchval("SELECT count(*) FROM amex_direct_sign_rows") == 16
        )
    finally:
        if rollback_task is not None and not rollback_task.done():
            rollback_task.cancel()
            await asyncio.gather(rollback_task, return_exceptions=True)
        await rollback_conn.close()
