"""Responsive synthetic ASGI upload-to-policy journey with real HTML forms."""

import socket
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
import uvicorn

from devnous.gastos.routes import admin_amex_statement_routes as routes
from tests.unit.gastos.test_amex_statement_routes import workspace as _workspace

workspace = _workspace


@contextmanager
def live_app(app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Synthetic ASGI browser server failed to start")
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize(
    "page",
    [
        {"name": "desktop", "width": 1440, "height": 1000},
        {"name": "mobile", "width": 390, "height": 844},
    ],
    indirect=True,
)
def test_actual_forms_three_steps_confirmation_and_download(page, workspace):
    async def confirm(*args, **kwargs):
        workspace.view["linked_cut_ids"] = {uuid4()}
        workspace.view["financial_progress"] = {
            "linked_cut_count": 1,
            "selected_source_count": None,
            "selected_generated_count": None,
        }
        return SimpleNamespace(status="created", reason=None)

    routes.confirm_statement_informe.side_effect = confirm
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    with live_app(workspace.app) as base_url:
        page.goto(base_url + routes.BASE + "/nuevo")
        assert page.locator("script").count() == 0
        page.locator('[name="card_account_id"]').select_option(index=1)
        page.locator('[name="employee_id"]').select_option(index=1)
        page.locator('[name="period_start"]').fill("2026-09-01")
        page.locator('[name="period_end"]').fill("2026-09-30")
        page.locator('[name="file"]').set_input_files(
            {
                "name": "synthetic.csv",
                "mimeType": "text/csv",
                "buffer": b"date,amount\n2026-08-31,229.98",
            }
        )
        page.get_by_role("button", name="Cargar y continuar").click()
        page.wait_for_url("**/pendientes?**")
        assert "$229.98 MXN" in page.locator("main").inner_text()
        assert "Pendientes operativos: 1" in page.locator("main").inner_text()
        assert "Banco por vincular: 1" in page.locator("main").inner_text()
        assert page.locator("img").count() == 0
        assert page.locator("script").count() == 0
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_role("link", name="Continuar a pólizas").click()
        page.locator('[name="informe_id"]').select_option(str(workspace.informe.id))
        page.get_by_role("button", name="Preparar informe completo").click()
        page.wait_for_url("**?informe_id=**")
        assert page.locator("#amex-cut-confirmation").count() == 1
        assert page.locator('input[name="expected_versions"]').count() == 1
        assert page.locator(".amex-batch-item").count() == 2
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#amex-batch-treatment").select_option("expense")
        page.locator(".amex-batch-item").nth(1).check()
        page.locator("#amex-apply-batch").click()
        form = page.locator("#amex-cut-confirmation")
        form.locator('[name="confirmed"]').check()
        form.locator('[name="reason"]').fill("Atestación sintética actual")
        form.get_by_role("button", name="Generar una póliza del corte").click()
        page.wait_for_url("**/polizas?msg=**")
        assert "Póliza completa guardada" in page.locator("main").inner_text()
        assert "Pólizas vinculadas: 1" in page.locator("main").inner_text()
        assert "Banco por vincular: 1" in page.locator("main").inner_text()
        assert "no registra importación" in page.locator("main").inner_text()
        with page.expect_download() as downloading:
            page.get_by_role("link", name="CSV", exact=True).click()
        assert downloading.value.suggested_filename == "amex-polizas.csv"
        assert not errors
