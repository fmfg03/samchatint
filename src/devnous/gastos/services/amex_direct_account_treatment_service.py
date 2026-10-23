"""Explicit audited AMEX destinations; no transaction or authority bypass."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text

from ..models import AmexCardAccount, CuentaContable, ExpenseReport
from .amex_direct_common import (
    DirectAccessError,
    DirectConflict,
    DirectResult,
    actor_scope,
    base_context_config,
    digest,
    load_items,
    load_statement,
    money,
    record_event,
    require_unfrozen,
)
from .amex_direct_effective_source_service import load_effective_sources
from .amex_direct_no_cfdi_service import economic_source_hash

TREATMENTS = frozenset({"project_expense", "benefit_expense", "partner_receivable"})
LABELS = {
    "project_expense": "Gasto de proyecto / torneo",
    "benefit_expense": "Prestación absorbida por la empresa",
    "partner_receivable": "Cargo a cuenta deudora del socio",
    "unclassified": "Tratamiento pendiente",
}


def _model():
    from ..models import AmexDirectAccountTreatmentDecision

    return AmexDirectAccountTreatmentDecision


async def policy_for_config(
    session: Any,
    config: dict[str, Any],
    context: Any = None,
) -> Any:
    """Resolve the immutable owner-installed current policy, never env JSON alone."""
    from ..models import AmexDirectAccountTreatmentPolicy

    base_hash = digest(base_context_config(config))
    policy = (
        await session.execute(
            select(AmexDirectAccountTreatmentPolicy)
            .where(AmexDirectAccountTreatmentPolicy.base_config_hash == base_hash)
            .order_by(AmexDirectAccountTreatmentPolicy.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        policy is None
        or policy.config_hash != digest(config)
        or policy.config_text
        != json.dumps(config, sort_keys=True, default=str, separators=(",", ":"))
        or policy.base_config_text
        != json.dumps(
            base_context_config(config),
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
    ):
        raise DirectConflict("account_treatment_policy_unconfigured")
    if context is not None and context.config_hash not in {
        policy.config_hash,
        policy.base_config_hash,
    }:
        trusted_previous = (
            await session.execute(
                select(AmexDirectAccountTreatmentPolicy.id)
                .where(
                    AmexDirectAccountTreatmentPolicy.config_hash == context.config_hash,
                    AmexDirectAccountTreatmentPolicy.base_config_hash == base_hash,
                    AmexDirectAccountTreatmentPolicy.revision < policy.revision,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if trusted_previous is None:
            raise DirectConflict("authorized_context_changed")
    return policy


def _ids(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    try:
        return {str(UUID(str(v))) for v in value}
    except (ValueError, TypeError, AttributeError):
        return set()


def _pairs(value: Any) -> set[tuple[str, str]]:
    if not isinstance(value, list):
        return set()
    try:
        return {
            (
                str(UUID(str(v["budget_version_id"]))),
                str(UUID(str(v["budget_concept_id"]))),
            )
            for v in value
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        return set()


def benefit_grants(config: dict[str, Any], actor_id: UUID) -> dict[str, Any]:
    """Intersect actual context and actor grants; absence never grants all."""
    scope = actor_scope(config, actor_id)
    if scope is None:
        raise DirectAccessError("actor_scope_denied")
    lines = _ids(config.get("benefit_budget_line_ids")) & _ids(
        scope.get("benefit_budget_line_ids")
    )
    pairs = _pairs(config.get("benefit_budget_pairs")) & _pairs(
        scope.get("benefit_budget_pairs")
    )
    if not lines and not pairs:
        raise DirectAccessError("benefit_budget_catalog_unconfigured")
    return {
        "benefit_budget_line_ids": sorted(lines),
        "benefit_budget_pairs": [
            {"budget_version_id": v, "budget_concept_id": c} for v, c in sorted(pairs)
        ],
    }


def benefit_line_allowed(
    config: dict[str, Any], actor_id: UUID, line: dict[str, Any]
) -> bool:
    grants = benefit_grants(config, actor_id)
    return str(line["id"]) in grants["benefit_budget_line_ids"] or (
        str(line["budget_version_id"]),
        str(line["budget_concept_id"]),
    ) in _pairs(grants["benefit_budget_pairs"])


def debtor_account_ids(config: dict[str, Any], actor_id: UUID) -> set[str]:
    scope = actor_scope(config, actor_id)
    if scope is None:
        raise DirectAccessError("actor_scope_denied")
    return _ids(config.get("partner_debtor_account_ids")) & _ids(
        scope.get("partner_debtor_account_ids")
    )


def _account_snapshot(account: Any) -> dict[str, Any]:
    return {
        "id": str(account.id),
        "code": account.codigo,
        "name": account.nombre,
        "active": bool(account.activo),
        "type": account.tipo,
    }


async def load_account_treatment_heads(
    session: Any, items: list[Any]
) -> dict[UUID, Any]:
    ids = {getattr(i, "account_treatment_decision_id", None) for i in items} - {None}
    if not ids:
        return {}
    model = _model()
    rows = (
        (await session.execute(select(model).where(model.id.in_(ids)))).scalars().all()
    )
    return {d.id: d for d in rows}


def active_account_treatment(item: Any, heads: dict[UUID, Any]) -> Any:
    pointer = getattr(item, "account_treatment_decision_id", None)
    if pointer is None:
        return None
    decision = heads.get(pointer)
    if (
        decision is None
        or decision.item_id != item.id
        or decision.import_id != item.import_id
        or decision.action not in {"set", "revoke"}
        or decision.treatment not in TREATMENTS
    ):
        raise DirectConflict("account_treatment_head_conflict")
    return decision if decision.action == "set" else None


def treatment_kind(item: Any, heads: dict[UUID, Any]) -> str:
    if getattr(item, "account_treatment_decision_id", None) is None:
        return "project_expense"
    decision = active_account_treatment(item, heads)
    return decision.treatment if decision else "unclassified"


def account_treatment_snapshot(decision: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = json.loads(
        json.dumps(
            {
                key: getattr(decision, key, None)
                for key in (
                    "id",
                    "item_id",
                    "import_id",
                    "imported_expense_id",
                    "context_id",
                    "context_version",
                    "revision",
                    "action",
                    "treatment",
                    "debtor_account_id",
                    "account_snapshot_json",
                    "authorization_json",
                    "reason",
                    "actor_id",
                    "created_at",
                    "economic_source_hash",
                    "payload_hash",
                    "source_version",
                    "source_hash",
                    "policy_id",
                )
            },
            default=str,
        )
    )
    return snapshot


def validate_account_treatment(
    statement: Any,
    context: Any,
    item: Any,
    source: Any,
    expense: Any,
    decision: Any,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Economic/context proof remains valid across later fiscal/budget events."""
    if (
        not decision
        or decision.action != "set"
        or decision.treatment not in TREATMENTS
        or decision.id != getattr(item, "account_treatment_decision_id", None)
        or decision.item_id != item.id
        or decision.import_id != statement.id
        or source.import_id != statement.id
        or not context
        or not expense
        or source.movement_kind != "debit"
        or source.imported_expense_id != expense.id
        or decision.imported_expense_id != expense.id
        or item.context_id != context.id
        or item.context_version != context.version
        or decision.context_id != context.id
        or decision.context_version != context.version
        or decision.economic_source_hash
        != economic_source_hash(statement, item, source)
        or decision.authorization_json.get("config_hash") != digest(config)
        or decision.authorization_json.get("config_text")
        != json.dumps(config, sort_keys=True, default=str, separators=(",", ":"))
        or expense.estado_gasto != "activo"
        or money(expense.gasto_cantidad) != abs(money(source.raw_signed_amount))
    ):
        raise DirectConflict("account_treatment_source_changed")
    if decision.treatment in {"benefit_expense", "partner_receivable"} and (
        item.tournament_id is not None
        or item.project_code is not None
        or getattr(expense, "proyecto", None) not in (None, "")
    ):
        raise DirectConflict("account_treatment_operational_project_conflict")
    if decision.treatment == "partner_receivable" and any(
        getattr(item, k, None) is not None
        for k in (
            "budget_concept_id",
            "budget_line_id",
            "budget_version_id",
            "budget_assignment_id",
            "edition_year",
        )
    ):
        raise DirectConflict("partner_budget_conflict")
    return account_treatment_snapshot(decision)


async def validate_treatment_accounts(
    session: Any,
    statement: Any,
    decision: Any,
    config: dict[str, Any],
    actor_id: UUID | None = None,
    *,
    locked: bool = False,
) -> dict[str, Any]:
    """Revalidate mutable accounts and the original/current actor's actual grant."""
    from .amex_accounting_posting_service import ALLOWED_AMEX_LIABILITY_CODES

    policy = await policy_for_config(session, config)
    if getattr(decision, "policy_id", None) != policy.id:
        raise DirectConflict("account_treatment_policy_changed")
    card = await session.get(
        AmexCardAccount,
        statement.card_account_id,
        populate_existing=True,
        with_for_update=locked,
    )
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
    if (
        not card
        or not card.active
        or not liability
        or not liability.activo
        or liability.codigo not in ALLOWED_AMEX_LIABILITY_CODES
    ):
        raise DirectConflict("invalid_amex_liability")
    debtor = None
    if decision.treatment == "partner_receivable":
        selected = actor_id or decision.actor_id
        if str(decision.debtor_account_id) not in debtor_account_ids(config, selected):
            raise DirectAccessError("partner_account_scope_denied")
        debtor = await session.get(
            CuentaContable,
            decision.debtor_account_id,
            populate_existing=True,
            with_for_update=locked,
        )
        if (
            not debtor
            or not debtor.activo
            or not re.fullmatch(r"1170-002-[0-9]{3}", debtor.codigo)
        ):
            raise DirectConflict("invalid_amex_partner_decision")
    current = {
        "debtor": _account_snapshot(debtor) if debtor else None,
        "liability": _account_snapshot(liability),
    }
    if current != decision.account_snapshot_json:
        raise DirectConflict("account_treatment_account_changed")
    return {"debtor": debtor, "liability": liability, "snapshot": current}


async def list_direct_account_treatment_options(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
) -> dict[str, Any]:
    _, _, context, config = await load_statement(
        session, actor_id, import_id, "clasificar", finance=True
    )
    await policy_for_config(session, config, context)
    ids = debtor_account_ids(config, actor_id)
    rows = []
    if ids:
        rows = (
            (
                await session.execute(
                    select(CuentaContable)
                    .where(
                        CuentaContable.id.in_([UUID(v) for v in ids]),
                        CuentaContable.activo.is_(True),
                    )
                    .order_by(CuentaContable.codigo)
                )
            )
            .scalars()
            .all()
        )
    try:
        benefit_grants(config, actor_id)
        benefit_available = True
    except DirectAccessError:
        benefit_available = False
    return {
        "partner_accounts": [
            {
                "id": str(a.id),
                "code": a.codigo,
                "name": a.nombre,
                "label": f"{a.codigo} · {a.nombre}",
            }
            for a in rows
            if re.fullmatch(r"1170-002-[0-9]{3}", a.codigo)
        ],
        "benefit_budget_available": benefit_available,
        "treatments": [
            {"value": v, "label": LABELS[v]}
            for v in ("benefit_expense", "partner_receivable", "project_expense")
        ],
    }


async def _plan(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_ids: list[UUID],
    treatment: str,
    debtor_account_id: UUID | None,
    reason: str,
    expected_versions: dict[Any, int],
    action: str,
    locked: bool,
) -> tuple[
    Any,
    Any,
    Any,
    dict[str, Any],
    list[Any],
    dict[Any, Any],
    dict[Any, Any],
    dict[str, Any],
]:
    statement, state, context, config = await load_statement(
        session, actor_id, import_id, "clasificar", locked=locked, finance=True
    )
    await require_unfrozen(session, import_id)
    if not context:
        raise DirectConflict("contexto_fiscal_pendiente")
    if action not in {"set", "revoke"} or treatment not in TREATMENTS:
        raise DirectConflict("invalid_account_treatment")
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise DirectConflict("account_treatment_reason_required")
    if not item_ids or len(set(item_ids)) != len(item_ids):
        raise DirectConflict("explicit_item_selection_required")
    versions = {str(k): v for k, v in expected_versions.items()}
    if set(versions) != {str(v) for v in item_ids}:
        raise DirectConflict("item_context_mismatch")
    if any(type(v) is not int or v <= 0 for v in versions.values()):
        raise DirectConflict("stale_item_version")
    items = [
        i
        for i in await load_items(session, import_id, locked=locked)
        if i.id in item_ids
    ]
    if len(items) != len(item_ids):
        raise DirectConflict("item_context_mismatch")
    sources = await load_effective_sources(session, import_id)
    heads = await load_account_treatment_heads(session, items)
    if treatment == "benefit_expense" and action == "set":
        benefit_grants(config, actor_id)
    if treatment != "partner_receivable" and debtor_account_id is not None:
        raise DirectConflict("account_treatment_account_conflict")
    from .amex_accounting_posting_service import ALLOWED_AMEX_LIABILITY_CODES

    card = await session.get(
        AmexCardAccount, statement.card_account_id, populate_existing=True
    )
    liability = (
        await session.get(
            CuentaContable, card.liability_cuenta_contable_id, populate_existing=True
        )
        if card
        else None
    )
    if (
        not card
        or not liability
        or (
            action == "set"
            and (
                not card.active
                or not liability.activo
                or liability.codigo not in ALLOWED_AMEX_LIABILITY_CODES
            )
        )
    ):
        raise DirectConflict("invalid_amex_liability")
    debtor = None
    if treatment == "partner_receivable":
        if action == "set" and str(debtor_account_id) not in debtor_account_ids(
            config, actor_id
        ):
            raise DirectAccessError("partner_account_scope_denied")
        debtor = await session.get(
            CuentaContable, debtor_account_id, populate_existing=True
        )
        if not debtor or (
            action == "set"
            and (
                not debtor.activo
                or not re.fullmatch(r"1170-002-[0-9]{3}", debtor.codigo)
            )
        ):
            raise DirectConflict("invalid_amex_partner_decision")
    account_snapshot = {
        "debtor": _account_snapshot(debtor) if debtor else None,
        "liability": _account_snapshot(liability),
    }
    from .amex_direct_accounting_service import covered_source_proof
    from .amex_expense_account_settlement_service import selected_origin

    expenses = {}
    for item in items:
        source = sources[item.id]
        if versions[str(item.id)] != item.source_version:
            raise DirectConflict("stale_item_version")
        if (
            item.context_id != context.id
            or item.context_version != context.version
            or source.movement_kind != "debit"
            or source.imported_expense_id is None
            or item.accounting_status == "recognized"
            or item.credit_excluded
        ):
            raise DirectConflict("account_treatment_new_debit_required")
        if item.budget_status in {"sent", "assigned"} or item.budget_assignment_id:
            raise DirectConflict("classification_return_required")
        expense = await session.get(
            ExpenseReport,
            source.imported_expense_id,
            populate_existing=True,
            with_for_update=locked,
        )
        if (
            not expense
            or expense.estado_gasto != "activo"
            or await covered_source_proof(session, expense)
            or await selected_origin(session, item)
        ):
            raise DirectConflict("already_recognized_source")
        represented = (
            await session.execute(
                text(
                    "SELECT 1 FROM amex_recognition_representations WHERE expense_id=:id "
                    "UNION ALL SELECT 1 FROM amex_recognition_consumptions WHERE imported_expense_id=:id "
                    "OR source_report_expense_id=:id LIMIT 1"
                ),
                {"id": expense.id},
            )
        ).first()
        if represented:
            raise DirectConflict("recognized_source_immutable")
        active = active_account_treatment(item, heads)
        if action == "revoke" and (
            not active
            or active.treatment != treatment
            or active.debtor_account_id != debtor_account_id
        ):
            raise DirectConflict("account_treatment_transition_conflict")
        try:
            bank_date = datetime.fromisoformat(str(source.source_json["date"])).date()
        except (KeyError, TypeError, ValueError) as exc:
            raise DirectConflict("account_treatment_economics_changed") from exc
        if (
            money(expense.gasto_cantidad) != abs(money(source.raw_signed_amount))
            or money(expense.gasto_cantidad) <= 0
            or expense.currency != context.currency
            or source.source_json.get("currency") != context.currency
            or expense.fecha.date() != bank_date
        ):
            raise DirectConflict("account_treatment_economics_changed")
        existing_account = getattr(expense, "cuenta_contable_id", None)
        existing_contra = getattr(expense, "contra_cuenta_contable_id", None)
        if existing_account is not None and not (
            active
            and active.treatment == "partner_receivable"
            and existing_account == active.debtor_account_id
            and str(existing_contra)
            == (active.account_snapshot_json.get("liability") or {}).get("id")
        ):
            raise DirectConflict("account_mapping_conflict")
        if existing_account is None and existing_contra is not None:
            raise DirectConflict("account_mapping_conflict")
        # Fiscal declarations are typed; changing their meaning requires explicit revoke first.
        if getattr(item, "no_cfdi_decision_id", None):
            from .amex_direct_no_cfdi_service import (
                active_decision,
                load_decision_heads,
            )

            fiscal = active_decision(item, await load_decision_heads(session, [item]))
            if fiscal and (
                (fiscal.treatment == "full_amount_partner_receivable_no_tax")
                != (treatment == "partner_receivable")
                or action == "revoke"
            ):
                raise DirectConflict("no_cfdi_revoke_required")
        expenses[item.id] = expense
    if action == "revoke":
        previous_snapshots = [
            heads[i.account_treatment_decision_id].account_snapshot_json for i in items
        ]
        if any(snapshot != previous_snapshots[0] for snapshot in previous_snapshots):
            raise DirectConflict("account_treatment_homogeneous_batch_required")
        account_snapshot = previous_snapshots[0]
    policy = await policy_for_config(session, config, context)
    scope = actor_scope(config, actor_id)
    authorization = {
        "policy_id": str(policy.id),
        "config_text": json.dumps(
            config, sort_keys=True, default=str, separators=(",", ":")
        ),
        "config_hash": digest(config),
        "base_config_text": json.dumps(
            base_context_config(config),
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ),
        "base_config_hash": digest(base_context_config(config)),
        "actor_id": str(actor_id),
        "context_id": str(context.id),
        "context_version": context.version,
        "actor_scope_hash": digest(scope),
    }
    if treatment == "benefit_expense" and action == "set":
        authorization.update(benefit_grants(config, actor_id))
    elif treatment == "partner_receivable":
        authorization["partner_debtor_account_ids"] = sorted(
            debtor_account_ids(config, actor_id)
        )
    manifest = {
        "actor_id": str(actor_id),
        "import_id": str(import_id),
        "action": action,
        "treatment": treatment,
        "debtor_account_id": str(debtor_account_id) if debtor_account_id else None,
        "reason": reason,
        "expected_versions": versions,
        "source_version": state.source_version,
        "account_snapshot": account_snapshot,
        "authorization": authorization,
        "policy_id": str(policy.id),
        "items": [
            {
                "id": str(i.id),
                "source_hash": i.source_hash,
                "previous_decision_id": (
                    str(i.account_treatment_decision_id)
                    if getattr(i, "account_treatment_decision_id", None)
                    else None
                ),
                "expense_id": str(sources[i.id].imported_expense_id),
                "row_number": sources[i.id].source_json.get("row_number"),
                "description": sources[i.id].source_json.get("description"),
                "amount": format(abs(money(sources[i.id].raw_signed_amount)), ".2f"),
                "economic_source_hash": economic_source_hash(
                    statement, i, sources[i.id]
                ),
            }
            for i in sorted(items, key=lambda i: str(i.id))
        ],
    }
    manifest["preview_hash"] = digest(manifest)
    return statement, state, context, config, items, heads, expenses, manifest


async def preview_direct_account_treatment(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_ids: list[UUID],
    treatment: str,
    reason: str,
    expected_versions: dict[Any, int],
    debtor_account_id: UUID | None = None,
    action: str = "set",
) -> DirectResult:
    with session.no_autoflush:
        statement, state, context, _, items, _, _, manifest = await _plan(
            session,
            actor_id=actor_id,
            import_id=import_id,
            item_ids=item_ids,
            treatment=treatment,
            debtor_account_id=debtor_account_id,
            reason=reason,
            expected_versions=expected_versions,
            action=action,
            locked=False,
        )
    return DirectResult(
        "preview",
        statement=statement,
        context=context,
        items=items,
        source_version=state.source_version,
        progress={"manifest": manifest, "preview_hash": manifest["preview_hash"]},
    )


async def confirm_direct_account_treatment(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_ids: list[UUID],
    treatment: str,
    reason: str,
    expected_versions: dict[Any, int],
    expected_preview_hash: str,
    idempotency_key: str,
    confirmed: bool,
    debtor_account_id: UUID | None = None,
    action: str = "set",
) -> DirectResult:
    if not confirmed:
        raise DirectConflict("account_treatment_confirmation_required")
    if not idempotency_key.strip() or len(idempotency_key) > 160:
        raise DirectConflict("account_treatment_request_key_required")
    # Authorize before reading audit or returning a replay result.
    statement, state, context, _ = await load_statement(
        session, actor_id, import_id, "clasificar", locked=True, finance=True
    )
    await require_unfrozen(session, import_id)
    payload = {
        "confirmed": True,
        "actor_id": str(actor_id),
        "import_id": str(import_id),
        "items": sorted(str(v) for v in item_ids),
        "treatment": treatment,
        "action": action,
        "debtor_account_id": str(debtor_account_id) if debtor_account_id else None,
        "reason": reason.strip(),
        "expected_versions": {str(k): v for k, v in expected_versions.items()},
        "preview_hash": expected_preview_hash,
        "request_key": idempotency_key,
    }
    payload_hash = digest(payload)
    model = _model()
    keys = {
        i: digest({"request_key": idempotency_key, "item_id": str(i)}) for i in item_ids
    }
    existing = (
        (
            await session.execute(
                select(model).where(
                    model.import_id == import_id,
                    model.item_id.in_(item_ids),
                    model.idempotency_key.in_(list(keys.values())),
                )
            )
        )
        .scalars()
        .all()
    )
    if existing:
        if len(existing) != len(item_ids) or any(
            d.payload_hash != payload_hash for d in existing
        ):
            raise DirectConflict("account_treatment_idempotency_conflict")
        return DirectResult(
            "exists",
            statement=statement,
            context=context,
            source_version=state.source_version,
        )
    statement, state, context, config, items, heads, expenses, manifest = await _plan(
        session,
        actor_id=actor_id,
        import_id=import_id,
        item_ids=item_ids,
        treatment=treatment,
        debtor_account_id=debtor_account_id,
        reason=reason,
        expected_versions=expected_versions,
        action=action,
        locked=True,
    )
    if not expected_preview_hash or manifest["preview_hash"] != expected_preview_hash:
        raise DirectConflict("account_treatment_preview_changed")
    source_facts = {UUID(v["id"]): v for v in manifest["items"]}
    async with session.begin_nested():
        for item in sorted(items, key=lambda i: str(i.id)):
            prior = heads.get(getattr(item, "account_treatment_decision_id", None))
            if action == "revoke":
                if prior is None:
                    raise DirectConflict("account_treatment_transition_conflict")
                policy_id = prior.policy_id
                authorization = prior.authorization_json
            else:
                policy_id = UUID(manifest["policy_id"])
                authorization = manifest["authorization"]
            decision = model(
                id=uuid4(),
                item_id=item.id,
                import_id=import_id,
                policy_id=policy_id,
                imported_expense_id=expenses[item.id].id,
                context_id=context.id,
                context_version=context.version,
                revision=prior.revision + 1 if prior else 1,
                previous_decision_id=prior.id if prior else None,
                action=action,
                treatment=treatment,
                debtor_account_id=debtor_account_id,
                account_snapshot_json=manifest["account_snapshot"],
                authorization_json=authorization,
                reason=reason.strip(),
                actor_id=actor_id,
                request_id=idempotency_key,
                idempotency_key=keys[item.id],
                payload_hash=payload_hash,
                economic_source_hash=source_facts[item.id]["economic_source_hash"],
                source_version=item.source_version,
                source_hash=item.source_hash,
                versions_json=payload,
                created_at=datetime.now(timezone.utc),
            )
            session.add(decision)
            await session.flush()
            item.account_treatment_decision_id = decision.id
            expense = expenses[item.id]
            # A returned budget assignment was released by its own owner already.
            if treatment != "project_expense" or action == "revoke":
                item.tournament_id = item.project_code = item.edition_year = None
                item.budget_concept_id = item.budget_line_id = (
                    item.budget_version_id
                ) = item.budget_assignment_id = None
                expense.proyecto = (
                    ""  # Legacy column is NOT NULL; no operational project.
                )
                expense.edicion = None
                expense.budget_concept_id = None
                expense.cuenta_contable_budget_concept_id = None
            if action == "revoke" or treatment != "partner_receivable":
                if prior and prior.treatment == "partner_receivable":
                    if expense.cuenta_contable_id != prior.debtor_account_id:
                        raise DirectConflict("account_mapping_conflict")
                    expense.cuenta_contable_id = None
                    expense.contra_cuenta_contable_id = None
                item.budget_status = (
                    "unclassified"
                    if action == "revoke" or treatment == "project_expense"
                    else "classified"
                )
            else:
                expense.cuenta_contable_id = debtor_account_id
                expense.contra_cuenta_contable_id = UUID(
                    manifest["account_snapshot"]["liability"]["id"]
                )
                item.budget_status = "not_applicable"
            await record_event(
                session,
                item,
                state,
                actor_id,
                "account_treatment_" + action,
                {
                    "account_treatment_decision_id": str(decision.id),
                    "treatment": treatment,
                    "reason": reason.strip(),
                    "request_payload_hash": payload_hash,
                    "confirmed": True,
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
        items=items,
        source_version=state.source_version,
        progress={"manifest": manifest},
    )
