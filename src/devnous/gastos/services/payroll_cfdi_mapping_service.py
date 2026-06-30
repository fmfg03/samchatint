"""
SAT payroll catalog mapping and CFDI projection helpers for prenomina.

This module does not timbrar CFDI. It only projects internal payroll lines into
SAT-facing groups:
- percepciones
- deducciones
- otros pagos
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    PayrollConcept,
    PayrollEmployee,
    PayrollRunLine,
    PayrollSATCatalogEntry,
    PayrollSATConceptMapping,
)

_D = Decimal

SAT_GUIDE_URL = "https://www.sat.gob.mx/cs/Satellite?blobcol=urldata&blobkey=id&blobtable=MungoBlobs&blobwhere=1461173358253&ssbinary=true"

DEFAULT_SAT_CATALOG: tuple[dict[str, str], ...] = (
    {"sat_group": "percepcion", "code": "001", "description": "Sueldos, Salarios Rayas y Jornales"},
    {"sat_group": "percepcion", "code": "005", "description": "Fondo de ahorro"},
    {"sat_group": "percepcion", "code": "010", "description": "Premios por puntualidad"},
    {"sat_group": "percepcion", "code": "015", "description": "Becas para trabajadores y/o hijos"},
    {"sat_group": "percepcion", "code": "019", "description": "Horas extra"},
    {"sat_group": "percepcion", "code": "020", "description": "Prima dominical"},
    {"sat_group": "percepcion", "code": "021", "description": "Prima vacacional"},
    {"sat_group": "percepcion", "code": "029", "description": "Vales de despensa"},
    {"sat_group": "percepcion", "code": "036", "description": "Ayuda para transporte"},
    {"sat_group": "percepcion", "code": "037", "description": "Ayuda para renta"},
    {"sat_group": "percepcion", "code": "038", "description": "Otros ingresos por salarios"},
    {"sat_group": "percepcion", "code": "046", "description": "Ingresos asimilados a salarios"},
    {"sat_group": "percepcion", "code": "047", "description": "Alimentación"},
    {"sat_group": "percepcion", "code": "048", "description": "Habitación"},
    {"sat_group": "percepcion", "code": "049", "description": "Premios por asistencia"},
    {"sat_group": "deduccion", "code": "001", "description": "Seguridad social"},
    {"sat_group": "deduccion", "code": "002", "description": "ISR"},
    {"sat_group": "deduccion", "code": "004", "description": "Otros"},
    {"sat_group": "deduccion", "code": "007", "description": "Pensión alimenticia"},
    {"sat_group": "deduccion", "code": "009", "description": "Préstamos provenientes del Fondo Nacional de la Vivienda para los Trabajadores"},
    {"sat_group": "deduccion", "code": "011", "description": "Pago de abonos INFONACOT"},
    {"sat_group": "otro_pago", "code": "002", "description": "Subsidio para el empleo (efectivamente entregado al trabajador)"},
    {"sat_group": "otro_pago", "code": "999", "description": "Pagos distintos a los listados y que no deben considerarse como ingreso por sueldos, salarios o ingresos asimilados"},
)

DEFAULT_SAT_MAPPINGS: tuple[dict[str, str], ...] = (
    {"concept_key": "salary", "sat_group": "percepcion", "sat_code": "001"},
    {"concept_key": "salary_asimilado", "sat_group": "percepcion", "sat_code": "046"},
    {"concept_key": "overtime_double", "sat_group": "percepcion", "sat_code": "019"},
    {"concept_key": "overtime_triple", "sat_group": "percepcion", "sat_code": "019"},
    {"concept_key": "sunday_premium", "sat_group": "percepcion", "sat_code": "020"},
    {"concept_key": "vacation_premium", "sat_group": "percepcion", "sat_code": "021"},
    {"concept_key": "food_vouchers", "sat_group": "percepcion", "sat_code": "029"},
    {"concept_key": "meal_support_in_kind", "sat_group": "percepcion", "sat_code": "047"},
    {"concept_key": "meal_support_cash", "sat_group": "percepcion", "sat_code": "038"},
    {"concept_key": "transport_aid", "sat_group": "percepcion", "sat_code": "036"},
    {"concept_key": "transport_service", "sat_group": "percepcion", "sat_code": "036"},
    {"concept_key": "lodging_support", "sat_group": "percepcion", "sat_code": "048"},
    {"concept_key": "school_aid", "sat_group": "percepcion", "sat_code": "015"},
    {"concept_key": "punctuality_bonus", "sat_group": "percepcion", "sat_code": "010"},
    {"concept_key": "attendance_bonus", "sat_group": "percepcion", "sat_code": "049"},
    {"concept_key": "savings_fund", "sat_group": "percepcion", "sat_code": "005"},
    {"concept_key": "worked_rest_day", "sat_group": "percepcion", "sat_code": "038"},
    {"concept_key": "holiday_work", "sat_group": "percepcion", "sat_code": "038"},
    {"concept_key": "birth_aid", "sat_group": "percepcion", "sat_code": "038"},
    {"concept_key": "death_aid_legacy_v", "sat_group": "percepcion", "sat_code": "037"},
    {"concept_key": "other_perception", "sat_group": "percepcion", "sat_code": "038"},
    {"concept_key": "social_security_employee", "sat_group": "deduccion", "sat_code": "001"},
    {"concept_key": "isr_withheld", "sat_group": "deduccion", "sat_code": "002"},
    {"concept_key": "infonavit_credit_retention", "sat_group": "deduccion", "sat_code": "009"},
    {"concept_key": "fonacot_retention", "sat_group": "deduccion", "sat_code": "011"},
    {"concept_key": "alimony_retention", "sat_group": "deduccion", "sat_code": "007"},
    {"concept_key": "recurring_deduction", "sat_group": "deduccion", "sat_code": "004"},
    {"concept_key": "subsidy_applied", "sat_group": "otro_pago", "sat_code": "002"},
)

DEFAULT_CONCEPT_LABELS: dict[str, str] = {
    "salary": "Sueldo del período",
    "salary_asimilado": "Ingreso asimilado",
    "social_security_employee": "Seguridad social obrera",
    "isr_withheld": "ISR retenido",
    "subsidy_applied": "Subsidio al empleo aplicado",
    "infonavit_credit_retention": "Retención crédito INFONAVIT",
    "fonacot_retention": "Retención FONACOT",
    "alimony_retention": "Pensión alimenticia",
    "recurring_deduction": "Deducción recurrente",
}


def _money(value: Decimal) -> Decimal:
    return value.quantize(_D("0.01"), rounding=ROUND_HALF_UP)


def _dec(value: Any) -> Decimal:
    if value is None:
        return _D("0")
    if isinstance(value, Decimal):
        return value
    return _D(str(value))


def _employee_social_security_from_payload(payload: Mapping[str, Any]) -> Decimal:
    total = _D("0.00")
    components = dict(payload.get("components") or {})
    for component in components.values():
        total += _dec(dict(component or {}).get("employee_amount"))
    return _money(total)


def _default_mapping_dict() -> dict[tuple[str, str], str]:
    return {(row["sat_group"], row["concept_key"]): row["sat_code"] for row in DEFAULT_SAT_MAPPINGS}


def _default_catalog_dict() -> dict[tuple[str, str], dict[str, str]]:
    return {(row["sat_group"], row["code"]): row for row in DEFAULT_SAT_CATALOG}


@dataclass(frozen=True)
class PayrollCFDIProjectionEntry:
    sat_group: str
    concept_key: str
    internal_label: str
    sat_code: str
    sat_description: str
    amount: Decimal
    taxable_amount: Decimal
    exempt_amount: Decimal
    mapping_basis: str


@dataclass(frozen=True)
class PayrollCFDIProjection:
    perceptions: List[PayrollCFDIProjectionEntry]
    deductions: List[PayrollCFDIProjectionEntry]
    other_payments: List[PayrollCFDIProjectionEntry]


async def ensure_payroll_sat_seed(session: AsyncSession) -> None:
    existing_catalog = {
        (row.sat_group, row.code)
        for row in (
            await session.execute(select(PayrollSATCatalogEntry))
        ).scalars().all()
    }
    for item in DEFAULT_SAT_CATALOG:
        key = (item["sat_group"], item["code"])
        if key in existing_catalog:
            continue
        session.add(
            PayrollSATCatalogEntry(
                sat_group=item["sat_group"],
                code=item["code"],
                description=item["description"],
                official_source_url=SAT_GUIDE_URL,
                notes="Seed conservador para proyección CFDI de nómina.",
                active=True,
            )
        )

    existing_mappings = {
        (row.sat_group, row.concept_key)
        for row in (
            await session.execute(select(PayrollSATConceptMapping))
        ).scalars().all()
    }
    for item in DEFAULT_SAT_MAPPINGS:
        key = (item["sat_group"], item["concept_key"])
        if key in existing_mappings:
            continue
        session.add(
            PayrollSATConceptMapping(
                concept_key=item["concept_key"],
                sat_group=item["sat_group"],
                sat_code=item["sat_code"],
                active=True,
                mapping_basis="default_seed",
                notes="Mapeo default editable para proyección CFDI.",
            )
        )
    await session.commit()


async def load_payroll_sat_reference_data(
    session: AsyncSession,
) -> tuple[list[PayrollSATCatalogEntry], list[PayrollSATConceptMapping], list[PayrollConcept]]:
    catalog_rows = (
        await session.execute(
            select(PayrollSATCatalogEntry)
            .where(PayrollSATCatalogEntry.active.is_(True))
            .order_by(PayrollSATCatalogEntry.sat_group.asc(), PayrollSATCatalogEntry.code.asc())
        )
    ).scalars().all()
    mapping_rows = (
        await session.execute(
            select(PayrollSATConceptMapping)
            .where(PayrollSATConceptMapping.active.is_(True))
            .order_by(PayrollSATConceptMapping.sat_group.asc(), PayrollSATConceptMapping.concept_key.asc())
        )
    ).scalars().all()
    concept_rows = (
        await session.execute(
            select(PayrollConcept)
            .where(PayrollConcept.active.is_(True))
            .order_by(PayrollConcept.display_order.asc(), PayrollConcept.name.asc())
        )
    ).scalars().all()
    return list(catalog_rows), list(mapping_rows), list(concept_rows)


def resolve_sat_mapping(
    *,
    concept_key: str,
    sat_group: str,
    explicit_mapping_by_key: Mapping[tuple[str, str], PayrollSATConceptMapping],
    catalog_by_key: Mapping[tuple[str, str], PayrollSATCatalogEntry],
) -> tuple[str, str, str]:
    mapping = explicit_mapping_by_key.get((sat_group, concept_key))
    if mapping is not None:
        catalog = catalog_by_key.get((sat_group, mapping.sat_code))
        return (
            mapping.sat_code,
            catalog.description if catalog else "Clave SAT configurada",
            mapping.mapping_basis or "configured",
        )

    default_code = _default_mapping_dict().get((sat_group, concept_key))
    if default_code:
        catalog = catalog_by_key.get((sat_group, default_code))
        if catalog is None:
            fallback = _default_catalog_dict().get((sat_group, default_code), {})
            description = fallback.get("description", "Clave SAT default")
        else:
            description = catalog.description
        return default_code, description, "default"

    fallback_code = "999" if sat_group == "otro_pago" else "004" if sat_group == "deduccion" else "038"
    catalog = catalog_by_key.get((sat_group, fallback_code))
    if catalog is None:
        fallback = _default_catalog_dict().get((sat_group, fallback_code), {})
        description = fallback.get("description", "Clave SAT fallback")
    else:
        description = catalog.description
    return fallback_code, description, "fallback"


def _resolve_salary_concept_key(payroll_employee: Optional[PayrollEmployee], raw_concept_key: str) -> str:
    if raw_concept_key != "salary" or payroll_employee is None or payroll_employee.compensation_profile is None:
        return raw_concept_key
    regime = (payroll_employee.compensation_profile.compensation_regime or "").strip().lower()
    if "asimil" in regime:
        return "salary_asimilado"
    return raw_concept_key


async def build_payroll_cfdi_projection(
    session: AsyncSession,
    *,
    line: PayrollRunLine,
) -> PayrollCFDIProjection:
    await ensure_payroll_sat_seed(session)
    catalog_rows, mapping_rows, _ = await load_payroll_sat_reference_data(session)
    return build_payroll_cfdi_projection_with_refs(
        line=line,
        catalog_rows=catalog_rows,
        mapping_rows=mapping_rows,
    )


def build_payroll_cfdi_projection_with_refs(
    *,
    line: PayrollRunLine,
    catalog_rows: Sequence[PayrollSATCatalogEntry],
    mapping_rows: Sequence[PayrollSATConceptMapping],
) -> PayrollCFDIProjection:
    explicit_mapping_by_key = {(row.sat_group, row.concept_key): row for row in mapping_rows}
    catalog_by_key = {(row.sat_group, row.code): row for row in catalog_rows}

    perceptions: List[PayrollCFDIProjectionEntry] = []
    deductions: List[PayrollCFDIProjectionEntry] = []
    other_payments: List[PayrollCFDIProjectionEntry] = []

    for item in list((line.perceptions_json or {}).get("items") or []):
        raw_concept_key = str(item.get("concept_key") or "other_perception")
        concept_key = _resolve_salary_concept_key(line.payroll_employee, raw_concept_key)
        taxable_amount = _money(_dec(item.get("taxable_amount")))
        exempt_amount = _money(_dec(item.get("exempt_amount")))
        total_amount = _money(taxable_amount + exempt_amount)
        if total_amount == 0:
            continue
        sat_code, sat_description, basis = resolve_sat_mapping(
            concept_key=concept_key,
            sat_group="percepcion",
            explicit_mapping_by_key=explicit_mapping_by_key,
            catalog_by_key=catalog_by_key,
        )
        perceptions.append(
            PayrollCFDIProjectionEntry(
                sat_group="percepcion",
                concept_key=concept_key,
                internal_label=str(item.get("label") or DEFAULT_CONCEPT_LABELS.get(concept_key) or concept_key),
                sat_code=sat_code,
                sat_description=sat_description,
                amount=total_amount,
                taxable_amount=taxable_amount,
                exempt_amount=exempt_amount,
                mapping_basis=basis,
            )
        )

    social_security_employee = _employee_social_security_from_payload(dict(line.employer_charges_json or {}))
    if social_security_employee > 0:
        sat_code, sat_description, basis = resolve_sat_mapping(
            concept_key="social_security_employee",
            sat_group="deduccion",
            explicit_mapping_by_key=explicit_mapping_by_key,
            catalog_by_key=catalog_by_key,
        )
        deductions.append(
            PayrollCFDIProjectionEntry(
                sat_group="deduccion",
                concept_key="social_security_employee",
                internal_label=DEFAULT_CONCEPT_LABELS["social_security_employee"],
                sat_code=sat_code,
                sat_description=sat_description,
                amount=social_security_employee,
                taxable_amount=social_security_employee,
                exempt_amount=_D("0.00"),
                mapping_basis=basis,
            )
        )

    for item in list((line.deductions_json or {}).get("items") or []):
        concept_key = str(item.get("concept_key") or "recurring_deduction")
        amount = _money(abs(_dec(item.get("amount"))))
        if amount == 0:
            continue
        if concept_key == "subsidy_applied":
            sat_code, sat_description, basis = resolve_sat_mapping(
                concept_key=concept_key,
                sat_group="otro_pago",
                explicit_mapping_by_key=explicit_mapping_by_key,
                catalog_by_key=catalog_by_key,
            )
            other_payments.append(
                PayrollCFDIProjectionEntry(
                    sat_group="otro_pago",
                    concept_key=concept_key,
                    internal_label=str(item.get("label") or DEFAULT_CONCEPT_LABELS.get(concept_key) or concept_key),
                    sat_code=sat_code,
                    sat_description=sat_description,
                    amount=amount,
                    taxable_amount=_D("0.00"),
                    exempt_amount=amount,
                    mapping_basis=basis,
                )
            )
            continue
        sat_code, sat_description, basis = resolve_sat_mapping(
            concept_key=concept_key,
            sat_group="deduccion",
            explicit_mapping_by_key=explicit_mapping_by_key,
            catalog_by_key=catalog_by_key,
        )
        deductions.append(
            PayrollCFDIProjectionEntry(
                sat_group="deduccion",
                concept_key=concept_key,
                internal_label=str(item.get("label") or DEFAULT_CONCEPT_LABELS.get(concept_key) or concept_key),
                sat_code=sat_code,
                sat_description=sat_description,
                amount=amount,
                taxable_amount=amount,
                exempt_amount=_D("0.00"),
                mapping_basis=basis,
            )
        )

    if line.subsidy_applied and not any(item.concept_key == "subsidy_applied" for item in other_payments):
        subsidy_amount = _money(_dec(line.subsidy_applied))
        if subsidy_amount > 0:
            sat_code, sat_description, basis = resolve_sat_mapping(
                concept_key="subsidy_applied",
                sat_group="otro_pago",
                explicit_mapping_by_key=explicit_mapping_by_key,
                catalog_by_key=catalog_by_key,
            )
            other_payments.append(
                PayrollCFDIProjectionEntry(
                    sat_group="otro_pago",
                    concept_key="subsidy_applied",
                    internal_label=DEFAULT_CONCEPT_LABELS["subsidy_applied"],
                    sat_code=sat_code,
                    sat_description=sat_description,
                    amount=subsidy_amount,
                    taxable_amount=_D("0.00"),
                    exempt_amount=subsidy_amount,
                    mapping_basis=basis,
                )
            )

    return PayrollCFDIProjection(
        perceptions=perceptions,
        deductions=deductions,
        other_payments=other_payments,
    )


def build_cfdi_mapping_rows(
    *,
    concepts: Sequence[PayrollConcept],
    explicit_mappings: Sequence[PayrollSATConceptMapping],
    catalog_entries: Sequence[PayrollSATCatalogEntry],
) -> list[dict[str, Any]]:
    concept_name_by_key = {row.concept_key: row.name for row in concepts}
    catalog_by_key = {(row.sat_group, row.code): row for row in catalog_entries}
    explicit_by_key = {(row.sat_group, row.concept_key): row for row in explicit_mappings}

    all_keys = {(item["sat_group"], item["concept_key"]) for item in DEFAULT_SAT_MAPPINGS}
    all_keys.update((row.sat_group, row.concept_key) for row in explicit_mappings)

    rows: list[dict[str, Any]] = []
    for sat_group, concept_key in sorted(all_keys, key=lambda item: (item[0], item[1])):
        sat_code, sat_description, basis = resolve_sat_mapping(
            concept_key=concept_key,
            sat_group=sat_group,
            explicit_mapping_by_key=explicit_by_key,
            catalog_by_key=catalog_by_key,
        )
        rows.append(
            {
                "sat_group": sat_group,
                "concept_key": concept_key,
                "concept_label": concept_name_by_key.get(concept_key) or DEFAULT_CONCEPT_LABELS.get(concept_key) or concept_key,
                "sat_code": sat_code,
                "sat_description": sat_description,
                "mapping_basis": basis,
                "notes": (explicit_by_key.get((sat_group, concept_key)).notes if explicit_by_key.get((sat_group, concept_key)) else None) or "",
            }
        )
    return rows
