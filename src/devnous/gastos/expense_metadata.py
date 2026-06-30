"""Validation and formatting helpers for expense workflow metadata."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Iterable, List, Mapping, Optional

DEFAULT_CURRENCY = "MXN"
COMMON_CURRENCIES = ("MXN", "USD", "EUR")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_CANTIDAD_LETRA_UNITS = {
    "MXN": "PESOS",
    "USD": "DOLARES",
    "EUR": "EUROS",
}


def normalize_currency(value: Optional[str]) -> str:
    currency = (value or DEFAULT_CURRENCY).strip().upper() or DEFAULT_CURRENCY
    if not _CURRENCY_RE.fullmatch(currency):
        raise ValueError("La moneda debe ser un código ISO de tres letras.")
    return currency


def normalize_edition(
    value: object, *, default_current_year: bool = False
) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return date.today().year if default_current_year else None
    try:
        edition = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("La edición debe ser un año de cuatro dígitos.") from exc
    if edition < 1900 or edition > 2100:
        raise ValueError("La edición debe ser un año entre 1900 y 2100.")
    return edition


def configured_categories(tournament: object) -> List[str]:
    raw = getattr(tournament, "categorias", None)
    if not isinstance(raw, list):
        return []
    return list(
        dict.fromkeys(
            str(item).strip()
            for item in raw
            if item is not None and str(item).strip()
        )
    )


def normalize_categories(values: Iterable[object], tournament: object) -> List[str]:
    allowed = configured_categories(tournament)
    allowed_set = set(allowed)
    selected = list(
        dict.fromkeys(
            str(item).strip()
            for item in values
            if item is not None and str(item).strip()
        )
    )
    invalid = [item for item in selected if item not in allowed_set]
    if invalid:
        raise ValueError(
            "Una o más categorías no corresponden al proyecto seleccionado."
        )
    return selected


def cantidad_letra_currency_parts(currency: Optional[str] = None) -> tuple[str, str]:
    """Return (unit label, ISO code) for CANTIDAD CON LETRA suffixes."""
    code = normalize_currency(currency)
    unit = _CANTIDAD_LETRA_UNITS.get(code, code)
    return unit, code


def currency_for(record: object) -> str:
    try:
        return normalize_currency(getattr(record, "currency", None))
    except ValueError:
        return DEFAULT_CURRENCY


def format_solicitud_proyecto_display(
    proyecto: Optional[str] = None,
    *,
    fase: Optional[str] = None,
    categorias: Optional[Iterable[object]] = None,
    edicion: Optional[object] = None,
) -> str:
    """Single PROYECTO cell value: name, subproyecto, categorías, edición."""
    parts: List[str] = []
    base = (proyecto or "").strip()
    if base:
        parts.append(base)
    fase_st = (fase or "").strip()
    if fase_st:
        parts.append(fase_st)
    cats = [
        str(item).strip()
        for item in (categorias or [])
        if item is not None and str(item).strip()
    ]
    if cats:
        parts.append(", ".join(cats))
    if edicion is not None and str(edicion).strip():
        parts.append(str(edicion).strip())
    return " · ".join(parts)


def group_amounts_by_currency(
    records: Iterable[object],
    amount_attr: str,
) -> Mapping[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for record in records:
        currency = currency_for(record)
        amount = Decimal(str(getattr(record, amount_attr, 0) or 0))
        totals[currency] = totals.get(currency, Decimal("0")) + amount
    return totals
