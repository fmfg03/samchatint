"""
Detect which physical column on expense_reports stores the legacy receipt blob.

Some deployments used a quoted "Archivos" column; the canonical name is
archivo_data. We probe information_schema once at process startup (before ORM
mappers are built) so ExpenseReport.archivo_data maps to the real column
without per-query workarounds.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import FrozenSet, Optional

logger = logging.getLogger(__name__)

ENV_RECEIPT_BLOB_COLUMN = "EXPENSE_REPORT_RECEIPT_BLOB_COLUMN"

# Whitelist only: never pass user input into DDL/DML identifiers.
KNOWN_RECEIPT_BLOB_PHYSICAL_COLUMNS: FrozenSet[str] = frozenset(
    {"archivo_data", "Archivos", "archivos"}
)


def resolved_receipt_blob_column_for_orm() -> str:
    """Physical column name for ExpenseReport.archivo_data."""
    value = (os.getenv(ENV_RECEIPT_BLOB_COLUMN) or "archivo_data").strip()
    return value if value in KNOWN_RECEIPT_BLOB_PHYSICAL_COLUMNS else "archivo_data"


def _normalize_postgres_dsn(url: str) -> Optional[str]:
    raw = (url or "").strip()
    if not raw or "postgresql" not in raw:
        return None
    if raw.startswith("postgresql+asyncpg://"):
        raw = raw.replace("postgresql+asyncpg://", "postgresql://", 1)
    elif raw.startswith("postgres+asyncpg://"):
        raw = raw.replace("postgres+asyncpg://", "postgres://", 1)
    return raw.split("?", 1)[0]


def pick_receipt_blob_column(column_names: FrozenSet[str]) -> Optional[str]:
    """Prefer canonical archivo_data, then legacy Archivos, then archivos."""
    for preferred in ("archivo_data", "Archivos", "archivos"):
        if preferred in column_names:
            return preferred
    return None


async def _probe_receipt_blob_column_async(dsn: str) -> Optional[str]:
    try:
        import asyncpg
    except ImportError:
        return None

    conn = None
    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn=dsn), timeout=5.0)
        rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'expense_reports'
              AND column_name = ANY($1::text[])
            """,
            list(KNOWN_RECEIPT_BLOB_PHYSICAL_COLUMNS),
        )
        names = frozenset(str(row["column_name"]) for row in rows)
        return pick_receipt_blob_column(names)
    except Exception:
        logger.debug(
            "Could not probe expense_reports receipt blob column; using default",
            exc_info=True,
        )
        return None
    finally:
        if conn is not None:
            await conn.close()


def configure_expense_receipt_blob_column_from_db() -> None:
    """
    If EXPENSE_REPORT_RECEIPT_BLOB_COLUMN is unset, detect archivo_data vs
    Archivos (etc.) and set the env var before devnous.gastos.models is
    imported.
    """
    existing = (os.getenv(ENV_RECEIPT_BLOB_COLUMN) or "").strip()
    if existing in KNOWN_RECEIPT_BLOB_PHYSICAL_COLUMNS:
        return
    if existing:
        logger.warning(
            "Ignoring invalid %s=%r (allowed: %s)",
            ENV_RECEIPT_BLOB_COLUMN,
            existing,
            ", ".join(sorted(KNOWN_RECEIPT_BLOB_PHYSICAL_COLUMNS)),
        )

    dsn = _normalize_postgres_dsn(
        os.getenv("DATABASE_URL") or os.getenv("POSTGRESQL_URL") or ""
    )
    if not dsn:
        return

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.debug("Skipping receipt blob column probe inside running event loop")
        return

    chosen = asyncio.run(_probe_receipt_blob_column_async(dsn))

    if chosen:
        os.environ[ENV_RECEIPT_BLOB_COLUMN] = chosen
        if chosen != "archivo_data":
            logger.info(
                "Mapped ExpenseReport.archivo_data to physical column %r (%s is set).",
                chosen,
                ENV_RECEIPT_BLOB_COLUMN,
            )
