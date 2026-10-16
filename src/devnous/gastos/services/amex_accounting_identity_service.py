"""Read predicates for explicit AMEX bank control identities. No matching or DDL."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import exists, select, text
from sqlalchemy.sql.elements import ColumnElement

from ..models import (
    AmexRecognitionConsumption,
    AmexStatementImport,
    AmexStatementImportItem,
    ExpenseReport,
)


def amex_bank_header_condition(expense_model: Any) -> ColumnElement[bool]:
    """Only an explicit nonposting bridge marks a bank row as control evidence."""
    return exists(
        select(AmexRecognitionConsumption.id).where(
            AmexRecognitionConsumption.imported_expense_id == expense_model.id,
            AmexRecognitionConsumption.source_kind == "statement",
            AmexRecognitionConsumption.binding_mode == "report_bridge",
        )
    ).correlate(expense_model)


def accounting_expense_condition(expense_model: Any) -> ColumnElement[bool]:
    """Preserve report targets and every unrelated or unassociated expense."""
    return ~amex_bank_header_condition(expense_model)


def accounting_expense_sql(alias: str = "e") -> str:
    """Same identity predicate for existing parameterized budget SQL readers."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", alias):
        raise ValueError("Invalid expense SQL alias")
    return f"""NOT EXISTS (SELECT 1 FROM amex_recognition_consumptions amex_identity
        WHERE amex_identity.imported_expense_id = {alias}.id
          AND amex_identity.source_kind = 'statement'
          AND amex_identity.binding_mode = 'report_bridge')"""


async def effective_direct_sources_available(session: Any) -> bool:
    """Legacy installations lack the additive owner; a partial owner fails closed."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT to_regclass('amex_direct_effective_sources') "
                    "IS NOT NULL AS view_present, "
                    "(to_regclass('amex_direct_recovered_rows') IS NOT NULL OR "
                    "to_regclass('amex_direct_coverage_revisions') IS NOT NULL) "
                    "AS recovery_present"
                )
            )
        )
        .mappings()
        .one()
    )
    if row["recovery_present"] and not row["view_present"]:
        from .amex_direct_schema_service import DirectSchemaUnavailable

        raise DirectSchemaUnavailable("effective_direct_source_unavailable")
    if row["view_present"]:
        from .amex_direct_schema_service import require_amex_direct_schema

        await require_amex_direct_schema(session)
    return bool(row["view_present"])


def direct_amex_expense_condition(
    expense_model: Any, *, effective_sources: bool = False
) -> ColumnElement[bool]:
    """Resolve audited direct ownership; preserve pre-owner legacy installations."""
    if effective_sources:
        from .amex_direct_effective_source_service import effective_sources_select

        sources = effective_sources_select()
        return exists(
            select(sources.c.id).where(
                sources.c.imported_expense_id == expense_model.id
            )
        ).correlate(expense_model)
    return exists(
        select(AmexStatementImportItem.id)
        .join(
            AmexStatementImport,
            AmexStatementImport.id == AmexStatementImportItem.import_id,
        )
        .where(
            AmexStatementImportItem.imported_expense_id == expense_model.id,
            AmexStatementImportItem.workflow_mode == "direct",
            AmexStatementImport.workflow_mode == "direct",
        )
    ).correlate(expense_model)


async def is_direct_amex_expense(session: Any, expense_id: Any) -> bool:
    """Read source ownership before a legacy fiscal mutation."""
    effective = await effective_direct_sources_available(session)
    return bool(
        await session.scalar(
            select(
                direct_amex_expense_condition(
                    ExpenseReport, effective_sources=effective
                )
            ).where(ExpenseReport.id == expense_id)
        )
    )
