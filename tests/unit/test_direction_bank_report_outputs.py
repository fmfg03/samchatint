"""Verify that Direction consumes the same certified bank values in every output."""

import io

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from test_direction_home import scope, snapshot

from samchat.client_executive.conversation import answer_snapshot
from samchat.client_executive.home_ui import render_home
from samchat.client_executive.report_layouts import report_layouts
from samchat.client_executive.reports import build_report
from samchat.executive.exporter import (
    generate_direction_report_pdf,
    generate_direction_report_xlsx,
)

SOURCE = "Estado de cuenta · evidencia bancaria f402e3c7"
SCOPE = "Saldo de la cuenta •••• 1234"
GAP = "El cierre solicitado excede la cobertura acreditada."
PERIOD = {"start": "2026-06-01", "end": "2026-06-30"}
INTERNAL_ACCOUNT_NUMBER = "8720135698121234"


def bank_report():
    data = snapshot()
    month = {
        **PERIOD,
        "label": "Junio",
        "opening": "0.00",
        "origins_total": "1536.03",
        "applications_total": "128.01",
        "closing": "1408.02",
        "gaps": {},
        "source": SOURCE,
        "scope_label": SCOPE,
        "account_number": INTERNAL_ACCOUNT_NUMBER,
    }
    partial = {
        **month,
        "label": "Julio",
        "start": "2026-07-01",
        "end": "2026-07-31",
        "opening": month["closing"],
        "origins_total": None,
        "applications_total": None,
        "closing": None,
        "gaps": dict.fromkeys(("origins_total", "applications_total", "closing"), GAP),
    }
    data["period"] = PERIOD
    data["reports"] = report_layouts(
        PERIOD, bank={"periods": [month, partial], "gap": None, "revision": "fixture"}
    )
    return data


def test_bank_totals_preserve_cents_and_missing_values_across_direction_outputs():
    data = bank_report()
    cashflow = data["reports"]["cashflow"]
    assert cashflow["bank_revision"] == "fixture"
    assert cashflow["periods"][0]["origins"] == [None] * 6
    assert cashflow["periods"][0]["applications"] == [None] * 6
    assert cashflow["periods"][0]["closing"] == "1.40802"
    assert cashflow["periods"][1]["closing"] is None

    html = render_home(data, scope(), token="synthetic", csrf="synthetic")
    assert 'data-display="1.40802 Miles de MXN"' in html
    assert 'data-display="0.00 Miles de MXN"' in html
    assert f'data-gap="{GAP}"' in html
    assert f'data-source="{SOURCE}"' in html
    assert SCOPE in html and GAP in html
    assert "Abonos bancarios" in html and "Cargos bancarios" in html
    assert INTERNAL_ACCOUNT_NUMBER not in html

    report = build_report(data)
    xlsx = generate_direction_report_xlsx(report)
    sheet = load_workbook(io.BytesIO(xlsx), data_only=True)["EFE"]
    assert [sheet[f"B{i}"].value for i in (6, 8, 15, 22)] == [
        0,
        1.53603,
        0.12801,
        1.40802,
    ]
    assert sheet["B22"].number_format == "#,##0.00###;[Red](#,##0.00###);0.00"
    assert sheet["C22"].value is None
    assert sheet["C29"].value == GAP
    assert sheet["B24"].value == SCOPE and sheet["B25"].value == SOURCE
    assert INTERNAL_ACCOUNT_NUMBER not in str(list(sheet.values))
    assert sheet["B9"].value is None and "clasificación" in sheet["A23"].value

    pdf = generate_direction_report_pdf(report)
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    # The PDF standard Helvetica font encodes its bullet glyph at 0x7F.
    text = text.replace("\x7f", "•")
    assert "Saldo final · 1.40802" in text
    assert "Saldo inicial · 0.00" in text
    assert GAP in text and SOURCE in text and SCOPE in text
    assert INTERNAL_ACCOUNT_NUMBER not in text

    for index, formatted in ((0, "1.40802 Miles de MXN"), (1, "Sin dato")):
        reply = answer_snapshot(
            data,
            "liquidity",
            "¿De dónde viene el saldo?",
            report_cell={
                "report": "cashflow",
                "column": "closing",
                "period": str(index),
            },
        )
        assert formatted in reply["assistant_message"]
        assert (
            SOURCE in reply["assistant_message"] and SCOPE in reply["assistant_message"]
        )
        assert reply["missing_evidence"] == ([GAP] if index else [])
        assert INTERNAL_ACCOUNT_NUMBER not in reply["assistant_message"]


def test_bank_gap_without_period_preserves_missing_workbook_amounts():
    data = snapshot()
    data["reports"] = report_layouts(
        PERIOD, bank={"periods": [], "gap": GAP, "revision": None}
    )
    html = render_home(data, scope(), token="synthetic", csrf="synthetic")
    assert GAP in html
    assert "Abonos bancarios" in html
    assert "2026-06-01 a 2026-06-30" in html
    sheet = load_workbook(
        io.BytesIO(generate_direction_report_xlsx(build_report(data))), data_only=True
    )["EFE"]
    assert sheet["A3"].value == GAP
    assert sheet["B6"].value is None and sheet["G22"].value is None


def test_excel_preserves_exact_large_balance_outside_numeric_precision():
    data = bank_report()
    large_balance = "1234567890123.12345"
    data["reports"]["cashflow"]["periods"][0]["closing"] = large_balance
    sheet = load_workbook(
        io.BytesIO(generate_direction_report_xlsx(build_report(data))), data_only=True
    )["EFE"]
    assert sheet["B22"].value == large_balance
    assert sheet["B22"].number_format == "@"


@pytest.mark.parametrize(
    "periods",
    [
        [{**PERIOD, "start": "2026-07-01"}],
        [{**PERIOD, "start": "fecha-inválida"}],
        [PERIOD] * 7,
    ],
)
def test_bank_report_rejects_ambiguous_period_slots(periods):
    with pytest.raises(ValueError):
        report_layouts(PERIOD, bank={"periods": periods, "gap": GAP})
