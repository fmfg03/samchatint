from pathlib import Path


def test_amex_card_account_catalog_route_is_exposed():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert '"/admin/gastos/amex/tarjetas"' in source
    assert "Catálogo tarjetas AMEX" in source
    assert "list_amex_liability_account_options" in source
    assert "Catálogo tarjetas" in source


def test_amex_reconciliation_has_finance_breadcrumb_context():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()
    start = source.index("async def amex_conciliacion_view")
    end = source.index(
        '@router.get("/admin/contabilidad/cuentas-por-cobrar"',
        start,
    )
    block = source[start:end]

    assert 'render_top_navigation(current_empleado, "finanzas")' in block
    assert "_gastos_breadcrumb_html([" in block
    assert '("Finanzas", "/admin/gastos")' in block
    assert '("Conciliación AMEX", None)' in block


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


def test_amex_p1218_fee_interest_bulk_action_is_exposed():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert "p1218_fee_interest" in source
    assert "Clasificar comisión/interés AMEX → P1218" in source
    assert "apply_amex_fee_interest_p1218" in source


def test_amex_reconciliation_validation_notification_surface_is_exposed():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert "/admin/gastos/amex/conciliacion/validar-notificar" in source
    assert "Validar y notificar" in source
    assert "notify_amex_reconciliation_validated" in source


def test_amex_card_payment_run_surface_is_exposed():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert "/admin/gastos/amex/conciliacion/programar-pago" in source
    assert "Programar pago de AMEX" in source
    assert "Enviar a Payment Run" in source
    assert "create_amex_card_payment_request" in source
    assert "list_amex_card_accounts(session)" in source


def test_amex_bulk_cfdi_linking_surface_keeps_user_in_suggestions():
    source = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    assert '"/admin/gastos/amex/conciliacion/vincular-cfdi-masivo"' in source
    assert 'id="amex-bulk-link-form"' in source
    assert 'name="selected_links"' in source
    assert 'form="amex-bulk-link-form"' in source
    assert 'id="sugerencias"' in source
    assert 'redirect_anchor = "#sugerencias"' in source
    assert 'Vincular seleccionados' in source
