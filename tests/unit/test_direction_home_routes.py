"""Session, CSRF and scope revalidation for the executive conversation."""

import json
import re
from unittest.mock import AsyncMock

import pytest
from test_direction_home import scope, snapshot
from test_direction_route_middleware import _direction_client, _employee

from devnous.gastos.routes import client_executive_routes as routes
from samchat.client_executive.conversation import sign_context


@pytest.fixture
def context_client(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-only-direction-context")
    client = _direction_client(monkeypatch, _employee())
    data = snapshot()
    data["source_access"] = {"budget": True, "finance": True}
    monkeypatch.setattr(routes, "build_home", AsyncMock(return_value=(data, scope())))
    monkeypatch.setattr(routes, "resolve_scope", AsyncMock(return_value=scope()))
    monkeypatch.setattr(
        routes, "save_turn", AsyncMock(return_value="test-conversation")
    )
    client.get("/_test/login")
    response = client.get("/direccion/inicio")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    page = json.loads(
        re.search(
            r'<script id="home-data" type="application/json">(.*?)</script>',
            response.text,
        ).group(1)
    )
    return client, page


def ask(client, page, **changes):
    body = {
        "context_token": page["token"],
        "metric_id": "actual",
        "question": "Fuente y definición",
    }
    body.update(changes)
    return client.post(
        "/direccion/tableros/asistente/consulta",
        json=body,
        headers={"X-Direction-CSRF": page["csrf"]},
    )


def test_question_uses_exact_visible_snapshot(context_client):
    client, page = context_client
    response = ask(client, page)
    assert response.status_code == 200
    assert response.json()["snapshot_id"] == page["snapshot"]["snapshot_id"]
    assert response.json()["metric_id"] == "actual"
    assert response.headers["cache-control"] == "no-store"


def test_detached_context_sam_scenario_and_exports_share_exact_receipt(
    context_client, monkeypatch
):
    from samchat.client_executive import conversation

    client, _ = context_client
    monkeypatch.setattr(conversation, "MAX_INLINE_TOKEN", 1)
    response = client.get("/direccion/inicio")
    page = json.loads(
        re.search(
            r'<script id="home-data" type="application/json">(.*?)</script>',
            response.text,
        ).group(1)
    )
    assert len(page["token"]) < 100000 and page["context_receipt"]
    response = ask(
        client,
        page,
        context_receipt=page["context_receipt"],
        scenario={"kind": "expense_reduction", "percent": "10"},
    )
    assert response.status_code == 200
    answer = response.json()
    assert answer["snapshot_id"] == page["snapshot"]["snapshot_id"]
    assert answer["analysis_receipt"] and len(answer["analysis_token"]) < 100000
    body = {key: answer[key] for key in ("analysis_token", "analysis_receipt")}
    body.update(context_token=page["token"], context_receipt=page["context_receipt"])
    for fmt in ("pdf", "xlsx"):
        exported = client.post(
            f"/direccion/reportes/exportar/{fmt}",
            json=body,
            headers={"X-Direction-CSRF": page["csrf"]},
        )
        assert exported.status_code == 200
        assert exported.headers["X-Direction-Snapshot"] == answer["snapshot_id"]
    assert (
        ask(client, page, context_receipt=page["context_receipt"] + " ").status_code
        == 409
    )
    assert ask(client, page).status_code == 409


def test_multi_selection_is_forwarded_and_revalidated_in_sam_and_exports(
    context_client, monkeypatch
):
    client, page = context_client
    tids = page["snapshot"]["tournament_ids"]
    response = client.get(
        "/direccion/inicio", params=[("tournament_ids", tid) for tid in tids]
    )
    assert response.status_code == 200
    assert routes.build_home.await_args.kwargs["tournament_ids"] == tids
    assert client.get("/direccion/inicio?tournament_ids=invalid").status_code == 422
    data = page["snapshot"]
    data["scope"]["tournament_ids"] = tids
    page["token"] = sign_context(data, str(_employee().id))
    assert ask(client, page).status_code == 200
    assert routes.resolve_scope.await_args.kwargs["tournament_ids"] == tids
    narrowed = scope()
    narrowed["selected"] = narrowed["selected"][:1]
    monkeypatch.setattr(routes, "resolve_scope", AsyncMock(return_value=narrowed))
    for fmt in ("pdf", "xlsx"):
        response = client.post(
            f"/direccion/reportes/exportar/{fmt}",
            json={"context_token": page["token"]},
            headers={"X-Direction-CSRF": page["csrf"]},
        )
        assert response.status_code == 409


def test_missing_csrf_and_foreign_actor_rejected(context_client):
    client, page = context_client
    assert (
        client.post(
            "/direccion/tableros/asistente/consulta",
            json={
                "context_token": page["token"],
                "metric_id": "actual",
                "question": "Fuente",
            },
        ).status_code
        == 403
    )
    foreign = sign_context(page["snapshot"], "foreign-actor")
    assert ask(client, page, context_token=foreign).status_code == 409


def test_revoked_tournament_and_domain_permissions_reject_before_persistence(
    context_client, monkeypatch
):
    client, page = context_client
    current = scope()
    current["selected"] = current["selected"][:1]
    monkeypatch.setattr(routes, "resolve_scope", AsyncMock(return_value=current))
    assert ask(client, page).status_code == 409
    routes.save_turn.assert_not_awaited()
    monkeypatch.setattr(routes, "resolve_scope", AsyncMock(return_value=scope()))
    monkeypatch.setattr(
        routes,
        "_direction_source_access",
        AsyncMock(return_value={"budget": True, "finance": False}),
    )
    assert ask(client, page).status_code == 409
    routes.save_turn.assert_not_awaited()


def test_bad_filters_and_missing_signing_configuration_fail_closed(
    context_client, monkeypatch
):
    client, _page = context_client
    assert client.get("/direccion/inicio?portfolio_id=bad").status_code == 422
    assert client.get("/direccion/inicio?edition_year=1900").status_code == 422
    monkeypatch.delenv("SESSION_SECRET_KEY")
    assert client.get("/direccion/inicio").status_code == 503


@pytest.mark.parametrize("role", ["superadmin", "super_admin"])
def test_superadmin_home_and_sam_use_supervision_scope(monkeypatch, role):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-only-direction-context")
    client = _direction_client(
        monkeypatch,
        _employee(role),
        portfolios=lambda supervision: ["portfolio-a"] if supervision else [],
    )
    data = snapshot()
    data["source_access"] = {"budget": True, "finance": True}
    build = AsyncMock(return_value=(data, scope()))
    resolve = AsyncMock(return_value=scope())
    monkeypatch.setattr(routes, "build_home", build)
    monkeypatch.setattr(routes, "resolve_scope", resolve)
    monkeypatch.setattr(routes, "save_turn", AsyncMock(return_value="super-turn"))
    client.get("/_test/login")
    response = client.get("/direccion/inicio")
    assert response.status_code == 200
    assert build.await_args.kwargs["superadmin"] is True
    page = json.loads(
        re.search(
            r'<script id="home-data" type="application/json">(.*?)</script>',
            response.text,
        ).group(1)
    )
    assert ask(client, page).status_code == 200
    assert resolve.await_args.kwargs["superadmin"] is True
    routes.save_turn.assert_awaited_once()


@pytest.mark.parametrize("role", ["superadmin", "super_admin"])
def test_superadmin_home_and_sam_respect_explicit_denial(monkeypatch, role):
    client = _direction_client(
        monkeypatch,
        _employee(role),
        decision=lambda _action: False,
    )
    build = AsyncMock()
    save = AsyncMock()
    monkeypatch.setattr(routes, "build_home", build)
    monkeypatch.setattr(routes, "save_turn", save)
    client.get("/_test/login")
    assert client.get("/direccion/inicio").status_code == 403
    assert ask(client, {"token": "unused", "csrf": "unused"}).status_code == 403
    build.assert_not_awaited()
    save.assert_not_awaited()


def test_legacy_single_context_sam_and_exports_use_one_selector(
    context_client, monkeypatch
):
    client, page = context_client
    data = page["snapshot"]
    tid = data["tournament_ids"][0]
    data["tournament_ids"] = [tid]
    data["scope"].update(tournament_id=tid, tournament_ids=[tid])
    page["token"] = sign_context(data, str(_employee().id))

    async def resolve(*args, **kwargs):
        assert kwargs["tournament_id"] == tid
        assert not kwargs["tournament_ids"]
        result = scope()
        result["selected"] = [t for t in result["selected"] if t["id"] == tid]
        return result

    monkeypatch.setattr(routes, "resolve_scope", resolve)
    assert ask(client, page).status_code == 200
    for fmt in ("pdf", "xlsx"):
        response = client.post(
            f"/direccion/reportes/exportar/{fmt}",
            json={"context_token": page["token"]},
            headers={"X-Direction-CSRF": page["csrf"]},
        )
        assert response.status_code == 200
