from pathlib import Path


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")


def _artifact_route_block() -> str:
    source = ADMIN_ROUTES.read_text()
    return source.split(
        '@router.get("/admin/artifacts"',
        maxsplit=1,
    )[1].split(
        '@router.get("/admin/finanzas/cashflow"',
        maxsplit=1,
    )[0]


def test_admin_routes_exposes_runtime_artifact_index_route() -> None:
    source = ADMIN_ROUTES.read_text()

    assert '@router.get("/admin/artifacts"' in source
    assert "async def admin_runtime_artifacts" in source
    assert '"/admin/artifacts"' in source
    assert '"artifacts"' in source


def test_artifact_route_uses_runtime_index_and_finance_auth() -> None:
    block = _artifact_route_block()

    assert "require_admin_finanzas()" in block
    assert "build_runtime_artifact_index" in block
    assert "render_runtime_artifact_index_html" in block
    assert "artifact_admin_styles" in block


def test_artifact_route_is_read_only_and_does_not_execute_exports() -> None:
    block = _artifact_route_block()

    assert "INSERT " not in block.upper()
    assert "UPDATE " not in block.upper()
    assert "DELETE " not in block.upper()
    assert "@router.post" not in block
    assert "export_assistant_report" not in block
    assert "admin_finance_platform_export_xlsx" not in block
    assert "assistant_artifacts" in block
