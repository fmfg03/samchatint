"""
Build solicitud/informe form autofill payloads from parsed CFDI data.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional, Tuple

from .cfdi_parser import parse_cfdi_xml
from .cfdi_pdf_reader import parse_cfdi_pdf


@dataclass(frozen=True)
class CfdiAutofillData:
    emisor_rfc: str
    emisor_nombre: str
    monto: str
    currency: str
    numero_factura: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


def format_numero_factura(parsed: Dict[str, Any]) -> str:
    """UUID first; Serie+Folio fallback."""
    uuid_val = (parsed.get("cfdi_uuid") or "").strip()
    if uuid_val:
        return uuid_val.upper()
    serie = (parsed.get("serie") or "").strip()
    folio = (parsed.get("folio") or "").strip()
    if serie and folio:
        return f"{serie}-{folio}"
    return serie or folio or ""


def autofill_from_parsed_cfdi(parsed: Dict[str, Any]) -> Optional[CfdiAutofillData]:
    """Map canonical CFDI parser output to form prefill fields."""
    if not parsed:
        return None

    emisor_rfc = (parsed.get("emisor_rfc") or "").strip().upper()
    emisor_nombre = re.sub(
        r"\s+",
        " ",
        (parsed.get("emisor_nombre") or "").strip(),
    )
    moneda = (parsed.get("moneda") or "MXN").strip().upper() or "MXN"
    numero_factura = format_numero_factura(parsed)

    total_raw = parsed.get("total")
    monto = ""
    if total_raw is not None and total_raw != "":
        try:
            monto = f"{float(total_raw):.2f}"
        except (TypeError, ValueError):
            monto = ""

    if not emisor_rfc and not emisor_nombre and not monto and not numero_factura:
        return None

    return CfdiAutofillData(
        emisor_rfc=emisor_rfc,
        emisor_nombre=emisor_nombre,
        monto=monto,
        currency=moneda,
        numero_factura=numero_factura,
    )


@dataclass(frozen=True)
class QuickExpenseAutofillData:
    concepto: str
    fecha: str
    numero_factura: str
    subtotal: str
    descuento: str
    impuestos_y_retenciones: str
    total: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class QuickExpenseTaxComponents:
    subtotal: Decimal
    descuento: Decimal
    impuestos_trasladados: Decimal
    retenciones: Decimal
    iva: Decimal
    total: Decimal

    @property
    def impuestos_y_retenciones(self) -> Decimal:
        return (self.impuestos_trasladados - self.retenciones).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    @property
    def calculated_total(self) -> Decimal:
        return compute_quick_expense_total(
            self.subtotal,
            self.descuento,
            self.impuestos_y_retenciones,
        )


def _decimal_money(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _format_money(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    if amount == 0:
        return ""
    return f"{amount:.2f}"


def _format_cfdi_fecha(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()[:10]
    return ""


def _traslados_from_parsed(parsed: Dict[str, Any]) -> Decimal:
    traslados = _decimal_money(parsed.get("total_impuestos_trasladados"))
    if traslados != 0:
        return traslados
    impuestos_detalle = parsed.get("impuestos_detalle") or {}
    traslados_list = list(impuestos_detalle.get("traslados") or [])
    if traslados_list:
        return sum(
            (_decimal_money(item.get("importe")) for item in traslados_list),
            Decimal("0"),
        )
    return sum(
        (
            _decimal_money(tax.get("importe"))
            for item in parsed.get("conceptos", [])
            for tax in item.get("impuestos", [])
        ),
        Decimal("0"),
    )


def _retenciones_from_parsed(parsed: Dict[str, Any]) -> Decimal:
    impuestos_detalle = parsed.get("impuestos_detalle") or {}
    retenciones_list = list(impuestos_detalle.get("retenciones") or [])
    return sum(
        (_decimal_money(item.get("importe")) for item in retenciones_list),
        Decimal("0"),
    )


def _iva_trasladado_from_parsed(parsed: Dict[str, Any]) -> Decimal:
    impuestos_detalle = parsed.get("impuestos_detalle") or {}
    traslados_list = list(impuestos_detalle.get("traslados") or [])
    if traslados_list:
        iva = sum(
            (
                _decimal_money(item.get("importe"))
                for item in traslados_list
                if str(item.get("impuesto") or "").strip() == "002"
            ),
            Decimal("0"),
        )
        if iva != 0:
            return iva
    return sum(
        (
            _decimal_money(tax.get("importe"))
            for item in parsed.get("conceptos", [])
            for tax in item.get("impuestos", [])
            if str(tax.get("impuesto") or "").strip() == "002"
        ),
        Decimal("0"),
    )


def quick_expense_tax_components_from_parsed(
    parsed: Dict[str, Any],
) -> QuickExpenseTaxComponents:
    """Extract captura rápida tax fields from canonical CFDI parser output."""
    subtotal = _decimal_money(parsed.get("subtotal"))
    descuento = _decimal_money(parsed.get("descuento"))
    traslados = _traslados_from_parsed(parsed)
    retenciones = _retenciones_from_parsed(parsed)
    total = _decimal_money(parsed.get("total"))

    if total == 0 and subtotal > 0:
        total = compute_quick_expense_total(
            subtotal,
            descuento,
            traslados - retenciones,
        )

    return QuickExpenseTaxComponents(
        subtotal=subtotal,
        descuento=descuento,
        impuestos_trasladados=traslados,
        retenciones=retenciones,
        iva=_iva_trasladado_from_parsed(parsed),
        total=total,
    )


def compute_quick_expense_total(
    subtotal: Decimal,
    descuento: Decimal,
    impuestos_y_retenciones: Decimal,
) -> Decimal:
    return (subtotal - descuento + impuestos_y_retenciones).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def autofill_quick_expense_from_parsed_cfdi(
    parsed: Dict[str, Any],
) -> Optional[QuickExpenseAutofillData]:
    """Map canonical CFDI parser output to captura rápida form fields."""
    if not parsed:
        return None

    taxes = quick_expense_tax_components_from_parsed(parsed)
    concepto = (parsed.get("descripcion_concepto_principal") or "").strip()
    fecha = _format_cfdi_fecha(parsed.get("fecha"))
    numero_factura = format_numero_factura(parsed)
    subtotal = f"{taxes.subtotal:.2f}" if taxes.subtotal else ""
    descuento = f"{taxes.descuento:.2f}" if taxes.descuento else "0.00"
    impuestos_y_retenciones = f"{taxes.impuestos_y_retenciones:.2f}"
    total = f"{taxes.total:.2f}" if taxes.total else ""
    if not total and subtotal:
        computed = taxes.calculated_total
        if computed > 0:
            total = f"{computed:.2f}"

    if not any(
        (
            concepto,
            fecha,
            numero_factura,
            subtotal,
            descuento,
            impuestos_y_retenciones,
            total,
        )
    ):
        return None

    return QuickExpenseAutofillData(
        concepto=concepto,
        fecha=fecha,
        numero_factura=numero_factura,
        subtotal=subtotal,
        descuento=descuento or "0.00",
        impuestos_y_retenciones=impuestos_y_retenciones or "0.00",
        total=total,
    )


def parse_cfdi_for_autofill(
    *,
    xml_bytes: Optional[bytes] = None,
    pdf_bytes: Optional[bytes] = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Parse CFDI bytes for form autofill. XML takes precedence when both are supplied.
    """
    if xml_bytes:
        xml_text = None
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                xml_text = xml_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if xml_text is None:
            return {}, "El CFDI XML no usa una codificación válida."
        parsed = parse_cfdi_xml(xml_text)
        if not parsed:
            return {}, "El archivo XML no es un CFDI válido."
        return parsed, None

    if pdf_bytes:
        parsed = parse_cfdi_pdf(pdf_bytes)
        if not parsed:
            return {}, "No se pudieron extraer datos del CFDI PDF."
        return parsed, None

    return {}, "Adjunte un CFDI XML o PDF."
