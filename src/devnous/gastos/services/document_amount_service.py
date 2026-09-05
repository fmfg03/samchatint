"""Canonical payable amounts for expense documents."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Optional

EMPLOYEE_REIMBURSEMENT_PREFIX = "reembolso de saldo a favor"


def _value(document: Any, field: str) -> Any:
    if isinstance(document, Mapping):
        return document.get(field)
    return getattr(document, field, None)


def is_employee_reimbursement_document(document: Any) -> bool:
    concept = str(_value(document, "concepto_pago") or "").strip().lower()
    return concept.startswith(EMPLOYEE_REIMBURSEMENT_PREFIX)


def resolve_payable_document_amount(document: Any) -> Optional[Decimal]:
    """Return the payable amount, failing closed for reimbursements."""
    total = _value(document, "monto_total")
    if is_employee_reimbursement_document(document):
        if total is None:
            return None
        return Decimal(str(total)).quantize(Decimal("0.01"))

    requested = _value(document, "monto_solicitado")
    value = total if total is not None else requested
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))
