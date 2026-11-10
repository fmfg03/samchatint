"""A2/A6: registration matching is server-bound and never a model label."""

import pytest

from devnous.gastos.services.batisenal_case_authority import verified_provenance
from devnous.gastos.services.batisenal_service import BatisenalError

CONFIG = dict(
    issuer="https://pilot.example/issuer",
    audience="https://pilot.example/mcp",
    client_id="registered-client",
    registration_sha256="a" * 64,
)
BINDING = dict(
    agent_kind="ai_agent",
    agent_client_id=CONFIG["client_id"],
    agent_issuer=CONFIG["issuer"],
    agent_resource=CONFIG["audience"],
    agent_registration_sha256=CONFIG["registration_sha256"],
)


def test_matching_registered_client_provenance():
    assert verified_provenance(BINDING, **CONFIG) == "ai_agent"


@pytest.mark.parametrize("field", list(BINDING))
def test_mutated_binding_fails_closed(field):
    with pytest.raises(BatisenalError):
        verified_provenance({**BINDING, field: "other"}, **CONFIG)


@pytest.mark.parametrize("field", list(CONFIG))
def test_mutated_server_registration_fails_closed(field):
    with pytest.raises(BatisenalError):
        verified_provenance(BINDING, **{**CONFIG, field: "other"})


def test_missing_migration_never_promotes_stub():
    with pytest.raises(BatisenalError):
        verified_provenance({}, **CONFIG)


def test_stub_remains_explicit_and_rejects_real_registration():
    binding = {key: None for key in BINDING}
    binding["agent_kind"] = "synthetic_stub"
    assert (
        verified_provenance(binding, **{**CONFIG, "registration_sha256": None})
        == "synthetic_stub"
    )
    with pytest.raises(BatisenalError):
        verified_provenance(binding, **CONFIG)
