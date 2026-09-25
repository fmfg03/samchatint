"""XLSX export for the No Deducibles control."""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


def _safe_cell_text(value: Any) -> str:
    """Prevent spreadsheet formulas from untrusted expense values."""
    text = "" if value is None else str(value)
    return f"\'{text}" if text.startswith(("=", "+", "-", "@")) else text


def generate_no_deductibles_xlsx(report: dict[str, Any]) -> bytes:
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Resumen"
    summary_sheet.append(["Control de No Deducibles"])
    summary_sheet["A1"].font = Font(size=16, bold=True)
    period = report.get("period") or {}
    summary = report.get("summary") or {}
    summary_sheet.append(
        [
            "Periodo de fecha de gasto",
            f"{period.get('year')}-{int(period.get('month') or 0):02d}",
        ]
    )
    summary_sheet.append(
        [
            "Moneda",
            "Gasto total",
            "Deducible (CFDI vinculado)",
            "No deducible (sin CFDI)",
            "% no deducible",
        ]
    )
    for cell in summary_sheet[3]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="0F766E")
    for currency, currency_summary in (summary.get("by_currency") or {}).items():
        summary_sheet.append(
            [
                _safe_cell_text(currency),
                currency_summary.get("total_amount") or 0,
                currency_summary.get("deductible_amount") or 0,
                currency_summary.get("non_deductible_amount") or 0,
                (currency_summary.get("non_deductible_percent") or 0) / 100,
            ]
        )
    for row in summary_sheet.iter_rows(min_row=4, min_col=2, max_col=4):
        for cell in row:
            cell.number_format = "#,##0.00"
    for row in summary_sheet.iter_rows(min_row=4, min_col=5, max_col=5):
        row[0].number_format = "0.00%"
    summary_sheet.column_dimensions["A"].width = 34
    summary_sheet.column_dimensions["B"].width = 22
    summary_sheet.column_dimensions["C"].width = 28
    summary_sheet.column_dimensions["D"].width = 28
    summary_sheet.column_dimensions["E"].width = 20

    detail_sheet = workbook.create_sheet("Detalle no deducible")
    headers = [
        "Fecha gasto", "Torneo", "Fase", "Documento", "Referencia documento",
        "Gasto", "Concepto", "Responsable", "Moneda", "Monto", "Estatus", "Motivo", "UUID CFDI",
    ]
    header_fill = PatternFill("solid", fgColor="0F766E")
    detail_sheet.append(headers)
    for cell in detail_sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
    for row in report.get("non_deductible_rows") or []:
        detail_sheet.append([
            _safe_cell_text(str(row.get("expense_date") or "")[:10]),
            _safe_cell_text(row.get("tournament_name")),
            _safe_cell_text(row.get("phase")),
            _safe_cell_text(row.get("source_type")),
            _safe_cell_text(row.get("source_reference")),
            _safe_cell_text(row.get("reference")),
            _safe_cell_text(row.get("concept")),
            _safe_cell_text(row.get("employee_name")),
            _safe_cell_text(row.get("currency")),
            row.get("amount") or 0,
            "No deducible",
            _safe_cell_text(row.get("fiscal_reason")),
            _safe_cell_text(row.get("cfdi_uuid")),
        ])
    for row in detail_sheet.iter_rows(min_row=2, min_col=10, max_col=10):
        row[0].number_format = "#,##0.00"
    detail_sheet.freeze_panes = "A2"
    for column, width in {"A": 14, "B": 28, "C": 18, "D": 14, "E": 22, "F": 22, "G": 36, "H": 28, "I": 12, "J": 15, "K": 16, "L": 38, "M": 38}.items():
        detail_sheet.column_dimensions[column].width = width

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
