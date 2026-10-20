"""Actual review migrations + flow + MCP on runner-owned ephemeral PostgreSQL.

The engine is injected by run_postgres_tests.py; never accept a DSN or consult
runtime configuration. Business projections remain synthetic, with canonical
Direction guard/resolver bodies exercised by the existing fixture.
"""

import asyncio
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import test_direction_read as direction_fixture
import test_oauth_flow as flow_fixture
from http_fixture import FixtureHTTP
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import insert
from sqlalchemy.exc import DBAPIError

from samchat.private_plugin.contracts import Denied
from samchat.private_plugin.direction_read import DirectionContext
from samchat.private_plugin.local_reads import create_local_read_server
from samchat.private_plugin.oauth_adapters import (
    SQLAlchemyIdentityRecords,
    SQLAlchemyReadAudit,
)
from samchat.private_plugin.oauth_identity import (
    OAuthIdentityProvider,
    OAuthResourceConfig,
    VerificationKey,
)
from samchat.private_plugin.oauth_store import (
    AUDIT,
    CODES,
    EMPLOYEES,
    GRANTS,
    LINKS,
    PENDING,
    OAuthStore,
    OAuthTransaction,
)
from samchat.private_plugin.perimeter import digest

ROOT = Path(__file__).resolve().parents[3]


class PostgreSQLFlowTests(flow_fixture.OAuthFlowTests):
    def setUp(self):
        context = sys.modules.get("samchat_private_pg_fixture")
        if context is None:
            self.skipTest("Use isolated run_postgres_tests.py; external DBs forbidden")
        super().setUp()
        self.engine.dispose()
        self.engine = context.engine
        assert self.engine.url.query["host"].startswith("/tmp/samchat-plugin-pg-")
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
            connection.exec_driver_sql(
                "CREATE TABLE empleados (id UUID PRIMARY KEY, activo BOOLEAN NOT NULL)"
            )
            connection.exec_driver_sql(
                (
                    ROOT / "database/migrations/20260919_agent_action_receipts_v01.sql"
                ).read_text()
            )
            connection.execute(
                insert(EMPLOYEES),
                [
                    {"id": flow_fixture.EMPLOYEE, "activo": True},
                    {"id": flow_fixture.OTHER, "activo": True},
                ],
            )
        raw = self.engine.raw_connection()
        try:
            raw.driver_connection.autocommit = True
            raw.driver_connection.execute(
                (
                    ROOT
                    / "database/migrations/20261001_private_oauth_storage_review.sql"
                ).read_text()
            )
        finally:
            raw.driver_connection.autocommit = False
            raw.close()
        self.store = OAuthStore(
            self.engine,
            issuer=self.config.issuer,
            installation_id="current-installation",
            organization_id="installation-namespace",
        )
        self.flow.store = self.store

    async def test_complete_code_pkce_token_revoke_is_audited_and_hash_only(self):
        preview = await self.preview()
        code = await self.confirm(preview)
        token = await self.flow.exchange(self.exchange_parameters(code))
        await self.flow.revoke(grant_id=token.grant_id, csrf=self.browser.csrf)
        dump = json.dumps(
            {
                t.name: [dict(r) for r in self.rows(t)]
                for t in (PENDING, CODES, LINKS, GRANTS, AUDIT)
            },
            default=str,
        )
        for secret in (
            preview.csrf,
            preview.state,
            self.browser.browser_binding,
            code.code,
            token.access_token,
            self.verifier,
        ):
            self.assertNotIn(secret, dump)
        self.assertTrue(self.rows(GRANTS)[0]["revoked"])
        self.assertEqual(len(self.rows(AUDIT)), 4)
        self.assertEqual(len(self.rows(EMPLOYEES)), 2)

    async def test_concurrent_code_exchange_has_exactly_one_winner(self):
        code = await self.code()
        parameters = self.exchange_parameters(code)

        def attempt(_):
            try:
                asyncio.run(self.flow.exchange(parameters))
                return True
            except Denied:
                return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(4)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.rows(GRANTS)), 1)
        self.assertEqual(
            sum(r["action_id"] == "oauth.exchange" for r in self.rows(AUDIT)), 1
        )

    async def test_employee_select_only_role_cannot_take_required_row_lock(self):
        # A disposable NOLOGIN role in the runner-owned cluster proves the
        # activation gap without granting UPDATE or creating a real account.
        with self.engine.begin() as connection:
            connection.exec_driver_sql("CREATE ROLE fixture_employee_reader NOLOGIN")
            connection.exec_driver_sql(
                "GRANT USAGE ON SCHEMA public TO fixture_employee_reader"
            )
            connection.exec_driver_sql(
                "GRANT SELECT (id, activo) ON empleados TO fixture_employee_reader"
            )
        with self.engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL ROLE fixture_employee_reader")
            self.assertEqual(
                len(
                    connection.exec_driver_sql("SELECT id, activo FROM empleados").all()
                ),
                2,
            )
            with self.assertRaises(DBAPIError) as rejected:
                with connection.begin_nested():
                    connection.exec_driver_sql(
                        "SELECT id, activo FROM empleados FOR UPDATE"
                    )
            self.assertEqual(rejected.exception.orig.sqlstate, "42501")

    async def test_exchange_and_consent_share_employee_first_lock_order(self):
        code, preview = await self.code(), await self.preview()
        link_locked, confirm_attempt, confirm_locked, release = [
            threading.Event() for _ in range(4)
        ]
        original_employee, original_link = (
            OAuthTransaction.current_employee,
            OAuthTransaction.read_link,
        )
        failures = []

        def employee(transaction, employee_id):
            confirming = threading.current_thread().name == "oauth-confirm"
            if confirming:
                confirm_attempt.set()
            result = original_employee(transaction, employee_id)
            if confirming:
                confirm_locked.set()
            return result

        def link(transaction, link_id):
            result = original_link(transaction, link_id)
            if (
                threading.current_thread().name == "oauth-exchange"
                and not link_locked.is_set()
            ):
                link_locked.set()
                if not release.wait(5):
                    raise RuntimeError("FIXTURE_LOCK_TIMEOUT")
            return result

        def invoke(coroutine):
            try:
                asyncio.run(coroutine())
            except Exception as exc:
                failures.append(type(exc).__name__)

        exchange = threading.Thread(
            name="oauth-exchange",
            target=invoke,
            args=(lambda: self.flow.exchange(self.exchange_parameters(code)),),
        )
        confirm = threading.Thread(
            name="oauth-confirm", target=invoke, args=(lambda: self.confirm(preview),)
        )
        with patch.object(OAuthTransaction, "current_employee", employee), patch.object(
            OAuthTransaction, "read_link", link
        ):
            exchange.start()
            try:
                self.assertTrue(link_locked.wait(5))
                confirm.start()
                self.assertTrue(confirm_attempt.wait(5))
                self.assertFalse(confirm_locked.wait(0.1))
            finally:
                release.set()
                exchange.join(5)
                if confirm.ident is not None:
                    confirm.join(5)
        self.assertFalse(exchange.is_alive())
        self.assertFalse(confirm.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(confirm_locked.is_set())

    async def test_autocommit_configuration_is_rejected(self):
        autocommit = self.engine.execution_options(isolation_level="AUTOCOMMIT")
        invalid = OAuthStore(
            autocommit,
            issuer=self.config.issuer,
            installation_id="current-installation",
            organization_id="installation-namespace",
        )
        with self.assertRaisesRegex(Exception, "STORE_TRANSACTION_REQUIRED"):
            with invalid.transaction(now=100):
                pass
        self.assertEqual(self.rows(AUDIT), [])

    async def test_actual_database_audit_failure_rolls_back_token_and_code(self):
        code = await self.code()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("""
              CREATE FUNCTION reject_fixture_audit() RETURNS trigger
              LANGUAGE plpgsql AS $$
              BEGIN IF NEW.action_id='oauth.exchange' THEN RETURN NULL; END IF;
              RETURN NEW; END $$;
              CREATE TRIGGER reject_fixture_audit BEFORE INSERT ON agent_action_receipts
              FOR EACH ROW EXECUTE FUNCTION reject_fixture_audit();
            """)
        with self.assertRaisesRegex(Denied, "^OAUTH_UNAVAILABLE$"):
            await self.flow.exchange(self.exchange_parameters(code))
        self.assertFalse(self.rows(CODES)[0]["used"])
        self.assertEqual(self.rows(GRANTS), [])
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER reject_fixture_audit ON agent_action_receipts"
            )
        await self.flow.exchange(self.exchange_parameters(code))

    async def test_oauth_to_mcp_reads_current_authority_receipts_and_revocation(self):
        token = await self.flow.exchange(self.exchange_parameters(await self.code()))
        records = SQLAlchemyIdentityRecords(self.store)
        provider = OAuthIdentityProvider(
            config=OAuthResourceConfig(
                issuer=self.config.issuer,
                audience=self.config.resource,
                installation_id="current-installation",
                allowed_scopes=self.config.scopes,
                allowed_clients=frozenset({self.config.client_id}),
                keys={"synthetic-key": VerificationKey("ES256", self.key.public_key())},
            ),
            transport_bearer=lambda _: token.access_token,
            records=records,
            now=lambda: self.clock,
        )
        identity = provider.resolve_current("trusted-transport")
        fixture = direction_fixture.DirectionReadTests()
        fixture.setUp()
        fixture.mapping.return_value = identity.organization_id
        fixture.context.return_value = DirectionContext(
            identity, fixture.employee, fixture.session
        )
        audit = SQLAlchemyReadAudit(self.store, now=lambda: self.clock)
        server = create_local_read_server(
            identities=provider,
            audit=audit,
            direction=fixture.adapter,
            current_connection=lambda: "trusted-transport",
            now=lambda: self.clock,
            issuer=self.config.issuer,
            audience=self.config.resource,
            installation_id="current-installation",
        )
        async with create_connected_server_and_client_session(server) as client:
            self.assertEqual(len((await client.list_tools()).tools), 3)
            for action, arguments in (
                ("get_profile", {}),
                ("direction_list_scopes", {}),
                ("direction_read_summary", {"year": 2026}),
            ):
                result = await client.call_tool(action, arguments)
                self.assertFalse(
                    result.isError, result.content[0].text if result.isError else ""
                )
                receipt = audit.get_for_actor(
                    result.meta["samchat/receipt"], actor_id=identity.actor_id
                )
                self.assertEqual(
                    receipt.evidence_digest, digest(result.structuredContent)
                )
                self.assertIsNone(
                    audit.get_for_actor(receipt.receipt_id, actor_id=flow_fixture.OTHER)
                )
            fixture.decisions["direccion.tableros_ejecutivos"] = False
            denied = await client.call_tool("direction_read_summary", {"year": 2026})
            self.assertTrue(denied.isError)
            fixture.decisions.clear()
            fixture.mapping.return_value = "foreign-organization"
            denied = await client.call_tool("direction_list_scopes", {})
            self.assertTrue(denied.isError)
            fixture.mapping.return_value = identity.organization_id
            await self.flow.revoke(grant_id=token.grant_id, csrf=self.browser.csrf)
            denied = await client.call_tool("get_profile", {})
            self.assertTrue(denied.isError)
            self.assertEqual(denied.content[0].text, "UNAUTHENTICATED")
        self.assertEqual(fixture.build.await_count, 1)
        with self.assertRaisesRegex(Denied, "UNAUTHENTICATED"):
            provider.resolve_current("trusted-transport")

    async def test_mcp_audit_failure_suppresses_result(self):
        audit = SQLAlchemyReadAudit(self.store, now=lambda: self.clock)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("""
              CREATE FUNCTION reject_read_audit() RETURNS trigger LANGUAGE plpgsql AS $$
              BEGIN RETURN NULL; END $$;
              CREATE TRIGGER reject_read_audit BEFORE INSERT ON agent_action_receipts
              FOR EACH ROW EXECUTE FUNCTION reject_read_audit();
            """)
        with self.assertRaises(Exception):
            audit.append_read(
                actor_id=flow_fixture.EMPLOYEE,
                action="get_profile",
                evidence_digest="a" * 64,
                scope_digest="b" * 64,
            )
        self.assertEqual(self.rows(AUDIT), [])

    async def test_signed_canonical_session_http_pkce_consent_and_token(self):
        harness = FixtureHTTP(self)
        prefix = urlsplit(self.config.issuer).path
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=harness.app),
            base_url="https://samchat.invalid",
        ) as client:
            unauthenticated = await client.get(
                prefix + "/authorize", params=self.parameters
            )
            self.assertEqual(unauthenticated.status_code, 401)
            signed = await client.post(
                "/__fixture_session", json={"employee_id": flow_fixture.EMPLOYEE}
            )
            self.assertEqual(signed.status_code, 200)
            cookie = client.cookies.get("samchat_session")
            preview = await client.get(prefix + "/authorize", params=self.parameters)
            self.assertEqual(preview.status_code, 200)
            data = preview.json()
            self.assertEqual(preview.headers["cache-control"], "no-store")
            arguments = {
                k: data[k] for k in ("draft_id", "fingerprint", "csrf", "state")
            }
            denied = await client.post(
                prefix + "/consent", json={**arguments, "approved": False}
            )
            self.assertEqual(denied.status_code, 400)
            consent = await client.post(
                prefix + "/consent", json={**arguments, "approved": True}
            )
            self.assertEqual(consent.status_code, 303)
            callback = urlsplit(consent.headers["location"])
            self.assertEqual(
                callback.scheme + "://" + callback.netloc + callback.path,
                self.config.redirect_uri,
            )
            query = parse_qs(callback.query)
            self.assertEqual(query["state"], [self.parameters["state"]])
            self.assertEqual(query["iss"], [self.config.issuer])
            fields = {
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "resource": self.config.resource,
                "code_verifier": self.verifier,
            }
            token = await client.post(prefix + "/token", data=fields)
            self.assertEqual(token.status_code, 200)
            self.assertEqual(token.json()["token_type"], "Bearer")
            self.assertEqual(token.headers["cache-control"], "no-store")
            replay = await client.post(prefix + "/token", data=fields)
            self.assertEqual(replay.status_code, 400)
            metadata = (
                await client.get("/.well-known/oauth-authorization-server" + prefix)
            ).json()
            self.assertEqual(metadata["code_challenge_methods_supported"], ["S256"])
            self.assertNotIn("refresh_token", metadata["grant_types_supported"])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=harness.app),
            base_url="https://samchat.invalid",
            cookies={"samchat_session": cookie + "tampered"},
        ) as tampered:
            denied = await tampered.get(prefix + "/authorize", params=self.parameters)
            self.assertEqual(denied.status_code, 401)
