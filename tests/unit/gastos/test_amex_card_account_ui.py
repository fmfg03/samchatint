from pathlib import Path


def test_amex_card_account_catalog_route_is_exposed():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert '"/admin/gastos/amex/tarjetas"' in source
    assert "Catálogo tarjetas AMEX" in source
    assert "list_amex_liability_account_options" in source
    assert "Catálogo tarjetas" in source
