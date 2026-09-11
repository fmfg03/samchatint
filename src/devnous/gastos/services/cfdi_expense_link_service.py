"""
CFDI fiscal UUID normalization and ExpenseReport ↔ CFDIReport linking.

Canonical storage: uppercase UUID string (SAT-style), after strip + uuid.UUID parse.
Matching is case- and whitespace-tolerant for legacy rows via upper(trim(...)) in SQL.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Aprobacion, CFDIReport, Documento, ExpenseReport

logger = logging.getLogger(__name__)

_CFDI_UUID_PREFIX_RE = re.compile(r"^[0-9a-fA-F]{8}$")


class ExpenseCFDIDuplicateError(ValueError):
    """A fiscal UUID is already linked to another active expense."""

    def __init__(self, fiscal_uuid: str):
        self.fiscal_uuid = fiscal_uuid
        super().__init__(
            "La factura ya está vinculada a otra partida activa. "
            "Confirma factura compartida e indica el motivo para continuar."
        )

# PostgreSQL: link any pending expense that has a manual fiscal UUID to a CFDI
# row by UUID string.
BULK_LINK_EXPENSES_TO_CFDI_REPORTS_SQL = text(
    """
    UPDATE expense_reports er
    SET cfdi_report_id = c.id
    FROM cfdi_reports c
    WHERE UPPER(TRIM(er.cfdi_uuid_manual)) = UPPER(TRIM(c.cfdi_uuid))
        AND er.cfdi_report_id IS NULL
        AND er.cfdi_uuid_manual IS NOT NULL
        AND TRIM(er.cfdi_uuid_manual) <> ''
    """
)

# PostgreSQL: same logic for documentos. SOLICITUD a terceros capture the CFDI
# UUID up-front.
BULK_LINK_DOCUMENTOS_TO_CFDI_REPORTS_SQL = text(
    """
    UPDATE documentos d
    SET cfdi_report_id = c.id
    FROM cfdi_reports c
    WHERE UPPER(TRIM(d.cfdi_uuid_manual)) = UPPER(TRIM(c.cfdi_uuid))
        AND d.cfdi_report_id IS NULL
        AND d.cfdi_uuid_manual IS NOT NULL
        AND TRIM(d.cfdi_uuid_manual) <> ''
    """
)


def normalize_cfdi_uuid_to_canonical(raw: str) -> str:
    """Return stripped, validated UUID in uppercase (canonical storage)."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("CFDI UUID vacío")
    return str(UUID(s)).upper()


def try_normalize_cfdi_uuid_to_canonical(raw: Optional[str]) -> Optional[str]:
    if raw is None or not str(raw).strip():
        return None
    return normalize_cfdi_uuid_to_canonical(str(raw))


def is_cfdi_uuid_prefix_candidate(raw: Optional[str]) -> bool:
    return bool(_CFDI_UUID_PREFIX_RE.match((raw or "").strip()))


async def find_cfdi_report_by_fiscal_uuid(
    session: AsyncSession, fiscal_uuid: str
) -> Optional[CFDIReport]:
    """Lookup CFDI by fiscal cfdi_uuid; case- and trim-insensitive."""
    canon = normalize_cfdi_uuid_to_canonical(fiscal_uuid)
    result = await session.execute(
        select(CFDIReport).where(
            func.upper(func.trim(CFDIReport.cfdi_uuid)) == canon
        )
    )
    return result.scalar_one_or_none()


async def find_cfdi_report_by_fiscal_uuid_or_prefix(
    session: AsyncSession, fiscal_uuid_or_prefix: str
) -> Optional[CFDIReport]:
    """
    Lookup CFDI by full fiscal UUID or by the first 8 UUID characters.

    Prefix lookup only returns a row when the match is unique.
    """
    raw = (fiscal_uuid_or_prefix or "").strip()
    try:
        return await find_cfdi_report_by_fiscal_uuid(session, raw)
    except ValueError:
        pass

    if not _CFDI_UUID_PREFIX_RE.match(raw):
        raise ValueError("CFDI UUID inválido")

    prefix = raw.upper()
    result = await session.execute(
        select(CFDIReport)
        .where(func.upper(func.trim(CFDIReport.cfdi_uuid)).like(f"{prefix}-%"))
        .limit(2)
    )
    matches = list(result.scalars().all())
    if len(matches) == 1:
        return matches[0]
    return None


async def _enforce_expense_cfdi_uniqueness(
    session: AsyncSession,
    *,
    expense: ExpenseReport,
    report: Optional[CFDIReport],
    allow_shared: bool,
    shared_reason: Optional[str],
    actor_id: Optional[UUID],
) -> None:
    # Serialize competing captures of the same fiscal UUID.  A database unique
    # constraint cannot express the approved shared-invoice exception, so the
    # transaction-scoped PostgreSQL advisory lock protects check-then-link.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": expense.cfdi_uuid_manual},
    )
    conditions = [
        func.upper(func.trim(ExpenseReport.cfdi_uuid_manual))
        == expense.cfdi_uuid_manual
    ]
    if report is not None:
        conditions.append(ExpenseReport.cfdi_report_id == report.id)
    duplicate_result = await session.execute(
        select(ExpenseReport.id)
        .where(
            ExpenseReport.id != expense.id,
            ExpenseReport.estado_gasto != "cancelado",
            or_(*conditions),
        )
        .limit(1)
    )
    duplicate_id = duplicate_result.scalar_one_or_none()
    if not duplicate_id:
        expense.cfdi_compartido_confirmado = False
        expense.cfdi_compartido_motivo = None
        return
    reason = (shared_reason or "").strip()
    if not allow_shared:
        raise ExpenseCFDIDuplicateError(expense.cfdi_uuid_manual)
    if not reason:
        raise ValueError("Indique el motivo de la factura compartida.")
    if actor_id is None:
        raise ValueError("No se pudo identificar al usuario que confirmó la factura compartida.")
    expense.cfdi_compartido_confirmado = True
    expense.cfdi_compartido_motivo = reason
    session.add(
        Aprobacion(
            tipo_entidad="gasto",
            entidad_id=expense.id,
            aprobador_id=actor_id,
            accion="confirmar_cfdi_compartido",
            comentario=(
                f"CFDI {expense.cfdi_uuid_manual} compartido con partida "
                f"{duplicate_id}: {reason}"
            ),
        )
    )


async def link_expense_to_cfdi_if_manual_uuid_set(
    session: AsyncSession,
    expense: ExpenseReport,
    *,
    clear_report_if_no_match: bool = False,
    require_unique: bool = False,
    allow_shared: bool = False,
    shared_reason: Optional[str] = None,
    actor_id: Optional[UUID] = None,
) -> bool:
    """
    Normalize expense.cfdi_uuid_manual and set cfdi_report_id if a CFDI exists.

    Returns True if a CFDI row was found and linked. If no row matches and
    clear_report_if_no_match is true, sets cfdi_report_id to None.
    """
    raw = expense.cfdi_uuid_manual
    if raw is None or not str(raw).strip():
        if clear_report_if_no_match:
            expense.cfdi_report_id = None
        return False
    try:
        canon = normalize_cfdi_uuid_to_canonical(str(raw))
    except ValueError:
        try:
            report = await find_cfdi_report_by_fiscal_uuid_or_prefix(session, str(raw))
        except ValueError:
            logger.warning(
                "Invalid cfdi_uuid_manual on expense %s; skipping link", expense.id
            )
            return False
    else:
        expense.cfdi_uuid_manual = canon
        report = await find_cfdi_report_by_fiscal_uuid(session, canon)
    if report:
        expense.cfdi_uuid_manual = normalize_cfdi_uuid_to_canonical(report.cfdi_uuid)
        expense.cfdi_report_id = report.id
    if require_unique:
        await _enforce_expense_cfdi_uniqueness(
            session,
            expense=expense,
            report=report,
            allow_shared=allow_shared,
            shared_reason=shared_reason,
            actor_id=actor_id,
        )
    if report:
        return True
    if clear_report_if_no_match:
        expense.cfdi_report_id = None
    return False


async def bulk_link_pending_expenses_to_cfdi_reports(session: AsyncSession) -> int:
    """
    Link all expenses that have manual fiscal UUID and no cfdi_report_id yet.
    Commit is the caller's responsibility.
    """
    result = await session.execute(BULK_LINK_EXPENSES_TO_CFDI_REPORTS_SQL)
    return int(result.rowcount or 0)


async def bulk_link_pending_documentos_to_cfdi_reports(session: AsyncSession) -> int:
    """
    Link all documentos (typically SOLICITUD a terceros) that have a manual fiscal UUID
    and no cfdi_report_id yet. Commit is the caller's responsibility.
    """
    result = await session.execute(BULK_LINK_DOCUMENTOS_TO_CFDI_REPORTS_SQL)
    return int(result.rowcount or 0)


async def link_documento_to_cfdi_if_manual_uuid_set(
    session: AsyncSession,
    documento: Documento,
    *,
    clear_report_if_no_match: bool = False,
) -> bool:
    """Mirror of link_expense_to_cfdi_if_manual_uuid_set for documentos."""
    raw = getattr(documento, "cfdi_uuid_manual", None)
    if raw is None or not str(raw).strip():
        if clear_report_if_no_match:
            documento.cfdi_report_id = None
        return False
    try:
        canon = normalize_cfdi_uuid_to_canonical(str(raw))
    except ValueError:
        logger.warning(
            "Invalid cfdi_uuid_manual on documento %s; skipping link", documento.id
        )
        return False
    documento.cfdi_uuid_manual = canon
    report = await find_cfdi_report_by_fiscal_uuid(session, canon)
    if report:
        documento.cfdi_report_id = report.id
        return True
    if clear_report_if_no_match:
        documento.cfdi_report_id = None
    return False
