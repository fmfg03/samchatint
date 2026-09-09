from __future__ import annotations

from pathlib import Path


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")
USER_ROUTES = Path("src/devnous/gastos/routes/user_routes.py")


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


def test_cashflow_route_uses_executive_visible_copy():
    block = _cashflow_route_block()

    assert "Flujo de efectivo ejecutivo" in block
    assert "Finanzas ejecutivas" in block
    assert "Sólo cobranza confirmada cuenta como entrada real" in block
    assert "Cashflow Planning" not in block
    assert "Finance Spine" not in block
    assert "accepted matches" not in block


def test_treasury_cfdi_match_requires_existing_collection_evidence():
    source = USER_ROUTES.read_text()
    accept_block = source.split(
        "async def contabilidad_tesoreria_matches_accept", maxsplit=1
    )[1].split(
        '@router.post("/admin/contabilidad/tesoreria-matches/{movement_id}/undo")',
        maxsplit=1,
    )[0]

    assert "debe estar marcado como cobrado antes de conciliarlo con banco" in accept_block
    assert "must not manufacture that state" in accept_block


def test_treasury_offers_and_validates_already_paid_payment_requests():
    source = USER_ROUTES.read_text()
    assert "Solicitudes pagadas: banco → solicitud" in source
    assert "accept-payment-request" in source
    assert 'Documento.estado == "pagado"' in source
    assert "accept_treasury_payment_request_match" in source
    assert '"undo_treasury_payment_request_match"' in source
    assert '"accept_treasury_payment_request_match"' in source
    assert "La solicitud ya no alcanza el score mínimo" in source
