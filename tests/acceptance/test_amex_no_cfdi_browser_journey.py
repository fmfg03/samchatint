"""Real browser and PostgreSQL journey for explicit no-CFDI bank charges.

The local server injects only identities. All financial owners remain real;
this test never configures SAT transport or queries a production target.
"""

import importlib.util
import io
import json
import zipfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook
from playwright.async_api import async_playwright
from sqlalchemy import text

from devnous.gastos.routes import admin_amex_direct_routes as routes
from devnous.gastos.services import amex_direct_accounting_service as accounting
from samchat.budgets.service import build_budget_actuals_snapshot

_path = Path(__file__).with_name("test_amex_qa_browser_accounting_journey.py")
_spec = importlib.util.spec_from_file_location("no_cfdi_browser_fixture", _path)
journey = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(journey)
direct_pg = journey.direct_pg


async def _submit_selection(page, action, label, reason):
    await page.locator("#direct-select-all").check()
    form = page.locator(f'form[action$="/{action}"]')
    assert await form.locator('input[name="item_ids"]').count() == 2
    await form.locator('input[name="reason"]').fill(reason)
    await form.locator('input[name="confirmed"]').check()
    await form.get_by_role("button", name=label, exact=True).click()
    await page.wait_for_url("**/directo?msg=**")


def _assert_decision_export(content, fmt, decision_ids):
    if fmt == "zip":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for name in archive.namelist():
                if name.endswith((".csv", ".xlsx")):
                    _assert_decision_export(
                        archive.read(name), name.rsplit(".", 1)[1], decision_ids
                    )
        return
    if fmt == "xlsx":
        book = load_workbook(io.BytesIO(content), data_only=False)
        cells = [cell for sheet in book for row in sheet for cell in row]
        assert not any(cell.data_type == "f" for cell in cells)
        evidence = " ".join(str(cell.value or "") for cell in cells)
    else:
        evidence = content.decode("cp1252")
    assert "Sin CFDI" in evidence
    assert all(identity in evidence for identity in decision_ids)


@pytest.mark.asyncio
async def test_bulk_no_cfdi_revoke_redeclare_classify_budget_cut_and_export(direct_pg):
    pg = direct_pg
    imported = await journey.owner._direct_import(
        pg,
        b"Date,Description,Amount\n2026-10-02,NO FACTURA UNO,-116.00\n"
        b"2026-10-03,NO FACTURA DOS,-232.00\n",
        key="no-cfdi-browser-intake",
    )
    assert imported.status == "imported", imported.reason
    statement_id = imported.statement.id
    expense_ids = {
        str(row["id"]) for row in await pg.conn.fetch("SELECT id FROM expense_reports")
    }

    async def budget_movements():
        async with pg.factory() as session:
            snapshot = await build_budget_actuals_snapshot(
                session,
                edition_year=2026,
                version_id=str(pg.version_id),
                tournament_id=str(pg.tournament.id),
                tournament_name=pg.tournament.name,
            )
        return [
            row for row in snapshot["movements"] if row.get("expense_id") in expense_ids
        ]

    async with journey.real_owner_browser_server(
        pg
    ) as base, async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        finance = await browser.new_context(
            extra_http_headers={"x-qa-actor": "finance"}
        )
        cp = await browser.new_context(extra_http_headers={"x-qa-actor": "budget"})
        page = await finance.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        statement_url = base + routes.BASE + f"/{statement_id}/directo"
        try:
            await page.goto(statement_url)
            await _submit_selection(
                page, "sin-cfdi", "Marcar Sin CFDI", "Sin comprobante autorizado"
            )
            assert "Sin CFDI declarados: 2" in await page.locator("body").inner_text()
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM amex_direct_no_cfdi_decisions"
                )
                == 2
            )
            # Revocation is an explicit POST with reason, never a search GET effect.
            await _submit_selection(
                page,
                "revocar-sin-cfdi",
                "Volver a buscar factura",
                "Revisar emision con el comercio",
            )
            assert "Sin CFDI declarados: 0" in await page.locator("body").inner_text()
            await _submit_selection(
                page,
                "sin-cfdi",
                "Marcar Sin CFDI",
                "Comercio confirma ausencia de comprobante",
            )
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM amex_direct_no_cfdi_decisions"
                )
                == 6
            )
            decision_ids = {
                str(row["no_cfdi_decision_id"])
                for row in await pg.conn.fetch(
                    "SELECT no_cfdi_decision_id FROM amex_direct_items"
                )
            }
            assert len(decision_ids) == 2
            assert await pg.conn.fetchval("SELECT count(*) FROM documentos") == 0

            # Exercise checkbox copying more than once to catch duplicate hidden IDs.
            await page.locator("#direct-select-all").check()
            await page.locator("#direct-select-all").uncheck()
            await page.locator("#direct-select-all").check()
            for action in ("sin-cfdi", "revocar-sin-cfdi", "enviar-presupuesto"):
                assert (
                    await page.locator(
                        f'form[action$="/{action}"] input[name="item_ids"]'
                    ).count()
                    == 2
                )
            classification = page.locator('form[action$="/clasificar"]')
            assert (
                await classification.locator(
                    'input[type="checkbox"][name="item_ids"]'
                ).count()
                == 2
            )
            await classification.locator("#direct-project").select_option(
                f"t:{pg.tournament.id}"
            )
            await classification.locator("#direct-concept").select_option(
                str(pg.concept.id)
            )
            await classification.locator('[name="edition_year"]').select_option("2026")
            await classification.get_by_role(
                "button",
                name="Guardar clasificación de cargos seleccionados",
                exact=True,
            ).click()
            await page.wait_for_url("**/directo?msg=**")
            assert "Sin CFDI declarados: 2" in await page.locator("body").inner_text()
            await page.locator("#direct-select-all").check()
            send = page.locator('form[action$="/enviar-presupuesto"]')
            assert await send.locator('input[name="item_ids"]').count() == 2
            await send.locator('[name="confirmed"]').check()
            await send.get_by_role(
                "button", name="Enviar seleccionados a Control Presupuestal", exact=True
            ).click()
            await page.wait_for_url("**/directo?msg=**")

            budget_page = await cp.new_page()
            await budget_page.goto(base + routes.QUEUE)
            for _ in range(2):
                assign = budget_page.locator('form[action$="/asignar"]').first
                await assign.locator('[name="budget_line_id"]').select_option(
                    str(pg.line_id)
                )
                await assign.get_by_role(
                    "button", name="Asignar partida existente", exact=True
                ).click()
                await budget_page.wait_for_url("**/amex-directo?msg=**")
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM budget_movement_assignments"
                )
                == 2
            )
            pending = await budget_movements()
            assert len(pending) == 2
            assert sum(Decimal(str(row["amount"])) for row in pending) == 348

            # PostgreSQL READ ONLY enforces preview purity with real financial owners.
            async with pg.factory() as session:
                await session.execute(text("SET TRANSACTION READ ONLY"))
                proposal = await accounting.preview_direct_accounting(
                    session, actor_id=pg.actor.id, import_id=statement_id
                )
                assert proposal.status == "ready", (proposal.reason, proposal.progress)
            lines = proposal.progress["lines"]
            assert len(lines) == 4
            assert sum(Decimal(str(line["debe"])) for line in lines) == 348
            assert sum(Decimal(str(line["haber"])) for line in lines) == 348
            await page.goto(statement_url)
            rendered = Counter()
            for row in await page.locator("#direct-journal tbody tr").all():
                cells = await row.locator("td").all_text_contents()
                rendered[(cells[0], Decimal(cells[2]), Decimal(cells[3]))] += 1
            assert rendered == Counter(
                (
                    str(line["cuenta_codigo"]),
                    Decimal(str(line["debe"])),
                    Decimal(str(line["haber"])),
                )
                for line in lines
            )
            confirm = page.locator('form[action$="/confirmar"]')
            await confirm.locator('[name="accounting_date"]').fill("2026-10-31")
            await confirm.locator('[name="reason"]').fill("Corte sintetico sin CFDI")
            await confirm.locator('[name="confirmed"]').check()
            await confirm.get_by_role(
                "button", name="Generar póliza directa completa", exact=True
            ).click()
            await page.wait_for_url("**/directo?msg=**")
            assert (
                await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 1
            )
            assert (
                await pg.conn.fetchval("SELECT count(*) FROM accounting_poliza_lines")
                == 4
            )
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM amex_recognition_consumptions"
                )
                == 2
            )
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM budget_movement_assignments"
                )
                == 2
            )
            assert await pg.conn.fetchval("SELECT count(*) FROM documentos") == 0
            actual = await budget_movements()
            assert len(actual) == 2 and all(
                row["kind"] == "ledger_expense" for row in actual
            )
            assert sum(Decimal(str(row["amount"])) for row in actual) == 348
            raw = await pg.conn.fetchval(
                "SELECT snapshot_json FROM amex_direct_accounting_cuts WHERE import_id=$1",
                statement_id,
            )
            snapshot = json.loads(raw) if isinstance(raw, str) else raw
            assert {
                part["no_cfdi_decision"]["id"] for part in snapshot["partidas"]
            } == decision_ids
            for part in snapshot["partidas"]:
                assert part["coverage_kind"] == "new_no_cfdi"
                assert not part.get("cfdi")
                assert len(part["journal_lines"]) == 2
                assert Decimal(str(part["journal_lines"][0]["debe"])) == Decimal(
                    str(part["amount"])
                )
            for fmt in ("xlsx", "csv", "zip"):
                async with page.expect_download() as downloading:
                    await page.get_by_role("link", name=fmt.upper(), exact=True).click()
                download = await downloading.value
                content = Path(await download.path()).read_bytes()
                journey.assert_exported_policy(content, fmt, [], snapshot["lines"])
                _assert_decision_export(content, fmt, decision_ids)
            assert (
                await page.locator(
                    'form[action$="/sin-cfdi"],form[action$="/revocar-sin-cfdi"]'
                ).count()
                == 0
            )
            await budget_page.goto(base + routes.QUEUE)
            assert (
                await budget_page.locator(
                    'form[action$="/asignar"],form[action$="/rechazar"]'
                ).count()
                == 0
            )
            assert not errors
        finally:
            await browser.close()
