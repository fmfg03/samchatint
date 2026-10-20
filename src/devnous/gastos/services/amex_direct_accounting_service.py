"""Whole-statement direct recognition and frozen COI exports; never commit."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from ..models import (
    AccountingClosePeriod,
    AccountingPolizaLine,
    AmexAccountingCut,
    AmexCardAccount,
    AmexDirectAccountingCut,
    AmexDirectCompletionReceipt,
    AmexDirectCutItem,
    AmexDirectPriorCoverage,
    AmexExpenseAccountSettlement,
    AmexExpenseAccountSettlementAllocation,
    AmexRecognitionConsumption,
    AmexRecognitionRepresentation,
    AmexStatementImportItem,
    BudgetConcept,
    CFDIReport,
    CuentaContable,
    ExpenseReport,
)
from .amex_accounting_cut_service import _freeze_cfdi, _freeze_lines
from .amex_accounting_posting_service import (
    ALLOWED_AMEX_LIABILITY_CODES,
    _credit_line,
    _debit_line,
    posting_is_balanced,
)
from .amex_direct_common import (
    DirectConflict,
    DirectResult,
    digest,
    load_items,
    load_statement,
    money,
    record_event,
)
from .amex_direct_effective_source_service import (
    effective_source_for_item,
    load_effective_sources,
)
from .amex_direct_matching_service import (
    _available,
    cfdi_identity,
    current_status_receipt,
    fiscal_source_hash,
)
from .amex_direct_no_cfdi_service import (
    active_decision,
    decision_snapshot,
    load_decision_heads,
    validate_decision,
)
from .amex_expense_account_settlement_service import (
    prepare_settlement,
    prior_source,
    selected_origin,
    settlement_lines,
)
from .amex_recognition_service import (
    activation_reason,
    fiscal_lines,
    load_expenses,
)
from .employee_debtor_accounting_service import _create_poliza


async def covered_source_proof(session: Any, expense: Any) -> Any:
    """Accept only explicit, posted canonical identity, never amount similarity."""
    if expense is None:
        return None
    consumption = (
        await session.execute(
            select(AmexRecognitionConsumption).where(
                AmexRecognitionConsumption.imported_expense_id == expense.id
            )
        )
    ).scalar_one_or_none()
    if consumption is None:
        consumption = (
            await session.execute(
                select(AmexRecognitionConsumption)
                .join(
                    AmexRecognitionRepresentation,
                    AmexRecognitionRepresentation.consumption_id
                    == AmexRecognitionConsumption.id,
                )
                .where(AmexRecognitionRepresentation.expense_id == expense.id)
            )
        ).scalar_one_or_none()
    if (
        consumption is None
        or consumption.accounting_poliza_id is None
        or consumption.binding_mode == "report_bridge"
    ):
        return None
    if consumption.binding_mode == "direct":
        proof = (
            await session.execute(
                select(AmexDirectCutItem.id).where(
                    AmexDirectCutItem.consumption_id == consumption.id,
                    AmexDirectCutItem.accounting_poliza_id
                    == consumption.accounting_poliza_id,
                )
            )
        ).first()
    else:
        cuts = list(
            (
                await session.execute(
                    select(AmexAccountingCut).where(
                        AmexAccountingCut.accounting_poliza_id
                        == consumption.accounting_poliza_id
                    )
                )
            )
            .scalars()
            .all()
        )
        proof = any(
            str(consumption.id) in (cut.snapshot_json.get("consumption_ids") or [])
            for cut in cuts
        )
    if not proof:
        raise DirectConflict("recognized_source_receipt_incomplete")
    if (
        money(consumption.amount) != money(expense.gasto_cantidad)
        or consumption.currency != expense.currency
    ):
        raise DirectConflict("recognized_source_identity_changed")
    return consumption


async def validate_direct_match(session: Any, item: Any) -> str | None:
    """Revalidate actual fiscal identity and observed status without SOAP."""
    from ..models import AmexStatementDirectContext

    if item.match_status != "matched" or not item.cfdi_id or not item.match_proof:
        return item.match_reason or "fiscal_match_required"
    source = await effective_source_for_item(session, item)
    expense = await session.get(ExpenseReport, source.imported_expense_id)
    context = await session.get(AmexStatementDirectContext, item.context_id)
    cfdi = await session.get(CFDIReport, item.cfdi_id, populate_existing=True)
    if not expense or not context or not cfdi or expense.estado_gasto != "activo":
        return "fiscal_source_missing"
    try:
        identity = cfdi_identity(cfdi)
    except DirectConflict as exc:
        return str(exc)
    if identity["rfc_receptor"] != context.receiver_rfc or digest(
        identity
    ) != item.match_proof.get("cfdi_hash"):
        return "fiscal_identity_changed"
    if (
        item.match_proof.get("source_hash")
        != fiscal_source_hash(item, context, expense, source)
        or expense.cfdi_report_id != item.cfdi_id
    ):
        return "fiscal_source_changed"
    receipt = await current_status_receipt(session, cfdi)
    if (
        not receipt
        or receipt.observed_status != "vigente"
        or receipt.id != item.status_receipt_id
    ):
        return "fresh_vigente_receipt_required"
    if item.merchant_alias_id and not item.match_proof.get("manual"):
        from ..models import AmexMerchantIdentityAlias

        alias = await session.get(
            AmexMerchantIdentityAlias, item.merchant_alias_id, populate_existing=True
        )
        if (
            not alias
            or not alias.active
            or alias.version != item.match_proof.get("alias_version")
            or alias.issuer_rfc != identity["rfc_emisor"]
        ):
            return "merchant_identity_changed"
    if not await _available(
        session, cfdi, expense, shared=bool(expense.cfdi_compartido_confirmado)
    ):
        return "cfdi_global_usage_conflict"
    return None


async def _prepare(
    session: Any, *, actor_id: UUID, import_id: UUID, locked: bool = False
) -> DirectResult:
    statement, state, context, config = await load_statement(
        session,
        actor_id,
        import_id,
        "confirmar" if locked else "ver",
        locked=locked,
        finance=locked,
    )
    cut = (
        await session.execute(
            select(AmexDirectAccountingCut).where(
                AmexDirectAccountingCut.import_id == import_id
            )
        )
    ).scalar_one_or_none()
    items = await load_items(session, import_id, locked=locked)
    sources = await load_effective_sources(session, import_id)
    heads = await load_decision_heads(session, items)
    card = await session.get(
        AmexCardAccount,
        statement.card_account_id,
        populate_existing=True,
        with_for_update=locked,
    )
    result = DirectResult(
        "ready",
        statement=statement,
        context=context,
        card=card,
        items=items,
        cut=cut,
        source_version=state.source_version,
    )
    completion = (
        await session.execute(
            select(AmexDirectCompletionReceipt).where(
                AmexDirectCompletionReceipt.import_id == import_id
            )
        )
    ).scalar_one_or_none()
    if completion:
        result.status = "completed"
        result.source_key = completion.source_hash
        result.progress["completion_receipt"] = {
            "id": str(completion.id),
            "created_at": str(completion.created_at),
            "source_hash": completion.source_hash,
        }
        result._completion = completion
        return result
    if cut:
        result.status = "existing"
        result.source_key = cut.source_hash
        return result
    expenses = (
        await load_expenses(
            session,
            [
                cast(UUID, sources[item.id].imported_expense_id)
                for item in items
                if sources[item.id].imported_expense_id
            ],
        )
        if locked
        else list(
            (
                await session.execute(
                    select(ExpenseReport)
                    .options(
                        selectinload(ExpenseReport.cfdi_report),
                        selectinload(ExpenseReport.cuenta_contable),
                        selectinload(ExpenseReport.cuenta_iva),
                    )
                    .where(
                        ExpenseReport.id.in_(
                            [
                                sources[item.id].imported_expense_id
                                for item in items
                                if sources[item.id].imported_expense_id
                            ]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    )
    by_id: dict[Any, Any] = {expense.id: expense for expense in expenses if expense}
    liability = (
        await session.get(
            CuentaContable,
            card.liability_cuenta_contable_id,
            populate_existing=True,
            with_for_update=locked,
        )
        if card
        else None
    )
    reasons = []
    issues = []
    proofs = {}
    journal = []
    facts = []
    new = []
    settlements = {}
    no_cfdi = {}
    prior_proofs = {}
    if not context:
        reasons.append("contexto_fiscal_pendiente")
    if (
        not card
        or not card.active
        or not liability
        or not liability.activo
        or liability.codigo not in ALLOWED_AMEX_LIABILITY_CODES
    ):
        reasons.append("invalid_amex_liability")
    if locked:
        # Statement/global AMEX locks precede all sorted historical ledger locks.
        selected = [await selected_origin(session, item) for item in items]
        line_ids = sorted({UUID(v["origin_line_id"]) for v in selected if v}, key=str)
        if line_ids:
            await session.execute(
                select(AccountingPolizaLine.id)
                .where(AccountingPolizaLine.id.in_(line_ids))
                .order_by(AccountingPolizaLine.id)
                .with_for_update()
            )
    for item in items:
        first_reason = len(reasons)
        expense = by_id.get(sources[item.id].imported_expense_id)
        receipt = await session.get(AmexStatementImportItem, item.import_item_id)
        decision = active_decision(item, heads)
        fact = {
            "item_id": item.id,
            "no_cfdi_decision": decision_snapshot(decision) if decision else None,
            "source_hash": item.source_hash,
            "source_version": item.source_version,
            "row": receipt.normalized_json if receipt else None,
            "effective_source": sources[item.id].source_json,
            "recovery_revision_id": sources[item.id].recovery_revision_id,
            "recovery_proof_hash": sources[item.id].recovery_proof_hash,
            "effective_period": (
                sources[item.id].period_start,
                sources[item.id].period_end,
            ),
        }
        if item.context_id != (
            context.id if context else None
        ) or item.context_version != (context.version if context else None):
            reasons.append("stale_item_context")
        if sources[item.id].movement_kind == "credit":
            if not item.credit_excluded:
                reasons.append("credit_exclusion_required")
        elif sources[item.id].movement_kind != "debit" or not expense:
            reasons.append("statement_row_unresolved")
        else:
            prior = None
            settlement = None
            try:
                prior = await prior_source(
                    session,
                    item=item,
                    source=sources[item.id],
                    context=context,
                    card=card,
                    config=config,
                    actor_id=actor_id,
                )
                if not prior:
                    settlement = await prepare_settlement(
                        session,
                        item=item,
                        context=context,
                        config=config,
                        actor_id=actor_id,
                        source=sources[item.id],
                        locked=locked,
                    )
            except DirectConflict as exc:
                reasons.append(str(exc))
            proof = prior["consumption"] if prior else None
            proofs[item.id] = proof
            if (prior or settlement) and decision:
                reasons.append("no_cfdi_historical_source_conflict")
            if prior:
                prior_proofs[item.id] = prior
            if settlement:
                settlements[item.id] = settlement
                fact["settlement"] = settlement
                if liability:
                    journal.extend(
                        settlement_lines(
                            settlement,
                            expense_id=expense.id,
                            amount=abs(money(sources[item.id].raw_signed_amount)),
                            liability=liability,
                        )
                    )
            if proof:
                fact["prior"] = {
                    "id": proof.id,
                    "poliza_id": proof.accounting_poliza_id,
                    "amount": proof.amount,
                    "card_id": proof.card_account_id,
                }
                if proof.card_account_id != statement.card_account_id:
                    reasons.append("recognized_source_card_mismatch")
            elif not settlement:
                if decision:
                    try:
                        validate_decision(
                            statement,
                            context,
                            item,
                            sources[item.id],
                            expense,
                            decision,
                        )
                        if await covered_source_proof(session, expense):
                            raise DirectConflict("recognized_source_immutable")
                        no_cfdi[item.id] = decision
                    except DirectConflict as exc:
                        reasons.append(str(exc))
                else:
                    reason = await validate_direct_match(session, item)
                    if reason:
                        reasons.append(reason)
                if item.budget_status != "assigned" or not item.budget_assignment_id:
                    reasons.append("budget_assignment_required")
                concept = (
                    await session.get(
                        BudgetConcept,
                        item.budget_concept_id,
                        populate_existing=True,
                        with_for_update=locked,
                    )
                    if item.budget_concept_id
                    else None
                )
                if (
                    not concept
                    or not concept.active
                    or concept.budget_direction != "expense"
                    or not concept.cuenta_contable_id
                    or concept.cuenta_contable_id != expense.cuenta_contable_id
                    or expense.contra_cuenta_contable_id
                    != (liability.id if liability else None)
                ):
                    reasons.append("budget_account_mapping_required")
                assignment = (
                    (
                        await session.execute(
                            text(
                                """SELECT a.*,l.budget_concept_id,l.tournament_id,
                                l.tournament_code,v.edition_year,
                                v.status AS version_status
                                FROM budget_movement_assignments a
                                JOIN budget_lines l ON l.id=a.budget_line_id
                                JOIN budget_versions v
                                ON v.id=a.budget_version_id WHERE a.id=:id"""
                                + (" FOR UPDATE OF a,l,v" if locked else "")
                            ),
                            {"id": item.budget_assignment_id},
                        )
                    )
                    .mappings()
                    .first()
                    if item.budget_assignment_id
                    else None
                )
                if (
                    not assignment
                    or str(assignment["movement_key"]) != f"expense:{expense.id}"
                    or assignment["budget_line_id"] != item.budget_line_id
                    or assignment["budget_version_id"] != item.budget_version_id
                    or assignment["budget_concept_id"] != item.budget_concept_id
                    or assignment["edition_year"] != item.edition_year
                ):
                    reasons.append("budget_assignment_changed")
                elif assignment["version_status"] not in {
                    "approved",
                    "frozen",
                } and not (
                    config.get("allow_draft_reforecast") is True
                    and assignment["version_status"] in {"draft", "reforecast"}
                ):
                    reasons.append("budget_version_not_operational")
                cfdi = (
                    await session.get(CFDIReport, item.cfdi_id)
                    if item.cfdi_id
                    else None
                )
                fact.update(
                    expense={
                        c.name: getattr(expense, c.name)
                        for c in expense.__table__.columns
                    },
                    cfdi=cfdi_identity(cfdi) if cfdi else None,
                    assignment=dict(assignment) if assignment else None,
                    concept=(
                        {
                            c.name: getattr(concept, c.name)
                            for c in concept.__table__.columns
                        }
                        if concept
                        else None
                    ),
                )
                if not decision:
                    new.append(expense)
        for code in dict.fromkeys(reasons[first_reason:]):
            budget_issue = code.startswith("budget_")
            issues.append(
                {
                    "item_id": str(item.id),
                    "row_number": sources[item.id].source_json.get("row_number"),
                    "reason": code,
                    "responsible": (
                        "Control Presupuestal" if budget_issue else "Contabilidad"
                    ),
                    "next_action": (
                        "Revisar clasificación y asignación de partida presupuestal"
                        if budget_issue
                        else "Revisar evidencia y resolver el bloqueo del cargo"
                    ),
                }
            )
        facts.append(fact)
    if no_cfdi:
        reason = await activation_reason(
            session, [by_id[sources[i].imported_expense_id] for i in no_cfdi]
        )
        if reason:
            reasons.append(reason)
        for item in items:
            if item.id not in no_cfdi:
                continue
            expense = by_id[sources[item.id].imported_expense_id]
            account = (
                await session.get(
                    CuentaContable,
                    expense.cuenta_contable_id,
                    populate_existing=True,
                    with_for_update=locked,
                )
                if expense.cuenta_contable_id
                else None
            )
            if not account or not account.activo or not account.codigo or not liability:
                reasons.append("budget_account_mapping_required")
                continue
            journal.extend(
                no_cfdi_lines(
                    item,
                    expense,
                    no_cfdi[item.id],
                    account,
                    liability,
                    sources[item.id],
                )
            )
    if new:
        reason = await activation_reason(session, new)
        if reason:
            reasons.append(reason)
        if not reasons:
            lines, total, reason = await fiscal_lines(
                session, new, strict_direct=True, lock=locked
            )
            if reason:
                reasons.append(reason)
            else:
                assert liability is not None
                journal = [*journal, *(lines or [])]
                item_by_expense = {
                    str(sources[i.id].imported_expense_id): i
                    for i in items
                    if sources[i.id].imported_expense_id
                }
                for line in journal:
                    if (line.get("raw_row_json") or {}).get("coverage_kind") in {
                        "settlement",
                        "new_no_cfdi",
                    }:
                        continue
                    metadata = line.get("raw_row_json") or {}
                    item = item_by_expense[metadata["expense_id"]]
                    expense = by_id[sources[item.id].imported_expense_id]
                    line["raw_row_json"] = {
                        **metadata,
                        "source_kind": "amex_direct",
                        "budget_assignment_id": str(item.budget_assignment_id),
                        "budget_concept_id": str(item.budget_concept_id),
                        "tournament_id": (
                            str(item.tournament_id) if item.tournament_id else None
                        ),
                        "project": expense.proyecto,
                        "edicion": item.edition_year,
                    }
                for expense in new:
                    journal.append(
                        _credit_line(
                            code=liability.codigo,
                            account_id=liability.id,
                            amount=money(expense.gasto_cantidad),
                            concept=expense.concepto,
                            meta={
                                "expense_id": str(expense.id),
                                "source_kind": "amex_direct",
                                "budget_concept_id": str(expense.budget_concept_id),
                                "budget_assignment_id": str(
                                    item_by_expense[
                                        str(expense.id)
                                    ].budget_assignment_id
                                ),
                                "project": expense.proyecto,
                            },
                            movement="haber_amex",
                        )
                    )
                if not posting_is_balanced(journal):
                    reasons.append("unbalanced_direct_journal")
    elif not reasons and not settlements and not prior_proofs and not no_cfdi:
        reasons.append("statement_has_no_debit_activity")
    if journal and not posting_is_balanced(journal):
        reasons.append("unbalanced_direct_journal")
    from .amex_direct_fiscal_evidence_service import (
        read_direct_cfdi_fiscal_evidence_batch,
    )

    fiscal_ids = {expense.cfdi_report_id for expense in new if expense.cfdi_report_id}
    fiscal_cfdis = (
        list(
            (
                await session.execute(
                    select(CFDIReport).where(CFDIReport.id.in_(fiscal_ids))
                )
            )
            .scalars()
            .all()
        )
        if fiscal_ids
        else []
    )
    evidence_by_cfdi = (
        await read_direct_cfdi_fiscal_evidence_batch(
            session,
            fiscal_cfdis,
            receiver_rfc=context.receiver_rfc if context else "",
            company_code=context.company_code if context else None,
        )
        if fiscal_cfdis
        else {}
    )
    fiscal_evidence = {
        str(item.id): evidence_by_cfdi[
            str(by_id[sources[item.id].imported_expense_id].cfdi_report_id)
        ]
        for item in items
        if sources[item.id].imported_expense_id in by_id
        and str(by_id[sources[item.id].imported_expense_id].cfdi_report_id)
        in evidence_by_cfdi
    }
    result.source_key = digest(
        {
            "fiscal_evidence": fiscal_evidence,
            "context": context.payload_hash if context else None,
            "version": state.source_version,
            "facts": facts,
            "card": (
                {c.name: getattr(card, c.name) for c in card.__table__.columns}
                if card
                else None
            ),
            "liability": (
                {
                    c.name: getattr(liability, c.name)
                    for c in liability.__table__.columns
                }
                if liability
                else None
            ),
            "lines": journal,
        }
    )
    result.status = "pending" if reasons else "ready"
    result.reason = reasons[0] if reasons else None
    result.progress = {
        "exceptions": list(dict.fromkeys(reasons)),
        "issues": issues,
        "global_issues": [
            {
                "reason": code,
                "responsible": "Contabilidad",
                "next_action": "Revisar configuración y evidencia del estado",
            }
            for code in dict.fromkeys(reasons)
            if code not in {issue["reason"] for issue in issues}
        ],
        "original_period_start": statement.period_start,
        "original_period_end": statement.period_end,
        "effective_period_start": (
            min(v.period_start for v in sources.values())
            if sources
            else statement.period_start
        ),
        "effective_period_end": (
            max(v.period_end for v in sources.values())
            if sources
            else statement.period_end
        ),
        "fiscal_evidence": fiscal_evidence,
        "no_cfdi_count": len(no_cfdi),
        "new_count": len(new) + len(no_cfdi),
        "settlement_count": len(settlements),
        "completion_only": bool(prior_proofs)
        and not new
        and not settlements
        and not no_cfdi,
        "prior_count": sum(bool(v) for v in proofs.values()),
        "lines": _freeze_lines(journal),
    }
    result.progress["no_cfdi_decisions"] = {
        str(k): decision_snapshot(v) for k, v in no_cfdi.items()
    }
    result._settlements = settlements
    result._prior_proofs = prior_proofs
    result._sources = sources
    result._expenses = by_id
    result._proofs = proofs
    result._lines = journal
    result._liability = liability
    return result


async def preview_direct_accounting(
    session: Any, *, actor_id: UUID, import_id: UUID
) -> DirectResult:
    """Pure local preview; no status query, mutation or implicit autoflush."""
    with session.no_autoflush:
        return await _prepare(session, actor_id=actor_id, import_id=import_id)


async def confirm_direct_accounting(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    expected_source_key: str,
    expected_source_version: int,
    accounting_date: date,
    confirmed: bool,
    reason: str = "",
    idempotency_key: str,
) -> DirectResult:
    payload = {
        "source_key": expected_source_key,
        "source_version": expected_source_version,
        "accounting_date": str(accounting_date),
        "reason": reason.strip(),
        "idempotency_key": idempotency_key,
    }
    result = await _prepare(
        session, actor_id=actor_id, import_id=import_id, locked=True
    )
    sources = result._sources
    if result.status == "completed":
        if result._completion.payload_hash == digest(payload):
            return result
        return DirectResult(
            "pending", "direct_completion_request_conflict", progress=result.progress
        )
    if result.cut:
        if result.cut.payload_hash == digest(payload):
            return result
        return DirectResult("pending", "direct_cut_request_conflict", cut=result.cut)
    if result.status != "ready":
        return result
    if not confirmed or not idempotency_key:
        return DirectResult("pending", "direct_confirmation_required")
    if (
        result.source_key != expected_source_key
        or result.source_version != expected_source_version
    ):
        return DirectResult("pending", "stale_direct_source")
    if (
        not min(source.period_start for source in sources.values())
        <= accounting_date
        <= max(source.period_end for source in sources.values())
    ):
        return DirectResult("pending", "accounting_date_outside_statement")
    from samchat.budgets.service import _date_in_edition_year

    if any(
        not _date_in_edition_year(accounting_date, item.edition_year)
        for item in result.items
        if sources[item.id].movement_kind == "debit"
        and not result._proofs.get(item.id)
        and item.id not in result._settlements
    ):
        return DirectResult("pending", "accounting_date_outside_budget_edition")
    if result.progress.get("completion_only"):
        return await _complete_prior_statement(
            session, result=result, actor_id=actor_id, payload=payload
        )
    periods = list(
        (
            await session.execute(
                select(AccountingClosePeriod)
                .where(
                    AccountingClosePeriod.fiscal_year == accounting_date.year,
                    AccountingClosePeriod.fiscal_month == accounting_date.month,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if not periods:
        return DirectResult("pending", "explicit_open_accounting_period_required")
    if any(period.status != "open" for period in periods):
        return DirectResult("pending", "closed_amex_accounting_period")
    async with session.begin_nested():
        poliza = await _create_poliza(
            session,
            origen="amex_direct",
            numero_poliza=f"AMEX-DIRECT-{str(import_id)[:8]}",
            fecha=datetime.combine(accounting_date, datetime.min.time()),
            beneficiario_nombre=result.card.cardholder_name,
            concepto=f"Estado AMEX {import_id}",
            lines=result._lines,
        )
        await session.flush()
        memberships = []
        partidas = []
        for item in result.items:
            expense = result._expenses.get(sources[item.id].imported_expense_id)
            prior = result._proofs.get(item.id)
            coverage = (
                "credit_exclusion"
                if sources[item.id].movement_kind == "credit"
                else (
                    "prior"
                    if prior
                    else (
                        "settlement"
                        if item.id in result._settlements
                        else (
                            "new_no_cfdi"
                            if str(item.id)
                            in result.progress.get("no_cfdi_decisions", {})
                            else "new"
                        )
                    )
                )
            )
            consumption = prior
            if coverage in {"new", "new_no_cfdi"}:
                assert expense is not None
                consumption = AmexRecognitionConsumption(
                    id=uuid4(),
                    source_kind="statement",
                    binding_mode="direct",
                    imported_expense_id=expense.id,
                    card_account_id=result.card.id,
                    amount=money(expense.gasto_cantidad),
                    currency="MXN",
                    economic_date=expense.fecha.date(),
                    liability_account_id=result._liability.id,
                    liability_code=result._liability.codigo,
                    classification_json={
                        "budget_concept_id": str(item.budget_concept_id),
                        "budget_assignment_id": str(item.budget_assignment_id),
                        "context_id": str(result.context.id),
                        **(
                            {"no_cfdi_decision_id": str(item.no_cfdi_decision_id)}
                            if coverage == "new_no_cfdi"
                            else {}
                        ),
                    },
                    accounting_poliza_id=poliza.id,
                    actor_id=actor_id,
                )
                session.add(consumption)
                await session.flush()
                session.add(
                    AmexRecognitionRepresentation(
                        consumption_id=consumption.id,
                        expense_id=expense.id,
                        role="statement",
                    )
                )
            item.accounting_status = (
                "recognized" if coverage != "credit_exclusion" else "excluded"
            )
            lines = [
                line
                for line in result._lines
                if (line.get("raw_row_json") or {}).get("expense_id")
                == str(sources[item.id].imported_expense_id)
            ]
            retention_proof = []
            if expense and coverage == "new":
                from .expense_accounting_service import build_expense_accounting_preview

                preview = await build_expense_accounting_preview(
                    session, expense, strict_direct=True
                )
                retention_proof = [
                    {
                        "impuesto": tax["impuesto"],
                        "importe": str(money(tax["importe"])),
                        "cuenta_contable_id": tax["account"]["cuenta_contable_id"],
                        "cuenta_codigo": tax["account"]["codigo"],
                    }
                    for tax in (preview.get("taxes") or {}).get("retenciones", [])
                    if money(tax["importe"]) > 0
                ]
                if retention_proof:
                    from ..models import AmexStatementDirectState

                    before_mapping = dict(expense.retencion_cuentas_json or {})
                    mapping = dict(before_mapping)
                    for tax in retention_proof:
                        existing = str(mapping.get(tax["impuesto"]) or "").strip()
                        if existing and existing != tax["cuenta_contable_id"]:
                            raise DirectConflict("retention_account_mapping_conflict")
                        mapping[tax["impuesto"]] = tax["cuenta_contable_id"]
                    if mapping != before_mapping:
                        expense.retencion_cuentas_json = mapping
                        state = await session.get(AmexStatementDirectState, import_id)
                        await record_event(
                            session,
                            item,
                            state,
                            actor_id,
                            "canonical_retention_mapping",
                            {
                                "source": "canonical_fiscal_preview",
                                "before": before_mapping,
                                "resolved": retention_proof,
                            },
                            bump=False,
                        )
            fact = {
                "no_cfdi_decision_id": (
                    str(item.no_cfdi_decision_id) if coverage == "new_no_cfdi" else None
                ),
                "taxes": None,
                "no_cfdi_decision": result.progress.get("no_cfdi_decisions", {}).get(
                    str(item.id)
                ),
                "context_version": result.context.version,
                "source_version": item.source_version,
                "source_hash": item.source_hash,
                "settlement_proof": json.loads(
                    json.dumps(result._settlements.get(item.id), default=str)
                ),
                "fiscal_retention_proof": retention_proof,
                "accounting_poliza_id": (
                    str(consumption.accounting_poliza_id)
                    if consumption
                    else str(poliza.id) if coverage == "settlement" else None
                ),
                "direct_item_id": str(item.id),
                "expense_id": (
                    str(sources[item.id].imported_expense_id) if expense else None
                ),
                "amount": str(money(expense.gasto_cantidad)) if expense else None,
                "bank_signed_amount": (
                    str(sources[item.id].raw_signed_amount)
                    if sources[item.id].raw_signed_amount is not None
                    else None
                ),
                "concepto": (
                    expense.concepto
                    if expense
                    else sources[item.id].source_json.get("description")
                ),
                "coverage_kind": coverage,
                "consumption_id": str(consumption.id) if consumption else None,
                "cfdi": (
                    _freeze_cfdi(expense.cfdi_report)
                    if expense and coverage == "new"
                    else (
                        result._settlements[item.id]["origin_cfdi_json"]
                        if coverage == "settlement"
                        else None
                    )
                ),
                "journal_lines": _freeze_lines(lines),
                "source": sources[item.id].source_json,
                "recovery_revision_id": (
                    str(sources[item.id].recovery_revision_id)
                    if sources[item.id].recovery_revision_id
                    else None
                ),
                "recovery_proof_hash": sources[item.id].recovery_proof_hash,
                "context_id": str(result.context.id),
                "budget_assignment_id": (
                    str(item.budget_assignment_id)
                    if item.budget_assignment_id
                    else None
                ),
            }
            partidas.append(fact)
            memberships.append((item, consumption, coverage, fact))
        await session.flush()
        cut = AmexDirectAccountingCut(
            id=uuid4(),
            import_id=import_id,
            context_id=result.context.id,
            context_version=result.context.version,
            source_version=result.source_version,
            source_hash=result.source_key,
            payload_hash=digest(payload),
            accounting_poliza_id=poliza.id,
            accounting_date=accounting_date,
            actor_id=actor_id,
            snapshot_json={
                "version": 1,
                "kind": "direct",
                "partidas": partidas,
                "lines": _freeze_lines(result._lines),
                "request": payload,
                "original_period": {
                    "start": str(result.statement.period_start),
                    "end": str(result.statement.period_end),
                },
                "effective_period": {
                    "start": str(
                        min(source.period_start for source in sources.values())
                    ),
                    "end": str(max(source.period_end for source in sources.values())),
                },
                "recovery_revision_ids": sorted(
                    {
                        str(source.recovery_revision_id)
                        for source in sources.values()
                        if source.recovery_revision_id
                    }
                ),
                "fiscal_evidence_version": "amex_direct_fiscal_evidence_v1",
                "fiscal_evidence": result.progress.get("fiscal_evidence", {}),
                "context": {
                    c.name: str(getattr(result.context, c.name))
                    for c in result.context.__table__.columns
                },
            },
        )
        session.add(cut)
        await session.flush()
        for item, consumption, coverage, fact in memberships:
            session.add(
                AmexDirectCutItem(
                    cut_id=cut.id,
                    direct_item_id=item.id,
                    import_item_id=item.import_item_id,
                    imported_expense_id=sources[item.id].imported_expense_id,
                    consumption_id=consumption.id if consumption else None,
                    accounting_poliza_id=(
                        consumption.accounting_poliza_id
                        if consumption
                        else poliza.id if coverage == "settlement" else None
                    ),
                    coverage_kind=coverage,
                    no_cfdi_decision_id=(
                        item.no_cfdi_decision_id if coverage == "new_no_cfdi" else None
                    ),
                    source_hash=item.source_hash,
                    snapshot_json=fact,
                )
            )
        await session.flush()
        await _persist_coverage(
            session,
            result=result,
            cut=cut,
            poliza=poliza,
            actor_id=actor_id,
            payload=payload,
        )
        # Force deferred owner guards inside this savepoint, not caller commit.
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    return DirectResult(
        "recognized",
        statement=result.statement,
        context=result.context,
        card=result.card,
        items=result.items,
        cut=cut,
        poliza=poliza,
        source_key=result.source_key,
        source_version=result.source_version,
    )


async def _persist_prior(
    session: Any,
    *,
    result: DirectResult,
    item: Any,
    actor_id: UUID,
    cut: Any = None,
    completion: Any = None,
) -> None:
    prior = result._prior_proofs[item.id]
    consumption = prior["consumption"]
    session.add(
        AmexDirectPriorCoverage(
            import_id=result.statement.id,
            direct_item_id=item.id,
            imported_expense_id=result._sources[item.id].imported_expense_id,
            consumption_id=consumption.id,
            representation_id=prior["representation"].id,
            original_cut_id=prior["cut"].id if prior["cut"] else None,
            original_direct_cut_id=(
                prior["direct_cut"].id if prior["direct_cut"] else None
            ),
            accounting_poliza_id=consumption.accounting_poliza_id,
            card_account_id=result.card.id,
            context_id=result.context.id,
            amount=money(consumption.amount),
            currency=consumption.currency,
            cut_id=cut.id if cut else None,
            completion_receipt_id=completion.id if completion else None,
            source_hash=item.source_hash,
            proof_json={
                "selection": prior.get("selection"),
                "consumption_id": str(consumption.id),
            },
            actor_id=actor_id,
        )
    )


async def _persist_coverage(
    session: Any,
    *,
    result: DirectResult,
    cut: Any,
    poliza: Any,
    actor_id: UUID,
    payload: dict,
) -> None:
    ledger = (
        (
            await session.execute(
                select(AccountingPolizaLine).where(
                    AccountingPolizaLine.poliza_id == poliza.id
                )
            )
        )
        .scalars()
        .all()
    )
    for item in result.items:
        if item.id in result._prior_proofs:
            await _persist_prior(
                session, result=result, item=item, actor_id=actor_id, cut=cut
            )
        proof = result._settlements.get(item.id)
        if not proof:
            continue
        source = result._sources[item.id]
        settlement = AmexExpenseAccountSettlement(
            import_id=result.statement.id,
            direct_item_id=item.id,
            imported_expense_id=source.imported_expense_id,
            context_id=result.context.id,
            card_account_id=result.card.id,
            cut_id=cut.id,
            accounting_poliza_id=poliza.id,
            amount=abs(source.raw_signed_amount),
            currency="MXN",
            source_hash=item.source_hash,
            payload_hash=digest(payload),
            actor_id=actor_id,
            reason=proof["selection"]["reason"],
            proof_json=json.loads(json.dumps(proof, default=str)),
        )
        session.add(settlement)
        await session.flush()
        own = [
            entry
            for entry in ledger
            if (entry.raw_row_json or {}).get("expense_id")
            == str(source.imported_expense_id)
        ]
        debit = next(entry for entry in own if money(entry.debe or 0) > 0)
        credit = next(entry for entry in own if money(entry.haber or 0) > 0)
        session.add(
            AmexExpenseAccountSettlementAllocation(
                settlement_id=settlement.id,
                **{
                    k: proof[k]
                    for k in (
                        "origin_poliza_id",
                        "origin_line_id",
                        "origin_expense_id",
                        "origin_document_id",
                        "origin_cuenta_gastos_id",
                        "origin_account_id",
                        "origin_account_code",
                    )
                },
                destination_debit_line_id=debit.id,
                destination_credit_line_id=credit.id,
                amount=abs(source.raw_signed_amount),
                proof_json=settlement.proof_json,
            )
        )
    await session.flush()


async def _complete_prior_statement(
    session: Any, *, result: DirectResult, actor_id: UUID, payload: dict
) -> DirectResult:
    import json

    with session.no_autoflush:
        facts = []
        for item in result.items:
            source = result._sources[item.id]
            prior = result._prior_proofs.get(item.id)
            facts.append(
                {
                    "direct_item_id": str(item.id),
                    "source_hash": item.source_hash,
                    "source": source.source_json,
                    "effective_source": source.source_json,
                    "coverage_kind": "prior" if prior else "credit_exclusion",
                    "consumption_id": str(prior["consumption"].id) if prior else None,
                    "accounting_poliza_id": (
                        str(prior["consumption"].accounting_poliza_id)
                        if prior
                        else None
                    ),
                }
            )
    async with session.begin_nested():
        receipt = AmexDirectCompletionReceipt(
            import_id=result.statement.id,
            context_id=result.context.id,
            context_version=result.context.version,
            source_version=result.source_version,
            source_hash=result.source_key,
            payload_hash=digest(payload),
            actor_id=actor_id,
            snapshot_json=json.loads(
                json.dumps(
                    {
                        "version": 1,
                        "kind": "prior_only",
                        "partidas": facts,
                        "request": payload,
                    },
                    default=str,
                )
            ),
        )
        # Freeze projection state before inserting the receipt.
        for item in result.items:
            item.accounting_status = (
                "recognized" if item.id in result._prior_proofs else "excluded"
            )
        await session.flush()
        session.add(receipt)
        await session.flush()
        for item in result.items:
            if item.id in result._prior_proofs:
                await _persist_prior(
                    session,
                    result=result,
                    item=item,
                    actor_id=actor_id,
                    completion=receipt,
                )
        await session.flush()
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    result.status = "completed"
    result.progress["completion_receipt"] = {
        "id": str(receipt.id),
        "created_at": str(receipt.created_at),
        "source_hash": receipt.source_hash,
    }
    return result


async def export_direct_cut(
    session: Any, *, actor_id: UUID, import_id: UUID, format: str
) -> tuple[bytes, str, str]:
    """Export frozen evidence after current scope validation, without XML reads."""
    from .amex_cut_export_service import cut_expense_cfdis
    from .coi_poliza_exporter import (
        generate_coi_poliza_csv,
        generate_coi_poliza_xlsx,
        generate_coi_poliza_zip,
    )

    with session.no_autoflush:
        statement, state, context, config = await load_statement(
            session, actor_id, import_id, "exportar", finance=True
        )
        cut = (
            await session.execute(
                select(AmexDirectAccountingCut).where(
                    AmexDirectAccountingCut.import_id == import_id
                )
            )
        ).scalar_one_or_none()
        if not cut:
            raise DirectConflict("direct_cut_required")
        rows = cut_expense_cfdis(cut)
        generators = {
            "csv": (generate_coi_poliza_csv, "text/csv"),
            "xlsx": (
                generate_coi_poliza_xlsx,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            "zip": (generate_coi_poliza_zip, "application/zip"),
        }
        if format not in generators:
            raise DirectConflict("unsupported_export_format")
        generate, mime = generators[format]
        return cast(Any, generate)(rows), f"AMEX_directo_{cut.id}.{format}", mime


def no_cfdi_lines(
    item: Any, expense: Any, decision: Any, account: Any, liability: Any, source: Any
) -> list[dict[str, Any]]:
    """Post full bank amount; no tax base, withholding, tip or fiscal identity."""
    metadata = {
        "expense_id": str(expense.id),
        "direct_item_id": str(item.id),
        "source_kind": "amex_direct",
        "coverage_kind": "new_no_cfdi",
        "budget_concept_id": str(item.budget_concept_id),
        "budget_assignment_id": str(item.budget_assignment_id),
        "no_cfdi_decision_id": str(decision.id),
        "treatment": decision.treatment,
        "project": expense.proyecto,
        "edicion": item.edition_year,
    }
    amount = abs(money(source.raw_signed_amount))
    return [
        _debit_line(
            code=account.codigo,
            account_id=account.id,
            amount=amount,
            concept=expense.concepto,
            meta=metadata,
            movement="debe_gasto_sin_cfdi",
        ),
        _credit_line(
            code=liability.codigo,
            account_id=liability.id,
            amount=amount,
            concept=expense.concepto,
            meta=metadata,
            movement="haber_amex",
        ),
    ]
