"""A11 technical browser QA only: synthetic fixture, no real Chatty claim."""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "/soporte/batisenal/piloto"
pytestmark = pytest.mark.skipif(
    not os.environ.get("BATISENAL_BROWSER_ENGINE"),
    reason="Run scripts/batisenal/run_isolated_acceptance.py",
)


@pytest.fixture(scope="module")
def pilot_ui_server(tmp_path_factory):
    temporary = tmp_path_factory.mktemp("batisenal-browser")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = dict(
        os.environ, BATISENAL_BROWSER_ORIGIN=origin, PYTHONPATH=str(ROOT / "src")
    )
    with (temporary / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app:app",
                "--app-dir",
                str(ROOT / "tests/browser/fixtures/batisenal"),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError((temporary / "server.log").read_text())
                try:
                    urllib.request.urlopen(
                        origin + "/_test/health", timeout=0.2
                    ).close()
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Technical browser fixture did not start")
            yield origin
        finally:
            process.terminate()
            process.wait(timeout=10)


@pytest.mark.parametrize(
    "viewport", [{"width": 1280, "height": 900}, {"width": 390, "height": 844}]
)
def test_private_panel_revalidation_states_and_keyboard(
    pilot_ui_server, viewport, tmp_path
):
    origin = pilot_ui_server
    output = Path(os.environ.get("SAMCHAT_BROWSER_ARTIFACT_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as runner:
        engine = os.environ.get("BATISENAL_BROWSER_ENGINE", "firefox")
        assert engine in {"firefox", "chromium"}
        assert not os.environ.get("MOZ_DISABLE_CONTENT_SANDBOX")
        # Explicit engine, normal sandbox; never fall back after a launch failure.
        browser = (
            runner.firefox.launch()
            if engine == "firefox"
            else runner.chromium.launch(chromium_sandbox=True)
        )
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        external = []
        page.on(
            "request",
            lambda request: (
                external.append(request.url)
                if not request.url.startswith(origin)
                else None
            ),
        )
        context.request.post(origin + "/_test/state/stored")
        page.goto(origin + PREFIX + "/case")
        expect(
            page.get_by_role("heading", name="Borrador privado guardado")
        ).to_be_visible()
        expect(
            page.get_by_text("Stub sintético · no es Chatty", exact=True)
        ).to_be_visible()
        assert page.locator("img, iframe, input[type=file]").count() == 0
        assert not page.evaluate("Boolean(window.BAD)")
        page.get_by_label("Notas privadas de trabajo · no se envían").fill(
            "Mi nota sin enviar"
        )
        page.get_by_role("button", name="Minimizar", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("button", name="Abrir Batiseñal")).to_be_focused()
        assert page.locator("#bs-content").inner_text() == ""
        page.keyboard.press("Enter")
        expect(page.get_by_role("button", name="Minimizar", exact=True)).to_be_focused()
        expect(
            page.get_by_label("Notas privadas de trabajo · no se envían")
        ).to_have_value("Mi nota sin enviar")
        expect(page.locator("#bs-reference")).to_contain_text("operacion-sintetica-001")
        page.screenshot(
            path=str(output / f'batisenal-private-{viewport["width"]}.png'),
            full_page=True,
        )
        context.request.post(origin + "/_test/state/stale")
        page.get_by_role("button", name="Consultar actualización").click()
        expect(page.locator("#bs-live")).to_contain_text("El contexto cambió")
        context.request.post(origin + "/_test/state/error")
        page.get_by_role("button", name="Consultar actualización").click()
        expect(page.locator("#bs-live")).to_contain_text("Resultado por confirmar")
        expect(page.locator("#bs-notes")).to_have_value("Mi nota sin enviar")
        assert "synthetic internal failure" not in page.content()
        context.request.post(origin + "/_test/state/ended")
        page.get_by_role("button", name="Consultar actualización").click()
        expect(page.locator("#bs-live")).to_contain_text("Acceso terminado")
        expect(page.locator("#bs-notes")).to_have_value("")
        assert "Propuesta sintética" not in page.content()
        assert page.evaluate("localStorage.length + sessionStorage.length") == 0
        assert external == []
        page.screenshot(
            path=str(output / f'batisenal-ended-{viewport["width"]}.png'),
            full_page=True,
        )
        context.close()
        browser.close()
