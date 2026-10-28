"""Local synthetic browser evidence over the actual shared cashflow read model.

No production server, identity or data is used. Requests are intercepted locally;
the source fixture is projected again on every detail or pagination navigation.
"""

import os
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from playwright.sync_api import Page

from samchat.cashflow.admin_ui import (
    cashflow_admin_styles,
    render_cashflow_filters,
    render_cashflow_planning_html,
)
from samchat.cashflow.service import build_cashflow_report

OUT = Path(os.environ.get("SAMCHAT_BROWSER_ARTIFACT_DIR", "/tmp/cashflow001-browser"))


def _sources():
    return {
        "company_id": "company-authorized",
        "company_label": "Empresa de prueba autorizada",
        "source_status": {
            key: {"available": True, "complete": True, "reason": None}
            for key in ("bank", "obligations", "ar", "budget")
        },
        "bank_rows": [
            {
                "id": f"bank-{index}",
                "fecha": date(2026, 1, 5),
                "cuenta_bancaria": "123456781234",
                "currency": "MXN",
                "signo": "+" if index % 2 == 0 else "-",
                "importe": "100.10" if index % 2 == 0 else "20.05",
                "nombre_ordenante": "Cliente <prueba>",
                "nombre_beneficiario": "Proveedor de prueba",
                "concepto_banco": "<script>window.untrusted = true</script>",
                "referencia_bancaria": f"REF-{index}",
            }
            for index in range(4)
        ],
        "documents": [
            {
                "id": "request-1",
                "currency": "MXN",
                "estado": "enviado",
                "payable_amount": "5",
                "numero_referencia": "SOL-1",
            }
        ],
        "ar_payload": {"issued_linked": [], "issued_unlinked": []},
    }


def _report(query):
    def value(key, default=None):
        return query.get(key, [default])[0]

    data = build_cashflow_report(
        _sources(),
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        cutoff=date(2026, 2, 1),
        forecast_end=date(2026, 3, 1),
        page=int(value("page", "1")),
        page_size=1,
        detail_currency=value("detail_currency"),
        detail_period=value("detail_period"),
        detail_direction=value("detail_direction"),
    )

    def link(**changes):
        params = {key: items[0] for key, items in query.items()}
        params.update(changes)
        return "/admin/finanzas/cashflow?" + urlencode(params) + "#cashflow-movements"

    data["filters"] = {
        "cuenta_bancaria": value("cuenta_bancaria", "all"),
        "proyecto_scope": value("proyecto_scope", "all"),
        "currency": value("currency", "all"),
        "dias_credito": value("dias_credito", "30"),
        "horizon_days": value("horizon_days", "30"),
        "horizon_months": value("horizon_months", "1"),
    }
    data["account_options"] = [
        {
            "key": "account-key",
            "label": "Cuenta ••1234",
        }
    ]
    data["project_options"] = [{"key": "project-key", "label": "Proyecto"}]
    data["page_links"] = {}
    if data["details"]["page"] > 1:
        data["page_links"]["previous"] = link(page=data["details"]["page"] - 1)
    if data["details"]["page"] < data["details"]["pages"]:
        data["page_links"]["next"] = link(page=data["details"]["page"] + 1)
    data["detail_links"] = {
        f"MXN:{period}:{direction}": link(
            detail_currency="MXN",
            detail_period="2026-01" if period != "total" else "",
            detail_direction=direction,
            page=1,
        )
        for period in ("2026-01", "total")
        for direction in ("in", "out")
    }
    return data


def _document(data):
    return (
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<style>" + cashflow_admin_styles() + "</style></head><body>"
        '<main class="cashflow-shell">'
        + render_cashflow_filters(data, "/admin/finanzas/cashflow")
        + render_cashflow_planning_html(data)
        + "</main></body></html>"
    )


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_report_details_pagination_context_and_mobile(page: Page, width, height):
    page.set_viewport_size({"width": width, "height": height})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        query = parse_qs(urlsplit(route.request.url).query)
        route.fulfill(content_type="text/html", body=_document(_report(query)))

    page.route("http://cashflow.test/**", serve)
    page.goto(
        "http://cashflow.test/admin/finanzas/cashflow?year=2026&month=1"
        "&currency=MXN&dias_credito=45"
    )
    assert page.get_by_label("Días de crédito CxC").input_value() == "45"
    assert page.get_by_label("Moneda", exact=True).input_value() == "MXN"
    assert page.get_by_text("Empresa de prueba autorizada", exact=True).is_visible()
    assert page.locator("#cashflow-report").is_visible()
    assert page.get_by_text("$200.20", exact=True).count() >= 1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate("window.untrusted === undefined")

    summary = page.locator("#cashflow-movements summary")
    summary.focus()
    page.keyboard.press("Enter")
    assert not page.locator("#cashflow-movements details").evaluate("el => el.open")
    page.keyboard.press("Enter")
    assert page.locator("#cashflow-movements details").evaluate("el => el.open")
    assert "Cliente <prueba>" in page.locator("#cashflow-movements").inner_text()
    assert "123456781234" not in page.content()

    page.get_by_role("link", name="Siguiente", exact=True).click()
    query = parse_qs(urlsplit(page.url).query)
    assert query["year"] == ["2026"] and query["month"] == ["1"]
    assert query["currency"] == ["MXN"] and query["page"] == ["2"]
    assert "REF-1" in page.locator("#cashflow-movements").inner_text()
    page.get_by_role("link", name="Volver al reporte", exact=True).click()
    assert urlsplit(page.url).fragment == "cashflow-report"
    assert parse_qs(urlsplit(page.url).query) == query

    page.get_by_role(
        "link",
        name="Ver detalle de Orígenes · entradas reales · 2026-01 · MXN",
        exact=True,
    ).click()
    query = parse_qs(urlsplit(page.url).query)
    assert query["detail_direction"] == ["in"]
    assert query["detail_period"] == ["2026-01"]
    assert query["page"] == ["1"]
    assert "REF-0" in page.locator("#cashflow-movements").inner_text()
    assert "REF-1" not in page.locator("#cashflow-movements").inner_text()
    page.get_by_role("link", name="Siguiente", exact=True).click()
    assert "REF-2" in page.locator("#cashflow-movements").inner_text()
    assert parse_qs(urlsplit(page.url).query)["detail_direction"] == ["in"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.get_by_label("Días de crédito CxC").input_value() == "45"
    page.get_by_role("button", name="Actualizar reporte", exact=True).click()
    query = parse_qs(urlsplit(page.url).query)
    assert query["dias_credito"] == ["45"]
    assert query["currency"] == ["MXN"]
    assert query["year"] == ["2026"] and query["month"] == ["1"]
    assert "detail_direction" not in query
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"cashflow-{width}.png"), full_page=True)
