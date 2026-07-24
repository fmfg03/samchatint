from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import admin_routes, user_routes


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = value if isinstance(value, list) else []
    return result


@pytest.mark.asyncio
async def test_employee_registry_checkbox_creates_employee_not_provider():
    employee_id = uuid4()
    linked_employee = SimpleNamespace(
        id=employee_id,
        nombre="BIBIANA RAQUEL ROMAN ARGUELLES",
        activo=True,
    )
    session = AsyncMock()
    session.execute.return_value = _scalar_result(linked_employee)
    session.add = MagicMock()
    request = SimpleNamespace(
        form=AsyncMock(return_value={"activo": "on", "es_empleado": "on"})
    )

    response = await admin_routes.create_proveedor_cliente(
        request=request,
        tipo="proveedor",
        nombre="",
        rfc="RFC-QUE-NO-DEBE-GUARDARSE",
        banco="Banco Demo",
        cuenta_clabe="123456789012345678",
        cuenta_bancaria=None,
        entidad_region="CDMX",
        empleado_id=str(employee_id),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
    )

    assert response.status_code == 200
    created = session.add.call_args.args[0]
    assert created.tipo == "empleado"
    assert created.empleado_id == employee_id
    assert created.nombre == linked_employee.nombre
    assert created.rfc is None


@pytest.mark.asyncio
async def test_exact_employee_accounts_replace_fuzzy_name_matching():
    employee = SimpleNamespace(id=uuid4(), nombre="BIBIANA RAQUEL ROMAN ARGUELLES")
    exact_account = SimpleNamespace(
        id=uuid4(),
        nombre=employee.nombre,
        banco="Banco Demo",
        cuenta_clabe="123456789012345678",
        cuenta_bancaria=None,
    )
    session = AsyncMock()
    session.execute.return_value = _scalar_result([exact_account])

    matches = await user_routes._get_matching_bank_accounts_for_empleado(
        session=session,
        empleado=employee,
    )

    assert matches == [(exact_account, 1.0)]
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_account_matching_remains_as_compatibility_fallback():
    employee = SimpleNamespace(id=uuid4(), nombre="BIBIANA RAQUEL ROMAN ARGUELLES")
    legacy_account = SimpleNamespace(
        id=uuid4(),
        nombre=employee.nombre,
        banco="Banco Demo",
        cuenta_clabe="123456789012345678",
        cuenta_bancaria=None,
    )
    exact_result = _scalar_result([])
    legacy_result = _scalar_result([legacy_account])
    session = AsyncMock()
    session.execute.side_effect = [exact_result, legacy_result]

    matches = await user_routes._get_matching_bank_accounts_for_empleado(
        session=session,
        empleado=employee,
    )

    assert matches == [(legacy_account, 1.0)]
    assert session.execute.await_count == 2
