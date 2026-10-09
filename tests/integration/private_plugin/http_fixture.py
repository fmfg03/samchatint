"""In-memory HTTP harness only; never import into the SamChat runtime.

The sign-in helper creates a synthetic signed cookie, not a password/login
endpoint. Existing SessionMiddleware and canonical identity dependency are
exercised; OAuth nonce fields here are fixture-only, not a live cookie change.
"""

import secrets
from contextvars import ContextVar
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

from sqlalchemy import select
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route
from test_direction_read import source_functions

from samchat.private_plugin.authorization import resolve_session_principal
from samchat.private_plugin.contracts import Denied
from samchat.private_plugin.oauth_flow import BrowserSession
from samchat.private_plugin.oauth_store import EMPLOYEES


class FixtureHTTP:
    def __init__(self, fixture):
        self.fixture = fixture
        self.request = ContextVar("fixture_http_request")
        prefix = urlsplit(fixture.config.issuer).path
        resource_path = urlsplit(fixture.config.resource).path

        async def employee(session, employee_id):
            with fixture.engine.connect() as connection:
                row = (
                    connection.execute(
                        select(EMPLOYEES).where(EMPLOYEES.c.id == str(employee_id))
                    )
                    .mappings()
                    .one_or_none()
                )
            return SimpleNamespace(id=row["id"], activo=row["activo"]) if row else None

        namespace = {
            "Depends": lambda _: None,
            "get_db_session": lambda: None,
            "UUID": UUID,
            "HTTPException": lambda **_: RuntimeError("UNAUTHENTICATED"),
            "_load_empleado_proxy_by_id": employee,
            "visible_tools_for": AsyncMock(return_value=set()),
            "_uses_route_owned_authorization": lambda _: False,
            "can_access_path": AsyncMock(return_value=True),
            "_login_redirect_for_request": lambda _: "/login",
        }
        canonical = source_functions(
            "src/devnous/gastos/routes/dependencies.py",
            {"get_current_empleado"},
            namespace,
        )["get_current_empleado"]

        async def browser():
            request = self.request.get()
            principal = await resolve_session_principal(
                request=request, session=None, get_current_empleado=canonical
            )
            return BrowserSession(
                principal,
                request.session["fixture_binding"],
                request.session["fixture_csrf"],
            )

        fixture.flow._browser_resolver = browser

        async def dispatch(request):
            token = self.request.set(request)
            try:
                path = request.url.path
                if path == "/__fixture_session":
                    body = await request.json()
                    if body.get("employee_id") not in {
                        "10000000-0000-0000-0000-000000000001",
                        "10000000-0000-0000-0000-000000000002",
                    }:
                        raise Denied("UNAUTHENTICATED")
                    request.session.clear()
                    request.session.update(
                        empleado_id=body["employee_id"],
                        fixture_binding=secrets.token_urlsafe(32),
                        fixture_csrf=secrets.token_urlsafe(32),
                    )
                    return JSONResponse(
                        {"signed_synthetic_session": True},
                        headers={"Cache-Control": "no-store"},
                    )
                if path == prefix + "/authorize":
                    result = await fixture.flow.begin(
                        request.query_params.multi_items()
                    )
                    return JSONResponse(
                        asdict(result), headers={"Cache-Control": "no-store"}
                    )
                if path == prefix + "/consent":
                    body = await request.json()
                    if set(body) != {
                        "draft_id",
                        "fingerprint",
                        "csrf",
                        "state",
                        "approved",
                    }:
                        raise Denied("CONSENT_REJECTED")
                    result = await fixture.flow.confirm(**body)
                    return RedirectResponse(
                        result.redirect_uri
                        + "?"
                        + urlencode(
                            {
                                "code": result.code,
                                "state": result.state,
                                "iss": fixture.config.issuer,
                            }
                        ),
                        status_code=303,
                        headers={"Cache-Control": "no-store"},
                    )
                if path == prefix + "/token":
                    body = await request.body()
                    if len(body) > 16384:
                        raise Denied("INVALID_GRANT")
                    result = await fixture.flow.exchange(
                        parse_qsl(body.decode("ascii"), keep_blank_values=True)
                    )
                    return JSONResponse(
                        asdict(result),
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
                    )
                if path == "/.well-known/oauth-protected-resource" + resource_path:
                    return JSONResponse(fixture.config.resource_metadata())
                return JSONResponse(fixture.config.authorization_metadata())
            except Denied as exc:
                return JSONResponse(
                    {"error": str(exc)},
                    status_code=401 if str(exc) == "UNAUTHENTICATED" else 400,
                    headers={"Cache-Control": "no-store"},
                )
            except Exception:
                return JSONResponse(
                    {"error": "OAUTH_UNAVAILABLE"},
                    status_code=400,
                    headers={"Cache-Control": "no-store"},
                )
            finally:
                self.request.reset(token)

        self.app = Starlette(
            routes=[
                Route("/__fixture_session", dispatch, methods=["POST"]),
                Route(prefix + "/authorize", dispatch, methods=["GET"]),
                Route(prefix + "/consent", dispatch, methods=["POST"]),
                Route(prefix + "/token", dispatch, methods=["POST"]),
                Route(
                    "/.well-known/oauth-protected-resource" + resource_path,
                    dispatch,
                    methods=["GET"],
                ),
                Route(
                    "/.well-known/oauth-authorization-server" + prefix,
                    dispatch,
                    methods=["GET"],
                ),
            ]
        )
        self.app.add_middleware(
            SessionMiddleware,
            secret_key=secrets.token_urlsafe(32),
            session_cookie="samchat_session",
            https_only=True,
            same_site="lax",
            max_age=86400,
        )
