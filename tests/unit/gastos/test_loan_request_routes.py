from pathlib import Path
from types import SimpleNamespace

from devnous.gastos.routes import user_routes


SOURCE = Path(user_routes.__file__).read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    block_start = SOURCE.index(start)
    block_end = SOURCE.index(end, block_start)
    return SOURCE[block_start:block_end]


def test_gastos_navigation_links_to_prestamos_module() -> None:
    nav_block = _block(
        "def _gastos_workspace_nav_html",
        "def _gastos_breadcrumb_html",
    )

    assert '("/prestamos", "Préstamos", "prestamos")' in nav_block


def test_prestamos_list_filters_visibility_and_renders_summary() -> None:
    route_block = _block(
        '@router.get("/prestamos", response_class=HTMLResponse)',
        '@router.get("/prestamos/nuevo", response_class=HTMLResponse)',
    )

    assert "can_view_all_prestamos(current_empleado)" in route_block
    assert "selectinload(SolicitudPrestamo.abonos)" in route_block
    assert (
        "SolicitudPrestamo.solicitante_empleado_id == current_empleado.id"
        in route_block
    )
    assert "selected_view" in route_block
    assert "view_filters" in route_block
    assert "por_aprobar" in route_block
    assert "por_programar" in route_block
    assert "por_pagar" in route_block
    assert "abonos_pendientes" in route_block
    assert "PRESTAMO_ABONO_STATUS_ENVIADO" in route_block
    assert "_prestamo_row_html(prestamo, current_empleado)" in route_block
    assert "Nueva solicitud" in route_block
    assert "Saldo pendiente" in route_block


def test_prestamo_form_uses_mutually_exclusive_beneficiary_controls() -> None:
    route_block = _block(
        '@router.get("/prestamos/nuevo", response_class=HTMLResponse)',
        '@router.post("/prestamos")',
    )

    assert 'name="beneficiario_tipo"' in route_block
    assert (
        'data-prestamo-target="{PRESTAMO_BENEFICIARIO_EMPLEADO}"'
        in route_block
    )
    assert (
        'data-prestamo-target="{PRESTAMO_BENEFICIARIO_OPERADOR_REGIONAL}"'
        in route_block
    )
    assert (
        'data-prestamo-target="{PRESTAMO_BENEFICIARIO_PROVEEDOR}"'
        in route_block
    )
    assert "input.disabled = !active" in route_block
    assert 'name="monto_solicitado"' in route_block
    assert 'name="motivo"' in route_block


def test_prestamo_create_persists_draft_or_submits_via_service() -> None:
    route_block = _block(
        '@router.post("/prestamos")',
        '@router.get("/prestamos/{prestamo_id}", response_class=HTMLResponse)',
    )

    assert "_build_prestamo_payload_from_form" in route_block
    assert "build_prestamo_from_payload(payload)" in route_block
    assert 'if action == "submit":' in route_block
    assert "submit_prestamo(prestamo, current_empleado)" in route_block
    assert "await session.commit()" in route_block
    assert "await session.rollback()" in route_block


def test_prestamo_detail_exposes_send_cancel_and_abonos_sections() -> None:
    route_block = _block(
        '@router.get("/prestamos/{prestamo_id}", response_class=HTMLResponse)',
        '@router.post("/prestamos/{prestamo_id}/enviar")',
    )

    assert "_load_prestamo_for_user" in route_block
    assert 'action="/prestamos/{prestamo.id}/enviar"' in route_block
    assert 'action="/prestamos/{prestamo.id}/cancelar"' in route_block
    assert 'action="/prestamos/{prestamo.id}/aprobar"' in route_block
    assert 'action="/prestamos/{prestamo.id}/rechazar"' in route_block
    assert 'action="/prestamos/{prestamo.id}/programar-pago"' in route_block
    assert 'action="/prestamos/{prestamo.id}/cuenta-deudor"' in route_block
    assert 'name="cuenta_deudor_contable_id"' in route_block
    assert "_prestamo_debtor_account_options(" in route_block
    assert "can_manage_payment_run(current_empleado)" in route_block
    assert 'action="/prestamos/{prestamo.id}/comprobante-pago"' in route_block
    assert 'action="/prestamos/{prestamo.id}/abonos"' in route_block
    assert 'name="comprobante_abono"' in route_block
    assert "excess_confirmed" in route_block
    assert 'action="/prestamos/abonos/{abono.id}/aprobar"' in route_block
    assert 'action="/prestamos/abonos/{abono.id}/rechazar"' in route_block
    assert 'name="comprobante_pago"' in route_block
    assert "can_confirm_payment_run_payment(current_empleado)" in route_block
    assert "can_approve_prestamo_abono(current_empleado)" in route_block
    assert "can_approve_prestamo(current_empleado)" in route_block
    assert "lista para programación de pago" in route_block
    assert "marcado como pagado" in route_block
    assert "Movimientos registrados" in route_block
    assert "monto_excedente" in route_block


def test_prestamo_approval_routes_commit_or_rollback_via_service() -> None:
    route_block = _block(
        '@router.post("/prestamos/{prestamo_id}/aprobar")',
        '@router.get("/gastos-terceros", response_class=HTMLResponse)',
    )

    assert "approve_prestamo(" in route_block
    assert "reject_prestamo(" in route_block
    assert "comentario" in route_block
    assert "await session.commit()" in route_block
    assert "await session.rollback()" in route_block


def test_prestamo_schedule_payment_route_commits_or_rolls_back() -> None:
    route_block = _block(
        '@router.post("/prestamos/{prestamo_id}/programar-pago")',
        '@router.post("/prestamos/{prestamo_id}/comprobante-pago")',
    )

    assert "schedule_prestamo_payment(" in route_block
    assert "await session.commit()" in route_block
    assert "await session.rollback()" in route_block


def test_prestamo_assign_debtor_account_route_commits_or_rolls_back() -> None:
    route_block = _block(
        '@router.post("/prestamos/{prestamo_id}/cuenta-deudor")',
        '@router.post("/prestamos/{prestamo_id}/comprobante-pago")',
    )

    assert "assign_prestamo_debtor_account(" in route_block
    assert "await session.commit()" in route_block
    assert "await session.rollback()" in route_block


def test_prestamo_payment_proof_route_saves_upload_and_marks_paid() -> None:
    route_block = _block(
        '@router.post("/prestamos/{prestamo_id}/comprobante-pago")',
        '@router.get("/gastos-terceros", response_class=HTMLResponse)',
    )

    assert "_save_prestamo_payment_proof_upload" in route_block
    assert "register_prestamo_payment_proof(" in route_block
    assert "ensure_prestamo_payment_posting(" in route_block
    assert 'posting.status == "pending"' in route_block
    assert "await session.commit()" in route_block
    assert "await session.rollback()" in route_block


def test_prestamo_abono_routes_register_and_review_repayments() -> None:
    route_block = _block(
        '@router.post("/prestamos/{prestamo_id}/abonos")',
        '@router.get("/gastos-terceros", response_class=HTMLResponse)',
    )

    assert "_save_prestamo_abono_proof_upload" in route_block
    assert "register_prestamo_abono(" in route_block
    assert "approve_prestamo_abono(" in route_block
    assert "ensure_prestamo_abono_posting(" in route_block
    assert "reject_prestamo_abono(" in route_block
    assert "await session.commit()" in route_block
    assert "await session.rollback()" in route_block


def test_prestamo_route_helpers_render_status_and_account_display() -> None:
    badge = user_routes._prestamo_status_badge("enviada")
    prestamo = SimpleNamespace(
        banco_beneficiario="Santander",
        cuenta_beneficiario="1234",
    )

    assert "Enviada" in badge
    assert (
        "Santander / 1234"
        == user_routes._prestamo_account_display(prestamo)
    )
