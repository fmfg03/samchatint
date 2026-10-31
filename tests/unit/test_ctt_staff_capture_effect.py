"""Whole-effect binding protects explicit columns and physical player slots."""

import copy
import json
from uuid import UUID, uuid4

import pytest

from devnous.copa_telmex.models import Player, Team, TeamStaff
from devnous.copa_telmex.staff_capture_effect import (
    bind_capture_effect,
    freeze_capture_effect,
)
from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from test_ctt_staff_capture_source_check import sample


def row(model):
    value = {}
    for column in model.__table__.columns:
        value[column.name] = None
        if not column.nullable:
            kind = column.type.python_type
            if kind is UUID:
                value[column.name] = str(uuid4())
            elif kind is int:
                value[column.name] = 1
            elif kind is dict:
                value[column.name] = {}
            elif kind is str:
                value[column.name] = "synthetic"
            else:
                value[column.name] = (
                    "2026-10-30T12:00:00+00:00"
                    if getattr(column.type, "timezone", False)
                    else "2026-10-30T12:00:00"
                )
    if "postcommit_snapshot_hash" in value:
        value["postcommit_snapshot_hash"] = "sha256:" + "0" * 64
    return value


def effect():
    _, scope, _, _ = sample()
    team = row(Team)
    team.update(id=str(scope.team_id), tournament_slug=scope.tournament_slug)
    player = row(Player)
    player.update(
        team_id=team["id"],
        roster_index=1,
        governance_state="PENDING_FINALITY",
        postcommit_revision=0,
        governance_draft_id=str(scope.draft_id),
        governance_draft_version=scope.draft_version,
    )
    staff_row = row(TeamStaff)
    staff_row.update(
        team_id=team["id"],
        staff_slot=1,
        role="director_tecnico",
        governance_state="PENDING_FINALITY",
        governance_draft_id=str(scope.draft_id),
        governance_draft_version=scope.draft_version,
    )
    return dict(
        scope=scope,
        team=team,
        players=[player],
        player_source_refs=["page:1:jugador_3"],
        staff=[
            dict(
                slot=1,
                role="director_tecnico",
                presence="PRESENT",
                row=staff_row,
            ),
            dict(slot=2, role="auxiliar", presence="ABSENT", row=None),
        ],
    )


def test_complete_effect_binds_all_entities_without_eligibility():
    args = effect()
    payload = freeze_capture_effect(**args)
    decoded = json.loads(payload)
    assert decoded["player_sources"][0]["source_slot"] == 3
    assert decoded["players"][0]["roster_index"] == 1
    assert decoded["staff"][1]["presence"] == "ABSENT"
    changed = copy.deepcopy(args)
    changed["team"]["name"] = "other"
    assert bind_capture_effect(payload, b"k" * 32) != bind_capture_effect(
        freeze_capture_effect(**changed), b"k" * 32
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "active",
        "team",
        "draft",
        "index",
        "duplicate",
        "staff_role",
        "absence",
        "nan",
    ],
)
def test_partial_ambiguous_or_unreviewed_effect_is_denied(change):
    args = effect()
    if change == "missing":
        del args["players"][0]["email"]
    elif change == "extra":
        args["team"]["unexpected"] = "value"
    elif change == "active":
        args["players"][0]["governance_state"] = "LEGACY_ACTIVE"
    elif change == "team":
        args["players"][0]["team_id"] = str(uuid4())
    elif change == "draft":
        args["players"][0]["governance_draft_version"] += 1
    elif change == "index":
        args["players"][0]["roster_index"] = 3
    elif change == "duplicate":
        args["staff"][0]["row"]["id"] = args["players"][0]["id"]
    elif change == "staff_role":
        args["staff"][0]["row"]["role"] = "auxiliar"
    elif change == "absence":
        args["staff"][1]["row"] = {}
    else:
        args["players"][0]["ocr_confidence"] = float("nan")
    with pytest.raises(CaptureDenied):
        freeze_capture_effect(**args)


def lifecycle(issue_receipt=None, *, prereceipt_scope=False):
    from dataclasses import replace
    from hashlib import sha256
    from devnous.copa_telmex.staff_capture_effect import (
        DATABASE_DERIVED_FIELDS,
        LATE_AUTHORITY_FIELDS,
        bind_review_plan,
        freeze_review_plan,
    )

    args = effect()
    if prereceipt_scope:
        args["scope"] = replace(
            args["scope"],
            decision_hash=None,
            review_receipt_id=None,
            field_evidence_binding="hmac-sha256:" + "7" * 64,
        )
    generated = {}
    for entry in [args["team"]] + args["players"]:
        generated[entry["id"]] = {
            field: entry[field] for field in DATABASE_DERIVED_FIELDS
        }
        for field in DATABASE_DERIVED_FIELDS:
            entry[field] = None
    for entry in args["players"] + [
        item["row"] for item in args["staff"] if item["row"]
    ]:
        for field in LATE_AUTHORITY_FIELDS:
            entry[field] = None
    plan = freeze_review_plan(**args)
    key = b"isolated-review-plan-test-key-32-bytes"
    binding = bind_review_plan(plan, key)
    args["scope"] = replace(args["scope"], staff_and_changes_binding=binding)
    assert freeze_review_plan(**args) == plan
    # Synthetic preauthorization is issued only after the plan exists.
    decision = "sha256:" + sha256(binding.encode()).hexdigest()
    for entry in [args["team"]] + args["players"]:
        entry.update(generated[entry["id"]])
    metadata = {}
    for entry in args["players"] + [
        item["row"] for item in args["staff"] if item["row"]
    ]:
        fields = {
            "governance_decision_id": decision,
            "roster_draft_binding": binding,
            "preauthorization_receipt_id": "sha256:"
            + sha256((decision + entry["id"]).encode()).hexdigest(),
            "finality_receipt_id": None,
        }
        if issue_receipt is not None:
            fields["preauthorization_receipt_id"] = issue_receipt(
                binding, decision, entry["id"]
            )
        entry.update(fields)
        metadata[entry["id"]] = fields
    return args, plan, key, metadata


def test_plan_precedes_receipts_and_final_effect_preserves_reviewed_data():
    from devnous.copa_telmex.staff_capture_effect import (
        check_effect_follows_review,
        bind_review_plan,
    )

    args, plan, key, metadata = lifecycle()
    final = freeze_capture_effect(**args)
    check_effect_follows_review(
        review_bytes=plan,
        effect_bytes=final,
        scope=args["scope"],
        review_key=key,
        authority_metadata=metadata,
    )
    assert bind_review_plan(plan, key) != bind_capture_effect(final, key)


def test_prereceipt_plan_binds_operation_and_survives_later_receipts():
    from dataclasses import replace
    from devnous.copa_telmex.staff_capture_effect import check_effect_follows_review
    from devnous.copa_telmex.staff_review_challenge import canonical_scope_bytes

    args, plan, key, metadata = lifecycle(prereceipt_scope=True)
    context = json.loads(plan)["review_context"]
    assert context["schema"] == "ctt.staff-review.challenge-scope.v2"
    assert context["operation_id"] == args["scope"].operation_id
    assert context["field_evidence_binding"] == args["scope"].field_evidence_binding
    assert "decision_hash" not in context and "review_receipt_id" not in context
    scope_bytes = canonical_scope_bytes(args["scope"])
    final = freeze_capture_effect(**args)
    check_effect_follows_review(
        review_bytes=plan,
        effect_bytes=final,
        scope=args["scope"],
        review_key=key,
        authority_metadata=metadata,
    )
    assert canonical_scope_bytes(args["scope"]) == scope_bytes
    args["scope"] = replace(args["scope"], operation_id="sha256:" + "9" * 64)
    changed = freeze_capture_effect(**args)
    with pytest.raises(CaptureDenied):
        check_effect_follows_review(
            review_bytes=plan,
            effect_bytes=changed,
            scope=args["scope"],
            review_key=key,
            authority_metadata=metadata,
        )


@pytest.mark.parametrize(
    "change",
    [
        "name",
        "photo",
        "source",
        "absence",
        "receipt",
        "scope",
        "key",
    ],
)
def test_post_review_changes_are_denied(change):
    from dataclasses import replace
    from devnous.copa_telmex.staff_capture_effect import (
        check_effect_follows_review,
    )

    args, plan, key, metadata = lifecycle()
    if change == "name":
        args["team"]["name"] = "changed"
    elif change == "photo":
        args["players"][0]["photo_path"] = "/changed.png"
    elif change == "source":
        args["player_source_refs"][0] = "page:1:jugador_4"
    elif change == "absence":
        args["staff"][0].update(presence="ABSENT", row=None)
    elif change == "receipt":
        args["players"][0]["preauthorization_receipt_id"] = "sha256:" + "f" * 64
    elif change == "scope":
        args["scope"] = replace(args["scope"], policy_hash="sha256:" + "f" * 64)
    else:
        key = b"other-key-that-is-more-than-32-bytes"
    final = freeze_capture_effect(**args)
    with pytest.raises(CaptureDenied):
        check_effect_follows_review(
            review_bytes=plan,
            effect_bytes=final,
            scope=args["scope"],
            review_key=key,
            authority_metadata=metadata,
        )


def test_review_plan_cannot_contain_later_receipts():
    from devnous.copa_telmex.staff_capture_effect import freeze_review_plan

    with pytest.raises(CaptureDenied):
        freeze_review_plan(**effect())


def test_sequence_with_signed_receipts_generated_after_review_plan():
    import base64
    import hashlib
    from datetime import datetime, timezone
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from devnous.copa_telmex.staff_governance_receipt import (
        verify_staff_evidence_receipt,
    )
    from devnous.copa_telmex.staff_capture_effect import (
        check_effect_follows_review,
    )

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    issued = []

    def canonical(value):
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()

    def digest(value):
        return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()

    def issue(binding, decision, row_id):
        event = dict(
            event_type="samchat_registration_roster_decision_v2",
            tenant_id="synthetic",
            review_plan_binding=binding,
            decision_id=decision,
            row_id=row_id,
        )
        receipt = dict(
            receipt_type="EvidenceReceipt.v1",
            event_hash=digest(event),
            event_type=event["event_type"],
            tenant_id=event["tenant_id"],
            accepted_at=datetime.now(timezone.utc).isoformat(),
            verified=True,
            issuer="zaubern-evidence-bus",
            kid="test",
            alg="Ed25519",
        )
        receipt["signature"] = base64.b64encode(key.sign(canonical(receipt))).decode()
        receipt["receipt_id"] = digest(receipt)
        assert verify_staff_evidence_receipt(
            receipt, event, public_keys={"test": public}
        )
        assert not verify_staff_evidence_receipt(
            receipt,
            {**event, "row_id": str(uuid4())},
            public_keys={"test": public},
        )
        issued.append(receipt)
        return receipt["receipt_id"]

    args, plan, review_key, metadata = lifecycle(issue)
    final = freeze_capture_effect(**args)
    check_effect_follows_review(
        review_bytes=plan,
        effect_bytes=final,
        scope=args["scope"],
        review_key=review_key,
        authority_metadata=metadata,
    )
    assert len(issued) == 2


@pytest.mark.parametrize(
    "field",
    [
        "governance_decision_id",
        "roster_draft_binding",
        "preauthorization_receipt_id",
    ],
)
@pytest.mark.parametrize("invalid", [None, "", "synthetic"])
def test_player_authority_must_exist_even_if_mapping_agrees(field, invalid):
    from devnous.copa_telmex.staff_capture_effect import (
        check_effect_follows_review,
    )

    args, plan, key, metadata = lifecycle()
    player = args["players"][0]
    player[field] = invalid
    metadata[player["id"]][field] = invalid
    with pytest.raises(CaptureDenied):
        check_effect_follows_review(
            review_bytes=plan,
            effect_bytes=freeze_capture_effect(**args),
            scope=args["scope"],
            review_key=key,
            authority_metadata=metadata,
        )


def test_timezone_and_generated_metadata_are_explicit():
    from devnous.copa_telmex.staff_capture_effect import (
        check_effect_follows_review,
        freeze_review_plan,
    )

    args = effect()
    args["staff"][0]["row"]["created_at"] = "2026-10-30T14:00:00+02:00"
    with pytest.raises(CaptureDenied):
        freeze_capture_effect(**args)
    args, plan, key, metadata = lifecycle()
    args["players"][0]["postcommit_revision"] = 1
    with pytest.raises(CaptureDenied):
        check_effect_follows_review(
            review_bytes=plan,
            effect_bytes=freeze_capture_effect(**args),
            scope=args["scope"],
            review_key=key,
            authority_metadata=metadata,
        )
    args = effect()
    # A review plan cannot pretend to know hashes produced after receipts.
    with pytest.raises(CaptureDenied):
        freeze_review_plan(**args)
