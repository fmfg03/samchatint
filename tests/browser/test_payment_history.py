"""Browser checks for the real history form and links using synthetic responses.

SQL semantics are tested separately on disposable PostgreSQL.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import expect

from devnous.gastos.routes import admin_routes
from devnous.gastos.routes.payment_history_ui import FILTER_DEFAULTS, render_history


@pytest.mark.parametrize(
    "page",
    [
        {"name": "desktop", "width": 1440, "height": 900},
        {"name": "mobile", "width": 390, "height": 844},
    ],
    indirect=True,
)
def test_history_form_sort_and_pagination_keep_filters(page, monkeypatch):
    payload = {
        "rows": [
            {
                "id": str(uuid4()),
                "numero_referencia": "S-SINTETICA",
                "monto": "125.50",
                "currency": "MXN",
                "status": "en proceso de pago",
            }
        ],
        "totals": [
            {
                "currency": "MXN",
                "paid": "100.00",
                "pending": "125.50",
                "paid_count": 1,
                "pending_count": 1,
                "missing_amounts": 0,
            }
        ],
        "total_count": 603,
        "current_page": 1,
    }
    monkeypatch.setattr(
        admin_routes, "list_payment_history", AsyncMock(return_value=payload)
    )
    monkeypatch.setattr(
        admin_routes,
        "render_admin_navigation",
        lambda *a, **kw: '<a href="/panel">Gastos</a>',
    )

    # Playwright owns the main thread's loop; build the async route response
    # on a separate test thread.
    def build_response():
        return asyncio.run(
            admin_routes.admin_finance_payment_history(
                SimpleNamespace(query_params={}),
                session=AsyncMock(),
                current_empleado=SimpleNamespace(id=uuid4(), rol="finanzas"),
            )
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(build_response).result()
    template = response.body.decode()
    original_content = render_history(FILTER_DEFAULTS, payload)

    def respond(route):
        selected = {
            **FILTER_DEFAULTS,
            **dict(parse_qsl(urlsplit(route.request.url).query)),
        }
        current_payload = {**payload, "current_page": int(selected["page"])}
        route.fulfill(
            content_type="text/html",
            body=template.replace(
                original_content, render_history(selected, current_payload)
            ),
        )

    page.route("http://history.test/**", respond)
    page.goto("http://history.test/admin/finanzas/payment-history")
    expect(page.get_by_role("heading", name="Pagado y pendiente")).to_be_visible()
    page.get_by_label("Empleado (solicitante o beneficiario)", exact=True).fill("Ana")
    page.get_by_label("Proveedor", exact=True).fill("Proveedor sintético")
    page.get_by_label("Mes y año", exact=True).fill("2026-10")
    page.get_by_label("Monto mínimo", exact=True).fill("100")
    page.get_by_label("Filtrar por fecha de", exact=True).select_option("pago")
    page.get_by_role("button", name="Filtrar", exact=True).click()
    assert "employee=Ana" in page.url and "month=2026-10" in page.url
    expect(page.get_by_label("Monto mínimo", exact=True)).to_have_value("100")
    page.get_by_role("link", name="Monto", exact=False).click()
    assert "sort=monto" in page.url and "direction=desc" in page.url
    page.get_by_role("link", name="Monto", exact=False).click()
    assert "direction=asc" in page.url
    page.get_by_role("link", name="Siguiente", exact=True).click()
    assert "page=2" in page.url and "employee=Ana" in page.url
    assert "sort=monto" in page.url and "direction=asc" in page.url
    expect(page.get_by_text("Página 2 de 13", exact=False)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    artifacts = Path("/tmp/samchat-payment-history-browser")
    artifacts.mkdir(exist_ok=True)
    page.screenshot(
        path=str(artifacts / f"history-{page.viewport_size['width']}.png"),
        full_page=True,
    )
