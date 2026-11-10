"""Private panel HTTP rendering and origin/error regressions (technical fixtures)."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import batisenal_pilot_routes as routes


class Pilot:
    enabled = True
    pilot = SimpleNamespace(
        case_id="00000000-0000-0000-0000-000000000001", source_kind="synthetic_stub"
    )
    flow = SimpleNamespace(
        config=SimpleNamespace(
            issuer="https://pilot.test" + routes.PREFIX,
            resource="https://pilot.test" + routes.PREFIX + "/mcp",
        )
    )

    def gate(self):
        if not self.enabled:
            raise routes.owner.BatisenalError("ACTIVATION_REQUIRED", 503)


@pytest.fixture
def application(monkeypatch):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="only-technical-ui-test")
    pilot = Pilot()
    routes.mount_pilot(app, pilot)
    detail = dict(
        title="<script>bad</script>",
        description="Descripción privada",
        context={"módulo": "Informe"},
        pilot_permission="Vigente",
        private_owner_view=True,
        can_support=False,
    )
    drafts = [
        dict(
            body='<img src="https://evil.test/">',
            source_kind="synthetic_stub",
            stale=False,
            operation_id="op-1",
        )
    ]

    async def data(*args):
        return detail, drafts

    monkeypatch.setattr(routes, "case_data", data)
    return app, pilot, detail, drafts


@pytest.mark.asyncio
async def test_private_render_assets_escape_and_no_general_routes(application):
    app, pilot, detail, drafts = application
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://pilot.test"
    ) as client:
        response = await client.get(routes.PREFIX + "/case")
        assert response.status_code == 200
        assert "Stub sintético · no es Chatty" in response.text
        assert "<img src=" not in response.text and "<script>bad" not in response.text
        assert "op-1" in response.text
        assert "No publicado" in response.text
        assert response.headers["cache-control"] == "no-store"
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert "unsafe-inline" not in response.headers["content-security-policy"]
        assert response.headers["referrer-policy"] == "same-origin"
        for path, kind in [
            ("/style.css", "text/css"),
            ("/widget.js", "application/javascript"),
        ]:
            result = await client.get(routes.PREFIX + path)
            assert result.status_code == 200
            assert kind in result.headers["content-type"]
        for path in [
            "/soporte/batisenal/cases",
            "/soporte/batisenal/cases/other/image",
        ]:
            assert (await client.get(path)).status_code == 404
        assert (
            await client.post("/soporte/batisenal/cases", json={})
        ).status_code == 404
        assert (
            await client.get(routes.PREFIX + "/case", headers={"host": "other.test"})
        ).status_code == 403
        pilot.enabled = False
        assert (await client.get(routes.PREFIX + "/case")).status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", ["Revocada; sin acceso", "Vencida; sin acceso"])
async def test_terminal_view_omits_case_draft_and_reference(application, permission):
    app, _, detail, _ = application
    detail["pilot_permission"] = permission
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://pilot.test"
    ) as client:
        response = await client.get(routes.PREFIX + "/case")
    assert "Acceso terminado" in response.text
    assert "Descripción privada" not in response.text
    assert "op-1" not in response.text
    assert "evil.test" not in response.text


@pytest.mark.asyncio
async def test_verified_agent_label_and_stale_are_distinct(application):
    app, _, _, drafts = application
    drafts[0].update(source_kind="ai_agent", stale=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://pilot.test"
    ) as client:
        response = await client.get(routes.PREFIX + "/case")
    assert "Chatty · conexión configurada" in response.text
    assert "El contexto cambió" in response.text
    assert 'data-state="stale"' in response.text


@pytest.mark.asyncio
async def test_no_draft_does_not_claim_agent_running(application):
    app, _, _, drafts = application
    drafts.clear()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://pilot.test"
    ) as client:
        response = await client.get(routes.PREFIX + "/case")
    assert 'data-state="pending"' in response.text
    assert "Autorizar no significa que el agente esté analizando" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,ctype,status",
    [
        ("csrf=x&csrf=y", "application/x-www-form-urlencoded", 400),
        ("x=" + "a" * 8200, "application/x-www-form-urlencoded", 413),
        ("{}", "application/json", 415),
        ("stage=invalid", "application/x-www-form-urlencoded", 400),
    ],
)
async def test_consent_rejects_invalid_form(application, body, ctype, status):
    app, _, _, _ = application
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://pilot.test"
    ) as client:
        response = await client.post(
            routes.PREFIX + "/consent", content=body, headers={"content-type": ctype}
        )
    assert response.status_code == status


def test_mount_collision_and_config_fail_before_mount():
    app = FastAPI()
    routes.mount_pilot(app, Pilot())
    with pytest.raises(ValueError, match="COLLISION"):
        routes.mount_pilot(app, Pilot())
    pilot = Pilot()
    pilot.flow = SimpleNamespace(
        config=SimpleNamespace(
            issuer="https://pilot.test/other", resource="https://pilot.test/mcp"
        )
    )
    count = len(app.routes)
    with pytest.raises(ValueError, match="CONFIG_MISMATCH"):
        routes.mount_pilot(app, pilot)
    assert len(app.routes) == count


@pytest.mark.asyncio
async def test_consent_preview_confirm_revoke_and_token_response(
    application, monkeypatch
):
    from dataclasses import dataclass
    from html.parser import HTMLParser

    class Fields(HTMLParser):
        def __init__(self, source):
            super().__init__()
            self.values = {}
            self.feed(source)

        def handle_starttag(self, tag, attributes):
            attrs = dict(attributes)
            if tag == "input":
                self.values[attrs["name"]] = attrs["value"]

    @dataclass
    class Token:
        access_token: str = "synthetic-only"
        token_type: str = "Bearer"
        expires_in: int = 60
        scope: str = "batisenal.support"

    app, pilot, detail, _ = application
    detail["viewer_name"] = "Synthetic viewer"
    calls = []

    async def browser_for_request(request):
        return None

    async def browser_call(request, operation):
        payload = await request.json()
        calls.append((operation, payload, request.headers.get("x-batisenal-csrf")))
        if operation == "begin":
            return dict(
                draft_id="draft-1",
                fingerprint="fingerprint",
                csrf="csrf-value",
                state="state-1",
                pilot={
                    "organization": "test",
                    "project": "demo",
                    "source_kind": "synthetic_stub",
                },
            )
        return SimpleNamespace(
            redirect_uri="https://client.test/callback", code="code-1", state="state-1"
        )

    async def exchange(values):
        return Token()

    pilot.browser_for_request = browser_for_request
    pilot.browser_call = browser_call
    pilot.exchange = exchange
    monkeypatch.setattr(routes, "validate_authorization_request", lambda *args: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://pilot.test"
    ) as client:
        first = await client.get(routes.PREFIX + "/authorize?state=state-1")
        assert first.status_code == 200
        assert "60 minutos" in first.text
        second = await client.post(
            routes.PREFIX + "/consent", data=Fields(first.text).values
        )
        assert second.status_code == 200
        assert "Stub sintético · no es Chatty" in second.text
        final = await client.post(
            routes.PREFIX + "/consent", data=Fields(second.text).values
        )
        assert final.status_code == 303
        assert (
            final.headers["location"]
            == "https://client.test/callback?code=code-1&state=state-1"
        )
        revoked = await client.post(
            routes.PREFIX + "/revoke", data={"csrf": "csrf-value", "confirmed": "true"}
        )
        assert revoked.status_code == 303
        assert [c[0] for c in calls] == ["begin", "confirm", "revoke"]
        assert calls[1][1]["approved"] is True
        assert calls[1][2] == "csrf-value"
        # Cookies must not be accepted by the bearer token endpoint.
        rejected = await client.post(routes.PREFIX + "/token", data={"code": "code-1"})
        assert rejected.status_code == 400
        client.cookies.clear()
        accepted = await client.post(routes.PREFIX + "/token", data={"code": "code-1"})
        assert accepted.status_code == 200
        assert accepted.json()["access_token"] == "synthetic-only"
        assert "refresh_token" not in accepted.json()
        bad = await client.post(
            routes.PREFIX + "/consent",
            data={"stage": "begin", "csrf": "x", "parameters": "{"},
        )
        assert bad.status_code == 400
        bad = await client.post(routes.PREFIX + "/revoke", data={"confirmed": "false"})
        assert bad.status_code == 403


@pytest.mark.asyncio
async def test_session_payload_csrf_content_and_json_checks():
    from starlette.requests import Request

    from devnous.gastos.routes.batisenal_routes import read_payload

    def request(body=b'{"confirmed":true}', headers=None):
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "scheme": "https",
                "server": ("pilot.test", 443),
                "query_string": b"",
                "session": {"batisenal_csrf": "test"},
                "headers": (
                    headers
                    if headers is not None
                    else [
                        (b"host", b"pilot.test"),
                        (b"origin", b"https://pilot.test"),
                        (b"x-batisenal-csrf", b"test"),
                        (b"content-type", b"application/json"),
                    ]
                ),
            },
            receive,
        )

    policy = SimpleNamespace(max_image_bytes=0)
    assert await read_payload(request(), policy) == {"confirmed": True}
    for body, headers, code in [
        (b"{}", [], "ORIGIN_DENIED"),
        (
            b"{}",
            [(b"host", b"pilot.test"), (b"origin", b"https://pilot.test")],
            "CSRF_DENIED",
        ),
        (b"[]", None, "JSON_INVALID"),
        (b"{", None, "JSON_INVALID"),
        (b"a" * 64001, None, "BODY_TOO_LARGE"),
    ]:
        with pytest.raises(routes.owner.BatisenalError, match=code):
            await read_payload(request(body, headers), policy)


@pytest.mark.asyncio
async def test_private_permission_resolution_honors_explicit_denial(monkeypatch):
    from devnous.gastos.routes import batisenal_routes, dependencies
    from devnous.gastos.services import access_control_service as access

    async def load(*args):
        return ["batisenal:test:demo:request"]

    async def decision(*args):
        return False

    async def allowed(*args):
        return True

    monkeypatch.setattr(dependencies, "_load_effective_permissions", load)
    monkeypatch.setattr(access, "explicit_tool_decision", decision)
    monkeypatch.setattr(access, "can_access_tool", allowed)
    monkeypatch.setattr(
        batisenal_routes.service,
        "principal_from_tokens",
        lambda employee, tokens, **kw: kw,
    )
    result = await batisenal_routes.resolve_web_principal(
        None, SimpleNamespace(id="test"), "GET"
    )
    assert result == {"can_request": False, "can_support": False}
    monkeypatch.setattr(access, "explicit_tool_decision", allowed)
    result = await batisenal_routes.resolve_web_principal(
        None, SimpleNamespace(id="test"), "POST"
    )
    assert result == {"can_request": True, "can_support": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status",
    [
        (routes.Denied("OAUTH_BUSY"), 503),
        (routes.owner.BatisenalError("UNAUTHENTICATED", 401), 401),
        (RuntimeError("secret"), 503),
    ],
)
async def test_guarded_errors_are_sanitized(application, monkeypatch, error, status):
    app, _, _, _ = application

    async def denied(*args):
        raise error

    monkeypatch.setattr(routes, "case_data", denied)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://pilot.test"
    ) as client:
        result = await client.get(routes.PREFIX + "/case")
    assert result.status_code == status
    assert "secret" not in result.text


@pytest.mark.asyncio
async def test_metadata_challenge_and_disabled_resource(application):
    from fastapi.responses import JSONResponse

    app, pilot, _, _ = application
    pilot.flow = SimpleNamespace(
        config=SimpleNamespace(
            issuer="https://pilot.test" + routes.PREFIX,
            resource="https://pilot.test" + routes.PREFIX + "/mcp",
        )
    )
    # Existing mount captured config; attach methods on that shared config object.
    config = Pilot.flow.config
    config.authorization_metadata = lambda: {"issuer": config.issuer}
    config.resource_metadata = lambda: {"resource": config.resource}

    async def mcp(scope, receive, send):
        await JSONResponse({"error": "UNAUTHENTICATED"}, status_code=401)(
            scope, receive, send
        )

    pilot.mcp = mcp
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://pilot.test"
    ) as client:
        for path, key in [
            (routes.AUTH_META, "issuer"),
            (routes.RESOURCE_META, "resource"),
        ]:
            response = await client.get(path)
            assert response.status_code == 200 and response.json()[key].startswith(
                "https://pilot.test/"
            )
        response = await client.post(routes.PREFIX + "/mcp", json={})
        assert response.status_code == 401
        assert routes.RESOURCE_META in response.headers["www-authenticate"]
        pilot.enabled = False
        response = await client.post(routes.PREFIX + "/mcp", json={})
        assert response.status_code == 503
        assert response.json()["error"] == "ACTIVATION_REQUIRED"
