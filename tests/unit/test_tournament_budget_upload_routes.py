from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from devnous.gastos.routes import admin_budget_upload_routes as routes


@pytest.fixture
def harness(monkeypatch):
    actor = SimpleNamespace(id=str(uuid4()))
    session = SimpleNamespace(rollback=AsyncMock())
    permissions = ["read"]

    async def get_actor():
        return actor

    async def get_session():
        return session

    def guard(_actor, capability):
        if capability not in permissions:
            raise HTTPException(403, "denied")

    router = APIRouter()
    routes.register_tournament_budget_upload_routes(
        router,
        get_db_session=get_session,
        get_current_empleado=get_actor,
        require_budget_access=guard,
    )
    app = FastAPI()
    app.include_router(router)
    preview = AsyncMock(
        return_value={
            "operations": [],
            "blockers": [],
            "plan_sha256": "approved",
            "currency": "MXN",
            "tax_basis": "includes_vat",
        }
    )
    apply = AsyncMock(return_value={"applied": True})
    monkeypatch.setattr(routes, "preview_tournament_upload", preview)
    monkeypatch.setattr(routes, "apply_tournament_upload", apply)
    return TestClient(app), permissions, preview, apply, actor


def send(client, action="preview", **extra):
    return client.post(
        f"/admin/presupuestos/torneo/{uuid4()}/archivo",
        data={
            "version_id": str(uuid4()),
            "edition_year": 2026,
            "action": action,
            **extra,
        },
        files={"archivo_presupuesto": ("budget.xlsx", b"payload")},
    )


def test_readonly_preview_does_not_apply(harness):
    client, _, preview, apply, _ = harness
    response = send(client)
    assert response.status_code == 200
    assert "Vista previa" in response.text
    preview.assert_awaited_once()
    apply.assert_not_awaited()


def test_apply_requires_existing_mutation_authority(harness):
    client, permissions, _, apply, _ = harness
    assert send(client, "apply").status_code == 403
    permissions.append("line_update")
    assert send(client, "apply").status_code == 403
    apply.assert_not_awaited()


def test_apply_uses_authenticated_actor_and_confirmed_hash(harness):
    client, permissions, _, apply, actor = harness
    permissions.extend(["line_update", "create"])
    response = send(
        client,
        "apply",
        confirmed="true",
        approved_plan_sha256="approved",
        currency="mxn",
        tax_basis="includes_vat",
        confirmed_rounding="true",
    )
    assert response.status_code == 200
    assert apply.call_args.kwargs["actor_empleado_id"] == actor.id
    assert apply.call_args.kwargs["approved_plan_sha256"] == "approved"
    assert apply.call_args.kwargs["currency"] == "MXN"
    assert apply.call_args.kwargs["confirmed"] is True


def test_validation_error_is_escaped_and_rolls_back(harness):
    client, _, preview, _, _ = harness
    preview.side_effect = ValueError("<script>bad</script>")
    response = send(client)
    assert response.status_code == 422
    assert "&lt;script&gt;" in response.text
    assert "<script>" not in response.text


def test_unknown_action_and_missing_read_access(harness):
    client, permissions, preview, apply, _ = harness
    assert send(client, "invalid").status_code == 400
    permissions.clear()
    assert send(client).status_code == 403
    preview.assert_not_awaited()
    apply.assert_not_awaited()


def test_form_requires_budget_read_and_exact_uuid(harness):
    client, permissions, _, _, _ = harness
    path = f"/admin/presupuestos/torneo/{uuid4()}/archivo"
    params = {"version_id": str(uuid4()), "edition_year": 2026}
    assert client.get(path, params=params).status_code == 200
    permissions.clear()
    assert client.get(path, params=params).status_code == 403
    assert (
        client.get(
            "/admin/presupuestos/torneo/not-a-uuid/archivo", params=params
        ).status_code
        == 422
    )
