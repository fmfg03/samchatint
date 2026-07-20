from __future__ import annotations

import uuid

from samchat.assistant.action_router import (
    supported_actions,
    supported_read_actions,
    supported_write_actions,
)
from samchat.assistant.context import AssistantContext
from samchat.assistant.router import (
    _build_accounting_assign_canonical_pending,
    _build_accounting_post_canonical_pending,
    _build_bank_link_canonical_pending,
    _build_cfdi_canonical_pending,
    _build_expense_canonical_pending,
    _build_link_cfdi_canonical_pending,
    _extract_cfdi_use_from_message,
    _extract_uuid_candidates,
    _is_explicit_approval_message,
    _is_explicit_rejection_message,
    _maybe_append_export_prompt,
)


class _ConversationStub:
    def __init__(self, metadata_: dict | None = None) -> None:
        self.metadata_ = metadata_


def test_assistant_context_from_dict_normalizes_common_aliases() -> None:
    context = AssistantContext.from_dict(
        {
            "deporte": "beisbol",
            "tournament_id": "abc",
            "torneo": "Liga Telmex Telcel",
            "edicion": "2025",
            "phase": "Nacional",
            "concept": "Hospedaje",
            "department": "Operaciones",
            "rama": "Varonil",
            "categoria": "13-14",
            "empleado_id": "user-1",
            "cuenta_id": "cuenta-1",
            "documento_id": "doc-1",
            "referencia_base": "RB-100",
            "referencia_operaciones": "42",
        }
    )

    assert context.sport == "beisbol"
    assert context.tournament_id == "abc"
    assert context.tournament_name == "Liga Telmex Telcel"
    assert context.edition == "2025"
    assert context.fase_torneo == "Nacional"
    assert context.concepto == "Hospedaje"
    assert context.departamento == "Operaciones"
    assert context.branch == "Varonil"
    assert context.category == "13-14"
    assert context.responsible_user_id == "user-1"
    assert context.expense_account_id == "cuenta-1"
    assert context.document_id == "doc-1"
    assert context.referencia_base == "RB-100"
    assert context.referencia_operaciones == "42"


def test_supported_actions_lists_initial_adapter_surface() -> None:
    assert supported_actions() == [
        "accounting.assign_expense_accounting",
        "accounting.build_expense_preview",
        "accounting.link_bank_to_expense",
        "accounting.post_expense_accounting",
        "budgets.approve_version",
        "budgets.freeze_version",
        "budgets.reforecast",
        "budgets.snapshot",
        "budgets.submit_for_approval",
        "budgets.update_line",
        "budgets.update_version",
        "communications.send_tournament_email",
        "communications.send_tournament_whatsapp",
        "executive.accounting_report",
        "executive.alerts_scan",
        "executive.planner_snapshot",
        "executive.realtime_report",
        "executive.strategy_snapshot",
        "expense.full_workflow_snapshot",
        "expenses.create_manual_expense",
        "expenses.create_personal_receipt_workflow",
        "expenses.create_solicitud_personal",
        "expenses.create_solicitud_terceros",
        "expenses.create_third_party_receipt_workflow",
        "operations.create_expense_from_context",
        "operations.create_media_asset",
        "operations.create_solicitud_from_commitment",
        "operations.folder_planner_snapshot",
        "operations.send_tournament_reminder",
        "operations.tournament_soul_snapshot",
        "operations.update_commitment",
        "operations.update_team_status",
        "operations.verify_player_document",
        "receipts.approve_document",
        "receipts.cfdi_matching_overview",
        "receipts.cfdi_workflow_snapshot",
        "receipts.link_expense_to_cfdi",
        "receipts.pending_payment_overview",
        "receipts.register_document_payment",
        "receipts.register_document_reembolso",
        "receipts.reject_document",
        "receipts.request_cfdi",
        "receipts.send_document",
    ]


def test_supported_read_and_write_actions_are_split_correctly() -> None:
    assert supported_read_actions() == [
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
    assert supported_write_actions() == [
        "accounting.assign_expense_accounting",
        "accounting.link_bank_to_expense",
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
        "expenses.create_personal_receipt_workflow",
        "expenses.create_solicitud_personal",
        "expenses.create_solicitud_terceros",
        "expenses.create_third_party_receipt_workflow",
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


def test_confirmation_message_helpers_detect_common_yes_and_no_forms() -> None:
    assert _is_explicit_approval_message("Sí, confirma")
    assert _is_explicit_approval_message("adelante")
    assert _is_explicit_approval_message("/ok")
    assert _is_explicit_rejection_message("cancela")
    assert _is_explicit_rejection_message("/cancel")
    assert not _is_explicit_rejection_message("adelante")


def test_export_prompt_is_not_appended_to_provider_timeout() -> None:
    message = (
        "El proveedor del asistente tardó demasiado en responder. "
        "No ejecuté acciones ni cambios; intenta de nuevo con una consulta más corta."
    )
    stale_exportable_trace = [
        {
            "tool": "finance.realtime_report",
            "result": {
                "totals": {"monto": 100},
                "breakdown": {"items": [{"concepto": "Hospedaje"}]},
            },
        }
    ]

    assert _maybe_append_export_prompt(message, stale_exportable_trace) == message


def test_export_prompt_is_appended_to_successful_report() -> None:
    message = "Comparativo listo."
    trace = [
        {
            "tool": "finance.realtime_report",
            "result": {
                "totals": {"monto": 100},
                "breakdown": {"items": [{"concepto": "Hospedaje"}]},
            },
        }
    ]

    result = _maybe_append_export_prompt(message, trace)

    assert result.startswith(message)
    assert "¿Quieres que te lo exporte ahora?" in result


def test_build_expense_canonical_pending_prefers_operations_action_when_phase_exists():
    conversation = _ConversationStub(
        metadata_={
            "module_context": {
                "torneo": "Liga Telmex Telcel",
                "fase_torneo": "Nacional",
                "concepto": "Hospedaje",
                "departamento": "Operaciones",
                "payment_method": "transferencia",
                "expense_type": "manual",
                "amount": 12500,
                "expense_date": "2026-04-17",
                "requires_cfdi": False,
            }
        }
    )

    pending = _build_expense_canonical_pending(
        raw_message="Registra un gasto de hospedaje",
        conversation=conversation,
        empleado_id=uuid.UUID("b8816679-ad77-4590-83d5-50ffce335854"),
    )

    assert pending is not None
    tool_name, tool_args, assistant_message = pending
    assert tool_name == "assistant_canonical_action"
    assert tool_args["action"] == "operations.create_expense_from_context"
    assert tool_args["payload"]["gasto_cantidad"] == 12500
    assert "Confirma para ejecutar el registro" in assistant_message


def test_build_expense_canonical_pending_returns_none_when_fields_missing():
    conversation = _ConversationStub(
        metadata_={
            "module_context": {
                "concepto": "Hospedaje",
                "torneo": "Liga Telmex Telcel",
            }
        }
    )

    pending = _build_expense_canonical_pending(
        raw_message="Registra un gasto",
        conversation=conversation,
        empleado_id=uuid.UUID("b8816679-ad77-4590-83d5-50ffce335854"),
    )

    assert pending is None


def test_extract_cfdi_use_from_message_detects_sat_code() -> None:
    assert _extract_cfdi_use_from_message("Solicita factura con uso CFDI G03") == "G03"
    assert _extract_cfdi_use_from_message("sin codigo") is None


def test_build_cfdi_canonical_pending_uses_existing_expense_context() -> None:
    conversation = _ConversationStub(
        metadata_={
            "module_context": {
                "expense_id": "375645ec-da26-49ed-bcff-0b0938cac2b6",
                "torneo": "Liga Telmex Telcel",
            }
        }
    )

    pending = _build_cfdi_canonical_pending(
        raw_message=(
            "Solicita factura del gasto "
            "375645ec-da26-49ed-bcff-0b0938cac2b6 con uso CFDI G03."
        ),
        conversation=conversation,
        empleado_id=uuid.UUID("b8816679-ad77-4590-83d5-50ffce335854"),
    )

    assert pending is not None
    tool_name, tool_args, assistant_message = pending
    assert tool_name == "assistant_canonical_action"
    assert tool_args["action"] == "receipts.request_cfdi"
    assert tool_args["payload"]["expense_id"] == "375645ec-da26-49ed-bcff-0b0938cac2b6"
    assert tool_args["payload"]["cfdi_use"] == "G03"
    assert "solicitar el CFDI" in assistant_message


def test_extract_uuid_candidates_finds_all_uuids_in_message() -> None:
    uuids = _extract_uuid_candidates(
        "Liga el UUID C027C9F4-92CF-4190-BB89-3E76AB2ECA70 al gasto "
        "375645ec-da26-49ed-bcff-0b0938cac2b6"
    )

    assert uuids == [
        "C027C9F4-92CF-4190-BB89-3E76AB2ECA70",
        "375645ec-da26-49ed-bcff-0b0938cac2b6",
    ]


def test_build_link_cfdi_canonical_pending_uses_expense_context_and_uuid_from_message():
    conversation = _ConversationStub(
        metadata_={
            "module_context": {
                "expense_id": "375645ec-da26-49ed-bcff-0b0938cac2b6",
                "torneo": "Liga Telmex Telcel",
            }
        }
    )

    pending = _build_link_cfdi_canonical_pending(
        raw_message=(
            "Liga el UUID C027C9F4-92CF-4190-BB89-3E76AB2ECA70 al gasto "
            "375645ec-da26-49ed-bcff-0b0938cac2b6"
        ),
        conversation=conversation,
        empleado_id=uuid.UUID("b8816679-ad77-4590-83d5-50ffce335854"),
    )

    assert pending is not None
    tool_name, tool_args, assistant_message = pending
    assert tool_name == "assistant_canonical_action"
    assert tool_args["action"] == "receipts.link_expense_to_cfdi"
    assert tool_args["payload"]["expense_id"] == "375645ec-da26-49ed-bcff-0b0938cac2b6"
    assert (
        tool_args["payload"]["cfdi_uuid_manual"]
        == "C027C9F4-92CF-4190-BB89-3E76AB2ECA70"
    )
    assert "ligar el CFDI" in assistant_message


def test_build_bank_link_canonical_pending_uses_expense_context_and_movement_uuid() -> (
    None
):
    conversation = _ConversationStub(
        metadata_={
            "module_context": {
                "expense_id": "375645ec-da26-49ed-bcff-0b0938cac2b6",
                "torneo": "Liga Telmex Telcel",
            }
        }
    )

    pending = _build_bank_link_canonical_pending(
        raw_message=(
            "Concilia manualmente el movimiento bancario "
            "969443f5-c984-48b1-9757-467d3a9e9e10 contra el gasto "
            "375645ec-da26-49ed-bcff-0b0938cac2b6"
        ),
        conversation=conversation,
        empleado_id=uuid.UUID("b8816679-ad77-4590-83d5-50ffce335854"),
    )

    assert pending is not None
    tool_name, tool_args, assistant_message = pending
    assert tool_name == "assistant_canonical_action"
    assert tool_args["action"] == "accounting.link_bank_to_expense"
    assert tool_args["payload"]["expense_id"] == "375645ec-da26-49ed-bcff-0b0938cac2b6"
    assert tool_args["payload"]["movement_id"] == "969443f5-c984-48b1-9757-467d3a9e9e10"
    assert tool_args["payload"]["empleado_id"] == "b8816679-ad77-4590-83d5-50ffce335854"
    assert "conciliar el movimiento bancario" in assistant_message


def test_build_accounting_assign_canonical_pending_uses_suggested_or_explicit_account():
    conversation = _ConversationStub(
        metadata_={
            "module_context": {
                "expense_id": "375645ec-da26-49ed-bcff-0b0938cac2b6",
                "cuenta_codigo": "6000-001",
            }
        }
    )

    pending = _build_accounting_assign_canonical_pending(
        raw_message="Asigna cuenta contable a este gasto",
        conversation=conversation,
        empleado_id=uuid.UUID("b8816679-ad77-4590-83d5-50ffce335854"),
    )

    assert pending is not None
    tool_name, tool_args, assistant_message = pending
    assert tool_name == "assistant_canonical_action"
    assert tool_args["action"] == "accounting.assign_expense_accounting"
    assert tool_args["payload"]["expense_id"] == "375645ec-da26-49ed-bcff-0b0938cac2b6"
    assert tool_args["payload"]["cuenta_codigo"] == "6000-001"
    assert "clasificación contable" in assistant_message


def test_build_accounting_post_canonical_pending_uses_expense_context() -> None:
    conversation = _ConversationStub(
        metadata_={
            "module_context": {
                "expense_id": "375645ec-da26-49ed-bcff-0b0938cac2b6",
                "tipo_poliza": "Eg",
            }
        }
    )

    pending = _build_accounting_post_canonical_pending(
        raw_message="Postea la póliza de este gasto",
        conversation=conversation,
        empleado_id=uuid.UUID("b8816679-ad77-4590-83d5-50ffce335854"),
    )

    assert pending is not None
    tool_name, tool_args, assistant_message = pending
    assert tool_name == "assistant_canonical_action"
    assert tool_args["action"] == "accounting.post_expense_accounting"
    assert tool_args["payload"]["expense_id"] == "375645ec-da26-49ed-bcff-0b0938cac2b6"
    assert tool_args["payload"]["tipo_poliza"] == "Eg"
    assert tool_args["payload"]["empleado_id"] == "b8816679-ad77-4590-83d5-50ffce335854"
    assert "posteo contable" in assistant_message
