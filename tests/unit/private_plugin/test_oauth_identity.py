"""Synthetic JWT signatures and existing-record fixtures; no grants are issued.

Keys live in test process memory only and do not represent any real account.
These tests verify the resource verifier, not an IdP or production OAuth flow.
"""

import unittest
from dataclasses import replace

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from samchat.private_plugin.contracts import Denied
from samchat.private_plugin.oauth_identity import (
    CurrentEmployee,
    ExistingGrant,
    ExistingLink,
    OAuthIdentityProvider,
    OAuthResourceConfig,
    VerificationKey,
)


class RecordsFixture:
    """Read-only interface; mutation methods deliberately do not exist."""

    def __init__(self):
        self.grant = ExistingGrant(
            grant_id="existing-grant",
            issuer="https://fixture.invalid",
            subject="subject-a",
            token_id="token-a",
            client_id="client-a",
            installation_id="current-installation",
            link_id="existing-link",
            scopes=frozenset({"direction:read", "expenses:read"}),
            expires_at=180,
            active=True,
            revoked=False,
            employee_id="existing-employee",
            organization_id="installation-partition",
            profile_id="opaque-existing-profile",
        )
        self.link = ExistingLink(
            link_id="existing-link",
            issuer="https://fixture.invalid",
            subject="subject-a",
            installation_id="current-installation",
            employee_id="existing-employee",
            organization_id="installation-partition",
            profile_id="opaque-existing-profile",
            active=True,
        )
        self.employee = CurrentEmployee("existing-employee", True)
        self.calls = []

    def read_grant(self, issuer, subject, token_id):
        self.calls.append(("grant", issuer, subject, token_id))
        return self.grant

    def read_link(self, link_id):
        self.calls.append(("link", link_id))
        return self.link

    def read_employee(self, employee_id):
        self.calls.append(("employee", employee_id))
        return self.employee


class OAuthIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = ec.generate_private_key(ec.SECP256R1())
        cls.other_key = ec.generate_private_key(ec.SECP256R1())

    def setUp(self):
        self.config = OAuthResourceConfig(
            issuer="https://fixture.invalid",
            audience="samchat-resource",
            installation_id="current-installation",
            allowed_scopes=frozenset({"direction:read", "expenses:read"}),
            allowed_clients=frozenset({"client-a"}),
            keys={
                "fixture-key": VerificationKey("ES256", self.signing_key.public_key())
            },
        )
        self.claims = {
            "iss": self.config.issuer,
            "aud": self.config.audience,
            "sub": "subject-a",
            "jti": "token-a",
            "client_id": "client-a",
            "iat": 80,
            "nbf": 90,
            "exp": 200,
            "scope": "direction:read expenses:read",
        }
        self.records = RecordsFixture()
        self.token = self.sign()
        self.references = []
        self.provider = OAuthIdentityProvider(
            config=self.config,
            transport_bearer=self.bearer,
            records=self.records,
            now=lambda: 100,
        )

    def sign(self, changes=None, *, key=None, headers=None):
        return jwt.encode(
            {**self.claims, **(changes or {})},
            key or self.signing_key,
            algorithm="ES256",
            headers={"kid": "fixture-key", "typ": "at+jwt", **(headers or {})},
        )

    def bearer(self, reference):
        self.references.append(reference)
        return self.token

    def denied(self):
        with self.assertRaisesRegex(Denied, "^UNAUTHENTICATED$"):
            self.provider.resolve_current("transport-reference")

    def test_real_signature_maps_only_existing_identity_and_stable_profile(self):
        identity = self.provider.resolve_current("transport-reference")
        self.assertEqual(identity.actor_id, "existing-employee")
        self.assertEqual(identity.profile_id, "opaque-existing-profile")
        self.assertEqual(identity.organization_id, "installation-partition")
        self.assertEqual(identity.expires_at, 180)
        self.assertEqual(identity.oauth_scopes, self.records.grant.scopes)
        self.assertEqual(
            [r[0] for r in self.records.calls], ["grant", "link", "employee"]
        )
        self.assertEqual(self.references, ["transport-reference"])

    def test_token_cannot_choose_actor_role_organization_or_profile(self):
        self.token = self.sign(
            {
                "actor_id": "other-employee",
                "role": "superadmin",
                "organization_id": "foreign-company",
                "profile_id": "invented",
            }
        )
        identity = self.provider.resolve_current("transport-reference")
        self.assertEqual(identity.actor_id, self.records.link.employee_id)
        self.assertEqual(identity.organization_id, self.records.link.organization_id)
        self.assertEqual(identity.profile_id, self.records.link.profile_id)

    def test_bad_signature_rejected_before_records(self):
        self.token = self.sign(key=self.other_key)
        self.denied()
        self.assertEqual(self.records.calls, [])

    def test_no_unsigned_symmetric_or_token_supplied_key_sources(self):
        self.token = jwt.encode(
            self.claims,
            key=None,
            algorithm="none",
            headers={"kid": "fixture-key", "typ": "at+jwt"},
        )
        self.denied()
        self.token = jwt.encode(
            self.claims,
            "synthetic-key-for-fixtures-only-0000000",
            algorithm="HS256",
            headers={"kid": "fixture-key", "typ": "at+jwt"},
        )
        self.denied()
        for header in (
            {"kid": "unknown"},
            {"typ": "JWT"},
            {"jku": "https://fixture.invalid/keys"},
            {"jwk": {"kty": "RSA"}},
            {"x5u": "https://fixture.invalid/cert"},
            {"crit": ["unknown"]},
        ):
            self.token = self.sign(headers=header)
            self.denied()
        self.assertEqual(self.records.calls, [])

    def test_wrong_issuer_audience_subject_token_and_client_bindings(self):
        for change in (
            {"iss": "https://other.invalid"},
            {"aud": "other-resource"},
            {"aud": [self.config.audience, "other"]},
            {"sub": "other-subject"},
            {"jti": "other-token"},
            {"client_id": "other-client"},
            {"azp": "other-client"},
            {"installation_id": "foreign-installation"},
        ):
            with self.subTest(change=change):
                self.token = self.sign(change)
                self.denied()

    def test_strict_time_and_required_claims(self):
        for change in (
            {"exp": 100},
            {"exp": 99},
            {"nbf": 101},
            {"iat": 101},
            {"iat": -1},
            {"exp": "200"},
            {"exp": 200.5},
            {"nbf": True},
            {"iat": 80.0},
        ):
            self.token = self.sign(change)
            self.denied()
        for missing in self.claims:
            claims = {k: v for k, v in self.claims.items() if k != missing}
            self.token = jwt.encode(
                claims,
                self.signing_key,
                algorithm="ES256",
                headers={"kid": "fixture-key", "typ": "at+jwt"},
            )
            self.denied()

    def test_scopes_are_strict_and_cannot_exceed_current_grant(self):
        for scope in (
            "*",
            "unknown:scope",
            "direction:read direction:read",
            "",
            "direction:read\nexpenses:read",
            ["direction:read"],
            " direction:read",
        ):
            self.token = self.sign({"scope": scope})
            self.denied()
        self.token = self.sign()
        self.records.grant = replace(
            self.records.grant, scopes=frozenset({"direction:read"})
        )
        identity = self.provider.resolve_current("transport-reference")
        self.assertEqual(identity.oauth_scopes, frozenset({"direction:read"}))

    def test_revocation_and_employee_changes_are_reloaded_each_action(self):
        self.provider.resolve_current("transport-reference")
        original = self.records.grant
        self.records.grant = replace(original, revoked=True)
        self.denied()
        self.records.grant = original
        self.records.employee = CurrentEmployee("existing-employee", False)
        self.denied()
        self.assertEqual(sum(c[0] == "grant" for c in self.records.calls), 3)

    def test_missing_inactive_or_malformed_existing_records_deny(self):
        for field in ("grant", "link", "employee"):
            original = getattr(self.records, field)
            for invalid in (
                None,
                replace(original, active=False),
                replace(original, active="true"),
            ):
                setattr(self.records, field, invalid)
                self.denied()
            setattr(self.records, field, original)
        grant = self.records.grant
        for changes in (
            {"revoked": 0},
            {"expires_at": 100},
            {"expires_at": 180.0},
            {"scopes": frozenset()},
            {"scopes": {"direction:read"}},
            {"grant_id": ""},
        ):
            self.records.grant = replace(grant, **changes)
            self.denied()

    def test_foreign_installation_and_reused_links_deny(self):
        grant, link = self.records.grant, self.records.link
        for change in (
            {"installation_id": "foreign"},
            {"issuer": "other"},
            {"subject": "other"},
            {"token_id": "other"},
            {"client_id": "other"},
        ):
            self.records.grant = replace(grant, **change)
            self.denied()
        self.records.grant = grant
        for change in (
            {"installation_id": "foreign"},
            {"issuer": "other"},
            {"subject": "other"},
            {"link_id": "other-link"},
            {"organization_id": ""},
            {"employee_id": "other-employee"},
            {"profile_id": ""},
            {"profile_id": "existing-employee"},
            {"profile_id": "private@example.invalid"},
        ):
            self.records.link = replace(link, **change)
            self.denied()

    def test_stable_profile_survives_synthetic_token_rotation(self):
        first = self.provider.resolve_current("transport-reference")
        self.token = self.sign({"jti": "rotated-token", "exp": 300})
        self.records.grant = replace(
            self.records.grant,
            token_id="rotated-token",
            grant_id="rotated-existing-grant",
        )
        second = self.provider.resolve_current("transport-reference")
        self.assertEqual(first.profile_id, second.profile_id)

    def test_existing_bearer_cannot_follow_reassigned_link(self):
        self.provider.resolve_current("transport-reference")
        original = self.records.link
        for changes in (
            {"employee_id": "second-employee"},
            {"organization_id": "second-organization"},
            {"profile_id": "second-profile"},
        ):
            self.records.link = replace(original, **changes)
            self.records.employee = CurrentEmployee(self.records.link.employee_id, True)
            self.denied()

    def test_sanitized_errors_and_no_tokens_in_logs(self):
        def failed(_):
            raise RuntimeError("synthetic-private-error " + self.token)

        self.provider._bearer = failed
        with self.assertNoLogs(level="DEBUG"):
            self.denied()

    def test_missing_malformed_or_oversized_transport_bearer(self):
        for token in (None, "", "not-a-jwt", "x" * 16385, b"not-a-string"):
            self.token = token
            self.denied()
        self.assertEqual(self.records.calls, [])

    def test_rsa_signature_and_trusted_algorithm_configuration(self):
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        config = replace(
            self.config, keys={"rsa": VerificationKey("RS256", rsa_key.public_key())}
        )
        self.token = jwt.encode(
            self.claims,
            rsa_key,
            algorithm="RS256",
            headers={"kid": "rsa", "typ": "at+jwt"},
        )
        provider = OAuthIdentityProvider(
            config=config,
            records=self.records,
            transport_bearer=self.bearer,
            now=lambda: 100,
        )
        self.assertEqual(
            provider.resolve_current("transport-reference").actor_id,
            "existing-employee",
        )
        with self.assertRaisesRegex(ValueError, "INVALID_OAUTH_RESOURCE_CONFIGURATION"):
            replace(config, keys={"bad": VerificationKey("HS256", "synthetic")})


if __name__ == "__main__":
    unittest.main()
