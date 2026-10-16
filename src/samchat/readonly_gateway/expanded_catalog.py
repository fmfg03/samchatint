"""Candidate-only dataset contracts; no additions to the accepted tool catalog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from .contracts import ReadError


@dataclass(frozen=True)
class Dataset:
    fields: tuple[str, ...]
    measures: tuple[str, ...]
    groups: tuple[str, ...]
    period_basis: str
    source: str
    version_required: bool = False


DATASETS = {
    "solicitud": Dataset(
        ("object_id", "kind", "state", "created_at", "currency", "amount"),
        ("amount",),
        ("state", "currency"),
        "document_created_at",
        "devnous.gastos.services.connector_read_service.read_solicitudes",
    ),
    "budget.snapshot": Dataset(
        ("object_id", "budget_total", "actual_total", "committed_total", "paid_total"),
        (),
        (),
        "actuals_by_expense_date_documents_by_creation_budget_total_full_edition",
        "samchat.budgets.service.build_budget_snapshot",
        True,
    ),
    "ar.summary": Dataset(
        (
            "object_id",
            "expected_income_total",
            "invoiced_total",
            "collected_total",
            "balance_total",
            "collection_gap_count",
            "matching_gap_count",
        ),
        (),
        (),
        "current_source_snapshot_with_aging_as_of_date_not_historical_balances",
        "samchat.ar.service.build_ar_read_model",
        True,
    ),
    "payment_run": Dataset(
        (
            "object_id",
            "state",
            "scheduled_date",
            "effective_payment_date",
            "currency",
            "amount",
            "amount_missing",
        ),
        ("amount",),
        ("state", "currency"),
        "scheduled_payment_date_inclusive_owner",
        "devnous.gastos.services.payment_run_service.list_payment_run_items",
    ),
    "amex.statement": Dataset(
        (
            "object_id",
            "period_start",
            "period_end",
            "workflow_mode",
            "total_rows",
            "imported_count",
            "skipped_count",
            "credit_count",
            "unknown_count",
        ),
        (),
        ("workflow_mode",),
        "declared_statement_period_overlap",
        "samchat.readonly_gateway.canonical_authority.CanonicalAuthorityResolver.amex_reader",
    ),
}


@dataclass(frozen=True)
class Query:
    dataset: str
    object_ids: tuple[str, ...]
    tournament_id: str
    version_id: str
    start: date
    end: date
    fields: tuple[str, ...]
    group_by: str
    measure: str
    page_size: int
    cursor: str

    @classmethod
    def parse(cls, args: dict[str, Any]) -> Query:
        allowed = {
            "dataset",
            "object_ids",
            "tournament_id",
            "version_id",
            "date_from",
            "date_to",
            "fields",
            "group_by",
            "measure",
            "page_size",
            "cursor",
        }
        if type(args) is not dict or set(args) - allowed:
            raise ReadError("INVALID_ARGUMENTS")
        try:
            name = args["dataset"]
            spec = DATASETS[name]
            ids = args["object_ids"]
            if type(ids) is not list or not 1 <= len(ids) <= 100:
                raise ValueError
            for identifier in ids + [args["tournament_id"]]:
                if type(identifier) is not str or str(UUID(identifier)) != identifier:
                    raise ValueError
            if len(set(ids)) != len(ids):
                raise ValueError
            version = args.get("version_id", "")
            if spec.version_required:
                if str(UUID(version)) != version or ids != [version]:
                    raise ValueError
            elif version:
                raise ValueError
            start, end = date.fromisoformat(args["date_from"]), date.fromisoformat(
                args["date_to"]
            )
            if (
                start >= end
                or (end - start).days > 366
                or args["date_from"] != start.isoformat()
                or args["date_to"] != end.isoformat()
            ):
                raise ValueError
            if spec.version_required and (end - timedelta(days=1)).year != start.year:
                raise ValueError
            # AR owns an as-of model; arbitrary transaction-period slicing is not proven.
            if name == "ar.summary" and (start.month != 1 or start.day != 1):
                raise ValueError
            page = args.get("page_size", 25)
            if type(page) is not int or not 1 <= page <= 100:
                raise ValueError
            fields = args.get("fields", list(spec.fields))
            if (
                type(fields) is not list
                or not fields
                or any(type(f) is not str for f in fields)
            ):
                raise ValueError
            if len(set(fields)) != len(fields) or set(fields) - set(spec.fields):
                raise ValueError
            group, measure = args.get("group_by", ""), args.get("measure", "")
            if type(group) is not str or type(measure) is not str:
                raise ValueError
            if (
                group
                and group not in spec.groups
                or measure
                and measure not in spec.measures
            ):
                raise ValueError
            if measure and not group:
                raise ValueError
            cursor = args.get("cursor", "")
            if type(cursor) is not str or len(cursor) > 4096:
                raise ValueError
            return cls(
                name,
                tuple(sorted(ids)),
                args["tournament_id"],
                version,
                start,
                end,
                tuple(fields),
                group,
                measure,
                page,
                cursor,
            )
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise ReadError("INVALID_ARGUMENTS") from exc


def dataset_catalog() -> dict[str, Any]:
    return {
        name: {
            "fields": list(spec.fields),
            "sum_fields": list(spec.measures),
            "group_by": list(spec.groups),
            "period_basis": spec.period_basis,
            "filters": ["object_ids", "tournament_id", "date_from", "date_to"]
            + (["version_id"] if spec.version_required else []),
            "source": spec.source,
            "maximum_period_days": 366,
            "maximum_objects": 100,
            "maximum_page_size": 100,
            "pagination": "signed_cursor_bound_to_identity_policy_query_snapshot",
            "arbitrary_sql": False,
            "arbitrary_functions": False,
        }
        for name, spec in DATASETS.items()
    }
