"""Business semantics shared by document routes and presentation layers."""

from __future__ import annotations

from typing import Any


EMPLOYEE_REIMBURSEMENT_CONCEPT_PREFIX = "Reembolso de saldo a favor"


def effective_account_beneficiary_id(cuenta: Any) -> Any:
    """Return who must receive funds without changing report ownership."""
    return (
        getattr(cuenta, "beneficiario_empleado_id", None)
        or getattr(cuenta, "empleado_id", None)
    )


def is_employee_reimbursement(documento: Any) -> bool:
    """Identify system-generated employee reimbursements, including legacy rows."""
    concept = str(getattr(documento, "concepto_pago", None) or "").strip()
    return bool(
        getattr(documento, "tipo", None) == "SOLICITUD"
        and getattr(documento, "cuenta_gastos_id", None)
        and getattr(documento, "beneficiario_empleado_id", None)
        and concept.startswith(EMPLOYEE_REIMBURSEMENT_CONCEPT_PREFIX)
    )


def approval_subject_empleado(documento: Any) -> Any:
    """Return the employee whose assigned approver governs approval routing.

    Third-party expense reports and advances are owned by the requester, but the
    approval lane belongs to the employee who receives/benefits from the funds.
    Existing direct documents keep the requester as the approval subject.
    """
    return (
        getattr(documento, "beneficiario_empleado", None)
        or getattr(documento, "empleado", None)
    )


def approval_subject_empleado_id(documento: Any) -> Any:
    """Return the effective employee id for approval routing without loading relations."""
    return (
        getattr(documento, "beneficiario_empleado_id", None)
        or getattr(documento, "empleado_id", None)
    )
