"""Regression contracts for delayed CFDI reservation and form replays."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INGESTION = (ROOT / "src/devnous/gastos/services/cfdi_ingestion_service.py").read_text()
WORKFLOW = (ROOT / "src/devnous/gastos/services/documento_workflow_service.py").read_text()
SERVICE = (ROOT / "src/devnous/gastos/services/documento_service.py").read_text()
ROUTES = (ROOT / "src/devnous/gastos/routes/user_routes.py").read_text()
MODELS = (ROOT / "src/devnous/gastos/models.py").read_text()
SCHEMA = (ROOT / "src/devnous/gastos/schema_guard.py").read_text()


def test_cfdi_is_reserved_at_budget_control_with_conflict_context() -> None:
    assert '"control_presupuestal"' in INGESTION
    assert "class CFDIUsageConflict" in INGESTION
    assert "created by" not in INGESTION  # Spanish, user-facing copy stays localized.
    assert "creada por" in INGESTION
    assert "async def reserve_documento_cfdis_or_raise" in WORKFLOW
    assert "pg_advisory_xact_lock" in WORKFLOW
    assert "await reserve_documento_cfdis_or_raise(session, documento, actor)" in WORKFLOW


def test_expense_cfdi_reservation_follows_its_informe() -> None:
    reservation = WORKFLOW.split("async def reserve_documento_cfdis_or_raise", 1)[1].split(
        "def documento_requires_budget_control", 1
    )[0]
    assert "ExpenseReport.informe_documento_id == documento.id" in reservation
    assert 'ExpenseReport.estado_gasto != "cancelado"' in reservation
    assert "ExpenseReport.cfdi_compartido_confirmado" in reservation
    assert "if not cfdi_compartido_confirmado" in reservation
    assert "not documento.cfdi_compartido_confirmado" in reservation
    assert "Documento.cfdi_compartido_confirmado.is_(False)" in INGESTION
    assert "ExpenseReport.cfdi_compartido_confirmado.is_(False)" in INGESTION


def test_cuenta_close_uses_the_same_cfdi_reservation_policy() -> None:
    close_helper = ROUTES.split("async def _sync_informe_documento_to_enviado", 1)[1].split(
        '@router.get("/api/informes-de-gastos/activas"', 1
    )[0]
    assert "await reserve_documento_cfdis_or_raise(session, informe_doc, actor)" in close_helper


def test_solicitud_form_submission_is_idempotent_server_and_client_side() -> None:
    assert "client_submission_id = Column" in MODELS
    assert "documentos_client_submission_id_column" in SCHEMA
    assert "ux_documentos_empleado_client_submission" in SCHEMA
    assert "pg_advisory_xact_lock(hashtext(:lock_key))" in SERVICE
    assert "_idempotent_replay" in SERVICE
    assert 'name="client_submission_id"' in ROUTES
    assert "button.disabled = true" in ROUTES
    assert 'if not getattr(documento, "_idempotent_replay", False):' in ROUTES
    assert "idempotent_replay and exc.code == \"invalid_estado\"" in ROUTES
    assert "with_for_update()" in WORKFLOW
