"""Seed payroll concepts and rule metadata from the legacy workbook plus official 2026 overrides."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import PayrollConcept, PayrollConceptRule, RegulatorySource
from .payroll_workbook_parser import WorkbookConceptSpec, parse_payroll_workbook


WORKBOOK_SOURCE_KEY = "legacy_payroll_workbook_nomina_sst"
LISR_SOURCE_KEY = "lisr_texto_vigente_2026"
SAT_CRITERION_SOURCE_KEY = "sat_criterio_prevision_social_2021"


@dataclass(frozen=True)
class PayrollConceptSeedSummary:
    concepts: int
    concept_rules: int
    regulatory_sources: int


_MANUAL_CONCEPTS: tuple[dict[str, Any], ...] = (
    {
        "concept_key": "attendance_bonus",
        "display_name": "Premio Asistencia",
        "input_mode": "amount",
        "tax_group": "incentive_bonus",
        "affects_sbc": True,
        "aliases": ["premio asistencia", "bono asistencia"],
        "metadata": {"origin": "manual_official_override"},
        "rule_payload": {
            "isr_treatment": {
                "mode": "fully_taxable",
                "legal_reference": "ISR conservador: incentivo sujeto a gravamen; no se trata como previsión social genérica por default.",
            },
            "sbc_treatment": {
                "mode": "partial_sbc_pct_per_period",
                "percent": 10.0,
                "legal_reference": "LSS art. 27 fr. VII",
            },
        },
        "taxable_mode": "fully_taxable",
        "sbc_mode": "include_partial",
        "source_key": "lss_texto_vigente_2026",
        "notes": "Excluye para SBC hasta 10% del SBC del periodo; el excedente integra.",
    },
    {
        "concept_key": "transport_aid",
        "display_name": "Ayuda de Transporte en Efectivo",
        "input_mode": "amount",
        "tax_group": "transport_support",
        "affects_sbc": True,
        "aliases": ["ayuda transporte", "bono transporte"],
        "metadata": {"origin": "manual_official_override", "assumption": "cash_support"},
        "rule_payload": {
            "isr_treatment": {
                "mode": "fully_taxable",
                "legal_reference": "Sin plan específico de previsión social documentado, se trata como gravado.",
            },
            "sbc_treatment": {
                "mode": "include_full",
                "legal_reference": "Tratamiento conservador para apoyos en efectivo.",
            },
        },
        "taxable_mode": "fully_taxable",
        "sbc_mode": "include_full",
        "source_key": LISR_SOURCE_KEY,
        "notes": "Tratamiento conservador: apoyo en efectivo grava ISR e integra SBC salvo configuración específica distinta.",
    },
    {
        "concept_key": "meal_support_cash",
        "display_name": "Apoyo de Alimentos en Efectivo",
        "input_mode": "amount",
        "tax_group": "meal_support",
        "affects_sbc": True,
        "aliases": ["apoyo alimentos efectivo", "ayuda comida efectivo"],
        "metadata": {"origin": "manual_official_override", "assumption": "cash_support"},
        "rule_payload": {
            "isr_treatment": {
                "mode": "fully_taxable",
                "legal_reference": "Apoyo de alimentos en efectivo sin esquema específico documentado se trata como gravado.",
            },
            "sbc_treatment": {
                "mode": "include_full",
                "legal_reference": "Tratamiento conservador para apoyo de alimentos en efectivo.",
            },
        },
        "taxable_mode": "fully_taxable",
        "sbc_mode": "include_full",
        "source_key": LISR_SOURCE_KEY,
        "notes": "No se trata como previsión social exenta por default si se entrega en efectivo.",
    },
    {
        "concept_key": "meal_support_in_kind",
        "display_name": "Apoyo de Alimentos en Especie",
        "input_mode": "amount",
        "tax_group": "meal_support",
        "affects_sbc": True,
        "aliases": ["comedor", "alimentos especie", "servicio comedor"],
        "metadata": {"origin": "manual_official_override", "assumption": "in_kind_service"},
        "rule_payload": {
            "isr_treatment": {
                "mode": "prevision_social_global",
                "requires_generality": True,
                "requires_documented_plan": True,
                "legal_reference": "Tratamiento posible de previsión social sujeto a generalidad y soporte documental.",
                "classification_warning": "Validar política interna y soporte del plan antes de exentar para ISR.",
            },
            "sbc_treatment": {
                "mode": "include_full",
                "requires_employee_copay_percent_at_least": 20.0,
                "legal_reference": "Tratamiento conservador; sin copago suficiente integra SBC.",
            },
        },
        "taxable_mode": "fully_taxable",
        "sbc_mode": "include_full",
        "source_key": SAT_CRITERION_SOURCE_KEY,
        "notes": "Sin copago o soporte formal suficiente, se trata conservadoramente.",
    },
    {
        "concept_key": "transport_service",
        "display_name": "Servicio de Transporte",
        "input_mode": "amount",
        "tax_group": "transport_support",
        "affects_sbc": True,
        "aliases": ["transporte empresa", "ruta personal", "camion personal"],
        "metadata": {"origin": "manual_official_override", "assumption": "service_in_kind"},
        "rule_payload": {
            "isr_treatment": {
                "mode": "prevision_social_global",
                "requires_generality": True,
                "requires_documented_plan": True,
                "legal_reference": "Tratamiento posible de previsión social sujeto a generalidad y soporte documental.",
            },
            "sbc_treatment": {
                "mode": "include_full",
                "requires_employee_copay_percent_at_least": 20.0,
                "legal_reference": "Tratamiento conservador; sin copago suficiente integra SBC.",
            },
        },
        "taxable_mode": "fully_taxable",
        "sbc_mode": "include_full",
        "source_key": SAT_CRITERION_SOURCE_KEY,
        "notes": "Sin copago y documentación formal, conviene tratarlo conservadoramente.",
    },
    {
        "concept_key": "lodging_support",
        "display_name": "Hospedaje",
        "input_mode": "amount",
        "tax_group": "lodging_support",
        "affects_sbc": True,
        "aliases": ["hospedaje", "alojamiento", "hotel personal"],
        "metadata": {"origin": "manual_official_override", "assumption": "service_in_kind"},
        "rule_payload": {
            "isr_treatment": {
                "mode": "prevision_social_global",
                "requires_generality": True,
                "requires_documented_plan": True,
                "legal_reference": "Tratamiento posible de previsión social sujeto a generalidad y soporte documental.",
                "classification_warning": "Validar si es herramienta de trabajo o beneficio personal antes de exentar.",
            },
            "sbc_treatment": {
                "mode": "include_full",
                "requires_employee_copay_percent_at_least": 20.0,
                "legal_reference": "Tratamiento conservador; si es beneficio personal sin copago, integra SBC.",
            },
        },
        "taxable_mode": "fully_taxable",
        "sbc_mode": "include_full",
        "source_key": SAT_CRITERION_SOURCE_KEY,
        "notes": "Distinguir hospedaje por servicio al puesto vs beneficio personal.",
    },
)


def _default_rule_payload(spec: WorkbookConceptSpec) -> dict[str, Any]:
    if spec.concept_key == "food_vouchers":
        return {
            "isr_treatment": {
                "mode": "prevision_social_global",
                "requires_generality": True,
                "requires_documented_plan": True,
                "requires_authorized_delivery_channel": True,
                "annual_global_limit_uma_multiplier": 7.0,
                "annual_restricted_exempt_uma_multiplier": 1.0,
                "legal_reference": "LISR art. 93 y generalidad de previsión social.",
                "classification_warning": "No exentar ISR si no existe monedero autorizado o política documentada.",
            },
            "sbc_treatment": {
                "mode": "partial_uma_pct_per_period",
                "percent": 40.0,
                "legal_reference": "LSS art. 27 fr. VI",
            },
        }
    if spec.concept_key == "punctuality_bonus":
        return {
            "isr_treatment": {
                "mode": "fully_taxable",
                "legal_reference": "Tratamiento conservador: incentivo sujeto a gravamen.",
            },
            "sbc_treatment": {
                "mode": "partial_sbc_pct_per_period",
                "percent": 10.0,
                "legal_reference": "LSS art. 27 fr. VII",
            },
        }
    if spec.concept_key == "savings_fund":
        return {
            "isr_treatment": {
                "mode": "conditional_savings_fund",
                "requires_equal_contribution": True,
                "max_percent_of_salary": 13.0,
                "annual_uma_multiplier_cap": 1.3,
                "legal_reference": "LISR art. 93 fr. XI",
                "requires_documented_plan": True,
            },
            "sbc_treatment": {
                "mode": "conditional_savings_fund",
                "requires_equal_contribution": True,
                "max_percent_of_salary": 13.0,
                "annual_uma_multiplier_cap": 1.3,
                "legal_reference": "LSS art. 27 fr. II",
                "requires_documented_plan": True,
            },
        }
    if spec.concept_key in {"sunday_premium", "vacation_premium", "overtime_double", "death_aid_legacy_v"}:
        return {
            "isr_treatment": {
                "mode": spec.taxable_mode,
                "formula_key": spec.exempt_formula_key,
                "legal_reference": "Regla heredada del workbook legado; pendiente de migración normativa completa por concepto.",
            },
            "sbc_treatment": {
                "mode": "ignore",
                "legal_reference": "Sin ajuste SBC específico en esta fase.",
            },
        }
    if spec.concept_key == "worked_rest_day":
        return {
            "isr_treatment": {
                "mode": spec.taxable_mode,
                "formula_key": spec.exempt_formula_key,
                "legal_reference": "Regla heredada del workbook legado.",
            },
            "sbc_treatment": {
                "mode": "include_full",
                "legal_reference": "El workbook lo suma a Otros SBC; se conserva ese comportamiento en esta fase.",
            },
        }
    return {
        "isr_treatment": {
            "mode": spec.taxable_mode,
            "formula_key": spec.exempt_formula_key,
            "legal_reference": "Regla heredada del workbook legado.",
        },
        "sbc_treatment": {
            "mode": "ignore",
            "legal_reference": "Sin ajuste SBC específico en esta fase.",
        },
    }


def _concept_tax_group(spec: WorkbookConceptSpec) -> str:
    mapping = {
        "salary": "salary",
        "overtime_double": "overtime",
        "overtime_triple": "overtime",
        "sunday_premium": "premium",
        "food_vouchers": "prevision_social",
        "punctuality_bonus": "incentive_bonus",
        "vacation_premium": "premium",
        "worked_rest_day": "workday_adjustment",
        "holiday_work": "workday_adjustment",
        "birth_aid": "aid",
        "death_aid_legacy_v": "aid",
        "school_aid": "aid",
        "savings_fund": "prevision_social",
        "other_perception": "other",
    }
    return mapping.get(spec.concept_key, "other")


def _concept_source_key(spec: WorkbookConceptSpec) -> str:
    if spec.concept_key in {"food_vouchers", "savings_fund"}:
        return LISR_SOURCE_KEY
    if spec.concept_key == "punctuality_bonus":
        return SAT_CRITERION_SOURCE_KEY
    return WORKBOOK_SOURCE_KEY


def _manual_concepts() -> Iterable[dict[str, Any]]:
    return _MANUAL_CONCEPTS


def build_payroll_concepts_seed_bundle(workbook_path: str | Path) -> Dict[str, Any]:
    summary = parse_payroll_workbook(workbook_path)

    sources = [
        {
            "source_key": WORKBOOK_SOURCE_KEY,
            "source_type": "legacy_workbook",
            "authority": "Cliente / Workbook legado",
            "title": summary.workbook_name,
            "url": f"file://{Path(workbook_path).resolve()}",
            "legal_reference": "Workbook heredado; no es fuente normativa.",
            "published_at": None,
            "effective_from": date(2024, 1, 1),
            "effective_to": None,
            "summary_json": {
                "payroll_type": summary.payroll_type,
                "warnings": summary.warnings,
                "legacy_uma_daily": summary.uma_daily,
                "legacy_smgv": summary.smgv,
            },
        },
        {
            "source_key": LISR_SOURCE_KEY,
            "source_type": "law",
            "authority": "Cámara de Diputados",
            "title": "Ley del Impuesto sobre la Renta",
            "url": "https://www.diputados.gob.mx/LeyesBiblio/pdf/LISR.pdf",
            "legal_reference": "Texto vigente 2026",
            "published_at": date(2026, 1, 1),
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "summary_json": {"scope": "ISR payroll concept exemptions and previsión social"},
        },
        {
            "source_key": SAT_CRITERION_SOURCE_KEY,
            "source_type": "tax_criterion",
            "authority": "SAT / Cámara de Diputados",
            "title": "Criterio sobre previsión social y generalidad",
            "url": "https://www.diputados.gob.mx/LeyesBiblio/ref/lih/LIH_cant05_11ene21.pdf",
            "legal_reference": "Criterio normativo vigente de previsión social y generalidad",
            "published_at": date(2021, 1, 11),
            "effective_from": date(2021, 1, 11),
            "effective_to": None,
            "summary_json": {"scope": "Generalidad y clasificación conservadora de previsión social"},
        },
    ]

    concepts: List[dict[str, Any]] = []
    rules: List[dict[str, Any]] = []

    for idx, spec in enumerate(summary.concepts, start=1):
        metadata = {
            "legacy_columns": {
                "input": spec.input_column,
                "taxable": spec.taxable_column,
                "exempt": spec.exempt_column,
            },
            "legacy_header": spec.input_header,
            "legacy_notes": spec.notes,
            "origin": "legacy_workbook",
        }
        concepts.append(
            {
                "concept_key": spec.concept_key,
                "name": spec.display_name,
                "concept_type": "perception",
                "input_mode": "amount",
                "tax_group": _concept_tax_group(spec),
                "affects_sbc": spec.affects_sbc or spec.concept_key in {"food_vouchers", "punctuality_bonus", "savings_fund"},
                "active": True,
                "display_order": idx * 10,
                "aliases_json": [spec.input_header] if spec.input_header else [],
                "metadata_json": metadata,
            }
        )
        rules.append(
            {
                "concept_key": spec.concept_key,
                "source_key": _concept_source_key(spec),
                "effective_from": date(2026, 1, 1),
                "effective_to": None,
                "taxable_mode": spec.taxable_mode,
                "exempt_formula_key": spec.exempt_formula_key if spec.exempt_formula_key != "none" else None,
                "taxable_formula_key": None,
                "sbc_mode": "include_full" if spec.affects_sbc else "ignore",
                "payload_json": _default_rule_payload(spec),
                "notes": spec.notes,
            }
        )

    base_order = len(concepts) * 10
    for idx, item in enumerate(_manual_concepts(), start=1):
        concepts.append(
            {
                "concept_key": item["concept_key"],
                "name": item["display_name"],
                "concept_type": "perception",
                "input_mode": item["input_mode"],
                "tax_group": item["tax_group"],
                "affects_sbc": item["affects_sbc"],
                "active": True,
                "display_order": base_order + idx * 10,
                "aliases_json": item.get("aliases") or [],
                "metadata_json": item.get("metadata") or {},
            }
        )
        rules.append(
            {
                "concept_key": item["concept_key"],
                "source_key": item["source_key"],
                "effective_from": date(2026, 1, 1),
                "effective_to": None,
                "taxable_mode": item["taxable_mode"],
                "exempt_formula_key": None,
                "taxable_formula_key": None,
                "sbc_mode": item["sbc_mode"],
                "payload_json": item["rule_payload"],
                "notes": item.get("notes"),
            }
        )

    return {
        "sources": sources,
        "concepts": concepts,
        "rules": rules,
        "workbook_warnings": summary.warnings,
    }


async def _upsert_source(session: AsyncSession, payload: Mapping[str, Any]) -> RegulatorySource:
    existing = await session.scalar(select(RegulatorySource).where(RegulatorySource.source_key == payload["source_key"]))
    if existing is None:
        existing = RegulatorySource(id=uuid4())
        session.add(existing)
    for field, value in payload.items():
        setattr(existing, field, value)
    await session.flush()
    return existing


async def _upsert_concept(session: AsyncSession, payload: Mapping[str, Any]) -> PayrollConcept:
    existing = await session.scalar(select(PayrollConcept).where(PayrollConcept.concept_key == payload["concept_key"]))
    if existing is None:
        existing = PayrollConcept(id=uuid4())
        session.add(existing)
    for field, value in payload.items():
        setattr(existing, field, value)
    await session.flush()
    return existing


async def _upsert_rule(
    session: AsyncSession,
    concept: PayrollConcept,
    source_lookup: Mapping[str, RegulatorySource],
    payload: Mapping[str, Any],
) -> PayrollConceptRule:
    existing = await session.scalar(
        select(PayrollConceptRule).where(
            PayrollConceptRule.concept_id == concept.id,
            PayrollConceptRule.effective_from == payload["effective_from"],
        )
    )
    if existing is None:
        existing = PayrollConceptRule(id=uuid4())
        session.add(existing)

    source = source_lookup.get(str(payload.get("source_key") or ""))
    existing.concept_id = concept.id
    existing.source_id = source.id if source else None
    existing.effective_from = payload["effective_from"]
    existing.effective_to = payload["effective_to"]
    existing.taxable_mode = payload["taxable_mode"]
    existing.exempt_formula_key = payload.get("exempt_formula_key")
    existing.taxable_formula_key = payload.get("taxable_formula_key")
    existing.sbc_mode = payload["sbc_mode"]
    existing.payload_json = payload.get("payload_json")
    existing.notes = payload.get("notes")
    await session.flush()
    return existing


async def seed_payroll_concepts_from_workbook(
    session: AsyncSession,
    workbook_path: str | Path,
) -> PayrollConceptSeedSummary:
    bundle = build_payroll_concepts_seed_bundle(workbook_path)

    sources: Dict[str, RegulatorySource] = {}
    for source_payload in bundle["sources"]:
        source = await _upsert_source(session, source_payload)
        sources[source.source_key] = source

    concept_entities: Dict[str, PayrollConcept] = {}
    for concept_payload in bundle["concepts"]:
        concept = await _upsert_concept(session, concept_payload)
        concept_entities[concept.concept_key] = concept

    for rule_payload in bundle["rules"]:
        concept = concept_entities[rule_payload["concept_key"]]
        await _upsert_rule(session, concept, sources, rule_payload)

    await session.commit()
    return PayrollConceptSeedSummary(
        concepts=len(bundle["concepts"]),
        concept_rules=len(bundle["rules"]),
        regulatory_sources=len(bundle["sources"]),
    )


__all__ = [
    "PayrollConceptSeedSummary",
    "build_payroll_concepts_seed_bundle",
    "seed_payroll_concepts_from_workbook",
]
