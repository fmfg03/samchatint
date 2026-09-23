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

    support_upload = page.locator("#archivo_pdf")
    expect(support_upload).to_be_visible()
    core_precedes_support = provider_select.evaluate(
        "(core) => Boolean(core.compareDocumentPosition("
        "document.getElementById('archivo_pdf')) & Node.DOCUMENT_POSITION_FOLLOWING)"
    )
    assert core_precedes_support is True

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


def test_approver_decision_actions_stay_in_view_at_1280(
    page: Page, browser_server: str
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    response = page.goto(f"{browser_server}/documentos/pendientes")
    assert response is not None
    assert response.status == 200

    shell = page.locator(".table-shell").last
    expect(shell).to_be_visible()
    _assert_no_body_overflow(page)
    assert shell.evaluate("(el) => el.scrollWidth > el.clientWidth") is True

    cell = page.locator("td.approval-actions-cell").first
    approve = cell.get_by_role("button", name="Aprobar", exact=True)
    reject = cell.get_by_role("button", name="Rechazar", exact=True)
    expect(approve).to_be_visible()
    expect(reject).to_be_visible()
    assert cell.evaluate("(el) => getComputedStyle(el).position") == "sticky"

    shell_box = shell.bounding_box()
    cell_box = cell.bounding_box()
    assert shell_box is not None
    assert cell_box is not None
    assert cell_box["x"] >= shell_box["x"] - 1
    assert cell_box["x"] + cell_box["width"] <= (
        shell_box["x"] + shell_box["width"] + 1
    )

    before_x = cell_box["x"]
    shell.evaluate("(el) => { el.scrollLeft = el.scrollWidth; }")
    after_box = cell.bounding_box()
    assert after_box is not None
    assert abs(after_box["x"] - before_x) <= 1
    _capture(page, "approver-sticky-decision-actions-1280")


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


def test_budget_control_decision_and_context_stay_visible_at_1280(
    page: Page, browser_server: str
) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    response = page.goto(f"{browser_server}/documentos/control-presupuestal")
    assert response is not None
    assert response.status == 200

    _assert_no_body_overflow(page)
    shell = page.locator(".table-shell").last
    expect(shell).to_be_visible()
    assert shell.evaluate("(el) => el.scrollWidth > el.clientWidth") is True

    row = page.locator("tbody tr").filter(has_text="S-UX-0001").first
    decision = row.locator("td.budget-control-decision-cell")
    amount = row.locator("td.budget-control-amount")
    description = row.locator("td.budget-control-description")
    concept = decision.locator('select[name^="budget_concept_id_"]')
    assign = decision.get_by_role("button", name="Asignar y enviar", exact=True)

    expect(decision).to_be_visible()
    expect(concept).to_be_visible()
    expect(assign).to_be_visible()
    assert decision.evaluate("(el) => getComputedStyle(el).position") == "sticky"

    shell.evaluate("(el) => { el.scrollLeft = el.scrollWidth; }")
    shell_box = shell.bounding_box()
    decision_box = decision.bounding_box()
    amount_box = amount.bounding_box()
    description_box = description.bounding_box()
    assert shell_box is not None
    assert decision_box is not None
    assert amount_box is not None
    assert description_box is not None

    shell_left = shell_box["x"]
    shell_right = shell_box["x"] + shell_box["width"]
    assert decision_box["x"] >= shell_left - 1
    assert decision_box["x"] + decision_box["width"] <= shell_right + 1
    assert decision_box["width"] <= 370
    assert description_box["x"] >= shell_left - 1
    assert description_box["x"] + description_box["width"] <= decision_box["x"] + 1
    assert amount_box["x"] >= shell_left - 1
    assert amount_box["x"] + amount_box["width"] <= description_box["x"] + 1

    _capture(page, "budget-control-sticky-decision-1280")


def test_budget_operator_finds_budget_actual_and_partida_with_context(
    page: Page, browser_server: str
) -> None:
    response = page.goto(f"{browser_server}/admin/presupuestos")
    assert response is not None
    assert response.status == 200

    expect(page.get_by_role("heading", name="Presupuestos 2026")).to_be_visible()
    tournament = page.get_by_role("link", name="Copa Browser UX").first
    expect(tournament).to_be_visible()
    expect(tournament).to_contain_text("Presupuesto autorizado")
    expect(tournament).to_contain_text("$120,000.00")
    expect(tournament).to_contain_text("Ejercido real")
    expect(tournament).to_contain_text("$45,000.00")

    tournament.click()
    expect(page).to_have_url(
        f"{browser_server}/admin/presupuestos/torneo/copa-browser-ux"
        "?edition_year=2026&version_id=92000000-0000-0000-0000-000000000001"
        "&show_committed=1&show_yoy=0"
    )
    expect(page.get_by_role("heading", name="Copa Browser UX")).to_be_visible()
    dashboard = page.locator("#tablero-ejecutivo-presupuesto")
    expect(dashboard).to_be_visible()
    expect(dashboard).to_contain_text("Presupuesto autorizado")
    expect(dashboard).to_contain_text("$120,000.00")
    expect(dashboard).to_contain_text("Ejercido real")
    expect(dashboard).to_contain_text("$45,000.00")
    expect(dashboard).to_contain_text("Comprometido pendiente")
    expect(page.get_by_text("Hospedaje y alimentación", exact=True)).to_be_visible()
    expect(page.get_by_text("Fase regional", exact=True)).to_be_visible()
    _capture(page, "budget-versus-actual")


def test_budget_journey_keeps_wide_partida_inside_scroll_at_mobile(
    page: Page, browser_server: str
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    response = page.goto(
        f"{browser_server}/admin/presupuestos/torneo/copa-browser-ux"
        "?edition_year=2026&version_id=92000000-0000-0000-0000-000000000001"
    )
    assert response is not None
    assert response.status == 200

    _assert_no_body_overflow(page)
    matrix = page.locator(".budget-excel-grid")
    expect(matrix).to_be_visible()
    assert matrix.evaluate("(el) => el.scrollWidth > el.clientWidth") is True
    _capture(page, "budget-versus-actual-mobile")


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
        page.get_by_text("Programa de pagos", exact=True)
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
            "carga varios archivos y revisa la asignación antes de confirmar",
            exact=False,
        )
    ).to_be_visible()

    expect(page.get_by_role("button", name="Cerrar corte", exact=True)).to_be_visible()
    expect(
        page.get_by_role("button", name="Subir comprobante y marcar pagado", exact=True)
    ).to_be_visible()
    expect(
        page.get_by_role(
            "button", name="Cargar testigos seleccionados y pagar", exact=True
        )
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


def test_finance_cxc_keeps_candidate_separate_until_acceptance(
    page: Page, browser_server: str
) -> None:
    response = page.goto(
        f"{browser_server}/admin/finanzas/cuentas-por-cobrar?reset=true"
    )
    assert response is not None
    assert response.status == 200

    expect(page.get_by_text("Cuentas por Cobrar", exact=True).first).to_be_visible()
    expect(
        page.get_by_text(
            "Un CFDI vinculado no prueba pago; solo los matches aceptados cuentan como cobro.",
            exact=False,
        )
    ).to_be_visible()
    expect(page.get_by_text("Evidencia candidata; no prueba cobranza hasta su aceptación.")).to_be_visible()
    expect(page.get_by_text("Cobranza desconocida", exact=True).first).to_be_visible()

    row = page.locator("tbody tr").filter(has_text="cfdi:UX-CXC-001").last
    expect(row.get_by_text("candidate_match", exact=True)).to_be_visible()
    row.locator('select[name="bank_account_id"]').select_option(index=1)
    row.locator('input[name="acceptance_reason"]').fill("RFC y monto verificados")
    row.get_by_role("button", name="Aceptar match", exact=True).click()

    expect(page).to_have_url(f"{browser_server}/admin/finanzas/cuentas-por-cobrar")
    expect(page.get_by_text("accepted_collection_match", exact=True)).to_be_visible()
    operational = page.locator("tbody tr").filter(has_text="UX-CXC-001").first
    expect(operational.get_by_text("Cobrado", exact=True)).to_be_visible()
    _capture(page, "finance-cxc-accepted-match")

    page.locator('input[name="reversal_reason"]').fill(
        "Evidencia bancaria corregida"
    )
    page.get_by_role("button", name="Revertir", exact=True).click()
    expect(page).to_have_url(f"{browser_server}/admin/finanzas/cuentas-por-cobrar")
    expect(page.get_by_text("candidate_match", exact=True)).to_be_visible()
    expect(page.get_by_text("Cobranza desconocida", exact=True).first).to_be_visible()


def test_finance_cxc_exposes_prepolliza_and_accounting_context(
    page: Page, browser_server: str
) -> None:
    response = page.goto(f"{browser_server}/_test/profile/finance")
    assert response is not None
    assert response.status == 200

    entry = page.locator('a[href="/admin/finanzas/cuentas-por-cobrar"]').first
    expect(entry).to_be_visible()
    entry.click()
    expect(page).to_have_url(f"{browser_server}/admin/finanzas/cuentas-por-cobrar")

    prepoliza = page.get_by_role("link", name="Descargar prepólizas CxC", exact=True)
    expect(prepoliza).to_have_attribute(
        "href", "/admin/finanzas/cuentas-por-cobrar/prepolizas-coi.xlsx"
    )
    accounting = page.get_by_role("link", name="Vista contable", exact=True)
    expect(accounting).to_be_visible()
    accounting.click()
    expect(page).to_have_url(
        f"{browser_server}/admin/contabilidad/cuentas-por-cobrar"
    )
    expect(page.get_by_role("heading", name="Vista contable CxC")).to_be_visible()
    expect(
        page.get_by_text("Esta vista no acepta ni revierte matches de cobranza.")
    ).to_be_visible()


def test_finance_cxc_remains_operable_without_mobile_body_overflow(
    page: Page, browser_server: str
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    response = page.goto(
        f"{browser_server}/admin/finanzas/cuentas-por-cobrar?reset=true"
    )
    assert response is not None
    assert response.status == 200

    expect(page.get_by_text("Pre-matching AR", exact=True)).to_be_visible()
    _assert_no_body_overflow(page)
    accept = page.get_by_role("button", name="Aceptar match", exact=True)
    expect(accept).to_be_visible()
    accept.focus()
    assert page.evaluate("() => document.activeElement.textContent") == "Aceptar match"


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
