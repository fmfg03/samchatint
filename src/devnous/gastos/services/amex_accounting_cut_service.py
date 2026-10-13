"""Finance-reviewed, atomic AMEX accounting cuts: one report, one journal."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    AccountingPoliza,
    Aprobacion,
    AmexReportSourceEvidence,
    AmexAccountingCut,
    AmexAccountingReview,
    AmexCardAccount,
    AmexRecognitionConsumption,
    AmexRecognitionRepresentation,
    CuentaContable,
    CuentaDeGastos,
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
    review_source_key,
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
    source_evidence_id: UUID | None = None,
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
    if review and source_evidence_id is None:
        # Adopted legacy projections deliberately keep their original NULL FK.
        # Their immutable Finance receipt still protects subsequent decisions.
        protected = getattr(review, "source_evidence_id", None) is not None
        if not protected:
            adopted = (
                await session.execute(
                    select(AmexReportSourceEvidence.id)
                    .where(
                        AmexReportSourceEvidence.informe_id == informe_id,
                        AmexReportSourceEvidence.kind == "finance_confirmation",
                        AmexReportSourceEvidence.decisions_json[
                            "adopted_reviews"
                        ].contains([{"expense_id": str(expense_id)}]),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            protected = adopted is not None
        if protected:
            return AmexCutResult("pending", "amex_review_update_requires_confirmation")
    async with session.begin_nested():
        if review is None:
            review = AmexAccountingReview(
                expense_id=expense_id, informe_id=informe_id, version=1
            )
            session.add(review)
        else:
            review.version += 1
        review.source_evidence_id = source_evidence_id
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
    source_evidence_id: UUID | None = None,
) -> AmexCutResult:
    """Confirm all active report items atomically, or compensate a prior cut."""
    from .amex_report_evidence_service import (
        build_amex_report_source_snapshot,
        load_amex_report_attachment_evidence,
    )

    if not isinstance(accounting_date, date) or isinstance(accounting_date, datetime):
        return AmexCutResult("pending", "invalid_amex_accounting_date")
    await lock_recognition(session)
    stored_actor = await _actor(session, actor)
    if stored_actor is None:
        return AmexCutResult("pending", "unauthorized_amex_accounting_actor")
    informe = await _informe(session, informe_id)
    if informe is None:
        return AmexCutResult("pending", "amex_informe_not_approved")
    finance_evidence = None
    if source_evidence_id is not None:
        finance_evidence = await session.get(
            AmexReportSourceEvidence, source_evidence_id
        )
        if (
            finance_evidence is None
            or finance_evidence.kind != "finance_confirmation"
            or finance_evidence.informe_id != informe_id
            or finance_evidence.actor_id != stored_actor.id
        ):
            return AmexCutResult("pending", "invalid_amex_finance_evidence")
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
    if finance_evidence is not None:
        await load_amex_report_attachment_evidence(session, expenses, locked=True)
        if (
            finance_evidence.source_key
            != build_amex_report_source_snapshot(expenses)["source_key"]
        ):
            return AmexCutResult("pending", "stale_amex_finance_source_evidence")

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
        if source_evidence_id is not None and same.snapshot_json.get(
            "source_evidence_id"
        ) != str(source_evidence_id):
            return AmexCutResult(
                "pending", "amex_finance_confirmation_payload_conflict"
            )
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
    missing = ids - set(links)
    if missing and source_evidence_id is None:
        return AmexCutResult("pending", "missing_amex_consumption_identity")
    if missing:
        # This branch is reached only inside the consolidated Finance transaction.
        evidence = await session.get(AmexReportSourceEvidence, source_evidence_id)
        if (
            evidence is None
            or evidence.kind != "finance_confirmation"
            or evidence.informe_id != informe_id
            or evidence.actor_id != stored_actor.id
        ):
            return AmexCutResult("pending", "invalid_amex_finance_evidence")
        for expense in expenses:
            if expense.id not in missing:
                continue
            if expense.origen == "amex_batch" or expense.coi_estado in {
                "contabilizado",
                "reversar",
            }:
                return AmexCutResult("pending", "invalid_amex_report_anchor")
            card = (
                await session.execute(
                    select(AmexCardAccount).where(
                        AmexCardAccount.last4 == expense.ultimos_4_digitos,
                        AmexCardAccount.active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            liability = (
                await session.get(
                    CuentaContable,
                    card.liability_cuenta_contable_id,
                    populate_existing=True,
                )
                if card
                else None
            )
            if (
                card is None
                or liability is None
                or not liability.activo
                or liability.codigo not in ALLOWED_AMEX_LIABILITY_CODES
            ):
                return AmexCutResult("pending", "invalid_amex_liability")
            if (
                str(expense.currency or "").upper() != "MXN"
                or _money(expense.gasto_cantidad) <= 0
            ):
                return AmexCutResult("pending", "invalid_amex_consumption_amount")
            consumption = AmexRecognitionConsumption(
                id=uuid4(),
                source_kind="report",
                binding_mode="report",
                imported_expense_id=None,
                source_report_expense_id=expense.id,
                card_account_id=card.id,
                amount=_money(expense.gasto_cantidad),
                currency="MXN",
                economic_date=expense.fecha.date(),
                liability_account_id=liability.id,
                liability_code=liability.codigo,
                classification_json=[],
                actor_id=stored_actor.id,
            )
            session.add(consumption)
            await session.flush()
            link = AmexRecognitionRepresentation(
                id=uuid4(),
                consumption_id=consumption.id,
                expense_id=expense.id,
                role="report",
            )
            session.add(link)
            links[expense.id] = link
        await session.flush()
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
        if getattr(consumption, "binding_mode", "legacy") == "report_bridge":
            return AmexCutResult("pending", "amex_bridge_header_not_postable")
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
            .where(
                ExpenseReport.id
                == (
                    consumption.source_report_expense_id
                    if getattr(consumption, "source_kind", "statement") == "report"
                    else consumption.imported_expense_id
                )
            )
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
            "source_key": review_source_key(expense),
            "report_source_key": build_amex_report_source_snapshot([expense])[
                "source_key"
            ],
            "source_evidence_id": (
                str(review.source_evidence_id)
                if getattr(review, "source_evidence_id", None)
                else None
            ),
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
                "source_evidence_id": (
                    str(source_evidence_id) if source_evidence_id else None
                ),
                "operational_evidence_id": (
                    str(finance_evidence.operational_evidence_id)
                    if finance_evidence and finance_evidence.operational_evidence_id
                    else None
                ),
                "finance_confirmation_actor_id": (
                    str(finance_evidence.actor_id) if finance_evidence else None
                ),
                "finance_confirmation_payload_hash": (
                    finance_evidence.request_payload_hash if finance_evidence else None
                ),
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


@dataclass(frozen=True)
class AmexApprovedInformePreview:
    status: str
    reason: str | None = None
    informe: Any = None
    expenses: Any = field(default_factory=list)
    operational_evidence: Any = None
    approval_event: Any = None
    source_key: str | None = None
    reviews: Any = field(default_factory=dict)
    current_versions: Any = field(default_factory=dict)
    approval_version: int | None = None
    fresh_review_ids: Any = field(default_factory=set)
    exceptions: Any = field(default_factory=dict)


def _receipt_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _review_projection(review: Any) -> dict[str, Any]:
    return {
        "expense_id": str(review.expense_id),
        "actor_id": str(review.actor_id),
        "reviewed_at": str(review.reviewed_at),
        "treatment": review.treatment,
        "debtor_account_id": (
            str(review.debtor_account_id) if review.debtor_account_id else None
        ),
        "reason": review.reason,
        "version": review.version,
        "source_key": review.source_key,
        "source_evidence_id": (
            str(review.source_evidence_id)
            if getattr(review, "source_evidence_id", None)
            else None
        ),
    }


async def _approved_informe_preview(
    session: AsyncSession,
    *,
    informe_id: UUID,
    actor: Empleado,
    locked: bool,
) -> AmexApprovedInformePreview:
    from .amex_report_evidence_service import (
        build_amex_report_source_snapshot,
        load_amex_report_attachment_evidence,
    )

    if await _actor(session, actor) is None:
        return AmexApprovedInformePreview(
            "pending", "unauthorized_amex_accounting_actor"
        )
    query = (
        select(Documento)
        .options(*coi_document_loader_options())
        .where(Documento.id == informe_id)
        .execution_options(populate_existing=True)
    )
    if locked:
        query = query.with_for_update()
    informe = (await session.execute(query)).scalar_one_or_none()
    if (
        informe is None
        or informe.tipo != "INFORME"
        or informe.estado != "aprobado"
        or not informe.cuenta_gastos_id
    ):
        return AmexApprovedInformePreview("pending", "amex_informe_not_approved")
    if locked:
        cuenta = await session.get(
            CuentaDeGastos,
            informe.cuenta_gastos_id,
            populate_existing=True,
            with_for_update=True,
        )
        if cuenta is None:
            return AmexApprovedInformePreview(
                "pending", "missing_amex_informe_account", informe=informe
            )
    query = (
        select(ExpenseReport)
        .options(
            selectinload(ExpenseReport.cuenta_contable),
            selectinload(ExpenseReport.cuenta_iva),
            selectinload(ExpenseReport.cfdi_report),
        )
        .where(
            ExpenseReport.cuenta_gastos_id == informe.cuenta_gastos_id,
            ExpenseReport.estado_gasto != "cancelado",
        )
        .order_by(ExpenseReport.id)
    )
    expenses: list[Any]
    if locked:
        ids = list(
            (
                await session.execute(
                    select(ExpenseReport.id)
                    .where(
                        ExpenseReport.cuenta_gastos_id == informe.cuenta_gastos_id,
                        ExpenseReport.estado_gasto != "cancelado",
                    )
                    .order_by(ExpenseReport.id)
                )
            )
            .scalars()
            .all()
        )
        expenses = await load_expenses(session, ids)
    else:
        expenses = list((await session.execute(query)).scalars().all())
    if not expenses:
        return AmexApprovedInformePreview(
            "pending", "empty_amex_accounting_cut", informe=informe
        )
    if any(
        not (
            e.pagado_con_amex_empresa is True
            or (e.pagado_con_amex_empresa is None and e.origen == "amex_batch")
        )
        for e in expenses
    ):
        return AmexApprovedInformePreview(
            "pending",
            "unsupported_mixed_amex_informe",
            informe=informe,
            expenses=expenses,
        )
    evidences = list(
        (
            await session.execute(
                select(AmexReportSourceEvidence)
                .where(
                    AmexReportSourceEvidence.informe_id == informe_id,
                    AmexReportSourceEvidence.kind == "operational_approval",
                )
                .order_by(AmexReportSourceEvidence.version.desc())
            )
        )
        .scalars()
        .all()
    )
    operational: Any = evidences[0] if evidences else None
    approval: Any = (
        await session.get(Aprobacion, operational.approval_id) if operational else None
    )
    if operational is None:
        # Only the final event matching the real approval timestamp is evidence.
        approval = (
            await session.execute(
                select(Aprobacion)
                .where(
                    Aprobacion.tipo_entidad == "documento",
                    Aprobacion.entidad_id == informe_id,
                    Aprobacion.accion == "aprobar",
                    Aprobacion.fecha == informe.aprobado_en,
                )
                .order_by(Aprobacion.id)
            )
        ).scalar_one_or_none()
    await load_amex_report_attachment_evidence(session, expenses, locked=locked)
    source_snapshot = build_amex_report_source_snapshot(expenses)
    exceptions: dict[str, str] = {}
    if operational and operational.source_key != source_snapshot["source_key"]:
        exceptions["source"] = "stale_amex_operational_source"
    elif operational is None:
        exceptions["source"] = "missing_amex_operational_attestation"
    query = (
        select(AmexAccountingReview)
        .where(AmexAccountingReview.expense_id.in_([e.id for e in expenses]))
        .execution_options(populate_existing=True)
    )
    if locked:
        query = query.order_by(AmexAccountingReview.expense_id).with_for_update()
    reviews: dict[UUID, Any] = {
        cast(Any, r).expense_id: r
        for r in (await session.execute(query)).scalars().all()
    }
    fresh = set()
    fingerprints: dict[str, Any] = {}
    for expense in expenses:
        review = reviews.get(cast(Any, expense).id)
        if review and review.informe_id != informe_id:
            exceptions[str(expense.id)] = "amex_partida_reviewed_for_another_informe"
            continue
        # Bind displayed factual expense/tax mappings even before a treatment
        # is declared. Computing this key does not assign a financial decision.
        fingerprints["expense_mapping:" + str(expense.id)] = (
            await accounting_review_source_key(
                session,
                expense,
                treatment="expense",
                debtor_account_id=None,
                locked=locked,
            )
        )
        if review:
            key = await accounting_review_source_key(
                session,
                expense,
                treatment=review.treatment,
                debtor_account_id=review.debtor_account_id,
                locked=locked,
            )
            fingerprints[str(expense.id)] = key
            if key == review.source_key:
                fresh.add(expense.id)
            else:
                exceptions[str(expense.id)] = "stale_amex_review_source"
        else:
            exceptions[str(expense.id)] = "unreviewed_amex_partida"
        card = (
            await session.execute(
                select(AmexCardAccount)
                .where(AmexCardAccount.last4 == expense.ultimos_4_digitos)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if locked and card is not None:
            card = await session.get(
                AmexCardAccount, card.id, populate_existing=True, with_for_update=True
            )
        liability = (
            await session.get(
                CuentaContable,
                card.liability_cuenta_contable_id,
                populate_existing=True,
            )
            if card
            else None
        )
        fingerprints["card:" + str(expense.id)] = {
            "id": str(card.id) if card else None,
            "active": bool(card.active) if card else None,
            "last4": card.last4 if card else None,
            "account": str(liability.id) if liability else None,
            "code": liability.codigo if liability else None,
            "account_active": bool(liability.activo) if liability else None,
        }
        if (
            card is None
            or not card.active
            or liability is None
            or not liability.activo
            or liability.codigo not in ALLOWED_AMEX_LIABILITY_CODES
        ):
            exceptions[str(expense.id)] = "invalid_amex_liability"
    cutoff = await activation_reason(session, expenses)
    if cutoff:
        exceptions["activation"] = cutoff
    versions = {
        str(e.id): reviews[cast(Any, e).id].version if cast(Any, e).id in reviews else 0
        for e in expenses
    }
    partner_catalog = list(
        (
            await session.execute(
                select(CuentaContable)
                .where(
                    CuentaContable.activo.is_(True),
                    CuentaContable.codigo.like("1170-002-%"),
                )
                .order_by(CuentaContable.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    fingerprints["partner_catalog"] = [
        {"id": str(account.id), "code": account.codigo, "active": bool(account.activo)}
        for account in partner_catalog
        if len(account.codigo.removeprefix("1170-002-")) == 3
        and account.codigo.removeprefix("1170-002-").isdigit()
    ]
    aggregate = _receipt_hash(
        {
            "source": source_snapshot["source_key"],
            "fingerprints": fingerprints,
            "reviews": {str(k): _review_projection(v) for k, v in reviews.items()},
            "approval_id": str(approval.id) if approval else None,
            "approval_version": operational.version if operational else None,
        }
    )
    return AmexApprovedInformePreview(
        "ready",
        informe=informe,
        expenses=expenses,
        operational_evidence=operational,
        approval_event=approval,
        source_key=aggregate,
        reviews=reviews,
        current_versions=versions,
        approval_version=operational.version if operational else None,
        fresh_review_ids=fresh,
        exceptions=exceptions,
    )


async def preview_amex_approved_informe(
    session: AsyncSession,
    *,
    informe_id: UUID,
    actor: Empleado,
) -> AmexApprovedInformePreview:
    """Read-only consolidated evidence; GET never writes accounting decisions."""
    with session.no_autoflush:
        return await _approved_informe_preview(
            session, informe_id=informe_id, actor=actor, locked=False
        )


class _PendingFinanceConfirmation(Exception):
    def __init__(self, result: AmexCutResult):
        self.result = result


async def confirm_amex_approved_informe_cut(
    session: AsyncSession,
    *,
    informe_id: UUID,
    actor: Empleado,
    expected_source_key: str,
    expected_versions: dict[str, int],
    expected_approval_id: UUID | None,
    expected_approval_version: int | None,
    decisions: list[dict[str, Any]],
    accounting_date: date,
    confirmed: bool,
    reason: str = "",
    adjustment: bool = False,
) -> AmexCutResult:
    """One financial confirmation, immutable provenance and complete atomic cut."""
    from .amex_report_evidence_service import build_amex_report_source_snapshot

    await lock_recognition(session)
    stored_actor = await _actor(session, actor)
    if stored_actor is None:
        return AmexCutResult("pending", "unauthorized_amex_accounting_actor")
    if not confirmed:
        return AmexCutResult("pending", "missing_amex_finance_confirmation")
    if not isinstance(accounting_date, date) or isinstance(accounting_date, datetime):
        return AmexCutResult("pending", "invalid_amex_accounting_date")
    normalized = []
    declared = {}
    try:
        for group in decisions:
            ids = sorted({str(UUID(str(i))) for i in group["expense_ids"]})
            if not ids or len(ids) != len(group["expense_ids"]):
                raise ValueError("duplicate IDs")
            treatment = group["treatment"]
            if treatment not in {"expense", "partner_receivable"}:
                raise ValueError("treatment")
            account = (
                str(UUID(str(group["debtor_account_id"])))
                if group.get("debtor_account_id")
                else None
            )
            item = {
                "expense_ids": ids,
                "treatment": treatment,
                "debtor_account_id": account,
                "reason": str(group.get("reason") or "").strip(),
            }
            for expense_id in ids:
                if expense_id in declared:
                    raise ValueError("overlap")
                declared[expense_id] = item
            normalized.append(item)
    except (KeyError, TypeError, ValueError):
        return AmexCutResult("pending", "invalid_amex_finance_decisions")
    normalized.sort(key=lambda g: g["expense_ids"])
    payload = {
        "informe_id": str(informe_id),
        "source_key": expected_source_key,
        "versions": expected_versions,
        "approval_id": str(expected_approval_id) if expected_approval_id else None,
        "approval_version": expected_approval_version,
        "decisions": normalized,
        "accounting_date": str(accounting_date),
        "adjustment": bool(adjustment),
        "reason": reason.strip(),
    }
    payload_hash = _receipt_hash(payload)
    previous_evidences = list(
        (
            await session.execute(
                select(AmexReportSourceEvidence)
                .where(
                    AmexReportSourceEvidence.informe_id == informe_id,
                    AmexReportSourceEvidence.kind == "finance_confirmation",
                )
                .order_by(AmexReportSourceEvidence.version.desc())
            )
        )
        .scalars()
        .all()
    )
    for evidence in previous_evidences:
        if evidence.request_payload_hash == payload_hash:
            cuts = list(
                (
                    await session.execute(
                        select(AmexAccountingCut).where(
                            AmexAccountingCut.informe_id == informe_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            same = next(
                (
                    c
                    for c in cuts
                    if c.snapshot_json.get("source_evidence_id") == str(evidence.id)
                ),
                None,
            )
            if same:
                return AmexCutResult(
                    "exists",
                    cut=same,
                    poliza=await session.get(
                        AccountingPoliza, same.accounting_poliza_id
                    ),
                )
    prior_cuts = list(
        (
            await session.execute(
                select(AmexAccountingCut).where(
                    AmexAccountingCut.informe_id == informe_id
                )
            )
        )
        .scalars()
        .all()
    )
    if not adjustment and any(c.kind == "initial" for c in prior_cuts):
        return AmexCutResult("pending", "amex_finance_confirmation_payload_conflict")
    prepared = await _approved_informe_preview(
        session, informe_id=informe_id, actor=stored_actor, locked=True
    )
    if prepared.status != "ready":
        return AmexCutResult("pending", prepared.reason)
    if (
        prepared.source_key != expected_source_key
        or prepared.current_versions != expected_versions
    ):
        return AmexCutResult("pending", "stale_amex_finance_preview")
    actual_approval = prepared.approval_event.id if prepared.approval_event else None
    if (
        actual_approval != expected_approval_id
        or prepared.approval_version != expected_approval_version
    ):
        return AmexCutResult("pending", "stale_amex_approval_evidence")
    full_ids = {str(e.id) for e in prepared.expenses}
    if set(expected_versions) != full_ids or not set(declared).issubset(full_ids):
        return AmexCutResult("pending", "incomplete_amex_finance_decisions")
    if (
        prepared.operational_evidence is None
        or prepared.exceptions.get("source") == "stale_amex_operational_source"
    ) and not reason.strip():
        return AmexCutResult("pending", "amex_current_attestation_requires_reason")
    if prepared.exceptions.get("activation"):
        return AmexCutResult("pending", prepared.exceptions["activation"])
    adoptions = []
    projections = []
    for expense in prepared.expenses:
        review = prepared.reviews.get(expense.id)
        decision = declared.get(str(expense.id))
        if decision is None:
            if review is None or expense.id not in prepared.fresh_review_ids:
                return AmexCutResult("pending", "incomplete_amex_finance_decisions")
            adoptions.append(_review_projection(review))
        elif review is not None:
            if expense.id in prepared.fresh_review_ids and (
                review.treatment == decision["treatment"]
                and str(review.debtor_account_id or "")
                == str(decision["debtor_account_id"] or "")
                and review.reason == decision["reason"]
            ):
                adoptions.append(_review_projection(review))
            else:
                projections.append(_review_projection(review))
    resulting_reviews = []
    adopted_ids = {r["expense_id"] for r in adoptions}
    for expense in prepared.expenses:
        if str(expense.id) in adopted_ids:
            continue
        decision = declared[str(expense.id)]
        old = prepared.reviews.get(expense.id)
        resulting_reviews.append(
            {
                "expense_id": str(expense.id),
                "version": old.version + 1 if old else 1,
                "actor_id": str(stored_actor.id),
                "treatment": decision["treatment"],
                "debtor_account_id": decision["debtor_account_id"],
                "reason": decision["reason"],
                "source_key": await accounting_review_source_key(
                    session,
                    expense,
                    treatment=decision["treatment"],
                    debtor_account_id=(
                        UUID(decision["debtor_account_id"])
                        if decision["debtor_account_id"]
                        else None
                    ),
                ),
            }
        )
    finance_evidence: Any = AmexReportSourceEvidence(
        id=uuid4(),
        informe_id=informe_id,
        kind="finance_confirmation",
        approval_id=None,
        operational_evidence_id=(
            prepared.operational_evidence.id if prepared.operational_evidence else None
        ),
        actor_id=stored_actor.id,
        created_at=datetime.now(timezone.utc),
        version=max([e.version for e in previous_evidences], default=0) + 1,
        source_key=build_amex_report_source_snapshot(prepared.expenses)["source_key"],
        snapshot_json=build_amex_report_source_snapshot(prepared.expenses),
        decisions_json={
            "payload": payload,
            "adopted_reviews": adoptions,
            "prior_review_projection": projections,
            "resulting_reviews": resulting_reviews,
        },
        request_payload_hash=payload_hash,
        reason=reason.strip() or None,
    )
    try:
        async with session.begin_nested():
            session.add(finance_evidence)
            await session.flush()
            adopted_ids = {r["expense_id"] for r in adoptions}
            for expense_id, decision in declared.items():
                if expense_id in adopted_ids:
                    continue
                result = await review_amex_partida(
                    session,
                    informe_id=informe_id,
                    expense_id=UUID(expense_id),
                    actor=stored_actor,
                    treatment=decision["treatment"],
                    debtor_account_id=(
                        UUID(decision["debtor_account_id"])
                        if decision["debtor_account_id"]
                        else None
                    ),
                    reason=decision["reason"],
                    source_evidence_id=finance_evidence.id,
                )
                if result.status == "pending":
                    raise _PendingFinanceConfirmation(result)
            current_reviews: list[Any] = list(
                (
                    await session.execute(
                        select(AmexAccountingReview).where(
                            AmexAccountingReview.expense_id.in_(
                                [e.id for e in prepared.expenses]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            observed = {str(r.expense_id): r for r in current_reviews}
            for expected in resulting_reviews:
                current = observed[expected["expense_id"]]
                if (
                    current.version != expected["version"]
                    or current.source_key != expected["source_key"]
                    or current.source_evidence_id != finance_evidence.id
                ):
                    raise _PendingFinanceConfirmation(
                        AmexCutResult("pending", "amex_finance_projection_conflict")
                    )
            result = await create_amex_accounting_cut(
                session,
                informe_id=informe_id,
                actor=stored_actor,
                expected_versions={
                    str(r.expense_id): r.version for r in current_reviews
                },
                accounting_date=accounting_date,
                adjustment=adjustment,
                reason=reason,
                source_evidence_id=finance_evidence.id,
            )
            if result.status == "pending":
                raise _PendingFinanceConfirmation(result)
    except _PendingFinanceConfirmation as pending:
        return pending.result
    return result
