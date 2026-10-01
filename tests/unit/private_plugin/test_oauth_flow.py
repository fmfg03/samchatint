"""Synthetic in-process OAuth flow with real local SQL transactions/signatures.

No HTTP listeners, users, credentials or persistent grants in a real service.
Temporary files contain synthetic existing employees only.
"""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import jwt
import test_authorization as authorization_fixture
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine, insert, select, update

from samchat.private_plugin.authorization import SessionPrincipal
from samchat.private_plugin.contracts import Denied
from samchat.private_plugin.oauth_flow import BrowserSession, JWTSigner, LocalOAuthFlow
from samchat.private_plugin.oauth_identity import CurrentEmployee
from samchat.private_plugin.oauth_store import (
    AUDIT,
    CODES,
    EMPLOYEES,
    GRANTS,
    LINKS,
    PENDING,
    OAuthStore,
    OAuthTransaction,
    create_local_fixture_schema,
)
from samchat.private_plugin.perimeter import digest

EMPLOYEE = "10000000-0000-0000-0000-000000000001"
OTHER = "10000000-0000-0000-0000-000000000002"


class OAuthFlowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = ec.generate_private_key(ec.SECP256R1())

    def setUp(self):
        fixture = authorization_fixture.AuthorizationTests()
        fixture.setUp()
        self.config, self.parameters, self.verifier = (
            fixture.config,
            fixture.parameters,
            fixture.verifier,
        )
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = create_engine(
            f"sqlite:///{Path(self.directory.name) / 'oauth.sqlite'}"
        )
        self.addCleanup(self.engine.dispose)
        EMPLOYEES.create(self.engine)
        with self.engine.begin() as connection:
            connection.execute(
                insert(EMPLOYEES),
                [{"id": EMPLOYEE, "activo": True}, {"id": OTHER, "activo": True}],
            )
        create_local_fixture_schema(self.engine)
        self.store = OAuthStore(
            self.engine,
            issuer=self.config.issuer,
            installation_id="current-installation",
            organization_id="installation-namespace",
        )
        self.clock = 100
        self.browser = BrowserSession(SessionPrincipal(EMPLOYEE), "b" * 43, "c" * 43)
        self.employee_reads = []

        async def browser():
            return self.browser

        async def employee(employee_id):
            self.employee_reads.append(employee_id)
            with self.engine.connect() as connection:
                row = (
                    connection.execute(
                        select(EMPLOYEES).where(EMPLOYEES.c.id == employee_id)
                    )
                    .mappings()
                    .one_or_none()
                )
            return CurrentEmployee(employee_id, row["activo"] if row else False)

        self.flow = LocalOAuthFlow(
            config=self.config,
            installation_id="current-installation",
            organization_id="installation-namespace",
            store=self.store,
            current_browser=browser,
            current_employee=employee,
            signer=JWTSigner("synthetic-key", self.key),
            now=lambda: self.clock,
        )

    def rows(self, table):
        with self.engine.connect() as connection:
            return connection.execute(select(table)).mappings().all()

    async def preview(self):
        return await self.flow.begin(list(self.parameters.items()))

    async def confirm(self, preview, **changes):
        args = dict(
            draft_id=preview.draft_id,
            fingerprint=preview.fingerprint,
            csrf=preview.csrf,
            state=preview.state,
            approved=True,
        )
        return await self.flow.confirm(**{**args, **changes})

    async def code(self):
        return await self.confirm(await self.preview())

    def exchange_parameters(self, code, **changes):
        return list(
            {
                "grant_type": "authorization_code",
                "code": code.code,
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "resource": self.config.resource,
                "code_verifier": self.verifier,
                **changes,
            }.items()
        )

    async def test_complete_code_pkce_token_revoke_is_audited_and_hash_only(self):
        preview = await self.preview()
        self.assertNotIn(preview.csrf, repr(preview))
        self.assertNotIn(preview.state, repr(preview))
        pending = self.rows(PENDING)[0]
        self.assertEqual(pending["request_json"]["state"], digest(preview.state))
        self.assertEqual(pending["csrf_hash"], digest(preview.csrf))
        code = await self.confirm(preview)
        self.assertEqual(code.state, self.parameters["state"])
        self.assertEqual(code.redirect_uri, self.config.redirect_uri)
        self.assertNotIn(code.code, repr(code))
        token = await self.flow.exchange(self.exchange_parameters(code))
        self.assertNotIn(token.access_token, repr(token))
        claims = jwt.decode(
            token.access_token,
            self.key.public_key(),
            algorithms=["ES256"],
            issuer=self.config.issuer,
            audience=self.config.resource,
            options={"verify_exp": False, "verify_nbf": False, "verify_iat": False},
        )
        grant = self.rows(GRANTS)[0]
        self.assertEqual(claims["jti"], grant["token_id"])
        self.assertEqual(grant["employee_id"], EMPLOYEE)
        self.assertEqual(grant["organization_id"], "installation-namespace")
        self.assertEqual(token.scope, "direction:read")
        self.assertEqual(claims["exp"], self.clock + token.expires_in)
        self.assertNotIn("refresh_token", vars(token))
        self.assertTrue(self.rows(CODES)[0]["used"])
        self.assertTrue(self.rows(PENDING)[0]["used"])
        revoked = await self.flow.revoke(
            grant_id=token.grant_id, csrf=self.browser.csrf
        )
        self.assertTrue(revoked.revoked)
        self.assertTrue(self.rows(GRANTS)[0]["revoked"])
        self.assertEqual(
            [r["action_id"] for r in self.rows(AUDIT)],
            ["oauth.begin", "oauth.confirm", "oauth.exchange", "oauth.revoke"],
        )
        self.assertEqual(self.employee_reads, [EMPLOYEE] * 4)
        with self.engine.connect() as connection:
            dump = "\n".join(connection.connection.driver_connection.iterdump())
        for secret in (
            preview.state,
            preview.csrf,
            self.browser.browser_binding,
            code.code,
            token.access_token,
            self.verifier,
        ):
            self.assertNotIn(secret, dump)

    async def test_exact_consent_draft_csrf_browser_and_state(self):
        preview = await self.preview()
        for changes in (
            {"fingerprint": "0" * 64},
            {"csrf": "z" * 43},
            {"state": "other-client-state"},
            {"approved": False},
            {"approved": 1},
        ):
            with self.assertRaisesRegex(Denied, "CONSENT_REJECTED"):
                await self.confirm(preview, **changes)
        for browser in (
            replace(self.browser, browser_binding="z" * 43),
            replace(self.browser, csrf="z" * 43),
            replace(self.browser, principal=SessionPrincipal(OTHER)),
        ):
            original, self.browser = self.browser, browser
            with self.assertRaises(Denied):
                await self.confirm(preview)
            self.browser = original
        self.assertFalse(self.rows(PENDING)[0]["used"])
        self.assertEqual(self.rows(CODES), [])
        await self.confirm(preview)
        with self.assertRaisesRegex(Denied, "CONSENT_REJECTED"):
            await self.confirm(preview)

    async def test_exchange_exact_client_resource_redirect_pkce_and_one_use(self):
        code = await self.code()
        for changes in (
            {"client_id": "foreign"},
            {"resource": "https://other.invalid"},
            {"redirect_uri": "https://client.invalid/evil"},
            {"code_verifier": "w" * 43},
            {"grant_type": "refresh_token"},
            {"actor_id": OTHER},
        ):
            with self.assertRaisesRegex(Denied, "INVALID_GRANT"):
                await self.flow.exchange(self.exchange_parameters(code, **changes))
        with self.assertRaisesRegex(Denied, "INVALID_GRANT"):
            await self.flow.exchange(
                self.exchange_parameters(code) + [("client_id", self.config.client_id)]
            )
        self.assertFalse(self.rows(CODES)[0]["used"])
        await self.flow.exchange(self.exchange_parameters(code))
        with self.assertRaisesRegex(Denied, "INVALID_GRANT"):
            await self.flow.exchange(self.exchange_parameters(code))
        self.assertEqual(len(self.rows(GRANTS)), 1)

    async def test_expired_consent_and_code_and_current_employee(self):
        preview = await self.preview()
        self.clock = preview.expires_at
        with self.assertRaisesRegex(Denied, "CONSENT_REJECTED"):
            await self.confirm(preview)
        self.clock = 100
        code = await self.code()
        with self.engine.begin() as connection:
            connection.execute(
                update(EMPLOYEES).where(EMPLOYEES.c.id == EMPLOYEE).values(activo=False)
            )
        with self.assertRaisesRegex(Denied, "UNAUTHENTICATED"):
            await self.flow.exchange(self.exchange_parameters(code))
        with self.engine.begin() as connection:
            connection.execute(
                update(EMPLOYEES).where(EMPLOYEES.c.id == EMPLOYEE).values(activo=True)
            )
        self.clock = self.rows(CODES)[0]["expires_at"]
        with self.assertRaisesRegex(Denied, "INVALID_GRANT"):
            await self.flow.exchange(self.exchange_parameters(code))
        self.assertFalse(self.rows(CODES)[0]["used"])

    async def test_reassigned_or_revoked_link_cannot_change_code_identity(self):
        code = await self.code()
        original = self.rows(LINKS)[0]
        for change in (
            {"profile_id": "f" * 32},
            {"employee_id": OTHER},
            {"active": False},
        ):
            with self.engine.begin() as connection:
                connection.execute(update(LINKS).values(**change))
            with self.assertRaisesRegex(Denied, "INVALID_GRANT"):
                await self.flow.exchange(self.exchange_parameters(code))
            with self.engine.begin() as connection:
                connection.execute(
                    update(LINKS).values(
                        profile_id=original["profile_id"],
                        employee_id=EMPLOYEE,
                        active=True,
                    )
                )
        self.assertFalse(self.rows(CODES)[0]["used"])

    async def test_audit_failure_rolls_back_consent_code_grant_and_consumption(self):
        with patch.object(
            OAuthTransaction, "append_audit", side_effect=RuntimeError("private-error")
        ):
            with self.assertRaisesRegex(Denied, "^OAUTH_UNAVAILABLE$"):
                await self.preview()
        self.assertEqual(self.rows(PENDING), [])
        preview = await self.preview()
        with patch.object(
            OAuthTransaction, "append_audit", side_effect=RuntimeError("private-error")
        ):
            with self.assertRaisesRegex(Denied, "^OAUTH_UNAVAILABLE$"):
                await self.confirm(preview)
        self.assertFalse(self.rows(PENDING)[0]["used"])
        self.assertEqual(self.rows(LINKS), [])
        code = await self.confirm(preview)
        with patch.object(
            OAuthTransaction, "append_audit", side_effect=RuntimeError("private-error")
        ):
            with self.assertRaisesRegex(Denied, "^OAUTH_UNAVAILABLE$"):
                await self.flow.exchange(self.exchange_parameters(code))
        self.assertFalse(self.rows(CODES)[0]["used"])
        self.assertEqual(self.rows(GRANTS), [])
        await self.flow.exchange(self.exchange_parameters(code))

    async def test_signing_failure_or_expiry_while_signing_rolls_back(self):
        code = await self.code()
        with patch.object(
            JWTSigner, "sign", side_effect=RuntimeError("private-key-error")
        ):
            with self.assertRaisesRegex(Denied, "^OAUTH_UNAVAILABLE$"):
                await self.flow.exchange(self.exchange_parameters(code))
        self.assertFalse(self.rows(CODES)[0]["used"])
        self.assertEqual(self.rows(GRANTS), [])

        def expired(_):
            self.clock += 1000
            return "synthetic-not-returned"

        with patch.object(JWTSigner, "sign", side_effect=expired):
            with self.assertRaisesRegex(Denied, "^INVALID_GRANT$"):
                await self.flow.exchange(self.exchange_parameters(code))
        self.assertFalse(self.rows(CODES)[0]["used"])

    async def test_consent_expiring_during_link_lock_rolls_back(self):
        preview = await self.preview()
        original = OAuthTransaction.get_or_create_link

        def delayed(transaction, employee_id):
            result = original(transaction, employee_id)
            self.clock = preview.expires_at
            return result

        with patch.object(OAuthTransaction, "get_or_create_link", delayed):
            with self.assertRaisesRegex(Denied, "CONSENT_REJECTED"):
                await self.confirm(preview)
        self.assertFalse(self.rows(PENDING)[0]["used"])
        self.assertEqual(self.rows(CODES), [])
        self.assertEqual(self.rows(LINKS), [])
        self.assertEqual(len(self.rows(AUDIT)), 1)

    async def test_code_expiring_during_consume_lock_rolls_back(self):
        code = await self.code()
        original = OAuthTransaction.current_employee

        def delayed(transaction, employee_id):
            result = original(transaction, employee_id)
            self.clock += 120
            return result

        with patch.object(OAuthTransaction, "current_employee", delayed):
            with self.assertRaisesRegex(Denied, "INVALID_GRANT"):
                await self.flow.exchange(self.exchange_parameters(code))
        self.assertFalse(self.rows(CODES)[0]["used"])
        self.assertEqual(self.rows(GRANTS), [])
        self.assertEqual(len(self.rows(AUDIT)), 2)

    async def test_expiration_during_final_audit_rolls_back_entire_exchange(self):
        code = await self.code()
        original = OAuthTransaction._verify_audits

        def delayed(transaction):
            original(transaction)
            self.clock += 120

        with patch.object(OAuthTransaction, "_verify_audits", delayed):
            with self.assertRaisesRegex(Denied, "INVALID_GRANT"):
                await self.flow.exchange(self.exchange_parameters(code))
        self.assertFalse(self.rows(CODES)[0]["used"])
        self.assertEqual(self.rows(GRANTS), [])
        self.assertEqual(len(self.rows(AUDIT)), 2)

    async def test_revocation_is_owner_csrf_bound_and_audit_atomic(self):
        token = await self.flow.exchange(self.exchange_parameters(await self.code()))
        with self.assertRaisesRegex(Denied, "FORBIDDEN"):
            await self.flow.revoke(grant_id=token.grant_id, csrf="z" * 43)
        original = self.browser
        self.browser = replace(self.browser, principal=SessionPrincipal(OTHER))
        with self.assertRaisesRegex(Denied, "FORBIDDEN"):
            await self.flow.revoke(grant_id=token.grant_id, csrf=self.browser.csrf)
        self.browser = original
        with patch.object(
            OAuthTransaction, "append_audit", side_effect=RuntimeError("private-error")
        ):
            with self.assertRaisesRegex(Denied, "OAUTH_UNAVAILABLE"):
                await self.flow.revoke(grant_id=token.grant_id, csrf=self.browser.csrf)
        self.assertFalse(self.rows(GRANTS)[0]["revoked"])

    async def test_reconsent_keeps_stable_profile_and_does_not_provision(self):
        first = await self.flow.exchange(self.exchange_parameters(await self.code()))
        original_link = dict(self.rows(LINKS)[0])
        await self.flow.revoke(grant_id=first.grant_id, csrf=self.browser.csrf)
        second = await self.flow.exchange(self.exchange_parameters(await self.code()))
        self.assertNotEqual(first.grant_id, second.grant_id)
        self.assertEqual([dict(row) for row in self.rows(LINKS)], [original_link])
        self.assertEqual(len(self.rows(EMPLOYEES)), 2)
        with self.engine.begin() as connection:
            connection.execute(
                update(EMPLOYEES).where(EMPLOYEES.c.id == EMPLOYEE).values(activo=False)
            )
        with self.assertRaisesRegex(Denied, "UNAUTHENTICATED"):
            await self.preview()
        self.assertFalse(
            next(row for row in self.rows(EMPLOYEES) if row["id"] == EMPLOYEE)["activo"]
        )


if __name__ == "__main__":
    unittest.main()
