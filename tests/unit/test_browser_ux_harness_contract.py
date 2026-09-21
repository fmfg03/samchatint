from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_browser_test_login_is_isolated_from_production_runtime() -> None:
    browser_app = _read("tests/browser/app.py")
    production_app = _read("copa_telmex_dashboard.py")
    auth_routes = _read("src/devnous/gastos/routes/auth_routes.py")

    assert '@app.get("/_test/login")' in browser_app
    assert "SessionMiddleware" in browser_app
    assert "client_executive_routes.router" in browser_app
    assert "/_test/login" not in production_app
    assert "/_test/login" not in auth_routes


def test_browser_pilot_is_separate_from_the_required_pr_gate() -> None:
    browser_workflow = _read(".github/workflows/browser-ux.yml")
    required_workflow = _read(".github/workflows/test.yml")

    assert "name: Browser UX Pilot" in browser_workflow
    assert "tests/browser" in browser_workflow
    assert "playwright install --with-deps chromium" in browser_workflow
    assert "browser-smoke" not in required_workflow
