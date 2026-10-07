import io
import zipfile
from datetime import datetime
from types import SimpleNamespace

from openpyxl import load_workbook

from devnous.gastos.services.coi_poliza_exporter import (
    ExpenseCFDI,
    _expense_description,
    build_coi_poliza_rows,
    generate_coi_poliza_xlsx,
    generate_coi_poliza_zip,
)
from devnous.gastos.services.documento_semantics import (
    effective_document_project_name,
)
from devnous.gastos.services.expense_coi_export_service import (
    group_expense_cfdis_for_document,
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
            poliza_document_id="informe-1",
            poliza_operation_reference="104",
            poliza_party_name="Ana Pérez",
            poliza_context_description="Copa Telmex 2026",
        )
        for reference in ("G-1", "G-2")
    ]

    payload = generate_coi_poliza_zip(expenses)

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["Poliza_COI_I-26000001.xlsx"]
        workbook = load_workbook(io.BytesIO(archive.read(archive.namelist()[0])))
    sheet = workbook["Poliza COI"]
    assert [sheet[cell].value for cell in ("C2", "D2", "E2")] == [None] * 3
    assert sheet["C3"].value == (
        "104 / Ana Pérez / Copa Telmex 2026 / Informe de Gastos I-26000001"
    )


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


def _coi_sheet(expenses):
    workbook = load_workbook(io.BytesIO(generate_coi_poliza_xlsx(expenses)))
    return workbook["Poliza COI"]


def test_effective_document_project_name_handles_none_and_manual_project():
    assert (
        effective_document_project_name(None, fallback="Sin proyecto")
        == "Sin proyecto"
    )
    assert (
        effective_document_project_name(SimpleNamespace(proyecto_otro="  Gira Norte  "))
        == "Gira Norte"
    )


def test_solicitud_xlsx_prefixes_c3_with_operation_provider_and_tournament():
    expense = _expense(export_reference="O-26000001")
    document = SimpleNamespace(
        id="solicitud-1",
        tipo="SOLICITUD",
        referencia_operaciones="104",
        proveedor_cliente=SimpleNamespace(nombre="Servicios Deportivos SA de CV"),
        beneficiario_empleado=None,
        beneficiario_proveedor_cliente=None,
        empleado=None,
        torneo=SimpleNamespace(name="Copa Telmex 2026"),
        proyecto_otro=None,
        cuenta_gastos=None,
    )

    sheet = _coi_sheet(group_expense_cfdis_for_document([expense], document))

    assert sheet["A2"].value == "|||"
    assert [sheet[cell].value for cell in ("C2", "D2", "E2")] == [None] * 3
    assert sheet["A3"].value == "Eg"
    assert sheet["C3"].value == (
        "104 / Servicios Deportivos SA de CV / Copa Telmex 2026 / "
        "O-26000001 / Hospedaje Fase Nacional LTTB"
    )


def test_solicitud_xlsx_uses_beneficiary_when_project_is_missing():
    expense = _expense(export_reference="O-26000002")
    document = SimpleNamespace(
        id="solicitud-2",
        tipo="SOLICITUD",
        referencia_operaciones="107",
        proveedor_cliente=SimpleNamespace(nombre="Proveedor Dos"),
        beneficiario_empleado=SimpleNamespace(nombre="Beneficiaria Dos"),
        beneficiario_proveedor_cliente=None,
        empleado=None,
        torneo=None,
        proyecto_otro=None,
        cuenta_gastos=None,
    )

    sheet = _coi_sheet(group_expense_cfdis_for_document([expense], document))

    assert sheet["C3"].value == (
        "107 / Proveedor Dos / Beneficiaria Dos / "
        "O-26000002 / Hospedaje Fase Nacional LTTB"
    )


def test_informe_xlsx_uses_beneficiary_and_expense_reason_fallback():
    expenses = [_expense(export_reference="G-1"), _expense(export_reference="G-2")]
    document = SimpleNamespace(
        id="informe-1",
        tipo="INFORME",
        numero_referencia="I-26000001",
        referencia_operaciones="105",
        proveedor_cliente=None,
        beneficiario_empleado=SimpleNamespace(nombre="Ana Pérez"),
        beneficiario_proveedor_cliente=None,
        empleado=SimpleNamespace(nombre="Solicitante"),
        torneo=None,
        proyecto_otro=None,
        cuenta_gastos=SimpleNamespace(
            nombre="Viáticos para eliminatoria nacional",
            torneo=None,
            beneficiario_empleado=None,
            beneficiario_proveedor_cliente=None,
            empleado=None,
        ),
    )

    sheet = _coi_sheet(group_expense_cfdis_for_document(expenses, document))

    assert [sheet[cell].value for cell in ("C2", "D2", "E2")] == [None] * 3
    assert sheet["C3"].value == (
        "105 / Ana Pérez / Viáticos para eliminatoria nacional / "
        "Informe de Gastos I-26000001"
    )
    assert len([row for row in sheet.iter_rows() if row[0].value == "Eg"]) == 1


def test_mixed_document_xlsx_enriches_each_policy_description_independently():
    first = _expense(
        poliza_document_id="documento-1",
        poliza_operation_reference="104",
        poliza_party_name="Proveedor Uno",
        poliza_context_description="Torneo Uno",
    )
    second = _expense(
        poliza_document_id="documento-2",
        poliza_operation_reference="105",
        poliza_party_name="Beneficiario Dos",
        poliza_context_description="Torneo Dos",
    )

    sheet = _coi_sheet([first, second])

    assert [sheet[cell].value for cell in ("C2", "D2", "E2")] == [None] * 3
    assert [
        row[2].value for row in sheet.iter_rows() if row[0].value == "Eg"
    ] == [
        "104 / Proveedor Uno / Torneo Uno / Hospedaje Fase Nacional LTTB",
        "105 / Beneficiario Dos / Torneo Dos / Hospedaje Fase Nacional LTTB",
    ]


def test_group_with_conflicting_document_metadata_is_marked_for_review():
    expenses = [
        _expense(
            poliza_group_key="informe:1",
            poliza_description="Informe de Gastos I-26000001",
            poliza_document_id=document_id,
            poliza_operation_reference=operation_reference,
        )
        for document_id, operation_reference in (
            ("documento-1", "104"),
            ("documento-2", "105"),
        )
    ]

    sheet = _coi_sheet(expenses)

    assert sheet["C3"].value == (
        "Múltiples documentos / Informe de Gastos I-26000001"
    )


def test_metadata_free_policy_keeps_its_existing_description():
    document_expense = _expense(
        poliza_document_id="documento-1",
        poliza_operation_reference="104",
        poliza_party_name="Proveedor Uno",
        poliza_context_description="Torneo Uno",
    )
    standalone_expense = _expense(export_reference="G-SIN-DOCUMENTO")

    sheet = _coi_sheet([document_expense, standalone_expense])

    assert [
        row[2].value for row in sheet.iter_rows() if row[0].value == "Eg"
    ] == [
        "104 / Proveedor Uno / Torneo Uno / Hospedaje Fase Nacional LTTB",
        "G-SIN-DOCUMENTO / Hospedaje Fase Nacional LTTB",
    ]


def test_coi_metadata_and_manifest_neutralize_spreadsheet_formulas():
    expense = _expense(
        poliza_document_id="documento-1",
        poliza_operation_reference="=1+1",
        poliza_party_name="+Proveedor",
        poliza_context_description="@Proyecto",
    )
    workbook = load_workbook(
        io.BytesIO(
            generate_coi_poliza_xlsx(
                [expense],
                lote_manifest_rows=[
                    ["referencia_operaciones", "beneficiario_razon_social"],
                    ["=1+1", "+Proveedor"],
                ],
            )
        )
    )

    sheet = workbook["Poliza COI"]
    assert [sheet[cell].value for cell in ("C2", "D2", "E2")] == [None] * 3
    assert sheet["C3"].value == (
        "'=1+1 / +Proveedor / @Proyecto / Hospedaje Fase Nacional LTTB"
    )
    assert [cell.value for cell in workbook["Manifest"][2]] == [
        "'=1+1",
        "'+Proveedor",
    ]
