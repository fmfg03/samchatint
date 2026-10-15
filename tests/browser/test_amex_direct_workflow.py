"""Real browser/forms over synthetic direct-owner boundaries; no SAT network."""

from pathlib import Path

import pytest

from devnous.gastos.routes import admin_amex_statement_routes as statements
from tests.browser.test_amex_statement_workflow import live_app
from tests.unit.gastos.test_amex_direct_routes import (
    complete_direct_workspace as _complete_direct_workspace,
)
from tests.unit.gastos.test_amex_direct_routes import (
    direct_workspace as _direct_workspace,
)
from tests.unit.gastos.test_amex_direct_routes import workspace as _workspace

complete_direct_workspace = _complete_direct_workspace
direct_workspace = _direct_workspace
workspace = _workspace


@pytest.mark.parametrize(
    "page",
    [
        {"name": "desktop", "width": 1440, "height": 1000},
        {"name": "mobile", "width": 390, "height": 844},
    ],
    indirect=True,
)
def test_preview_actual_original_then_intake_and_complete_state(
    page, complete_direct_workspace
):
    w = complete_direct_workspace
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    with live_app(w.app) as base:
        page.goto(base + statements.BASE + "/directo/nuevo")
        assert page.locator(".container").count() == 1
        hero = page.locator(".workspace-hero")
        assert "informe completo" not in hero.inner_text().lower()
        assert "conciliación fiscal, presupuesto y póliza" in hero.inner_text()
        assert (
            hero.get_by_role("link", name="Estados directos", exact=True).get_attribute(
                "href"
            )
            == statements.BASE + "/directo"
        )
        assert page.locator("#amex-direct-import").is_disabled()
        page.locator('[name="card_account_id"]').select_option(str(w.actor.id))
        page.locator('[name="employee_id"]').select_option(str(w.actor.id))
        page.locator('[name="period_start"]').fill("2026-09-01")
        page.locator('[name="period_end"]').fill("2026-09-30")
        page.locator('[name="company_code"]').select_option("TEST")
        page.locator('[name="receiver_rfc"]').select_option("AAA010101AAA")
        page.locator('[name="currency"]').select_option("MXN")
        page.locator('[name="profile"]').select_option("debit_negative")
        page.locator('[name="file"]').set_input_files(
            {
                "name": "synthetic.csv",
                "mimeType": "text/csv",
                "buffer": b"date,description,amount\n2026-09-03,COMERCIO,-116.00\n2026-09-04,ABONO,20.00",
            }
        )
        page.get_by_role("button", name="Previsualizar archivo y signo").click()
        page.wait_for_function(
            "!document.getElementById('amex-direct-import').disabled"
        )
        preview = page.locator("#amex-direct-preview-result")
        assert "-116.00" in preview.inner_text() and "Crédito" in preview.inner_text()
        page.locator('[name="profile"]').select_option("debit_positive")
        assert page.locator("#amex-direct-import").is_disabled()
        page.locator('[name="profile"]').select_option("debit_negative")
        page.get_by_role("button", name="Previsualizar archivo y signo").click()
        page.wait_for_function(
            "!document.getElementById('amex-direct-import').disabled"
        )
        page.locator('[name="confirmed"]').check()
        assert page.locator("#amex-direct-import").is_enabled()
        page.get_by_role("button", name="Cargar estado directo", exact=True).click()
        page.wait_for_url("**/directo?msg=**")
        assert "-116.00" in page.locator("main").inner_text()
        assert "1170-002-003" in page.locator("main").inner_text()
        assert page.locator('select[name="informe_id"]').count() == 0
        assert page.get_by_role(
            "link", name="Programar pago de AMEX", exact=True
        ).first.is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator("img").count() == 0
        if page.viewport_size["width"] > 1000:
            assert page.locator(".direct-classification .direct-table").evaluate(
                "element => element.clientWidth > 1000"
            )
        evidence = Path("/tmp/samchat-amex-direct-ui-20261015")
        evidence.mkdir(exist_ok=True)
        page.screenshot(
            path=str(evidence / f"direct-{page.viewport_size['width']}.png"),
            full_page=True,
        )
        assert not errors
    w.routes.status.consult_direct_statement_status.assert_not_awaited()
    w.routes.intake.import_direct_statement.assert_awaited_once()


def test_cp_browser_selects_line_only_and_owner_denies_unscoped(
    page, complete_direct_workspace
):
    w = complete_direct_workspace
    w.actor.rol = "usuario"
    with live_app(w.app) as base:
        page.goto(base + w.routes.QUEUE)
        assert page.locator('select[name="budget_line_id"]').input_value() == ""
        assert page.locator('select[name="tournament_id"]').count() == 0
        page.locator('select[name="budget_line_id"]').select_option(
            str(w.item.budget_line_id)
        )
        page.get_by_role("button", name="Asignar partida existente").click()
        page.wait_for_url("**amex-directo?msg=**")
        assert "no registra una póliza ni un pago" in page.locator("main").inner_text()
        w.routes.budget.list_direct_budget_queue.side_effect = PermissionError(
            "same name denied"
        )
        response = page.goto(base + w.routes.QUEUE)
        assert response.status == 403
        assert "COMERCIO" not in page.locator("body").inner_text()
    kwargs = w.routes.budget.assign_direct_budget_line.await_args.kwargs
    assert kwargs["budget_line_id"] == w.item.budget_line_id
    assert "budget_concept_id" not in kwargs and "project_code" not in kwargs
