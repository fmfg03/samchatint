import io
import zipfile
from xml.etree import ElementTree

import pytest
from openpyxl import Workbook

from devnous.gastos.services import payroll_amount_workbook_parser as parser

HEADERS = [
    "CLAVE",
    "NOMBRE DEL EMPLEADO",
    "DIAS\nPAG.",
    "S.DIARIO",
    "CUENTA CONTABLE SUELDO",
    "SUELDO",
    "CUENTA CONTABLE HORAS EXTRAS",
    "HORAS EXTRAS",
    "BONO",
    "CUENTA CONTABLE INTERNET",
    "AYUDA INTERNET Y LUZ",
    "SUBSIDIO",
    "CUENTA CONTABLE VACACIONES",
    "VACACIONES",
    "CUENTA CONTABLE PRIMA VACACIONAL",
    "PRIM VACACIONAL",
    "COMPENSACION",
    "TOTAL\nPERCEP.",
    "CUENTA CONTABLE PERSONAL",
    "PRESTAMO PERSONAL",
    "CUENTA CONTABLE IMSS",
    "I.M.S.S",
    "CUENTA CONTABLE ISR",
    "I.S.R.",
    "TOTAL\nDEDUCC.",
    "CUENTA CONTABLE PAGO",
    "NETO A \nPAGAR",
]


def workbook_bytes(overrides=None, *, count=1, headers=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "N"
    ws["C2"] = "Nómina de prueba"
    headings = HEADERS if headers is None else headers
    for col, heading in enumerate(headings, 1):
        ws.cell(5, col, heading)
    defaults = {heading: 0 for heading in HEADERS}
    defaults.update(
        {
            "CLAVE": "001",
            "NOMBRE DEL EMPLEADO": "Persona prueba",
            "DIAS\nPAG.": 15,
            "S.DIARIO": None,
            "CUENTA CONTABLE SUELDO": "5200-001-002",
            "CUENTA CONTABLE HORAS EXTRAS": "5200-001-003",
            "CUENTA CONTABLE INTERNET": "5200-001-008",
            "CUENTA CONTABLE VACACIONES": "5200-001-004",
            "CUENTA CONTABLE PRIMA VACACIONAL": "5200-001-005",
            "CUENTA CONTABLE PERSONAL": "1170-001-008",
            "CUENTA CONTABLE IMSS": "2150-003-001",
            "CUENTA CONTABLE ISR": "2150-001-004",
            "CUENTA CONTABLE PAGO": "1120-001-001",
            "SUELDO": 100,
            "TOTAL\nPERCEP.": 100,
            "NETO A \nPAGAR": 100,
        }
    )
    for index in range(count):
        data = {**defaults, "CLAVE": f"{index + 1:03d}", **(overrides or {})}
        for col, heading in enumerate(headings, 1):
            ws.cell(7 + index, col, data.get(heading))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_preserves_source_amounts_identifiers_and_accounts():
    parsed = parser.parse_payroll_amount_workbook(
        "nomina.xlsx", workbook_bytes(count=2)
    )
    assert not parsed.errors
    assert [row["employee_code"] for row in parsed.rows] == ["001", "002"]
    assert parsed.rows[0]["daily_salary"] is None
    assert parsed.rows[0]["components"][0]["amount"] == "100.00"
    assert parsed.rows[0]["components"][0]["account_code"] == "5200-001-002"
    assert len(parsed.file_sha256) == len(parsed.content_sha256) == 64
    assert parsed.content_sha256 == parsed.content_sha256


def test_normalized_reordered_headers_are_resolved_by_name():
    result = parser.parse_payroll_amount_workbook(
        "n.xlsx", workbook_bytes(headers=list(reversed(HEADERS)))
    )
    assert not result.errors
    assert result.rows[0]["gross_total"] == "100.00"
    assert parser.normalized(" Nómina \n ") == "NOMINA"


@pytest.mark.parametrize(
    "changes,fragment",
    [
        ({"COMPENSACION": 10}, "diferencia en percepciones"),
        ({"I.S.R.": 10}, "diferencia en deducciones"),
        ({"NETO A \nPAGAR": 99}, "diferencia en neto"),
        ({"SUELDO": None}, "importe vacío"),
        ({"SUELDO": "5200-001-002"}, "importe inválido"),
        ({"SUELDO": "NaN"}, "fuera de rango"),
        ({"SUELDO": "Infinity"}, "fuera de rango"),
        ({"SUELDO": -1}, "ajuste negativo"),
        ({"SUELDO": 0.001}, "precisión"),
        ({"SUELDO": True}, "importe inválido"),
        ({"SUELDO": 10**14}, "fuera de rango"),
        ({"DIAS\nPAG.": 1000000}, "días fuera de rango"),
        ({"CLAVE": "A" * 81}, "identificación demasiado larga"),
        ({"CLAVE": None}, "identificación incompleta"),
        (
            {"SUELDO": 0, "TOTAL\nPERCEP.": 0, "NETO A \nPAGAR": 0},
            "sin importes efectivos",
        ),
        ({"SUELDO": "=1+1"}, "fórmula sin resultado"),
    ],
)
def test_invalid_amounts_and_totals_are_reported(changes, fragment):
    parsed = parser.parse_payroll_amount_workbook("n.xlsx", workbook_bytes(changes))
    assert any(fragment in error for error in parsed.errors)


def test_duplicate_employee_is_blocking_and_zero_is_distinct_from_blank():
    parsed = parser.parse_payroll_amount_workbook(
        "n.xlsx", workbook_bytes({"CLAVE": "7"}, count=2)
    )
    assert any("duplicada" in error for error in parsed.errors)
    assert parsed.rows[0]["components"][1]["amount"] == "0.00"


@pytest.mark.parametrize("headers", [HEADERS[:-1], HEADERS + [HEADERS[0]]])
def test_missing_or_duplicate_headers_are_rejected(headers):
    with pytest.raises(ValueError, match="encabezados"):
        parser.parse_payroll_amount_workbook("n.xlsx", workbook_bytes(headers=headers))


@pytest.mark.parametrize(
    "filename,contents", [("a.xlsm", b"x"), ("a.xlsx", b""), ("a.xlsx", b"broken")]
)
def test_invalid_file_is_rejected(filename, contents):
    with pytest.raises(ValueError):
        parser.parse_payroll_amount_workbook(filename, contents)


def test_file_package_and_sheet_limits(monkeypatch):
    data = workbook_bytes()
    monkeypatch.setattr(parser, "MAX_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="10 MiB"):
        parser.parse_payroll_amount_workbook("n.xlsx", data)
    monkeypatch.setattr(parser, "MAX_FILE_BYTES", 10**7)
    monkeypatch.setattr(parser, "MAX_XML_BYTES", 1)
    with pytest.raises(ValueError, match="descomprimido"):
        parser.parse_payroll_amount_workbook("n.xlsx", data)
    monkeypatch.setattr(parser, "MAX_XML_BYTES", 10**8)
    monkeypatch.setattr(parser, "MAX_EMPLOYEES", 1)
    with pytest.raises(ValueError, match="5.000"):
        parser.parse_payroll_amount_workbook("n.xlsx", workbook_bytes(count=2))


def test_external_links_and_macros_are_rejected():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr("xl/externalLinks/externalLink1.xml", "x")
    with pytest.raises(ValueError, match="vínculos externos"):
        parser.parse_payroll_amount_workbook("n.xlsx", output.getvalue())


def test_style_only_far_column_does_not_reject_the_real_layout():
    from openpyxl import load_workbook
    from openpyxl.styles import Font

    wb = load_workbook(io.BytesIO(workbook_bytes()))
    wb["N"]["XFD7"].font = Font(bold=True)
    out = io.BytesIO()
    wb.save(out)
    parsed = parser.parse_payroll_amount_workbook("n.xlsx", out.getvalue())
    assert not parsed.errors
    assert len(parsed.rows) == 1


def test_underdeclared_dimensions_do_not_silently_omit_employees():
    source = workbook_bytes(count=2)
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as original,
        zipfile.ZipFile(output, "w") as target,
    ):
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b'ref="A1:AA8"', b'ref="A1:AA7"')
            target.writestr(item, data)
    parsed = parser.parse_payroll_amount_workbook("n.xlsx", output.getvalue())
    assert not parsed.errors
    assert len(parsed.rows) == 2


def test_negative_amount_is_visible_but_blocks_confirmation():
    parsed = parser.parse_payroll_amount_workbook(
        "n.xlsx",
        workbook_bytes({"SUELDO": -1, "TOTAL\nPERCEP.": -1, "NETO A \nPAGAR": -1}),
    )
    assert any("ajuste negativo" in error for error in parsed.errors)
    assert parsed.rows[0]["components"][0]["amount"] == "-1.00"


def test_cached_formula_is_preserved_with_warning():
    source = workbook_bytes({"SUELDO": "=100"})
    output = io.BytesIO()
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with (
        zipfile.ZipFile(io.BytesIO(source)) as original,
        zipfile.ZipFile(output, "w") as target,
    ):
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                root = ElementTree.fromstring(data)
                cell = root.find('.//m:c[@r="F7"]/m:v', namespace)
                cell.text = "100"
                data = ElementTree.tostring(root)
            target.writestr(item, data)
    parsed = parser.parse_payroll_amount_workbook("n.xlsx", output.getvalue())
    assert not parsed.errors
    assert parsed.rows[0]["components"][0]["has_formula"]
    assert parsed.warnings


def test_missing_sheet_no_records_and_incomplete_row():
    wb = Workbook()
    out = io.BytesIO()
    wb.save(out)
    with pytest.raises(ValueError, match="hoja N"):
        parser.parse_payroll_amount_workbook("n.xlsx", out.getvalue())
    parsed = parser.parse_payroll_amount_workbook("n.xlsx", workbook_bytes(count=0))
    assert "No hay registros de empleados." in parsed.errors
    parsed = parser.parse_payroll_amount_workbook(
        "n.xlsx", workbook_bytes({"NOMBRE DEL EMPLEADO": None})
    )
    assert any("identificación incompleta" in error for error in parsed.errors)


async def test_upload_reads_at_most_the_approved_size():
    from unittest.mock import AsyncMock

    from devnous.gastos.routes.user_routes import _read_payroll_amount_upload

    upload = AsyncMock()
    upload.filename = "n.xlsx"
    upload.read.return_value = workbook_bytes()
    parsed = await _read_payroll_amount_upload(upload)
    assert not parsed.errors
    upload.read.assert_awaited_once_with(parser.MAX_FILE_BYTES + 1)


def test_unidentified_row_with_amounts_blocks_the_entire_batch():
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(workbook_bytes(count=2)))
    ws = wb["N"]
    ws["A8"] = None
    ws["B8"] = None
    ws["F8"] = 250
    ws["R8"] = 250
    ws["AA8"] = 250
    out = io.BytesIO()
    wb.save(out)
    parsed = parser.parse_payroll_amount_workbook("n.xlsx", out.getvalue())
    assert any("Fila 8: identificación incompleta" in error for error in parsed.errors)
    assert parsed.rows[0]["gross_total"] == "100.00"


def test_optional_daily_salary_formula_without_cache_is_not_a_blank():
    parsed = parser.parse_payroll_amount_workbook(
        "n.xlsx", workbook_bytes({"S.DIARIO": "=100/15"})
    )
    assert any(
        "S.DIARIO" in error and "fórmula sin resultado" in error
        for error in parsed.errors
    )


def test_explicit_summary_row_is_not_an_unidentified_employee():
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(workbook_bytes(count=2)))
    wb["N"]["A8"] = None
    wb["N"]["B8"] = "TOTAL"
    out = io.BytesIO()
    wb.save(out)
    parsed = parser.parse_payroll_amount_workbook("n.xlsx", out.getvalue())
    assert not parsed.errors
    assert len(parsed.rows) == 1
