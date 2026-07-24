"""Focused route tests for solicitud de transferencia a terceros."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import user_routes
from devnous.gastos.services.documento_service import SolicitudValidationError


def _scalars_result(items):
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _upload(filename: str, content: bytes, content_type: str):
    return SimpleNamespace(
        filename=filename,
        content_type=content_type,
        read=AsyncMock(return_value=content),
    )


def _documento_stub(documento_id):
    return SimpleNamespace(
        id=documento_id,
        empleado_id=uuid4(),
        numero_referencia="S-TEST",
        tipo="SOLICITUD",
        torneo_id=None,
        proveedor_cliente_id=None,
    )


@pytest.fixture(autouse=True)
def _disable_customer_success_audit(monkeypatch):
    monkeypatch.setattr(
        user_routes,
        "record_customer_success_audit_event",
        AsyncMock(),
    )


@pytest.mark.asyncio
async def test_terceros_form_shows_xml_and_materialidades_without_manual_cfdi_fields(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        user_routes,
        "fetch_active_tournaments_for_empleado",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_args: "")
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalars_result([]))
    empleado = SimpleNamespace(
        id=uuid4(),
        nombre="Ana Operaciones",
        departamento="Operaciones",
    )

    html = await user_routes.nueva_solicitud_terceros_form(
        request=SimpleNamespace(query_params={}),
        session=session,
        current_empleado=empleado,
    )

    assert "CFDI XML:" in html
    assert 'name="archivo_xml"' in html
    assert "Vista previa del PDF" in html
    assert 'id="archivo_pdf_preview"' in html
    assert 'id="archivo_pdf_preview_frame"' in html
    assert "MATERIALIDADES:" in html
    assert 'id="archivos_generales_picker"' in html
    assert "Quitar" in html
    assert "Seleccione un archivo a la vez" in html
    assert "OTROS DOCUMENTOS:" not in html
    assert "UUID CFDI:" not in html
    assert "ENLACE/QR CFDI:" not in html
    assert 'name="cfdi_uuid_manual"' not in html
    assert 'name="cfdi_qr_or_url"' not in html
    assert "Fase/Subproyecto (opcional)" in html
    assert 'name="fase"' in html
    assert 'id="fase_terceros"' in html
    assert 'name="categorias"' in html
    assert 'multiple size="4"' in html
    assert 'name="edicion"' in html
    assert 'name="currency"' in html
    assert 'id="cfdi_autofill_notice"' in html
    assert "/api/documentos/cfdi-autofill" in html
    assert "Crear solicitud y enviar para aprobación" in html
    assert 'name="submit_mode" value="create_and_send"' in html
    assert html.index("Documentación de soporte") < html.index("BENEFICIARIO:")
    assert 'name="fecha_pago"' not in html
    assert "st-fecha-pago-locked" in html
    assert "Si se aprueba hoy" in html
    assert ".replace(/[^a-z0-9]+/g, ' ')" in html
    assert "visibleOptions.length === 1" in html
    assert "uniqueOption.selected = true" in html
    assert "new Event('change', { bubbles: true })" in html


@pytest.mark.asyncio
async def test_terceros_edit_form_does_not_show_create_and_send_button(
    monkeypatch,
) -> None:
    documento = SimpleNamespace(
        id=uuid4(),
        torneo_id=None,
        proyecto_otro="Proyecto externo",
        fase="",
        currency="MXN",
        edicion=2026,
        categorias=[],
        proveedor_cliente_id=uuid4(),
        proveedor_cliente=SimpleNamespace(rfc="AAA010101AAA"),
        monto_solicitado=100.0,
        concepto_pago="Pago",
        numero_factura="",
        referencia_pago="",
        notas="",
        fecha_pago=None,
        budget_concept_id=None,
        fecha_inicio=None,
        fecha_fin=None,
        estado="borrador",
        empleado_id=uuid4(),
    )
    monkeypatch.setattr(
        user_routes,
        "fetch_active_tournaments_for_empleado",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_args: "")
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalars_result([]))
    empleado = SimpleNamespace(
        id=documento.empleado_id,
        nombre="Ana Operaciones",
        departamento="Operaciones",
    )

    html = await user_routes._render_solicitud_terceros_form(
        request=SimpleNamespace(query_params={}),
        session=session,
        current_empleado=empleado,
        edit_documento=documento,
    )

    assert "Crear solicitud y enviar para aprobación" not in html


@pytest.mark.asyncio
async def test_terceros_form_repopulates_fase_options_from_tournament_etapas(
    monkeypatch,
) -> None:
    torneo_id = uuid4()
    torneo = SimpleNamespace(
        id=torneo_id,
        name="Copa Demo",
        etapas=["Fase 1", "Fase 2"],
        categorias=["Operaciones", "Marketing"],
    )
    monkeypatch.setattr(
        user_routes,
        "fetch_active_tournaments_for_empleado",
        AsyncMock(return_value=[torneo]),
    )
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_args: "")
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalars_result([]))
    empleado = SimpleNamespace(
        id=uuid4(),
        nombre="Ana Operaciones",
        departamento="Operaciones",
    )

    html = await user_routes.nueva_solicitud_terceros_form(
        request=SimpleNamespace(
            query_params={"torneo_id": str(torneo_id), "fase": "Fase 2"}
        ),
        session=session,
        current_empleado=empleado,
    )

    assert "Fase 1" in html
    assert 'value="Fase 2" selected' in html
    assert "tournamentEtapasTerceros" in html
    assert 'value="Operaciones" selected' in html
    assert 'value="Marketing" selected' in html


@pytest.mark.asyncio
async def test_terceros_submit_passes_validated_fase(monkeypatch) -> None:
    documento_id = uuid4()
    captured = {}

    def fake_build_payload(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(
        user_routes, "build_solicitud_terceros_payload", fake_build_payload
    )
    monkeypatch.setattr(
        user_routes,
        "create_solicitud_terceros_document",
        AsyncMock(return_value=_documento_stub(documento_id)),
    )
    monkeypatch.setattr(
        user_routes,
        "_validate_solicitud_terceros_fase",
        AsyncMock(return_value=(None, "Fase 1")),
    )

    torneo_id = str(uuid4())
    response = await user_routes.crear_nueva_solicitud_terceros(
        request=SimpleNamespace(),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(id=uuid4(), nombre="Solicitante"),
        archivo_pdf=None,
        archivo_xml=None,
        archivos_generales=[],
        monto_solicitado="100",
        proveedor_cliente_id=str(uuid4()),
        torneo_id=torneo_id,
        proyecto_otro=None,
        fase="Fase 1",
        concepto_pago="Pago a tercero",
        numero_factura=None,
        referencia_pago=None,
        fecha_inicio=None,
        fecha_fin=None,
        notas=None,
        submit_mode="create",
    )

    assert response.status_code == 303
    assert captured["fase"] == "Fase 1"


@pytest.mark.asyncio
async def test_terceros_submit_passes_xml_as_cfdi_xml_attachment(monkeypatch) -> None:
    documento_id = uuid4()
    captured = {}

    def fake_build_payload(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(
        user_routes, "build_solicitud_terceros_payload", fake_build_payload
    )
    monkeypatch.setattr(
        user_routes,
        "create_solicitud_terceros_document",
        AsyncMock(return_value=_documento_stub(documento_id)),
    )

    response = await user_routes.crear_nueva_solicitud_terceros(
        request=SimpleNamespace(),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(id=uuid4(), nombre="Solicitante"),
        archivo_pdf=None,
        archivo_xml=_upload(
            "factura.xml", b"<?xml version='1.0'?><root />", "application/xml"
        ),
        archivos_generales=[],
        monto_solicitado="100",
        proveedor_cliente_id=str(uuid4()),
        torneo_id=str(uuid4()),
        proyecto_otro=None,
        fase=None,
        concepto_pago="Pago a tercero",
        numero_factura=None,
        referencia_pago=None,
        fecha_inicio=None,
        fecha_fin=None,
        notas=None,
        submit_mode="create",
    )

    assert response.status_code == 303
    assert len(captured["attachments"]) == 1
    attachment = captured["attachments"][0]
    assert attachment.filename == "factura.xml"
    assert attachment.mime_type == "application/xml"
    assert attachment.categoria == "cfdi_xml"
    assert "cfdi_uuid_manual" not in captured


@pytest.mark.asyncio
async def test_terceros_submit_rolls_back_when_cfdi_ingestion_is_rejected(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        user_routes,
        "build_solicitud_terceros_payload",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        user_routes,
        "create_solicitud_terceros_document",
        AsyncMock(
            side_effect=SolicitudValidationError(
                "invalid_cfdi_xml",
                "El archivo XML no es un CFDI válido o no contiene UUID",
            )
        ),
    )
    monkeypatch.setattr(
        user_routes,
        "_validate_solicitud_terceros_fase",
        AsyncMock(return_value=(None, None)),
    )
    session = AsyncMock()

    response = await user_routes.crear_nueva_solicitud_terceros(
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        archivo_pdf=None,
        archivo_xml=None,
        archivos_generales=[],
        monto_solicitado="100",
        proveedor_cliente_id=str(uuid4()),
        torneo_id=str(uuid4()),
        proyecto_otro=None,
        fase=None,
        categorias=[],
        edicion=None,
        currency="MXN",
        concepto_pago="Pago a tercero",
        budget_concept_id=None,
        numero_factura=None,
        referencia_pago=None,
        fecha_inicio=None,
        fecha_fin=None,
        notas=None,
        submit_mode="create",
    )

    assert response.status_code == 303
    assert "error=invalid_cfdi_xml" in response.headers["location"]
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_terceros_submit_rolls_back_on_unexpected_create_error(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        user_routes,
        "build_solicitud_terceros_payload",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        user_routes,
        "create_solicitud_terceros_document",
        AsyncMock(side_effect=RuntimeError("db exploded")),
    )
    monkeypatch.setattr(
        user_routes,
        "_validate_solicitud_terceros_fase",
        AsyncMock(return_value=(None, "Fase 9")),
    )
    session = AsyncMock()

    response = await user_routes.crear_nueva_solicitud_terceros(
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        archivo_pdf=None,
        archivo_xml=None,
        archivos_generales=[],
        monto_solicitado="100",
        proveedor_cliente_id=str(uuid4()),
        torneo_id=str(uuid4()),
        proyecto_otro=None,
        fase="Fase 9",
        categorias=[],
        edicion=None,
        currency="MXN",
        concepto_pago="Pago a tercero",
        budget_concept_id=str(uuid4()),
        numero_factura=None,
        referencia_pago=None,
        fecha_inicio=None,
        fecha_fin=None,
        notas=None,
        submit_mode="create",
    )

    assert response.status_code == 303
    assert "error=unexpected_create_error" in response.headers["location"]
    assert "torneo_id=" in response.headers["location"]
    assert "fase=Fase%209" in response.headers["location"]
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_terceros_create_and_send_submits_for_approval(monkeypatch) -> None:
    documento_id = uuid4()
    empleado_id = uuid4()
    monkeypatch.setattr(
        user_routes,
        "build_solicitud_terceros_payload",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        user_routes,
        "create_solicitud_terceros_document",
        AsyncMock(return_value=_documento_stub(documento_id)),
    )
    transition = AsyncMock()
    monkeypatch.setattr(user_routes, "transition_documento_workflow", transition)

    response = await user_routes.crear_nueva_solicitud_terceros(
        request=SimpleNamespace(),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(id=empleado_id, nombre="Solicitante"),
        archivo_pdf=None,
        archivo_xml=None,
        archivos_generales=[],
        monto_solicitado="100",
        proveedor_cliente_id=str(uuid4()),
        torneo_id=str(uuid4()),
        proyecto_otro=None,
        fase=None,
        concepto_pago="Pago a tercero",
        numero_factura=None,
        referencia_pago=None,
        fecha_inicio=None,
        fecha_fin=None,
        notas=None,
        submit_mode="create_and_send",
    )

    assert response.status_code == 303
    assert f"/documentos/{documento_id}" in response.headers["location"]
    assert "success_msg=" in response.headers["location"]
    transition.assert_awaited_once()
    assert transition.await_args.kwargs["documento_id"] == documento_id
    assert transition.await_args.kwargs["actor_id"] == empleado_id
    assert transition.await_args.kwargs["action"] == "send"
    assert "request_context" in transition.await_args.kwargs


@pytest.mark.asyncio
async def test_terceros_create_and_send_hides_unexpected_transition_error(
    monkeypatch,
) -> None:
    documento_id = uuid4()
    empleado_id = uuid4()
    logged = []
    monkeypatch.setattr(
        user_routes.logger,
        "exception",
        lambda *args, **kwargs: logged.append((args, kwargs)),
    )
    monkeypatch.setattr(
        user_routes,
        "build_solicitud_terceros_payload",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        user_routes,
        "create_solicitud_terceros_document",
        AsyncMock(return_value=_documento_stub(documento_id)),
    )
    monkeypatch.setattr(
        user_routes,
        "transition_documento_workflow",
        AsyncMock(side_effect=RuntimeError("workflow secret")),
    )
    session = AsyncMock()

    response = await user_routes.crear_nueva_solicitud_terceros(
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=empleado_id, nombre="Solicitante"),
        archivo_pdf=None,
        archivo_xml=None,
        archivos_generales=[],
        monto_solicitado="100",
        proveedor_cliente_id=str(uuid4()),
        torneo_id=str(uuid4()),
        proyecto_otro=None,
        fase=None,
        categorias=[],
        edicion=None,
        currency="MXN",
        concepto_pago="Pago a tercero",
        budget_concept_id=None,
        numero_factura=None,
        referencia_pago=None,
        fecha_inicio=None,
        fecha_fin=None,
        notas=None,
        submit_mode="create_and_send",
    )

    assert response.status_code == 303
    assert "workflow%20secret" not in response.headers["location"]
    assert "error=unexpected_solicitud_send" in response.headers["location"]
    session.rollback.assert_awaited_once()
    assert logged


@pytest.mark.asyncio
async def test_terceros_submit_hides_unexpected_upload_read_error(monkeypatch) -> None:
    logged = []
    monkeypatch.setattr(
        user_routes.logger,
        "exception",
        lambda *args, **kwargs: logged.append((args, kwargs)),
    )
    session = AsyncMock()

    response = await user_routes.crear_nueva_solicitud_terceros(
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        archivo_pdf=_upload("solicitud.pdf", b"", "application/pdf"),
        archivo_xml=SimpleNamespace(
            filename="factura.xml",
            content_type="application/xml",
            read=AsyncMock(side_effect=RuntimeError("upload secret")),
        ),
        archivos_generales=[],
        monto_solicitado="100",
        proveedor_cliente_id=str(uuid4()),
        torneo_id=str(uuid4()),
        proyecto_otro=None,
        fase="Fase 1",
        categorias=[],
        edicion=None,
        currency="MXN",
        concepto_pago="Pago a tercero",
        budget_concept_id=None,
        numero_factura=None,
        referencia_pago=None,
        fecha_inicio=None,
        fecha_fin=None,
        notas=None,
        submit_mode="create",
    )

    assert response.status_code == 303
    assert "upload%20secret" not in response.headers["location"]
    assert "error=unexpected_create_error" in response.headers["location"]
    session.rollback.assert_awaited_once()
    assert logged
