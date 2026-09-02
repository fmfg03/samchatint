from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4


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
    assert "documento.cfdi_report_id = None" in cancel_block
    assert "documento.cfdi_uuid_manual = None" in cancel_block
    assert "documento.cfdi_compartido_confirmado = False" in cancel_block
    assert 'aprobacion_accion = "cancelar"' in cancel_block
    assert 'documento.estado = "rechazado"' not in cancel_block


def test_cancelled_solicitudes_do_not_block_cfdi_reuse():
    source = (
        ROOT
        / "src"
        / "devnous"
        / "gastos"
        / "services"
        / "cfdi_ingestion_service.py"
    ).read_text()
    usage_block = source.split("async def has_existing_cfdi_usage(", 1)[1].split(
        "async def _ingest_cfdi_parsed(",
        1,
    )[0]

    assert "Documento.cfdi_report_id == report_id" in usage_block
    assert 'Documento.estado != "cancelado"' in usage_block
    assert "Documento.estado.is_(None)" in usage_block


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
    assert 'Cancelar solicitud' in helper


def test_legacy_solicitud_edit_aliases_redirect_to_canonical_route():
    from devnous.gastos.routes import user_routes

    source = (
        ROOT / "src" / "devnous" / "gastos" / "routes" / "user_routes.py"
    ).read_text()
    alias_block = source.split(
        "def _legacy_solicitud_edit_redirect_url(", 1
    )[1].split(
        '@router.get("/documentos/{documento_id}/editar"',
        1,
    )[0]
    documento_id = uuid4()
    request = SimpleNamespace(
        query_params={"next": "/gastos-terceros?estado=rechazado"}
    )

    redirect_url = user_routes._legacy_solicitud_edit_redirect_url(
        documento_id,
        request,
    )
    parsed = urlparse(redirect_url)

    assert '@router.get("/solicitudes/{documento_id}/editar")' in alias_block
    assert '@router.get("/gastos-terceros/{documento_id}/editar")' in alias_block
    assert "get_db_session" not in alias_block
    assert "get_current_empleado" not in alias_block
    assert parsed.path == f"/documentos/{documento_id}/editar"
    assert parse_qs(parsed.query) == {
        "next": ["/gastos-terceros?estado=rechazado"]
    }


def test_rejected_solicitud_owner_can_edit_even_with_budget_concept():
    from devnous.gastos.routes import user_routes

    owner_id = uuid4()
    documento = SimpleNamespace(
        tipo="SOLICITUD",
        estado="rechazado",
        empleado_id=owner_id,
        proveedor_cliente_id=uuid4(),
        cuenta_gastos_id=None,
        beneficiario_empleado_id=None,
        concepto_pago="Servicios rechazados",
        budget_concept_id=uuid4(),
    )
    actor = SimpleNamespace(id=owner_id, rol="empleado")

    assert user_routes._can_edit_solicitud_terceros(documento, actor) is True


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
    assert 'documento.budget_concept_id = None' in source
