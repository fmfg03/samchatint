from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_cancel_workflow_uses_cancelado_not_rechazado():
    source = (
        ROOT
        / "src"
        / "devnous"
        / "gastos"
        / "services"
        / "documento_workflow_service.py"
    ).read_text()

    cancel_block = source.split('"Solo las solicitudes pueden cancelarse desde este flujo."', 1)[1].split(
        "aprobacion = Aprobacion", 1
    )[0]

    assert 'documento.estado = "cancelado"' in cancel_block
    assert 'aprobacion_accion = "cancelar"' in cancel_block
    assert 'documento.estado = "rechazado"' not in cancel_block


def test_solicitud_list_exposes_edit_for_rejected_owner():
    source = (
        ROOT / "src" / "devnous" / "gastos" / "routes" / "user_routes.py"
    ).read_text()

    helper = source.split('def _solicitud_transferencia_list_actions_html(', 1)[1].split(
        '@router.get("/gastos-terceros"', 1
    )[0]

    assert 'can_edit_rejected' in helper
    assert 'getattr(documento, "estado", None) == "rechazado"' in helper
    assert '/documentos/{documento.id}/editar' in helper
    assert 'Cancelar borrador' in helper


def test_update_rejected_solicitud_returns_to_draft_after_edit():
    source = (
        ROOT
        / "src"
        / "devnous"
        / "gastos"
        / "services"
        / "documento_service.py"
    ).read_text()

    assert 'if documento.estado == "rechazado":' in source
    assert 'documento.estado = "borrador"' in source
    assert 'documento.enviado_en = None' in source
