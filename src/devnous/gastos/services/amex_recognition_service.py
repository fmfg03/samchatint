"""Explicit AMEX consumption identity and transactional recognition evidence.

An imported charge is the stable anchor. Similar dates/amounts never create
an association, and a CFDI UUID is deliberately not an identity key.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    AccountingClosePeriod,
    AccountingPoliza,
    AmexCardAccount,
    AmexRecognitionActivation,
    AmexRecognitionConsumption,
    AmexRecognitionRepresentation,
    CFDIReport,
    CuentaContable,
    Empleado,
    ExpenseReport,
)


@dataclass(frozen=True)
class AmexRecognitionResult:
    status: str
    reason: str | None = None
    consumption: Any = None
    poliza: AccountingPoliza | None = None


async def lock_recognition(session: AsyncSession) -> None:
    """Serialize identity edits and recognition across administrative origins."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "samchat:amex:shared-recognition"},
    )


async def load_expenses(session: AsyncSession, ids: list[UUID]) -> list[ExpenseReport]:
    accounts_result = await session.execute(
        select(CuentaContable)
        .order_by(CuentaContable.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    accounts_result.scalars().all()
    result = await session.execute(
        select(ExpenseReport)
        .options(
            selectinload(ExpenseReport.cuenta_contable),
            selectinload(ExpenseReport.cuenta_iva),
            selectinload(ExpenseReport.cfdi_report),
        )
        .where(ExpenseReport.id.in_(ids))
        .order_by(ExpenseReport.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    expenses = list(result.scalars().all())
    cfdi_ids = {e.cfdi_report_id for e in expenses if e.cfdi_report_id}
    if cfdi_ids:
        cfdi_result = await session.execute(
            select(CFDIReport)
            .where(CFDIReport.id.in_(cfdi_ids))
            .order_by(CFDIReport.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        cfdi_result.scalars().all()
    return expenses


def review_source_key(
    expense: Any, *, accounts: list[dict[str, Any]] | None = None
) -> str:
    """Fingerprint only the material accounting source reviewed by Finance."""
    expense_fields = (
        "id",
        "cuenta_gastos_id",
        "gasto_cantidad",
        "fecha",
        "currency",
        "estado_gasto",
        "pagado_con_amex_empresa",
        "origen",
        "ultimos_4_digitos",
        "cuenta_contable_id",
        "contra_cuenta_contable_id",
        "cuenta_iva_id",
        "cfdi_report_id",
        "cfdi_uuid_manual",
        "nova_request_id",
        "iva",
        "hospedaje_entidad_fiscal",
        "hospedaje_tasa_impuesto",
        "hospedaje_impuesto_monto",
        "hospedaje_impuesto_confirmado",
        "propina_no_deducible",
        "cfdi_compartido_confirmado",
        "retencion_cuentas_json",
        "concepto",
        "metodo_pago",
    )
    cfdi_fields = (
        "id",
        "cfdi_uuid",
        "fecha",
        "subtotal",
        "descuento",
        "total",
        "moneda",
        "tipo_cambio",
        "tipo_de_comprobante",
        "metodo_pago",
        "emisor_regimen_fiscal",
        "receptor_uso_cfdi",
        "total_impuestos_trasladados",
        "conceptos",
        "impuestos_detalle",
    )
    cfdi = getattr(expense, "cfdi_report", None)
    source = {
        "accounts": accounts or [],
        "expense": {key: getattr(expense, key, None) for key in expense_fields},
        "cfdi": (
            {key: getattr(cfdi, key, None) for key in cfdi_fields} if cfdi else None
        ),
    }
    return hashlib.sha256(
        json.dumps(source, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


async def accounting_review_source_key(
    session: AsyncSession,
    expense: Any,
    *,
    treatment: str,
    debtor_account_id: UUID | None,
) -> str:
    """Freeze the accounts relevant to this decision, including resolved taxes.

    Catalog rows are locked/refreshed by load_expenses. Partner decisions do
    not depend on a fiscal preview: they charge the full amount to the debtor.
    Unrelated catalog edits do not invalidate a completed Finance check.
    """
    account_ids = {
        value
        for value in (expense.cuenta_contable_id, expense.cuenta_iva_id)
        if value is not None
    }
    if treatment == "partner_receivable":
        if debtor_account_id is not None:
            account_ids.add(debtor_account_id)
    else:
        from .expense_accounting_service import build_expense_accounting_preview

        preview = await build_expense_accounting_preview(session, expense)
        taxes = preview.get("taxes") or {}
        candidates = [taxes.get("iva_account")]
        for category in ("retenciones", "impuestos_locales", "gastos_no_deducibles"):
            candidates.extend(row.get("account") for row in taxes.get(category) or [])
        for account in candidates:
            if account and account.get("cuenta_contable_id"):
                account_ids.add(UUID(str(account["cuenta_contable_id"])))
    accounts = []
    for account_id in sorted(account_ids, key=str):
        account = await session.get(
            CuentaContable, account_id, populate_existing=True, with_for_update=True
        )
        accounts.append(
            {
                "id": str(account_id),
                "code": str(account.codigo) if account else None,
                "active": bool(account.activo) if account else None,
            }
        )
    return review_source_key(expense, accounts=accounts)


async def activation_reason(
    session: AsyncSession, expenses: list[ExpenseReport]
) -> str | None:
    activation = await session.get(AmexRecognitionActivation, 1)
    if activation is None:
        return "amex_recognition_not_activated"
    cutoff = activation.activated_at
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    for expense in expenses:
        created: Any = expense.created_at
        if created is None:
            return "missing_amex_source_creation_date"
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < cutoff:
            return "historical_amex_representation_requires_review"
    return None


async def period_reason(session: AsyncSession, economic_date: date) -> str | None:
    result = await session.execute(
        select(AccountingClosePeriod)
        .where(
            AccountingClosePeriod.fiscal_year == economic_date.year,
            AccountingClosePeriod.fiscal_month == economic_date.month,
        )
        .with_for_update()
    )
    periods = list(result.scalars().all())
    if any(period.status != "open" for period in periods):
        return "closed_amex_accounting_period"
    return None


def signature(lines: list[dict[str, Any]]) -> list[dict[str, str]]:
    totals: dict[tuple[str, str], list[Decimal]] = {}
    for line in lines:
        key = (str(line.get("cuenta_contable_id")), line["cuenta_codigo"])
        amounts = totals.setdefault(key, [Decimal(0), Decimal(0)])
        amounts[0] += Decimal(str(line.get("debe") or 0))
        amounts[1] += Decimal(str(line.get("haber") or 0))
    return [
        {
            "account_id": key[0],
            "code": key[1],
            "debe": str(values[0].quantize(Decimal(".01"))),
            "haber": str(values[1].quantize(Decimal(".01"))),
        }
        for key, values in sorted(totals.items())
    ]


async def fiscal_lines(
    session: AsyncSession, expenses: list[ExpenseReport]
) -> tuple[list[dict[str, Any]] | None, Decimal, str | None]:
    """Allocate a shared invoice only after verifying full statement coverage.

    The existing fiscal constructor resolves accounts and allocates taxes
    only for explicitly confirmed shared invoices. Coverage is checked across
    imported charges, never across duplicated administrative representations.
    """
    from .amex_accounting_posting_service import _money, _single_expense_fiscal_lines

    rows = []
    total = Decimal("0.00")
    for expense in expenses:
        if not expense.cfdi_report_id or expense.cfdi_report is None:
            return None, Decimal(0), "unlinked_amex_charges"
        result = await session.execute(
            select(ExpenseReport)
            .options(
                selectinload(ExpenseReport.cfdi_report),
            )
            .where(
                ExpenseReport.origen == "amex_batch",
                ExpenseReport.estado_gasto != "cancelado",
                ExpenseReport.cfdi_report_id == expense.cfdi_report_id,
            )
            .order_by(ExpenseReport.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        coverage = list(result.scalars().all())
        invoice_total = _money(expense.cfdi_report.total)
        covered = sum(
            (
                _money(item.gasto_cantidad)
                - _money(getattr(item, "propina_no_deducible", 0))
                for item in coverage
            ),
            Decimal(0),
        )
        if invoice_total <= 0 or covered != invoice_total:
            return None, Decimal(0), "amex_cfdi_coverage_mismatch"
        constructed, net, reason = await _single_expense_fiscal_lines(
            session, expense, meta={"expense_id": str(expense.id)}
        )
        if reason:
            return None, Decimal(0), reason
        amount = _money(expense.gasto_cantidad)
        if amount <= 0:
            return None, Decimal(0), "invalid_amex_consumption_amount"
        if (
            len(coverage) > 1
            or amount - _money(getattr(expense, "propina_no_deducible", 0))
            != invoice_total
        ):
            if not getattr(expense, "cfdi_compartido_confirmado", False):
                return None, Decimal(0), "unconfirmed_shared_amex_cfdi"
        if net != amount:
            return None, Decimal(0), "amex_amount_fiscal_mismatch"
        rows.extend(constructed or [])
        total += net
    return rows, total, None


async def bind_amex_consumption(
    session: AsyncSession,
    *,
    imported_expense_id: UUID,
    report_expense_ids: list[UUID],
    actor_id: UUID,
) -> AmexRecognitionResult:
    """Bind explicitly selected representations; never post or commit here."""
    from .amex_accounting_posting_service import ALLOWED_AMEX_LIABILITY_CODES, _money

    await lock_recognition(session)
    actor = await session.get(Empleado, actor_id, populate_existing=True)
    if (
        actor is None
        or not actor.activo
        or actor.rol
        not in {
            "finanzas",
            "admin",
            "superadmin",
            "super_admin",
        }
    ):
        return AmexRecognitionResult("pending", "unauthorized_amex_accounting_actor")
    ids = [imported_expense_id, *report_expense_ids]
    if len(set(ids)) != len(ids):
        return AmexRecognitionResult("pending", "duplicate_amex_representation")
    expenses = await load_expenses(session, ids)
    if len(expenses) != len(ids):
        return AmexRecognitionResult("pending", "missing_amex_representation")
    imported = next(e for e in expenses if e.id == imported_expense_id)
    reports = [e for e in expenses if e.id != imported_expense_id]
    if imported.origen != "amex_batch" or any(
        e.origen == "amex_batch" for e in reports
    ):
        return AmexRecognitionResult("pending", "invalid_amex_representation_role")
    if any(e.estado_gasto == "cancelado" for e in expenses):
        return AmexRecognitionResult("pending", "cancelled_amex_representation")
    reason = await activation_reason(session, expenses)
    if reason:
        return AmexRecognitionResult("pending", reason)
    result = await session.execute(
        select(AmexCardAccount).where(
            AmexCardAccount.last4 == imported.ultimos_4_digitos,
            AmexCardAccount.active.is_(True),
        )
    )
    card = result.scalar_one_or_none()
    liability = (
        await session.get(CuentaContable, card.liability_cuenta_contable_id)
        if card
        else None
    )
    if (
        card is None
        or not liability
        or not liability.activo
        or liability.codigo not in ALLOWED_AMEX_LIABILITY_CODES
    ):
        return AmexRecognitionResult("pending", "invalid_amex_liability")
    amount = _money(imported.gasto_cantidad)
    if amount <= 0 or any(_money(e.gasto_cantidad) <= 0 for e in reports):
        return AmexRecognitionResult("pending", "invalid_amex_consumption_amount")
    currency = str(imported.currency or "").upper()
    if currency != "MXN":
        return AmexRecognitionResult("pending", "unsupported_amex_currency")
    if imported.fecha is None or any(e.fecha is None for e in reports):
        return AmexRecognitionResult("pending", "missing_amex_economic_date")
    day = imported.fecha.date()
    if any(
        e.ultimos_4_digitos != card.last4
        or str(e.currency).upper() != currency
        or e.fecha.date() != day
        for e in reports
    ):
        return AmexRecognitionResult("pending", "amex_card_currency_date_mismatch")
    if (
        reports
        and sum((_money(e.gasto_cantidad) for e in reports), Decimal(0)) != amount
    ):
        return AmexRecognitionResult("pending", "amex_representation_amount_mismatch")
    snapshot: list[dict[str, Any]] = (
        []
    )  # Fiscal/manual treatment is frozen only by the accounting cut.
    result = await session.execute(
        select(AmexRecognitionConsumption).where(
            AmexRecognitionConsumption.imported_expense_id == imported_expense_id
        )
    )
    consumption = result.scalar_one_or_none()
    result = await session.execute(
        select(AmexRecognitionRepresentation).where(
            AmexRecognitionRepresentation.expense_id.in_(ids)
        )
    )
    links = list(result.scalars().all())
    if any(
        consumption is None or link.consumption_id != consumption.id for link in links
    ):
        return AmexRecognitionResult("pending", "amex_representation_already_bound")
    if consumption and (
        consumption.liability_account_id != liability.id
        or consumption.liability_code != liability.codigo
    ):
        return AmexRecognitionResult("pending", "amex_recognition_snapshot_mismatch")
    # Rebinding cannot silently omit report partitions that were already selected.
    if consumption:
        result = await session.execute(
            select(AmexRecognitionRepresentation).where(
                AmexRecognitionRepresentation.consumption_id == consumption.id,
                AmexRecognitionRepresentation.role == "report",
            )
        )
        old_reports = {r.expense_id for r in result.scalars().all()}
        if old_reports and old_reports != set(report_expense_ids):
            return AmexRecognitionResult(
                "pending", "amex_representation_partition_mismatch"
            )
    async with session.begin_nested():
        if consumption is None:
            consumption = AmexRecognitionConsumption(
                id=uuid4(),
                imported_expense_id=imported.id,
                card_account_id=card.id,
                amount=amount,
                currency=currency,
                economic_date=day,
                liability_account_id=liability.id,
                liability_code=liability.codigo,
                classification_json=snapshot,
                actor_id=actor_id,
            )
            session.add(consumption)
            await session.flush()
        linked_ids = {r.expense_id for r in links}
        for expense in expenses:
            if expense.id not in linked_ids:
                session.add(
                    AmexRecognitionRepresentation(
                        id=uuid4(),
                        consumption_id=consumption.id,
                        expense_id=expense.id,
                        role="statement" if expense.id == imported.id else "report",
                    )
                )
        await session.flush()
    return AmexRecognitionResult("bound", consumption=consumption)


async def recognize_expenses(
    session: AsyncSession, expenses: list[ExpenseReport]
) -> AmexRecognitionResult:
    """Compatibility preparation hook: only the explicit cut may post."""
    return AmexRecognitionResult("prepared")
