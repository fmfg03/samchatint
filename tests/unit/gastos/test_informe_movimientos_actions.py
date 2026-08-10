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
