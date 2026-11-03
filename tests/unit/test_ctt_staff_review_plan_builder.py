"""Acceptance of blocked drafts and exact private material plans, no authority."""

import copy
import hashlib
import json
from io import BytesIO
from types import MappingProxyType, SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from devnous.copa_telmex.staff_capture_effect import (
    bind_review_evidence,
    bind_review_plan,
)
from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_source_adapter import SourceSnapshot
from devnous.copa_telmex.staff_photo_store import PrivateReviewPhotoStore
from devnous.copa_telmex.staff_review_challenge import canonical_scope_bytes
from devnous.copa_telmex.staff_review_plan_builder import ReviewPlanBuilder


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    def missing_printed_photo(*args):
        raise CaptureDenied("synthetic page has no photo")

    monkeypatch.setattr(
        "devnous.copa_telmex.staff_review_plan_builder.derive_batch_review_photo",
        missing_printed_photo,
    )
    policy = dict(
        minimum_roster_size=16,
        maximum_roster_size=35,
        minimum_effective_pixels=12000,
        required_staff_slots=[],
        required_photo_kinds=[],
    )
    policy["policy_hash"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    directory = tmp_path / "photos"
    directory.mkdir(mode=0o700)
    builder = ReviewPlanBuilder(
        review_key=b"r" * 32,
        policy_artifact=policy,
        expected_policy_hash=policy["policy_hash"],
        photo_store=PrivateReviewPhotoStore(directory),
        gate=SimpleNamespace(
            _policy_hash=policy["policy_hash"], _max_page_bytes=1000000
        ),
    )
    image = BytesIO()
    Image.new("RGB", (700, 900), "white").save(image, "PNG")
    raw = image.getvalue()
    pdf = b"synthetic-private-source"
    players = [
        dict(
            name=f"Synthetic Player {i}",
            birth_date="2000-01-01",
            curp=f"ABCD000101HDFRRN{i:02d}",
            confidence=1.0,
            slot_ref=f"page:{1 if i <= 8 else 2}:jugador_{i}",
        )
        for i in range(1, 17)
    ]
    extraction = dict(team={"name": "Synthetic"}, players=players, staff=[])
    context = dict(
        tenant_id="synthetic",
        roster_slug="sample",
        edition_id=str(uuid4()),
        principal_id=str(uuid4()),
        account_id=str(uuid4()),
        actor_role="coordinador",
        backend_pin="isolated",
        account_version=1,
        principal_version=1,
        faculty_version=1,
        credential_version=1,
        credential_id_hex="abcd",
        faculty_valid_until_seconds=9999999999,
        manifest_sha256="b" * 64,
        document_id="c" * 64,
        pdf_sha256=hashlib.sha256(pdf).hexdigest(),
        session={"id": str(uuid4())},
        draft=dict(
            id=str(uuid4()),
            draft_version=1,
            content_hash="sha256:" + "d" * 64,
            extraction=extraction,
            review_edits={},
        ),
        existing_photo_records=[],
        source_pages=[
            dict(
                page_index=i,
                pdf_page=i,
                asset_id=str(uuid4()),
                image_sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
                width=700,
                height=900,
            )
            for i in (1, 2)
        ],
    )
    proposal = uuid4()

    def snapshot(proposal_id=proposal):
        scope = builder.initial_scope(context, proposal_id)
        return SourceSnapshot(
            canonical(
                {"documents": [dict(document_id="c" * 64, extraction=extraction)]}
            ),
            BytesIO(pdf),
            MappingProxyType({p.asset_id: raw for p in scope.source_pages}),
            canonical_scope_bytes(scope),
            10000,
        )

    return builder, context, proposal, snapshot


def completed_changes(builder, context, proposal, snapshot):
    first = builder.build(context, {}, snapshot(), proposal)
    preview = json.loads(first.preview_bytes)
    changes = {
        "team": preview["team"],
        "players": preview["players"],
        "staff": preview["staff"],
    }
    for player in changes["players"]:
        page = player["source_page_index"]
        player["boxes"] = {
            field: dict(page_index=page, x=10, y=10, width=200, height=100)
            for field in ("full_name", "birth_date", "curp")
        }
        player.pop("photo_preview", None)
    for staff in changes["staff"]:
        staff.pop("role")
        staff.update(
            presence="ABSENT",
            presence_confirmed=True,
            source_page_index=1,
            absence_box=dict(page_index=1, x=10, y=10, width=200, height=100),
        )
    return changes


def test_incomplete_is_reviewable_without_executable_rows(prepared):
    builder, context, proposal, snapshot = prepared
    result = builder.build(context, {}, snapshot(), proposal)
    plan = json.loads(result.review_bytes)
    assert set(plan) == {"schema", "review_context", "team", "observations"}
    assert plan["schema"] == "ctt.review.incomplete-proposal.v1"
    preview = json.loads(result.preview_bytes)
    assert not preview["ready_to_approve"] and preview["blockers"]
    assert all(s["presence"] == "UNCERTAIN" for s in preview["staff"])
    assert (
        json.loads(result.validation_bytes)["incident_policy"]["summary"][
            "total_players"
        ]
        == 16
    )


def test_complete_source_rectangles_freeze_exact_rows_and_bindings(prepared):
    builder, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    result = builder.build(context, changes, snapshot(), proposal)
    preview = json.loads(result.preview_bytes)
    assert preview["blockers"] == []
    assert preview["ready_to_approve"] is True
    plan = json.loads(result.review_bytes)
    assert plan["schema"] == "ctt.capture.review-plan.v1"
    assert len(plan["players"]) == 16
    assert all(p["governance_state"] == "PENDING_FINALITY" for p in plan["players"])
    assert all(p["curp_valid"] is False for p in plan["players"])
    assert [p["source_slot"] for p in plan["player_sources"]] == list(range(1, 17))
    assert all(s["row"] is None and s["presence"] == "ABSENT" for s in plan["staff"])
    assert result.scope.staff_and_changes_binding == bind_review_plan(
        result.review_bytes, b"r" * 32
    )
    assert result.scope.field_evidence_binding == bind_review_evidence(
        result.evidence_bytes, b"r" * 32
    )
    assert "preauthorization_receipt_id" not in plan["players"][0]


@pytest.mark.parametrize(
    "failure",
    [
        "too_small",
        "ambiguous_date",
        "curp",
        "missing_geometry",
        "bounds",
        "page",
        "unknown_staff",
        "unconfirmed",
    ],
)
def test_proposal_blocks_missing_or_ambiguous_evidence(prepared, failure):
    builder, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    player = changes["players"][0]
    if failure == "too_small":
        player["boxes"]["full_name"]["width"] = 1
    elif failure == "ambiguous_date":
        player["birth_date"] = "01/01/00"
    elif failure == "curp":
        player["curp"] = "SHORT"
    elif failure == "missing_geometry":
        player["boxes"] = {}
    elif failure == "bounds":
        player["boxes"]["full_name"]["x"] = 650
    elif failure == "page":
        player["source_page_index"] = 2
    elif failure == "unknown_staff":
        changes["staff"][0]["presence"] = "UNCERTAIN"
    elif failure == "unconfirmed":
        changes["staff"][0]["presence_confirmed"] = False
    result = builder.build(context, changes, snapshot(), proposal)
    assert json.loads(result.preview_bytes)["ready_to_approve"] is False
    assert (
        json.loads(result.review_bytes)["schema"] == "ctt.review.incomplete-proposal.v1"
    )


@pytest.mark.parametrize("failure", ["authority", "drop", "reorder", "add", "role"])
def test_browser_cannot_change_authority_or_membership(prepared, failure):
    builder, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    if failure == "authority":
        changes["principal_id"] = str(uuid4())
    elif failure == "drop":
        changes["players"].pop()
    elif failure == "reorder":
        changes["players"].reverse()
    elif failure == "add":
        changes["players"].append(copy.deepcopy(changes["players"][0]))
    elif failure == "role":
        changes["staff"][0]["role"] = "admin"
    with pytest.raises(CaptureDenied):
        builder.build(context, changes, snapshot(), proposal)


def test_changed_source_fails_before_plan(prepared):
    builder, context, proposal, snapshot = prepared
    source = snapshot()
    source.pdf.seek(0)
    source.pdf.write(b"altered")
    with pytest.raises(CaptureDenied):
        builder.build(context, {}, source, proposal)


def test_incremental_edit_retains_only_private_previous_geometry(prepared):
    builder, context, proposal, snapshot = prepared
    first = builder.build(context, completed_changes(*prepared), snapshot(), proposal)
    context["previous_preview"] = json.loads(first.preview_bytes)
    second = builder.build(
        context, {"team": {"name": "Corrected"}}, snapshot(), proposal
    )
    assert json.loads(second.preview_bytes)["ready_to_approve"]
    assert json.loads(second.review_bytes)["team"]["name"] == "Corrected"


def test_registry_version_change_changes_authority_binding(prepared):
    builder, context, proposal, snapshot = prepared
    first = builder.initial_scope(context, proposal)
    context["faculty_version"] += 1
    second = builder.initial_scope(context, proposal)
    assert first.authority_binding != second.authority_binding


@pytest.mark.parametrize("photo_size", [(160, 150), (72, 72), (40, 40)])
def test_present_staff_materialization_and_private_photo(prepared, photo_size):
    builder, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    staff = changes["staff"][0]
    staff.update(
        presence="PRESENT",
        first_name="Synthetic",
        last_name="Coach",
        birth_date="1980-01-01",
        curp="ABCD800101HDFRRN01",
        boxes=copy.deepcopy(changes["players"][0]["boxes"]),
        photo_box=dict(
            page_index=1, x=20, y=20, width=photo_size[0], height=photo_size[1]
        ),
    )
    result = builder.build(context, changes, snapshot(), proposal)
    assert json.loads(result.preview_bytes)["ready_to_approve"]
    row = json.loads(result.review_bytes)["staff"][0]["row"]
    assert row["role"] == "director_tecnico"
    assert row["governance_state"] == "PENDING_FINALITY"
    assert row["photo_path"].startswith("ctt-photo:")
    assert "preauthorization_receipt_id" not in row
    evidence = json.loads(result.evidence_bytes)["members"][-2]
    assert evidence["photo_evidence"]["mode"] == "SOURCE_RECTANGLE"
    assert builder._photos.read(row["photo_path"][10:] + ".jpg").startswith(b"\xff\xd8")


def test_duplicate_photos_recount_blocks_capture(prepared):
    builder, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    for member in changes["players"][:2]:
        member["photo_box"] = dict(page_index=1, x=20, y=20, width=160, height=150)
    result = builder.build(context, changes, snapshot(), proposal)
    assert not json.loads(result.preview_bytes)["ready_to_approve"]
    assert (
        json.loads(result.evidence_bytes)["incident_state"]["blocking_incident_count"]
        == 2
    )


@pytest.mark.parametrize(
    "failure",
    [
        "missing_ref",
        "wrong_count",
        "required_staff",
        "required_photo",
        "too_long",
        "small_roster",
    ],
)
def test_policy_and_physical_template_boundaries_block(prepared, failure):
    builder, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    if failure == "missing_ref":
        context["draft"]["extraction"]["players"][0].pop("slot_ref")
    elif failure == "wrong_count":
        context["draft"]["review_edits"] = copy.deepcopy(context["draft"]["extraction"])
        context["draft"]["review_edits"]["players"].pop()
        changes["players"].pop()
    elif failure == "required_staff":
        builder._policy["required_staff_slots"] = [1]
    elif failure == "required_photo":
        builder._policy["required_photo_kinds"] = ["PLAYER"]
    elif failure == "too_long":
        changes["team"]["name"] = "x" * 101
    elif failure == "small_roster":
        context["draft"]["extraction"]["players"].pop()
        changes["players"].pop()
    result = builder.build(context, changes, snapshot(), proposal)
    assert not json.loads(result.preview_bytes)["ready_to_approve"]
    assert (
        json.loads(result.review_bytes)["schema"] == "ctt.review.incomplete-proposal.v1"
    )


def test_wrong_proposal_snapshot_and_missing_authority_denied(prepared):
    builder, context, proposal, snapshot = prepared
    with pytest.raises(CaptureDenied):
        builder.build(context, {}, snapshot(), uuid4())
    context.pop("credential_version")
    with pytest.raises(CaptureDenied):
        builder.initial_scope(context, proposal)


@pytest.mark.parametrize("field", ["slot", "source_page_index", "box_page_index"])
@pytest.mark.parametrize("value", [True, 1.0, "1", None])
def test_source_references_require_exact_integer_types(prepared, field, value):
    builder, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    if field == "box_page_index":
        changes["players"][0]["boxes"]["full_name"]["page_index"] = value
    else:
        changes["players"][0][field] = value
    try:
        result = builder.build(context, changes, snapshot(), proposal)
    except CaptureDenied:
        return
    assert json.loads(result.preview_bytes)["ready_to_approve"] is False


def test_initial_missing_values_remain_blank_and_blocked(prepared):
    builder, context, proposal, snapshot = prepared
    player = context["draft"]["extraction"]["players"][0]
    player.update(
        name=None, birth_date=None, curp=None, first_name=None, last_name=None
    )
    result = builder.build(context, {}, snapshot(), proposal)
    preview = json.loads(result.preview_bytes)
    assert preview["players"][0]["first_name"] == ""
    assert not preview["ready_to_approve"]


def national_builder(prepared, mutate=None):
    old, context, _, _ = prepared
    policy = copy.deepcopy(old._policy)
    policy.pop("policy_hash")
    policy.update(
        policy_version="2.1.0",
        effective_sal="SAL-3",
        field_order=["full_name", "birth_date", "curp"],
        phase_binding=dict(
            phase="NACIONAL",
            tenant_id=context["tenant_id"],
            edition_id=context["edition_id"],
            manifest_sha256=context["manifest_sha256"],
        ),
        optional_curp_kinds=["PLAYER", "STAFF"],
    )
    if mutate:
        mutate(policy)
    policy["policy_hash"] = "sha256:" + hashlib.sha256(canonical(policy)).hexdigest()
    return ReviewPlanBuilder(
        review_key=old._key,
        policy_artifact=policy,
        expected_policy_hash=policy["policy_hash"],
        photo_store=old._photos,
        gate=SimpleNamespace(
            _policy_hash=policy["policy_hash"], _max_page_bytes=1000000
        ),
    )


def use_national(prepared):
    builder = national_builder(prepared)
    old = prepared[0]
    # Snapshot factory closes over this trusted builder object.
    old.__dict__.update(builder.__dict__)
    return old


def test_national_absent_curps_are_null_without_fictitious_evidence(prepared):
    builder = use_national(prepared)
    _, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    for player in changes["players"][:2]:
        player["curp"] = ""
        player["boxes"].pop("curp")
    for index, staff in enumerate(changes["staff"]):
        staff.update(
            presence="PRESENT",
            first_name="Synthetic",
            last_name=f"Coach {index}",
            birth_date="1980-01-01",
            curp=None,
            boxes=copy.deepcopy(changes["players"][0]["boxes"]),
        )
    result = builder.build(context, changes, snapshot(), proposal)
    preview = json.loads(result.preview_bytes)
    assert preview["ready_to_approve"]
    assert preview["identity_policy"] == {
        "phase": "NACIONAL",
        "optional_curp_kinds": ["PLAYER", "STAFF"],
    }
    plan = json.loads(result.review_bytes)
    assert [p["curp"] for p in plan["players"][:2]] == [None, None]
    assert all(p["curp_valid"] is False for p in plan["players"][:2])
    assert [s["row"]["curp"] for s in plan["staff"]] == [None, None]
    evidence = json.loads(result.evidence_bytes)["members"]
    for member in evidence[:2] + evidence[-2:]:
        assert set(member["fields"]) == {"full_name", "birth_date"}
    assert "curp" in evidence[2]["fields"]


@pytest.mark.parametrize("field", ["tenant_id", "edition_id", "manifest_sha256"])
def test_national_scope_mismatch_denied(prepared, field):
    builder = use_national(prepared)
    _, context, proposal, _ = prepared
    context[field] = str(uuid4()) if field == "edition_id" else "unmatched"
    with pytest.raises(CaptureDenied):
        builder.initial_scope(context, proposal)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.pop("phase_binding"),
        lambda p: p.update(effective_sal="SAL-2"),
        lambda p: p.update(field_order=["full_name", "birth_date"]),
        lambda p: p.pop("optional_curp_kinds"),
        lambda p: p.update(policy_version="2.0.0"),
        lambda p: p["phase_binding"].update(phase="ESTATAL"),
        lambda p: p["phase_binding"].update(extra=True),
        lambda p: p["phase_binding"].update(manifest_sha256="SHA256:" + "b" * 64),
        lambda p: p["phase_binding"].update(edition_id="not-uuid"),
        lambda p: p.update(optional_curp_kinds=["PLAYER", "PLAYER"]),
        lambda p: p.update(optional_curp_kinds=["OTHER"]),
    ],
)
def test_national_malformed_profile_rejected_independently(prepared, mutation):
    with pytest.raises(ValueError):
        national_builder(prepared, mutation)


@pytest.mark.parametrize("national", [False, True])
@pytest.mark.parametrize(
    "failure", ["invalid_curp", "missing_geometry", "missing_name", "missing_birth"]
)
def test_national_preserves_present_and_required_field_checks(
    prepared, national, failure
):
    builder = use_national(prepared) if national else prepared[0]
    _, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    player = changes["players"][0]
    if failure == "invalid_curp":
        player["curp"] = "SHORT"
    elif failure == "missing_geometry":
        player["boxes"].pop("curp")
    elif failure == "missing_name":
        player.update(first_name="", last_name="")
    else:
        player["birth_date"] = ""
    result = builder.build(context, changes, snapshot(), proposal)
    assert not json.loads(result.preview_bytes)["ready_to_approve"]


def test_legacy_missing_curp_still_blocks(prepared):
    builder, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    changes["players"][0]["curp"] = ""
    result = builder.build(context, changes, snapshot(), proposal)
    assert not json.loads(result.preview_bytes)["ready_to_approve"]


def test_deployed_national_policy_and_preserved_strict_artifact():
    from pathlib import Path

    directory = Path(__file__).resolve().parents[2] / "deployment/ctt_protected_review"
    strict = directory / "policy-copa-telmex-2026-strict-v2.json"
    national = json.loads((directory / "policy-copa-telmex-2026.json").read_bytes())
    strict_policy = json.loads(strict.read_bytes())
    assert strict_policy["policy_version"] == "2.0.0"
    assert strict_policy["policy_hash"] == (
        "sha256:4c02da4d4981bedd60a34771236f0aa0db76c5392477413f3d03e68cc4e2e697"
    )
    assert national["policy_version"] == "2.1.0"
    assert national["field_order"] == strict_policy["field_order"]
    assert national["effective_sal"] == strict_policy["effective_sal"] == "SAL-3"
    assert national["phase_binding"] == dict(
        phase="NACIONAL",
        tenant_id="samchat-prod",
        edition_id="47bcc601-929a-4a8a-9005-99f10ee1aa58",
        manifest_sha256="9a4bcaa149bccaafbc479ce7b70b225b6df"
        "155a97761ee56c2c079d202c3ee67",
    )
    for policy in (national, strict_policy):
        pinned = policy.pop("policy_hash")
        assert pinned == "sha256:" + hashlib.sha256(canonical(policy)).hexdigest()


def test_semver_without_exception_extensions_remains_strict(prepared):
    builder = national_builder(
        prepared, lambda p: (p.pop("phase_binding"), p.pop("optional_curp_kinds"))
    )
    old, context, proposal, snapshot = prepared
    old.__dict__.update(builder.__dict__)
    changes = completed_changes(*prepared)
    changes["players"][0]["curp"] = None
    result = old.build(context, changes, snapshot(), proposal)
    preview = json.loads(result.preview_bytes)
    assert not preview["ready_to_approve"]
    assert preview["identity_policy"] == {"phase": None, "optional_curp_kinds": []}


@pytest.mark.parametrize(
    "optional_kind,missing_kind", [("PLAYER", "STAFF"), ("STAFF", "PLAYER")]
)
def test_optional_curp_exception_is_limited_to_declared_kinds(
    prepared, optional_kind, missing_kind
):
    builder = national_builder(
        prepared, lambda p: p.update(optional_curp_kinds=[optional_kind])
    )
    old, context, proposal, snapshot = prepared
    old.__dict__.update(builder.__dict__)
    changes = completed_changes(*prepared)
    if missing_kind == "PLAYER":
        changes["players"][0]["curp"] = None
    else:
        changes["staff"][0].update(
            presence="PRESENT",
            first_name="Synthetic",
            last_name="Coach",
            birth_date="1980-01-01",
            curp=None,
            boxes=copy.deepcopy(changes["players"][0]["boxes"]),
        )
    result = old.build(context, changes, snapshot(), proposal)
    assert not json.loads(result.preview_bytes)["ready_to_approve"]
