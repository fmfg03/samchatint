from __future__ import annotations

import pytest

from samchat.sports_platform.sponsor_media import (
    CLIENT_AUTHORIZATION_STATUS,
    build_sponsor_proof_package,
    create_approval_workflow,
    transition_approval_workflow,
)


def _sponsor():
    return {"id": "telmex"}


def _event():
    return {"id": "matchday_01", "tournament_id": "tor-1"}


def _obligations():
    return [
        {"id": "logo_backwall"},
        {"id": "recap_mention"},
        {"id": "score_card_logo"},
    ]


def _complete_evidence():
    return [
        {
            "id": "photo-1",
            "type": "photo",
            "linked_obligation_id": "logo_backwall",
            "source": "manual_upload",
        },
        {
            "id": "video-1",
            "type": "video",
            "linked_obligation_id": "recap_mention",
            "source": "video_manifest",
        },
        {
            "id": "shot-1",
            "type": "screenshot",
            "linked_obligation_id": "score_card_logo",
            "source": "render_manifest",
        },
    ]


def test_sponsor_proof_package_computes_missing_evidence_coverage():
    package = build_sponsor_proof_package(
        sponsor=_sponsor(),
        event=_event(),
        obligations=_obligations(),
        evidence_items=_complete_evidence()[:2],
    )

    assert package["package_id"] == "proof_pkg_telmex_matchday_01"
    assert package["sponsor_id"] == "telmex"
    assert package["event_id"] == "matchday_01"
    assert package["status"] == "missing_evidence"
    assert package["obligation_coverage"] == {
        "total_obligations": 3,
        "fulfilled": 2,
        "missing": 1,
        "coverage_ratio": 0.6667,
    }
    assert package["missing_evidence"] == [
        {"linked_obligation_id": "score_card_logo"}
    ]
    assert package["approval_required"] is True
    assert package["external_publishing_enabled"] is False
    assert package["manual_distribution_required"] is True
    assert package["client_authorization_status"] == CLIENT_AUTHORIZATION_STATUS
    assert all(
        item["requires_human_review"] is True
        for item in package["evidence_index"]
    )


def test_complete_sponsor_proof_package_is_ready_for_review_not_auto_approved():
    package = build_sponsor_proof_package(
        sponsor=_sponsor(),
        event=_event(),
        obligations=_obligations(),
        evidence_items=_complete_evidence(),
    )

    assert package["status"] == "ready_for_review"
    assert package["obligation_coverage"] == {
        "total_obligations": 3,
        "fulfilled": 3,
        "missing": 0,
        "coverage_ratio": 1.0,
    }
    assert package["missing_evidence"] == []
    assert package["external_publishing_enabled"] is False
    assert package["manual_distribution_required"] is True


def test_approved_proof_package_requires_ready_manual_distribution_state():
    workflow = _approved_workflow()
    workflow = transition_approval_workflow(
        workflow,
        to_state="ready_for_manual_distribution",
        actor_role="ops",
        action="mark_ready_for_manual_distribution",
        comment="Manual delivery package prepared.",
    )
    package = build_sponsor_proof_package(
        sponsor=_sponsor(),
        event=_event(),
        obligations=_obligations(),
        evidence_items=_complete_evidence(),
        approval_state=workflow["state"],
    )

    assert workflow["state"] == "ready_for_manual_distribution"
    assert package["status"] == "approved_for_manual_distribution"
    assert package["external_publishing_enabled"] is False
    assert package["manual_distribution_required"] is True


def test_approval_workflow_cannot_skip_directly_from_draft_to_approved():
    workflow = create_approval_workflow(asset_id="asset-1")

    with pytest.raises(ValueError, match="Invalid transition"):
        transition_approval_workflow(
            workflow,
            to_state="approved",
            actor_role="ops",
            action="approve",
        )


def test_sponsor_content_requires_ops_and_sponsor_review_before_approval():
    workflow = create_approval_workflow(asset_id="asset-1")
    workflow = transition_approval_workflow(
        workflow,
        to_state="automated_review",
        actor_role="system",
        action="submit_for_automated_review",
    )
    workflow = transition_approval_workflow(
        workflow,
        to_state="ops_review",
        actor_role="system",
        action="pass_automated_review",
    )

    with pytest.raises(ValueError, match="Invalid transition"):
        transition_approval_workflow(
            workflow,
            to_state="approved",
            actor_role="ops",
            action="approve",
        )


def test_changes_requested_and_rejected_require_comments():
    workflow = create_approval_workflow(asset_id="asset-1")
    workflow = transition_approval_workflow(
        workflow,
        to_state="automated_review",
        actor_role="system",
        action="submit_for_automated_review",
    )

    with pytest.raises(ValueError, match="require a comment"):
        transition_approval_workflow(
            workflow,
            to_state="changes_requested",
            actor_role="ops",
            action="request_changes",
        )

    with pytest.raises(ValueError, match="require a comment"):
        transition_approval_workflow(
            workflow,
            to_state="rejected",
            actor_role="ops",
            action="reject",
        )


def test_ready_for_manual_distribution_requires_approved_state():
    workflow = create_approval_workflow(asset_id="asset-1")
    workflow = transition_approval_workflow(
        workflow,
        to_state="automated_review",
        actor_role="system",
        action="submit_for_automated_review",
    )

    with pytest.raises(ValueError, match="Invalid transition"):
        transition_approval_workflow(
            workflow,
            to_state="ready_for_manual_distribution",
            actor_role="ops",
            action="mark_ready_for_manual_distribution",
            comment="Trying to skip approval.",
        )


def test_approval_workflow_records_audit_trail_and_keeps_distribution_manual():
    workflow = _approved_workflow()

    assert workflow["state"] == "approved"
    assert workflow["external_publishing_enabled"] is False
    assert workflow["manual_distribution_required"] is True
    assert workflow["client_authorization_status"] == CLIENT_AUTHORIZATION_STATUS
    assert [event["to_state"] for event in workflow["audit_trail"]] == [
        "automated_review",
        "ops_review",
        "sponsor_review",
        "approved",
    ]
    assert all(
        event["timestamp"] == "deterministic_timestamp"
        for event in workflow["audit_trail"]
    )


def _approved_workflow():
    workflow = create_approval_workflow(asset_id="asset-1")
    workflow = transition_approval_workflow(
        workflow,
        to_state="automated_review",
        actor_role="system",
        action="submit_for_automated_review",
    )
    workflow = transition_approval_workflow(
        workflow,
        to_state="ops_review",
        actor_role="system",
        action="pass_automated_review",
    )
    workflow = transition_approval_workflow(
        workflow,
        to_state="sponsor_review",
        actor_role="ops",
        action="ops_approve",
        comment="Ready for sponsor approval.",
    )
    return transition_approval_workflow(
        workflow,
        to_state="approved",
        actor_role="sponsor",
        action="sponsor_approve",
        comment="Approved for manual distribution.",
    )
