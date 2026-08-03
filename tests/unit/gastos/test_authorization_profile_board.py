from pathlib import Path
from types import SimpleNamespace

from devnous.gastos.routes import user_routes
from devnous.gastos.services.authorization_profile_service import (
    PROFILE_DEFINITIONS,
    default_profile_rules,
    summarize_profile_rules,
)


def test_default_authorization_profiles_are_person_like():
    names = {name for _key, name, _role, _matcher in PROFILE_DEFINITIONS}

    assert "Perfil Odilon" in names
    assert "Perfil Luis Angel" in names
    assert "Perfil Olof" in names
    assert "Perfil Benjamin" in names
    assert "Perfil DG" in names


def test_profile_rules_expose_switchable_authorization_surface():
    odilon_rules = default_profile_rules("director_operaciones")
    luis_rules = default_profile_rules("dayf")
    dg_rules = default_profile_rules("dg")

    assert any(
        rule["rule_key"] == "ops_supplier_transfer_gt_100k" for rule in odilon_rules
    )
    assert any(rule["rule_key"] == "ops_no_deductible" for rule in luis_rules)
    assert any(rule["can_second_approve"] for rule in dg_rules)
    assert all("enabled" in rule for rule in odilon_rules)
    assert all("can_first_approve" in rule for rule in odilon_rules)
    assert all("can_second_approve" in rule for rule in odilon_rules)


def test_profile_summary_counts_switches_and_exceptions():
    rules = default_profile_rules("director_operaciones")
    summary = summarize_profile_rules(rules)

    assert summary["total"] >= 1
    assert summary["enabled"] == summary["total"]
    assert summary["first"] >= 1
    assert summary["exceptions"] >= 1


def test_authorization_profile_board_routes_and_nav_are_registered():
    source = Path(user_routes.__file__).read_text()
    access_source = Path(
        "/root/samchat/src/devnous/gastos/services/access_control_service.py"
    ).read_text()
    tool_start = access_source.index('"configuracion.estrategias_autorizacion"')
    tool_end = access_source.index(
        'AccessTool(',
        tool_start + len('"configuracion.estrategias_autorizacion"'),
    )
    tool_block = access_source[tool_start:tool_end]

    assert '@router.get("/admin/estrategias-autorizacion"' in source
    assert "copy_authorization_profile" in source
    assert "update_authorization_profile_rules" in source
    assert "Estrategias de autorizacion" in source
    assert "configuracion.estrategias_autorizacion" in source
    assert '"/admin/estrategias-autorizacion"' in source
    assert "_require_authorization_strategy_admin" in source
    assert "ADMIN_ROLES" in tool_block


def test_document_authorization_input_inference_for_no_invoice_solicitud():
    from devnous.gastos.services.authorization_profile_service import (
        infer_document_authorization_inputs,
    )

    documento = SimpleNamespace(
        tipo="SOLICITUD",
        empleado=SimpleNamespace(departamento="Operaciones"),
        monto_solicitado="1500.00",
        monto_total=None,
        cfdi_report_id=None,
        cfdi_uuid_manual=None,
        numero_factura=None,
        pago_urgente=False,
        budget_concept_id=None,
        concepto_pago="Compra de material",
        notas="",
        numero_referencia="S-1",
    )

    inputs = infer_document_authorization_inputs(documento)

    assert inputs["area"] == "Operaciones"
    assert inputs["erogation_type"] == "no_deductible"
    assert inputs["has_invoice"] is False
    assert inputs["amount_mxn"] == "1500.00"


def test_document_workflow_send_persists_authorization_strategy_evidence():
    source = Path(
        "/root/samchat/src/devnous/gastos/services/documento_workflow_service.py"
    ).read_text()
    send_block_start = source.index(
        'if normalized_action == "send":',
        source.index("authorization_strategy_evidence"),
    )
    audit_block_start = source.index(
        "await record_customer_success_audit_event", send_block_start
    )
    block = source[send_block_start:audit_block_start]

    assert "build_document_authorization_evidence" in block
    assert 'audit_metadata["authorization_strategy"]' in source
    assert "metadata=audit_metadata" in source


def test_document_detail_renders_authorization_strategy_evidence_panel():
    source = Path(user_routes.__file__).read_text()

    assert "_render_document_authorization_strategy_evidence" in source
    assert "Ruta de autorizacion sugerida" in source
    assert "customer_success_audit_events" in source
    assert "authorization_strategy_html = await _render_document_authorization_strategy_evidence" in source
    assert "{authorization_strategy_html}" in source
    assert "no bloquea ni sustituye el flujo actual" in source


def test_document_detail_compares_authorization_strategy_with_actual_route():
    source = Path(user_routes.__file__).read_text()

    assert "_authorization_strategy_actual_route_preview_html" in source
    assert "Comparacion consultiva" in source
    assert "Roles cubiertos" in source
    assert "Roles faltantes" in source
    assert "FROM aprobaciones a" in source
    assert "Coincide con matriz" in source
    assert "Diferencia consultiva" in source


def test_document_detail_previews_authorization_strategy_before_send():
    source = Path(user_routes.__file__).read_text()

    assert "_render_document_authorization_pre_send_preview" in source
    assert "Preview de autorizacion al enviar" in source
    assert "build_document_authorization_evidence" in source
    assert "can_send_documento=can_send_documento" in source
    assert "{authorization_pre_send_preview_html}" in source
    assert "no bloquea el envio" in source


def test_document_workflow_approval_persists_soft_authorization_warning():
    source = Path(
        "/root/samchat/src/devnous/gastos/services/documento_workflow_service.py"
    ).read_text()
    profile_source = Path(
        "/root/samchat/src/devnous/gastos/services/authorization_profile_service.py"
    ).read_text()

    assert "build_authorization_route_soft_warning" in profile_source
    assert "authorization_route_mismatch" in profile_source
    assert 'if normalized_action == "approve":' in source
    assert "build_authorization_route_soft_warning" in source
    assert 'audit_metadata["authorization_route_warning"]' in source


def test_document_detail_renders_soft_authorization_warning_panel():
    source = Path(user_routes.__file__).read_text()

    assert "_render_document_authorization_route_warning" in source
    assert "Warning suave de autorizacion" in source
    assert "authorization_route_warning" in source
    assert "metadata_json ? 'authorization_route_warning'" in source
    assert "{authorization_route_warning_html}" in source


def test_authorization_warnings_dashboard_is_registered_read_only():
    source = Path(user_routes.__file__).read_text()
    access_source = Path(
        "/root/samchat/src/devnous/gastos/services/access_control_service.py"
    ).read_text()

    assert '@router.get("/admin/estrategias-autorizacion/warnings"' in source
    assert "authorization_strategy_warnings_page" in source
    assert "Warnings de autorizacion" in source
    assert "authorization_route_warning" in source
    assert "documento.approved" in source
    assert "configuracion.authorization_warnings" in source
    assert "configuracion.authorization_warnings" in access_source
    assert "FINANCE_ADMIN_ROLES" in access_source
    assert '("ver",)' in access_source


def test_authorization_strategy_enforcement_is_off_by_default(monkeypatch):
    from devnous.gastos.services.authorization_profile_service import (
        authorization_strategy_enforcement_enabled,
    )

    monkeypatch.delenv("SAMCHAT_AUTHORIZATION_STRATEGY_ENFORCEMENT", raising=False)
    monkeypatch.delenv("AUTHORIZATION_STRATEGY_ENFORCEMENT", raising=False)

    assert authorization_strategy_enforcement_enabled() is False


def test_authorization_strategy_enforcement_accepts_explicit_switch(monkeypatch):
    from devnous.gastos.services.authorization_profile_service import (
        authorization_strategy_enforcement_enabled,
    )

    monkeypatch.setenv("SAMCHAT_AUTHORIZATION_STRATEGY_ENFORCEMENT", "enabled")

    assert authorization_strategy_enforcement_enabled() is True


def test_authorization_strategy_actor_profile_matching_uses_copied_profile_matcher():
    from devnous.gastos.services.authorization_profile_service import (
        authorization_strategy_actor_matches_required_profile,
    )

    actor = SimpleNamespace(
        nombre="José Odilon Trujillo Macedo",
        rol="finanzas",
        departamento="Operaciones",
    )
    profiles = [
        {
            "name": "Perfil Odilon",
            "role_key": "director_operaciones",
            "employee_matcher": "odilon trujillo",
        }
    ]

    assert authorization_strategy_actor_matches_required_profile(
        actor=actor,
        profiles=profiles,
        required_role_keys=("director_operaciones",),
    ) == ("director_operaciones",)


def test_document_workflow_has_feature_flagged_authorization_enforcement():
    source = Path(
        "/root/samchat/src/devnous/gastos/services/documento_workflow_service.py"
    ).read_text()
    profile_source = Path(
        "/root/samchat/src/devnous/gastos/services/authorization_profile_service.py"
    ).read_text()

    assert "SAMCHAT_AUTHORIZATION_STRATEGY_ENFORCEMENT" in profile_source
    assert "build_authorization_route_hard_block" in profile_source
    assert "build_authorization_route_hard_block" in source
    assert "authorization_strategy_required" in source
    assert "documento.estado = \"aprobado\"" in source
    assert source.index("authorization_strategy_required") < source.index(
        "documento.estado = \"aprobado\"",
        source.index("authorization_strategy_required"),
    )
