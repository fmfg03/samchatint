"""Actual ledger-owner acceptance of historical AMEX credit settlements."""

import hashlib
import importlib.util
import json
import pprint
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from devnous.gastos import models
from devnous.gastos.services import amex_direct_accounting_service as accounting
from devnous.gastos.services import (
    amex_expense_account_settlement_service as settlements,
)

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "settlement_pg_fixture",
    ROOT / "tests/acceptance/test_amex_sat_budget_direct_postgres.py",
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
direct_pg = fixture.direct_pg


@pytest.mark.asyncio
async def test_schema_receipt_capture(direct_pg):
    """Metadata receipt only; requested explicitly during owner-DDL development."""
    import os

    if os.getenv("CAPTURE_SETTLEMENT_SCHEMA") != "1":
        pytest.skip("Receipt regeneration is explicit")
    from devnous.gastos.amex_schema_policy import (
        AMEX_DIRECT_OWNER_MIGRATION_TABLES,
        _catalog_check,
    )

    names = sorted(
        AMEX_DIRECT_OWNER_MIGRATION_TABLES
        | {
            "amex_statement_imports",
            "amex_statement_import_items",
            "amex_recognition_consumptions",
        }
    )
    rows = await direct_pg.conn.fetch(
        """SELECT c.relname,k.conname,k.contype::text,k.convalidated,
ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY AS
key(attnum,ord) JOIN pg_attribute a ON a.attrelid=k.conrelid AND
a.attnum=key.attnum ORDER BY key.ord) columns,
r.relname target,ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY
AS key(attnum,ord) JOIN pg_attribute a ON a.attrelid=k.confrelid AND
a.attnum=key.attnum ORDER BY key.ord)
target_columns,k.confdeltype::text,pg_get_constraintdef(k.oid) definition
FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n
ON n.oid=c.relnamespace LEFT JOIN pg_class r ON r.oid=k.confrelid WHERE
n.nspname=current_schema() AND c.relname=ANY($1)""",
        names,
    )
    checks = {}
    keys = []
    for r in rows:
        base = (r["relname"], r["contype"], tuple(r["columns"]))
        if r["contype"] == "c":
            checks[(r["relname"], r["conname"])] = r["definition"]
        elif r["contype"] == "f":
            keys.append(
                (*base, r["target"], tuple(r["target_columns"]), r["confdeltype"])
            )
        elif r["contype"] in ("u", "p"):
            keys.append(base)
    import subprocess

    baseline = {}
    exec(
        subprocess.check_output(
            [
                "git",
                "show",
                "HEAD:src/devnous/gastos/services/amex_direct_schema_contract.py",
            ],
            cwd=ROOT,
            text=True,
        ),
        baseline,
    )
    triggers = await direct_pg.conn.fetch(
        "SELECT t.tgname,c.relname,p.proname,t.tgtype::integer,"
        "t.tgdeferrable,t.tginitdeferred FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_proc p ON p.oid=t.tgfoid "
        "WHERE n.nspname=current_schema() AND NOT t.tgisinternal AND "
        "(t.tgname=ANY($1) OR t.tgname LIKE 'ad_%' OR "
        "t.tgname LIKE 'aesc_%' OR t.tgname LIKE 'amex_direct_%')",
        list(baseline["EXPECTED_TRIGGERS"]),
    )
    functions = {}
    for name in set(r["proname"] for r in triggers) | set(
        baseline["EXPECTED_FUNCTION_HASHES"]
    ):
        body = await direct_pg.conn.fetchval(
            (
                "SELECT prosrc FROM pg_proc p JOIN pg_namespace n ON "
                "n.oid=p.pronamespace WHERE n.nspname=current_schema() "
                "AND p.proname=$1"
            ),
            name,
        )
        functions[name] = hashlib.sha256(body.encode()).hexdigest()
    old = {}
    exec(
        (
            ROOT / "src/devnous/gastos/services/amex_direct_schema_contract.py"
        ).read_text(),
        old,
    )
    assert set(baseline["EXPECTED_TRIGGERS"]) <= {r["tgname"] for r in triggers}
    assert set(baseline["EXPECTED_FUNCTION_HASHES"]) <= set(functions)
    values = {
        "EXPECTED_CHECKS": checks,
        "EXPECTED_KEYS": sorted(keys),
        "EXPECTED_TRIGGERS": {
            r["tgname"]: tuple(
                r[k]
                for k in (
                    "relname",
                    "proname",
                    "tgtype",
                    "tgdeferrable",
                    "tginitdeferred",
                )
            )
            for r in triggers
        },
        "EXPECTED_FUNCTION_HASHES": functions,
        "EXPECTED_INDEXES": old["EXPECTED_INDEXES"],
        "EXPECTED_EFFECTIVE_VIEW_HASH": old["EXPECTED_EFFECTIVE_VIEW_HASH"],
        "EXPECTED_EFFECTIVE_VIEW_DEFINITION": old["EXPECTED_EFFECTIVE_VIEW_DEFINITION"],
    }
    views = {}
    view_definitions = {}
    for name in ["amex_expense_account_credit_sources", "amex_direct_prior_sources"]:
        body = await direct_pg.conn.fetchval(
            "SELECT pg_get_viewdef($1::regclass,true)", name
        )
        view_definitions[name] = body.replace(direct_pg.schema + ".", "")
        views[name] = hashlib.sha256(
            _catalog_check(body.replace(direct_pg.schema + ".", "")).encode()
        ).hexdigest()
    values["EXPECTED_SETTLEMENT_VIEW_HASHES"] = views
    values["EXPECTED_SETTLEMENT_VIEW_DEFINITIONS"] = view_definitions
    path = Path("/tmp/samchat-settlement-schema-contract-20261020.py")
    path.write_text(
        (
            '"""Static owner-DDL receipt from disposable '
            'PostgreSQL; never runtime-learned."""\n\n'
        )
        + "\n\n".join(
            k + " = " + pprint.pformat(v, width=88, sort_dicts=True)
            for k, v in values.items()
        )
        + "\n"
    )
    assert keys and functions and views


async def historical_credit(
    pg, *, post=True, fiscal_uuid="10000000-0000-4000-8000-000000000001", origin="sat"
):
    from devnous.gastos.services.cfdi_ingestion_service import ingest_cfdi_xml
    from devnous.gastos.services.employee_debtor_accounting_service import (
        ensure_debtor_comprobacion_posting_for_informe,
    )

    async with pg.factory.begin() as session:
        cfdi = (
            await ingest_cfdi_xml(
                session, fixture._synthetic_sat_xml(fiscal_uuid), source=origin
            )
        ).cfdi_report
    async with pg.factory.begin() as session:
        debtor = (
            await session.execute(
                select(models.CuentaContable).where(
                    models.CuentaContable.codigo == "1170-001-899"
                )
            )
        ).scalar_one_or_none() or models.CuentaContable(
            id=uuid4(),
            codigo="1170-001-899",
            nombre=pg.owner.nombre,
            tipo="activo",
            activo=True,
        )
        session.add(debtor)
        cuenta = models.CuentaDeGastos(
            id=uuid4(),
            empleado_id=pg.owner.id,
            referencia_base=uuid4().hex,
            nombre="Original",
            tipo_cuenta="local",
            torneo_id=pg.tournament.id,
        )
        session.add(cuenta)
        await session.flush()
        document = models.Documento(
            id=uuid4(),
            empleado_id=pg.owner.id,
            tipo="INFORME",
            estado="aprobado",
            aprobado_en=datetime(2026, 10, 5),
            cuenta_gastos_id=cuenta.id,
            numero_referencia="I-" + uuid4().hex[:8],
            monto_total=116,
            torneo_id=pg.tournament.id,
        )
        session.add(document)
        expense = models.ExpenseReport(
            id=uuid4(),
            empleado_id=pg.owner.id,
            proyecto=pg.tournament.name,
            concepto="Comida histórica",
            gasto_cantidad=116,
            currency="MXN",
            fecha=datetime(2026, 10, 2),
            cuenta_gastos_id=cuenta.id,
            informe_documento_id=document.id,
            cfdi_report_id=cfdi.id,
            cuenta_contable_id=pg.gasto.id,
            cuenta_iva_id=pg.iva.id,
            estado_gasto="activo",
            pagado_con_amex_empresa=False,
        )
        session.add(expense)
        await session.flush()
        if post:
            result = await ensure_debtor_comprobacion_posting_for_informe(
                session, informe_documento=document
            )
            assert result.status == "created", result.reason
            origin = result.poliza.id
    current = await fixture._direct_import(
        pg,
        (
            "Date,Description,Amount\n" f"2026-10-02,COMERCIO {fiscal_uuid},-116.00\n"
        ).encode(),
        key="history-" + fiscal_uuid,
    )
    from devnous.gastos.services.amex_direct_context_service import (
        list_direct_statement,
    )

    async with pg.factory() as session:
        current = await list_direct_statement(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
    if not post:
        return current, None, expense, cuenta, document
    rows = await pg.conn.fetch(
        "SELECT * FROM amex_expense_account_credit_sources WHERE origin_poliza_id=$1",
        origin,
    )
    assert len(rows) == 1, rows
    return current, dict(rows[0]), expense, cuenta, document


async def select_credit(pg, current, row):
    item = current.items[0]
    async with pg.factory.begin() as session:
        listed = await settlements.list_settlement_sources(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            item_id=item.id,
        )
        assert len(listed["candidates"]) == 1, listed
        selected = await settlements.select_settlement_source(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            item_id=item.id,
            **{k: row[k] for k in settlements.IDENTITY_KEYS},
            expected_item_version=listed["item_version"],
            expected_source_version=listed["source_version"],
            reason="Cancelación explícita de cuenta comprobada",
            confirmed=True,
        )
        assert selected.status == "selected"
    return selected


async def confirm_selected(pg, current, key="settlement"):
    async with pg.factory.begin() as session:
        preview = await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
        assert preview.status == "ready", preview.progress
        result = await accounting.confirm_direct_accounting(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            expected_source_key=preview.source_key,
            expected_source_version=preview.source_version,
            accounting_date=date(2026, 10, 31),
            confirmed=True,
            idempotency_key=key,
        )
        assert result.status in {"recognized", "completed"}, result.reason
    return result, preview


@pytest.mark.asyncio
async def test_actual_debtor_owner_settlement_and_auxiliary_no_new_fiscal_or_budget(
    direct_pg,
):
    pg = direct_pg
    current, row, expense, cuenta, document = await historical_credit(pg)
    await select_credit(pg, current, row)
    before = await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments")
    result, preview = await confirm_selected(pg, current)
    assert preview.progress["settlement_count"] == 1
    lines = await pg.conn.fetch(
        "SELECT * FROM accounting_poliza_lines WHERE poliza_id=$1 ORDER BY line_no",
        result.poliza.id,
    )
    assert len(lines) == 2
    assert lines[0]["cuenta_contable_id"] == row["origin_account_id"]
    assert lines[0]["debe"] == 116 and lines[1]["haber"] == 116
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments")
        == before
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 0
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_expense_account_settlements")
        == 1
    )
    from devnous.gastos.services.employee_debtor_accounting_service import (
        build_cuenta_debtor_auxiliary,
    )

    async with pg.factory() as session:
        auxiliary = await build_cuenta_debtor_auxiliary(session, cuenta_id=cuenta.id)
        assert auxiliary["saldo"] == 0
        assert auxiliary["comprobado"] == 116
        for format in ["csv", "xlsx", "zip"]:
            body, name, mime = await accounting.export_direct_cut(
                session,
                actor_id=pg.actor.id,
                import_id=current.statement.id,
                format=format,
            )
            assert body and name.endswith("." + format)
    async with pg.factory.begin() as session:
        replay = await accounting.confirm_direct_accounting(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            expected_source_key=preview.source_key,
            expected_source_version=preview.source_version,
            accounting_date=date(2026, 10, 31),
            confirmed=True,
            idempotency_key="settlement",
        )
        assert replay.cut.id == result.cut.id
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_expense_account_settlements")
        == 1
    )


async def historical_prior(
    pg, *, fiscal_uuid="10000000-0000-4000-8000-000000000001", origin="sat"
):
    from devnous.gastos.services import amex_accounting_cut_service as cuts

    current, _, expense, cuenta, document = await historical_credit(
        pg, post=False, fiscal_uuid=fiscal_uuid, origin=origin
    )
    async with pg.factory.begin() as session:
        bank = await session.get(
            models.ExpenseReport, current.items[0].imported_expense_id
        )
        bank.cfdi_report_id = None
        e = await session.get(models.ExpenseReport, expense.id)
        e.pagado_con_amex_empresa = True
        e.ultimos_4_digitos = pg.card.last4
        e.contra_cuenta_contable_id = pg.liability.id
        await session.flush()
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor.id)
        preview = await cuts.preview_amex_approved_informe(
            session, informe_id=document.id, actor=actor
        )
        assert preview.status == "ready", preview.reason
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor.id)
        result = await cuts.confirm_amex_approved_informe_cut(
            session,
            actor=actor,
            informe_id=document.id,
            expected_source_key=preview.source_key,
            expected_versions=preview.current_versions,
            expected_approval_id=(
                preview.approval_event.id if preview.approval_event else None
            ),
            expected_approval_version=preview.approval_version,
            decisions=[
                dict(expense_ids=[str(expense.id)], treatment="expense", reason="")
            ],
            accounting_date=date(2026, 10, 2),
            confirmed=True,
            reason="Confirmación histórica actual",
        )
        assert result.status == "created", result.reason
    rows = await pg.conn.fetch("SELECT * FROM amex_direct_prior_sources")
    assert len(rows) == 1, rows
    return current, dict(rows[0]), expense, cuenta, document


@pytest.mark.asyncio
async def test_actual_report_owner_prior_only_completion_no_empty_journal(direct_pg):
    pg = direct_pg
    current, row, expense, cuenta, document = await historical_prior(pg)
    await select_credit(pg, current, row)
    result, preview = await confirm_selected(pg, current)
    assert result.status == "completed"
    assert result.poliza is None and result.cut is None
    assert preview.progress["completion_only"] is True
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_completion_receipts")
        == 1
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_prior_coverages") == 1
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_accounting_cuts") == 0
    )
    async with pg.factory() as session:
        frozen = await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
        assert frozen.status == "completed"
        assert (
            frozen.progress["completion_receipt"]["id"]
            == result.progress["completion_receipt"]["id"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,reason",
    [
        ("debit_advance", "expense_account_residual_not_attributable"),
        ("foreign_receiver", "historical_cfdi_identity_conflict"),
        ("reversed", "expense_account_credit_not_proven"),
        ("wrong_account", "expense_account_credit_not_proven"),
    ],
)
async def test_credit_source_rejects_ambiguous_or_changed_original(
    direct_pg, change, reason
):
    pg = direct_pg
    current, row, expense, cuenta, document = await historical_credit(pg)
    if change == "debit_advance":
        await pg.conn.execute(
            (
                "INSERT INTO "
                "accounting_poliza_lines(id,poliza_id,line_no,cuenta_codigo,"
                "cuenta_contable_id,debe,haber,raw_row_json,created_at) "
                "VALUES($1,$2,99,$3,$4,116,0,$5::jsonb,now())"
            ),
            uuid4(),
            row["origin_poliza_id"],
            row["origin_account_code"],
            row["origin_account_id"],
            json.dumps({"cuenta_gastos_id": str(cuenta.id), "movement": "debe_deudor"}),
        )
    elif change == "foreign_receiver":
        await pg.conn.execute(
            "UPDATE cfdi_reports SET receptor_rfc='CCC010101CCC' WHERE id=$1",
            expense.cfdi_report_id,
        )
    elif change == "reversed":
        await pg.conn.execute(
            "UPDATE expense_reports SET coi_estado='reversar' WHERE id=$1", expense.id
        )
    else:
        await pg.conn.execute(
            (
                "UPDATE accounting_poliza_lines SET "
                "cuenta_codigo='1170-001-000' WHERE id=$1"
            ),
            row["origin_line_id"],
        )
    async with pg.factory() as session:
        with pytest.raises(settlements.DirectConflict, match=reason):
            await settlements.credit_source(
                session,
                origin_line_id=row["origin_line_id"],
                context=current.context,
                config=__import__(
                    "devnous.gastos.services.amex_direct_common",
                    fromlist=["configured_scopes"],
                ).configured_scopes()[0],
                actor_id=pg.actor.id,
                amount=Decimal("116"),
            )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_expense_account_settlements")
        == 0
    )


@pytest.mark.asyncio
async def test_settlement_late_guard_rolls_back_even_caller_catches_and_commits(
    direct_pg,
):
    pg = direct_pg
    current, row, *_ = await historical_credit(pg)
    await select_credit(pg, current, row)
    await pg.conn.execute(
        (
            "CREATE FUNCTION test_late_settlement_failure() RETURNS "
            "trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
            "'test late failure' USING ERRCODE='23514'; END $$; "
            "CREATE CONSTRAINT TRIGGER test_late_failure AFTER "
            "INSERT ON amex_expense_account_settlement_allocations "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE "
            "FUNCTION test_late_settlement_failure();"
        )
    )
    async with pg.factory.begin() as session:
        preview = await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
        with pytest.raises(Exception, match="test late failure"):
            await accounting.confirm_direct_accounting(
                session,
                actor_id=pg.actor.id,
                import_id=current.statement.id,
                expected_source_key=preview.source_key,
                expected_source_version=preview.source_version,
                accounting_date=date(2026, 10, 31),
                confirmed=True,
                idempotency_key="late",
            )
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_expense_account_settlements")
        == 0
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_accounting_cuts") == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    ["allocation", "header", "origin_line", "origin_expense", "origin_document"],
)
async def test_final_settlement_and_original_evidence_frozen(direct_pg, target):
    pg = direct_pg
    current, row, expense, cuenta, document = await historical_credit(pg)
    await select_credit(pg, current, row)
    await confirm_selected(pg, current)
    queries = {
        "allocation": (
            "UPDATE amex_expense_account_settlement_allocations SET amount=1",
            (),
        ),
        "header": ("DELETE FROM amex_expense_account_settlements", ()),
        "origin_line": (
            "UPDATE accounting_poliza_lines SET haber=1 WHERE id=$1",
            (row["origin_line_id"],),
        ),
        "origin_expense": (
            "UPDATE expense_reports SET coi_estado='reversar' WHERE id=$1",
            (expense.id,),
        ),
        "origin_document": (
            "UPDATE documentos SET estado='cancelado' WHERE id=$1",
            (document.id,),
        ),
    }
    query, args = queries[target]
    with pytest.raises(Exception, match="append|immutable|frozen"):
        await pg.conn.execute(query, *args)


@pytest.mark.asyncio
async def test_concurrent_same_statement_yields_one_settlement(direct_pg):
    import asyncio

    pg = direct_pg
    current, row, *_ = await historical_credit(pg)
    await select_credit(pg, current, row)
    async with pg.factory() as session:
        preview = await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
    args = dict(
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        expected_source_key=preview.source_key,
        expected_source_version=preview.source_version,
        accounting_date=date(2026, 10, 31),
        confirmed=True,
        idempotency_key="race",
    )

    async def execute():
        async with pg.factory.begin() as session:
            return await accounting.confirm_direct_accounting(session, **args)

    first, second = await asyncio.gather(execute(), execute())
    assert first.cut.id == second.cut.id
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_expense_account_settlements")
        == 1
    )


@pytest.mark.asyncio
async def test_distinct_imports_compete_for_one_original_credit_without_overallocation(
    direct_pg,
):
    import asyncio

    from devnous.gastos.services.amex_direct_context_service import (
        list_direct_statement,
    )

    pg = direct_pg
    first, row, *_ = await historical_credit(pg)
    second = await fixture._direct_import(
        pg,
        b"Date,Description,Amount\n2026-10-02,SEGUNDO CARGO,-116.00\n",
        key="second-credit-competitor",
    )
    async with pg.factory() as session:
        second = await list_direct_statement(
            session, actor_id=pg.actor.id, import_id=second.statement.id
        )
    await select_credit(pg, first, row)
    await select_credit(pg, second, row)

    async def execute(current):
        async with pg.factory.begin() as session:
            preview = await accounting.preview_direct_accounting(
                session, actor_id=pg.actor.id, import_id=current.statement.id
            )
            return await accounting.confirm_direct_accounting(
                session,
                actor_id=pg.actor.id,
                import_id=current.statement.id,
                expected_source_key=preview.source_key,
                expected_source_version=preview.source_version,
                accounting_date=date(2026, 10, 31),
                confirmed=True,
                idempotency_key="compete-" + str(current.statement.id),
            )

    outcomes = await asyncio.gather(execute(first), execute(second))
    assert sorted(r.status for r in outcomes) == ["pending", "recognized"]
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_expense_account_settlements")
        == 1
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 2
    assert await pg.conn.fetchval(
        "SELECT sum(amount) FROM amex_expense_account_settlement_allocations"
    ) == Decimal("116")
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_accounting_cuts") == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["mutation", "allocation"])
async def test_source_mutation_and_allocation_serialize_both_winner_directions(
    direct_pg, first
):
    import asyncio

    pg = direct_pg
    current, row, expense, *_ = await historical_credit(pg)
    await select_credit(pg, current, row)
    async with pg.factory() as session:
        preview = await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
    args = dict(
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        expected_source_key=preview.source_key,
        expected_source_version=preview.source_version,
        accounting_date=date(2026, 10, 31),
        confirmed=True,
        idempotency_key="source-race",
    )
    if first == "mutation":
        tx = pg.conn.transaction()
        await tx.start()
        await pg.conn.execute(
            "UPDATE expense_reports SET coi_estado='reversar' WHERE id=$1", expense.id
        )

        async def allocate():
            async with pg.factory.begin() as session:
                return await accounting.confirm_direct_accounting(session, **args)

        task = asyncio.create_task(allocate())
        await asyncio.sleep(0.1)
        assert not task.done()
        await tx.commit()
        result = await asyncio.wait_for(task, 10)
        assert result.status == "pending"
        assert (
            await pg.conn.fetchval(
                "SELECT count(*) FROM amex_expense_account_settlements"
            )
            == 0
        )
        assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    else:
        session = pg.factory()
        await session.begin()
        result = await accounting.confirm_direct_accounting(session, **args)
        assert result.status == "recognized"
        task = asyncio.create_task(
            pg.conn.execute(
                "UPDATE expense_reports SET coi_estado='reversar' WHERE id=$1",
                expense.id,
            )
        )
        await asyncio.sleep(0.1)
        assert not task.done()
        await session.commit()
        await session.close()
        with pytest.raises(Exception, match="frozen"):
            await asyncio.wait_for(task, 10)
        assert (
            await pg.conn.fetchval(
                "SELECT count(*) FROM amex_expense_account_settlements"
            )
            == 1
        )
        assert (
            await pg.conn.fetchval(
                "SELECT coi_estado FROM expense_reports WHERE id=$1", expense.id
            )
            != "reversar"
        )


@pytest.mark.asyncio
async def test_prior_source_rechecks_revoked_project_scope_after_selection(direct_pg):
    from copy import deepcopy

    from devnous.gastos.services.amex_direct_common import configured_scopes
    from devnous.gastos.services.amex_direct_effective_source_service import (
        effective_source_for_item,
    )

    pg = direct_pg
    current, row, *_ = await historical_prior(pg)
    await select_credit(pg, current, row)
    config = deepcopy(configured_scopes()[0])
    for actor in config["actor_scopes"]:
        if actor["actor_id"] == str(pg.actor.id):
            actor["tournament_ids"] = []
            actor["project_codes"] = []
    async with pg.factory() as session:
        item = await session.get(models.AmexDirectItem, current.items[0].id)
        source = await effective_source_for_item(session, item)
        with pytest.raises(settlements.DirectConflict, match="scope_mismatch"):
            await settlements.prior_source(
                session,
                item=item,
                source=source,
                context=current.context,
                card=pg.card,
                config=config,
                actor_id=pg.actor.id,
            )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_prior_coverages") == 0
    )


@pytest.mark.asyncio
async def test_source_picker_mixed_pages_and_foreign_forged_stale_cursor(
    direct_pg,
):
    pg = direct_pg
    prior, prior_row, *_ = await historical_prior(pg, fiscal_uuid=str(uuid4()))
    for _ in range(26):
        current, row, *_ = await historical_credit(pg, fiscal_uuid=str(uuid4()))
    navigation = {}
    args = dict(
        actor_id=pg.actor.id,
        import_id=current.statement.id,
        item_id=current.items[0].id,
        navigation=navigation,
    )
    async with pg.factory() as session:
        first = await settlements.list_settlement_sources(session, **args)
        assert (
            len(first["candidates"]) == 25
            and first["has_more"]
            and first["next_cursor"]
        )
        assert first["candidates"][0]["coverage_kind"] == "prior"
        last = await settlements.list_settlement_sources(
            session, **args, cursor=first["next_cursor"]
        )
        assert len(last["candidates"]) == 2 and not last["has_more"]
        ids = [
            str(r["origin_line_id"]) for r in first["candidates"] + last["candidates"]
        ]
        assert len(set(ids)) == 27 and str(row["origin_line_id"]) in ids
        for token in ["forged", first["next_cursor"]]:
            invalid = {**args}
            if token != "forged":
                invalid.update(import_id=prior.statement.id, item_id=prior.items[0].id)
            with pytest.raises(
                settlements.DirectConflict,
                match="invalid_expense_account_source_cursor",
            ):
                await settlements.list_settlement_sources(
                    session, **invalid, cursor=token
                )
    async with pg.factory.begin() as session:
        await settlements.select_settlement_source(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            item_id=current.items[0].id,
            **{k: row[k] for k in settlements.IDENTITY_KEYS},
            expected_item_version=first["item_version"],
            expected_source_version=first["source_version"],
            reason="Selección entre todas las páginas",
            confirmed=True,
        )
    async with pg.factory() as session:
        with pytest.raises(
            settlements.DirectConflict, match="invalid_expense_account_source_cursor"
        ):
            await settlements.list_settlement_sources(
                session, **args, cursor=first["next_cursor"]
            )


@pytest.mark.asyncio
async def test_mixed_r1_r2_r3_single_cut_actual_owners(direct_pg):
    from devnous.gastos.services import amex_direct_budget_service as budget

    pg = direct_pg
    _, credit_row, original, cuenta, _ = await historical_credit(
        pg, fiscal_uuid=str(uuid4())
    )
    _, prior_row, *_ = await historical_prior(pg, fiscal_uuid=str(uuid4()))
    mixed, _, _ = await fixture._matched_statement(
        pg,
        content=(
            b"Date,Description,Amount\n"
            b"2026-10-02,COMERCIO ACME CDMX,-116.00\n"
            b"2026-10-02,CUENTA HISTORICA,-116.00\n"
            b"2026-10-02,PASIVO PREVIO,-116.00\n"
        ),
    )
    current = await fixture._direct_current(pg, mixed.statement.id)
    new = next(i for i in current.items if i.match_status == "matched")
    async with pg.factory.begin() as session:
        result = await budget.classify_direct_items(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            item_ids=[new.id],
            tournament_id=pg.tournament.id,
            project_code=None,
            budget_concept_id=pg.concept.id,
            edition_year=2026,
            expected_versions={str(new.id): new.source_version},
            reason="Gasto nuevo",
        )
        assert result.status != "pending", result.reason
    current = await fixture._direct_current(pg, current.statement.id)
    new = next(i for i in current.items if i.id == new.id)
    async with pg.factory.begin() as session:
        result = await budget.send_direct_items_to_budget(
            session,
            actor_id=pg.actor.id,
            import_id=current.statement.id,
            item_ids=[new.id],
            expected_versions={str(new.id): new.source_version},
            confirmed=True,
        )
        assert result.status != "pending", result.reason
    current = await fixture._direct_current(pg, current.statement.id)
    new = next(i for i in current.items if i.id == new.id)
    async with pg.factory.begin() as session:
        result = await budget.assign_direct_budget_line(
            session,
            actor_id=pg.cp_actor.id,
            item_id=new.id,
            budget_line_id=pg.line_id,
            expected_source_version=new.source_version,
            expected_source_hash=new.source_hash,
            idempotency_key="mixed-new-budget",
        )
        assert result.status != "pending", result.reason
    for description, row in [
        ("CUENTA HISTORICA", credit_row),
        ("PASIVO PREVIO", prior_row),
    ]:
        current = await fixture._direct_current(pg, current.statement.id)
        item = next(i for i in current.items if description in str(i.source_json))
        async with pg.factory.begin() as session:
            await settlements.select_settlement_source(
                session,
                actor_id=pg.actor.id,
                import_id=current.statement.id,
                item_id=item.id,
                **{k: row[k] for k in settlements.IDENTITY_KEYS},
                expected_item_version=item.source_version,
                expected_source_version=current.source_version,
                reason="Identidad explícita " + description,
                confirmed=True,
            )
    before = await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas")
    result, preview = await confirm_selected(pg, current, key="mixed")
    assert (
        preview.progress["new_count"] == 1
        and preview.progress["settlement_count"] == 1
        and preview.progress["prior_count"] == 1
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == before + 1
    )
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_cut_items WHERE cut_id=$1", result.cut.id
        )
        == 3
    )
    lines = await pg.conn.fetch(
        "SELECT * FROM accounting_poliza_lines WHERE poliza_id=$1", result.poliza.id
    )
    assert len(lines) == 5
    assert sum(
        Decimal(str(entry["haber"] or 0))
        for entry in lines
        if entry["cuenta_contable_id"] == pg.liability.id
    ) == Decimal("232")
    assert sum(
        Decimal(str(entry["debe"] or 0))
        for entry in lines
        if entry["cuenta_contable_id"] == pg.iva.id
    ) == Decimal("16")
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_prior_coverages") == 1
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_expense_account_settlements")
        == 1
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments") == 1
    )


@pytest.mark.asyncio
async def test_prior_invalid_membership_and_competing_cut_rejected(
    direct_pg,
):
    from sqlalchemy.exc import IntegrityError

    pg = direct_pg
    current, row, *_ = await historical_prior(pg)
    await select_credit(pg, current, row)
    async with pg.factory() as session:
        preview = await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=current.statement.id
        )
    with pytest.raises(IntegrityError, match="Completion requires"):
        async with pg.factory.begin() as session:
            session.add(
                models.AmexDirectCompletionReceipt(
                    import_id=current.statement.id,
                    context_id=current.context.id,
                    context_version=current.context.version,
                    source_version=preview.source_version,
                    source_hash=preview.source_key,
                    payload_hash="a" * 64,
                    actor_id=pg.actor.id,
                    snapshot_json={
                        "partidas": [{"direct_item_id": str(current.items[0].id)}]
                    },
                )
            )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_completion_receipts")
        == 0
    )
    completed, preview = await confirm_selected(pg, current, key="completion-negative")
    assert completed.status == "completed"
    with pytest.raises(IntegrityError, match="Completed statement cannot create cut"):
        async with pg.factory.begin() as session:
            session.add(
                models.AmexDirectAccountingCut(
                    import_id=current.statement.id,
                    context_id=current.context.id,
                    context_version=current.context.version,
                    source_version=preview.source_version,
                    source_hash=preview.source_key,
                    payload_hash="b" * 64,
                    accounting_poliza_id=row["origin_poliza_id"],
                    accounting_date=date(2026, 10, 31),
                    actor_id=pg.actor.id,
                    snapshot_json={"partidas": []},
                )
            )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_accounting_cuts") == 0
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1


@pytest.mark.asyncio
async def test_owner_migration_empty_rollback_roundtrip_and_final_evidence_refusal(
    direct_pg,
):
    pg = direct_pg
    forward = ROOT / "database/migrations/20261020_amex_expense_account_settlements.sql"
    rollback = (
        ROOT
        / "database/migrations/20261020_amex_expense_account_settlements_rollback.sql"
    )
    await pg.conn.execute(rollback.read_text())
    assert (
        await pg.conn.fetchval("SELECT to_regclass('amex_expense_account_settlements')")
        is None
    )
    await pg.conn.execute(forward.read_text())
    from devnous.gastos.services.amex_direct_schema_service import (
        inspect_amex_direct_schema,
    )

    async with pg.factory() as session:
        receipt = await inspect_amex_direct_schema(session)
        assert receipt["ready"], receipt
    current, row, *_ = await historical_credit(pg)
    await select_credit(pg, current, row)
    await confirm_selected(pg, current)
    with pytest.raises(Exception, match="Settlement evidence prevents rollback"):
        await pg.conn.execute(rollback.read_text())
    # Owner script explicitly starts a transaction; refusal leaves it aborted.
    await pg.conn.execute("ROLLBACK")
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_expense_account_settlements")
        == 1
    )


@pytest.mark.asyncio
async def test_unrelated_ordinary_ledger_does_not_acquire_settlement_source_seal(
    direct_pg,
):
    from devnous.gastos.services.employee_debtor_accounting_service import (
        _create_poliza,
    )

    pg = direct_pg
    transaction = pg.conn.transaction()
    await transaction.start()
    await pg.conn.execute(
        (
            "SELECT "
            "pg_advisory_xact_lock(hashtextextended("
            "'amex-settlement-source-seal-v1',0))"
        )
    )
    try:
        async with pg.factory.begin() as session:
            await session.execute(text("SET LOCAL lock_timeout='250ms'"))
            result = await _create_poliza(
                session,
                origen="unrelated",
                numero_poliza="unrelated-seal-check",
                fecha=datetime(2026, 10, 2),
                beneficiario_nombre="Una operación ajena",
                concepto="Ajena",
                lines=[
                    {
                        "cuenta_codigo": pg.gasto.codigo,
                        "cuenta_contable_id": pg.gasto.id,
                        "concepto": "Ajena",
                        "debe": Decimal("10"),
                        "haber": 0,
                        "raw_row_json": {},
                    },
                    {
                        "cuenta_codigo": pg.liability.codigo,
                        "cuenta_contable_id": pg.liability.id,
                        "concepto": "Ajena",
                        "debe": 0,
                        "haber": Decimal("10"),
                        "raw_row_json": {},
                    },
                ],
            )
            assert result.id
    finally:
        await transaction.rollback()
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM accounting_polizas WHERE origen='unrelated'"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_uploaded_original_cfdi_settlement_preserves_original_evidence(direct_pg):
    pg = direct_pg
    current, row, expense, *_ = await historical_credit(pg, origin="user_upload")
    assert (
        await pg.conn.fetchval(
            "SELECT origen FROM cfdi_reports WHERE id=$1", expense.cfdi_report_id
        )
        == "user_upload"
    )
    await select_credit(pg, current, row)
    result, preview = await confirm_selected(pg, current, key="historical-upload")
    assert result.status == "recognized"
    assert (
        preview.progress["settlement_count"] == 1 and preview.progress["new_count"] == 0
    )
    assert result.cut.snapshot_json["partidas"][0]["cfdi"]["uuid"] == row["cfdi_uuid"]
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 0
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments") == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("missing", "expense_account_credit_not_proven"),
        ("malformed", "historical_cfdi_identity_conflict"),
        ("contradictory", "historical_cfdi_identity_conflict"),
    ],
)
async def test_uploaded_original_missing_or_inconsistent_xml_fails_closed(
    direct_pg, mutation, reason
):
    pg = direct_pg
    current, row, expense, *_ = await historical_credit(pg, origin="user_upload")
    if mutation == "missing":
        await pg.conn.execute(
            "UPDATE cfdi_reports SET xml_raw=NULL WHERE id=$1", expense.cfdi_report_id
        )
    elif mutation == "malformed":
        await pg.conn.execute(
            "UPDATE cfdi_reports SET xml_raw='<invalid>' WHERE id=$1",
            expense.cfdi_report_id,
        )
    else:
        await pg.conn.execute(
            "UPDATE cfdi_reports SET total=999 WHERE id=$1", expense.cfdi_report_id
        )
    before_events = await pg.conn.fetchval("SELECT count(*) FROM amex_direct_events")
    async with pg.factory.begin() as session:
        with pytest.raises(settlements.DirectConflict, match=reason):
            await settlements.select_settlement_source(
                session,
                actor_id=pg.actor.id,
                import_id=current.statement.id,
                item_id=current.items[0].id,
                **{k: row[k] for k in settlements.IDENTITY_KEYS},
                expected_item_version=current.items[0].source_version,
                expected_source_version=current.source_version,
                reason="Revisar evidencia de origen",
                confirmed=True,
            )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_expense_account_settlements")
        == 0
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_events")
        == before_events
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1


@pytest.mark.asyncio
async def test_uploaded_original_report_prior_only_completion_preserves_its_policy(
    direct_pg,
):
    pg = direct_pg
    current, row, expense, *_ = await historical_prior(pg, origin="user_upload")
    assert (
        await pg.conn.fetchval(
            "SELECT origen FROM cfdi_reports WHERE id=$1", expense.cfdi_report_id
        )
        == "user_upload"
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    await select_credit(pg, current, row)
    result, preview = await confirm_selected(pg, current, key="historical-upload-prior")
    assert result.status == "completed" and result.poliza is None and result.cut is None
    assert preview.progress["prior_count"] == 1 and preview.progress["new_count"] == 0
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_prior_coverages") == 1
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 1
    )
