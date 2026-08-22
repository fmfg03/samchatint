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
