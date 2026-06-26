import inspect

from fastapi.params import Depends

from devnous.gastos.models import Empleado
from devnous.gastos.routes import admin_routes


TARGET_ADMIN_ROUTE_GATES = [
    ("POST", "/admin/torneos/create"),
    ("POST", "/admin/torneos/create/from-torneo"),
    ("POST", "/admin/torneos/link/{tournament_id}"),
    ("GET", "/admin/torneos/edit/{tournament_id}"),
    ("POST", "/admin/torneos/update/{tournament_id}"),
    ("POST", "/admin/torneos/toggle/{tournament_id}"),
    ("POST", "/admin/torneos/delete/{tournament_id}"),
    ("GET", "/admin/gastos/expenses/export"),
    ("GET", "/admin/gastos/invoices/export"),
]

EXPECTED_ADMIN_FINANZAS_ROLES = {
    "admin",
    "finanzas",
    "superadmin",
    "super_admin",
}
EXPECTED_ADMIN_FINANZAS_PERMISSIONS = [
    "admin.finanzas.manage",
    "finanzas.manage",
    "admin.*",
]


def _target_route(method, path):
    matches = [
        route
        for route in admin_routes.router.routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", set()) or set())
    ]
    assert len(matches) == 1
    return matches[0]


def _dependency_closure_values(dependency):
    freevars = dependency.__code__.co_freevars
    values = [cell.cell_contents for cell in dependency.__closure__ or ()]
    return dict(zip(freevars, values))


def test_target_admin_routes_exist_once_with_expected_methods():
    for method, path in TARGET_ADMIN_ROUTE_GATES:
        route = _target_route(method, path)

        assert method in route.methods


def test_target_admin_routes_require_admin_finanzas_dependency():
    for method, path in TARGET_ADMIN_ROUTE_GATES:
        route = _target_route(method, path)
        signature = inspect.signature(route.endpoint)
        current_empleado = signature.parameters.get("current_empleado")

        assert current_empleado is not None, (
            f"{method} {path} missing auth param"
        )
        assert current_empleado.annotation is Empleado
        assert isinstance(current_empleado.default, Depends)

        dependency = current_empleado.default.dependency
        assert dependency.__qualname__ == (
            "require_permission_factory.<locals>.require_permission"
        )
        closure_values = _dependency_closure_values(dependency)
        assert closure_values["role_allow"] == EXPECTED_ADMIN_FINANZAS_ROLES
        assert (
            closure_values["permission_list"]
            == EXPECTED_ADMIN_FINANZAS_PERMISSIONS
        )

        route_dependency_names = {
            dependency.name for dependency in route.dependant.dependencies
        }
        assert "current_empleado" in route_dependency_names
