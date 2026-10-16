"""Accountant-to-COI browser acceptance over real isolated PostgreSQL owners.

Only local identity/session injection and the external SAT transport are test
boundaries. Financial, fiscal, budget, posting and export services stay real.
"""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
import json
import socket
import zipfile
from collections import Counter
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
import uvicorn
from fastapi import Depends, FastAPI, Request
from openpyxl import load_workbook
from playwright.async_api import async_playwright
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.models import Empleado
from devnous.gastos.routes import admin_amex_direct_routes as routes
from devnous.gastos.routes import dependencies
from devnous.gastos.services import amex_direct_status_service as status_owner
from devnous.gastos.services.cfdi_ingestion_service import ingest_cfdi_xml

_fixture_path = Path(__file__).with_name("test_amex_sat_budget_direct_postgres.py")
_spec = importlib.util.spec_from_file_location("amex_qa_browser_fixture", _fixture_path)
owner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(owner)
direct_pg = owner.direct_pg


@asynccontextmanager
async def real_owner_browser_server(pg):
    """Serve the actual router on localhost with two isolated business actors."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="isolated-browser-acceptance")

    async def session_dependency():
        async with pg.factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    async def actor_dependency(
        request: Request, session=Depends(dependencies.get_db_session)
    ):
        actor_id = (
            pg.cp_actor.id
            if request.headers.get("x-qa-actor") == "budget"
            else pg.actor.id
        )
        return await session.get(Empleado, actor_id)

    app.dependency_overrides[dependencies.get_db_session] = session_dependency
    app.dependency_overrides[dependencies.get_current_empleado] = actor_dependency
    pg.monkeypatch.setattr(dependencies, "_db_session_maker", pg.factory)
    app.include_router(routes.router)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            timeout_graceful_shutdown=3,
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
                raise RuntimeError("The local acceptance server stopped")
            await asyncio.sleep(0.01)
        assert server.started, "The local acceptance server did not start"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except TimeoutError:
            requests = list(server.server_state.tasks)
            for request_task in requests:
                request_task.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            server.force_exit = True
            await asyncio.wait_for(task, timeout=5)


async def seed_browser_cfdis(pg):
    """Seed real canonical XML ingestion; neither invoice is already matched."""
    uuids = [
        "10000000-0000-4000-8000-000000000001",
        "10000000-0000-4000-8000-000000000002",
    ]
    first = owner._synthetic_sat_xml(uuids[0])
    second = (
        owner._synthetic_sat_xml(uuids[1])
        .replace("100.00", "200.00")
        .replace("116.00", "232.00")
        .replace("16.00", "32.00")
    )
    async with pg.factory.begin() as session:
        for xml in (first, second):
            await ingest_cfdi_xml(session, xml, source="sat")
    await pg.conn.execute(
        "UPDATE tournaments SET name=$1 WHERE id=$2",
        "Gastos Administrativos - Dirección General",
        pg.tournament.id,
    )
    await pg.conn.execute(
        "UPDATE budget_concepts SET tournament_name=$1 WHERE id=$2",
        "Gastos Administrativos - Dirección General",
        pg.concept.id,
    )
    observed = owner.ObservedSAT()
    pg.monkeypatch.setattr(status_owner, "SATCFDIStatusService", lambda: observed)
    return uuids, observed


def assert_exported_policy(
    content: bytes, fmt: str, uuids: list[str], expected_lines: list[dict]
) -> None:
    """Check actual downloaded content, rather than accepting HTTP 200 alone."""
    if fmt == "zip":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            assert archive.namelist()
            files = [n for n in archive.namelist() if n.endswith((".xlsx", ".csv"))]
            assert files
            for name in files:
                assert_exported_policy(
                    archive.read(name), name.rsplit(".", 1)[1], uuids, expected_lines
                )
        return
    if fmt == "xlsx":
        workbook = load_workbook(io.BytesIO(content), data_only=True)
        rows = list(workbook["Poliza COI"].values)
    else:
        assert fmt == "csv"
        rows = list(csv.reader(io.StringIO(content.decode("latin-1"))))
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
        identity.lower() for identity in uuids
    )
    assert all(
        tuple(str(value).strip() for value in row[5:7])
        == ("AAA010101AAA", "BBB010101BBB")
        for row in fiscal
    )
    if fmt == "xlsx":
        assert all(
            tuple(row[5:7]) == ("AAA010101AAA", "BBB010101BBB") for row in fiscal
        )
    assert sum(row[0] == "Eg" for row in rows) == 1
    assert sum(row[1] == "FIN_PARTIDAS" for row in rows) == 1
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
        for line in expected_lines
    )
    assert (
        expected and actual == expected
    ), "Downloaded movements differ from frozen cut"
    assert sum(debit * count for (_, debit, _), count in actual.items()) == sum(
        credit * count for (_, _, credit), count in actual.items()
    )


@pytest.mark.asyncio
async def test_accountant_discovers_cfdi_then_cp_and_downloads_complete_policy(
    direct_pg,
):
    """Two real-owner rows exercise the precise final-export failure from QA."""
    pg = direct_pg
    uuids, observed = await seed_browser_cfdis(pg)
    original = (
        b"Date,Description,Amount\n"
        b"2026-10-02,COMERCIO SIN ALIAS UNO,116.00\n"
        b"2026-10-03,COMERCIO SIN ALIAS DOS,232.00\n"
    )
    assert await owner._count(pg, owner.models.AccountingPoliza) == 0
    async with real_owner_browser_server(pg) as base, async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        finance_context = await browser.new_context(
            extra_http_headers={"x-qa-actor": "finance"}
        )
        budget_context = await browser.new_context(
            extra_http_headers={"x-qa-actor": "budget"}
        )
        page = await finance_context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.goto(base + routes.BASE + "/directo/nuevo")
            await page.locator('[name="card_account_id"]').select_option(
                str(pg.card.id)
            )
            await page.locator('[name="employee_id"]').select_option(str(pg.owner.id))
            await page.locator('[name="period_start"]').fill("2026-10-01")
            await page.locator('[name="period_end"]').fill("2026-10-31")
            await page.locator('[name="company_code"]').select_option("TEST")
            await page.locator('[name="receiver_rfc"]').select_option("BBB010101BBB")
            await page.locator('[name="currency"]').select_option("MXN")
            await page.locator('[name="profile"]').select_option("debit_positive")
            await page.locator('[name="file"]').set_input_files(
                {"name": "synthetic-qa.csv", "mimeType": "text/csv", "buffer": original}
            )
            await page.get_by_role(
                "button", name="Previsualizar archivo y signo"
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
            assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 2
            assert await pg.conn.fetchval("SELECT count(*) FROM documentos") == 0
            assert not observed.requests
            # The remainder deliberately uses the actual business picker/forms.
            await complete_picker_budget_and_export(
                pg, page, budget_context, statement_url, statement_id, uuids, observed
            )
            assert not errors
        finally:
            await browser.close()


async def complete_picker_budget_and_export(
    pg, page, budget_context, statement_url, statement_id, uuids, observed
):
    """Choose fiscal UUIDs in the UI, then complete each separate owner step."""
    original_items = await pg.conn.fetch(
        "SELECT d.id FROM amex_direct_items d JOIN amex_statement_import_items r "
        "ON r.id=d.import_item_id ORDER BY r.row_number"
    )
    for item, fiscal_uuid in zip(original_items, uuids, strict=True):
        await page.goto(statement_url)
        row = page.locator(f'tr[data-item-id="{item["id"]}"]')
        await row.get_by_role("link", name="Buscar CFDI", exact=True).click()
        await page.locator('input[name="q"]').fill(fiscal_uuid)
        await page.get_by_role("button", name="Buscar facturas", exact=True).click()
        candidate = page.locator(f'section[data-cfdi-uuid="{fiscal_uuid}"]')
        observe_form = candidate.locator('form[action$="/cfdi/vigencia"]')
        await observe_form.locator('input[name="confirmed"]').check()
        await observe_form.get_by_role(
            "button", name="Consultar vigencia de esta factura", exact=True
        ).click()
        await page.wait_for_load_state("networkidle")
        candidate = page.locator(f'section[data-cfdi-uuid="{fiscal_uuid}"]')
        link_form = candidate.locator('form[action$="/vincular"]')
        await link_form.locator('input[name="reason"]').fill(
            "Correspondencia fiscal explícita de la prueba sintética"
        )
        await link_form.locator('input[name="confirmed"]').check()
        await link_form.get_by_role(
            "button", name="Vincular factura seleccionada", exact=True
        ).click()
        await page.wait_for_url("**/directo?msg=**")
    assert len(observed.requests) == 2
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_merchant_identity_aliases")
        == 0
    )
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_items WHERE match_status='matched'"
        )
        == 2
    )

    classification = page.locator('form[action$="/clasificar"]')
    for checkbox in await classification.locator('input[name="item_ids"]').all():
        await checkbox.check()
    assert await classification.locator(
        'select[name="budget_concept_id"]'
    ).is_disabled()
    await classification.locator("#direct-project").select_option(
        f"t:{pg.tournament.id}"
    )
    assert await classification.locator("#direct-concept option").count() == 2
    await classification.locator("#direct-concept").select_option(str(pg.concept.id))
    await classification.locator('select[name="edition_year"]').select_option("2026")
    assert await classification.locator('input[name="confirmed"]').count() == 0
    assert await classification.evaluate("form => form.checkValidity()")
    await classification.get_by_role(
        "button", name="Guardar clasificación de cargos seleccionados", exact=True
    ).click()
    await page.wait_for_url("**/directo?msg=**")
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_items WHERE budget_status='classified'"
        )
        == 2
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments") == 0
    )

    for checkbox in await page.locator(
        'form[action$="/clasificar"] input[name="item_ids"]'
    ).all():
        await checkbox.check()
    send_form = page.locator('form[action$="/enviar-presupuesto"]')
    assert await send_form.locator('input[name="item_ids"]').count() == 2
    await send_form.locator('input[name="confirmed"]').check()
    await send_form.get_by_role(
        "button", name="Enviar seleccionados a Control Presupuestal", exact=True
    ).click()
    await page.wait_for_url("**/directo?msg=**")

    budget_page = await budget_context.new_page()
    base = statement_url.split(routes.BASE, 1)[0]
    await budget_page.goto(base + routes.QUEUE)
    for _ in range(2):
        assign_form = budget_page.locator('form[action$="/asignar"]').first
        await assign_form.locator('select[name="budget_line_id"]').select_option(
            str(pg.line_id)
        )
        assert await assign_form.locator('select[name="tournament_id"]').count() == 0
        await assign_form.get_by_role(
            "button", name="Asignar partida existente", exact=True
        ).click()
        await budget_page.wait_for_url("**/amex-directo?msg=**")
    assert await budget_page.locator('form[action$="/asignar"]').count() == 0
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM budget_movement_assignments") == 2
    )

    await page.goto(statement_url)
    from devnous.gastos.services import amex_direct_accounting_service as accounting

    async with pg.factory() as session:
        proposal = await accounting.preview_direct_accounting(
            session, actor_id=pg.actor.id, import_id=statement_id
        )
    assert proposal.status == "ready", proposal.reason
    expected_preview = Counter(
        (
            str(line["cuenta_codigo"]),
            Decimal(str(line["debe"])),
            Decimal(str(line["haber"])),
        )
        for line in proposal.progress["lines"]
    )
    assert await page.get_by_role(
        "heading", name="Propuesta contable Debe / Haber", exact=True
    ).is_visible()
    rendered_preview = Counter()
    for row in await page.locator("#direct-journal tbody tr").all():
        cells = await row.locator("td").all_text_contents()
        rendered_preview[(cells[0], Decimal(cells[2]), Decimal(cells[3]))] += 1
    assert rendered_preview == expected_preview
    total_debit = sum(Decimal(str(line["debe"])) for line in proposal.progress["lines"])
    total_credit = sum(
        Decimal(str(line["haber"])) for line in proposal.progress["lines"]
    )
    assert total_debit == total_credit == 348
    totals_text = await page.locator("#direct-journal").locator("xpath=..").inner_text()
    assert f"Total Debe: {total_debit}" in totals_text
    assert f"Total Haber: {total_credit}" in totals_text
    confirm_form = page.locator('form[action$="/confirmar"]')
    assert await confirm_form.count() == 1
    await confirm_form.locator('input[name="accounting_date"]').fill("2026-10-31")
    await confirm_form.locator('input[name="reason"]').fill("Cierre sintético completo")
    await confirm_form.locator('input[name="confirmed"]').check()
    await confirm_form.get_by_role(
        "button", name="Generar póliza directa completa", exact=True
    ).click()
    await page.wait_for_url("**/directo?msg=**")
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    assert (
        await pg.conn.fetchval("SELECT sum(debe) FROM accounting_poliza_lines") == 348
    )
    assert (
        await pg.conn.fetchval("SELECT sum(haber) FROM accounting_poliza_lines") == 348
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_recognition_consumptions")
        == 2
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM documentos") == 0
    await budget_page.goto(base + routes.QUEUE)
    assert await budget_page.locator('form[action$="/asignar"]').count() == 0
    assert await budget_page.locator('form[action$="/rechazar"]').count() == 0

    snapshot_raw = await pg.conn.fetchval(
        "SELECT snapshot_json FROM amex_direct_accounting_cuts WHERE import_id=$1",
        statement_id,
    )
    snapshot = (
        json.loads(snapshot_raw) if isinstance(snapshot_raw, str) else snapshot_raw
    )
    for fmt in ("xlsx", "csv", "zip"):
        async with page.expect_download() as downloading:
            await page.get_by_role("link", name=fmt.upper(), exact=True).click()
        download = await downloading.value
        content = Path(await download.path()).read_bytes()
        assert_exported_policy(content, fmt, uuids, snapshot["lines"])
    # Repeated downloads remain pure and preserve the single posted policy.
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
    receipt = {
        "case": "two_charge_browser_real_owners",
        "synthetic_fiscal_data": True,
        "internal_business_services_mocked": False,
        "sat_transport_substituted": True,
        "roles": ["Finance", "Control Presupuestal"],
        "statement_id": str(statement_id),
        "charges": 2,
        "policies": 1,
        "balanced_total": "348.00",
        "downloaded_formats": ["xlsx", "csv", "zip"],
        "rfc_trimmed": True,
        "recognized_items_not_actionable_in_cp": True,
    }
    artifact = Path("/tmp/amex-remediation-browser-real-owner-result.json")
    artifact.write_text(json.dumps(receipt, indent=2) + "\n")
