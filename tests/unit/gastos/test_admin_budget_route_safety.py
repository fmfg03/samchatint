import re
from pathlib import Path

from devnous.gastos.routes.admin_budget_routes import (
    _render_budget_status_message,
    _select_requested_budget_version,
)
from devnous.gastos.routes.admin_budget_ui import render_budget_matrix_filters


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")
ADMIN_BUDGET_ROUTES = Path("src/devnous/gastos/routes/admin_budget_routes.py")
ADMIN_BUDGET_UI = Path("src/devnous/gastos/routes/admin_budget_ui.py")
FINANCE_READ_ADAPTER = Path("src/samchat/assistant/finance_read_adapter.py")
RUNTIME_ARTIFACT_INDEX = Path("src/samchat/artifacts/runtime_index.py")
SUPPORT_ROUTES = Path("src/devnous/gastos/routes/support_routes.py")
OPERATIONS_ANALYTICS_ROUTES = Path(
    "src/devnous/gastos/routes/operations_analytics_routes.py"
)
USER_ROUTES = Path("src/devnous/gastos/routes/user_routes.py")
ROUTE_DECORATOR_PATTERN = re.compile(
    r'@router\.(get|post|put|delete)\(\s*"([^"]+)"',
    re.MULTILINE,
)
VISIBLE_ROUTE_TARGET_PATTERN = re.compile(
    r'(?:href|action)=["\']([^"\']*/admin/presupuestos[^"\']*)["\']'
)

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

ADMIN_ROUTE_BUDGET_POLICY = {
    "/admin/presupuestos-legacy": "legacy_reference",
    "/admin/presupuestos/import-default": "candidate_remove_later",
    "/admin/presupuestos/conceptos/bulk-save": "bridge_required_by_canonical_ui",
    "/admin/presupuestos/conceptos/{concept_id}/hide": (
        "bridge_required_by_canonical_ui"
    ),
    "/admin/presupuestos/conceptos/export.xlsx": (
        "bridge_required_by_canonical_ui"
    ),
    "/admin/presupuestos/conceptos/import": "bridge_required_by_canonical_ui",
    "/admin/presupuestos/versiones/{version_id}/lineas/import": (
        "candidate_remove_later"
    ),
    "/admin/presupuestos/export.xlsx": "bridge_external_dependency",
    "/admin/presupuestos/versiones/create": "bridge_required_by_canonical_ui",
    "/admin/presupuestos/versiones/{version_id}/transition": (
        "bridge_required_by_canonical_ui"
    ),
    "/admin/presupuestos/versiones/{version_id}/update": (
        "bridge_required_by_canonical_ui"
    ),
    "/admin/presupuestos/versiones/{version_id}/lineas/create": (
        "bridge_required_by_canonical_ui"
    ),
    "/admin/presupuestos/lineas/{line_id}/update": (
        "bridge_required_by_canonical_ui"
    ),
}

CANONICAL_BUDGET_ROUTE_PATHS = {
    "/admin/presupuestos",
    "/admin/presupuestos/torneo/{tournament_key}",
    "/admin/presupuestos/torneo/{tournament_key}/ingresos/import",
    "/admin/presupuestos/torneo/{tournament_key}/ingresos/export.xlsx",
    "/admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos",
    "/admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/link",
    "/admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/upload-link",
    "/admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/{link_id}/unlink",
    "/admin/presupuestos/versiones/copy-forward",
}

CANONICAL_UI_BRIDGE_TARGET_SNIPPETS = {
    "/admin/presupuestos/conceptos/{concept_id}/hide": (
        "/admin/presupuestos/conceptos/{escape_html(concept_id, quote=True)}/hide",
    ),
    "/admin/presupuestos/conceptos/bulk-save": (
        "/admin/presupuestos/conceptos/bulk-save",
    ),
    "/admin/presupuestos/conceptos/export.xlsx": (
        "/admin/presupuestos/conceptos/export.xlsx",
    ),
    "/admin/presupuestos/conceptos/import": (
        "/admin/presupuestos/conceptos/import",
    ),
    "/admin/presupuestos/versiones/{version_id}/transition": (
        '/admin/presupuestos/versiones/{row.get("id")}/transition',
    ),
    "/admin/presupuestos/versiones/create": (
        "/admin/presupuestos/versiones/create",
    ),
    "/admin/presupuestos/versiones/{version_id}/update": (
        '/admin/presupuestos/versiones/{selected_version["id"]}/update',
    ),
    "/admin/presupuestos/versiones/{version_id}/lineas/create": (
        "/admin/presupuestos/versiones/{escape(str(version_id))}/lineas/create",
    ),
    "/admin/presupuestos/lineas/{line_id}/update": (
        "/admin/presupuestos/lineas/{escape(line_id)}/update",
    ),
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


def test_presupuestos_canonical_route_inventory_is_owned_by_budget_module() -> None:
    budget_source = ADMIN_BUDGET_ROUTES.read_text()
    canonical_routes = {
        match.group(2)
        for match in ROUTE_DECORATOR_PATTERN.finditer(budget_source)
        if match.group(2).startswith("/admin/presupuestos")
    }

    assert canonical_routes == CANONICAL_BUDGET_ROUTE_PATHS


def test_admin_routes_budget_namespace_is_limited_to_legacy_and_bridge_actions() -> None:
    admin_source = ADMIN_ROUTES.read_text()
    route_paths = {
        match.group(2)
        for match in ROUTE_DECORATOR_PATTERN.finditer(admin_source)
        if match.group(2).startswith("/admin/presupuestos")
    }

    assert route_paths == ALLOWED_ADMIN_ROUTE_BUDGET_PATHS
    assert "/admin/presupuestos-legacy" in route_paths
    assert "/admin/presupuestos" not in route_paths
    assert "/admin/presupuestos/torneo/{tournament_key}" not in route_paths


def test_admin_routes_budget_namespace_has_explicit_policy_classification() -> None:
    assert set(ADMIN_ROUTE_BUDGET_POLICY) == ALLOWED_ADMIN_ROUTE_BUDGET_PATHS
    assert ADMIN_ROUTE_BUDGET_POLICY["/admin/presupuestos-legacy"] == (
        "legacy_reference"
    )
    assert set(ADMIN_ROUTE_BUDGET_POLICY.values()) == {
        "bridge_required_by_canonical_ui",
        "bridge_external_dependency",
        "candidate_remove_later",
        "legacy_reference",
    }
    assert "canonical_owner" not in set(ADMIN_ROUTE_BUDGET_POLICY.values())


def test_visible_budget_entrypoints_do_not_link_to_legacy_route() -> None:
    route_sources = "\n".join(
        path.read_text()
        for path in (
            ADMIN_ROUTES,
            ADMIN_BUDGET_ROUTES,
            SUPPORT_ROUTES,
            OPERATIONS_ANALYTICS_ROUTES,
            USER_ROUTES,
        )
    )
    visible_targets = {
        match.group(1) for match in VISIBLE_ROUTE_TARGET_PATTERN.finditer(route_sources)
    }

    assert "/admin/presupuestos" in route_sources
    assert not any("/admin/presupuestos-legacy" in target for target in visible_targets)


def test_canonical_budget_ui_bridge_targets_are_classified() -> None:
    canonical_ui_source = "\n".join(
        [ADMIN_BUDGET_ROUTES.read_text(), ADMIN_BUDGET_UI.read_text()]
    )
    bridge_routes = {
        route
        for route, policy in ADMIN_ROUTE_BUDGET_POLICY.items()
        if policy == "bridge_required_by_canonical_ui"
    }

    assert set(CANONICAL_UI_BRIDGE_TARGET_SNIPPETS) == bridge_routes
    for snippets in CANONICAL_UI_BRIDGE_TARGET_SNIPPETS.values():
        assert any(snippet in canonical_ui_source for snippet in snippets)
    assert "/admin/presupuestos-legacy" not in canonical_ui_source


def test_candidate_remove_later_budget_routes_are_not_canonical_ui_targets() -> None:
    canonical_ui_source = "\n".join(
        [ADMIN_BUDGET_ROUTES.read_text(), ADMIN_BUDGET_UI.read_text()]
    )
    candidate_remove_later = {
        route
        for route, policy in ADMIN_ROUTE_BUDGET_POLICY.items()
        if policy == "candidate_remove_later"
    }

    assert candidate_remove_later == {
        "/admin/presupuestos/import-default",
        "/admin/presupuestos/versiones/{version_id}/lineas/import",
    }
    assert "/admin/presupuestos/import-default" not in canonical_ui_source
    assert "lineas/import" not in canonical_ui_source


def test_external_budget_export_dependencies_are_not_marked_removable() -> None:
    external_sources = "\n".join(
        [FINANCE_READ_ADAPTER.read_text(), RUNTIME_ARTIFACT_INDEX.read_text()]
    )
    external_dependency_routes = {
        route
        for route, policy in ADMIN_ROUTE_BUDGET_POLICY.items()
        if policy == "bridge_external_dependency"
    }

    assert external_dependency_routes == {"/admin/presupuestos/export.xlsx"}
    for route in external_dependency_routes:
        assert route in external_sources
        assert ADMIN_ROUTE_BUDGET_POLICY[route] != "candidate_remove_later"
