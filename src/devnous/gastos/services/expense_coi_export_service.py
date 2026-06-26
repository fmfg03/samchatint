"""Per-expense COI export helpers (limpieza-contable-ready gastos only)."""

from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import ExpenseReport
from .coi_poliza_exporter import ExpenseCFDI
from .expense_accounting_cleanup_service import build_cleanup_preview
from .expense_accounting_service import build_expense_accounting_preview

_NON_FISCAL_ACCOUNT_NAMES = {
    "sin requisitos fiscales",
    "no deducible",
    "gastos no deducibles",
}


def _normalize_account_name(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def allows_coi_without_cfdi(account: object) -> bool:
    name = _normalize_account_name(getattr(account, "nombre", None))
    return name in _NON_FISCAL_ACCOUNT_NAMES


async def assess_expense_coi_cleanup_ready(
    session: AsyncSession,
    expense: ExpenseReport,
) -> Tuple[bool, List[str]]:
    """True when the expense matches Centro de Limpieza 'Listo COI' after save."""
    state = await build_cleanup_preview(session, expense)
    issues = list(state.get("issues") or [])
    return state.get("status") == "Listo COI", issues


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
    ready, issues = await assess_expense_coi_cleanup_ready(session, expense)
    if require_cleanup_ready and not ready:
        detail = "; ".join(issues) if issues else "Gasto pendiente de limpieza contable."
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
    "allows_coi_without_cfdi",
    "assess_expense_coi_cleanup_ready",
    "build_expense_cfdi_for_export",
    "load_expense_for_coi_export",
]
