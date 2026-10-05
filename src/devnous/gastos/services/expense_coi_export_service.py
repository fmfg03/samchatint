"""Per-expense COI export helpers (limpieza-contable-ready gastos only)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Tuple

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from sqlalchemy.sql.elements import ColumnElement

from ..models import AmexAccountingCut, CuentaDeGastos, Documento, ExpenseReport
from .amex_expense_service import company_amex_sql_condition, is_company_amex_expense
from .coi_poliza_exporter import ExpenseCFDI
from .employee_debtor_accounting_service import (
    debtor_account_block_label_for_employee,
    resolve_cuenta_debtor_account,
    resolve_cuenta_debtor_empleado,
)
from .expense_accounting_cleanup_service import build_cleanup_preview
from .expense_accounting_service import build_expense_accounting_preview

_NON_FISCAL_ACCOUNT_NAMES = {
    "sin requisitos fiscales",
    "no deducible",
    "gastos no deducibles",
}


def informe_expense_link_condition(
    expense_model: Any = ExpenseReport,
) -> ColumnElement[bool]:
    """Canonical direct and legacy links from an expense to its INFORME."""
    return or_(
        expense_model.documento_id == Documento.id,
        expense_model.informe_documento_id == Documento.id,
        and_(
            Documento.cuenta_gastos_id.isnot(None),
            expense_model.cuenta_gastos_id == Documento.cuenta_gastos_id,
        ),
    )


def informe_coi_period_condition(start: datetime, end: datetime) -> ColumnElement[bool]:
    """Select one policy period; incomplete period evidence stays visibly blocked.

    Normal reports belong to their approval month. AMEX belongs to its frozen
    initial cut month. Expense dates locate missing evidence only, never authorize
    a policy in another period.
    """
    expense = aliased(ExpenseReport)
    active_link = and_(
        expense.estado_gasto != "cancelado", informe_expense_link_condition(expense)
    )
    has_amex = exists(
        select(expense.id).where(active_link, company_amex_sql_condition(expense))
    ).correlate(Documento)
    expense_in_period = exists(
        select(expense.id).where(
            active_link, expense.fecha >= start, expense.fecha < end
        )
    ).correlate(Documento)
    cut_link = and_(
        AmexAccountingCut.informe_id == Documento.id,
        AmexAccountingCut.kind == "initial",
    )
    has_cut = exists(select(AmexAccountingCut.id).where(cut_link)).correlate(Documento)
    cut_in_period = exists(
        select(AmexAccountingCut.id).where(
            cut_link,
            AmexAccountingCut.accounting_date >= start.date(),
            AmexAccountingCut.accounting_date < end.date(),
        )
    ).correlate(Documento)
    return or_(
        and_(~has_amex, Documento.aprobado_en >= start, Documento.aprobado_en < end),
        and_(~has_amex, Documento.aprobado_en.is_(None), expense_in_period),
        and_(has_amex, cut_in_period),
        and_(has_amex, ~has_cut, expense_in_period),
    )


def expense_coi_batch_period_condition(
    start: datetime, end: datetime
) -> ColumnElement[bool]:
    """Use the owner's policy period, retaining expense dates for standalone rows."""
    owner = and_(Documento.tipo == "INFORME", informe_expense_link_condition())
    has_owner = exists(select(Documento.id).where(owner)).correlate(ExpenseReport)
    owner_in_period = exists(
        select(Documento.id).where(owner, informe_coi_period_condition(start, end))
    ).correlate(ExpenseReport)
    return or_(
        owner_in_period,
        and_(~has_owner, ExpenseReport.fecha >= start, ExpenseReport.fecha < end),
    )


def _normalize_account_name(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def allows_coi_without_cfdi(account: object) -> bool:
    name = _normalize_account_name(getattr(account, "nombre", None))
    return name in _NON_FISCAL_ACCOUNT_NAMES


async def _resolve_informe_detail_counterpart(
    session: AsyncSession,
    expense: ExpenseReport,
) -> Optional[str]:
    """Resolve the beneficiary detail account for employee-paid report expenses.

    The resolution is cached on the request/session object by CuentaDeGastos so
    a grouped COI policy does not repeat beneficiary and chart-account lookups
    for every expense line.
    """
    cuenta_gastos_id = getattr(expense, "cuenta_gastos_id", None)
    if not cuenta_gastos_id or is_company_amex_expense(expense):
        return None

    cache_attr = "_samchat_informe_counterpart_cache"
    cache = getattr(session, cache_attr, None)
    if cache is None:
        cache = {}
        try:
            setattr(session, cache_attr, cache)
        except (AttributeError, TypeError):
            pass

    cache_key = str(cuenta_gastos_id)
    if cache_key in cache:
        cached_code, cached_error = cache[cache_key]
        if cached_error:
            raise ValueError(cached_error)
        return cached_code

    cuenta = await session.get(CuentaDeGastos, cuenta_gastos_id)
    if cuenta is None:
        error = "El gasto pertenece a un Informe de Gastos sin cuenta vinculada válida."
        cache[cache_key] = (None, error)
        raise ValueError(error)

    empleado = await resolve_cuenta_debtor_empleado(session, cuenta)
    debtor_account = await resolve_cuenta_debtor_account(session, cuenta, empleado)
    code = str(getattr(debtor_account, "codigo", "") or "").strip()
    if code:
        cache[cache_key] = (code, None)
        return code

    block = debtor_account_block_label_for_employee(empleado)
    error = (
        "Falta subcuenta contable de detalle para el beneficiario del Informe "
        f"de Gastos ({block})."
    )
    cache[cache_key] = (None, error)
    raise ValueError(error)


def group_expense_cfdis_for_document(
    expense_cfdis: List[ExpenseCFDI],
    documento: Any,
) -> List[ExpenseCFDI]:
    """Bind all INFORME expenses to one COI policy without changing SOLICITUD."""
    if getattr(documento, "tipo", None) != "INFORME":
        return expense_cfdis
    reference = str(
        getattr(documento, "numero_referencia", None)
        or getattr(documento, "id", "INFORME")
    )
    group_key = f"informe:{getattr(documento, 'id', reference)}"
    description = f"Informe de Gastos {reference}"
    for expense_cfdi in expense_cfdis:
        expense_cfdi.poliza_group_key = group_key
        expense_cfdi.poliza_reference = reference
        expense_cfdi.poliza_description = description
    return expense_cfdis


async def assess_expense_coi_cleanup_ready(
    session: AsyncSession,
    expense: ExpenseReport,
) -> Tuple[bool, List[str]]:
    """True when the expense is safe to emit in a COI policy."""
    state = await build_cleanup_preview(session, expense)
    issues = list(state.get("issues") or [])
    try:
        await _resolve_informe_detail_counterpart(session, expense)
    except ValueError as exc:
        issues.append(str(exc))
    return state.get("status") == "Listo COI" and not issues, issues


async def build_expense_cfdi_for_export(
    session: AsyncSession,
    expense: ExpenseReport,
    *,
    require_cleanup_ready: bool = True,
) -> ExpenseCFDI:
    """
    Build one ExpenseCFDI row using the same path as solicitudes a terceros / finanzas.

    Requires persisted cleanup fields (cuenta, contrapartida, CFDI unless non-fiscal).
    """
    if require_cleanup_ready:
        ready, issues = await assess_expense_coi_cleanup_ready(session, expense)
        if not ready:
            detail = (
                "; ".join(issues) if issues else "Gasto pendiente de limpieza contable."
            )
            raise ValueError(detail)

    cuenta_contable = getattr(expense, "cuenta_contable", None)
    contra_cuenta = getattr(expense, "contra_cuenta_contable", None)
    cfdi = getattr(expense, "cfdi_report", None)

    if cuenta_contable is None or not getattr(cuenta_contable, "codigo", None):
        raise ValueError("Falta cuenta de cargo persistida en el gasto.")

    allows_missing_cfdi = bool(
        allows_coi_without_cfdi(cuenta_contable)
        and not getattr(expense, "cfdi_report_id", None)
    )
    if not getattr(expense, "cfdi_report_id", None) and not allows_missing_cfdi:
        raise ValueError("Falta CFDI vinculado en el gasto.")

    preview = await build_expense_accounting_preview(session, expense)
    taxes = preview.get("taxes") or {}
    contra_account = preview.get("contra_account") or {}

    contra_codigo = str(
        contra_account.get("codigo")
        or (contra_cuenta.codigo if contra_cuenta else "")
        or ""
    ).strip()
    informe_detail_counterpart = await _resolve_informe_detail_counterpart(
        session, expense
    )
    if informe_detail_counterpart:
        contra_codigo = informe_detail_counterpart
    if not contra_codigo:
        raise ValueError("Falta contrapartida persistida en el gasto.")

    iva_amount = round(float(taxes.get("iva_trasladado") or 0), 2)
    total_amount = round(float(expense.gasto_cantidad or 0), 2)
    subtotal_amount = round(total_amount - iva_amount, 2)

    retenciones = [
        {
            "label": item.get("label"),
            "importe": float(item.get("importe") or 0.0),
            "cuenta_contable": (item.get("account", {}) or {}).get("codigo"),
        }
        for item in list(taxes.get("retenciones") or [])
    ]
    impuestos_locales = [
        {
            "kind": item.get("kind") or "tax",
            "label": item.get("label") or "Impuesto local",
            "importe": float(item.get("importe") or 0.0),
            "cuenta_contable": (item.get("account", {}) or {}).get("codigo"),
            "entidad": item.get("entidad"),
            "tasa_pct": item.get("tasa_pct"),
            "confirmado": bool(item.get("confirmado")),
        }
        for item in list(taxes.get("impuestos_locales") or [])
    ]
    gastos_no_deducibles = [
        {
            "kind": item.get("kind") or "gasto",
            "label": item.get("label") or "No deducible",
            "importe": float(item.get("importe") or 0.0),
            "cuenta_contable": (item.get("account", {}) or {}).get("codigo"),
        }
        for item in list(taxes.get("gastos_no_deducibles") or [])
    ]

    return ExpenseCFDI(
        fecha=expense.fecha,
        total=total_amount,
        iva_amount=iva_amount,
        subtotal_amount=subtotal_amount,
        concepto=expense.concepto or "Gasto",
        cuenta_contable=str(cuenta_contable.codigo),
        cuenta_contrapartida=contra_codigo,
        cfdi_uuid=getattr(cfdi, "cfdi_uuid", None),
        cfdi_date=getattr(cfdi, "fecha", None),
        rfc_emisor=getattr(cfdi, "emisor_rfc", None),
        rfc_receptor=getattr(cfdi, "receptor_rfc", None),
        folio=getattr(cfdi, "folio", None),
        nombre_emisor=getattr(cfdi, "emisor_nombre", None),
        receptor_uso_cfdi=getattr(cfdi, "receptor_uso_cfdi", None),
        cuenta_iva=str((taxes.get("iva_account") or {}).get("codigo") or ""),
        retenciones=retenciones,
        impuestos_locales=impuestos_locales,
        gastos_no_deducibles=gastos_no_deducibles,
        neto_contrapartida=float(taxes.get("neto_contrapartida") or total_amount),
        base_amount=float(taxes.get("base_gasto") or subtotal_amount),
        export_reference=expense.numero_referencia or expense.concepto or "",
        proyecto=expense.proyecto,
        cuenta_contable_nombre=str(getattr(cuenta_contable, "nombre", "") or ""),
        allows_missing_cfdi=allows_missing_cfdi,
        missing_cfdi_warning=(
            "No deducible sin CFDI. Verifica que la cuenta contable sea "
            "'Sin requisitos fiscales' o 'No deducible'."
            if allows_missing_cfdi
            else None
        ),
    )


async def load_expense_for_coi_export(
    session: AsyncSession,
    expense_id,
) -> Optional[ExpenseReport]:
    result = await session.execute(
        select(ExpenseReport)
        .options(selectinload(ExpenseReport.cuenta_contable))
        .options(selectinload(ExpenseReport.contra_cuenta_contable))
        .options(selectinload(ExpenseReport.cfdi_report))
        .options(selectinload(ExpenseReport.cuenta_iva))
        .where(ExpenseReport.id == expense_id)
    )
    return result.scalar_one_or_none()


__all__ = [
    "informe_expense_link_condition",
    "informe_coi_period_condition",
    "expense_coi_batch_period_condition",
    "allows_coi_without_cfdi",
    "assess_expense_coi_cleanup_ready",
    "build_expense_cfdi_for_export",
    "group_expense_cfdis_for_document",
    "load_expense_for_coi_export",
]
