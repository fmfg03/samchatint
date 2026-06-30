"""CFDI consistency checks across invoice, autofill, and user input."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional

MONEY_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class ConsistencyIssue:
    code: str
    field: str
    message: str
    expected: Any = None
    actual: Any = None
    source: str = "cfdi_consistency"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FieldComparison:
    field: str
    expected: Any
    actual: Any
    matches: bool
    difference: Optional[str] = None
    tolerance: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CFDIConsistencyResult:
    status: str
    blockers: List[ConsistencyIssue] = field(default_factory=list)
    warnings: List[ConsistencyIssue] = field(default_factory=list)
    info: List[ConsistencyIssue] = field(default_factory=list)
    field_comparisons: List[FieldComparison] = field(default_factory=list)
    requires_shared_invoice_confirmation: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "blockers": [item.to_dict() for item in self.blockers],
            "warnings": [item.to_dict() for item in self.warnings],
            "info": [item.to_dict() for item in self.info],
            "field_comparisons": [
                item.to_dict() for item in self.field_comparisons
            ],
            "requires_shared_invoice_confirmation": (
                self.requires_shared_invoice_confirmation
            ),
        }


def _first_value(payload: Optional[Dict[str, Any]], names: Iterable[str]) -> Any:
    if not isinstance(payload, dict):
        return None
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return value
    return None


def _money(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, ValueError):
        return None


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().split())


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip()[:10]


def _compare_money(
    *,
    field_name: str,
    expected: Optional[Decimal],
    actual: Optional[Decimal],
    blockers: List[ConsistencyIssue],
    field_comparisons: List[FieldComparison],
    blocker_code: str,
    blocker_message: str,
) -> None:
    if expected is None or actual is None:
        return
    difference = abs(expected - actual).quantize(Decimal("0.01"))
    matches = difference <= MONEY_TOLERANCE
    field_comparisons.append(
        FieldComparison(
            field=field_name,
            expected=f"{expected:.2f}",
            actual=f"{actual:.2f}",
            matches=matches,
            difference=f"{difference:.2f}",
            tolerance=f"{MONEY_TOLERANCE:.2f}",
        )
    )
    if not matches:
        blockers.append(
            ConsistencyIssue(
                code=blocker_code,
                field=field_name,
                message=blocker_message,
                expected=f"{expected:.2f}",
                actual=f"{actual:.2f}",
            )
        )


def _invoice_tax_components(invoice_data: Dict[str, Any]) -> Dict[str, Decimal]:
    detalle = invoice_data.get("impuestos_detalle") or {}
    components: Dict[str, Decimal] = {}
    for item in detalle.get("traslados") or []:
        impuesto = str(item.get("impuesto") or "traslado").strip() or "traslado"
        key = f"traslado_{impuesto}"
        components[key] = components.get(key, Decimal("0.00")) + (
            _money(item.get("importe")) or Decimal("0.00")
        )
    for item in detalle.get("retenciones") or []:
        impuesto = str(item.get("impuesto") or "retencion").strip() or "retencion"
        key = f"retencion_{impuesto}"
        components[key] = components.get(key, Decimal("0.00")) + (
            _money(item.get("importe")) or Decimal("0.00")
        )
    if not components:
        total_traslados = _money(invoice_data.get("total_impuestos_trasladados"))
        if total_traslados is not None:
            components["traslados_total"] = total_traslados
    return {key: value.quantize(Decimal("0.01")) for key, value in components.items()}


def _user_tax_components(user_values: Dict[str, Any]) -> Dict[str, Decimal]:
    explicit = user_values.get("impuestos_detalle")
    if isinstance(explicit, dict):
        return _invoice_tax_components({"impuestos_detalle": explicit})

    components: Dict[str, Decimal] = {}
    for key, aliases in {
        "traslado_002": ("iva", "iva_trasladado", "impuesto_iva"),
        "retencion_001": ("isr_retenido", "retencion_isr"),
        "retencion_002": ("iva_retenido", "retencion_iva"),
        "traslados_total": ("impuestos_trasladados",),
    }.items():
        value = _money(_first_value(user_values, aliases))
        if value is not None:
            components[key] = value

    net_tax = _money(
        _first_value(
            user_values,
            ("impuestos_y_retenciones", "impuestos_retenciones", "tax_total"),
        )
    )
    if not components and net_tax is not None:
        components["neto_impuestos_retenciones"] = net_tax
    return components


def _compare_tax_components(
    invoice_data: Dict[str, Any],
    user_values: Dict[str, Any],
    blockers: List[ConsistencyIssue],
    field_comparisons: List[FieldComparison],
) -> None:
    expected = _invoice_tax_components(invoice_data)
    actual = _user_tax_components(user_values)
    for key in sorted(set(expected) & set(actual)):
        _compare_money(
            field_name=f"impuestos.{key}",
            expected=expected.get(key),
            actual=actual.get(key),
            blockers=blockers,
            field_comparisons=field_comparisons,
            blocker_code="TAX_COMPONENT_MISMATCH",
            blocker_message=(
                "Los impuestos desglosados capturados no coinciden con la factura."
            ),
        )


def validate_cfdi_consistency(
    *,
    invoice_data: Optional[Dict[str, Any]],
    user_values: Optional[Dict[str, Any]],
    autofill_values: Optional[Dict[str, Any]] = None,
    duplicate_found: bool = False,
    shared_invoice_confirmed: bool = False,
    invoice_required: bool = False,
) -> CFDIConsistencyResult:
    """Compare user-entered values against parsed CFDI/PDF and autofill data."""
    blockers: List[ConsistencyIssue] = []
    warnings: List[ConsistencyIssue] = []
    info: List[ConsistencyIssue] = []
    field_comparisons: List[FieldComparison] = []
    invoice = invoice_data or {}
    user = user_values or {}

    if not invoice:
        if invoice_required:
            blockers.append(
                ConsistencyIssue(
                    code="INVOICE_REQUIRED",
                    field="cfdi",
                    message="La factura es obligatoria para este flujo.",
                )
            )
        return CFDIConsistencyResult(
            status="blocked" if blockers else "ok",
            blockers=blockers,
        )

    expected_total = _money(invoice.get("total"))
    actual_total = _money(_first_value(user, ("total", "monto", "gasto_cantidad")))
    _compare_money(
        field_name="total",
        expected=expected_total,
        actual=actual_total,
        blockers=blockers,
        field_comparisons=field_comparisons,
        blocker_code="TOTAL_MISMATCH",
        blocker_message="El total capturado no coincide con el total de la factura.",
    )

    _compare_tax_components(invoice, user, blockers, field_comparisons)

    invoice_rfc = _norm_text(invoice.get("emisor_rfc"))
    user_rfc = _norm_text(
        _first_value(user, ("emisor_rfc", "rfc", "proveedor_rfc", "rfc_proveedor"))
    )
    if invoice_rfc and user_rfc and invoice_rfc != user_rfc:
        warnings.append(
            ConsistencyIssue(
                code="PROVIDER_RFC_MISMATCH",
                field="emisor_rfc",
                message="El RFC capturado no coincide con el RFC emisor.",
                expected=invoice_rfc,
                actual=user_rfc,
            )
        )

    invoice_name = _norm_text(invoice.get("emisor_nombre"))
    user_name = _norm_text(
        _first_value(user, ("emisor_nombre", "proveedor", "proveedor_nombre"))
    )
    if invoice_name and user_name and invoice_name != user_name:
        warnings.append(
            ConsistencyIssue(
                code="PROVIDER_NAME_MISMATCH",
                field="emisor_nombre",
                message=(
                    "El proveedor capturado no coincide exactamente con el emisor."
                ),
                expected=invoice_name,
                actual=user_name,
            )
        )

    invoice_date = _date_text(invoice.get("fecha"))
    user_date = _date_text(_first_value(user, ("fecha", "fecha_gasto")))
    if invoice_date and user_date and invoice_date != user_date:
        warnings.append(
            ConsistencyIssue(
                code="DATE_MISMATCH",
                field="fecha",
                message="La fecha capturada no coincide con la fecha del CFDI.",
                expected=invoice_date,
                actual=user_date,
            )
        )

    invoice_currency = _norm_text(invoice.get("moneda"))
    user_currency = _norm_text(_first_value(user, ("moneda", "currency")))
    if invoice_currency and user_currency and invoice_currency != user_currency:
        warnings.append(
            ConsistencyIssue(
                code="CURRENCY_MISMATCH",
                field="moneda",
                message="La moneda capturada no coincide con la factura.",
                expected=invoice_currency,
                actual=user_currency,
            )
        )

    invoice_concept = _norm_text(invoice.get("descripcion_concepto_principal"))
    user_concept = _norm_text(_first_value(user, ("concepto", "descripcion")))
    if invoice_concept and user_concept and invoice_concept not in user_concept:
        warnings.append(
            ConsistencyIssue(
                code="CONCEPT_MISMATCH",
                field="concepto",
                message="El concepto capturado difiere de la descripción fiscal.",
                expected=invoice_concept,
                actual=user_concept,
            )
        )

    if autofill_values:
        info.append(
            ConsistencyIssue(
                code="AUTOFILL_COMPARED",
                field="autofill",
                message="Se compararon datos prellenados contra factura y captura.",
            )
        )

    requires_confirmation = bool(duplicate_found and not shared_invoice_confirmed)
    if duplicate_found:
        if shared_invoice_confirmed:
            info.append(
                ConsistencyIssue(
                    code="SHARED_INVOICE_CONFIRMED",
                    field="cfdi_uuid",
                    message="La factura compartida fue confirmada explícitamente.",
                )
            )
        else:
            blockers.append(
                ConsistencyIssue(
                    code="DUPLICATE_INVOICE_REQUIRES_CONFIRMATION",
                    field="cfdi_uuid",
                    message=(
                        "La factura ya está ligada a otro gasto y requiere "
                        "confirmación explícita."
                    ),
                )
            )

    status = "blocked" if blockers else "warning" if warnings else "ok"
    return CFDIConsistencyResult(
        status=status,
        blockers=blockers,
        warnings=warnings,
        info=info,
        field_comparisons=field_comparisons,
        requires_shared_invoice_confirmation=requires_confirmation,
    )


__all__ = [
    "CFDIConsistencyResult",
    "ConsistencyIssue",
    "FieldComparison",
    "validate_cfdi_consistency",
]
