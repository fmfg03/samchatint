"""Pure diagnostic projection; no workflow handler, ORM or notifications."""

from __future__ import annotations

from typing import Any


def diagnose(document: dict[str, Any], authority: dict[str, Any]) -> dict[str, Any]:
    """Only the verified state precondition is attributed to the base workflow.

    Other canonical gates remain unknown until scoped owner facts are admitted.
    In particular, an 'enviado' document is never reported safe to approve.
    """
    state_allowed = document["state"] == "enviado"
    observations: list[str] = []
    if authority["resolution"] != "resolved":
        observations.append("authority_unresolved")
    elif not authority["actor_can_decide"]:
        observations.append("actor_is_observer_not_approver")
    if document["kind"] == "INFORME" and document["active_expenses"] == 0:
        observations.append("no_active_expenses")
    if not document["classification_present"]:
        observations.append("classification_missing")
    if document.get("recorded_approval"):
        observations.append("documento_already_advanced")
    if document.get("employee_reimbursement") and not document.get(
        "linked_informe_approved"
    ):
        observations.append("informe_approval_required")
    if document.get("partial_report") and not document.get("partial_reason_present"):
        first = "partial_reason_required"
    elif document.get("financial_terminal"):
        first = "workflow_locked_document"
    elif not state_allowed:
        first = "invalid_estado"
    else:
        # The canonical surplus and hard-strategy owners still precede a claim
        # of complete eligibility. Known later blockers remain observations.
        first = "unknown"
    return {
        "state_precondition": state_allowed,
        "state_blocker": None if state_allowed else "invalid_estado",
        "first_workflow_blocker": first,
        "observations": observations,
        "approval_eligibility": "unknown",
        "gaps": [
            "informe_surplus_and_authorization_strategy_not_projected",
            "accounting_posting_preflight_not_projected",
        ],
        "authority": authority,
        "effects": "none",
    }
