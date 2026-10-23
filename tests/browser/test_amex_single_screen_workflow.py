"""Single-screen browser acceptance over real HTML and synthetic financial owners."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from playwright.sync_api import expect

from tests.browser.test_amex_account_treatment_workflow import (
    VIEWPORTS,
    account_treatment_workspace,
    complete_direct_workspace,
    direct_workspace,
    workspace,
)
from tests.browser.test_amex_statement_workflow import live_app


def watch_navigation(page):
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.url)
            if request.is_navigation_request() and request.frame == page.main_frame
            else None
        ),
    )
    return requests


@pytest.mark.parametrize("page", VIEWPORTS, indirect=True)
def test_dialog_cancel_escape_restores_focus_without_writes(
    page, account_treatment_workspace
):
    w = account_treatment_workspace
    navigations = watch_navigation(page)
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)
        page.locator('input[type="checkbox"][name="item_ids"]').check()
        opener = page.locator('[data-amex-open="treatment"]')
        opener.click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        assert dialog.get_attribute("aria-labelledby") or dialog.get_attribute(
            "aria-label"
        )
        assert dialog.evaluate("element => element.contains(document.activeElement)")
        page.locator("#direct-treatment").select_option("benefit_expense")
        dialog.locator('[name="reason"]').fill("Borrador sin efectos")
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        expect(opener).to_be_focused()
        assert page.url == url and len(navigations) == 1
    w.routes.treatments.preview_direct_account_treatment.assert_not_awaited()
    w.routes.treatments.confirm_direct_account_treatment.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.mark.parametrize("page", VIEWPORTS, indirect=True)
def test_hundred_rows_filter_selection_and_cancel_preserve_context(
    page, account_treatment_workspace
):
    w = account_treatment_workspace
    w.view.items[:] = [
        SimpleNamespace(
            **{
                **vars(w.item),
                "id": uuid4(),
                "row_number": n + 2,
                "descriptor": f"COMERCIO {n:03d}",
            }
        )
        for n in range(100)
    ]
    navigations = watch_navigation(page)
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)
        rows = page.locator("tr[data-item-id]")
        assert rows.count() == 100
        selected = rows.nth(70).get_attribute("data-item-id")
        rows.nth(70).locator('input[name="item_ids"]').check()
        search = page.locator("[data-amex-search-input]")
        search.fill("COMERCIO 070")
        expect(page.locator("tr[data-item-id]:visible")).to_have_count(1)
        page.locator('[data-amex-open="treatment"]').click()
        form = page.locator('form[action$="/tratamiento/preview"]')
        assert form.locator('input[name="item_ids"]').count() == 1
        assert form.locator('input[name="item_ids"]').input_value() == selected
        page.keyboard.press("Escape")
        expect(search).to_have_value("COMERCIO 070")
        expect(rows.nth(70).locator('input[name="item_ids"]')).to_be_checked()
        search.fill("")
        expect(page.locator("tr[data-item-id]:visible")).to_have_count(100)
        page.evaluate("window.scrollTo(0, 500)")
        before_scroll = page.evaluate("scrollY")
        page.locator("[data-amex-refresh]").evaluate("button => button.click()")
        expect(rows.nth(70).locator('input[name="item_ids"]')).to_be_checked()
        page.wait_for_function(
            "expected => Math.abs(scrollY - expected) < 5", arg=before_scroll
        )
        assert page.url == url and len(navigations) == 1
    w.session.commit.assert_not_awaited()


def test_followed_303_owner_error_is_visible_without_false_success(
    page, account_treatment_workspace
):
    w = account_treatment_workspace
    navigations = watch_navigation(page)
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)

        def pending_response(route):
            w.view.source_version = 5
            w.item.source_version = 3
            route.fulfill(
                status=303,
                headers={"location": url + "?error_msg=Bloqueo%20autoritativo"},
            )

        page.route("**/tratamiento/preview", pending_response)
        page.locator('input[type="checkbox"][name="item_ids"]').check()
        page.locator('[data-amex-open="treatment"]').click()
        page.locator("#direct-treatment").select_option("benefit_expense")
        form = page.locator('form[action$="/tratamiento/preview"]')
        form.locator('[name="reason"]').fill("Motivo debe conservarse")
        form.get_by_role(
            "button", name="Revisar tratamiento de los cargos seleccionados"
        ).click()
        expect(page.locator("body")).to_contain_text("Bloqueo autoritativo")
        expect(page.locator('dialog[open] [name="reason"]')).to_have_value(
            "Motivo debe conservarse"
        )
        fresh = page.locator('dialog[open] form[action$="/tratamiento/preview"]')
        assert fresh.locator('[name="expected_source_version"]').input_value() == "5"
        import json

        assert (
            json.loads(fresh.locator('[name="expected_versions"]').input_value())[
                str(w.item.id)
            ]
            == 3
        )
        assert page.url == url and len(navigations) == 1
        assert not page.get_by_role(
            "button", name="Confirmar tratamiento del lote"
        ).is_visible()
    w.routes.treatments.confirm_direct_account_treatment.assert_not_awaited()
    w.session.commit.assert_not_awaited()


def test_pending_write_disables_double_submit_and_escape(
    page, account_treatment_workspace
):
    import asyncio
    from unittest.mock import AsyncMock

    w = account_treatment_workspace
    original = w.routes.treatments.confirm_direct_account_treatment.return_value

    async def delayed_confirmation(*args, **kwargs):
        await asyncio.sleep(0.6)
        return original

    w.routes.treatments.confirm_direct_account_treatment = AsyncMock(
        side_effect=delayed_confirmation
    )
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)
        page.locator('input[type="checkbox"][name="item_ids"]').check()
        page.locator('[data-amex-open="treatment"]').click()
        page.locator("#direct-treatment").select_option("benefit_expense")
        form = page.locator('form[action$="/tratamiento/preview"]')
        form.locator('[name="reason"]').fill("Confirmación única con bloqueo pendiente")
        form.get_by_role(
            "button", name="Revisar tratamiento de los cargos seleccionados"
        ).click()
        button = page.get_by_role("button", name="Confirmar tratamiento del lote")
        expect(button).to_be_visible()
        page.locator('dialog[open] [name="confirmed"]').check()
        button.evaluate("button => { button.click(); button.click(); }")
        expect(button).to_be_disabled()
        expect(page.get_by_role("button", name="Cerrar ventana AMEX")).to_be_disabled()
        page.keyboard.press("Escape")
        expect(page.locator("dialog[open]")).to_be_visible()
        expect(page.locator("dialog[open]")).to_have_count(0)
        assert page.url == url
    w.routes.treatments.confirm_direct_account_treatment.assert_awaited_once()


def test_late_picker_response_cannot_replace_newer_item(
    page, account_treatment_workspace
):
    w = account_treatment_workspace
    second = SimpleNamespace(**{**vars(w.item), "id": uuid4(), "row_number": 3})
    w.view.items.append(second)
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)
        page.evaluate(
            """canonical => {
          const original = window.fetch;
          let count = 0;
          window.fetch = (url, options) => {
            if (!String(url).includes('/cfdi')) return original(url, options);
            const first = ++count === 1;
            return new Promise(resolve => setTimeout(() => {
              const html = '<div id="amex-workspace-view" data-amex-view="detail" data-statement-url="' + canonical + '"><h2>' + (first ? 'RESPUESTA ANTIGUA' : 'CARGO ACTUAL') + '</h2></div>';
              const response = new Response(html, {headers: {'Content-Type': 'text/html'}});
              Object.defineProperty(response, 'url', {value: String(url)});
              resolve(response);
            }, first ? 600 : 10));
          };
        }""",
            url,
        )
        links = page.locator('tr[data-item-id] a[href*="/cfdi?"]')
        links.first.click()
        links.nth(1).evaluate("link => link.click()")
        expect(page.locator("dialog.amex-drawer[open]")).to_contain_text("CARGO ACTUAL")
        page.wait_for_timeout(700)
        assert "RESPUESTA ANTIGUA" not in page.locator("dialog").inner_text()
        assert page.locator("#amex-workspace-view").count() == 1
        assert page.url == url
    w.session.commit.assert_not_awaited()


@pytest.mark.parametrize("status", [403, 409, 422])
def test_json_error_preserves_capture_and_never_injects_html(
    page, account_treatment_workspace, status
):
    w = account_treatment_workspace
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)
        page.route(
            "**/tratamiento/preview",
            lambda route: route.fulfill(
                status=status,
                json={
                    "detail": '<img src=x onerror="window.injected=true"> Error vigente'
                },
            ),
        )
        page.locator('input[type="checkbox"][name="item_ids"]').check()
        page.locator('[data-amex-open="treatment"]').click()
        page.locator("#direct-treatment").select_option("benefit_expense")
        form = page.locator('form[action$="/tratamiento/preview"]')
        form.locator('[name="reason"]').fill("Captura que no debe perderse")
        form.get_by_role(
            "button", name="Revisar tratamiento de los cargos seleccionados"
        ).click()
        expect(page.locator("body")).to_contain_text("Error vigente")
        expect(form.locator('[name="reason"]')).to_have_value(
            "Captura que no debe perderse"
        )
        assert page.locator("img").count() == 0
        assert page.evaluate("window.injected") is None
        assert page.url == url
        if status == 409:
            w.view.source_version = 6
            w.item.source_version = 4
            preview = form.get_by_role(
                "button", name="Revisar tratamiento de los cargos seleccionados"
            )
            expect(preview).to_be_disabled()
            page.get_by_role(
                "button", name="Actualizar evidencia y revisar", exact=True
            ).click()
            fresh = page.locator('dialog[open] form[action$="/tratamiento/preview"]')
            expect(fresh.locator('[name="expected_source_version"]')).to_have_value("6")
            expect(fresh.locator('[name="reason"]')).to_have_value(
                "Captura que no debe perderse"
            )
            import json

            assert (
                json.loads(fresh.locator('[name="expected_versions"]').input_value())[
                    str(w.item.id)
                ]
                == 4
            )
            expect(
                fresh.get_by_role(
                    "button", name="Revisar tratamiento de los cargos seleccionados"
                )
            ).to_be_enabled()
            assert page.locator('dialog[open] [name="confirmed"]:checked').count() == 0
    w.routes.treatments.confirm_direct_account_treatment.assert_not_awaited()
    w.session.commit.assert_not_awaited()


def test_login_html_is_rejected_without_discarding_draft(
    page, account_treatment_workspace
):
    w = account_treatment_workspace
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)
        page.route(
            "**/tratamiento/preview",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<html><h1>Iniciar sesión</h1></html>",
            ),
        )
        page.locator('input[type="checkbox"][name="item_ids"]').check()
        page.locator('[data-amex-open="treatment"]').click()
        page.locator("#direct-treatment").select_option("benefit_expense")
        form = page.locator('form[action$="/tratamiento/preview"]')
        form.locator('[name="reason"]').fill("Borrador durante sesión vencida")
        form.get_by_role(
            "button", name="Revisar tratamiento de los cargos seleccionados"
        ).click()
        expect(page.locator("body")).to_contain_text("La sesión o el contexto cambió")
        expect(form.locator('[name="reason"]')).to_have_value(
            "Borrador durante sesión vencida"
        )
        assert page.locator("#amex-workspace-view").count() == 1
        assert page.url == url
    w.session.commit.assert_not_awaited()


def test_drawer_pagination_manual_search_and_explicit_selected_sat(
    page, complete_direct_workspace, monkeypatch
):
    from unittest.mock import AsyncMock

    from tests.unit.gastos.test_amex_suggestions_settlement_routes import (
        suggested_workspace,
    )

    # Reuse the same canonical candidate payload as route acceptance.
    w = suggested_workspace.__wrapped__(complete_direct_workspace)
    w.suggestions["items"][0].update(
        has_more=True, cursor="opaque-cursor", coverage_complete=False
    )
    finder = AsyncMock(
        return_value={
            "candidates": [w.candidate],
            "row_number": 2,
            "item_version": 2,
            "source_version": 4,
            "has_more": False,
            "cursor": None,
        }
    )
    monkeypatch.setattr(w.routes.finder, "search_direct_cfdis", finder)
    from devnous.gastos.services.amex_direct_common import DirectResult

    selected_sat = AsyncMock(
        return_value=DirectResult("matched", progress={"status": "vigente"})
    )
    monkeypatch.setattr(
        w.routes.status, "consult_selected_direct_cfdi_status", selected_sat
    )
    navigations = watch_navigation(page)
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)
        page.get_by_role("link", name="Buscar CFDI", exact=True).click()
        drawer = page.locator("dialog.amex-drawer[open]")
        expect(drawer).to_be_visible()
        drawer.get_by_role("link", name="Siguiente página", exact=True).click()
        expect(drawer.locator("section[data-cfdi-uuid]")).to_have_count(1)
        assert (
            w.routes.suggestions.suggest_direct_statement_cfdis.await_args.kwargs[
                "cursor"
            ]
            == "opaque-cursor"
        )
        drawer.locator('[name="q"]').fill("Suntory")
        drawer.get_by_role("button", name="Buscar facturas", exact=True).click()
        expect(drawer.locator('[name="q"]')).to_have_value("Suntory")
        expect(
            drawer.get_by_role("link", name="Siguiente página", exact=True)
        ).to_have_count(0)
        assert finder.await_args.kwargs["q"] == "Suntory"
        observe = drawer.locator('form[action$="/cfdi/vigencia"]')
        assert observe.locator('[name="expected_item_version"]').input_value() == "2"
        assert observe.locator('[name="expected_source_version"]').input_value() == "4"
        assert observe.locator('[name="cfdi_id"]').input_value() == w.candidate["id"]
        w.routes.status.consult_direct_statement_status.assert_not_awaited()
        assert (
            observe.locator('[name="confirmed"]').get_attribute("required") is not None
        )
        observe.locator('[name="confirmed"]').check()
        observe.get_by_role(
            "button", name="Consultar vigencia de esta factura", exact=True
        ).click()
        expect(drawer).to_contain_text("Vigencia observada: vigente")
        assert page.url == url and len(navigations) == 1
    selected_sat.assert_awaited_once()
    values = selected_sat.await_args.kwargs
    assert values["item_id"] == w.item.id
    assert str(values["cfdi_id"]) == w.candidate["id"]
    assert values["consented"] is True


def test_actual_pending_journal_refreshes_nonce_keeps_draft_and_unchecks_confirmation(
    page, complete_direct_workspace
):
    from devnous.gastos.services.amex_direct_common import DirectResult

    w = complete_direct_workspace

    async def pending(*args, **kwargs):
        import asyncio

        await asyncio.sleep(0.5)
        w.view.source_version = 5
        w.preview.source_version = 5
        w.preview.source_key = "fresh-owner-proof"
        return DirectResult("pending", "stale_item_version")

    w.routes.accounting.confirm_direct_accounting.side_effect = pending
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)
        page.locator('[data-amex-open="journal"]').click()
        form = page.locator('dialog[open] form[action$="/confirmar"]')
        old_nonce = form.locator('[name="idempotency_key"]').input_value()
        form.locator('[name="accounting_date"]').fill("2026-09-30")
        form.locator('[name="reason"]').fill(
            "Conservar borrador, revisar evidencia nueva"
        )
        form.locator('[name="confirmed"]').check()
        form.get_by_role(
            "button", name="Generar póliza directa completa", exact=True
        ).click()
        expect(form.locator('[name="accounting_date"]')).to_be_disabled()
        expect(form.locator('[name="reason"]')).to_be_disabled()
        expect(form.locator('[name="confirmed"]')).to_be_disabled()
        expect(form.locator('[name="expected_source_version"]')).to_have_value("5")
        expect(form.locator('[name="reason"]')).to_have_value(
            "Conservar borrador, revisar evidencia nueva"
        )
        expect(form.locator('[name="accounting_date"]')).to_have_value("2026-09-30")
        expect(form.locator('[name="confirmed"]')).not_to_be_checked()
        assert (
            form.locator('[name="expected_source_key"]').input_value()
            == "fresh-owner-proof"
        )
        assert form.locator('[name="idempotency_key"]').input_value() != old_nonce
        assert page.url == url
    w.session.commit.assert_not_awaited()
    w.session.rollback.assert_awaited()


@pytest.mark.parametrize("page", VIEWPORTS, indirect=True)
def test_typing_during_delayed_drawer_read_keeps_capture_and_visual_evidence(
    page, complete_direct_workspace
):
    from pathlib import Path

    from tests.unit.gastos.test_amex_suggestions_settlement_routes import (
        suggested_workspace,
    )

    w = suggested_workspace.__wrapped__(complete_direct_workspace)
    w.preview.progress = {
        "lines": [
            {
                "cuenta_codigo": "6000-001",
                "concepto": "Gasto sintético",
                "debe": "116.00",
                "haber": "0.00",
            },
            {
                "cuenta_codigo": "1170-002-003",
                "concepto": "Pasivo AMEX sintético",
                "debe": "0.00",
                "haber": "116.00",
            },
        ]
    }
    output = Path("/tmp/amex-single-screen-final-20261023")
    output.mkdir(exist_ok=True)
    suffix = str(page.viewport_size["width"])
    navigations = watch_navigation(page)
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)
        page.screenshot(path=str(output / f"main-{suffix}.png"), full_page=True)
        page.get_by_role("link", name="Buscar CFDI", exact=True).click()
        drawer = page.locator("dialog.amex-drawer[open]")
        expect(drawer.locator('[name="q"]')).to_be_visible()
        page.screenshot(path=str(output / f"drawer-{suffix}.png"), full_page=False)
        page.evaluate(
            """canonical => {
            const original = window.fetch;
            window.fetch = (url, options) => {
                if (!String(url).includes('/cfdi')) return original(url, options);
                window.__amexDelayedReadStarted = true;
                return new Promise(resolve => setTimeout(() => {
                    const html = '<div id="amex-workspace-view" data-amex-view="detail" data-statement-url="' + canonical + '"><h2>LATE REPLACEMENT</h2></div>';
                    const response = new Response(html, {headers: {'Content-Type': 'text/html'}});
                    Object.defineProperty(response, 'url', {value: String(url)});
                    resolve(response);
                }, 600));
            };
        }""",
            url,
        )
        drawer.locator('[name="q"]').fill("primera búsqueda")
        drawer.get_by_role("button", name="Buscar facturas", exact=True).click()
        page.wait_for_function("window.__amexDelayedReadStarted")
        drawer.locator('[name="q"]').fill("captura más reciente")
        reason = drawer.locator('form[action$="/vincular"] [name="reason"]')
        reason.fill("Razón escrita mientras se consulta")
        page.wait_for_timeout(750)
        expect(drawer.locator('[name="q"]')).to_have_value("captura más reciente")
        expect(reason).to_have_value("Razón escrita mientras se consulta")
        assert "LATE REPLACEMENT" not in drawer.inner_text()
        page.get_by_role("button", name="Cerrar ventana AMEX", exact=True).click()
        page.locator('[data-amex-open="journal"]').click()
        expect(page.get_by_role("dialog")).to_contain_text("Confirmar póliza completa")
        page.screenshot(path=str(output / f"journal-{suffix}.png"), full_page=False)
        assert page.url == url and len(navigations) == 1
    w.routes.matching.link_direct_cfdi_manual.assert_not_awaited()
    w.session.commit.assert_not_awaited()


def test_cancel_preview_reopens_editable_original_form(
    page, account_treatment_workspace
):
    w = account_treatment_workspace
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)
        page.locator('input[type="checkbox"][name="item_ids"]').check()
        page.locator('[data-amex-open="treatment"]').click()
        form = page.locator('form[action$="/tratamiento/preview"]')
        page.locator("#direct-treatment").select_option("benefit_expense")
        form.locator('[name="reason"]').fill("Borrador antes de cancelar revisión")
        form.get_by_role(
            "button", name="Revisar tratamiento de los cargos seleccionados"
        ).click()
        expect(
            page.get_by_role("button", name="Confirmar tratamiento del lote")
        ).to_be_visible()
        page.keyboard.press("Escape")
        page.locator('[data-amex-open="treatment"]').click()
        expect(form.locator('[name="reason"]')).to_be_enabled()
        expect(form.locator('[name="reason"]')).to_have_value(
            "Borrador antes de cancelar revisión"
        )
        expect(
            form.get_by_role(
                "button", name="Revisar tratamiento de los cargos seleccionados"
            )
        ).to_be_enabled()
        form.locator('[name="reason"]').fill("Borrador editable después de cancelar")
        assert page.url == url
    w.routes.treatments.confirm_direct_account_treatment.assert_not_awaited()
    w.session.commit.assert_not_awaited()


def test_network_failure_keeps_capture_warns_uncertainty_and_never_retries_post(
    page, account_treatment_workspace
):
    w = account_treatment_workspace
    attempts = []
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)

        def fail(route):
            attempts.append(route.request.method)
            route.abort("failed")

        page.route("**/tratamiento/preview", fail)
        page.locator('input[type="checkbox"][name="item_ids"]').check()
        page.locator('[data-amex-open="treatment"]').click()
        form = page.locator('form[action$="/tratamiento/preview"]')
        page.locator("#direct-treatment").select_option("benefit_expense")
        form.locator('[name="reason"]').fill("Captura retenida ante falla de red")
        form.get_by_role(
            "button", name="Revisar tratamiento de los cargos seleccionados"
        ).click()
        expect(page.locator("dialog[open]")).to_contain_text(
            "la operación puede haberse guardado"
        )
        expect(form.locator('[name="reason"]')).to_have_value(
            "Captura retenida ante falla de red"
        )
        expect(form.locator('[name="reason"]')).to_be_enabled()
        page.wait_for_timeout(350)
        assert attempts == ["POST"]
        assert page.url == url
    w.session.commit.assert_not_awaited()


@pytest.mark.parametrize("page", [VIEWPORTS[1]], indirect=True)
def test_mobile_journal_keeps_monetary_cells_on_one_line(
    page, complete_direct_workspace
):
    from pathlib import Path

    w = complete_direct_workspace
    w.preview.progress = {
        "lines": [
            {
                "cuenta_codigo": "6000-001",
                "concepto": "Gasto sintético",
                "debe": "116.00",
                "haber": "0.00",
            },
            {
                "cuenta_codigo": "1170-002-003",
                "concepto": "Pasivo AMEX sintético",
                "debe": "0.00",
                "haber": "116.00",
            },
        ]
    }
    with live_app(w.app) as base:
        url = base + f"{w.routes.BASE}/{w.statement.id}/directo"
        page.goto(url)
        page.locator('[data-amex-open="journal"]').click()
        table = page.locator("dialog[open] #direct-journal")
        expect(table).to_be_visible()
        values = table.locator("tbody tr td:nth-child(3), tbody tr td:nth-child(4)")
        assert values.all_text_contents() == ["116.00", "0.00", "0.00", "116.00"]
        for cell in values.all():
            geometry = cell.evaluate(
                """element => {
                const range = document.createRange();
                range.selectNodeContents(element);
                const style = getComputedStyle(element);
                return {lines: range.getClientRects().length, whiteSpace: style.whiteSpace,
                        align: style.textAlign, numeric: style.fontVariantNumeric};
            }"""
            )
            assert geometry["lines"] == 1
            assert geometry["whiteSpace"] == "nowrap"
            assert geometry["align"] == "right"
            assert "tabular-nums" in geometry["numeric"]
        assert table.evaluate(
            "element => element.getBoundingClientRect().width <= element.closest('dialog').clientWidth"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.url == url
        output = Path("/tmp/amex-single-screen-final-20261023")
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / "journal-390.png"), full_page=False)
    w.session.commit.assert_not_awaited()
