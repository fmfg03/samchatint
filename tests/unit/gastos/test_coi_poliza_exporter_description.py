from datetime import datetime

from devnous.gastos.services.coi_poliza_exporter import (
    ExpenseCFDI,
    _expense_description,
    build_coi_poliza_rows,
)


def _expense(**overrides) -> ExpenseCFDI:
    values = {
        "fecha": datetime(2026, 9, 6),
        "total": 40334.64,
        "iva_amount": 0.0,
        "subtotal_amount": 40334.64,
        "concepto": "Hospedaje Fase Nacional LTTB",
        "cuenta_contable": "5300-012-031",
        "cuenta_contrapartida": "1200-001-001",
    }
    values.update(overrides)
    return ExpenseCFDI(**values)


def test_coi_description_uses_accounting_order_and_removes_provider_duplication():
    expense = _expense(
        export_reference="03/269",
        nombre_emisor="ANTONIO GARCES HINOJOZA",
        concepto=(
            "Pago a proveedor: ANTONIO GARCES HINOJOZA - "
            "HOSPEDAJE FASE NACIONAL LTTB"
        ),
        proyecto="SEDE ECATEPEC",
    )

    assert _expense_description(expense) == (
        "03/269 / ANTONIO GARCES HINOJOZA / "
        "HOSPEDAJE FASE NACIONAL LTTB / SEDE ECATEPEC"
    )


def test_coi_description_does_not_emit_empty_fields_or_provider_wrapper_without_cfdi():
    expense = _expense(
        export_reference="03/270",
        concepto="Pago a proveedor: Servicio médico",
    )

    assert _expense_description(expense) == "03/270 / Servicio médico"


def test_coi_header_and_movement_rows_share_compact_description():
    expense = _expense(
        export_reference="03/269",
        nombre_emisor="ANTONIO GARCES HINOJOZA",
        concepto="Pago a proveedor: ANTONIO GARCES HINOJOZA - Hospedaje",
        proyecto="LTTB",
    )

    rows = build_coi_poliza_rows([expense])
    description = "03/269 / ANTONIO GARCES HINOJOZA / Hospedaje / LTTB"

    assert rows[2][2] == description
    assert rows[3][3] == description
    assert rows[4][3] == description
