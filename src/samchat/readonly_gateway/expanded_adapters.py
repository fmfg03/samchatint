"""Real canonical call adapters, unwired and connection-free by default.

Tests use explicitly labelled synthetic sources/sessions. No adapter is evidence
of production access. Binding a session or policy requires a separate review.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Callable
from uuid import UUID

from .contracts import ReadError
from .expanded import Authority, Observation, digest
from .expanded_catalog import Query


@asynccontextmanager
async def postgres_read_session(
    factory: Callable[[], Any], *, expected_role: str, authority: bool = False
):
    """Dedicated fresh session; no DSN, credentials or role alteration here.

    The existing INFORME role cannot be reused for this separate candidate.
    Rollback/close are mandatory on success, cancellation and exceptions.
    """
    from sqlalchemy import text

    if not expected_role or expected_role == "samchat_ro_fmfg":
        raise ReadError("SEPARATE_READ_ROLE_REQUIRED")
    isolation = "READ COMMITTED" if authority else "REPEATABLE READ"
    async with factory() as session:
        if session.in_transaction() or session.new or session.dirty or session.deleted:
            raise ReadError("FRESH_SESSION_REQUIRED")
        session.autoflush = False
        try:
            await session.execute(
                text(f"SET TRANSACTION ISOLATION LEVEL {isolation}, READ ONLY")
            )
            await session.execute(text("SET LOCAL statement_timeout = '2500ms'"))
            await session.execute(text("SET LOCAL lock_timeout = '500ms'"))
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT current_user AS role, session_user AS session_role, "
                            "current_setting('transaction_read_only') AS readonly, "
                            "current_setting('transaction_isolation') AS isolation, "
                            "rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls "
                            "FROM pg_roles WHERE rolname=current_user"
                        )
                    )
                )
                .mappings()
                .one()
            )
            if (
                row["role"] != expected_role
                or row["session_role"] != expected_role
                or row["readonly"] != "on"
                or row["isolation"] != isolation.lower()
                or any(
                    row[k]
                    for k in (
                        "rolsuper",
                        "rolcreaterole",
                        "rolcreatedb",
                        "rolreplication",
                        "rolbypassrls",
                    )
                )
            ):
                raise ReadError("READONLY_ROLE_UNPROVEN")
            yield session
        finally:
            await session.rollback()


class CanonicalDatasetAdapter:
    """Source-specific canonical invocations and minimal projections.

    `session_scope` is trusted composition around postgres_read_session, never
    an MCP argument. Domain guards + fiscal/card/object scope belong to the
    fresh Authority resolver. All IDs must be authorized before opening it.
    """

    def __init__(
        self, session_scope: Callable[[], Any], *, amex_authorized_reader=None
    ) -> None:
        self.session_scope = session_scope
        # Server composition only. Must perform the canonical legacy/direct
        # guard including card/context scope; a get-by-ID reader is insufficient.
        self.amex_authorized_reader = amex_authorized_reader

    def supports(self, dataset: str) -> bool:
        return dataset != "amex.statement" or self.amex_authorized_reader is not None

    async def read(self, query: Query, authority: Authority) -> Observation:
        if not authority.permits(query):
            raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
        if not self.supports(query.dataset):
            raise ReadError("AMEX_AUTHORITY_UNBOUND")
        async with self.session_scope() as session:
            return await self._read(session, query, authority)

    async def _read(
        self, session: Any, q: Query, authority: Authority | None = None
    ) -> Observation:
        gaps: list[str] = []
        if q.dataset == "solicitud":
            from devnous.gastos.services.connector_read_service import read_solicitudes

            rows = await read_solicitudes(
                session,
                object_ids=q.object_ids,
                tournament_id=q.tournament_id,
                date_from=q.start,
                date_to=q.end,
            )
            gaps.append("persisted_document_amount_not_payment_or_accounting_proof")
        elif q.dataset == "budget.snapshot":
            from samchat.budgets.service import build_budget_snapshot

            raw = await build_budget_snapshot(
                session,
                tournament_id=q.tournament_id,
                version_id=q.version_id,
                edition_year=q.start.year,
                date_from=q.start,
                date_to=q.end - timedelta(days=1),
                ensure_schema=False,
                strict_tournament_scope=True,
                executive_read=True,
            )
            if (
                raw.get("source") != "budget_db"
                or str(raw.get("version", {}).get("id")) != q.version_id
            ):
                raise ReadError("SOURCE_VERSION_UNPROVEN")
            summary = raw.get("summary", {})
            rows = [
                {
                    "object_id": q.version_id,
                    **{
                        k: summary.get(k)
                        for k in (
                            "budget_total",
                            "actual_total",
                            "committed_total",
                            "paid_total",
                        )
                    },
                }
            ]
            if raw.get("executive_quality_gaps"):
                # Canonical gaps do not license an apparently complete numeric total.
                rows = [
                    {
                        key: value if key == "object_id" else None
                        for key, value in rows[0].items()
                    }
                ]
                gaps.append("canonical_budget_quality_gaps_totals_withheld")
            gaps.append("paid_document_amount_is_not_bank_cash")
            gaps.append("budget_total_full_version_edition_not_period_prorated")
        elif q.dataset == "ar.summary":
            from samchat.ar.service import build_ar_read_model
            from samchat.budgets.service import _select_budget_version

            version = await _select_budget_version(
                session,
                edition_year=q.start.year,
                version_id=q.version_id,
                ensure_schema=False,
            )
            if (
                not version
                or version.get("id") != q.version_id
                or version.get("edition_year") != q.start.year
                or version.get("status") not in {"approved", "frozen"}
            ):
                raise ReadError("SOURCE_VERSION_UNPROVEN")

            raw = await build_ar_read_model(
                session,
                budget_version_id=q.version_id,
                tournament_id=q.tournament_id,
                ensure_schema=False,
                strict_tournament_scope=True,
                calendar_year=q.start.year,
                as_of_date=q.end - timedelta(days=1),
                limit=500,
            )
            if (
                raw.get("budget_version_id") != q.version_id
                or raw.get("tournament_id") != q.tournament_id
            ):
                raise ReadError("SOURCE_SCOPE_ESCAPE")
            status = raw.get("source_status", {})
            if status.get("strict_tournament_scope") is not True:
                raise ReadError("SOURCE_SCOPE_ESCAPE")
            # The owner collapses multiple legal partial payments into one dict
            # entry. Withhold that lossy summary; do not rewrite accounting here.
            item_ids = [
                str(item["ar_item_id"])
                for item in [
                    *raw.get("issued_linked", []),
                    *raw.get("issued_unlinked", []),
                ]
                if item.get("ar_item_id")
            ]
            multiple_matches = False
            if item_ids:
                from samchat.ar.collection_matches import list_ar_collection_matches

                matches = await list_ar_collection_matches(
                    session,
                    budget_version_id=q.version_id,
                    include_reversed=False,
                    ensure_schema=False,
                    ar_item_ids=item_ids,
                )
                matched_ids = [str(item["ar_item_id"]) for item in matches]
                if set(matched_ids) - set(item_ids):
                    raise ReadError("SOURCE_SCOPE_ESCAPE")
                multiple_matches = len(matched_ids) != len(set(matched_ids))
                if multiple_matches:
                    gaps.append("canonical_ar_multiple_matches_totals_withheld")
            summary = raw.get("summary", {})
            rows = [
                {
                    "object_id": q.version_id,
                    **{
                        k: summary.get(k)
                        for k in (
                            "expected_income_total",
                            "invoiced_total",
                            "collected_total",
                            "balance_total",
                            "collection_gap_count",
                            "matching_gap_count",
                        )
                    },
                }
            ]
            if multiple_matches or any(
                status.get(key)
                for key in (
                    "income_lines_truncated",
                    "candidates_truncated",
                    "currency_gap",
                    "amount_gap",
                )
            ):
                rows = [
                    {
                        key: value if key == "object_id" else None
                        for key, value in rows[0].items()
                    }
                ]
                gaps.append("canonical_ar_quality_or_coverage_gaps_totals_withheld")
            gaps.extend(
                (
                    "collection_requires_canonical_match_or_posted_collection",
                    "as_of_model_not_transaction_period_slice",
                    "historical_as_of_balances_not_supported",
                )
            )
        elif q.dataset == "payment_run":
            from devnous.gastos.services.payment_run_service import (
                list_payment_run_items,
            )

            raw = await list_payment_run_items(
                session,
                status_filter="todas",
                date_from=q.start,
                date_to=q.end - timedelta(days=1),
                limit=101,
                ensure_schema=False,
                document_ids=list(q.object_ids),
                tournament_id=q.tournament_id,
            )
            rows = [
                {
                    "object_id": str(row["id"]),
                    "state": row.get("estado"),
                    "scheduled_date": (
                        str(row["fecha_pago"]) if row.get("fecha_pago") else None
                    ),
                    "effective_payment_date": (
                        str(row["fecha_pago_efectiva"])
                        if row.get("fecha_pago_efectiva")
                        else None
                    ),
                    "currency": row.get("currency"),
                    "amount": (
                        None
                        if row.get("amount_issue") or row["source_amount_missing"]
                        else row.get("monto")
                    ),
                    "amount_missing": bool(
                        row.get("amount_issue") or row["source_amount_missing"]
                    ),
                }
                for row in raw
            ]
            gaps.extend(
                (
                    "cutoff_is_not_payment_proof",
                    "unscheduled_rows_outside_date_selection",
                )
            )
        elif q.dataset == "amex.statement":
            if self.amex_authorized_reader is None or authority is None:
                raise ReadError("AMEX_AUTHORITY_UNBOUND")

            rows = []
            for identifier in q.object_ids:
                raw = await self.amex_authorized_reader(
                    session, authority, UUID(identifier)
                )
                if raw is None or raw.period_end < q.start or raw.period_start >= q.end:
                    continue
                rows.append(
                    {
                        "object_id": str(raw.id),
                        "period_start": raw.period_start.isoformat(),
                        "period_end": raw.period_end.isoformat(),
                        **{
                            key: getattr(raw, key, None)
                            for key in (
                                "workflow_mode",
                                "total_rows",
                                "imported_count",
                                "skipped_count",
                                "credit_count",
                                "unknown_count",
                            )
                        },
                    }
                )
            gaps.extend(
                (
                    "statement_identity_only_not_reconciliation_or_accounting_proof",
                    "card_source_attachments_and_related_reports_not_exposed",
                )
            )
        else:
            raise ReadError("CANONICAL_ADAPTER_UNBOUND")
        found = {str(row["object_id"]) for row in rows}
        if found != set(q.object_ids):
            gaps.append("requested_objects_missing_or_outside_period")
        return Observation(
            rows, tuple(gaps), digest(rows), "bounded_authorized_candidate_projection"
        )
