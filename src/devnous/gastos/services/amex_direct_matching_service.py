"""Exact merchant/UUID policy with canonical fiscal claims and observed status."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from ..models import (
    AmexCFDIStatusReceipt,
    AmexDirectItem,
    AmexMerchantIdentityAlias,
    AmexRecognitionRepresentation,
    AmexStatementDirectContext,
    AmexStatementDirectState,
    AmexStatementImport,
    CFDIReport,
    ExpenseReport,
)
from .amex_accounting_identity_service import accounting_expense_condition
from .amex_direct_common import (
    DirectConflict,
    DirectResult,
    descriptor_key,
    digest,
    load_items,
    load_statement,
    money,
    normalize_rfc,
    record_event,
    require_unfrozen,
)
from .cfdi_expense_link_service import link_expense_to_cfdi_if_manual_uuid_set
from .cfdi_ingestion_service import (
    find_blocking_cfdi_usage,
    lock_cfdi_identity,
    validate_cfdi_material_identity,
)
from .cfdi_parser import parse_cfdi_xml

POLICY_VERSION = "amex_direct_match_v1"
STATUS_TTL = timedelta(hours=24)


def cfdi_identity(cfdi: Any) -> dict[str, Any]:
    if not cfdi.xml_raw or not cfdi.xml_parsed or str(cfdi.origen).lower() != "sat":
        raise DirectConflict("canonical_sat_xml_required")
    try:
        fiscal_uuid = str(UUID(str(cfdi.cfdi_uuid))).upper()
        parsed = parse_cfdi_xml(cfdi.xml_raw)
        if str(parsed.get("cfdi_uuid") or "").upper() != fiscal_uuid:
            raise ValueError
        validate_cfdi_material_identity(cfdi, parsed)
    except Exception as exc:
        raise DirectConflict("cfdi_material_identity_conflict") from exc
    if str(cfdi.tipo_de_comprobante or "").upper() != "I":
        raise DirectConflict("invoice_type_i_required")
    if str(cfdi.moneda or "").upper() != "MXN":
        raise DirectConflict("unsupported_direct_currency")
    return {
        "uuid": fiscal_uuid,
        "rfc_emisor": normalize_rfc(cfdi.emisor_rfc),
        "rfc_receptor": normalize_rfc(cfdi.receptor_rfc),
        "total": str(money(cfdi.total)),
        "xml_hash": digest(cfdi.xml_raw.encode("utf-8")),
    }


async def current_status_receipt(
    session: Any, cfdi: Any, *, now: datetime | None = None
) -> Any:
    identity = cfdi_identity(cfdi)
    current = now or datetime.now(timezone.utc)
    receipts = list(
        (
            await session.execute(
                select(AmexCFDIStatusReceipt)
                .where(
                    AmexCFDIStatusReceipt.cfdi_id == cfdi.id,
                    AmexCFDIStatusReceipt.request_hash == digest(identity),
                )
                .order_by(
                    AmexCFDIStatusReceipt.observed_at.desc(),
                    AmexCFDIStatusReceipt.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    if not receipts:
        return None
    receipt = receipts[0]
    observed = (
        receipt.observed_at
        if receipt.observed_at.tzinfo
        else receipt.observed_at.replace(tzinfo=timezone.utc)
    )
    if not timedelta(0) <= current - observed <= STATUS_TTL:
        return None
    if (
        receipt.xml_hash != identity["xml_hash"]
        or receipt.fiscal_uuid != identity["uuid"]
        or receipt.issuer_rfc != identity["rfc_emisor"]
        or receipt.receiver_rfc != identity["rfc_receptor"]
        or money(receipt.amount) != money(identity["total"])
    ):
        return None
    return receipt


def fiscal_source_hash(item: Any, context: Any, expense: Any) -> str:
    return digest(
        {
            "row": item.source_json,
            "context_id": str(context.id),
            "context_version": context.version,
            "expense_id": str(expense.id),
            "amount": str(money(expense.gasto_cantidad)),
            "date": str(expense.fecha),
            "currency": expense.currency,
            "last4": expense.ultimos_4_digitos,
            "active": expense.estado_gasto,
            "origin": expense.origen,
        }
    )


async def _merchant(
    session: Any, item: Any, context: Any, card_id: UUID
) -> tuple[str | None, Any]:
    if item.issuer_rfc:
        return normalize_rfc(item.issuer_rfc), None
    aliases = list(
        (
            await session.execute(
                select(AmexMerchantIdentityAlias).where(
                    AmexMerchantIdentityAlias.entity_code == context.entity_code,
                    AmexMerchantIdentityAlias.card_account_id == card_id,
                    AmexMerchantIdentityAlias.descriptor
                    == descriptor_key(item.source_json["description"]),
                    AmexMerchantIdentityAlias.active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(aliases) != 1:
        return None, None
    return aliases[0].issuer_rfc, aliases[0]


async def eligible_candidates(
    session: Any, item: Any, context: Any, statement: Any, expense: Any
) -> list[tuple[Any, Any, Any]]:
    if (
        item.movement_kind != "debit"
        or expense is None
        or expense.estado_gasto != "activo"
        or str(expense.currency).upper() != "MXN"
    ):
        return []
    merchant, alias = await _merchant(session, item, context, statement.card_account_id)
    if not item.declared_uuid and not merchant:
        return []
    query = (
        select(CFDIReport)
        .where(
            CFDIReport.origen == "sat",
            func.upper(func.trim(CFDIReport.receptor_rfc)) == context.receiver_rfc,
            func.upper(CFDIReport.moneda) == "MXN",
            func.upper(CFDIReport.tipo_de_comprobante) == "I",
        )
        .order_by(CFDIReport.id)
    )
    if item.declared_uuid:
        query = query.where(func.upper(CFDIReport.cfdi_uuid) == item.declared_uuid)
    else:
        query = query.where(
            func.upper(func.trim(CFDIReport.emisor_rfc)) == merchant,
            CFDIReport.fecha >= expense.fecha - timedelta(days=3),
            CFDIReport.fecha <= expense.fecha + timedelta(days=3),
        )
    candidates = []
    for cfdi in (await session.execute(query)).scalars().all():
        try:
            identity = cfdi_identity(cfdi)
        except DirectConflict:
            continue
        if money(identity["total"]) != money(expense.gasto_cantidad):
            continue
        receipt = await current_status_receipt(session, cfdi)
        if receipt is None or receipt.observed_status != "vigente":
            continue
        if await _available(session, cfdi, expense):
            candidates.append((cfdi, receipt, alias))
    return candidates


async def _available(
    session: Any, cfdi: Any, expense: Any, *, shared: bool = False
) -> bool:
    if not shared and await find_blocking_cfdi_usage(session, cfdi.id):
        return False
    if shared:
        return True  # Actual amount/late-evidence guards run in the canonical owner.
    uses = (
        await session.execute(
            select(ExpenseReport.id)
            .where(
                ExpenseReport.id != expense.id,
                ExpenseReport.estado_gasto != "cancelado",
                accounting_expense_condition(ExpenseReport),
                (ExpenseReport.cfdi_report_id == cfdi.id)
                | (
                    func.upper(func.trim(ExpenseReport.cfdi_uuid_manual))
                    == str(cfdi.cfdi_uuid).upper()
                ),
            )
            .limit(1)
        )
    ).first()
    return uses is None


async def _reverse_unique(session: Any, item: Any, cfdi: Any) -> bool:
    # No candidate LIMIT and no cursor supplied by clients: the policy universe
    # includes every active current direct source, not only the displayed import.
    others = list(
        (
            await session.execute(
                select(AmexDirectItem)
                .where(
                    AmexDirectItem.id != item.id,
                    AmexDirectItem.movement_kind == "debit",
                    AmexDirectItem.accounting_status != "recognized",
                )
                .order_by(AmexDirectItem.id)
            )
        )
        .scalars()
        .all()
    )
    for other in others:
        state = await session.get(AmexStatementDirectState, other.import_id)
        if not state or not state.context_id or other.context_id != state.context_id:
            # A potentially competing same-descriptor source without context is
            # incomplete evidence, not an authorized zero-candidate universe.
            if descriptor_key(
                other.source_json.get("description", "")
            ) == descriptor_key(
                item.source_json.get("description", "")
            ) and other.source_json.get(
                "amount"
            ) == item.source_json.get(
                "amount"
            ):
                return False
            continue
        context = await session.get(AmexStatementDirectContext, state.context_id)
        statement = await session.get(AmexStatementImport, other.import_id)
        expense = await session.get(ExpenseReport, other.imported_expense_id)
        if context.receiver_rfc != str(cfdi.receptor_rfc).strip().upper():
            continue
        if any(
            candidate[0].id == cfdi.id
            for candidate in await eligible_candidates(
                session, other, context, statement, expense
            )
        ):
            return False
    # Unproven legacy sign cannot establish eligibility. Do not ignore a
    # same-identity candidate merely because its source predates strict intake.
    legacy = list(
        (
            await session.execute(
                select(ExpenseReport).where(
                    ExpenseReport.origen == "amex_batch",
                    ExpenseReport.estado_gasto == "activo",
                    ExpenseReport.id != item.imported_expense_id,
                    ~select(AmexDirectItem.id)
                    .where(AmexDirectItem.imported_expense_id == ExpenseReport.id)
                    .exists(),
                    ~select(AmexRecognitionRepresentation.id)
                    .where(AmexRecognitionRepresentation.expense_id == ExpenseReport.id)
                    .exists(),
                )
            )
        )
        .scalars()
        .all()
    )
    for expense in legacy:
        if money(expense.gasto_cantidad) == money(cfdi.total) and descriptor_key(
            expense.concepto or ""
        ) == descriptor_key(item.source_json.get("description", "")):
            return False
    return True


async def confirm_merchant_alias(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    descriptor: str,
    issuer_rfc: str,
    reason: str,
    confirmed: bool,
) -> DirectResult:
    statement, state, context, config = await load_statement(
        session, actor_id, import_id, "alias", locked=True, finance=True
    )
    await require_unfrozen(session, import_id)
    if (
        not context
        or not confirmed
        or not reason.strip()
        or not descriptor_key(descriptor)
    ):
        return DirectResult("pending", "merchant_identity_confirmation_required")
    issuer = normalize_rfc(issuer_rfc)
    payload = {
        "entity_code": context.entity_code,
        "card_account_id": str(statement.card_account_id),
        "descriptor": descriptor_key(descriptor),
        "issuer_rfc": issuer,
        "reason": reason.strip(),
    }
    aliases = list(
        (
            await session.execute(
                select(AmexMerchantIdentityAlias)
                .where(
                    AmexMerchantIdentityAlias.entity_code == context.entity_code,
                    AmexMerchantIdentityAlias.card_account_id
                    == statement.card_account_id,
                    AmexMerchantIdentityAlias.descriptor == payload["descriptor"],
                )
                .order_by(AmexMerchantIdentityAlias.version.desc())
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if aliases and aliases[0].active and aliases[0].issuer_rfc == issuer:
        return DirectResult(
            "exists", statement=statement, source_version=state.source_version
        )
    async with session.begin_nested():
        for prior in aliases:
            if prior.active:
                prior.active = False
        await session.flush()
        alias = AmexMerchantIdentityAlias(
            id=uuid4(),
            **{k: v for k, v in payload.items() if k != "card_account_id"},
            card_account_id=statement.card_account_id,
            version=(aliases[0].version + 1 if aliases else 1),
            active=True,
            actor_id=actor_id,
            payload_hash=digest(payload),
        )
        session.add(alias)
        await session.flush()
        state.source_version += 1
        state.progress_json = None
        await session.flush()
    return DirectResult(
        "ready", statement=statement, source_version=state.source_version
    )


async def _link(
    session: Any,
    item: Any,
    state: Any,
    context: Any,
    expense: Any,
    cfdi: Any,
    receipt: Any,
    alias: Any,
    actor_id: UUID,
    *,
    manual: bool = False,
    reason: str = "",
    shared: bool = False,
    tip: Decimal = Decimal(0),
) -> None:
    from .documento_service import validate_shared_cfdi_payment_amount

    if expense.cfdi_report_id not in (None, cfdi.id):
        raise DirectConflict("foreign_cfdi_application_conflict")
    if (
        context.receiver_rfc != normalize_rfc(cfdi.receptor_rfc)
        or str(cfdi.moneda).upper() != "MXN"
    ):
        raise DirectConflict("receiver_or_currency_conflict")
    await lock_cfdi_identity(session, cfdi.cfdi_uuid)
    cfdi = await session.get(
        CFDIReport, cfdi.id, populate_existing=True, with_for_update=True
    )
    fresh = await current_status_receipt(session, cfdi)
    if not fresh or fresh.id != receipt.id or fresh.observed_status != "vigente":
        raise DirectConflict("fresh_sat_vigency_required")
    if (
        context.receiver_rfc != normalize_rfc(cfdi.receptor_rfc)
        or str(cfdi.moneda).upper() != "MXN"
    ):
        raise DirectConflict("receiver_or_currency_conflict")
    if not await _available(session, cfdi, expense, shared=shared):
        raise DirectConflict("cfdi_already_used")
    if shared or tip:
        await validate_shared_cfdi_payment_amount(
            session,
            cfdi_report=cfdi,
            requested_amount=money(expense.gasto_cantidad) - tip,
            exclude_expense_id=expense.id,
        )
    before = {
        "cfdi_id": expense.cfdi_report_id,
        "manual_uuid": expense.cfdi_uuid_manual,
        "tip": expense.propina_no_deducible,
    }
    expense.cfdi_uuid_manual = str(UUID(cfdi.cfdi_uuid)).upper()
    expense.propina_no_deducible = float(tip)
    await link_expense_to_cfdi_if_manual_uuid_set(
        session,
        expense,
        require_unique=True,
        allow_shared=shared,
        shared_reason=reason if shared else None,
        actor_id=actor_id,
    )
    item.cfdi_id = cfdi.id
    item.status_receipt_id = receipt.id
    item.merchant_alias_id = alias.id if alias else None
    item.match_status = "matched"
    item.match_reason = None
    item.match_proof = {
        "policy": POLICY_VERSION,
        "manual": manual,
        "reason": reason,
        "shared_confirmed": shared,
        "tip_amount": str(tip),
        "context_id": str(context.id),
        "context_version": context.version,
        "source_hash": fiscal_source_hash(item, context, expense),
        "cfdi_hash": receipt.request_hash,
        "alias_id": str(alias.id) if alias else None,
        "alias_version": alias.version if alias else None,
        "merchant_rfc": cfdi.emisor_rfc,
        "declared_uuid": item.declared_uuid,
        "status_receipt_id": str(receipt.id),
        "actor_id": str(actor_id),
    }
    await record_event(
        session,
        item,
        state,
        actor_id,
        "manual_match" if manual else "automatic_match",
        {"before": before, "proof": item.match_proof},
    )


async def reconcile_direct_statement(
    session: Any, *, actor_id: UUID, import_id: UUID, expected_source_version: int
) -> DirectResult:
    from .amex_recognition_service import load_expenses

    statement, state, context, config = await load_statement(
        session, actor_id, import_id, "conciliar", locked=True, finance=True
    )
    await require_unfrozen(session, import_id)
    if not context or state.source_version != expected_source_version:
        return DirectResult(
            "pending",
            "stale_source_version" if context else "contexto_fiscal_pendiente",
        )
    items = await load_items(session, import_id, locked=True)
    expenses = {
        e.id: e
        for e in await load_expenses(
            session, [i.imported_expense_id for i in items if i.imported_expense_id]
        )
    }
    for item in items:
        if item.movement_kind != "debit" or item.accounting_status == "recognized":
            continue
        expense = expenses[item.imported_expense_id]
        if item.match_status == "matched" and item.cfdi_id:
            current = await session.get(CFDIReport, item.cfdi_id)
            try:
                latest = (
                    await current_status_receipt(session, current) if current else None
                )
            except DirectConflict:
                latest = None
            if latest is None or latest.observed_status != "vigente":
                if (
                    item.match_status != "pending"
                    or item.match_reason != "fresh_sat_vigency_required"
                ):
                    before = {
                        "match_status": item.match_status,
                        "match_reason": item.match_reason,
                        "status_receipt_id": item.status_receipt_id,
                    }
                    item.match_status = "pending"
                    item.match_reason = "fresh_sat_vigency_required"
                    await record_event(
                        session,
                        item,
                        state,
                        actor_id,
                        "match_pending",
                        {"before": before, "reason": item.match_reason},
                    )
                continue
            current_alias = (
                await session.get(
                    AmexMerchantIdentityAlias,
                    item.merchant_alias_id,
                    populate_existing=True,
                )
                if item.merchant_alias_id
                else None
            )
            alias_current = not item.merchant_alias_id or bool(
                current_alias
                and current_alias.active
                and current_alias.version
                == (item.match_proof or {}).get("alias_version")
            )
            if (
                alias_current
                and latest.id == item.status_receipt_id
                and item.match_proof
                and item.match_proof["source_hash"]
                == fiscal_source_hash(item, context, expense)
            ):
                continue
        candidates = await eligible_candidates(
            session, item, context, statement, expense
        )
        reason = "merchant_identity_or_fresh_sat_evidence_pending"
        if len(candidates) > 1:
            reason = "ambiguous_cfdi_candidates"
        elif len(candidates) == 1:
            cfdi, receipt, alias = candidates[0]
            if not await _reverse_unique(session, item, cfdi):
                reason = "global_bidirectional_match_conflict"
            else:
                try:
                    async with session.begin_nested():
                        await _link(
                            session,
                            item,
                            state,
                            context,
                            expense,
                            cfdi,
                            receipt,
                            alias,
                            actor_id,
                        )
                        await session.flush()
                    continue
                except (DirectConflict, ValueError):
                    await session.refresh(item)
                    await session.refresh(state)
                    await session.refresh(expense)
                    reason = "cfdi_global_usage_conflict"
        if item.match_status != "pending" or item.match_reason != reason:
            before = {
                "match_status": item.match_status,
                "match_reason": item.match_reason,
            }
            item.match_status = "pending"
            item.match_reason = reason
            await record_event(
                session,
                item,
                state,
                actor_id,
                "match_pending",
                {"before": before, "reason": reason},
            )
    await session.flush()
    return DirectResult(
        "matched",
        statement=statement,
        context=context,
        items=items,
        source_version=state.source_version,
    )


async def link_direct_cfdi_manual(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_id: UUID,
    cfdi_id: UUID,
    expected_source_version: int,
    reason: str,
    confirmed: bool,
    shared_confirmed: bool = False,
    applied_amount: Decimal | None = None,
    tip_amount: Decimal | None = None,
) -> DirectResult:
    from .amex_recognition_service import load_expenses

    statement, state, context, config = await load_statement(
        session, actor_id, import_id, "conciliar", locked=True, finance=True
    )
    await require_unfrozen(session, import_id)
    if (
        not context
        or state.source_version != expected_source_version
        or not confirmed
        or not reason.strip()
    ):
        return DirectResult("pending", "manual_matching_confirmation_required")
    item = await session.get(
        AmexDirectItem, item_id, populate_existing=True, with_for_update=True
    )
    if item is None or item.import_id != import_id or item.movement_kind != "debit":
        raise DirectConflict("item_context_mismatch")
    expense = (await load_expenses(session, [item.imported_expense_id]))[0]
    cfdi = await session.get(
        CFDIReport, cfdi_id, populate_existing=True, with_for_update=True
    )
    if cfdi is None:
        return DirectResult("pending", "cfdi_missing")
    cfdi_identity(cfdi)
    receipt = await current_status_receipt(session, cfdi)
    if receipt is None or receipt.observed_status != "vigente":
        return DirectResult("pending", "fresh_sat_vigency_required")
    tip = money(tip_amount) if tip_amount is not None else Decimal(0)
    if tip < 0 or tip >= money(expense.gasto_cantidad):
        return DirectResult("pending", "invalid_confirmed_tip")
    applied = (
        money(applied_amount)
        if applied_amount is not None
        else money(expense.gasto_cantidad) - tip
    )
    if applied != money(expense.gasto_cantidad) - tip or (
        not shared_confirmed and applied != money(cfdi.total)
    ):
        return DirectResult("pending", "manual_applied_amount_conflict")
    try:
        async with session.begin_nested():
            if item.cfdi_id and item.cfdi_id != cfdi.id:
                await release_direct_application(
                    session, item, actor_id=actor_id, reason=reason
                )
                item.cfdi_id = None
                item.match_proof = None
            await _link(
                session,
                item,
                state,
                context,
                expense,
                cfdi,
                receipt,
                None,
                actor_id,
                manual=True,
                reason=reason.strip(),
                shared=shared_confirmed,
                tip=tip,
            )
            await session.flush()
    except ValueError:
        return DirectResult("pending", "cfdi_global_usage_conflict")
    return DirectResult(
        "matched",
        statement=statement,
        context=context,
        items=[item],
        source_version=state.source_version,
    )


async def release_direct_application(
    session: Any, item: Any, *, actor_id: UUID, reason: str
) -> None:
    from ..models import Aprobacion

    expense = await session.get(
        ExpenseReport,
        item.imported_expense_id,
        populate_existing=True,
        with_for_update=True,
    )
    if (
        expense is None
        or not item.match_proof
        or item.cfdi_id != expense.cfdi_report_id
    ):
        raise DirectConflict("foreign_cfdi_application_conflict")
    if (
        await session.execute(
            select(AmexRecognitionRepresentation.id).where(
                AmexRecognitionRepresentation.expense_id == expense.id
            )
        )
    ).first():
        raise DirectConflict("recognized_source_immutable")
    await lock_cfdi_identity(session, expense.cfdi_uuid_manual)
    expense.cfdi_uuid_manual = None
    # Reuse the canonical owner to clear this origin's unrecognized application.
    await link_expense_to_cfdi_if_manual_uuid_set(
        session, expense, clear_report_if_no_match=True
    )
    expense.cfdi_compartido_confirmado = False
    expense.cfdi_compartido_motivo = None
    expense.propina_no_deducible = 0
    session.add(
        Aprobacion(
            tipo_entidad="gasto",
            entidad_id=expense.id,
            aprobador_id=actor_id,
            accion="editar",
            fecha=datetime.now(timezone.utc),
            comentario=(
                "Liberación de aplicación AMEX directo por "
                "corrección explícita de contexto: " + reason
            ),
        )
    )


async def exclude_direct_credit(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_id: UUID,
    expected_source_version: int,
    reason: str,
    confirmed: bool,
) -> DirectResult:
    statement, state, context, config = await load_statement(
        session, actor_id, import_id, "excluir_credito", locked=True, finance=True
    )
    await require_unfrozen(session, import_id)
    if (
        state.source_version != expected_source_version
        or not confirmed
        or not reason.strip()
    ):
        return DirectResult("pending", "credit_exclusion_confirmation_required")
    item = await session.get(
        AmexDirectItem, item_id, populate_existing=True, with_for_update=True
    )
    if item is None or item.import_id != import_id or item.movement_kind != "credit":
        raise DirectConflict("known_credit_required")
    if item.credit_excluded:
        return DirectResult(
            "exists", statement=statement, source_version=state.source_version
        )
    item.credit_excluded = True
    await record_event(
        session,
        item,
        state,
        actor_id,
        "credit_exclusion",
        {"reason": reason, "not_payment": True, "not_fiscal_reversal": True},
    )
    await session.flush()
    return DirectResult(
        "ready", statement=statement, source_version=state.source_version
    )
