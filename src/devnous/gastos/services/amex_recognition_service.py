"""Explicit AMEX consumption identity and transactional recognition evidence.

An imported charge is the stable anchor. Similar dates/amounts never create
an association, and a CFDI UUID is deliberately not an identity key.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    AccountingClosePeriod,
    AccountingPoliza,
    AmexAccountingCut,
    AmexCardAccount,
    AmexRecognitionActivation,
    AmexRecognitionConsumption,
    AmexRecognitionRepresentation,
    Aprobacion,
    CFDIReport,
    CuentaContable,
    Documento,
    Empleado,
    ExpenseReport,
)


@dataclass(frozen=True)
class AmexRecognitionResult:
    status: str
    reason: str | None = None
    consumption: Any = None
    poliza: AccountingPoliza | None = None


@dataclass(frozen=True)
class AmexCardDisplayIdentity:
    """Minimal display context from an explicit consumption/card association."""

    cardholder_name: str
    last4: str | None


async def load_amex_card_display_identities(
    session: AsyncSession, expense_ids: list[UUID]
) -> dict[UUID, AmexCardDisplayIdentity]:
    """Read card context in one batch; missing or ambiguous links stay unknown.

    Use the same representation → consumption → card IDs as recognition.
    Never identify a card by its suffix or load its unrelated payment metadata.
    This read does not validate or authorize accounting treatment.
    """
    if not expense_ids:
        return {}
    # Core columns keep this projection typed without loading ORM entities.
    representation = AmexRecognitionRepresentation.__table__
    consumption = AmexRecognitionConsumption.__table__
    card = AmexCardAccount.__table__
    statement = (
        select(
            representation.c.expense_id,
            card.c.id,
            card.c.cardholder_name,
            card.c.last4,
        )
        .select_from(representation)
        .outerjoin(
            consumption,
            consumption.c.id == representation.c.consumption_id,
        )
        .outerjoin(
            card,
            card.c.id == consumption.c.card_account_id,
        )
        .where(representation.c.expense_id.in_(expense_ids))
    )
    result = await session.execute(statement)
    linked: dict[UUID, list[Any]] = {}
    for row in result.all():
        linked.setdefault(row[0], []).append(row)
    identities = {}
    for expense_id, rows in linked.items():
        if len(rows) != 1 or rows[0][1] is None:
            continue
        _, _, name, suffix = rows[0]
        last4 = str(suffix or "")
        identities[expense_id] = AmexCardDisplayIdentity(
            cardholder_name=str(name or "").strip() or "Sin identificar",
            last4=(
                last4
                if len(last4) == 4 and last4.isascii() and last4.isdigit()
                else None
            ),
        )
    return identities


async def lock_recognition(session: AsyncSession) -> None:
    """Serialize identity edits and recognition across administrative origins."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "samchat:amex:shared-recognition"},
    )


@dataclass(frozen=True)
class AmexCardIdentityResult:
    """Metadata assignment receipt; the transaction belongs to the caller."""

    status: str
    reason: str | None = None
    updated_count: int = 0
    unchanged_count: int = 0


async def validate_amex_card_identity(
    session: AsyncSession, card_account_id: UUID
) -> tuple[AmexCardAccount | None, str | None]:
    """Lock and refresh the explicit card and its canonical liability account."""
    from .amex_accounting_posting_service import ALLOWED_AMEX_LIABILITY_CODES

    card = await session.get(
        AmexCardAccount,
        card_account_id,
        populate_existing=True,
        with_for_update=True,
    )
    if card is None or not card.active:
        return None, "invalid_amex_card_identity"
    liability = await session.get(
        CuentaContable,
        card.liability_cuenta_contable_id,
        populate_existing=True,
        with_for_update=True,
    )
    if (
        liability is None
        or not liability.activo
        or liability.codigo not in ALLOWED_AMEX_LIABILITY_CODES
    ):
        return None, "invalid_amex_liability"
    if len(str(card.last4 or "")) != 4 or not str(card.last4).isdigit():
        return None, "invalid_amex_card_identity"
    return card, None


async def _card_identity_eligibility(
    session: AsyncSession,
    expenses: list[ExpenseReport],
    card: AmexCardAccount | None,
    *,
    require_amex: bool = True,
) -> str | None:
    """Validate the complete locked batch before writing any metadata."""
    from .expense_coi_export_service import informe_expense_link_condition

    for expense in expenses:
        if (
            expense.estado_gasto != "activo"
            or expense.origen == "amex_batch"
            or (require_amex and not expense.pagado_con_amex_empresa)
            or expense.coi_estado in {"contabilizado", "reversar"}
        ):
            return "invalid_amex_report_expense"
        if card is not None and expense.ultimos_4_digitos not in (None, "", card.last4):
            return "amex_card_identity_conflict"
        result = await session.execute(
            select(Documento)
            .join(ExpenseReport, informe_expense_link_condition())
            .where(ExpenseReport.id == expense.id, Documento.tipo == "INFORME")
            .order_by(Documento.id)
            .with_for_update(of=Documento)
            .execution_options(populate_existing=True)
        )
        informes = list(result.scalars().all())
        if len(informes) != 1 or informes[0].estado not in {
            "borrador",
            "control_presupuestal",
            "enviado",
            "aprobado",
        }:
            return "invalid_amex_informe"
        result = await session.execute(
            select(AmexAccountingCut.id).where(
                AmexAccountingCut.informe_id == informes[0].id
            )
        )
        if result.first() is not None:
            return "amex_informe_already_cut"
    result = await session.execute(
        select(AmexRecognitionRepresentation.id).where(
            AmexRecognitionRepresentation.expense_id.in_([e.id for e in expenses])
        )
    )
    if result.first() is not None:
        return "amex_representation_already_bound"
    return None


async def validate_amex_pending_edit_transition(
    session: AsyncSession, *, expense_id: UUID
) -> str | None:
    """Guard factual AMEX marking pending Finance without accessing its catalog.

    Existing route permissions remain authoritative. This helper cannot assign
    corporate identity and never clears a previously recorded card identity.
    """
    await lock_recognition(session)
    expenses = await load_expenses(session, [expense_id])
    if len(expenses) != 1:
        return "missing_amex_representation"
    if expenses[0].ultimos_4_digitos:
        return "amex_card_identity_requires_finance"
    return await _card_identity_eligibility(session, expenses, None, require_amex=False)


async def validate_amex_card_edit_transition(
    session: AsyncSession, *, expense_id: UUID, card_account_id: UUID
) -> tuple[AmexCardAccount | None, str | None]:
    """Guard an existing row's AMEX transition without granting edit authority.

    The route owns authorization and audit. Transaction-scoped recognition and
    source locks remain held until its edit commits or rolls back, so a cut or
    binding cannot freeze the source between this validation and that write.
    """
    await lock_recognition(session)
    card, invalid = await validate_amex_card_identity(session, card_account_id)
    if invalid:
        return None, invalid
    expenses = await load_expenses(session, [expense_id])
    if len(expenses) != 1:
        return None, "missing_amex_representation"
    invalid = await _card_identity_eligibility(
        session, expenses, card, require_amex=False
    )
    if invalid:
        return None, invalid
    return card, None


def _write_card_identity(
    session: AsyncSession,
    expenses: list[ExpenseReport],
    card: AmexCardAccount,
    *,
    actor_id: UUID,
    reason: str,
) -> int:
    updated = 0
    for expense in expenses:
        previous = expense.ultimos_4_digitos
        if previous == card.last4:
            continue
        expense.ultimos_4_digitos = card.last4
        session.add(
            Aprobacion(
                tipo_entidad="gasto",
                entidad_id=expense.id,
                aprobador_id=actor_id,
                accion="editar",
                comentario=(
                    f"Identificación explícita AMEX: {previous!r} -> {card.last4}; "
                    f"tarjeta_id={card.id}; motivo={reason.strip()}"
                ),
                fecha=datetime.now(timezone.utc),
            )
        )
        updated += 1
    return updated


async def assign_missing_amex_card(
    session: AsyncSession,
    *,
    expense_ids: list[UUID],
    card_account_id: UUID,
    actor_id: UUID,
    reason: str,
    confirmed: bool,
) -> AmexCardIdentityResult:
    """Identify explicit AMEX report rows without posting or committing.

    All guards precede mutations. The caller must roll back transaction errors.
    Historical activation restrictions remain owned by recognition, not identity.
    """
    await lock_recognition(session)
    actor = await session.get(
        Empleado, actor_id, populate_existing=True, with_for_update=True
    )
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
        return AmexCardIdentityResult("pending", "unauthorized_amex_accounting_actor")
    if not confirmed:
        return AmexCardIdentityResult("pending", "missing_amex_card_confirmation")
    if not reason or not reason.strip():
        return AmexCardIdentityResult("pending", "missing_amex_card_reason")
    if not expense_ids or len(set(expense_ids)) != len(expense_ids):
        return AmexCardIdentityResult("pending", "missing_amex_representation")
    card, invalid = await validate_amex_card_identity(session, card_account_id)
    if invalid:
        return AmexCardIdentityResult("pending", invalid)
    expenses = await load_expenses(session, expense_ids)
    if len(expenses) != len(expense_ids):
        return AmexCardIdentityResult("pending", "missing_amex_representation")
    invalid = await _card_identity_eligibility(session, expenses, card)
    if invalid:
        return AmexCardIdentityResult("pending", invalid)
    updated = _write_card_identity(
        session, expenses, card, actor_id=actor_id, reason=reason
    )
    return AmexCardIdentityResult(
        "assigned" if updated else "unchanged",
        updated_count=updated,
        unchanged_count=len(expenses) - updated,
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
    locked: bool = True,
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
            CuentaContable, account_id, populate_existing=True, with_for_update=locked
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
    session: AsyncSession,
    expenses: list[ExpenseReport],
    *,
    strict_direct: bool = False,
    lock: bool = True,
) -> tuple[list[dict[str, Any]] | None, Decimal, str | None]:
    """Verify full invoice coverage across explicitly distinct economic sources.

    Linked legacy report aliases and bank bridge headers reuse their canonical
    source receipts. Unbound similar rows remain distinct and may conflict.
    The fiscal constructor and explicit shared-invoice rules remain unchanged.
    """
    from .amex_accounting_posting_service import _money, _single_expense_fiscal_lines

    rows = []
    total = Decimal("0.00")
    duplicate_legacy_report = exists(
        select(AmexRecognitionRepresentation.id)
        .join(
            AmexRecognitionConsumption,
            AmexRecognitionConsumption.id
            == AmexRecognitionRepresentation.consumption_id,
        )
        .where(
            AmexRecognitionRepresentation.expense_id == ExpenseReport.id,
            AmexRecognitionRepresentation.role == "report",
            AmexRecognitionConsumption.source_kind == "statement",
            AmexRecognitionConsumption.binding_mode == "legacy",
        )
    )
    bridge_header = exists(
        select(AmexRecognitionConsumption.id).where(
            AmexRecognitionConsumption.imported_expense_id == ExpenseReport.id,
            AmexRecognitionConsumption.binding_mode == "report_bridge",
        )
    )
    for expense in expenses:
        if not expense.cfdi_report_id or expense.cfdi_report is None:
            return None, Decimal(0), "unlinked_amex_charges"
        coverage_query = (
            select(ExpenseReport)
            .options(
                selectinload(ExpenseReport.cfdi_report),
            )
            .where(
                or_(
                    and_(ExpenseReport.origen == "amex_batch", ~bridge_header),
                    and_(
                        or_(
                            ExpenseReport.origen.is_(None),
                            ExpenseReport.origen != "amex_batch",
                        ),
                        ExpenseReport.pagado_con_amex_empresa.is_(True),
                        ~duplicate_legacy_report,
                    ),
                ),
                ExpenseReport.estado_gasto != "cancelado",
                ExpenseReport.cfdi_report_id == expense.cfdi_report_id,
            )
            .order_by(ExpenseReport.id)
        )
        if lock:
            coverage_query = coverage_query.with_for_update().execution_options(
                populate_existing=True
            )
        else:
            coverage_query = coverage_query.execution_options(autoflush=False)
        result = await session.execute(coverage_query)
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
            session,
            expense,
            meta={"expense_id": str(expense.id)},
            **({"strict_direct": True} if strict_direct else {}),
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


async def direct_statement_source(session: Any, expense_ids: list[UUID]) -> bool:
    """New direct receipts belong exclusively to their accounting owner."""
    from ..models import AmexStatementImport, AmexStatementImportItem
    from .amex_accounting_identity_service import effective_direct_sources_available
    from .amex_direct_effective_source_service import effective_sources_select

    if await effective_direct_sources_available(session):
        sources = effective_sources_select()
        return bool(
            (
                await session.execute(
                    select(sources.c.id).where(
                        sources.c.imported_expense_id.in_(expense_ids)
                    )
                )
            ).first()
        )
    return bool(
        (
            await session.execute(
                select(AmexStatementImportItem.id)
                .join(
                    AmexStatementImport,
                    AmexStatementImport.id == AmexStatementImportItem.import_id,
                )
                .where(
                    AmexStatementImportItem.imported_expense_id.in_(expense_ids),
                    AmexStatementImport.workflow_mode == "direct",
                )
            )
        ).first()
    )


async def bind_amex_consumption(
    session: AsyncSession,
    *,
    imported_expense_id: UUID,
    report_expense_ids: list[UUID],
    actor_id: UUID,
    statement_only_reason: str = "",
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
    if not report_expense_ids and not statement_only_reason.strip():
        return AmexRecognitionResult(
            "pending", "amex_statement_only_requires_exception"
        )
    ids = [imported_expense_id, *report_expense_ids]
    if len(set(ids)) != len(ids):
        return AmexRecognitionResult("pending", "duplicate_amex_representation")
    if await direct_statement_source(session, ids):
        return AmexRecognitionResult("pending", "direct_statement_owner_required")
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
    if (
        consumption
        and getattr(consumption, "binding_mode", "legacy") == "report_bridge"
    ):
        return AmexRecognitionResult("pending", "amex_bridge_header_not_postable")
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
            if not report_expense_ids:
                session.add(
                    Aprobacion(
                        tipo_entidad="gasto",
                        entidad_id=imported_expense_id,
                        aprobador_id=actor_id,
                        accion="editar",
                        fecha=datetime.now(timezone.utc),
                        comentario="Excepción financiera AMEX sin informe: "
                        + statement_only_reason.strip(),
                    )
                )
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


async def bind_amex_report_bank_consumptions(
    session: AsyncSession,
    *,
    imported_expense_id: UUID,
    report_consumption_ids: list[UUID],
    actor_id: UUID,
) -> AmexRecognitionResult:
    """Attach an explicit complete bank charge to existing report receipts only."""
    from ..models import AmexReportBankAssociation
    from .amex_accounting_posting_service import ALLOWED_AMEX_LIABILITY_CODES, _money
    from .amex_report_evidence_service import (
        build_amex_report_source_snapshot,
        load_amex_report_attachment_evidence,
    )

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
    if await direct_statement_source(session, [imported_expense_id]):
        return AmexRecognitionResult("pending", "direct_statement_owner_required")
    if not report_consumption_ids or len(set(report_consumption_ids)) != len(
        report_consumption_ids
    ):
        return AmexRecognitionResult("pending", "invalid_amex_bridge_selection")
    result = await session.execute(
        select(AmexRecognitionConsumption)
        .where(AmexRecognitionConsumption.id.in_(report_consumption_ids))
        .order_by(AmexRecognitionConsumption.id)
        .execution_options(populate_existing=True)
    )
    targets: list[Any] = list(result.scalars().all())
    if len(targets) != len(report_consumption_ids) or any(
        c.source_kind != "report"
        or c.binding_mode != "report"
        or c.accounting_poliza_id is None
        for c in targets
    ):
        return AmexRecognitionResult("pending", "amex_bridge_requires_report_receipts")
    # Lock owning documents before canonical accounts and source rows.
    result = await session.execute(
        select(AmexAccountingCut).where(
            AmexAccountingCut.accounting_poliza_id.in_(
                [c.accounting_poliza_id for c in targets]
            )
        )
    )
    cuts: dict[UUID, Any] = {
        cast(Any, c).accounting_poliza_id: c for c in result.scalars().all()
    }
    if set(cuts) != {c.accounting_poliza_id for c in targets}:
        return AmexRecognitionResult("pending", "amex_bridge_requires_complete_cuts")
    await session.execute(
        select(Documento)
        .where(Documento.id.in_({c.informe_id for c in cuts.values()}))
        .order_by(Documento.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    expenses: list[Any] = await load_expenses(
        session, [imported_expense_id, *[c.source_report_expense_id for c in targets]]
    )
    await load_amex_report_attachment_evidence(session, expenses, locked=True)
    by_id: dict[UUID, Any] = {e.id: e for e in expenses}
    if len(by_id) != len(targets) + 1:
        return AmexRecognitionResult("pending", "missing_amex_representation")
    imported = by_id[imported_expense_id]
    if (
        imported.origen != "amex_batch"
        or imported.estado_gasto != "activo"
        or imported.coi_estado in {"contabilizado", "reversar"}
    ):
        return AmexRecognitionResult("pending", "invalid_amex_statement_source")
    if imported.fecha is None or str(imported.currency or "").upper() != "MXN":
        return AmexRecognitionResult("pending", "amex_card_currency_date_mismatch")
    blocked = await activation_reason(session, expenses)
    if blocked:
        return AmexRecognitionResult("pending", blocked)
    card = (
        await session.execute(
            select(AmexCardAccount)
            .where(AmexCardAccount.last4 == imported.ultimos_4_digitos)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    liability = (
        await session.get(
            CuentaContable, card.liability_cuenta_contable_id, populate_existing=True
        )
        if card
        else None
    )
    if (
        card is None
        or not card.active
        or liability is None
        or not liability.activo
        or liability.codigo not in ALLOWED_AMEX_LIABILITY_CODES
    ):
        return AmexRecognitionResult("pending", "invalid_amex_liability")
    for target in targets:
        report = by_id[target.source_report_expense_id]
        cut = cuts[target.accounting_poliza_id]
        frozen = next(
            (
                p
                for p in cut.snapshot_json["partidas"]
                if p["expense_id"] == str(report.id)
                and p["consumption_id"] == str(target.id)
            ),
            None,
        )
        if (
            frozen is None
            or str(target.id) not in cut.snapshot_json["consumption_ids"]
            or frozen.get("source_key") != review_source_key(report)
            or frozen.get("report_source_key")
            != build_amex_report_source_snapshot([report])["source_key"]
            or report.estado_gasto != "activo"
            or report.coi_estado == "reversar"
            or not report.pagado_con_amex_empresa
            or target.card_account_id != card.id
            or target.currency != "MXN"
            or target.economic_date != imported.fecha.date()
            or target.liability_account_id != liability.id
            or target.liability_code != liability.codigo
            or report.fecha is None
            or report.fecha.date() != target.economic_date
            or report.ultimos_4_digitos != card.last4
            or str(report.currency or "").upper() != "MXN"
            or _money(report.gasto_cantidad) != _money(target.amount)
        ):
            return AmexRecognitionResult("pending", "amex_bridge_source_changed")
        links = list(
            (
                await session.execute(
                    select(AmexRecognitionRepresentation).where(
                        AmexRecognitionRepresentation.consumption_id == target.id
                    )
                )
            )
            .scalars()
            .all()
        )
        if (
            len(links) != 1
            or links[0].role != "report"
            or links[0].expense_id != report.id
        ):
            return AmexRecognitionResult("pending", "invalid_amex_report_anchor")
    amount = _money(imported.gasto_cantidad)
    if amount <= 0 or sum((_money(c.amount) for c in targets), Decimal(0)) != amount:
        return AmexRecognitionResult("pending", "amex_representation_amount_mismatch")
    header: Any = (
        await session.execute(
            select(AmexRecognitionConsumption)
            .where(
                AmexRecognitionConsumption.imported_expense_id == imported_expense_id
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if header and (
        header.source_kind != "statement"
        or header.binding_mode != "report_bridge"
        or header.accounting_poliza_id is not None
        or header.card_account_id != card.id
        or _money(header.amount) != amount
        or header.economic_date != imported.fecha.date()
        or header.currency != "MXN"
        or header.liability_account_id != liability.id
        or header.liability_code != liability.codigo
        or header.classification_json.get("source_key")
        != build_amex_report_source_snapshot([imported])["source_key"]
    ):
        return AmexRecognitionResult("pending", "amex_bridge_header_conflict")
    result = await session.execute(
        select(AmexReportBankAssociation).where(
            AmexReportBankAssociation.report_consumption_id.in_(report_consumption_ids)
        )
    )
    associations = list(result.scalars().all())
    if associations and (
        header is None
        or any(a.statement_consumption_id != header.id for a in associations)
    ):
        return AmexRecognitionResult("pending", "amex_report_already_bank_associated")
    if header:
        all_associations = list(
            (
                await session.execute(
                    select(AmexReportBankAssociation).where(
                        AmexReportBankAssociation.statement_consumption_id == header.id
                    )
                )
            )
            .scalars()
            .all()
        )
        if {a.report_consumption_id for a in all_associations} != set(
            report_consumption_ids
        ):
            return AmexRecognitionResult("pending", "amex_bridge_partition_mismatch")
        return AmexRecognitionResult("exists", consumption=header)
    representations = list(
        (
            await session.execute(
                select(AmexRecognitionRepresentation).where(
                    AmexRecognitionRepresentation.expense_id == imported_expense_id
                )
            )
        )
        .scalars()
        .all()
    )
    if representations:
        return AmexRecognitionResult("pending", "amex_representation_already_bound")
    # Existing cut ownership on an otherwise-unbound imported source is frozen.
    from .expense_coi_export_service import informe_expense_link_condition

    result = await session.execute(
        select(AmexAccountingCut.id)
        .join(Documento, Documento.id == AmexAccountingCut.informe_id)
        .join(ExpenseReport, informe_expense_link_condition())
        .where(ExpenseReport.id == imported_expense_id)
    )
    if result.first() is not None:
        return AmexRecognitionResult("pending", "amex_bridge_header_conflict")
    async with session.begin_nested():
        header = AmexRecognitionConsumption(
            id=uuid4(),
            source_kind="statement",
            binding_mode="report_bridge",
            imported_expense_id=imported_expense_id,
            source_report_expense_id=None,
            card_account_id=card.id,
            amount=amount,
            currency="MXN",
            economic_date=imported.fecha.date(),
            liability_account_id=liability.id,
            liability_code=liability.codigo,
            accounting_poliza_id=None,
            classification_json={
                "source_key": build_amex_report_source_snapshot([imported])[
                    "source_key"
                ]
            },
            actor_id=actor_id,
        )
        session.add(header)
        await session.flush()
        session.add(
            AmexRecognitionRepresentation(
                id=uuid4(),
                consumption_id=header.id,
                expense_id=imported_expense_id,
                role="statement",
            )
        )
        for target in targets:
            session.add(
                AmexReportBankAssociation(
                    statement_consumption_id=header.id,
                    report_consumption_id=target.id,
                    actor_id=actor_id,
                )
            )
        await session.flush()
    return AmexRecognitionResult("bound", consumption=header)


async def recognize_expenses(
    session: AsyncSession, expenses: list[ExpenseReport]
) -> AmexRecognitionResult:
    """Compatibility preparation hook: only the explicit cut may post."""
    return AmexRecognitionResult("prepared")
