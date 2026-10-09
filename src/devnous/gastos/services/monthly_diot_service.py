"""Monthly DIOT scope resolved from canonical effective payment dates."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Documento, ExpenseReport


@dataclass(frozen=True)
class MonthlyDiotIssue:
    expense_id: str
    reference: str
    code: str
    message: str
    effective_payment_date: date | None = None
    cfdi_uuid: str = ""

    def as_row(self) -> Dict[str, Any]:
        return {
            "referencia": self.reference,
            "fecha_pago_efectiva": (
                self.effective_payment_date.isoformat()
                if self.effective_payment_date
                else ""
            ),
            "codigo": self.code,
            "detalle": self.message,
            "uuid_cfdi": self.cfdi_uuid,
        }


@dataclass
class MonthlyDiotScope:
    year: int
    month: int
    start_date: date
    end_date: date
    eligible_expenses: List[ExpenseReport] = field(default_factory=list)
    effective_payment_dates: Dict[str, date] = field(default_factory=dict)
    payment_date_sources: Dict[str, str] = field(default_factory=dict)
    blockers: List[MonthlyDiotIssue] = field(default_factory=list)
    undated: List[MonthlyDiotIssue] = field(default_factory=list)

    @property
    def can_export_txt(self) -> bool:
        return bool(self.eligible_expenses) and not self.blockers


def monthly_period_bounds(year: int, month: int) -> tuple[date, date]:
    if year < 2000 or year > 2100:
        raise ValueError("El año DIOT debe estar entre 2000 y 2100.")
    if month < 1 or month > 12:
        raise ValueError("El mes DIOT debe estar entre 1 y 12.")
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _normalized_dates(values: Iterable[date | None]) -> List[date]:
    return sorted({value for value in values if value is not None})


def _expense_key(expense: ExpenseReport) -> str:
    return str(expense.id)


def _expense_reference(expense: ExpenseReport) -> str:
    return str(getattr(expense, "numero_referencia", None) or expense.id)


def _cfdi_uuid(expense: ExpenseReport) -> str:
    cfdi = getattr(expense, "cfdi_report", None)
    return str(getattr(cfdi, "cfdi_uuid", None) or "")


def _issue(
    expense: ExpenseReport,
    code: str,
    message: str,
    *,
    effective_payment_date: date | None = None,
) -> MonthlyDiotIssue:
    return MonthlyDiotIssue(
        expense_id=_expense_key(expense),
        reference=_expense_reference(expense),
        code=code,
        message=message,
        effective_payment_date=effective_payment_date,
        cfdi_uuid=_cfdi_uuid(expense),
    )


def _applied_fiscal_amount(expense: ExpenseReport) -> Decimal:
    amount = Decimal(str(getattr(expense, "gasto_cantidad", None) or 0))
    tip = Decimal(str(getattr(expense, "propina_no_deducible", None) or 0))
    return amount - tip


def build_monthly_diot_scope_from_records(
    expenses: Sequence[ExpenseReport],
    *,
    year: int,
    month: int,
    direct_payment_dates: Mapping[str, Sequence[date]],
    account_payment_dates: Mapping[str, Sequence[date]],
) -> MonthlyDiotScope:
    """Classify loaded expenses without mutating financial records."""
    start_date, end_date = monthly_period_bounds(year, month)
    scope = MonthlyDiotScope(year, month, start_date, end_date)
    active_expenses = [
        expense
        for expense in expenses
        if (
            str(getattr(expense, "estado_gasto", "activo") or "activo")
            == "activo"
            and str(
                getattr(expense, "coi_estado", "pendiente") or "pendiente"
            )
            != "reversar"
        )
    ]

    duplicate_groups: Dict[str, List[ExpenseReport]] = {}
    for expense in active_expenses:
        cfdi_id = getattr(expense, "cfdi_report_id", None)
        if cfdi_id:
            duplicate_groups.setdefault(str(cfdi_id), []).append(expense)

    duplicate_errors: Dict[str, str] = {}
    for group in duplicate_groups.values():
        if len(group) <= 1:
            continue
        if not all(
            bool(getattr(expense, "cfdi_compartido_confirmado", False))
            for expense in group
        ):
            for expense in group:
                duplicate_errors[_expense_key(expense)] = (
                    "El CFDI está vinculado a varios gastos sin confirmación "
                    "completa de factura compartida."
                )
            continue
        cfdi = getattr(group[0], "cfdi_report", None)
        fiscal_total = Decimal(str(getattr(cfdi, "total", None) or 0))
        applied_total = sum(
            (_applied_fiscal_amount(expense) for expense in group),
            Decimal("0"),
        )
        invalid_application = (
            fiscal_total <= 0
            or applied_total <= 0
            or applied_total > fiscal_total + Decimal("0.02")
        )
        if invalid_application:
            for expense in group:
                duplicate_errors[_expense_key(expense)] = (
                    "Las aplicaciones de la factura compartida no son válidas "
                    "contra el total fiscal del CFDI."
                )

    for expense in active_expenses:
        key = _expense_key(expense)
        direct_dates = _normalized_dates(direct_payment_dates.get(key, ()))
        account_dates = _normalized_dates(
            account_payment_dates.get(
                str(getattr(expense, "cuenta_gastos_id", "")), ()
            )
        )
        effective_date: date | None = None
        source = ""
        if len(direct_dates) == 1:
            effective_date = direct_dates[0]
            source = "documento_directo"
        elif len(direct_dates) > 1:
            scope.undated.append(
                _issue(
                    expense,
                    "fecha_pago_ambigua",
                    "El gasto tiene varias fechas efectivas en documentos "
                    "vinculados.",
                )
            )
            continue
        elif len(account_dates) == 1:
            effective_date = account_dates[0]
            source = "cuenta_gastos_fecha_unica"
        elif len(account_dates) > 1:
            scope.undated.append(
                _issue(
                    expense,
                    "fecha_pago_ambigua",
                    "La cuenta de gastos tiene varias fechas efectivas "
                    "posibles.",
                )
            )
            continue
        else:
            if getattr(expense, "cfdi_report_id", None):
                scope.undated.append(
                    _issue(
                        expense,
                        "fecha_pago_faltante",
                        "No existe fecha efectiva de pago vinculada al gasto.",
                    )
                )
            continue

        if effective_date < start_date or effective_date > end_date:
            continue

        cfdi = getattr(expense, "cfdi_report", None)
        if cfdi is None:
            scope.blockers.append(
                _issue(
                    expense,
                    "cfdi_faltante",
                    "El movimiento pagado no tiene CFDI vinculado.",
                    effective_payment_date=effective_date,
                )
            )
            continue
        if not str(getattr(cfdi, "emisor_rfc", None) or "").strip():
            scope.blockers.append(
                _issue(
                    expense,
                    "rfc_emisor_faltante",
                    "El CFDI no tiene RFC de emisor.",
                    effective_payment_date=effective_date,
                )
            )
            continue
        currency = str(getattr(cfdi, "moneda", None) or "MXN").strip().upper()
        if currency != "MXN":
            scope.blockers.append(
                _issue(
                    expense,
                    "moneda_no_soportada",
                    "La DIOT mensual no convierte automáticamente moneda "
                    f"{currency}.",
                    effective_payment_date=effective_date,
                )
            )
            continue
        cfdi_type = str(
            getattr(cfdi, "tipo_de_comprobante", None) or "I"
        ).upper()
        if cfdi_type != "I":
            scope.blockers.append(
                _issue(
                    expense,
                    "tipo_cfdi_no_soportado",
                    f"El CFDI tipo {cfdi_type} requiere revisión fiscal "
                    "manual.",
                    effective_payment_date=effective_date,
                )
            )
            continue
        if key in duplicate_errors:
            scope.blockers.append(
                _issue(
                    expense,
                    "cfdi_duplicado_no_confirmado",
                    duplicate_errors[key],
                    effective_payment_date=effective_date,
                )
            )
            continue

        scope.eligible_expenses.append(expense)
        scope.effective_payment_dates[key] = effective_date
        scope.payment_date_sources[key] = source

    scope.eligible_expenses.sort(
        key=lambda expense: (
            scope.effective_payment_dates[_expense_key(expense)],
            _expense_reference(expense),
        )
    )
    scope.blockers.sort(
        key=lambda issue: (
            issue.effective_payment_date or date.min,
            issue.reference,
        )
    )
    scope.undated.sort(key=lambda issue: issue.reference)
    return scope


async def build_monthly_diot_scope(
    session: AsyncSession,
    *,
    year: int,
    month: int,
) -> MonthlyDiotScope:
    """Load the complete read-only monthly DIOT candidate scope."""
    expenses = list(
        (
            await session.execute(
                select(ExpenseReport)
                .options(selectinload(ExpenseReport.cfdi_report))
                .where(ExpenseReport.estado_gasto == "activo")
            )
        )
        .scalars()
        .all()
    )
    if not expenses:
        return build_monthly_diot_scope_from_records(
            [],
            year=year,
            month=month,
            direct_payment_dates={},
            account_payment_dates={},
        )

    expense_ids = [expense.id for expense in expenses]
    document_to_expenses: Dict[str, set[str]] = {}
    direct_document_ids = set()
    for expense in expenses:
        expense_id = _expense_key(expense)
        for document_id in (
            getattr(expense, "documento_id", None),
            getattr(expense, "solicitud_documento_id", None),
        ):
            if document_id:
                document_key = str(document_id)
                direct_document_ids.add(document_id)
                document_to_expenses.setdefault(document_key, set()).add(
                    expense_id
                )

    direct_conditions = [Documento.gasto_generado_id.in_(expense_ids)]
    if direct_document_ids:
        direct_conditions.append(Documento.id.in_(direct_document_ids))
    direct_documents = list(
        (
            await session.execute(
                select(Documento).where(
                    or_(*direct_conditions),
                    Documento.fecha_pago_efectiva.is_not(None),
                    or_(
                        Documento.estado == "pagado",
                        Documento.pagado_en.is_not(None),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    direct_dates: Dict[str, List[date]] = {}
    for document in direct_documents:
        linked_expense_ids = set(
            document_to_expenses.get(str(document.id), set())
        )
        if document.gasto_generado_id:
            linked_expense_ids.add(str(document.gasto_generado_id))
        for expense_id in linked_expense_ids:
            direct_dates.setdefault(expense_id, []).append(
                document.fecha_pago_efectiva
            )

    account_ids = {
        expense.cuenta_gastos_id
        for expense in expenses
        if expense.cuenta_gastos_id
    }
    account_dates: Dict[str, List[date]] = {}
    if account_ids:
        account_documents = list(
            (
                await session.execute(
                    select(Documento).where(
                        Documento.cuenta_gastos_id.in_(account_ids),
                        Documento.tipo == "SOLICITUD",
                        Documento.fecha_pago_efectiva.is_not(None),
                        or_(
                            Documento.estado == "pagado",
                            Documento.pagado_en.is_not(None),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        for document in account_documents:
            account_key = str(document.cuenta_gastos_id)
            account_dates.setdefault(account_key, []).append(
                document.fecha_pago_efectiva
            )

    return build_monthly_diot_scope_from_records(
        expenses,
        year=year,
        month=month,
        direct_payment_dates=direct_dates,
        account_payment_dates=account_dates,
    )


__all__ = [
    "MonthlyDiotIssue",
    "MonthlyDiotScope",
    "build_monthly_diot_scope",
    "build_monthly_diot_scope_from_records",
    "monthly_period_bounds",
]
