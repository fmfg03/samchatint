"""Append-only fiscal context and scoped, side-effect-free direct views."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from ..models import (
    AmexCardAccount,
    AmexCFDIStatusReceipt,
    AmexDirectAccountingCut,
    AmexDirectCompletionReceipt,
    AmexDirectCutItem,
    AmexDirectPriorCoverage,
    AmexExpenseAccountSettlement,
    AmexStatementDirectContext,
    BudgetConcept,
    CFDIReport,
    CuentaContable,
    ExpenseReport,
    Tournament,
)
from .amex_direct_common import (
    DirectAccessError,
    DirectConflict,
    DirectResult,
    actor_scope,
    authorize,
    configuration_for,
    configured_scopes,
    digest,
    load_budget_items,
    load_items,
    load_statement,
    normalize_rfc,
    record_event,
    require_unfrozen,
)
from .amex_direct_effective_source_service import (
    load_effective_sources,
)
from .amex_direct_no_cfdi_service import active_decision, load_decision_heads


async def set_direct_context(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    company_code: str,
    receiver_rfc: str,
    currency: str,
    profile: str | None,
    expected_source_version: int,
    reason: str,
    confirmed: bool,
) -> DirectResult:
    from .amex_direct_matching_service import release_direct_application

    await authorize(session, actor_id, "contexto", finance=True)
    receiver = normalize_rfc(receiver_rfc)
    statement, state, old, old_config = await load_statement(
        session,
        actor_id,
        import_id,
        "contexto",
        locked=True,
        finance=True,
        requested_company_code=company_code,
        requested_receiver_rfc=receiver,
    )
    config = configuration_for(statement.card_account_id, company_code, receiver)
    if config is None:
        return DirectResult("pending", "contexto_fiscal_pendiente")
    await authorize(session, actor_id, "contexto", config=config, finance=True)
    if state.source_version != expected_source_version:
        return DirectResult("pending", "stale_source_version")
    if not confirmed:
        return DirectResult("pending", "context_confirmation_required")
    if str(currency).strip().upper() != "MXN":
        return DirectResult("pending", "unsupported_direct_currency")
    if profile != statement.summary_json.get("profile"):
        return DirectResult("pending", "csv_profile_change_requires_review")
    await require_unfrozen(session, import_id)
    payload = {
        "company_code": company_code,
        "receiver_rfc": receiver,
        "currency": "MXN",
        "profile": profile,
        "config_hash": digest(config),
        "entity_code": config["entity_code"],
    }
    key = digest(payload)
    if old and old.payload_hash == key:
        return DirectResult(
            "exists",
            statement=statement,
            context=old,
            source_version=state.source_version,
        )
    if old and not reason.strip():
        return DirectResult("pending", "context_correction_reason_required")
    items = await load_items(session, import_id, locked=True)
    sources = await load_effective_sources(session, import_id)
    heads = await load_decision_heads(session, items)
    if any(active_decision(item, heads) for item in items):
        return DirectResult("pending", "no_cfdi_revoke_required")
    if old:
        from .amex_direct_accounting_service import covered_source_proof

        for item in items:
            expense = (
                await session.get(ExpenseReport, sources[item.id].imported_expense_id)
                if sources[item.id].imported_expense_id
                else None
            )
            if await covered_source_proof(session, expense):
                return DirectResult("pending", "recognized_source_context_locked")
    async with session.begin_nested():
        context = AmexStatementDirectContext(
            id=uuid4(),
            import_id=import_id,
            version=(old.version + 1 if old else 1),
            entity_code=config["entity_code"],
            company_code=company_code,
            receiver_rfc=receiver,
            currency="MXN",
            profile=profile,
            config_hash=digest(config),
            payload_hash=key,
            actor_id=actor_id,
            reason=reason.strip() or None,
        )
        session.add(context)
        await session.flush()
        state.context_id = context.id
        state.context_version = context.version
        state.source_version += 1
        state.progress_json = None
        for item in items:
            prior = {
                key: getattr(item, key, None)
                for key in (
                    "context_id",
                    "context_version",
                    "source_version",
                    "source_hash",
                    "cfdi_id",
                    "match_status",
                    "tournament_id",
                    "project_code",
                    "edition_year",
                    "budget_concept_id",
                    "budget_line_id",
                    "budget_version_id",
                    "budget_assignment_id",
                    "budget_status",
                )
            }
            from .amex_direct_budget_service import release_direct_budget_mapping

            prior["released_mapping"] = await release_direct_budget_mapping(
                session, item, statement.card_account_id
            )
            if item.cfdi_id:
                await release_direct_application(
                    session, item, actor_id=actor_id, reason=reason
                )
            item.context_id = context.id
            item.context_version = context.version
            item.source_version += 1
            item.cfdi_id = None
            item.status_receipt_id = None
            item.merchant_alias_id = None
            item.match_status = "pending"
            item.match_reason = (
                "classification_context_changed" if old else "fiscal_identity_pending"
            )
            item.match_proof = None
            item.tournament_id = None
            item.project_code = None
            item.edition_year = None
            item.budget_concept_id = None
            item.budget_line_id = None
            item.budget_version_id = None
            item.budget_assignment_id = None
            item.budget_status = "unclassified"
            await record_event(
                session,
                item,
                state,
                actor_id,
                "context_revision",
                {"prior": prior, "context_id": context.id, "reason": reason},
                bump=False,
            )
        await session.flush()
    return DirectResult(
        "ready",
        statement=statement,
        context=context,
        source_version=state.source_version,
    )


async def list_direct_statement(
    session: Any, *, actor_id: UUID, import_id: UUID, budget: bool = False
) -> DirectResult:
    with session.no_autoflush:
        statement, state, context, config = await load_statement(
            session, actor_id, import_id, "ver", budget=budget
        )
        card = await session.get(AmexCardAccount, statement.card_account_id)
        items = (
            await load_budget_items(session, import_id, config, actor_id)
            if budget
            else await load_items(session, import_id)
        )
        sources = await load_effective_sources(session, import_id)
        ledger_by_item: dict[Any, list[Any]] = {}
        coverage_by_item: dict[Any, str] = {}
        for model in (AmexDirectPriorCoverage, AmexExpenseAccountSettlement):
            rows = (
                (
                    await session.execute(
                        select(model).where(model.import_id == import_id)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                ledger_by_item.setdefault(row.direct_item_id, []).append(
                    row.accounting_poliza_id
                )
                coverage_by_item[row.direct_item_id] = (
                    "settlement" if model is AmexExpenseAccountSettlement else "prior"
                )
        cut_rows = (
            (
                await session.execute(
                    select(AmexDirectCutItem).where(
                        AmexDirectCutItem.direct_item_id.in_(
                            [item.id for item in items]
                        )
                    )
                )
            )
            .scalars()
            .all()
            if items
            else []
        )
        for row in cut_rows:
            if row.accounting_poliza_id:
                ledger_by_item.setdefault(row.direct_item_id, []).append(
                    row.accounting_poliza_id
                )
            coverage_by_item[row.direct_item_id] = row.coverage_kind
        heads = await load_decision_heads(session, items)
        from .amex_direct_no_cfdi_service import decision_snapshot

        views = []
        for item in items:
            expense = (
                await session.get(ExpenseReport, sources[item.id].imported_expense_id)
                if sources[item.id].imported_expense_id
                else None
            )
            cfdi = await session.get(CFDIReport, item.cfdi_id) if item.cfdi_id else None
            status = (
                await session.get(AmexCFDIStatusReceipt, item.status_receipt_id)
                if item.status_receipt_id
                else None
            )
            concept = (
                await session.get(BudgetConcept, item.budget_concept_id)
                if item.budget_concept_id
                else None
            )
            tournament = (
                await session.get(Tournament, item.tournament_id)
                if item.tournament_id
                else None
            )
            account = (
                await session.get(CuentaContable, expense.cuenta_contable_id)
                if expense and expense.cuenta_contable_id
                else None
            )
            liability = (
                await session.get(CuentaContable, card.liability_cuenta_contable_id)
                if card
                else None
            )
            facts = {
                column.name: getattr(item, column.name)
                for column in item.__table__.columns
            }
            pointer = getattr(item, "no_cfdi_decision_id", None)
            decision = heads.get(pointer) if pointer is not None else None
            active_decision(item, heads)  # Fail closed on an invalid pointer.
            facts.update(
                no_cfdi_decision=decision_snapshot(decision) if decision else None,
                movement_kind=sources[item.id].movement_kind,
                imported_expense_id=sources[item.id].imported_expense_id,
                source_json=sources[item.id].source_json,
                recovery_revision_id=sources[item.id].recovery_revision_id,
                row_number=sources[item.id].source_json["row_number"],
                fecha=(
                    expense.fecha
                    if expense
                    else sources[item.id].source_json.get("date")
                ),
                descriptor=sources[item.id].source_json.get("description"),
                amount=(
                    Decimal(sources[item.id].source_json["amount"])
                    if sources[item.id].source_json.get("amount") is not None
                    else None
                ),
                currency=sources[item.id].source_json.get("currency"),
                expense_id=sources[item.id].imported_expense_id,
                cfdi_uuid=cfdi.cfdi_uuid if cfdi else None,
                sat_status=status.observed_status if status else None,
                status_observed_at=status.observed_at if status else None,
                budget_concept_name=concept.concept_name if concept else None,
                tournament_name=tournament.name if tournament else None,
                project_name=item.project_code,
                account_code=account.codigo if account else None,
                liability_code=liability.codigo if liability else None,
                budget_line_label=(
                    str(item.budget_line_id) if item.budget_line_id else None
                ),
                budget_version_label=(
                    str(item.budget_version_id) if item.budget_version_id else None
                ),
                recognized_poliza_ids=list(
                    dict.fromkeys(ledger_by_item.get(item.id, []))
                ),
                coverage_kind=coverage_by_item.get(item.id),
            )
            views.append(SimpleNamespace(**facts))
        cut = (
            await session.execute(
                select(AmexDirectAccountingCut).where(
                    AmexDirectAccountingCut.import_id == import_id
                )
            )
        ).scalar_one_or_none()
        return DirectResult(
            "ready" if context else "pending",
            None if context else "contexto_fiscal_pendiente",
            statement=statement,
            context=context,
            card=card,
            items=views,
            cut=cut,
            source_version=state.source_version,
            source_key=digest(
                {
                    "context": context.payload_hash if context else None,
                    "items": [v.source_hash for v in items],
                }
            ),
            progress={
                **(state.progress_json or {}),
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
                "recovery_revision_ids": sorted(
                    {
                        str(v.recovery_revision_id)
                        for v in sources.values()
                        if v.recovery_revision_id
                    }
                ),
            },
        )


async def list_direct_context_options(
    session: Any, *, actor_id: UUID, import_id: UUID
) -> dict[str, Any]:
    """Fiscal choices require context authority, independently of classification."""
    with session.no_autoflush:
        await authorize(session, actor_id, "contexto", finance=True)
        configs = [
            row for row in configured_scopes() if actor_scope(row, actor_id) is not None
        ]
        choices = []
        statement = None
        for config in configs:
            if (
                statement is not None
                and str(statement.card_account_id) not in config["card_account_ids"]
            ):
                continue
            try:
                statement, _, _, _ = await load_statement(
                    session,
                    actor_id,
                    import_id,
                    "contexto",
                    finance=True,
                    requested_company_code=config["company_code"],
                    requested_receiver_rfc=config["receiver_rfc"],
                )
            except DirectConflict as exc:
                if str(exc) != "contexto_fiscal_pendiente":
                    raise
                continue
            choices.append(
                {
                    "company_code": config["company_code"],
                    "receiver_rfc": config["receiver_rfc"],
                    "currency": "MXN",
                }
            )
        if statement is None:
            raise DirectAccessError("actor_scope_denied")
        return {"contexts": choices}


async def list_direct_classification_options(
    session: Any, *, actor_id: UUID, import_id: UUID
) -> dict[str, Any]:
    statement, state, context, config = await load_statement(
        session, actor_id, import_id, "clasificar"
    )
    scope = actor_scope(config, actor_id)
    assert scope is not None
    tournament_ids = set(config.get("tournament_ids", [])) & set(
        scope.get("tournament_ids", [])
    )
    tournaments = list(
        (
            await session.execute(
                select(Tournament)
                .where(
                    Tournament.id.in_([UUID(v) for v in tournament_ids]),
                    Tournament.active.is_(True),
                )
                .order_by(Tournament.name)
            )
        )
        .scalars()
        .all()
    )
    concepts = list(
        (
            await session.execute(
                select(BudgetConcept)
                .where(
                    BudgetConcept.active.is_(True),
                    BudgetConcept.budget_direction == "expense",
                    BudgetConcept.tournament_id.in_([v.id for v in tournaments]),
                )
                .order_by(BudgetConcept.concept_name)
            )
        )
        .scalars()
        .all()
    )
    project_codes = set(config.get("project_codes", [])) & set(
        scope.get("project_codes", [])
    )
    # Non-tournament codes are valid only when already present in this catalog.
    project_concepts = list(
        (
            await session.execute(
                select(BudgetConcept).where(
                    BudgetConcept.active.is_(True),
                    BudgetConcept.budget_direction == "expense",
                    BudgetConcept.tournament_id.is_(None),
                    BudgetConcept.tournament_code.in_(list(project_codes)),
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "tournaments": [{"id": str(v.id), "name": v.name} for v in tournaments],
        "projects": [
            {"code": v, "name": v}
            for v in sorted(project_codes)
            if any(c.tournament_code == v for c in project_concepts)
        ],
        "concepts": [
            {
                "id": str(v.id),
                "name": v.concept_name,
                "tournament_id": str(v.tournament_id) if v.tournament_id else None,
                "project_code": v.tournament_code,
            }
            for v in [*concepts, *project_concepts]
        ],
        "contexts": [
            {
                "entity_code": config["entity_code"],
                "company_code": config["company_code"],
                "receiver_rfc": config["receiver_rfc"],
                "currency": "MXN",
                "card_account_ids": config["card_account_ids"],
                "label": config["entity_code"],
            }
        ],
        "edition_years": sorted(
            set(config.get("edition_years", [])) & set(scope.get("edition_years", []))
        ),
    }


async def list_direct_intake_options(session: Any, *, actor_id: UUID) -> dict[str, Any]:
    from ..models import Empleado

    with session.no_autoflush:
        await authorize(session, actor_id, "cargar", finance=True)
        configs = [
            row for row in configured_scopes() if actor_scope(row, actor_id) is not None
        ]
        if not configs:
            return {
                "cards": [],
                "contexts": [],
                "people": [],
                "reason": "contexto_fiscal_pendiente",
            }
        for config in configs:
            await authorize(session, actor_id, "cargar", config=config, finance=True)
        cards = list(
            (
                await session.execute(
                    select(AmexCardAccount)
                    .where(
                        AmexCardAccount.id.in_(
                            [UUID(v) for c in configs for v in c["card_account_ids"]]
                        ),
                        AmexCardAccount.active.is_(True),
                    )
                    .order_by(AmexCardAccount.card_label)
                )
            )
            .scalars()
            .all()
        )
        people_query = (
            select(Empleado).where(Empleado.activo.is_(True)).order_by(Empleado.nombre)
        )
        if all("employee_ids" in c for c in configs):
            people_query = people_query.where(
                Empleado.id.in_([UUID(v) for c in configs for v in c["employee_ids"]])
            )
        people = list((await session.execute(people_query)).scalars().all())
        return {
            "cards": [
                {"id": str(c.id), "label": c.card_label, "last4": c.last4}
                for c in cards
            ],
            "contexts": [
                {
                    "company_code": c["company_code"],
                    "receiver_rfc": c["receiver_rfc"],
                    "currency": "MXN",
                    "card_account_ids": c["card_account_ids"],
                    "label": c["entity_code"],
                }
                for c in configs
            ],
            "people": [{"id": str(p.id), "name": p.nombre} for p in people],
            "reason": None,
        }


async def list_direct_statements(
    session: Any, *, actor_id: UUID, budget: bool = False
) -> list[dict[str, Any]]:
    """Discover only direct sources in the actor's declared card/entity scope."""
    from ..models import AmexStatementDirectState, AmexStatementImport
    from .amex_direct_common import require_amex_direct_schema

    with session.no_autoflush:
        await authorize(session, actor_id, "ver", budget=budget)
        configs = [
            c for c in configured_scopes() if actor_scope(c, actor_id) is not None
        ]
        for c in configs:
            await authorize(session, actor_id, "ver", config=c, budget=budget)
        if not configs:
            return []
        await require_amex_direct_schema(session)
        statements = list(
            (
                await session.execute(
                    select(AmexStatementImport)
                    .where(
                        AmexStatementImport.workflow_mode == "direct",
                        AmexStatementImport.card_account_id.in_(
                            [UUID(v) for c in configs for v in c["card_account_ids"]]
                        ),
                    )
                    .order_by(
                        AmexStatementImport.created_at.desc(), AmexStatementImport.id
                    )
                )
            )
            .scalars()
            .all()
        )
        result = []
        for statement in statements:
            state = await session.get(AmexStatementDirectState, statement.id)
            context = (
                await session.get(AmexStatementDirectContext, state.context_id)
                if state.context_id
                else None
            )
            config = configuration_for(
                statement.card_account_id,
                context.company_code if context else None,
                context.receiver_rfc if context else None,
            )
            if config is None or actor_scope(config, actor_id) is None:
                continue
            await authorize(session, actor_id, "ver", config=config, budget=budget)
            card = await session.get(AmexCardAccount, statement.card_account_id)
            items = (
                await load_budget_items(session, statement.id, config, actor_id)
                if budget
                else await load_items(session, statement.id)
            )
            sources = await load_effective_sources(session, statement.id)
            counts = {
                kind: sum(sources[i.id].movement_kind == kind for i in items)
                for kind in ("debit", "credit", "unknown", "skipped")
            }
            heads = await load_decision_heads(session, items)
            counts.update(
                no_cfdi=sum(bool(active_decision(i, heads)) for i in items),
                matched=sum(i.match_status == "matched" for i in items),
                classified=sum(
                    bool(context and i.context_id == context.id and i.budget_concept_id)
                    for i in items
                ),
                sent=sum(i.budget_status == "sent" for i in items),
                assigned=sum(i.budget_status == "assigned" for i in items),
                recognized=sum(i.accounting_status == "recognized" for i in items),
            )
            cut = (
                await session.execute(
                    select(AmexDirectAccountingCut).where(
                        AmexDirectAccountingCut.import_id == statement.id
                    )
                )
            ).scalar_one_or_none()
            completion = (
                await session.execute(
                    select(AmexDirectCompletionReceipt).where(
                        AmexDirectCompletionReceipt.import_id == statement.id
                    )
                )
            ).scalar_one_or_none()
            result.append(
                {
                    "completion_receipt": (
                        {
                            "id": str(completion.id),
                            "created_at": str(completion.created_at),
                        }
                        if completion
                        else None
                    ),
                    "statement": statement,
                    "context": context,
                    "card": card,
                    "counts": counts,
                    "cut": cut,
                    "source_version": state.source_version,
                }
            )
        return result
