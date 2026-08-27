"""Durable, fail-closed accounting postings for the corporate AMEX flow.

This module owns only AMEX events.  It intentionally does not commit: the
workflow transition and its accounting entry must share the caller's
transaction.  Every public function is idempotent by event key and returns a
``pending`` result instead of guessing an account or silently producing a
partial journal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    AccountingPoliza,
    AmexCardAccount,
    CuentaContable,
    Documento,
    ExpenseReport,
)
from .amex_cfdi_matching_service import is_pase_expense
from .amex_expense_service import company_amex_sql_condition
from .employee_debtor_accounting_service import _create_poliza, _existing_poliza
from .expense_accounting_service import build_expense_accounting_preview


MONEY = Decimal("0.01")
AMEX_REPORT_DEBTOR_CODE = "1170-002-004"
SANTANDER_BANK_CODE = "1120-001-001"
ALLOWED_AMEX_LIABILITY_CODES = frozenset(
    {
        "2120-002-062",
        "2120-002-063",
        "2120-002-064",
        "2120-002-065",
        "2120-002-066",
        "2120-002-067",
        "2120-002-100",
    }
)
AMEX_PAYMENT_CARD_MARKER = "SAMCHAT_AMEX_CARD_ACCOUNT_ID"
_CARD_MARKER_RE = re.compile(
    rf"(?:^|\n){AMEX_PAYMENT_CARD_MARKER}=([0-9a-fA-F-]{{36}})(?:\n|$)"
)


@dataclass(frozen=True)
class AmexPostingResult:
    status: str
    reason: Optional[str] = None
    poliza: Optional[AccountingPoliza] = None


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _uuid(value: Any) -> Optional[UUID]:
    if value in (None, ""):
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


def amex_payment_card_marker(card_account_id: UUID | str) -> str:
    """Return the durable card binding embedded in an AMEX payment request."""

    card_id = _uuid(card_account_id)
    if card_id is None:
        raise ValueError("invalid AMEX card account id")
    return f"{AMEX_PAYMENT_CARD_MARKER}={card_id}"


def parse_amex_payment_card_id(documento: Documento) -> Optional[UUID]:
    """Read the structured card binding; free-text card labels are not trusted."""

    if str(getattr(documento, "metodo_pago", "") or "").strip().upper() != "AMEX":
        return None
    match = _CARD_MARKER_RE.search(str(getattr(documento, "notas", "") or ""))
    return _uuid(match.group(1)) if match else None


def posting_is_balanced(lines: list[dict[str, Any]]) -> bool:
    debit = sum((_money(line.get("debe")) for line in lines), Decimal("0.00"))
    credit = sum((_money(line.get("haber")) for line in lines), Decimal("0.00"))
    return debit == credit and debit > Decimal("0.00")


async def _lock_event(session: AsyncSession, event_key: str) -> None:
    # Production is PostgreSQL.  The transaction lock closes the race between
    # the idempotency lookup and insertion without requiring a schema change.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:event_key))"),
        {"event_key": event_key},
    )


async def _active_account(
    session: AsyncSession, code: str
) -> Optional[CuentaContable]:
    result = await session.execute(
        select(CuentaContable).where(
            CuentaContable.codigo == code,
            CuentaContable.activo.is_(True),
        )
    )
    return result.scalar_one_or_none()


def _preview_account(value: Any) -> tuple[Optional[str], Optional[UUID]]:
    if not isinstance(value, dict):
        return None, None
    code = str(value.get("codigo") or "").strip() or None
    return code, _uuid(value.get("cuenta_contable_id"))


def _debit_line(
    *,
    code: str,
    account_id: UUID,
    amount: Decimal,
    concept: str,
    meta: dict[str, Any],
    movement: str,
) -> dict[str, Any]:
    return {
        "cuenta_codigo": code,
        "cuenta_contable_id": account_id,
        "concepto": concept,
        "debe": amount,
        "haber": 0,
        "raw_row_json": {**meta, "movement": movement},
    }


def _credit_line(
    *,
    code: str,
    account_id: UUID,
    amount: Decimal,
    concept: str,
    meta: dict[str, Any],
    movement: str,
) -> dict[str, Any]:
    return {
        "cuenta_codigo": code,
        "cuenta_contable_id": account_id,
        "concepto": concept,
        "debe": 0,
        "haber": amount,
        "raw_row_json": {**meta, "movement": movement},
    }


async def _single_expense_fiscal_lines(
    session: AsyncSession,
    expense: ExpenseReport,
    *,
    meta: dict[str, Any],
) -> tuple[Optional[list[dict[str, Any]]], Decimal, Optional[str]]:
    account = getattr(expense, "cuenta_contable", None)
    if account is None and getattr(expense, "cuenta_contable_id", None):
        account = await session.get(CuentaContable, expense.cuenta_contable_id)
    if account is None or not getattr(account, "activo", False):
        return None, Decimal("0.00"), f"missing_expense_account:{expense.id}"

    preview = await build_expense_accounting_preview(session, expense)
    taxes = preview.get("taxes") or {}
    base = _money(taxes.get("base_gasto"))
    net = _money(taxes.get("neto_contrapartida"))
    if net <= 0:
        return None, Decimal("0.00"), f"invalid_expense_total:{expense.id}"

    common = {**meta, "expense_id": str(expense.id)}
    concept = str(expense.concepto or "Gasto AMEX")
    lines: list[dict[str, Any]] = []
    if base > 0:
        lines.append(
            _debit_line(
                code=account.codigo,
                account_id=account.id,
                amount=base,
                concept=concept,
                meta=common,
                movement="debe_gasto",
            )
        )

    iva = _money(taxes.get("iva_trasladado"))
    if iva > 0:
        code, account_id = _preview_account(taxes.get("iva_account"))
        if not code or account_id is None:
            return None, Decimal("0.00"), f"missing_iva_account:{expense.id}"
        lines.append(
            _debit_line(
                code=code,
                account_id=account_id,
                amount=iva,
                concept=f"IVA - {concept}",
                meta=common,
                movement="debe_iva",
            )
        )

    for item in taxes.get("impuestos_locales") or []:
        amount = _money(item.get("importe"))
        if amount <= 0:
            continue
        code, account_id = _preview_account(item.get("account"))
        if not code or account_id is None:
            return None, Decimal("0.00"), f"missing_local_tax_account:{expense.id}"
        lines.append(
            _debit_line(
                code=code,
                account_id=account_id,
                amount=amount,
                concept=str(item.get("label") or "Impuesto local"),
                meta=common,
                movement="debe_impuesto_local",
            )
        )

    for item in taxes.get("gastos_no_deducibles") or []:
        amount = _money(item.get("importe"))
        if amount <= 0:
            continue
        code, account_id = _preview_account(item.get("account"))
        if not code or account_id is None:
            return None, Decimal("0.00"), f"missing_non_deductible_account:{expense.id}"
        lines.append(
            _debit_line(
                code=code,
                account_id=account_id,
                amount=amount,
                concept=str(item.get("label") or "No deducible"),
                meta=common,
                movement="debe_no_deducible",
            )
        )

    for item in taxes.get("retenciones") or []:
        amount = _money(item.get("importe"))
        if amount <= 0:
            continue
        code, account_id = _preview_account(item.get("account"))
        if not code or account_id is None:
            return None, Decimal("0.00"), f"missing_retention_account:{expense.id}"
        lines.append(
            _credit_line(
                code=code,
                account_id=account_id,
                amount=amount,
                concept=str(item.get("label") or "Retencion"),
                meta=common,
                movement="haber_retencion",
            )
        )

    return lines, net, None


async def _pase_group_fiscal_lines(
    session: AsyncSession,
    expenses: list[ExpenseReport],
    *,
    meta: dict[str, Any],
) -> tuple[Optional[list[dict[str, Any]]], Decimal, Optional[str]]:
    """Post one global PASE CFDI once, never once per individual toll."""

    first = expenses[0]
    cfdi = getattr(first, "cfdi_report", None)
    if cfdi is None or any(
        expense.cfdi_report_id != first.cfdi_report_id for expense in expenses
    ):
        return None, Decimal("0.00"), "invalid_pase_cfdi_group"
    if not all(is_pase_expense(expense) for expense in expenses):
        return None, Decimal("0.00"), f"shared_non_pase_cfdi:{cfdi.id}"
    account_ids = {expense.cuenta_contable_id for expense in expenses}
    if None in account_ids or len(account_ids) != 1:
        return None, Decimal("0.00"), f"pase_mixed_expense_accounts:{cfdi.id}"
    if any(
        _money(getattr(expense, "propina_no_deducible", 0)) > 0
        or _money(getattr(expense, "hospedaje_impuesto_monto", 0)) > 0
        for expense in expenses
    ):
        return None, Decimal("0.00"), f"pase_unexpected_tax_component:{cfdi.id}"

    charge_total = sum(
        (_money(expense.gasto_cantidad) for expense in expenses), Decimal("0.00")
    )
    cfdi_total = _money(getattr(cfdi, "total", 0))
    if abs(charge_total - cfdi_total) > Decimal("0.02"):
        return None, Decimal("0.00"), f"pase_cfdi_total_mismatch:{cfdi.id}"

    # The preview is used only to resolve governed tax accounts.  Amounts are
    # recomputed from the single global CFDI so they cannot be multiplied by N.
    preview = await build_expense_accounting_preview(session, first)
    taxes = preview.get("taxes") or {}
    iva = _money(taxes.get("iva_trasladado"))
    retentions = _money(taxes.get("retenciones_total"))
    base = cfdi_total - iva + retentions
    if base < 0:
        return None, Decimal("0.00"), f"pase_negative_base:{cfdi.id}"

    account = await session.get(CuentaContable, next(iter(account_ids)))
    if account is None or not getattr(account, "activo", False):
        return None, Decimal("0.00"), f"pase_missing_expense_account:{cfdi.id}"
    common = {
        **meta,
        "cfdi_report_id": str(cfdi.id),
        "expense_ids": [str(expense.id) for expense in expenses],
        "shared_pase_cfdi": True,
    }
    rows: list[dict[str, Any]] = []
    if base > 0:
        rows.append(
            _debit_line(
                code=account.codigo,
                account_id=account.id,
                amount=base,
                concept="PASE/TAG mensual",
                meta=common,
                movement="debe_gasto_pase",
            )
        )
    if iva > 0:
        code, account_id = _preview_account(taxes.get("iva_account"))
        if not code or account_id is None:
            return None, Decimal("0.00"), f"pase_missing_iva_account:{cfdi.id}"
        rows.append(
            _debit_line(
                code=code,
                account_id=account_id,
                amount=iva,
                concept="IVA acreditable PASE/TAG mensual",
                meta=common,
                movement="debe_iva_pase",
            )
        )
    for item in taxes.get("retenciones") or []:
        amount = _money(item.get("importe"))
        if amount <= 0:
            continue
        code, account_id = _preview_account(item.get("account"))
        if not code or account_id is None:
            return None, Decimal("0.00"), f"pase_missing_retention_account:{cfdi.id}"
        rows.append(
            _credit_line(
                code=code,
                account_id=account_id,
                amount=amount,
                concept=str(item.get("label") or "Retencion PASE/TAG"),
                meta=common,
                movement="haber_retencion_pase",
            )
        )
    return rows, cfdi_total, None


async def _fiscal_lines_for_expenses(
    session: AsyncSession,
    expenses: list[ExpenseReport],
    *,
    meta: dict[str, Any],
) -> tuple[Optional[list[dict[str, Any]]], Decimal, Optional[str]]:
    by_cfdi: dict[Optional[UUID], list[ExpenseReport]] = {}
    for expense in expenses:
        by_cfdi.setdefault(expense.cfdi_report_id, []).append(expense)

    rows: list[dict[str, Any]] = []
    net_total = Decimal("0.00")
    for cfdi_id, group in by_cfdi.items():
        if cfdi_id is not None and len(group) > 1:
            group_rows, group_net, reason = await _pase_group_fiscal_lines(
                session, group, meta=meta
            )
            if reason:
                return None, Decimal("0.00"), reason
            rows.extend(group_rows or [])
            net_total += group_net
            continue
        for expense in group:
            expense_rows, expense_net, reason = await _single_expense_fiscal_lines(
                session, expense, meta=meta
            )
            if reason:
                return None, Decimal("0.00"), reason
            rows.extend(expense_rows or [])
            net_total += expense_net
    return rows, net_total, None


async def _existing_after_lock(
    session: AsyncSession, *, origin: str, number: str, event_key: str
) -> Optional[AccountingPoliza]:
    await _lock_event(session, event_key)
    return await _existing_poliza(
        session, origen=origin, numero_poliza=number
    )


async def ensure_amex_report_approval_posting(
    session: AsyncSession,
    *,
    informe_documento: Documento,
) -> AmexPostingResult:
    """Rule 9: approved AMEX report -> fiscal debits / 1170-002-004."""

    if informe_documento.tipo != "INFORME" or not informe_documento.cuenta_gastos_id:
        return AmexPostingResult(status="skipped", reason="not_expense_report")
    result = await session.execute(
        select(ExpenseReport)
        .options(
            selectinload(ExpenseReport.cuenta_contable),
            selectinload(ExpenseReport.cuenta_iva),
            selectinload(ExpenseReport.cfdi_report),
        )
        .where(
            ExpenseReport.cuenta_gastos_id == informe_documento.cuenta_gastos_id,
            ExpenseReport.estado_gasto != "cancelado",
            company_amex_sql_condition(),
            # Imported statement charges belong exclusively to rule 10.
            ExpenseReport.origen != "amex_batch",
        )
        .order_by(ExpenseReport.fecha.asc(), ExpenseReport.created_at.asc())
    )
    expenses = list(result.scalars().all())
    if not expenses:
        return AmexPostingResult(status="skipped", reason="no_report_amex_expenses")

    origin = "amex_informe_aprobado"
    number = f"AMX-REP-{str(informe_documento.id)[:8]}"
    existing = await _existing_after_lock(
        session,
        origin=origin,
        number=number,
        event_key=f"{origin}:{informe_documento.id}",
    )
    if existing is not None:
        return AmexPostingResult(status="exists", poliza=existing)

    debtor = await _active_account(session, AMEX_REPORT_DEBTOR_CODE)
    if debtor is None:
        return AmexPostingResult(status="pending", reason="missing_amex_partner_debtor")
    meta = {
        "origin": origin,
        "documento_id": str(informe_documento.id),
        "cuenta_gastos_id": str(informe_documento.cuenta_gastos_id),
    }
    rows, net, reason = await _fiscal_lines_for_expenses(session, expenses, meta=meta)
    if reason:
        return AmexPostingResult(status="pending", reason=reason)
    if net <= 0:
        return AmexPostingResult(status="pending", reason="invalid_amex_report_total")
    concept = f"Informe AMEX aprobado - {informe_documento.numero_referencia}"
    rows = list(rows or [])
    rows.append(
        _credit_line(
            code=debtor.codigo,
            account_id=debtor.id,
            amount=net,
            concept=concept,
            meta=meta,
            movement="haber_deudor_socio_amex",
        )
    )
    if not posting_is_balanced(rows):
        return AmexPostingResult(status="pending", reason="unbalanced_amex_report")
    poliza = await _create_poliza(
        session,
        origen=origin,
        numero_poliza=number,
        fecha=informe_documento.aprobado_en or datetime.utcnow(),
        beneficiario_nombre=debtor.nombre,
        concepto=concept,
        lines=rows,
    )
    return AmexPostingResult(status="created", poliza=poliza)


async def ensure_amex_reconciliation_posting(
    session: AsyncSession,
    *,
    year: int,
    month: int,
    card_account: AmexCardAccount,
) -> AmexPostingResult:
    """Rule 10: validated AMEX reconciliation -> fiscal debits / card liability."""

    if not 1 <= month <= 12 or not getattr(card_account, "active", False):
        return AmexPostingResult(status="pending", reason="invalid_card_period")
    liability = getattr(card_account, "liability_cuenta_contable", None)
    if liability is None:
        liability = await session.get(
            CuentaContable, card_account.liability_cuenta_contable_id
        )
    if (
        liability is None
        or not getattr(liability, "activo", False)
        or liability.codigo not in ALLOWED_AMEX_LIABILITY_CODES
    ):
        return AmexPostingResult(status="pending", reason="invalid_amex_liability")

    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    result = await session.execute(
        select(ExpenseReport)
        .options(
            selectinload(ExpenseReport.cuenta_contable),
            selectinload(ExpenseReport.cuenta_iva),
            selectinload(ExpenseReport.cfdi_report),
        )
        .where(
            and_(
                ExpenseReport.origen == "amex_batch",
                ExpenseReport.estado_gasto == "activo",
                ExpenseReport.ultimos_4_digitos == card_account.last4,
                ExpenseReport.fecha >= start,
                ExpenseReport.fecha < end,
            )
        )
        .order_by(ExpenseReport.fecha.asc(), ExpenseReport.created_at.asc())
    )
    expenses = list(result.scalars().all())
    if not expenses:
        return AmexPostingResult(status="pending", reason="no_amex_charges")
    if any(not expense.cfdi_report_id for expense in expenses):
        return AmexPostingResult(status="pending", reason="unlinked_amex_charges")

    origin = "amex_conciliacion"
    number = f"AMX-REC-{year:04d}{month:02d}-{card_account.last4}"
    existing = await _existing_after_lock(
        session,
        origin=origin,
        number=number,
        event_key=f"{origin}:{year:04d}-{month:02d}:{card_account.id}",
    )
    if existing is not None:
        return AmexPostingResult(status="exists", poliza=existing)

    meta = {
        "origin": origin,
        "year": year,
        "month": month,
        "amex_card_account_id": str(card_account.id),
        "card_last4": card_account.last4,
    }
    rows, net, reason = await _fiscal_lines_for_expenses(session, expenses, meta=meta)
    if reason:
        return AmexPostingResult(status="pending", reason=reason)
    if net <= 0:
        return AmexPostingResult(status="pending", reason="invalid_reconciliation_total")
    concept = f"Conciliacion AMEX {year:04d}-{month:02d} ****{card_account.last4}"
    rows = list(rows or [])
    rows.append(
        _credit_line(
            code=liability.codigo,
            account_id=liability.id,
            amount=net,
            concept=concept,
            meta=meta,
            movement="haber_pasivo_amex",
        )
    )
    if not posting_is_balanced(rows):
        return AmexPostingResult(status="pending", reason="unbalanced_reconciliation")
    poliza = await _create_poliza(
        session,
        origen=origin,
        numero_poliza=number,
        fecha=datetime.utcnow(),
        beneficiario_nombre=card_account.cardholder_name or card_account.card_label,
        concepto=concept,
        lines=rows,
    )
    return AmexPostingResult(status="created", poliza=poliza)


async def ensure_amex_payment_posting(
    session: AsyncSession,
    *,
    documento: Documento,
    payment_date: date | datetime,
) -> AmexPostingResult:
    """Rule 11: confirmed AMEX payment -> card liability / Santander."""

    card_id = parse_amex_payment_card_id(documento)
    if card_id is None:
        return AmexPostingResult(status="pending", reason="missing_amex_card_binding")
    result = await session.execute(
        select(AmexCardAccount)
        .options(selectinload(AmexCardAccount.liability_cuenta_contable))
        .where(AmexCardAccount.id == card_id, AmexCardAccount.active.is_(True))
    )
    card = result.scalar_one_or_none()
    if card is None:
        return AmexPostingResult(status="pending", reason="inactive_amex_card")
    liability = card.liability_cuenta_contable
    if (
        liability is None
        or not getattr(liability, "activo", False)
        or liability.codigo not in ALLOWED_AMEX_LIABILITY_CODES
    ):
        return AmexPostingResult(status="pending", reason="invalid_amex_liability")
    bank = await _active_account(session, SANTANDER_BANK_CODE)
    if bank is None:
        return AmexPostingResult(status="pending", reason="missing_santander_bank")
    amount = _money(documento.monto_solicitado or documento.monto_total)
    if amount <= 0:
        return AmexPostingResult(status="pending", reason="invalid_payment_amount")

    origin = "amex_pago"
    number = f"AMX-PAY-{str(documento.id)[:8]}"
    existing = await _existing_after_lock(
        session,
        origin=origin,
        number=number,
        event_key=f"{origin}:{documento.id}",
    )
    if existing is not None:
        return AmexPostingResult(status="exists", poliza=existing)
    when = (
        payment_date
        if isinstance(payment_date, datetime)
        else datetime.combine(payment_date, datetime.min.time())
    )
    concept = f"Pago AMEX ****{card.last4} - {documento.numero_referencia}"
    meta = {
        "origin": origin,
        "documento_id": str(documento.id),
        "amex_card_account_id": str(card.id),
        "card_last4": card.last4,
    }
    rows = [
        _debit_line(
            code=liability.codigo,
            account_id=liability.id,
            amount=amount,
            concept=concept,
            meta=meta,
            movement="debe_pasivo_amex",
        ),
        _credit_line(
            code=bank.codigo,
            account_id=bank.id,
            amount=amount,
            concept=concept,
            meta=meta,
            movement="haber_banco_santander",
        ),
    ]
    poliza = await _create_poliza(
        session,
        origen=origin,
        numero_poliza=number,
        fecha=when,
        beneficiario_nombre=card.cardholder_name or card.card_label,
        concepto=concept,
        lines=rows,
    )
    return AmexPostingResult(status="created", poliza=poliza)


__all__ = [
    "ALLOWED_AMEX_LIABILITY_CODES",
    "AMEX_PAYMENT_CARD_MARKER",
    "AMEX_REPORT_DEBTOR_CODE",
    "SANTANDER_BANK_CODE",
    "AmexPostingResult",
    "amex_payment_card_marker",
    "ensure_amex_payment_posting",
    "ensure_amex_reconciliation_posting",
    "ensure_amex_report_approval_posting",
    "parse_amex_payment_card_id",
    "posting_is_balanced",
]
