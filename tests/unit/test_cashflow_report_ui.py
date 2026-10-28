from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from samchat.cashflow.admin_ui import (
    _href,
    _money,
    cashflow_admin_styles,
    export_cashflow_xlsx,
    render_cashflow_filters,
    render_cashflow_planning_html,
)


def payload():
    movement = {
        "id": "bank-1",
        "date": "2026-01-05",
        "account": "••1234",
        "currency": "MXN",
        "direction": "in",
        "amount": Decimal("100.10"),
        "party": "Cliente <uno>",
        "concept": "Patrocinio",
        "project": None,
        "reference": "REF-1",
        "reconciliation": "Conciliado",
        "source": "Banco",
    }
    summary = {
        "actual_cash_in": Decimal("100.10"),
        "actual_cash_out": Decimal("20.05"),
        "actual_cash_net": Decimal("80.05"),
        "opening_balance": None,
        "closing_balance": None,
        "expected_uncollected_income": Decimal("15"),
        "approved_obligations": Decimal("10"),
        "submitted_obligations": Decimal("5"),
        "forecast_net": Decimal("85.05"),
    }
    return {
        "company_id": "company-1",
        "period": {
            "start": "2026-01-01",
            "end": "2026-02-01",
            "cutoff": "2026-02-01",
            "forecast_start": "2026-02-01",
            "forecast_end": "2026-05-01",
        },
        "summary": summary,
        "summary_by_currency": {"MXN": summary},
        "monthly_buckets": [{"key": "2026-01", "currency": "MXN", **summary}],
        "movements": [movement, {**movement, "id": "bank-2", "reference": "REF-2"}],
        "details": {"page": 1, "pages": 2, "total": 2, "rows": [movement]},
        "page_links": {"next": "?year=2026&month=1&page=2#cashflow-movements"},
        "source_status": {
            "bank": {"available": True, "complete": True, "reason": None},
            "budget": {
                "available": False,
                "complete": False,
                "reason": "Moneda no acreditada",
            },
        },
        "source_notes": ["Sin saldo inicial verificado"],
        "obligations": [
            {
                "id": "request-1",
                "reference": "SOL-1",
                "date": None,
                "currency": "MXN",
                "amount": Decimal("5"),
                "state": "enviado",
                "party": "Proveedor",
                "concept": "Servicio",
                "window": "undated",
            }
        ],
        "receivables": [
            {
                "id": "ar-1",
                "reference": "CFDI-1",
                "date": "2025-12-31",
                "currency": "MXN",
                "amount": Decimal("15"),
                "state": "parcial",
                "party": "Cliente",
                "window": "overdue",
            }
        ],
    }


def test_real_report_has_stocks_and_separate_future_with_context():
    html = render_cashflow_planning_html(payload())
    assert html.index("Saldo inicial") < html.index("Orígenes · entradas reales")
    assert html.index("Aplicaciones · salidas reales") < html.index("Saldo final")
    assert html.index("Saldo final") < html.index("Lo que viene")
    assert "$100.10" in html
    assert "N/D" in html
    assert "La variación del período no es saldo disponible" in html
    assert "2026-02-01" in html
    assert "company-1" in html
    assert "Moneda no acreditada" in html


def test_detail_is_paginated_but_export_includes_complete_source_set():
    data = payload()
    html = render_cashflow_planning_html(data)
    assert "REF-1" in html and "REF-2" not in html
    assert "página 1 de 2" in html
    assert "year=2026&amp;month=1&amp;page=2" in html
    workbook = load_workbook(BytesIO(export_cashflow_xlsx(data)))
    bank = list(workbook["Banco"].values)
    assert len(bank) == 3
    assert bank[1][8] == "REF-1"
    assert bank[2][8] == "REF-2"
    assert any(
        row[0] == "Orígenes · entradas reales" and row[3] == 100.10
        for row in workbook["Reporte"].values
    )


def test_export_keeps_unknown_stocks_and_exact_shared_summary_values():
    workbook = load_workbook(BytesIO(export_cashflow_xlsx(payload())))
    rows = list(workbook["Reporte"].values)
    actual = {(r[0], r[2]): r[3] for r in rows if r[2] is not None}
    assert actual[("Saldo inicial", "2026-01")] == "N/D"
    assert actual[("Saldo final", "Acumulado")] == "N/D"
    assert actual[("Variación neta del período", "Acumulado")] == 80.05
    assert actual[("Variación proyectada", "Acumulado")] == 85.05


def test_export_never_sums_stock_balances():
    data = payload()
    data["summary_by_currency"]["MXN"]["opening_balance"] = Decimal("900")
    data["summary_by_currency"]["MXN"]["closing_balance"] = Decimal("980.05")
    data["monthly_buckets"].append(
        {
            "key": "2026-02",
            "currency": "MXN",
            "opening_balance": Decimal("980.05"),
            "closing_balance": Decimal("1000"),
        }
    )
    workbook = load_workbook(BytesIO(export_cashflow_xlsx(data)))
    rows = list(workbook["Reporte"].values)
    accumulated = {r[0]: r[3] for r in rows if r[2] == "Acumulado"}
    assert accumulated["Saldo inicial"] == 900
    assert accumulated["Saldo final"] == 980.05


def test_currency_blocks_remain_separate_without_invented_consolidated_total():
    data = payload()
    data["summary_by_currency"]["USD"] = {
        "actual_cash_in": Decimal("30"),
        "actual_cash_out": Decimal("10"),
        "actual_cash_net": Decimal("20"),
    }
    data["monthly_buckets"].append(
        {
            "key": "2026-01",
            "currency": "USD",
            "actual_cash_in": Decimal("30"),
        }
    )
    html = render_cashflow_planning_html(data)
    assert "Flujo de efectivo · MXN" in html
    assert "Flujo de efectivo · USD" in html
    assert "$130.10" not in html
    workbook = load_workbook(BytesIO(export_cashflow_xlsx(data)))
    accumulated = [
        row
        for row in workbook["Reporte"].values
        if row[0] == "Orígenes · entradas reales" and row[2] == "Acumulado"
    ]
    assert [(r[1], r[3]) for r in accumulated] == [("MXN", 100.1), ("USD", 30)]


def test_untrusted_strings_are_escaped_in_html_and_not_excel_formulas():
    data = payload()
    data["movements"][0]["concept"] = '=HYPERLINK("https://evil.invalid")'
    data["movements"][1]["concept"] = "  +1+1"
    data["source_notes"] = ["<script>alert(1)</script>"]
    data["page_links"]["next"] = "javascript:alert(1)"
    html = render_cashflow_planning_html(data)
    assert "Cliente &lt;uno&gt;" in html
    assert "<script>" not in html
    assert "javascript:" not in html
    workbook = load_workbook(BytesIO(export_cashflow_xlsx(data)))
    assert workbook["Banco"].cell(2, 4).data_type == "s"
    assert workbook["Banco"].cell(2, 4).value.startswith("'=")
    assert workbook["Banco"].cell(3, 4).value.startswith("'  +")


def test_unknown_and_invalid_money_are_not_displayed_as_zero():
    for value in [None, "bad", "NaN", "Infinity", True]:
        assert _money(value) == "N/D"
    assert _money(0) == "$0.00"
    assert _money(Decimal("-1.25")) == "$-1.25"


def test_pending_rows_keep_undated_and_overdue_states_separate():
    html = render_cashflow_planning_html(payload())
    assert "Sin fecha" in html
    assert "Vencido" in html
    assert "aún no son pagos autorizados" in html
    assert "SOL-1" in html and "CFDI-1" in html


def test_report_uses_native_keyboard_details_and_responsive_table_regions():
    html = render_cashflow_planning_html(payload())
    assert "<details open><summary>" in html
    assert 'role="region" tabindex="0"' in html
    assert 'aria-label="Páginas de movimientos"' in html
    assert 'href="#cashflow-report"' in html
    css = cashflow_admin_styles()
    assert "overflow-x:auto" in css
    assert ":focus-visible" in css


def test_detail_navigation_rejects_external_and_browser_normalized_urls():
    for value in [
        "https://evil.invalid",
        "//evil.invalid",
        "/\\evil.invalid",
        "/\n/evil.invalid",
        "javascript:alert(1)",
    ]:
        assert _href(value) == "#cashflow-movements"
    assert _href("?page=2&currency=MXN") == "?page=2&amp;currency=MXN"


def test_filters_keep_credit_days_and_use_only_opaque_account_keys():
    data = payload()
    data["filters"] = {"dias_credito": 45, "cuenta_bancaria": "account-key"}
    data["account_options"] = [{"key": "account-key", "label": "Cuenta ••1234"}]
    html = render_cashflow_filters(data, "/admin/finanzas/cashflow")
    assert 'name="dias_credito" value="45"' in html
    assert '<option value="account-key" selected>' in html
    assert 'method="get"' in html
    assert "Días de crédito CxC" in html


def test_budget_filter_displays_readable_name_and_preserves_selection():
    data = payload()
    data["filters"] = {"budget_version_id": "version-id"}
    data["budget_version_options"] = [
        {
            "id": "version-id",
            "version_name": "Presupuesto aprobado 2026",
            "status": "definitivo",
        }
    ]
    html = render_cashflow_filters(data, "/admin/finanzas/cashflow")
    assert '<option value="version-id" selected>' in html
    assert "Presupuesto aprobado 2026 · definitivo</option>" in html
    assert '<input name="budget_version_id"' not in html
    data["budget_version_options"] = []
    html = render_cashflow_filters(data, "/admin/finanzas/cashflow")
    assert "Versión seleccionada · nombre no disponible" in html
    assert ">version-id<" not in html


def test_unknown_currency_uses_readable_copy_in_report():
    data = payload()
    data["summary_by_currency"] = {"SIN_MONEDA": {"actual_cash_in": None}}
    data["monthly_buckets"] = []
    html = render_cashflow_planning_html(data)
    assert "Moneda no acreditada" in html
    assert "SIN_MONEDA" not in html


def test_provenance_and_missing_source_reasons_use_same_readable_copy_in_excel():
    data = payload()
    data["movements"][0]["source"] = "bank_movements"
    data["source_status"]["ar"] = {
        "available": False,
        "complete": False,
        "reason": "Cobranza no disponible (ProgrammingError)",
    }
    data["source_notes"] = [
        "bank: cobertura del período incompleta (OperationalError)",
        "ar: saldo pendiente no acreditado",
    ]
    html = render_cashflow_planning_html(data)
    assert "Movimiento bancario importado" in html
    assert "Cuentas por cobrar: Incompleta / no acreditada" in html
    assert "Banco: cobertura del período incompleta" in html
    assert "Cuentas por cobrar: saldo pendiente no acreditado" in html
    assert "ProgrammingError" not in html and "OperationalError" not in html
    workbook = load_workbook(BytesIO(export_cashflow_xlsx(data)))
    assert workbook["Banco"].cell(2, 11).value == "Movimiento bancario importado"
    coverage = list(workbook["Cobertura"].values)
    assert any(row[0] == "Cuentas por cobrar" for row in coverage)
    assert any(row[3] == "Cobranza no disponible" for row in coverage)
    assert "ProgrammingError" not in str(coverage)
    assert "OperationalError" not in str(coverage)
    assert data["movements"][0]["source"] == "bank_movements"
