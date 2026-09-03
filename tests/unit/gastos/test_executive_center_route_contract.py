from __future__ import annotations

from pathlib import Path


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")


def _executive_center_block() -> str:
    source = ADMIN_ROUTES.read_text()
    return source.split(
        '@router.get("/admin/ejecutivo"',
        maxsplit=1,
    )[1].split(
        '@router.get("/admin/ejecutivo/export.xlsx"',
        maxsplit=1,
    )[0]


def test_admin_routes_exposes_unified_executive_center() -> None:
    source = ADMIN_ROUTES.read_text()

    assert '@router.get("/admin/ejecutivo"' in source
    assert "async def admin_executive_center" in source
    assert '"/admin/ejecutivo", "Ejecutivo", "ejecutivo"' in source


def test_executive_center_links_to_key_surfaces() -> None:
    block = _executive_center_block()

    assert "Centro Ejecutivo" in block
    assert "Asistente Ejecutivo" in block
    assert "/assistant" in block
    assert "/admin/presupuestos" in block
    assert "/admin/finanzas/cashflow" in block
    assert "/admin/finanzas/cuentas-por-cobrar" in block
    assert "/api/assistant/owner-pack/export-preview.html" in block
    assert "Alertas ejecutivas" in block
    assert "Export ejecutivo" in block
    assert "/admin/ejecutivo/export.xlsx" in block
    assert "Descargar export ejecutivo" in block
    assert "render_admin_navigation" in block


def test_executive_center_is_navigation_only_without_mutations() -> None:
    block = _executive_center_block()

    assert "session.execute" not in block
    assert "INSERT " not in block.upper()
    assert "UPDATE " not in block.upper()
    assert "DELETE " not in block.upper()
    assert "@router.post" not in block
    assert "Esta pantalla no modifica datos ni crea solicitudes." in block
    assert "read-only" not in block
