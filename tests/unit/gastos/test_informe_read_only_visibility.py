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


@pytest.mark.asyncio
async def test_existing_expense_owner_access_does_not_require_a_cuenta_lookup():
    owner_id = uuid4()
    owner = SimpleNamespace(id=owner_id, correo="owner@example.com", rol="operaciones")
    expense = SimpleNamespace(empleado_id=owner_id, cuenta_gastos_id=None)
    session = SimpleNamespace(get=AsyncMock())

    assert await user_routes._can_access_read_only_informe_expense(
        session, expense, owner
    )
    session.get.assert_not_awaited()


def test_delegate_can_read_linked_informe_attachment_but_not_a_solicitud():
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    informe = SimpleNamespace(
        empleado_id=uuid4(), tipo="INFORME", cuenta_gastos_id=uuid4()
    )
    solicitud = SimpleNamespace(
        empleado_id=informe.empleado_id,
        tipo="SOLICITUD",
        cuenta_gastos_id=informe.cuenta_gastos_id,
    )

    assert user_routes._can_access_documento_adjunto(informe, delegate)
    assert not user_routes._can_access_documento_adjunto(solicitud, delegate)


@pytest.mark.asyncio
async def test_non_delegate_mutation_guard_leaves_existing_authorization_unchanged():
    owner = SimpleNamespace(id=uuid4(), correo="owner@example.com", rol="operaciones")
    expense = SimpleNamespace(cuenta_gastos_id=None)
    session = SimpleNamespace(get=AsyncMock())

    await user_routes._ensure_can_mutate_informe_expense(session, expense, owner)

    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_can_download_linked_expense_receipt():
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    cuenta = SimpleNamespace(empleado_id=uuid4())
    expense = SimpleNamespace(
        empleado_id=cuenta.empleado_id,
        cuenta_gastos_id=uuid4(),
        archivo_data="aGk=",
        archivo_nombre="recibo.pdf",
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: expense)
        ),
        get=AsyncMock(return_value=cuenta),
    )

    response = await user_routes.descargar_gasto_comprobante(
        uuid4(), session, delegate
    )

    assert response.status_code == 200
    assert response.body == b"hi"


@pytest.mark.asyncio
async def test_delegate_is_blocked_from_unlinked_expense_receipt():
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    expense = SimpleNamespace(
        empleado_id=uuid4(),
        cuenta_gastos_id=None,
        archivo_data="aGk=",
        archivo_nombre="recibo.pdf",
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: expense)
        ),
        get=AsyncMock(),
    )

    with pytest.raises(user_routes.HTTPException) as error:
        await user_routes.descargar_gasto_comprobante(uuid4(), session, delegate)

    assert error.value.status_code == 403
    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_can_download_a_linked_expense_attachment():
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    cuenta = SimpleNamespace(empleado_id=uuid4())
    expense = SimpleNamespace(
        empleado_id=cuenta.empleado_id,
        cuenta_gastos_id=uuid4(),
        archivo_data="aGk=",
        archivo_nombre="recibo.pdf",
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: expense)
        ),
        get=AsyncMock(return_value=cuenta),
    )

    response = await user_routes.descargar_gasto_adjunto(
        uuid4(), user_routes.LEGACY_RECEIPT_KEY, session, delegate
    )

    assert response.status_code == 200
    assert response.body == b"hi"


@pytest.mark.asyncio
async def test_unlinked_expense_is_rejected_before_rendering_detail(monkeypatch):
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    expense = SimpleNamespace(empleado_id=uuid4(), cuenta_gastos_id=None)
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: expense)
        )
    )

    async def no_schema_change(_session):
        return None

    monkeypatch.setattr(user_routes, "_ensure_expense_tip_schema", no_schema_change)

    with pytest.raises(user_routes.HTTPException) as error:
        await user_routes.ver_gasto(
            uuid4(), SimpleNamespace(query_params={}), session, delegate
        )

    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("route_name", ["cancelar_gasto", "editar_gasto_form"])
async def test_delegate_is_rejected_before_expense_mutation_route_work(
    monkeypatch, route_name
):
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    cuenta = SimpleNamespace(empleado_id=uuid4())
    expense = SimpleNamespace(empleado_id=cuenta.empleado_id, cuenta_gastos_id=uuid4())
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: expense)
        ),
        get=AsyncMock(return_value=cuenta),
    )

    async def no_schema_change(_session):
        return None

    monkeypatch.setattr(user_routes, "_ensure_expense_tip_schema", no_schema_change)
    route = getattr(user_routes, route_name)

    with pytest.raises(user_routes.HTTPException) as error:
        await route(uuid4(), SimpleNamespace(), session, delegate)

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_delegate_can_follow_the_final_informe_export_link(monkeypatch):
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    cuenta_id = uuid4()
    cuenta = SimpleNamespace(empleado_id=uuid4())
    informe = SimpleNamespace(id=uuid4())
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: cuenta)
        )
    )

    async def linked_informe(_session, _cuenta_id):
        return informe

    monkeypatch.setattr(user_routes, "_informe_documento_for_cuenta", linked_informe)

    response = await user_routes.exportar_informe_excel_cuenta(
        cuenta_id, session, delegate
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/documentos/{informe.id}/exportar-informe"


@pytest.mark.asyncio
async def test_delegate_cannot_export_a_linked_solicitud_as_an_informe():
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    solicitud = SimpleNamespace(
        empleado_id=uuid4(), tipo="SOLICITUD", cuenta_gastos_id=uuid4()
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: solicitud)
        )
    )

    with pytest.raises(user_routes.HTTPException) as error:
        await user_routes.exportar_informe_gastos(uuid4(), session, delegate)

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_unrelated_employee_cannot_open_another_employees_informe_detail():
    employee = SimpleNamespace(
        id=uuid4(), correo="other@example.com", rol="operaciones"
    )
    cuenta = SimpleNamespace(empleado_id=uuid4())
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: cuenta)
        )
    )

    with pytest.raises(user_routes.HTTPException) as error:
        await user_routes.cuenta_de_gastos_detail(
            uuid4(), SimpleNamespace(), session, employee
        )

    assert error.value.status_code == 403


def test_regular_readers_keep_their_existing_account_access_rules():
    owner_id = uuid4()
    owner = SimpleNamespace(id=owner_id, correo="owner@example.com", rol="operaciones")
    cuenta = SimpleNamespace(empleado_id=owner_id)

    assert user_routes._can_read_cuenta_de_gastos(cuenta, owner)
    assert user_routes._can_mutate_cuenta_de_gastos(cuenta, owner)
    assert not user_routes._can_access_reembolso_cuenta(
        SimpleNamespace(empleado_id=uuid4()), owner
    )


def test_delegate_cannot_settle_or_open_a_cross_account_reimbursement():
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    foreign_cuenta = SimpleNamespace(empleado_id=uuid4())

    assert not user_routes._can_access_reembolso_cuenta(foreign_cuenta, delegate)
    assert not user_routes._can_submit_settlement(
        foreign_cuenta, delegate, "reembolso"
    )
    assert not user_routes._can_submit_settlement(
        foreign_cuenta, delegate, "devolucion"
    )


@pytest.mark.asyncio
async def test_delegate_is_denied_when_expense_is_not_linked_to_a_cuenta():
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    expense = SimpleNamespace(empleado_id=uuid4(), cuenta_gastos_id=None)

    assert not await user_routes._can_access_read_only_informe_expense(
        SimpleNamespace(get=AsyncMock()), expense, delegate
    )


@pytest.mark.asyncio
async def test_delegate_is_denied_when_linked_account_is_missing():
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    expense = SimpleNamespace(empleado_id=uuid4(), cuenta_gastos_id=uuid4())
    session = SimpleNamespace(get=AsyncMock(return_value=None))

    assert not await user_routes._can_access_read_only_informe_expense(
        session, expense, delegate
    )


@pytest.mark.asyncio
async def test_mutation_guard_rejects_delegate_before_a_write():
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    expense = SimpleNamespace(cuenta_gastos_id=uuid4())
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(empleado_id=uuid4()))
    )

    with pytest.raises(user_routes.HTTPException) as error:
        await user_routes._ensure_can_mutate_informe_expense(
            session, expense, delegate
        )

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_cerrar_cuenta_rejects_delegate_before_workflow_mutation():
    delegate = SimpleNamespace(
        id="90701d00-5f0b-4b3d-b677-e491e53caf82",
        correo="azuniga@plataformasports.com",
        rol="operaciones",
    )
    cuenta = SimpleNamespace(id=uuid4(), empleado_id=uuid4())
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: cuenta)
        )
    )

    with pytest.raises(user_routes.HTTPException) as error:
        await user_routes.cerrar_cuenta_de_gastos(
            cuenta.id, SimpleNamespace(), session, delegate
        )

    assert error.value.status_code == 403
