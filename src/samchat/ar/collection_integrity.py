"""Cross-channel collection exclusion on the existing CFDI transaction lock."""

from datetime import datetime
from typing import Any

from sqlalchemy import text


class CollectionSourceConflict(ValueError):
    """A new collection would overlap preserved financial evidence."""


async def require_collection_period_open(session: Any, collection_date: Any) -> None:
    """Honor the existing accounting_close_periods rule without choosing a new date."""
    try:
        dated = datetime.fromisoformat(str(collection_date).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CollectionSourceConflict("collection_date_required") from exc
    period = (
        (
            await session.execute(
                text("""
        SELECT status FROM accounting_close_periods
        WHERE fiscal_year = :year AND fiscal_month = :month
        FOR SHARE
    """),
                {"year": dated.year, "month": dated.month},
            )
        )
        .mappings()
        .first()
    )
    if period and str(period.get("status") or "").strip().lower() == "closed":
        raise CollectionSourceConflict("collection_accounting_period_closed")


async def load_collection_source_context(
    session: Any, item: dict[str, Any]
) -> dict[str, Any]:
    """Revalidate canonical object identity; this does not grant actor/company access."""
    item_id = str(item.get("ar_item_id") or "")
    cfdi_id = str(item.get("cfdi_report_id") or "")
    linked = item_id.startswith("linked:")
    if not linked and item_id != f"candidate:{cfdi_id}":
        raise CollectionSourceConflict("ar_source_context_changed")
    rows = (
        (
            await session.execute(
                text("""
        SELECT l.id, l.budget_version_id, l.budget_line_id, l.cfdi_report_id,
               l.accounting_poliza_id, l.tournament_id, l.status, l.unlinked_at,
               c.receptor_rfc, c.receptor_nombre
        FROM budget_cfdi_income_links l
        JOIN cfdi_reports c ON c.id = l.cfdi_report_id
        WHERE (CAST(:linked AS boolean) AND l.id::text = :link_id)
           OR (NOT CAST(:linked AS boolean)
               AND l.cfdi_report_id::text = :cfdi_id
               AND l.unlinked_at IS NULL
               AND l.status IN ('pending_approval', 'approved'))
        FOR SHARE OF l
    """),
                {
                    "linked": linked,
                    "link_id": item_id.removeprefix("linked:"),
                    "cfdi_id": cfdi_id,
                },
            )
        )
        .mappings()
        .all()
    )
    if not linked:
        if rows or item.get("budget_line_id"):
            raise CollectionSourceConflict("ar_source_context_changed")
        return {"cfdi_report_id": cfdi_id, "source": "unlinked_candidate"}
    if len(rows) != 1:
        raise CollectionSourceConflict("ar_source_context_changed")
    row = dict(rows[0])
    if (
        row.get("status") != "approved"
        or row.get("unlinked_at")
        or not row.get("accounting_poliza_id")
        or any(
            str(row.get(key) or "") != str(item.get(key) or "")
            for key in ("budget_version_id", "budget_line_id", "cfdi_report_id")
        )
    ):
        raise CollectionSourceConflict("ar_source_context_changed")
    return row


async def lock_collection_source(session: Any, cfdi_report_id: str) -> None:
    """Serialize collection owners on one existing source row."""
    if not cfdi_report_id:
        raise CollectionSourceConflict("collection_cfdi_source_required")
    row = (
        (
            await session.execute(
                text("""
        SELECT id FROM cfdi_reports WHERE id = CAST(:cfdi_report_id AS uuid)
        FOR UPDATE
    """),
                {"cfdi_report_id": cfdi_report_id},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise CollectionSourceConflict("collection_cfdi_source_not_found")


async def guard_collection_channel(
    session: Any,
    *,
    cfdi_report_id: str,
    channel: str,
    existing_direct_poliza_id: str = "",
) -> None:
    """Reject overlaps, including soft-unlinked history; never repair evidence."""
    if channel not in {"match", "direct"}:
        raise ValueError("Unknown collection channel")
    await lock_collection_source(session, cfdi_report_id)
    result = (
        (
            await session.execute(
                text("""
        SELECT
          EXISTS (SELECT 1 FROM ar_collection_matches
                  WHERE cfdi_report_id = CAST(:cfdi_report_id AS uuid)
                    AND status = 'accepted_collection_match') AS has_matches,
          EXISTS (SELECT 1 FROM budget_cfdi_income_links
                  WHERE cfdi_report_id = CAST(:cfdi_report_id AS uuid)
                    AND (collection_poliza_id IS NOT NULL OR collection_date IS NOT NULL)
                    AND (NULLIF(:existing_poliza, '') IS NULL
                         OR collection_poliza_id IS DISTINCT FROM CAST(NULLIF(:existing_poliza, '') AS uuid)))
          OR EXISTS (SELECT 1 FROM accounting_polizas p
                     JOIN cfdi_reports c ON c.id = CAST(:cfdi_report_id AS uuid)
                     WHERE (p.cfdi_report_id = c.id
                            OR (NULLIF(c.cfdi_uuid, '') IS NOT NULL
                                AND UPPER(p.cfdi_uuid) = UPPER(c.cfdi_uuid)))
                       AND p.origen IN ('cxc_collection', 'ingreso_cobrado_ui')
                       AND (NULLIF(:existing_poliza, '') IS NULL
                            OR p.id <> CAST(NULLIF(:existing_poliza, '') AS uuid))) AS has_other_direct
    """),
                {
                    "cfdi_report_id": cfdi_report_id,
                    "existing_poliza": (
                        existing_direct_poliza_id if channel == "direct" else ""
                    ),
                },
            )
        )
        .mappings()
        .first()
    )
    if not result or "has_matches" not in result or "has_other_direct" not in result:
        raise CollectionSourceConflict("collection_sources_unavailable")
    if result["has_other_direct"] or (channel == "direct" and result["has_matches"]):
        raise CollectionSourceConflict(
            "collection_channel_conflict: revisar recibos existentes; no se modificó su evidencia"
        )
