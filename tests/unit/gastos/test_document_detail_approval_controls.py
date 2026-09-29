from pathlib import Path
from types import SimpleNamespace

from devnous.gastos.routes import user_routes
from devnous.gastos.workflow_guidance import build_document_workflow_guidance


def _approval_controls_source() -> str:
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index(
        "<!-- Aprobar / Rechazar (only if estado = enviado AND user has permission) -->"
    )
    end = source.index("<!-- Saldar cuenta", start)
    return source[start:end]


def test_document_detail_approval_submit_does_not_depend_on_javascript() -> None:
    block = _approval_controls_source()

    assert '<summary class="button primary">Aprobar</summary>' in block
    assert "Confirmar aprobación</button>" in block
    assert 'action="/documentos/{documento_id}/aprobar"' in block
    assert 'id="aprobar-form"' not in block
    assert "document.getElementById" not in block


def test_document_detail_rejection_uses_native_disclosure_and_post_form() -> None:
    block = _approval_controls_source()

    assert "<details>" in block
    assert '<summary class="button danger">Rechazar</summary>' in block
    assert 'action="/documentos/{documento_id}/rechazar"' in block
    assert 'textarea name="comentario" id="comentario_rechazar" required' in block


def test_document_detail_has_one_canonical_approval_control_surface() -> None:
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("async def ver_documento")
    end = source.index("# CUENTAS DE GASTOS ROUTES", start)
    block = source[start:end]

    assert block.count('action="/documentos/{documento_id}/aprobar"') == 1
    assert block.count('action="/documentos/{documento_id}/rechazar"') == 1
    assert "detail_approval_actions_html" not in block


def test_document_detail_mounts_read_only_workflow_guidance() -> None:
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("async def ver_documento")
    end = source.index("# CUENTAS DE GASTOS ROUTES", start)
    block = source[start:end]

    assert "build_document_workflow_guidance(" in block
    assert "latest_rejection = _latest_document_rejection(aprobaciones)" in block
    assert "locked_reason=locked_reason" in block
    assert "has_payment_timestamp=documento.pagado_en is not None" in block
    assert "{workflow_guidance_html}" in block


def test_document_workflow_guidance_escapes_evidence_and_adds_no_actions() -> None:
    guidance = build_document_workflow_guidance(
        state="rechazado",
        document_type="SOLICITUD",
        is_owner=True,
        can_approve_or_reject=False,
        rejection_reason='<script>alert("x")</script>',
        rejection_actor="Aprobador <demo>",
    )

    html = user_routes._render_document_workflow_guidance_html(guidance)

    assert "<script>" not in html
    assert "Aprobador &lt;demo&gt;" in html
    assert "&lt;script&gt;" in html
    assert "<form" not in html
    assert "<button" not in html


def test_document_detail_preserves_post_approval_transfer_actions() -> None:
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("async def ver_documento")
    end = source.index("# CUENTAS DE GASTOS ROUTES", start)
    block = source[start:end]

    assert "Ver gasto generado" in block
    assert "documento.estado == 'pagado'" in block


def test_pending_queue_renders_redirect_feedback() -> None:
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("async def documentos_pendientes(")
    end = source.index("async def documentos_pendientes_accion_lote(", start)
    block = source[start:end]

    assert 'request.query_params.get("success_msg", "").strip()' in block
    assert 'request.query_params.get("error_msg", "").strip()' in block
    assert 'role="status"' in block
    assert 'role="alert"' in block
    assert "No se pudo completar la acción:" in block
    assert "escape(error_msg)" in block


def test_budget_control_queue_renders_assignment_feedback() -> None:
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("async def documentos_control_presupuestal(")
    end = source.index("async def _apply_control_presupuestal_assignment(", start)
    block = source[start:end]

    assert 'request.query_params.get("success_msg", "").strip()' in block
    assert 'request.query_params.get("error_msg", "").strip()' in block
    assert 'role="status"' in block
    assert 'role="alert"' in block
    assert "Acción completada:" in block
    assert "No se pudo completar la asignación:" in block
    assert "escape(success_msg)" in block
    assert "escape(error_msg)" in block
    assert "_render_transient_message_query_cleanup_script()" in block


def test_latest_rejection_includes_budget_control_reason_and_actor():
    approval = SimpleNamespace(accion="aprobar", comentario="Autorizada")
    budget_rejection = SimpleNamespace(
        accion="rechazar_control_presupuestal",
        comentario="Partida no válida",
        aprobador=SimpleNamespace(nombre="Control Presupuestal"),
    )
    older_rejection = SimpleNamespace(accion="rechazar", comentario="Motivo anterior")
    latest = user_routes._latest_document_rejection(
        [approval, budget_rejection, older_rejection]
    )
    guidance = build_document_workflow_guidance(
        state="rechazado",
        document_type="SOLICITUD",
        is_owner=True,
        can_approve_or_reject=False,
        rejection_reason=latest.comentario,
        rejection_actor=latest.aprobador.nombre,
    )
    assert guidance.blocker == "Partida no válida"
    assert "Control Presupuestal" in guidance.why_here
    assert user_routes._latest_document_rejection([approval]) is None
