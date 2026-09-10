from pathlib import Path

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


def test_routes_snapshot_employee_holders_and_resubmission_recomputes_route():
    service = Path(
        "src/devnous/gastos/services/project_authorization_service.py"
    ).read_text()
    workflow = Path(
        "src/devnous/gastos/services/documento_workflow_service.py"
    ).read_text()
    migration = Path(
        "database/migrations/20260909_project_authorization_routes.sql"
    ).read_text()

    assert "eligible_empleado_ids JSONB NOT NULL" in service
    assert "eligible_empleado_ids" in service
    assert "invalidate_document_route" in service
    assert "await invalidate_document_route(session, documento.id)" in workflow
    assert "eligible_empleado_ids JSONB NOT NULL" in migration
    assert "ADD COLUMN IF NOT EXISTS eligible_empleado_ids" in migration
    assert "requires_operations_reference = FALSE" in migration


def test_route_authorization_covers_rejection_and_routed_inbox():
    workflow = Path(
        "src/devnous/gastos/services/documento_workflow_service.py"
    ).read_text()
    telegram = Path(
        "src/devnous/gastos/services/documento_telegram.py"
    ).read_text()
    web = Path("src/devnous/gastos/routes/user_routes.py").read_text()

    reject_block = workflow.split('elif normalized_action == "reject":', 1)[1]
    assert "actor_is_route_approver" in reject_block
    assert "eligible_empleado_ids" in telegram
    assert "eligible_empleado_ids" in web
    assert "NOT EXISTS (SELECT 1 FROM documento_authorization_routes" in telegram
    assert "NOT EXISTS (SELECT 1 FROM documento_authorization_routes" in web
    assert "route_result" in web
