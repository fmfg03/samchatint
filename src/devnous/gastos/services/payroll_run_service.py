"""
Payroll run calculation service.

This service calculates a prenómina snapshot for one period and persists:
- payroll_runs
- payroll_run_lines

Current scope:
- base salary by period
- period incidents
- ISR by periodicity
- subsidio al empleo prorated from monthly rule
- IMSS/INFONAVIT worker charges and employer charges for sueldos
- patronal risk by employer registration

It intentionally treats `asimilados` conservatively:
- ISR yes
- IMSS / INFONAVIT / patronal charges no
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    AccountingImportRun,
    AccountingPoliza,
    AccountingPolizaLine,
    CuentaContable,
    Empleado,
    LaborRuleSnapshot,
    PayrollAccountMapping,
    PayrollEmployee,
    PayrollEmployerRegistration,
    PayrollIncident,
    PayrollPeriod,
    PayrollRun,
    PayrollRunLine,
    SocialSecurityTable,
    TaxTableISR,
    TaxTableSubsidioEmpleo,
)
from .payroll_calculator import (
    SocialSecurityCalculationContext,
    build_sbc_snapshot,
    calculate_social_security_breakdown,
)


_D = Decimal


def _dec(value: Any) -> Decimal:
    if value is None:
        return _D("0")
    if isinstance(value, Decimal):
        return value
    return _D(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(_D("0.01"), rounding=ROUND_HALF_UP)


def _periodicity_key(period_type: str) -> str:
    normalized = (period_type or "").strip().lower()
    return {
        "weekly": "semanal",
        "semanal": "semanal",
        "biweekly": "quincenal",
        "quincenal": "quincenal",
        "monthly": "mensual",
        "mensual": "mensual",
        "daily": "diaria",
        "diaria": "diaria",
        "decenal": "decenal",
    }.get(normalized, "quincenal")


def _is_asimilado(compensation_regime: Optional[str]) -> bool:
    normalized = (compensation_regime or "").strip().lower()
    return "asimil" in normalized


def _prorate_monthly_amount(amount: Decimal, days_paid: Decimal) -> Decimal:
    return _money((amount / _D("30.4")) * days_paid)


def _find_effective_isr_brackets(
    rows: Sequence[TaxTableISR],
    *,
    periodicity: str,
    calculation_date: date,
) -> List[TaxTableISR]:
    selected = [
        row
        for row in rows
        if (row.periodicity or "").strip().lower() == periodicity
        and _date_of(row.effective_from) <= calculation_date
        and (row.effective_to is None or _date_of(row.effective_to) >= calculation_date)
    ]
    return sorted(selected, key=lambda row: int(row.row_order or 0))


def _calculate_isr_from_brackets(
    *,
    taxable_income: Decimal,
    brackets: Sequence[TaxTableISR],
) -> Decimal:
    income = _money(taxable_income)
    if income <= 0:
        return _D("0.00")
    for row in brackets:
        lower = _dec(row.lower_limit)
        upper = _dec(row.upper_limit) if row.upper_limit is not None else None
        if income < lower:
            continue
        if upper is None or income <= upper:
            excess = income - lower
            tax = _dec(row.fixed_fee) + (excess * (_dec(row.marginal_rate) / _D("100")))
            return _money(tax)
    return _D("0.00")


def _find_effective_subsidio_row(
    rows: Sequence[TaxTableSubsidioEmpleo],
    *,
    calculation_date: date,
) -> Optional[TaxTableSubsidioEmpleo]:
    applicable = [
        row
        for row in rows
        if _date_of(row.effective_from) <= calculation_date
        and (row.effective_to is None or _date_of(row.effective_to) >= calculation_date)
    ]
    applicable.sort(key=lambda row: _date_of(row.effective_from), reverse=True)
    return applicable[0] if applicable else None


def _date_of(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(f"Unsupported date value: {value!r}")


@dataclass(frozen=True)
class PayrollRunComputationLine:
    payroll_employee_id: UUID
    empleado_name: str
    days_paid: Decimal
    taxable_total: Decimal
    exempt_total: Decimal
    deductions_total: Decimal
    employer_charges_total: Decimal
    isr_withheld: Decimal
    subsidy_applied: Decimal
    net_pay: Decimal
    integrated_daily_salary_used: Decimal
    perceptions_json: Dict[str, Any]
    deductions_json: Dict[str, Any]
    employer_charges_json: Dict[str, Any]
    incidents_summary: Dict[str, Any]
    notes: List[str]


@dataclass(frozen=True)
class PayrollRunComputationResult:
    run_id: UUID
    period_id: UUID
    line_count: int
    gross_total: Decimal
    deductions_total: Decimal
    employer_charges_total: Decimal
    net_total: Decimal
    lines: List[PayrollRunComputationLine]


@dataclass(frozen=True)
class PayrollPaymentInstruction:
    kind: str  # employee_net|alimony_beneficiary
    payroll_employee_id: UUID
    empleado_name: str
    beneficiary_name: str
    amount: Decimal
    bank_name: Optional[str]
    account_number: Optional[str]
    clabe: Optional[str]
    reference: Optional[str]
    metadata: Dict[str, Any]


@dataclass(frozen=True)
class PayrollAccountingPolicyResult:
    poliza_id: UUID
    import_run_id: UUID
    line_count: int
    total_debe: Decimal
    total_haber: Decimal
    unresolved_accounts: List[str]


@dataclass(frozen=True)
class PayrollAccountPurposeStatus:
    purpose_key: str
    purpose_label: str
    amount: Decimal
    resolution_kind: str  # explicit | heuristic | missing
    cuenta_codigo: Optional[str]
    cuenta_nombre: Optional[str]
    source_scope: str  # employer | global | heuristic | missing
    payroll_employer_id: Optional[UUID] = None
    payroll_employer_name: Optional[str] = None


@dataclass(frozen=True)
class PayrollAccountCoverageReport:
    run_id: UUID
    employer_ids: List[UUID]
    required_purpose_count: int
    explicit_count: int
    heuristic_count: int
    missing_count: int
    statuses: List[PayrollAccountPurposeStatus]

    @property
    def missing_purpose_keys(self) -> List[str]:
        return [status.purpose_key for status in self.statuses if status.resolution_kind == "missing"]

    @property
    def heuristic_purpose_keys(self) -> List[str]:
        return [status.purpose_key for status in self.statuses if status.resolution_kind == "heuristic"]


def has_strict_payroll_account_coverage(report: PayrollAccountCoverageReport) -> bool:
    return report.missing_count == 0 and report.heuristic_count == 0


PAYROLL_ACCOUNT_PURPOSE_LABELS: dict[str, str] = {
    "sueldos_salarios": "Sueldos y salarios",
    "asimilados_salarios": "Asimilados a salarios",
    "cargas_patronales": "Cargas patronales",
    "isr_retenido_nomina": "ISR retenido nómina",
    "imss_obrero_por_pagar": "IMSS obrero por pagar",
    "imss_patronal_por_pagar": "IMSS patronal por pagar",
    "infonavit_patronal_por_pagar": "INFONAVIT patronal por pagar",
    "infonavit_credito_por_pagar": "INFONAVIT crédito por pagar",
    "fonacot_por_pagar": "FONACOT por pagar",
    "pension_alimenticia_por_pagar": "Pensión alimenticia por pagar",
    "nomina_por_pagar": "Nómina por pagar",
}


async def calculate_payroll_run_for_period(
    session: AsyncSession,
    *,
    period_id: UUID,
    created_by_empleado_id: Optional[UUID],
    run_type: str = "ordinary",
) -> PayrollRunComputationResult:
    period = (
        await session.execute(select(PayrollPeriod).where(PayrollPeriod.id == period_id))
    ).scalar_one()
    calculation_date = period.payment_date or period.end_date

    employees = (
        await session.execute(
            select(PayrollEmployee)
            .options(
                selectinload(PayrollEmployee.empleado),
                selectinload(PayrollEmployee.compensation_profile),
                selectinload(PayrollEmployee.deduction_profile),
                selectinload(PayrollEmployee.benefit_profile),
                selectinload(PayrollEmployee.employer_registration),
            )
            .where(PayrollEmployee.active == True)
            .order_by(PayrollEmployee.id.asc())
        )
    ).scalars().all()

    incidents = (
        await session.execute(
            select(PayrollIncident)
            .where(PayrollIncident.period_id == period_id)
            .order_by(PayrollIncident.created_at.asc())
        )
    ).scalars().all()
    incidents_by_employee: Dict[UUID, List[PayrollIncident]] = {}
    for incident in incidents:
        incidents_by_employee.setdefault(incident.payroll_employee_id, []).append(incident)

    labor_rules = (
        await session.execute(
            select(LaborRuleSnapshot).where(
                LaborRuleSnapshot.effective_from <= calculation_date,
            )
        )
    ).scalars().all()
    social_rows = (
        await session.execute(
            select(SocialSecurityTable).where(
                SocialSecurityTable.effective_from <= calculation_date,
            )
        )
    ).scalars().all()
    isr_rows = (
        await session.execute(
            select(TaxTableISR).where(
                TaxTableISR.regime_key == "payroll_retention",
                TaxTableISR.effective_from <= calculation_date,
            )
        )
    ).scalars().all()
    subsidio_rows = (
        await session.execute(
            select(TaxTableSubsidioEmpleo).where(
                TaxTableSubsidioEmpleo.effective_from <= calculation_date,
            )
        )
    ).scalars().all()

    uma_daily = _lookup_numeric_rule(labor_rules, "uma_daily", calculation_date) or _D("0")
    vacation_schedule = _lookup_vacation_schedule(labor_rules, calculation_date)
    periodicity = _periodicity_key(period.period_type)
    isr_brackets = _find_effective_isr_brackets(
        isr_rows,
        periodicity=periodicity,
        calculation_date=calculation_date,
    )
    subsidio_row = _find_effective_subsidio_row(subsidio_rows, calculation_date=calculation_date)

    existing_run = (
        await session.execute(
            select(PayrollRun)
            .where(
                PayrollRun.period_id == period_id,
                PayrollRun.run_type == run_type,
                PayrollRun.status.in_(["draft", "calculated"]),
            )
            .order_by(PayrollRun.updated_at.desc())
        )
    ).scalars().first()

    run = existing_run or PayrollRun(
        id=uuid4(),
        period_id=period_id,
        run_type=run_type,
        status="draft",
        created_by_empleado_id=created_by_empleado_id,
    )
    if existing_run is None:
        session.add(run)
    else:
        existing_lines = (
            await session.execute(
                select(PayrollRunLine).where(PayrollRunLine.run_id == run.id)
            )
        ).scalars().all()
        for line in existing_lines:
            await session.delete(line)

    lines: List[PayrollRunComputationLine] = []
    gross_total = _D("0.00")
    deductions_total = _D("0.00")
    employer_charges_total = _D("0.00")
    net_total = _D("0.00")

    for payroll_employee in employees:
        line = _build_payroll_line(
            payroll_employee=payroll_employee,
            period=period,
            calculation_date=calculation_date,
            incidents=incidents_by_employee.get(payroll_employee.id, []),
            vacation_schedule=vacation_schedule,
            uma_daily=uma_daily,
            social_rows=social_rows,
            isr_brackets=isr_brackets,
            subsidio_row=subsidio_row,
        )
        lines.append(line)

        run_line = PayrollRunLine(
            id=uuid4(),
            run_id=run.id,
            payroll_employee_id=payroll_employee.id,
            days_paid=float(line.days_paid),
            taxable_total=float(line.taxable_total),
            exempt_total=float(line.exempt_total),
            deductions_total=float(line.deductions_total),
            employer_charges_total=float(line.employer_charges_total),
            isr_withheld=float(line.isr_withheld),
            subsidy_applied=float(line.subsidy_applied),
            net_pay=float(line.net_pay),
            integrated_daily_salary_used=float(line.integrated_daily_salary_used),
            perceptions_json=line.perceptions_json,
            deductions_json=line.deductions_json,
            employer_charges_json=line.employer_charges_json,
            incidents_summary=line.incidents_summary,
            notes="\n".join(line.notes) if line.notes else None,
        )
        session.add(run_line)

        gross_total += _money(line.taxable_total + line.exempt_total)
        deductions_total += line.deductions_total
        employer_charges_total += line.employer_charges_total
        net_total += line.net_pay

    run.status = "calculated"
    run.source_snapshot_tag = f"period:{period.fiscal_year}-{period.period_no}:{calculation_date.isoformat()}"
    run.gross_total = float(_money(gross_total))
    run.deductions_total = float(_money(deductions_total))
    run.employer_charges_total = float(_money(employer_charges_total))
    run.net_total = float(_money(net_total))
    await session.commit()

    return PayrollRunComputationResult(
        run_id=run.id,
        period_id=period.id,
        line_count=len(lines),
        gross_total=_money(gross_total),
        deductions_total=_money(deductions_total),
        employer_charges_total=_money(employer_charges_total),
        net_total=_money(net_total),
        lines=lines,
    )


def _lookup_numeric_rule(
    rows: Iterable[LaborRuleSnapshot],
    rule_key: str,
    calculation_date: date,
) -> Optional[Decimal]:
    applicable = [
        row
        for row in rows
        if row.rule_key == rule_key
        and _date_of(row.effective_from) <= calculation_date
        and (row.effective_to is None or _date_of(row.effective_to) >= calculation_date)
        and row.numeric_value is not None
    ]
    applicable.sort(key=lambda row: _date_of(row.effective_from), reverse=True)
    if not applicable:
        return None
    return _dec(applicable[0].numeric_value)


def _lookup_vacation_schedule(
    rows: Iterable[LaborRuleSnapshot],
    calculation_date: date,
) -> Dict[int, int]:
    applicable = [
        row
        for row in rows
        if row.rule_key == "vacation_days_schedule"
        and _date_of(row.effective_from) <= calculation_date
        and (row.effective_to is None or _date_of(row.effective_to) >= calculation_date)
    ]
    applicable.sort(key=lambda row: _date_of(row.effective_from), reverse=True)
    if not applicable:
        return {0: 12, 1: 12, 2: 14, 3: 16, 4: 18, 5: 20}
    payload = dict(applicable[0].payload_json or {})
    schedule: Dict[int, int] = {}
    for key, value in dict(payload.get("years_1_to_5") or {}).items():
        schedule[int(key)] = int(value)
    for label, value in dict(payload.get("after_5_years_every_5") or {}).items():
        start = int(str(label).split("_to_")[0])
        schedule[start] = int(value)
    schedule[0] = schedule.get(1, 12)
    return schedule


def _build_payroll_line(
    *,
    payroll_employee: PayrollEmployee,
    period: PayrollPeriod,
    calculation_date: date,
    incidents: Sequence[PayrollIncident],
    vacation_schedule: Mapping[int, int],
    uma_daily: Decimal,
    social_rows: Sequence[SocialSecurityTable],
    isr_brackets: Sequence[TaxTableISR],
    subsidio_row: Optional[TaxTableSubsidioEmpleo],
) -> PayrollRunComputationLine:
    notes: List[str] = []
    compensation = payroll_employee.compensation_profile
    deduction_profile = payroll_employee.deduction_profile
    employee_name = (payroll_employee.empleado.nombre if payroll_employee.empleado else None) or (payroll_employee.empleado.email if payroll_employee.empleado else None) or str(payroll_employee.id)

    days_paid = _resolve_days_paid(payroll_employee, period, incidents)
    daily_salary = _resolve_daily_salary(payroll_employee)
    if daily_salary <= 0:
        notes.append("No hay salario diario configurado; la percepción base queda en cero.")

    aguinaldo_days = _D("15")
    vacation_premium_rate = _D("0.25")
    sbc_snapshot = build_sbc_snapshot(
        hire_date=payroll_employee.hire_date or payroll_employee.seniority_date,
        as_of=calculation_date,
        schedule=vacation_schedule,
        daily_salary=daily_salary,
        vacation_premium_rate=vacation_premium_rate,
        aguinaldo_days=aguinaldo_days,
        other_sbc_amount=_resolve_variable_salary(payroll_employee),
        period_days=days_paid or _D("1"),
    )
    integrated_daily_salary_used = _resolve_integrated_daily_salary(payroll_employee, sbc_snapshot.integrated_daily_salary)

    base_salary_amount = _money(daily_salary * days_paid)
    perception_items: List[Dict[str, Any]] = []
    if base_salary_amount > 0:
        perception_items.append(
            {
                "concept_key": "base_salary",
                "label": "Sueldo base del período",
                "taxable_amount": float(base_salary_amount),
                "exempt_amount": 0.0,
            }
        )

    taxable_total = base_salary_amount
    exempt_total = _D("0.00")
    manual_deductions_total = _D("0.00")
    deduction_items: List[Dict[str, Any]] = []
    incident_summary: List[Dict[str, Any]] = []

    for incident in incidents:
        incident_amount = _money(_dec(incident.taxable_amount) + _dec(incident.exempt_amount))
        incident_summary.append(
            {
                "id": str(incident.id),
                "type": incident.incident_type,
                "code": incident.incident_code,
                "quantity": float(_dec(incident.quantity)),
                "taxable_amount": float(_money(_dec(incident.taxable_amount))),
                "exempt_amount": float(_money(_dec(incident.exempt_amount))),
                "description": incident.description,
            }
        )

        if (incident.incident_type or "").strip().lower() in {"deduction", "loan"}:
            manual_deductions_total += incident_amount
            deduction_items.append(
                {
                    "concept_key": incident.incident_code or incident.incident_type,
                    "label": incident.description or incident.incident_code or "Deducción manual",
                    "amount": float(incident_amount),
                }
            )
            continue

        taxable_part = _money(_dec(incident.taxable_amount))
        exempt_part = _money(_dec(incident.exempt_amount))
        taxable_total += taxable_part
        exempt_total += exempt_part
        if taxable_part > 0 or exempt_part > 0:
            perception_items.append(
                {
                    "concept_key": incident.incident_code or incident.incident_type,
                    "label": incident.description or incident.incident_code or "Incidencia",
                    "taxable_amount": float(taxable_part),
                    "exempt_amount": float(exempt_part),
                }
            )

    compensation_regime = (compensation.compensation_regime if compensation else None) or ""
    social_security_employee = _D("0.00")
    social_security_employer = _D("0.00")
    social_security_payload: Dict[str, Any] = {}
    if _is_asimilado(compensation_regime):
        notes.append("Régimen asimilado: se omiten IMSS, INFONAVIT y cargas patronales.")
    else:
        registration = payroll_employee.employer_registration
        social_breakdown = calculate_social_security_breakdown(
            rows=social_rows,
            context=SocialSecurityCalculationContext(
                calculation_date=calculation_date,
                payroll_days=days_paid,
                sbc_daily=integrated_daily_salary_used,
                uma_daily=uma_daily,
                employer_risk_premium=_dec(registration.risk_premium) if registration and registration.risk_premium is not None else None,
                employer_risk_class=(registration.risk_class if registration else None),
            ),
        )
        social_security_employee = social_breakdown.employee_total
        social_security_employer = social_breakdown.employer_total
        social_security_payload = {
            "components": {
                key: {
                    "base_amount": float(component.base_amount),
                    "employer_amount": float(component.employer_amount),
                    "employee_amount": float(component.employee_amount),
                    "employer_rate": float(component.employer_rate) if component.employer_rate is not None else None,
                    "employee_rate": float(component.employee_rate) if component.employee_rate is not None else None,
                    "notes": component.notes,
                }
                for key, component in social_breakdown.components.items()
            },
            "notes": social_breakdown.notes,
        }
        deduction_items.append(
            {
                "concept_key": "imss_infonavit_trabajador",
                "label": "IMSS/INFONAVIT trabajador",
                "amount": float(social_security_employee),
            }
        )
        notes.extend(social_breakdown.notes)

    gross_taxable = _money(taxable_total)
    gross_exempt = _money(exempt_total)

    raw_isr = _calculate_isr_from_brackets(
        taxable_income=gross_taxable,
        brackets=isr_brackets,
    )
    subsidy_applied = _D("0.00")
    if subsidio_row:
        prorated_limit = _prorate_monthly_amount(_dec(subsidio_row.income_limit), days_paid)
        if gross_taxable <= prorated_limit:
            subsidy_candidate = _prorate_monthly_amount(_dec(subsidio_row.subsidy_amount), days_paid)
            subsidy_applied = min(raw_isr, subsidy_candidate)

    isr_withheld = _money(max(_D("0.00"), raw_isr - subsidy_applied))
    deduction_items.append(
        {
            "concept_key": "isr_withheld",
            "label": "ISR retenido",
            "amount": float(isr_withheld),
        }
    )
    if subsidy_applied > 0:
        deduction_items.append(
            {
                "concept_key": "subsidy_applied",
                "label": "Subsidio al empleo aplicado",
                "amount": float(-subsidy_applied),
            }
        )

    recurring_deduction = _money(_dec(deduction_profile.monthly_deduction_amount) if deduction_profile and deduction_profile.monthly_deduction_amount is not None else _D("0"))
    if recurring_deduction > 0:
        manual_deductions_total += recurring_deduction
        deduction_items.append(
            {
                "concept_key": "recurring_deduction",
                "label": deduction_profile.payroll_deduction_name or deduction_profile.deduction_name or "Deducción recurrente",
                "amount": float(recurring_deduction),
            }
        )

    pre_credit_net = _money(gross_taxable + gross_exempt - manual_deductions_total - social_security_employee - isr_withheld)
    credit_deductions_total, credit_deduction_items, credit_notes = _calculate_credit_deductions(
        deduction_profile=deduction_profile,
        calculation_date=calculation_date,
        gross_total=_money(gross_taxable + gross_exempt),
        net_before_credits=pre_credit_net,
    )
    if credit_deductions_total > 0:
        manual_deductions_total += credit_deductions_total
        deduction_items.extend(credit_deduction_items)
        notes.extend(credit_notes)

    pre_alimony_net = _money(gross_taxable + gross_exempt - manual_deductions_total - social_security_employee - isr_withheld)
    alimony_amount, alimony_item, alimony_notes = _calculate_alimony_deduction(
        deduction_profile=deduction_profile,
        calculation_date=calculation_date,
        gross_total=_money(gross_taxable + gross_exempt),
        net_before_alimony=pre_alimony_net,
        extraordinary_total=_money(exempt_total),
    )
    if alimony_amount > 0 and alimony_item:
        manual_deductions_total += alimony_amount
        deduction_items.append(alimony_item)
        notes.extend(alimony_notes)

    deductions_total = _money(manual_deductions_total + social_security_employee + isr_withheld)
    net_pay = _money(gross_taxable + gross_exempt - deductions_total)

    incidents_meta = {
        "count": len(incidents),
        "items": incident_summary,
    }

    return PayrollRunComputationLine(
        payroll_employee_id=payroll_employee.id,
        empleado_name=str(employee_name),
        days_paid=_money(days_paid),
        taxable_total=gross_taxable,
        exempt_total=gross_exempt,
        deductions_total=deductions_total,
        employer_charges_total=social_security_employer,
        isr_withheld=isr_withheld,
        subsidy_applied=_money(subsidy_applied),
        net_pay=net_pay,
        integrated_daily_salary_used=_money(integrated_daily_salary_used),
        perceptions_json={"items": perception_items},
        deductions_json={"items": deduction_items},
        employer_charges_json=social_security_payload,
        incidents_summary=incidents_meta,
        notes=notes,
    )


def _calculate_credit_deductions(
    *,
    deduction_profile: Optional[Any],
    calculation_date: date,
    gross_total: Decimal,
    net_before_credits: Decimal,
) -> tuple[Decimal, List[Dict[str, Any]], List[str]]:
    if deduction_profile is None:
        return _D("0.00"), [], []

    remaining_available = max(_D("0.00"), _money(net_before_credits))
    total = _D("0.00")
    items: List[Dict[str, Any]] = []
    notes: List[str] = []

    def _append_credit_item(
        *,
        concept_key: str,
        label: str,
        amount: Decimal,
        metadata: Dict[str, Any],
        note: str,
    ) -> None:
        nonlocal remaining_available, total
        if amount <= 0 or remaining_available <= 0:
            return
        applied = min(_money(amount), remaining_available)
        if applied <= 0:
            return
        total += applied
        remaining_available = _money(max(_D("0.00"), remaining_available - applied))
        payload = {"concept_key": concept_key, "label": label, "amount": float(applied)}
        payload.update(metadata)
        items.append(payload)
        notes.append(note + (" (capado por neto disponible)" if applied != _money(amount) else ""))

    infonavit_start = getattr(deduction_profile, "infonavit_start_date", None)
    infonavit_type = (getattr(deduction_profile, "infonavit_discount_type", None) or "").strip().lower()
    infonavit_value = _money(_dec(getattr(deduction_profile, "infonavit_discount_value", None)))
    if infonavit_value > 0 and (infonavit_start is None or calculation_date >= infonavit_start):
        if "porcentaje" in infonavit_type:
            infonavit_amount = _money(gross_total * (infonavit_value / _D("100")))
            infonavit_note = f"INFONAVIT calculado como porcentaje sobre bruto ({infonavit_value}%)."
        else:
            infonavit_amount = infonavit_value
            infonavit_note = "INFONAVIT calculado como cuota fija del aviso."
        _append_credit_item(
            concept_key="infonavit_credit_retention",
            label="Crédito INFONAVIT",
            amount=infonavit_amount,
            metadata={
                "discount_type": getattr(deduction_profile, "infonavit_discount_type", None),
                "credit_number": getattr(deduction_profile, "infonavit_credit_number", None),
                "notice_folio": getattr(deduction_profile, "infonavit_notice_folio", None),
            },
            note=infonavit_note,
        )

    fonacot_start = getattr(deduction_profile, "fonacot_start_date", None)
    fonacot_type = (getattr(deduction_profile, "fonacot_discount_type", None) or "").strip().lower()
    fonacot_value = _money(_dec(getattr(deduction_profile, "fonacot_discount_value", None)))
    if fonacot_value > 0 and (fonacot_start is None or calculation_date >= fonacot_start):
        if "neto" in fonacot_type:
            fonacot_amount = _money(net_before_credits * (fonacot_value / _D("100")))
            fonacot_note = f"FONACOT calculado como porcentaje sobre neto ({fonacot_value}%)."
        elif "bruto" in fonacot_type or "porcentaje" in fonacot_type:
            fonacot_amount = _money(gross_total * (fonacot_value / _D("100")))
            fonacot_note = f"FONACOT calculado como porcentaje sobre bruto ({fonacot_value}%)."
        else:
            fonacot_amount = fonacot_value
            fonacot_note = "FONACOT calculado como cuota fija."
        _append_credit_item(
            concept_key="fonacot_retention",
            label="Crédito FONACOT",
            amount=fonacot_amount,
            metadata={
                "discount_type": getattr(deduction_profile, "fonacot_discount_type", None),
                "credit_folio": getattr(deduction_profile, "fonacot_credit_folio", None),
            },
            note=fonacot_note,
        )

    return _money(total), items, notes


def _calculate_alimony_deduction(
    *,
    deduction_profile: Optional[Any],
    calculation_date: date,
    gross_total: Decimal,
    net_before_alimony: Decimal,
    extraordinary_total: Decimal,
) -> tuple[Decimal, Optional[Dict[str, Any]], List[str]]:
    if deduction_profile is None:
        return _D("0.00"), None, []

    effective_from = getattr(deduction_profile, "alimony_effective_from", None)
    effective_to = getattr(deduction_profile, "alimony_effective_to", None)
    if effective_from and calculation_date < effective_from:
        return _D("0.00"), None, []
    if effective_to and calculation_date > effective_to:
        return _D("0.00"), None, []

    mode = (getattr(deduction_profile, "alimony_mode", None) or "").strip().lower()
    percent = _dec(getattr(deduction_profile, "alimony_percentage", None))
    fixed_amount = _money(_dec(getattr(deduction_profile, "alimony_fixed_amount", None)))
    apply_to_extraordinary = bool(getattr(deduction_profile, "alimony_apply_to_extraordinary", False))

    if not apply_to_extraordinary and extraordinary_total > 0:
        base_gross = max(_D("0.00"), _money(gross_total - extraordinary_total))
        base_net = max(_D("0.00"), _money(net_before_alimony - extraordinary_total))
    else:
        base_gross = gross_total
        base_net = net_before_alimony

    notes: List[str] = []
    amount = _D("0.00")
    if mode == "percent_gross" and percent > 0:
        amount = _money(base_gross * (percent / _D("100")))
        notes.append(f"Pensión alimenticia calculada sobre bruto ({percent}%).")
    elif mode == "percent_net" and percent > 0:
        amount = _money(base_net * (percent / _D("100")))
        notes.append(f"Pensión alimenticia calculada sobre neto ({percent}%).")
    elif mode == "fixed_amount" and fixed_amount > 0:
        amount = fixed_amount
        notes.append("Pensión alimenticia calculada como monto fijo por oficio.")
    elif percent > 0:
        amount = _money(base_net * (percent / _D("100")))
        notes.append(f"Modo no definido; se aplicó criterio conservador sobre neto ({percent}%).")

    amount = min(amount, max(_D("0.00"), net_before_alimony))
    if amount <= 0:
        return _D("0.00"), None, notes

    item = {
        "concept_key": "alimony_retention",
        "label": "Retención por pensión alimenticia",
        "amount": float(amount),
        "mode": mode or "percent_net",
        "case_number": getattr(deduction_profile, "alimony_case_number", None),
        "beneficiary_name": getattr(deduction_profile, "alimony_beneficiary_name", None),
        "beneficiary_bank": getattr(deduction_profile, "alimony_beneficiary_bank", None),
        "beneficiary_account": getattr(deduction_profile, "alimony_beneficiary_account", None),
        "beneficiary_clabe": getattr(deduction_profile, "alimony_beneficiary_clabe", None),
        "court_name": getattr(deduction_profile, "alimony_court_name", None),
        "office_reference": getattr(deduction_profile, "alimony_office_reference", None),
        "priority_order": getattr(deduction_profile, "alimony_priority_order", None),
    }
    return amount, item, notes


async def build_payment_instructions_for_run(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> List[PayrollPaymentInstruction]:
    lines = (
        await session.execute(
            select(PayrollRunLine)
            .options(
                selectinload(PayrollRunLine.payroll_employee).selectinload(PayrollEmployee.empleado),
                selectinload(PayrollRunLine.payroll_employee).selectinload(PayrollEmployee.payment_profile),
                selectinload(PayrollRunLine.payroll_employee).selectinload(PayrollEmployee.deduction_profile),
            )
            .where(PayrollRunLine.run_id == run_id)
            .order_by(PayrollRunLine.net_pay.desc())
        )
    ).scalars().all()

    instructions: List[PayrollPaymentInstruction] = []
    for line in lines:
        payroll_employee = line.payroll_employee
        empleado_name = (payroll_employee.empleado.nombre if payroll_employee and payroll_employee.empleado else None) or (payroll_employee.empleado.email if payroll_employee and payroll_employee.empleado else None) or str(line.payroll_employee_id)
        payment = payroll_employee.payment_profile if payroll_employee else None
        instructions.append(
            PayrollPaymentInstruction(
                kind="employee_net",
                payroll_employee_id=line.payroll_employee_id,
                empleado_name=str(empleado_name),
                beneficiary_name=str(empleado_name),
                amount=_money(_dec(line.net_pay)),
                bank_name=payment.bank_name if payment else None,
                account_number=payment.account_number if payment else None,
                clabe=payment.clabe if payment else None,
                reference=f"Nomina run {run_id}",
                metadata={"payment_method": payment.payment_method if payment else None},
            )
        )

        for item in list((line.deductions_json or {}).get("items") or []):
            if item.get("concept_key") != "alimony_retention":
                continue
            deduction_profile = payroll_employee.deduction_profile if payroll_employee else None
            instructions.append(
                PayrollPaymentInstruction(
                    kind="alimony_beneficiary",
                    payroll_employee_id=line.payroll_employee_id,
                    empleado_name=str(empleado_name),
                    beneficiary_name=str(item.get("beneficiary_name") or getattr(deduction_profile, "alimony_beneficiary_name", None) or "Beneficiario pensión"),
                    amount=_money(_dec(item.get("amount"))),
                    bank_name=item.get("beneficiary_bank") or (getattr(deduction_profile, "alimony_beneficiary_bank", None) if deduction_profile else None),
                    account_number=item.get("beneficiary_account") or (getattr(deduction_profile, "alimony_beneficiary_account", None) if deduction_profile else None),
                    clabe=item.get("beneficiary_clabe") or (getattr(deduction_profile, "alimony_beneficiary_clabe", None) if deduction_profile else None),
                    reference=item.get("case_number") or item.get("office_reference"),
                    metadata={
                        "case_number": item.get("case_number"),
                        "court_name": item.get("court_name"),
                        "office_reference": item.get("office_reference"),
                        "priority_order": item.get("priority_order"),
                    },
                )
            )
    return instructions


async def assess_account_mapping_for_run(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> PayrollAccountCoverageReport:
    run = (
        await session.execute(
            select(PayrollRun).where(PayrollRun.id == run_id)
        )
    ).scalar_one()
    lines = (
        await session.execute(
            select(PayrollRunLine)
            .options(
                selectinload(PayrollRunLine.payroll_employee)
                .selectinload(PayrollEmployee.employer_registration)
                .selectinload(PayrollEmployerRegistration.employer),
                selectinload(PayrollRunLine.payroll_employee).selectinload(PayrollEmployee.compensation_profile),
                selectinload(PayrollRunLine.payroll_employee).selectinload(PayrollEmployee.empleado),
            )
            .where(PayrollRunLine.run_id == run_id)
        )
    ).scalars().all()
    active_accounts = (
        await session.execute(
            select(CuentaContable).where(CuentaContable.activo == True).order_by(CuentaContable.codigo.asc())
        )
    ).scalars().all()
    employer_ids = {
        line.payroll_employee.employer_registration.payroll_employer_id
        for line in lines
        if line.payroll_employee
        and line.payroll_employee.employer_registration
        and line.payroll_employee.employer_registration.payroll_employer_id
    }
    employer_filter_ids = employer_ids or {UUID(int=0)}
    mappings = (
        await session.execute(
            select(PayrollAccountMapping)
            .options(selectinload(PayrollAccountMapping.cuenta_contable))
            .where(
                PayrollAccountMapping.active == True,
                or_(
                    PayrollAccountMapping.payroll_employer_id.is_(None),
                    PayrollAccountMapping.payroll_employer_id.in_(employer_filter_ids),
                ),
            )
        )
    ).scalars().all()
    statuses = [
        _assess_payroll_account_resolution(
            mappings=mappings,
            accounts=active_accounts,
            purpose=purpose,
            amount=amount,
            employer_ids={employer_id} if employer_id else set(),
            payroll_employer_id=employer_id,
            payroll_employer_name=employer_name,
        )
        for _, amount, purpose, _, employer_id, employer_name in _build_policy_buckets_by_employer(lines)
    ]
    return PayrollAccountCoverageReport(
        run_id=run.id,
        employer_ids=sorted(employer_ids, key=str),
        required_purpose_count=len(statuses),
        explicit_count=sum(1 for status in statuses if status.resolution_kind == "explicit"),
        heuristic_count=sum(1 for status in statuses if status.resolution_kind == "heuristic"),
        missing_count=sum(1 for status in statuses if status.resolution_kind == "missing"),
        statuses=statuses,
    )


async def generate_accounting_policy_for_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    created_by_empleado_id: Optional[UUID],
) -> PayrollAccountingPolicyResult:
    run = (
        await session.execute(
            select(PayrollRun)
            .options(selectinload(PayrollRun.period))
            .where(PayrollRun.id == run_id)
        )
    ).scalar_one()
    lines = (
        await session.execute(
            select(PayrollRunLine)
            .options(
                selectinload(PayrollRunLine.payroll_employee).selectinload(PayrollEmployee.compensation_profile),
                selectinload(PayrollRunLine.payroll_employee).selectinload(PayrollEmployee.empleado),
                selectinload(PayrollRunLine.payroll_employee)
                .selectinload(PayrollEmployee.employer_registration)
                .selectinload(PayrollEmployerRegistration.employer),
            )
            .where(PayrollRunLine.run_id == run_id)
        )
    ).scalars().all()
    active_accounts = (
        await session.execute(
            select(CuentaContable).where(CuentaContable.activo == True).order_by(CuentaContable.codigo.asc())
        )
    ).scalars().all()
    employer_ids = {
        line.payroll_employee.employer_registration.payroll_employer_id
        for line in lines
        if line.payroll_employee
        and line.payroll_employee.employer_registration
        and line.payroll_employee.employer_registration.payroll_employer_id
    }
    employer_filter_ids = employer_ids or {UUID(int=0)}
    mappings = (
        await session.execute(
            select(PayrollAccountMapping)
            .options(selectinload(PayrollAccountMapping.cuenta_contable))
            .where(
                PayrollAccountMapping.active == True,
                or_(
                    PayrollAccountMapping.payroll_employer_id.is_(None),
                    PayrollAccountMapping.payroll_employer_id.in_(employer_filter_ids),
                ),
            )
        )
    ).scalars().all()

    poliza_source = f"payroll_run:{run.id}"
    import_run = (
        await session.execute(
            select(AccountingImportRun).where(
                AccountingImportRun.source_type == "payroll",
                AccountingImportRun.filename == poliza_source,
            )
        )
    ).scalar_one_or_none()
    if import_run is None:
        import_run = AccountingImportRun(
            id=uuid4(),
            source_type="payroll",
            filename=poliza_source,
            mode="apply",
            status="completed",
            started_by_empleado_id=created_by_empleado_id,
            finished_at=datetime.utcnow(),
        )
        session.add(import_run)
        await session.flush()

    poliza = (
        await session.execute(
            select(AccountingPoliza).where(
                AccountingPoliza.source_file == poliza_source,
                AccountingPoliza.tipo_poliza == "Diario",
                AccountingPoliza.numero_poliza == f"NOM-{run.period.fiscal_year}-{run.period.period_no}",
            )
        )
    ).scalar_one_or_none()
    if poliza is None:
        poliza = AccountingPoliza(
            id=uuid4(),
            import_run_id=import_run.id,
            source_file=poliza_source,
            source_sheet="payroll_run",
            tipo_poliza="Diario",
            numero_poliza=f"NOM-{run.period.fiscal_year}-{run.period.period_no}",
            fecha_poliza=datetime.combine(run.period.payment_date or run.period.end_date, datetime.min.time()),
            beneficiario_nombre="Nómina",
            concepto=f"Nómina {run.period.period_type} {run.period.fiscal_year}-{run.period.period_no}",
            concepto_resumen="Póliza automática de nómina",
            line_count_declared=0,
            line_count_actual=0,
            origen="payroll",
        )
        session.add(poliza)
        await session.flush()
    else:
        existing_lines = (
            await session.execute(select(AccountingPolizaLine).where(AccountingPolizaLine.poliza_id == poliza.id))
        ).scalars().all()
        for line in existing_lines:
            await session.delete(line)

    buckets = _build_policy_buckets_by_employer(lines)

    unresolved_accounts: List[str] = []
    line_no = 1
    total_debe = _D("0.00")
    total_haber = _D("0.00")
    for side, amount, account_purpose, concept, employer_id, _ in buckets:
        account = _resolve_payroll_account_mapping(
            mappings=mappings,
            accounts=active_accounts,
            purpose=account_purpose,
            employer_ids={employer_id} if employer_id else set(),
        )
        if account is None:
            unresolved_accounts.append(account_purpose)
            cuenta_codigo = f"PENDIENTE:{account_purpose}"
            cuenta_id = None
        else:
            cuenta_codigo = account.codigo
            cuenta_id = account.id

        poliza_line = AccountingPolizaLine(
            id=uuid4(),
            poliza_id=poliza.id,
            line_no=line_no,
            cuenta_codigo=cuenta_codigo,
            cuenta_contable_id=cuenta_id,
            concepto=concept,
            movimiento_no=str(line_no),
            debe=float(amount) if side == "debe" else 0.0,
            haber=float(amount) if side == "haber" else 0.0,
            raw_row_json={"payroll_purpose": account_purpose},
        )
        session.add(poliza_line)
        if side == "debe":
            total_debe += amount
        else:
            total_haber += amount
        line_no += 1

    poliza.line_count_declared = len(buckets)
    poliza.line_count_actual = len(buckets)
    import_run.summary_json = {
        "run_id": str(run.id),
        "line_count": len(buckets),
        "unresolved_accounts": unresolved_accounts,
        "total_debe": float(_money(total_debe)),
        "total_haber": float(_money(total_haber)),
    }
    await session.commit()

    return PayrollAccountingPolicyResult(
        poliza_id=poliza.id,
        import_run_id=import_run.id,
        line_count=len(buckets),
        total_debe=_money(total_debe),
        total_haber=_money(total_haber),
        unresolved_accounts=unresolved_accounts,
    )


async def close_payroll_period(
    session: AsyncSession,
    *,
    period_id: UUID,
    closed_by_empleado_id: Optional[UUID],
) -> PayrollAccountCoverageReport:
    period = (
        await session.execute(
            select(PayrollPeriod).where(PayrollPeriod.id == period_id)
        )
    ).scalar_one()
    latest_run = (
        await session.execute(
            select(PayrollRun)
            .where(PayrollRun.period_id == period_id)
            .order_by(PayrollRun.updated_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if latest_run is None:
        raise ValueError("No hay prenómina calculada para este período.")
    coverage = await assess_account_mapping_for_run(session, run_id=latest_run.id)
    if not has_strict_payroll_account_coverage(coverage):
        raise ValueError("La cobertura contable no está al 100% configurada.")
    period.status = "closed"
    latest_run.status = "posted"
    latest_run.notes = (
        ((latest_run.notes or "").strip() + f"\nPeriodo cerrado por {closed_by_empleado_id} el {datetime.utcnow().isoformat()}")
        .strip()
    )
    await session.commit()
    return coverage


def _pick_account_for_payroll_purpose(
    accounts: Sequence[CuentaContable],
    purpose: str,
) -> Optional[CuentaContable]:
    purpose_map: dict[str, list[str]] = {
        "sueldos_salarios": ["sueldo", "sueldos", "salarios", "nómina", "nomina"],
        "asimilados_salarios": ["asimilado"],
        "cargas_patronales": ["cuotas patronales", "seguridad social", "imss patronal", "cargas patronales"],
        "isr_retenido_nomina": ["isr retenido", "isr por pagar", "retenciones isr"],
        "imss_obrero_por_pagar": ["imss obrero", "imss trabajador", "cuotas obreras"],
        "imss_patronal_por_pagar": ["imss patronal", "cuotas patronales por pagar"],
        "infonavit_patronal_por_pagar": ["infonavit"],
        "infonavit_credito_por_pagar": ["infonavit crédito", "infonavit credito", "credito infonavit", "infonavit por pagar"],
        "fonacot_por_pagar": ["fonacot", "fonacot por pagar"],
        "pension_alimenticia_por_pagar": ["pensión alimenticia", "pension alimenticia", "alimentos por pagar"],
        "nomina_por_pagar": ["nómina por pagar", "nomina por pagar", "sueldos por pagar", "acreedores diversos"],
    }
    patterns = purpose_map.get(purpose, [])
    for account in accounts:
        haystack = f"{account.codigo} {account.nombre} {account.tipo}".lower()
        if any(pattern in haystack for pattern in patterns):
            return account
    return None


def _build_policy_buckets(
    lines: Sequence[PayrollRunLine],
) -> List[tuple[str, Decimal, str, str]]:
    buckets: List[tuple[str, Decimal, str, str]] = []
    gross_sueldos = _money(sum((_money(_dec(line.taxable_total) + _dec(line.exempt_total)) for line in lines if not _is_asimilado((line.payroll_employee.compensation_profile.compensation_regime if line.payroll_employee and line.payroll_employee.compensation_profile else ""))), _D("0")))
    gross_asimilados = _money(sum((_money(_dec(line.taxable_total) + _dec(line.exempt_total)) for line in lines if _is_asimilado((line.payroll_employee.compensation_profile.compensation_regime if line.payroll_employee and line.payroll_employee.compensation_profile else ""))), _D("0")))
    total_isr = _money(sum((_dec(line.isr_withheld) for line in lines), _D("0")))
    total_employee_ss = _money(sum((_employee_social_security_from_line(line) for line in lines), _D("0")))
    total_employer_ss = _money(sum((_employer_social_security_from_line(line) for line in lines), _D("0")))
    total_infonavit = _money(sum((_component_sum_from_line(line, "infonavit_patron", side="employer") for line in lines), _D("0")))
    total_infonavit_credit = _money(sum((_deduction_component_sum_from_line(line, "infonavit_credit_retention") for line in lines), _D("0")))
    total_fonacot = _money(sum((_deduction_component_sum_from_line(line, "fonacot_retention") for line in lines), _D("0")))
    total_alimony = _money(sum((_alimony_from_line(line) for line in lines), _D("0")))
    total_net = _money(sum((_dec(line.net_pay) for line in lines), _D("0")))

    if gross_sueldos > 0:
        buckets.append(("debe", gross_sueldos, "sueldos_salarios", "Sueldos y salarios del período"))
    if gross_asimilados > 0:
        buckets.append(("debe", gross_asimilados, "asimilados_salarios", "Asimilados a salarios del período"))
    if total_employer_ss > 0:
        buckets.append(("debe", total_employer_ss, "cargas_patronales", "Cargas patronales IMSS/INFONAVIT"))
    if total_isr > 0:
        buckets.append(("haber", total_isr, "isr_retenido_nomina", "ISR retenido por pagar"))
    if total_employee_ss > 0:
        buckets.append(("haber", total_employee_ss, "imss_obrero_por_pagar", "IMSS trabajador por pagar"))
    if total_infonavit > 0:
        buckets.append(("haber", total_infonavit, "infonavit_patronal_por_pagar", "INFONAVIT patronal por pagar"))
    if total_infonavit_credit > 0:
        buckets.append(("haber", total_infonavit_credit, "infonavit_credito_por_pagar", "Crédito INFONAVIT retenido por pagar"))
    if total_fonacot > 0:
        buckets.append(("haber", total_fonacot, "fonacot_por_pagar", "Crédito FONACOT retenido por pagar"))
    if total_employer_ss - total_infonavit > 0:
        buckets.append(("haber", _money(total_employer_ss - total_infonavit), "imss_patronal_por_pagar", "IMSS patronal por pagar"))
    if total_alimony > 0:
        buckets.append(("haber", total_alimony, "pension_alimenticia_por_pagar", "Pensión alimenticia por pagar"))
    if total_net > 0:
        buckets.append(("haber", total_net, "nomina_por_pagar", "Nómina por pagar a empleados"))
    return buckets


def _build_policy_buckets_by_employer(
    lines: Sequence[PayrollRunLine],
) -> List[tuple[str, Decimal, str, str, Optional[UUID], Optional[str]]]:
    grouped: dict[tuple[Optional[UUID], Optional[str]], list[PayrollRunLine]] = {}
    for line in lines:
        employer_key = _line_employer_identity(line)
        grouped.setdefault(employer_key, []).append(line)

    buckets: List[tuple[str, Decimal, str, str, Optional[UUID], Optional[str]]] = []
    for (employer_id, employer_name), employer_lines in grouped.items():
        for side, amount, purpose, concept in _build_policy_buckets(employer_lines):
            scoped_concept = concept if not employer_name else f"{concept} · {employer_name}"
            buckets.append((side, amount, purpose, scoped_concept, employer_id, employer_name))
    return buckets


def _line_employer_identity(line: PayrollRunLine) -> tuple[Optional[UUID], Optional[str]]:
    payroll_employee = line.payroll_employee
    registration = payroll_employee.employer_registration if payroll_employee else None
    employer = registration.employer if registration else None
    if employer is None:
        return None, None
    return employer.id, employer.legal_name or None


def _assess_payroll_account_resolution(
    *,
    mappings: Sequence[PayrollAccountMapping],
    accounts: Sequence[CuentaContable],
    purpose: str,
    amount: Decimal,
    employer_ids: set[UUID],
    payroll_employer_id: Optional[UUID] = None,
    payroll_employer_name: Optional[str] = None,
) -> PayrollAccountPurposeStatus:
    explicit_account, explicit_scope = _find_explicit_payroll_account_mapping(
        mappings=mappings,
        purpose=purpose,
        employer_ids=employer_ids,
    )
    if explicit_account is not None:
        return PayrollAccountPurposeStatus(
            purpose_key=purpose,
            purpose_label=PAYROLL_ACCOUNT_PURPOSE_LABELS.get(purpose, purpose),
            amount=_money(amount),
            resolution_kind="explicit",
            cuenta_codigo=explicit_account.codigo,
            cuenta_nombre=explicit_account.nombre,
            source_scope=explicit_scope,
            payroll_employer_id=payroll_employer_id,
            payroll_employer_name=payroll_employer_name,
        )
    heuristic_account = _pick_account_for_payroll_purpose(accounts, purpose)
    if heuristic_account is not None:
        return PayrollAccountPurposeStatus(
            purpose_key=purpose,
            purpose_label=PAYROLL_ACCOUNT_PURPOSE_LABELS.get(purpose, purpose),
            amount=_money(amount),
            resolution_kind="heuristic",
            cuenta_codigo=heuristic_account.codigo,
            cuenta_nombre=heuristic_account.nombre,
            source_scope="heuristic",
            payroll_employer_id=payroll_employer_id,
            payroll_employer_name=payroll_employer_name,
        )
    return PayrollAccountPurposeStatus(
        purpose_key=purpose,
        purpose_label=PAYROLL_ACCOUNT_PURPOSE_LABELS.get(purpose, purpose),
        amount=_money(amount),
        resolution_kind="missing",
        cuenta_codigo=None,
        cuenta_nombre=None,
        source_scope="missing",
        payroll_employer_id=payroll_employer_id,
        payroll_employer_name=payroll_employer_name,
    )


def _find_explicit_payroll_account_mapping(
    *,
    mappings: Sequence[PayrollAccountMapping],
    purpose: str,
    employer_ids: set[UUID],
) -> tuple[Optional[CuentaContable], str]:
    employer_specific = [
        mapping
        for mapping in mappings
        if mapping.purpose_key == purpose and mapping.payroll_employer_id in employer_ids
    ]
    if employer_specific:
        return employer_specific[0].cuenta_contable, "employer"
    global_mapping = next(
        (
            mapping
            for mapping in mappings
            if mapping.purpose_key == purpose and mapping.payroll_employer_id is None
        ),
        None,
    )
    if global_mapping:
        return global_mapping.cuenta_contable, "global"
    return None, "missing"


def _resolve_payroll_account_mapping(
    *,
    mappings: Sequence[PayrollAccountMapping],
    accounts: Sequence[CuentaContable],
    purpose: str,
    employer_ids: set[UUID],
) -> Optional[CuentaContable]:
    explicit_account, _ = _find_explicit_payroll_account_mapping(
        mappings=mappings,
        purpose=purpose,
        employer_ids=employer_ids,
    )
    if explicit_account is not None:
        return explicit_account
    return _pick_account_for_payroll_purpose(accounts, purpose)


def _employee_social_security_from_line(line: PayrollRunLine) -> Decimal:
    payload = dict(line.employer_charges_json or {})
    total = _D("0.00")
    for component in dict(payload.get("components") or {}).values():
        total += _dec(component.get("employee_amount"))
    return _money(total)


def _employer_social_security_from_line(line: PayrollRunLine) -> Decimal:
    payload = dict(line.employer_charges_json or {})
    total = _D("0.00")
    for component in dict(payload.get("components") or {}).values():
        total += _dec(component.get("employer_amount"))
    return _money(total)


def _component_sum_from_line(line: PayrollRunLine, component_key: str, *, side: str) -> Decimal:
    payload = dict(line.employer_charges_json or {})
    component = dict(dict(payload.get("components") or {}).get(component_key) or {})
    return _money(_dec(component.get(f"{side}_amount")))


def _deduction_component_sum_from_line(line: PayrollRunLine, concept_key: str) -> Decimal:
    total = _D("0.00")
    for item in list((line.deductions_json or {}).get("items") or []):
        if item.get("concept_key") == concept_key:
            total += _dec(item.get("amount"))
    return _money(total)


def _alimony_from_line(line: PayrollRunLine) -> Decimal:
    total = _D("0.00")
    for item in list((line.deductions_json or {}).get("items") or []):
        if item.get("concept_key") == "alimony_retention":
            total += _dec(item.get("amount"))
    return _money(total)


def _resolve_days_paid(
    payroll_employee: PayrollEmployee,
    period: PayrollPeriod,
    incidents: Sequence[PayrollIncident],
) -> Decimal:
    base_days = _dec(payroll_employee.worked_days_override)
    if base_days <= 0:
        base_days = _D(str((period.end_date - period.start_date).days + 1))
    absence_days = sum(
        (_dec(incident.quantity) for incident in incidents if (incident.incident_type or "").strip().lower() == "absence"),
        _D("0"),
    )
    return _money(max(_D("0.00"), base_days - absence_days))


def _resolve_daily_salary(payroll_employee: PayrollEmployee) -> Decimal:
    compensation = payroll_employee.compensation_profile
    candidates = [
        compensation.daily_salary if compensation else None,
        payroll_employee.daily_salary,
    ]
    for candidate in candidates:
        value = _dec(candidate)
        if value > 0:
            return _money(value)
    if compensation and compensation.monthly_net_salary:
        return _money(_dec(compensation.monthly_net_salary) / _D("30.4"))
    return _D("0.00")


def _resolve_variable_salary(payroll_employee: PayrollEmployee) -> Decimal:
    compensation = payroll_employee.compensation_profile
    return _money(
        _dec(compensation.variable_salary if compensation else None) or _dec(payroll_employee.variable_salary)
    )


def _resolve_integrated_daily_salary(
    payroll_employee: PayrollEmployee,
    fallback: Decimal,
) -> Decimal:
    compensation = payroll_employee.compensation_profile
    candidates = [
        compensation.integrated_daily_salary if compensation else None,
        payroll_employee.integrated_daily_salary,
    ]
    for candidate in candidates:
        value = _dec(candidate)
        if value > 0:
            return _money(value)
    return _money(fallback)


__all__ = [
    "PayrollAccountingPolicyResult",
    "PayrollPaymentInstruction",
    "PayrollRunComputationLine",
    "PayrollRunComputationResult",
    "build_payment_instructions_for_run",
    "calculate_payroll_run_for_period",
    "generate_accounting_policy_for_run",
]
