"""Contracts for the superadmin duplicate-CFDI release console."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SERVICE = (ROOT / "src/devnous/gastos/services/cfdi_duplicate_release_service.py").read_text()
DOCUMENTO_SERVICE = (ROOT / "src/devnous/gastos/services/documento_service.py").read_text()
ROUTES = (ROOT / "src/devnous/gastos/routes/user_routes.py").read_text()
MODELS = (ROOT / "src/devnous/gastos/models.py").read_text()
SCHEMA = (ROOT / "src/devnous/gastos/schema_guard.py").read_text()
MIGRATION = (ROOT / "database/migrations/20260918_cfdi_duplicate_release_console.sql").read_text()


def test_console_is_superadmin_only_and_requires_confirmation() -> None:
    assert "_require_cfdi_duplicate_release_superadmin" in ROUTES
    assert "Solo superadmin puede cancelar y liberar comprobantes duplicados" in ROUTES
    assert "Cancelar y liberar comprobantes" in ROUTES
    assert 'name="confirmar"' in ROUTES
    assert 'confirmar != "si"' in ROUTES


def test_preview_is_read_only_and_apply_is_idempotent() -> None:
    assert "/vista-previa" in ROUTES
    assert "/aplicar" in ROUTES
    assert "/recibos/{operation_id}" in ROUTES
    assert "idempotency_key" in ROUTES
    assert "preview_duplicate_releases" in SERVICE
    assert "selection_hash" in SERVICE
    assert "ux_cfdi_duplicate_release_operation_key" in MIGRATION


def test_console_uses_the_admin_shell_and_explains_each_step() -> None:
    assert "_workspace_shell_styles(\"1180px\")" in ROUTES
    assert 'render_top_navigation(current_empleado, "admin")' in ROUTES
    assert "Paso 1 de 2" in ROUTES
    assert "Paso 2 de 2" in ROUTES
    assert "Esta consulta no realiza cambios." in ROUTES
    assert "status-badge" in ROUTES
    assert "confirmation-panel" in ROUTES
    assert "recibo auditable" in ROUTES


def test_release_preserves_fiscal_evidence_and_blocks_payment_states() -> None:
    assert "PROTECTED_DOCUMENT_STATES" in SERVICE
    assert '"en_proceso_pago"' in SERVICE
    assert '"pagado"' in SERVICE
    assert "expense.informe_documento_id" in SERVICE
    assert 'expense.coi_estado' in SERVICE
    assert 'cfdi-reservation:{preview.cfdi_report_id}' in SERVICE
    assert "block_reason = _release_block_reason(documents, expenses)" in SERVICE
    assert "document.cfdi_report_id = None" in SERVICE
    assert "expense.cfdi_report_id = None" in SERVICE
    assert "CFDIDuplicateReleaseOperationItem" in MODELS
    assert "liberar_cfdi_duplicado" in SCHEMA
    assert "liberar_cfdi_duplicado" in MIGRATION
    assert "este control evita duplicar comprobantes" in DOCUMENTO_SERVICE
