"""Strict signed CSV intake, separate from the unchanged legacy matcher."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text

from ..models import (
    AmexCardAccount,
    AmexDirectItem,
    AmexStatementDirectState,
    AmexStatementImport,
    AmexStatementImportItem,
    AmexStatementSourceFile,
    Empleado,
)
from .amex_direct_common import (
    DirectConflict,
    DirectResult,
    authorize,
    configuration_for,
    digest,
    item_hash,
    load_statement,
)
from .amex_direct_schema_service import require_amex_direct_schema
from .amex_recognition_service import lock_recognition
from .amex_statement_import_service import _DATE_FORMATS
from .expense_service import create_expense_from_data

PARSER_VERSION = "amex-direct-csv-v1"
MAX_SOURCE_BYTES = 10 * 1024 * 1024
PROFILES = {"debit_negative", "debit_positive", "separate_columns"}
MIME_TYPES = {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"}


def _signed_amount(raw: str) -> Decimal:
    clean = raw.strip().replace("$", "").replace(" ", "")
    if "," in clean:
        if not re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", clean):
            raise InvalidOperation
        clean = clean.replace(",", "")
    value = Decimal(clean)
    if not value.is_finite() or value == 0 or value != value.quantize(Decimal(".01")):
        raise InvalidOperation
    return value


def parse_direct_csv(
    content: bytes, profile: str | None, currency: str | None
) -> list[dict[str, Any]]:
    """Pure profile preview; preserve cells and sign without financial effects."""
    if not content or len(content) > MAX_SOURCE_BYTES:
        raise DirectConflict("source_size_invalid")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DirectConflict("invalid_utf8") from exc
    reader = csv.DictReader(io.StringIO(decoded))
    names = {str(v).strip().lower(): v for v in (reader.fieldnames or [])}

    def column(*aliases):
        return next((names[v] for v in aliases if v in names), None)

    date_column = column(
        "fecha", "fecha de compra", "date", "transaction date", "fecha de transacción"
    )
    desc_column = column(
        "descripción", "descripcion", "description", "merchant", "merchant name"
    )
    amount_column = column("importe", "amount", "charge amount", "monto", "cargo")
    debit_column = column("cargo", "debit", "debits")
    credit_column = column("abono", "credit", "credits")
    uuid_column = column("uuid", "uuid fiscal", "cfdi uuid")
    merchant_column = column("rfc emisor", "issuer rfc", "merchant rfc")
    currency_column = column("moneda", "currency")
    if (
        not date_column
        or not desc_column
        or (profile != "separate_columns" and not amount_column)
    ):
        raise DirectConflict("missing_columns")
    rows = []
    for number, cells in enumerate(reader, 2):
        raw_date = str(cells.get(date_column) or "").strip()
        description = str(cells.get(desc_column) or "").strip()
        parsed_date = None
        for fmt in _DATE_FORMATS:
            try:
                parsed_date = datetime.strptime(raw_date, fmt)
                break
            except ValueError:
                continue
        raw_amount = str(cells.get(amount_column) or "") if amount_column else ""
        kind = "unknown"
        reason = None
        signed = None
        used_column = amount_column
        try:
            if profile == "separate_columns":
                debit = (
                    str(cells.get(debit_column) or "").strip() if debit_column else ""
                )
                credit = (
                    str(cells.get(credit_column) or "").strip() if credit_column else ""
                )
                if not debit_column or not credit_column or bool(debit) == bool(credit):
                    reason = "contradictory_debit_credit_columns"
                else:
                    raw_amount = debit or credit
                    used_column = debit_column if debit else credit_column
                    signed = _signed_amount(raw_amount)
                    if signed < 0:
                        reason = "contradictory_column_sign"
                    else:
                        kind = "debit" if debit else "credit"
            else:
                if (
                    debit_column
                    and credit_column
                    and str(cells.get(debit_column) or "").strip()
                    and str(cells.get(credit_column) or "").strip()
                ):
                    raise ValueError("contradictory_debit_credit_columns")
                signed = _signed_amount(raw_amount)
                if profile not in PROFILES:
                    reason = "csv_profile_required"
                else:
                    is_debit = (
                        (signed < 0) if profile == "debit_negative" else (signed > 0)
                    )
                    kind = "debit" if is_debit else "credit"
        except (InvalidOperation, ValueError):
            reason = "invalid_signed_amount"
        declared_currency = (
            str(cells.get(currency_column) or currency or "").strip().upper()
            if currency_column
            else str(currency or "").strip().upper()
        )
        if not declared_currency:
            kind = "unknown"
            reason = "statement_currency_required"
        elif declared_currency != "MXN":
            kind = "unknown"
            reason = "unsupported_direct_currency"
        if parsed_date is None or not description:
            kind = "skipped"
            reason = "invalid_date" if parsed_date is None else "missing_description"
        row_uuid = str(cells.get(uuid_column) or "").strip() if uuid_column else ""
        if row_uuid:
            try:
                row_uuid = str(UUID(row_uuid)).upper()
            except ValueError:
                kind = "unknown"
                reason = "invalid_declared_uuid"
        rows.append(
            dict(
                row_number=number,
                status="imported" if kind == "debit" else kind,
                reason=reason,
                movement_kind=kind,
                raw_amount=raw_amount,
                raw_signed_amount=signed,
                amount=abs(signed) if kind == "debit" and signed is not None else None,
                amount_column=used_column,
                profile=profile,
                profile_version=PARSER_VERSION,
                date=parsed_date,
                description=description,
                currency=declared_currency,
                currency_declared=not bool(currency_column),
                declared_uuid=row_uuid or None,
                issuer_rfc=(
                    str(cells.get(merchant_column) or "").strip().upper()
                    if merchant_column
                    else None
                ),
                cells={str(k): str(v) for k, v in cells.items()},
            )
        )
    if not rows:
        raise DirectConflict("no_statement_rows")
    return rows


def evaluate_direct_period(
    content: bytes,
    profile: str | None,
    currency: str | None,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    """Evaluate declared coverage without altering parsed source rows."""
    if (
        not isinstance(period_start, date)
        or not isinstance(period_end, date)
        or period_end < period_start
    ):
        raise DirectConflict("invalid_statement_period")
    rows = parse_direct_csv(content, profile, currency)
    dates = [row["date"].date() for row in rows if row["date"] is not None]
    outside = [
        {
            "row_number": row["row_number"],
            "date": row["date"].date(),
            "reason": "row_outside_declared_period",
        }
        for row in rows
        if row["date"] is not None
        and not period_start <= row["date"].date() <= period_end
    ]
    return {
        "rows": rows,
        "period_start": period_start,
        "period_end": period_end,
        "min_date": min(dates) if dates else None,
        "max_date": max(dates) if dates else None,
        "outside_rows": outside,
        "outside_count": len(outside),
        "counts": {
            kind: sum(row["movement_kind"] == kind for row in rows)
            for kind in ("debit", "credit", "unknown", "skipped")
        },
    }


async def import_direct_statement(
    session: Any,
    *,
    actor_id: UUID,
    employee_id: UUID,
    card_account_id: UUID,
    period_start: date,
    period_end: date,
    content: bytes,
    filename: str,
    mime_type: str,
    profile: str | None,
    company_code: str | None,
    receiver_rfc: str | None,
    currency: str | None,
    idempotency_key: str,
    confirmed: bool,
) -> DirectResult:
    """Atomic original source, receipt and real debit anchors; never SOAP/commit."""
    await authorize(session, actor_id, "cargar", finance=True)
    config = configuration_for(
        card_account_id,
        company_code,
        receiver_rfc.strip().upper() if receiver_rfc else None,
    )
    if config is None:
        return DirectResult("pending", "contexto_fiscal_pendiente")
    await authorize(session, actor_id, "cargar", config=config, finance=True)
    if "employee_ids" in config and str(employee_id) not in config["employee_ids"]:
        return DirectResult("pending", "employee_context_denied")
    await require_amex_direct_schema(session)
    if not confirmed or not idempotency_key:
        return DirectResult("pending", "source_confirmation_required")
    if not filename.lower().endswith(".csv") or mime_type not in MIME_TYPES:
        return DirectResult("pending", "source_type_invalid")
    if (
        not isinstance(period_start, date)
        or not isinstance(period_end, date)
        or period_end < period_start
    ):
        return DirectResult("pending", "invalid_statement_period")
    coverage = evaluate_direct_period(
        content, profile, currency, period_start, period_end
    )
    rows = coverage["rows"]
    card = await session.get(AmexCardAccount, card_account_id)
    employee = await session.get(Empleado, employee_id)
    if card is None or not card.active or employee is None or not employee.activo:
        return DirectResult("pending", "inactive_context")
    source_hash = digest(content)
    # Match the existing intake's file/period advisory order; no global -> import.
    for key in (
        "amex-file:" + source_hash,
        f"amex-period:{card.id}:{period_start}:{period_end}",
    ):
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {
                "key": int.from_bytes(
                    __import__("hashlib").sha256(key.encode()).digest()[:8],
                    "big",
                    signed=True,
                )
            },
        )
    existing = list(
        (
            await session.execute(
                select(AmexStatementImport).where(
                    AmexStatementImport.file_sha256 == source_hash
                )
            )
        )
        .scalars()
        .all()
    )
    if existing:
        same = next(
            (
                v
                for v in existing
                if v.workflow_mode == "direct"
                and v.card_account_id == card_account_id
                and v.employee_id == employee_id
                and v.period_start == period_start
                and v.period_end == period_end
                and v.summary_json.get("profile") == profile
                and v.summary_json.get("currency") == currency
            ),
            None,
        )
        if same is None:
            return DirectResult("pending", "file_context_conflict")
        return DirectResult(
            "exists",
            statement=same,
            card=card,
            source_version=(
                await session.get(AmexStatementDirectState, same.id)
            ).source_version,
        )
    # Existing exact-context replays above remain available; a new incomplete
    # declaration must never create receipts or debit anchors.
    if coverage["outside_count"]:
        return DirectResult("pending", "statement_period_mismatch", progress=coverage)
    counts = {
        kind: sum(row["movement_kind"] == kind for row in rows)
        for kind in ("debit", "credit", "unknown", "skipped")
    }
    safe_name = re.sub(r"[^\w. -]", "_", filename.replace("\\", "/").split("/")[-1])[
        :200
    ]
    async with session.begin_nested():
        statement: Any = AmexStatementImport(
            id=uuid4(),
            workflow_mode="direct",
            card_account_id=card_account_id,
            employee_id=employee_id,
            period_start=period_start,
            period_end=period_end,
            file_sha256=source_hash,
            filename=safe_name,
            parser_version=PARSER_VERSION,
            actor_id=actor_id,
            total_rows=len(rows),
            imported_count=counts["debit"],
            credit_count=counts["credit"],
            unknown_count=counts["unknown"],
            skipped_count=counts["skipped"],
            summary_json={
                "profile": profile,
                "currency": currency,
                "idempotency_key": idempotency_key,
            },
        )
        session.add(statement)
        await session.flush()
        state: Any = AmexStatementDirectState(import_id=statement.id, source_version=1)
        session.add(state)
        session.add(
            AmexStatementSourceFile(
                import_id=statement.id,
                content=content,
                file_sha256=source_hash,
                byte_size=len(content),
                filename=safe_name,
                mime_type=mime_type,
                actor_id=actor_id,
            )
        )
        # The statement/state owner precedes global recognition, which must
        # precede the expense owner's folio advisory lock (also used by recovery).
        await session.flush()
        await lock_recognition(session)
        for row in rows:
            expense: Any = None
            if row["movement_kind"] == "debit":
                expense = await create_expense_from_data(
                    session=session,
                    concepto=row["description"],
                    gasto_cantidad=float(row["amount"]),
                    fecha=row["date"],
                    empleado_id=employee_id,
                    nombre_enviador=employee.nombre,
                    tipo_gasto="manual",
                    metodo_pago="TARJETA CREDITO AMEX",
                    ultimos_4_digitos=card.last4,
                    origen="amex_batch",
                )
                expense.pagado_con_amex_empresa = True
                expense.currency = row["currency"]
                await session.flush()
            source = dict(row)
            source["file_sha256"] = source_hash
            source = {
                k: (str(v) if isinstance(v, (Decimal, datetime)) else v)
                for k, v in source.items()
            }
            receipt = AmexStatementImportItem(
                id=uuid4(),
                import_id=statement.id,
                workflow_mode="direct",
                row_number=row["row_number"],
                imported_expense_id=expense.id if expense else None,
                status=row["status"],
                reason_code=row["reason"],
                normalized_json=source,
            )
            session.add(receipt)
            await session.flush()
            item: Any = AmexDirectItem(
                id=uuid4(),
                import_id=statement.id,
                import_item_id=receipt.id,
                imported_expense_id=expense.id if expense else None,
                movement_kind=row["movement_kind"],
                raw_signed_amount=row["raw_signed_amount"],
                raw_amount=row["raw_amount"],
                amount_column=row["amount_column"],
                profile=profile,
                profile_version=PARSER_VERSION,
                source_json=source,
                source_version=1,
                source_hash="",
                declared_uuid=row["declared_uuid"],
                issuer_rfc=row["issuer_rfc"],
                match_status="pending",
                match_reason="contexto_fiscal_pendiente",
                budget_status="unclassified",
                accounting_status="pending",
                credit_excluded=False,
            )
            item.source_hash = item_hash(item)
            session.add(item)
        await session.flush()
        if company_code and receiver_rfc and currency:
            from .amex_direct_context_service import set_direct_context

            initialized = await set_direct_context(
                session,
                actor_id=actor_id,
                import_id=statement.id,
                company_code=company_code,
                receiver_rfc=receiver_rfc,
                currency=currency,
                profile=profile,
                expected_source_version=1,
                reason="",
                confirmed=True,
            )
            if initialized.status == "pending":
                raise DirectConflict(initialized.reason or "contexto_fiscal_pendiente")
    return DirectResult(
        "imported", statement=statement, card=card, source_version=state.source_version
    )


async def download_direct_source(
    session: Any, *, actor_id: UUID, import_id: UUID
) -> tuple[bytes, str, str]:
    await load_statement(session, actor_id, import_id, "fuente", finance=True)
    source = (
        await session.execute(
            select(AmexStatementSourceFile).where(
                AmexStatementSourceFile.import_id == import_id
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise DirectConflict("original_source_missing")
    if (
        len(source.content) != source.byte_size
        or digest(source.content) != source.file_sha256
    ):
        raise DirectConflict("original_source_integrity_failed")
    return source.content, source.filename, source.mime_type
