from __future__ import annotations

from pathlib import Path


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")


def _source() -> str:
    return ADMIN_ROUTES.read_text(encoding="utf-8")


def _executive_alerts_block() -> str:
    source = _source()
    return source.split(
        '@router.get("/admin/ejecutivo/alertas"',
        maxsplit=1,
    )[1].split(
        '@router.get("/admin/gastos/finance-training"',
        maxsplit=1,
    )[0]


def test_admin_routes_exposes_consolidated_executive_alerts_route() -> None:
    source = _source()

    assert '@router.get("/admin/ejecutivo/alertas"' in source
    assert "async def admin_executive_alerts" in source
    assert "Alertas ejecutivas consolidadas" in source
    assert "require_admin_finanzas()" in source


def test_executive_center_links_to_consolidated_alerts() -> None:
    source = _source()
    center_block = source.split(
        '@router.get("/admin/ejecutivo"',
        maxsplit=1,
    )[1].split(
        '@router.get("/admin/ejecutivo/alertas"',
        maxsplit=1,
    )[0]

    assert '"title": "Alertas ejecutivas"' in center_block
    assert '"href": "/admin/ejecutivo/alertas"' in center_block
    assert "/admin/finanzas" not in center_block.split(
        '"title": "Alertas ejecutivas"',
        maxsplit=1,
    )[1].split("},", maxsplit=1)[0]


def test_executive_alerts_route_uses_existing_read_models() -> None:
    block = _executive_alerts_block()

    assert "build_finance_source_snapshot" in block
    assert "build_finance_platform_snapshot" in block
    assert "build_sam_inbox_payload" in block
    assert "_build_consolidated_executive_alerts" in block
    assert "Finance Action Queue" in block
    assert "Payment Run" in block
    assert "Tax Readiness" in block
    assert "Accounting Close" in block


def test_executive_alerts_route_has_kpis_and_prioritized_list() -> None:
    block = _executive_alerts_block()

    assert "Semáforo ejecutivo" in block
    assert "Alertas totales" in block
    assert "Alta prioridad" in block
    assert "Media prioridad" in block
    assert "Pagos pendientes" in block
    assert "DIOT/CFDI bloqueado" in block
    assert "Pólizas descuadradas" in block
    assert "Acciones prioritarias" in block
    assert "Lista priorizada" in block
    assert "Ordenada por severidad" in block


def test_executive_alerts_route_is_read_only_without_mutations() -> None:
    block = _executive_alerts_block()

    assert "@router.post" not in block
    assert "commit(" not in block
    assert "session.execute" not in block
    assert "INSERT " not in block.upper()
    assert "UPDATE " not in block.upper()
    assert "DELETE " not in block.upper()
    assert "Sin mutaciones ni tablas nuevas." in block
    assert "Esta pantalla no ejecuta acciones." in block


def test_executive_alerts_route_surfaces_source_failures() -> None:
    block = _executive_alerts_block()

    assert "source_errors" in block
    assert "logger.exception" in block
    assert "Finanzas no disponible para alertas ejecutivas" in block
    assert "Sam Inbox Dirección no disponible" in block
    assert "Sin alertas ejecutivas activas con las fuentes disponibles" in block
