import io
import zipfile
from datetime import datetime

from devnous.gastos.services.coi_poliza_exporter import (
    ExpenseCFDI,
    _expense_description,
    build_coi_poliza_rows,
    generate_coi_poliza_zip,
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


def test_expense_report_partidas_share_one_coi_policy_header_and_closure():
    first = _expense(
        export_reference="G-1",
        concepto="Hospedaje",
        poliza_group_key="informe:1",
        poliza_reference="I-26000001",
        poliza_description="Informe de Gastos I-26000001",
    )
    second = _expense(
        export_reference="G-2",
        concepto="Alimentos",
        poliza_group_key="informe:1",
        poliza_reference="I-26000001",
        poliza_description="Informe de Gastos I-26000001",
    )

    rows = build_coi_poliza_rows([first, second])

    headers = [row for row in rows if row[0] == "Eg"]
    closures = [row for row in rows if row[1] == "FIN_PARTIDAS"]
    assert headers == [
        [
            "Eg",
            "1",
            "Informe de Gastos I-26000001",
            "4",
            "",
            "",
            "",
            "",
            "",
        ]
    ]
    assert len(closures) == 1


def test_ungrouped_standalone_expenses_keep_one_policy_each():
    rows = build_coi_poliza_rows(
        [
            _expense(export_reference="G-1"),
            _expense(export_reference="G-2"),
        ]
    )

    assert [row[1] for row in rows if row[0] == "Eg"] == ["1", "2"]
    assert len([row for row in rows if row[1] == "FIN_PARTIDAS"]) == 2


def test_grouped_report_produces_one_workbook_in_zip():
    expenses = [
        _expense(
            export_reference=reference,
            poliza_group_key="informe:1",
            poliza_reference="I-26000001",
            poliza_description="Informe de Gastos I-26000001",
        )
        for reference in ("G-1", "G-2")
    ]

    payload = generate_coi_poliza_zip(expenses)

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["Poliza_COI_I-26000001.xlsx"]


def test_grouped_report_preserves_each_cfdi_block_inside_single_policy():
    expenses = [
        _expense(
            export_reference=reference,
            cfdi_uuid=cfdi_uuid,
            rfc_emisor="AAA010101AAA",
            rfc_receptor="BBB010101BBB",
            poliza_group_key="informe:1",
            poliza_reference="I-26000001",
            poliza_description="Informe de Gastos I-26000001",
        )
        for reference, cfdi_uuid in (("G-1", "UUID-1"), ("G-2", "UUID-2"))
    ]

    rows = build_coi_poliza_rows(expenses)

    assert len([row for row in rows if row[0] == "Eg"]) == 1
    assert len([row for row in rows if row[2] == "INICIO_CFDI"]) == 2
    assert len([row for row in rows if row[2] == "FIN_CFDI"]) == 2
    assert {row[8] for row in rows if row[8]} == {"UUID-1", "UUID-2"}


def test_report_policy_preserves_tax_movements_and_balance():
    expenses = [
        _expense(
            export_reference=f"G-{index}",
            total=116,
            subtotal_amount=100,
            iva_amount=16,
            cuenta_iva="1180",
            poliza_group_key="informe:tax",
            poliza_reference="I-TAX",
            poliza_description="Informe I-TAX",
        )
        for index in (1, 2)
    ]
    rows = build_coi_poliza_rows(expenses)
    movements = [row for row in rows if row[4] == "1"]

    assert len([row for row in rows if row[0] == "Eg"]) == 1
    assert len([row for row in movements if row[1] == "1180"]) == 2
    assert sum(float(row[5] or 0) for row in movements) == 232
    assert sum(float(row[6] or 0) for row in movements) == 232


def test_standalone_zip_preserves_references_and_duplicate_filename_suffix():
    payload = generate_coi_poliza_zip(
        [
            _expense(export_reference="G-1"),
            _expense(export_reference="G-1"),
        ]
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["Poliza_COI_G-1.xlsx", "Poliza_COI_002_G-1.xlsx"]
