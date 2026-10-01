"""Real SDK + signed synthetic OAuth + file-backed receipts, offline only."""

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import test_direction_read as direction_fixture
import test_oauth_identity as oauth_fixture
from mcp.shared.memory import create_connected_server_and_client_session

from samchat.private_plugin.direction_read import DirectionContext
from samchat.private_plugin.durable_audit import SQLiteAudit, initialize_local_audit
from samchat.private_plugin.local_reads import create_local_read_server
from samchat.private_plugin.perimeter import digest


class AuthenticatedReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_read_receipt_reopens_and_revocation_blocks_next_read(self):
        oauth_fixture.OAuthIdentityTests.setUpClass()
        auth = oauth_fixture.OAuthIdentityTests()
        auth.setUp()
        fixture = direction_fixture.DirectionReadTests()
        fixture.setUp()
        auth.records.link = replace(
            auth.records.link,
            employee_id=fixture.identity.actor_id,
            organization_id=fixture.identity.organization_id,
        )
        auth.records.grant = replace(
            auth.records.grant,
            employee_id=auth.records.link.employee_id,
            organization_id=auth.records.link.organization_id,
            profile_id=auth.records.link.profile_id,
        )
        auth.records.employee = replace(
            auth.records.employee, employee_id=fixture.identity.actor_id
        )
        identity = auth.provider.resolve_current("local-transport")
        fixture.context.return_value = DirectionContext(
            identity, fixture.employee, fixture.session
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite"
            connection = sqlite3.connect(path)
            initialize_local_audit(connection)
            audit = SQLiteAudit(connection)
            server = create_local_read_server(
                identities=auth.provider,
                audit=audit,
                direction=fixture.adapter,
                current_connection=lambda: "local-transport",
                now=lambda: 100,
                issuer=auth.config.issuer,
                audience=auth.config.audience,
                installation_id=auth.config.installation_id,
            )
            try:
                async with create_connected_server_and_client_session(server) as client:
                    scoped = await client.call_tool("direction_list_scopes", {})
                    self.assertFalse(scoped.isError, scoped)
                    scoped_receipt_id = scoped.meta["samchat/receipt"]
                    scoped_evidence = digest(scoped.structuredContent)
                    result = await client.call_tool(
                        "direction_read_summary", {"year": 2026}
                    )
                    self.assertFalse(result.isError, result)
                    receipt_id = result.meta["samchat/receipt"]
                    evidence = digest(result.structuredContent)
                    auth.records.grant = replace(auth.records.grant, revoked=True)
                    denied = await client.call_tool(
                        "direction_read_summary", {"year": 2026}
                    )
                    self.assertTrue(denied.isError)
                    self.assertEqual(denied.content[0].text, "UNAUTHENTICATED")
                    self.assertEqual(fixture.build.await_count, 1)
            finally:
                connection.close()
            reopened = sqlite3.connect(path)
            try:
                restored = SQLiteAudit(reopened)
                receipt = restored.get_for_actor(receipt_id, actor_id=identity.actor_id)
                self.assertEqual(receipt.evidence_digest, evidence)
                scope_receipt = restored.get_for_actor(
                    scoped_receipt_id, actor_id=identity.actor_id
                )
                self.assertEqual(scope_receipt.evidence_digest, scoped_evidence)
                self.assertEqual(scope_receipt.action, "direction_list_scopes")
                self.assertIsNone(restored.get_for_actor(receipt_id, actor_id="other"))
            finally:
                reopened.close()
