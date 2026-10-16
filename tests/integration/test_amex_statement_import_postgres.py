"""Owner DDL and transactional intake in isolated localhost schemas."""

import asyncio
import os
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import ForeignKeyConstraint, MetaData, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devnous.gastos import models
from devnous.gastos.amex_schema_policy import (
    AMEX_DIRECT_OWNER_MIGRATION_TABLES,
    AMEX_OWNER_MIGRATION_TABLES,
    AMEX_STATEMENT_OWNER_MIGRATION_TABLES,
)
from devnous.gastos.services import amex_statement_import_service as service
from devnous.gastos.services.amex_statement_schema_service import (
    inspect_amex_statement_schema,
)

ROOT = Path(__file__).resolve().parents[2]
CSV = b"Date,Description,Amount\n2026-09-30,Merchant,229.98\n2026-10-01,,1\n"


@pytest_asyncio.fixture
async def pg(monkeypatch):
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
        pytest.fail("Isolated localhost database required")
    name = "statement_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as c:
        await c.execute(text(f'CREATE SCHEMA "{name}"'))
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": name}}
    )
    metadata = MetaData()
    for table in models.Base.metadata.tables.values():
        if table.name in (
            AMEX_STATEMENT_OWNER_MIGRATION_TABLES
            | AMEX_DIRECT_OWNER_MIGRATION_TABLES
            | AMEX_OWNER_MIGRATION_TABLES
        ):
            continue
        clone = table.to_metadata(metadata)
        for constraint in list(clone.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                clone.constraints.remove(constraint)
                for element in constraint.elements:
                    clone.foreign_keys.discard(element)
                    element.parent.foreign_keys.discard(element)
    async with engine.begin() as c:
        await c.run_sync(metadata.create_all)
    conn = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=url.database,
        server_settings={"search_path": name},
    )
    for filename in (
        "20261001_amex_shared_recognition.sql",
        "20261013_amex_approved_informe_recognition.sql",
        "20261013_amex_statement_workflow.sql",
        "20261014_amex_sat_budget_direct.sql",
    ):
        if filename == "20261014_amex_sat_budget_direct.sql":
            from samchat.budgets.service import ensure_budget_schema

            async with async_sessionmaker(engine).begin() as fixture_session:
                await ensure_budget_schema(fixture_session)
        await conn.execute((ROOT / "database/migrations" / filename).read_text())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    actor = models.Empleado(id=uuid4(), nombre="Finance", rol="finanzas", activo=True)
    employee = models.Empleado(id=uuid4(), nombre="Owner", rol="empleado", activo=True)
    card = models.AmexCardAccount(
        id=uuid4(),
        card_label="Test",
        cardholder_key="empresa",
        last4="5007",
        liability_cuenta_contable_id=uuid4(),
        active=True,
    )
    async with factory.begin() as s:
        s.add_all([actor, employee, card])

    async def create(session, **kwargs):
        expense = models.ExpenseReport(
            id=uuid4(),
            proyecto="Test",
            concepto=kwargs["concepto"],
            gasto_cantidad=kwargs["gasto_cantidad"],
            fecha=kwargs["fecha"],
            empleado_id=kwargs["empleado_id"],
            metodo_pago=kwargs["metodo_pago"],
            ultimos_4_digitos=kwargs["ultimos_4_digitos"],
            origen=kwargs["origen"],
        )
        session.add(expense)
        await session.flush()
        return expense

    async def match(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "create_expense_from_data", create)
    monkeypatch.setattr(service, "find_amex_cfdi_match", match)
    try:
        yield SimpleNamespace(
            factory=factory, conn=conn, actor=actor, employee=employee, card=card
        )
    finally:
        if conn.is_in_transaction():
            await conn.execute("ROLLBACK")
        await conn.close()
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
        await admin.dispose()


async def intake(pg, **kwargs):
    async with pg.factory.begin() as s:
        return await service.import_amex_statement(
            s,
            pg.actor,
            pg.card.id,
            pg.employee.id,
            kwargs.pop("period_start", date(2026, 10, 1)),
            date(2026, 10, 31),
            kwargs.pop("filename", "statement.csv"),
            kwargs.pop("content", CSV),
            **kwargs,
        )


@pytest.mark.asyncio
async def test_readiness_and_replay_concurrency(pg):
    async with pg.factory() as s:
        ready = await inspect_amex_statement_schema(s)
        assert ready["ready"], ready
    results = await asyncio.gather(intake(pg), intake(pg))
    assert sorted(r["status"] for r in results) == ["imported", "replay"]
    assert results[0]["import"].id == results[1]["import"].id
    async with pg.factory() as s:
        assert (
            await s.scalar(select(func.count()).select_from(models.ExpenseReport)) == 1
        )
        items = (
            (await s.execute(select(models.AmexStatementImportItem))).scalars().all()
        )
        assert len(items) == 2
        assert sorted(i.status for i in items) == ["imported", "skipped"]
        expense = await s.get(
            models.ExpenseReport,
            next(i.imported_expense_id for i in items if i.status == "imported"),
        )
        assert expense.fecha == datetime(
            2026, 9, 30
        )  # Declaration does not rewrite date.


@pytest.mark.asyncio
async def test_context_conflicts_and_additional_explicit(pg):
    assert (await intake(pg))["status"] == "imported"
    assert (await intake(pg, period_start=date(2026, 9, 1)))[
        "status"
    ] == "context_conflict"
    different = CSV.replace(b"229.98", b"99.00")
    assert (await intake(pg, content=different))[
        "status"
    ] == "additional_confirmation_required"
    assert (await intake(pg, content=different, additional_confirmed=True))[
        "status"
    ] == "imported"


@pytest.mark.asyncio
async def test_failed_transaction_has_no_phantom_state(pg, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("synthetic database failure")

    monkeypatch.setattr(service, "find_amex_cfdi_match", fail)
    with pytest.raises(RuntimeError):
        await intake(pg)
    async with pg.factory() as s:
        assert (
            await s.scalar(select(func.count()).select_from(models.ExpenseReport)) == 0
        )
        assert (
            await s.scalar(select(func.count()).select_from(models.AmexStatementImport))
            == 0
        )


@pytest.mark.asyncio
async def test_all_invalid_is_not_loaded(pg):
    assert (await intake(pg, content=b"Date,Description,Amount\ninvalid,,zero\n"))[
        "status"
    ] == "invalid"
    async with pg.factory() as s:
        assert (
            await s.scalar(select(func.count()).select_from(models.AmexStatementImport))
            == 0
        )


@pytest.mark.asyncio
async def test_receipts_and_expense_anchor_are_immutable(pg):
    await intake(pg)
    for sql in [
        "UPDATE amex_statement_imports SET filename='other.csv'",
        "DELETE FROM amex_statement_import_items",
        "UPDATE expense_reports SET ultimos_4_digitos='9999'",
    ]:
        with pytest.raises(asyncpg.PostgresError):
            await pg.conn.execute(sql)
    with pytest.raises(asyncpg.PostgresError):
        await pg.conn.execute(
            (
                ROOT
                / "database/migrations/20261013_amex_statement_workflow_rollback.sql"
            ).read_text()
        )


@pytest.mark.asyncio
async def test_missing_guard_fails_readiness(pg):
    await pg.conn.execute(
        (
            "ALTER TABLE amex_statement_imports DISABLE TRIGGER amex_st"
            "atement_import_guard"
        )
    )
    async with pg.factory() as s:
        assert not (await inspect_amex_statement_schema(s))["ready"]


@pytest.mark.asyncio
async def test_empty_schema_rollback_allowed(pg):
    await pg.conn.execute(
        (
            ROOT / "database/migrations/20261014_amex_sat_budget_direct_rollback.sql"
        ).read_text()
    )
    await pg.conn.execute(
        (
            ROOT / "database/migrations/20261013_amex_statement_workflow_rollback.sql"
        ).read_text()
    )
    async with pg.factory() as s:
        assert not (await inspect_amex_statement_schema(s))["ready"]


@pytest.mark.asyncio
async def test_actor_and_inactive_context_rejected_without_writes(pg):
    pg.actor.rol = "empleado"
    with pytest.raises(PermissionError):
        await intake(pg)
    pg.actor.rol = "finanzas"
    async with pg.factory.begin() as s:
        card = await s.get(models.AmexCardAccount, pg.card.id)
        card.active = False
    assert (await intake(pg))["reason"] == "inactive_context"
    async with pg.factory() as s:
        assert (
            await s.scalar(select(func.count()).select_from(models.ExpenseReport)) == 0
        )


@pytest.mark.asyncio
async def test_metadata_inspector_rejects_weakened_check(pg):
    await pg.conn.execute(
        (
            "ALTER TABLE amex_statement_imports DROP CONSTRAINT ck_amex"
            "_statement_counts"
        )
    )
    await pg.conn.execute(
        (
            "ALTER TABLE amex_statement_imports ADD CONSTRAINT ck_amex_"
            "statement_counts CHECK(imported_count >= 0)"
        )
    )
    async with pg.factory() as s:
        result = await inspect_amex_statement_schema(s)
        assert not result["ready"] and result["schema_errors"]


@pytest.mark.asyncio
async def test_direct_sql_count_and_foreign_key_guards(pg):
    receipt_id = uuid4()
    with pytest.raises(asyncpg.PostgresError, match="row counts"):
        async with pg.conn.transaction():
            await pg.conn.execute(
                (
                    "INSERT INTO amex_statement_imports(id,card_account_id,empl"
                    "oyee_id,period_start,period_end,file_sha256,filename,parse"
                    "r_version,actor_id,total_rows,imported_count,skipped_count"
                    ",summary_json) VALUES($1,$2,$3,'2026-10-01','2026-10-31',$"
                    "4,'test.csv','v1',$5,1,1,0,'{}')"
                ),
                receipt_id,
                pg.card.id,
                pg.employee.id,
                "a" * 64,
                pg.actor.id,
            )
    await intake(pg)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pg.conn.execute(
            (
                "INSERT INTO amex_statement_cut_links(id,import_id,cut_id,a"
                "ctor_id,request_payload_hash,request_payload_json) VALUES("
                "$1,(SELECT id FROM amex_statement_imports LIMIT 1),$2,$3,$"
                "4,'{}')"
            ),
            uuid4(),
            uuid4(),
            pg.actor.id,
            "a" * 64,
        )
    with pytest.raises(asyncpg.PostgresError, match="after intake"):
        await pg.conn.execute(
            (
                "INSERT INTO amex_statement_import_items(id,import_id,row_n"
                "umber,status,reason_code,normalized_json) VALUES($1,(SELEC"
                "T id FROM amex_statement_imports LIMIT 1),99,'skipped','in"
                "valid_date','{}')"
            ),
            uuid4(),
        )


@pytest.mark.asyncio
async def test_monthly_conflict_and_invalid_period(pg):
    assert (await intake(pg, period_start=date(2026, 11, 1)))[
        "reason"
    ] == "invalid_period"
    assert (await intake(pg, cfdi_uuid_mensual="bad"))[
        "reason"
    ] == "invalid_monthly_cfdi"
    assert (await intake(pg, cfdi_uuid_mensual=str(uuid4())))[
        "reason"
    ] == "monthly_cfdi_multiple_months"
    one_month = CSV.replace(b"2026-09-30", b"2026-10-01")
    monthly = str(uuid4())
    first = await intake(pg, content=one_month, cfdi_uuid_mensual=monthly)
    assert first["status"] == "imported"
    assert (await intake(pg, content=one_month, cfdi_uuid_mensual=str(uuid4())))[
        "reason"
    ] == "monthly_cfdi_conflict"


@pytest.mark.asyncio
async def test_list_get_and_blank_monthly_preserve_multimonth(pg):
    receipt = (await intake(pg, cfdi_uuid_mensual="   "))["import"]
    async with pg.factory() as s:
        items = await service.list_amex_statement_imports(s)
        assert [item.id for item in items] == [receipt.id]
        found = await service.get_amex_statement_import(s, receipt.id)
        assert found.filename == "statement.csv"
        assert await service.get_amex_statement_import(s, uuid4()) is None
        assert (await intake(pg))["status"] == "replay"


@pytest.mark.asyncio
async def test_same_named_guard_with_wrong_events_is_not_admitted(pg):
    await pg.conn.execute(
        "DROP TRIGGER amex_statement_import_guard ON amex_statement_imports"
    )
    await pg.conn.execute(
        "CREATE TRIGGER amex_statement_import_guard BEFORE UPDATE "
        "ON amex_statement_imports FOR EACH ROW "
        "EXECUTE FUNCTION amex_statement_immutable()"
    )
    async with pg.factory() as s:
        result = await inspect_amex_statement_schema(s)
        assert not result["ready"]
        assert "amex_statement_import_guard" in result["missing_triggers"]


@pytest.mark.asyncio
async def test_same_named_guard_with_weakened_body_is_not_admitted(pg):
    await pg.conn.execute(
        "CREATE OR REPLACE FUNCTION amex_statement_immutable() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;"
    )
    async with pg.factory() as s:
        result = await inspect_amex_statement_schema(s)
        assert not result["ready"]
        assert any("immutable guard body" in error for error in result["schema_errors"])


@pytest.mark.asyncio
async def test_intake_inside_nested_savepoints_succeeds_and_stays_sealed(pg):
    async with pg.factory.begin() as session:
        async with session.begin_nested():
            async with session.begin_nested():
                result = await service.import_amex_statement(
                    session,
                    pg.actor,
                    pg.card.id,
                    pg.employee.id,
                    date(2026, 10, 1),
                    date(2026, 10, 31),
                    "statement.csv",
                    CSV,
                )
                assert result["status"] == "imported"
        async with session.begin_nested():
            replay = await service.import_amex_statement(
                session,
                pg.actor,
                pg.card.id,
                pg.employee.id,
                date(2026, 10, 1),
                date(2026, 10, 31),
                "statement.csv",
                CSV,
            )
            assert replay["status"] == "replay"
    async with pg.factory.begin() as session:
        with pytest.raises(DBAPIError, match="after intake"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO amex_statement_import_items "
                        "(id,import_id,row_number,status,reason_code,normalized_json) "
                        "VALUES(:id,:import_id,99,'skipped','invalid_date','{}')"
                    ),
                    {"id": uuid4(), "import_id": result["import"].id},
                )
    async with pg.factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AmexStatementImportItem)
            )
            == 2
        )


@pytest.mark.asyncio
async def test_deferred_item_counts_reject_partial_membership_across_savepoints(pg):
    # Prove the item guard itself checks the final set independently of the
    # header event; production admission requires both enabled guards.
    await pg.conn.execute(
        "DROP TRIGGER amex_statement_counts_guard ON amex_statement_imports"
    )
    receipt_id = uuid4()
    with pytest.raises(asyncpg.PostgresError, match="row counts"):
        async with pg.conn.transaction():
            async with pg.conn.transaction():
                await pg.conn.execute(
                    "INSERT INTO amex_statement_imports "
                    "(id,card_account_id,employee_id,period_start,period_end, "
                    "file_sha256,filename,parser_version,actor_id,total_rows, "
                    "imported_count,skipped_count,summary_json) VALUES "
                    "($1,$2,$3,'2026-10-01','2026-10-31',$4,'test.csv','v1',"
                    "$5,2,1,1,'{}')",
                    receipt_id,
                    pg.card.id,
                    pg.employee.id,
                    "b" * 64,
                    pg.actor.id,
                )
            async with pg.conn.transaction():
                await pg.conn.execute(
                    "INSERT INTO amex_statement_import_items "
                    "(id,import_id,row_number,status,reason_code,normalized_json) "
                    "VALUES($1,$2,2,'skipped','invalid_date','{}')",
                    uuid4(),
                    receipt_id,
                )
    assert await pg.conn.fetchval("SELECT count(*) FROM amex_statement_imports") == 0
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_statement_import_items") == 0
    )
