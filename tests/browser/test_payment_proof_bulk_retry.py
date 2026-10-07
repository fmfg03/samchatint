from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from playwright.sync_api import Page, Route, expect

from devnous.gastos.routes import admin_routes


def _render_payment_proof_page(monkeypatch, document_ids: list[str]) -> str:
    proof_rows = [
        {
            "id": document_id,
            "numero_referencia": f"S-RETRY-{index}",
            "solicitante_nombre": "Dani",
            "beneficiario_nombre": "Proveedor Pago",
            "concepto_pago": "Hospedaje",
            "fecha_pago": None,
            "monto": Decimal("900.00"),
            "currency": "MXN",
            "status": "en proceso de pago",
            "can_edit_fecha_pago": False,
            "can_close": False,
            "can_upload_payment_proof": True,
        }
        for index, document_id in enumerate(document_ids, start=1)
    ]
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_items",
        AsyncMock(side_effect=[[], proof_rows]),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_closures",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_prestamo_payment_run_items",
        AsyncMock(side_effect=[[], []]),
    )

    coroutine = admin_routes.admin_finance_payment_run(
        request=SimpleNamespace(query_params={"vista": "comprobantes"}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(
            id=uuid4(),
            rol="usuario",
            departamento="Contabilidad",
            nombre="Dani",
        ),
        status="cerradas",
        date_from=None,
        date_to=None,
        q=None,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        response = executor.submit(asyncio.run, coroutine).result()
    return response.body.decode("utf-8")


def test_bulk_payment_proof_retry_skips_accepted_apply_one_rows(
    page: Page,
    monkeypatch,
) -> None:
    document_ids = [str(uuid4()), str(uuid4())]
    html = _render_payment_proof_page(monkeypatch, document_ids)
    attempts: list[str] = []

    def handle(route: Route) -> None:
        url = route.request.url
        if url == "http://samchat.test/payment-run":
            route.fulfill(status=200, content_type="text/html", body=html)
            return
        if url.endswith("/comprobante-pago/revision"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "status": "match",
                        "template_id": "browser-test",
                        "evidence_source": "local_pdf_text",
                        "reasons": [],
                    }
                ),
            )
            return
        if url.endswith("/comprobantes-pago/lote"):
            payload = (route.request.post_data_buffer or b"").decode(
                "latin-1", errors="ignore"
            )
            document_id = next(item for item in document_ids if item in payload)
            attempts.append(document_id)
            if document_id == document_ids[1] and attempts.count(document_id) == 1:
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "ok": False,
                            "code": "payment_proof_conflict",
                            "message": "El comprobante requiere corrección.",
                        }
                    ),
                )
                return
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "accepted_document_ids": [document_id]}),
            )
            return
        route.abort()

    page.route("**/*", handle)
    page.goto("http://samchat.test/payment-run")
    for checkbox in page.locator("[data-payment-proof-selection]").all():
        checkbox.check()
    page.locator("#payment-proof-apply-one").check()
    page.locator("#payment-proof-files").set_input_files(
        {
            "name": "comprobante.pdf",
            "mimeType": "application/pdf",
            "buffer": b"%PDF-1.4 browser retry",
        }
    )
    for effective_date in page.locator(
        'input[name="effective_payment_dates"]'
    ).all():
        effective_date.fill("2026-10-07")
    expect(
        page.locator('[data-payment-proof-review][data-review-status="match"]')
    ).to_have_count(2)

    page.locator("#payment-run-bulk-proof-form").evaluate(
        "form => form.requestSubmit()"
    )
    expect(page.get_by_role("alert")).to_contain_text(
        "El comprobante requiere corrección."
    )
    expect(page.get_by_role("status")).to_contain_text("1 comprobante(s) aceptado(s).")

    page.locator("#payment-run-bulk-proof-form").evaluate(
        "form => form.requestSubmit()"
    )
    expect(page.get_by_role("status")).to_contain_text("2 comprobante(s) aceptado(s).")

    assert attempts == [document_ids[0], document_ids[1], document_ids[1]]
