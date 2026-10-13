"""Atomic statement intake using the existing CSV and fiscal matcher semantics."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    AmexCardAccount,
    AmexStatementImport,
    AmexStatementImportItem,
    CFDIReport,
    Empleado,
)
from .amex_statement_matcher import find_amex_cfdi_match
from .expense_service import create_expense_from_data

PARSER_VERSION = "amex-csv-v1"
_DATE_FORMATS = (
    "%d %b %Y",
    "%d %B %Y",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%m-%d-%Y",
)
_COLUMNS = (
    ("fecha de compra", "date", "fecha", "transaction date", "fecha de transacción"),
    ("descripción", "description", "merchant", "descripcion", "merchant name"),
    ("importe", "amount", "charge amount", "monto", "cargo"),
)


class StatementValidationError(ValueError):
    """Safe, non-personal intake error code."""


def parse_amex_csv(
    content: bytes, cfdi_uuid_mensual: str | None = None
) -> list[dict[str, Any]]:
    """Preserve date, absolute amount and column matching from legacy intake."""
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StatementValidationError("invalid_utf8") from exc
    reader = csv.DictReader(io.StringIO(decoded))
    if not reader.fieldnames:
        raise StatementValidationError("missing_headers")
    names = {name.lower(): name for name in reader.fieldnames}
    columns = [
        next((names[name] for name in aliases if name in names), None)
        for aliases in _COLUMNS
    ]
    if not all(columns):
        raise StatementValidationError("missing_columns")
    receipts = []
    month = None
    for number, row in enumerate(reader, 2):
        receipt: dict[str, Any] = {
            "row_number": number,
            "status": "skipped",
            "reason_code": None,
            "normalized_json": {},
        }
        values = [(row.get(column) or "").strip() for column in columns]
        raw_date, description, raw_amount = values
        parsed_date = None
        if not raw_date:
            receipt["reason_code"] = "missing_date"
        else:
            for fmt in _DATE_FORMATS:
                try:
                    parsed_date = datetime.strptime(raw_date, fmt)
                    break
                except ValueError:
                    continue
            if parsed_date is None:
                receipt["reason_code"] = "invalid_date"
        if parsed_date is not None and cfdi_uuid_mensual:
            row_month = (parsed_date.year, parsed_date.month)
            if month is not None and row_month != month:
                raise StatementValidationError("monthly_cfdi_multiple_months")
            month = row_month
        if not receipt["reason_code"] and not description:
            receipt["reason_code"] = "missing_description"
        amount = None
        if not receipt["reason_code"]:
            try:
                amount = float(
                    abs(
                        Decimal(
                            raw_amount.replace("$", "")
                            .replace(",", "")
                            .replace(" ", "")
                        )
                    )
                )
                if not math.isfinite(amount) or amount <= 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError, OverflowError):
                receipt["reason_code"] = (
                    "invalid_amount" if raw_amount else "missing_amount"
                )
        if receipt["reason_code"] is None:
            assert parsed_date is not None
            receipt.update(
                status="imported",
                fecha=parsed_date,
                descripcion=description,
                amount=amount,
            )
            # Concepts stay on Expense, not duplicated in the row audit JSON.
            receipt["normalized_json"] = {
                "date": parsed_date.date().isoformat(),
                "amount": str(amount),
                "currency": "MXN",
            }
        receipts.append(receipt)
    return receipts


async def import_amex_csv_rows(
    session: AsyncSession,
    employee: Empleado,
    card_account: AmexCardAccount,
    content: bytes,
    cfdi_uuid_mensual: str | None = None,
) -> dict[str, Any]:
    """Shared legacy/new intake helper: no commit, DDL or DB error suppression."""
    monthly = (
        str(UUID(cfdi_uuid_mensual.strip())).upper()
        if cfdi_uuid_mensual and cfdi_uuid_mensual.strip()
        else None
    )
    receipts = parse_amex_csv(content, monthly)
    monthly_report = None
    if monthly:
        monthly_report = (
            await session.execute(
                select(CFDIReport).where(func.upper(CFDIReport.cfdi_uuid) == monthly)
            )
        ).scalar_one_or_none()
    counts = {
        "total_rows": len(receipts),
        "created_count": 0,
        "skipped_count": 0,
        "auto_cfdi_match_count": 0,
        "auto_tip_match_count": 0,
        "auto_pase_match_count": 0,
    }
    for row in receipts:
        if row["status"] == "skipped":
            counts["skipped_count"] += 1
            continue
        expense: Any = await create_expense_from_data(
            session=session,
            concepto=row["descripcion"],
            gasto_cantidad=row["amount"],
            fecha=row["fecha"],
            empleado_id=cast(UUID, employee.id),
            nombre_enviador=cast(str, employee.nombre),
            tipo_gasto="manual",
            metodo_pago="TARJETA CREDITO AMEX",
            ultimos_4_digitos=cast(str, card_account.last4),
            origen="amex_batch",
        )
        expense.pagado_con_amex_empresa = True
        if monthly:
            expense.cfdi_uuid_manual = monthly
            if monthly_report:
                expense.cfdi_report_id = monthly_report.id
        else:
            match = await find_amex_cfdi_match(
                session,
                charge_description=row["descripcion"],
                charge_amount=row["amount"],
                charge_date=row["fecha"],
            )
            if match is not None:
                expense.cfdi_report_id = match.cfdi.id
                expense.cfdi_uuid_manual = (
                    match.cfdi.cfdi_uuid or ""
                ).strip().upper() or None
                if match.inferred_tip > 0:
                    expense.propina_no_deducible = round(float(match.inferred_tip), 2)
                    counts["auto_tip_match_count"] += 1
                elif match.confidence == "pase_monthly":
                    counts["auto_pase_match_count"] += 1
                else:
                    counts["auto_cfdi_match_count"] += 1
        session.add(expense)
        row["imported_expense_id"] = expense.id
        counts["created_count"] += 1
    await session.flush()
    return {
        **counts,
        "rows": receipts,
        "errors": [
            f"Fila {row['row_number']}: {row['reason_code']}"
            for row in receipts
            if row["status"] == "skipped"
        ],
        "cfdi_monthly_linked": monthly_report is not None,
    }


async def import_amex_statement(
    session: AsyncSession,
    actor: Empleado,
    card_account_id: UUID,
    employee_id: UUID,
    period_start: date,
    period_end: date,
    filename: str,
    content: bytes,
    cfdi_uuid_mensual: str | None = None,
    additional_confirmed: bool = False,
) -> dict[str, Any]:
    """Persist complete intake atomically; the authorized caller owns commit."""
    if not actor.activo or actor.rol not in {
        "finanzas",
        "admin",
        "superadmin",
        "super_admin",
    }:
        raise PermissionError("finance_actor_required")
    if period_end < period_start:
        return {"status": "invalid", "reason": "invalid_period"}
    if not filename or not filename.endswith(".csv"):
        return {"status": "invalid", "reason": "invalid_filename"}
    card = await session.get(AmexCardAccount, card_account_id)
    employee = await session.get(Empleado, employee_id)
    if not card or not card.active or not employee or not employee.activo:
        return {"status": "invalid", "reason": "inactive_context"}
    try:
        if cfdi_uuid_mensual and cfdi_uuid_mensual.strip():
            UUID(cfdi_uuid_mensual.strip())
        rows = parse_amex_csv(
            content, cfdi_uuid_mensual.strip() if cfdi_uuid_mensual else None
        )
    except (StatementValidationError, ValueError) as exc:
        return {
            "status": "invalid",
            "reason": (
                str(exc)
                if isinstance(exc, StatementValidationError)
                else "invalid_monthly_cfdi"
            ),
        }
    if not any(row["status"] == "imported" for row in rows):
        return {"status": "invalid", "reason": "no_valid_rows", "rows": rows}
    digest = hashlib.sha256(content).hexdigest()
    # Ordered global file lock followed by card/period lock avoids duplicate or
    # cross-context races without any amount/date matching.
    for key in (
        "amex-file:" + digest,
        f"amex-period:{card.id}:{period_start}:{period_end}",
    ):
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {
                "key": int.from_bytes(
                    hashlib.sha256(key.encode()).digest()[:8], "big", signed=True
                )
            },
        )
    existing = (
        (
            await session.execute(
                select(AmexStatementImport).where(
                    AmexStatementImport.file_sha256 == digest
                )
            )
        )
        .scalars()
        .all()
    )
    for existing_item in existing:
        item: Any = existing_item
        if (
            item.card_account_id,
            item.employee_id,
            item.period_start,
            item.period_end,
            item.parser_version,
        ) == (card.id, employee.id, period_start, period_end, PARSER_VERSION):
            requested_monthly = (
                str(UUID(cfdi_uuid_mensual.strip())).upper()
                if cfdi_uuid_mensual and cfdi_uuid_mensual.strip()
                else None
            )
            if item.summary_json.get("cfdi_uuid_mensual") != requested_monthly:
                return {"status": "context_conflict", "reason": "monthly_cfdi_conflict"}
            return {"status": "replay", "reason": None, "import": item}
    if existing:
        return {"status": "context_conflict", "reason": "file_context_conflict"}
    previous = (
        await session.execute(
            select(AmexStatementImport.id)
            .where(
                AmexStatementImport.card_account_id == card.id,
                AmexStatementImport.period_start == period_start,
                AmexStatementImport.period_end == period_end,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if previous and not additional_confirmed:
        return {
            "status": "additional_confirmation_required",
            "reason": "card_period_already_loaded",
        }
    result = await import_amex_csv_rows(
        session, employee, card, content, cfdi_uuid_mensual
    )
    monthly = (
        str(UUID(cfdi_uuid_mensual.strip())).upper()
        if cfdi_uuid_mensual and cfdi_uuid_mensual.strip()
        else None
    )
    receipt = AmexStatementImport(
        card_account_id=card.id,
        employee_id=employee.id,
        period_start=period_start,
        period_end=period_end,
        file_sha256=digest,
        filename=re.sub(r"[^\w. -]", "_", filename.replace("\\", "/").split("/")[-1])[
            :200
        ],
        parser_version=PARSER_VERSION,
        actor_id=actor.id,
        total_rows=result["total_rows"],
        imported_count=result["created_count"],
        skipped_count=result["skipped_count"],
        summary_json={
            "cfdi_uuid_mensual": monthly,
            "additional_confirmed": bool(additional_confirmed),
            **{
                k: result[k]
                for k in (
                    "auto_cfdi_match_count",
                    "auto_tip_match_count",
                    "auto_pase_match_count",
                )
            },
        },
    )
    session.add(receipt)
    await session.flush()
    for row in result["rows"]:
        session.add(
            AmexStatementImportItem(
                import_id=receipt.id,
                row_number=row["row_number"],
                imported_expense_id=row.get("imported_expense_id"),
                status=row["status"],
                reason_code=row["reason_code"],
                normalized_json=row["normalized_json"],
            )
        )
    await session.flush()
    return {"status": "imported", "reason": None, "import": receipt, **result}


async def list_amex_statement_imports(
    session: AsyncSession,
) -> list[AmexStatementImport]:
    """Read persisted declarations without changing banking or financial state."""
    return list(
        (
            await session.execute(
                select(AmexStatementImport).order_by(
                    AmexStatementImport.created_at.desc()
                )
            )
        )
        .scalars()
        .all()
    )


async def get_amex_statement_import(
    session: AsyncSession, import_id: UUID
) -> AmexStatementImport | None:
    """Read one exact statement identity."""
    return await session.get(AmexStatementImport, import_id)
