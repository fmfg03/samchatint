"""Offline acceptance of actual Payment Run bulk-proof form submission."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from devnous.gastos.routes import admin_routes


@pytest.fixture
def proof_html():
    document_ids = [uuid4() for _ in range(3)]
    rows = [
        {
            "id": identifier,
            "numero_referencia": f"S-test-{index}",
            "referencia_operaciones": f"OPS-{index}",
            "fecha_pago": date(2026, 10, 1),
            "confirmation_date": date(2026, 10, 8),
            "monto": 100,
            "status": "en proceso de pago",
            "can_upload_payment_proof": True,
        }
        for index, identifier in enumerate(document_ids)
    ]

    async def render():
        with (
            patch.object(
                admin_routes, "list_payment_run_items",
                AsyncMock(side_effect=[[], rows]),
            ),
            patch.object(
                admin_routes, "list_payment_run_closures",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                admin_routes, "list_prestamo_payment_run_items",
                AsyncMock(side_effect=[[], []]),
            ),
        ):
            response = await admin_routes.admin_finance_payment_run(
                request=SimpleNamespace(query_params={}),
                session=AsyncMock(),
                current_empleado=SimpleNamespace(
                    id=uuid4(), rol="contabilidad", nombre="Fixture"
                ),
                status="pendientes", date_from=None, date_to=None, q=None,
                vista="comprobantes",
            )
            return response.body.decode()

    # Playwright's synchronous fixture owns an event loop on the main thread.
    with ThreadPoolExecutor(max_workers=1) as executor:
        html = executor.submit(lambda: asyncio.run(render())).result()
    return html, document_ids


def _upload(page):
    page.locator("#payment-proof-files").set_input_files(
        {"name": "proof.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-test"}
    )
    page.locator("#payment-proof-apply-one").check()


def test_bulk_proof_submits_edited_dates_only_for_selected_documents(page, proof_html):
    html, identifiers = proof_html
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content(html)
    for identifier in identifiers[:2]:
        page.locator(
            f'[data-payment-proof-selection][value="{identifier}"]'
        ).check()
    for identifier, value in zip(identifiers, ["2026-10-07", "2026-10-09", ""]):
        page.locator(f'[data-payment-date-document="{identifier}"]').fill(value)
    _upload(page)
    result = page.evaluate("""() => {
        const form = document.querySelector('#payment-run-bulk-proof-form');
        let valid;
        form.addEventListener('submit', event => {
            valid = !event.defaultPrevented; event.preventDefault();
        });
        form.dispatchEvent(new Event('submit', {cancelable:true}));
        return {valid, entries:Array.from(new FormData(form).entries()).filter(
            ([key]) => key.startsWith('fecha_pago') || key === 'selected_document_ids'
        )};
    }""")
    assert result["valid"]
    entries = result["entries"]
    assert {key: value for key, value in entries if key.startswith("fecha_pago")} == {
        f"fecha_pago_{identifiers[0]}": "2026-10-07",
        f"fecha_pago_{identifiers[1]}": "2026-10-09",
    }
    assert {value for key, value in entries if key == "selected_document_ids"} == {
        str(identifier) for identifier in identifiers[:2]
    }
    assert len(entries) == 4
    assert errors == []


def test_bulk_proof_blocks_selected_document_without_date(page, proof_html):
    html, identifiers = proof_html
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content(html)
    identifier = identifiers[0]
    page.locator(f'[data-payment-proof-selection][value="{identifier}"]').check()
    page.locator(f'[data-payment-date-document="{identifier}"]').fill("")
    _upload(page)
    blocked = page.evaluate("""() => {
        const form = document.querySelector('#payment-run-bulk-proof-form');
        const event = new Event('submit', {cancelable:true});
        form.dispatchEvent(event);
        return event.defaultPrevented;
    }""")
    assert blocked
    assert "Revisa la fecha" in page.locator("#payment-proof-form-error").inner_text()
    assert page.locator('#payment-proof-selected-inputs input').count() == 0
    assert errors == []
