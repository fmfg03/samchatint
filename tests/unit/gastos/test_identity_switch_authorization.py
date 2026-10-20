from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import admin_routes, auth_routes, user_routes


def _employee(role: str, *, impersonator_id=None):
    return SimpleNamespace(
        id=uuid4(),
        nombre="Usuario de prueba",
        correo="usuario@example.invalid",
        rol=role,
        activo=True,
        visible_tool_keys=set(),
        direction_entry_visible=False,
        impersonator_empleado_id=impersonator_id,
        impersonator_nombre="Superadmin original" if impersonator_id else None,
        impersonator_rol="superadmin" if impersonator_id else None,
    )


@pytest.mark.parametrize(
    "role", ["empleado", "coordinador", "finanzas", "admin"]
)
def test_direct_non_superadmin_navigation_hides_identity_switch(
    role: str,
) -> None:
    employee = _employee(role)

    assert 'href="/admin/identidad"' not in user_routes.render_top_navigation(
        employee
    )
    admin_html = admin_routes.render_admin_navigation(employee)
    assert 'href="/admin/identidad"' not in admin_html


@pytest.mark.parametrize("role", ["superadmin", "super_admin"])
def test_superadmin_navigation_exposes_identity_switch(role: str) -> None:
    employee = _employee(role)

    user_html = user_routes.render_top_navigation(employee)
    admin_html = admin_routes.render_admin_navigation(employee)
    assert 'href="/admin/identidad"' in user_html
    assert 'href="/admin/identidad"' in admin_html


def test_impersonated_navigation_marks_superadmin_authority() -> None:
    employee = _employee("empleado", impersonator_id=uuid4())

    for html in (
        user_routes.render_top_navigation(employee),
        admin_routes.render_admin_navigation(employee),
    ):
        assert 'href="/admin/identidad"' in html
        assert "Cambiar identidad (facultad del superadmin)" in html
        assert "Superadmin real: Superadmin original" in html


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["page", "switch"])
@pytest.mark.parametrize(
    "role", ["empleado", "coordinador", "finanzas", "admin"]
)
async def test_identity_switch_routes_reject_direct_non_superadmin(
    monkeypatch, route: str, role: str
) -> None:
    actor_id = uuid4()
    request = SimpleNamespace(session={"empleado_id": str(actor_id)})

    async def load_employee(_session, empleado_id):
        assert str(empleado_id) == str(actor_id)
        return {
            "id": actor_id,
            "nombre": "Administración",
            "correo": "admin@example.invalid",
            "rol": role,
            "activo": True,
        }

    monkeypatch.setattr(auth_routes, "_load_session_employee", load_employee)

    with pytest.raises(HTTPException) as exc_info:
        if route == "page":
            await auth_routes.identity_switch_page(request, session=object())
        else:
            await auth_routes.switch_identity(
                request,
                empleado_id=str(uuid4()),
                session=object(),
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Sólo superadmin puede cambiar identidad."


@pytest.mark.asyncio
async def test_impersonation_uses_active_original_superadmin(
    monkeypatch,
) -> None:
    original_id = uuid4()
    effective_id = uuid4()
    request = SimpleNamespace(
        session={
            "empleado_id": str(effective_id),
            "impersonator_empleado_id": str(original_id),
        }
    )

    async def load_employee(_session, empleado_id):
        assert str(empleado_id) == str(original_id)
        return {
            "id": original_id,
            "nombre": "Superadmin original",
            "correo": "superadmin@example.invalid",
            "rol": "superadmin",
            "activo": True,
        }

    monkeypatch.setattr(auth_routes, "_load_session_employee", load_employee)

    actor = await auth_routes._require_identity_switch_authority(
        request, object()
    )

    assert actor["id"] == original_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "active"),
    [("admin", True), ("superadmin", False)],
)
async def test_impersonation_rejects_invalid_original_actor(
    monkeypatch, role: str, active: bool
) -> None:
    original_id = uuid4()
    request = SimpleNamespace(
        session={
            "empleado_id": str(uuid4()),
            "impersonator_empleado_id": str(original_id),
        }
    )

    async def load_employee(_session, empleado_id):
        assert str(empleado_id) == str(original_id)
        return {
            "id": original_id,
            "nombre": "Actor original",
            "correo": "actor@example.invalid",
            "rol": role,
            "activo": active,
        }

    monkeypatch.setattr(auth_routes, "_load_session_employee", load_employee)

    with pytest.raises(HTTPException) as exc_info:
        await auth_routes._require_identity_switch_authority(request, object())

    assert exc_info.value.status_code == 403
