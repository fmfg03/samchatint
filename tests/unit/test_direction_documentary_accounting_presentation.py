"""Documentary account evidence stays distinct from fiscal/project totals."""

from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fitz
import pytest
from openpyxl import load_workbook

import devnous.gastos  # noqa: F401 - bootstrap the live runtime's model registry
from samchat.client_executive import home, home_ui, reports, ui
from samchat.executive.exporter import (
    generate_direction_report_pdf,
    generate_direction_report_xlsx,
)

TID = "67c556df-f953-4f99-8cab-87ed4d111b05"
ACTOR = "10000000-0000-0000-0000-000000000431"


def documentary():
    return {
        "source_status": "partial",
        "company_code": "01",
        "fiscal_year": 2026,
        "currency": None,
        "tax_basis": None,
        "source_sha256": "a" * 64,
        "coverage": {
            "trial_balance_months": [8],
            "policy_months": [8, 9],
            "withheld_months": [{"month_num": 3, "reason": "Conciliación"}],
        },
        "gaps": ["Moneda y base fiscal por acreditar"],
        "accounts": [
            {
                "account_code": "5300-010-001",
                "account_name": "<script>Cuenta compartida</script>",
                "classification": "shared_active",
                "attribution_note": "Cuenta compartida; reparto pendiente",
                "exclusive_attribution": False,
                "has_active_concept": False,
                "evidence": [
                    {
                        "filename": "/private/BGPSP.xls",
                        "sheet": "AGOSTO",
                        "row_number": 479,
                    }
                ],
                "trial_balance_periods": [
                    {
                        "month": 8,
                        "debits": "0.00",
                        "credits": "0.00",
                        "closing_balance": "0.00",
                        "source_sha256": "b" * 64,
                    }
                ],
                "policy_periods": [
                    {
                        "month": 9,
                        "debits": "10.25",
                        "credits": "1.00",
                        "registered_net": "9.25",
                        "source_sha256": "c" * 64,
                        "quality_flags": ["missing_summary_marker"],
                    }
                ],
            },
            {
                "account_code": "5300-010-020",
                "account_name": "Año anterior",
                "classification": "prior_year",
                "exclusive_attribution": False,
                "trial_balance_periods": [],
                "policy_periods": [],
            },
        ],
    }


def report():
    return {
        "title": "Informe para consejo",
        "snapshot_id": "snapshot",
        "cut": "2026-10-30T12:00:00Z",
        "period": {"start": "2026-01-01", "end": "2026-10-30"},
        "scope": "Copa",
        "conclusion": "Validación pendiente",
        "validation": "Pendiente de Finanzas",
        "method": "Lecturas independientes",
        "indicators": [],
        "recommendations": [],
        "risks": [],
        "gaps": [],
        "tournaments": [
            {
                "id": TID,
                "name": "Copa",
                "values": {"budget": "100", "actual": "40"},
                "budget_actual_comparison": {
                    "status": "unavailable",
                    "reason": "Base común por acreditar",
                },
                "accounting": {
                    "source_status": "unavailable",
                    "documentary": documentary(),
                },
            }
        ],
    }


@pytest.mark.parametrize("raw", [None, "", True, "NaN", "Infinity", "bad"])
def test_unavailable_recorded_amounts_never_become_zero_or_currency(raw):
    assert ui.registered_amount(raw) == "N/D"


def test_recorded_amount_keeps_explicit_zero_and_decimal_precision():
    assert ui.registered_amount("0.00") == "0.00"
    assert ui.registered_amount("1234567890.23") == "1,234,567,890.23"


def test_documentary_rows_keep_families_missing_periods_and_catalogue_gaps():
    rows = ui.documentary_accounting_rows(documentary())
    assert len(rows) == 3
    assert [(r["source_family"], r.get("month")) for r in rows] == [
        ("Balanza oficial", 8),
        ("Pólizas admitidas", 9),
        ("Sin periodos admitidos", None),
    ]
    assert rows[0]["registered_net"] is None
    assert rows[1]["closing_balance"] is None
    assert "Sin partida presupuestal activa" in rows[0]["attribution_note"]
    assert rows[0]["evidence"] == "BGPSP.xls · AGOSTO · fila 479"


def test_documentary_html_survives_strict_unavailability_without_fx_or_attribution():
    html = ui._accounting_detail(report()["tournaments"][0])
    assert "Contabilidad registrada" in html
    assert "Cuenta compartida; reparto pendiente" in html
    assert "año anterior" in html
    assert "sin conversión ni variación" in html
    assert "Moneda y base fiscal contables: no acreditadas" in html
    assert "Meses retenidos: 3" in html
    assert "0.00" in html and "10.25" in html and "N/D" in html
    assert "$" not in html and "MXN" not in html
    assert "<script>Cuenta" not in html and "&lt;script&gt;Cuenta" in html
    assert "missing_summary_marker" not in html
    assert "Advertencia de formato" in html


def test_unavailable_source_keeps_catalogue_and_missing_amounts_explicit():
    data = documentary()
    data["source_status"] = "unavailable"
    data["reason"] = "Fuente seleccionada no disponible"
    for account in data["accounts"]:
        account["trial_balance_periods"] = []
        account["policy_periods"] = []
    html = ui.render_documentary_accounting(data)
    assert "Fuente seleccionada no disponible" in html
    assert "Sin periodos admitidos" in html
    assert "0.00" not in html


def test_legacy_comparison_guard_prevents_fallback_amounts_and_percentages():
    card = {
        "budget": "100",
        "actual": "40",
        "committed": "50",
        "paid": "30",
        "available": None,
        "projected": "120",
        "budget_actual_comparison": {
            "status": "unavailable",
            "reason": "Base común por acreditar",
        },
        "budget_breakdowns": {
            "by_concept": [
                {"label": "Partida", "budget_total": 100, "actual_total": 40}
            ],
            "by_phase": [{"label": "Fase", "budget_total": 100, "actual_total": 40}],
        },
    }
    html = ui._budget_kpis(card) + ui._budget_detail(card)
    assert "$40.00" in html and "$50.00" in html and "$30.00" in html
    assert "$120.00" not in html
    assert "40%" not in html and "50%" not in html
    assert "Base común por acreditar" in html


def test_report_html_and_workbook_use_same_unconverted_documentary_rows():
    data = report()
    html = reports.render_report(data)
    book = load_workbook(BytesIO(generate_direction_report_xlsx(data)))
    assert '<details class="documentary-accounts" open' in html
    assert "10.25" in html and "9.25" in html
    recorded = book["Contabilidad registrada"]
    assert recorded["I6"].value == 0
    assert recorded["K6"].value is None
    assert recorded["I7"].value == 10.25
    assert recorded["K7"].value == 9.25
    assert recorded["L7"].value is None
    assert recorded["I8"].value is None
    assert "$" not in recorded["I7"].number_format
    assert not book["Comparación"]._charts
    assert book["Fuentes contables"]["G2"].value == "3"
    assert "MXN" not in " ".join(str(c.value or "") for row in recorded for c in row)


def test_documentary_export_escapes_formula_labels_and_leaves_other_charts_unchanged():
    data = report()
    data["tournaments"][0]["accounting"]["documentary"]["accounts"][0][
        "account_name"
    ] = "=1+1"
    data["tournaments"].append(
        {"id": "other", "name": "Otro", "values": {"budget": "20", "actual": "10"}}
    )
    book = load_workbook(BytesIO(generate_direction_report_xlsx(data)), data_only=False)
    assert book["Contabilidad registrada"]["E6"].data_type == "s"
    assert book["Contabilidad registrada"]["E6"].value == "=1+1"
    assert len(book["Comparación"]._charts) == 1


def test_pdf_honors_comparison_guard_and_preserves_unconverted_account_numbers():
    pdf = fitz.open(stream=generate_direction_report_pdf(report()), filetype="pdf")
    text = "\n".join(page.get_text() for page in pdf)
    assert "Base común por acreditar" in text
    assert "10.25" in text and "9.25" in text and "0.00" in text
    assert "Moneda y base fiscal contables: no acreditadas" in text
    assert "Cuenta compartida; reparto pendiente" in text
    assert "Advertencia de formato" in text
    assert "$10.25" not in text and "$9.25" not in text
    bar_colors = {(0.541, 0.667, 0.706), (0.027, 0.522, 0.565)}
    for page in pdf:
        for drawing in page.get_drawings():
            color = drawing.get("fill")
            assert color is None or tuple(round(v, 3) for v in color) not in bar_colors


@pytest.mark.asyncio
@pytest.mark.parametrize("finance_allowed", [False, True])
async def test_home_reads_only_authorized_documentary_scope_and_blocks_false_forecast(
    monkeypatch, finance_allowed
):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 30, 12, tzinfo=tz)

    monkeypatch.setattr(home, "datetime", Clock)
    scope = {
        "portfolio_ids": [],
        "portfolio_id": None,
        "tournament_id": None,
        "tournaments": [{"id": TID, "name": "Copa"}],
        "selected": [{"id": TID, "name": "Copa"}],
        "portfolios": [],
    }
    monkeypatch.setattr(home, "resolve_scope", AsyncMock(return_value=scope))
    calls = []

    async def read(_session, loader, **kwargs):
        calls.append((loader, kwargs))
        if loader is home.build_executive_facts:
            return {
                "by_tournament": {
                    TID: {
                        "values": {
                            "actual": "40",
                            "committed": "50" if finance_allowed else None,
                            "paid": "30" if finance_allowed else None,
                        },
                        "gaps": {},
                    }
                }
            }
        if loader is home.service._build_direction_budget_snapshot:
            return {
                "source": "budget_db",
                "version": {"status": "approved", "id": "version"},
                "summary": {
                    "budget_total": 100,
                    "actual_total": 40,
                    "committed_total": 50,
                    "paid_total": 30,
                },
                "forecast": {"projected_close_total": 120},
            }
        assert loader is home.service.build_tournament_accounting
        assert kwargs == {
            "tournament_id": TID,
            "edition_year": 2026,
            "budget_version_id": "version",
        }
        return {"source_status": "unavailable", "documentary": documentary()}

    monkeypatch.setattr(home, "_optional_read", read)
    monkeypatch.setattr(home, "payment_values", AsyncMock(return_value={}))
    monkeypatch.setattr(
        home, "receivable_values", AsyncMock(return_value={"value": None, "gaps": []})
    )
    monkeypatch.setattr(
        home.service, "dispatch_registration_snapshot", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        home.service, "_build_operational_dossier", AsyncMock(return_value={})
    )
    got, resolved = await home.build_home(
        SimpleNamespace(),
        actor=ACTOR,
        superadmin=True,
        year=2026,
        start=date(2026, 1, 1),
        end=date(2026, 10, 30),
        source_access={"budget": True, "finance": finance_allowed},
    )
    row = got["tournaments"][0]
    assert row["values"]["actual"] == "40.00"
    assert row["values"]["forecast"] is None and row["values"]["deviation"] is None
    assert row["budget_actual_comparison"]["status"] == "unavailable"
    accounting_calls = [
        c for c in calls if c[0] is home.service.build_tournament_accounting
    ]
    assert len(accounting_calls) == int(finance_allowed)
    html = home_ui.render_home(got, resolved, token="signed", csrf="csrf")
    assert ("Contabilidad registrada" in html) is finance_allowed
    assert 'class="home-track"' not in html
