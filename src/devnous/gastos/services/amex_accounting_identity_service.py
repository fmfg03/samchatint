"""Read predicates for explicit AMEX bank control identities. No matching or DDL."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.sql.elements import ColumnElement

from ..models import AmexRecognitionConsumption


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
