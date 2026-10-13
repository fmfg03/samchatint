"""Desktop/mobile review context and manual form behavior, using synthetic data."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.unit.gastos.test_amex_accounting_identity_context import (
    identity_review_fixture,
    render_identity_review,
)


@pytest.mark.parametrize(
    "page",
    [
        {"name": "desktop", "width": 1440, "height": 1000},
        {"name": "mobile", "width": 390, "height": 844},
    ],
    indirect=True,
)
def test_review_people_context_and_explicit_debtor_selection(page):
    session, informe, expenses, accounts = identity_review_fixture()
    # Deliberately long escaped names exercise wrapping at both viewport sizes.
    name = 'Persona <sintética> & "prueba" ' + "ApellidoLargo" * 8
    informe.beneficiario_empleado.nombre = name
    session.identity_rows[0] = (
        expenses[0].id,
        session.identity_rows[0][1],
        name,
        "1234",
    )
    # Playwright's sync driver owns a loop on this thread.
    with ThreadPoolExecutor(max_workers=1) as executor:
        response = executor.submit(
            asyncio.run, render_identity_review(session, informe)
        ).result()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content(response.body.decode())
    assert page.locator("h1").inner_text() == "Informe I-SYNTHETIC · AMEX"
    assert page.locator(".report-beneficiary").inner_text().endswith(name)
    context = page.locator(".person-context").first
    assert name in context.inner_text() and "Tarjeta: ••••1234" in context.inner_text()
    assert page.locator("img, script").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert context.evaluate("el => el.scrollWidth <= el.clientWidth")
    debtor = page.locator('select[name="debtor_account_id"]')
    assert debtor.nth(0).input_value() == str(accounts[1].id)
    assert debtor.nth(1).input_value() == ""
    assert (
        debtor.nth(1).locator("option").first.inner_text()
        == "Selecciona la cuenta del socio"
    )
    treatment = page.locator('select[name="treatment"]').nth(1)
    treatment.select_option("partner_receivable")
    assert (
        debtor.nth(1).input_value() == ""
    )  # Choosing treatment never infers a debtor.
    debtor.nth(1).select_option(str(accounts[1].id))
    form = page.locator("tbody tr").nth(1).locator("form")
    form.locator('input[name="reviewed"]').check()
    form.locator('input[name="reason"]').fill("Elección manual sintética")
    submitted = form.evaluate("el => Object.fromEntries(new FormData(el))")
    assert submitted == {
        "reviewed": "true",
        "treatment": "partner_receivable",
        "debtor_account_id": str(accounts[1].id),
        "reason": "Elección manual sintética",
    }
    assert form.get_attribute("action").endswith(f"/partidas/{expenses[1].id}")
    shell = page.locator(".table-shell")
    shell.evaluate("el => el.scrollLeft = el.scrollWidth")
    debtor.nth(1).scroll_into_view_if_needed()
    assert debtor.nth(1).is_visible()
    assert not errors
    # Optional local evidence; all people and financial data here are synthetic.
    evidence_dir = os.environ.get("AMEX_CONTEXT_SCREENSHOT_DIR")
    if evidence_dir:
        from pathlib import Path

        destination = Path(evidence_dir)
        destination.mkdir(parents=True, exist_ok=True)
        shell.evaluate("el => el.scrollLeft = 0")
        page.screenshot(
            path=str(destination / f"amex-context-{page.viewport_size['width']}.png"),
            full_page=True,
        )
