"""
Helpers for Mexican lodging-tax capture and estimation.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Dict, Optional


HOSPEDAJE_STATE_RATES_2026: Dict[str, float] = {
    "aguascalientes": 0.03,
    "baja california": 0.05,
    "baja california sur": 0.04,
    "campeche": 0.02,
    "chiapas": 0.02,
    "chihuahua": 0.04,
    "ciudad de mexico": 0.035,
    "coahuila": 0.03,
    "colima": 0.03,
    "durango": 0.03,
    "estado de mexico": 0.04,
    "guanajuato": 0.04,
    "guerrero": 0.04,
    "hidalgo": 0.025,
    "jalisco": 0.04,
    "michoacan": 0.03,
    "morelos": 0.0375,
    "nayarit": 0.05,
    "nuevo leon": 0.03,
    "oaxaca": 0.03,
    "puebla": 0.03,
    "queretaro": 0.035,
    "quintana roo": 0.05,
    "san luis potosi": 0.04,
    "sinaloa": 0.03,
    "sonora": 0.03,
    "tabasco": 0.03,
    "tamaulipas": 0.03,
    "tlaxcala": 0.02,
    "veracruz": 0.02,
    "yucatan": 0.045,
    "zacatecas": 0.03,
}

_HOSPEDAJE_STATE_ALIASES: Dict[str, str] = {
    "aguascalientes": "aguascalientes",
    "baja california sur": "baja california sur",
    "bcs": "baja california sur",
    "baja california": "baja california",
    "campeche": "campeche",
    "chiapas": "chiapas",
    "chihuahua": "chihuahua",
    "cdmx": "ciudad de mexico",
    "ciudad de mexico": "ciudad de mexico",
    "coahuila": "coahuila",
    "colima": "colima",
    "durango": "durango",
    "edo mex": "estado de mexico",
    "edomex": "estado de mexico",
    "estado de mexico": "estado de mexico",
    "guanajuato": "guanajuato",
    "guerrero": "guerrero",
    "hidalgo": "hidalgo",
    "jalisco": "jalisco",
    "michoacan": "michoacan",
    "morelos": "morelos",
    "nayarit": "nayarit",
    "nuevo leon": "nuevo leon",
    "oaxaca": "oaxaca",
    "puebla": "puebla",
    "qro": "queretaro",
    "queretaro": "queretaro",
    "quintana roo": "quintana roo",
    "qroo": "quintana roo",
    "san luis potosi": "san luis potosi",
    "slp": "san luis potosi",
    "sinaloa": "sinaloa",
    "sonora": "sonora",
    "tabasco": "tabasco",
    "tamaulipas": "tamaulipas",
    "tlaxcala": "tlaxcala",
    "veracruz": "veracruz",
    "yucatan": "yucatan",
    "zacatecas": "zacatecas",
}


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_hospedaje_rate(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    if rate > 1:
        rate = rate / 100.0
    return round(rate, 6)


def is_hospedaje_related(*values: Any) -> bool:
    haystack = " ".join(_normalize_text(value) for value in values if value)
    return any(
        token in haystack
        for token in (
            "hospedaje",
            "hotel",
            "hotelera",
            "hostal",
            "airbnb",
            "alojamiento",
            "habitacion",
        )
    )


def normalize_hospedaje_state(value: Any) -> Optional[str]:
    haystack = _normalize_text(value)
    if not haystack:
        return None
    for alias in sorted(_HOSPEDAJE_STATE_ALIASES.keys(), key=len, reverse=True):
        if alias == haystack or alias in haystack:
            return _HOSPEDAJE_STATE_ALIASES[alias]
    return None


def infer_hospedaje_state_from_text(*values: Any) -> Optional[str]:
    haystack = " ".join(_normalize_text(value) for value in values if value)
    if not haystack:
        return None
    for alias in sorted(_HOSPEDAJE_STATE_ALIASES.keys(), key=len, reverse=True):
        if alias and alias in haystack:
            return _HOSPEDAJE_STATE_ALIASES[alias]
    return None


def resolve_hospedaje_local_tax(
    expense: Any,
    *,
    cfdi_report: Optional[Any] = None,
    iva_amount: Optional[float] = None,
    retenciones_total: float = 0.0,
) -> Dict[str, Any]:
    explicit_amount = _money(getattr(expense, "hospedaje_impuesto_monto", None))
    explicit_state = normalize_hospedaje_state(
        getattr(expense, "hospedaje_entidad_fiscal", None)
    )
    explicit_rate = normalize_hospedaje_rate(
        getattr(expense, "hospedaje_tasa_impuesto", None)
    )
    confirmed = bool(getattr(expense, "hospedaje_impuesto_confirmado", False))
    inferred_state = explicit_state or infer_hospedaje_state_from_text(
        getattr(expense, "concepto", None),
        getattr(expense, "proyecto", None),
        getattr(cfdi_report, "descripcion_concepto_principal", None),
        getattr(cfdi_report, "emisor_nombre", None),
    )
    lodging_related = bool(
        explicit_amount > 0
        or explicit_state
        or explicit_rate
        or is_hospedaje_related(
            getattr(expense, "concepto", None),
            getattr(expense, "proyecto", None),
            getattr(cfdi_report, "descripcion_concepto_principal", None),
            getattr(cfdi_report, "emisor_nombre", None),
        )
    )
    rate = explicit_rate or HOSPEDAJE_STATE_RATES_2026.get(inferred_state or "")
    total_amount = _money(
        getattr(expense, "gasto_cantidad", None) or getattr(cfdi_report, "total", None)
    )
    effective_iva = _money(
        iva_amount if iva_amount is not None else getattr(expense, "iva", None)
    )
    gross_before_iva = _money(total_amount - effective_iva + _money(retenciones_total))
    cfdi_subtotal = _money(getattr(cfdi_report, "subtotal", None))

    estimated_amount = 0.0
    if lodging_related and rate and gross_before_iva > 0:
        if cfdi_subtotal > 0 and gross_before_iva >= cfdi_subtotal:
            expected_amount = _money(cfdi_subtotal * rate)
            diff_amount = _money(gross_before_iva - cfdi_subtotal)
            tolerance = max(10.0, round(expected_amount * 0.5, 2))
            if diff_amount > 0 and abs(diff_amount - expected_amount) <= tolerance:
                estimated_amount = diff_amount
            else:
                estimated_amount = expected_amount
        else:
            estimated_base = _money(gross_before_iva / (1.0 + rate))
            estimated_amount = _money(gross_before_iva - estimated_base)

    amount = explicit_amount if explicit_amount > 0 else estimated_amount
    source = "none"
    if explicit_amount > 0:
        source = "explicit_amount"
    elif explicit_rate or explicit_state or confirmed:
        source = "explicit_context_estimated" if amount > 0 else "explicit_context"
    elif amount > 0:
        source = "inferred_estimated"

    notes = []
    if explicit_state and not explicit_rate and inferred_state:
        notes.append("Se aplicó la tasa 2026 de la entidad capturada.")
    if not explicit_state and inferred_state:
        notes.append("La entidad fiscal de hospedaje fue inferida del texto del expediente.")
    if lodging_related and amount <= 0 and confirmed:
        notes.append("El gasto fue marcado como hospedaje confirmado, pero falta monto del impuesto local.")

    return {
        "lodging_related": lodging_related,
        "entity": inferred_state,
        "rate": rate,
        "rate_pct": round(rate * 100, 4) if rate else None,
        "amount": _money(amount),
        "confirmed": confirmed,
        "source": source,
        "gross_before_iva": gross_before_iva,
        "notes": notes,
    }


__all__ = [
    "HOSPEDAJE_STATE_RATES_2026",
    "infer_hospedaje_state_from_text",
    "is_hospedaje_related",
    "normalize_hospedaje_rate",
    "normalize_hospedaje_state",
    "resolve_hospedaje_local_tax",
]
