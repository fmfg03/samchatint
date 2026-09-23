from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
USER_ROUTES = ROOT / "src/devnous/gastos/routes/user_routes.py"
ADMIN_ROUTES = ROOT / "src/devnous/gastos/routes/admin_routes.py"
ARTIFACT_UI = ROOT / "src/samchat/artifacts/admin_ui.py"
CASHFLOW_UI = ROOT / "src/samchat/cashflow/admin_ui.py"
EXECUTIVE_UI = ROOT / "src/samchat/client_executive/ui.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_source(path: Path, start_marker: str, end_marker: str) -> str:
    source = _source(path)
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _route_source(path: Path, marker: str) -> str:
    source = _source(path)
    start = source.index(marker)
    end = source.find("\n@router.", start + 1)
    return source[start:] if end == -1 else source[start:end]


def test_shared_workspace_contract_uses_page_scroll_and_keeps_headers_sticky():
    for path, start_marker, end_marker in (
        (USER_ROUTES, "def _workspace_shell_styles", "def _render_workspace_hero"),
        (ADMIN_ROUTES, "def _admin_workspace_styles", "def _render_admin_workspace_hero"),
    ):
        styles = _function_source(path, start_marker, end_marker)
        assert 'layout: str = "reading"' in styles
        assert "layout must be 'reading' or 'data'" in styles
        assert "max-width:{container_max_width}" in styles
        assert "table_shell_style" in styles
        assert "table_shell_table_style" in styles
        assert '"overflow-x:auto;overflow-y:visible;-webkit-overflow-scrolling:touch;"' in styles
        assert "max-block-size:min(68vh, 46rem)" in styles
        assert ".table-shell thead th" in styles
        assert "position:sticky" in styles
        assert "top:0" in styles


def test_shared_action_contract_keeps_labels_complete_and_separated():
    for path, start_marker, end_marker in (
        (USER_ROUTES, "def _workspace_shell_styles", "def _render_workspace_hero"),
        (ADMIN_ROUTES, "def _admin_workspace_styles", "def _render_admin_workspace_hero"),
    ):
        styles = _function_source(path, start_marker, end_marker)
        assert "white-space:nowrap" in styles
        assert "min-inline-size:max-content" in styles
        assert ".table-actions" in styles
        assert "gap:8px" in styles
        assert "min-block-size:44px" in styles
        assert ".table-status" in styles
        assert ".table-actions-cell" in styles
        assert ".table-value-nowrap" in styles


def test_mobile_buttons_can_shrink_but_table_actions_keep_complete_labels():
    for path, start_marker, end_marker in (
        (USER_ROUTES, "def _workspace_shell_styles", "def _render_workspace_hero"),
        (ADMIN_ROUTES, "def _admin_workspace_styles", "def _render_admin_workspace_hero"),
    ):
        styles = _function_source(path, start_marker, end_marker)
        mobile = styles[styles.index("@media (max-width:") :]
        assert "min-inline-size:0" in mobile
        assert ".table-actions-cell .button" in mobile
        assert "min-inline-size:max-content" in mobile


def test_pending_approval_witness_uses_semantic_action_group_and_existing_posts():
    page = _function_source(
        USER_ROUTES,
        '@router.get("/documentos/pendientes"',
        '@router.post("/documentos/pendientes/accion-lote")',
    )
    assert 'class="table-actions"' in page
    assert 'formaction="/documentos/{documento.id}/aprobar"' in page
    assert 'formaction="/documentos/{documento.id}/rechazar"' in page
    assert 'action="/documentos/pendientes/accion-lote"' in page
    assert 'value="approve"' in page
    assert 'value="reject"' in page


def test_pending_approval_keeps_decision_column_sticky():
    page = _function_source(
        USER_ROUTES,
        '@router.get("/documentos/pendientes"',
        '@router.post("/documentos/pendientes/accion-lote")',
    )
    assert 'class="approval-queue-table"' in page
    assert page.count('class="approval-actions-cell"') >= 2
    assert ".approval-queue-table .approval-actions-cell" in page
    assert "position: sticky;" in page
    assert "right: 0;" in page
    assert ".approval-queue-table thead .approval-actions-cell" in page
    assert "background: #0f172a;" in page
    assert "color: #f8fafc;" in page


def test_budget_control_keeps_compact_decision_column_sticky():
    page = _function_source(
        USER_ROUTES,
        "async def documentos_control_presupuestal(",
        "async def _apply_control_presupuestal_assignment(",
    )
    assert 'class="budget-control-table"' in page
    assert page.count('class="budget-control-decision-cell"') >= 2
    assert ".budget-control-table .budget-control-decision-cell" in page
    assert "position: sticky;" in page
    assert "right: 0;" in page
    assert ".budget-control-table thead .budget-control-decision-cell" in page
    assert "background: #0f172a;" in page
    assert "color: #f8fafc;" in page
    assert 'class="budget-control-decision"' in page
    assert "min-width: 320px;" in page
    assert "min-width:470px" not in page


def test_admin_fragments_adopt_shared_table_shell():
    artifact = _source(ARTIFACT_UI)
    cashflow = _source(CASHFLOW_UI)
    assert artifact.count('<div class="table-shell"><table class="artifact-table">') == 4
    assert '<div class="table-shell"><table class="cashflow-table">' in cashflow


def test_finance_command_center_tables_adopt_shared_table_shell():
    finance = _function_source(
        ADMIN_ROUTES,
        '@router.get("/admin/finanzas", response_class=HTMLResponse)',
        '@router.get("/admin/finanzas/export.xlsx", response_class=Response)',
    )
    assert finance.count('<div class="table-shell"') == 6
    assert finance.count('<table class="finance-table"') == 6


def test_informe_and_solicitud_summaries_mark_non_wrapping_columns():
    source = _source(USER_ROUTES)
    solicitudes = source[source.index("<h2>Resumen de solicitudes</h2>") :]
    solicitudes = solicitudes[: solicitudes.index("</html>")]
    informes = source[source.index("<h2>Resumen por informe</h2>") :]
    informes = informes[: informes.index("</html>")]
    for page in (solicitudes, informes):
        assert 'class="table-status"' in page
        assert 'class="table-actions-cell"' in page
    assert informes.count('class="table-value-nowrap"') >= 5


def test_direction_domain_shell_has_same_scroll_contract():
    executive = _source(EXECUTIVE_UI)
    assert ".table-wrap thead th" in executive
    assert "position:sticky" in executive
    assert "width:calc(100% - 40px)" in executive
    assert "max-block-size:min(68vh, 46rem)" not in executive
    assert "overflow-x:auto" in executive


def test_operational_data_surfaces_opt_into_full_width_layout():
    user_source = _source(USER_ROUTES)
    admin_source = _source(ADMIN_ROUTES)
    for marker in (
        "async def panel_operaciones_console",
        "async def gastos_terceros",
        "async def documentos_control_presupuestal",
        "async def documentos_pendientes",
        "async def documentos_todos",
        "async def cuentas_de_gastos_list",
    ):
        assert 'layout="data"' in _route_source(USER_ROUTES, marker)
    for marker in (
        "async def admin_expenses",
        "async def admin_finance_platform",
        "async def admin_finance_payment_run",
        "async def gastos_sin_cuenta_contable",
    ):
        assert 'layout="data"' in _route_source(ADMIN_ROUTES, marker)


def test_canonical_budget_routes_opt_into_full_width_layout():
    source = _source(ROOT / "src/devnous/gastos/routes/admin_budget_routes.py")
    assert source.count('_admin_workspace_styles("1380px", layout="data")') == 1
    assert source.count('_admin_workspace_styles("1400px", layout="data")') == 1


def test_reading_layout_preserves_legacy_table_viewport_contract():
    for path, start_marker, end_marker in (
        (USER_ROUTES, "def _workspace_shell_styles", "def _render_workspace_hero"),
        (ADMIN_ROUTES, "def _admin_workspace_styles", "def _render_admin_workspace_hero"),
    ):
        styles = _function_source(path, start_marker, end_marker)
        assert '"overflow:auto;max-block-size:min(68vh, 46rem);' in styles
        assert '"min-width:max-content;"' in styles


def test_standalone_data_pages_override_the_injected_container_cap():
    source = _source(USER_ROUTES)
    override = (
        "body .container {{ max-width:none !important; width:100% !important; "
        "margin:0 !important; }}"
    )
    assert source.count(override) == 3


def test_operations_navigation_remains_horizontal_without_sidebar_markup():
    block = _function_source(
        USER_ROUTES,
        "async def panel_operaciones_console",
        '@router.get("/panel", response_class=HTMLResponse)',
    )
    assert 'render_top_navigation(current_empleado, "operacion")' in block
    assert 'class="ops-sidebar"' not in block
    assert 'class="sam-layout-data"' in block
