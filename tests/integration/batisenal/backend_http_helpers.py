from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from backend_modern_wire import mcp_request
from backend_oauth_scenario import VERIFIER, parameters
from httpx import ASGITransport, AsyncClient

from devnous.gastos.routes.batisenal_pilot_routes import PREFIX


class Form(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.values = {}
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "hidden":
            self.values[attrs["name"]] = attrs["value"]


async def begin(s):
    page = await s.http.get(PREFIX + "/authorize", params=parameters(s))
    assert page.status_code == 200, page.text
    return page


async def confirm(s):
    page = await begin(s)
    preview = await s.http.post(PREFIX + "/consent", data=Form(page.text).values)
    assert preview.status_code == 200, preview.text
    confirmed = await s.http.post(PREFIX + "/consent", data=Form(preview.text).values)
    assert confirmed.status_code == 303, confirmed.text
    return preview, confirmed


async def token(s, confirmed):
    values = parse_qs(urlsplit(confirmed.headers["location"]).query)
    async with AsyncClient(
        transport=ASGITransport(app=s.app), base_url="https://test"
    ) as agent_http:
        r = await agent_http.post(
            PREFIX + "/token",
            data={
                "grant_type": "authorization_code",
                "code": values["code"][0],
                "client_id": s.config.client_id,
                "redirect_uri": s.config.redirect_uri,
                "resource": s.config.resource,
                "code_verifier": VERIFIER,
            },
        )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def rpc(s, bearer, name, args):
    async with AsyncClient(
        transport=ASGITransport(app=s.app), base_url="https://test"
    ) as c:
        wire_headers, message = mcp_request(
            "tools/call", {"name": "samchat_batisenal_" + name, "arguments": args}
        )
        r = await c.post(
            PREFIX + "/mcp",
            headers={**wire_headers, "Authorization": "Bearer " + bearer},
            json=message,
        )
    return r


def result(r):
    assert r.status_code == 200 and "error" not in r.json(), r.text
    return r.json()["result"]["structuredContent"]
