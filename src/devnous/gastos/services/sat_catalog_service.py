"""Local SAT catalog helpers used by gastos UI.

These are intentionally local constants for Sprint 1: forms should not depend
on a live SAT call to render common fiscal options.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List


@dataclass(frozen=True)
class SATCatalogItem:
    code: str
    label: str


USO_CFDI = [
    SATCatalogItem("G01", "Adquisición de mercancías"),
    SATCatalogItem("G03", "Gastos en general"),
    SATCatalogItem("I01", "Construcciones"),
    SATCatalogItem("I04", "Equipo de cómputo y accesorios"),
    SATCatalogItem("S01", "Sin efectos fiscales"),
]

REGIMEN_FISCAL = [
    SATCatalogItem("601", "General de Ley Personas Morales"),
    SATCatalogItem("603", "Personas Morales con Fines no Lucrativos"),
    SATCatalogItem("605", "Sueldos y Salarios e Ingresos Asimilados"),
    SATCatalogItem("612", "Personas Físicas con Actividades Empresariales"),
    SATCatalogItem("626", "Régimen Simplificado de Confianza"),
]

FORMA_PAGO = [
    SATCatalogItem("01", "Efectivo"),
    SATCatalogItem("02", "Cheque nominativo"),
    SATCatalogItem("03", "Transferencia electrónica de fondos"),
    SATCatalogItem("04", "Tarjeta de crédito"),
    SATCatalogItem("28", "Tarjeta de débito"),
    SATCatalogItem("99", "Por definir"),
]

METODO_PAGO = [
    SATCatalogItem("PUE", "Pago en una sola exhibición"),
    SATCatalogItem("PPD", "Pago en parcialidades o diferido"),
]

_CATALOGS: Dict[str, List[SATCatalogItem]] = {
    "uso_cfdi": USO_CFDI,
    "regimen_fiscal": REGIMEN_FISCAL,
    "forma_pago": FORMA_PAGO,
    "metodo_pago": METODO_PAGO,
}


def list_sat_catalogs() -> Dict[str, List[SATCatalogItem]]:
    return {key: list(value) for key, value in _CATALOGS.items()}


def get_sat_catalog(name: str) -> List[SATCatalogItem]:
    key = (name or "").strip().lower()
    if key not in _CATALOGS:
        raise KeyError(f"Catálogo SAT desconocido: {name}")
    return list(_CATALOGS[key])


def find_sat_catalog_item(name: str, code: str) -> SATCatalogItem | None:
    code_clean = (code or "").strip().upper()
    for item in get_sat_catalog(name):
        if item.code.upper() == code_clean:
            return item
    return None


def render_catalog_preview_rows(items: Iterable[SATCatalogItem]) -> str:
    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td><code>{item.code}</code></td>"
            f"<td>{item.label}</td>"
            "</tr>"
        )
    return "".join(rows)
