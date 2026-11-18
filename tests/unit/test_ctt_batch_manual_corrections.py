"""Actual immutable source replay of pending manual card interpretations."""

import copy

import fitz
import pytest
from test_ctt_batch_observation_inputs import (
    _evidence,
    _paint_photo,
    _replace_source,
    _staff_photo,
)
from test_ctt_batch_observation_inputs import retained as _retained

from devnous.copa_telmex import staff_batch_observation_inputs as inputs
from devnous.copa_telmex.staff_batch_contracts import digest
from devnous.copa_telmex.staff_batch_corrections import (
    editable_fields,
    validate_edit_value,
)
from devnous.copa_telmex.staff_batch_store import BatchDenied

retained = _retained


def proposal(parts, field, value):
    snapshot, pages, profile, analysis = parts
    return [
        dict(
            principal_id="actor",
            source_pages=pages,
            edits=[dict(field_id=field, value=value)],
        )
    ]


def replay(parts, records):
    snapshot, pages, profile, analysis = parts
    return inputs.verify_manual_observation_inputs(
        snapshot, pages, profile, analysis, records=records, principal_id="actor"
    )


def fields(parts):
    return {
        f["field_id"]: f
        for f in editable_fields(parts[3], profile=parts[2], source_pages=parts[1])
    }


@pytest.mark.parametrize(
    "field,value",
    [
        (
            "players.1.first_name",
            {
                "kind": "source_field",
                "value": "Ana",
                "rectangle": {"page_index": 1, "x": 1, "y": 2, "width": 3, "height": 4},
            },
        ),
        ("staff.1.presence", "ABSENT"),
        ("staff.1.presence", {"kind": "presence", "presence": "PRESENT"}),
        (
            "players.1.photo",
            {
                "kind": "photo",
                "presence": "ABSENT",
                "rectangle": {"page_index": 1, "x": 1, "y": 2, "width": 3, "height": 4},
            },
        ),
    ],
)
def test_closed_value_union_accepts_pending_interpretations(field, value):
    validate_edit_value(field, value)


@pytest.mark.parametrize(
    "fault",
    [
        "float",
        "bool",
        "page",
        "unknown",
        "missing_value",
        "extra",
        "path",
        "wrong_kind",
        "null_presence",
        "raw_photo",
        "unknown_slot",
    ],
)
def test_closed_value_union_rejects_foreign_or_implicit_claims(fault):
    field = "players.1.birth_date"
    value = {
        "kind": "source_field",
        "value": "2000-01-01",
        "rectangle": {"page_index": 1, "x": 1, "y": 2, "width": 3, "height": 4},
    }
    if fault == "float":
        value["rectangle"]["x"] = 1.0
    elif fault == "bool":
        value["rectangle"]["width"] = True
    elif fault == "page":
        value["rectangle"]["page_index"] = 0
    elif fault == "unknown":
        value["rectangle"]["source_path"] = "/tmp/secret"
    elif fault == "missing_value":
        del value["value"]
    elif fault == "extra":
        value["approved"] = True
    elif fault == "path":
        value = "a\nsecret"
    elif fault == "wrong_kind":
        value["kind"] = "photo"
    elif fault == "null_presence":
        field = "players.1.presence"
        value = None
    elif fault == "raw_photo":
        field = "players.1.photo"
        value = "PRESENT"
    else:
        field = "slots.1.99.presence"
        value = "PRESENT"
    with pytest.raises(BatchDenied):
        validate_edit_value(field, value)


def test_geometry_and_bad_date_only_override_explicit_field(retained):
    original = copy.deepcopy(retained[3])
    data = retained[3]
    field = "players.1.birth_date"
    data["auto_ready"] = False
    data["issues"] = [{"code": "READER_DISAGREEMENT"}]
    data["verification_input"]["readings"][0]["fields"][field] = "bad raw date"
    records = proposal(
        retained,
        field,
        {
            "kind": "source_field",
            "value": "2000-01-01",
            "rectangle": fields(retained)[field]["rectangle"],
        },
    )
    corrected, evidence = replay(retained, records)
    assert evidence["readings"][0]["fields"][field] == "bad raw date"
    assert corrected["human_edits_hash"] == digest(records)
    assert data["players"] == original["players"]
    assert not any(p.get("verified_by_human") for p in corrected["players"])
    data["verification_input"]["readings"][1]["fields"]["team.name"] = "other"
    with pytest.raises(BatchDenied):
        replay(retained, records)


@pytest.mark.parametrize("fault", ["cross_card", "cross_page", "actor", "source"])
def test_source_rectangle_cannot_leave_admitted_card(retained, fault):
    field = "players.1.first_name"
    rect = fields(retained)[field]["rectangle"]
    records = proposal(
        retained, field, {"kind": "source_field", "value": "Alpha", "rectangle": rect}
    )
    if fault == "cross_card":
        rect["x"] = 300
    elif fault == "cross_page":
        rect["page_index"] = 2
    elif fault == "actor":
        records[0]["principal_id"] = "other"
    else:
        records[0]["source_pages"] = [{"page_index": 4}]
    with pytest.raises(BatchDenied):
        replay(retained, records)


def test_present_photo_rederived_from_original_pixels(retained):
    parts = _paint_photo(retained, field="players.1.photo_sha256")
    field = "players.1.photo"
    records = proposal(
        parts,
        field,
        {
            "kind": "photo",
            "presence": "PRESENT",
            "rectangle": fields(parts)[field]["rectangle"],
        },
    )
    before = copy.deepcopy(parts[3])
    corrected, evidence = replay(parts, records)
    assert corrected["players"][0]["photo_evidence"]["mode"] == "SOURCE_RECTANGLE"
    assert (
        corrected["players"][0]["photo"]["sha256"]
        == evidence["proposed_fields"]["players.1.photo_sha256"]
    )
    assert evidence["readings"] == before["verification_input"]["readings"]
    assert before["players"][0]["photo"] is None
    corrected["players"][0]["photo"]["sha256"] = "a" * 64
    # Replay always derives again; metadata from a caller is never an input.
    assert replay(parts, records)[0]["players"][0]["photo"]["sha256"] != "a" * 64


@pytest.mark.parametrize("fault", ["provided", "nonblank", "narrow", "native_photo"])
def test_absence_cannot_discard_a_photo(retained, tmp_path, monkeypatch, fault):
    parts = retained
    if fault == "provided":
        parts = _staff_photo(retained, tmp_path)
        field = "staff.1.photo"
    else:
        field = "players.1.photo"
    if fault == "nonblank":
        parts = _paint_photo(parts, field="players.1.photo_sha256")
    rect = fields(parts)[field]["rectangle"]
    if fault == "narrow":
        rect = {**rect, "width": rect["width"] - 1}
    if fault == "native_photo":
        monkeypatch.setattr(
            inputs, "_native_photo_check", lambda *args: ((1, (1, 2, 3, 4)), "photo")
        )
    records = proposal(
        parts, field, {"kind": "photo", "presence": "ABSENT", "rectangle": rect}
    )
    with pytest.raises(BatchDenied):
        replay(parts, records)


def test_blank_pinned_photo_can_resolve_native_uncertainty(retained, monkeypatch):
    def uncertain(*args):
        raise BatchDenied("REVIEW_BLOCKED")

    monkeypatch.setattr(inputs, "_native_photo_check", uncertain)
    # Untouched other player photo remains normative, so review both exact ROIs.
    records = []
    for n in (1, 2):
        field = f"players.{n}.photo"
        records.extend(
            proposal(
                retained,
                field,
                {
                    "kind": "photo",
                    "presence": "ABSENT",
                    "rectangle": fields(retained)[field]["rectangle"],
                },
            )
        )
    corrected, _ = replay(retained, records)
    assert all(p["photo"] is None for p in corrected["players"])


@pytest.mark.parametrize("kind", ["players", "staff"])
def test_known_person_can_never_be_dropped(retained, kind):
    with pytest.raises(BatchDenied):
        replay(retained, proposal(retained, f"{kind}.1.presence", "ABSENT"))


def blank_unknown_staff(parts):
    snapshot, pages, profile, analysis = parts
    rect = fields(parts)["staff.2.presence"]["card_bounds"]
    stream = snapshot.pdf_for_page(1)
    stream.seek(0)
    with fitz.open(stream=stream.read(), filetype="pdf") as doc:
        doc[0].draw_rect(
            fitz.Rect(
                rect["x"],
                rect["y"],
                rect["x"] + rect["width"],
                rect["y"] + rect["height"],
            ),
            color=None,
            fill=(1, 1, 1),
        )
        raw = doc.tobytes()
    snapshot, pages = _replace_source(snapshot, raw)
    analysis["source_binding_hash"] = digest(pages)
    for name in ("first_name", "last_name", "birth_date", "curp"):
        analysis["staff"][1][name] = None
    _evidence(snapshot, pages, profile, analysis)
    return snapshot, pages, profile, analysis


def test_blank_unknown_staff_absence_keeps_slot_inventory(retained):
    parts = blank_unknown_staff(retained)
    corrected, _ = replay(parts, proposal(parts, "staff.2.presence", "ABSENT"))
    assert len(corrected["staff"]) == len(parts[3]["staff"])
    assert corrected["staff"][1]["presence"] == "ABSENT"
    assert corrected["source_physical_slots"] == parts[3]["source_physical_slots"]


def test_mapped_blank_player_placeholder_preserves_automatic_inventory(retained):
    snapshot, pages, profile, analysis = retained
    # Existing physical slot 2 is admitted and blank; add its fixed index as an
    # explicit placeholder while preserving the two occupied indexes.
    profile["physical_slots"][1]["person_index"] = 3
    rect = inputs._pixel_box(
        profile["observation_views"][0]["fields"]["slots.1.2.presence"],
        {1: type("Size", (), {"width": 400, "height": 600})()},
    )
    from devnous.copa_telmex.staff_batch_corrections import _box

    placeholder = dict(
        first_name=None,
        last_name=None,
        birth_date=None,
        curp=None,
        presence="ABSENT",
        photo=None,
        source_page_index=1,
        slot_ref="page:1:jugador_2",
        absence_box=_box(rect),
    )
    analysis["players"].append(placeholder)
    for view in profile["observation_views"]:
        fields = view["fields"]
        card = fields["slots.1.2.presence"]
        for name in (
            "first_name",
            "last_name",
            "birth_date",
            "curp",
            "presence",
            "photo_sha256",
        ):
            fields["players.3." + name] = copy.deepcopy(card)
    _evidence(snapshot, pages, profile, analysis)
    assert (
        inputs.verify_observation_inputs(snapshot, pages, profile, analysis)
        == analysis["verification_input"]
    )
    placeholder["first_name"] = "known identity"
    _evidence(snapshot, pages, profile, analysis)
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(snapshot, pages, profile, analysis)


@pytest.mark.asyncio
async def test_private_signer_binds_structured_photo_to_pending_ledger(retained):
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from devnous.copa_telmex.staff_batch_verification import BatchPrivateVerifier

    parts = _paint_photo(retained, field="players.1.photo_sha256")
    field = "players.1.photo"
    records = proposal(
        parts,
        field,
        {
            "kind": "photo",
            "presence": "PRESENT",
            "rectangle": fields(parts)[field]["rectangle"],
        },
    )
    corrected, evidence = replay(parts, records)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )

    class Store:
        async def verifier_context(self, *args):
            return {
                "public_key": base64.b64encode(public).decode(),
                "not_after_seconds": 2000,
            }

    class Reader:
        async def verification_input(self, member):
            return replay(parts, records)[1]

    profile_hash = digest(parts[2])
    verifier = BatchPrivateVerifier(
        source_reader=Reader(),
        store=Store(),
        signing_key=key,
        kid="manual-test",
        profile_hash=profile_hash,
        clock=lambda: 1000,
    )
    member = {
        "status": "ready",
        "document_id": "a" * 64,
        "manifest_sha256": "b" * 64,
        "policy_hash": "c" * 64,
        "scope_hash": "d" * 64,
        "payload_hash": "e" * 64,
        "source_pages": parts[1],
        "human_edits": records,
        "frozen_snapshot": {"analysis": corrected},
    }
    proof = (
        await verifier.attest_members(
            {
                "profile_hash": profile_hash,
                "principal_id": "actor",
                "members": [member],
            },
            {"sample": "retained"},
        )
    )[0]
    assert proof["body"]["human_edits_hash"] == digest(records)
    assert proof["body"]["verified_by_human"] is False


@pytest.mark.asyncio
async def test_private_signer_keeps_player_presence_and_printed_slot_bound(retained):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from devnous.copa_telmex.staff_batch_verification import BatchPrivateVerifier
    from devnous.copa_telmex.staff_batch_corrections import _box

    snapshot, pages, profile, analysis = retained
    profile["physical_slots"][1]["person_index"] = 3
    rect = inputs._pixel_box(
        profile["observation_views"][0]["fields"]["slots.1.2.presence"],
        {1: type("Size", (), {"width": 400, "height": 600})()},
    )
    analysis["players"].append(
        dict(
            first_name=None,
            last_name=None,
            birth_date=None,
            curp=None,
            presence="PRESENT",
            photo=None,
            source_page_index=1,
            slot_ref="page:1:jugador_2",
            absence_box=_box(rect),
        )
    )
    for view in profile["observation_views"]:
        for name in (
            "first_name",
            "last_name",
            "birth_date",
            "curp",
            "presence",
            "photo_sha256",
        ):
            view["fields"]["players.3." + name] = copy.deepcopy(
                view["fields"]["slots.1.2.presence"]
            )
    _evidence(
        snapshot, pages, profile, analysis, statuses=["PRESENT", "PRESENT", "PRESENT"]
    )
    records = proposal(
        retained, "players.3.presence", {"kind": "presence", "presence": "ABSENT"}
    )
    corrected, evidence = replay(retained, records)
    assert (
        len(corrected["players"]) == 3
        and corrected["players"][2]["presence"] == "ABSENT"
    )
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )

    class Store:
        async def verifier_context(self, *args):
            return {
                "public_key": base64.b64encode(public).decode(),
                "not_after_seconds": 2000,
            }

    class Reader:
        async def verification_input(self, member):
            return replay(retained, records)[1]

    profile_hash = digest(profile)
    verifier = BatchPrivateVerifier(
        source_reader=Reader(),
        store=Store(),
        signing_key=key,
        kid="manual-test",
        profile_hash=profile_hash,
        clock=lambda: 1000,
    )
    member = {
        "status": "ready",
        "document_id": "a" * 64,
        "manifest_sha256": "b" * 64,
        "policy_hash": "c" * 64,
        "scope_hash": "d" * 64,
        "payload_hash": "e" * 64,
        "source_pages": pages,
        "human_edits": records,
        "frozen_snapshot": {"analysis": corrected},
    }
    proof = (
        await verifier.attest_members(
            {
                "profile_hash": profile_hash,
                "principal_id": "actor",
                "members": [member],
            },
            {"sample": "retained"},
        )
    )[0]
    assert proof["body"]["human_edits_hash"] == digest(records)
    assert evidence["readings"][0]["fields"]["slots.1.2.presence"] == "PRESENT"
    assert evidence["proposed_fields"]["slots.1.2.presence"] == "ABSENT"
    assert proof["body"]["verified_by_human"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "profile",
        "key",
        "expired",
        "required",
        "source_binding",
        "human_type",
        "not_ready",
    ],
)
async def test_machine_verifier_stays_closed_around_pending_review(retained, fault):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from devnous.copa_telmex.staff_batch_verification import BatchPrivateVerifier

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    admitted = {
        "public_key": base64.b64encode(public).decode(),
        "not_after_seconds": 2000,
    }
    evidence = copy.deepcopy(retained[3]["verification_input"])

    class Store:
        async def verifier_context(self, *args):
            return admitted

    class Reader:
        async def verification_input(self, member):
            return evidence

    profile_hash = digest(retained[2])
    verifier = BatchPrivateVerifier(
        source_reader=Reader(),
        store=Store(),
        signing_key=key,
        kid="manual-test",
        profile_hash=profile_hash,
        clock=lambda: 1000,
    )
    private = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    with pytest.raises(ValueError):
        verifier.validate_channel_key(private)
    verifier.validate_channel_key(b"x" * 32)
    member = {
        "status": "ready",
        "document_id": "a" * 64,
        "manifest_sha256": "b" * 64,
        "policy_hash": "c" * 64,
        "scope_hash": "d" * 64,
        "payload_hash": "e" * 64,
        "source_pages": retained[1],
        "human_edits": [],
    }
    run = {"profile_hash": profile_hash, "principal_id": "actor", "members": [member]}
    if fault == "profile":
        run["profile_hash"] = "0" * 64
    elif fault == "key":
        admitted["public_key"] = base64.b64encode(b"x" * 32).decode()
    elif fault == "expired":
        admitted["not_after_seconds"] = 999
    elif fault == "required":
        evidence["proposed_fields"]["team.name"] = None
        for reading in evidence["readings"]:
            reading["fields"]["team.name"] = None
    elif fault == "source_binding":
        evidence["source_binding_hash"] = "f" * 64
    elif fault == "human_type":
        member["human_edits"] = {}
    else:
        member["status"] = "exception"
        assert await verifier.attest_members(run, {"sample": "retained"}) == []
        return
    with pytest.raises(BatchDenied):
        await verifier.attest_members(run, {"sample": "retained"})


@pytest.mark.parametrize(
    "code",
    [
        "INVALID_FIELD:players.1.birth_date",
        "AMBIGUOUS_FIELD:players.1.birth_date",
        "MISSING_FIELD:players.1.birth_date",
        "READINGS_DISAGREE",
        "SOURCE_OBSERVATION_REPLAY_FAILED",
        "OBSERVATION_INPUTS_INVALID",
        "BATCH_PLAN_REJECTED",
        "GEOMETRY_UNCERTAIN",
    ],
)
def test_actual_analyzer_codes_resolve_only_the_explicit_field(retained, code):
    field = "players.1.birth_date"
    retained[3]["issues"] = [code]
    records = proposal(
        retained,
        field,
        {
            "kind": "source_field",
            "value": "2000-01-01",
            "rectangle": fields(retained)[field]["rectangle"],
        },
    )
    assert replay(retained, records)[0]["issues"] == []


@pytest.mark.parametrize(
    "code",
    [
        "MISSING_FIELD:staff.1.birth_date",
        "UNMAPPED_OCCUPIED_SLOT",
        "NONCONTIGUOUS_PERSON_MAPPING",
        "STAFF_ROLE_MISMATCH",
        "STRUCTURAL_CATEGORY_MISMATCH",
        "PHOTO_UNCERTAIN:staff.1.",
        "STAFF_REQUIRED",
    ],
)
def test_unreviewed_or_structural_analyzer_codes_stay_blocked(retained, code):
    retained[3]["issues"] = [code]
    with pytest.raises(BatchDenied):
        replay(retained, proposal(retained, "players.1.birth_date", "2000-01-01"))


@pytest.mark.parametrize(
    "code",
    [
        "PHOTO_UNCERTAIN:players.1.",
        "PHOTO_REQUIRED:players.1.",
        "PHOTO_UNCERTAIN",
        "PHOTO_MISSING",
        "PHOTO_GEOMETRY",
    ],
)
def test_photo_analyzer_codes_require_actual_reviewed_source_crop(retained, code):
    parts = _paint_photo(retained, field="players.1.photo_sha256")
    parts[3]["issues"] = [code]
    field = "players.1.photo"
    records = proposal(
        parts,
        field,
        {
            "kind": "photo",
            "presence": "PRESENT",
            "rectangle": fields(parts)[field]["rectangle"],
        },
    )
    assert replay(parts, records)[0]["players"][0]["photo"] is not None


@pytest.mark.parametrize(
    "kind,code",
    [
        ("staff", "PRESENCE_UNCERTAIN:staff.2.presence"),
        ("staff", "STAFF_REQUIRED"),
        ("staff", "ABSENT_WITH_IDENTITY:staff.2.first_name"),
        ("players", "PRESENCE_UNCERTAIN:slots.1.3.presence"),
        ("players", "ROSTER_SIZE_INVALID"),
        ("players", "PRESENCE_UNCERTAIN"),
    ],
)
def test_present_interpretation_keeps_known_person_in_existing_card(
    retained, kind, code
):
    person = retained[3][kind][1]
    person["presence"] = "ABSENT"
    person["absence_box"] = fields(retained)[f"{kind}.2.presence"]["card_bounds"]
    _evidence(
        *retained,
        statuses=["PRESENT", "ABSENT", "ABSENT"] if kind == "players" else None,
    )
    retained[3]["issues"] = [code]
    corrected, _ = replay(retained, proposal(retained, f"{kind}.2.presence", "PRESENT"))
    assert corrected[kind][1]["presence"] == "PRESENT"
    assert corrected[kind][1]["first_name"] == person["first_name"]


def test_later_ledger_record_cannot_erase_a_supplied_photo(retained):
    present = proposal(
        retained,
        "players.1.photo",
        {
            "kind": "photo",
            "presence": "PRESENT",
            "rectangle": fields(retained)["players.1.first_name"]["rectangle"],
        },
    )
    absent = proposal(
        retained,
        "players.1.photo",
        {
            "kind": "photo",
            "presence": "ABSENT",
            "rectangle": fields(retained)["players.1.photo"]["rectangle"],
        },
    )
    with pytest.raises(BatchDenied):
        replay(retained, present + absent)


def test_later_ledger_record_cannot_drop_identity_just_supplied(retained):
    parts = blank_unknown_staff(retained)
    names = proposal(parts, "staff.2.first_name", "New pending name")
    absent = proposal(parts, "staff.2.presence", "ABSENT")
    with pytest.raises(BatchDenied):
        replay(parts, names + absent)
