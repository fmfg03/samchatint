"""Authorization strategy matrix for expense documents.

Pure resolver for Plataforma Sports' authorization matrix. It is advisory for
this stage: it codifies the business rules without replacing the current
single-approver workflow yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
import unicodedata
from typing import Iterable, Optional

ANY_AMOUNT = "any"
MAX_AMOUNT = "max"
MIN_EXCLUSIVE_AMOUNT = "gt"


@dataclass(frozen=True)
class ApproverRole:
    key: str
    label: str
    employee_matchers: tuple[str, ...]


@dataclass(frozen=True)
class AuthorizationStrategyRule:
    key: str
    area_key: str
    erogation_key: str
    erogation_label: str
    amount_mode: str
    amount_value: Optional[Decimal]
    requester_group: str
    first_approver_role: str
    second_approver_roles: tuple[str, ...] = ()
    alternate_when_other_area_role: Optional[str] = None
    conditions: tuple[str, ...] = ()
    priority: int = 100
    requires_pending_advance_review: bool = False
    requires_no_invoice: bool = False
    requires_unbudgeted: bool = False
    requires_budget_excess: bool = False
    requires_urgent: bool = False


@dataclass(frozen=True)
class AuthorizationStrategyDecision:
    rule: Optional[AuthorizationStrategyRule]
    first_approver_roles: tuple[ApproverRole, ...]
    second_approver_roles: tuple[ApproverRole, ...]
    applies_alternate_for_other_area: bool = False
    fallback_reason: Optional[str] = None

    @property
    def required_role_keys(self) -> tuple[str, ...]:
        keys = [role.key for role in self.first_approver_roles]
        keys.extend(role.key for role in self.second_approver_roles)
        return tuple(dict.fromkeys(keys))


APPROVER_ROLES: dict[str, ApproverRole] = {
    "dg": ApproverRole("dg", "DG", ("federico gonzalez",)),
    "dayf": ApproverRole("dayf", "DAyF", ("luis angel orozco", "luis angel")),
    "dgoat": ApproverRole("dgoat", "DGoat", ("olof",)),
    "director_operaciones": ApproverRole(
        "director_operaciones",
        "Director de Operaciones",
        ("odilon trujillo", "jose odilon trujillo"),
    ),
    "gerente_ayf": ApproverRole(
        "gerente_ayf", "Gerente de AyF", ("benjamin jimenez", "benjamin")
    ),
    "director_ayf": ApproverRole(
        "director_ayf", "Director de AyF", ("luis angel orozco", "luis angel")
    ),
}


def _money(value: int | str) -> Decimal:
    return Decimal(str(value))


def _rule(
    key: str,
    area: str,
    erogation: str,
    label: str,
    amount_mode: str,
    amount_value: Optional[int | str],
    requester: str,
    first: str,
    *,
    second: Iterable[str] = (),
    alternate_other_area: Optional[str] = None,
    conditions: Iterable[str] = (),
    priority: int = 100,
    pending_advance: bool = False,
    no_invoice: bool = False,
    unbudgeted: bool = False,
    budget_excess: bool = False,
    urgent: bool = False,
) -> AuthorizationStrategyRule:
    return AuthorizationStrategyRule(
        key=key,
        area_key=area,
        erogation_key=erogation,
        erogation_label=label,
        amount_mode=amount_mode,
        amount_value=_money(amount_value) if amount_value is not None else None,
        requester_group=requester,
        first_approver_role=first,
        second_approver_roles=tuple(second),
        alternate_when_other_area_role=alternate_other_area,
        conditions=tuple(conditions),
        priority=priority,
        requires_pending_advance_review=pending_advance,
        requires_no_invoice=no_invoice,
        requires_unbudgeted=unbudgeted,
        requires_budget_excess=budget_excess,
        requires_urgent=urgent,
    )


AUTHORIZATION_STRATEGY_RULES: tuple[AuthorizationStrategyRule, ...] = (
    _rule(
        "ops_supplier_transfer_le_100k",
        "operaciones",
        "supplier_transfer",
        "Solicitud de transferencia por gasto operativo y compras a proveedores",
        MAX_AMOUNT,
        100000,
        "Team Operaciones",
        "director_operaciones",
        alternate_other_area="director_operaciones",
        conditions=(
            "Proveedor con contrato/convenio",
            "Presupuestado",
            "Factura fiscal del mismo mes",
        ),
    ),
    _rule(
        "ops_supplier_transfer_gt_100k",
        "operaciones",
        "supplier_transfer",
        "Solicitud de transferencia por gasto operativo y compras a proveedores",
        MIN_EXCLUSIVE_AMOUNT,
        100000,
        "Team Operaciones",
        "director_operaciones",
        second=("dg",),
    ),
    _rule(
        "ops_travel_expense_report",
        "operaciones",
        "travel_expense_report",
        "Informe de gastos por viaticos y gastos de viaje",
        ANY_AMOUNT,
        None,
        "Team Operaciones",
        "director_operaciones",
        conditions=("Presupuestado", "No excede presupuesto"),
    ),
    _rule(
        "ops_tournament_advance_le_10k",
        "operaciones",
        "tournament_advance",
        "Anticipos para gastos de torneos",
        MAX_AMOUNT,
        10000,
        "Team Operaciones",
        "director_operaciones",
        conditions=("Maximo dos anticipos sin comprobar", "Comprobacion 30 dias"),
    ),
    _rule(
        "ops_tournament_advance_gt_10k_or_pending",
        "operaciones",
        "tournament_advance",
        "Anticipos para gastos de torneos",
        MIN_EXCLUSIVE_AMOUNT,
        10000,
        "Team Operaciones",
        "director_operaciones",
        pending_advance=True,
        priority=20,
    ),
    _rule(
        "ops_tournament_reimbursement",
        "operaciones",
        "tournament_reimbursement",
        "Informe de gastos por reembolso de gastos de torneos",
        ANY_AMOUNT,
        None,
        "Team Operaciones",
        "director_operaciones",
    ),
    _rule(
        "ops_urgent_le_25k",
        "operaciones",
        "urgent_exception",
        "Pagos urgentes o de excepcion",
        MAX_AMOUNT,
        25000,
        "Gerente de Operaciones",
        "director_operaciones",
        urgent=True,
        conditions=("Presupuestado",),
    ),
    _rule(
        "ops_urgent_gt_25k",
        "operaciones",
        "urgent_exception",
        "Pagos urgentes o de excepcion",
        MIN_EXCLUSIVE_AMOUNT,
        25000,
        "Gerente de Operaciones",
        "director_operaciones",
        second=("dg", "dayf"),
        urgent=True,
    ),
    _rule(
        "ops_extra_cost",
        "operaciones",
        "extra_operation_cost",
        "Costos extra de operacion",
        ANY_AMOUNT,
        None,
        "Gerente de Operaciones",
        "director_operaciones",
        second=("dg",),
    ),
    _rule(
        "ops_unbudgeted",
        "operaciones",
        "unbudgeted_cost",
        "Costos no presupuestados",
        ANY_AMOUNT,
        None,
        "Gerente de Operaciones",
        "director_operaciones",
        second=("dg",),
        unbudgeted=True,
    ),
    _rule(
        "ops_no_deductible",
        "operaciones",
        "no_deductible",
        "No deducibles / gastos sin factura",
        ANY_AMOUNT,
        None,
        "Team Operaciones",
        "director_operaciones",
        second=("dayf",),
        no_invoice=True,
        priority=10,
    ),
    _rule(
        "ops_budget_excess",
        "operaciones",
        "budget_excess",
        "Excedentes contra presupuesto",
        ANY_AMOUNT,
        None,
        "Gerente de Operaciones",
        "director_operaciones",
        second=("dg",),
        budget_excess=True,
    ),
    _rule(
        "goat_supplier_transfer_le_10k",
        "comunicaciones_rrss_patrocinios",
        "supplier_transfer",
        "Solicitud de transferencia por gasto operativo y compras a proveedores",
        MAX_AMOUNT,
        10000,
        "Team Goat",
        "dgoat",
        alternate_other_area="dgoat",
    ),
    _rule(
        "goat_supplier_transfer_gt_10k",
        "comunicaciones_rrss_patrocinios",
        "supplier_transfer",
        "Solicitud de transferencia por gasto operativo y compras a proveedores",
        MIN_EXCLUSIVE_AMOUNT,
        10000,
        "Team Goat",
        "dgoat",
        second=("dg",),
    ),
    _rule(
        "goat_travel_expense_report_le_10k",
        "comunicaciones_rrss_patrocinios",
        "travel_expense_report",
        "Informe de gastos por viaticos y gastos de viaje",
        MAX_AMOUNT,
        10000,
        "Team Goat",
        "dgoat",
    ),
    _rule(
        "goat_travel_expense_report_gt_10k",
        "comunicaciones_rrss_patrocinios",
        "travel_expense_report",
        "Informe de gastos por viaticos y gastos de viaje",
        MIN_EXCLUSIVE_AMOUNT,
        10000,
        "Team Goat",
        "dgoat",
        second=("dg",),
    ),
    _rule(
        "goat_tournament_advance_gt_10k_or_pending",
        "comunicaciones_rrss_patrocinios",
        "tournament_advance",
        "Anticipos para gastos de torneos",
        MIN_EXCLUSIVE_AMOUNT,
        10000,
        "Team Goat",
        "dgoat",
        second=("dg",),
        pending_advance=True,
        priority=20,
    ),
    _rule(
        "goat_no_deductible",
        "comunicaciones_rrss_patrocinios",
        "no_deductible",
        "No deducibles / gastos sin factura",
        ANY_AMOUNT,
        None,
        "Team Goat",
        "dgoat",
        second=("dayf",),
        no_invoice=True,
        priority=10,
    ),
    _rule(
        "admin_fixed_asset",
        "administracion",
        "fixed_asset",
        "Adquisicion de activo fijo",
        ANY_AMOUNT,
        None,
        "Todos",
        "dayf",
        second=("dg",),
        conditions=("OK previo de DG",),
    ),
    _rule(
        "admin_ordinary_payroll",
        "administracion",
        "ordinary_payroll",
        "Nomina ordinaria y servicios externos de base",
        ANY_AMOUNT,
        None,
        "Gerente de AyF",
        "director_ayf",
    ),
    _rule(
        "admin_extraordinary_payroll",
        "administracion",
        "extraordinary_payroll",
        "Nomina extraordinaria",
        ANY_AMOUNT,
        None,
        "Gerente de AyF",
        "director_ayf",
        second=("dg",),
    ),
    _rule(
        "admin_office_maintenance_gt_10k",
        "administracion",
        "office_maintenance",
        "Mantenimiento de oficina",
        MIN_EXCLUSIVE_AMOUNT,
        10000,
        "Gerente de AyF",
        "director_ayf",
        second=("dg",),
    ),
    _rule(
        "admin_office_maintenance_le_10k",
        "administracion",
        "office_maintenance",
        "Mantenimiento de oficina",
        MAX_AMOUNT,
        10000,
        "Gerente de AyF",
        "director_ayf",
    ),
    _rule(
        "admin_no_deductible",
        "administracion",
        "no_deductible",
        "No deducibles / gastos sin factura",
        ANY_AMOUNT,
        None,
        "Team AyF",
        "director_ayf",
        no_invoice=True,
        priority=10,
    ),
)


def normalize_strategy_key(value: object) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


AREA_ALIASES = {
    "operaciones": "operaciones",
    "comunicaciones": "comunicaciones_rrss_patrocinios",
    "rrss": "comunicaciones_rrss_patrocinios",
    "patrocinios": "comunicaciones_rrss_patrocinios",
    "mercadotecnia": "comunicaciones_rrss_patrocinios",
    "administracion": "administracion",
    "administracion_finanzas": "administracion",
    "finanzas": "administracion",
}


def canonical_area_key(value: object) -> str:
    key = normalize_strategy_key(value)
    return AREA_ALIASES.get(key, key)


def _amount_matches(rule: AuthorizationStrategyRule, amount: Decimal) -> bool:
    if rule.amount_mode == ANY_AMOUNT or rule.amount_value is None:
        return True
    if rule.amount_mode == MAX_AMOUNT:
        return amount <= rule.amount_value
    if rule.amount_mode == MIN_EXCLUSIVE_AMOUNT:
        return amount > rule.amount_value
    return False


def _flag_matches(
    rule: AuthorizationStrategyRule,
    *,
    has_pending_advance: bool,
    has_invoice: bool,
    is_budgeted: bool,
    exceeds_budget: bool,
    is_urgent: bool,
) -> bool:
    if rule.requires_pending_advance_review and not has_pending_advance:
        return False
    if rule.requires_no_invoice and has_invoice:
        return False
    if rule.requires_unbudgeted and is_budgeted:
        return False
    if rule.requires_budget_excess and not exceeds_budget:
        return False
    if rule.requires_urgent and not is_urgent:
        return False
    return True


def resolve_authorization_strategy(
    *,
    area: object,
    erogation_type: object,
    amount_mxn: object,
    requester_area: object = None,
    has_pending_advance: bool = False,
    has_invoice: bool = True,
    is_budgeted: bool = True,
    exceeds_budget: bool = False,
    is_urgent: bool = False,
) -> AuthorizationStrategyDecision:
    area_key = canonical_area_key(area)
    erogation_key = normalize_strategy_key(erogation_type)
    amount = Decimal(str(amount_mxn or 0))
    requester_area_key = canonical_area_key(requester_area or area)

    candidates = [
        rule
        for rule in AUTHORIZATION_STRATEGY_RULES
        if rule.area_key == area_key
        and rule.erogation_key == erogation_key
        and (
            _amount_matches(rule, amount)
            or (rule.requires_pending_advance_review and has_pending_advance)
        )
        and _flag_matches(
            rule,
            has_pending_advance=has_pending_advance,
            has_invoice=has_invoice,
            is_budgeted=is_budgeted,
            exceeds_budget=exceeds_budget,
            is_urgent=is_urgent,
        )
    ]
    if not candidates:
        return AuthorizationStrategyDecision(
            rule=None,
            first_approver_roles=(),
            second_approver_roles=(),
            fallback_reason="no_matching_authorization_strategy_rule",
        )

    rule = sorted(candidates, key=lambda item: item.priority)[0]
    first_role_key = rule.first_approver_role
    alternate = False
    if requester_area_key != area_key and rule.alternate_when_other_area_role:
        first_role_key = rule.alternate_when_other_area_role
        alternate = True

    first_roles = tuple(
        role for role in (APPROVER_ROLES.get(first_role_key),) if role is not None
    )
    second_roles = tuple(
        role
        for role in (APPROVER_ROLES.get(key) for key in rule.second_approver_roles)
        if role is not None
    )
    return AuthorizationStrategyDecision(
        rule=rule,
        first_approver_roles=first_roles,
        second_approver_roles=second_roles,
        applies_alternate_for_other_area=alternate,
    )


def employee_matches_approver_role(employee: object, role: ApproverRole) -> bool:
    haystack = normalize_strategy_key(
        " ".join(
            str(getattr(employee, attr, "") or "")
            for attr in ("nombre", "correo", "departamento")
        )
    )
    return any(
        normalize_strategy_key(matcher) in haystack
        for matcher in role.employee_matchers
    )


def employee_matches_any_required_role(
    employee: object,
    decision: AuthorizationStrategyDecision,
) -> bool:
    return any(
        employee_matches_approver_role(employee, role)
        for role in (*decision.first_approver_roles, *decision.second_approver_roles)
    )
