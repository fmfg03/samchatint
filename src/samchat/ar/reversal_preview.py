"""Read-only, exact-original preparation; never authorize an accounting reversal."""

from decimal import Decimal
from typing import Any

from sqlalchemy import text

REVERSAL_BLOCKER = (
    "reversal_accounting_policy_required: Finanzas debe definir la fecha contable "
    "antes de contabilizar la reversa y aplicar el control de periodo abierto; "
    "cancelar ingreso/CFDI con cobros o impuestos requiere tratamiento explícito."
)


async def prepare_collection_reversal(
    session: Any, match: dict[str, Any]
) -> dict[str, Any]:
    """Reverse original amounts/accounts in a preview, retaining each source line."""
    original_id = str(match.get("accounting_poliza_id") or "")
    if not original_id:
        raise ValueError("collection_posting_missing")
    rows = (
        (
            await session.execute(
                text("""
        SELECT p.id AS original_poliza_id, p.fecha_poliza, p.cfdi_report_id,
               l.id AS original_line_id, l.cuenta_codigo, l.cuenta_contable_id,
               l.debe, l.haber
        FROM accounting_polizas p
        JOIN accounting_poliza_lines l ON l.poliza_id = p.id
        WHERE p.id = CAST(:original_id AS uuid)
          AND p.origen = 'ar_collection_match'
        ORDER BY l.line_no, l.id
    """),
                {"original_id": original_id},
            )
        )
        .mappings()
        .all()
    )
    if len(rows) < 2 or any(
        str(row.get("cfdi_report_id") or "") != str(match.get("cfdi_report_id") or "")
        or not row.get("cuenta_contable_id")
        for row in rows
    ):
        raise ValueError("collection_original_posting_incomplete")
    debit = sum(Decimal(str(row.get("debe") or 0)) for row in rows)
    credit = sum(Decimal(str(row.get("haber") or 0)) for row in rows)
    if debit <= 0 or debit != credit:
        raise ValueError("collection_original_posting_unbalanced")
    return {
        "match_id": str(match.get("id") or ""),
        "original_poliza_id": original_id,
        "original_accounting_date": rows[0].get("fecha_poliza"),
        "reversal_accounting_date": None,
        "can_post": False,
        "blockers": [REVERSAL_BLOCKER],
        "lines": [
            {
                "original_line_id": str(row["original_line_id"]),
                "account_id": str(row["cuenta_contable_id"]),
                "account_code": row["cuenta_codigo"],
                "debe": str(row.get("haber") or 0),
                "haber": str(row.get("debe") or 0),
            }
            for row in rows
        ],
    }
