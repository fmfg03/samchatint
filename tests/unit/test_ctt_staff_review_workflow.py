"""Public completion states must follow protected durable results."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.copa_telmex.staff_review_workflow import (
    ReviewWorkflowDenied,
    ReviewWorkflowService,
)
from devnous.copa_telmex.staff_capture_finalizing_writer import _blocking_incident_count
from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_transport import CaptureTransportUncertain


@pytest.mark.parametrize("key", [b"r" * 32, b"e" * 32, b"x", None])
def test_operations_key_cannot_reuse_authority_domains(key):
    with pytest.raises(ValueError):
        service().validate_channel_key(key)


@pytest.mark.parametrize(
    "status",
    [
        "AWAITING_REGISTRATION",
        "PENDING_OPERATOR_REVIEW",
        "ACTIVE",
        "EXPIRED",
        "REVOKED",
    ],
)
def test_enrollment_states_remain_distinct_from_faculty(status):
    result = ReviewWorkflowService._enrollment_view(
        {"status": status, "grants_faculty": False}
    )
    assert result["grants_faculty"] is False
    if status == "PENDING_OPERATOR_REVIEW":
        assert result["state"] == "pending_activation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "private,state",
    [
        ({"committed": False, "is_current": False, "expired": False}, "stale"),
        ({"committed": False, "is_current": True, "expired": True}, "expired"),
        (
            {
                "committed": False,
                "is_current": True,
                "expired": False,
                "signed_review": b"privateproof",
                "payload_admitted": False,
            },
            "uncertain",
        ),
    ],
)
async def test_protected_status_drives_recovery_without_leaking_proof(private, state):
    workflow = service(store=SimpleNamespace(status=AsyncMock(return_value=private)))
    result = await workflow.status(uuid4(), uuid4(), uuid4())
    assert result["state"] == state
    assert "signed_review" not in result


@pytest.mark.asyncio
async def test_inactive_binding_view_never_claims_an_active_credential():
    class Unaccredited(ValueError):
        code = "ENROLLMENT_REQUIRED"

    workflow = service(
        store=SimpleNamespace(
            resolve_source_scope=AsyncMock(return_value={}),
            load_context=AsyncMock(side_effect=Unaccredited()),
        )
    )
    result = await workflow.view(uuid4(), uuid4())
    assert result["passkey"]["status"] == "not_enrolled"
    assert result["ready_to_approve"] is False


@pytest.mark.asyncio
async def test_enrollment_complete_checks_scope_before_creating_candidate():
    enrollment = SimpleNamespace(
        status=AsyncMock(
            return_value={"tenant_id": "other", "edition_id": str(uuid4())}
        ),
        complete=AsyncMock(),
    )
    workflow = service(
        store=SimpleNamespace(
            resolve_source_scope=AsyncMock(
                return_value={"tenant_id": "correct", "edition_id": str(uuid4())}
            )
        ),
        enrollment=enrollment,
    )
    with pytest.raises(ReviewWorkflowDenied):
        await workflow.enrollment_complete(uuid4(), uuid4(), uuid4(), {})
    enrollment.complete.assert_not_awaited()


def service(**overrides):
    policy = {
        "minimum_roster_size": 16,
        "maximum_roster_size": 35,
        "policy_hash": "sha256:" + "a" * 64,
    }
    values = dict(
        store=SimpleNamespace(),
        builder=None,
        enrollment=None,
        issuer=None,
        business_gate=None,
        capture_client=None,
        private_source_root="/tmp/ctt-synthetic-only",
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
        gate_public_keys={},
        policy_artifact=policy,
        expected_policy_hash=policy["policy_hash"],
        max_source_bytes=1000,
        max_asset_bytes=1000,
        max_manifest_bytes=1000,
    )
    values.update(overrides)
    return ReviewWorkflowService(**values)


@pytest.mark.parametrize("minimum,maximum", [(1, 35), (True, 35), (16, 36), (17, 16)])
def test_no_operational_minimum_or_capacity_bypass(minimum, maximum):
    with pytest.raises(ValueError):
        service(
            policy_artifact={
                "minimum_roster_size": minimum,
                "maximum_roster_size": maximum,
                "policy_hash": "sha256:" + "a" * 64,
            }
        )


@pytest.mark.asyncio
async def test_socket_acknowledgment_does_not_mean_official_completion():
    account, session, attempt, proposal = [uuid4() for _ in range(4)]
    # Already admitted payload: resume uses the stored signed proof and same
    # attempt; it does not reissue or open another review operation.
    store = SimpleNamespace(
        status=AsyncMock(
            return_value={
                "committed": False,
                "payload_admitted": True,
                "is_current": True,
                "proposal_id": str(proposal),
                "signed_review": b"protected",
            }
        ),
        load_context=AsyncMock(return_value={}),
        read_proposal=AsyncMock(return_value={}),
    )
    capture = SimpleNamespace(execute=AsyncMock())
    workflow = service(store=store, capture_client=capture)
    actual = await workflow.assert_and_capture(account, session, attempt, None)
    assert actual["state"] == "uncertain"
    assert actual["attempt_id"] == str(attempt)
    capture.execute.assert_awaited_once()
    assert capture.execute.await_args.args == (attempt, b"protected")


@pytest.mark.asyncio
async def test_lost_socket_response_preserves_same_attempt_for_recovery():
    account, session, attempt, proposal = [uuid4() for _ in range(4)]
    store = SimpleNamespace(
        status=AsyncMock(
            return_value={
                "committed": False,
                "payload_admitted": True,
                "is_current": True,
                "proposal_id": str(proposal),
                "signed_review": b"protected",
            }
        ),
        load_context=AsyncMock(return_value={}),
        read_proposal=AsyncMock(return_value={}),
    )
    capture = SimpleNamespace(
        execute=AsyncMock(side_effect=CaptureTransportUncertain())
    )
    actual = await service(store=store, capture_client=capture).assert_and_capture(
        account, session, attempt, None
    )
    assert actual == {
        "state": "uncertain",
        "session_id": str(session),
        "attempt_id": str(attempt),
    }


@pytest.mark.asyncio
async def test_committed_retry_is_read_only_even_after_challenge_expiry():
    account, session, attempt, result = [uuid4() for _ in range(4)]
    store = SimpleNamespace(
        status=AsyncMock(
            return_value={
                "committed": True,
                "result_id": str(result),
                "session_status": "committed",
            }
        )
    )
    capture = SimpleNamespace(execute=AsyncMock())
    workflow = service(store=store, capture_client=capture)
    actual = await workflow.assert_and_capture(account, session, attempt, None)
    assert actual == {
        "state": "committed",
        "session_id": str(session),
        "attempt_id": str(attempt),
        "result_id": str(result),
    }
    capture.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        {"committed": True, "session_status": "draft", "result_id": str(uuid4())},
        {"committed": True, "session_status": "committed"},
    ],
)
async def test_private_result_cannot_override_missing_official_session(status):
    workflow = service(store=SimpleNamespace(status=AsyncMock(return_value=status)))
    with pytest.raises(ReviewWorkflowDenied):
        await workflow.status(uuid4(), uuid4(), uuid4())


@pytest.mark.asyncio
async def test_missing_confirmation_cannot_open_challenge():
    workflow = service()
    with pytest.raises(ReviewWorkflowDenied):
        await workflow.challenge(uuid4(), uuid4(), uuid4(), 1)


@pytest.mark.asyncio
async def test_enrollment_cross_edition_is_denied():
    edition = uuid4()
    workflow = service(
        store=SimpleNamespace(
            resolve_source_scope=AsyncMock(
                return_value={"tenant_id": "test", "edition_id": str(edition)}
            )
        ),
        enrollment=SimpleNamespace(
            status=AsyncMock(
                return_value={
                    "tenant_id": "test",
                    "edition_id": str(uuid4()),
                    "status": "ACTIVE",
                    "grants_faculty": False,
                }
            )
        ),
    )
    with pytest.raises(ReviewWorkflowDenied, match="SCOPE_DENIED"):
        await workflow.enrollment_status(uuid4(), uuid4(), uuid4())


@pytest.mark.parametrize("status", ["PENDING_ACTIVATION", "ACTIVE"])
def test_even_active_credential_does_not_grant_faculty(status):
    with pytest.raises(ReviewWorkflowDenied):
        ReviewWorkflowService._enrollment_view(
            {"status": status, "grants_faculty": True}
        )


def test_actual_nonblocking_incident_policy_is_accepted_without_granting_eligibility():
    validation = {
        "incident_policy": {
            "player_results": [
                {
                    "player_index": 1,
                    "eligibility_status": "INCIDENT_PENDING",
                    "counts_toward_minimum": True,
                    "incidents": [
                        {
                            "blocks_player_eligibility": False,
                            "blocks_team_registration": False,
                        }
                    ],
                }
            ]
        },
        "blockers": [],
    }
    assert _blocking_incident_count(validation, [1]) == 0
    assert (
        validation["incident_policy"]["player_results"][0]["eligibility_status"]
        == "INCIDENT_PENDING"
    )


def test_actual_pending_blocker_is_not_turned_into_zero():
    validation = {
        "incident_policy": {
            "player_results": [
                {
                    "player_index": 1,
                    "eligibility_status": "INCIDENT_PENDING",
                    "counts_toward_minimum": False,
                    "incidents": [
                        {
                            "blocks_player_eligibility": True,
                            "blocks_team_registration": False,
                        }
                    ],
                }
            ]
        },
        "blockers": [],
    }
    assert _blocking_incident_count(validation, [1]) == 1


@pytest.mark.parametrize("flag", [None, 1, 0, "true"])
def test_pending_status_requires_real_boolean_count(flag):
    validation = {
        "incident_policy": {
            "player_results": [
                {
                    "player_index": 1,
                    "eligibility_status": "INCIDENT_PENDING",
                    "counts_toward_minimum": flag,
                    "incidents": [
                        {
                            "blocks_player_eligibility": False,
                            "blocks_team_registration": False,
                        }
                    ],
                }
            ]
        },
        "blockers": [],
    }
    with pytest.raises(CaptureDenied):
        _blocking_incident_count(validation, [1])


@pytest.mark.asyncio
async def test_initial_view_uses_read_only_photo_preparation():
    import json
    from contextlib import contextmanager
    from unittest.mock import Mock

    snapshot = object()

    @contextmanager
    def retained(*args):
        yield snapshot

    context = {}
    builder = SimpleNamespace(
        initial_scope=Mock(return_value=object()),
        build=Mock(
            return_value=SimpleNamespace(
                preview_bytes=json.dumps({"staff": [], "blockers": []}).encode()
            )
        ),
    )
    store = SimpleNamespace(
        resolve_source_scope=AsyncMock(), load_context=AsyncMock(return_value=context)
    )
    workflow = service(store=store, builder=builder)
    workflow._snapshot = retained
    result = await workflow.view(uuid4(), uuid4())
    assert result["state"] == "unpublished"
    assert result["ready_to_approve"] is False
    assert builder.build.call_args.kwargs == {"stage_photos": False}
    assert builder.build.call_args.args[2] is snapshot
