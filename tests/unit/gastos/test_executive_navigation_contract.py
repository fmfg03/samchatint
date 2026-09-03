from __future__ import annotations

from pathlib import Path


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")
BUDGET_ROUTES = Path("src/devnous/gastos/routes/admin_budget_routes.py")


def _admin_source() -> str:
    return ADMIN_ROUTES.read_text(encoding="utf-8")


def _budget_source() -> str:
    return BUDGET_ROUTES.read_text(encoding="utf-8")


def _route_block(source: str, route: str, next_route: str) -> str:
    return source.split(route, maxsplit=1)[1].split(next_route, maxsplit=1)[0]


def test_executive_center_is_navigation_home() -> None:
    block = _route_block(
        _admin_source(),
        '@router.get("/admin/ejecutivo"',
        '@router.get("/admin/ejecutivo/export.xlsx"',
    )

    assert "_admin_breadcrumb_html" in block
    assert '("Centro Ejecutivo", None)' in block
    assert "/admin/presupuestos" in block
    assert "/admin/finanzas/cashflow" in block
    assert "/admin/finanzas/cuentas-por-cobrar" in block
    assert "/admin/ejecutivo/alertas" in block
    assert "/admin/ejecutivo/export.xlsx" in block


def test_cashflow_links_back_to_executive_center() -> None:
    block = _route_block(
        _admin_source(),
        '@router.get("/admin/finanzas/cashflow"',
        '@router.get("/admin/finanzas/cuentas-por-cobrar"',
    )

    assert "_admin_breadcrumb_html" in block
    assert '("Centro Ejecutivo", "/admin/ejecutivo")' in block
    assert '("Flujo de efectivo", None)' in block
    assert 'href="/admin/ejecutivo"' in block
    assert "Centro Ejecutivo</a>" in block


def test_accounts_receivable_links_back_to_executive_center() -> None:
    block = _route_block(
        _admin_source(),
        '@router.get("/admin/finanzas/cuentas-por-cobrar"',
        '@router.get("/admin/finanzas/cuentas-por-cobrar/item/',
    )

    assert "_admin_breadcrumb_html" in block
    assert '("Centro Ejecutivo", "/admin/ejecutivo")' in block
    assert '("Finanzas", "/admin/finanzas")' in block
    assert '("Cuentas por Cobrar", None)' in block
    assert 'href="/admin/ejecutivo"' in block
    assert "Centro Ejecutivo</a>" in block


def test_presupuestos_links_back_to_executive_center() -> None:
    source = _budget_source()
    block = _route_block(
        source,
        '@router.get("/admin/presupuestos"',
        '@router.get("/admin/presupuestos/export.xlsx"',
    )

    assert "_admin_breadcrumb_html" in source
    assert '("Centro Ejecutivo", "/admin/ejecutivo")' in block
    assert '("Presupuestos", None)' in block
    assert 'href="/admin/ejecutivo"' in block
    assert "Centro Ejecutivo</a>" in block


def test_executive_navigation_changes_do_not_add_mutation_routes() -> None:
    source = _admin_source() + _budget_source()

    assert '@router.post("/admin/ejecutivo' not in source
    assert '@router.post("/admin/finanzas/cashflow' not in source
