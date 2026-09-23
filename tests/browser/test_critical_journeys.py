from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, expect


def _capture(page: Page, name: str) -> None:
    root = Path(
        os.environ.get(
            "SAMCHAT_BROWSER_ARTIFACT_DIR",
            Path(__file__).resolve().parents[2] / "artifacts" / "browser",
        )
    )
    target = root / "journeys"
    target.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target / f"{name}.png"), full_page=True)


def _assert_no_body_overflow(page: Page) -> None:
    dimensions = page.evaluate("""() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth
        })""")
    assert dimensions["scrollWidth"] <= dimensions["clientWidth"] + 1


def _mutation_home(page: Page, browser_server: str) -> None:
    response = page.goto(f"{browser_server}/_test/mutations?reset=true")
    assert response is not None
    assert response.status == 200
    expect(
        page.get_by_role("heading", name="Simulación aislada de mutaciones UX")
    ).to_be_visible()


def test_employee_transfer_request_reaches_canonical_creation_form(
    page: Page, browser_server: str
) -> None:
    response = page.goto(f"{browser_server}/_test/panel/employee")
    assert response is not None
    assert response.status == 200

    transfer_entry = page.locator('a[href="/gastos-terceros"]').first
    expect(transfer_entry).to_be_visible()
    transfer_entry.click()

    expect(page).to_have_url(f"{browser_server}/gastos-terceros")
    expect(
        page.get_by_role("heading", name="Solicitudes de transferencia")
    ).to_be_visible()
    expect(
        page.get_by_text(
            "Bandeja operativa para proveedores, pagos a terceros",
            exact=False,
        )
    ).to_be_visible()

    create_entry = page.locator('a[href="/documentos/nueva-solicitud-terceros"]').first
    expect(create_entry).to_be_visible()
    expect(create_entry).to_have_text("Solicitud a terceros")
    _capture(page, "employee-transfer-list")

    create_entry.click()
    expect(page).to_have_url(f"{browser_server}/documentos/nueva-solicitud-terceros")
    expect(
        page.get_by_text("Nueva solicitud a terceros", exact=True).first
    ).to_be_visible()

    project_select = page.locator('select[name="torneo_id"]')
    provider_select = page.locator('select[name="proveedor_cliente_id"]')
    expect(project_select).to_be_visible()
    expect(provider_select).to_be_visible()
    expect(project_select.locator("option", has_text="Copa Browser UX")).to_have_count(
        1
    )
    expect(
        provider_select.locator("option", has_text="Proveedor Browser UX")
    ).to_have_count(1)

    expect(
        page.get_by_role("button", name="Crear solicitud", exact=True)
    ).to_be_visible()
    expect(
        page.get_by_role(
            "button", name="Crear solicitud y enviar para aprobación", exact=True
        )
    ).to_be_visible()
    _capture(page, "employee-transfer-create")

    cancel = page.get_by_role("link", name="Cancelar", exact=True)
    expect(cancel).to_be_visible()
    cancel.click()
    expect(page).to_have_url(f"{browser_server}/gastos-terceros")
    expect(
        page.get_by_role("heading", name="Solicitudes de transferencia")
    ).to_be_visible()


def test_approver_reaches_real_pending_queue_with_decision_context(
    page: Page, browser_server: str
) -> None:
    response = page.goto(f"{browser_server}/_test/panel/approver")
    assert response is not None
    assert response.status == 200

    entry = page.locator('a[href="/documentos/pendientes"]').first
    expect(entry).to_be_visible()
    expect(entry).to_contain_text("Aprobaciones pendientes")
    entry.click()

    expect(page).to_have_url(f"{browser_server}/documentos/pendientes")
    expect(page.get_by_role("heading", name="Pendientes por aprobar")).to_be_visible()
    expect(page.get_by_text("S-UX-0001", exact=True)).to_be_visible()
    expect(page.get_by_text("Solicitante Browser UX", exact=True)).to_be_visible()
    expect(page.get_by_text("Proveedor Browser UX", exact=True)).to_be_visible()
    expect(page.get_by_text("Copa Browser UX", exact=True)).to_be_visible()
    expect(
        page.get_by_text("Hospedaje para torneo regional", exact=True)
    ).to_be_visible()

    approve = page.get_by_role("button", name="Aprobar", exact=True)
    reject = page.get_by_role("button", name="Rechazar", exact=True)
    expect(approve).to_be_visible()
    expect(reject).to_be_visible()
    expect(
        page.get_by_text("Documentos esperando tu decisión", exact=False)
    ).to_be_visible()

    reject.click()
    expect(page).to_have_url(f"{browser_server}/documentos/pendientes")
    reason = page.get_by_label("Motivo de rechazo").first
    expect(reason).to_be_focused()
    expect(
        page.get_by_role("alert").get_by_text(
            "Escribe el motivo del rechazo", exact=False
        )
    ).to_be_visible()

    reason.fill("Falta la orden de compra")
    expect(reason).to_have_value("Falta la orden de compra")
    _capture(page, "approver-pending-queue")


def test_approver_pending_queue_contains_table_scroll_on_mobile(
    page: Page, browser_server: str
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    response = page.goto(f"{browser_server}/documentos/pendientes")
    assert response is not None
    assert response.status == 200

    expect(page.get_by_role("heading", name="Pendientes por aprobar")).to_be_visible()
    _assert_no_body_overflow(page)

    shell = page.locator(".table-shell").last
    expect(shell).to_be_visible()
    has_horizontal_scroll = shell.evaluate("(el) => el.scrollWidth > el.clientWidth")
    assert has_horizontal_scroll is True


def test_budget_control_reaches_classification_queue_with_context(
    page: Page, browser_server: str
) -> None:
    response = page.goto(f"{browser_server}/_test/panel/budget_control")
    assert response is not None
    assert response.status == 200

    entry = page.locator('a[href="/documentos/control-presupuestal"]').first
    expect(entry).to_be_visible()
    expect(entry).to_contain_text("Control Presupuestal")
    entry.click()

    expect(page).to_have_url(f"{browser_server}/documentos/control-presupuestal")
    expect(
        page.get_by_role("heading", name="Documentos por clasificar")
    ).to_be_visible()
    expect(
        page.get_by_text(
            "Asigna el concepto presupuestal antes de enviar",
            exact=False,
        )
    ).to_be_visible()

    expect(page.get_by_text("S-UX-0001", exact=True)).to_be_visible()
    expect(page.get_by_text("Copa Browser UX", exact=True)).to_be_visible()
    expect(page.get_by_text("Solicitante Browser UX", exact=True)).to_be_visible()
    expect(page.get_by_text("Proveedor Browser UX", exact=True)).to_be_visible()
    expect(
        page.get_by_text("Hospedaje para torneo regional", exact=True)
    ).to_be_visible()

    concept = page.locator('select[name^="budget_concept_id_"]').first
    expect(concept).to_be_visible()
    expect(
        concept.locator("option", has_text="Hospedaje y alimentación")
    ).to_have_count(1)

    expect(
        page.get_by_role("button", name="Asignar y enviar", exact=True)
    ).to_be_visible()
    expect(page.get_by_role("button", name="Rechazar", exact=True)).to_be_visible()
    _capture(page, "budget-control-queue")


def test_budget_control_queue_keeps_wide_actions_inside_table_scroll(
    page: Page, browser_server: str
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    response = page.goto(f"{browser_server}/documentos/control-presupuestal")
    assert response is not None
    assert response.status == 200

    expect(
        page.get_by_role("heading", name="Documentos por clasificar")
    ).to_be_visible()
    _assert_no_body_overflow(page)

    shell = page.locator(".table-shell").last
    expect(shell).to_be_visible()
    assert shell.evaluate("(el) => el.scrollWidth > el.clientWidth") is True


def test_finance_reaches_payment_run_and_state_boundary_is_explicit(
    page: Page, browser_server: str
) -> None:
    response = page.goto(f"{browser_server}/_test/profile/finance")
    assert response is not None
    assert response.status == 200

    entry = page.locator('a[href="/admin/finanzas/payment-run"]').first
    expect(entry).to_be_visible()
    entry.click()

    expect(page).to_have_url(f"{browser_server}/admin/finanzas/payment-run")
    expect(
        page.get_by_role("heading", name="Programación de pagos")
    ).to_be_visible()
    expect(
        page.get_by_text(
            "Al cerrar, las solicitudes pasan a En Proceso de Pago",
            exact=False,
        )
    ).to_be_visible()

    expect(
        page.get_by_text("Solicitudes aprobadas para corte", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("S-PAY-0001", exact=True)).to_be_visible()
    expect(
        page.get_by_text("Hospedaje aprobado para corte", exact=False)
    ).to_be_visible()

    expect(
        page.get_by_text("Comprobantes pendientes - En Proceso de Pago", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("S-PAY-0002", exact=True)).to_be_visible()
    expect(page.get_by_text("Comprobante de pago", exact=True).first).to_be_visible()
    expect(
        page.get_by_text(
            "al guardarlo, la solicitud se marca Pagada automáticamente",
            exact=False,
        )
    ).to_be_visible()

    expect(page.get_by_role("button", name="Cerrar corte", exact=True)).to_be_visible()
    expect(
        page.get_by_role("button", name="Subir comprobante y marcar pagado", exact=True)
    ).to_be_visible()
    _capture(page, "finance-payment-run")


def test_accounting_reaches_cleanup_and_sees_exact_blockers(
    page: Page, browser_server: str
) -> None:
    response = page.goto(f"{browser_server}/_test/profile/accounting")
    assert response is not None
    assert response.status == 200

    entry = page.locator('a[href="/admin/gastos/sin-cuenta-contable"]').first
    expect(entry).to_be_visible()
    entry.click()

    expect(page).to_have_url(f"{browser_server}/admin/gastos/sin-cuenta-contable")
    expect(page.get_by_role("heading", name="Limpieza contable")).to_be_visible()
    expect(
        page.get_by_text(
            "Bandeja de preparación para completar CFDI, cuenta de cargo",
            exact=False,
        )
    ).to_be_visible()

    expect(page.get_by_text("G-UX-COI-001", exact=True).first).to_be_visible()
    expect(
        page.get_by_text(
            "Hospedaje regional sin clasificación contable",
            exact=True,
        )
    ).to_be_visible()
    summary_row = page.locator("#row-80000000-0000-0000-0000-000000000001")
    expect(
        summary_row.get_by_text("Falta cuenta cargo", exact=True).first
    ).to_be_visible()
    expect(
        summary_row.get_by_text("Falta contrapartida", exact=True).first
    ).to_be_visible()
    expect(summary_row.get_by_text("Falta CFDI", exact=True).first).to_be_visible()

    review = summary_row.get_by_role("button", name="Revisar", exact=True)
    expect(review).to_be_visible()
    review.click()

    expect(page.get_by_text("Cuentas contables", exact=True)).to_be_visible()
    expect(page.get_by_text("Desglose fiscal", exact=True)).to_be_visible()
    expect(
        page.get_by_role("button", name="Guardar preparación COI", exact=True)
    ).to_be_visible()
    expect(
        page.get_by_text(
            "Falta clasificación contable antes de exportar a COI.",
            exact=True,
        )
    ).to_be_visible()
    _capture(page, "accounting-cleanup")


def test_accounting_cleanup_stays_actionable_on_mobile(
    page: Page, browser_server: str
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    response = page.goto(f"{browser_server}/admin/gastos/sin-cuenta-contable")
    assert response is not None
    assert response.status == 200

    expect(page.get_by_role("heading", name="Limpieza contable")).to_be_visible()
    _assert_no_body_overflow(page)

    shell = page.locator(".table-shell").last
    expect(shell).to_be_visible()
    expect(page.get_by_role("button", name="Revisar", exact=True)).to_be_visible()


def test_isolated_approval_and_rejection_preserve_outcome_and_reason(
    page: Page, browser_server: str
) -> None:
    _mutation_home(page, browser_server)
    page.get_by_role("button", name="Aprobar solicitud", exact=True).click()
    expect(page.get_by_text("Resultado: Aprobado", exact=True)).to_be_visible()
    expect(page.get_by_text("Estado: aprobado", exact=True)).to_be_visible()
    expect(page.get_by_text("Siguiente cola: Payment Run", exact=True)).to_be_visible()

    _mutation_home(page, browser_server)
    page.get_by_role("button", name="Rechazar solicitud", exact=True).click()
    expect(
        page.get_by_text("Resultado: Rechazo no enviado", exact=True)
    ).to_be_visible()
    expect(
        page.get_by_text("Motivo requerido por el escenario UX", exact=False)
    ).to_be_visible()

    _mutation_home(page, browser_server)
    page.get_by_label("Motivo de rechazo").fill("Falta la orden de compra")
    page.get_by_role("button", name="Rechazar solicitud", exact=True).click()
    expect(page.get_by_text("Estado: rechazado", exact=True)).to_be_visible()
    expect(
        page.get_by_text("Motivo conservado: Falta la orden de compra", exact=True)
    ).to_be_visible()


def test_isolated_budget_assignment_rejects_invalid_concept_and_advances_valid_document(
    page: Page, browser_server: str
) -> None:
    _mutation_home(page, browser_server)
    page.get_by_label("Concepto").fill("invalid-concept")
    page.get_by_role("button", name="Asignar concepto y enviar", exact=True).click()
    expect(
        page.get_by_text("Resultado: Asignación rechazada", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("Estado: control_presupuestal", exact=True)).to_be_visible()

    _mutation_home(page, browser_server)
    page.get_by_role("button", name="Asignar concepto y enviar", exact=True).click()
    expect(page.get_by_text("Resultado: Concepto asignado", exact=True)).to_be_visible()
    expect(page.get_by_text("Estado: enviado", exact=True)).to_be_visible()
    expect(page.get_by_text("Siguiente cola: Aprobación", exact=True)).to_be_visible()


def test_isolated_payment_cutoff_proof_and_duplicate_guard(
    page: Page, browser_server: str
) -> None:
    _mutation_home(page, browser_server)
    page.get_by_role("button", name="Registrar comprobante y pago", exact=True).click()
    expect(page.get_by_text("Resultado: Pago rechazado", exact=True)).to_be_visible()
    expect(page.get_by_text("Se requiere corte previo", exact=False)).to_be_visible()

    _mutation_home(page, browser_server)
    page.get_by_role("button", name="Cerrar corte", exact=True).click()
    expect(page.get_by_text("Resultado: Corte cerrado", exact=True)).to_be_visible()
    expect(page.get_by_text("Estado: en_proceso_pago", exact=True)).to_be_visible()
    expect(page.get_by_text("no se marcó pagada", exact=False)).to_be_visible()

    page.get_by_role("button", name="Cerrar corte", exact=True).click()
    expect(page.get_by_text("Resultado: Corte rechazado", exact=True)).to_be_visible()


def test_isolated_payment_proof_marks_paid_with_actor_and_cleanup_is_row_scoped(
    page: Page, browser_server: str
) -> None:
    _mutation_home(page, browser_server)
    page.get_by_role("button", name="Cerrar corte", exact=True).click()
    page.set_input_files(
        'input[name="proof"]',
        {
            "name": "comprobante.pdf",
            "mimeType": "application/pdf",
            "buffer": b"browser fixture proof",
        },
    )
    page.get_by_role("button", name="Registrar comprobante y pago", exact=True).click()
    expect(page.get_by_text("Resultado: Pago registrado", exact=True)).to_be_visible()
    expect(page.get_by_text("Estado: pagado", exact=True)).to_be_visible()
    expect(
        page.get_by_text("Actor: Contabilidad Browser UX", exact=False)
    ).to_be_visible()
    expect(
        page.get_by_text(
            "evidencia persistida: comprobante.pdf (21 bytes)", exact=False
        )
    ).to_be_visible()
    expect(page.get_by_text("gasto generado: G-MUT-PAY", exact=False)).to_be_visible()

    _mutation_home(page, browser_server)
    page.get_by_role("button", name="Corregir cuentas de la fila", exact=True).click()
    expect(page.get_by_text("Resultado: Fila corregida", exact=True)).to_be_visible()
    expect(page.get_by_text("Siguiente cola: CFDI", exact=True)).to_be_visible()
    expect(
        page.get_by_text("solo las cuentas; CFDI sigue pendiente", exact=False)
    ).to_be_visible()
