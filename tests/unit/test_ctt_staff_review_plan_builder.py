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
    extraction = dict(
        team={"name": "Synthetic"},
        players=players,
        staff=[
            dict(slot=slot, role=role, slot_ref=f"page:1:{role}", presence="UNCERTAIN")
            for slot, role in enumerate(("director_tecnico", "auxiliar"), 1)
        ],
    )
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
        for key in (
            "photo_preview",
            "photo_status",
            "source_card",
            "manual_fields",
            "presence_proposed",
        ):
            player.pop(key, None)
    for staff in changes["staff"]:
        staff.pop("role")
        for key in (
            "photo_status",
            "source_card",
            "manual_fields",
            "presence_proposed",
        ):
            staff.pop(key, None)
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


@pytest.fixture
def automatic(prepared):
    """Real original-grid measurements, retaining source values without OCR."""
    from dataclasses import replace
    from test_ctt_batch_photo_alignment import _page

    builder, context, proposal, old_snapshot = prepared
    raw_pages = []
    for item in context["source_pages"]:
        stream = BytesIO()
        _page(back=item["page_index"] == 2).save(stream, "PNG")
        raw_pages.append(stream.getvalue())
        item.update(
            image_sha256="sha256:" + hashlib.sha256(stream.getvalue()).hexdigest(),
            width=612,
            height=792,
        )
    for index, staff in enumerate(context["draft"]["extraction"]["staff"]):
        staff.update(
            presence="present",
            first_name=f"Tecnico{index}",
            last_name="Literal",
            birth_date="1980-01-01",
            curp=f"ABCD800101HDFRRN{index:02d}",
        )

    def snapshot(proposal_id=proposal):
        original = old_snapshot(proposal_id)
        scope = builder.initial_scope(context, proposal_id)
        return replace(
            original,
            page_assets=MappingProxyType(
                {page.asset_id: raw for page, raw in zip(scope.source_pages, raw_pages)}
            ),
        )

    return builder, context, proposal, snapshot


def test_verified_cards_seed_every_field_without_manual_boxes_or_confirmation(
    automatic,
):
    builder, context, proposal, snapshot = automatic
    original = copy.deepcopy(context["draft"]["extraction"])
    result = builder.build(context, {}, snapshot(), proposal)
    preview = json.loads(result.preview_bytes)
    assert context["draft"]["extraction"] == original
    assert all(
        item["source_card"]["status"] == "verified"
        for item in preview["players"] + preview["staff"]
    )
    assert all(
        item["presence"] == "PRESENT"
        and item["presence_proposed"]
        and not item["presence_confirmed"]
        for item in preview["staff"]
    )
    assert {item["code"] for item in preview["blockers"]} == {
        "STAFF_CONFIRMATION_REQUIRED"
    }
    first = preview["players"][0]
    assert first["first_name"] == "Synthetic"
    assert first["birth_date"] == "2000-01-01"
    assert (
        first["boxes"]["full_name"]
        == first["boxes"]["birth_date"]
        == first["boxes"]["curp"]
    )
    context["previous_preview"] = preview
    confirmed = builder.build(
        context, {"confirm_present_staff": True}, snapshot(), proposal
    )
    assert json.loads(confirmed.preview_bytes)["ready_to_approve"]
    evidence = json.loads(confirmed.evidence_bytes)["members"]
    assert all(
        member["fields"]["full_name"]["effective_pixels"] >= 12000
        for member in evidence
    )
    assert all(
        member["fields"]["full_name"]["coordinate_frame"] == "ORIGINAL"
        for member in evidence
    )


@pytest.mark.parametrize("value", [1, "true", None, [], {}])
def test_team_confirmation_requires_literal_boolean(automatic, value):
    builder, context, proposal, snapshot = automatic
    with pytest.raises(CaptureDenied):
        builder.build(context, {"confirm_present_staff": value}, snapshot(), proposal)


def test_manual_rectangle_and_literal_edit_survive_auto_preparation(automatic):
    builder, context, proposal, snapshot = automatic
    first = builder.build(context, {}, snapshot(), proposal)
    context["previous_preview"] = json.loads(first.preview_bytes)
    players = [
        {"slot": item["slot"]} for item in context["previous_preview"]["players"]
    ]
    manual = dict(page_index=1, x=50, y=335, width=240, height=70)
    players[0].update(first_name="  Nombre literal  ", boxes={"full_name": manual})
    edited = builder.build(context, {"players": players}, snapshot(), proposal)
    preview = json.loads(edited.preview_bytes)
    assert preview["players"][0]["first_name"] == "  Nombre literal  "
    assert preview["players"][0]["boxes"]["full_name"] == manual
    assert preview["players"][0]["boxes"].get("birth_date") is None
    context["previous_preview"] = preview
    again = builder.build(context, {}, snapshot(), proposal)
    assert json.loads(again.preview_bytes)["players"][0] == preview["players"][0]


def test_manual_clear_remains_missing_after_prepare_and_next_prepare(automatic):
    builder, context, proposal, snapshot = automatic
    first = builder.build(context, {}, snapshot(), proposal)
    context["previous_preview"] = json.loads(first.preview_bytes)
    players = [
        {"slot": item["slot"]} for item in context["previous_preview"]["players"]
    ]
    players[0]["boxes"] = {}
    cleared = builder.build(context, {"players": players}, snapshot(), proposal)
    preview = json.loads(cleared.preview_bytes)
    assert preview["players"][0]["boxes"] == {}
    assert preview["players"][0]["manual_fields"] == ["birth_date", "curp", "full_name"]
    context["previous_preview"] = preview
    again = json.loads(builder.build(context, {}, snapshot(), proposal).preview_bytes)
    assert again["players"][0]["boxes"] == {}
    failures = [
        item
        for item in again["blockers"]
        if item["kind"] == "PLAYER" and item["slot"] == 1
    ]
    assert {item["field"] for item in failures} == {"full_name", "birth_date", "curp"}
    assert all(item["navigation_target"]["source_page_index"] == 1 for item in failures)


@pytest.mark.parametrize("state", ["UNCERTAIN", "ABSENT", None])
def test_mutable_staff_present_cannot_replace_immutable_uncertainty(automatic, state):
    builder, context, proposal, snapshot = automatic
    original = context["draft"]["extraction"]
    context["draft"]["review_edits"] = copy.deepcopy(original)
    original["staff"][0]["presence"] = state
    context["draft"]["review_edits"]["staff"][0]["presence"] = "PRESENT"
    result = builder.build(
        context, {"confirm_present_staff": True}, snapshot(), proposal
    )
    member = json.loads(result.preview_bytes)["staff"][0]
    assert member["presence"] == "UNCERTAIN" and not member["presence_confirmed"]


@pytest.mark.parametrize(
    "reference",
    [
        "page:2:director_tecnico",
        "page:1:auxiliar",
        "director_tecnico",
        "page:99:director_tecnico",
    ],
)
def test_staff_automatic_presence_requires_exact_physical_role_reference(
    automatic, reference
):
    builder, context, proposal, snapshot = automatic
    context["draft"]["extraction"]["staff"][0]["slot_ref"] = reference
    result = builder.build(
        context, {"confirm_present_staff": True}, snapshot(), proposal
    )
    member = json.loads(result.preview_bytes)["staff"][0]
    assert member["presence"] == "UNCERTAIN" and not member["presence_confirmed"]
    assert member["source_card"]["status"] == "uncertain"


def test_edit_of_confirmed_staff_invalidates_confirmation_and_global_cannot_reconfirm(
    automatic,
):
    builder, context, proposal, snapshot = automatic
    first = builder.build(
        context, {"confirm_present_staff": True}, snapshot(), proposal
    )
    context["previous_preview"] = json.loads(first.preview_bytes)
    changes = {
        "staff": [{"slot": 1, "first_name": "Correction"}, {"slot": 2}],
        "confirm_present_staff": True,
    }
    result = json.loads(
        builder.build(context, changes, snapshot(), proposal).preview_bytes
    )
    assert result["staff"][0]["presence_confirmed"] is False
    assert not result["ready_to_approve"]


def test_changed_source_discards_old_automatic_boxes_and_invalidates_confirmation(
    automatic,
):
    builder, context, proposal, snapshot = automatic
    first = builder.build(
        context, {"confirm_present_staff": True}, snapshot(), proposal
    )
    context["previous_preview"] = json.loads(first.preview_bytes)
    context["previous_preview"]["source_binding"] = "sha256:" + "0" * 64
    old = context["previous_preview"]["players"][0]
    old["source_card"]["box"]["x"] += 1
    old["boxes"] = {
        key: copy.deepcopy(old["source_card"]["box"])
        for key in ("full_name", "birth_date", "curp")
    }
    result = json.loads(builder.build(context, {}, snapshot(), proposal).preview_bytes)
    assert (
        result["players"][0]["boxes"]["full_name"]["x"]
        == old["boxes"]["full_name"]["x"] - 1
    )
    assert all(not member["presence_confirmed"] for member in result["staff"])
    assert not result["ready_to_approve"]
    assert any(item["code"] == "SOURCE_CHANGED" for item in result["blockers"])


@pytest.mark.parametrize(
    "state,code",
    [
        ("missing", "PHOTO_MISSING"),
        ("uncertain", "PHOTO_UNCERTAIN"),
        ("error", "PHOTO_SOURCE_INVALID"),
    ],
)
def test_photo_source_exception_is_identified_by_person(
    automatic, monkeypatch, state, code
):
    builder, context, proposal, snapshot = automatic
    builder._policy["required_photo_kinds"] = ["PLAYER"]

    def detector(*args, **kwargs):
        if state == "error":
            raise OSError("private path must not be shown")
        return None, state

    monkeypatch.setattr(
        "devnous.copa_telmex.staff_review_plan_builder.extract_batch_photo", detector
    )
    monkeypatch.setattr(
        "devnous.copa_telmex.staff_review_plan_builder.prepare_batch_page",
        lambda *a: object(),
    )
    result = json.loads(builder.build(context, {}, snapshot(), proposal).preview_bytes)
    failure = next(item for item in result["blockers"] if item["code"] == code)
    assert (
        failure["kind"] == "PLAYER"
        and failure["slot"] == 1
        and failure["field"] == "photo"
    )
    assert "private path" not in json.dumps(result)


def test_photo_staging_failure_is_recoverable_without_requesting_another_crop(
    prepared, monkeypatch
):
    builder, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    changes["players"][0]["photo_box"] = dict(
        page_index=1, x=20, y=20, width=72, height=72
    )

    def broken(*args):
        raise CaptureDenied("sealed storage failed")

    monkeypatch.setattr(builder._photos, "stage", broken)
    result = json.loads(
        builder.build(context, changes, snapshot(), proposal).preview_bytes
    )
    failure = next(
        item for item in result["blockers"] if item["code"] == "PHOTO_STORAGE_FAILED"
    )
    assert failure["navigation_target"]["slot"] == 1
    assert result["players"][0]["photo_status"] == "error"
    assert not result["ready_to_approve"]


def test_global_confirmation_preserves_an_unchanged_individual_staff_decision(
    automatic,
):
    builder, context, proposal, snapshot = automatic
    context["draft"]["extraction"]["staff"][1]["presence"] = "UNCERTAIN"
    first = builder.build(context, {}, snapshot(), proposal)
    context["previous_preview"] = json.loads(first.preview_bytes)
    individual = builder.build(
        context,
        {
            "staff": [
                {"slot": 1},
                {"slot": 2, "presence": "PRESENT", "presence_confirmed": True},
            ]
        },
        snapshot(),
        proposal,
    )
    context["previous_preview"] = json.loads(individual.preview_bytes)
    assert context["previous_preview"]["staff"][1]["presence_proposed"] is False
    assert context["previous_preview"]["staff"][1]["presence_confirmed"] is True
    result = json.loads(
        builder.build(
            context, {"confirm_present_staff": True}, snapshot(), proposal
        ).preview_bytes
    )
    assert result["ready_to_approve"]
    assert all(member["presence_confirmed"] for member in result["staff"])


def test_optional_staff_photo_does_not_show_a_false_missing_exception(automatic):
    builder, context, proposal, snapshot = automatic
    builder._policy["required_photo_kinds"] = ["PLAYER"]
    result = json.loads(builder.build(context, {}, snapshot(), proposal).preview_bytes)
    assert all(member["photo_status"] == "not_required" for member in result["staff"])
    assert not any(
        item["kind"] == "STAFF" and item["field"] == "photo"
        for item in result["blockers"]
    )


def test_repeated_individual_flag_cannot_reconfirm_changed_values_in_same_request(
    automatic,
):
    builder, context, proposal, snapshot = automatic
    initial = builder.build(
        context, {"confirm_present_staff": True}, snapshot(), proposal
    )
    context["previous_preview"] = json.loads(initial.preview_bytes)
    assert context["previous_preview"]["staff"][0]["presence_confirmed"] is True
    result = json.loads(
        builder.build(
            context,
            {
                "staff": [
                    {"slot": 1, "first_name": "Corrected", "presence_confirmed": True},
                    {"slot": 2},
                ]
            },
            snapshot(),
            proposal,
        ).preview_bytes
    )
    assert result["staff"][0]["presence_confirmed"] is False
    assert not result["ready_to_approve"]
    context["previous_preview"] = result
    confirmed = json.loads(
        builder.build(
            context,
            {
                "staff": [
                    {"slot": 1, "presence_confirmed": True},
                    {"slot": 2},
                ]
            },
            snapshot(),
            proposal,
        ).preview_bytes
    )
    assert confirmed["staff"][0]["presence_confirmed"] is True
    assert confirmed["ready_to_approve"]


def test_read_only_inspection_derives_preview_without_staging_private_photos(
    prepared, monkeypatch
):
    builder, context, proposal, snapshot = prepared
    changes = completed_changes(*prepared)
    changes["players"][0]["photo_box"] = dict(
        page_index=1, x=20, y=20, width=72, height=72
    )
    calls = []
    monkeypatch.setattr(builder._photos, "stage", lambda photo: calls.append(photo))
    view = json.loads(
        builder.build(
            context, changes, snapshot(), proposal, stage_photos=False
        ).preview_bytes
    )
    assert view["players"][0]["photo_preview"].startswith("data:image/jpeg;base64,")
    assert view["players"][0]["photo_status"] == "photo"
    assert calls == []
    builder.build(context, changes, snapshot(), proposal)
    assert len(calls) == 1


def test_source_change_cannot_reuse_individual_flags_or_global_confirmation(automatic):
    builder, context, proposal, snapshot = automatic
    first = builder.build(
        context, {"confirm_present_staff": True}, snapshot(), proposal
    )
    context["previous_preview"] = json.loads(first.preview_bytes)
    context["previous_preview"]["source_binding"] = "sha256:" + "0" * 64
    changed = json.loads(
        builder.build(
            context,
            {
                "staff": [
                    {"slot": 1, "presence_confirmed": True},
                    {"slot": 2, "presence_confirmed": True},
                ],
                "confirm_present_staff": True,
            },
            snapshot(),
            proposal,
        ).preview_bytes
    )
    assert all(not member["presence_confirmed"] for member in changed["staff"])
    assert not changed["ready_to_approve"]


@pytest.mark.parametrize("failure", ["missing", "duplicate"])
def test_bad_player_reference_keeps_other_unique_sources_reviewable_without_execution(
    automatic, failure
):
    builder, context, proposal, snapshot = automatic
    extraction = context["draft"]["extraction"]
    original = copy.deepcopy(extraction["players"])
    bad_slots = {1}
    if failure == "missing":
        extraction["players"][0].pop("slot_ref")
    else:
        extraction["players"][0]["slot_ref"] = extraction["players"][1]["slot_ref"]
        bad_slots.add(2)
    result = builder.build(context, {}, snapshot(), proposal)
    view = json.loads(result.preview_bytes)
    assert not view["ready_to_approve"]
    assert (
        json.loads(result.review_bytes)["schema"] == "ctt.review.incomplete-proposal.v1"
    )
    player_failures = [item for item in view["blockers"] if item["kind"] == "PLAYER"]
    assert {item["slot"] for item in player_failures} == bad_slots
    for index, member in enumerate(view["players"]):
        assert member["curp"] == original[index]["curp"]
        assert member["birth_date"] == original[index]["birth_date"]
        if member["slot"] not in bad_slots:
            assert member["source_card"]["status"] == "verified"
            assert (
                member["boxes"]["full_name"]["page_index"]
                == member["source_page_index"]
            )


def test_duplicate_reference_with_previous_preview_does_not_reuse_automatic_photo(
    automatic, monkeypatch
):
    builder, context, proposal, snapshot = automatic
    context["previous_preview"] = json.loads(
        builder.build(context, {}, snapshot(), proposal).preview_bytes
    )
    players = context["draft"]["extraction"]["players"]
    players[0]["slot_ref"] = players[1]["slot_ref"]
    attempted = []

    def absent_photo(retained, ref):
        attempted.append(ref)
        raise CaptureDenied("no synthetic photo")

    monkeypatch.setattr(
        "devnous.copa_telmex.staff_review_plan_builder.derive_batch_review_photo",
        absent_photo,
    )
    result = builder.build(context, {}, snapshot(), proposal)
    view = json.loads(result.preview_bytes)
    assert not view["ready_to_approve"]
    assert players[1]["slot_ref"] not in attempted
    failures = [
        item
        for item in view["blockers"]
        if item["code"] == "PLAYER_SOURCE_MISSING" and item["kind"] == "PLAYER"
    ]
    assert {item["slot"] for item in failures} == {1, 2}
    assert all("photo_preview" not in member for member in view["players"][:2])
