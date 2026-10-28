"""Read the approved payroll amount layout without calculating fiscal concepts."""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import load_workbook
from openpyxl.xml.functions import iterparse

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_XML_BYTES = 100 * 1024 * 1024
MAX_EMPLOYEES = 5000
PARSER_VERSION = "payroll-amounts-v1"

# The account belongs to the row, never to a global salary-purpose default.
COMPONENTS = (
    ("salary", "SUELDO", "CUENTA CONTABLE SUELDO", "perception"),
    ("overtime", "HORAS EXTRAS", "CUENTA CONTABLE HORAS EXTRAS", "perception"),
    ("bonus", "BONO", None, "perception"),
    ("internet", "AYUDA INTERNET Y LUZ", "CUENTA CONTABLE INTERNET", "perception"),
    ("subsidy", "SUBSIDIO", None, "perception"),
    ("vacation", "VACACIONES", "CUENTA CONTABLE VACACIONES", "perception"),
    (
        "vacation_premium",
        "PRIM VACACIONAL",
        "CUENTA CONTABLE PRIMA VACACIONAL",
        "perception",
    ),
    ("compensation", "COMPENSACION", None, "perception"),
    ("personal_loan", "PRESTAMO PERSONAL", "CUENTA CONTABLE PERSONAL", "deduction"),
    ("imss", "I.M.S.S", "CUENTA CONTABLE IMSS", "deduction"),
    ("isr", "I.S.R.", "CUENTA CONTABLE ISR", "deduction"),
    ("net_pay", "NETO A PAGAR", "CUENTA CONTABLE PAGO", "net"),
)


def normalized(value: Any) -> str:
    """Normalize headings without losing account codes or employee identifiers."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().upper()


def digest(value: Any) -> str:
    """Return a deterministic, non-reversible receipt for JSON-compatible data."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


@dataclass
class ParsedPayrollAmounts:
    """Original row values, arithmetic checks and provenance for a single workbook."""

    file_sha256: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_title: str = ""

    @property
    def content_sha256(self) -> str:
        return digest(sorted(self.rows, key=lambda row: row["employee_code"]))


def _number(
    value: Any,
    *,
    optional: bool = False,
    places: int = 2,
    allow_negative: bool = False,
) -> str | None:
    if value is None or value == "":
        if optional:
            return None
        raise ValueError("importe vacío o fórmula sin resultado almacenado")
    if isinstance(value, bool):
        raise ValueError("importe inválido")
    try:
        amount = Decimal(str(value).strip())
        if not amount.is_finite() or abs(amount) >= Decimal("100000000000000"):
            raise ValueError("importe fuera de rango")
        quantized = amount.quantize(Decimal(1).scaleb(-places))
        if amount != quantized:
            raise ValueError("precisión inválida")
        if amount < 0 and not allow_negative:
            raise ValueError("ajuste negativo sin regla aprobada")
        return str(quantized)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("importe inválido") from exc


def parse_payroll_amount_workbook(
    filename: str, contents: bytes
) -> ParsedPayrollAmounts:
    """Parse bounded XLSX input; never execute formulas or infer missing amounts."""
    if not (filename or "").lower().endswith(".xlsx"):
        raise ValueError("Seleccione un archivo XLSX.")
    if not contents or len(contents) > MAX_FILE_BYTES:
        raise ValueError("El archivo está vacío o supera 10 MiB.")
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as package:
            entries = package.infolist()
            if sum(entry.file_size for entry in entries) > MAX_XML_BYTES:
                raise ValueError("El contenido descomprimido supera el límite.")
            if len(entries) > 10000:
                raise ValueError("Demasiadas entradas en el archivo.")
            if any(
                "externallinks/" in entry.filename.lower()
                or "vbaproject" in entry.filename.lower()
                for entry in entries
            ):
                raise ValueError("No se admiten macros ni vínculos externos.")
        wb = load_workbook(io.BytesIO(contents), read_only=True, data_only=False)
        try:
            cached = load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
            try:
                return _parse_sheets(wb, cached, contents)
            finally:
                cached.close()
        finally:
            wb.close()
    except (zipfile.BadZipFile, KeyError, OSError, SyntaxError) as exc:
        raise ValueError("El archivo XLSX no se puede leer.") from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("El archivo XLSX no se puede leer.") from exc


def _parse_sheets(wb, cached, contents: bytes) -> ParsedPayrollAmounts:
    if "N" not in wb.sheetnames:
        raise ValueError("No se encontró la hoja N.")
    ws, values = wb["N"], cached["N"]
    # Validate real XML rows: declared dimensions can be stale or understate data.
    with zipfile.ZipFile(io.BytesIO(contents)) as package:
        with package.open(ws._worksheet_path.lstrip("/")) as stream:
            row_count = 0
            for _, element in iterparse(stream, events=("end",)):
                if element.tag.endswith("}row"):
                    row_count += 1
                    if (
                        max(int(element.get("r", row_count)), row_count)
                        > MAX_EMPLOYEES + 100
                    ):
                        raise ValueError("La hoja supera el límite de filas.")
                element.clear()
    ws.reset_dimensions()
    values.reset_dimensions()
    # Excel full-row formatting can advertise XFD as the last used column.
    # Bound the actual projection instead of trusting that style-only dimension.
    heads = list(ws.iter_rows(min_row=5, max_row=5, max_col=64, values_only=True))[0]
    headings = [normalized(h) for h in heads]
    nonempty = [h for h in headings if h]
    if len(nonempty) != len(set(nonempty)):
        raise ValueError("Hay encabezados duplicados.")
    columns = {h: i for i, h in enumerate(headings) if h}
    required = {
        "CLAVE",
        "NOMBRE DEL EMPLEADO",
        "DIAS PAG.",
        "S.DIARIO",
        "TOTAL PERCEP.",
        "TOTAL DEDUCC.",
    }
    for _, heading, account, _ in COMPONENTS:
        required.add(heading)
        if account:
            required.add(account)
    if required - columns.keys():
        raise ValueError("Faltan encabezados obligatorios del layout de nómina.")
    parsed = ParsedPayrollAmounts(hashlib.sha256(contents).hexdigest())
    parsed.source_title = str(values["C2"].value or "")[:500]
    seen: set[str] = set()
    formula_found = False
    original_rows = ws.iter_rows(min_row=6, max_col=64)
    cached_rows = values.iter_rows(min_row=6, max_col=64)
    for row_no, (source, row) in enumerate(zip(original_rows, cached_rows), start=6):

        def value(heading):
            return row[columns[heading]].value

        code = value("CLAVE")
        name = value("NOMBRE DEL EMPLEADO")
        if isinstance(code, str):
            code = code.strip()
        if isinstance(name, str):
            name = name.strip()
        if code in (None, "") and name in (None, ""):
            data_headings = [heading for _, heading, _, _ in COMPONENTS] + [
                "DIAS PAG.",
                "S.DIARIO",
                "TOTAL PERCEP.",
                "TOTAL DEDUCC.",
            ]
            unidentified_data = False
            for heading in data_headings:
                raw = value(heading)
                if source[columns[heading]].data_type == "f" and raw in (None, ""):
                    unidentified_data = True
                elif raw not in (None, ""):
                    try:
                        amount = Decimal(str(raw))
                        unidentified_data |= not amount.is_finite() or amount != 0
                    except InvalidOperation:
                        unidentified_data = True
            if unidentified_data:
                parsed.errors.append(f"Fila {row_no}: identificación incompleta.")
            continue
        if code in (None, "") and normalized(name) in {
            "TOTAL",
            "TOTALES",
            "TOTAL GENERAL",
            "TOTAL NOMINA",
            "TOTAL DE NOMINA",
        }:
            continue
        if code in (None, "") or name in (None, ""):
            parsed.errors.append(f"Fila {row_no}: identificación incompleta.")
            continue
        if isinstance(code, (float, int)) and not isinstance(code, bool):
            if float(code).is_integer():
                code = str(int(code))
        code = str(code).strip()
        if len(code) > 80 or len(str(name).strip()) > 255:
            parsed.errors.append(f"Fila {row_no}: identificación demasiado larga.")
            continue
        if code in seen:
            parsed.errors.append(f"Fila {row_no}: clave de empleado duplicada.")
        seen.add(code)
        record: dict[str, Any] = {
            "source_row": row_no,
            "employee_code": code,
            "source_name": str(name).strip(),
            "components": [],
            "source_formulas": {},
        }
        row_errors = len(parsed.errors)
        for key, heading, account, side in COMPONENTS:
            try:
                amount = _number(value(heading), allow_negative=True)
                cell = source[columns[heading]]
                is_formula = cell.data_type == "f"
                formula_found |= is_formula
                record["components"].append(
                    {
                        "concept_key": key,
                        "label": heading,
                        "side": side,
                        "amount": amount,
                        "account_code": (
                            str(value(account) or "").strip() if account else None
                        ),
                        "has_formula": is_formula,
                    }
                )
            except ValueError as exc:
                parsed.errors.append(f"Fila {row_no}, {heading}: {exc}.")
        for key, heading, optional, places in (
            ("days_paid", "DIAS PAG.", False, 4),
            ("daily_salary", "S.DIARIO", True, 4),
            ("gross_total", "TOTAL PERCEP.", False, 2),
            ("deductions_total", "TOTAL DEDUCC.", False, 2),
        ):
            try:
                is_formula = source[columns[heading]].data_type == "f"
                formula_found |= is_formula
                record["source_formulas"][key] = is_formula
                if is_formula and value(heading) in (None, ""):
                    raise ValueError("fórmula sin resultado almacenado")
                record[key] = _number(
                    value(heading),
                    optional=optional,
                    places=places,
                    allow_negative=key in {"gross_total", "deductions_total"},
                )
                if key == "days_paid" and Decimal(record[key]) >= Decimal("1000000"):
                    raise ValueError("días fuera de rango")
            except ValueError as exc:
                parsed.errors.append(f"Fila {row_no}, {heading}: {exc}.")
        if len(parsed.errors) != row_errors:
            continue
        components = record["components"]
        if any(Decimal(component["amount"]) < 0 for component in components):
            parsed.errors.append(
                f"Fila {row_no}: ajuste negativo sin regla aprobada; "
                "se conserva el importe para revisión."
            )
        gross = sum(
            (Decimal(c["amount"]) for c in components if c["side"] == "perception"),
            Decimal(0),
        )
        deductions = sum(
            (Decimal(c["amount"]) for c in components if c["side"] == "deduction"),
            Decimal(0),
        )
        net = next(c["amount"] for c in components if c["side"] == "net")
        record["net_total"] = net
        record["computed_totals"] = {
            "gross": str(gross),
            "deductions": str(deductions),
            "net": str(gross - deductions),
        }
        for label, expected, actual in (
            ("percepciones", gross, record["gross_total"]),
            ("deducciones", deductions, record["deductions_total"]),
            ("neto", gross - deductions, net),
        ):
            if expected != Decimal(actual):
                parsed.errors.append(f"Fila {row_no}: diferencia en {label}.")
        parsed.rows.append(record)
        if len(parsed.rows) > MAX_EMPLOYEES:
            raise ValueError("La hoja supera 5.000 empleados.")
    if not seen:
        parsed.errors.append("No hay registros de empleados.")
    if formula_found:
        parsed.warnings.append(
            "Se usan resultados almacenados de fórmulas; "
            "recalcule el Excel antes de cargarlo."
        )
    if parsed.rows and not any(
        Decimal(c["amount"]) for row in parsed.rows for c in row["components"]
    ):
        parsed.errors.append(
            "Plantilla sin importes efectivos: sólo permite vista previa."
        )
    return parsed
