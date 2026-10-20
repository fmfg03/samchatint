"""Owner DDL and direct-SQL invariants in disposable localhost schemas only."""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from test_amex_statement_import_postgres import pg as _current_owner_pg_fixture

current_owner_pg = _current_owner_pg_fixture

ROOT = Path(__file__).resolve().parents[2]
DDL = ROOT / "database/migrations/20261013_amex_approved_informe_recognition.sql"
ROLLBACK = (
    ROOT / "database/migrations/20261013_amex_approved_informe_recognition_rollback.sql"
)


@pytest_asyncio.fixture
async def schema():
    raw = os.environ.get("TEST_AMEX_DATABASE_URL")
    if not raw:
        pytest.skip("Set isolated TEST_AMEX_DATABASE_URL")
    url = make_url(raw)
    if url.host not in {"localhost", "127.0.0.1"} or url.port != 55471:
        pytest.fail("Owner DDL tests require isolated localhost:55471")
    conn = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=url.database,
    )
    name = "amex_owner_" + uuid4().hex
    await conn.execute(f'CREATE SCHEMA "{name}"')
    await conn.execute(f'SET search_path TO "{name}"')
    await conn.execute(
        """
    CREATE TABLE empleados(id UUID PRIMARY KEY, rol TEXT, activo BOOLEAN DEFAULT TRUE);
    CREATE TABLE documentos(id UUID PRIMARY KEY, tipo TEXT, estado TEXT,
aprobado_en TIMESTAMPTZ, cuenta_gastos_id UUID);
    CREATE TABLE aprobaciones(id UUID PRIMARY KEY, tipo_entidad TEXT,
entidad_id UUID, aprobador_id UUID, accion TEXT, fecha TIMESTAMPTZ);
    CREATE TABLE expense_reports(id UUID PRIMARY KEY, origen TEXT,
gasto_cantidad DOUBLE PRECISION, fecha TIMESTAMP, currency TEXT,
      ultimos_4_digitos TEXT, estado_gasto TEXT DEFAULT 'activo',
cuenta_gastos_id UUID, informe_documento_id UUID, cfdi_report_id UUID);
    CREATE TABLE cuentas_contables(id UUID PRIMARY KEY);
    CREATE TABLE amex_card_accounts(id UUID PRIMARY KEY,last4 TEXT);
    CREATE TABLE accounting_polizas(id UUID PRIMARY KEY);
    """
    )
    await conn.execute(
        (ROOT / "database/migrations/20261001_amex_shared_recognition.sql").read_text()
    )
    await conn.execute(DDL.read_text())
    try:
        yield SimpleNamespace(conn=conn, url=url, name=name)
    finally:
        if conn.is_in_transaction():
            await conn.execute("ROLLBACK")
        await conn.execute("SET search_path TO public")
        await conn.execute(f'DROP SCHEMA "{name}" CASCADE')
        await conn.close()


async def seed(
    schema,
    amounts=(100, 200),
    *,
    mode="report_bridge",
    witness="valid",
    cut_kind="initial",
    reference_header=False,
):
    conn = schema.conn
    actor, owner, doc, account, card, bank, header, poliza = [uuid4() for _ in range(8)]
    now = datetime.now(timezone.utc)
    await conn.execute(
        "INSERT INTO empleados VALUES($1,'finanzas',true),($2,'empleado',true)",
        actor,
        owner,
    )
    await conn.execute(
        "INSERT INTO documentos VALUES($1,'INFORME','aprobado',$2,$3)",
        doc,
        now,
        uuid4(),
    )
    await conn.execute("INSERT INTO cuentas_contables VALUES($1)", account)
    await conn.execute(
        "INSERT INTO amex_card_accounts(id,last4) VALUES($1,'5007')", card
    )
    await conn.execute("INSERT INTO accounting_polizas VALUES($1)", poliza)
    await conn.execute(
        (
            "INSERT INTO expense_reports(id,origen,gasto_cantidad,fecha"
            ",currency,ultimos_4_digitos) VALUES($1,'amex_batch',$2,'20"
            "26-10-13','MXN','5007')"
        ),
        bank,
        sum(amounts),
    )
    await conn.execute(
        (
            "INSERT INTO amex_recognition_consumptions(id,imported_expe"
            "nse_id,card_account_id,amount,currency,economic_date,liabi"
            "lity_account_id,liability_code,actor_id,binding_mode) VALU"
            "ES($1,$2,$3,$4,'MXN','2026-10-13',$5,'2120-002-062',$6,$7)"
        ),
        header,
        bank,
        card,
        sum(amounts),
        account,
        actor,
        mode,
    )
    await conn.execute(
        (
            "INSERT INTO amex_recognition_representations(id,consumptio"
            "n_id,expense_id,role) VALUES($1,$2,$3,'statement')"
        ),
        uuid4(),
        header,
        bank,
    )
    targets, expenses = [], []
    async with conn.transaction():
        for amount in amounts:
            target, expense = uuid4(), uuid4()
            await conn.execute(
                (
                    "INSERT INTO expense_reports(id,origen,gasto_cantidad,fecha"
                    ",currency,ultimos_4_digitos,informe_documento_id) VALUES($"
                    "1,'informe_quick_entry',$2,'2026-10-13','MXN','5007',$3)"
                ),
                expense,
                amount,
                doc,
            )
            await conn.execute(
                (
                    "INSERT INTO amex_recognition_consumptions(id,source_kind,b"
                    "inding_mode,source_report_expense_id,card_account_id,amoun"
                    "t,currency,economic_date,liability_account_id,liability_co"
                    "de,actor_id,accounting_poliza_id) VALUES($1,'report','repo"
                    "rt',$2,$3,$4,'MXN','2026-10-13',$5,'2120-002-062',$6,$7)"
                ),
                target,
                expense,
                card,
                amount,
                account,
                actor,
                poliza,
            )
            await conn.execute(
                (
                    "INSERT INTO amex_recognition_representations(id,consumptio"
                    "n_id,expense_id,role) VALUES($1,$2,$3,'report')"
                ),
                uuid4(),
                target,
                expense,
            )
            targets.append(target)
            expenses.append(expense)
    snapshot = {
        "consumption_ids": [str(t) for t in targets],
        "partidas": [
            {"expense_id": str(e), "consumption_id": str(t)}
            for e, t in zip(expenses, targets)
        ],
    }
    if witness == "missing_partidas":
        snapshot["partidas"] = []
    if reference_header:
        snapshot["consumption_ids"].append(str(header))
    cut_doc = doc
    if witness == "other_informe":
        cut_doc = uuid4()
        await conn.execute(
            "INSERT INTO documentos VALUES($1,'INFORME','aprobado',$2,$3)",
            cut_doc,
            now,
            uuid4(),
        )
    await conn.execute(
        (
            "INSERT INTO amex_accounting_cuts(id,informe_id,kind,state_"
            "key,accounting_date,accounting_poliza_id,actor_id,snapshot"
            "_json) VALUES($1,$2,$3,$4,'2026-10-13',$5,$6,$7::jsonb)"
        ),
        uuid4(),
        cut_doc,
        cut_kind,
        uuid4().hex,
        poliza,
        actor,
        json.dumps(snapshot),
    )
    return SimpleNamespace(
        actor=actor,
        owner=owner,
        doc=doc,
        account=account,
        card=card,
        bank=bank,
        header=header,
        poliza=poliza,
        targets=targets,
        expenses=expenses,
        now=now,
    )


async def associate(conn, item, targets=None):
    for target in targets or item.targets:
        await conn.execute(
            (
                "INSERT INTO amex_report_bank_associations VALUES($1,$2,$3,"
                "CURRENT_TIMESTAMP)"
            ),
            item.header,
            target,
            item.actor,
        )


@pytest.mark.asyncio
async def test_upgrade_idempotent_legacy_ids_and_safe_empty_downgrade(schema):
    await schema.conn.execute(DDL.read_text())
    assert (
        await schema.conn.fetchval("SELECT count(*) FROM amex_report_source_evidences")
        == 0
    )
    await schema.conn.execute(ROLLBACK.read_text())
    assert not await schema.conn.fetchval(
        (
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHE"
            "RE table_schema=current_schema() AND table_name='amex_reco"
            "gnition_consumptions' AND column_name='source_kind')"
        )
    )


@pytest.mark.asyncio
async def test_report_anchor_requires_matching_rep_at_commit(schema):
    item = await seed(schema, (100,))
    conn = schema.conn
    expense = uuid4()
    await conn.execute(
        "INSERT INTO expense_reports(id,origen) VALUES($1,'informe_quick_entry')",
        expense,
    )
    with pytest.raises(asyncpg.CheckViolationError, match="own report representation"):
        async with conn.transaction():
            await conn.execute(
                (
                    "INSERT INTO amex_recognition_consumptions(id,source_kind,b"
                    "inding_mode,source_report_expense_id,card_account_id,amoun"
                    "t,currency,economic_date,liability_account_id,liability_co"
                    "de,actor_id) VALUES($1,'report','report',$2,$3,10,'MXN','2"
                    "026-10-13',$4,'2120-002-062',$5)"
                ),
                uuid4(),
                expense,
                item.card,
                item.account,
                item.actor,
            )


@pytest.mark.asyncio
async def test_exact_one_to_many_bridge_keeps_report_receipts(schema):
    item = await seed(schema)
    before = await schema.conn.fetch(
        (
            "SELECT id,accounting_poliza_id,classification_json FROM am"
            "ex_recognition_consumptions ORDER BY id"
        )
    )
    async with schema.conn.transaction():
        await associate(schema.conn, item)
    assert (
        await schema.conn.fetch(
            (
                "SELECT id,accounting_poliza_id,classification_json FROM am"
                "ex_recognition_consumptions ORDER BY id"
            )
        )
        == before
    )
    assert (
        await schema.conn.fetchval("SELECT count(*) FROM amex_report_bank_associations")
        == 2
    )
    with pytest.raises(asyncpg.RaiseError, match="history exists"):
        await schema.conn.execute(ROLLBACK.read_text())
    await schema.conn.execute("ROLLBACK")


@pytest.mark.asyncio
async def test_partial_group_rolls_back_every_membership(schema):
    item = await seed(schema)
    with pytest.raises(asyncpg.CheckViolationError, match="exact complete bank amount"):
        async with schema.conn.transaction():
            await associate(schema.conn, item, item.targets[:1])
    assert (
        await schema.conn.fetchval("SELECT count(*) FROM amex_report_bank_associations")
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "delete_link",
        "update_link",
        "delete_child",
        "change_child",
        "delete_parent",
        "downgrade_parent",
        "post_parent",
        "change_bank",
        "delete_rep",
        "insert_report_rep",
    ],
)
async def test_bridge_parent_child_membership_immutable(schema, operation):
    item = await seed(schema)
    async with schema.conn.transaction():
        await associate(schema.conn, item)
    sql, args = {
        "delete_link": (
            (
                "DELETE FROM amex_report_bank_associations WHERE statement_"
                "consumption_id=$1"
            ),
            [item.header],
        ),
        "update_link": (
            (
                "UPDATE amex_report_bank_associations SET actor_id=$2 WHERE"
                " statement_consumption_id=$1"
            ),
            [item.header, item.owner],
        ),
        "delete_child": (
            "DELETE FROM amex_recognition_consumptions WHERE id=$1",
            [item.targets[0]],
        ),
        "change_child": (
            "UPDATE amex_recognition_consumptions SET amount=99 WHERE id=$1",
            [item.targets[0]],
        ),
        "delete_parent": (
            "DELETE FROM amex_recognition_consumptions WHERE id=$1",
            [item.header],
        ),
        "downgrade_parent": (
            "UPDATE amex_recognition_consumptions "
            "SET binding_mode='legacy' WHERE id=$1",
            [item.header],
        ),
        "post_parent": (
            (
                "UPDATE amex_recognition_consumptions SET accounting_poliza"
                "_id=$2 WHERE id=$1"
            ),
            [item.header, item.poliza],
        ),
        "change_bank": (
            "UPDATE expense_reports SET gasto_cantidad=299 WHERE id=$1",
            [item.bank],
        ),
        "delete_rep": (
            "DELETE FROM amex_recognition_representations WHERE consumption_id=$1",
            [item.targets[0]],
        ),
        "insert_report_rep": (
            (
                "INSERT INTO amex_recognition_representations VALUES($1,$2,"
                "$3,'report',CURRENT_TIMESTAMP)"
            ),
            [uuid4(), item.header, item.expenses[0]],
        ),
    }[operation]
    with pytest.raises(asyncpg.PostgresError):
        await schema.conn.execute(sql, *args)


@pytest.mark.asyncio
async def test_bridge_never_posts_even_without_children(schema):
    item = await seed(schema, (100,))
    with pytest.raises(asyncpg.CheckViolationError):
        await schema.conn.execute(
            (
                "UPDATE amex_recognition_consumptions SET accounting_poliza"
                "_id=$2 WHERE id=$1"
            ),
            item.header,
            item.poliza,
        )


@pytest.mark.asyncio
async def test_legacy_cannot_accept_bridge_child(schema):
    item = await seed(schema, (100,), mode="legacy")
    with pytest.raises(asyncpg.CheckViolationError):
        await associate(schema.conn, item)


@pytest.mark.asyncio
async def test_real_operational_event_proof_and_immutable_history(schema):
    item = await seed(schema, (100,))
    event, evidence = uuid4(), uuid4()
    await schema.conn.execute(
        "INSERT INTO aprobaciones VALUES($1,'documento',$2,$3,'aprobar',$4)",
        event,
        item.doc,
        item.owner,
        item.now,
    )
    await schema.conn.execute(
        (
            "INSERT INTO amex_report_source_evidences(id,informe_id,kin"
            "d,approval_id,actor_id,created_at,version,source_key,snaps"
            "hot_json) VALUES($1,$2,'operational_approval',$3,$4,$5,1,$"
            "6,'{}')"
        ),
        evidence,
        item.doc,
        event,
        item.owner,
        item.now,
        "a" * 64,
    )
    assert (
        await schema.conn.fetchval(
            "SELECT actor_id FROM amex_report_source_evidences WHERE id=$1", evidence
        )
        == item.owner
    )
    for sql, args in [
        ("UPDATE aprobaciones SET aprobador_id=$2 WHERE id=$1", (event, item.actor)),
        ("DELETE FROM amex_report_source_evidences WHERE id=$1", (evidence,)),
    ]:
        with pytest.raises(asyncpg.CheckViolationError):
            await schema.conn.execute(sql, *args)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["area", "entity", "actor", "date"])
async def test_forged_or_intermediate_event_rejected(schema, invalid):
    item = await seed(schema, (100,))
    event = uuid4()
    await schema.conn.execute(
        "INSERT INTO aprobaciones VALUES($1,'documento',$2,$3,$4,$5)",
        event,
        uuid4() if invalid == "entity" else item.doc,
        item.owner,
        "aprobar_area" if invalid == "area" else "aprobar",
        item.now,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await schema.conn.execute(
            (
                "INSERT INTO amex_report_source_evidences(id,informe_id,kin"
                "d,approval_id,actor_id,created_at,version,source_key,snaps"
                "hot_json) VALUES($1,$2,'operational_approval',$3,$4,$5,1,$"
                "6,'{}')"
            ),
            uuid4(),
            item.doc,
            event,
            item.actor if invalid == "actor" else item.owner,
            (
                datetime(2000, 1, 1, tzinfo=timezone.utc)
                if invalid == "date"
                else item.now
            ),
            "a" * 64,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "witness,kind",
    [
        ("missing_partidas", "initial"),
        ("other_informe", "initial"),
        ("valid", "adjustment"),
    ],
)
async def test_pseudo_cut_witness_cannot_authorize_bank_group(schema, witness, kind):
    item = await seed(schema, (100,), witness=witness, cut_kind=kind)
    with pytest.raises(asyncpg.CheckViolationError, match="complete cut receipt"):
        await associate(schema.conn, item)


@pytest.mark.asyncio
async def test_legacy_header_referenced_by_cut_cannot_upgrade(schema):
    item = await seed(schema, (100,), mode="legacy", reference_header=True)
    with pytest.raises(asyncpg.CheckViolationError):
        await schema.conn.execute(
            (
                "UPDATE amex_recognition_consumptions SET binding_mode='rep"
                "ort_bridge' WHERE id=$1"
            ),
            item.header,
        )


@pytest.mark.asyncio
async def test_cut_history_cannot_be_rewritten_or_deleted(schema):
    item = await seed(schema, (100,))
    for sql in [
        "UPDATE amex_accounting_cuts SET snapshot_json='{}' WHERE informe_id=$1",
        "DELETE FROM amex_accounting_cuts WHERE informe_id=$1",
    ]:
        with pytest.raises(asyncpg.CheckViolationError, match="history is immutable"):
            await schema.conn.execute(sql, item.doc)


@pytest.mark.asyncio
async def test_cross_owner_race_serializes_on_real_source_row(schema):
    item = await seed(schema, (100,), mode="legacy")
    conn = schema.conn
    source, recognition = uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO expense_reports(id,origen) VALUES($1,'informe_quick_entry')",
        source,
    )
    other = await asyncpg.connect(
        user=schema.url.username,
        password=schema.url.password,
        host=schema.url.host,
        port=schema.url.port,
        database=schema.url.database,
        server_settings={"search_path": schema.name},
    )
    transaction = conn.transaction()
    await transaction.start()
    task = None
    try:
        await conn.execute(
            (
                "INSERT INTO amex_recognition_consumptions(id,source_kind,b"
                "inding_mode,source_report_expense_id,card_account_id,amoun"
                "t,currency,economic_date,liability_account_id,liability_co"
                "de,actor_id) VALUES($1,'report','report',$2,$3,10,'MXN','2"
                "026-10-13',$4,'2120-002-062',$5)"
            ),
            recognition,
            source,
            item.card,
            item.account,
            item.actor,
        )
        pid = await other.fetchval("SELECT pg_backend_pid()")
        task = asyncio.create_task(
            other.execute(
                (
                    "INSERT INTO amex_recognition_representations(id,consumptio"
                    "n_id,expense_id,role) VALUES($1,$2,$3,'report')"
                ),
                uuid4(),
                item.header,
                source,
            )
        )
        async with asyncio.timeout(5):
            while not await conn.fetchval(
                "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1", pid
            ):
                await asyncio.sleep(0)
        assert not task.done()
        await conn.execute(
            (
                "INSERT INTO amex_recognition_representations(id,consumptio"
                "n_id,expense_id,role) VALUES($1,$2,$3,'report')"
            ),
            uuid4(),
            recognition,
            source,
        )
        await transaction.commit()
        with pytest.raises(asyncpg.CheckViolationError, match="ownership conflicts"):
            await task
    finally:
        if conn.is_in_transaction():
            await transaction.rollback()
        if task is not None and not task.done():
            task.cancel()
        await other.close()


@pytest.mark.asyncio
async def test_metadata_only_readonly_schema_health_after_owner_upgrade(
    current_owner_pg,
):
    from devnous.gastos.amex_schema_policy import inspect_approved_informe_schema

    # Current runtime admission uses the complete current owner chain. The
    # other tests intentionally retain the historical migration-stage fixture.
    engine = current_owner_pg.factory.kw["bind"]
    async with engine.begin() as connection:
        await connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        result = await inspect_approved_informe_schema(connection)
    assert result == {"ready": True, "missing_columns": {}, "missing_triggers": []}


@pytest.mark.asyncio
async def test_final_approval_source_failure_rolls_back_event_and_state(schema):
    item = await seed(schema, (100,))
    await schema.conn.execute(
        "UPDATE documentos SET estado='enviado',aprobado_en=NULL WHERE id=$1", item.doc
    )
    event = uuid4()
    with pytest.raises(asyncpg.CheckViolationError):
        async with schema.conn.transaction():
            await schema.conn.execute(
                "UPDATE documentos SET estado='aprobado',aprobado_en=$2 WHERE id=$1",
                item.doc,
                item.now,
            )
            await schema.conn.execute(
                "INSERT INTO aprobaciones VALUES($1,'documento',$2,$3,'aprobar',$4)",
                event,
                item.doc,
                item.owner,
                item.now,
            )
            await schema.conn.execute(
                (
                    "INSERT INTO amex_report_source_evidences(id,informe_id,kin"
                    "d,approval_id,actor_id,created_at,version,source_key,snaps"
                    "hot_json) VALUES($1,$2,'operational_approval',$3,$4,$5,1,$"
                    "6,'{}')"
                ),
                uuid4(),
                item.doc,
                event,
                item.actor,
                item.now,
                "a" * 64,
            )
    assert (
        await schema.conn.fetchval(
            "SELECT estado FROM documentos WHERE id=$1", item.doc
        )
        == "enviado"
    )
    assert (
        await schema.conn.fetchval(
            "SELECT count(*) FROM aprobaciones WHERE id=$1", event
        )
        == 0
    )


@pytest.mark.asyncio
async def test_upgrade_preserves_real_legacy_anchor_and_receipt_bytes(schema):
    conn = schema.conn
    await conn.execute(ROLLBACK.read_text())
    actor, card, account, source, consumption, poliza, informe = [
        uuid4() for _ in range(7)
    ]
    await conn.execute("INSERT INTO empleados VALUES($1,'finanzas',true)", actor)
    await conn.execute(
        "INSERT INTO amex_card_accounts(id,last4) VALUES($1,'5007')", card
    )
    await conn.execute("INSERT INTO cuentas_contables VALUES($1)", account)
    await conn.execute("INSERT INTO accounting_polizas VALUES($1)", poliza)
    await conn.execute(
        "INSERT INTO documentos VALUES($1,'INFORME','aprobado',CURRENT_TIMESTAMP,$2)",
        informe,
        uuid4(),
    )
    await conn.execute(
        "INSERT INTO expense_reports(id,origen) VALUES($1,'amex_batch')", source
    )
    await conn.execute(
        "INSERT INTO amex_recognition_consumptions(id,imported_expense_id,"
        "card_account_id,amount,currency,economic_date,liability_account_id,"
        "liability_code,actor_id,accounting_poliza_id,classification_json) "
        "VALUES($1,$2,$3,116,'MXN','2026-10-13',$4,'2120-002-062',"
        "$5,$6,'[{\"legacy\":true}]')",
        consumption,
        source,
        card,
        account,
        actor,
        poliza,
    )
    snapshot = json.dumps({"consumption_ids": [str(consumption)], "partidas": []})
    await conn.execute(
        "INSERT INTO amex_accounting_cuts(id,informe_id,kind,state_key,"
        "accounting_date,accounting_poliza_id,actor_id,snapshot_json) "
        "VALUES($1,$2,'initial',$3,'2026-10-13',$4,$5,$6::jsonb)",
        uuid4(),
        informe,
        uuid4().hex,
        poliza,
        actor,
        snapshot,
    )
    original = await conn.fetchrow(
        "SELECT id,imported_expense_id,accounting_poliza_id,classification_json "
        "FROM amex_recognition_consumptions WHERE id=$1",
        consumption,
    )
    original_cut = await conn.fetchval("SELECT snapshot_json FROM amex_accounting_cuts")
    await conn.execute(DDL.read_text())
    assert (
        await conn.fetchrow(
            "SELECT id,imported_expense_id,accounting_poliza_id,classification_json "
            "FROM amex_recognition_consumptions WHERE id=$1",
            consumption,
        )
        == original
    )
    assert (
        await conn.fetchval("SELECT snapshot_json FROM amex_accounting_cuts")
        == original_cut
    )
    assert await conn.fetchrow(
        "SELECT source_kind,binding_mode FROM amex_recognition_consumptions "
        "WHERE id=$1",
        consumption,
    ) == ("statement", "legacy")


@pytest.mark.asyncio
async def test_committed_complete_group_cannot_expand_even_via_savepoint(schema):
    item = await seed(schema, (100,))
    conn = schema.conn
    async with conn.transaction():
        async with conn.transaction():
            await associate(conn, item)
    target, expense, doc, poliza = [uuid4() for _ in range(4)]
    await conn.execute(
        "INSERT INTO documentos VALUES($1,'INFORME','aprobado',"
        "CURRENT_TIMESTAMP,$2)",
        doc,
        uuid4(),
    )
    await conn.execute("INSERT INTO accounting_polizas VALUES($1)", poliza)
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO expense_reports(id,origen,gasto_cantidad,fecha,"
            "currency,ultimos_4_digitos,informe_documento_id) "
            "VALUES($1,'informe_quick_entry',10,'2026-10-13','MXN','5007',$2)",
            expense,
            doc,
        )
        await conn.execute(
            "INSERT INTO amex_recognition_consumptions(id,source_kind,"
            "binding_mode,source_report_expense_id,card_account_id,amount,"
            "currency,economic_date,liability_account_id,liability_code,"
            "actor_id,accounting_poliza_id) VALUES($1,'report','report',$2,"
            "$3,10,'MXN','2026-10-13',$4,'2120-002-062',$5,$6)",
            target,
            expense,
            item.card,
            item.account,
            item.actor,
            poliza,
        )
        await conn.execute(
            "INSERT INTO amex_recognition_representations(id,consumption_id,"
            "expense_id,role) VALUES($1,$2,$3,'report')",
            uuid4(),
            target,
            expense,
        )
    await conn.execute(
        "INSERT INTO amex_accounting_cuts(id,informe_id,kind,state_key,"
        "accounting_date,accounting_poliza_id,actor_id,snapshot_json) "
        "VALUES($1,$2,'initial',$3,'2026-10-13',$4,$5,$6::jsonb)",
        uuid4(),
        doc,
        uuid4().hex,
        poliza,
        item.actor,
        json.dumps(
            {
                "consumption_ids": [str(target)],
                "partidas": [
                    {"expense_id": str(expense), "consumption_id": str(target)}
                ],
            }
        ),
    )
    with pytest.raises(asyncpg.CheckViolationError, match="exact complete bank amount"):
        async with conn.transaction():
            async with conn.transaction():
                await associate(conn, item, [target])
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM amex_report_bank_associations "
            "WHERE statement_consumption_id=$1",
            item.header,
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["source_update", "association"])
async def test_physical_source_update_vs_association_serializes(schema, first):
    item = await seed(schema, (100,))
    conn = schema.conn
    other = await asyncpg.connect(
        user=schema.url.username,
        password=schema.url.password,
        host=schema.url.host,
        port=schema.url.port,
        database=schema.url.database,
        server_settings={"search_path": schema.name},
    )
    transaction = conn.transaction()
    await transaction.start()
    task = None
    try:
        pid = await other.fetchval("SELECT pg_backend_pid()")
        if first == "source_update":
            await conn.execute(
                "UPDATE expense_reports SET gasto_cantidad=99 WHERE id=$1",
                item.expenses[0],
            )
            task = asyncio.create_task(associate(other, item))
        else:
            await associate(conn, item)
            task = asyncio.create_task(
                other.execute(
                    "UPDATE expense_reports SET gasto_cantidad=99 WHERE id=$1",
                    item.expenses[0],
                )
            )
        async with asyncio.timeout(5):
            while not await conn.fetchval(
                "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
                pid,
            ):
                await asyncio.sleep(0)
        assert not task.done()
        await transaction.commit()
        with pytest.raises(asyncpg.CheckViolationError):
            await task
        assert await conn.fetchval(
            "SELECT count(*) FROM amex_report_bank_associations"
        ) == (0 if first == "source_update" else 1)
        assert await conn.fetchval(
            "SELECT gasto_cantidad FROM expense_reports WHERE id=$1",
            item.expenses[0],
        ) == (99 if first == "source_update" else 100)
    finally:
        if conn.is_in_transaction():
            await transaction.rollback()
        if task is not None and not task.done():
            task.cancel()
        await other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["evidence_insert", "approval_update"])
async def test_actual_approval_update_vs_evidence_serializes(schema, first):
    item = await seed(schema, (100,))
    conn = schema.conn
    event = uuid4()
    await conn.execute(
        "INSERT INTO aprobaciones VALUES($1,'documento',$2,$3,'aprobar',$4)",
        event,
        item.doc,
        item.owner,
        item.now,
    )
    other = await asyncpg.connect(
        user=schema.url.username,
        password=schema.url.password,
        host=schema.url.host,
        port=schema.url.port,
        database=schema.url.database,
        server_settings={"search_path": schema.name},
    )
    evidence_id = uuid4()
    statement = (
        "INSERT INTO amex_report_source_evidences(id,informe_id,kind,approval_id,"
        "actor_id,created_at,version,source_key,snapshot_json) "
        "VALUES($1,$2,'operational_approval',$3,$4,$5,1,$6,'{}')"
    )
    arguments = (evidence_id, item.doc, event, item.owner, item.now, "a" * 64)
    transaction = conn.transaction()
    await transaction.start()
    task = None
    try:
        pid = await other.fetchval("SELECT pg_backend_pid()")
        if first == "evidence_insert":
            await conn.execute(statement, *arguments)
            task = asyncio.create_task(
                other.execute(
                    "UPDATE aprobaciones SET aprobador_id=$2 WHERE id=$1",
                    event,
                    item.actor,
                )
            )
        else:
            await conn.execute(
                "UPDATE aprobaciones SET aprobador_id=$2 WHERE id=$1",
                event,
                item.actor,
            )
            task = asyncio.create_task(other.execute(statement, *arguments))
        async with asyncio.timeout(5):
            while not await conn.fetchval(
                "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
                pid,
            ):
                await asyncio.sleep(0)
        assert not task.done()
        await transaction.commit()
        with pytest.raises(asyncpg.CheckViolationError):
            await task
        assert await conn.fetchval(
            "SELECT count(*) FROM amex_report_source_evidences WHERE id=$1",
            evidence_id,
        ) == (1 if first == "evidence_insert" else 0)
        assert await conn.fetchval(
            "SELECT aprobador_id FROM aprobaciones WHERE id=$1",
            event,
        ) == (item.owner if first == "evidence_insert" else item.actor)
    finally:
        if conn.is_in_transaction():
            await transaction.rollback()
        if task is not None and not task.done():
            task.cancel()
        await other.close()
