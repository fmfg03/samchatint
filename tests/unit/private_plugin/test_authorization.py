"""Local authorization policy and canonical session dependency; no grants."""

import base64
import hashlib
import logging
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from starlette.datastructures import QueryParams
from test_direction_read import ACTOR, source_functions

from samchat.private_plugin.authorization import (
    AuthorizationConfig,
    ConsentDraft,
    SessionPrincipal,
    confirm_consent,
    pkce_matches,
    resolve_session_principal,
    validate_authorization_request,
    validate_code_exchange,
)
from samchat.private_plugin.contracts import Denied
from samchat.private_plugin.perimeter import digest


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.config = AuthorizationConfig(
            "https://samchat.invalid/plugin-oauth",
            "https://samchat.invalid/mcp",
            "review-client",
            "https://client.invalid/callback",
            frozenset({"direction:read"}),
        )
        self.verifier = "v" * 43
        self.challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self.parameters = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "resource": self.config.resource,
            "scope": "direction:read",
            "state": "opaque-client-state",
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
        }
        self.request = validate_authorization_request(
            self.config, list(self.parameters.items())
        )

    def test_metadata_and_pkce_binding(self):
        self.assertEqual(
            self.config.resource_metadata()["resource"], self.config.resource
        )
        metadata = self.config.authorization_metadata()
        self.assertEqual(metadata["code_challenge_methods_supported"], ["S256"])
        self.assertNotIn("registration_endpoint", metadata)
        self.assertNotIn("refresh_token", metadata["grant_types_supported"])
        validate_code_exchange(
            config=self.config,
            consented_request=self.request,
            client_id=self.config.client_id,
            redirect_uri=self.config.redirect_uri,
            resource=self.config.resource,
            verifier=self.verifier,
        )
        self.assertFalse(pkce_matches("bad", self.challenge))
        self.assertFalse(pkce_matches("w" * 43, self.challenge))
        for key in ("client_id", "redirect_uri", "resource", "verifier"):
            arguments = dict(
                config=self.config,
                consented_request=self.request,
                client_id=self.config.client_id,
                redirect_uri=self.config.redirect_uri,
                resource=self.config.resource,
                verifier=self.verifier,
            )
            arguments[key] = "other"
            with self.subTest(key=key), self.assertRaises(Denied):
                validate_code_exchange(**arguments)

    def test_request_rejects_wildcards_duplicates_identity_and_downgrades(self):
        duplicate_query = QueryParams(
            list(self.parameters.items()) + [("scope", "direction:read")]
        )
        for parameters in (
            duplicate_query,
            self.parameters,
            duplicate_query.multi_items(),
        ):
            with self.assertRaises(Denied):
                validate_authorization_request(self.config, parameters)
        for key, value in (
            ("actor", ACTOR),
            ("resource", "https://other.invalid"),
            ("redirect_uri", "https://client.invalid/callback/evil"),
            ("scope", "direction:read direction:read"),
            ("scope", "expenses:write"),
            ("scope", "*"),
            ("code_challenge_method", "plain"),
            ("response_type", "token"),
            ("state", "x"),
            ("code_challenge", "x"),
            ("client_id", "unknown"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(Denied):
                validate_authorization_request(
                    self.config, list({**self.parameters, key: value}.items())
                )
        with self.assertRaises(Denied):
            validate_authorization_request(
                self.config,
                list(self.parameters.items()) + [("scope", "direction:read")],
            )
        for changes in (
            {"issuer": "http://samchat.invalid"},
            {"redirect_uri": "https://client.invalid/#bad"},
            {"scopes": frozenset({"expenses:write"})},
        ):
            with self.assertRaises(ValueError):
                replace(self.config, **changes)

    def test_consent_is_exact_current_account_browser_request_and_expiry(self):
        draft = ConsentDraft(
            ACTOR, self.request, digest("browser"), digest("csrf"), 200
        )
        arguments = dict(
            draft=draft,
            principal=SessionPrincipal(ACTOR),
            browser_binding="browser",
            csrf="csrf",
            fingerprint=draft.fingerprint,
            approved=True,
            now=100,
        )
        self.assertEqual(confirm_consent(**arguments), self.request)
        for key, value in (
            ("principal", SessionPrincipal("other")),
            ("browser_binding", "other"),
            ("csrf", "other"),
            ("fingerprint", "old"),
            ("approved", False),
            ("approved", 1),
            ("now", 200),
        ):
            with self.subTest(key=key), self.assertRaises(Denied):
                confirm_consent(**{**arguments, key: value})
        changed = replace(
            draft, request=replace(self.request, state="different-client-state")
        )
        with self.assertRaises(Denied):
            confirm_consent(**{**arguments, "draft": changed})

    def test_malformed_challenges_and_stored_codes_fail_closed(self):
        for challenge in (None, {}, "á" * 43, "a" * 42):
            self.assertFalse(pkce_matches(self.verifier, challenge))
            with self.assertRaisesRegex(Denied, "^INVALID_GRANT$"):
                validate_code_exchange(
                    config=self.config,
                    consented_request=replace(self.request, challenge=challenge),
                    client_id=self.config.client_id,
                    redirect_uri=self.config.redirect_uri,
                    resource=self.config.resource,
                    verifier=self.verifier,
                )


class CanonicalSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_existing_employee_reload_and_no_impersonation(self):
        employee = SimpleNamespace(id=UUID(ACTOR), activo=True)
        loader = AsyncMock(return_value=employee)
        namespace = dict(
            Depends=lambda fn: None,
            get_db_session=lambda: None,
            UUID=UUID,
            HTTPException=lambda **kwargs: RuntimeError("unauthenticated"),
            _load_empleado_proxy_by_id=loader,
            visible_tools_for=AsyncMock(return_value=set()),
            _uses_route_owned_authorization=lambda _: False,
            can_access_path=AsyncMock(return_value=True),
            _login_redirect_for_request=lambda _: "/login",
            logger=logging.getLogger("offline-session-test"),
        )
        canonical = source_functions(
            "src/devnous/gastos/routes/dependencies.py",
            {"get_current_empleado"},
            namespace,
        )["get_current_empleado"]
        request = SimpleNamespace(
            session={"empleado_id": ACTOR},
            url=SimpleNamespace(path="/plugin-consent"),
            method="GET",
        )
        arguments = dict(
            request=request, session=object(), get_current_empleado=canonical
        )
        principal = await resolve_session_principal(**arguments)
        self.assertEqual(principal.employee_id, ACTOR)
        loader.assert_awaited_once()
        request.session["impersonator_empleado_id"] = "admin"
        with self.assertRaises(Denied):
            await resolve_session_principal(**arguments)
        request.session.pop("impersonator_empleado_id")
        employee.activo = False
        with self.assertRaises(Denied):
            await resolve_session_principal(**arguments)
        self.assertEqual(request.session, {})
        with self.assertRaises(Denied):
            await resolve_session_principal(**arguments)
