from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_admin_finance_workspace_styles_contain_mobile_guards():
    source = _read("src/devnous/gastos/routes/admin_routes.py")
    start = source.index("def _admin_workspace_styles")
    end = source.index("def _admin_money")
    helper = source[start:end]

    assert "min-height:100dvh" in helper
    assert "overflow-x:hidden" in helper
    assert ".table-shell" in helper
    assert "-webkit-overflow-scrolling:touch" in helper
    assert "@media (max-width: 720px)" in helper
    assert "grid-template-columns:1fr !important" in helper
    assert "overflow-wrap:anywhere" in helper


def test_user_finance_workspace_styles_contain_mobile_guards():
    source = _read("src/devnous/gastos/routes/user_routes.py")
    start = source.index("def _workspace_shell_styles")
    end = source.index("def _render_workspace_hero")
    helper = source[start:end]

    assert "min-height:100dvh" in helper
    assert "overflow-x:hidden" in helper
    assert ".table-shell" in helper
    assert "-webkit-overflow-scrolling:touch" in helper
    assert "@media (max-width: 760px)" in helper
    assert "grid-template-columns:1fr !important" in helper
    assert "overflow-wrap:anywhere" in helper


def test_accounting_accounts_legacy_page_wraps_table_for_mobile():
    source = _read("src/devnous/gastos/routes/admin_routes.py")
    start = source.index("async def admin_cuentas_contables")
    end = source.index('@router.post("/admin/cuentas-contables/create"')
    route = source[start:end]

    assert "account-table-shell" in route
    assert "-webkit-overflow-scrolling: touch" in route
    assert "@media (max-width: 720px)" in route
    assert '<div class="account-table-shell">' in route


def test_budget_versions_table_uses_scroll_container():
    source = _read("src/devnous/gastos/routes/admin_budget_routes.py")
    start = source.index("async def admin_presupuestos_dashboard")
    end = source.index('@router.get("/admin/presupuestos/export.xlsx")')
    dashboard = source[start:end]

    assert '<div class="table-shell">' in dashboard
    assert "<th>Versión</th>" in dashboard
