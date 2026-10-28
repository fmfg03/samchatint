"""HTTP acceptance: guards, context, period and XLSX use the same report."""

import inspect
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from devnous.gastos.routes import admin_routes, user_routes
from devnous.gastos.routes.dependencies import get_db_session
from samchat.cashflow import service


@pytest.fixture
def client():
    app = FastAPI()
    for owner, path in (
        (user_routes, "/admin/contabilidad/cash-flow"),
        (user_routes, "/admin/contabilidad/cash-flow/export.xlsx"),
        (admin_routes, "/admin/finanzas/cashflow"),
    ):
        route = next(r for r in owner.router.routes if getattr(r, "path", None) == path)
        app.add_api_route(
            path,
            route.endpoint,
            methods=list(route.methods),
            response_class=route.response_class,
        )
        guard = (
            inspect.signature(route.endpoint)
            .parameters["current_empleado"]
            .default.dependency
        )
        app.dependency_overrides[guard] = lambda: SimpleNamespace(id="actor")
    app.dependency_overrides[get_db_session] = lambda: object()
    app.dependency_overrides[user_routes.get_db_session] = lambda: object()
    app.dependency_overrides[admin_routes.get_db_session] = lambda: object()
    data = dict(
        bank_rows=[
            dict(
                id="bank-1",
                fecha="2026-01-05",
                signo="+",
                importe="100.12",
                currency="MXN",
                cuenta_bancaria="secret-account-1234",
                nombre_ordenante="Cliente",
            )
        ],
        documents=[],
        ar_payload={},
        source_status={
            k: dict(available=True, complete=True)
            for k in ("bank", "obligations", "ar", "budget")
        },
    )
    with (
        patch.object(
            service, "load_cashflow_sources", new=AsyncMock(return_value=data)
        ) as loader,
        patch.object(user_routes, "render_top_navigation", return_value=""),
        patch.object(user_routes, "_contabilidad_subnav", return_value=""),
        patch.object(admin_routes, "render_admin_navigation", return_value=""),
        TestClient(app) as http,
    ):
        yield http, loader, app


def test_html_and_excel_same_period_currency_and_amount(client):
    http, loader, _ = client
    query = (
        "?year=2026&month=1&horizon_months=1&horizon_days=90"
        "&periodo=mensual&currency=MXN&dias_credito=45"
    )
    html = http.get("/admin/contabilidad/cash-flow" + query)
    html_source = loader.await_args.kwargs
    excel = http.get("/admin/contabilidad/cash-flow/export.xlsx" + query)
    assert html.status_code == excel.status_code == 200
    assert html_source == loader.await_args.kwargs
    assert "$100.12" in html.text
    wb = load_workbook(BytesIO(excel.content))
    assert wb["Banco"].max_row == 2
    assert wb["Banco"]["G2"].value == 100.12
    assert html_source["credit_days"] == 45
    assert html_source["forecast_end"].month > 2
    assert "secret-account" not in html.text
    assert "secret-account" not in str(list(wb["Banco"].values))


def test_finance_view_and_export_preserve_monthly_horizon(client):
    http, loader, _ = client
    query = "?year=2026&month=1&horizon_months=3&periodo=mensual&currency=MXN"
    response = http.get("/admin/finanzas/cashflow" + query)
    expected = loader.await_args.kwargs
    assert response.status_code == 200
    assert "horizon_months=3" in response.text
    excel = http.get("/admin/contabilidad/cash-flow/export.xlsx" + query)
    assert excel.status_code == 200
    assert expected == loader.await_args.kwargs


def test_invalid_filter_is_rejected_before_source_read(client):
    http, loader, _ = client
    assert http.get("/admin/contabilidad/cash-flow?month=13").status_code == 422
    assert http.get("/admin/contabilidad/cash-flow?currency=unknown").status_code == 422
    loader.assert_not_awaited()


def test_original_finance_authorization_dependency_is_still_enforced(client):
    http, loader, app = client
    route = next(r for r in app.routes if r.path == "/admin/contabilidad/cash-flow")
    guard = (
        inspect.signature(route.endpoint)
        .parameters["current_empleado"]
        .default.dependency
    )

    def deny():
        raise HTTPException(403, "denied")

    app.dependency_overrides[guard] = deny
    assert http.get("/admin/contabilidad/cash-flow").status_code == 403
    loader.assert_not_awaited()


def test_requested_uninstalled_company_activation_fails_closed(client, monkeypatch):
    http, loader, _ = client
    monkeypatch.setenv("SAMCHAT_MULTIEMPRESA_ENABLED", "true")
    assert http.get("/admin/contabilidad/cash-flow").status_code == 503
    loader.assert_not_awaited()


def test_installed_scope_exception_preserves_denial_status(client, monkeypatch):
    import sys
    from types import ModuleType

    http, loader, _ = client
    module_name = "devnous.gastos.services.company_scope_service"
    owner = ModuleType(module_name)

    class CompanyScopeError(Exception):
        def __init__(self, code, status_code=403):
            self.code = code
            self.status_code = status_code
            super().__init__(code)

    owner.CompanyScopeError = CompanyScopeError
    monkeypatch.setitem(sys.modules, module_name, owner)
    loader.side_effect = CompanyScopeError("company_selection_denied", 403)
    response = http.get("/admin/contabilidad/cash-flow")
    assert response.status_code == 403
    assert response.json()["detail"] == "company_selection_denied"


def test_pagination_preserves_selected_page_size_without_skipping_rows(client):
    import re
    from html import unescape

    http, loader, _ = client
    row = loader.return_value["bank_rows"][0]
    loader.return_value["bank_rows"] = [
        {**row, "id": f"bank-{index:03d}"} for index in range(60)
    ]
    response = http.get("/admin/finanzas/cashflow?year=2026&month=1&limit=10")
    assert response.status_code == 200
    assert "bank-009" in response.text and "bank-010" not in response.text
    match = re.search(r'href="([^"]+)">Siguiente</a>', response.text)
    assert match is not None
    target = unescape(match.group(1))
    assert "limit=10" in target and "page=2" in target
    page_two = http.get(target)
    assert page_two.status_code == 200
    assert "bank-010" in page_two.text and "bank-019" in page_two.text
    assert "bank-020" not in page_two.text


def test_uncaptured_payables_risk_is_explicitly_unavailable(client):
    http, _, _ = client
    response = http.get("/admin/contabilidad/cash-flow?year=2026&month=1")
    assert response.status_code == 200
    assert "CxP sin captura: N/D" in response.text
    assert "Abrir Cuentas por pagar" in response.text
    assert "/admin/contabilidad/cuentas-por-pagar" in response.text
