"""Audited recovery of demonstrable debits omitted only by declared period.

The owner never changes original headers, receipts or physical source fields.
It never commits, performs SAT calls or supplies fiscal/budget decisions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text

from ..models import (
    AmexCardAccount,
    AmexDirectCoverageRevision,
    AmexDirectRecoveredRow,
    AmexStatementImportItem,
    AmexStatementSourceFile,
    Empleado,
)
from .amex_direct_common import (
    DirectConflict,
    DirectResult,
    digest,
    load_items,
    load_statement,
    record_event,
    require_unfrozen,
)
from .amex_direct_intake_service import PARSER_VERSION, parse_direct_csv
from .expense_service import create_expense_from_data


def _json_source(row: dict[str, Any], file_hash: str) -> dict[str, Any]:
    return {
        **{
            k: str(v) if isinstance(v, (Decimal, datetime)) else v
            for k, v in row.items()
        },
        "file_sha256": file_hash,
    }


def _validate_period(start: date, end: date, statement: Any) -> None:
    if (
        not isinstance(start, date)
        or not isinstance(end, date)
        or (start > statement.period_start or end < statement.period_end or end < start)
    ):
        raise DirectConflict("recovery_period_must_contain_original")


async def _plan(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    period_start: date,
    period_end: date,
    reason: str,
    expected_source_version: int,
    locked: bool = False,
) -> tuple[Any, Any, Any, Any, list[Any], dict[str, Any]]:
    statement, state, context, _ = await load_statement(
        session, actor_id, import_id, "corregir_periodo", finance=True, locked=locked
    )
    await require_unfrozen(session, import_id)
    if (
        await session.execute(
            text("SELECT 1 FROM amex_direct_sign_revisions WHERE import_id=:id"),
            {"id": import_id},
        )
    ).first():
        raise DirectConflict("sign_correction_period_recovery_unsupported")
    _validate_period(period_start, period_end, statement)
    if not reason.strip() or len(reason) > 2000:
        raise DirectConflict("recovery_reason_required")
    if state.source_version != expected_source_version:
        raise DirectConflict("source_version_conflict")
    source = (
        await session.execute(
            select(AmexStatementSourceFile).where(
                AmexStatementSourceFile.import_id == import_id
            )
        )
    ).scalar_one_or_none()
    if source is None or (
        source.file_sha256 != statement.file_sha256
        or digest(source.content) != source.file_sha256
        or len(source.content) != source.byte_size
        or statement.parser_version != PARSER_VERSION
    ):
        raise DirectConflict("recovery_original_source_invalid")
    if context is None:
        raise DirectConflict("contexto_fiscal_pendiente")
    profile = statement.summary_json.get("profile")
    if profile != context.profile or context.currency != "MXN":
        raise DirectConflict("recovery_parser_context_conflict")
    rows = parse_direct_csv(
        source.content, profile, statement.summary_json.get("currency")
    )
    items = await load_items(session, import_id, locked=locked)
    from .amex_direct_no_cfdi_service import active_decision, load_decision_heads

    heads = await load_decision_heads(session, items)
    if any(active_decision(item, heads) for item in items):
        raise DirectConflict("no_cfdi_revoke_required")
    receipts = list(
        (
            await session.execute(
                select(AmexStatementImportItem).where(
                    AmexStatementImportItem.import_id == import_id
                )
            )
        ).scalars()
    )
    by_number = {r.row_number: r for r in receipts}
    by_receipt = {i.import_item_id: i for i in items}
    if (
        len(rows) != statement.total_rows
        or len(items) != len(rows)
        or len(by_number) != len(rows)
    ):
        raise DirectConflict("recovery_original_membership_invalid")
    recognized = (
        await session.execute(
            text(
                "SELECT 1 FROM amex_direct_effective_sources es WHERE es.import_id=:id "
                "AND (EXISTS(SELECT 1 FROM amex_recognition_representations rp WHERE "
                "rp.expense_id=es.imported_expense_id) OR EXISTS(SELECT 1 FROM "
                "amex_recognition_consumptions rc WHERE "
                "rc.imported_expense_id=es.imported_expense_id OR "
                "rc.source_report_expense_id=es.imported_expense_id)) LIMIT 1"
            ),
            {"id": import_id},
        )
    ).first()
    if recognized:
        raise DirectConflict("recovery_recognized_source")
    recoverable = []
    for row in rows:
        receipt = by_number.get(row["row_number"])
        if receipt is None:
            raise DirectConflict("recovery_original_row_proof_mismatch")
        item = by_receipt.get(receipt.id)
        original_row = dict(row)
        if (
            row["date"]
            and not statement.period_start <= row["date"].date() <= statement.period_end
        ):
            original_row.update(
                status="skipped",
                movement_kind="skipped",
                reason="row_outside_declared_period",
                amount=None,
            )
        expected = _json_source(original_row, source.file_sha256)
        if (
            item is None
            or receipt.normalized_json != expected
            or item.source_json != expected
        ):
            raise DirectConflict("recovery_original_row_proof_mismatch")
        if row["date"] is None or not period_start <= row["date"].date() <= period_end:
            raise DirectConflict("recovery_period_excludes_file")
        if item.movement_kind == "skipped":
            if (
                receipt.reason_code != "row_outside_declared_period"
                or row["movement_kind"] != "debit"
            ):
                raise DirectConflict("recovery_only_period_debits_supported")
            if (
                receipt.imported_expense_id is not None
                or item.imported_expense_id is not None
            ):
                raise DirectConflict("recovery_original_anchor_conflict")
            if any(
                (
                    item.cfdi_id,
                    item.status_receipt_id,
                    item.budget_assignment_id,
                    item.budget_line_id,
                )
            ) or (item.accounting_status != "pending"):
                raise DirectConflict("recovery_source_effect_conflict")
            recoverable.append((item, receipt, row))
        elif item.movement_kind not in {"debit", "credit"}:
            raise DirectConflict("recovery_unresolved_source")
    if not recoverable:
        raise DirectConflict("recovery_no_period_omissions")
    original_counts = dict(
        debit=statement.imported_count,
        credit=statement.credit_count,
        unknown=statement.unknown_count,
        skipped=statement.skipped_count,
    )
    effective_counts = dict(original_counts)
    effective_counts["debit"] += len(recoverable)
    effective_counts["skipped"] -= len(recoverable)
    ids = sorted(str(i.id) for i, _, _ in recoverable)
    plan = dict(
        import_id=str(import_id),
        file_sha256=source.file_sha256,
        source_file_id=str(source.id),
        parser_version=statement.parser_version,
        profile=profile,
        original_period_start=str(statement.period_start),
        original_period_end=str(statement.period_end),
        period_start=str(period_start),
        period_end=str(period_end),
        original_counts=original_counts,
        effective_counts=effective_counts,
        expected_count=len(ids),
        expected_item_ids=ids,
        expected_source_version=expected_source_version,
        context_id=str(context.id),
        context_version=context.version,
        reason=reason.strip(),
        actor_id=str(actor_id),
        membership_digest=hashlib.sha256(",".join(ids).encode()).hexdigest(),
        rows=[
            dict(
                item_id=str(i.id),
                row_number=r.row_number,
                date=str(row["date"].date()),
                amount=str(row["amount"]),
            )
            for i, r, row in recoverable
        ],
    )
    plan["preview_hash"] = digest(plan)
    return statement, state, context, source, recoverable, plan


async def preview_direct_period_recovery(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    period_start: date,
    period_end: date,
    reason: str,
    expected_source_version: int,
) -> DirectResult:
    """Reparse retained bytes and return a proof without any business writes."""
    try:
        statement, _, _, _ = await load_statement(
            session, actor_id, import_id, "corregir_periodo", finance=True
        )
        existing = (
            await session.execute(
                select(AmexDirectCoverageRevision).where(
                    AmexDirectCoverageRevision.import_id == import_id
                )
            )
        ).scalar_one_or_none()
        if existing:
            return DirectResult(
                "exists",
                statement=statement,
                progress={
                    "recovery_id": str(existing.id),
                    "period_start": str(existing.period_start),
                    "period_end": str(existing.period_end),
                },
            )
        statement, state, context, _, _, plan = await _plan(
            session,
            actor_id=actor_id,
            import_id=import_id,
            period_start=period_start,
            period_end=period_end,
            reason=reason,
            expected_source_version=expected_source_version,
        )
        return DirectResult(
            "preview",
            statement=statement,
            context=context,
            source_version=state.source_version,
            progress=plan,
        )
    except DirectConflict as exc:
        return DirectResult("pending", str(exc))


async def confirm_direct_period_recovery(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    period_start: date,
    period_end: date,
    reason: str,
    expected_source_version: int,
    preview_hash: str,
    idempotency_key: str,
    confirmed: bool,
) -> DirectResult:
    """Atomic missing anchors and append-only evidence; caller owns commit."""
    statement, _, _, _ = await load_statement(
        session, actor_id, import_id, "corregir_periodo", finance=True
    )
    if not confirmed or not idempotency_key or len(idempotency_key) > 200:
        return DirectResult("pending", "recovery_confirmation_required")
    request_hash = digest(
        dict(
            import_id=str(import_id),
            actor_id=str(actor_id),
            period_start=str(period_start),
            period_end=str(period_end),
            reason=reason.strip(),
            expected_source_version=expected_source_version,
            preview_hash=preview_hash,
            idempotency_key=idempotency_key,
        )
    )
    # Canonical file -> sorted periods -> import/state -> global recognition.
    keys = ["amex-file:" + statement.file_sha256] + sorted(
        {
            (
                f"amex-period:{statement.card_account_id}:"
                f"{statement.period_start}:{statement.period_end}"
            ),
            f"amex-period:{statement.card_account_id}:{period_start}:{period_end}",
        }
    )
    for key in keys:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {
                "key": int.from_bytes(
                    hashlib.sha256(key.encode()).digest()[:8], "big", signed=True
                )
            },
        )
    # Acquire import/global in load_statement's canonical order before replay.
    await load_statement(
        session, actor_id, import_id, "corregir_periodo", finance=True, locked=True
    )
    existing = (
        await session.execute(
            select(AmexDirectCoverageRevision).where(
                AmexDirectCoverageRevision.import_id == import_id
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.request_hash != request_hash:
            return DirectResult("pending", "recovery_idempotency_conflict")
        return DirectResult(
            "exists", statement=statement, progress={"recovery_id": str(existing.id)}
        )
    async with session.begin_nested():
        statement, state, _, source, recoverable, plan = await _plan(
            session,
            actor_id=actor_id,
            import_id=import_id,
            period_start=period_start,
            period_end=period_end,
            reason=reason,
            expected_source_version=expected_source_version,
            locked=True,
        )
        if plan["preview_hash"] != preview_hash:
            raise DirectConflict("recovery_preview_changed")
        revision = AmexDirectCoverageRevision(
            id=uuid4(),
            import_id=import_id,
            source_file_id=source.id,
            file_sha256=source.file_sha256,
            parser_version=statement.parser_version,
            profile=plan["profile"],
            original_period_start=statement.period_start,
            original_period_end=statement.period_end,
            period_start=period_start,
            period_end=period_end,
            original_counts=plan["original_counts"],
            effective_counts=plan["effective_counts"],
            expected_item_ids=plan["expected_item_ids"],
            expected_count=plan["expected_count"],
            expected_source_version=expected_source_version,
            actor_id=actor_id,
            reason=reason.strip(),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            preview_hash=preview_hash,
            membership_digest=plan["membership_digest"],
        )
        session.add(revision)
        await session.flush()
        card = await session.get(AmexCardAccount, statement.card_account_id)
        owner = await session.get(Empleado, statement.employee_id)
        if card is None or not card.active or owner is None or not owner.activo:
            raise DirectConflict("inactive_context")
        for item, receipt, row in recoverable:
            expense: Any = await create_expense_from_data(
                session=session,
                concepto=row["description"],
                gasto_cantidad=float(row["amount"]),
                fecha=row["date"],
                empleado_id=owner.id,
                nombre_enviador=owner.nombre,
                tipo_gasto="manual",
                metodo_pago="TARJETA CREDITO AMEX",
                ultimos_4_digitos=card.last4,
                origen="amex_batch",
            )
            expense.pagado_con_amex_empresa = True
            expense.currency = row["currency"]
            await session.flush()
            effective_json = _json_source(row, source.file_sha256)
            proof_hash = (
                await session.execute(
                    text(
                        (
                            "SELECT encode(sha256(convert_to(CAST(:source AS js"
                            "onb)::text,'UTF8')),'hex')"
                        )
                    ),
                    {"source": json.dumps(effective_json)},
                )
            ).scalar_one()
            session.add(
                AmexDirectRecoveredRow(
                    id=uuid4(),
                    revision_id=revision.id,
                    direct_item_id=item.id,
                    import_item_id=receipt.id,
                    imported_expense_id=expense.id,
                    row_number=receipt.row_number,
                    movement_kind="debit",
                    economic_date=row["date"].date(),
                    currency=row["currency"],
                    raw_signed_amount=row["raw_signed_amount"],
                    raw_amount=row["raw_amount"],
                    amount_column=row["amount_column"],
                    profile=row["profile"],
                    effective_source_json=effective_json,
                    proof_hash=proof_hash,
                )
            )
            await session.flush()
            # Existing operational fields are written on the original physical item.
            item.match_reason = "period_recovered_pending_cfdi"
            item.source_version += 1
            await record_event(
                session,
                item,
                state,
                actor_id,
                "period_recovered",
                {"revision_id": str(revision.id), "proof_hash": proof_hash},
                bump=False,
            )
        state.source_version += 1
        state.progress_json = None
        await session.flush()
    return DirectResult(
        "recovered",
        statement=statement,
        source_version=state.source_version,
        progress={**plan, "recovery_id": str(revision.id)},
    )
