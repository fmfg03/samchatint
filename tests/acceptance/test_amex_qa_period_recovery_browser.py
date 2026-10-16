"""Actual Finance recovery UI over owner PostgreSQL; no business-service mocks."""

import pytest
from playwright.async_api import async_playwright
from test_amex_direct_period_recovery_postgres import seed_previous_period_omission
from test_amex_qa_browser_accounting_journey import direct_pg as _direct_pg
from test_amex_qa_browser_accounting_journey import (
    real_owner_browser_server,
)

from devnous.gastos.routes import admin_amex_direct_routes as routes

direct_pg = _direct_pg


@pytest.mark.asyncio
async def test_real_recovery_forms_preserve_originals_and_recover_only_five(direct_pg):
    """Small6row fixture proves five missing anchors without rewriting history."""
    pg = direct_pg
    import_id, version = await seed_previous_period_omission(pg)
    header_before = await pg.conn.fetchval(
        "SELECT row_to_json(h)::text FROM amex_statement_imports h WHERE id=$1",
        import_id,
    )
    physical_before = await pg.conn.fetch(
        "SELECT id,import_item_id,imported_expense_id,movement_kind,source_json "
        "FROM amex_direct_items WHERE import_id=$1 ORDER BY id",
        import_id,
    )
    receipts_before = await pg.conn.fetch(
        "SELECT row_to_json(r)::text FROM amex_statement_import_items r "
        "WHERE import_id=$1 ORDER BY id",
        import_id,
    )
    original_bytes = await pg.conn.fetchval(
        "SELECT content FROM amex_statement_source_files WHERE import_id=$1",
        import_id,
    )
    original_expenses = {
        row["imported_expense_id"]
        for row in physical_before
        if row["imported_expense_id"]
    }
    assert len(physical_before) == 6 and len(original_expenses) == 1
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 1

    async with real_owner_browser_server(pg) as base, async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        browser_context = await browser.new_context(
            extra_http_headers={"x-qa-actor": "finance"}
        )
        page = await browser_context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        statement_path = f"{routes.BASE}/{import_id}/directo"
        form_url = base + statement_path + "/periodo"
        try:
            await page.goto(form_url)
            assert "2026-10-01 a 2026-10-31" in await page.locator("main").inner_text()
            form = page.locator('form[action$="/periodo/previsualizar"]')
            await form.locator('[name="period_start"]').fill("2026-10-02")
            await form.locator('[name="period_end"]').fill("2026-10-31")
            await form.locator('[name="reason"]').fill("Cobertura inválida de prueba")
            await form.get_by_role(
                "button", name="Previsualizar recuperación de período"
            ).click()
            assert await page.locator('form[action$="/periodo/confirmar"]').count() == 0
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM amex_direct_coverage_revisions"
                )
                == 0
            )

            await page.goto(form_url)
            form = page.locator('form[action$="/periodo/previsualizar"]')
            await form.locator('[name="period_start"]').fill("2026-09-30")
            await form.locator('[name="period_end"]').fill("2026-10-31")
            await form.locator('[name="reason"]').fill(
                "QA: recuperar cinco cargos originales omitidos por período"
            )
            await form.get_by_role(
                "button", name="Previsualizar recuperación de período"
            ).click()
            assert "Cargos por recuperar: 5" in await page.locator("main").inner_text()
            confirmation = page.locator('form[action$="/periodo/confirmar"]')
            assert await confirmation.count() == 1
            payload = await confirmation.evaluate(
                "form => Object.fromEntries(new FormData(form).entries())"
            )
            assert int(payload["expected_source_version"]) == version
            assert "confirmed" not in payload
            assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 1
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM amex_direct_recovered_rows"
                )
                == 0
            )

            endpoint = base + statement_path + "/periodo/confirmar"
            denied = await browser_context.request.post(
                endpoint,
                form={**payload, "_csrf_token": "wrong", "confirmed": "true"},
                max_redirects=0,
            )
            assert denied.status == 403
            no_consent = await browser_context.request.post(
                endpoint, form=payload, max_redirects=0
            )
            assert no_consent.status == 303
            stale = await browser_context.request.post(
                endpoint,
                form={
                    **payload,
                    "expected_source_version": str(version + 1),
                    "confirmed": "true",
                },
                max_redirects=0,
            )
            assert stale.status == 409
            assert (
                await pg.conn.fetchval(
                    "SELECT count(*) FROM amex_direct_coverage_revisions"
                )
                == 0
            )
            assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 1

            await confirmation.locator('[name="confirmed"]').check()
            await confirmation.get_by_role(
                "button", name="Confirmar recuperación de período"
            ).click()
            await page.wait_for_url("**/directo?msg=**")
            assert page.url.split("?", 1)[0] == base + statement_path
            assert (
                "Cobertura efectiva corregida"
                in await page.locator("main").inner_text()
            )
            repeated = await browser_context.request.post(
                endpoint, form={**payload, "confirmed": "true"}, max_redirects=0
            )
            assert repeated.status == 303
            assert not errors
        finally:
            await browser.close()

    assert await pg.conn.fetchval("SELECT count(*) FROM amex_statement_imports") == 1
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_coverage_revisions")
        == 1
    )
    assert (
        await pg.conn.fetchval("SELECT count(*) FROM amex_direct_recovered_rows") == 5
    )
    assert await pg.conn.fetchval("SELECT count(*) FROM expense_reports") == 6
    assert original_expenses <= {
        row["id"] for row in await pg.conn.fetch("SELECT id FROM expense_reports")
    }
    assert header_before == await pg.conn.fetchval(
        "SELECT row_to_json(h)::text FROM amex_statement_imports h WHERE id=$1",
        import_id,
    )
    assert physical_before == await pg.conn.fetch(
        "SELECT id,import_item_id,imported_expense_id,movement_kind,source_json "
        "FROM amex_direct_items WHERE import_id=$1 ORDER BY id",
        import_id,
    )
    assert receipts_before == await pg.conn.fetch(
        "SELECT row_to_json(r)::text FROM amex_statement_import_items r "
        "WHERE import_id=$1 ORDER BY id",
        import_id,
    )
    assert original_bytes == await pg.conn.fetchval(
        "SELECT content FROM amex_statement_source_files WHERE import_id=$1", import_id
    )
    effective = await pg.conn.fetch(
        "SELECT movement_kind,imported_expense_id,recovery_revision_id "
        "FROM amex_direct_effective_sources WHERE import_id=$1",
        import_id,
    )
    assert len(effective) == 6
    assert all(
        row["movement_kind"] == "debit" and row["imported_expense_id"]
        for row in effective
    )
    assert sum(row["recovery_revision_id"] is not None for row in effective) == 5
    assert await pg.conn.fetchval("SELECT count(*) FROM cfdi_reports") == 0
    assert await pg.conn.fetchval("SELECT count(*) FROM accounting_polizas") == 0
    assert await pg.conn.fetchval("SELECT count(*) FROM documentos") == 0
    states = await pg.conn.fetch(
        "SELECT match_status,budget_status,accounting_status FROM amex_direct_items"
    )
    assert all(
        tuple(row.values()) == ("pending", "unclassified", "pending") for row in states
    )
