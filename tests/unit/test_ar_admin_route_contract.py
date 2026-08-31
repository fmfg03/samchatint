from __future__ import annotations

from pathlib import Path


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")


def test_admin_routes_exposes_finance_ar_read_only_route():
    source = ADMIN_ROUTES.read_text()

    assert '@router.get("/admin/finanzas/cuentas-por-cobrar"' in source
    assert "async def admin_finance_accounts_receivable" in source
    assert "require_admin_finanzas()" in source


def test_admin_route_consumes_canonical_ar_read_model():
    source = ADMIN_ROUTES.read_text()
    route_body = source.split(
        "async def admin_finance_accounts_receivable",
        maxsplit=1,
    )[1].split(
        '@router.get("/admin/finanzas/export.xlsx"',
        maxsplit=1,
    )[0]

    assert "build_ar_read_model" in route_body
    assert "build_ar_matching_workbench" in route_body
    assert "render_ar_read_model_html" in route_body
    assert "render_ar_matching_workbench_html" in route_body
    assert "INSERT " not in route_body.upper()
    assert "UPDATE " not in route_body.upper()
    assert "DELETE " not in route_body.upper()


def test_admin_ar_route_does_not_run_budget_schema_ddl_on_get():
    source = ADMIN_ROUTES.read_text()
    route_body = source.split(
        "async def admin_finance_accounts_receivable",
        maxsplit=1,
    )[1].split(
        '@router.get("/admin/finanzas/export.xlsx"',
        maxsplit=1,
    )[0]

    assert "ensure_budget_schema(" not in route_body
    assert "resolve_definitive_budget_version(" not in route_body
    assert "ensure_schema=False" in route_body
    assert "resolve_definitive_budget_version_from_versions" in route_body
    assert "build_ar_read_model(" in route_body
    assert "build_ar_matching_workbench(" in route_body


def test_admin_routes_expose_ar_collection_match_posts():
    source = ADMIN_ROUTES.read_text()

    assert (
        '@router.post("/admin/finanzas/cuentas-por-cobrar/matches/accept")'
        in source
    )
    assert (
        '@router.post("/admin/finanzas/cuentas-por-cobrar/matches/{match_id}/reverse")'
        in source
    )
    assert "accept_ar_collection_match" in source
    assert "reverse_ar_collection_match" in source


def test_ar_collection_match_posts_do_not_touch_legacy_bank_state():
    source = ADMIN_ROUTES.read_text()
    block = source.split(
        '@router.post("/admin/finanzas/cuentas-por-cobrar/matches/accept")',
        maxsplit=1,
    )[1].split(
        '@router.get("/admin/finanzas/export.xlsx"',
        maxsplit=1,
    )[0]

    assert "require_admin_finanzas()" in block
    assert "conciliacion_estado =" not in block
    assert "UPDATE bank_movements" not in block


def test_finance_navigation_links_to_ar_route():
    source = ADMIN_ROUTES.read_text()

    assert '"/admin/finanzas/cuentas-por-cobrar"' in source
    assert '"ar_cxc"' in source
