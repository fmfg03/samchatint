from devnous.gastos.services.project_authorization_service import (
    DIRECCION_ADMINISTRACION_FINANZAS,
    DIRECCION_GENERAL,
    DIRECTOR_OPERACIONES,
    ProjectAuthorizationRoute,
    resolve_project_authorization_route,
)


def test_beneficiary_position_exception_overrides_project_route():
    result = resolve_project_authorization_route(
        beneficiary_position_keys=(DIRECTOR_OPERACIONES,),
        project_rule=ProjectAuthorizationRoute(
            (DIRECTOR_OPERACIONES,), True, "project"
        ),
    )
    assert result is not None
    assert result.eligible_position_keys == (
        DIRECCION_GENERAL,
        DIRECCION_ADMINISTRACION_FINANZAS,
    )
    assert result.requires_operations_reference is False


def test_project_route_is_preserved_when_beneficiary_is_not_exception():
    route = ProjectAuthorizationRoute((DIRECTOR_OPERACIONES,), True, "project")
    assert (
        resolve_project_authorization_route(
            beneficiary_position_keys=(), project_rule=route
        )
        == route
    )
