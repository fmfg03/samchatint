"""Direct SQL integrity checks in a disposable PostgreSQL owner schema."""

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "amex_direct_fixture",
    ROOT / "tests/acceptance/test_amex_sat_budget_direct_postgres.py",
)
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
direct_pg = fixture_module.direct_pg
DDL = ROOT / "database/migrations/20261014_amex_sat_budget_direct.sql"
ROLLBACK = ROOT / "database/migrations/20261014_amex_sat_budget_direct_rollback.sql"


async def intake(pg, kind="credit", content=b"original", bad_hash=False, mode="direct"):
    header, receipt, item, file = [uuid4() for _ in range(4)]
    sha = hashlib.sha256(content).hexdigest()
    await pg.conn.execute(
        (
            "INSERT INTO amex_statement_imports(id,card_account_id,employ"
            "ee_id,period_start,period_end,file_sha256,filename,parser_ve"
            "rsion,actor_id,total_rows,imported_count,skipped_count,credi"
            "t_count,unknown_count,workflow_mode,summary_json) VALUES($1,"
            "$2,$3,'2026-10-01','2026-10-31',$4,'test.csv','strict',$5,1,"
            "0,0,$6,$7,$8,'{}')"
        ),
        header,
        pg.card.id,
        pg.owner.id,
        sha,
        pg.actor.id,
        int(kind == "credit"),
        int(kind == "unknown"),
        mode,
    )
    await pg.conn.execute(
        (
            "INSERT INTO amex_statement_import_items(id,import_id,row_num"
            "ber,status,reason_code,normalized_json,workflow_mode) VALUES"
            "($1,$2,2,$3,$4,'{}',$5)"
        ),
        receipt,
        header,
        kind,
        "unknown" if kind == "unknown" else None,
        mode,
    )
    await pg.conn.execute(
        (
            "INSERT INTO amex_statement_source_files(id,import_id,content"
            ",file_sha256,byte_size,filename,mime_type,actor_id) VALUES($"
            "1,$2,$3,$4,$5,'test.csv','text/csv',$6)"
        ),
        file,
        header,
        content,
        "a" * 64 if bad_hash else sha,
        len(content),
        pg.actor.id,
    )
    await pg.conn.execute(
        (
            "INSERT INTO amex_direct_items(id,import_id,import_item_id,mo"
            "vement_kind,raw_signed_amount,profile,profile_version,source"
            "_json,source_version,source_hash,match_status,budget_status,"
            "accounting_status,credit_excluded) VALUES($1,$2,$3,$4,100,'d"
            "ebit_negative','amex-direct-csv-v1','{}',1,$5,'pending','unc"
            "lassified','pending',false)"
        ),
        item,
        header,
        receipt,
        kind,
        sha,
    )
    return header, item, file


@pytest.mark.asyncio
async def test_migration_replay_preserves_guard_definition(direct_pg):
    before = await direct_pg.conn.fetchval(
        "SELECT pg_get_functiondef('amex_guard_bank_association()'::regprocedure)"
    )
    await direct_pg.conn.execute(DDL.read_text())
    assert (
        await direct_pg.conn.fetchval(
            "SELECT pg_get_functiondef('amex_guard_bank_association()'::regprocedure)"
        )
        == before
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["credit", "unknown"])
async def test_nonexpense_intake_commits_without_fictitious_debit(direct_pg, kind):
    async with direct_pg.conn.transaction():
        header, item, file = await intake(direct_pg, kind)
    assert (
        await direct_pg.conn.fetchval(
            "SELECT imported_count FROM amex_statement_imports WHERE id=$1", header
        )
        == 0
    )
    assert (
        await direct_pg.conn.fetchval(
            "SELECT imported_expense_id FROM amex_direct_items WHERE id=$1", item
        )
        is None
    )


@pytest.mark.asyncio
async def test_original_sha_checked_against_exact_bytes(direct_pg):
    with pytest.raises(asyncpg.CheckViolationError, match="hash/size"):
        async with direct_pg.conn.transaction():
            await intake(direct_pg, bad_hash=True)
    assert (
        await direct_pg.conn.fetchval("SELECT count(*) FROM amex_statement_imports")
        == 0
    )


@pytest.mark.asyncio
async def test_incomplete_direct_membership_fails_at_commit(direct_pg):
    with pytest.raises(asyncpg.CheckViolationError, match="typed membership"):
        async with direct_pg.conn.transaction():
            header, item, file = await intake(direct_pg)
            await direct_pg.conn.execute(
                (
                    "ALTER TABLE amex_direct_items DISABLE TRIGGER ad_projection_"
                    "amex_direct_items"
                )
            )
            await direct_pg.conn.execute(
                "DELETE FROM amex_direct_items WHERE id=$1", item
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table",
    [
        "amex_statement_source_files",
        "amex_statement_import_items",
        "amex_statement_imports",
    ],
)
async def test_intake_evidence_is_append_only(direct_pg, table):
    async with direct_pg.conn.transaction():
        await intake(direct_pg)
    with pytest.raises(asyncpg.PostgresError, match="immutable"):
        await direct_pg.conn.execute("DELETE FROM " + table)


@pytest.mark.asyncio
async def test_mixed_mode_rejected(direct_pg):
    with pytest.raises(asyncpg.CheckViolationError):
        async with direct_pg.conn.transaction():
            await intake(direct_pg, mode="legacy")


@pytest.mark.asyncio
async def test_wrong_context_pointer_rejected(direct_pg):
    async with direct_pg.conn.transaction():
        header, item, file = await intake(direct_pg)
        other, _, _ = await intake(direct_pg, content=b"different")
        ctx = uuid4()
        await direct_pg.conn.execute(
            (
                "INSERT INTO amex_statement_direct_contexts(id,import_id,vers"
                "ion,entity_code,company_code,receiver_rfc,currency,config_ha"
                "sh,payload_hash,actor_id) VALUES($1,$2,1,'TEST','TEST','BBB0"
                "10101BBB','MXN',$3,$3,$4)"
            ),
            ctx,
            other,
            "a" * 64,
            direct_pg.actor.id,
        )
    with pytest.raises(asyncpg.CheckViolationError, match="membership mismatch"):
        await direct_pg.conn.execute(
            (
                "INSERT INTO amex_statement_direct_state(import_id,context_id"
                ",context_version,source_version) VALUES($1,$2,1,1)"
            ),
            header,
            ctx,
        )


@pytest.mark.asyncio
async def test_rollback_refuses_persisted_evidence(direct_pg):
    async with direct_pg.conn.transaction():
        await intake(direct_pg)
    with pytest.raises(asyncpg.PostgresError, match="populated"):
        await direct_pg.conn.execute(ROLLBACK.read_text())
    await direct_pg.conn.execute("ROLLBACK")
    assert (
        await direct_pg.conn.fetchval(
            "SELECT count(*) FROM amex_statement_source_files"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_empty_rollback_restores_old_check(direct_pg):
    await direct_pg.conn.execute(ROLLBACK.read_text())
    assert (
        await direct_pg.conn.fetchval("SELECT to_regclass('amex_direct_items')") is None
    )
    check = await direct_pg.conn.fetchval(
        (
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE co"
            "nrelid='amex_statement_imports'::regclass AND conname='ck_am"
            "ex_statement_counts'"
        )
    )
    assert "imported_count > 0" in check


async def debit_source(pg):
    header, receipt, item, file, expense, ctx, cfdi, status, assignment = [
        uuid4() for _ in range(9)
    ]
    sha = hashlib.sha256(b"debit").hexdigest()
    c = pg.conn
    await c.execute(
        (
            "INSERT INTO expense_reports(id,empleado_id,origen,gasto_cant"
            "idad,fecha,currency,ultimos_4_digitos,pagado_con_amex_empres"
            "a,estado_gasto,proyecto,concepto,hospedaje_impuesto_confirma"
            "do,propina_no_deducible,cfdi_compartido_confirmado,coi_estad"
            "o,created_at) VALUES($1,$2,'amex_batch',100,'2026-10-10','MX"
            "N','5007',true,'activo','TEST','TEST',false,0,false,'pendien"
            "te',now())"
        ),
        expense,
        pg.owner.id,
    )
    await c.execute(
        (
            "INSERT INTO amex_statement_imports(id,card_account_id,employ"
            "ee_id,period_start,period_end,file_sha256,filename,parser_ve"
            "rsion,actor_id,total_rows,imported_count,skipped_count,workf"
            "low_mode,summary_json) VALUES($1,$2,$3,'2026-10-01','2026-10"
            "-31',$4,'debit.csv','strict',$5,1,1,0,'direct','{}')"
        ),
        header,
        pg.card.id,
        pg.owner.id,
        sha,
        pg.actor.id,
    )
    await c.execute(
        (
            "INSERT INTO amex_statement_import_items(id,import_id,row_num"
            "ber,imported_expense_id,status,normalized_json,workflow_mode"
            ") VALUES($1,$2,2,$3,'imported','{}','direct')"
        ),
        receipt,
        header,
        expense,
    )
    await c.execute(
        (
            "INSERT INTO amex_statement_source_files(id,import_id,content"
            ",file_sha256,byte_size,filename,mime_type,actor_id) VALUES($"
            "1,$2,$3,$4,5,'debit.csv','text/csv',$5)"
        ),
        file,
        header,
        b"debit",
        sha,
        pg.actor.id,
    )
    await c.execute(
        (
            "INSERT INTO amex_statement_direct_contexts(id,import_id,vers"
            "ion,entity_code,company_code,receiver_rfc,currency,profile,c"
            "onfig_hash,payload_hash,actor_id) VALUES($1,$2,1,'TEST','TES"
            "T','BBB010101BBB','MXN','debit_negative',$3,$3,$4)"
        ),
        ctx,
        header,
        sha,
        pg.actor.id,
    )
    await c.execute(
        (
            "INSERT INTO amex_statement_direct_state(import_id,context_id"
            ",context_version,source_version) VALUES($1,$2,1,1)"
        ),
        header,
        ctx,
    )
    await c.execute(
        (
            "INSERT INTO cfdi_reports(id,cfdi_uuid,total,moneda,emisor_rf"
            "c,receptor_rfc,created_at,updated_at,origen,xml_raw) VALUES("
            "$1,$2,100,'MXN','AAA010101AAA','BBB010101BBB',now(),now(),'s"
            "at','xml')"
        ),
        cfdi,
        str(uuid4()),
    )
    await c.execute(
        (
            "INSERT INTO amex_cfdi_status_receipts(id,cfdi_id,fiscal_uuid"
            ",issuer_rfc,receiver_rfc,amount,xml_hash,request_hash,observ"
            "ed_status,response_json,response_hash,actor_id) VALUES($1,$2"
            ",$3,'AAA010101AAA','BBB010101BBB',100,$4,$4,'Vigente','{}',$"
            "4,$5)"
        ),
        status,
        cfdi,
        await c.fetchval("SELECT cfdi_uuid FROM cfdi_reports WHERE id=$1", cfdi),
        hashlib.sha256(b"xml").hexdigest(),
        pg.actor.id,
    )
    await c.execute(
        (
            "INSERT INTO budget_movement_assignments(id,budget_version_id"
            ",budget_concept_id,budget_line_id,movement_key,assigned_by_e"
            "mpleado_id) VALUES($1,$2,$3,$4,$5,$6)"
        ),
        assignment,
        pg.version_id,
        pg.concept.id,
        pg.line_id,
        "amex_direct:" + str(item),
        pg.cp_actor.id,
    )
    await c.execute(
        (
            "INSERT INTO amex_direct_items(id,import_id,import_item_id,im"
            "ported_expense_id,movement_kind,raw_signed_amount,profile,pr"
            "ofile_version,source_json,context_id,context_version,source_"
            "version,source_hash,match_status,cfdi_id,status_receipt_id,b"
            "udget_concept_id,budget_line_id,budget_version_id,budget_ass"
            "ignment_id,budget_status,accounting_status,credit_excluded) "
            "VALUES($1,$2,$3,$4,'debit',-100,'debit_negative','amex-direc"
            "t-csv-v1','{}',$5,1,1,$6,'matched',$7,$8,$9,$10,$11,$12,'ass"
            "igned','recognized',false)"
        ),
        item,
        header,
        receipt,
        expense,
        ctx,
        sha,
        cfdi,
        status,
        pg.concept.id,
        pg.line_id,
        pg.version_id,
        assignment,
    )
    await c.execute(
        "UPDATE expense_reports SET cuenta_contable_id=$1 WHERE id=$2",
        pg.gasto.id,
        expense,
    )
    await c.execute("UPDATE amex_direct_items SET edition_year=2026 WHERE id=$1", item)
    return dict(
        header=header,
        item=item,
        receipt=receipt,
        expense=expense,
        ctx=ctx,
        sha=sha,
        cfdi=cfdi,
        assignment=assignment,
    )


async def posting(
    pg,
    data,
    *,
    amount=100,
    membership=True,
    lines=True,
    currency="MXN",
    economic_date=date(2026, 10, 10),
    journal_rows=None,
    snapshot_json="{}",
    line_metadata=None,
):
    poliza, consumption, cut = uuid4(), uuid4(), uuid4()
    c = pg.conn
    await c.execute(
        (
            "INSERT INTO accounting_polizas(id,source_file,tipo_poliza,nu"
            "mero_poliza,fecha_poliza,concepto,created_at,updated_at) VAL"
            "UES($1,'direct','Dr','1','2026-10-31','direct',now(),now())"
        ),
        poliza,
    )
    await c.execute(
        (
            "INSERT INTO amex_recognition_consumptions(id,imported_expens"
            "e_id,card_account_id,amount,currency,economic_date,liability"
            "_account_id,liability_code,accounting_poliza_id,actor_id,bin"
            "ding_mode) VALUES($1,$2,$3,100,$7,$8,$4,'2120-0"
            "02-062',$5,$6,'direct')"
        ),
        consumption,
        data["expense"],
        pg.card.id,
        pg.liability.id,
        poliza,
        pg.actor.id,
        currency,
        economic_date,
    )
    if lines:
        for no, debe, haber, account in journal_rows or [
            (1, amount, 0, pg.gasto.id),
            (2, 0, amount, pg.liability.id),
        ]:
            await c.execute(
                (
                    "INSERT INTO accounting_poliza_lines(id,poliza_id,line_no,cue"
                    "nta_codigo,cuenta_contable_id,debe,haber,created_at,raw_row_json) "
                    "VALUES($1,$2,$3,'test',$4,$5,$6,now(),$7::jsonb)"
                ),
                uuid4(),
                poliza,
                no,
                account,
                debe,
                haber,
                json.dumps((line_metadata or {}).get(no, {})),
            )
    await c.execute(
        (
            "INSERT INTO amex_direct_accounting_cuts(id,import_id,context"
            "_id,context_version,source_version,source_hash,payload_hash,"
            "accounting_poliza_id,accounting_date,actor_id,snapshot_json)"
            " VALUES($1,$2,$3,1,1,$4,$4,$5,'2026-10-31',$6,'{}')"
        ),
        cut,
        data["header"],
        data["ctx"],
        data["sha"],
        poliza,
        pg.actor.id,
    )
    if membership:
        await c.execute(
            (
                "INSERT INTO amex_direct_cut_items(id,cut_id,direct_item_id,i"
                "mport_item_id,imported_expense_id,consumption_id,accounting_"
                "poliza_id,coverage_kind,source_hash,snapshot_json) VALUES($1"
                ",$2,$3,$4,$5,$6,$7,'new',$8,$9::jsonb)"
            ),
            uuid4(),
            cut,
            data["item"],
            data["receipt"],
            data["expense"],
            consumption,
            poliza,
            data["sha"],
            snapshot_json,
        )
    return poliza, consumption, cut


@pytest.mark.asyncio
async def test_direct_cut_atomic_valid_journal_and_freeze(direct_pg):
    async with direct_pg.conn.transaction():
        data = await debit_source(direct_pg)
        poliza, consumption, cut = await posting(direct_pg, data)
    for sql, identifier in [
        (
            "UPDATE amex_direct_items SET match_status='pending' WHERE id=$1",
            data["item"],
        ),
        (
            (
                "UPDATE amex_statement_direct_state SET source_version=2 WHER"
                "E import_id=$1"
            ),
            data["header"],
        ),
        ("UPDATE expense_reports SET gasto_cantidad=101 WHERE id=$1", data["expense"]),
        ("DELETE FROM amex_direct_accounting_cuts WHERE id=$1", cut),
        (
            "UPDATE amex_recognition_consumptions SET amount=101 WHERE id=$1",
            consumption,
        ),
        ("DELETE FROM accounting_polizas WHERE id=$1", poliza),
        ("DELETE FROM budget_movement_assignments WHERE id=$1", data["assignment"]),
    ]:
        with pytest.raises(asyncpg.CheckViolationError):
            await direct_pg.conn.execute(sql, identifier)
    # A normal canonical XML refresh remains allowed; frozen receipts are untouched.
    await direct_pg.conn.execute(
        "UPDATE cfdi_reports SET emisor_nombre='Refresh' WHERE id=$1", data["cfdi"]
    )
    assert (
        await direct_pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_cut_items WHERE cut_id=$1", cut
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing_membership", "missing_lines", "wrong_total"])
async def test_direct_cut_proof_rejects_partial_zero_or_wrong_journal(direct_pg, mode):
    with pytest.raises(asyncpg.CheckViolationError):
        async with direct_pg.conn.transaction():
            data = await debit_source(direct_pg)
            await posting(
                direct_pg,
                data,
                membership=mode != "missing_membership",
                lines=mode != "missing_lines",
                amount=99 if mode == "wrong_total" else 100,
            )
    assert await direct_pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0
    assert (
        await direct_pg.conn.fetchval("SELECT count(*) FROM amex_statement_imports")
        == 0
    )


@pytest.mark.asyncio
async def test_context_revision_race_serializes_declared_version(direct_pg):
    import asyncio
    import os

    from sqlalchemy.engine import make_url

    async with direct_pg.conn.transaction():
        header, _, _ = await intake(direct_pg)
    url = make_url(os.environ["TEST_AMEX_DATABASE_URL"])
    other = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=url.database,
        server_settings={"search_path": direct_pg.schema},
    )
    sql = (
        "INSERT INTO amex_statement_direct_contexts(id,import_id,vers"
        "ion,entity_code,company_code,receiver_rfc,currency,config_ha"
        "sh,payload_hash,actor_id) VALUES($1,$2,1,'TEST','TEST','BBB0"
        "10101BBB','MXN',$3,$3,$4)"
    )
    tx = direct_pg.conn.transaction()
    await tx.start()
    try:
        await direct_pg.conn.execute(sql, uuid4(), header, "a" * 64, direct_pg.actor.id)
        pending = asyncio.create_task(
            other.execute(sql, uuid4(), header, "b" * 64, direct_pg.actor.id)
        )
        await asyncio.sleep(0.05)
        assert not pending.done()
        await tx.commit()
        with pytest.raises(asyncpg.CheckViolationError, match="version/hash/RFC"):
            await asyncio.wait_for(pending, 3)
        assert (
            await other.fetchval(
                (
                    "SELECT count(*) FROM amex_statement_direct_contexts WHERE im"
                    "port_id=$1"
                ),
                header,
            )
            == 1
        )
    finally:
        if direct_pg.conn.is_in_transaction():
            await tx.rollback()
        await other.close()


@pytest.mark.asyncio
async def test_direct_consumption_cannot_acquire_report_representation(direct_pg):
    async with direct_pg.conn.transaction():
        data = await debit_source(direct_pg)
        _, consumption, _ = await posting(direct_pg, data)
    with pytest.raises(asyncpg.CheckViolationError, match="report representations"):
        await direct_pg.conn.execute(
            (
                "INSERT INTO amex_recognition_representations(id,consumption_"
                "id,expense_id,role) VALUES($1,$2,$3,'report')"
            ),
            uuid4(),
            consumption,
            data["expense"],
        )


@pytest.mark.asyncio
async def test_existing_owner_cannot_be_converted_to_direct(direct_pg):
    async with direct_pg.conn.transaction():
        data = await debit_source(direct_pg)
        consumption = uuid4()
        await direct_pg.conn.execute(
            (
                "INSERT INTO amex_recognition_consumptions(id,imported_expens"
                "e_id,card_account_id,amount,currency,economic_date,liability"
                "_account_id,liability_code,actor_id,binding_mode) VALUES($1,"
                "$2,$3,100,'MXN','2026-10-10',$4,'2120-002-062',$5,'legacy')"
            ),
            consumption,
            data["expense"],
            direct_pg.card.id,
            direct_pg.liability.id,
            direct_pg.actor.id,
        )
    with pytest.raises(asyncpg.CheckViolationError, match="convert existing"):
        await direct_pg.conn.execute(
            (
                "UPDATE amex_recognition_consumptions SET binding_mode='direc"
                "t' WHERE id=$1"
            ),
            consumption,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition",
    [
        "canceled",
        "wrong_receiver",
        "stale",
        "wrong_currency",
        "xml_changed",
        "wrong_mapping",
    ],
)
async def test_direct_cut_rejects_changed_fiscal_or_account_proof(direct_pg, condition):
    with pytest.raises(asyncpg.CheckViolationError):
        async with direct_pg.conn.transaction():
            data = await debit_source(direct_pg)
            if condition == "canceled":
                # Append-only receipt: create the source with a canceled observation.
                await direct_pg.conn.execute(
                    (
                        "ALTER TABLE amex_cfdi_status_receipts DISABLE TRIGGER ad_imm"
                        "utable_amex_cfdi_status_receipts"
                    )
                )
                await direct_pg.conn.execute(
                    "UPDATE amex_cfdi_status_receipts SET observed_status='cancelado'"
                )
            elif condition == "wrong_receiver":
                await direct_pg.conn.execute(
                    "UPDATE cfdi_reports SET receptor_rfc='CCC010101CCC' WHERE id=$1",
                    data["cfdi"],
                )
            elif condition == "stale":
                await direct_pg.conn.execute(
                    (
                        "ALTER TABLE amex_cfdi_status_receipts DISABLE TRIGGER ad_imm"
                        "utable_amex_cfdi_status_receipts"
                    )
                )
                await direct_pg.conn.execute(
                    (
                        "UPDATE amex_cfdi_status_receipts SET observed_at=now()-INTER"
                        "VAL '25 hours'"
                    )
                )
            elif condition == "wrong_currency":
                await direct_pg.conn.execute(
                    "UPDATE cfdi_reports SET moneda='USD' WHERE id=$1", data["cfdi"]
                )
            elif condition == "xml_changed":
                await direct_pg.conn.execute(
                    "UPDATE cfdi_reports SET xml_raw='changed' WHERE id=$1",
                    data["cfdi"],
                )
            else:
                await direct_pg.conn.execute(
                    "UPDATE expense_reports SET cuenta_contable_id=NULL WHERE id=$1",
                    data["expense"],
                )
            await posting(direct_pg, data)
    assert (
        await direct_pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_accounting_cuts"
        )
        == 0
    )


@pytest.mark.asyncio
async def test_owner_preflight_accepts_real_schema_and_rejects_disabled_guard(
    direct_pg,
):
    from devnous.gastos.services.amex_direct_schema_service import (
        inspect_amex_direct_schema,
    )

    async with direct_pg.factory() as session:
        result = await inspect_amex_direct_schema(session)
    assert result["ready"], result
    await direct_pg.conn.execute(
        "ALTER TABLE amex_direct_items DISABLE TRIGGER ad_projection_amex_direct_items"
    )
    async with direct_pg.factory() as session:
        result = await inspect_amex_direct_schema(session)
    assert not result["ready"]


@pytest.mark.asyncio
async def test_owner_preflight_rejects_weakened_function(direct_pg):
    from devnous.gastos.services.amex_direct_schema_service import (
        inspect_amex_direct_schema,
    )

    await direct_pg.conn.execute(
        (
            "CREATE OR REPLACE FUNCTION amex_direct_context_guard() RETUR"
            "NS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"
        )
    )
    async with direct_pg.factory() as session:
        result = await inspect_amex_direct_schema(session)
    assert not result["ready"]
    assert any(
        "amex_direct_context_guard" in value for value in result["schema_errors"]
    )


@pytest.mark.asyncio
async def test_frozen_direct_journal_cannot_gain_zero_line(direct_pg):
    async with direct_pg.conn.transaction():
        data = await debit_source(direct_pg)
        poliza, _, _ = await posting(direct_pg, data)
    with pytest.raises(asyncpg.CheckViolationError, match="gain new lines"):
        await direct_pg.conn.execute(
            (
                "INSERT INTO accounting_poliza_lines(id,poliza_id,line_no,cue"
                "nta_codigo,debe,haber,created_at) VALUES($1,$2,3,'zero',0,0,"
                "now())"
            ),
            uuid4(),
            poliza,
        )


@pytest.mark.asyncio
async def test_alias_retirement_requires_replacement_and_preserves_old_evidence(
    direct_pg,
):
    alias = uuid4()
    sql = (
        "INSERT INTO amex_merchant_identity_aliases(id,entity_code,ca"
        "rd_account_id,descriptor,issuer_rfc,version,active,actor_id,"
        "reason,payload_hash) VALUES($1,'TEST',$2,'EXACT MERCHANT','A"
        "AA010101AAA',$3,true,$4,'confirmed',$5)"
    )
    await direct_pg.conn.execute(
        sql, alias, direct_pg.card.id, 1, direct_pg.actor.id, "a" * 64
    )
    with pytest.raises(asyncpg.CheckViolationError, match="replacement revision"):
        await direct_pg.conn.execute(
            "UPDATE amex_merchant_identity_aliases SET active=false WHERE id=$1", alias
        )
    async with direct_pg.conn.transaction():
        await direct_pg.conn.execute(
            "UPDATE amex_merchant_identity_aliases SET active=false WHERE id=$1", alias
        )
        await direct_pg.conn.execute(
            sql, uuid4(), direct_pg.card.id, 2, direct_pg.actor.id, "b" * 64
        )
    assert (
        await direct_pg.conn.fetchval(
            "SELECT issuer_rfc FROM amex_merchant_identity_aliases WHERE id=$1", alias
        )
        == "AAA010101AAA"
    )
    with pytest.raises(asyncpg.CheckViolationError, match="retirement"):
        await direct_pg.conn.execute(
            (
                "UPDATE amex_merchant_identity_aliases SET issuer_rfc='CCC010"
                "101CCC' WHERE id=$1"
            ),
            alias,
        )


@pytest.mark.asyncio
async def test_recognized_cfdi_cannot_acquire_new_expense_owner(direct_pg):
    async with direct_pg.conn.transaction():
        data = await debit_source(direct_pg)
        await posting(direct_pg, data)
    from devnous.gastos.models import ExpenseReport

    columns = [column.name for column in ExpenseReport.__table__.columns]
    selections = [
        "$1" if name == "id" else "$2" if name == "cfdi_report_id" else name
        for name in columns
    ]
    query = (
        "INSERT INTO expense_reports("
        + ",".join(columns)
        + ") SELECT "
        + ",".join(selections)
        + " FROM expense_reports WHERE id=$3"
    )
    with pytest.raises(asyncpg.CheckViolationError, match="new economic owner"):
        await direct_pg.conn.execute(query, uuid4(), data["cfdi"], data["expense"])


@pytest.mark.asyncio
async def test_cfdi_new_owner_race_waits_for_cut_then_rejects(direct_pg):
    import asyncio
    import os

    from sqlalchemy.engine import make_url

    from devnous.gastos.models import ExpenseReport

    async with direct_pg.conn.transaction():
        data = await debit_source(direct_pg)
    url = make_url(os.environ["TEST_AMEX_DATABASE_URL"])
    other = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=url.database,
        server_settings={"search_path": direct_pg.schema},
    )
    columns = [column.name for column in ExpenseReport.__table__.columns]
    selections = [
        "$1" if name == "id" else "$2" if name == "cfdi_report_id" else name
        for name in columns
    ]
    query = (
        "INSERT INTO expense_reports("
        + ",".join(columns)
        + ") SELECT "
        + ",".join(selections)
        + " FROM expense_reports WHERE id=$3"
    )
    tx = direct_pg.conn.transaction()
    await tx.start()
    try:
        await posting(direct_pg, data)
        pending = asyncio.create_task(
            other.execute(query, uuid4(), data["cfdi"], data["expense"])
        )
        await asyncio.sleep(0.05)
        assert not pending.done()
        await tx.commit()
        with pytest.raises(asyncpg.CheckViolationError, match="new economic owner"):
            await asyncio.wait_for(pending, 3)
        assert await other.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    finally:
        if direct_pg.conn.is_in_transaction():
            await tx.rollback()
        await other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "currency,economic_date", [("USD", date(2026, 10, 10)), ("MXN", date(2026, 11, 1))]
)
async def test_direct_recognition_rejects_fx_or_outside_declared_period(
    direct_pg, currency, economic_date
):
    with pytest.raises(asyncpg.CheckViolationError, match="period/currency"):
        async with direct_pg.conn.transaction():
            data = await debit_source(direct_pg)
            await direct_pg.conn.execute(
                "UPDATE expense_reports SET currency=$1,fecha=$2 WHERE id=$3",
                currency,
                economic_date,
                data["expense"],
            )
            await posting(
                direct_pg, data, currency=currency, economic_date=economic_date
            )
    assert await direct_pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["valid", "wrong_account", "wrong_amount", "missing_proof"]
)
async def test_withholding_requires_exact_canonical_amount_and_account(direct_pg, mode):
    """A net bank debit permits real tax credit, never a forged balancing credit."""
    pg = direct_pg

    async def post():
        async with pg.conn.transaction():
            data = await debit_source(pg)
            retention, forged = uuid4(), uuid4()
            await pg.conn.executemany(
                "INSERT INTO cuentas_contables(id,codigo,nombre,tipo,activo,"
                "creado_en,actualizado_en) "
                "VALUES($1,$2,'Retencion','pasivo',true,now(),now())",
                [(retention, "2150-001-002"), (forged, "9999-999-999")],
            )
            await pg.conn.execute(
                "UPDATE expense_reports SET retencion_cuentas_json=$1::jsonb "
                "WHERE id=$2",
                json.dumps({"001": str(retention)}),
                data["expense"],
            )
            await pg.conn.execute(
                "UPDATE cfdi_reports SET impuestos_detalle=$1::jsonb WHERE id=$2",
                json.dumps({"retenciones": [{"impuesto": "001", "importe": 10}]}),
                data["cfdi"],
            )
            account = forged if mode == "wrong_account" else retention
            retained = 11 if mode == "wrong_amount" else 10
            proof = {
                "impuesto": "001",
                "importe": str(retained),
                "cuenta_contable_id": str(account),
                "cuenta_codigo": (
                    "9999-999-999" if mode == "wrong_account" else "2150-001-002"
                ),
            }
            frozen_line = {
                "cuenta_contable_id": str(account),
                "haber": str(retained),
                "debe": "0",
            }
            poliza, consumption, cut = await posting(
                pg,
                data,
                journal_rows=[
                    (1, 94 + retained - 10, 0, pg.gasto.id),
                    (2, 16, 0, pg.iva.id),
                    (3, 0, 100, pg.liability.id),
                    (4, 0, retained, account),
                ],
                line_metadata={
                    4: {
                        "movement": "haber_retencion",
                        "expense_id": str(data["expense"]),
                    }
                },
                snapshot_json=json.dumps(
                    {
                        "fiscal_retention_proof": (
                            [] if mode == "missing_proof" else [proof]
                        ),
                        "journal_lines": [frozen_line],
                    }
                ),
            )
        assert (
            await pg.conn.fetchval(
                "SELECT sum(haber) FROM accounting_poliza_lines WHERE poliza_id=$1",
                poliza,
            )
            == 110
        )
        return consumption, cut

    if mode == "valid":
        await post()
    else:
        with pytest.raises(asyncpg.CheckViolationError):
            await post()
        assert (
            await pg.conn.fetchval("SELECT count(*) FROM amex_direct_accounting_cuts")
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("edition", [None, 2025])
async def test_new_cut_requires_actual_economic_and_accounting_budget_edition(
    direct_pg, edition
):
    with pytest.raises(asyncpg.CheckViolationError, match="budget edition"):
        async with direct_pg.conn.transaction():
            data = await debit_source(direct_pg)
            await direct_pg.conn.execute(
                "UPDATE amex_direct_items SET edition_year=$1 WHERE id=$2",
                edition,
                data["item"],
            )
            await posting(direct_pg, data)


@pytest.mark.asyncio
async def test_fractional_retention_rounded_to_zero_requires_no_credit(direct_pg):
    async with direct_pg.conn.transaction():
        data = await debit_source(direct_pg)
        await direct_pg.conn.execute(
            "UPDATE cfdi_reports SET impuestos_detalle=$1::jsonb WHERE id=$2",
            json.dumps({"retenciones": [{"impuesto": "001", "importe": "0.004"}]}),
            data["cfdi"],
        )
        poliza, _, _ = await posting(direct_pg, data)
    assert (
        await direct_pg.conn.fetchval(
            "SELECT sum(haber) FROM accounting_poliza_lines WHERE poliza_id=$1", poliza
        )
        == 100
    )
