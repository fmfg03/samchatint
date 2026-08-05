"""Focused route tests for solicitud de transferencia a terceros."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import user_routes
from devnous.gastos.routes.solicitud_transferencia_ui import (
    render_cfdi_quick_expense_autofill_script,
    render_materialidades_file_picker_html,
    render_materialidades_file_picker_script,
)
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


def test_budget_concept_filter_requires_selected_fase() -> None:
    concepts = [
        {"id": "global", "label": "Hospedaje", "applicable_keys": [], "global": True},
        {
            "id": "fase-estatal",
            "label": "Renta de sede",
            "applicable_keys": ["estatal", "fase_estatal"],
            "global": False,
        },
    ]

    assert user_routes._filter_budget_concepts_for_fase(concepts, "") == []
    filtered = user_routes._filter_budget_concepts_for_fase(concepts, "Estatal")
    assert [item["id"] for item in filtered] == ["global", "fase-estatal"]


def test_budget_concept_sync_script_requires_fase_and_hides_account_code() -> None:
    html = user_routes._render_budget_concept_sync_script(
        concept_map={
            "tournament-1": [
                {
                    "id": "concept-1",
                    "label": "Hospedaje",
                    "cuenta_contable_codigo": "6000-001",
                    "applicable_keys": ["estatal"],
                    "global": False,
                }
            ]
        },
        tournament_select_id="torneo_id",
        concept_select_id="budget_concept_id",
        selected_id=None,
        required=True,
        phase_select_id="fase_id",
    )

    assert 'if (phaseSelect && !fase)' in html
    assert 'label += " (" + item.cuenta_contable_codigo + ")"' not in html


@pytest.mark.asyncio
async def test_validate_solicitud_terceros_fase_requires_phase_when_tournament_has_etapas(
    monkeypatch,
) -> None:
    tournament_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(
            id=tournament_id,
            active=True,
            etapas=["Estatal", "Nacional"],
        )
    )
    monkeypatch.setattr(user_routes, "visibility_validation_error", lambda *_args: None)

    error, fase = await user_routes._validate_solicitud_terceros_fase(
        session,
        empleado=SimpleNamespace(id=uuid4()),
        torneo_id_raw=str(tournament_id),
        fase_raw="",
    )

    assert error == "Debe seleccionar una Fase/Subproyecto para el proyecto."
    assert fase is None


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


@pytest.mark.asyncio
async def test_gastos_terceros_includes_provider_search_filter(monkeypatch) -> None:
    doc_id = uuid4()
    doc = SimpleNamespace(
        id=doc_id,
        numero_referencia="S-26000054",
        referencia_operaciones="12",
        proveedor_cliente=SimpleNamespace(nombre="HK DISENO, S.A. DE C.V."),
        empleado=SimpleNamespace(nombre="ALICIA EDITH ZUNIGA SALAZAR"),
        monto_solicitado=8700,
        currency="MXN",
        fecha_pago=None,
        concepto_pago="PRIMER APOYO DE ACUERDO A CONVENIO",
        estado="enviado",
    )
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_args: "")
    monkeypatch.setattr(user_routes, "fetch_documento_adjuntos_meta_batch", AsyncMock(return_value={doc_id: []}))
    monkeypatch.setattr(user_routes, "fetch_documento_aprobador_display_batch", AsyncMock(return_value={doc_id: "JOSE ODILON"}))
    monkeypatch.setattr(user_routes, "empleado_list_view_department_scope", lambda *_args: None)
    monkeypatch.setattr(user_routes, "has_permission", lambda *_args, **_kwargs: False)

    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalars_result([doc]))
    empleado = SimpleNamespace(
        id=uuid4(),
        nombre="Ana Operaciones",
        departamento="Operaciones",
        rol="finanzas",
    )

    html = await user_routes.gastos_terceros(
        request=SimpleNamespace(query_params={}),
        session=session,
        current_empleado=empleado,
    )

    assert 'class="terceros-filter-bar"' in html
    assert 'id="terceros-search-proveedor"' in html
    assert "Por Proveedor" in html
    assert 'data-proveedor="hk diseno, s.a. de c.v."' in html
    assert "normalize('NFD')" in html
    assert ".replace(/[^a-z0-9]+/g, ' ')" in html
    assert "matchProveedor" in html


def test_quick_expense_cfdi_autofill_keeps_xml_total_as_authority():
    script = render_cfdi_quick_expense_autofill_script()

    assert "if (concepto && payload.concepto)" not in script
    assert "CFDI leido. Se precargaron fecha, folio, montos e impuestos" in script
    assert "if (total && payload.total)" in script
    assert "total.value = payload.total" in script
    assert "tipInput" in script
    assert "&& (!subtotal || !subtotal.value)" not in script
    assert script.count("formData.append(sourceInput.name, sourceInput.files[0]);") == 1


def test_comprobante_pago_attachment_is_registered_atomically_with_payment() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("async def agregar_documento_adjuntos")
    end = source.index('@router.post("/documentos/{documento_id}/adjuntos/{adjunto_id}/eliminar")', start)
    handler = source[start:end]

    assert 'if categoria_norm == "comprobante_pago":' in handler
    assert "commit=False" in handler
    assert "await register_document_payment(" in handler
    assert "payment_registered = True" in handler


def test_comprobante_pago_attachment_requires_payment_run_cutoff() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("def _can_finance_add_comprobante_pago")
    end = source.index("def _nueva_solicitud_terceros_form_url", start)
    helper = source[start:end]

    assert 'return documento.estado == "en_proceso_pago"' in helper
    assert '"aprobado", "en_proceso_pago", "pagado"' not in helper


def test_document_detail_does_not_render_manual_mark_paid_button() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("async def ver_documento")
    end = source.index("# =============================================================================\n# CUENTAS DE GASTOS ROUTES", start)
    detail = source[start:end]

    assert "Marcar pagado" not in detail
    assert "/documentos/{documento_id}/registrar-pago" not in detail


def test_manual_register_payment_endpoint_is_blocked() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("async def registrar_pago")
    end = source.index('@router.post("/documentos/{documento_id}/registrar-reembolso")', start)
    endpoint = source[start:end]

    assert "payment_proof_required" in endpoint
    assert "El pago se confirma adjuntando comprobante" in endpoint
    assert "await register_document_payment(" not in endpoint


def test_quick_expense_form_exposes_air_supplement_facturas() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()

    assert 'id="quick-air-supplements"' in source
    assert "Partidas aéreas adicionales" in source
    assert "Asiento preferencial" in source
    assert "Exceso de equipaje" in source
    assert 'name="asiento_preferencial_cfdi_xml"' in source
    assert 'name="asiento_preferencial_cfdi_pdf"' in source
    assert 'name="exceso_equipaje_cfdi_xml"' in source
    assert 'name="exceso_equipaje_cfdi_pdf"' in source
    assert "airTokens" in source
    assert "isAir()" in source


def test_quick_expense_route_creates_air_supplement_expenses() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("async def crear_gasto_rapido_en_informe")
    end = source.index('@router.post("/informes-de-gastos/{cuenta_id}/gastos/amex")', start)
    handler = source[start:end]

    assert "asiento_preferencial_cfdi_xml" in handler
    assert "exceso_equipaje_cfdi_xml" in handler
    assert "async def _create_air_supplement_expense" in handler
    assert "label=\"Asiento preferencial\"" in handler
    assert "label=\"Exceso de equipaje\"" in handler
    assert "requiere cargar su factura PDF o XML" in handler
    assert "supplement_expense.cuenta_gastos_id = cuenta.id" in handler
    assert "supplement_expense.informe_documento_id = informe_doc.id" in handler


def test_materialidades_picker_contract_keeps_files_verifiable_before_submit() -> None:
    html = render_materialidades_file_picker_html()
    script = render_materialidades_file_picker_script()
    combined = html + script

    assert 'id="archivos_generales_picker"' in html
    assert 'name="archivos_generales"' in html
    assert 'multiple' in html
    assert 'hidden' in html
    assert 'id="archivos_generales_list"' in html
    assert 'id="archivos_generales_empty"' in html
    assert 'Seleccione un archivo a la vez' in html
    assert 'Puede agregar varios antes de guardar' in html
    assert 'Toque la miniatura para verla completa' in html
    assert 'image/*' in html
    assert 'application/pdf' in html

    assert 'const selectedFiles = []' in script
    assert 'new DataTransfer()' in script
    assert 'URL.createObjectURL(file)' in script
    assert 'st-materialidades-thumbnail' in script
    assert "previewLink.target = '_blank'" in script
    assert "previewLink.textContent = 'Abrir PDF'" in script
    assert 'st-materialidades-name' in script
    assert 'formatFileSize(file.size)' in script
    assert "removeBtn.textContent = 'Quitar'" in script
    assert 'selectedFiles.splice(index, 1)' in script
    assert 'syncHiddenInput()' in script

    assert 'archivo_pdf_preview_frame' not in combined
    assert 'name="archivo_xml"' not in combined
    assert 'name="archivo_pdf"' not in combined


def test_solicitante_can_add_materialidad_after_financial_closure():
    documento_id = uuid4()
    empleado_id = uuid4()
    documento = SimpleNamespace(
        id=documento_id,
        tipo="SOLICITUD",
        proveedor_cliente_id=uuid4(),
        empleado_id=empleado_id,
        estado="pagado",
    )
    empleado = SimpleNamespace(id=empleado_id, rol="empleado")

    assert user_routes._can_add_solicitud_adjuntos(documento, empleado) is True

    documento.estado = "cerrado"
    assert user_routes._can_add_solicitud_adjuntos(documento, empleado) is True

    documento.estado = "reembolsado"
    assert user_routes._can_add_solicitud_adjuntos(documento, empleado) is True

    documento.estado = "aplicado"
    assert user_routes._can_add_solicitud_adjuntos(documento, empleado) is True


def test_workflow_closure_does_not_reopen_solicitud_editing():
    documento = SimpleNamespace(
        tipo="SOLICITUD",
        proveedor_cliente_id=uuid4(),
        empleado_id=uuid4(),
        estado="pagado",
    )
    empleado = SimpleNamespace(id=documento.empleado_id, rol="empleado")

    assert user_routes._can_edit_solicitud_terceros(documento, empleado) is False


def test_workflow_blocks_reopen_after_any_approval_but_allows_materiality():
    workflow_source = Path("src/devnous/gastos/services/documento_workflow_service.py").read_text(encoding="utf-8")
    routes_source = Path("src/devnous/gastos/routes/user_routes.py").read_text(encoding="utf-8")

    assert "authorization_closed_document" in workflow_source
    assert 'normalized_action in {"send", "reject", "cancel", "withdraw"}' in workflow_source
    assert "documento_has_approval" in workflow_source
    assert "documento_workflow_locked_reason" in routes_source
    assert '"cerrado",' in routes_source
    assert '"reembolsado",' in routes_source
    assert '"aplicado",' in routes_source
