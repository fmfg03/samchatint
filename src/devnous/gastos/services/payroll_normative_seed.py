"""
Seed bundle for payroll normative baseline valid for January 2026.

Primary official sources:
- SAT / DOF Anexo 8 RMF 2026 for ISR retentions
- DOF 2025-12-31 for subsidy to employment
- INEGI UMA 2025 / 2026 bulletins
- CONASAMI 2026 salary minimum resolution
- Cámara de Diputados LFT / LSS texts
- INFONAVIT official help portal for employer contribution rate

The goal of this module is not to implement payroll calculation yet.
It only seeds the normative baseline required by prenómina and payroll runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LaborRuleSnapshot, RegulatorySource, SocialSecurityTable, TaxTableISR, TaxTableSubsidioEmpleo


_D = Decimal


def _money(value: Decimal) -> float:
    return float(value.quantize(_D("0.01"), rounding=ROUND_HALF_UP))


def _rate(value: str) -> float:
    return float(_D(value))


@dataclass(frozen=True)
class SeedSummary:
    regulatory_sources: int
    labor_rule_snapshots: int
    tax_tables_isr: int
    tax_tables_subsidio_empleo: int
    social_security_tables: int


def _isr_rows() -> Dict[str, Sequence[tuple[float, Optional[float], float, float]]]:
    return {
        "anual": (
            (0.01, 10135.11, 0.00, 1.92),
            (10135.12, 86022.11, 194.59, 6.40),
            (86022.12, 151176.19, 5051.37, 10.88),
            (151176.20, 175735.66, 12140.13, 16.00),
            (175735.67, 210403.69, 16069.64, 17.92),
            (210403.70, 424353.97, 22282.14, 21.36),
            (424353.98, 668840.14, 67981.92, 23.52),
            (668840.15, 1276925.98, 125485.07, 30.00),
            (1276925.99, 1702567.97, 307910.81, 32.00),
            (1702567.98, 5107703.92, 444116.23, 34.00),
            (5107703.93, None, 1601862.46, 35.00),
        ),
        "diaria": (
            (0.01, 27.78, 0.00, 1.92),
            (27.79, 235.81, 0.53, 6.40),
            (235.82, 414.41, 13.85, 10.88),
            (414.42, 481.73, 33.28, 16.00),
            (481.74, 576.76, 44.05, 17.92),
            (576.77, 1163.25, 61.08, 21.36),
            (1163.26, 1833.44, 186.35, 23.52),
            (1833.45, 3500.35, 343.98, 30.00),
            (3500.36, 4667.13, 844.05, 32.00),
            (4667.14, 14001.38, 1217.42, 34.00),
            (14001.39, None, 4391.07, 35.00),
        ),
        "semanal": (
            (0.01, 194.46, 0.00, 1.92),
            (194.47, 1650.67, 3.71, 6.40),
            (1650.68, 2900.87, 96.95, 10.88),
            (2900.88, 3372.11, 232.96, 16.00),
            (3372.12, 4037.32, 308.35, 17.92),
            (4037.33, 8142.75, 427.56, 21.36),
            (8142.76, 12834.08, 1304.45, 23.52),
            (12834.09, 24502.45, 2407.86, 30.00),
            (24502.46, 32669.91, 5908.35, 32.00),
            (32669.92, 98009.66, 8521.94, 34.00),
            (98009.67, None, 30737.49, 35.00),
        ),
        "decenal": (
            (0.01, 277.80, 0.00, 1.92),
            (277.81, 2358.10, 5.30, 6.40),
            (2358.11, 4144.10, 138.50, 10.88),
            (4144.11, 4817.30, 332.80, 16.00),
            (4817.31, 5767.60, 440.50, 17.92),
            (5767.61, 11632.50, 610.80, 21.36),
            (11632.51, 18334.40, 1863.50, 23.52),
            (18334.41, 35003.50, 3439.80, 30.00),
            (35003.51, 46671.30, 8440.50, 32.00),
            (46671.31, 140013.80, 12174.20, 34.00),
            (140013.81, None, 43910.70, 35.00),
        ),
        "quincenal": (
            (0.01, 416.70, 0.00, 1.92),
            (416.71, 3537.15, 7.95, 6.40),
            (3537.16, 6216.15, 207.75, 10.88),
            (6216.16, 7225.95, 499.20, 16.00),
            (7225.96, 8651.40, 660.75, 17.92),
            (8651.41, 17448.75, 916.20, 21.36),
            (17448.76, 27501.60, 2795.25, 23.52),
            (27501.61, 52505.25, 5159.70, 30.00),
            (52505.26, 70006.95, 12660.75, 32.00),
            (70006.96, 210020.70, 18261.30, 34.00),
            (210020.71, None, 65866.05, 35.00),
        ),
        "mensual": (
            (0.01, 844.59, 0.00, 1.92),
            (844.60, 7168.51, 16.22, 6.40),
            (7168.52, 12598.02, 420.95, 10.88),
            (12598.03, 14644.64, 1011.68, 16.00),
            (14644.65, 17533.64, 1339.14, 17.92),
            (17533.65, 35362.83, 1856.84, 21.36),
            (35362.84, 55736.68, 5665.16, 23.52),
            (55736.69, 106410.50, 10457.09, 30.00),
            (106410.51, 141880.66, 25659.23, 32.00),
            (141880.67, 425641.99, 37009.69, 34.00),
            (425642.00, None, 133488.54, 35.00),
        ),
    }


def build_payroll_normative_seed_bundle() -> Dict[str, Any]:
    uma_2025_daily = _D("113.14")
    uma_2025_monthly = _D("3439.46")
    uma_2025_annual = _D("41273.52")
    uma_2026_daily = _D("117.31")
    uma_2026_monthly = _D("3566.22")
    uma_2026_annual = _D("42794.64")

    subsidy_jan_2026 = _money(uma_2025_monthly * _D("0.1559"))
    subsidy_feb_2026 = _money(uma_2026_monthly * _D("0.1502"))

    sources = [
        {
            "source_key": "sat_anexo8_rmf_2026",
            "source_type": "tax_table",
            "authority": "SAT",
            "title": "Anexo 8 de la Resolución Miscelánea Fiscal para 2026",
            "url": "https://www.sat.gob.mx/minisitio/NormatividadRMFyRGCE/documentos2026/rmf/anexos/Anexo-8-RMF-2026_DOF-28122025.pdf",
            "legal_reference": "DOF 28/12/2025, Anexo 8 RMF 2026",
            "published_at": date(2025, 12, 28),
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "summary_json": {
                "scope": "ISR payroll retentions",
                "periodicities": ["anual", "diaria", "semanal", "decenal", "quincenal", "mensual"],
            },
        },
        {
            "source_key": "dof_subsidio_empleo_2025_12_31",
            "source_type": "tax_decree",
            "authority": "DOF",
            "title": "Decreto por el que se modifica el diverso que otorga el subsidio para el empleo",
            "url": "https://www.dof.gob.mx/nota_detalle.php?codigo=5777649&fecha=31/12/2025",
            "legal_reference": "DOF 31/12/2025",
            "published_at": date(2025, 12, 31),
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "summary_json": {
                "monthly_income_limit": 11492.66,
                "jan_2026_percent": 15.59,
                "feb_2026_percent": 15.02,
            },
        },
        {
            "source_key": "inegi_uma_2025",
            "source_type": "indicator",
            "authority": "INEGI",
            "title": "Unidad de Medida y Actualización 2025",
            "url": "https://www.dof.gob.mx/nota_detalle.php?codigo=5746930&fecha=10/01/2025&print=true",
            "legal_reference": "DOF 10/01/2025",
            "published_at": date(2025, 1, 10),
            "effective_from": date(2025, 2, 1),
            "effective_to": date(2026, 1, 31),
            "summary_json": {
                "daily": _money(uma_2025_daily),
                "monthly": _money(uma_2025_monthly),
                "annual": _money(uma_2025_annual),
            },
        },
        {
            "source_key": "inegi_uma_2026",
            "source_type": "indicator",
            "authority": "INEGI",
            "title": "Unidad de Medida y Actualización 2026",
            "url": "https://www.inegi.org.mx/contenidos/saladeprensa/boletines/2026/uma/uma2026.pdf",
            "legal_reference": "INEGI boletín 08/01/2026",
            "published_at": date(2026, 1, 8),
            "effective_from": date(2026, 2, 1),
            "effective_to": None,
            "summary_json": {
                "daily": _money(uma_2026_daily),
                "monthly": _money(uma_2026_monthly),
                "annual": _money(uma_2026_annual),
            },
        },
        {
            "source_key": "conasami_salario_minimo_2026",
            "source_type": "labor_resolution",
            "authority": "CONASAMI",
            "title": "Incremento a los Salarios Mínimos para 2026",
            "url": "https://www.gob.mx/conasami/articulos/incremento-a-los-salarios-minimos-para-2026?idiom=es",
            "legal_reference": "CONASAMI, vigente desde 01/01/2026",
            "published_at": date(2025, 12, 1),
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "summary_json": {
                "general_daily": 315.04,
                "zlfn_daily": 440.87,
                "mir_general": 17.01,
                "increase_general_percent": 6.5,
                "increase_zlfn_percent": 5.0,
            },
        },
        {
            "source_key": "lft_texto_vigente_2026_01_15",
            "source_type": "law",
            "authority": "Cámara de Diputados",
            "title": "Ley Federal del Trabajo",
            "url": "https://www.diputados.gob.mx/LeyesBiblio/pdf/LFT.pdf",
            "legal_reference": "Última reforma DOF 15/01/2026",
            "published_at": date(2026, 1, 15),
            "effective_from": date(2026, 1, 15),
            "effective_to": None,
            "summary_json": {"scope": "payroll labor minimums"},
        },
        {
            "source_key": "lss_texto_vigente_2026",
            "source_type": "law",
            "authority": "Cámara de Diputados",
            "title": "Ley del Seguro Social",
            "url": "https://www.diputados.gob.mx/LeyesBiblio/pdf/LSS.pdf",
            "legal_reference": "Texto vigente 2026",
            "published_at": date(2026, 1, 1),
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "summary_json": {"scope": "social security employer/employee rates"},
        },
        {
            "source_key": "infonavit_aportacion_patronal",
            "source_type": "official_guidance",
            "authority": "INFONAVIT",
            "title": "Aportaciones patronales al INFONAVIT",
            "url": "https://portalmx.infonavit.org.mx/wps/portal/infonavitmx/mx2/derechohabientes/centro_ayuda/11_aportaciones_credito/10_aportaciones_patron/",
            "legal_reference": "Ley del INFONAVIT, aportación patronal 5% del SBC",
            "published_at": date(2026, 1, 1),
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "summary_json": {"employer_rate_percent": 5.0},
        },
    ]

    labor_rules = [
        {
            "source_key": "inegi_uma_2025",
            "rule_key": "uma_daily",
            "category": "uma",
            "title": "UMA diaria 2025",
            "legal_reference": "DOF 10/01/2025",
            "effective_from": date(2025, 2, 1),
            "effective_to": date(2026, 1, 31),
            "numeric_value": _money(uma_2025_daily),
            "unit": "mxn_daily",
            "payload_json": None,
            "notes": "Vigente para enero 2026 hasta el 31/01/2026.",
        },
        {
            "source_key": "inegi_uma_2025",
            "rule_key": "uma_monthly",
            "category": "uma",
            "title": "UMA mensual 2025",
            "legal_reference": "DOF 10/01/2025",
            "effective_from": date(2025, 2, 1),
            "effective_to": date(2026, 1, 31),
            "numeric_value": _money(uma_2025_monthly),
            "unit": "mxn_monthly",
            "payload_json": None,
            "notes": "Base para subsidio al empleo de enero 2026.",
        },
        {
            "source_key": "inegi_uma_2025",
            "rule_key": "uma_annual",
            "category": "uma",
            "title": "UMA anual 2025",
            "legal_reference": "DOF 10/01/2025",
            "effective_from": date(2025, 2, 1),
            "effective_to": date(2026, 1, 31),
            "numeric_value": _money(uma_2025_annual),
            "unit": "mxn_annual",
            "payload_json": None,
            "notes": None,
        },
        {
            "source_key": "inegi_uma_2026",
            "rule_key": "uma_daily",
            "category": "uma",
            "title": "UMA diaria 2026",
            "legal_reference": "INEGI 08/01/2026",
            "effective_from": date(2026, 2, 1),
            "effective_to": None,
            "numeric_value": _money(uma_2026_daily),
            "unit": "mxn_daily",
            "payload_json": None,
            "notes": None,
        },
        {
            "source_key": "inegi_uma_2026",
            "rule_key": "uma_monthly",
            "category": "uma",
            "title": "UMA mensual 2026",
            "legal_reference": "INEGI 08/01/2026",
            "effective_from": date(2026, 2, 1),
            "effective_to": None,
            "numeric_value": _money(uma_2026_monthly),
            "unit": "mxn_monthly",
            "payload_json": None,
            "notes": "Base general del subsidio al empleo desde febrero 2026.",
        },
        {
            "source_key": "inegi_uma_2026",
            "rule_key": "uma_annual",
            "category": "uma",
            "title": "UMA anual 2026",
            "legal_reference": "INEGI 08/01/2026",
            "effective_from": date(2026, 2, 1),
            "effective_to": None,
            "numeric_value": _money(uma_2026_annual),
            "unit": "mxn_annual",
            "payload_json": None,
            "notes": None,
        },
        {
            "source_key": "conasami_salario_minimo_2026",
            "rule_key": "salary_minimum_general_daily",
            "category": "salary_minimum",
            "title": "Salario mínimo general 2026",
            "legal_reference": "CONASAMI 2026",
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "numeric_value": 315.04,
            "unit": "mxn_daily",
            "payload_json": {"zone": "general"},
            "notes": None,
        },
        {
            "source_key": "conasami_salario_minimo_2026",
            "rule_key": "salary_minimum_zlfn_daily",
            "category": "salary_minimum",
            "title": "Salario mínimo ZLFN 2026",
            "legal_reference": "CONASAMI 2026",
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "numeric_value": 440.87,
            "unit": "mxn_daily",
            "payload_json": {"zone": "zlfn"},
            "notes": None,
        },
        {
            "source_key": "lft_texto_vigente_2026_01_15",
            "rule_key": "aguinaldo_days_min",
            "category": "lft",
            "title": "Aguinaldo mínimo",
            "legal_reference": "LFT art. 87",
            "effective_from": date(2026, 1, 15),
            "effective_to": None,
            "numeric_value": 15.0,
            "unit": "days",
            "payload_json": None,
            "notes": "Mínimo anual legal.",
        },
        {
            "source_key": "lft_texto_vigente_2026_01_15",
            "rule_key": "vacation_premium_rate_min",
            "category": "lft",
            "title": "Prima vacacional mínima",
            "legal_reference": "LFT art. 80",
            "effective_from": date(2026, 1, 15),
            "effective_to": None,
            "numeric_value": 25.0,
            "unit": "percent",
            "payload_json": None,
            "notes": None,
        },
        {
            "source_key": "lft_texto_vigente_2026_01_15",
            "rule_key": "vacation_days_schedule",
            "category": "lft",
            "title": "Tabla mínima de vacaciones por antigüedad",
            "legal_reference": "LFT art. 76",
            "effective_from": date(2026, 1, 15),
            "effective_to": None,
            "numeric_value": None,
            "unit": None,
            "payload_json": {
                "years_1_to_5": {"1": 12, "2": 14, "3": 16, "4": 18, "5": 20},
                "after_5_years_every_5": {
                    "6_to_10": 22,
                    "11_to_15": 24,
                    "16_to_20": 26,
                    "21_to_25": 28,
                    "26_to_30": 30,
                    "31_to_35": 32,
                },
            },
            "notes": "Reforma de vacaciones vigente.",
        },
    ]

    isr_rows: List[Dict[str, Any]] = []
    for periodicity, brackets in _isr_rows().items():
        for idx, (lower, upper, fee, rate) in enumerate(brackets, start=1):
            isr_rows.append(
                {
                    "source_key": "sat_anexo8_rmf_2026",
                    "regime_key": "payroll_retention",
                    "periodicity": periodicity,
                    "effective_from": date(2026, 1, 1),
                    "effective_to": None,
                    "row_order": idx,
                    "lower_limit": lower,
                    "upper_limit": upper,
                    "fixed_fee": fee,
                    "marginal_rate": rate,
                }
            )

    subsidio_rows = [
        {
            "source_key": "dof_subsidio_empleo_2025_12_31",
            "periodicity": "mensual",
            "effective_from": date(2026, 1, 1),
            "effective_to": date(2026, 1, 31),
            "income_limit": 11492.66,
            "subsidy_amount": subsidy_jan_2026,
            "subsidy_percent": 15.59,
            "uma_value": _money(uma_2025_monthly),
            "uma_periodicity": "mensual",
            "legal_reference": "DOF 31/12/2025",
            "notes": "Regla transitoria para enero 2026: 15.59% de la UMA mensual 2025.",
        },
        {
            "source_key": "dof_subsidio_empleo_2025_12_31",
            "periodicity": "mensual",
            "effective_from": date(2026, 2, 1),
            "effective_to": None,
            "income_limit": 11492.66,
            "subsidy_amount": subsidy_feb_2026,
            "subsidy_percent": 15.02,
            "uma_value": _money(uma_2026_monthly),
            "uma_periodicity": "mensual",
            "legal_reference": "DOF 31/12/2025",
            "notes": "Regla general desde febrero 2026: 15.02% de la UMA mensual 2026.",
        },
    ]

    social_security_rows = [
        {
            "source_key": "lss_texto_vigente_2026",
            "component_key": "em_cuota_fija_patron",
            "component_name": "Enfermedades y maternidad - cuota fija patrón",
            "branch": "enfermedades_maternidad",
            "calculation_mode": "uma_percent",
            "base_type": "uma",
            "employer_rate": 20.40,
            "employee_rate": None,
            "fixed_amount": None,
            "min_uma": None,
            "max_uma": None,
            "legal_reference": "LSS art. 106 fr. I",
            "formula_json": {"applies_on": "uma_vigente", "percent_of_uma": 20.40},
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "notes": None,
        },
        {
            "source_key": "lss_texto_vigente_2026",
            "component_key": "em_excedente_3uma",
            "component_name": "Enfermedades y maternidad - excedente sobre 3 UMA",
            "branch": "enfermedades_maternidad",
            "calculation_mode": "rate_over_threshold",
            "base_type": "sbc",
            "employer_rate": 1.10,
            "employee_rate": 0.40,
            "fixed_amount": None,
            "min_uma": 3.0,
            "max_uma": None,
            "legal_reference": "LSS art. 106 fr. II",
            "formula_json": {"threshold_uma": 3.0},
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "notes": None,
        },
        {
            "source_key": "lss_texto_vigente_2026",
            "component_key": "em_prestaciones_dinero",
            "component_name": "Enfermedades y maternidad - prestaciones en dinero",
            "branch": "enfermedades_maternidad",
            "calculation_mode": "rate",
            "base_type": "sbc",
            "employer_rate": 0.70,
            "employee_rate": 0.25,
            "fixed_amount": None,
            "min_uma": None,
            "max_uma": None,
            "legal_reference": "LSS art. 107 fr. I",
            "formula_json": None,
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "notes": None,
        },
        {
            "source_key": "lss_texto_vigente_2026",
            "component_key": "em_gastos_medicos_pensionados",
            "component_name": "Enfermedades y maternidad - gastos médicos pensionados",
            "branch": "enfermedades_maternidad",
            "calculation_mode": "rate",
            "base_type": "sbc",
            "employer_rate": 1.05,
            "employee_rate": 0.375,
            "fixed_amount": None,
            "min_uma": None,
            "max_uma": None,
            "legal_reference": "LSS art. 107 fr. II",
            "formula_json": None,
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "notes": None,
        },
        {
            "source_key": "lss_texto_vigente_2026",
            "component_key": "invalidez_vida",
            "component_name": "Invalidez y vida",
            "branch": "invalidez_vida",
            "calculation_mode": "rate",
            "base_type": "sbc",
            "employer_rate": 1.75,
            "employee_rate": 0.625,
            "fixed_amount": None,
            "min_uma": None,
            "max_uma": None,
            "legal_reference": "LSS art. 147",
            "formula_json": None,
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "notes": None,
        },
        {
            "source_key": "lss_texto_vigente_2026",
            "component_key": "guarderias_prestaciones_sociales",
            "component_name": "Guarderías y prestaciones sociales",
            "branch": "guarderias_prestaciones",
            "calculation_mode": "rate",
            "base_type": "sbc",
            "employer_rate": 1.00,
            "employee_rate": None,
            "fixed_amount": None,
            "min_uma": None,
            "max_uma": None,
            "legal_reference": "LSS art. 211",
            "formula_json": None,
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "notes": None,
        },
        {
            "source_key": "lss_texto_vigente_2026",
            "component_key": "retiro_patron",
            "component_name": "Retiro patrón",
            "branch": "rcv",
            "calculation_mode": "rate",
            "base_type": "sbc",
            "employer_rate": 2.00,
            "employee_rate": None,
            "fixed_amount": None,
            "min_uma": None,
            "max_uma": None,
            "legal_reference": "LSS art. 168 fr. I",
            "formula_json": None,
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "notes": None,
        },
        {
            "source_key": "lss_texto_vigente_2026",
            "component_key": "cesantia_vejez",
            "component_name": "Cesantía en edad avanzada y vejez",
            "branch": "rcv",
            "calculation_mode": "rate",
            "base_type": "sbc",
            "employer_rate": 3.15,
            "employee_rate": 1.125,
            "fixed_amount": None,
            "min_uma": None,
            "max_uma": None,
            "legal_reference": "LSS art. 168 fr. II",
            "formula_json": None,
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "notes": None,
        },
        {
            "source_key": "infonavit_aportacion_patronal",
            "component_key": "infonavit_patron",
            "component_name": "Aportación patronal INFONAVIT",
            "branch": "infonavit",
            "calculation_mode": "rate",
            "base_type": "sbc",
            "employer_rate": 5.00,
            "employee_rate": None,
            "fixed_amount": None,
            "min_uma": None,
            "max_uma": None,
            "legal_reference": "Ley del INFONAVIT art. 29 fr. II",
            "formula_json": None,
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "notes": None,
        },
    ]

    return {
        "sources": sources,
        "labor_rules": labor_rules,
        "isr_rows": isr_rows,
        "subsidio_rows": subsidio_rows,
        "social_security_rows": social_security_rows,
    }


async def _upsert_regulatory_source(
    session: AsyncSession,
    payload: Mapping[str, Any],
) -> RegulatorySource:
    existing = (
        await session.execute(
            select(RegulatorySource).where(RegulatorySource.source_key == payload["source_key"])
        )
    ).scalar_one_or_none()
    if not existing:
        existing = RegulatorySource(id=uuid4(), source_key=str(payload["source_key"]))
        session.add(existing)
    existing.source_type = str(payload["source_type"])
    existing.authority = str(payload["authority"])
    existing.title = str(payload["title"])
    existing.url = str(payload["url"])
    existing.legal_reference = payload.get("legal_reference")
    existing.verification_status = "verified"
    existing.published_at = payload.get("published_at")
    existing.effective_from = payload.get("effective_from")
    existing.effective_to = payload.get("effective_to")
    existing.summary_json = payload.get("summary_json")
    return existing


async def _upsert_labor_rule(
    session: AsyncSession,
    payload: Mapping[str, Any],
    source_by_key: Mapping[str, RegulatorySource],
) -> LaborRuleSnapshot:
    existing = (
        await session.execute(
            select(LaborRuleSnapshot).where(
                LaborRuleSnapshot.rule_key == payload["rule_key"],
                LaborRuleSnapshot.effective_from == payload["effective_from"],
            )
        )
    ).scalar_one_or_none()
    if not existing:
        existing = LaborRuleSnapshot(id=uuid4(), rule_key=str(payload["rule_key"]))
        session.add(existing)
    existing.source_id = source_by_key[payload["source_key"]].id
    existing.category = str(payload["category"])
    existing.title = str(payload["title"])
    existing.legal_reference = payload.get("legal_reference")
    existing.effective_from = payload["effective_from"]
    existing.effective_to = payload.get("effective_to")
    existing.numeric_value = payload.get("numeric_value")
    existing.unit = payload.get("unit")
    existing.payload_json = payload.get("payload_json")
    existing.notes = payload.get("notes")
    return existing


async def _upsert_isr_row(
    session: AsyncSession,
    payload: Mapping[str, Any],
    source_by_key: Mapping[str, RegulatorySource],
) -> TaxTableISR:
    existing = (
        await session.execute(
            select(TaxTableISR).where(
                TaxTableISR.regime_key == payload["regime_key"],
                TaxTableISR.periodicity == payload["periodicity"],
                TaxTableISR.effective_from == payload["effective_from"],
                TaxTableISR.row_order == payload["row_order"],
            )
        )
    ).scalar_one_or_none()
    if not existing:
        existing = TaxTableISR(id=uuid4())
        session.add(existing)
    existing.source_id = source_by_key[payload["source_key"]].id
    existing.regime_key = str(payload["regime_key"])
    existing.periodicity = str(payload["periodicity"])
    existing.effective_from = payload["effective_from"]
    existing.effective_to = payload.get("effective_to")
    existing.row_order = int(payload["row_order"])
    existing.lower_limit = float(payload["lower_limit"])
    existing.upper_limit = float(payload["upper_limit"]) if payload.get("upper_limit") is not None else None
    existing.fixed_fee = float(payload["fixed_fee"])
    existing.marginal_rate = float(payload["marginal_rate"])
    return existing


async def _upsert_subsidio_row(
    session: AsyncSession,
    payload: Mapping[str, Any],
    source_by_key: Mapping[str, RegulatorySource],
) -> TaxTableSubsidioEmpleo:
    existing = (
        await session.execute(
            select(TaxTableSubsidioEmpleo).where(
                TaxTableSubsidioEmpleo.periodicity == payload["periodicity"],
                TaxTableSubsidioEmpleo.effective_from == payload["effective_from"],
            )
        )
    ).scalar_one_or_none()
    if not existing:
        existing = TaxTableSubsidioEmpleo(id=uuid4())
        session.add(existing)
    existing.source_id = source_by_key[payload["source_key"]].id
    existing.periodicity = str(payload["periodicity"])
    existing.effective_from = payload["effective_from"]
    existing.effective_to = payload.get("effective_to")
    existing.income_limit = float(payload["income_limit"])
    existing.subsidy_amount = float(payload["subsidy_amount"]) if payload.get("subsidy_amount") is not None else None
    existing.subsidy_percent = float(payload["subsidy_percent"]) if payload.get("subsidy_percent") is not None else None
    existing.uma_value = float(payload["uma_value"]) if payload.get("uma_value") is not None else None
    existing.uma_periodicity = payload.get("uma_periodicity")
    existing.legal_reference = payload.get("legal_reference")
    existing.notes = payload.get("notes")
    return existing


async def _upsert_social_security_row(
    session: AsyncSession,
    payload: Mapping[str, Any],
    source_by_key: Mapping[str, RegulatorySource],
) -> SocialSecurityTable:
    existing = (
        await session.execute(
            select(SocialSecurityTable).where(
                SocialSecurityTable.component_key == payload["component_key"],
                SocialSecurityTable.effective_from == payload["effective_from"],
            )
        )
    ).scalar_one_or_none()
    if not existing:
        existing = SocialSecurityTable(id=uuid4())
        session.add(existing)
    existing.source_id = source_by_key[payload["source_key"]].id
    existing.component_key = str(payload["component_key"])
    existing.component_name = str(payload["component_name"])
    existing.branch = str(payload["branch"])
    existing.calculation_mode = str(payload["calculation_mode"])
    existing.base_type = str(payload["base_type"])
    existing.employer_rate = payload.get("employer_rate")
    existing.employee_rate = payload.get("employee_rate")
    existing.fixed_amount = payload.get("fixed_amount")
    existing.min_uma = payload.get("min_uma")
    existing.max_uma = payload.get("max_uma")
    existing.legal_reference = payload.get("legal_reference")
    existing.formula_json = payload.get("formula_json")
    existing.effective_from = payload["effective_from"]
    existing.effective_to = payload.get("effective_to")
    existing.notes = payload.get("notes")
    return existing


async def seed_payroll_normative_2026(
    session: AsyncSession,
    *,
    apply: bool,
) -> Dict[str, Any]:
    bundle = build_payroll_normative_seed_bundle()

    source_by_key: Dict[str, RegulatorySource] = {}
    for payload in bundle["sources"]:
        obj = await _upsert_regulatory_source(session, payload)
        source_by_key[payload["source_key"]] = obj
    await session.flush()

    for payload in bundle["labor_rules"]:
        await _upsert_labor_rule(session, payload, source_by_key)
    for payload in bundle["isr_rows"]:
        await _upsert_isr_row(session, payload, source_by_key)
    for payload in bundle["subsidio_rows"]:
        await _upsert_subsidio_row(session, payload, source_by_key)
    for payload in bundle["social_security_rows"]:
        await _upsert_social_security_row(session, payload, source_by_key)

    summary = SeedSummary(
        regulatory_sources=len(bundle["sources"]),
        labor_rule_snapshots=len(bundle["labor_rules"]),
        tax_tables_isr=len(bundle["isr_rows"]),
        tax_tables_subsidio_empleo=len(bundle["subsidio_rows"]),
        social_security_tables=len(bundle["social_security_rows"]),
    )

    if apply:
        await session.commit()
    else:
        await session.rollback()

    return {
        "mode": "apply" if apply else "dry_run",
        "summary": summary.__dict__,
        "sources": [item["source_key"] for item in bundle["sources"]],
    }


__all__ = [
    "build_payroll_normative_seed_bundle",
    "seed_payroll_normative_2026",
]
