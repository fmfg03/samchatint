"""Workbook export for a closed Payment Run."""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


_HEADER_FILL = PatternFill("solid", fgColor="0F766E")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_TITLE_FONT = Font(size=16, bold=True)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def generate_payment_run_order_xlsx(*, closure: dict[str, Any]) -> bytes:
    """Build the controlled payment-order workbook for one closed cutoff."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Orden de pago"

    sheet["A1"] = "Orden de pago - SamChat"
    sheet["A1"].font = _TITLE_FONT
    sheet["A2"] = f"Corte: {_text(closure.get('id'))}"
    sheet["A3"] = f"Fecha de corte: {_text(closure.get('run_date') or '-')}"
    sheet["A4"] = f"Total del corte: {_money(closure.get('total_amount')):,.2f}"

    headers = [
        "Solicitud",
        "Referencia operaciones",
        "Solicitante",
        "Beneficiario",
        "Banco",
        "Cuenta",
        "CLABE",
        "Concepto",
        "Fecha programada",
        "Moneda",
        "Monto pagable",
        "Estatus datos de pago",
    ]
    header_row = 6
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row=header_row, column=column, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )

    for row_number, item in enumerate(
        closure.get("items") or [], start=header_row + 1
    ):
        row = [
            item.get("numero_referencia"),
            item.get("referencia_operaciones"),
            item.get("solicitante"),
            item.get("beneficiario"),
            item.get("banco"),
            item.get("cuenta_bancaria"),
            item.get("cuenta_clabe"),
            item.get("concepto_pago"),
            item.get("fecha_pago"),
            item.get("currency") or "MXN",
            _money(item.get("monto")),
            item.get("payment_data_status"),
        ]
        for column, value in enumerate(row, start=1):
            cell = sheet.cell(row=row_number, column=column, value=value)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        sheet.cell(row=row_number, column=11).number_format = '#,##0.00'

    for column in range(1, len(headers) + 1):
        max_length = max(
            len(_text(sheet.cell(row=row, column=column).value))
            for row in range(1, sheet.max_row + 1)
        )
        sheet.column_dimensions[get_column_letter(column)].width = min(
            max(max_length + 2, 14),
            42,
        )
    sheet.freeze_panes = "A7"
    sheet.auto_filter.ref = f"A{header_row}:L{sheet.max_row}"

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
