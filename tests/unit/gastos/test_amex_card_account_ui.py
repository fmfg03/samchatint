from pathlib import Path


def test_amex_card_account_catalog_route_is_exposed():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert '"/admin/gastos/amex/tarjetas"' in source
    assert "Catálogo tarjetas AMEX" in source
    assert "list_amex_liability_account_options" in source
    assert "Catálogo tarjetas" in source


def test_amex_cfdi_matching_surface_is_exposed():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert '"/admin/gastos/amex/conciliacion/vincular-cfdi"' in source
    assert "AMEX vs CFDI descargados" in source
    assert "suggest_amex_cfdi_matches" in source
    assert "validate_amex_cfdi_suggestion" in source


def test_amex_pase_monthly_matching_surface_is_exposed():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert '"/admin/gastos/amex/conciliacion/vincular-pase-mensual"' in source
    assert "PASE mensual consolidado" in source
    assert "suggest_pase_monthly_cfdi_matches" in source
    assert "validate_pase_monthly_cfdi_suggestion" in source
