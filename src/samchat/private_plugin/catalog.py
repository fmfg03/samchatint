"""Finite catalog. Every business operation is disabled, without feature flags."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    action: str
    scope: str
    write: bool
    canonical: str
    disabled_reason: str = "CANONICAL_SCOPE_UNPROVEN"


# Explicit canonical names, not a model-directed dispatcher or import path.
READS = (
    "budgets.snapshot",
    "executive.realtime_report",
    "executive.strategy_snapshot",
    "executive.accounting_report",
    "executive.alerts_scan",
    "executive.planner_snapshot",
    "expense.full_workflow_snapshot",
    "operations.folder_planner_snapshot",
    "operations.tournament_registration_executive_reports",
    "operations.tournament_soul_snapshot",
    "accounting.build_expense_preview",
    "receipts.cfdi_matching_overview",
    "receipts.cfdi_workflow_snapshot",
    "receipts.pending_payment_overview",
)
WRITES = (
    "expenses.create_manual_expense",
    "expenses.create_solicitud_personal",
    "expenses.create_solicitud_terceros",
    "expenses.create_personal_receipt_workflow",
    "expenses.create_third_party_receipt_workflow",
    "budgets.update_line",
    "budgets.update_version",
    "budgets.submit_for_approval",
    "budgets.approve_version",
    "budgets.freeze_version",
    "budgets.reforecast",
    "operations.create_media_asset",
    "operations.create_solicitud_from_commitment",
    "operations.send_tournament_reminder",
    "operations.update_commitment",
    "operations.update_team_status",
    "operations.verify_player_document",
    "operations.create_expense_from_context",
    "communications.send_tournament_email",
    "communications.send_tournament_whatsapp",
    "receipts.link_expense_to_cfdi",
    "receipts.request_cfdi",
    "accounting.assign_expense_accounting",
    "accounting.post_expense_accounting",
    "receipts.register_document_payment",
    "receipts.register_document_reembolso",
    "receipts.send_document",
    "receipts.approve_document",
    "receipts.reject_document",
    "accounting.link_bank_to_expense",
)


def operations() -> tuple[Operation, ...]:
    """Candidates only; no canonical handler is imported or invoked."""
    result = [
        Operation(action, f'{action.split(".")[0]}:{mode}', write, action)
        for names, mode, write in ((READS, "read", False), (WRITES, "write", True))
        for action in names
    ]
    for name, write, path in (
        ("registration.review", False, "/api/registration-review/{session_id}"),
        ("registration.edit", True, "/api/registration-review/{session_id}/edit"),
        (
            "registration.adopt",
            True,
            "/api/registration-review/{session_id}/canonical-adopt",
        ),
        ("registration.reject", True, "/api/registration-review/{session_id}/reject"),
        (
            "registration.reprocess",
            True,
            "/api/registration-review/{session_id}/reprocess",
        ),
        ("registration.assets", True, "/api/registration-review/{session_id}/assets"),
        ("registration.commit", True, "/api/registration-review/{session_id}/commit"),
    ):
        result.append(
            Operation(name, f'registration:{"write" if write else "read"}', write, path)
        )
    return tuple(result)


def advertised_tools() -> list[dict]:
    """No disabled capability may appear as a usable MCP tool."""
    return []
