"""
Initial reusable payroll calculation helpers.

This service ports the workbook's reusable logic into Python:
- seniority / vacation lookup
- integrated daily salary (SBC)
- taxable / exempt split by concept

Normative values are expected to come from DB-backed tables seeded elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


_D = Decimal


def _dec(value: float | int | str | Decimal | None) -> Decimal:
    if value is None:
        return _D("0")
    if isinstance(value, Decimal):
        return value
    return _D(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(_D("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ConceptEvaluationContext:
    uma_daily: Decimal
    uma_annual: Optional[Decimal] = None
    payroll_days: Decimal = _D("15.21")
    sbc_daily: Optional[Decimal] = None
    annual_salary_total: Optional[Decimal] = None
    annual_prevision_social_total_before: Decimal = _D("0")
    annual_prevision_social_exempt_used: Decimal = _D("0")
    generality_met: bool = False
    documented_plan_met: bool = False
    authorized_delivery_channel_met: bool = False
    equal_employer_employee_contribution: bool = False
    savings_fund_percent_of_salary: Optional[Decimal] = None
    annual_savings_fund_total: Optional[Decimal] = None
    employee_copay_percent: Optional[Decimal] = None


@dataclass(frozen=True)
class ConceptEvaluationResult:
    taxable_amount: Decimal
    exempt_amount: Decimal
    sbc_integration_amount: Decimal
    notes: List[str] = field(default_factory=list)


def compute_seniority_years(hire_date: date | None, as_of: date) -> int:
    if hire_date is None or hire_date > as_of:
        return 0
    years = as_of.year - hire_date.year
    if (as_of.month, as_of.day) < (hire_date.month, hire_date.day):
        years -= 1
    return max(years, 0)


def lookup_vacation_days(schedule: Mapping[int, int], seniority_years: int) -> int:
    if seniority_years <= 0:
        return int(schedule.get(0) or schedule.get(1) or 12)
    if seniority_years in schedule:
        return schedule[seniority_years]
    lower_keys = sorted(k for k in schedule if k <= seniority_years)
    if lower_keys:
        return schedule[lower_keys[-1]]
    return 12


def compute_integrated_daily_salary(
    *,
    daily_salary: float | Decimal,
    vacation_days: int,
    vacation_premium_rate: float | Decimal,
    aguinaldo_days: float | Decimal,
    other_sbc_amount: float | Decimal = 0,
    period_days: float | Decimal = 15.21,
) -> Decimal:
    sd = _dec(daily_salary)
    vac_days = _dec(vacation_days)
    vac_rate = _dec(vacation_premium_rate)
    aguinaldo = _dec(aguinaldo_days)
    other_sbc = _dec(other_sbc_amount)
    payroll_days = _dec(period_days) if _dec(period_days) != 0 else _D("1")

    integration_factor = _D("1") + ((vac_days * vac_rate) / _D("365")) + (aguinaldo / _D("365"))
    value = (integration_factor * sd) + (other_sbc / payroll_days)
    return _money(value)


def split_taxable_exempt_amount(
    *,
    concept_key: str,
    amount: float | Decimal,
    uma_daily: float | Decimal,
) -> Tuple[Decimal, Decimal]:
    value = _money(_dec(amount))
    uma = _dec(uma_daily)

    if value <= 0:
        return _D("0.00"), _D("0.00")

    if concept_key in {"salary", "overtime_triple", "food_vouchers", "punctuality_bonus", "holiday_work", "birth_aid", "school_aid", "other_perception"}:
        return value, _D("0.00")

    if concept_key == "savings_fund":
        return _D("0.00"), value

    if concept_key == "overtime_double":
        exempt = min(_money(value / _D("2")), _money(uma * _D("5")))
        taxable = max(_D("0.00"), _money(value - exempt))
        return taxable, exempt

    if concept_key in {"sunday_premium", "vacation_premium"}:
        exempt = min(value, _money(uma))
        taxable = max(_D("0.00"), _money(value - exempt))
        return taxable, exempt

    if concept_key == "worked_rest_day":
        half_value = _money(value / _D("2"))
        threshold = _money(uma * _D("5"))
        exempt = half_value if half_value <= threshold else _D("0.00")
        taxable = max(_D("0.00"), _money(value - exempt))
        return taxable, exempt

    if concept_key == "death_aid_legacy_v":
        exempt = min(value, _money(uma * _D("365")))
        taxable = max(_D("0.00"), _money(value - exempt))
        return taxable, exempt

    return value, _D("0.00")


def evaluate_concept_amount(
    *,
    concept_key: str,
    amount: float | Decimal,
    context: ConceptEvaluationContext,
    rule_payload: Optional[Mapping[str, Any]] = None,
) -> ConceptEvaluationResult:
    value = _money(_dec(amount))
    if value <= 0:
        return ConceptEvaluationResult(_D("0.00"), _D("0.00"), _D("0.00"), [])

    notes: List[str] = []
    taxable, exempt = split_taxable_exempt_amount(
        concept_key=concept_key,
        amount=value,
        uma_daily=context.uma_daily,
    )
    sbc_integration = _D("0.00")

    payload = dict(rule_payload or {})
    isr_treatment = dict(payload.get("isr_treatment") or {})
    sbc_treatment = dict(payload.get("sbc_treatment") or {})

    isr_mode = str(isr_treatment.get("mode") or "").strip().lower()
    if isr_mode == "prevision_social_global":
        taxable, exempt, local_notes = _evaluate_prevision_social_global(value, context)
        notes.extend(local_notes)
    elif isr_mode == "conditional_savings_fund":
        taxable, exempt, local_notes = _evaluate_savings_fund(value, context)
        notes.extend(local_notes)
    elif isr_mode == "fully_taxable":
        taxable, exempt = value, _D("0.00")
    elif isr_mode == "fully_exempt":
        taxable, exempt = _D("0.00"), value

    local_notes = _evaluate_requirement_flags(
        treatment=isr_treatment,
        context=context,
        channel="isr",
    )
    if local_notes:
        taxable, exempt = value, _D("0.00")
        notes.extend(local_notes)

    sbc_mode = str(sbc_treatment.get("mode") or "").strip().lower()
    if sbc_mode == "include_full":
        sbc_integration = value
    elif sbc_mode == "partial_uma_pct_per_period":
        percent = _dec(sbc_treatment.get("percent") or 0)
        cap = _money(context.uma_daily * context.payroll_days * (percent / _D("100")))
        sbc_integration = max(_D("0.00"), _money(value - cap))
        notes.append(f"SBC exento hasta {percent}% de UMA diaria por días del periodo.")
    elif sbc_mode == "partial_sbc_pct_per_period":
        percent = _dec(sbc_treatment.get("percent") or 0)
        sbc_daily = context.sbc_daily or _D("0")
        cap = _money(sbc_daily * context.payroll_days * (percent / _D("100")))
        sbc_integration = max(_D("0.00"), _money(value - cap))
        notes.append(f"SBC exento hasta {percent}% del SBC del periodo.")
    elif sbc_mode == "conditional_savings_fund":
        savings_taxable, savings_exempt, local_notes = _evaluate_savings_fund(value, context)
        if savings_exempt >= value:
            sbc_integration = _D("0.00")
        else:
            sbc_integration = value
        notes.extend(local_notes)

    sbc_requirement_notes = _evaluate_requirement_flags(
        treatment=sbc_treatment,
        context=context,
        channel="sbc",
    )
    if sbc_requirement_notes:
        sbc_integration = value
        notes.extend(sbc_requirement_notes)

    return ConceptEvaluationResult(
        taxable_amount=_money(taxable),
        exempt_amount=_money(exempt),
        sbc_integration_amount=_money(sbc_integration),
        notes=notes,
    )


def _evaluate_prevision_social_global(
    value: Decimal,
    context: ConceptEvaluationContext,
) -> Tuple[Decimal, Decimal, List[str]]:
    notes: List[str] = []
    if context.uma_annual is None or context.annual_salary_total is None:
        return value, _D("0.00"), ["Falta contexto anual para aplicar el límite global de previsión social."]

    global_cap = _money(context.uma_annual * _D("7"))
    annual_total = _money(context.annual_salary_total + context.annual_prevision_social_total_before + value)
    if annual_total <= global_cap:
        return _D("0.00"), value, ["La suma anual de salarios y previsión social no rebasa 7 UMA anuales."]

    restricted_cap = _money(context.uma_annual)
    remaining_exempt = max(_D("0.00"), _money(restricted_cap - context.annual_prevision_social_exempt_used))
    exempt = min(value, remaining_exempt)
    taxable = max(_D("0.00"), _money(value - exempt))
    notes.append("La suma anual rebasa 7 UMA anuales; la exención ISR se restringe al remanente de 1 UMA anual.")
    return taxable, exempt, notes


def _evaluate_savings_fund(
    value: Decimal,
    context: ConceptEvaluationContext,
) -> Tuple[Decimal, Decimal, List[str]]:
    notes: List[str] = []
    if not context.equal_employer_employee_contribution:
        return value, _D("0.00"), ["El fondo de ahorro no cumple aportación igual patrón/trabajador."]
    if context.uma_annual is None or context.annual_salary_total is None:
        return value, _D("0.00"), ["Falta UMA anual o salario anual para validar el fondo de ahorro."]

    max_percent = _D("13")
    percent = context.savings_fund_percent_of_salary
    if percent is not None and percent > max_percent:
        return value, _D("0.00"), ["El fondo de ahorro excede 13% del salario del trabajador."]

    annual_cap = _money(context.uma_annual * _D("1.3"))
    total = _money((context.annual_savings_fund_total or _D("0")) + value)
    if total <= annual_cap:
        return _D("0.00"), value, ["El fondo de ahorro cumple tope anual de 1.3 UMA y aportación igualitaria."]

    exempt = max(_D("0.00"), _money(annual_cap - (context.annual_savings_fund_total or _D("0"))))
    exempt = min(exempt, value)
    taxable = max(_D("0.00"), _money(value - exempt))
    notes.append("El fondo de ahorro rebasa el tope anual de 1.3 UMA; solo el remanente disponible queda exento.")
    return taxable, exempt, notes


def _evaluate_requirement_flags(
    *,
    treatment: Mapping[str, Any],
    context: ConceptEvaluationContext,
    channel: str,
) -> List[str]:
    notes: List[str] = []
    if treatment.get("requires_generality") and not context.generality_met:
        notes.append(f"Falta generalidad para tratamiento {channel.upper()}; se aplica criterio conservador.")
    if treatment.get("requires_documented_plan") and not context.documented_plan_met:
        notes.append(f"Falta plan o política documentada para tratamiento {channel.upper()}; se aplica criterio conservador.")
    if treatment.get("requires_authorized_delivery_channel") and not context.authorized_delivery_channel_met:
        notes.append(f"Falta medio autorizado/documentado de entrega para tratamiento {channel.upper()}; se aplica criterio conservador.")

    min_copay = treatment.get("requires_employee_copay_percent_at_least")
    if min_copay is not None:
        copay = context.employee_copay_percent or _D("0")
        if copay < _dec(min_copay):
            notes.append(
                f"El copago del trabajador es menor al mínimo requerido para tratamiento {channel.upper()}; se aplica criterio conservador."
            )
    return notes


def summarize_concepts_taxability(
    concept_amounts: Mapping[str, float | Decimal],
    *,
    uma_daily: float | Decimal,
) -> Dict[str, Dict[str, float]]:
    summary: Dict[str, Dict[str, float]] = {}
    for concept_key, raw_amount in concept_amounts.items():
        taxable, exempt = split_taxable_exempt_amount(
            concept_key=concept_key,
            amount=raw_amount,
            uma_daily=uma_daily,
        )
        summary[concept_key] = {
            "input_amount": float(_money(_dec(raw_amount))),
            "taxable_amount": float(taxable),
            "exempt_amount": float(exempt),
        }
    return summary


@dataclass(frozen=True)
class PayrollSbcSnapshot:
    seniority_years: int
    vacation_days: int
    integrated_daily_salary: Decimal


@dataclass(frozen=True)
class SocialSecurityCalculationContext:
    calculation_date: date
    payroll_days: Decimal
    sbc_daily: Decimal
    uma_daily: Decimal
    employer_risk_premium: Optional[Decimal] = None
    employer_risk_class: Optional[str] = None


@dataclass(frozen=True)
class SocialSecurityComponentAmount:
    component_key: str
    base_amount: Decimal
    employer_amount: Decimal
    employee_amount: Decimal
    employer_rate: Optional[Decimal] = None
    employee_rate: Optional[Decimal] = None
    notes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SocialSecurityBreakdown:
    employer_total: Decimal
    employee_total: Decimal
    components: Dict[str, SocialSecurityComponentAmount]
    notes: List[str] = field(default_factory=list)


def build_sbc_snapshot(
    *,
    hire_date: date | None,
    as_of: date,
    schedule: Mapping[int, int],
    daily_salary: float | Decimal,
    vacation_premium_rate: float | Decimal,
    aguinaldo_days: float | Decimal,
    other_sbc_amount: float | Decimal = 0,
    period_days: float | Decimal = 15.21,
) -> PayrollSbcSnapshot:
    seniority_years = compute_seniority_years(hire_date, as_of)
    vacation_days = lookup_vacation_days(schedule, seniority_years)
    sdi = compute_integrated_daily_salary(
        daily_salary=daily_salary,
        vacation_days=vacation_days,
        vacation_premium_rate=vacation_premium_rate,
        aguinaldo_days=aguinaldo_days,
        other_sbc_amount=other_sbc_amount,
        period_days=period_days,
    )
    return PayrollSbcSnapshot(
        seniority_years=seniority_years,
        vacation_days=vacation_days,
        integrated_daily_salary=sdi,
    )


def select_effective_social_security_rows(
    rows: Sequence[Mapping[str, Any] | Any],
    *,
    calculation_date: date,
) -> List[Mapping[str, Any] | Any]:
    selected: Dict[str, Mapping[str, Any] | Any] = {}
    selected_from: Dict[str, date] = {}
    for row in rows:
        component_key = str(_row_value(row, "component_key") or "").strip()
        if not component_key:
            continue
        effective_from = _row_date(row, "effective_from")
        effective_to = _row_date(row, "effective_to")
        if effective_from and calculation_date < effective_from:
            continue
        if effective_to and calculation_date > effective_to:
            continue

        current_from = selected_from.get(component_key)
        if current_from is None or (effective_from and effective_from >= current_from):
            selected[component_key] = row
            selected_from[component_key] = effective_from or date.min
    return list(selected.values())


def calculate_social_security_breakdown(
    *,
    rows: Sequence[Mapping[str, Any] | Any],
    context: SocialSecurityCalculationContext,
) -> SocialSecurityBreakdown:
    components: Dict[str, SocialSecurityComponentAmount] = {}
    notes: List[str] = []
    payroll_days = _dec(context.payroll_days)
    sbc_daily = _dec(context.sbc_daily)
    uma_daily = _dec(context.uma_daily)

    effective_rows = select_effective_social_security_rows(
        rows,
        calculation_date=context.calculation_date,
    )

    for row in effective_rows:
        component_key = str(_row_value(row, "component_key") or "").strip()
        if not component_key:
            continue
        component_notes: List[str] = []
        calculation_mode = str(_row_value(row, "calculation_mode") or "rate").strip().lower()
        base_amount = _resolve_social_security_base_amount(
            row,
            calculation_mode=calculation_mode,
            sbc_daily=sbc_daily,
            uma_daily=uma_daily,
            payroll_days=payroll_days,
        )
        employer_rate = _optional_dec(_row_value(row, "employer_rate"))
        employee_rate = _optional_dec(_row_value(row, "employee_rate"))

        employer_amount = _D("0.00")
        employee_amount = _D("0.00")
        if employer_rate is not None:
            employer_amount = _money(base_amount * (employer_rate / _D("100")))
        if employee_rate is not None:
            employee_amount = _money(base_amount * (employee_rate / _D("100")))

        if calculation_mode == "rate_over_threshold":
            threshold_uma = _dec((_row_value(row, "formula_json") or {}).get("threshold_uma"))
            component_notes.append(f"Base sobre excedente diario arriba de {threshold_uma} UMA.")

        components[component_key] = SocialSecurityComponentAmount(
            component_key=component_key,
            base_amount=_money(base_amount),
            employer_amount=employer_amount,
            employee_amount=employee_amount,
            employer_rate=employer_rate,
            employee_rate=employee_rate,
            notes=component_notes,
        )

    risk_premium = _optional_dec(context.employer_risk_premium)
    if risk_premium is not None and risk_premium > 0:
        risk_base = _money(sbc_daily * payroll_days)
        risk_notes: List[str] = []
        if context.employer_risk_class:
            risk_notes.append(f"Prima patronal asociada a clase de riesgo {context.employer_risk_class}.")
        components["riesgo_trabajo_patron"] = SocialSecurityComponentAmount(
            component_key="riesgo_trabajo_patron",
            base_amount=risk_base,
            employer_amount=_money(risk_base * (risk_premium / _D("100"))),
            employee_amount=_D("0.00"),
            employer_rate=risk_premium,
            employee_rate=None,
            notes=risk_notes,
        )
    else:
        notes.append("No se aplicó riesgo de trabajo patronal por falta de prima vigente en el registro patronal.")

    employer_total = _money(sum((component.employer_amount for component in components.values()), _D("0")))
    employee_total = _money(sum((component.employee_amount for component in components.values()), _D("0")))
    return SocialSecurityBreakdown(
        employer_total=employer_total,
        employee_total=employee_total,
        components=components,
        notes=notes,
    )


def _resolve_social_security_base_amount(
    row: Mapping[str, Any] | Any,
    *,
    calculation_mode: str,
    sbc_daily: Decimal,
    uma_daily: Decimal,
    payroll_days: Decimal,
) -> Decimal:
    base_type = str(_row_value(row, "base_type") or "sbc").strip().lower()
    formula_json = _row_value(row, "formula_json") or {}
    daily_base = sbc_daily

    max_uma = _optional_dec(_row_value(row, "max_uma"))
    if max_uma is not None and max_uma > 0:
        daily_base = min(daily_base, uma_daily * max_uma)

    if calculation_mode == "uma_percent" or base_type == "uma":
        return _money(uma_daily * payroll_days)

    if calculation_mode == "rate_over_threshold":
        threshold_uma = _dec(formula_json.get("threshold_uma"))
        excess_daily = max(_D("0.00"), sbc_daily - (uma_daily * threshold_uma))
        return _money(excess_daily * payroll_days)

    if base_type == "sbc":
        return _money(daily_base * payroll_days)

    return _money(sbc_daily * payroll_days)


def _row_value(row: Mapping[str, Any] | Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _row_date(row: Mapping[str, Any] | Any, key: str) -> Optional[date]:
    value = _row_value(row, key)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _optional_dec(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    dec_value = _dec(value)
    return dec_value


__all__ = [
    "ConceptEvaluationContext",
    "ConceptEvaluationResult",
    "PayrollSbcSnapshot",
    "SocialSecurityBreakdown",
    "SocialSecurityCalculationContext",
    "SocialSecurityComponentAmount",
    "build_sbc_snapshot",
    "calculate_social_security_breakdown",
    "compute_integrated_daily_salary",
    "compute_seniority_years",
    "evaluate_concept_amount",
    "lookup_vacation_days",
    "select_effective_social_security_rows",
    "split_taxable_exempt_amount",
    "summarize_concepts_taxability",
]
