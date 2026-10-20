"""Browser acceptance with canonical fiscal, budget and ledger owners.

Only authenticated actor selection and external SAT transport are substituted.
Synthetic invoices prove software behavior, never real fiscal correspondence.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import re
import zipfile
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from openpyxl import load_workbook
from playwright.async_api import async_playwright
from sqlalchemy import event, text

from devnous.gastos import models
from devnous.gastos.services import amex_direct_accounting_service as accounting
from devnous.gastos.services import amex_direct_status_service as status_owner
from devnous.gastos.services.cfdi_ingestion_service import ingest_cfdi_xml


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROOT = Path(__file__).resolve().parents[2]
browser_owner = _module(
    "suggestions_browser_owner",
    ROOT / "tests/acceptance/test_amex_qa_browser_accounting_journey.py",
)
settlement_owner = _module(
    "suggestions_settlement_owner",
    ROOT / "tests/integration/test_amex_settlement_postgres.py",
)
owner = browser_owner.owner
routes = browser_owner.routes
direct_pg = owner.direct_pg
SUNTORY_UUID = "10000000-0000-4000-8000-000000003814"


def _assert_export(content, fmt, lines, expected_uuid):
    if fmt == "zip":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [n for n in archive.namelist() if n.endswith((".csv", ".xlsx"))]
            assert names
            for name in names:
                _assert_export(
                    archive.read(name), name.rsplit(".", 1)[1], lines, expected_uuid
                )
        return
    rows = (
        list(load_workbook(io.BytesIO(content), data_only=True)["Poliza COI"].values)
        if fmt == "xlsx"
        else list(csv.reader(io.StringIO(content.decode("latin-1"))))
    )
    actual = Counter(
        (str(row[1]), Decimal(str(row[5] or 0)), Decimal(str(row[6] or 0)))
        for row in rows
        if len(row) >= 7 and not row[0] and str(row[2]) == "0" and str(row[4]) == "1"
    )
    expected = Counter(
        (
            str(line["cuenta_codigo"]),
            Decimal(str(line["debe"])),
            Decimal(str(line["haber"])),
        )
        for line in lines
    )
    assert actual == expected and expected
    assert sum(row[0] == "Eg" for row in rows) == 1
    assert sum(row[1] == "FIN_PARTIDAS" for row in rows) == 1
    fiscal = []
    for row in rows:
        if len(row) < 9 or not row[8]:
            continue
        try:
            UUID(str(row[8]))
        except ValueError:
            continue
        fiscal.append(row)
    assert Counter(str(row[8]).lower() for row in fiscal) == Counter(
        [expected_uuid.lower()]
    )
    for row in fiscal:
        assert str(row[5]).strip() == "AAA010101AAA"
        assert str(row[6]).strip() in {"BBB010101BBB", "PSP1705058S4"}
        if fmt == "xlsx":
            assert row[5] == str(row[5]).strip()
            assert row[6] == str(row[6]).strip()


async def _read_only_preview(pg, statement_id):
    """Prove the actual complete proposal works in a READ ONLY transaction."""
    statements = []
    engine = pg.factory.kw["bind"].sync_engine

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    async with pg.factory() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        event.listen(engine, "before_cursor_execute", observe)
        try:
            preview = await accounting.preview_direct_accounting(
                session, actor_id=pg.actor.id, import_id=statement_id
            )
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        assert preview.status == "ready", (preview.reason, preview.progress)
        assert statements
        forbidden = re.compile(
            r"\b(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|LOCK)\b"
            r"|pg_advisory|FOR\s+(?:UPDATE|SHARE)",
            re.IGNORECASE,
        )
        assert not [sql for sql in statements if forbidden.search(sql)]
        return preview


async def _confirm_and_export(
    pg, page, statement_id, expected_total, expected_uuid, accounting_date="2026-10-31"
):
    """Use the physical confirmation form and compare all export movements."""
    preview = await _read_only_preview(pg, statement_id)
    assert (
        sum(Decimal(str(x["debe"])) for x in preview.progress["lines"])
        == expected_total
    )
    assert (
        sum(Decimal(str(x["haber"])) for x in preview.progress["lines"])
        == expected_total
    )
    assert await page.locator("#direct-journal tbody tr").count() == len(
        preview.progress["lines"]
    )
    form = page.locator('form[action$="/confirmar"]')
    confirmation = {
        "idempotency_key": await form.locator('[name="idempotency_key"]').input_value(),
        "reason": "Prueba aislada de aceptación contable",
    }
    await form.locator('[name="accounting_date"]').fill(accounting_date)
    await form.locator('[name="reason"]').fill("Prueba aislada de aceptación contable")
    await form.locator('[name="confirmed"]').check()
    await form.get_by_role(
        "button", name="Generar póliza directa completa", exact=True
    ).click()
    await page.wait_for_url("**/directo?msg=**")
    raw = await pg.conn.fetchval(
        "SELECT snapshot_json FROM amex_direct_accounting_cuts WHERE import_id=$1",
        statement_id,
    )
    snapshot = json.loads(raw) if isinstance(raw, str) else raw
    assert snapshot["lines"]
    for fmt in ("xlsx", "csv", "zip"):
        async with page.expect_download() as pending:
            await page.get_by_role("link", name=fmt.upper(), exact=True).click()
        download = await pending.value
        content = Path(await download.path()).read_bytes()
        # Settlement lines legitimately need no repeated fiscal attachment;
        # compare every movement with the immutable exported snapshot.
        _assert_export(content, fmt, snapshot["lines"], expected_uuid)
    return preview, snapshot, confirmation


@pytest.mark.asyncio
async def test_suntory_cross_month_suggestion_ppd_without_rep_to_real_policy(direct_pg):
    pg = direct_pg
    contexts = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
    contexts[0]["receiver_rfc"] = "PSP1705058S4"
    pg.monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(contexts))
    await pg.conn.execute(
        "UPDATE budget_lines SET budget_amount=10000 WHERE id=$1", pg.line_id
    )
    async with pg.factory.begin() as session:
        session.add(
            models.AccountingClosePeriod(
                fiscal_year=2026, fiscal_month=9, status="open"
            )
        )
    xml = (
        owner._synthetic_sat_xml(SUNTORY_UUID)
        .replace("BBB010101BBB", "PSP1705058S4")
        .replace('Nombre="ACME"', 'Nombre="RESTAURANTE SUNTORY"')
        .replace("2026-10-03", "2026-10-06")
        .replace(
            'TipoDeComprobante="I"',
            'TipoDeComprobante="I" MetodoPago="PPD" FormaPago="99"',
        )
        .replace("100.00", "3287.93")
        .replace("116.00", "3814.00")
        .replace("16.00", "526.07")
    )
    async with pg.factory.begin() as session:
        await ingest_cfdi_xml(session, xml, source="sat")
    observed = owner.ObservedSAT()
    pg.monkeypatch.setattr(status_owner, "SATCFDIStatusService", lambda: observed)
    async with browser_owner.real_owner_browser_server(
        pg
    ) as base, async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            finance = await browser.new_context(
                extra_http_headers={"x-qa-actor": "finance"}
            )
            budget = await browser.new_context(
                extra_http_headers={"x-qa-actor": "budget"}
            )
            page = await finance.new_page()
            page.set_default_timeout(10000)
            await page.goto(base + routes.BASE + "/directo/nuevo")
            for name, value in {
                "card_account_id": str(pg.card.id),
                "employee_id": str(pg.owner.id),
                "company_code": "TEST",
                "receiver_rfc": "PSP1705058S4",
                "currency": "MXN",
                "profile": "debit_positive",
            }.items():
                await page.locator(f'[name="{name}"]').select_option(value)
            await page.locator('[name="period_start"]').fill("2026-09-01")
            await page.locator('[name="period_end"]').fill("2026-09-30")
            await page.locator('[name="file"]').set_input_files(
                {
                    "name": "suntory.csv",
                    "mimeType": "text/csv",
                    "buffer": (
                        b"Date,Description,Amount\n"
                        b"2026-09-29,RESTAURANTE SUNTORY,3814.00\n"
                    ),
                }
            )
            await page.get_by_role(
                "button", name="Previsualizar archivo y signo", exact=True
            ).click()
            await page.wait_for_function(
                "!document.getElementById('amex-direct-import').disabled"
            )
            await page.locator('[name="confirmed"]').check()
            await page.get_by_role(
                "button", name="Cargar estado directo", exact=True
            ).click()
            await page.wait_for_url("**/directo?msg=**")
            statement_id = UUID(page.url.split("/estados/", 1)[1].split("/", 1)[0])
            statement_url = base + routes.BASE + f"/{statement_id}/directo"
            suggestions = page.get_by_role("region", name="Facturas sugeridas")
            # The initial list must discover the candidate without q/UUID input.
            assert SUNTORY_UUID in await suggestions.inner_text()
            assert "2026-10-06" in await suggestions.inner_text()
            assert "2026-09-29" in await suggestions.inner_text()
            assert "PPD" in await suggestions.inner_text()
            assert "no bloquea" in await suggestions.inner_text()
            assert not observed.requests
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM amex_direct_items "
                    "WHERE match_status='matched'"
                )
                == 0
            )
            await suggestions.get_by_role(
                "link", name="Revisar propuestas", exact=True
            ).first.click()
            candidate = page.locator(f'section[data-cfdi-uuid="{SUNTORY_UUID}"]')
            assert await candidate.is_visible()
            observe = candidate.locator('form[action$="/cfdi/vigencia"]')
            await observe.locator('[name="confirmed"]').check()
            await observe.get_by_role(
                "button", name="Consultar vigencia de esta factura", exact=True
            ).click()
            await page.wait_for_load_state("networkidle")
            link = page.locator(f'section[data-cfdi-uuid="{SUNTORY_UUID}"]').locator(
                'form[action$="/vincular"]'
            )
            await link.locator('[name="reason"]').fill(
                "Consumo Suntory confirmado, emisión siete días posterior"
            )
            await link.locator('[name="confirmed"]').check()
            await link.get_by_role(
                "button", name="Vincular factura seleccionada", exact=True
            ).click()
            await page.wait_for_url("**/directo?msg=**")
            classify = page.locator('form[action$="/clasificar"]')
            await classify.locator('[name="item_ids"]').check()
            await classify.locator("#direct-project").select_option(
                f"t:{pg.tournament.id}"
            )
            await classify.locator("#direct-concept").select_option(str(pg.concept.id))
            await classify.locator('[name="edition_year"]').select_option("2026")
            await classify.get_by_role(
                "button",
                name="Guardar clasificación de cargos seleccionados",
                exact=True,
            ).click()
            await page.wait_for_url("**/directo?msg=**")
            await page.locator('form[action$="/clasificar"] [name="item_ids"]').check()
            send = page.locator('form[action$="/enviar-presupuesto"]')
            await send.locator('[name="confirmed"]').check()
            await send.get_by_role(
                "button", name="Enviar seleccionados a Control Presupuestal", exact=True
            ).click()
            await page.wait_for_url("**/directo?msg=**")
            cp = await budget.new_page()
            await cp.goto(base + routes.QUEUE)
            assign = cp.locator('form[action$="/asignar"]')
            assert await assign.locator('[name="tournament_id"]').count() == 0
            await assign.locator('[name="budget_line_id"]').select_option(
                str(pg.line_id)
            )
            await assign.get_by_role(
                "button", name="Asignar partida existente", exact=True
            ).click()
            await cp.wait_for_url("**/amex-directo?msg=**")
            await page.goto(statement_url)
            _, snapshot, _ = await _confirm_and_export(
                pg,
                page,
                statement_id,
                Decimal("3814"),
                SUNTORY_UUID,
                accounting_date="2026-09-30",
            )
            assert (
                snapshot["fiscal_evidence_version"] == "amex_direct_fiscal_evidence_v1"
            )
            frozen_evidence = list(snapshot["fiscal_evidence"].values())
            assert len(frozen_evidence) == 1
            assert frozen_evidence[0]["payment_method"] == "PPD"
            assert frozen_evidence[0]["complement_status"] == "not_local"
            assert frozen_evidence[0]["policy_applies"] is True
            assert frozen_evidence[0]["policy"]["rep_required"] is False
            assert frozen_evidence[0]["policy"]["receiver_rfc"] == "PSP1705058S4"
            actual_iva = await pg.conn.fetchval(
                "SELECT sum(debe) FROM accounting_poliza_lines "
                "WHERE cuenta_contable_id=$1",
                pg.iva.id,
            )
            assert Decimal(str(actual_iva)) == Decimal("526.07")
            assert (
                await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
            )
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM budget_movement_assignments"
                )
                == 1
            )
            assert len(observed.requests) == 1
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_original_expense_credit_selected_in_browser_settles_without_new_iva(
    direct_pg,
):
    pg = direct_pg
    observed = owner.ObservedSAT()
    pg.monkeypatch.setattr(status_owner, "SATCFDIStatusService", lambda: observed)
    current, row, expense, cuenta, document = await settlement_owner.historical_credit(
        pg, origin="user_upload"
    )
    assert (
        await pg.conn.fetchval(
            "SELECT origen FROM cfdi_reports WHERE id=$1", expense.cfdi_report_id
        )
        == "user_upload"
    )
    original_iva = await pg.conn.fetchval(
        "SELECT coalesce(sum(debe),0) FROM accounting_poliza_lines "
        "WHERE cuenta_contable_id=$1",
        pg.iva.id,
    )
    original_budget = await pg.conn.fetchval(
        "SELECT count(*) FROM budget_movement_assignments"
    )
    async with browser_owner.real_owner_browser_server(
        pg
    ) as base, async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            page.set_default_timeout(10000)
            url = base + routes.BASE + f"/{current.statement.id}/directo"
            await page.goto(url)
            await page.get_by_role(
                "link", name="Cuenta de gastos reconocida", exact=True
            ).click()
            assert row["origin_account_code"] in await page.locator("body").inner_text()
            form = page.locator('form[action$="/cuenta-gastos"]')
            await form.locator('[name="reason"]').fill(
                "Cancelar crédito original identificado por su póliza y partida"
            )
            await form.locator('[name="confirmed"]').check()
            await form.get_by_role(
                "button", name="Seleccionar cuenta de gastos de origen", exact=True
            ).click()
            await page.wait_for_url("**/directo?msg=**")
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM amex_expense_account_settlements"
                )
                == 0
            )
            preview, snapshot, confirmation = await _confirm_and_export(
                pg, page, current.statement.id, Decimal("116"), str(row["cfdi_uuid"])
            )
            assert len(snapshot["lines"]) == 2
            cut = await pg.conn.fetchrow(
                "SELECT * FROM amex_direct_accounting_cuts WHERE import_id=$1",
                current.statement.id,
            )
            lines = await pg.conn.fetch(
                "SELECT cuenta_contable_id,debe,haber FROM accounting_poliza_lines "
                "WHERE poliza_id=$1 ORDER BY line_no",
                cut["accounting_poliza_id"],
            )
            assert len(lines) == 2
            assert lines[0]["cuenta_contable_id"] == row["origin_account_id"]
            assert lines[0]["debe"] == Decimal("116")
            assert lines[1]["cuenta_contable_id"] == pg.liability.id
            assert lines[1]["haber"] == Decimal("116")
            assert (
                await pg.conn.fetchval(
                    "SELECT coalesce(sum(debe),0) FROM accounting_poliza_lines "
                    "WHERE cuenta_contable_id=$1",
                    pg.iva.id,
                )
                == original_iva
            )
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM budget_movement_assignments"
                )
                == original_budget
            )
            from devnous.gastos.services.employee_debtor_accounting_service import (
                build_cuenta_debtor_auxiliary,
            )

            async with pg.factory() as session:
                auxiliary = await build_cuenta_debtor_auxiliary(
                    session, cuenta_id=cuenta.id
                )
            assert auxiliary["saldo"] == 0
            assert auxiliary["comprobado"] == 116
            assert not observed.requests
            await page.goto(url)
            assert await page.locator('a[href*="/cuenta-gastos"]').count() == 0
            assert await page.locator('form[action$="/confirmar"]').count() == 0
            async with pg.factory.begin() as session:
                replay = await accounting.confirm_direct_accounting(
                    session,
                    actor_id=pg.actor.id,
                    import_id=current.statement.id,
                    expected_source_key=preview.source_key,
                    expected_source_version=preview.source_version,
                    accounting_date=date(2026, 10, 31),
                    confirmed=True,
                    **confirmation,
                )
            assert replay.cut.id == cut["id"]
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM amex_expense_account_settlements"
                )
                == 1
            )
        finally:
            await browser.close()
