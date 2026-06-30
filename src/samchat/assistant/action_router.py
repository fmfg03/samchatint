from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from .adapters import (
    AdapterResult,
    budgets_approve_version_adapter,
    budgets_freeze_version_adapter,
    budgets_reforecast_adapter,
    budgets_snapshot_adapter,
    budgets_submit_for_approval_adapter,
    budgets_update_line_adapter,
    budgets_update_version_adapter,
    assign_expense_accounting_adapter,
    approve_document_adapter,
    build_expense_accounting_preview_adapter,
    cfdi_matching_overview_adapter,
    cfdi_workflow_snapshot_adapter,
    create_solicitud_personal_adapter,
    create_solicitud_terceros_adapter,
    create_manual_expense_adapter,
    create_expense_from_operations_context_adapter,
    send_tournament_email_adapter,
    send_tournament_whatsapp_adapter,
    executive_accounting_report_adapter,
    executive_alerts_scan_adapter,
    executive_planner_snapshot_adapter,
    executive_realtime_report_adapter,
    executive_strategy_snapshot_adapter,
    expense_full_workflow_snapshot_adapter,
    operations_folder_planner_snapshot_adapter,
    operations_tournament_soul_snapshot_adapter,
    operations_create_media_asset_adapter,
    operations_create_solicitud_from_commitment_adapter,
    operations_send_tournament_reminder_adapter,
    operations_update_commitment_adapter,
    operations_update_team_status_adapter,
    operations_verify_player_document_adapter,
    link_bank_movement_to_expense_adapter,
    link_expense_to_cfdi_adapter,
    pending_document_payment_overview_adapter,
    post_expense_accounting_adapter,
    reject_document_adapter,
    register_document_payment_adapter,
    register_document_reembolso_adapter,
    request_cfdi_adapter,
    send_document_adapter,
)
from .context import AssistantContext


AdapterCallable = Callable[
    [AsyncSession], Awaitable[AdapterResult]
]  # pragma: no cover - documentation alias only


_ROUTES: Dict[str, Callable[..., Awaitable[AdapterResult]]] = {
    "expenses.create_manual_expense": create_manual_expense_adapter,
    "expenses.create_solicitud_personal": create_solicitud_personal_adapter,
    "expenses.create_solicitud_terceros": create_solicitud_terceros_adapter,
    "budgets.snapshot": budgets_snapshot_adapter,
    "budgets.update_line": budgets_update_line_adapter,
    "budgets.update_version": budgets_update_version_adapter,
    "budgets.submit_for_approval": budgets_submit_for_approval_adapter,
    "budgets.approve_version": budgets_approve_version_adapter,
    "budgets.freeze_version": budgets_freeze_version_adapter,
    "budgets.reforecast": budgets_reforecast_adapter,
    "executive.realtime_report": executive_realtime_report_adapter,
    "executive.strategy_snapshot": executive_strategy_snapshot_adapter,
    "executive.accounting_report": executive_accounting_report_adapter,
    "executive.alerts_scan": executive_alerts_scan_adapter,
    "executive.planner_snapshot": executive_planner_snapshot_adapter,
    "expense.full_workflow_snapshot": expense_full_workflow_snapshot_adapter,
    "operations.folder_planner_snapshot": operations_folder_planner_snapshot_adapter,
    "operations.tournament_soul_snapshot": operations_tournament_soul_snapshot_adapter,
    "operations.create_media_asset": operations_create_media_asset_adapter,
    "operations.create_solicitud_from_commitment": operations_create_solicitud_from_commitment_adapter,
    "operations.send_tournament_reminder": operations_send_tournament_reminder_adapter,
    "operations.update_commitment": operations_update_commitment_adapter,
    "operations.update_team_status": operations_update_team_status_adapter,
    "operations.verify_player_document": operations_verify_player_document_adapter,
    "operations.create_expense_from_context": create_expense_from_operations_context_adapter,
    "communications.send_tournament_email": send_tournament_email_adapter,
    "communications.send_tournament_whatsapp": send_tournament_whatsapp_adapter,
    "receipts.link_expense_to_cfdi": link_expense_to_cfdi_adapter,
    "receipts.request_cfdi": request_cfdi_adapter,
    "accounting.build_expense_preview": build_expense_accounting_preview_adapter,
    "accounting.assign_expense_accounting": assign_expense_accounting_adapter,
    "accounting.post_expense_accounting": post_expense_accounting_adapter,
    "receipts.cfdi_matching_overview": cfdi_matching_overview_adapter,
    "receipts.cfdi_workflow_snapshot": cfdi_workflow_snapshot_adapter,
    "receipts.pending_payment_overview": pending_document_payment_overview_adapter,
    "receipts.register_document_payment": register_document_payment_adapter,
    "receipts.register_document_reembolso": register_document_reembolso_adapter,
    "receipts.send_document": send_document_adapter,
    "receipts.approve_document": approve_document_adapter,
    "receipts.reject_document": reject_document_adapter,
    "accounting.link_bank_to_expense": link_bank_movement_to_expense_adapter,
}


async def execute_canonical_action(
    action: str,
    *,
    session: AsyncSession,
    context: AssistantContext | Dict[str, Any] | None = None,
    payload: Dict[str, Any] | None = None,
) -> AdapterResult:
    """Route a canonical assistant action to the current module-owned implementation."""

    adapter = _ROUTES.get(action)
    if adapter is None:
        raise KeyError(f"unknown canonical action: {action}")
    normalized_context = (
        context
        if isinstance(context, AssistantContext)
        else AssistantContext.from_dict(context)
    )
    return await adapter(
        session,
        context=normalized_context,
        payload=payload or {},
    )


def supported_actions() -> list[str]:
    return sorted(_ROUTES.keys())


def supported_read_actions() -> list[str]:
    return sorted(
        [
            "accounting.build_expense_preview",
            "budgets.snapshot",
            "executive.accounting_report",
            "executive.alerts_scan",
            "executive.planner_snapshot",
            "executive.realtime_report",
            "executive.strategy_snapshot",
            "expense.full_workflow_snapshot",
            "operations.folder_planner_snapshot",
            "operations.tournament_soul_snapshot",
            "receipts.cfdi_matching_overview",
            "receipts.cfdi_workflow_snapshot",
            "receipts.pending_payment_overview",
        ]
    )


def supported_write_actions() -> list[str]:
    return sorted(
        [
            "accounting.link_bank_to_expense",
            "accounting.assign_expense_accounting",
            "accounting.post_expense_accounting",
            "budgets.approve_version",
            "budgets.freeze_version",
            "budgets.reforecast",
            "budgets.submit_for_approval",
            "budgets.update_line",
            "budgets.update_version",
            "communications.send_tournament_email",
            "communications.send_tournament_whatsapp",
            "expenses.create_manual_expense",
            "expenses.create_solicitud_personal",
            "expenses.create_solicitud_terceros",
            "operations.create_expense_from_context",
            "operations.create_media_asset",
            "operations.create_solicitud_from_commitment",
            "operations.send_tournament_reminder",
            "operations.update_commitment",
            "operations.update_team_status",
            "operations.verify_player_document",
            "receipts.approve_document",
            "receipts.link_expense_to_cfdi",
            "receipts.register_document_payment",
            "receipts.register_document_reembolso",
            "receipts.reject_document",
            "receipts.request_cfdi",
            "receipts.send_document",
        ]
    )
