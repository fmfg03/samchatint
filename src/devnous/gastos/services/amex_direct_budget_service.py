"""Classification and scoped movement assignment, never budget-plan editing."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, text

from ..models import (
    AmexCardAccount,
    AmexDirectItem,
    BudgetConcept,
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
    configured_scopes,
    digest,
    load_items,
    load_statement,
    money,
    record_event,
    require_unfrozen,
    resolve_item_import,
)
from .amex_direct_effective_source_service import (
    effective_source_for_item,
    effective_sources_select,
    load_effective_sources,
)
from .amex_recognition_service import load_expenses
from .budget_concept_account_service import apply_budget_concept_cuenta_mapping


def _scope(
    config: dict[str, Any],
    actor_id: UUID,
    *,
    tournament_id: UUID | None,
    project_code: str | None,
    edition_year: int,
) -> None:
    scope = actor_scope(config, actor_id)
    if (
        scope is None
        or edition_year not in config.get("edition_years", [])
        or edition_year not in scope.get("edition_years", [])
    ):
        raise DirectAccessError("project_entity_scope_denied")
    if tournament_id:
        if str(tournament_id) not in config.get("tournament_ids", []) or str(
            tournament_id
        ) not in scope.get("tournament_ids", []):
            raise DirectAccessError("project_entity_scope_denied")
    elif (
        not project_code
        or project_code not in config.get("project_codes", [])
        or project_code not in scope.get("project_codes", [])
    ):
        raise DirectAccessError("project_entity_scope_denied")


def _selected(
    items: list[Any], item_ids: list[UUID], expected_versions: dict[str, int]
) -> list[Any]:
    if not item_ids or len(set(item_ids)) != len(item_ids):
        raise DirectConflict("explicit_item_selection_required")
    chosen = [item for item in items if item.id in item_ids]
    if len(chosen) != len(item_ids) or set(expected_versions) != {
        str(v) for v in item_ids
    }:
        raise DirectConflict("item_context_mismatch")
    if any(expected_versions[str(i.id)] != i.source_version for i in chosen):
        raise DirectConflict("stale_item_version")
    return chosen


async def classify_direct_items(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_ids: list[UUID],
    tournament_id: UUID | None,
    project_code: str | None,
    budget_concept_id: UUID,
    edition_year: int,
    expected_versions: dict[str, int],
    reason: str = "",
) -> DirectResult:
    statement, state, context, config = await load_statement(
        session, actor_id, import_id, "clasificar", locked=True
    )
    await require_unfrozen(session, import_id)
    if context is None or bool(tournament_id) == bool(project_code):
        return DirectResult("pending", "explicit_project_required")
    _scope(
        config,
        actor_id,
        tournament_id=tournament_id,
        project_code=project_code,
        edition_year=edition_year,
    )
    items = _selected(
        await load_items(session, import_id, locked=True), item_ids, expected_versions
    )
    sources = await load_effective_sources(session, import_id)
    if any(
        sources[i.id].movement_kind != "debit"
        or i.context_id != context.id
        or i.accounting_status == "recognized"
        or i.budget_status in {"sent", "assigned"}
        for i in items
    ):
        return DirectResult("pending", "classification_return_required")
    concept = await session.get(
        BudgetConcept, budget_concept_id, populate_existing=True, with_for_update=True
    )
    tournament = await session.get(Tournament, tournament_id) if tournament_id else None
    if (
        concept is None
        or not concept.active
        or concept.budget_direction != "expense"
        or concept.tournament_id != tournament_id
        or (tournament_id and (tournament is None or not tournament.active))
        or (not tournament_id and concept.tournament_code != project_code)
    ):
        return DirectResult("pending", "budget_category_context_mismatch")
    expenses: dict[Any, Any] = {
        e.id: e
        for e in await load_expenses(
            session, [cast(UUID, sources[i.id].imported_expense_id) for i in items]
        )
    }
    from samchat.budgets.service import _date_in_edition_year

    if any(
        not _date_in_edition_year(expense.fecha, edition_year)
        for expense in expenses.values()
    ):
        return DirectResult("pending", "expense_date_outside_budget_edition")
    for item in items:
        expense = expenses[sources[item.id].imported_expense_id]
        from .amex_direct_accounting_service import covered_source_proof

        if await covered_source_proof(session, expense):
            return DirectResult("pending", "already_recognized_source")
    for item in items:
        before = {
            "tournament_id": item.tournament_id,
            "project_code": item.project_code,
            "budget_concept_id": item.budget_concept_id,
            "edition_year": item.edition_year,
        }
        item.tournament_id = tournament_id
        item.project_code = project_code
        item.budget_concept_id = budget_concept_id
        item.edition_year = edition_year
        item.budget_status = "classified"
        expense = expenses[sources[item.id].imported_expense_id]
        expense.proyecto = tournament.name if tournament else project_code
        expense.edicion = edition_year
        expense.budget_concept_id = budget_concept_id
        await record_event(
            session,
            item,
            state,
            actor_id,
            "classification",
            {
                "before": before,
                "project": str(tournament_id or project_code),
                "concept": budget_concept_id,
                "edition": edition_year,
                "reason": reason,
            },
        )
    await session.flush()
    return DirectResult(
        "classified",
        statement=statement,
        items=items,
        source_version=state.source_version,
    )


async def send_direct_items_to_budget(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_ids: list[UUID],
    expected_versions: dict[str, int],
    confirmed: bool,
) -> DirectResult:
    statement, state, context, config = await load_statement(
        session, actor_id, import_id, "enviar_presupuesto", locked=True
    )
    await require_unfrozen(session, import_id)
    if not confirmed:
        return DirectResult("pending", "budget_submission_confirmation_required")
    items = _selected(
        await load_items(session, import_id, locked=True), item_ids, expected_versions
    )
    for item in items:
        if item.budget_status != "classified" or item.context_id != context.id:
            return DirectResult("pending", "classification_required")
        _scope(
            config,
            actor_id,
            tournament_id=item.tournament_id,
            project_code=item.project_code,
            edition_year=item.edition_year,
        )
    for item in items:
        item.budget_status = "sent"
        await record_event(
            session,
            item,
            state,
            actor_id,
            "budget_submission",
            {
                "project": str(item.tournament_id or item.project_code),
                "concept": item.budget_concept_id,
            },
        )
    await session.flush()
    return DirectResult(
        "sent", statement=statement, items=items, source_version=state.source_version
    )


async def _line(
    session: Any, line_id: UUID, *, locked: bool = False
) -> dict[str, Any] | None:
    sql = """SELECT l.*, v.edition_year, v.status AS version_status, v.version_name
        FROM budget_lines l JOIN budget_versions v ON v.id=l.budget_version_id
        WHERE l.id=:line_id"""
    if locked:
        sql += " FOR UPDATE OF l,v"
    row = (await session.execute(text(sql), {"line_id": line_id})).mappings().first()
    return dict(row) if row else None


def _line_matches(item: Any, line: dict[str, Any], config: dict[str, Any]) -> bool:
    status = str(line["version_status"]).lower()
    allowed = status in {"approved", "frozen"} or (
        config.get("allow_draft_reforecast") is True
        and status in {"draft", "reforecast"}
    )
    return (
        allowed
        and line["budget_concept_id"] == item.budget_concept_id
        and line.get("line_direction") == "expense"
        and line.get("tournament_id") == item.tournament_id
        and (
            item.tournament_id is not None
            or line.get("tournament_code") == item.project_code
        )
        and int(line["edition_year"]) == item.edition_year
    )


async def assign_direct_budget_line(
    session: Any,
    *,
    actor_id: UUID,
    item_id: UUID,
    budget_line_id: UUID,
    expected_source_version: int,
    expected_source_hash: str,
    idempotency_key: str,
) -> DirectResult:
    parent = await resolve_item_import(
        session,
        actor_id=actor_id,
        action="asignar_partida",
        item_id=item_id,
        budget=True,
    )
    statement, state, context, config = await load_statement(
        session, actor_id, parent, "asignar_partida", locked=True, budget=True
    )
    await require_unfrozen(session, parent)
    item = await session.get(
        AmexDirectItem, item_id, populate_existing=True, with_for_update=True
    )
    _scope(
        config,
        actor_id,
        tournament_id=item.tournament_id,
        project_code=item.project_code,
        edition_year=item.edition_year,
    )
    payload = {
        "item_id": str(item_id),
        "line_id": str(budget_line_id),
        "idempotency_key": idempotency_key,
        "expected_version": expected_source_version,
        "expected_hash": expected_source_hash,
    }
    from ..models import AmexDirectEvent

    prior = (
        await session.execute(
            select(AmexDirectEvent).where(
                AmexDirectEvent.item_id == item_id,
                AmexDirectEvent.action == "budget_assignment",
                AmexDirectEvent.payload_hash == digest(payload),
            )
        )
    ).scalar_one_or_none()
    if (
        prior
        and item.budget_line_id == budget_line_id
        and item.budget_status == "assigned"
    ):
        return DirectResult(
            "exists",
            statement=statement,
            items=[item],
            source_version=state.source_version,
        )
    if (
        not idempotency_key
        or item.source_version != expected_source_version
        or item.source_hash != expected_source_hash
    ):
        return DirectResult("pending", "stale_item_version")
    if item.context_id != context.id or item.budget_status != "sent":
        return DirectResult("pending", "budget_submission_required")
    source = await effective_source_for_item(session, item)
    expense = (await load_expenses(session, [cast(UUID, source.imported_expense_id)]))[
        0
    ]
    line = await _line(session, budget_line_id, locked=True)
    if line is None or not _line_matches(item, line, config):
        return DirectResult("pending", "budget_line_context_mismatch")
    concept = await session.get(
        BudgetConcept,
        item.budget_concept_id,
        populate_existing=True,
        with_for_update=True,
    )
    mapped = (
        await session.get(CuentaContable, concept.cuenta_contable_id)
        if concept and concept.cuenta_contable_id
        else None
    )
    card = await session.get(AmexCardAccount, statement.card_account_id)
    liability = (
        await session.get(CuentaContable, card.liability_cuenta_contable_id)
        if card
        else None
    )
    from .amex_accounting_posting_service import ALLOWED_AMEX_LIABILITY_CODES

    if (
        concept is None
        or not concept.active
        or concept.budget_direction != "expense"
        or mapped is None
        or not mapped.activo
    ):
        return DirectResult("pending", "budget_account_mapping_required")
    if (
        card is None
        or not card.active
        or liability is None
        or not liability.activo
        or liability.codigo not in ALLOWED_AMEX_LIABILITY_CODES
    ):
        return DirectResult("pending", "invalid_amex_liability")
    if expense.cuenta_contable_id not in (
        None,
        mapped.id,
    ) or expense.contra_cuenta_contable_id not in (None, liability.id):
        return DirectResult("pending", "account_mapping_conflict")
    from samchat.budgets.service import assign_budget_movement_to_line

    async with session.begin_nested():
        await assign_budget_movement_to_line(
            session,
            budget_version_id=str(line["budget_version_id"]),
            budget_concept_id=str(item.budget_concept_id),
            budget_line_id=str(budget_line_id),
            movement_key=f"expense:{expense.id}",
            actor_empleado_id=str(actor_id),
            ensure_schema=False,
            source_kind="amex_direct",
        )
        assignment = (
            await session.execute(
                text(
                    """SELECT id FROM budget_movement_assignments
                    WHERE budget_version_id=:version AND movement_key=:key"""
                ),
                {"version": line["budget_version_id"], "key": f"expense:{expense.id}"},
            )
        ).scalar_one()
        await apply_budget_concept_cuenta_mapping(
            session, expense, budget_concept_id=item.budget_concept_id
        )
        if expense.cuenta_contable_id != mapped.id:
            raise DirectConflict("budget_account_mapping_required")
        expense.cuenta_contable_budget_concept_id = item.budget_concept_id
        expense.contra_cuenta_contable_id = liability.id
        item.budget_line_id = budget_line_id
        item.budget_version_id = line["budget_version_id"]
        item.budget_assignment_id = assignment
        item.budget_status = "assigned"
        await record_event(session, item, state, actor_id, "budget_assignment", payload)
        await session.flush()
    return DirectResult(
        "assigned",
        statement=statement,
        items=[item],
        source_version=state.source_version,
    )


async def reject_direct_budget_item(
    session: Any,
    *,
    actor_id: UUID,
    item_id: UUID,
    expected_source_version: int,
    reason: str,
) -> DirectResult:
    parent = await resolve_item_import(
        session,
        actor_id=actor_id,
        action="rechazar_presupuesto",
        item_id=item_id,
        budget=True,
    )
    statement, state, context, config = await load_statement(
        session, actor_id, parent, "rechazar_presupuesto", locked=True, budget=True
    )
    await require_unfrozen(session, parent)
    item = await session.get(
        AmexDirectItem, item_id, populate_existing=True, with_for_update=True
    )
    _scope(
        config,
        actor_id,
        tournament_id=item.tournament_id,
        project_code=item.project_code,
        edition_year=item.edition_year,
    )
    if (
        item.source_version != expected_source_version
        or item.budget_status not in {"sent", "assigned"}
        or not reason.strip()
    ):
        return DirectResult("pending", "budget_return_reason_required")
    before = {
        "line_id": item.budget_line_id,
        "version_id": item.budget_version_id,
        "assignment_id": item.budget_assignment_id,
    }
    before["released_mapping"] = await release_direct_budget_mapping(
        session, item, statement.card_account_id
    )
    item.budget_status = "classified"
    item.budget_line_id = None
    item.budget_version_id = None
    item.budget_assignment_id = None
    await record_event(
        session,
        item,
        state,
        actor_id,
        "budget_return",
        {"prior": before, "reason": reason},
    )
    await session.flush()
    return DirectResult(
        "rejected",
        statement=statement,
        items=[item],
        source_version=state.source_version,
    )


async def budget_assignment_source(
    session: Any,
    movement_key: str,
    tournament_id: str | None,
    edition_year: int,
    budget_concept_id: str,
    version_status: str,
    actor_empleado_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    if not actor_empleado_id or not movement_key.startswith("expense:"):
        raise DirectAccessError("configured_budget_actor_required")
    actor_id = UUID(actor_empleado_id)
    expense_id = UUID(movement_key.split(":", 1)[1])
    parent = await resolve_item_import(
        session,
        actor_id=actor_id,
        action="asignar_partida",
        expense_id=expense_id,
        budget=True,
    )
    sources_view = effective_sources_select()
    item = (
        await session.execute(
            select(AmexDirectItem)
            .join(sources_view, sources_view.c.id == AmexDirectItem.id)
            .where(sources_view.c.imported_expense_id == expense_id)
        )
    ).scalar_one()
    statement, state, context, config = await load_statement(
        session, actor_id, parent, "asignar_partida", locked=True, budget=True
    )
    item = await session.get(
        AmexDirectItem, item.id, populate_existing=True, with_for_update=True
    )
    await require_unfrozen(session, item.import_id)
    _scope(
        config,
        actor_id,
        tournament_id=item.tournament_id,
        project_code=item.project_code,
        edition_year=item.edition_year,
    )
    if (
        item.context_id != context.id
        or item.budget_status != "sent"
        or str(item.budget_concept_id) != budget_concept_id
        or item.edition_year != edition_year
        or str(item.tournament_id or "") != str(tournament_id or "")
    ):
        raise DirectConflict("budget_line_context_mismatch")
    from .amex_direct_accounting_service import covered_source_proof

    expense = await session.get(ExpenseReport, expense_id)
    if await covered_source_proof(session, expense):
        raise DirectConflict("already_recognized_source")
    allowed = version_status in {"approved", "frozen"} or (
        config.get("allow_draft_reforecast") is True
        and version_status in {"draft", "reforecast"}
    )
    return {
        "movement_key": movement_key,
        "expense_id": str(expense_id),
        "concept_key": str(item.budget_concept_id),
        "source_kind": "amex_direct",
        "origen": "amex_direct",
        "movement_date": expense.fecha.date(),
    }, allowed


async def list_direct_budget_lines(
    session: Any, *, actor_id: UUID, item_id: UUID
) -> list[dict[str, Any]]:
    with session.no_autoflush:
        parent = await resolve_item_import(
            session, actor_id=actor_id, action="ver", item_id=item_id, budget=True
        )
        statement, state, context, config = await load_statement(
            session, actor_id, parent, "ver", budget=True
        )
        item = await session.get(AmexDirectItem, item_id)
        _scope(
            config,
            actor_id,
            tournament_id=item.tournament_id,
            project_code=item.project_code,
            edition_year=item.edition_year,
        )
        lines = (
            (
                await session.execute(
                    text(
                        """SELECT l.*,v.edition_year,v.status AS version_status,
            v.version_name
            FROM budget_lines l JOIN budget_versions v ON v.id=l.budget_version_id
            WHERE l.budget_concept_id=:concept
            ORDER BY v.version_name,l.concept_name,l.id"""
                    ),
                    {"concept": item.budget_concept_id},
                )
            )
            .mappings()
            .all()
        )
        return [
            {
                **dict(row),
                "id": str(row["id"]),
                "label": (
                    f"{row['concept_name']} · {row['version_name']} "
                    f"({row['version_status']})"
                ),
            }
            for row in lines
            if _line_matches(item, dict(row), config)
        ]


async def list_direct_budget_queue(session: Any, *, actor_id: UUID) -> list[Any]:
    from .amex_direct_context_service import (
        list_direct_statement,
        list_direct_statements,
    )

    await authorize(session, actor_id, "asignar_partida", budget=True)
    views = []
    for entry in await list_direct_statements(session, actor_id=actor_id, budget=True):
        result = await list_direct_statement(
            session, actor_id=actor_id, import_id=entry["statement"].id, budget=True
        )
        config = next(
            (
                c
                for c in configured_scopes()
                if c["entity_code"] == entry["context"].entity_code
            ),
            None,
        )
        if config is None or result.cut is not None:
            continue
        for item in result.items:
            if item.accounting_status == "recognized":
                continue
            from .amex_direct_accounting_service import covered_source_proof

            expense = (
                await session.get(ExpenseReport, item.expense_id)
                if item.expense_id
                else None
            )
            if await covered_source_proof(session, expense):
                continue
            if item.budget_status in {"sent", "assigned"}:
                try:
                    _scope(
                        config,
                        actor_id,
                        tournament_id=item.tournament_id,
                        project_code=item.project_code,
                        edition_year=item.edition_year,
                    )
                except DirectAccessError:
                    continue
                item.can_assign = item.budget_status == "sent"
                item.can_return = True
                item.statement_id = entry["statement"].id
                views.append(item)
    return views


async def list_budget_direct_movements(
    session: Any,
    edition_year: int,
    version_id: str | None,
    tournament_id: str | None,
    phase_filter: str | None = None,
) -> list[dict[str, Any]]:
    if not configured_scopes():
        return []
    present = await session.scalar(
        text(
            "SELECT to_regclass('amex_direct_items') IS NOT NULL "
            "AND to_regclass('amex_statement_direct_state') IS NOT NULL"
        )
    )
    if not present:
        return []
    from .amex_direct_accounting_service import (
        covered_source_proof,
        validate_direct_match,
    )
    from .expense_accounting_service import build_expense_accounting_preview

    rows = list(
        (
            await session.execute(
                select(AmexDirectItem)
                .where(
                    AmexDirectItem.edition_year == edition_year,
                    AmexDirectItem.budget_concept_id.is_not(None),
                    AmexDirectItem.accounting_status != "recognized",
                )
                .order_by(AmexDirectItem.id)
            )
        )
        .scalars()
        .all()
    )
    movements = []
    for item in rows:
        if tournament_id and str(item.tournament_id) != str(tournament_id):
            continue
        if (
            version_id
            and item.budget_version_id
            and str(item.budget_version_id) != str(version_id)
        ):
            continue
        state = await session.get(
            __import__(
                "devnous.gastos.models", fromlist=["AmexStatementDirectState"]
            ).AmexStatementDirectState,
            item.import_id,
        )
        if not state or item.context_id != state.context_id:
            continue
        source = await effective_source_for_item(session, item)
        expense = await session.get(ExpenseReport, source.imported_expense_id)
        if (
            expense is None
            or expense.estado_gasto != "activo"
            or expense.fecha is None
            or await covered_source_proof(session, expense)
        ):
            continue
        tournament = (
            await session.get(Tournament, item.tournament_id)
            if item.tournament_id
            else None
        )
        phase = source.source_json.get("phase")
        if phase_filter and phase_filter != phase:
            continue
        amount = None
        reason: str | None = "fiscal_evidence_pending"
        if not await validate_direct_match(session, item):
            preview = await build_expense_accounting_preview(session, expense)
            base = (preview.get("taxes") or {}).get("base_gasto")
            if base is not None:
                amount = money(base) + money(
                    (preview.get("taxes") or {}).get("gastos_no_deducibles_total", 0)
                )
                reason = None
        movements.append(
            {
                "movement_key": f"expense:{expense.id}",
                "expense_id": str(expense.id),
                "concept_key": str(item.budget_concept_id),
                "source_kind": "amex_direct",
                "origen": "amex_direct",
                "movement_date": expense.fecha.date(),
                "date": expense.fecha.date(),
                "tournament_id": (
                    str(item.tournament_id) if item.tournament_id else None
                ),
                "tournament_code": item.project_code,
                "tournament_name": tournament.name if tournament else item.project_code,
                "phase": phase,
                "project": expense.proyecto,
                "concept": source.source_json.get("description"),
                "amount": amount,
                "kind": (
                    "commitment" if amount is not None else "pending_fiscal_evidence"
                ),
                "reason": reason,
                "budget_assignment_id": (
                    str(item.budget_assignment_id)
                    if item.budget_assignment_id
                    else None
                ),
                "budget_line_id": (
                    str(item.budget_line_id) if item.budget_line_id else None
                ),
            }
        )
    return movements


async def release_direct_budget_mapping(
    session: Any, item: Any, card_id: UUID
) -> dict[str, Any]:
    """Release only this unposted projection's recorded mapping on explicit return."""
    source = await effective_source_for_item(session, item)
    if not item.budget_assignment_id or not source.imported_expense_id:
        return {}
    from .amex_direct_accounting_service import covered_source_proof

    expense: Any = (await load_expenses(session, [source.imported_expense_id]))[0]
    if await covered_source_proof(session, expense):
        raise DirectConflict("recognized_source_context_locked")
    concept = await session.get(BudgetConcept, item.budget_concept_id)
    card = await session.get(AmexCardAccount, card_id)
    prior = {
        "account_id": expense.cuenta_contable_id,
        "contra_account_id": expense.contra_cuenta_contable_id,
        "mapping_concept_id": expense.cuenta_contable_budget_concept_id,
    }
    if (
        concept
        and expense.cuenta_contable_budget_concept_id == item.budget_concept_id
        and expense.cuenta_contable_id == concept.cuenta_contable_id
    ):
        expense.cuenta_contable_id = None
        expense.cuenta_contable_budget_concept_id = None
    if card and expense.contra_cuenta_contable_id == card.liability_cuenta_contable_id:
        expense.contra_cuenta_contable_id = None
    return prior
