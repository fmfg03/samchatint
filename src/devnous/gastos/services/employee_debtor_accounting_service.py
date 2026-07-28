from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    AccountingPoliza,
    AccountingPolizaLine,
    CuentaContable,
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
    Reembolso,
)
from .amex_expense_service import employee_paid_sql_condition
from .expense_accounting_service import build_expense_accounting_preview


DEBTOR_ACCOUNT_ROOT_CODE = "1170-001-000"
DEBTOR_ACCOUNT_PREFIX = "1170-001-"
DEBTOR_ORIGINS = {
    "deudores_anticipo",
    "deudores_comprobacion",
    "deudores_devolucion",
}
MONEY_QUANT = Decimal("0.01")


@dataclass(slots=True)
class DebtorPostingResult:
    status: str
    reason: Optional[str] = None
    poliza: Optional[AccountingPoliza] = None
    debtor_account: Optional[CuentaContable] = None
    bank_account: Optional[CuentaContable] = None


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _money_float(value: Any) -> float:
    return float(_money(value))


def _normalize_text(value: Any) -> str:
    raw = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(ascii_text.split())


def _account_label(account: Optional[CuentaContable]) -> str:
    if account is None:
        return "Sin cuenta"
    return f"{account.codigo} · {account.nombre}"


async def resolve_employee_debtor_account(
    session: AsyncSession,
    empleado: Empleado,
) -> Optional[CuentaContable]:
    employee_name = _normalize_text(getattr(empleado, "nombre", None))
    if not employee_name:
        return None
    tokens = [token for token in employee_name.split() if len(token) > 1]
    result = await session.execute(
        select(CuentaContable)
        .where(
            CuentaContable.activo.is_(True),
            CuentaContable.codigo.like(f"{DEBTOR_ACCOUNT_PREFIX}%"),
            CuentaContable.codigo != DEBTOR_ACCOUNT_ROOT_CODE,
        )
        .order_by(CuentaContable.codigo.asc())
    )
    candidates = list(result.scalars().all())
    exact_matches: list[CuentaContable] = []
    token_matches: list[CuentaContable] = []
    for account in candidates:
        account_name = _normalize_text(account.nombre)
        if employee_name and employee_name in account_name:
            exact_matches.append(account)
        elif tokens and all(token in account_name for token in tokens):
            token_matches.append(account)
    matches = exact_matches or token_matches
    return matches[0] if len(matches) == 1 else None


async def resolve_default_bank_account(
    session: AsyncSession,
) -> Optional[CuentaContable]:
    result = await session.execute(
        select(CuentaContable)
        .where(CuentaContable.activo.is_(True), CuentaContable.tipo == "banco")
        .order_by(CuentaContable.codigo.asc())
        .limit(1)
    )
    account = result.scalar_one_or_none()
    if account is not None:
        return account
    result = await session.execute(
        select(CuentaContable)
        .where(
            CuentaContable.activo.is_(True),
            or_(
                CuentaContable.nombre.ilike("%banco%"),
                CuentaContable.nombre.ilike("%transfer%"),
            ),
        )
        .order_by(CuentaContable.codigo.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _existing_poliza(
    session: AsyncSession,
    *,
    origen: str,
    numero_poliza: str,
) -> Optional[AccountingPoliza]:
    result = await session.execute(
        select(AccountingPoliza)
        .options(selectinload(AccountingPoliza.lines))
        .where(
            AccountingPoliza.origen == origen,
            AccountingPoliza.numero_poliza == numero_poliza,
        )
    )
    return result.scalar_one_or_none()


async def _create_poliza(
    session: AsyncSession,
    *,
    origen: str,
    numero_poliza: str,
    fecha: datetime,
    beneficiario_nombre: str,
    concepto: str,
    lines: list[dict[str, Any]],
) -> AccountingPoliza:
    poliza = AccountingPoliza(
        id=uuid4(),
        source_file="samchat:auto_deudores",
        source_sheet=origen,
        source_row_start=None,
        tipo_poliza="Diario",
        numero_poliza=numero_poliza,
        fecha_poliza=fecha,
        beneficiario_nombre=beneficiario_nombre,
        concepto=concepto,
        concepto_resumen=concepto,
        line_count_declared=len(lines),
        line_count_actual=len(lines),
        origen=origen,
    )
    session.add(poliza)
    await session.flush()
    for idx, line in enumerate(lines, start=1):
        session.add(
            AccountingPolizaLine(
                id=uuid4(),
                poliza_id=poliza.id,
                line_no=idx,
                cuenta_codigo=line["cuenta_codigo"],
                cuenta_contable_id=line.get("cuenta_contable_id"),
                concepto=line.get("concepto"),
                movimiento_no=str(idx),
                debe=_money_float(line.get("debe")),
                haber=_money_float(line.get("haber")),
                raw_row_json=line.get("raw_row_json") or {},
            )
        )
    return poliza


def _common_meta(
    *,
    source_key: str,
    empleado: Empleado,
    cuenta_gastos_id: Optional[UUID],
    documento_id: Optional[UUID],
) -> dict[str, Any]:
    return {
        "origin": source_key,
        "empleado_id": str(empleado.id),
        "empleado_nombre": empleado.nombre,
        "cuenta_gastos_id": str(cuenta_gastos_id) if cuenta_gastos_id else None,
        "documento_id": str(documento_id) if documento_id else None,
    }


async def ensure_debtor_payment_posting_for_document(
    session: AsyncSession,
    *,
    documento: Documento,
    empleado: Empleado,
    fecha_pago: date | datetime,
    require_employee_beneficiary: bool = True,
) -> DebtorPostingResult:
    cuenta_gastos_id = getattr(documento, "cuenta_gastos_id", None)
    if cuenta_gastos_id is None:
        return DebtorPostingResult(status="skipped", reason="not_cuenta_payment")
    if (
        require_employee_beneficiary
        and getattr(documento, "beneficiario_empleado_id", None) is None
    ):
        return DebtorPostingResult(status="skipped", reason="not_employee_cuenta_payment")
    numero_poliza = f"DEU-PAG-{str(documento.id)[:8]}"
    existing = await _existing_poliza(
        session, origen="deudores_anticipo", numero_poliza=numero_poliza
    )
    if existing is not None:
        return DebtorPostingResult(status="exists", poliza=existing)

    debtor = await resolve_employee_debtor_account(session, empleado)
    if debtor is None:
        return DebtorPostingResult(status="pending", reason="missing_employee_debtor_account")
    bank = await resolve_default_bank_account(session)
    if bank is None:
        return DebtorPostingResult(status="pending", reason="missing_bank_account")

    amount = _money(documento.monto_solicitado or documento.monto_total)
    if amount <= 0:
        return DebtorPostingResult(status="skipped", reason="invalid_amount")
    fecha_dt = (
        fecha_pago
        if isinstance(fecha_pago, datetime)
        else datetime.combine(fecha_pago, datetime.min.time())
    )
    meta = _common_meta(
        source_key="deudores_anticipo",
        empleado=empleado,
        cuenta_gastos_id=cuenta_gastos_id,
        documento_id=documento.id,
    )
    concepto = f"Pago a deudor empleado - {documento.numero_referencia or documento.id}"
    poliza = await _create_poliza(
        session,
        origen="deudores_anticipo",
        numero_poliza=numero_poliza,
        fecha=fecha_dt,
        beneficiario_nombre=empleado.nombre,
        concepto=concepto,
        lines=[
            {
                "cuenta_codigo": debtor.codigo,
                "cuenta_contable_id": debtor.id,
                "concepto": concepto,
                "debe": amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_deudor"},
            },
            {
                "cuenta_codigo": bank.codigo,
                "cuenta_contable_id": bank.id,
                "concepto": concepto,
                "debe": 0,
                "haber": amount,
                "raw_row_json": {**meta, "movement": "haber_banco"},
            },
        ],
    )
    return DebtorPostingResult(
        status="created", poliza=poliza, debtor_account=debtor, bank_account=bank
    )


async def ensure_debtor_comprobacion_posting_for_informe(
    session: AsyncSession,
    *,
    informe_documento: Documento,
) -> DebtorPostingResult:
    cuenta_gastos_id = getattr(informe_documento, "cuenta_gastos_id", None)
    if cuenta_gastos_id is None or informe_documento.tipo != "INFORME":
        return DebtorPostingResult(status="skipped", reason="not_informe_cuenta")
    numero_poliza = f"DEU-COMP-{str(cuenta_gastos_id)[:8]}"
    existing = await _existing_poliza(
        session, origen="deudores_comprobacion", numero_poliza=numero_poliza
    )
    if existing is not None:
        return DebtorPostingResult(status="exists", poliza=existing)

    cuenta = await session.get(CuentaDeGastos, cuenta_gastos_id)
    empleado = await session.get(Empleado, cuenta.empleado_id) if cuenta else None
    if empleado is None:
        return DebtorPostingResult(status="pending", reason="missing_employee")
    debtor = await resolve_employee_debtor_account(session, empleado)
    if debtor is None:
        return DebtorPostingResult(status="pending", reason="missing_employee_debtor_account")

    result = await session.execute(
        select(ExpenseReport)
        .options(
            selectinload(ExpenseReport.cuenta_contable),
            selectinload(ExpenseReport.cuenta_iva),
            selectinload(ExpenseReport.cfdi_report),
        )
        .where(
            ExpenseReport.cuenta_gastos_id == cuenta_gastos_id,
            ExpenseReport.estado_gasto != "cancelado",
            employee_paid_sql_condition(),
        )
        .order_by(ExpenseReport.fecha.asc(), ExpenseReport.created_at.asc())
    )
    expenses = list(result.scalars().all())
    if not expenses:
        return DebtorPostingResult(status="skipped", reason="no_employee_paid_expenses")

    lines: list[dict[str, Any]] = []
    total_credit = Decimal("0.00")
    meta_base = _common_meta(
        source_key="deudores_comprobacion",
        empleado=empleado,
        cuenta_gastos_id=cuenta_gastos_id,
        documento_id=informe_documento.id,
    )
    for expense in expenses:
        if getattr(expense, "cuenta_contable", None) is None:
            return DebtorPostingResult(
                status="pending",
                reason=f"expense_missing_account:{expense.id}",
                debtor_account=debtor,
            )
        preview = await build_expense_accounting_preview(session, expense)
        taxes = preview.get("taxes") or {}
        total = _money(getattr(expense, "gasto_cantidad", 0))
        iva_amount = _money(taxes.get("iva_trasladado"))
        iva_account = taxes.get("iva_account") or {}
        if iva_amount > 0 and not iva_account.get("cuenta_contable_id"):
            return DebtorPostingResult(
                status="pending",
                reason=f"expense_missing_iva_account:{expense.id}",
                debtor_account=debtor,
            )
        expense_amount = total - iva_amount if iva_amount > 0 else total
        if expense_amount > 0:
            lines.append(
                {
                    "cuenta_codigo": expense.cuenta_contable.codigo,
                    "cuenta_contable_id": expense.cuenta_contable.id,
                    "concepto": expense.concepto or "Comprobación de gastos",
                    "debe": expense_amount,
                    "haber": 0,
                    "raw_row_json": {
                        **meta_base,
                        "movement": "debe_gasto",
                        "expense_id": str(expense.id),
                    },
                }
            )
        if iva_amount > 0:
            lines.append(
                {
                    "cuenta_codigo": iva_account["codigo"],
                    "cuenta_contable_id": UUID(str(iva_account["cuenta_contable_id"])),
                    "concepto": f"IVA acreditable - {expense.concepto or expense.id}",
                    "debe": iva_amount,
                    "haber": 0,
                    "raw_row_json": {
                        **meta_base,
                        "movement": "debe_iva",
                        "expense_id": str(expense.id),
                    },
                }
            )
        total_credit += total
    if total_credit <= 0:
        return DebtorPostingResult(status="skipped", reason="invalid_total")

    concepto = f"Comprobación de gastos - I-{getattr(cuenta, 'referencia_base', cuenta_gastos_id)}"
    lines.append(
        {
            "cuenta_codigo": debtor.codigo,
            "cuenta_contable_id": debtor.id,
            "concepto": concepto,
            "debe": 0,
            "haber": total_credit,
            "raw_row_json": {**meta_base, "movement": "haber_deudor"},
        }
    )
    poliza = await _create_poliza(
        session,
        origen="deudores_comprobacion",
        numero_poliza=numero_poliza,
        fecha=informe_documento.aprobado_en or datetime.utcnow(),
        beneficiario_nombre=empleado.nombre,
        concepto=concepto,
        lines=lines,
    )
    return DebtorPostingResult(status="created", poliza=poliza, debtor_account=debtor)


async def ensure_debtor_settlement_posting(
    session: AsyncSession,
    *,
    reembolso: Reembolso,
    cuenta: CuentaDeGastos,
) -> DebtorPostingResult:
    if (reembolso.estado or "") == "cancelado":
        return DebtorPostingResult(status="skipped", reason="cancelled")
    numero_poliza = f"DEU-LIQ-{str(reembolso.id)[:8]}"
    existing = await _existing_poliza(
        session, origen="deudores_devolucion", numero_poliza=numero_poliza
    )
    if existing is not None:
        return DebtorPostingResult(status="exists", poliza=existing)
    empleado = await session.get(Empleado, cuenta.empleado_id)
    if empleado is None:
        return DebtorPostingResult(status="pending", reason="missing_employee")
    debtor = await resolve_employee_debtor_account(session, empleado)
    if debtor is None:
        return DebtorPostingResult(status="pending", reason="missing_employee_debtor_account")
    bank = await resolve_default_bank_account(session)
    if bank is None:
        return DebtorPostingResult(status="pending", reason="missing_bank_account")
    amount = _money(reembolso.monto)
    if amount <= 0:
        return DebtorPostingResult(status="skipped", reason="invalid_amount")
    fecha_dt = reembolso.fecha_pago or reembolso.creado_en or datetime.utcnow()
    meta = _common_meta(
        source_key="deudores_devolucion",
        empleado=empleado,
        cuenta_gastos_id=cuenta.id,
        documento_id=reembolso.documento_id,
    )
    meta["reembolso_id"] = str(reembolso.id)
    tipo = (reembolso.tipo or "reembolso").strip().lower()
    concepto = f"{'Devolución' if tipo == 'devolucion' else 'Reembolso'} de informe - I-{cuenta.referencia_base}"
    if tipo == "devolucion":
        lines = [
            {
                "cuenta_codigo": bank.codigo,
                "cuenta_contable_id": bank.id,
                "concepto": concepto,
                "debe": amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_banco"},
            },
            {
                "cuenta_codigo": debtor.codigo,
                "cuenta_contable_id": debtor.id,
                "concepto": concepto,
                "debe": 0,
                "haber": amount,
                "raw_row_json": {**meta, "movement": "haber_deudor"},
            },
        ]
    else:
        lines = [
            {
                "cuenta_codigo": debtor.codigo,
                "cuenta_contable_id": debtor.id,
                "concepto": concepto,
                "debe": amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_deudor"},
            },
            {
                "cuenta_codigo": bank.codigo,
                "cuenta_contable_id": bank.id,
                "concepto": concepto,
                "debe": 0,
                "haber": amount,
                "raw_row_json": {**meta, "movement": "haber_banco"},
            },
        ]
    poliza = await _create_poliza(
        session,
        origen="deudores_devolucion",
        numero_poliza=numero_poliza,
        fecha=fecha_dt,
        beneficiario_nombre=empleado.nombre,
        concepto=concepto,
        lines=lines,
    )
    return DebtorPostingResult(
        status="created", poliza=poliza, debtor_account=debtor, bank_account=bank
    )


async def build_cuenta_debtor_auxiliary(
    session: AsyncSession,
    *,
    cuenta_id: UUID,
) -> dict[str, Any]:
    cuenta = await session.get(CuentaDeGastos, cuenta_id)
    empleado = await session.get(Empleado, cuenta.empleado_id) if cuenta else None
    debtor = (
        await resolve_employee_debtor_account(session, empleado)
        if empleado is not None
        else None
    )
    line_conditions = [
        AccountingPoliza.origen.in_(DEBTOR_ORIGINS),
        AccountingPolizaLine.raw_row_json.contains({"cuenta_gastos_id": str(cuenta_id)}),
    ]
    lines = (
        await session.execute(
            select(AccountingPolizaLine)
            .join(AccountingPoliza, AccountingPoliza.id == AccountingPolizaLine.poliza_id)
            .options(selectinload(AccountingPolizaLine.poliza))
            .where(and_(*line_conditions))
            .order_by(
                AccountingPoliza.fecha_poliza.asc(),
                AccountingPoliza.numero_poliza.asc(),
                AccountingPolizaLine.line_no.asc(),
            )
        )
    ).scalars().all()
    debe = sum(float(line.debe or 0) for line in lines if line.cuenta_codigo.startswith(DEBTOR_ACCOUNT_PREFIX))
    haber = sum(float(line.haber or 0) for line in lines if line.cuenta_codigo.startswith(DEBTOR_ACCOUNT_PREFIX))
    saldo = round(debe - haber, 2)
    status = "saldado" if abs(saldo) < 0.01 and lines else "pendiente"
    if debtor is None:
        status = "sin_subcuenta"
    elif lines and abs(saldo) >= 0.01:
        status = "diferencia_contable"
    return {
        "cuenta": cuenta,
        "empleado": empleado,
        "debtor_account": debtor,
        "debtor_account_label": _account_label(debtor),
        "lines": lines,
        "debe": round(debe, 2),
        "haber": round(haber, 2),
        "saldo": saldo,
        "status": status,
    }


async def build_debtors_admin_snapshot(
    session: AsyncSession,
    *,
    year: int,
    month: int,
    q: str = "",
) -> dict[str, Any]:
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    conditions = [
        AccountingPoliza.origen.in_(DEBTOR_ORIGINS),
        AccountingPoliza.fecha_poliza >= start,
        AccountingPoliza.fecha_poliza < end,
    ]
    selected_q = (q or "").strip()
    if selected_q:
        token = f"%{selected_q}%"
        conditions.append(
            or_(
                AccountingPoliza.beneficiario_nombre.ilike(token),
                AccountingPoliza.numero_poliza.ilike(token),
                AccountingPolizaLine.cuenta_codigo.ilike(token),
                AccountingPolizaLine.concepto.ilike(token),
            )
        )
    lines = (
        await session.execute(
            select(AccountingPolizaLine)
            .join(AccountingPoliza, AccountingPoliza.id == AccountingPolizaLine.poliza_id)
            .options(selectinload(AccountingPolizaLine.poliza))
            .where(and_(*conditions))
            .order_by(
                AccountingPoliza.fecha_poliza.desc(),
                AccountingPoliza.numero_poliza.asc(),
                AccountingPolizaLine.line_no.asc(),
            )
        )
    ).scalars().all()

    summary_by_employee: dict[str, dict[str, Any]] = {}
    for line in lines:
        raw = line.raw_row_json or {}
        employee_id = str(raw.get("empleado_id") or "")
        employee_name = str(raw.get("empleado_nombre") or line.poliza.beneficiario_nombre or "Sin empleado")
        bucket = summary_by_employee.setdefault(
            employee_id or employee_name,
            {
                "empleado_id": employee_id,
                "empleado_nombre": employee_name,
                "cuenta_codigo": "",
                "cuenta_nombre": "",
                "debe": 0.0,
                "haber": 0.0,
                "saldo": 0.0,
                "movimientos": 0,
            },
        )
        if str(line.cuenta_codigo or "").startswith(DEBTOR_ACCOUNT_PREFIX):
            bucket["cuenta_codigo"] = line.cuenta_codigo
            bucket["cuenta_nombre"] = ""
            bucket["debe"] += float(line.debe or 0)
            bucket["haber"] += float(line.haber or 0)
            bucket["movimientos"] += 1
            bucket["saldo"] = round(bucket["debe"] - bucket["haber"], 2)

    missing = await list_employees_missing_debtor_account(session)
    if selected_q:
        normalized = _normalize_text(selected_q)
        missing = [
            emp for emp in missing if normalized in _normalize_text(emp.nombre)
        ]
    return {
        "lines": lines,
        "summary": sorted(
            summary_by_employee.values(),
            key=lambda item: (item["empleado_nombre"], item["cuenta_codigo"]),
        ),
        "missing_employees": missing,
        "period": {"year": year, "month": month},
    }


async def list_employees_missing_debtor_account(
    session: AsyncSession,
) -> list[Empleado]:
    employee_ids = set(
        row[0]
        for row in (
            await session.execute(
                select(Documento.empleado_id)
                .where(
                    Documento.cuenta_gastos_id.isnot(None),
                    Documento.tipo == "SOLICITUD",
                    Documento.estado.in_(["aprobado", "pagado"]),
                )
                .distinct()
            )
        ).all()
        if row[0]
    )
    employee_ids.update(
        row[0]
        for row in (
            await session.execute(
                select(CuentaDeGastos.empleado_id)
                .where(CuentaDeGastos.estado.in_(["abierta", "cerrada"]))
                .distinct()
            )
        ).all()
        if row[0]
    )
    if not employee_ids:
        return []
    employees = (
        await session.execute(
            select(Empleado)
            .where(Empleado.id.in_(employee_ids), Empleado.activo.is_(True))
            .order_by(Empleado.nombre.asc())
        )
    ).scalars().all()
    missing = []
    for empleado in employees:
        if await resolve_employee_debtor_account(session, empleado) is None:
            missing.append(empleado)
    return missing


__all__ = [
    "DEBTOR_ACCOUNT_PREFIX",
    "DEBTOR_ACCOUNT_ROOT_CODE",
    "build_cuenta_debtor_auxiliary",
    "build_debtors_admin_snapshot",
    "ensure_debtor_comprobacion_posting_for_informe",
    "ensure_debtor_payment_posting_for_document",
    "ensure_debtor_settlement_posting",
    "list_employees_missing_debtor_account",
    "resolve_employee_debtor_account",
]
