"""Transport-contract tests with explicit spies, not SamChat integration.

No DB, real credential, grant, listener or network. Load real candidate ASGI and
canonical BatisenalError; test doubles only mark the existing authorization seam.
"""

import asyncio
import base64
import copy
import unittest
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient

from devnous.gastos.services.batisenal_service import BatisenalError
from samchat.batisenal_transport import framing
from samchat.batisenal_transport import http as transport


def request(method="server/discover", params=None, version=framing.PROTOCOL):
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            "_meta": {
                framing.VERSION: version,
                framing.CAPABILITIES: {},
                framing.CLIENT: {"name": "synthetic-contract-client", "version": "1"},
            },
            **(params or {}),
        },
    }
    headers = {
        "Authorization": "Bearer public-synthetic-fixture",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": version,
        "Mcp-Method": method,
    }
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]
    return message, headers


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class TransportSpy:
    """Instrumentation only; never represents a real authorized user or model."""

    def __init__(self):
        self.resolved, self.authorized, self.handled, self.audit = [], [], [], []
        self.deny_resolver = self.deny_authority = self.fail_handler = (
            self.fail_audit
        ) = False

    async def resolve(self, request):
        self.resolved.append(True)
        if self.deny_resolver:
            raise BatisenalError("UNAUTHENTICATED", 401)
        return request.headers.get("x-test-reference", "synthetic-reference")

    def sessions(self):
        return Session()

    async def authorize(self, session):
        self.authorized.append(transport.current_connection())
        if self.deny_authority:
            raise BatisenalError("GRANT_REVOKED", 403)
        return (
            SimpleNamespace(employee="synthetic-employee"),
            SimpleNamespace(profile_id="synthetic-profile"),
            None,
        )

    def audit_attempt(self, *args):
        if self.fail_audit:
            raise BatisenalError("AUDIT_UNAVAILABLE", 503)
        self.audit.append(args)

    async def handle(self, message):
        if self.fail_handler:
            raise RuntimeError("DO_NOT_LEAK_INTERNAL_DETAIL")
        self.handled.append(copy.deepcopy(message))
        own_reference = transport.current_connection()
        await asyncio.sleep(0)
        assert transport.current_connection() == own_reference
        result = (
            {"tools": []}
            if message["method"] == "tools/list"
            else {"content": [], "isError": False}
        )
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}


class ModernTransport(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.spy = TransportSpy()
        self.app = transport.create_app(
            self.spy, resolve_connection=self.spy.resolve, enabled=True
        )
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="https://synthetic.test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.assertEqual(transport.current_connection(), "")

    async def send(self, message=None, headers=None, **kwargs):
        if message is None:
            message, defaults = request()
            headers = defaults if headers is None else headers
        return await self.client.post("/mcp", json=message, headers=headers, **kwargs)

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["error"]["code"], code, response.text)

    async def test_discovery_schema_and_private_no_cache(self):
        response = await self.send()
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["supportedVersions"], [framing.PROTOCOL])
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual((result["cacheScope"], result["ttlMs"]), ("private", 0))
        self.assertIn("io.modelcontextprotocol/serverInfo", result["_meta"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(len(self.spy.authorized), 1)

    async def test_inline_tools_list_without_handshake(self):
        response = await self.send(*request("tools/list"))
        result = response.json()["result"]
        self.assertEqual(result["tools"], [])  # No extra profile tool injected.
        self.assertEqual(result["cacheScope"], "private")
        self.assertEqual(self.spy.handled[0]["params"], {})

    async def test_tool_arguments_preserved_metadata_removed_only(self):
        params = {
            "name": "samchat_batisenal_get_case_context",
            "arguments": {"synthetic": "unchanged"},
        }
        msg, headers = request("tools/call", params)
        original = copy.deepcopy(msg)
        response = await self.send(msg, headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.spy.handled[0]["params"], params)
        self.assertEqual(msg, original)
        self.assertEqual(response.json()["result"]["resultType"], "complete")

    async def test_case_call_and_ping_are_authorized_modern_results(self):
        for method, params in [
            ("ping", None),
            (
                "tools/call",
                {"name": "samchat_batisenal_get_case_context", "arguments": {}},
            ),
        ]:
            response = await self.send(*request(method, params))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["result"]["resultType"], "complete")
        self.assertEqual(len(self.spy.authorized), 2)

    async def test_unsupported_versions_and_retry(self):
        for version in ("2025-11-25", "1900-01-01", "2099-01-01"):
            response = await self.send(*request(version=version))
            self.error(response, 400, -32022)
            self.assertEqual(
                response.json()["error"]["data"],
                {"supported": [framing.PROTOCOL], "requested": version},
            )
        self.assertFalse(self.spy.resolved)
        self.assertEqual((await self.send()).status_code, 200)

    async def test_legacy_handshake_is_not_claimed_supported(self):
        msg, headers = request(
            "initialize",
            {"protocolVersion": "2025-11-25", "clientInfo": {}, "capabilities": {}},
            "2025-11-25",
        )
        msg["params"].pop("_meta")
        self.error(await self.send(msg, headers), 400, -32022)
        self.error(await self.send(*request("initialize")), 404, -32601)

    async def test_missing_mismatched_and_duplicate_headers(self):
        for name in ("MCP-Protocol-Version", "Mcp-Method"):
            for change in ("missing", "wrong", "duplicate"):
                with self.subTest(name=name, change=change):
                    msg, headers = request()
                    if change == "missing":
                        headers.pop(name)
                    elif change == "wrong":
                        headers[name] = "wrong"
                    else:
                        headers = list(headers.items()) + [
                            (name.lower(), headers[name])
                        ]
                    self.error(await self.send(msg, headers), 400, -32020)
        self.assertFalse(self.spy.resolved)

    async def test_name_header_mismatch_and_missing(self):
        for value in (None, "wrong", "=?base64?%%%?=", "=?base64?/w==?="):
            msg, headers = request(
                "tools/call",
                {"name": "samchat_batisenal_get_case_context", "arguments": {}},
            )
            if value is None:
                headers.pop("Mcp-Name")
            else:
                headers["Mcp-Name"] = value
            self.error(await self.send(msg, headers), 400, -32020)

    async def test_base64_names_compare_decoded_value(self):
        for name in (
            "samchat_batisenal_get_case_context",
            "synthetic_ñ",
            "=?base64?literal?=",
        ):
            msg, headers = request("tools/call", {"name": name, "arguments": {}})
            headers["Mcp-Name"] = (
                "=?base64?" + base64.b64encode(name.encode()).decode() + "?="
            )
            self.assertEqual((await self.send(msg, headers)).status_code, 200)

    async def test_header_case_rules(self):
        msg, headers = request()
        self.assertEqual(
            (
                await self.send(msg, {k.lower(): v for k, v in headers.items()})
            ).status_code,
            200,
        )
        headers["Mcp-Method"] = "SERVER/DISCOVER"
        self.error(await self.send(msg, headers), 400, -32020)
        headers["Mcp-Method"] = " server/discover "
        self.error(await self.send(msg, headers), 400, -32020)

    async def test_required_metadata_and_bad_types(self):
        for meta in (
            None,
            [],
            {},
            {framing.VERSION: framing.PROTOCOL},
            {framing.VERSION: framing.PROTOCOL, framing.CAPABILITIES: []},
            {
                framing.VERSION: framing.PROTOCOL,
                framing.CAPABILITIES: {},
                "actor": "forged",
            },
        ):
            msg, headers = request()
            msg["params"]["_meta"] = meta
            self.error(await self.send(msg, headers), 400, -32602)
        self.assertFalse(self.spy.resolved)

    async def test_known_optional_metadata_is_inert(self):
        msg, headers = request()
        msg["params"]["_meta"].update(progressToken=12.5)
        msg["params"]["_meta"][framing.LOG_LEVEL] = "warning"
        msg["params"]["_meta"][framing.CAPABILITIES] = {
            "roots": {},
            "sampling": {"tools": {}},
            "elicitation": {"form": {}},
            "extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html"]}},
            "experimental": {"example": {}},
        }
        msg["params"]["_meta"][framing.CLIENT].update(
            title="Synthetic",
            websiteUrl="https://never-fetch.invalid",
            icons=[
                {
                    "src": "https://never-fetch.invalid/icon.png",
                    "sizes": ["16x16"],
                    "theme": "light",
                }
            ],
        )
        response = await self.send(msg, headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["capabilities"], {"tools": {}})

    async def test_client_info_is_optional_and_not_identity(self):
        msg, headers = request()
        msg["params"]["_meta"].pop(framing.CLIENT)
        self.assertEqual((await self.send(msg, headers)).status_code, 200)
        msg["params"]["_meta"][framing.CLIENT] = {
            "name": "administrator",
            "version": "1",
        }
        self.assertEqual((await self.send(msg, headers)).status_code, 200)
        self.assertEqual(self.spy.authorized, ["synthetic-reference"] * 2)

    async def test_invalid_optional_metadata(self):
        variants = [
            (framing.CLIENT, {}),
            (framing.CLIENT, []),
            (framing.CLIENT, {"name": "x", "version": 1}),
            (framing.CLIENT, {"name": "x", "version": "1", "employee_id": "forged"}),
            (framing.CLIENT, {"name": "x", "version": "1", "icons": "bad"}),
            (
                framing.CLIENT,
                {"name": "x", "version": "1", "icons": [{"src": "x", "theme": "bad"}]},
            ),
            (framing.CAPABILITIES, {"unknown": {}}),
            (framing.CAPABILITIES, {"roots": {"unexpected": True}}),
            (framing.CAPABILITIES, {"sampling": []}),
            (framing.CAPABILITIES, {"sampling": {"bad": {}}}),
            (framing.CAPABILITIES, {"elicitation": {"bad": {}}}),
            (framing.CAPABILITIES, {"experimental": {"bad": False}}),
            (framing.CAPABILITIES, {"extensions": {"unprefixed": {}}}),
            (framing.CAPABILITIES, {"extensions": {"bad..prefix/extension": {}}}),
            (framing.CAPABILITIES, {"extensions": {"bad.-label/extension": {}}}),
            (framing.CAPABILITIES, {"extensions": {"bad.2label/extension": {}}}),
            (framing.LOG_LEVEL, []),
            (framing.LOG_LEVEL, "not-a-level"),
            ("progressToken", True),
        ]
        for key, value in variants:
            with self.subTest(key=key, value=value):
                msg, headers = request()
                msg["params"]["_meta"][key] = value
                self.error(await self.send(msg, headers), 400, -32602)

    async def test_unknown_arguments_do_not_disappear(self):
        for method, params in [
            ("server/discover", {"unexpected": True}),
            ("ping", {"actor": "forged"}),
            ("tools/list", {"cursor": "not-issued"}),
            (
                "tools/call",
                {
                    "name": "samchat_batisenal_get_case_context",
                    "arguments": {},
                    "actor": "forged",
                },
            ),
            (
                "tools/call",
                {"name": "samchat_batisenal_get_case_context", "arguments": []},
            ),
        ]:
            self.error(await self.send(*request(method, params)), 400, -32602)
        self.assertFalse(self.spy.handled)

    async def test_notifications_null_id_and_extra_name_header(self):
        for identifier in (None, "remove"):
            msg, headers = request()
            if identifier is None:
                msg["id"] = None
            else:
                del msg["id"]
            self.error(await self.send(msg, headers), 400, -32600)
        msg, headers = request()
        headers["Mcp-Name"] = "unexpected"
        self.error(await self.send(msg, headers), 400, -32020)

    async def test_authority_denial_cannot_be_overridden_by_metadata(self):
        self.spy.deny_authority = True
        response = await self.send()
        self.error(response, 403, -32000)
        self.assertEqual(response.json()["error"]["message"], "GRANT_REVOKED")
        self.assertFalse(self.spy.handled)

    async def test_resolver_denial_and_missing_bearer(self):
        msg, headers = request()
        headers.pop("Authorization")
        self.error(await self.send(msg, headers), 401, -32000)
        self.assertFalse(self.spy.resolved)
        self.spy.deny_resolver = True
        self.error(await self.send(), 401, -32000)
        self.assertFalse(self.spy.authorized)

    async def test_origin_cookie_and_query_guards_preserved(self):
        for origin in ("null", "https://foreign.invalid", ""):
            msg, headers = request()
            headers["Origin"] = origin
            self.error(await self.send(msg, headers), 403, -32000)
        msg, headers = request()
        headers["Cookie"] = "synthetic=value"
        self.error(await self.send(msg, headers), 400, -32000)
        self.error(
            await self.send(*request(), params={"synthetic": "value"}), 400, -32000
        )
        self.assertFalse(self.spy.resolved)

    async def test_activation_and_http_methods(self):
        app = transport.create_app(self.spy)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://synthetic.test"
        ) as client:
            self.error(await client.post("/mcp"), 503, -32000)
        for method in ("GET", "DELETE"):
            self.assertEqual(
                (await self.client.request(method, "/mcp")).status_code, 405
            )

    async def test_duplicate_json_nonfinite_and_size_limit(self):
        _, headers = request()
        for raw in ('{"jsonrpc":"2.0","jsonrpc":"2.0"}', '{"value":NaN}'):
            self.error(
                await self.client.post("/mcp", content=raw, headers=headers),
                400,
                -32700,
            )
        self.error(
            await self.client.post("/mcp", content="x" * 32769, headers=headers),
            413,
            -32000,
        )

    async def test_invalid_envelope_uses_json_rpc_errors(self):
        msg, headers = request()
        for malformed in ([], {**msg, "unexpected": True}, {**msg, "id": True}):
            self.error(await self.send(malformed, headers), 400, -32600)
        self.error(await self.send({**msg, "params": []}, headers), 400, -32602)

    async def test_exception_does_not_leak_or_leave_context(self):
        self.spy.fail_handler = True
        response = await self.send(*request("tools/list"))
        self.error(response, 503, -32000)
        self.assertNotIn("DO_NOT_LEAK", response.text)
        self.assertEqual(transport.current_connection(), "")

    async def test_audit_failure_stays_fail_closed(self):
        self.spy.fail_audit = True
        self.error(await self.send(), 503, -32000)
        self.assertFalse(self.spy.handled)

    async def test_concurrent_contexts_stay_separate(self):
        async def one(reference):
            msg, headers = request("tools/list")
            headers["x-test-reference"] = reference
            response = await self.send(msg, headers)
            self.assertEqual(response.status_code, 200)

        await asyncio.gather(one("synthetic-A"), one("synthetic-B"))
        self.assertEqual(set(self.spy.authorized), {"synthetic-A", "synthetic-B"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
