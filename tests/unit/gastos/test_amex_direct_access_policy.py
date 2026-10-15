"""New AMEX path ownership is narrow and explicit denials still prevail."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services import access_control_service as access


@pytest.mark.parametrize(
    "path",
    [
        "/admin/contabilidad/amex/estados/directo",
        "/admin/contabilidad/amex/estados/directo/nuevo",
        "/admin/contabilidad/amex/estados/directo/previsualizar",
        f"/admin/contabilidad/amex/estados/{uuid4()}/directo",
        f"/admin/contabilidad/amex/estados/{uuid4()}/directo/exportar",
        "/documentos/control-presupuestal/amex-directo",
        f"/documentos/control-presupuestal/amex-directo/{uuid4()}/asignar",
    ],
)
def test_only_new_direct_routes_have_new_capability(path):
    assert access.is_amex_direct_path(path)
    assert access.path_to_tool(path).key == "amex_direct"


@pytest.mark.parametrize(
    "path",
    [
        "/admin/contabilidad/amex/estados",
        "/admin/contabilidad/amex/estados/nuevo",
        f"/admin/contabilidad/amex/estados/{uuid4()}",
        "/admin/gastos/amex/conciliacion",
        "/documentos/control-presupuestal",
        "/documentos/control-presupuestal/amex-directo-extra",
        "/admin/contabilidad/amex/estados/invalid/directo",
    ],
)
def test_existing_and_similar_paths_do_not_acquire_direct_authority(path):
    assert not access.is_amex_direct_path(path)
    tool = access.path_to_tool(path)
    assert tool is None or tool.key != "amex_direct"


@pytest.mark.parametrize("role", ["empleado", "finanzas", "admin", "superadmin"])
def test_registered_capability_never_creates_scope_or_role_grant(role):
    assert not access.default_allows("amex_direct", role)
    assert "asignar_partida" in access.TOOLS_BY_KEY["amex_direct"].actions


@pytest.mark.parametrize(
    "path",
    [
        "/admin/contabilidad/amex/estados/directo/nuevo",
        f"/admin/contabilidad/amex/estados/{uuid4()}/directo/confirmar",
        "/documentos/control-presupuestal/amex-directo",
    ],
)
def test_direct_route_owner_preserves_explicit_denial_checks(path):
    from devnous.gastos.routes import dependencies

    assert dependencies._uses_route_owned_authorization(path)
    assert dependencies._requires_explicit_decision_check(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [None, True, False])
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_middleware_defers_to_owner_only_when_no_persisted_denial(
    monkeypatch,
    decision,
    method,
):
    explicit = AsyncMock(return_value=decision)
    monkeypatch.setattr(access, "explicit_tool_decision", explicit)
    allowed = await access.can_defer_path_authorization_to_route_guard(
        SimpleNamespace(),
        SimpleNamespace(id=uuid4(), activo=True, rol="empleado"),
        "/documentos/control-presupuestal/amex-directo",
        method,
    )
    assert allowed is (decision is not False)
    assert explicit.await_args.args[2:] == (
        "amex_direct",
        "ver" if method == "GET" else "editar",
    )


@pytest.mark.asyncio
async def test_failed_authority_lookup_is_not_a_default_permission(monkeypatch):
    monkeypatch.setattr(
        access,
        "explicit_tool_decision",
        AsyncMock(
            side_effect=access.AccessControlLookupError("unavailable"),
        ),
    )
    with pytest.raises(access.AccessControlLookupError):
        await access.can_defer_path_authorization_to_route_guard(
            SimpleNamespace(),
            SimpleNamespace(id=uuid4(), activo=True),
            "/documentos/control-presupuestal/amex-directo",
            "POST",
        )
