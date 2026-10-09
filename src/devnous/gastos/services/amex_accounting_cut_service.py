"""Finance-reviewed, atomic AMEX accounting cuts: one report, one journal."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    AccountingPoliza,
    AmexAccountingCut,
    AmexAccountingReview,
    AmexCardAccount,
    AmexRecognitionConsumption,
    AmexRecognitionRepresentation,
    CuentaContable,
    Documento,
    Empleado,
    ExpenseReport,
)
from .amex_accounting_posting_service import (
    ALLOWED_AMEX_LIABILITY_CODES,
    _create_poliza,
    _credit_line,
    _debit_line,
    _money,
    posting_is_balanced,
)
from .amex_recognition_service import (
    accounting_review_source_key,
    activation_reason,
    fiscal_lines,
    load_expenses,
    lock_recognition,
    period_reason,
    signature,
)
from .expense_coi_export_service import (
    coi_document_loader_options,
    coi_document_metadata,
)


@dataclass(frozen=True)
class AmexCutResult:
    status: str
    reason: str | None = None
    review: Any = None
    cut: Any = None
    poliza: Any = None


async def _actor(session: AsyncSession, actor: Empleado) -> Empleado | None:
    stored = await session.get(
        Empleado, getattr(actor, "id", None), populate_existing=True
    )
    if (
        stored is None
        or not stored.activo
        or stored.rol
        not in {
            "finanzas",
            "admin",
            "superadmin",
            "super_admin",
        }
    ):
        return None
    return stored


async def _informe(session: AsyncSession, informe_id: UUID) -> Documento | None:
    result = await session.execute(
        select(Documento)
        .options(*coi_document_loader_options())
        .where(Documento.id == informe_id)
        .with_for_update()
    )
    informe = result.scalar_one_or_none()
    if (
        informe is None
        or informe.tipo != "INFORME"
        or informe.estado != "aprobado"
        or not informe.cuenta_gastos_id
    ):
        return None
    return informe


async def _partner_account(session: AsyncSession, account_id: UUID | None):
    if account_id is None:
        return None
    account = await session.get(CuentaContable, account_id, populate_existing=True)
    if (
        account is None
        or not account.activo
        or not account.codigo.startswith("1170-002-")
    ):
        return None
    suffix = account.codigo.removeprefix("1170-002-")
    return account if len(suffix) == 3 and suffix.isdigit() else None


async def review_amex_partida(
    session: AsyncSession,
    *,
    informe_id: UUID,
    expense_id: UUID,
    treatment: str,
    debtor_account_id: UUID | None,
    actor: Empleado,
    reason: str = "",
) -> AmexCutResult:
    """Persist an explicit Finance decision; automatic matching cannot reset it."""
    await lock_recognition(session)
    stored_actor = await _actor(session, actor)
    if stored_actor is None:
        return AmexCutResult("pending", "unauthorized_amex_accounting_actor")
    informe = await _informe(session, informe_id)
    if informe is None:
        return AmexCutResult("pending", "amex_informe_not_approved")
    reviewed_expenses = await load_expenses(session, [expense_id])
    expense: Any = reviewed_expenses[0] if reviewed_expenses else None
    if (
        expense is None
        or expense.cuenta_gastos_id != informe.cuenta_gastos_id
        or expense.estado_gasto == "cancelado"
        or not (
            expense.pagado_con_amex_empresa is True
            or (
                expense.pagado_con_amex_empresa is None
                and expense.origen == "amex_batch"
            )
        )
    ):
        return AmexCutResult("pending", "invalid_amex_review_partida")
    if treatment not in {"expense", "partner_receivable"}:
        return AmexCutResult("pending", "invalid_amex_treatment")
    reason = reason.strip()
    if treatment == "partner_receivable":
        if not reason or not await _partner_account(session, debtor_account_id):
            return AmexCutResult("pending", "invalid_amex_partner_decision")
    elif debtor_account_id is not None:
        return AmexCutResult("pending", "unexpected_amex_debtor_account")
    review: Any = await session.get(AmexAccountingReview, expense_id)
    if review and review.informe_id != informe_id:
        return AmexCutResult("pending", "amex_partida_reviewed_for_another_informe")
    source_key = await accounting_review_source_key(
        session, expense, treatment=treatment, debtor_account_id=debtor_account_id
    )
    if review and (
        review.source_key == source_key
        and review.treatment == treatment
        and review.debtor_account_id == debtor_account_id
        and review.reason == reason
    ):
        return AmexCutResult("reviewed", review=review)
    async with session.begin_nested():
        if review is None:
            review = AmexAccountingReview(
                expense_id=expense_id, informe_id=informe_id, version=1
            )
            session.add(review)
        else:
            review.version += 1
        review.source_key = source_key
        review.treatment = treatment
        review.debtor_account_id = debtor_account_id
        review.actor_id = stored_actor.id
        review.reason = reason
        review.reviewed_at = datetime.now(timezone.utc)
        await session.flush()
    return AmexCutResult("reviewed", review=review)


def _freeze_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "cuenta_contable_id": str(row["cuenta_contable_id"]),
            "debe": str(_money(row.get("debe"))),
            "haber": str(_money(row.get("haber"))),
        }
        for row in lines
    ]


def _freeze_cfdi(cfdi: Any) -> dict[str, Any] | None:
    if cfdi is None:
        return None
    mapping = {
        "uuid": "cfdi_uuid",
        "date": "fecha",
        "rfc_emisor": "emisor_rfc",
        "rfc_receptor": "receptor_rfc",
        "total": "total",
        "folio": "folio",
        "nombre_emisor": "emisor_nombre",
        "uso_cfdi": "receptor_uso_cfdi",
    }
    return {
        key: (
            str(getattr(cfdi, source))
            if getattr(cfdi, source, None) is not None
            else None
        )
        for key, source in mapping.items()
    }


async def create_amex_accounting_cut(
    session: AsyncSession,
    *,
    informe_id: UUID,
    actor: Empleado,
    expected_versions: dict[str, int],
    accounting_date: date,
    adjustment: bool = False,
    reason: str = "",
) -> AmexCutResult:
    """Confirm all active report items atomically, or compensate a prior cut."""
    if not isinstance(accounting_date, date) or isinstance(accounting_date, datetime):
        return AmexCutResult("pending", "invalid_amex_accounting_date")
    await lock_recognition(session)
    stored_actor = await _actor(session, actor)
    if stored_actor is None:
        return AmexCutResult("pending", "unauthorized_amex_accounting_actor")
    informe = await _informe(session, informe_id)
    if informe is None:
        return AmexCutResult("pending", "amex_informe_not_approved")
    query_result_1 = await session.execute(
        select(ExpenseReport.id)
        .where(
            ExpenseReport.cuenta_gastos_id == informe.cuenta_gastos_id,
            ExpenseReport.estado_gasto != "cancelado",
        )
        .order_by(ExpenseReport.id)
    )
    expenses: list[Any] = await load_expenses(
        session, list(query_result_1.scalars().all())
    )
    if not expenses:
        return AmexCutResult("pending", "empty_amex_accounting_cut")
    if any(
        not (
            e.pagado_con_amex_empresa is True
            or (e.pagado_con_amex_empresa is None and e.origen == "amex_batch")
        )
        for e in expenses
    ):
        return AmexCutResult("pending", "unsupported_mixed_amex_informe")
    ids = {e.id for e in expenses}
    query_result_2 = await session.execute(
        select(AmexAccountingReview)
        .where(
            AmexAccountingReview.informe_id == informe_id,
            AmexAccountingReview.expense_id.in_(ids),
        )
        .with_for_update()
    )
    reviews: dict[UUID, Any] = {
        cast(Any, r).expense_id: r for r in query_result_2.scalars().all()
    }
    if set(reviews) != ids:
        return AmexCutResult("pending", "unreviewed_amex_partidas")
    for expense in expenses:
        review = reviews[expense.id]
        fresh_source = await accounting_review_source_key(
            session,
            expense,
            treatment=review.treatment,
            debtor_account_id=review.debtor_account_id,
        )
        if review.source_key != fresh_source:
            return AmexCutResult("pending", "stale_amex_review_source")
    actual_versions = {str(key): value.version for key, value in reviews.items()}
    if expected_versions != actual_versions:
        return AmexCutResult("pending", "stale_amex_review_versions")
    reason = reason.strip()
    state = {
        "informe_id": str(informe_id),
        "kind": "adjustment" if adjustment else "initial",
        "versions": actual_versions,
        "accounting_date": str(accounting_date),
    }
    state_key = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    query_result_3 = await session.execute(
        select(AmexAccountingCut).where(AmexAccountingCut.state_key == state_key)
    )
    same = query_result_3.scalar_one_or_none()
    if same:
        return AmexCutResult(
            "exists",
            cut=same,
            poliza=await session.get(AccountingPoliza, same.accounting_poliza_id),
        )
    query_result_4 = await session.execute(
        select(AmexAccountingCut)
        .where(AmexAccountingCut.informe_id == informe_id)
        .order_by(AmexAccountingCut.created_at.desc(), AmexAccountingCut.id.desc())
    )
    previous_cuts: list[Any] = list(query_result_4.scalars().all())
    initial = next((cut for cut in previous_cuts if cut.kind == "initial"), None)
    if previous_cuts:
        referenced = {cut.snapshot_json.get("previous_cut_id") for cut in previous_cuts}
        heads = [cut for cut in previous_cuts if str(cut.id) not in referenced]
        if len(heads) != 1:
            return AmexCutResult("pending", "ambiguous_amex_cut_history")
        previous_cuts = [heads[0], *[cut for cut in previous_cuts if cut != heads[0]]]
    if initial and not adjustment:
        return AmexCutResult("pending", "amex_informe_already_cut")
    if adjustment and (initial is None or not reason):
        return AmexCutResult("pending", "amex_adjustment_requires_initial_and_reason")
    cutoff_reason = await activation_reason(session, expenses)
    if cutoff_reason:
        return AmexCutResult("pending", cutoff_reason)
    if any(e.fecha is None for e in expenses):
        return AmexCutResult("pending", "missing_amex_economic_date")
    months = {(e.fecha.year, e.fecha.month) for e in expenses}
    if len(months) != 1:
        return AmexCutResult("pending", "amex_report_spans_accounting_months")
    if not adjustment and (accounting_date.year, accounting_date.month) not in months:
        return AmexCutResult("pending", "amex_initial_cut_date_period_mismatch")
    blocked = await period_reason(session, accounting_date)
    if blocked:
        return AmexCutResult("pending", blocked)
    query_result_5 = await session.execute(
        select(AmexRecognitionRepresentation).where(
            AmexRecognitionRepresentation.expense_id.in_(ids)
        )
    )
    links = {link.expense_id: link for link in query_result_5.scalars().all()}
    if set(links) != ids:
        return AmexCutResult("pending", "missing_amex_consumption_identity")
    query_result_6 = await session.execute(
        select(AmexRecognitionConsumption)
        .where(
            AmexRecognitionConsumption.id.in_(
                {r.consumption_id for r in links.values()}
            )
        )
        .order_by(AmexRecognitionConsumption.id)
        .with_for_update()
    )
    consumptions: dict[UUID, Any] = {
        cast(Any, c).id: c for c in query_result_6.scalars().all()
    }
    if set(consumptions) != {link.consumption_id for link in links.values()}:
        return AmexCutResult("pending", "missing_amex_consumption_identity")
    for consumption in consumptions.values():
        if consumption.currency != "MXN":
            return AmexCutResult("pending", "unsupported_amex_currency")
        query_result_7 = await session.execute(
            select(AmexRecognitionRepresentation).where(
                AmexRecognitionRepresentation.consumption_id == consumption.id,
                AmexRecognitionRepresentation.role == "report",
            )
        )
        report_ids = {r.expense_id for r in query_result_7.scalars().all()}
        if report_ids and not report_ids.issubset(ids):
            return AmexCutResult("pending", "incomplete_amex_report_partition")
        selected = [e for e in expenses if links[e.id].consumption_id == consumption.id]
        if sum((_money(e.gasto_cantidad) for e in selected), Decimal(0)) != _money(
            consumption.amount
        ):
            return AmexCutResult("pending", "amex_representation_amount_mismatch")
        card = await session.get(
            AmexCardAccount, consumption.card_account_id, populate_existing=True
        )
        account = await session.get(
            CuentaContable, consumption.liability_account_id, populate_existing=True
        )
        if (
            card is None
            or not card.active
            or account is None
            or not account.activo
            or account.codigo not in ALLOWED_AMEX_LIABILITY_CODES
            or account.codigo != consumption.liability_code
            or card.liability_cuenta_contable_id != account.id
        ):
            return AmexCutResult("pending", "amex_liability_mapping_changed")
        anchor_result = await session.execute(
            select(ExpenseReport)
            .where(ExpenseReport.id == consumption.imported_expense_id)
            .with_for_update()
        )
        anchor = anchor_result.scalar_one_or_none()
        if (
            anchor is None
            or anchor.estado_gasto == "cancelado"
            or _money(anchor.gasto_cantidad) != _money(consumption.amount)
            or anchor.fecha is None
            or anchor.fecha.date() != consumption.economic_date
            or anchor.ultimos_4_digitos != card.last4
            or str(anchor.currency).upper() != consumption.currency
        ):
            return AmexCutResult("pending", "amex_consumption_source_changed")
        blocked = await activation_reason(session, [anchor])
        if blocked:
            return AmexCutResult("pending", blocked)
        if any(
            e.fecha.date() != consumption.economic_date
            or str(e.currency).upper() != consumption.currency
            or e.ultimos_4_digitos != card.last4
            for e in selected
        ):
            return AmexCutResult("pending", "amex_card_currency_date_mismatch")
        if consumption.accounting_poliza_id and not initial:
            return AmexCutResult(
                "pending", "amex_previous_receipt_requires_separate_review"
            )
    old_partidas = (
        {p["expense_id"]: p for p in previous_cuts[0].snapshot_json["partidas"]}
        if initial
        else {}
    )
    if initial and set(old_partidas) != {str(i) for i in ids}:
        return AmexCutResult("pending", "amex_cut_partidas_changed")
    partidas: list[dict[str, Any]] = []
    journal_lines: list[dict[str, Any]] = []
    changes = 0
    for expense in expenses:
        review = reviews[expense.id]
        consumption = consumptions[cast(Any, links[expense.id]).consumption_id]
        amount = _money(expense.gasto_cantidad)
        if amount <= 0:
            return AmexCutResult("pending", "invalid_amex_consumption_amount")
        meta = {
            "expense_id": str(expense.id),
            "consumption_id": str(consumption.id),
            "informe_id": str(informe_id),
            "economic_date": str(expense.fecha.date()),
            "card_account_id": str(consumption.card_account_id),
            "treatment": review.treatment,
            "cfdi_report_id": (
                str(expense.cfdi_report_id) if expense.cfdi_report_id else None
            ),
        }
        previous = old_partidas.get(str(expense.id))
        if previous and (
            _money(previous["amount"]) != amount
            or previous["consumption_id"] != str(consumption.id)
        ):
            return AmexCutResult("pending", "amex_cut_source_changed")
        debtor = None
        if review.treatment == "partner_receivable":
            debtor = await _partner_account(session, review.debtor_account_id)
            if debtor is None or not review.reason:
                return AmexCutResult("pending", "invalid_amex_partner_decision")
            rows = [
                _debit_line(
                    code=debtor.codigo,
                    account_id=debtor.id,
                    amount=amount,
                    concept="Cargo de consumo AMEX a socio",
                    meta=meta,
                    movement="debe_deudor_socio_amex",
                )
            ]
        elif review.treatment == "expense":
            if adjustment and previous and previous["treatment"] == "expense":
                # Immutable prior fiscal evidence is reused, never recalculated.
                rows = previous["lines"]
            else:
                fiscal_rows, net, blocked = await fiscal_lines(session, [expense])
                if blocked or fiscal_rows is None or net != amount:
                    return AmexCutResult(
                        "pending", blocked or "amex_amount_fiscal_mismatch"
                    )
                rows = fiscal_rows
                for row in rows:
                    row["raw_row_json"] = {**row.get("raw_row_json", {}), **meta}
        else:
            return AmexCutResult("pending", "invalid_amex_treatment")
        frozen = _freeze_lines(rows)
        current = {
            **meta,
            "amount": str(amount),
            "currency": consumption.currency,
            "version": review.version,
            "actor_id": str(review.actor_id),
            "reason": review.reason,
            "debtor_account_id": (
                str(review.debtor_account_id) if review.debtor_account_id else None
            ),
            "liability_account_id": str(consumption.liability_account_id),
            "liability_code": consumption.liability_code,
            "lines": frozen,
            "reference": expense.numero_referencia,
            "concepto": expense.concepto,
            "economic_date": str(expense.fecha.date()),
            "journal_lines": [],
            "cfdi": (
                previous.get("cfdi") if previous else _freeze_cfdi(expense.cfdi_report)
            ),
        }

        partidas.append(current)
        journal_start = len(journal_lines)
        if adjustment:
            if previous is None:
                return AmexCutResult("pending", "amex_cut_partidas_changed")
            if previous["treatment"] == review.treatment:
                if previous.get("debtor_account_id") != current["debtor_account_id"]:
                    return AmexCutResult(
                        "pending", "unsupported_amex_partner_account_change"
                    )
                continue
            if (
                previous["treatment"] != "expense"
                or review.treatment != "partner_receivable"
            ):
                return AmexCutResult("pending", "unsupported_amex_treatment_reversal")
            for original in previous["lines"]:
                reverse = {
                    **original,
                    "debe": original["haber"],
                    "haber": original["debe"],
                    "raw_row_json": {
                        **original["raw_row_json"],
                        "reverses_cut_id": str(previous_cuts[0].id),
                    },
                }
                journal_lines.append(reverse)
            journal_lines.extend(frozen)
            changes += 1
        else:
            journal_lines.extend(frozen)
            journal_lines.append(
                _credit_line(
                    code=consumption.liability_code,
                    account_id=consumption.liability_account_id,
                    amount=amount,
                    concept="Pasivo AMEX del consumo",
                    meta=meta,
                    movement="haber_pasivo_amex",
                )
            )
        current["journal_lines"] = _freeze_lines(journal_lines[journal_start:])
    if adjustment and not changes:
        return AmexCutResult("pending", "no_amex_reclassification_changes")
    if not posting_is_balanced(journal_lines):
        return AmexCutResult("pending", "unbalanced_amex_accounting_cut")
    cut_id = uuid4()
    for row in journal_lines:
        row["raw_row_json"] = {**row.get("raw_row_json", {}), "cut_id": str(cut_id)}
    frozen_journal = _freeze_lines(journal_lines)
    for partida in partidas:
        partida["journal_lines"] = [
            row
            for row in frozen_journal
            if row["raw_row_json"].get("expense_id") == partida["expense_id"]
        ]
    persistence_lines = [
        {**row, "cuenta_contable_id": UUID(str(row["cuenta_contable_id"]))}
        for row in journal_lines
    ]
    async with session.begin_nested():
        poliza = await _create_poliza(
            session,
            origen="amex_cut_adjustment" if adjustment else "amex_cut_initial",
            numero_poliza=f"AMX-CUT-{cut_id}",
            fecha=datetime.combine(accounting_date, datetime.min.time()),
            beneficiario_nombre="AMEX",
            concepto=f"Corte AMEX {informe.numero_referencia}",
            lines=persistence_lines,
        )
        cut = AmexAccountingCut(
            id=cut_id,
            informe_id=informe_id,
            kind="adjustment" if adjustment else "initial",
            state_key=state_key,
            accounting_date=accounting_date,
            accounting_poliza_id=poliza.id,
            actor_id=stored_actor.id,
            reason=reason,
            snapshot_json={
                "partidas": partidas,
                "lines": frozen_journal,
                "informe_reference": informe.numero_referencia,
                "coi_metadata": coi_document_metadata(informe),
                "consumption_ids": [str(i) for i in consumptions],
                "review_versions": actual_versions,
                "previous_cut_id": str(previous_cuts[0].id) if previous_cuts else None,
            },
        )
        session.add(cut)
        if not adjustment:
            for consumption in consumptions.values():
                consumption.accounting_poliza_id = poliza.id
                matching = [
                    p for p in partidas if p["consumption_id"] == str(consumption.id)
                ]
                consumption.classification_json = signature(
                    [row for p in matching for row in p["lines"]]
                )
        await session.flush()
    return AmexCutResult("created", cut=cut, poliza=poliza)
