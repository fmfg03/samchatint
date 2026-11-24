"""Read and verify ExpenseReport partitions through their authoritative document."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from devnous.copa_telmex.registration_read_model import _rows
from samchat.client_executive.dossier_sources import _phase

EXPENSE_SOURCE = "Gastos · Partidas de informes"
LINE_LIMIT = 250

_LINE_SOURCE = """
    SELECT er.id::text AS id, d.id::text AS document_id,
           er.numero_referencia AS expense_reference,
           d.numero_referencia AS reference,
           er.gasto_cantidad::numeric AS amount, er.currency,
           er.fecha::date::text AS expense_date, er.updated_at,
           er.fase_torneo AS expense_phase, d.fase AS document_phase,
           er.categorias AS expense_categories, d.categorias AS document_categories,
           d.concepto_pago AS document_concept
      FROM expense_reports er
      JOIN documentos d ON d.id = COALESCE(er.informe_documento_id, er.documento_id)
     WHERE d.torneo_id = CAST(:tournament AS uuid) AND d.tipo = 'INFORME'
       AND (d.edicion = :edition OR d.edicion IS NULL)
       AND (er.edicion = :edition OR (er.edicion IS NULL AND d.edicion = :edition))
       AND COALESCE(er.estado_gasto, 'activo') = 'activo'
"""


def scope_matches(row: dict[str, Any], phase: str, category: str) -> bool:
    from samchat.client_executive.dossier_records import document_category_matches

    if _phase(row["expense_phase"]) != phase:
        return False
    if row["document_phase"] and _phase(row["document_phase"]) != phase:
        return False
    return all(
        document_category_matches(category, row[key])
        for key in ("expense_categories", "document_categories")
    )


async def verify_expense_link(
    session: Any, *, tournament_id: str, edition_year: int, expense_id: str
) -> dict[str, Any] | None:
    """Lock the expense's source document before testing attribution conflicts."""
    result = await session.execute(
        text(_LINE_SOURCE + " AND er.id = CAST(:expense AS uuid) FOR UPDATE OF d, er"),
        {"tournament": tournament_id, "edition": edition_year, "expense": expense_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def has_expense_attributions(session: Any, document_id: str) -> bool:
    if not await session.scalar(
        text("SELECT to_regclass('direction_dossier_extended_records') IS NOT NULL")
    ):
        return False
    return bool(
        await session.scalar(
            text("""
                SELECT EXISTS (
                    SELECT 1
                    FROM direction_dossier_extended_records r
                    WHERE r.kind = 'expense_line_link'
                      AND r.fields ->> 'source_document_id' = :document
                )
            """),
            {"document": document_id},
        )
    )


async def has_document_attribution(session: Any, document_id: str) -> bool:
    return bool(
        await session.scalar(
            text("""
                SELECT EXISTS (
                    SELECT 1 FROM direction_dossier_records
                     WHERE kind = 'financial_link' AND document_id = CAST(:document AS uuid)
                )
            """),
            {"document": document_id},
        )
    )


async def read_expense_lines(
    session: Any,
    *,
    tournament_id: str,
    edition_year: int,
    document_ids: tuple[str, ...],
    expense_ids: tuple[str, ...],
) -> dict[str, Any]:
    if not document_ids and not expense_ids:
        return {"status": "empty", "rows": []}
    try:
        rows = await _rows(
            session,
            _LINE_SOURCE + """
                 AND (d.id = ANY(CAST(:documents AS uuid[]))
                      OR er.id = ANY(CAST(:expenses AS uuid[])))
                 ORDER BY (er.id = ANY(CAST(:expenses AS uuid[]))) DESC, er.fecha, er.id
                 LIMIT :limit
            """,
            {
                "tournament": tournament_id,
                "edition": edition_year,
                "documents": list(document_ids),
                "expenses": list(expense_ids),
                "limit": LINE_LIMIT + len(expense_ids) + 1,
            },
        )
    except SQLAlchemyError:
        return {"status": "unavailable", "rows": []}
    limit = LINE_LIMIT + len(expense_ids)
    result = []
    for row in rows[:limit]:
        amount = Decimal(str(row["amount"])) if row["amount"] is not None else None
        result.append(
            {
                **row,
                "amount": (
                    format(amount, "f")
                    if amount is not None and amount.is_finite()
                    else None
                ),
                "phase": _phase(row["expense_phase"]),
                "source": EXPENSE_SOURCE,
                "updated_at": (
                    row["updated_at"].isoformat() if row["updated_at"] else None
                ),
            }
        )
    return {"status": "partial" if len(rows) > limit else "available", "rows": result}
