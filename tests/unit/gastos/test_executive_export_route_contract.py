from __future__ import annotations

from pathlib import Path


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")


def _source() -> str:
    return ADMIN_ROUTES.read_text(encoding="utf-8")


def _executive_export_block() -> str:
    source = _source()
    return source.split(
        '@router.get("/admin/ejecutivo/export.xlsx"',
        maxsplit=1,
    )[1].split(
        '@router.get("/admin/ejecutivo/alertas"',
        maxsplit=1,
    )[0]


def _executive_center_block() -> str:
    source = _source()
    return source.split(
        '@router.get("/admin/ejecutivo"',
        maxsplit=1,
    )[1].split(
        '@router.get("/admin/ejecutivo/export.xlsx"',
        maxsplit=1,
    )[0]


def test_admin_routes_exposes_executive_export_route() -> None:
    source = _source()

    assert '@router.get("/admin/ejecutivo/export.xlsx"' in source
    assert "async def admin_executive_export_xlsx" in source
    assert "generate_executive_export_xlsx" in source
    assert "require_admin_finanzas()" in source


def test_executive_center_links_to_export() -> None:
    block = _executive_center_block()

    assert '"title": "Export ejecutivo"' in block
    assert '"href": "/admin/ejecutivo/export.xlsx"' in block
    assert "Descargar export ejecutivo" in block


def test_executive_export_uses_existing_sources() -> None:
    block = _executive_export_block()

    assert "build_finance_source_snapshot" in block
    assert "build_finance_platform_snapshot" in block
    assert "list_budget_versions" in block
    assert "resolve_definitive_budget_version_from_versions" in block
    assert "build_budget_snapshot" in block
    assert "ensure_schema=False" in block
    assert "build_ar_read_model" in block
    assert "build_ar_operational_rows" in block
    assert "build_sam_inbox_payload" in block
    assert "_build_consolidated_executive_alerts" in block
    assert "generate_executive_export_xlsx" in block


def test_executive_export_handles_source_failures_without_mutations() -> None:
    block = _executive_export_block()

    assert "source_notes" in block
    assert "logger.exception" in block
    assert "Finanzas no disponible" in block
    assert "Presupuestos no disponible" in block
    assert "Cuentas por cobrar no disponible" in block
    assert "Sam Inbox Dirección no disponible" in block
    assert "@router.post" not in block
    assert "commit(" not in block
    assert "session.execute" not in block
    assert "INSERT " not in block.upper()
    assert "UPDATE " not in block.upper()
    assert "DELETE " not in block.upper()


def test_executive_export_returns_downloadable_excel() -> None:
    block = _executive_export_block()

    assert "export_ejecutivo_" in block
    assert (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        in block
    )
    assert "Content-Disposition" in block
