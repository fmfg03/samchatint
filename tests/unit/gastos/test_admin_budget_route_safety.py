import re
from pathlib import Path

from devnous.gastos.routes.admin_budget_routes import (
    _render_budget_status_message,
    _select_requested_budget_version,
)
from devnous.gastos.routes.admin_budget_ui import render_budget_matrix_filters


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")
ADMIN_BUDGET_ROUTES = Path("src/devnous/gastos/routes/admin_budget_routes.py")

ALLOWED_ADMIN_ROUTE_BUDGET_PATHS = {
    "/admin/presupuestos-legacy",
    "/admin/presupuestos/import-default",
    "/admin/presupuestos/conceptos/bulk-save",
    "/admin/presupuestos/conceptos/{concept_id}/hide",
    "/admin/presupuestos/conceptos/export.xlsx",
    "/admin/presupuestos/conceptos/import",
    "/admin/presupuestos/versiones/{version_id}/lineas/import",
    "/admin/presupuestos/export.xlsx",
    "/admin/presupuestos/versiones/create",
    "/admin/presupuestos/versiones/{version_id}/transition",
    "/admin/presupuestos/versiones/{version_id}/update",
    "/admin/presupuestos/versiones/{version_id}/lineas/create",
    "/admin/presupuestos/lineas/{line_id}/update",
}


def test_budget_status_message_escapes_query_html() -> None:
    rendered = _render_budget_status_message(
        '<img src=x onerror="alert(1)">',
        is_error=True,
    )

    assert "<img" not in rendered
    assert "&lt;img" in rendered
    assert "&quot;alert(1)&quot;" in rendered


def test_requested_budget_version_must_match_id_and_year() -> None:
    versions = [
        {"id": "draft-2026", "edition_year": 2026, "status": "draft"},
        {"id": "draft-2025", "edition_year": 2025, "status": "draft"},
    ]

    assert _select_requested_budget_version(
        versions,
        requested_version_id="draft-2026",
        edition_year=2026,
    ) == versions[0]
    assert (
        _select_requested_budget_version(
            versions,
            requested_version_id="draft-2025",
            edition_year=2026,
        )
        is None
    )


def test_budget_matrix_filters_preserve_selected_version() -> None:
    rendered = render_budget_matrix_filters(
        tournament_key="torneo-1",
        edition_year=2026,
        version_id='draft-2026"><script>',
        all_versions=[{"edition_year": 2026}],
        phase_options=[],
        visible_count=0,
        total_count=0,
    )

    assert 'name="version_id"' in rendered
    assert 'value="draft-2026&quot;&gt;&lt;script&gt;"' in rendered
    assert "<script>" not in rendered


def test_presupuestos_canonical_routes_are_registered_from_budget_module() -> None:
    admin_source = ADMIN_ROUTES.read_text()
    budget_source = ADMIN_BUDGET_ROUTES.read_text()

    assert "def register_presupuestos_routes(router) -> None:" in budget_source
    assert '@router.get("/admin/presupuestos", response_class=HTMLResponse)' in (
        budget_source
    )
    assert (
        '@router.get(\n        "/admin/presupuestos/torneo/{tournament_key}",'
        in budget_source
    )
    assert "from .admin_budget_routes import register_presupuestos_routes" in (
        admin_source
    )
    assert "register_presupuestos_routes(router)" in admin_source
    assert '@router.get("/admin/presupuestos", response_class=HTMLResponse)' not in (
        admin_source
    )


def test_admin_routes_budget_namespace_is_limited_to_legacy_and_bridge_actions() -> None:
    admin_source = ADMIN_ROUTES.read_text()
    route_paths = {
        match.group(2)
        for match in re.finditer(
            r'@router\.(get|post|put|delete)\("([^"]+)"',
            admin_source,
        )
        if match.group(2).startswith("/admin/presupuestos")
    }

    assert route_paths == ALLOWED_ADMIN_ROUTE_BUDGET_PATHS
    assert "/admin/presupuestos-legacy" in route_paths
    assert "/admin/presupuestos" not in route_paths
    assert "/admin/presupuestos/torneo/{tournament_key}" not in route_paths
