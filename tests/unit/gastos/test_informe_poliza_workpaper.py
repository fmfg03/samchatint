"""DR paper balance and provenance from the accountant's sample layout."""

from io import BytesIO

import pytest
from openpyxl import load_workbook

from devnous.gastos.services.informe_poliza_workpaper import (
    InformeWorkpaperExpense,
    generate_informe_poliza_workpaper,
)


def _expense(**changes):
    values = dict(
        source_id="expense-1",
        reference="G-1",
        date="2026-09-28",
        description="Gasolina / F-1",
        amount=700.0,
        vat=94.12,
        expense_account="5100-006-004",
        vat_account="1200-001-001",
        counterpart_account="1170-001-009",
        company_amex=False,
        cfdi_uuid="CFDI-1",
    )
    values.update(changes)
    return InformeWorkpaperExpense(**values)


def test_workpaper_has_one_dr_with_live_balance_and_traceable_expenses():
    payload = generate_informe_poliza_workpaper(
        [_expense(), _expense(
            source_id="expense-2", reference="G-2", amount=200.0,
            vat=None, expense_account="", counterpart_account="",
        )],
        reference="REF 3", title="Gastos Edgar", currency="MXN",
    )
    wb = load_workbook(BytesIO(payload))
    dr = wb["Papel DR"]
    assert (dr["A3"].value, dr["B3"].value, dr["D3"].value) == ("Dr", 3, 4)
    assert dr["F4"].value == 605.88
    assert dr["F5"].value == 94.12
    assert dr["F6"].value == 200.0
    assert dr["B6"].value is None
    assert dr["G7"].value == 900.0
    assert dr["B7"].value is None  # Mixed/missing counterparts are reviewed.
    assert dr["B8"].value == "FIN_PARTIDAS"
    assert wb["Cuadre"]["B5"].value == "=SUM('Papel DR'!F4:F7)"
    assert wb["Cuadre"]["B6"].value == "=SUM('Papel DR'!G4:G7)"
    assert wb["Cuadre"]["B7"].value == "=ROUND(B5-B6,2)"
    assert "COUNTBLANK" in wb["Cuadre"]["B8"].value
    assert "B8=0" in wb["Cuadre"]["B9"].value
    assert [wb["Origen y revisión"].cell(i, 1).value for i in (2, 3)] == [
        "expense-1", "expense-2",
    ]


def test_workpaper_keeps_amex_liability_separate_and_does_not_guess_iva():
    payload = generate_informe_poliza_workpaper(
        [_expense(), _expense(
            source_id="expense-2", amount=250, vat=None,
            company_amex=True, counterpart_account="2120-002-062",
        )],
        reference="REF 3", title="Mixto", currency="MXN",
    )
    dr = load_workbook(BytesIO(payload))["Papel DR"]
    assert dr["F6"].value == 250.0
    assert dr["G7"].value == 700.0
    assert dr["G8"].value == 250.0
    assert dr["B7"].value == "1170-001-009"
    assert dr["B8"].value == "2120-002-062"


@pytest.mark.parametrize("change", [dict(amount=0), dict(vat=800)])
def test_invalid_amounts_do_not_produce_a_plausible_dr(change):
    with pytest.raises(ValueError, match="Importe o IVA inválido"):
        generate_informe_poliza_workpaper(
            [_expense(**change)], reference="REF 3", title="X", currency="MXN"
        )
