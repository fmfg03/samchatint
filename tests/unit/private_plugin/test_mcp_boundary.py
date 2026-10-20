"""Actual MCP SDK sessions using memory streams and synthetic identities only."""

import unittest
from dataclasses import replace

from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session

from samchat.private_plugin.contracts import Identity
from samchat.private_plugin.mcp_boundary import create_server


class FakeIdentityProvider:
    def __init__(self):
        self.current = Identity(
            actor_id="fixture-user",
            installation_id="fixture-installation",
            organization_id="fixture-org",
            grant_id="fixture-grant",
            expires_at=200,
            issuer="fixture-issuer",
            audience="fixture-audience",
            oauth_scopes=frozenset(),
            active=True,
            revoked=False,
        )
        self.references = []
        self.failure = False

    def resolve_current(self, connection_ref):
        self.references.append(connection_ref)
        if self.failure:
            raise RuntimeError("synthetic-private-error")
        return self.current


class MemoryAudit:
    def __init__(self):
        self.rows = []
        self.failure = False

    def append(self, **row):
        if self.failure:
            raise RuntimeError("synthetic-private-error")
        self.rows.append(row)
        return "fixture-receipt"


class MCPBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.identities = FakeIdentityProvider()
        self.audit = MemoryAudit()
        self.reference = "trusted-transport-reference"
        self.server = create_server(
            identities=self.identities,
            audit=self.audit,
            current_connection=lambda: self.reference,
            now=lambda: 100,
            issuer="fixture-issuer",
            audience="fixture-audience",
            installation_id="fixture-installation",
        )

    async def test_initialize_empty_discovery_and_denied_calls(self):
        async with create_connected_server_and_client_session(self.server) as client:
            # The SDK helper performs and awaits initialize itself.
            self.assertEqual((await client.list_tools()).tools, [])
            for name in ("expense.get_status", "transfer.create_draft", "sql"):
                result = await client.call_tool(name, {"query": "synthetic-only"})
                self.assertTrue(result.isError)
                self.assertEqual(result.content[0].text, "CAPABILITIES_DISABLED")
        self.assertEqual(len(self.identities.references), 4)
        self.assertEqual(len(self.audit.rows), 4)

    async def test_model_cannot_supply_identity_or_connection(self):
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool(
                "synthetic-private-name",
                {
                    "actor": "admin",
                    "scopes": ["*"],
                    "bearer": "fake",
                    "connection_ref": "model-reference",
                },
            )
        self.assertTrue(result.isError)
        self.assertEqual(self.identities.references, [self.reference])
        self.assertEqual(self.audit.rows[0]["actor_id"], "fixture-user")
        self.assertNotIn("synthetic-private-name", str(self.audit.rows))

    async def test_revocation_and_expiry_rechecked_in_same_session(self):
        async with create_connected_server_and_client_session(self.server) as client:
            await client.list_tools()
            valid = self.identities.current
            for change in (
                {"revoked": True},
                {"expires_at": 100},
                {"active": False},
                {"issuer": "other"},
                {"audience": "other"},
                {"installation_id": "other"},
                {"actor_id": ""},
                {"grant_id": ""},
                {"organization_id": ""},
                {"active": "false"},
                {"revoked": 0},
                {"expires_at": 200.5},
            ):
                self.identities.current = replace(valid, **change)
                result = await client.call_tool("expense.get_status", {})
                self.assertEqual(result.content[0].text, "UNAUTHENTICATED")
                self.assertTrue(result.isError)
                with self.assertRaises(McpError) as raised:
                    await client.list_tools()
                self.assertEqual(str(raised.exception), "UNAUTHENTICATED")

    async def test_provider_and_audit_errors_do_not_leak(self):
        async with create_connected_server_and_client_session(self.server) as client:
            self.identities.failure = True
            result = await client.call_tool("anything", {})
            self.assertEqual(result.content[0].text, "UNAUTHENTICATED")
            self.identities.failure = False
            self.audit.failure = True
            result = await client.call_tool("anything", {})
            self.assertEqual(result.content[0].text, "AUDIT_UNAVAILABLE")
            with self.assertRaises(McpError) as raised:
                await client.list_tools()
            self.assertEqual(str(raised.exception), "AUDIT_UNAVAILABLE")

    async def test_missing_transport_context_fails_closed(self):
        self.reference = ""
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool("anything", {"connection_ref": "fake"})
        self.assertEqual(result.content[0].text, "UNAUTHENTICATED")
        self.assertEqual(self.identities.references, [])

    async def test_request_context_is_resolved_again_for_next_user(self):
        async with create_connected_server_and_client_session(self.server) as client:
            await client.call_tool("anything", {})
            self.reference = "second-transport-reference"
            self.identities.current = replace(
                self.identities.current, actor_id="second-user", grant_id="second"
            )
            await client.call_tool("anything", {})
        self.assertEqual(
            [r["actor_id"] for r in self.audit.rows], ["fixture-user", "second-user"]
        )
        self.assertEqual(self.identities.references[-1], self.reference)


if __name__ == "__main__":
    unittest.main()
