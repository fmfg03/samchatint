"""Signed-cookie cursor budget and authentication preservation."""

from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
import test_amex_direct_suggestions as suggestion_fixtures
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from devnous.gastos.services import amex_direct_navigation as nav
from devnous.gastos.services import amex_direct_suggestion_service as suggestions

world = suggestion_fixtures.world


@pytest.mark.parametrize("namespace", nav.CURSOR_NAMESPACES)
def test_shared_budget_prunes_old_tokens_and_preserves_auth_csrf(namespace):
    session = {"user_id": "user", "csrf_token": "csrf", "roles": ["superadmin"]}
    for key in nav.CURSOR_NAMESPACES:
        session[key] = {
            str(n): {"fingerprint": "f" * 64, "padding": "x" * 200} for n in range(64)
        }
    token = nav.save_navigation_cursor(session, namespace, {"fingerprint": "fresh"})
    assert session[namespace][token]["fingerprint"] == "fresh"
    assert nav.navigation_cursor_bytes(session) <= nav.MAX_CURSOR_BYTES
    assert all(len(session.get(key, {})) <= 4 for key in nav.CURSOR_NAMESPACES)
    assert session["user_id"] == "user" and session["csrf_token"] == "csrf"
    assert session["roles"] == ["superadmin"]


def test_only_navigation_namespaces_may_be_written_and_oversized_payload_rejected():
    session = {"user_id": "user", "csrf_token": "csrf"}
    with pytest.raises(ValueError, match="unsupported"):
        nav.save_navigation_cursor(session, "user_id", {})
    with pytest.raises(ValueError, match="payload_too_large"):
        nav.save_navigation_cursor(
            session, nav.CURSOR_NAMESPACES[0], {"large": "x" * 3000}
        )
    assert session == {"user_id": "user", "csrf_token": "csrf"}


@pytest.mark.asyncio
async def test_bulk_25_items_signed_cookie_roundtrip_retains_csrf_and_page_cursor(
    world,
):
    """Real SessionMiddleware signing/encoding, owner rendering 25 candidate rows."""
    w = world
    items = [NS(**{**vars(w.item), "id": uuid4()}) for _ in range(26)]
    suggestions.load_items.return_value = items
    suggestions.load_effective_sources.return_value = {
        item.id: w.source for item in items
    }
    # Four ranked hits per rendered item force every row's has_more condition.
    w.records([{**w.hit, "item_id": item.id} for item in items[:25] for _ in range(4)])

    async def seed(request):
        request.session.update(user_id="synthetic-actor", csrf_token="synthetic-csrf")
        for key in nav.CURSOR_NAMESPACES:
            for n in range(4):
                nav.save_navigation_cursor(
                    request.session, key, {"fingerprint": "f" * 64, "n": n}
                )
        return JSONResponse({"ok": True})

    async def render(request):
        result = await suggestions.suggest_direct_statement_cfdis(
            w.session, **{**w.args, "navigation": request.session}
        )
        assert len(result["items"]) == 25 and all(
            row["has_more"] for row in result["items"]
        )
        assert all(row["cursor"] is None for row in result["items"])
        token = result["next_cursor"]
        return JSONResponse(
            {
                "cursor": token,
                "fingerprint": request.session[suggestions.CURSOR_KEY][token][
                    "fingerprint"
                ],
            }
        )

    async def reload(request):
        token = request.query_params["cursor"]
        position = request.session[suggestions.CURSOR_KEY][token]
        w.records([])
        continued = await suggestions.suggest_direct_statement_cfdis(
            w.session, **{**w.args, "navigation": request.session, "cursor": token}
        )
        assert len(continued["items"]) == 1
        assert continued["items"][0]["item_id"] == str(items[25].id)
        return JSONResponse(
            {
                "fingerprint": position["fingerprint"],
                "offset": position["offset"],
                "csrf": request.session["csrf_token"],
                "user": request.session["user_id"],
            }
        )

    app = Starlette(
        routes=[
            Route("/seed", seed),
            Route("/render", render),
            Route("/reload", reload),
        ],
        middleware=[
            Middleware(SessionMiddleware, secret_key="synthetic-cookie-test-secret")
        ],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        await client.get("/seed")
        response = await client.get("/render")
        cookie = response.headers["set-cookie"]
        assert len(cookie.encode()) < 4096
        payload = response.json()
        restored = (
            await client.get("/reload", params={"cursor": payload["cursor"]})
        ).json()
        assert restored == {
            "fingerprint": payload["fingerprint"],
            "offset": 25,
            "csrf": "synthetic-csrf",
            "user": "synthetic-actor",
        }
