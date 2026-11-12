"""Review candidate: exact historical HTTP pilot plus unwired identity adapter.

Not executed in the preparing environment: native Unix sockets are denied.
No dataset is admitted. Existing case authority remains the decision maker.
"""
from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import text
from test_application import result, rpc
from test_batisenal import login
from test_case_http import app, base_app, mounted, ready, scenario  # fixture aliases
from test_case_operations import request

from devnous.gastos.routes.batisenal_pilot_routes import PREFIX
from samchat.batisenal_pilot import _bearer
from samchat.batisenal_transport.http import current_connection
from samchat.private_plugin.contracts import Identity
from samchat.readonly_gateway.authenticated_binding import (
    AuthenticatedBindingProvider, BindingAdmission,
)
from samchat.readonly_gateway.canonical_authority import ServerBinding
from samchat.readonly_gateway.contracts import ReadError


@pytest.fixture
async def observed(mounted, monkeypatch):
    s = mounted
    observations = []
    # Empty ceiling is deliberate: support consent must NOT admit datasets.
    def ceilings(identity):
        return ServerBinding(
            actor_binding="synthetic-composition-only",
            employee_id=UUID(identity.actor_id),
            installation=identity.installation_id,
            generation="synthetic-composition-v1",
            policy_version="no-dataset-admission-v1",
            expires_at=datetime.fromtimestamp(identity.expires_at, timezone.utc),
            ceilings=frozenset(),
        )

    provider = AuthenticatedBindingProvider(
        identities=s.composition.identity,
        current_connection=current_connection,
        ceilings_for=ceilings,
        admission=BindingAdmission(
            issuer=s.config.issuer, audience=s.config.resource,
            installation_id=s.composition.resource.installation_id,
            organization_id=s.composition.pilot.organization,
            required_scopes=frozenset({"batisenal.support"}),
        ),
    )
    original = s.composition.bridge.identity

    def observe_identity():
        identity = original()
        # Observation only: the original identity goes unchanged to case_authority.
        # Capture adapter exceptions for assertions AFTER the actual HTTP response,
        # never replace the pilot's decision with the adapter's result.
        try:
            binding = provider()
            error = None
        except Exception as exc:
            binding, error = None, type(exc).__name__
        observations.append((identity, binding, error, current_connection()))
        return identity

    monkeypatch.setattr(s.composition.bridge, "identity", observe_identity)
    yield s, provider, observations
    assert _bearer.get() is None
    assert current_connection() == ""


def assert_observations(observations):
    assert observations
    for identity, binding, error, connection in observations:
        assert isinstance(identity, Identity)
        assert error is None
        assert binding.employee_id == UUID(identity.actor_id)
        assert binding.installation == identity.installation_id
        assert binding.expires_at.timestamp() <= identity.expires_at
        assert binding.ceilings == frozenset()
        assert connection == "support-request"


async def test_a_actual_http_read_and_no_actor_selection(observed):
    s, provider, observations = observed
    bearer = await ready(s)
    result(await rpc(s, bearer, "get_case_context", request(s.case_id)))
    assert_observations(observations)
    first_actor = observations[0][0].actor_id
    rejected = await rpc(s, bearer, "get_case_context",
                         request(s.case_id, actor_id="other-employee"))
    assert "error" in rejected.json()
    assert all(i.actor_id == first_actor for i, _, _, _ in observations)
    assert _bearer.get() is None and current_connection() == ""
    with pytest.raises(ReadError):
        provider()


async def test_b_browser_session_denied_not_a_b_mcp_claim(observed):
    # B uses the existing canonical signed browser login. This does NOT mint a
    # second MCP grant and does NOT prove authenticated-B MCP denial.
    s, _, observations = observed
    bearer = await ready(s)
    result(await rpc(s, bearer, "get_case_context", request(s.case_id)))
    assert_observations(observations)
    await login(s.http, "bob")
    await s.http.get("/fixture/browser")
    response = await s.http.get(PREFIX + "/case")
    assert response.status_code in (403, 404)


async def test_revocation_after_operation_before_delivery(observed, monkeypatch):
    s, provider, observations = observed
    bearer = await ready(s)
    result(await rpc(s, bearer, "get_case_context", request(s.case_id)))
    assert_observations(observations)
    grant_id = observations[-1][0].grant_id
    original_audit = s.composition.bridge.audit_attempt
    revoked = []

    def revoke_after_audit(actor, action, outcome):
        receipt = original_audit(actor, action, outcome)
        if action.endswith("get_case_context") and outcome == "operation_complete" and not revoked:
            # Only the already-issued synthetic fixture grant. No real credentials.
            with s.engine.begin() as c:
                changed = c.execute(text(
                    "UPDATE private_oauth_grants SET revoked=true WHERE grant_id=:id"
                ), {"id": grant_id})
                assert changed.rowcount == 1
            revoked.append(True)
        return receipt

    monkeypatch.setattr(s.composition.bridge, "audit_attempt", revoke_after_audit)
    response = await rpc(s, bearer, "get_case_context", request(s.case_id))
    assert revoked
    assert "error" in response.json() and "result" not in response.json()
    assert response.json()["error"]["message"] == "UNAUTHENTICATED"
    assert (await rpc(s, bearer, "get_case_context", request(s.case_id))).status_code == 401
    with pytest.raises(ReadError):
        provider()
