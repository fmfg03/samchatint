"""SDK initialize/discovery/read roundtrips with synthetic per-user connections."""

import json
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

import test_direction_read as direction_fixture
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session

from samchat.private_plugin.contracts import Denied, Identity
from samchat.private_plugin.direction_read import DirectionContext
from samchat.private_plugin.local_reads import create_local_read_server
from samchat.private_plugin.perimeter import digest


class Connections:
    def __init__(self):
        self.identity = Identity(
            "employee-A",
            "installation-A",
            "organization-A",
            "grant-A",
            200,
            "https://issuer.invalid",
            "https://resource.invalid",
            frozenset({"direction:read"}),
            True,
            False,
            "prf_synthetic_account_A",
        )
        self.calls = []

    def resolve_current(self, ref):
        self.calls.append(ref)
        if ref != "trusted-reference":
            raise Denied("UNAUTHENTICATED")
        return self.identity


class Audit:
    def __init__(self):
        self.rows = []
        self.failure = False

    def append(self, **row):
        if self.failure:
            raise RuntimeError("synthetic-private-audit-error")
        self.rows.append(row)
        return str(len(self.rows))

    def append_read(self, **row):
        return self.append(**row, outcome="READ_VERIFIED")


class Reader:
    """Boundary fixture only; canonical integration covered in Direction tests."""

    def __init__(self):
        self.calls = []
        self.error = Denied("FORBIDDEN")

    async def read(self, **kwargs):
        self.calls.append(kwargs)
        raise self.error


class LocalReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.connections, self.audit, self.reader = Connections(), Audit(), Reader()
        self.server = create_local_read_server(
            identities=self.connections,
            audit=self.audit,
            direction=self.reader,
            current_connection=lambda: "trusted-reference",
            now=lambda: 100,
            issuer="https://issuer.invalid",
            audience="https://resource.invalid",
            installation_id="installation-A",
        )

    async def test_revocation_while_audit_waits_suppresses_read_release(self):
        append = self.audit.append_read

        def revoked(**values):
            receipt = append(**values)
            self.connections.identity = replace(self.connections.identity, revoked=True)
            return receipt

        self.audit.append_read = revoked
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool("get_profile", {})
            self.assertTrue(result.isError)
            self.assertIsNone(result.structuredContent)
            self.assertEqual(result.content[0].text, "UNAUTHENTICATED")

    async def test_initialize_discover_and_authenticated_profile_roundtrip(self):
        async with create_connected_server_and_client_session(self.server) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual(
                [t.name for t in tools],
                ["get_profile", "direction_read_summary", "direction_list_scopes"],
            )
            profile = tools[0].model_dump(by_alias=True, exclude_none=True)
            self.assertIs(profile["_meta"]["openai/profile"], True)
            self.assertFalse(profile["inputSchema"]["additionalProperties"])
            self.assertTrue(profile["annotations"]["readOnlyHint"])
            result = await client.call_tool("get_profile", {})
            self.assertFalse(result.isError)
            self.assertEqual(
                result.structuredContent, {"id": "prf_synthetic_account_A"}
            )
            self.assertEqual(
                json.loads(result.content[0].text), result.structuredContent
            )
            self.assertNotIn("employee-A", result.content[0].text)
        self.assertEqual(self.reader.calls, [])
        self.assertGreaterEqual(len(self.connections.calls), 2)

    async def test_profile_stable_reconnect_unique_other_account_and_no_selector(self):
        async with create_connected_server_and_client_session(self.server) as client:
            original = await client.call_tool("get_profile", {})
            self.connections.identity = replace(self.connections.identity, grant_id="B")
            repeat = await client.call_tool("get_profile", {})
            self.assertEqual(original.structuredContent, repeat.structuredContent)
            self.connections.identity = replace(
                self.connections.identity,
                actor_id="employee-B",
                profile_id="prf_synthetic_account_B",
            )
            other = await client.call_tool("get_profile", {})
            self.assertNotEqual(original.structuredContent, other.structuredContent)
            for args in (
                {"user_id": "A"},
                {"actor": "admin"},
                {"tenant": "X"},
                {"bearer": "synthetic"},
                {"scopes": ["*"]},
            ):
                result = await client.call_tool("get_profile", args)
                self.assertTrue(result.isError)
                self.assertEqual(result.content[0].text, "INVALID_ARGUMENTS")

    async def test_revoke_expire_wrong_audience_missing_profile_and_malformed_identity(
        self,
    ):
        good = self.connections.identity
        async with create_connected_server_and_client_session(self.server) as client:
            for change in (
                {"active": False},
                {"revoked": True},
                {"expires_at": 100},
                {"issuer": "other"},
                {"audience": "other"},
                {"profile_id": ""},
                {"organization_id": ""},
                {"active": "true"},
                {"oauth_scopes": "direction:read"},
            ):
                self.connections.identity = replace(good, **change)
                result = await client.call_tool("get_profile", {})
                self.assertEqual(result.content[0].text, "UNAUTHENTICATED")
                with self.assertRaises(McpError):
                    await client.list_tools()

    async def test_scopes_restrict_discovery_and_call_not_business_authority(self):
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertEqual(result.content[0].text, "FORBIDDEN")
            self.assertEqual(
                self.reader.calls[0]["identity"], self.connections.identity
            )
            self.connections.identity = replace(
                self.connections.identity, oauth_scopes=frozenset()
            )
            tools = (await client.list_tools()).tools
            self.assertEqual([t.name for t in tools], ["get_profile"])
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertEqual(result.content[0].text, "FORBIDDEN")
            self.assertEqual(len(self.reader.calls), 1)
            self.assertFalse((await client.call_tool("get_profile", {})).isError)

    async def test_selector_schemas_reject_actor_company_free_sql_and_bad_values(self):
        async with create_connected_server_and_client_session(self.server) as client:
            for args in (
                {},
                {"year": True},
                {"year": 1900},
                {"year": "2026"},
                {"year": 2026, "tournament_id": "not-an-id"},
                {"year": 2026, "portfolio_id": None},
                {"year": 2026, "organization_id": "organization-B"},
                {"year": 2026, "actor": "superadmin"},
                {"year": 2026, "sql": "synthetic query"},
            ):
                result = await client.call_tool("direction_read_summary", args)
                self.assertEqual(result.content[0].text, "INVALID_ARGUMENTS")
            for name in (
                "sql",
                "execute_canonical_action",
                "registration.commit",
                "transfer.create_draft",
                "events/subscribe",
            ):
                result = await client.call_tool(name, {})
                self.assertEqual(result.content[0].text, "CAPABILITY_DISABLED")
        self.assertEqual(self.reader.calls, [])

    async def test_audit_failure_prevents_release_and_dependency_errors_sanitized(self):
        async with create_connected_server_and_client_session(self.server) as client:
            self.reader.error = RuntimeError("synthetic-private-error")
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertEqual(result.content[0].text, "DEPENDENCY_UNAVAILABLE")
            self.audit.failure = True
            result = await client.call_tool("get_profile", {})
            self.assertEqual(result.content[0].text, "AUDIT_UNAVAILABLE")
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertEqual(result.content[0].text, "AUDIT_UNAVAILABLE")
            self.assertEqual(len(self.reader.calls), 1)
            with self.assertRaises(McpError):
                await client.list_tools()
        self.assertNotIn("synthetic-private-error", str(self.audit.rows))

    async def test_no_arguments_profile_and_missing_durable_read_receipt(self):
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool("get_profile")
            self.assertFalse(result.isError)
            self.audit.append_read = lambda **_: ""
            result = await client.call_tool("get_profile", {})
            self.assertEqual(result.content[0].text, "AUDIT_UNAVAILABLE")
            self.assertIsNone(result.structuredContent)


class CanonicalMCPReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = direction_fixture.DirectionReadTests()
        self.fixture.setUp()
        self.connections, self.audit = Connections(), Audit()
        self.connections.identity = replace(
            self.fixture.identity,
            profile_id="prf_synthetic_direction",
        )
        self.fixture.identity = self.connections.identity
        self.fixture.context.return_value = DirectionContext(
            self.connections.identity,
            self.fixture.employee,
            self.fixture.session,
        )
        self.server = create_local_read_server(
            identities=self.connections,
            audit=self.audit,
            direction=self.fixture.adapter,
            current_connection=lambda: "trusted-reference",
            now=lambda: 100,
            issuer="issuer",
            audience="audience",
            installation_id="installation",
        )

    async def test_handshake_discovery_actual_canonical_read_and_retry(self):
        async with create_connected_server_and_client_session(self.server) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual(len(tools), 3)
            self.assertTrue(all(t.annotations.readOnlyHint for t in tools))
            for _ in range(2):
                result = await client.call_tool(
                    "direction_read_summary",
                    {
                        "year": 2026,
                        "tournament_id": direction_fixture.TOURNAMENT,
                    },
                )
                self.assertFalse(result.isError, result)
                data = result.structuredContent
                values = {v["id"]: v for v in data["indicators"]}
                self.assertEqual(values["actual"]["value"], "40.00")
                self.assertIsNone(values["receivables"]["value"])
                self.assertEqual(data["tournament_ids"], [direction_fixture.TOURNAMENT])
                self.assertEqual(json.loads(result.content[0].text), data)
                self.assertNotIn("payment_evidence", result.content[0].text)
                self.assertEqual(self.audit.rows[-1]["evidence_digest"], digest(data))
                self.assertEqual(len(self.audit.rows[-1]["scope_digest"]), 64)
            self.assertEqual(self.fixture.build.await_count, 2)
            self.assertTrue(
                all(
                    q.strip().startswith("SELECT")
                    for q, _ in self.fixture.session.queries
                )
            )
        self.assertNotIn("40.00", str(self.audit.rows))

    async def test_cross_user_company_and_foreign_object_denied_before_data(self):
        async with create_connected_server_and_client_session(self.server) as client:
            self.fixture.context.return_value = DirectionContext(
                replace(self.connections.identity, actor_id="other-user"),
                self.fixture.employee,
                self.fixture.session,
            )
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertEqual(result.content[0].text, "UNAUTHENTICATED")
            self.fixture.context.return_value = DirectionContext(
                self.connections.identity,
                self.fixture.employee,
                self.fixture.session,
            )
            self.fixture.mapping.return_value = "other-organization"
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertEqual(result.content[0].text, "ORGANIZATION_UNPROVEN")
            self.fixture.mapping.return_value = "organization"
            result = await client.call_tool(
                "direction_read_summary",
                {
                    "year": 2026,
                    "tournament_id": direction_fixture.FOREIGN,
                },
            )
            self.assertEqual(result.content[0].text, "FORBIDDEN")
        self.fixture.build.assert_not_awaited()

    async def test_scope_listing_sdk_receipt_and_strict_noargs(self):
        import base64
        import json

        async with create_connected_server_and_client_session(self.server) as client:
            await client.list_tools()
            result = await client.call_tool("direction_list_scopes", {})
            self.assertFalse(result.isError, result)
            self.assertEqual(
                result.structuredContent["tournaments"],
                [
                    {
                        "id": direction_fixture.TOURNAMENT,
                        "label": "Fixture tournament",
                    }
                ],
            )
            self.assertEqual(
                self.audit.rows[-1]["evidence_digest"], digest(result.structuredContent)
            )
            cursor = base64.urlsafe_b64encode(
                json.dumps(
                    [result.structuredContent["scope_manifest"]["scope_digest"], 0]
                ).encode()
            ).decode()
            repeated = await client.call_tool(
                "direction_list_scopes", {"cursor": cursor}
            )
            self.assertFalse(repeated.isError, repeated)
            self.assertEqual(repeated.structuredContent, result.structuredContent)
            for arguments in (
                {"actor_id": "other"},
                {"year": 2026},
                {"portfolio_id": direction_fixture.PORTFOLIO},
                {"cursor": 1},
            ):
                denied = await client.call_tool("direction_list_scopes", arguments)
                self.assertEqual(denied.content[0].text, "INVALID_ARGUMENTS")
            self.audit.append_read = lambda **_: ""
            failed = await client.call_tool("direction_list_scopes", {})
            self.assertEqual(failed.content[0].text, "AUDIT_UNAVAILABLE")
            self.assertIsNone(failed.structuredContent)
        self.fixture.build.assert_not_awaited()
        self.fixture.budget.assert_not_awaited()

    async def test_source_denial_uses_canonical_gaps_without_zero_or_data(self):
        self.fixture.decisions["admin.finanzas"] = False
        self.fixture.decisions["admin.presupuestos"] = False
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertFalse(result.isError, result)
            for row in result.structuredContent["indicators"]:
                self.assertIsNone(row["value"])
                self.assertTrue(row["gaps"])
        self.fixture.budget.assert_not_awaited()
        self.fixture.payments.assert_not_awaited()
        self.fixture.receivables.assert_not_awaited()

    async def test_revocation_during_read_discards_result(self):
        original = self.fixture.adapter.read

        async def revoke(**kwargs):
            data = await original(**kwargs)
            self.connections.identity = replace(self.connections.identity, revoked=True)
            return data

        self.fixture.adapter.read = revoke
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertTrue(result.isError)
            self.assertEqual(result.content[0].text, "UNAUTHENTICATED")
            self.assertIsNone(result.structuredContent)
        self.assertNotIn("READ_VERIFIED", str(self.audit.rows))

    async def test_canonical_source_revocation_during_read_discards_result(self):
        canonical = self.fixture.build._mock_wraps

        async def revoke_source(*args, **kwargs):
            data = await canonical(*args, **kwargs)
            self.fixture.decisions["admin.finanzas"] = False
            return data

        self.fixture.build.side_effect = revoke_source
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertTrue(result.isError)
            self.assertEqual(result.content[0].text, "SCOPE_CHANGED")
            self.assertIsNone(result.structuredContent)
        self.assertNotIn("READ_VERIFIED", str(self.audit.rows))

    async def test_malformed_reader_output_never_gets_verified_receipt(self):
        self.fixture.adapter.read = AsyncMock(return_value={"private": "synthetic"})
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertEqual(result.content[0].text, "CANONICAL_RESULT_INVALID")
        self.assertNotIn("READ_VERIFIED", str(self.audit.rows))
        self.assertNotIn("synthetic", str(self.audit.rows))

    async def test_post_read_receipt_failure_discards_canonical_result(self):
        self.audit.append_read = lambda **_: ""
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertEqual(result.content[0].text, "AUDIT_UNAVAILABLE")
            self.assertIsNone(result.structuredContent)
        self.fixture.build.assert_awaited_once()

    async def test_changed_report_year_does_not_escape_scope(self):
        canonical = self.fixture.build._mock_wraps

        async def changed(*args, **kwargs):
            snapshot, scope = await canonical(*args, **kwargs)
            snapshot["edition_year"] = 2025
            return snapshot, scope

        self.fixture.build.side_effect = changed
        async with create_connected_server_and_client_session(self.server) as client:
            result = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertEqual(result.content[0].text, "SCOPE_CHANGED")


if __name__ == "__main__":
    unittest.main()
