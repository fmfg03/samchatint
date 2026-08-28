from pathlib import Path


def test_movimientos_gasto_rows_offer_view_edit_and_delete_actions():
    source = Path('src/devnous/gastos/routes/user_routes.py').read_text()
    start = source.index('def _movimiento_gasto_actions')
    end = source.index('movimientos_entries: List', start)
    helper = source[start:end]

    assert '/gastos/{exp.id}' in helper
    assert '/gastos/{exp.id}/editar' in helper
    assert '/gastos/{exp.id}/cancelar' in helper
    assert 'return_to' in helper
    assert '/informes-de-gastos/{cuenta.id}' in helper
    assert 'motivo_cancelacion' in helper


def test_cancelar_gasto_supports_safe_return_to_for_contextual_delete():
    source = Path('src/devnous/gastos/routes/user_routes.py').read_text()
    start = source.index('async def cancelar_gasto')
    end = source.index('@router.get("/gastos/{gasto_id}/editar"', start)
    handler = source[start:end]

    assert 'return_to: Optional[str] = Form(None)' in handler
    assert 'safe_return_to.startswith("/informes-de-gastos/")' in handler
    assert 'safe_return_to.startswith("/gastos/")' in handler
    assert 'redirect_url' in handler


def test_editar_gasto_form_offers_contextual_report_return():
    source = Path('src/devnous/gastos/routes/user_routes.py').read_text()
    start = source.index('async def editar_gasto_form')
    end = source.index('@router.post("/gastos/{gasto_id}/editar"', start)
    handler = source[start:end]

    assert 'request.query_params.get("return_to")' in handler
    assert 'linked_informe_url = (' in handler
    assert 'f"/informes-de-gastos/{expense.cuenta_gastos_id}"' in handler
    assert 'report_return_button_html' in handler
    assert 'Volver al informe de gastos' in handler
    assert 'name="return_to"' in handler
    assert 'href="{escape(safe_return_to)}" class="button secondary">Cancelar' in handler


def test_editar_gasto_post_preserves_contextual_report_return():
    source = Path('src/devnous/gastos/routes/user_routes.py').read_text()
    start = source.index('async def editar_gasto')
    end = source.index('async def mis_documentos', start)
    handler = source[start:end]

    assert 'return_to: Optional[str] = Form(None)' in handler
    assert 'safe_return_to = _safe_internal_next(' in handler
    assert 'edit_form_url = _append_query_params(' in handler
    assert '_append_error_params(\n                edit_form_url' in handler
    assert '_append_success_params(\n            safe_return_to' in handler
