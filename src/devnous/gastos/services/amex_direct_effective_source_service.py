"""Explicit economic source resolution; physical ORM membership stays immutable."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Column, Date, MetaData, Numeric, String, Table, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID


@dataclass(frozen=True)
class EffectiveSource:
    direct_item_id: UUID
    import_id: UUID
    import_item_id: UUID
    movement_kind: str
    imported_expense_id: UUID | None
    source_json: dict[str, Any]
    raw_signed_amount: Decimal | None
    raw_amount: str | None
    amount_column: str | None
    profile: str | None
    recovery_id: UUID | None
    recovery_revision_id: UUID | None
    recovery_proof_hash: str | None
    period_start: date
    period_end: date


# Read-only SQL relation, deliberately absent from runtime-managed Base.metadata.
_SOURCE_VIEW = Table(
    "amex_direct_effective_sources",
    MetaData(),
    *[
        Column(name, PGUUID(as_uuid=True))
        for name in (
            "id",
            "import_id",
            "import_item_id",
            "imported_expense_id",
            "recovery_id",
            "recovery_revision_id",
            "cfdi_id",
            "context_id",
        )
    ],
    Column("movement_kind", String),
    Column("source_json", JSONB),
    Column("raw_signed_amount", Numeric(18, 2)),
    Column("raw_amount", String),
    Column("amount_column", String),
    Column("profile", String),
    Column("recovery_proof_hash", String),
    Column("period_start", Date),
    Column("period_end", Date),
)


def effective_sources_select() -> Table:
    """Canonical scoped/global SQL source selectable, with no write owner."""
    return _SOURCE_VIEW


async def load_effective_sources(
    session: Any, import_id: UUID
) -> dict[UUID, EffectiveSource]:
    rows = (
        (
            await session.execute(
                select(_SOURCE_VIEW).where(_SOURCE_VIEW.c.import_id == import_id)
            )
        )
        .mappings()
        .all()
    )
    return {row["id"]: _from_row(row) for row in rows}


def _from_row(row: Any) -> EffectiveSource:
    values = {
        name: row[name]
        for name in EffectiveSource.__dataclass_fields__
        if name != "direct_item_id"
    }
    return EffectiveSource(direct_item_id=row["id"], **values)


async def effective_source_for_item(session: Any, item: Any) -> EffectiveSource:
    row = (
        (
            await session.execute(
                select(_SOURCE_VIEW).where(
                    _SOURCE_VIEW.c.id == item.id,
                    _SOURCE_VIEW.c.import_id == item.import_id,
                )
            )
        )
        .mappings()
        .one()
    )
    return _from_row(row)
