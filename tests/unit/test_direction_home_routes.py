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
