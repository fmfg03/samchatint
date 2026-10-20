from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import user_routes


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


@pytest.mark.asyncio
async def test_edit_expense_can_render_lock_reason_before_account_serialization(
    monkeypatch,
):
    """Exercise the production failure point before the form's later queries."""

    class ReachedMessageRendering(Exception):
        pass

    expense_id = uuid4()
    employee_id = uuid4()
    document_id = uuid4()
    expense = SimpleNamespace(
        id=expense_id,
        empleado_id=employee_id,
        cuenta_gastos_id=None,
        estado_gasto="activo",
        documento_id=document_id,
        estado_factura="en_proceso",
    )
    document = SimpleNamespace(
        id=document_id,
        estado="aprobado",
        numero_referencia="I-TEST",
    )
    results = iter(
        [
            SimpleNamespace(scalar_one_or_none=lambda: expense),
            SimpleNamespace(scalar_one_or_none=lambda: document),
        ]
    )
    session = SimpleNamespace(execute=AsyncMock(side_effect=lambda *_: next(results)))
    employee = SimpleNamespace(id=employee_id, rol="finanzas")

    async def no_schema_change(_session):
        return None

    async def allow_mutation(_session, _expense, _employee):
        return None

    async def stop_after_lock_reason(*_args):
        raise ReachedMessageRendering

    monkeypatch.setattr(user_routes, "_ensure_expense_tip_schema", no_schema_change)
    monkeypatch.setattr(
        user_routes,
        "_ensure_can_mutate_informe_expense",
        allow_mutation,
    )
    monkeypatch.setattr(user_routes, "_expense_block_message", stop_after_lock_reason)

    with pytest.raises(ReachedMessageRendering):
        await user_routes.editar_gasto_form(
            expense_id,
            SimpleNamespace(query_params={}),
            session,
            employee,
        )
