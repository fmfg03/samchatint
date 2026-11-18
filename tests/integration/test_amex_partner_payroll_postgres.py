"""Run the approved Sports/1218 flow from the original AMEX owner on real SQL."""

import asyncio
import importlib.util
import io
import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from lxml import html
from openpyxl import load_workbook
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos import models
from devnous.gastos.routes import admin_amex_direct_routes as routes
from devnous.gastos.services import amex_partner_payroll_service as service
from devnous.gastos.services.amex_direct_common import (
    DirectAccessError,
    DirectConflict,
    DirectSchemaUnavailable,
)
from devnous.gastos.services.import_coi_service import parse_coi_workbook

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "partner_cut_fixture",
    Path(__file__).with_name(
        "test_amex_direct_account_treatment_accounting_postgres.py"
    ),
)
cut_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cut_fixture)
direct_pg = cut_fixture.direct_pg
MIGRATION = ROOT / "database/migrations/20261118_amex_partner_payroll.sql"
ROLLBACK = ROOT / "database/migrations/20261118_amex_partner_payroll_rollback.sql"


async def setup(pg, *, migrate=True):
    await cut_fixture.configure(pg)
    view = await cut_fixture.imported(pg, "2026-10-02,PARTNER,-939.00\n")
    item_id = view.items[0].id
    view = await cut_fixture.treatment(pg, view, item_id, "partner_receivable")
    view = await cut_fixture.declare(pg, view, item_id)
    cut = await cut_fixture.owner._confirm(
        pg, await cut_fixture.owner._cut_args(pg, view)
    )
    assert cut.status == "recognized"
    if migrate:
        await pg.conn.execute(MIGRATION.read_text())

    employer = models.PayrollEmployer(
        id=uuid4(),
        employer_key="p1218",
        legal_name="Synthetic 1218",
        rfc="AAA010101AA1",
        active=True,
    )
    registration = models.PayrollEmployerRegistration(
        id=uuid4(),
        payroll_employer_id=employer.id,
        registration_code="synthetic",
        branch_name="Synthetic registration",
    )
    employee = models.PayrollEmployee(
        id=uuid4(),
        empleado_id=pg.actor.id,
        employee_number="SYN-1",
        employer_registration_id=registration.id,
    )
    policy = {
        "sports_actor_ids": [str(pg.actor.id)],
        "p1218_actor_ids": [str(pg.actor.id)],
        "sports_rfc": view.context.receiver_rfc,
        "p1218_rfc": employer.rfc,
        "employer_id": str(employer.id),
        "partners": {str(pg.debtor.id): str(employee.id)},
        "sports_bank_number": "10000001",
        "p1218_bank_number": "20000002",
        "deduction_concept": "amex_partner",
    }
    async with pg.factory.begin() as session:
        session.add_all([employer, registration, employee])
        for index, key in enumerate(
            (
                "sports_receivable",
                "p1218_payable",
                "p1218_withholding",
                "sports_bank",
                "p1218_bank",
            ),
            1,
        ):
            account = models.CuentaContable(
                id=uuid4(),
                codigo=f"9990-{index:03}",
                nombre=key,
                tipo="balance",
                activo=True,
            )
            session.add(account)
            policy[key] = {"id": str(account.id), "code": account.codigo}
    pg.monkeypatch.setenv(service.POLICY_ENV, json.dumps(policy))
    return (
        dict(actor_id=pg.actor.id, import_id=view.statement.id, item_id=item_id),
        policy,
        employee,
    )


async def payroll(pg, policy, employee, amount, number, *, already_payable=False):
    day = date(2026, 10, 10 + number)
    period = models.PayrollPeriod(
        id=uuid4(),
        fiscal_year=2026,
        period_no=number,
        period_type="quincenal",
        start_date=day,
        end_date=day,
        payment_date=day,
        status="closed",
    )
    run = models.PayrollRun(id=uuid4(), period_id=period.id, status="posted")
    row = models.PayrollRunLine(
        id=uuid4(),
        run_id=run.id,
        payroll_employee_id=employee.id,
        deductions_json=[{"concept_key": "amex_partner", "amount": amount}],
    )
    header = models.AccountingPoliza(
        id=uuid4(),
        source_file=f"payroll_run:{run.id}",
        tipo_poliza="Diario",
        numero_poliza=f"NOM-{number}",
        concepto="Retención confirmada",
        origen="payroll",
    )
    source = policy["p1218_payable" if already_payable else "p1218_withholding"]
    line = models.AccountingPolizaLine(
        id=uuid4(),
        poliza_id=header.id,
        line_no=1,
        cuenta_contable_id=source["id"],
        cuenta_codigo=source["code"],
        debe=0,
        haber=float(amount),
    )
    async with pg.factory.begin() as session:
        session.add_all([period, run, row, header, line])
    return service.Request(
        "payroll", Decimal(amount), uuid4(), row.id, line.id, 0, f"REC-1218-{number}"
    )


async def transfer(pg, policy, amount):
    debit = models.BankMovement(
        id=uuid4(),
        source_file="test-1218.csv",
        source_row_number=1,
        cuenta_bancaria=policy["p1218_bank_number"],
        fecha=datetime(2026, 10, 28),
        signo="-",
        importe=float(amount),
        clave_rastreo="TRACE-1218",
    )
    credit = models.BankMovement(
        id=uuid4(),
        source_file="test-sports.csv",
        source_row_number=1,
        cuenta_bancaria=policy["sports_bank_number"],
        fecha=datetime(2026, 10, 28),
        signo="+",
        importe=float(amount),
        clave_rastreo="TRACE-1218",
    )
    async with pg.factory.begin() as session:
        session.add_all([debit, credit])
    return service.Request("transfer", Decimal(amount), uuid4(), debit.id, credit.id)


async def review(pg, args, request):
    async with pg.factory() as session:
        return await service.prepare(session, **args, request=request)


async def confirm(pg, args, request, preview):
    async with pg.factory.begin() as session:
        return await service.prepare(
            session, **args, request=request, confirm_hash=preview["hash"]
        )


async def balances(pg, args):
    async with pg.factory() as session:
        return (await service.state(session, **args))[1]


async def book(pg, args, event_id, company):
    async with pg.factory() as session:
        blob, name = await service.export(
            session, **args, event_id=event_id, company=company
        )
    assert company in name
    return load_workbook(io.BytesIO(blob), data_only=True)


@pytest.mark.asyncio
async def test_partial_payroll_then_bank_liquidation_with_separate_exports(direct_pg):
    pg = direct_pg
    args, policy, employee = await setup(pg)
    first = await payroll(pg, policy, employee, "300.25", 1)
    second = await payroll(pg, policy, employee, "638.75", 2, already_payable=True)
    bank = await transfer(pg, policy, "939.00")
    journal_count = await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas")
    freeze = await pg.conn.fetchval(
        "SELECT payload_hash FROM amex_direct_accounting_cuts"
    )
    with pytest.raises(DirectConflict, match="exceeds_balance"):
        await review(pg, args, bank)
    proposal = await review(pg, args, first)
    assert (await balances(pg, args)).debtor == Decimal("939.00")
    assert await confirm(pg, args, first, proposal) == proposal
    assert await confirm(pg, args, first, proposal) == proposal
    assert (await balances(pg, args)).debtor == Decimal("638.75")
    p1218 = await book(pg, args, first.operation_id, "p1218")
    assert p1218["Poliza COI"]["B4"].value == policy["p1218_withholding"]["code"]
    assert p1218["Poliza COI"]["B5"].value == policy["p1218_payable"]["code"]
    assert p1218["Poliza COI"]["F4"].value == 300.25
    assert p1218["Poliza COI"]["G5"].value == 300.25
    assert p1218["Resumen"]["B7"].value == 0
    assert not any("INICIO_CFDI" in row for row in p1218["Poliza COI"].values)
    sports = await book(pg, args, first.operation_id, "sports")
    assert sports["Poliza COI"]["B4"].value == policy["sports_receivable"]["code"]
    assert sports["Poliza COI"]["B5"].value == pg.debtor.codigo
    assert sports["Manifest"]["B3"].value == p1218["Manifest"]["B3"].value

    proposal = await review(pg, args, second)
    assert "p1218" not in proposal["books"]
    await confirm(pg, args, second, proposal)
    with pytest.raises(DirectConflict, match="export_unavailable"):
        await book(pg, args, second.operation_id, "p1218")
    assert (await balances(pg, args)).intercompany == Decimal("939.00")
    await confirm(pg, args, bank, await review(pg, args, bank))
    balance = await balances(pg, args)
    assert balance.debtor == balance.intercompany == 0
    assert balance.withheld == balance.transferred == Decimal("939.00")
    for company, bank_code, bank_row in (
        ("sports", policy["sports_bank"]["code"], "B4"),
        ("p1218", policy["p1218_bank"]["code"], "B5"),
    ):
        workbook = await book(pg, args, bank.operation_id, company)
        assert workbook["Poliza COI"][bank_row].value == bank_code
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_partner_payroll_events") == 3
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas")
        == journal_count
    )
    assert (
        await pg.conn.fetchval("SELECT payload_hash FROM amex_direct_accounting_cuts")
        == freeze
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments") == 0
    )


@pytest.mark.asyncio
async def test_concurrent_confirmations_cannot_spend_the_same_balance(direct_pg):
    pg = direct_pg
    args, policy, employee = await setup(pg)
    first = await payroll(pg, policy, employee, "600.00", 1)
    second = replace(first, operation_id=uuid4())
    previews = await asyncio.gather(review(pg, args, first), review(pg, args, second))
    results = await asyncio.gather(
        confirm(pg, args, first, previews[0]),
        confirm(pg, args, second, previews[1]),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    denied = next(result for result in results if isinstance(result, Exception))
    assert isinstance(denied, DirectConflict)
    assert "exceeds_balance" in str(denied)
    assert (await balances(pg, args)).debtor == Decimal("339.00")


@pytest.mark.asyncio
async def test_evidence_scope_and_migration_rollback(direct_pg):
    pg = direct_pg
    args, policy, employee = await setup(pg, migrate=False)
    with pytest.raises(DirectSchemaUnavailable, match="schema_missing"):
        await balances(pg, args)
    await pg.conn.execute(MIGRATION.read_text())
    await pg.conn.execute(ROLLBACK.read_text())
    await pg.conn.execute(MIGRATION.read_text())
    pay = await payroll(pg, policy, employee, "939.00", 1)
    with pytest.raises(DirectConflict, match="receipt_required"):
        await review(pg, args, replace(pay, receipt_reference=None))
    with pytest.raises(DirectConflict, match="source_mismatch"):
        await review(pg, args, replace(pay, deduction_index=8))
    await pg.conn.execute(
        "UPDATE payroll_employees SET employer_registration_id = NULL WHERE id = $1",
        employee.id,
    )
    with pytest.raises(DirectConflict, match="source_mismatch"):
        await review(pg, args, pay)
    await pg.conn.execute(
        "UPDATE payroll_employees SET employer_registration_id = $1 WHERE id = $2",
        employee.employer_registration_id,
        employee.id,
    )
    with pytest.raises(DirectConflict, match="withholding_unproven"):
        await review(pg, args, replace(pay, second_id=uuid4()))
    preview = await review(pg, args, pay)
    denied = {**policy, "p1218_actor_ids": [str(uuid4())]}
    pg.monkeypatch.setenv(service.POLICY_ENV, json.dumps(denied))
    with pytest.raises(DirectAccessError, match="scope_required"):
        await confirm(pg, args, pay, preview)
    pg.monkeypatch.setenv(service.POLICY_ENV, json.dumps(policy))
    await confirm(pg, args, pay, preview)
    mismatched_bank = await transfer(pg, policy, "939.00")
    await pg.conn.execute(
        "UPDATE bank_movements SET clave_rastreo = 'OTHER' WHERE id = $1",
        mismatched_bank.second_id,
    )
    with pytest.raises(DirectConflict, match="bank_source_mismatch"):
        await review(pg, args, mismatched_bank)
    with pytest.raises(asyncpg.CheckViolationError, match="immutable"):
        await pg.conn.execute("DELETE FROM amex_partner_payroll_events")
    with pytest.raises(asyncpg.RaiseError, match="evidence prevents rollback"):
        await pg.conn.execute(ROLLBACK.read_text())
    await pg.conn.execute("ROLLBACK")
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_partner_payroll_events") == 1
    )


@pytest.mark.asyncio
async def test_same_transfer_reimported_under_different_csv_cannot_pay_twice(direct_pg):
    pg = direct_pg
    args, policy, employee = await setup(pg)
    pay = await payroll(pg, policy, employee, "939.00", 1)
    await confirm(pg, args, pay, await review(pg, args, pay))
    transfer_original = await transfer(pg, policy, "300.00")
    transfer_copy = await transfer(pg, policy, "300.00")
    await pg.conn.execute(
        "UPDATE bank_movements SET source_file = 'download-copy.csv' "
        "WHERE id IN ($1, $2)",
        transfer_copy.first_id,
        transfer_copy.second_id,
    )
    await confirm(
        pg, args, transfer_original, await review(pg, args, transfer_original)
    )
    with pytest.raises(DirectConflict, match="exceeds_balance"):
        await review(pg, args, transfer_copy)
    assert (await balances(pg, args)).intercompany == Decimal("639.00")


@pytest.mark.asyncio
async def test_http_previews_confirms_and_downloads_both_companies(direct_pg):
    pg = direct_pg
    args, policy, employee = await setup(pg)
    deduction = await payroll(pg, policy, employee, "939.00", 1)
    proof = await transfer(pg, policy, "939.00")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="isolated synthetic test key")
    app.include_router(routes.router)

    async def db_session():
        async with pg.factory() as session:
            yield session

    app.dependency_overrides[routes.get_db_session] = db_session
    app.dependency_overrides[routes.get_current_empleado] = lambda: pg.actor
    path = routes.PARTNER_PAYROLL_PATH.format(
        import_id=args["import_id"], item_id=args["item_id"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        statement = await client.get(f"{routes.BASE}/{args['import_id']}/directo")
        assert statement.status_code == 200
        assert path in html.fromstring(statement.text).xpath(
            '//a[contains(text(), "Conciliar nómina 1218")]/@href'
        )
        page = await client.get(path)
        assert page.status_code == 200
        markup = html.fromstring(page.text)
        assert markup.xpath('//datalist[@id="payroll-first"]/option/@value') == [
            str(deduction.first_id)
        ]
        assert "939.00 MXN" in page.text
        form = {
            "kind": "payroll",
            "amount": "939.00",
            "operation_id": str(deduction.operation_id),
            "first_id": str(deduction.first_id),
            "second_id": str(deduction.second_id),
            "deduction_number": "1",
            "receipt_reference": deduction.receipt_reference,
        }
        assert (await client.post(path + "/preview", data=form)).status_code == 403
        form["_csrf_token"] = markup.xpath('//input[@name="_csrf_token"]/@value')[0]
        planned = await client.post(path + "/preview", data=form)
        assert planned.status_code == 200
        assert "Plataforma Sports" in planned.text and "Plataforma 1218" in planned.text
        assert (
            await pg.conn.fetchval("SELECT count(*) FROM amex_partner_payroll_events")
            == 0
        )
        form["preview_hash"] = html.fromstring(planned.text).xpath(
            '//input[@name="preview_hash"]/@value'
        )[0]
        assert (await client.post(path + "/confirmar", data=form)).status_code == 422
        form["confirmed"] = "true"
        assert (await client.post(path + "/confirmar", data=form)).status_code == 303
        page = await client.get(path)
        markup = html.fromstring(page.text)
        assert markup.xpath('//datalist[@id="transfer-first"]/option/@value') == [
            str(proof.first_id)
        ]
        assert len(markup.xpath('//a[contains(@href, ".xlsx")]')) == 2
        for company in ("sports", "p1218"):
            book_response = await client.get(
                f"{path}/{deduction.operation_id}/{company}.xlsx"
            )
            assert book_response.status_code == 200
            assert book_response.headers["content-type"] == service.XLSX_MIME
            parsed = parse_coi_workbook("native-export.xlsx", book_response.content)
            assert len(parsed) == 1 and len(parsed[0].lines) == 2
            assert parsed[0].beneficiario_nombre is None
        assert (
            await client.get(f"{path}/{deduction.operation_id}/goat.xlsx")
        ).status_code == 409
        assert (await client.get(f"{path}/{uuid4()}/sports.xlsx")).status_code == 409
        pg.monkeypatch.setenv(
            service.POLICY_ENV,
            json.dumps({**policy, "p1218_actor_ids": [str(uuid4())]}),
        )
        assert (await client.get(path)).status_code == 403
        assert (
            await client.get(f"{path}/{deduction.operation_id}/sports.xlsx")
        ).status_code == 403


@pytest.mark.asyncio
async def test_approved_coi_payroll_and_posted_bank_sources_are_reused(direct_pg):
    pg = direct_pg
    args, policy, employee = await setup(pg)
    pay = await payroll(pg, policy, employee, "939.00", 1, already_payable=True)
    header_id = await pg.conn.fetchval(
        "SELECT poliza_id FROM accounting_poliza_lines WHERE id = $1", pay.second_id
    )
    run_id = await pg.conn.fetchval(
        "SELECT run_id FROM payroll_run_lines WHERE id = $1", pay.first_id
    )
    await pg.conn.execute(
        "UPDATE accounting_polizas SET source_file = '1218-coi.xlsx', origen = 'coi' "
        "WHERE id = $1",
        header_id,
    )
    with pytest.raises(DirectConflict, match="withholding_unproven"):
        await review(pg, args, pay)
    policy["p1218_payroll_journals"] = {str(run_id): str(header_id)}
    pg.monkeypatch.setenv(service.POLICY_ENV, json.dumps(policy))
    preview = await review(pg, args, pay)
    assert set(preview["books"]) == {"sports"}
    await confirm(pg, args, pay, preview)

    bank = await transfer(pg, policy, "939.00")
    async with pg.factory.begin() as session:
        for movement_id, accounts in (
            (bank.first_id, ("p1218_payable", "p1218_bank")),
            (bank.second_id, ("sports_bank", "sports_receivable")),
        ):
            journal_id = uuid4()
            session.add(
                models.AccountingPoliza(
                    id=journal_id,
                    source_file="settlement.xlsx",
                    tipo_poliza="Diario",
                    numero_poliza="1",
                    concepto="Liquidación registrada",
                    origen="coi",
                )
            )
            for i, key in enumerate(accounts, 1):
                account = policy[key]
                session.add(
                    models.AccountingPolizaLine(
                        id=uuid4(),
                        poliza_id=journal_id,
                        line_no=i,
                        cuenta_contable_id=account["id"],
                        cuenta_codigo=account["code"],
                        debe=939.0 if i == 1 else 0,
                        haber=939.0 if i == 2 else 0,
                    )
                )
            movement = await session.get(models.BankMovement, movement_id)
            movement.related_poliza_id = journal_id
    preview = await review(pg, args, bank)
    assert preview["books"] == {}
    assert all(preview["bank_polizas"].values())
    await confirm(pg, args, bank, preview)
    assert (await balances(pg, args)).intercompany == 0
