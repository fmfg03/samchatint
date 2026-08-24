from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.services.documento_workflow_service import documento_requires_budget_control


def test_solicitud_without_budget_concept_requires_budget_control():
    documento = SimpleNamespace(tipo="SOLICITUD", budget_concept_id=None)
    assert documento_requires_budget_control(documento) is True


def test_informe_without_budget_concept_requires_budget_control():
    documento = SimpleNamespace(tipo="INFORME", budget_concept_id=None)
    assert documento_requires_budget_control(documento) is True


def test_document_with_budget_concept_goes_to_regular_approval():
    documento = SimpleNamespace(tipo="SOLICITUD", budget_concept_id=uuid4())
    assert documento_requires_budget_control(documento) is False


def test_budget_control_send_schedules_control_presupuestal_telegram():
    source = Path(
        "src/devnous/gastos/services/documento_workflow_service.py"
    ).read_text()
    start = source.index("async def transition_documento_workflow")
    end = source.index("    return DocumentoWorkflowResult", start)
    block = source[start:end]

    assert "schedule_budget_control_telegram_notifications" in block
    assert "documento.estado == BUDGET_CONTROL_STATE" in block
    assert "schedule_document_workflow_telegram_notifications" in block


def test_informe_close_schedules_budget_control_telegram_when_gated():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("doc_synced = await _sync_informe_documento_to_enviado")
    end = source.index("    if reembolso_created:", start)
    block = source[start:end]

    assert "schedule_budget_control_telegram_notifications" in block
    assert 'informe_doc.estado == "control_presupuestal"' in block
    assert "schedule_document_workflow_telegram_notifications" in block


def test_budget_control_telegram_has_idempotent_outbox_type():
    telegram_source = Path(
        "src/devnous/gastos/services/documento_telegram.py"
    ).read_text()
    outbox_source = Path(
        "src/devnous/gastos/services/telegram_outbox_service.py"
    ).read_text()

    assert 'BUDGET_CONTROL_NOTIFICATION_TYPE = "budget_control_pending"' in telegram_source
    assert 'notification_type=BUDGET_CONTROL_NOTIFICATION_TYPE' in telegram_source
    assert '"budget_control_pending"' in outbox_source
    assert "Control Presupuestal pendiente" in outbox_source
    assert "resolve_budget_control_notification_recipients" in telegram_source


def test_approved_document_cannot_be_reapproved_or_rejected_by_previous_approver():
    source = Path(
        "src/devnous/gastos/services/documento_workflow_service.py"
    ).read_text()
    assert "async def _document_has_recorded_approval" in source
    assert "def _raise_if_document_already_advanced" in source
    assert "documento_already_advanced" in source

    approve_start = source.index('elif normalized_action == "approve":')
    reject_start = source.index('elif normalized_action == "reject":')
    withdraw_start = source.index('elif normalized_action == "withdraw":')
    approve_block = source[approve_start:reject_start]
    reject_block = source[reject_start:withdraw_start]

    assert "_raise_if_document_already_advanced" in approve_block
    assert "_document_has_recorded_approval(session, documento_uuid)" in approve_block
    assert "_raise_if_document_already_advanced" in reject_block
    assert "_document_has_recorded_approval(session, documento_uuid)" in reject_block



def test_budget_control_page_has_searchable_bulk_assignment_controls():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("async def documentos_control_presupuestal")
    end = source.index("async def _apply_control_presupuestal_assignment", start)
    block = source[start:end]

    assert "budget-concept-filter" in block
    assert "data-target" in block
    assert "documento_ids" in block
    assert "/documentos/control-presupuestal/asignar-lote" in block
    assert "data-select-all-budget" in block
    assert "Asignar seleccionados" in block


def test_pending_approval_page_has_bulk_selection_controls():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("async def documentos_pendientes")
    end = source.index("@router.post(\"/documentos/pendientes/accion-lote\")", start)
    block = source[start:end]

    assert "documento_ids" in block
    assert "data-select-all-approval" in block
    assert "Aprobar seleccionados" in block
    assert "Rechazar seleccionados" in block
    assert "formaction=\"/documentos/{documento.id}/aprobar\"" in block
    assert "formaction=\"/documentos/{documento.id}/rechazar\"" in block


def test_bulk_pending_approval_endpoint_uses_canonical_workflow_gate():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("async def documentos_pendientes_accion_lote")
    end = source.index("@router.get(\"/documentos/historial-aprobador\"", start)
    block = source[start:end]

    assert "transition_documento_workflow" in block
    assert "workflow_action" in block
    assert 'workflow_action = {"approve": "approve", "reject": "reject"}' in block
    assert "DocumentoWorkflowPermissionError" in block
