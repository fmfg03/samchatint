"""Browser acceptance of account-treatment forms over synthetic owner boundaries."""

from unittest.mock import AsyncMock

import pytest

from devnous.gastos.services.amex_direct_common import DirectConflict
from tests.browser.test_amex_statement_workflow import live_app
from tests.unit.gastos.test_amex_account_treatment_routes import (
    account_treatment_workspace as _account_treatment_workspace,
)
from tests.unit.gastos.test_amex_direct_routes import (
    complete_direct_workspace as _complete_direct_workspace,
)
from tests.unit.gastos.test_amex_direct_routes import (
    direct_workspace as _direct_workspace,
)
from tests.unit.gastos.test_amex_direct_routes import workspace as _workspace

account_treatment_workspace = _account_treatment_workspace
complete_direct_workspace = _complete_direct_workspace
direct_workspace = _direct_workspace
workspace = _workspace

VIEWPORTS = [
    {"name": "desktop", "width": 1440, "height": 1000},
    {"name": "mobile", "width": 390, "height": 844},
]


@pytest.mark.parametrize("page", VIEWPORTS, indirect=True)
def test_benefit_reaches_review_and_confirm_without_project(
    page, account_treatment_workspace
):
    w = account_treatment_workspace
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    with live_app(w.app) as base:
        page.goto(base + f"{w.routes.BASE}/{w.statement.id}/directo")
        page.locator('input[type="checkbox"][name="item_ids"]').check()
        page.locator("#direct-treatment").select_option("benefit_expense")
        form = page.locator('form[action$="/tratamiento/preview"]')
        assert (
            form.locator('[name="project_code"],[name="budget_concept_id"]').count()
            == 0
        )
        assert page.locator("#direct-debtor").is_disabled()
        form.locator('[name="reason"]').fill("Prestación para revisión presupuestal")
        form.get_by_role(
            "button", name="Revisar tratamiento de los cargos seleccionados"
        ).click()
        page.wait_for_url("**/tratamiento/preview")
        assert "Prestación absorbida" in page.locator("main").inner_text()
        assert "1 cargos seleccionados" in page.locator("main").inner_text()
        assert page.locator("img").count() == 0
        w.session.commit.assert_not_awaited()
        page.locator('[name="confirmed"]').check()
        page.get_by_role("button", name="Confirmar tratamiento del lote").click()
        page.wait_for_url("**/directo?msg=**")
        assert "Las prestaciones requieren partida" in page.locator("main").inner_text()
        assert not errors
    values = w.routes.treatments.confirm_direct_account_treatment.await_args.kwargs
    assert values["treatment"] == "benefit_expense"
    assert values["debtor_account_id"] is None
    assert "project_code" not in values and "budget_concept_id" not in values
    w.routes.budget.send_direct_items_to_budget.assert_not_awaited()


@pytest.mark.parametrize("page", VIEWPORTS, indirect=True)
def test_partner_requires_account_and_confirms_without_budget_submission(
    page, account_treatment_workspace
):
    w = account_treatment_workspace
    w.treatment_manifest["account_snapshot"]["debtor"] = {
        "id": str(w.debtor_id),
        "code": "1170-002-099",
        "name": "Socio sintético",
    }
    with live_app(w.app) as base:
        page.goto(base + f"{w.routes.BASE}/{w.statement.id}/directo")
        page.locator('input[type="checkbox"][name="item_ids"]').check()
        page.locator("#direct-treatment").select_option("partner_receivable")
        account = page.locator("#direct-debtor")
        assert account.is_visible() and account.is_enabled()
        assert account.get_attribute("required") is not None
        account.select_option(str(w.debtor_id))
        form = page.locator('form[action$="/tratamiento/preview"]')
        form.locator('[name="reason"]').fill("Consumo a la cuenta del socio")
        form.get_by_role(
            "button", name="Revisar tratamiento de los cargos seleccionados"
        ).click()
        page.wait_for_url("**/tratamiento/preview")
        assert "1170-002-099" in page.locator("main").inner_text()
        page.locator('[name="confirmed"]').check()
        page.get_by_role("button", name="Confirmar tratamiento del lote").click()
        page.wait_for_url("**/directo?msg=**")
    values = w.routes.treatments.confirm_direct_account_treatment.await_args.kwargs
    assert values["treatment"] == "partner_receivable"
    assert values["debtor_account_id"] == w.debtor_id
    w.routes.budget.send_direct_items_to_budget.assert_not_awaited()


@pytest.mark.parametrize("page", VIEWPORTS, indirect=True)
def test_stale_owner_response_preserves_reason_and_requires_new_review(
    page, account_treatment_workspace
):
    w = account_treatment_workspace
    w.routes.treatments.confirm_direct_account_treatment = AsyncMock(
        side_effect=DirectConflict("stale_item_version")
    )
    with live_app(w.app) as base:
        page.goto(base + f"{w.routes.BASE}/{w.statement.id}/directo")
        page.locator('input[type="checkbox"][name="item_ids"]').check()
        page.locator("#direct-treatment").select_option("benefit_expense")
        form = page.locator('form[action$="/tratamiento/preview"]')
        form.locator('[name="reason"]').fill("Motivo conservado al cambiar la fuente")
        form.get_by_role(
            "button", name="Revisar tratamiento de los cargos seleccionados"
        ).click()
        page.wait_for_url("**/tratamiento/preview")
        page.locator('[name="confirmed"]').check()
        page.get_by_role("button", name="Confirmar tratamiento del lote").click()
        page.wait_for_url("**/tratamiento")
        assert "El cargo cambió" in page.locator('[role="alert"]').inner_text()
        assert (
            page.locator('[name="reason"]').input_value()
            == "Motivo conservado al cambiar la fuente"
        )
        assert page.locator('[name="treatment"]').input_value() == "benefit_expense"
        assert page.get_by_role(
            "button", name="Revisar tratamiento de los cargos seleccionados"
        ).is_visible()
    w.session.commit.assert_not_awaited()


@pytest.mark.parametrize("page", VIEWPORTS, indirect=True)
def test_partner_selection_cannot_be_sent_to_budget(page, account_treatment_workspace):
    w = account_treatment_workspace
    w.item.treatment_kind = "partner_receivable"
    w.item.budget_status = "not_applicable"
    with live_app(w.app) as base:
        page.goto(base + f"{w.routes.BASE}/{w.statement.id}/directo")
        page.locator('input[type="checkbox"][name="item_ids"]').check()
        assert page.locator("#direct-send-budget").is_disabled()
        assert (
            page.locator('#direct-send-selection input[name="item_ids"]').count() == 0
        )
        assert (
            "1 seleccionados no se enviarán"
            in page.locator("#direct-budget-selection-count").inner_text()
        )
        assert (
            page.locator(
                'form[action$="/tratamiento/preview"] input[name="item_ids"]'
            ).count()
            == 1
        )
    w.routes.budget.send_direct_items_to_budget.assert_not_awaited()


@pytest.mark.parametrize("page", VIEWPORTS, indirect=True)
def test_mixed_selection_explains_and_submits_only_budget_eligible_benefits(
    page, account_treatment_workspace
):
    from types import SimpleNamespace
    from uuid import uuid4

    w = account_treatment_workspace
    w.item.treatment_kind = "benefit_expense"
    w.item.budget_status = "classified"
    partner = SimpleNamespace(
        **{
            **vars(w.item),
            "id": uuid4(),
            "row_number": 3,
            "treatment_kind": "partner_receivable",
            "budget_status": "not_applicable",
            "tournament_id": None,
            "project_code": None,
            "budget_concept_id": None,
            "budget_line_id": None,
            "budget_version_id": None,
        }
    )
    w.view.items.append(partner)
    with live_app(w.app) as base:
        page.goto(base + f"{w.routes.BASE}/{w.statement.id}/directo")
        page.locator("#direct-select-all").check()
        assert (
            "2 cargos seleccionados"
            in page.locator("#direct-selection-count").inner_text()
        )
        summary = page.locator("#direct-budget-selection-count").inner_text()
        assert (
            "1 cargos listos" in summary and "1 seleccionados no se enviarán" in summary
        )
        assert (
            page.locator('#direct-send-selection input[name="item_ids"]').count() == 1
        )
        assert page.locator(
            '#direct-send-selection input[name="item_ids"]'
        ).input_value() == str(w.item.id)
        assert (
            page.locator(
                'form[action$="/tratamiento/preview"] input[name="item_ids"]'
            ).count()
            == 2
        )
        form = page.locator('form[action$="/enviar-presupuesto"]')
        form.locator('[name="confirmed"]').check()
        form.get_by_role(
            "button", name="Enviar seleccionados a Control Presupuestal"
        ).click()
        page.wait_for_url("**/directo?msg=**")
    values = w.routes.budget.send_direct_items_to_budget.await_args.kwargs
    assert values["item_ids"] == [w.item.id]
    assert str(partner.id) not in values["expected_versions"]
