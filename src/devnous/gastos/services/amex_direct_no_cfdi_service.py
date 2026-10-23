"""Append-only explicit no-CFDI decisions; caller owns the transaction."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text

from ..models import AmexDirectNoCFDIDecision, ExpenseReport
from .amex_direct_common import (
    DirectConflict,
    DirectResult,
    digest,
    load_items,
    load_statement,
    money,
    record_event,
    require_unfrozen,
)
from .amex_direct_effective_source_service import load_effective_sources

TREATMENT = "full_amount_expense_no_tax"
PARTNER_TREATMENT = "full_amount_partner_receivable_no_tax"


def economic_source_hash(statement: Any, item: Any, source: Any) -> str:
    """SQL-compatible stable economics; classification/version hashes excluded."""
    data = source.source_json
    values = [
        statement.id,
        statement.file_sha256,
        statement.card_account_id,
        item.import_item_id,
        source.imported_expense_id,
        source.movement_kind,
        format(money(source.raw_signed_amount), ".2f"),
        data.get("currency"),
        data.get("date"),
        data.get("description"),
        source.recovery_id,
        source.recovery_revision_id,
        source.recovery_proof_hash,
        item.context_id,
        item.context_version,
        source.period_start,
        source.period_end,
    ]
    if getattr(source, "sign_revision_id", None):
        values.extend(
            [source.sign_revision_id, source.sign_row_id, source.sign_proof_hash]
        )
    return hashlib.sha256(
        "|".join("" if v is None else str(v) for v in values).encode()
    ).hexdigest()


async def load_decision_heads(session: Any, items: list[Any]) -> dict[UUID, Any]:
    ids = {getattr(i, "no_cfdi_decision_id", None) for i in items} - {None}
    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                select(AmexDirectNoCFDIDecision).where(
                    AmexDirectNoCFDIDecision.id.in_(ids)
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.id: row for row in rows}


def active_decision(item: Any, heads: dict[UUID, Any]) -> Any:
    pointer = getattr(item, "no_cfdi_decision_id", None)
    if pointer is None:
        return None
    decision = heads.get(pointer)
    if (
        decision is None
        or decision.item_id != item.id
        or decision.import_id != item.import_id
    ):
        raise DirectConflict("no_cfdi_decision_head_conflict")
    if decision.action not in {"declare", "revoke"}:
        raise DirectConflict("no_cfdi_decision_head_conflict")
    return decision if decision.action == "declare" else None


def decision_snapshot(decision: Any) -> dict[str, Any]:
    return {
        key: (str(value) if value is not None else None)
        for key in (
            "id",
            "action",
            "reason",
            "treatment",
            "actor_id",
            "created_at",
            "revision",
            "economic_source_hash",
            "context_id",
            "context_version",
            "imported_expense_id",
        )
        for value in [getattr(decision, key)]
    }


def _validate_source(
    statement: Any, context: Any, item: Any, source: Any, expense: Any
) -> None:
    if (
        not context
        or item.context_id != context.id
        or item.context_version != context.version
    ):
        raise DirectConflict("no_cfdi_context_conflict")
    if source.import_id != statement.id or item.import_id != statement.id:
        raise DirectConflict("item_context_mismatch")
    if (
        source.movement_kind != "debit"
        or source.raw_signed_amount is None
        or abs(money(source.raw_signed_amount)) <= 0
        or not expense
        or source.imported_expense_id != expense.id
        or expense.estado_gasto != "activo"
    ):
        raise DirectConflict("no_cfdi_new_debit_required")
    if (
        context.currency != "MXN"
        or source.source_json.get("currency") != "MXN"
        or expense.currency != "MXN"
        or money(expense.gasto_cantidad) != abs(money(source.raw_signed_amount))
    ):
        raise DirectConflict("no_cfdi_amount_currency_conflict")
    try:
        bank_date = datetime.fromisoformat(str(source.source_json["date"])).date()
        if expense.fecha.date() != bank_date:
            raise DirectConflict("no_cfdi_economic_date_conflict")
    except (KeyError, ValueError, AttributeError) as exc:
        raise DirectConflict("no_cfdi_economic_date_conflict") from exc
    if (
        any(
            getattr(item, key, None)
            for key in (
                "cfdi_id",
                "declared_uuid",
                "status_receipt_id",
                "match_proof",
            )
        )
        or source.source_json.get("declared_uuid")
        or any(
            getattr(expense, key, None)
            for key in (
                "cfdi_report_id",
                "cfdi_uuid_manual",
                "cfdi_compartido_confirmado",
                "cfdi_compartido_motivo",
            )
        )
    ):
        raise DirectConflict("no_cfdi_fiscal_identity_conflict")
    if any(
        getattr(expense, key, None)
        for key in (
            "iva",
            "cuenta_iva_id",
            "retencion_cuentas_json",
            "propina_no_deducible",
            "hospedaje_entidad_fiscal",
            "hospedaje_tasa_impuesto",
            "hospedaje_impuesto_monto",
            "hospedaje_impuesto_confirmado",
        )
    ):
        raise DirectConflict("no_cfdi_manual_tax_conflict")


def validate_decision(
    statement: Any,
    context: Any,
    item: Any,
    source: Any,
    expense: Any,
    decision: Any,
    *,
    expected_treatment: str | None = None,
) -> dict[str, Any]:
    _validate_source(statement, context, item, source, expense)
    if (
        not decision
        or decision.action != "declare"
        or decision.treatment != (expected_treatment or TREATMENT)
        or decision.id != item.no_cfdi_decision_id
        or decision.item_id != item.id
        or decision.import_id != statement.id
        or decision.imported_expense_id != expense.id
        or decision.context_id != context.id
        or decision.context_version != context.version
        or decision.economic_source_hash
        != economic_source_hash(statement, item, source)
    ):
        raise DirectConflict("no_cfdi_source_changed")
    return decision_snapshot(decision)


async def _decide(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_ids: list[UUID],
    expected_versions: dict[Any, int],
    expected_source_version: int,
    reason: str,
    request_key: str,
    confirmed: bool,
    action: str,
) -> DirectResult:
    statement, state, context, _ = await load_statement(
        session, actor_id, import_id, "conciliar", locked=True, finance=True
    )
    await require_unfrozen(session, import_id)
    if not confirmed:
        raise DirectConflict("no_cfdi_confirmation_required")
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise DirectConflict("no_cfdi_reason_required")
    if not request_key or not request_key.strip() or len(request_key) > 160:
        raise DirectConflict("no_cfdi_request_key_required")
    if not item_ids or len(set(item_ids)) != len(item_ids):
        raise DirectConflict("no_cfdi_selection_required")
    payload = {
        "actor_id": str(actor_id),
        "import_id": str(import_id),
        "action": action,
        "items": sorted(str(v) for v in item_ids),
        "reason": reason,
        "confirmed": True,
        "expected_source_version": expected_source_version,
        "expected_versions": {str(k): v for k, v in expected_versions.items()},
        "request_key": request_key,
    }
    payload_hash = digest(payload)
    items = await load_items(session, import_id, locked=True)
    selected = [i for i in items if i.id in item_ids]
    if len(selected) != len(item_ids):
        raise DirectConflict("item_context_mismatch")
    keys = {
        i.id: digest({"request_key": request_key, "item_id": str(i.id)})
        for i in selected
    }
    existing = (
        (
            await session.execute(
                select(AmexDirectNoCFDIDecision).where(
                    AmexDirectNoCFDIDecision.item_id.in_(item_ids),
                    AmexDirectNoCFDIDecision.idempotency_key.in_(list(keys.values())),
                )
            )
        )
        .scalars()
        .all()
    )
    if existing:
        if len(existing) != len(selected) or any(
            d.payload_hash != payload_hash for d in existing
        ):
            raise DirectConflict("no_cfdi_idempotency_conflict")
        return DirectResult(
            "exists",
            statement=statement,
            context=context,
            items=selected,
            source_version=state.source_version,
        )
    if state.source_version != expected_source_version:
        raise DirectConflict("stale_source_version")
    heads = await load_decision_heads(session, selected)
    sources = await load_effective_sources(session, import_id)
    decisions = []
    from .amex_direct_account_treatment_service import (
        load_account_treatment_heads,
        treatment_kind,
    )

    treatment_heads = await load_account_treatment_heads(session, selected)
    from .amex_direct_accounting_service import covered_source_proof
    from .amex_expense_account_settlement_service import selected_origin

    for item in selected:
        if (
            expected_versions.get(str(item.id), expected_versions.get(item.id))
            != item.source_version
        ):
            raise DirectConflict("stale_item_version")
        if item.budget_status in {"sent", "assigned"} or item.budget_assignment_id:
            raise DirectConflict("no_cfdi_budget_release_required")
        if item.accounting_status == "recognized":
            raise DirectConflict("recognized_source_immutable")
        pointer = getattr(item, "no_cfdi_decision_id", None)
        prior = heads.get(pointer) if pointer is not None else None
        active = active_decision(item, heads)
        if (action == "declare" and active) or (action == "revoke" and not active):
            raise DirectConflict("no_cfdi_transition_conflict")
        source = sources[item.id]
        expense = (
            await session.get(
                ExpenseReport,
                source.imported_expense_id,
                populate_existing=True,
                with_for_update=True,
            )
            if source.imported_expense_id
            else None
        )
        _validate_source(statement, context, item, source, expense)
        assert expense is not None
        if await selected_origin(session, item) or await covered_source_proof(
            session, expense
        ):
            raise DirectConflict("no_cfdi_historical_source_conflict")
        # A declaration is valid only for an unrecognized economic source.
        represented = (
            await session.execute(
                text(
                    "SELECT 1 FROM amex_recognition_representations "
                    "WHERE expense_id=:id UNION ALL SELECT 1 FROM "
                    "amex_recognition_consumptions WHERE "
                    "imported_expense_id=:id OR "
                    "source_report_expense_id=:id LIMIT 1"
                ),
                {"id": expense.id},
            )
        ).first()
        if represented:
            raise DirectConflict("recognized_source_immutable")
        kind = treatment_kind(item, treatment_heads)
        if kind == "unclassified":
            raise DirectConflict("account_treatment_required")
        chosen_treatment = (
            PARTNER_TREATMENT if kind == "partner_receivable" else TREATMENT
        )
        if action == "revoke":
            validate_decision(
                statement,
                context,
                item,
                source,
                expense,
                active,
                expected_treatment=chosen_treatment,
            )
        decisions.append(
            (
                item,
                AmexDirectNoCFDIDecision(
                    id=uuid4(),
                    item_id=item.id,
                    import_id=import_id,
                    imported_expense_id=expense.id,
                    context_id=context.id,
                    context_version=context.version,
                    revision=prior.revision + 1 if prior else 1,
                    action=action,
                    previous_decision_id=prior.id if prior else None,
                    treatment=chosen_treatment,
                    reason=reason,
                    actor_id=actor_id,
                    request_id=request_key,
                    idempotency_key=keys[item.id],
                    payload_hash=payload_hash,
                    economic_source_hash=economic_source_hash(statement, item, source),
                    versions_json=payload,
                    created_at=datetime.now(timezone.utc),
                ),
            )
        )
    async with session.begin_nested():
        for item, decision in decisions:
            session.add(decision)
            await session.flush()
            item.no_cfdi_decision_id = decision.id
            await record_event(
                session,
                item,
                state,
                actor_id,
                "no_cfdi_" + action,
                {
                    "no_cfdi_decision_id": str(decision.id),
                    "confirmed": True,
                    "reason": reason,
                    "request_payload_hash": payload_hash,
                },
            )
        state.progress_json = None
        await session.flush()
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    return DirectResult(
        "ready",
        statement=statement,
        context=context,
        items=selected,
        source_version=state.source_version,
    )


async def declare_no_cfdi_items(session: Any, **kwargs: Any) -> DirectResult:
    """Declare a validated batch atomically under existing Finance authority."""
    return await _decide(session, action="declare", **kwargs)


async def revoke_no_cfdi_items(session: Any, **kwargs: Any) -> DirectResult:
    """Append revocations without erasing the prior declaration."""
    return await _decide(session, action="revoke", **kwargs)
