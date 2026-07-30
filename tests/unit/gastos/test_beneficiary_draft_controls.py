"""Authorization and rendering tests for beneficiaries and empty drafts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import user_routes


def _authorized_requester(**overrides):
    values = {
        "id": UUID(next(iter(user_routes._THIRD_PARTY_EMPLOYEE_REQUESTER_IDS))),
        "nombre": "Alicia",
        "correo": "alicia@example.com",
        "rol": "empleado",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_only_allowlisted_requesters_can_choose_another_employee() -> None:
    assert user_routes._can_request_for_other_employee(_authorized_requester())
    assert not user_routes._can_request_for_other_employee(
        SimpleNamespace(id=uuid4())
    )


def test_named_requesters_can_choose_another_employee_by_corporate_email() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Juan Pablo Lopez",
        correo="JLOPEZ@PLATAFORMASPORTS.COM ",
        rol="empleado",
    )
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana")

    assert user_routes._can_request_for_other_employee(requester)
    assert user_routes._beneficiary_selection_allowed(requester, beneficiary)


def test_named_requesters_can_choose_another_employee_by_canonical_name() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Juan Pablo López Romero",
        correo="juanpablo@example.com",
        rol="empleado",
    )
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana")

    assert user_routes._can_request_for_other_employee(requester)
    assert user_routes._beneficiary_selection_allowed(requester, beneficiary)


def test_named_requesters_can_choose_another_employee_by_short_canonical_name() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Benjamin Jimenez",
        correo="personal@example.com",
        rol="empleado",
    )
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana")

    assert user_routes._can_request_for_other_employee(requester)
    assert user_routes._beneficiary_selection_allowed(requester, beneficiary)


def test_named_requesters_can_choose_another_employee_when_db_name_omits_second_surname() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Juan Pablo Lopez",
        correo="personal@example.com",
        rol="empleado",
    )
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana")

    assert user_routes._can_request_for_other_employee(requester)
    assert user_routes._beneficiary_selection_allowed(requester, beneficiary)


def test_name_matching_is_normalized_but_not_role_based() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Alicia Edith Zúñiga Salazar",
        correo="personal@example.com",
        rol="empleado",
    )
    impostor = SimpleNamespace(
        id=uuid4(),
        nombre="Juan Pablo",
        correo="personal@example.com",
        rol="superadmin",
    )

    assert user_routes._can_request_for_other_employee(requester)
    assert not user_routes._can_request_for_other_employee(impostor)


def test_permissioned_requester_can_choose_another_employee() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Usuario Operativo",
        correo="operativo@example.com",
        rol="empleado",
        permissions={"finance.employee_beneficiary.request"},
    )
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana")

    assert user_routes._can_request_for_other_employee(requester)
    assert user_routes._beneficiary_selection_allowed(requester, beneficiary)


def test_permission_wildcard_requester_can_choose_another_employee() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Usuario Operativo",
        correo="operativo@example.com",
        rol="empleado",
        permissions={"finance.employee_beneficiary.*"},
    )
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana")

    assert user_routes._can_request_for_other_employee(requester)
    assert user_routes._beneficiary_selection_allowed(requester, beneficiary)


def test_unlisted_email_cannot_choose_another_employee_even_with_similar_name() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Juan Pablo",
        correo="juanpablo@example.com",
        rol="superadmin",
    )
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana")

    assert not user_routes._can_request_for_other_employee(requester)
    assert not user_routes._beneficiary_selection_allowed(requester, beneficiary)




@pytest.mark.asyncio
async def test_active_beneficiary_empleados_returns_only_self_for_unlisted_requester() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Usuario Normal",
        correo="normal@example.com",
        rol="empleado",
    )
    session = SimpleNamespace(execute=AsyncMock())

    empleados = await user_routes._active_beneficiary_empleados(session, requester)

    assert empleados == [requester]
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_beneficiary_empleados_loads_all_active_for_authorized_requester() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Juan Pablo Lopez",
        correo="personal@example.com",
        rol="empleado",
    )
    bibiana = SimpleNamespace(id=uuid4(), nombre="Bibiana")

    result = Mock()
    result.scalars.return_value.all.return_value = [requester, bibiana]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    empleados = await user_routes._active_beneficiary_empleados(session, requester)

    assert empleados == [requester, bibiana]
    session.execute.assert_awaited_once()


def test_authorized_requester_sees_explicit_employee_selector() -> None:
    requester = _authorized_requester()
    beneficiary = SimpleNamespace(
        id=uuid4(), nombre="Bibiana Román", correo="bibiana@example.com"
    )

    html = user_routes._beneficiary_employee_control_html(
        requester=requester,
        beneficiary=beneficiary,
        options_html='<option value="one">Alicia</option><option value="two">Bibiana</option>',
        select_id="beneficiario-empleado-id",
        help_text="Seleccione a la persona que recibirá los recursos.",
    )

    assert 'for="beneficiario-empleado-id"' in html
    assert 'name="beneficiario_empleado_id"' in html
    assert '<select' in html
    assert "Alicia" in html and "Bibiana" in html
    assert "Empleado beneficiario" in html


def test_ordinary_requester_is_locked_to_self() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Carlos Solicitante",
        correo="carlos@example.com",
        rol="empleado",
    )

    html = user_routes._beneficiary_employee_control_html(
        requester=requester,
        beneficiary=requester,
        options_html='<option value="other">Otra persona</option>',
        select_id="beneficiario-empleado-id",
        help_text="No debe mostrarse.",
    )

    assert '<input type="hidden" name="beneficiario_empleado_id"' in html
    assert "Carlos Solicitante" in html
    assert "solamente puede solicitar para sí mismo" in html
    assert "<select" not in html
    assert "Otra persona" not in html


def test_solicitud_list_actions_show_cancel_for_owner_draft() -> None:
    owner_id = uuid4()
    documento = SimpleNamespace(
        id=uuid4(),
        tipo="SOLICITUD",
        estado="borrador",
        empleado_id=owner_id,
    )
    actor = SimpleNamespace(id=owner_id, rol="empleado")

    html = user_routes._solicitud_transferencia_list_actions_html(documento, actor)

    assert "Ver detalle" in html
    assert "Cancelar borrador" in html
    assert f'/documentos/{documento.id}/cancelar' in html
    assert 'name="next" value="/gastos-terceros"' in html


def test_solicitud_list_actions_do_not_show_cancel_for_other_user_or_sent() -> None:
    documento = SimpleNamespace(
        id=uuid4(),
        tipo="SOLICITUD",
        estado="enviado",
        empleado_id=uuid4(),
    )
    actor = SimpleNamespace(id=uuid4(), rol="superadmin")

    html = user_routes._solicitud_transferencia_list_actions_html(documento, actor)

    assert "Ver detalle" in html
    assert "Cancelar borrador" not in html
    assert "/cancelar" not in html


def test_empty_draft_cancellation_belongs_to_owner_with_superadmin_recovery() -> None:
    owner_id = uuid4()
    cuenta = SimpleNamespace(empleado_id=owner_id)

    assert user_routes._can_cancel_empty_informe_draft(
        SimpleNamespace(id=owner_id, rol="empleado"), cuenta
    )
    assert not user_routes._can_cancel_empty_informe_draft(
        SimpleNamespace(id=uuid4(), rol="empleado"), cuenta
    )
    assert user_routes._can_cancel_empty_informe_draft(
        SimpleNamespace(id=uuid4(), rol="superadmin"), cuenta
    )


@pytest.mark.parametrize(
    ("overrides", "expected_fragment"),
    [
        ({"cuenta_estado": "cerrada"}, "abierto"),
        ({"informe_estado": "enviado"}, "borrador"),
        ({"expense_count": 1}, "gastos"),
        ({"solicitud_count": 1}, "solicitudes"),
        ({"settlement_count": 1}, "liquidaciones"),
        ({"attachment_count": 1}, "archivos"),
    ],
)
def test_empty_draft_cancellation_rejects_nonempty_or_advanced_reports(
    overrides, expected_fragment
) -> None:
    payload = {
        "cuenta_estado": "abierta",
        "informe_estado": "borrador",
        "expense_count": 0,
        "solicitud_count": 0,
        "settlement_count": 0,
        "attachment_count": 0,
    }
    payload.update(overrides)

    error = user_routes._empty_informe_cancel_error(**payload)

    assert error is not None
    assert expected_fragment in error


def test_empty_draft_cancellation_accepts_pristine_draft() -> None:
    assert (
        user_routes._empty_informe_cancel_error(
            cuenta_estado="abierta",
            informe_estado="borrador",
            expense_count=0,
            solicitud_count=0,
            settlement_count=0,
            attachment_count=0,
        )
        is None
    )


@pytest.mark.asyncio
async def test_cancel_empty_draft_route_rejects_non_owner() -> None:
    cuenta = SimpleNamespace(
        id=uuid4(), empleado_id=uuid4(), estado="abierta"
    )

    class CuentaResult:
        def scalar_one_or_none(self):
            return cuenta

    session = SimpleNamespace(execute=AsyncMock(return_value=CuentaResult()))
    actor = SimpleNamespace(id=uuid4(), rol="empleado")

    with pytest.raises(HTTPException) as exc_info:
        await user_routes.cancelar_informe_vacio_borrador(
            cuenta_id=cuenta.id,
            request=SimpleNamespace(),
            session=session,
            current_empleado=actor,
        )

    assert exc_info.value.status_code == 403
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_cancel_empty_draft_commits_before_best_effort_audit_rollback(
    monkeypatch,
) -> None:
    owner_id = uuid4()
    cuenta = SimpleNamespace(
        id=uuid4(),
        empleado_id=owner_id,
        beneficiario_empleado_id=None,
        estado="abierta",
        closed_at=None,
    )
    informe = SimpleNamespace(
        id=uuid4(),
        cuenta_gastos_id=cuenta.id,
        tipo="INFORME",
        estado="borrador",
        numero_referencia="I-TEST",
    )

    class ScalarResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalar_one(self):
            return self.value

    events = []

    async def commit():
        events.append("business_commit")

    async def rollback():
        events.append("audit_rollback")

    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                ScalarResult(cuenta),
                ScalarResult(informe),
                ScalarResult(0),
                ScalarResult(0),
                ScalarResult(0),
                ScalarResult(0),
            ]
        ),
        add=Mock(),
        commit=AsyncMock(side_effect=commit),
        rollback=AsyncMock(side_effect=rollback),
    )

    async def failing_best_effort_audit(audit_session, **kwargs):
        events.append("audit_attempt")
        assert kwargs["commit"] is True
        await audit_session.rollback()

    monkeypatch.setattr(
        user_routes,
        "record_customer_success_audit_event",
        failing_best_effort_audit,
    )

    response = await user_routes.cancelar_informe_vacio_borrador(
        cuenta_id=cuenta.id,
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=owner_id, rol="empleado"),
    )

    assert response.status_code == 303
    assert events == ["business_commit", "audit_attempt", "audit_rollback"]
    assert cuenta.estado == "cerrada"
    assert informe.estado == "rechazado"
