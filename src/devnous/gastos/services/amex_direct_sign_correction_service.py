"""Audited sign reinterpretation; original bank evidence is never rewritten.

The caller owns the transaction and commit. Preview performs no writes; apply
uses the statement/global recognition locks and an atomic nested transaction.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text

from ..models import (
    AmexCardAccount,
    AmexDirectSignRevision,
    AmexDirectSignRow,
    AmexStatementImportItem,
    AmexStatementSourceFile,
    Aprobacion,
    Empleado,
    ExpenseReport,
)
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
from .amex_direct_intake_service import PARSER_VERSION, parse_direct_csv
from .amex_direct_recovery_service import _json_source
from .expense_service import create_expense_from_data

OPPOSITE_PROFILES = {
    "debit_negative": "debit_positive",
    "debit_positive": "debit_negative",
}


def _validate_item(item: Any) -> None:
    if (
        item.accounting_status != "pending"
        or item.budget_status != "unclassified"
        or item.match_status != "pending"
        or item.credit_excluded
        or any(
            getattr(item, field, None)
            for field in (
                "cfdi_id",
                "status_receipt_id",
                "merchant_alias_id",
                "match_proof",
                "no_cfdi_decision_id",
                "tournament_id",
                "project_code",
                "edition_year",
                "budget_concept_id",
                "budget_line_id",
                "budget_version_id",
                "budget_assignment_id",
            )
        )
    ):
        raise DirectConflict("sign_source_effect_conflict")


def _validate_expense(
    expense: Any, statement: Any, row: dict[str, Any], card: Any
) -> None:
    if (
        expense is None
        or expense.estado_gasto != "activo"
        or expense.origen != "amex_batch"
        or expense.empleado_id != statement.employee_id
        or expense.fecha != row["date"]
        or money(expense.gasto_cantidad) != abs(row["raw_signed_amount"])
        or expense.concepto != row["description"]
        or expense.currency != "MXN"
        or expense.ultimos_4_digitos != card.last4
        or expense.pagado_con_amex_empresa is not True
        or expense.estado_reembolso not in (None, "pendiente")
        or expense.estado_factura not in (None, "pendiente")
        or any(
            getattr(expense, key, None)
            for key in (
                "documento_id",
                "solicitud_documento_id",
                "informe_documento_id",
                "cuenta_gastos_id",
                "referencia_base",
                "budget_concept_id",
                "cuenta_contable_id",
                "contra_cuenta_contable_id",
                "cuenta_iva_id",
                "cfdi_report_id",
                "cfdi_uuid_manual",
                "numero_factura",
                "nova_request_id",
                "fecha_reembolso",
                "cancelado_en",
                "cancelado_por_id",
                "motivo_cancelacion",
            )
        )
    ):
        raise DirectConflict("sign_original_expense_conflict")


async def _require_independent(session: Any, import_id: UUID, items: list[Any]) -> None:
    """Inspect declared FK owners, plus known polymorphic accounting/budget owners.

    Catalog names are quoted identifiers supplied by PostgreSQL, never user SQL.
    Every dependent owner fails closed except original immutable intake/context
    rows and context-only audit events. This covers dependencies even when the
    corresponding screen flag was not updated.
    """
    allowed = {
        ("amex_statement_import_items", "import_id"),
        ("amex_direct_items", "import_id"),
        ("amex_direct_items", "imported_expense_id"),
        ("amex_statement_import_items", "imported_expense_id"),
        ("amex_statement_source_files", "import_id"),
        ("amex_statement_direct_contexts", "import_id"),
        ("amex_statement_direct_state", "import_id"),
        ("amex_direct_events", "item_id"),
    }
    ids = {
        "expense_reports": [
            i.imported_expense_id for i in items if i.imported_expense_id
        ],
        "amex_direct_items": [i.id for i in items],
        "amex_statement_imports": [import_id],
    }
    dependencies = (
        (
            await session.execute(
                text(
                    """
            SELECT ns.nspname AS schema_name, c.relname AS table_name,
                   a.attname AS column_name, target.relname AS target_name
            FROM pg_constraint fk
            JOIN pg_class c ON c.oid=fk.conrelid
            JOIN pg_namespace ns ON ns.oid=c.relnamespace
            JOIN pg_class target ON target.oid=fk.confrelid
            JOIN LATERAL unnest(fk.conkey, fk.confkey)
              AS key_pair(source_num,target_num)
              ON true
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=key_pair.source_num
            JOIN pg_attribute ta ON ta.attrelid=target.oid
              AND ta.attnum=key_pair.target_num
            WHERE fk.contype='f' AND ta.attname='id' AND fk.confrelid IN
              ('expense_reports'::regclass,'amex_direct_items'::regclass,
               'amex_statement_imports'::regclass)
            ORDER BY ns.nspname,c.relname,a.attname
        """
                )
            )
        )
        .mappings()
        .all()
    )
    for dependency in dependencies:
        table, column = dependency["table_name"], dependency["column_name"]
        if (table, column) in allowed:
            continue
        values = ids[dependency["target_name"]]
        if not values:
            continue

        def quote(value: str) -> str:
            return '"' + value.replace('"', '""') + '"'

        qualified = quote(dependency["schema_name"]) + "." + quote(table)
        found = (
            await session.execute(
                text(
                    f"SELECT 1 FROM {qualified} WHERE {quote(column)} "
                    "= ANY(CAST(:ids AS uuid[])) LIMIT 1"
                ),
                {"ids": values},
            )
        ).first()
        if found:
            raise DirectConflict("sign_incompatible_dependency")
    # These owners use polymorphic keys rather than an expense FK.
    found = (
        await session.execute(
            text(
                """
              SELECT 1 FROM amex_direct_events ev
              WHERE ev.item_id=ANY(CAST(:items AS uuid[]))
                AND ev.action!='context_revision'
              UNION ALL SELECT 1 FROM accounting_poliza_lines l
              WHERE l.raw_row_json->>'expense_id'=ANY(CAST(:expenses AS text[]))
                 OR l.raw_row_json->>'direct_item_id'=ANY(CAST(:item_strings AS text[]))
              UNION ALL SELECT 1 FROM budget_movement_assignments b
              WHERE b.movement_key=ANY(CAST(:keys AS text[]))
              UNION ALL SELECT 1 FROM aprobaciones a
              WHERE a.tipo_entidad='gasto'
                AND a.entidad_id=ANY(CAST(:expense_ids AS uuid[]))
              LIMIT 1
            """
            ),
            {
                "items": ids["amex_direct_items"],
                "item_strings": [str(v) for v in ids["amex_direct_items"]],
                "expenses": [str(v) for v in ids["expense_reports"]],
                "expense_ids": ids["expense_reports"],
                "keys": ["expense:" + str(v) for v in ids["expense_reports"]],
            },
        )
    ).first()
    if found:
        raise DirectConflict("sign_incompatible_dependency")


async def _plan(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    profile: str,
    reason: str,
    expected_source_version: int,
    locked: bool = False,
) -> tuple[Any, Any, Any, Any, list[Any], dict[str, Any]]:
    statement, state, context, _ = await load_statement(
        session, actor_id, import_id, "corregir_signos", finance=True, locked=locked
    )
    await require_unfrozen(session, import_id)
    if not reason.strip() or len(reason) > 1000:
        raise DirectConflict("sign_reason_required")
    if state.source_version != expected_source_version:
        raise DirectConflict("source_version_conflict")
    original_profile = statement.summary_json.get("profile")
    if OPPOSITE_PROFILES.get(original_profile) != profile:
        raise DirectConflict("sign_opposite_profile_required")
    if (
        context is None
        or context.profile != original_profile
        or context.currency != "MXN"
    ):
        raise DirectConflict("sign_parser_context_conflict")
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
        raise DirectConflict("sign_original_source_invalid")
    before = parse_direct_csv(source.content, original_profile, "MXN")
    after = parse_direct_csv(source.content, profile, "MXN")
    items = await load_items(session, import_id, locked=locked)
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
    if not (
        len(before)
        == len(after)
        == statement.total_rows
        == len(items)
        == len(receipts)
        == len(by_number)
        == len(by_receipt)
    ):
        raise DirectConflict("sign_original_membership_invalid")
    card = await session.get(AmexCardAccount, statement.card_account_id)
    owner = await session.get(Empleado, statement.employee_id)
    if card is None or not card.active or owner is None or not owner.activo:
        raise DirectConflict("inactive_context")
    expense_ids = sorted(
        (i.imported_expense_id for i in items if i.imported_expense_id), key=str
    )
    query = (
        select(ExpenseReport)
        .where(ExpenseReport.id.in_(expense_ids))
        .order_by(ExpenseReport.id)
        .execution_options(populate_existing=True)
    )
    if locked:
        query = query.with_for_update()
    expenses = {e.id: e for e in (await session.execute(query)).scalars()}
    await _require_independent(session, import_id, items)
    changes = []
    rows = []
    for old, new in zip(before, after):
        receipt = by_number.get(old["row_number"])
        if receipt is None:
            raise DirectConflict("sign_original_row_proof_mismatch")
        item = by_receipt.get(receipt.id)
        expected = _json_source(old, source.file_sha256)
        if (
            item is None
            or receipt.normalized_json != expected
            or item.source_json != expected
            or receipt.workflow_mode != "direct"
            or receipt.status != old["status"]
            or receipt.reason_code != old["reason"]
            or item.movement_kind != old["movement_kind"]
            or item.raw_signed_amount != old["raw_signed_amount"]
            or item.raw_amount != old["raw_amount"]
            or item.amount_column != old["amount_column"]
            or item.profile != original_profile
            or item.profile_version != PARSER_VERSION
            or item.context_id != context.id
            or item.context_version != context.version
            or receipt.imported_expense_id != item.imported_expense_id
        ):
            raise DirectConflict("sign_original_row_proof_mismatch")
        if (
            old["movement_kind"] not in {"debit", "credit"}
            or old["reason"]
            or new["movement_kind"] not in {"debit", "credit"}
            or new["reason"]
            or old["movement_kind"] == new["movement_kind"]
            or old["date"] is None
            or not statement.period_start <= old["date"].date() <= statement.period_end
        ):
            raise DirectConflict("sign_unresolved_source")
        _validate_item(item)
        if old["movement_kind"] == "debit":
            _validate_expense(
                expenses.get(item.imported_expense_id), statement, old, card
            )
        elif item.imported_expense_id is not None:
            raise DirectConflict("sign_original_anchor_conflict")
        effective = _json_source(new, source.file_sha256)
        rows.append(
            (item, receipt, new, effective, expenses.get(item.imported_expense_id))
        )
        changes.append(
            dict(
                item_id=str(item.id),
                import_item_id=str(receipt.id),
                row_number=receipt.row_number,
                source_version=item.source_version,
                source_hash=item.source_hash,
                old_kind=old["movement_kind"],
                movement_kind=new["movement_kind"],
                original_expense_id=(
                    str(item.imported_expense_id) if item.imported_expense_id else None
                ),
                date=str(new["date"].date()),
                amount=str(abs(new["raw_signed_amount"])),
                source_digest=digest(effective),
            )
        )

    def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {
            k: sum(r["movement_kind"] == k for r in rows)
            for k in ("debit", "credit", "unknown", "skipped")
        }

    def totals(rows: list[dict[str, Any]]) -> dict[str, str]:
        return {
            k: str(
                sum(
                    (
                        abs(r["raw_signed_amount"])
                        for r in rows
                        if r["movement_kind"] == k
                    ),
                    Decimal("0.00"),
                )
            )
            for k in ("debit", "credit")
        }

    original_counts = counts(before)
    if original_counts != dict(
        debit=statement.imported_count,
        credit=statement.credit_count,
        unknown=statement.unknown_count,
        skipped=statement.skipped_count,
    ):
        raise DirectConflict("sign_original_membership_invalid")
    ids = sorted(str(i.id) for i in items)
    plan = dict(
        import_id=str(import_id),
        source_file_id=str(source.id),
        file_sha256=source.file_sha256,
        parser_version=statement.parser_version,
        original_profile=original_profile,
        profile=profile,
        actor_id=str(actor_id),
        reason=reason.strip(),
        expected_source_version=expected_source_version,
        context_id=str(context.id),
        context_version=context.version,
        context_hash=context.payload_hash,
        expected_count=len(items),
        expected_item_ids=ids,
        membership_digest=hashlib.sha256(",".join(ids).encode()).hexdigest(),
        original_counts=original_counts,
        effective_counts=counts(after),
        original_totals=totals(before),
        effective_totals=totals(after),
        rows=changes,
    )
    plan["preview_hash"] = digest(plan)
    return statement, state, context, source, rows, plan


async def preview_direct_sign_correction(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    profile: str,
    reason: str,
    expected_source_version: int,
) -> DirectResult:
    """Reproduce every original receipt and return a write-free correction plan."""
    try:
        statement, _, _, _ = await load_statement(
            session, actor_id, import_id, "corregir_signos", finance=True
        )
        existing = (
            await session.execute(
                select(AmexDirectSignRevision).where(
                    AmexDirectSignRevision.import_id == import_id
                )
            )
        ).scalar_one_or_none()
        if existing:
            return DirectResult(
                "exists",
                statement=statement,
                progress={"sign_revision_id": str(existing.id)},
            )
        statement, state, context, _, _, plan = await _plan(
            session,
            actor_id=actor_id,
            import_id=import_id,
            profile=profile,
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


async def confirm_direct_sign_correction(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    profile: str,
    reason: str,
    expected_source_version: int,
    preview_hash: str,
    idempotency_key: str,
    confirmed: bool,
) -> DirectResult:
    """Apply once atomically; exact replay returns the durable original receipt."""
    if not confirmed or not idempotency_key or len(idempotency_key) > 200:
        return DirectResult("pending", "sign_confirmation_required")
    statement, _, _, _ = await load_statement(
        session, actor_id, import_id, "corregir_signos", finance=True, locked=True
    )
    request_hash = digest(
        dict(
            import_id=str(import_id),
            actor_id=str(actor_id),
            profile=profile,
            reason=reason.strip(),
            expected_source_version=expected_source_version,
            preview_hash=preview_hash,
            idempotency_key=idempotency_key,
        )
    )
    existing = (
        await session.execute(
            select(AmexDirectSignRevision).where(
                AmexDirectSignRevision.import_id == import_id
            )
        )
    ).scalar_one_or_none()
    if existing:
        if (
            existing.request_hash != request_hash
            or existing.idempotency_key != idempotency_key
        ):
            return DirectResult("pending", "sign_idempotency_conflict")
        return DirectResult(
            "exists",
            statement=statement,
            source_version=expected_source_version + 1,
            progress={
                "sign_revision_id": str(existing.id),
                "preview_hash": existing.preview_hash,
            },
        )
    async with session.begin_nested():
        statement, state, _, source, rows, plan = await _plan(
            session,
            actor_id=actor_id,
            import_id=import_id,
            profile=profile,
            reason=reason,
            expected_source_version=expected_source_version,
            locked=True,
        )
        if preview_hash != plan["preview_hash"]:
            raise DirectConflict("sign_preview_changed")
        revision = AmexDirectSignRevision(
            id=uuid4(),
            import_id=import_id,
            source_file_id=source.id,
            **{
                key: plan[key]
                for key in (
                    "file_sha256",
                    "parser_version",
                    "original_profile",
                    "profile",
                    "context_version",
                    "expected_source_version",
                    "expected_count",
                    "expected_item_ids",
                    "original_counts",
                    "effective_counts",
                    "original_totals",
                    "effective_totals",
                    "membership_digest",
                )
            },
            context_id=UUID(plan["context_id"]),
            actor_id=actor_id,
            reason=reason.strip(),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            preview_hash=preview_hash,
        )
        session.add(revision)
        await session.flush()
        card = await session.get(AmexCardAccount, statement.card_account_id)
        owner = await session.get(Empleado, statement.employee_id)
        for item, receipt, row, effective, original_expense in rows:
            expense: Any = None
            if row["movement_kind"] == "debit":
                expense = await create_expense_from_data(
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
            if original_expense is not None:
                original_expense.estado_gasto = "cancelado"
                original_expense.cancelado_en = datetime.now(timezone.utc)
                original_expense.cancelado_por_id = actor_id
                original_expense.motivo_cancelacion = reason.strip()
                session.add(
                    Aprobacion(
                        tipo_entidad="gasto",
                        entidad_id=original_expense.id,
                        aprobador_id=actor_id,
                        accion="cancelar",
                        comentario=reason.strip(),
                        fecha=original_expense.cancelado_en,
                    )
                )
            proof_hash = (
                await session.execute(
                    text(
                        "SELECT encode(sha256(convert_to("
                        "CAST(:source AS jsonb)::text,'UTF8')),'hex')"
                    ),
                    {"source": json.dumps(effective)},
                )
            ).scalar_one()
            sign_row = AmexDirectSignRow(
                id=uuid4(),
                revision_id=revision.id,
                direct_item_id=item.id,
                import_item_id=receipt.id,
                row_number=receipt.row_number,
                movement_kind=row["movement_kind"],
                profile=profile,
                effective_source_json=effective,
                raw_signed_amount=row["raw_signed_amount"],
                imported_expense_id=expense.id if expense else None,
                original_expense_id=item.imported_expense_id,
                proof_hash=proof_hash,
            )
            session.add(sign_row)
            await session.flush()
            item.source_version += 1
            item.match_reason = (
                "sign_corrected_pending_cfdi" if expense else "sign_corrected_credit"
            )
            await record_event(
                session,
                item,
                state,
                actor_id,
                "sign_corrected",
                dict(
                    revision_id=str(revision.id),
                    proof_hash=proof_hash,
                    reason=reason.strip(),
                    original_expense_id=(
                        str(item.imported_expense_id)
                        if item.imported_expense_id
                        else None
                    ),
                    imported_expense_id=str(expense.id) if expense else None,
                ),
                bump=False,
            )
        state.source_version += 1
        state.progress_json = None
        await session.flush()
    return DirectResult(
        "corrected",
        statement=statement,
        source_version=state.source_version,
        progress={**plan, "sign_revision_id": str(revision.id)},
    )
