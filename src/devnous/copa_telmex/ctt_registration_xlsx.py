"""Shared CTT aggregate tables and Excel export."""

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Literal

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: Literal["text", "count", "percent"] = "text"


@dataclass(frozen=True)
class ReportTable:
    key: str
    title: str
    sheet_title: str
    columns: tuple[Column, ...]
    rows: tuple[tuple[Any, ...], ...]


_GEOGRAPHY = (
    Column("nivel", "Nivel"),
    Column("estado", "Estado"),
    Column("municipio", "Municipio o demarcación"),
    Column("municipality_code", "Clave INEGI"),
)
_BIRTH_DATE_ISSUES = (
    Column("sin_fecha_nacimiento", "Sin fecha", "count"),
    Column("fecha_nacimiento_invalida", "Fecha inválida", "count"),
)
_AGE_TOTALS = (
    Column("otras_edades", "Otras edades", "count"),
    *_BIRTH_DATE_ISSUES,
    Column("jugadores", "Total", "count"),
)
_SECTIONS = (
    (
        "participacion_general",
        "Participación general",
        "Participación",
        (
            *_GEOGRAPHY,
            Column("categoria", "Categoría"),
            Column("equipos", "Equipos", "count"),
            Column("jugadores", "Jugadores", "count"),
        ),
    ),
    (
        "cobertura_municipal",
        "Cobertura municipal",
        "Cobertura municipal",
        (
            *_GEOGRAPHY,
            Column("municipios_participantes", "Municipios con CTT", "count"),
            Column("municipios_totales", "Municipios INEGI", "count"),
            Column("porcentaje", "Participación (%)", "percent"),
        ),
    ),
    (
        "femenil_edad",
        "Femenil",
        "Femenil",
        (
            *_GEOGRAPHY,
            Column("menores_17", "17 años o menos", "count"),
            Column("mayores_18", "18 años o más", "count"),
            *_BIRTH_DATE_ISSUES,
            Column("jugadores", "Total", "count"),
        ),
    ),
    (
        "juvenil_edades",
        "Juvenil",
        "Juvenil",
        (
            *_GEOGRAPHY,
            Column("edad_15", "15 años", "count"),
            Column("edad_16", "16 años", "count"),
            Column("edad_17", "17 años", "count"),
            *_AGE_TOTALS,
        ),
    ),
    (
        "varonil_rangos",
        "Varonil",
        "Varonil",
        (
            *_GEOGRAPHY,
            Column("edad_18", "18 años", "count"),
            Column("edad_19_24", "19–24 años", "count"),
            Column("edad_25_29", "25–29 años", "count"),
            Column("edad_30_mas", "30 años o más", "count"),
            *_AGE_TOTALS,
        ),
    ),
    (
        "calidad_datos",
        "Incidencias de datos",
        "Incidencias",
        (
            *_GEOGRAPHY,
            Column("equipos", "Equipos", "count"),
            Column("jugadores", "Jugadores", "count"),
            Column("equipos_sin_estado_inegi", "Sin clave estatal", "count"),
            Column(
                "equipos_sin_municipio_inegi",
                "Sin clave municipal",
                "count",
            ),
            Column("equipos_sin_categoria", "Equipos sin categoría", "count"),
            *_BIRTH_DATE_ISSUES,
        ),
    ),
)
_LEVEL_LABELS = {
    "nacional": "Nacional",
    "estatal": "Estatal",
    "municipal": "Municipal",
}


def _report_value(row: dict, column: Column) -> str | int | float | None:
    value = row.get(column.key)
    if column.kind == "count":
        return value if type(value) is int else None
    if column.kind == "percent":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value if 0 <= value <= 100 else None
        return None
    if not isinstance(value, str):
        return None
    return _LEVEL_LABELS.get(value) if column.key == "nivel" else value


def ctt_registration_report_tables(report: dict) -> tuple[ReportTable, ...]:
    """Project explicitly approved aggregate columns for HTML and Excel."""
    if not report.get("available"):
        return ()
    reports = report["reports"]
    return tuple(
        ReportTable(
            key,
            title,
            sheet,
            columns,
            tuple(
                tuple(_report_value(row, column) for column in columns)
                for row in reports[key]
            ),
        )
        for key, title, sheet, columns in _SECTIONS
    )


def _write_value(cell: Cell, value: Any, kind: str = "text") -> None:
    if value is None or value == "":
        return
    if kind == "percent":
        cell.value = value / 100
        cell.number_format = "0.00%"
    elif kind == "count":
        cell.value = value
        cell.number_format = "#,##0"
    else:
        cell.value = ILLEGAL_CHARACTERS_RE.sub("", str(value))
        cell.data_type = "s"


def render_ctt_registration_workbook(report: dict) -> bytes:
    """Export the six aggregate tables shown in the dashboard."""
    tables = ctt_registration_report_tables(report)
    if not tables:
        raise ValueError("No hay datos disponibles para exportar la edición.")

    filters = report.get("filters") or {}
    metadata = (
        ("Edición", report.get("edition_year")),
        ("Corte de edad", report.get("as_of_date")),
        ("Estado", filters.get("state")),
        ("Municipio", filters.get("municipality")),
        ("Categoría", filters.get("category")),
        ("Catálogo INEGI", report.get("catalog_source")),
        ("Corte del catálogo", report.get("catalog_date")),
        ("Fuente del catálogo", report.get("catalog_url")),
        ("Consulta del padrón", report.get("captured_at")),
    )

    workbook = Workbook()
    workbook.remove(workbook.active)
    header_fill = PatternFill(fill_type="solid", fgColor="334876")
    header_font = Font(color="FFFFFF", bold=True)
    metadata_label_width = max(len(label) for label, _ in metadata) + 3
    for table in tables:
        sheet = workbook.create_sheet(table.sheet_title)
        for row_index, (label, value) in enumerate(metadata, start=1):
            _write_value(sheet.cell(row_index, 1), label)
            if value is not None and value != "":
                sheet.merge_cells(
                    start_row=row_index,
                    start_column=2,
                    end_row=row_index,
                    end_column=len(table.columns),
                )
                _write_value(sheet.cell(row_index, 2), value)

        header_row = len(metadata) + 2
        sheet.freeze_panes = f"A{header_row + 1}"
        for column_index, column in enumerate(table.columns, start=1):
            cell = sheet.cell(header_row, column_index)
            _write_value(cell, column.label)
            cell.fill = header_fill
            cell.font = header_font
            letter = get_column_letter(column_index)
            dimension = sheet.column_dimensions[letter]
            dimension.width = max(14, len(column.label) + 3)
        for row_index, values in enumerate(table.rows, start=header_row + 1):
            for column_index, (column, value) in enumerate(
                zip(table.columns, values, strict=True), start=1
            ):
                cell = sheet.cell(row_index, column_index)
                _write_value(cell, value, column.kind)
                if column.kind == "text" and value:
                    letter = get_column_letter(column_index)
                    dimension = sheet.column_dimensions[letter]
                    width = len(str(value)) + 3
                    dimension.width = min(48, max(dimension.width, width))
        last_column = get_column_letter(len(table.columns))
        sheet.auto_filter.ref = f"A{header_row}:{last_column}{sheet.max_row}"
        first_column = sheet.column_dimensions["A"]
        first_column.width = max(first_column.width, metadata_label_width)

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
