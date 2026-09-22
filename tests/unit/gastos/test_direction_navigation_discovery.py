from types import SimpleNamespace

from devnous.gastos.routes import user_routes


def _employee(*, visible_tool_keys, rol="coordinador"):
    return SimpleNamespace(
        nombre="Usuario Dirección",
        rol=rol,
        departamento="Dirección",
        correo="direction-nav@example.invalid",
        visible_tool_keys=set(visible_tool_keys),
    )


def test_direction_effective_tool_is_discoverable_from_primary_navigation():
    employee = _employee(
        visible_tool_keys={"panel.home", "direccion.tableros_ejecutivos"}
    )

    html = user_routes.render_top_navigation(employee)

    assert 'href="/direccion/tableros"' in html
    assert ">Dirección</a>" in html


def test_direction_navigation_is_hidden_without_effective_tool_visibility():
    employee = _employee(visible_tool_keys={"panel.home"})

    html = user_routes.render_top_navigation(employee)

    assert 'href="/direccion/tableros"' not in html


def test_direction_navigation_has_no_role_fallback_for_generic_admin():
    employee = _employee(visible_tool_keys=set(), rol="admin")

    html = user_routes.render_top_navigation(employee)

    assert 'href="/direccion/tableros"' not in html
