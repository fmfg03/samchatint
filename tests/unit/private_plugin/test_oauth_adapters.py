"""Fresh SQL reads and committed MCP receipts against synthetic file fixtures."""

import unittest

import test_oauth_flow as fixture_module
from sqlalchemy import event

from samchat.private_plugin.oauth_adapters import (
    SQLAlchemyIdentityRecords,
    SQLAlchemyReadAudit,
)
from samchat.private_plugin.oauth_store import (
    AUDIT,
    GRANTS,
    LINKS,
    PENDING,
    OAuthStore,
    OAuthStoreError,
    PendingRecord,
)
from samchat.private_plugin.perimeter import digest


class OAuthAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixture_module.OAuthFlowTests.setUpClass()
        self.fixture = fixture_module.OAuthFlowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.records = SQLAlchemyIdentityRecords(self.fixture.store)
        self.audit = SQLAlchemyReadAudit(self.fixture.store, now=lambda: 100)

    async def test_fresh_identity_reads_revocation_and_namespace_isolation(self):
        fixture = self.fixture
        token = await fixture.flow.exchange(
            fixture.exchange_parameters(await fixture.code())
        )
        grant = fixture.rows(GRANTS)[0]
        stored = self.records.read_grant(
            fixture.config.issuer, grant["subject"], grant["token_id"]
        )
        self.assertEqual(stored.grant_id, token.grant_id)
        self.assertTrue(self.records.read_employee(grant["employee_id"]).active)
        self.assertEqual(
            self.records.read_link(grant["link_id"]).profile_id, grant["profile_id"]
        )
        other = SQLAlchemyIdentityRecords(
            OAuthStore(
                fixture.engine,
                issuer=fixture.config.issuer,
                installation_id="current-installation",
                organization_id="foreign",
            )
        )
        self.assertIsNone(other.read_link(grant["link_id"]))
        self.assertIsNone(
            other.read_grant(fixture.config.issuer, grant["subject"], grant["token_id"])
        )
        self.assertIsNone(
            self.records.read_grant("other", grant["subject"], grant["token_id"])
        )
        self.assertIsNone(self.records.read_link("missing"))
        self.assertIsNone(
            self.records.read_employee(fixture_module.OTHER.replace("002", "003"))
        )
        await fixture.flow.revoke(grant_id=token.grant_id, csrf=fixture.browser.csrf)
        self.assertTrue(
            self.records.read_grant(
                fixture.config.issuer, grant["subject"], grant["token_id"]
            ).revoked
        )

    async def test_read_receipt_committed_minimal_and_actor_isolated(self):
        receipt = self.audit.append_read(
            actor_id=fixture_module.EMPLOYEE,
            action="direction_list_scopes",
            evidence_digest="a" * 64,
            scope_digest="b" * 64,
        )
        restored = self.audit.get_for_actor(receipt, actor_id=fixture_module.EMPLOYEE)
        self.assertEqual(restored.evidence_digest, "a" * 64)
        self.assertIsNone(
            self.audit.get_for_actor(receipt, actor_id=fixture_module.OTHER)
        )
        self.assertIsNone(
            self.audit.get_for_actor("0" * 32, actor_id=fixture_module.EMPLOYEE)
        )
        receipt2 = self.audit.append(
            actor_id=None, action="mcp.tools.call", outcome="UNAUTHENTICATED"
        )
        self.assertTrue(receipt2)
        self.assertEqual(len(self.fixture.rows(AUDIT)), 2)
        with self.assertRaises(OAuthStoreError):
            self.audit.append(
                actor_id=None, action="arbitrary_sql", outcome="READ_VERIFIED"
            )
        with self.assertRaises(OAuthStoreError):
            self.audit.get_for_actor("invalid", actor_id=fixture_module.EMPLOYEE)

    async def test_link_revocation_commits_all_grants_with_audit(self):
        fixture = self.fixture
        await fixture.flow.exchange(fixture.exchange_parameters(await fixture.code()))
        await fixture.flow.exchange(fixture.exchange_parameters(await fixture.code()))
        link = fixture.rows(LINKS)[0]
        with fixture.store.transaction(now=100) as transaction:
            transaction.revoke_link(
                link["link_id"], employee_id=fixture_module.EMPLOYEE
            )
            transaction.append_audit(
                actor_id=fixture_module.EMPLOYEE,
                action="oauth.revoke_link",
                outcome="LINK_REVOKED",
                evidence_digest="a" * 64,
                scope_digest="b" * 64,
            )
        self.assertFalse(fixture.rows(LINKS)[0]["active"])
        self.assertEqual(len(fixture.rows(GRANTS)), 2)
        self.assertTrue(
            all(g["revoked"] and not g["active"] for g in fixture.rows(GRANTS))
        )

    async def test_link_revocation_unverified_grants_roll_back(self):
        fixture = self.fixture
        await fixture.flow.exchange(fixture.exchange_parameters(await fixture.code()))
        link = fixture.rows(LINKS)[0]
        with fixture.engine.begin() as connection:
            connection.exec_driver_sql(
                """CREATE TRIGGER ignored_grant_revocation
                BEFORE UPDATE ON private_oauth_grants BEGIN SELECT RAISE(IGNORE); END"""
            )
        with self.assertRaisesRegex(OAuthStoreError, "STORE_WRITE_UNVERIFIED"):
            with fixture.store.transaction(now=100) as transaction:
                transaction.revoke_link(
                    link["link_id"], employee_id=fixture_module.EMPLOYEE
                )
        self.assertTrue(fixture.rows(LINKS)[0]["active"])
        self.assertFalse(fixture.rows(GRANTS)[0]["revoked"])

    async def test_write_without_audit_cannot_commit(self):
        preview = await self.fixture.preview()
        with self.fixture.store.transaction(now=100) as transaction:
            pending = transaction.read_pending(digest(preview.draft_id))
        copied = PendingRecord("f" * 64, pending.draft)
        with self.assertRaisesRegex(OAuthStoreError, "STORE_AUDIT_REQUIRED"):
            with self.fixture.store.transaction(
                now=100, clock=lambda: 100
            ) as transaction:
                transaction.put_pending(copied)
        self.assertEqual(len(self.fixture.rows(PENDING)), 1)

    async def test_ignored_audit_insert_returns_no_receipt(self):
        with self.fixture.engine.begin() as connection:
            connection.exec_driver_sql("""CREATE TRIGGER ignored_audit
                BEFORE INSERT ON agent_action_receipts
                BEGIN SELECT RAISE(IGNORE); END""")
        with self.assertRaisesRegex(OAuthStoreError, "STORE_WRITE_UNVERIFIED"):
            self.audit.append_read(
                actor_id=fixture_module.EMPLOYEE,
                action="get_profile",
                evidence_digest="a" * 64,
                scope_digest="b" * 64,
            )
        self.assertEqual(self.fixture.rows(AUDIT), [])

    async def test_failed_commit_returns_no_receipt_and_rolls_back(self):
        def failed(connection):
            raise RuntimeError("synthetic-commit-failure")

        event.listen(self.fixture.engine, "commit", failed)
        try:
            with self.assertRaisesRegex(OAuthStoreError, "STORE_UNAVAILABLE"):
                self.audit.append_read(
                    actor_id=fixture_module.EMPLOYEE,
                    action="get_profile",
                    evidence_digest="a" * 64,
                    scope_digest="b" * 64,
                )
        finally:
            event.remove(self.fixture.engine, "commit", failed)
        self.assertEqual(self.fixture.rows(AUDIT), [])
