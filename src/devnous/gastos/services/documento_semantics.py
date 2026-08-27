"""Business semantics shared by document routes and presentation layers."""

from __future__ import annotations

from typing import Any


EMPLOYEE_REIMBURSEMENT_CONCEPT_PREFIX = "Reembolso de saldo a favor"


def effective_account_beneficiary_id(cuenta: Any) -> Any:
    """Return the employee beneficiary without changing report ownership."""
    return (
        getattr(cuenta, "beneficiario_empleado_id", None)
        or getattr(cuenta, "empleado_id", None)
    )


def effective_account_provider_beneficiary_id(cuenta: Any) -> Any:
    """Return provider/operator beneficiary id when the report is for a non-employee."""
    return getattr(cuenta, "beneficiario_proveedor_cliente_id", None)


def account_uses_provider_beneficiary(cuenta: Any) -> bool:
    """True when reimbursement/bank account must resolve against provider/operator."""
    return effective_account_provider_beneficiary_id(cuenta) is not None


def reimbursement_concept_from_cuenta(cuenta: Any) -> str:
    """Build the SOLICITUD concept for an employee reimbursement from an expense report.

    Keep the legacy prefix so existing reimbursement detection remains stable,
    while carrying the user-entered report motive/description for quick review.
    """
    prefix = EMPLOYEE_REIMBURSEMENT_CONCEPT_PREFIX
    if cuenta is None:
        return prefix

    report_ref = str(getattr(cuenta, "referencia_base", None) or "").strip()
    report_description = str(getattr(cuenta, "nombre", None) or "").strip()
    parts = [prefix]
    if report_description:
        parts.append(report_description)
    if report_ref:
        parts.append(f"I-{report_ref}")
    return " — ".join(parts)


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
