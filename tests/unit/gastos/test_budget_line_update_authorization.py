from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import admin_routes, operations_analytics_routes, user_routes
from samchat.budgets import service as budget_service


def test_only_superadmin_can_modify_budgets() -> None:
    superadmin = SimpleNamespace(
        rol="superadmin", departamento="Operaciones", correo="super@example.com"
    )
    director = SimpleNamespace(
        rol="admin", departamento="Dirección", correo="director@example.com"
    )

    assert admin_routes._budget_access_map(superadmin)["line_update"] is True
    director_access = admin_routes._budget_access_map(director)
    assert director_access["read"] is True
    assert director_access["line_update"] is False
    assert director_access["version_update"] is False
    assert director_access["approve"] is False
    assert director_access["freeze"] is False


def test_only_directors_alicia_and_superadmins_can_view() -> None:
    alicia = SimpleNamespace(
        rol="coordinador",
        departamento="Operaciones",
        correo="azuniga@plataformasports.com",
    )
    bibiana = SimpleNamespace(
        rol="empleado",
        departamento="Operaciones",
        correo="bibiana@example.com",
        permissions={"budgets.read", "budgets.line.update"},
    )

    assert admin_routes._budget_access_map(alicia)["read"] is True
    assert admin_routes._budget_access_map(alicia)["line_update"] is False
    assert admin_routes._budget_access_map(bibiana)["read"] is False
    assert admin_routes._budget_access_map(bibiana)["line_update"] is False


def test_user_without_budget_permission_is_rejected_server_side() -> None:
    employee = SimpleNamespace(
        rol="empleado", departamento="Operaciones", correo="other@example.com"
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_routes._require_budget_access(employee, "line_update")

    assert exc_info.value.status_code == 403


def test_budget_link_is_hidden_from_unauthorized_user() -> None:
    employee = SimpleNamespace(
        nombre="Usuario sin presupuesto",
        rol="empleado",
        departamento="Operaciones",
        correo="other@example.com",
        visible_tool_keys=set(),
    )

    navigation = admin_routes.render_admin_navigation(employee)

    assert "/admin/presupuestos" not in navigation


def test_operations_analytics_budget_api_uses_same_strict_policy() -> None:
    director = SimpleNamespace(
        rol="admin", departamento="Dirección", correo="director@example.com"
    )
    alicia = SimpleNamespace(
        rol="coordinador",
        departamento="Operaciones",
        correo="azuniga@plataformasports.com",
    )
    superadmin = SimpleNamespace(
        rol="superadmin", departamento="Finanzas", correo="super@example.com"
    )

    operations_analytics_routes._require_budget_view(director)
    operations_analytics_routes._require_budget_view(alicia)
    operations_analytics_routes._require_budget_mutation(superadmin)
    with pytest.raises(HTTPException) as exc_info:
        operations_analytics_routes._require_budget_mutation(director)
    assert exc_info.value.status_code == 403


def test_standard_navigation_shows_budget_only_to_allowed_viewers() -> None:
    alicia = SimpleNamespace(
        nombre="Alicia",
        rol="coordinador",
        departamento="Operaciones",
        correo="azuniga@plataformasports.com",
        visible_tool_keys=set(),
    )
    other = SimpleNamespace(
        nombre="Bibiana",
        rol="empleado",
        departamento="Operaciones",
        correo="bibiana@example.com",
        visible_tool_keys=set(),
    )

    assert "/admin/presupuestos" in user_routes.render_top_navigation(alicia)
    assert "/admin/presupuestos" not in user_routes.render_top_navigation(other)


@pytest.mark.asyncio
async def test_monthly_plan_rejects_frozen_version_before_mutation(monkeypatch) -> None:
    async def no_schema(_session):
        return None

    monkeypatch.setattr(budget_service, "ensure_budget_schema", no_schema)

    class MappingResult:
        def mappings(self):
            return self

        def first(self):
            return {
                "budget_version_id": "11111111-1111-1111-1111-111111111111",
                "version_status": "frozen",
            }

    class Session:
        def __init__(self):
            self.calls = 0

        async def execute(self, _statement, _params=None):
            self.calls += 1
            return MappingResult()

    session = Session()
    with pytest.raises(ValueError, match="draft or reforecast"):
        await budget_service.replace_budget_line_monthly_plan(
            session,
            budget_line_id="22222222-2222-2222-2222-222222222222",
            plan={1: {"budget_expense_amount": 100}},
            actor_empleado_id="33333333-3333-3333-3333-333333333333",
        )

    assert session.calls == 1


def test_named_operations_users_cannot_view_or_modify_budgets_even_with_tokens() -> None:
    for nombre, correo in [
        ("Bibiana Raquel Roman Arguelles", "bibiana@example.com"),
        ("Carlos Lozano", "carlos@example.com"),
        ("Roberto Martinez", "roberto@example.com"),
    ]:
        empleado = SimpleNamespace(
            nombre=nombre,
            rol="empleado",
            departamento="Operaciones",
            correo=correo,
            permissions={"budgets.read", "budgets.line.update", "budgets.*"},
        )

        access = admin_routes._budget_access_map(empleado)

        assert access["read"] is False
        assert access["line_update"] is False
        with pytest.raises(HTTPException):
            admin_routes._require_budget_access(empleado, "read")




def test_employee_beneficiary_effective_profile_preview_is_visible() -> None:
    preview = admin_routes._build_effective_profile_preview(
        empleado_role="empleado",
        permission_payloads=[
            {"permissions": ["finance.employee_beneficiary.request"], "scopes": []}
        ],
    )

    assert preview["highlights"]["employee_beneficiary"] is True
    assert "Beneficiarios empleado" in preview["enabled_surfaces"]
    assert preview["highlights"]["budgets"] is False


def test_employee_beneficiary_preset_grants_only_third_party_request_token() -> None:
    preset = admin_routes._PROFILE_PRESETS["solicitudes_beneficiario_empleado"]

    assert preset["base_role"] == "empleado"
    assert preset["permissions"] == ["finance.employee_beneficiary.request"]
    assert not any(token.startswith("budgets") for token in preset["permissions"])
    assert not any(token.startswith("admin") for token in preset["permissions"])


def test_finance_preset_grants_employee_beneficiary_request() -> None:
    preset = admin_routes._PROFILE_PRESETS["finanzas"]

    assert "finance.employee_beneficiary.request" in preset["permissions"]


def test_budget_profile_presets_do_not_grant_mutation_outside_superadmin() -> None:
    assert not any(
        token.startswith("budgets.line.update") or token.startswith("budgets.version.update")
        for token in admin_routes._PROFILE_PRESETS["c_suite"]["permissions"]
    )
    assert not any(
        token.startswith("budgets.")
        for token in admin_routes._PROFILE_PRESETS["finanzas"]["permissions"]
    )
    assert not any(
        token.startswith("budgets.")
        for token in admin_routes._PROFILE_PRESETS["contabilidad"]["permissions"]
    )


def test_employee_beneficiary_access_summary_lists_only_permissioned_users() -> None:
    permitted = {
        "empleado_nombre": "Bibiana Roman",
        "empleado_correo": "bibiana@example.com",
        "empleado_rol": "empleado",
        "profile_names": ["Solicitudes para empleados terceros"],
        "permission_payloads": [
            {"permissions": ["finance.employee_beneficiary.request"], "scopes": []}
        ],
    }
    ordinary = {
        "empleado_nombre": "Usuario Normal",
        "empleado_correo": "normal@example.com",
        "empleado_rol": "empleado",
        "profile_names": ["Lectura"],
        "permission_payloads": [{"permissions": ["finance.solicitudes.read"], "scopes": []}],
    }

    html = admin_routes._render_employee_beneficiary_access_summary(
        {"emp-1": permitted, "emp-2": ordinary}
    )

    assert "Bibiana Roman" in html
    assert "finance.employee_beneficiary.request" in html
    assert "Usuario Normal" not in html


def test_employee_beneficiary_access_summary_explains_empty_state() -> None:
    html = admin_routes._render_employee_beneficiary_access_summary({})

    assert "Ningun perfil activo" in html
    assert "Solicitudes para empleados terceros" in html


@pytest.mark.asyncio
async def test_budget_create_line_post_rejects_non_superadmin_before_mutation(monkeypatch) -> None:
    create_line = AsyncMock()
    monkeypatch.setattr(admin_routes, "create_budget_line", create_line)

    with pytest.raises(HTTPException) as exc_info:
        await admin_routes.admin_presupuestos_create_line(
            version_id=uuid4(),
            concept_name="Hospedaje",
            budget_amount=1000,
            session=SimpleNamespace(),
            current_empleado=SimpleNamespace(
                id=uuid4(),
                rol="admin",
                departamento="Direccion",
                correo="director@example.com",
            ),
        )

    assert exc_info.value.status_code == 403
    create_line.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_update_line_post_rejects_operations_user_before_reading_form(monkeypatch) -> None:
    update_line = AsyncMock()
    request = SimpleNamespace(form=AsyncMock(side_effect=AssertionError("form should not be read")))
    monkeypatch.setattr(admin_routes, "update_budget_line", update_line)

    with pytest.raises(HTTPException) as exc_info:
        await admin_routes.admin_presupuestos_update_line(
            request=request,
            line_id=uuid4(),
            version_id=str(uuid4()),
            session=SimpleNamespace(),
            current_empleado=SimpleNamespace(
                id=uuid4(),
                rol="empleado",
                departamento="Operaciones",
                correo="bibiana@example.com",
                permissions={"budgets.read", "budgets.line.update", "budgets.*"},
            ),
        )

    assert exc_info.value.status_code == 403
    request.form.assert_not_awaited()
    update_line.assert_not_awaited()
