from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.routes import user_routes
from devnous.gastos.services import documento_service
from devnous.gastos.services.documento_semantics import (
    EMPLOYEE_REIMBURSEMENT_CONCEPT_PREFIX,
)


def _doc(**overrides):
    values = {
        "id": uuid4(),
        "tipo": "SOLICITUD",
        "numero_referencia": "S-26000123",
        "estado": "aprobado",
        "empleado": SimpleNamespace(nombre="Odilon Reportero"),
        "beneficiario_empleado": None,
        "beneficiario_empleado_id": None,
        "proveedor_cliente": None,
        "proveedor_cliente_id": None,
        "concepto_pago": "Hospedaje regional",
        "referencia_pago": "RP-001",
        "referencia_operaciones": "456",
        "monto_solicitado": Decimal("1000.00"),
        "monto_total": Decimal("1160.00"),
        "currency": "MXN",
        "creado_en": datetime(2026, 7, 31, 12, 0, 0),
        "enviado_en": None,
        "aprobado_en": None,
        "pagado_en": None,
        "cuenta_gastos_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_documentos_todos_reporting_values_for_provider_solicitud():
    documento = _doc(
        proveedor_cliente_id=uuid4(),
        proveedor_cliente=SimpleNamespace(nombre="Federal Express"),
    )

    row = user_routes._documentos_todos_reporting_row_values(
        documento, aprobador_nombre="Finanzas"
    )

    assert row["tipo_documento"] == "SOLICITUD"
    assert row["tipo_solicitud"] == "Terceros"
    assert row["solicitante"] == "Odilon Reportero"
    assert row["beneficiario"] == "Federal Express"
    assert row["proveedor"] == "Federal Express"
    assert row["concepto"] == "Hospedaje regional"
    assert row["referencia_pago"] == "RP-001"
    assert row["referencia_operaciones"] == "456"
    assert row["monto_solicitado"] == "$1,000.00"
    assert row["monto_total"] == "$1,160.00"
    assert row["currency"] == "MXN"
    assert row["situacion"] == "Abierta"
    assert row["aprobador"] == "Finanzas"


def test_documentos_todos_reporting_values_for_employee_beneficiary():
    documento = _doc(
        beneficiario_empleado_id=uuid4(),
        beneficiario_empleado=SimpleNamespace(nombre="Alicia Beneficiaria"),
        proveedor_cliente_id=uuid4(),
        proveedor_cliente=SimpleNamespace(nombre="Cuenta bancaria Alicia"),
    )

    row = user_routes._documentos_todos_reporting_row_values(documento)

    assert row["tipo_solicitud"] == "Personal / empleado"
    assert row["beneficiario"] == "Alicia Beneficiaria"
    assert row["proveedor"] == "Cuenta bancaria Alicia"


def test_documentos_todos_reporting_values_for_employee_reimbursement():
    documento = _doc(
        cuenta_gastos_id=uuid4(),
        beneficiario_empleado_id=uuid4(),
        beneficiario_empleado=SimpleNamespace(nombre="Alicia Beneficiaria"),
        concepto_pago=f"{EMPLOYEE_REIMBURSEMENT_CONCEPT_PREFIX} I-26000001",
    )

    assert user_routes._documentos_todos_reporting_type(documento) == (
        "Reembolso empleado"
    )


def test_documentos_todos_reporting_values_for_informe():
    documento = _doc(tipo="INFORME", numero_referencia="I-26000001")

    row = user_routes._documentos_todos_reporting_row_values(documento)

    assert row["tipo_solicitud"] == "Informe de gastos"


def test_documentos_todos_reporting_situation_marks_closed_states():
    assert (
        user_routes._documentos_todos_reporting_situation(_doc(estado="pagado"))
        == "Cerrada"
    )
    assert (
        user_routes._documentos_todos_reporting_situation(_doc(estado="rechazado"))
        == "Cerrada"
    )
    assert (
        user_routes._documentos_todos_reporting_situation(_doc(estado="enviado"))
        == "Abierta"
    )


def test_documentos_todos_filter_form_preserves_q_field():
    source = "src/devnous/gastos/routes/user_routes.py"
    text = open(source, encoding="utf-8").read()

    assert 'name="q"' in text
    assert 'value="{escape(q_value)}"' in text
    assert 'name="situacion"' in text
    assert "Abiertas" in text
    assert "Cerradas" in text
    assert "Referencia, proveedor, beneficiario, concepto" in text


def test_documentos_todos_bulk_zip_href_is_built_from_filters():
    text = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = text.index("async def documentos_todos(")
    end = text.index("async def _query_documentos_todos_for_export", start)
    route = text[start:end]

    assert "bulk_params = {" in route
    assert '"estado": (estado or "").strip()' in route
    assert '"tipo": (tipo or "").strip()' in route
    assert '"empleado_nombre": (empleado_nombre or "").strip()' in route
    assert '"q": q_value' in route
    assert '"situacion": situacion_value' in route
    assert 'bulk_href = "/documentos/todos/exportar-exceles.zip"' in route
    assert "urlencode(bulk_params)" in route


def test_proceso_contable_exposes_bulk_document_excel_downloads():
    text = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()

    assert 'style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;"' in text
    assert "Descargar informes de gastos" in text
    assert "Descargar solicitudes de transferencia" in text
    assert "Descargar todo" in text
    assert "/documentos/todos/exportar-exceles.zip?tipo=INFORME&situacion=cerradas" in text
    assert "/documentos/todos/exportar-exceles.zip?tipo=SOLICITUD&situacion=cerradas" in text


def test_gastos_workspace_nav_shows_allowed_cross_links_and_active_page():
    empleado = SimpleNamespace(
        rol="empleado",
        visible_tool_keys={"gastos.informes", "gastos.solicitudes"},
    )

    html = user_routes._gastos_workspace_nav_html(empleado, "solicitudes")

    assert 'href="/informes-de-gastos"' in html
    assert "Informes de gastos" in html
    assert 'href="/gastos-terceros"' in html
    assert "Solicitudes de transferencia" in html
    assert 'href="/documentos/todos"' in html
    assert 'href="/beneficiarios/altas"' in html
    assert 'aria-current="page"' in html


def test_gastos_workspace_nav_filters_unauthorized_links():
    empleado = SimpleNamespace(
        rol="empleado",
        visible_tool_keys={"gastos.informes"},
    )

    html = user_routes._gastos_workspace_nav_html(empleado, "informes")

    assert 'href="/informes-de-gastos"' in html
    assert 'href="/gastos-terceros"' not in html
    assert 'href="/documentos/todos"' not in html
    assert 'href="/beneficiarios/altas"' not in html


def test_gastos_breadcrumb_html_escapes_labels_and_optional_links():
    html = user_routes._gastos_breadcrumb_html(
        [
            ("Informes <gastos>", "/informes-de-gastos?x=<bad>"),
            ("I-123", None),
        ]
    )

    assert 'aria-label="Ruta de navegación"' in html
    assert 'href="/informes-de-gastos?x=&lt;bad&gt;"' in html
    assert "Informes &lt;gastos&gt;" in html
    assert "<span>I-123</span>" in html
    assert "&rsaquo;" in html


def test_gastos_workspace_nav_is_rendered_on_primary_and_detail_pages():
    text = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()

    terceros = text[
        text.index("async def gastos_terceros(") :
        text.index(
            '@router.get("/gastos-terceros/solicitar-anticipo"',
            text.index("async def gastos_terceros("),
        )
    ]
    informes = text[
        text.index("async def cuentas_de_gastos_list(") :
        text.index(
            '@router.post("/informes-de-gastos/{cuenta_id}/cancelar-borrador"',
            text.index("async def cuentas_de_gastos_list("),
        )
    ]
    informe_detail = text[
        text.index("async def cuenta_de_gastos_detail(") :
        text.index(
            '@router.get("/informes-de-gastos/{cuenta_id}/editar"',
            text.index("async def cuenta_de_gastos_detail("),
        )
    ]
    documento_detail = text[
        text.index("async def ver_documento(") :
        text.index(
            '@router.get("/api/informes-de-gastos/activas"',
            text.index("async def ver_documento("),
        )
    ]
    documentos_todos = text[
        text.index("async def documentos_todos(") :
        text.index(
            "async def _query_documentos_todos_for_export",
            text.index("async def documentos_todos("),
        )
    ]
    beneficiarios = text[
        text.index("def _beneficiary_onboarding_page(") :
        text.index(
            '@router.get("/beneficiarios/altas/nueva"',
            text.index("def _beneficiary_onboarding_page("),
        )
    ]
    informe_edit = text[
        text.index("async def editar_cuenta_de_gastos_form(") :
        text.index(
            '@router.post("/informes-de-gastos/{cuenta_id}/editar")',
            text.index("async def editar_cuenta_de_gastos_form("),
        )
    ]
    informe_nueva_solicitud = text[
        text.index("async def nueva_solicitud_desde_cuenta_form(") :
        text.index(
            '@router.post("/informes-de-gastos/{cuenta_id}/nueva-solicitud")',
            text.index("async def nueva_solicitud_desde_cuenta_form("),
        )
    ]
    informe_saldar = text[
        text.index("async def saldar_cuenta_form(") :
        text.index(
            '@router.post("/informes-de-gastos/{cuenta_id}/saldar")',
            text.index("async def saldar_cuenta_form("),
        )
    ]
    informe_reembolso = text[
        text.index("async def ver_reembolso_cuenta(") :
        text.index(
            '@router.post(\n    "/informes-de-gastos/{cuenta_id}/reembolsos/{reembolso_id}/cancelar"',
            text.index("async def ver_reembolso_cuenta("),
        )
    ]

    assert '_gastos_workspace_nav_html(current_empleado, "solicitudes")' in terceros
    assert '_gastos_workspace_nav_html(current_empleado, "informes")' in informes
    assert '_gastos_workspace_nav_html(current_empleado, "informes")' in informe_detail
    assert '_gastos_workspace_nav_html(current_empleado, "informes")' in informe_edit
    assert (
        '_gastos_workspace_nav_html(current_empleado, "informes")'
        in informe_nueva_solicitud
    )
    assert '_gastos_workspace_nav_html(current_empleado, "informes")' in informe_saldar
    assert (
        '_gastos_workspace_nav_html(current_empleado, "informes")'
        in informe_reembolso
    )
    assert '_gastos_workspace_nav_html(current_empleado, "documentos")' in documento_detail
    assert '_gastos_workspace_nav_html(current_empleado, "documentos")' in documentos_todos
    assert '_gastos_workspace_nav_html(current_empleado, "beneficiarios")' in beneficiarios


def test_gastos_breadcrumbs_are_rendered_on_internal_pages():
    text = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()

    documento_detail = text[
        text.index("async def ver_documento(") :
        text.index(
            '@router.get("/api/informes-de-gastos/activas"',
            text.index("async def ver_documento("),
        )
    ]
    informe_detail = text[
        text.index("async def cuenta_de_gastos_detail(") :
        text.index(
            '@router.get("/informes-de-gastos/{cuenta_id}/editar"',
            text.index("async def cuenta_de_gastos_detail("),
        )
    ]
    informe_edit = text[
        text.index("async def editar_cuenta_de_gastos_form(") :
        text.index(
            '@router.post("/informes-de-gastos/{cuenta_id}/editar")',
            text.index("async def editar_cuenta_de_gastos_form("),
        )
    ]
    informe_nueva_solicitud = text[
        text.index("async def nueva_solicitud_desde_cuenta_form(") :
        text.index(
            '@router.post("/informes-de-gastos/{cuenta_id}/nueva-solicitud")',
            text.index("async def nueva_solicitud_desde_cuenta_form("),
        )
    ]
    informe_saldar = text[
        text.index("async def saldar_cuenta_form(") :
        text.index(
            '@router.post("/informes-de-gastos/{cuenta_id}/saldar")',
            text.index("async def saldar_cuenta_form("),
        )
    ]
    informe_reembolso = text[
        text.index("async def ver_reembolso_cuenta(") :
        text.index(
            '@router.post(\n    "/informes-de-gastos/{cuenta_id}/reembolsos/{reembolso_id}/cancelar"',
            text.index("async def ver_reembolso_cuenta("),
        )
    ]

    assert '_gastos_breadcrumb_html([' in documento_detail
    assert '("Todos los documentos", "/documentos/todos")' in documento_detail
    assert "(documento.numero_referencia, None)" in documento_detail

    assert '_gastos_breadcrumb_html([' in informe_detail
    assert '(f"I-{cuenta.referencia_base}", None)' in informe_detail

    for route_source, leaf in [
        (informe_edit, "Editar"),
        (informe_nueva_solicitud, "Nueva solicitud"),
        (informe_saldar, "Liquidación"),
        (informe_reembolso, "Liquidación"),
    ]:
        assert '_gastos_breadcrumb_html([' in route_source
        assert '("Informes de gastos", "/informes-de-gastos")' in route_source
        assert (
            '(f"I-{cuenta.referencia_base}", f"/informes-de-gastos/{cuenta.id}")'
            in route_source
        )
        assert f'("{leaf}", None)' in route_source


def test_informe_document_visible_copy_has_clean_spanish_encoding():
    text = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    blocks = [
        text[
            text.index("async def ver_documento(") :
            text.index(
                '@router.get("/api/informes-de-gastos/activas"',
                text.index("async def ver_documento("),
            )
        ],
        text[
            text.index("async def cuenta_de_gastos_detail(") :
            text.index(
                '@router.get("/informes-de-gastos/{cuenta_id}/editar"',
                text.index("async def cuenta_de_gastos_detail("),
            )
        ],
        text[
            text.index("async def cancelar_informe_vacio_borrador(") :
            text.index(
                "def _can_quick_capture_expense",
                text.index("async def cancelar_informe_vacio_borrador("),
            )
        ],
        text[
            text.index("async def editar_cuenta_de_gastos_form(") :
            text.index(
                '@router.post("/informes-de-gastos/{cuenta_id}/editar")',
                text.index("async def editar_cuenta_de_gastos_form("),
            )
        ],
        text[
            text.index("async def editar_cuenta_de_gastos_submit(") :
            text.index(
                '@router.post("/api/informes-de-gastos/cfdi-autofill"',
                text.index("async def editar_cuenta_de_gastos_submit("),
            )
        ],
        text[
            text.index("async def cerrar_cuenta_de_gastos(") :
            text.index(
                "async def _ensure_reembolso_solicitud_for_approved_informe",
                text.index("async def cerrar_cuenta_de_gastos("),
            )
        ],
    ]
    scoped = "\n".join(blocks)

    for broken_text in [
        "aprobaci?n",
        "â€”",
        "descripcion la captura",
        "Cree una desde",
        "contacte a soporte",
    ]:
        assert broken_text not in scoped

    for corrected_text in [
        "aprobación",
        "descripción la captura",
        "contacta a soporte",
    ]:
        assert corrected_text in scoped


def test_accounting_cleanup_is_labeled_as_coi_policies():
    admin_source = open(
        "src/devnous/gastos/routes/admin_routes.py", encoding="utf-8"
    ).read()
    user_source = open(
        "src/devnous/gastos/routes/user_routes.py", encoding="utf-8"
    ).read()
    nav_start = admin_source.index("def render_admin_navigation")
    nav_end = admin_source.index("def _render_admin_error_page", nav_start)
    coi_start = admin_source.index("async def gastos_sin_cuenta_contable")
    coi_end = admin_source.index(
        '@router.post("/admin/gastos/{gasto_id}/asignar-cuenta-contable"',
        coi_start,
    )
    admin_surface = admin_source[nav_start:nav_end] + admin_source[coi_start:coi_end]

    assert "Pólizas COI" in admin_surface
    assert "Preparar pólizas COI" in admin_surface
    assert "Guardar preparación COI" in admin_surface
    assert "Centro de Limpieza Contable" not in admin_surface
    assert "Limpieza contable" not in admin_surface
    assert "Pólizas COI" in user_source


def test_document_status_helper_uses_human_operational_labels():
    assert user_routes._documento_human_status("borrador") == (
        "Borrador",
        "Te falta enviarlo",
        "muted",
    )
    assert user_routes._documento_human_status("control_presupuestal") == (
        "Control presupuestal",
        "Pendiente de asignación presupuestal",
        "warn",
    )
    assert user_routes._documento_human_status("rechazado") == (
        "Rechazado",
        "Requiere corrección",
        "error",
    )


def test_action_labels_are_specific_for_reports_and_requests():
    request_doc = SimpleNamespace(id=uuid4(), tipo="SOLICITUD", estado="enviado")
    html = user_routes._solicitud_transferencia_list_actions_html(
        request_doc,
        SimpleNamespace(id=uuid4(), rol="empleado"),
    )
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()

    assert "Revisar solicitud" in html
    assert "Abrir informe" in source
    assert "Abrir gasto" in source
    assert "Abrir reembolso" in source
    assert ">Ver</a>" not in source[
        source.index("async def cuenta_de_gastos_detail(") :
        source.index(
            '@router.get("/informes-de-gastos/{cuenta_id}/editar"',
            source.index("async def cuenta_de_gastos_detail("),
        )
    ]


def test_informe_detail_uses_human_status_and_single_back_action():
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    detail = source[
        source.index("async def cuenta_de_gastos_detail(") :
        source.index(
            '@router.get("/informes-de-gastos/{cuenta_id}/editar"',
            source.index("async def cuenta_de_gastos_detail("),
        )
    ]
    solicitudes_section = detail[
        detail.index('solicitudes_section_rows += (') :
        detail.index(
            'nueva_solicitud_btn_html =',
            detail.index('solicitudes_section_rows += ('),
        )
    ]
    detail_actions = detail[
        detail.index('detail_actions_html = f"""') :
        detail.index(
            'detail_side_html = f"""',
            detail.index('detail_actions_html = f"""'),
        )
    ]

    assert "_documento_human_status_badge(d.estado)" in solicitudes_section
    assert "escape(d.estado or '-')" not in solicitudes_section
    assert "Revisar solicitud" in solicitudes_section
    assert '<th>Acción</th>' in detail
    assert 'colspan="7"' in detail
    assert (
        "Estas solicitudes son salidas de efectivo vinculadas al informe; "
        "afectan el saldo cuando Finanzas registra el pago."
    ) in detail
    assert "Crea una desde el botón anterior" not in detail
    assert "No hay solicitudes de transferencia vinculadas a este informe." in detail
    assert detail_actions.count('href="/informes-de-gastos"') == 1
    assert "Volver a mis informes" in detail_actions
    assert "Abrir informes de gastos" not in detail_actions


def test_document_detail_expenses_table_exposes_edit_actions():
    source = "src/devnous/gastos/routes/user_routes.py"
    text = open(source, encoding="utf-8").read()
    start = text.index("# Build expenses table rows")
    end = text.index("# Build aprobaciones rows", start)
    expenses_table_flow = text[start:end]

    assert "is_finance_admin_for_expense_actions" in expenses_table_flow
    assert "can_edit_expense_from_document" in expenses_table_flow
    assert 'href="/gastos/{expense.id}/editar"' in expenses_table_flow
    assert "<th>Acciones</th>" in text
    assert 'colspan="9"' in text



def test_budget_control_is_named_operator_only_not_role_or_department():
    allowed = SimpleNamespace(
        id="e3d13040-2360-420f-98a1-516440ef63c3",
        nombre="Juan Pablo López Romero",
        correo="jlopez@plataformasports.com",
        rol="empleado",
        departamento="operaciones",
    )
    luis = SimpleNamespace(
        id=uuid4(),
        nombre="Luis Angel Orozco",
        correo="luis@example.com",
        rol="empleado",
        departamento="direccion",
    )
    denied_finance_role = SimpleNamespace(
        id=uuid4(),
        nombre="Odilon Trujillo Macedo",
        correo="odilon@example.com",
        rol="finanzas",
        departamento="contabilidad",
    )

    assert user_routes._is_budget_control_user(allowed) is True
    assert user_routes._is_budget_control_user(luis) is True
    assert user_routes._is_budget_control_user(denied_finance_role) is False


def test_control_presupuestal_selects_are_not_globally_required():
    source = "src/devnous/gastos/routes/user_routes.py"
    text = open(source, encoding="utf-8").read()
    start = text.index("async def documentos_control_presupuestal")
    end = text.index("async def _apply_control_presupuestal_assignment", start)
    control_view = text[start:end]

    assert "required=False" in control_view
    assert "Todos los documentos" in text[text.index("operaciones_section"):text.index("aprobaciones_section")]
    assert "Descripción" in control_view


def test_solicitudes_transferencia_header_filters_include_solicitante_and_action():
    text = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = text.index("async def gastos_terceros(")
    end = text.index('@router.get("/gastos-terceros/solicitar-anticipo"', start)
    route = text[start:end]

    assert 'data-solicitante="{solicitante_attr}"' in route
    assert 'data-accion="{accion_attr}"' in route
    assert 'id="terceros-search-solicitante"' in route
    assert 'id="terceros-search-accion"' in route
    assert 'placeholder="Ej. revisar solicitud, editar, cancelar..."' in route
    assert 'accion_attr_parts = ["revisar solicitud"]' in route
    assert "matchSolicitante" in route
    assert "matchAccion" in route


def test_solicitudes_transferencia_header_does_not_show_anticipo_cta():
    text = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = text.index("async def gastos_terceros(")
    end = text.index('@router.get("/gastos-terceros/solicitar-anticipo"', start)
    route = text[start:end]

    assert "Solicitud a terceros" in route
    assert "Solicitar Anticipo" not in route
    assert "/gastos-terceros/solicitar-anticipo" not in route


def test_informes_de_gastos_header_filters_include_solicitante_provider_and_action():
    text = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = text.index("async def cuentas_de_gastos_list(")
    end = text.index('@router.post("/informes-de-gastos/{cuenta_id}/cancelar-borrador"', start)
    route = text[start:end]

    assert 'class="informes-filter-bar"' in route
    assert 'data-solicitante="{solicitante_attr}"' in route
    assert 'data-proveedor="{proveedor_attr}"' in route
    assert 'data-accion="{accion_attr}"' in route
    assert 'placeholder="Ej. abrir informe, cerrar, cancelar..."' in route
    assert 'placeholder="Ej. ver, cerrar, cancelar..."' not in route
    assert 'accion_attr_parts = ["abrir informe"]' in route


def test_gastos_list_tables_use_consistent_spanish_copy():
    text = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    terceros = text[
        text.index("async def gastos_terceros(") :
        text.index(
            '@router.get("/gastos-terceros/solicitar-anticipo"',
            text.index("async def gastos_terceros("),
        )
    ]
    informes = text[
        text.index("async def cuentas_de_gastos_list(") :
        text.index(
            '@router.post("/informes-de-gastos/{cuenta_id}/cancelar-borrador"',
            text.index("async def cuentas_de_gastos_list("),
        )
    ]
    scoped = terceros + "\n" + informes

    for expected in [
        "Fecha de aprobación",
        "Monto solicitado",
        "Fecha de pago",
        "Por proveedor",
        "Por solicitante",
        "Por acción",
    ]:
        assert expected in scoped

    for legacy in [
        "Fecha de Aprobacion",
        "Monto Solicitado",
        "Fecha Pago",
        "Por Proveedor",
        "Por Solicitante",
        "Por Acción",
    ]:
        assert legacy not in scoped


def test_pendientes_pago_uses_human_status_and_explicit_payment_action():
    text = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = text.index("async def documentos_pendientes_pago(")
    end = text.index(
        '@router.get("/documentos/nueva-solicitud-terceros"',
        start,
    )
    route = text[start:end]

    assert "_documento_human_status_badge(documento.estado)" in route
    assert "<th>Acción</th>" in route
    assert "Revisar para pago" in route
    assert 'next_url = quote("/documentos/pendientes-pago")' in route
    assert "<td>{documento.estado}</td>" not in route
    assert "<strong>Detalle</strong>" not in route


def test_informes_de_gastos_actions_are_spaced_not_inline_overlapped():
    text = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = text.index("async def cuentas_de_gastos_list(")
    end = text.index('@router.post("/informes-de-gastos/{cuenta_id}/cancelar-borrador"', start)
    route = text[start:end]

    assert 'style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;min-width:150px;"' in route
    assert 'style="display:inline-flex;margin:0;"' in route
    assert 'white-space:nowrap;' in route
    assert 'id="informes-search-solicitante"' in route
    assert 'id="informes-search-proveedor"' in route
    assert 'id="informes-search-accion"' in route
    assert "matchSolicitante" in route
    assert "matchProveedor" in route
    assert "matchAccion" in route


def test_documentos_todos_is_available_to_active_authenticated_users() -> None:
    active_user = SimpleNamespace(id=uuid4(), activo=True, rol="empleado")
    inactive_user = SimpleNamespace(id=uuid4(), activo=False, rol="empleado")
    anonymousish_user = SimpleNamespace(id=None, activo=True, rol="empleado")

    assert user_routes._can_view_documentos_todos(active_user) is True
    assert user_routes._can_view_documentos_todos(inactive_user) is False
    assert user_routes._can_view_documentos_todos(anonymousish_user) is False


def test_pending_approval_summary_shows_accumulated_amount() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("async def documentos_pendientes")
    end = source.index('@router.post("/documentos/pendientes/accion-lote")', start)
    block = source[start:end]

    assert "pending_amount_by_currency" in block
    assert "Monto acumulado" in block
    assert "pending_amount_display" in block


def test_historial_and_bulk_pending_actions_use_approver_visibility_gate() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    bulk_start = source.index("async def documentos_pendientes_accion_lote")
    bulk_end = source.index('@router.get("/documentos/historial-aprobador"', bulk_start)
    history_start = bulk_end
    history_end = source.index("# Build query based on role", history_start)

    assert "await _can_review_pending_approvals" in source[bulk_start:bulk_end]
    assert "await _can_review_pending_approvals" in source[history_start:history_end]


def test_solicitud_terceros_creation_does_not_render_budget_concept_selector() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("async def _render_solicitud_terceros_form")
    end = source.index('@router.post("/documentos/nueva-solicitud-terceros")', start)
    block = source[start:end]

    assert "budget_concept_row_terceros = """ in block
    assert "CONCEPTO:" not in block
    assert "budget_concept_id_terceros" not in block


def test_solicitud_terceros_create_and_edit_force_budget_control_assignment() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    create_start = source.index("async def crear_nueva_solicitud_terceros")
    edit_start = source.index("async def editar_solicitud_terceros_post")
    create_block = source[create_start:edit_start]
    edit_end = source.index('@router.post("/documentos/{documento_id}/adjuntos")', edit_start)
    edit_block = source[edit_start:edit_end]

    assert "budget_concept_id = None" in create_block
    assert "budget_concept_id = None" in edit_block


def test_operaciones_gasto_form_hides_budget_concept_selector() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("async def nuevo_gasto_form")
    end = source.index('@router.post("/gastos/nuevo")', start)
    block = source[start:end]

    assert "can_manage_budget_classification = False" in block
    assert 'style="display:none;" aria-hidden="true"' in block
    assert 'name="budget_concept_id_ignored"' in block
    assert 'name="budget_concept_id"' not in block


def test_operaciones_gasto_create_ignores_budget_concept_payload() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    start = source.index("async def crear_gasto(")
    end = source.index('@router.get("/gastos/carga-masiva-amex"', start)
    block = source[start:end]

    assert "budget_concept_id = None" in block
    assert "cuenta_contable_id = None" in block
    assert "resolve_budget_concept" not in block


def test_operaciones_gasto_edit_hides_and_ignores_budget_concept() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    form_start = source.index("async def editar_gasto_form")
    post_start = source.index('@router.post("/gastos/{gasto_id}/editar")', form_start)
    form_block = source[form_start:post_start]
    post_end = source.index("@router.get(\"/informes-de-gastos", post_start)
    post_block = source[post_start:post_end]

    assert "can_manage_budget_classification = False" in form_block
    assert 'name="budget_concept_id_ignored"' in form_block
    assert 'name="budget_concept_id"' not in form_block
    assert "can_manage_budget_classification = False" in post_block


def test_informe_quick_capture_hides_and_ignores_budget_concept() -> None:
    source = open("src/devnous/gastos/routes/user_routes.py", encoding="utf-8").read()
    detail_start = source.index("async def cuenta_de_gastos_detail")
    detail_end = source.index('@router.get("/informes-de-gastos/{cuenta_id}/editar"', detail_start)
    detail_block = source[detail_start:detail_end]
    quick_start = source.index("async def crear_gasto_rapido_en_informe")
    quick_end = source.index('@router.post("/informes-de-gastos/{cuenta_id}/gastos/amex")', quick_start)
    quick_block = source[quick_start:quick_end]

    assert "can_manage_budget_classification = False" in detail_block
    assert 'quick_budget_name = "budget_concept_id_ignored"' in detail_block
    assert '"required" if quick_budget_concepts_filtered and can_manage_budget_classification else ""' in detail_block
    assert "resolve_budget_concept" not in quick_block
    assert "budget_concept = None" in quick_block


class _LazyAprobadorBomb:
    @property
    def aprobador(self):  # pragma: no cover - only exercised on regression
        raise AssertionError("aprobador relation must be resolved in batch")


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeAprobadorSession:
    def __init__(self, employee):
        self._employee = employee
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        if self.calls == 1:
            return _FakeResult([])
        return _FakeResult([self._employee])


async def test_fetch_documento_aprobador_display_batch_does_not_lazy_load_subject_approver():
    empleado_id = uuid4()
    documento = _doc(
        empleado_id=empleado_id,
        empleado=_LazyAprobadorBomb(),
        beneficiario_empleado_id=None,
        beneficiario_empleado=None,
    )
    session = _FakeAprobadorSession(
        SimpleNamespace(
            id=empleado_id,
            aprobador=SimpleNamespace(nombre="Odilon Aprobador"),
        )
    )

    display = await documento_service.fetch_documento_aprobador_display_batch(
        session, [documento]
    )

    assert display[documento.id] == "Odilon Aprobador"
    assert session.calls == 2
