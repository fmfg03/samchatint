from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
BROWSER_DIR = ROOT / "tests" / "browser"
ARTIFACT_DIR = Path(
    os.environ.get("SAMCHAT_BROWSER_ARTIFACT_DIR", ROOT / "artifacts" / "browser")
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server_log_tail(log_path: Path, *, max_lines: int = 40) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"<server log unavailable: {exc}>"
    return "\n".join(lines[-max_lines:]) or "<server log empty>"


def _wait_until_ready(
    base_url: str,
    process: subprocess.Popen,
    log_path: Path,
    timeout: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "Browser test server exited early with code "
                f"{process.returncode}.\n--- server log ---\n"
                f"{_server_log_tail(log_path)}"
            )
        try:
            with urllib.request.urlopen(
                f"{base_url}/_test/health", timeout=1.0
            ) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # pragma: no cover - only exercised on startup races
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"Browser test server did not become ready: {last_error}")


@pytest.fixture(scope="session")
def browser_server() -> str:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server_log_path = ARTIFACT_DIR / "server.log"
    server_log = server_log_path.open("w", encoding="utf-8")
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    pythonpath_parts = [str(ROOT / "src"), str(ROOT)]
    if current_pythonpath:
        pythonpath_parts.append(current_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--app-dir",
            str(BROWSER_DIR),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "info",
        ],
        cwd=ROOT,
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        _wait_until_ready(base_url, process, server_log_path)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        server_log.close()


@pytest.fixture(scope="session")
def browser() -> Browser:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


def _artifact_stem(request: pytest.FixtureRequest, viewport: dict[str, int | str]) -> str:
    raw = f"{request.node.name}-{viewport.get('name', 'default')}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-")


@pytest.fixture
def page(request: pytest.FixtureRequest, browser: Browser) -> Page:
    requested = getattr(request, "param", None)
    viewport = (
        dict(requested)
        if isinstance(requested, dict)
        else {"name": "default", "width": 1280, "height": 800}
    )
    context = browser.new_context(
        viewport={
            "width": int(viewport["width"]),
            "height": int(viewport["height"]),
        }
    )
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    browser_page = context.new_page()

    try:
        yield browser_page
    finally:
        report = getattr(request.node, "rep_call", None)
        stem = _artifact_stem(request, viewport)
        if report is not None and report.failed:
            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
            try:
                browser_page.screenshot(
                    path=str(ARTIFACT_DIR / f"{stem}.png"), full_page=True
                )
            except Exception:
                pass
            try:
                context.tracing.stop(path=str(ARTIFACT_DIR / f"{stem}.zip"))
            except Exception:
                pass
        else:
            try:
                context.tracing.stop()
            except Exception:
                pass
        context.close()
