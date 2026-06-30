"""Accounting cleanup helpers for expense COI readiness.

This service persists cleanup decisions on ExpenseReport so COI export can
reuse the chosen accounts without reclassification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import CFDIReport, CuentaContable, ExpenseReport

DEFAULT_CLEANUP_CONTRA_CUENTA_CODIGO = "1120-001-001"
DEFAULT_CLEANUP_CONTRA_CUENTA_NOMBRE = "BANCO SANTANDER 65506206424"
from .budget_concept_account_service import cleanup_expense_loader_options
from .expense_accounting_service import build_expense_accounting_preview
from .hospedaje_tax_service import normalize_hospedaje_rate, normalize_hospedaje_state


@dataclass(frozen=True)
class CFDICleanupOption:
    """Compact CFDI option for the cleanup dropdown."""

    id: UUID
    uuid: str
    label: str
    total: Optional[float]
    emisor_nombre: Optional[str]
    fecha: Any


def _money_or_none(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
        "si",
        "sí",
    }


def resolve_default_cleanup_contra_cuenta(
    cuentas: Iterable[CuentaContable],
) -> Optional[CuentaContable]:
    """Return the standard Santander bank account used as cleanup contrapartida."""

    for cuenta in cuentas:
        if cuenta.codigo == DEFAULT_CLEANUP_CONTRA_CUENTA_CODIGO:
            return cuenta
    return None


def _cfdi_option_label(cfdi: CFDIReport) -> str:
    uuid = str(cfdi.cfdi_uuid or "").strip() or str(cfdi.id)
    emisor = str(cfdi.emisor_nombre or cfdi.emisor_rfc or "emisor sin dato").strip()
    total = float(cfdi.total or 0.0)
    fecha = (
        cfdi.fecha.strftime("%Y-%m-%d") if getattr(cfdi, "fecha", None) else "sin fecha"
    )
    return f"{uuid} · {emisor} · ${total:,.2f} · {fecha}"


async def list_unassigned_cfdi_options(
    session: AsyncSession,
    *,
    limit: int = 500,
) -> List[CFDICleanupOption]:
    """Return CFDI rows not currently linked to active expenses."""

    linked_cfdi_ids = (
        select(ExpenseReport.cfdi_report_id)
        .where(
            ExpenseReport.cfdi_report_id.isnot(None),
            ExpenseReport.estado_gasto == "activo",
        )
        .distinct()
    )
    result = await session.execute(
        select(CFDIReport)
        .where(~CFDIReport.id.in_(linked_cfdi_ids))
        .order_by(CFDIReport.fecha.desc().nullslast(), CFDIReport.created_at.desc())
        .limit(max(1, min(int(limit or 500), 1000)))
    )
    return [
        CFDICleanupOption(
            id=cfdi.id,
            uuid=cfdi.cfdi_uuid or "",
            label=_cfdi_option_label(cfdi),
            total=float(cfdi.total) if cfdi.total is not None else None,
            emisor_nombre=cfdi.emisor_nombre,
            fecha=cfdi.fecha,
        )
        for cfdi in result.scalars().all()
    ]


async def load_cleanup_expenses(
    session: AsyncSession,
    *,
    extra_conditions: Optional[Iterable[Any]] = None,
) -> List[ExpenseReport]:
    """Load active expenses that still need accounting cleanup."""

    conditions = [
        ExpenseReport.estado_gasto == "activo",
        or_(
            ExpenseReport.cuenta_contable_id.is_(None),
            ExpenseReport.contra_cuenta_contable_id.is_(None),
            ExpenseReport.cfdi_report_id.is_(None),
        ),
    ]
    if extra_conditions:
        conditions.extend(list(extra_conditions))

    loader_options = [
        selectinload(ExpenseReport.empleado),
        selectinload(ExpenseReport.cfdi_report),
        selectinload(ExpenseReport.cuenta_contable),
        selectinload(ExpenseReport.contra_cuenta_contable),
        selectinload(ExpenseReport.cuenta_iva),
        *cleanup_expense_loader_options(),
    ]
    query = select(ExpenseReport).options(*loader_options).where(and_(*conditions))
    result = await session.execute(query.order_by(ExpenseReport.fecha.desc()))
    return result.scalars().all()


async def build_cleanup_preview(
    session: AsyncSession,
    expense: ExpenseReport,
) -> Dict[str, Any]:
    """Build the fiscal/accounting preview plus readiness labels."""

    preview = await build_expense_accounting_preview(session, expense)
    taxes = preview.get("taxes") or {}
    issues = []
    if not getattr(expense, "cuenta_contable_id", None):
        issues.append("Falta cuenta de cargo")
    if not getattr(expense, "contra_cuenta_contable_id", None) and not preview.get(
        "contra_account"
    ):
        issues.append("Falta contrapartida")
    if not getattr(expense, "cfdi_report_id", None):
        issues.append("Falta CFDI vinculado")
    if float(taxes.get("iva_trasladado") or 0.0) > 0 and not (
        taxes.get("iva_account") or {}
    ).get("codigo"):
        issues.append("Falta cuenta de IVA")
    for retention in taxes.get("retenciones") or []:
        if not (retention.get("account") or {}).get("codigo"):
            issues.append(f"Falta cuenta de retención {retention.get('label')}")

    status = "Listo COI" if not issues else "Pendiente"
    return {
        "preview": preview,
        "issues": issues,
        "status": status,
    }


async def save_expense_cleanup(
    session: AsyncSession,
    expense_id: UUID,
    *,
    cuenta_contable_id: Optional[str] = None,
    contra_cuenta_contable_id: Optional[str] = None,
    cuenta_iva_id: Optional[str] = None,
    retention_account_ids: Optional[Dict[str, str]] = None,
    cfdi_report_id: Optional[str] = None,
    iva: Any = None,
    hospedaje_entidad_fiscal: Any = None,
    hospedaje_tasa_impuesto: Any = None,
    hospedaje_impuesto_monto: Any = None,
    hospedaje_impuesto_confirmado: Any = None,
) -> ExpenseReport:
    """Save cleanup choices into existing ExpenseReport fields."""

    result = await session.execute(
        select(ExpenseReport)
        .options(selectinload(ExpenseReport.cuenta_contable))
        .options(selectinload(ExpenseReport.contra_cuenta_contable))
        .options(selectinload(ExpenseReport.cuenta_iva))
        .where(ExpenseReport.id == expense_id)
    )
    expense = result.scalar_one_or_none()
    if expense is None:
        raise ValueError("Gasto no encontrado")

    if cuenta_contable_id:
        cuenta = await _load_active_account(
            session, cuenta_contable_id, "Cuenta contable"
        )
        expense.cuenta_contable_id = cuenta.id

    if contra_cuenta_contable_id:
        contra = await _load_active_account(
            session, contra_cuenta_contable_id, "Contrapartida"
        )
        expense.contra_cuenta_contable_id = contra.id

    if cuenta_iva_id:
        cuenta_iva = await _load_active_account(session, cuenta_iva_id, "Cuenta IVA")
        expense.cuenta_iva_id = cuenta_iva.id

    if retention_account_ids:
        normalized_retention_accounts: Dict[str, str] = {}
        for impuesto_code, raw_account_id in retention_account_ids.items():
            code = str(impuesto_code or "").strip()
            account_id = str(raw_account_id or "").strip()
            if not code or not account_id:
                continue
            account = await _load_active_account(
                session,
                account_id,
                f"Cuenta retención {code}",
            )
            normalized_retention_accounts[code] = str(account.id)
        expense.retencion_cuentas_json = normalized_retention_accounts or None

    if cfdi_report_id:
        cfdi = await _load_cfdi(session, cfdi_report_id)
        expense.cfdi_report_id = cfdi.id
        if cfdi.cfdi_uuid:
            expense.cfdi_uuid_manual = cfdi.cfdi_uuid
    elif not expense.cfdi_report_id and getattr(expense, "nova_request_id", None):
        cfdi = await _load_single_cfdi_by_nova_request_id(
            session, expense.nova_request_id
        )
        if cfdi is not None:
            expense.cfdi_report_id = cfdi.id
            if cfdi.cfdi_uuid:
                expense.cfdi_uuid_manual = cfdi.cfdi_uuid

    iva_amount = _money_or_none(iva)
    if iva in ("", None):
        expense.iva = None
    elif iva_amount is None:
        raise ValueError("El IVA capturado es inválido")
    else:
        expense.iva = iva_amount

    if hospedaje_entidad_fiscal not in (None, ""):
        expense.hospedaje_entidad_fiscal = normalize_hospedaje_state(
            hospedaje_entidad_fiscal
        )
    elif hospedaje_entidad_fiscal == "":
        expense.hospedaje_entidad_fiscal = None

    if hospedaje_tasa_impuesto in (None, ""):
        expense.hospedaje_tasa_impuesto = None
    else:
        rate = normalize_hospedaje_rate(hospedaje_tasa_impuesto)
        if rate is None:
            raise ValueError("La tasa de hospedaje es inválida")
        expense.hospedaje_tasa_impuesto = rate

    hosp_amount = _money_or_none(hospedaje_impuesto_monto)
    if hospedaje_impuesto_monto in (None, ""):
        expense.hospedaje_impuesto_monto = None
    elif hosp_amount is None:
        raise ValueError("El monto de hospedaje es inválido")
    else:
        expense.hospedaje_impuesto_monto = hosp_amount

    expense.hospedaje_impuesto_confirmado = _truthy(hospedaje_impuesto_confirmado)

    session.add(expense)
    await session.commit()
    return expense


async def _load_active_account(
    session: AsyncSession,
    raw_id: str,
    label: str,
) -> CuentaContable:
    try:
        account_id = UUID(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} inválida") from exc

    result = await session.execute(
        select(CuentaContable).where(
            CuentaContable.id == account_id,
            CuentaContable.activo.is_(True),
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError(f"{label} inválida o inactiva")
    return account


async def _load_cfdi(session: AsyncSession, raw_id: str) -> CFDIReport:
    try:
        cfdi_id = UUID(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("CFDI inválido") from exc

    result = await session.execute(select(CFDIReport).where(CFDIReport.id == cfdi_id))
    cfdi = result.scalar_one_or_none()
    if cfdi is None:
        raise ValueError("CFDI no encontrado")
    return cfdi


async def _load_single_cfdi_by_nova_request_id(
    session: AsyncSession,
    nova_request_id: str,
) -> Optional[CFDIReport]:
    result = await session.execute(
        select(CFDIReport)
        .where(CFDIReport.nova_request_id == nova_request_id)
        .order_by(CFDIReport.created_at.desc())
        .limit(2)
    )
    matches = result.scalars().all()
    return matches[0] if len(matches) == 1 else None


__all__ = [
    "CFDICleanupOption",
    "DEFAULT_CLEANUP_CONTRA_CUENTA_CODIGO",
    "DEFAULT_CLEANUP_CONTRA_CUENTA_NOMBRE",
    "build_cleanup_preview",
    "list_unassigned_cfdi_options",
    "load_cleanup_expenses",
    "resolve_default_cleanup_contra_cuenta",
    "save_expense_cleanup",
]
