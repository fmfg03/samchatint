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
    page.evaluate("""() => {
        window.paymentReceipts = [];
        window.fetch = async (url, options) => {
            if (String(url).endsWith('/revision')) {
                return new Response(JSON.stringify({status:'revision_required'}), {
                    headers:{'content-type':'application/json'}
                });
            }
            window.paymentReceipts.push(Array.from(options.body.entries()).filter(
                ([key]) => key === 'effective_payment_dates' ||
                           key === 'selected_document_ids'
            ));
            return new Response(JSON.stringify({ok:true}), {
                headers:{'content-type':'application/json'}
            });
        };
    }""")
    page.locator("#payment-proof-files").set_input_files(
        {"name": "proof.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-test"}
    )
    page.locator("#payment-proof-apply-one").check()
    page.wait_for_function("""() => Array.from(document.querySelectorAll(
        '#payment-proof-mapping [data-payment-proof-review]'
    )).every(element => element.dataset.reviewStatus !== 'checking')""")


def test_bulk_proof_submits_edited_dates_only_for_selected_documents(page, proof_html):
    html, identifiers = proof_html
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content(html)
    for identifier in identifiers[:2]:
        page.locator(
            f'[data-payment-proof-selection][value="{identifier}"]'
        ).check()
    _upload(page)
    for identifier, value in zip(identifiers[:2], ["2026-10-07", "2026-10-09"]):
        field = page.locator(
            f'#payment-proof-mapping [data-payment-proof-document-id="{identifier}"] '
            'input[name="effective_payment_dates"]'
        )
        assert field.input_value() == "2026-10-08"
        field.fill(value)
    page.evaluate("""() => document.querySelector('#payment-run-bulk-proof-form')
        .dispatchEvent(new Event('submit', {cancelable:true}))""")
    page.wait_for_function("() => window.paymentReceipts.length === 1")
    entries = page.evaluate("() => window.paymentReceipts[0]")
    ids = [value for key, value in entries if key == "selected_document_ids"]
    dates = [value for key, value in entries if key == "effective_payment_dates"]
    assert dict(zip(ids, dates)) == {
        str(identifiers[0]): "2026-10-07", str(identifiers[1]): "2026-10-09"
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
    _upload(page)
    page.locator(
        '#payment-proof-mapping input[name="effective_payment_dates"]'
    ).fill("")
    page.evaluate("""() => document.querySelector('#payment-run-bulk-proof-form')
        .dispatchEvent(new Event('submit', {cancelable:true}))""")
    assert "Captura una fecha efectiva" in page.locator(
        "#payment-proof-form-error"
    ).inner_text()
    assert page.evaluate("() => window.paymentReceipts") == []
    assert errors == []
