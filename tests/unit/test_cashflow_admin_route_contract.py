from __future__ import annotations

from pathlib import Path


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")


def _cashflow_route_block() -> str:
    source = ADMIN_ROUTES.read_text()
    return source.split(
        '@router.get("/admin/finanzas/cashflow"',
        maxsplit=1,
    )[1].split(
        '@router.get("/admin/finanzas/cuentas-por-cobrar"',
        maxsplit=1,
    )[0]


def test_admin_routes_exposes_cashflow_read_only_route():
    source = ADMIN_ROUTES.read_text()

    assert '@router.get("/admin/finanzas/cashflow"' in source
    assert "async def admin_finance_cashflow" in source
    assert '"/admin/finanzas/cashflow"' in source
    assert '"cashflow"' in source


def test_cashflow_route_uses_canonical_read_model_and_finance_auth():
    block = _cashflow_route_block()

    assert "require_admin_finanzas()" in block
    assert "build_cashflow_planning_read_model" in block
    assert "render_cashflow_planning_html" in block


def test_cashflow_route_is_read_only_and_not_legacy_sourced():
    block = _cashflow_route_block()

    assert "INSERT " not in block.upper()
    assert "UPDATE " not in block.upper()
    assert "DELETE " not in block.upper()
    assert "/admin/contabilidad/cash-flow" not in block
