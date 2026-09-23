from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import user_routes


def test_alicia_can_read_all_reports_without_receiving_financial_authority():
    alicia = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )

    assert user_routes._can_view_all_cuentas_de_gastos(alicia)
    assert alicia.rol not in {"admin", "finanzas", "superadmin", "super_admin"}


def test_odilon_has_read_only_cross_account_visibility_despite_admin_role():
    odilon = SimpleNamespace(
        id=uuid4(),
        correo="otrujillo@plataformasports.com",
        rol="admin",
    )

    assert user_routes._can_view_all_cuentas_de_gastos(odilon)
    assert user_routes._has_read_only_cross_account_informe_access(odilon)


def test_read_only_delegate_cannot_mutate_someone_elses_report():
    alicia = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
    )
    mike_report = SimpleNamespace(empleado_id=uuid4())

    assert not user_routes._can_mutate_cuenta_de_gastos(mike_report, alicia)


def test_an_unrelated_operations_user_cannot_read_all_reports():
    other = SimpleNamespace(id=uuid4(), correo="other@example.com", rol="operaciones")

    assert not user_routes._can_view_all_cuentas_de_gastos(other)


def test_read_only_delegate_can_open_only_a_linked_informe_document():
    alicia = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    informe = SimpleNamespace(
        empleado_id=uuid4(),
        tipo="INFORME",
        cuenta_gastos_id=uuid4(),
    )
    solicitud = SimpleNamespace(
        empleado_id=informe.empleado_id,
        tipo="SOLICITUD",
        cuenta_gastos_id=informe.cuenta_gastos_id,
    )

    assert user_routes._can_access_read_only_informe_document(informe, alicia)
    assert not user_routes._can_access_read_only_informe_document(solicitud, alicia)


@pytest.mark.asyncio
async def test_read_only_delegate_can_open_linked_expense_evidence():
    alicia = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    cuenta = SimpleNamespace(empleado_id=uuid4())
    expense = SimpleNamespace(
        empleado_id=cuenta.empleado_id,
        cuenta_gastos_id=uuid4(),
    )
    session = SimpleNamespace(get=AsyncMock(return_value=cuenta))

    assert await user_routes._can_access_read_only_informe_expense(
        session, expense, alicia
    )
    session.get.assert_awaited_once()
    assert session.get.await_args.args[1] == expense.cuenta_gastos_id
