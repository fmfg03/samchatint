from pathlib import Path


def test_edit_expense_loads_cuenta_gastos_fase_with_torneo_id():
    source = Path('src/devnous/gastos/routes/user_routes.py').read_text()
    start = source.index('async def editar_gasto_form')
    end = source.index('async def mis_documentos', start)
    edit_flow = source[start:end]

    assert edit_flow.count('undefer(CuentaDeGastos.torneo_id)') >= 4
    assert edit_flow.count('undefer(CuentaDeGastos.fase)') >= 4

    for occurrence in edit_flow.split('undefer(CuentaDeGastos.torneo_id)')[1:]:
        window = occurrence[:180]
        assert 'undefer(CuentaDeGastos.fase)' in window
