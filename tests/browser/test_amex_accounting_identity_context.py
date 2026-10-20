"""Desktop/mobile report context and consolidated decisions with synthetic data."""

import asyncio
import json
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
    name = (
        'Persona <sintética> & "prueba" '
        '<img src=x onerror="window.amexInjected=true">'
        "<script>window.amexInjected=true</script> " + "ApellidoLargo" * 8
    )
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
    assert page.locator("img").count() == 0
    assert context.locator("script").count() == 0
    assert page.locator(".report-beneficiary script").count() == 0
    assert page.evaluate("window.amexInjected === undefined")
    # Only the trusted platform tracker and explicit batch UI may execute.
    scripts = page.locator("script")
    assert scripts.count() == 2
    code = "\n".join(scripts.all_text_contents())
    assert "amex-apply-batch" in code
    assert "__samchatCustomerSuccessTracker" in code
    assert name not in code
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert context.evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.locator('input[name="reviewed"]').count() == 0
    debtor = page.locator("#amex-batch-debtor")
    treatment = page.locator("#amex-batch-treatment")
    assert debtor.input_value() == "" and treatment.input_value() == ""
    assert (
        debtor.locator("option").first.inner_text() == "Selecciona la cuenta del socio"
    )
    selected_expense = page.locator(".amex-batch-item").nth(1)
    selected_expense.check()
    treatment.select_option("partner_receivable")
    assert debtor.input_value() == ""  # Selecting a treatment never infers a debtor.
    page.locator("#amex-apply-batch").click()
    assert page.locator("#amex-decisions").input_value() == "[]"
    assert "cuenta y motivo" in page.locator("#amex-batch-error").inner_text()
    debtor.select_option(str(accounts[1].id))
    page.locator("#amex-batch-reason").fill("Elección manual sintética")
    page.locator("#amex-apply-batch").click()
    form = page.locator("#amex-cut-confirmation")
    form.locator('input[name="confirmed"]').check()
    form.locator('input[name="reason"]').fill("Atestación actual sintética")
    submitted = form.evaluate("el => Object.fromEntries(new FormData(el))")
    assert submitted["confirmation_mode"] == "approved_informe"
    assert submitted["confirmed"] == "true"
    assert submitted["expected_source_key"] == session.preview.source_key
    assert (
        json.loads(submitted["expected_versions"]) == session.preview.current_versions
    )
    assert json.loads(submitted["decisions"]) == [
        {
            "expense_ids": [str(expenses[1].id)],
            "treatment": "partner_receivable",
            "debtor_account_id": str(accounts[1].id),
            "reason": "Elección manual sintética",
        }
    ]
    assert submitted["reason"] == "Atestación actual sintética"
    assert form.get_attribute("action").endswith(f"/informes/{informe.id}/cortes")
    shell = page.locator(".table-shell")
    shell.evaluate("el => el.scrollLeft = el.scrollWidth")
    debtor.scroll_into_view_if_needed()
    assert debtor.is_visible()
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
