"""Source pixel replay and complete printed-slot coverage, without a provider."""

import copy
import hashlib
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import fitz
import pytest
from PIL import Image, ImageDraw

from devnous.copa_telmex import staff_batch_observation_inputs as inputs
from devnous.copa_telmex.registration_governance import _derive_geometry
from devnous.copa_telmex.staff_batch_contracts import (
    canonical,
    digest,
    stable_document_id,
    strict_loads,
    validate_source_manifest,
)
from devnous.copa_telmex.staff_batch_plan_builder import semantic_fields
from devnous.copa_telmex.staff_batch_source_adapter import SourceSnapshotV2
from devnous.copa_telmex.staff_batch_store import BatchDenied
from devnous.copa_telmex.staff_photo_store import (
    derive_rectangular_review_photo,
    encode_review_photo,
)


def _evidence(snapshot, pages, profile, analysis, statuses=None):
    fields = semantic_fields(analysis)
    for index, status in enumerate(statuses or ["PRESENT", "ABSENT", "PRESENT"], 1):
        fields[f"slots.1.{index}.presence"] = status
    hashes = inputs.derive_input_hashes(snapshot, pages, profile, list(fields))
    analysis["verification_input"] = {
        "expected_fields": list(fields),
        "required_fields": [k for k, v in fields.items() if v is not None],
        "proposed_fields": fields,
        "source_binding_hash": digest(pages),
        "readings": [{**h, "fields": dict(fields)} for h in hashes],
    }
    return analysis["verification_input"]


def _replace_source(snapshot, raw_pdf):
    """A new synthetic original, not mutation of an admitted source identity."""
    original_manifest = strict_loads(snapshot.manifest_json)
    with fitz.open(stream=raw_pdf, filetype="pdf") as document:
        pixmap = document[0].get_pixmap(alpha=False)
        raw_png = pixmap.tobytes("png")
        width, height = pixmap.width, pixmap.height
    pdf_hash, image_hash = (
        hashlib.sha256(raw_pdf).hexdigest(),
        hashlib.sha256(raw_png).hexdigest(),
    )
    edition = original_manifest["edition"]
    doc_id = stable_document_id(edition["edition_id"], [(pdf_hash, 1)])
    manifest = validate_source_manifest(
        {
            "schema_version": "ctt.registration.source-manifest.v2",
            "edition": edition,
            "pdfs": [
                {"filename": "synthetic.pdf", "sha256": pdf_hash, "page_count": 1}
            ],
            "documents": [
                {
                    "document_id": doc_id,
                    "source_slices": [{"pdf_sha256": pdf_hash, "pages": [1]}],
                    "extraction": {},
                }
            ],
        }
    )
    asset = uuid4()
    pages = [
        {
            "page_index": 1,
            "pdf_sha256": pdf_hash,
            "pdf_page": 1,
            "asset_id": str(asset),
            "image_sha256": image_hash,
            "width": width,
            "height": height,
        }
    ]
    scope = {
        "schema": "ctt.batch.source-snapshot.v2",
        "tenant_id": edition["tenant_id"],
        "edition_id": edition["edition_id"],
        "tournament_slug": edition["roster_slug"],
        "manifest_sha256": manifest["manifest_sha256"],
        "document_id": doc_id,
        "source_pages": pages,
    }
    result = SourceSnapshotV2(
        canonical(manifest),
        {pdf_hash: BytesIO(raw_pdf)},
        {asset: raw_png},
        canonical(scope),
        4 * 1024 * 1024,
    )
    return result, pages


def _paint_photo(retained, *, field="staff.1.photo_sha256"):
    snapshot, pages, profile, analysis = retained
    spec = profile["observation_views"][0]["fields"][field]
    x, y = round(spec["x"] * pages[0]["width"]), round(spec["y"] * pages[0]["height"])
    width, height = round(spec["width"] * pages[0]["width"]), round(
        spec["height"] * pages[0]["height"]
    )
    stream = snapshot.pdf_for_page(1)
    stream.seek(0)
    with fitz.open(stream=stream.read(), filetype="pdf") as document:
        document[0].draw_rect(
            fitz.Rect(x, y, x + width, y + height), color=None, fill=(0.2, 0.3, 0.6)
        )
        raw_pdf = document.tobytes()
    snapshot, pages = _replace_source(snapshot, raw_pdf)
    analysis["source_binding_hash"] = digest(pages)
    _evidence(snapshot, pages, profile, analysis)
    return snapshot, pages, profile, analysis


def _staff_photo(retained, tmp_path):
    snapshot, pages, profile, analysis = _paint_photo(retained)
    spec = profile["observation_views"][0]["fields"]["staff.1.photo_sha256"]
    coords = {
        "x": round(spec["x"] * pages[0]["width"]),
        "y": round(spec["y"] * pages[0]["height"]),
        "width": round(spec["width"] * pages[0]["width"]),
        "height": round(spec["height"] * pages[0]["height"]),
    }
    raw = snapshot.page_assets[next(iter(snapshot.page_assets))]
    image_path = tmp_path / "synthetic-source.png"
    image_path.write_bytes(raw)
    geometry = _derive_geometry(
        SimpleNamespace(image_path=image_path, sha256=hashlib.sha256(raw).hexdigest()),
        coords,
    )
    assert geometry is not None
    photo = derive_rectangular_review_photo(raw, geometry, max_page_bytes=1024 * 1024)
    analysis["staff"][0].update(
        photo={k: getattr(photo, k) for k in ("photo_path", "sha256", "ahash")},
        photo_evidence={"mode": "SOURCE_RECTANGLE", "geometry": geometry},
    )
    _evidence(snapshot, pages, profile, analysis)
    return snapshot, pages, profile, analysis


@pytest.fixture
def retained():
    document = fitz.open()
    page = document.new_page(width=400, height=600)
    page.insert_text((30, 70), "Alpha synthetic person one", fontsize=12)
    page.insert_text((30, 430), "Charlie synthetic person three", fontsize=12)
    page.insert_text((250, 70), "Director synthetic", fontsize=10)
    page.insert_text((250, 250), "Auxiliar synthetic", fontsize=10)
    raw_pdf = document.tobytes()
    raw_png = page.get_pixmap(alpha=False).tobytes("png")
    document.close()
    pdf_hash, image_hash = (
        hashlib.sha256(raw_pdf).hexdigest(),
        hashlib.sha256(raw_png).hexdigest(),
    )
    edition = {
        "tenant_id": "synthetic",
        "edition_id": str(uuid4()),
        "tournament_id": str(uuid4()),
        "edition_year": 2026,
        "roster_slug": "ctt-synthetic",
        "phase": "national",
    }
    doc_id = stable_document_id(edition["edition_id"], [(pdf_hash, 1)])
    manifest = validate_source_manifest(
        {
            "schema_version": "ctt.registration.source-manifest.v2",
            "edition": edition,
            "pdfs": [
                {"filename": "synthetic.pdf", "sha256": pdf_hash, "page_count": 1}
            ],
            "documents": [
                {
                    "document_id": doc_id,
                    "source_slices": [{"pdf_sha256": pdf_hash, "pages": [1]}],
                    "extraction": {},
                }
            ],
        }
    )
    asset_id = uuid4()
    pages = [
        {
            "page_index": 1,
            "pdf_sha256": pdf_hash,
            "pdf_page": 1,
            "asset_id": str(asset_id),
            "image_sha256": image_hash,
            "width": 400,
            "height": 600,
        }
    ]
    scope = {
        "schema": "ctt.batch.source-snapshot.v2",
        "tenant_id": edition["tenant_id"],
        "edition_id": edition["edition_id"],
        "tournament_slug": edition["roster_slug"],
        "manifest_sha256": manifest["manifest_sha256"],
        "document_id": doc_id,
        "source_pages": pages,
    }
    snapshot = SourceSnapshotV2(
        canonical(manifest),
        {pdf_hash: BytesIO(raw_pdf)},
        {asset_id: raw_png},
        canonical(scope),
        1024 * 1024,
    )

    def person(name, slot=None):
        result = {
            "first_name": name,
            "last_name": "Synthetic",
            "birth_date": "2000-01-01",
            "curp": None,
            "presence": "PRESENT",
            "photo": None,
        }
        if slot is not None:
            result["slot_ref"] = f"page:1:jugador_{slot}"
        return result

    analysis = {
        "team": {"name": "Synthetic", "category": "UNICA", "gender": "femenil"},
        "players": [person("Alpha", 1), person("Charlie", 3)],
        "staff": [
            {**person("Director"), "role": "director_tecnico"},
            {**person("Auxiliar"), "role": "auxiliar"},
        ],
        "source_binding_hash": digest(pages),
        "source_physical_slots": [f"page:1:jugador_{i}" for i in (1, 2, 3)],
    }
    boxes = {
        i: {"page_index": 1, "x": 0.05, "y": y, "width": 0.4, "height": 0.2}
        for i, y in ((1, 0.05), (2, 0.35), (3, 0.65))
    }
    fields = {field: copy.deepcopy(boxes[1]) for field in semantic_fields(analysis)}
    staff_boxes = {
        index: {"page_index": 1, "x": 0.6, "y": y, "width": 0.3, "height": 0.2}
        for index, y in ((1, 0.05), (2, 0.35))
    }
    for field in fields:
        if field.startswith("players.2."):
            fields[field] = copy.deepcopy(boxes[3])
        for index in (1, 2):
            if field.startswith(f"staff.{index}."):
                fields[field] = copy.deepcopy(staff_boxes[index])
        if field.endswith(".photo_sha256"):
            # Faithful blank photo area below the synthetic name line.
            fields[field]["y"] += 0.1
            fields[field]["height"] = 0.08
            fields[field]["width"] = 0.12
    for kind in ("players", "staff"):
        for index, person in enumerate(analysis[kind], 1):
            slot = 3 if person.get("slot_ref") == "page:1:jugador_3" else 1
            spec = boxes[slot] if kind == "players" else staff_boxes[index]
            pixel = {
                "page_index": 1,
                "x": round(spec["x"] * 400),
                "y": round(spec["y"] * 600),
                "width": round(spec["width"] * 400),
                "height": round(spec["height"] * 600),
            }
            person["source_page_index"] = 1
            person["boxes"] = {
                "full_name": copy.deepcopy(pixel),
                "birth_date": copy.deepcopy(pixel),
            }
    fields.update({f"slots.1.{i}.presence": copy.deepcopy(boxes[i]) for i in (1, 2, 3)})
    # A configured but absent identity crop remains part of the actual input.
    fields["players.3.first_name"] = copy.deepcopy(boxes[2])
    second = {
        field: {
            **box,
            "x": box["x"] - 0.01,
            "y": box["y"] - 0.01,
            "width": box["width"] + 0.02,
            "height": box["height"] + 0.02,
        }
        for field, box in fields.items()
    }
    profile = {
        "template_id": "synthetic-letter",
        "pipeline_revision": "synthetic-v1",
        "physical_slots": [
            {
                "slot_ref": f"page:1:jugador_{i}",
                "presence_field": f"slots.1.{i}.presence",
                **({"person_index": 1 if i == 1 else 2} if i != 2 else {}),
            }
            for i in (1, 2, 3)
        ],
        "observation_views": [
            {"method": "fields-v1", "fields": fields},
            {"method": "context-v1", "fields": second},
        ],
        "blank_profile": {"white_threshold": 250, "max_nonwhite_fraction": 0},
    }
    _evidence(snapshot, pages, profile, analysis)
    return snapshot, pages, profile, analysis


def test_real_source_pixels_and_all_slots_replay_without_granting_authority(retained):
    snapshot, pages, profile, analysis = retained
    result = inputs.verify_observation_inputs(*retained)
    assert result == analysis["verification_input"]
    assert "verified_by_human" not in result
    assert inputs.slot_presence_fields(profile) == tuple(
        f"slots.1.{i}.presence" for i in (1, 2, 3)
    )
    assert result["readings"][0]["input_hash"] != result["readings"][1]["input_hash"]


@pytest.mark.parametrize(
    "fault",
    [
        "hash",
        "method",
        "profile",
        "source",
        "dimensions",
        "duplicate_slot",
        "missing_slot",
        "unexpected_slot",
        "field_value",
    ],
)
def test_unrelated_inputs_or_source_inventory_fail_closed(retained, fault):
    snapshot, pages, profile, analysis = retained
    if fault == "hash":
        analysis["verification_input"]["readings"][0]["input_hash"] = "a" * 64
    elif fault == "method":
        analysis["verification_input"]["readings"][1]["method"] = "arbitrary"
    elif fault == "profile":
        profile["observation_views"][0]["fields"]["team.name"]["x"] += 0.01
    elif fault == "source":
        snapshot.page_assets[next(iter(snapshot.page_assets))] = b"corrupt"
    elif fault == "dimensions":
        pages[0]["width"] += 1
    elif fault == "duplicate_slot":
        analysis["players"][1]["slot_ref"] = analysis["players"][0]["slot_ref"]
    elif fault == "missing_slot":
        analysis["verification_input"]["expected_fields"].remove("slots.1.2.presence")
    elif fault == "unexpected_slot":
        analysis["players"][1]["slot_ref"] = "page:1:jugador_20"
    else:
        analysis["players"][0]["first_name"] = "Unobserved"
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(snapshot, pages, profile, analysis)


def test_omitted_occupied_slot_is_rejected_even_with_recomputed_receipt(retained):
    snapshot, pages, profile, analysis = retained
    analysis["players"].pop()
    _evidence(snapshot, pages, profile, analysis)
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(*retained)


def test_falsely_absent_occupied_slot_cannot_be_hidden_by_two_equal_readings(retained):
    snapshot, pages, profile, analysis = retained
    analysis["players"].pop()
    _evidence(snapshot, pages, profile, analysis, ["PRESENT", "ABSENT", "ABSENT"])
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(*retained)


@pytest.mark.parametrize(
    "fault",
    [
        "same_pixels",
        "missing_blank",
        "weak_blank",
        "outside",
        "boolean_page",
        "unknown_view",
        "missing_crop",
        "different_inventory",
        "budget",
    ],
)
def test_invalid_profile_cannot_supply_provenance(retained, monkeypatch, fault):
    snapshot, pages, profile, analysis = retained
    if fault == "same_pixels":
        profile["observation_views"][1]["fields"] = copy.deepcopy(
            profile["observation_views"][0]["fields"]
        )
    elif fault == "missing_blank":
        profile.pop("blank_profile")
    elif fault == "weak_blank":
        profile["blank_profile"]["max_nonwhite_fraction"] = 0.5
    elif fault == "outside":
        profile["observation_views"][0]["fields"]["team.name"]["width"] = 2
    elif fault == "boolean_page":
        profile["observation_views"][0]["fields"]["team.name"]["page_index"] = True
    elif fault == "unknown_view":
        profile["observation_views"][0]["request_geometry"] = {}
    elif fault == "missing_crop":
        del profile["observation_views"][0]["fields"]["slots.1.2.presence"]
    elif fault == "different_inventory":
        del profile["observation_views"][1]["fields"]["players.3.first_name"]
    else:
        monkeypatch.setattr(inputs, "_MAX_TOTAL_PIXELS", 100)
    with pytest.raises(BatchDenied):
        if fault in {"missing_blank", "weak_blank"}:
            _evidence(snapshot, pages, profile, analysis)
            inputs.verify_observation_inputs(*retained)
        else:
            inputs.derive_input_hashes(
                snapshot,
                pages,
                profile,
                analysis["verification_input"]["expected_fields"],
            )


def test_every_configured_crop_is_hashed_including_absent_identity(retained):
    snapshot, pages, profile, analysis = retained
    before = inputs.derive_input_hashes(
        snapshot, pages, profile, analysis["verification_input"]["expected_fields"]
    )
    assert (
        "players.3.first_name" not in analysis["verification_input"]["expected_fields"]
    )
    profile["observation_views"][0]["fields"]["players.3.first_name"]["width"] -= 0.01
    after = inputs.derive_input_hashes(
        snapshot, pages, profile, analysis["verification_input"]["expected_fields"]
    )
    assert before != after


def test_explicit_fixed_person_index_must_match_the_materialized_row(retained):
    snapshot, pages, profile, analysis = retained
    profile["physical_slots"][2]["person_index"] = 3
    _evidence(snapshot, pages, profile, analysis)
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(*retained)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_index",
        "physical_inventory",
        "name_geometry",
        "birth_geometry",
        "page",
        "staff_geometry",
        "profile_foreign_slot",
    ],
)
def test_materialized_rows_cannot_select_their_own_source_rectangles(retained, fault):
    snapshot, pages, profile, analysis = retained
    if fault == "missing_index":
        profile["physical_slots"][0].pop("person_index")
        _evidence(snapshot, pages, profile, analysis)
    elif fault == "physical_inventory":
        analysis["source_physical_slots"] = analysis["source_physical_slots"][:2]
    elif fault == "name_geometry":
        analysis["players"][0]["boxes"]["full_name"]["x"] += 1
    elif fault == "birth_geometry":
        analysis["players"][0]["boxes"]["birth_date"]["width"] -= 1
    elif fault == "page":
        analysis["players"][0]["source_page_index"] = 2
    elif fault == "staff_geometry":
        analysis["staff"][1]["boxes"]["full_name"] = dict(
            analysis["staff"][0]["boxes"]["full_name"]
        )
    else:
        for view in profile["observation_views"]:
            view["fields"]["players.2.first_name"] = dict(
                view["fields"]["players.1.first_name"]
            )
        _evidence(snapshot, pages, profile, analysis)
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(*retained)


@pytest.mark.parametrize("card", ["player", "staff"])
def test_repeated_card_pixels_cannot_prove_distinct_physical_slots(retained, card):
    snapshot, pages, profile, analysis = retained
    target = "slots.1.2.presence" if card == "player" else "staff.1.presence"
    for view in profile["observation_views"]:
        view["fields"][target] = dict(view["fields"]["slots.1.1.presence"])
    with pytest.raises(BatchDenied):
        inputs.derive_input_hashes(
            snapshot, pages, profile, analysis["verification_input"]["expected_fields"]
        )


def test_context_view_of_another_region_cannot_count_as_second_reading(retained):
    snapshot, pages, profile, analysis = retained
    profile["observation_views"][1]["fields"]["team.name"] = dict(
        profile["observation_views"][1]["fields"]["slots.1.3.presence"]
    )
    with pytest.raises(BatchDenied):
        inputs.derive_input_hashes(
            snapshot, pages, profile, analysis["verification_input"]["expected_fields"]
        )


def test_provided_curp_geometry_is_bound_to_the_configured_field(retained):
    snapshot, pages, profile, analysis = retained
    person = analysis["players"][0]
    person["curp"] = "SYNTHETIC-SOURCE-VALUE"
    person["boxes"]["curp"] = dict(person["boxes"]["birth_date"])
    _evidence(snapshot, pages, profile, analysis)
    assert inputs.verify_observation_inputs(*retained)
    person["boxes"]["curp"]["x"] += 1
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(*retained)


def test_staff_absence_needs_both_blank_pixels_and_exact_absence_geometry(retained):
    snapshot, pages, profile, analysis = retained
    for view in profile["observation_views"]:
        for field, box in view["fields"].items():
            if field.startswith("staff.2."):
                box["y"] += 0.3
    person = analysis["staff"][1]
    person.update(
        first_name=None,
        last_name=None,
        birth_date=None,
        presence="ABSENT",
        absence_box={"page_index": 1, "x": 240, "y": 390, "width": 120, "height": 120},
    )
    _evidence(snapshot, pages, profile, analysis)
    assert inputs.verify_observation_inputs(*retained)
    person["absence_box"]["width"] -= 1
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(*retained)


def test_supplied_staff_photo_is_rederived_from_its_pinned_source_roi(
    retained, tmp_path
):
    updated = _staff_photo(retained, tmp_path)
    assert inputs.verify_observation_inputs(*updated)


@pytest.mark.parametrize("kind", ["players.1", "staff.1"])
def test_two_null_photo_readings_cannot_omit_nonblank_source_photo(retained, kind):
    updated = _paint_photo(retained, field=kind + ".photo_sha256")
    assert all(
        r["fields"][kind + ".photo_sha256"] is None
        for r in updated[3]["verification_input"]["readings"]
    )
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(*updated)


@pytest.mark.parametrize(
    "fault",
    [
        "sha256",
        "ahash",
        "photo_path",
        "mode",
        "coordinates",
        "source_hash",
        "extra_metadata",
    ],
)
def test_staff_photo_cache_cannot_select_another_image_or_rectangle(
    retained, tmp_path, fault
):
    snapshot, pages, profile, analysis = _staff_photo(retained, tmp_path)
    person = analysis["staff"][0]
    if fault in {"sha256", "ahash", "photo_path"}:
        person["photo"][fault] = "unrelated-source"
    elif fault == "mode":
        person["photo_evidence"]["mode"] = "REQUEST_RECTANGLE"
    elif fault == "coordinates":
        person["photo_evidence"]["geometry"]["field_coordinates"]["x"] += 1
    elif fault == "source_hash":
        person["photo_evidence"]["geometry"]["original_image_hash"] = (
            "sha256:" + "f" * 64
        )
    else:
        person["photo"]["data"] = "untrusted-bytes"
    _evidence(snapshot, pages, profile, analysis)
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(snapshot, pages, profile, analysis)


def test_missing_photo_roi_and_latent_photo_evidence_fail_closed(retained):
    snapshot, pages, profile, analysis = retained
    analysis["players"][0]["photo_evidence"] = {
        "mode": "BATCH_PHYSICAL_SLOT",
        "slot_ref": "page:1:jugador_1",
    }
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(*retained)
    analysis["players"][0].pop("photo_evidence")
    del profile["observation_views"][0]["fields"]["players.1.photo_sha256"]
    with pytest.raises(BatchDenied):
        inputs.verify_observation_inputs(*retained)


def _letter_photo(*, page_index=1, slot=1):
    image = Image.new("RGB", (612, 792), "white")
    draw = ImageDraw.Draw(image)
    top, rows, step = (238, 5, 97) if page_index == 1 else (115, 6, 97)
    for x in (46, 122, 311, 385, 565):
        draw.line((x, top, x, top + rows * step), fill="black", width=2)
    for row in range(rows + 1):
        draw.line((46, top + row * step, 565, top + row * step), fill="black", width=2)
    row = (slot - 1) // 2 + 1 if page_index == 1 else (slot - 9) // 2
    column = (slot - 1) % 2 if page_index == 1 else (slot - 9) % 2
    left, right = (46, 122) if column == 0 else (311, 385)
    draw.rectangle(
        (left + 5, top + row * step + 5, right - 5, top + (row + 1) * step - 5),
        fill=(40, 60, 90),
    )
    return image


@pytest.mark.parametrize(
    "page_index,slot", [(1, 1), (1, 7), (1, 8), (2, 9), (2, 19), (2, 20)]
)
def test_profile_photo_bounds_match_the_unchanged_calibrated_crop(
    monkeypatch, page_index, slot
):
    from devnous.tournaments.core.ctt_batch_photo_alignment import extract_batch_photo

    monkeypatch.setattr(
        "devnous.tournaments.core.ctt_batch_photo_alignment._face_present",
        lambda _image: True,
    )
    image = _letter_photo(page_index=page_index, slot=slot)
    output = BytesIO()
    image.save(output, format="PNG")
    page, box = inputs._player_photo_box(
        output.getvalue(), f"page:{page_index}:jugador_{slot}"
    )
    expected, status = extract_batch_photo(image, page_index=page_index, slot=slot)
    assert page == page_index and status == "photo" and expected is not None
    assert (
        encode_review_photo(image.crop(box)).sha256
        == encode_review_photo(expected).sha256
    )


def _player_photo_arguments(retained, monkeypatch):
    monkeypatch.setattr(
        "devnous.tournaments.core.ctt_batch_photo_alignment._face_present",
        lambda _image: True,
    )
    image = _letter_photo()
    output = BytesIO()
    image.save(output, format="PNG")
    with fitz.open() as document:
        page = document.new_page(width=image.width, height=image.height)
        page.insert_image(page.rect, stream=output.getvalue())
        raw_pdf = document.tobytes()
    snapshot, pages = _replace_source(retained[0], raw_pdf)
    photo = snapshot.derive_player_photo("page:1:jugador_1")
    raw = snapshot.page_assets[next(iter(snapshot.page_assets))]
    photo_page, box = inputs._player_photo_box(raw, "page:1:jugador_1")
    with Image.open(BytesIO(raw)) as decoded:
        with decoded.crop(box).convert("L") as gray:
            histogram, pixels = gray.histogram(), gray.width * gray.height
    person = {
        "presence": "PRESENT",
        "slot_ref": "page:1:jugador_1",
        "photo": {k: getattr(photo, k) for k in ("photo_path", "sha256", "ahash")},
        "photo_evidence": {
            "mode": "BATCH_PHYSICAL_SLOT",
            "slot_ref": "page:1:jugador_1",
        },
    }
    rects = {("calibrated", "players.1.photo_sha256"): (photo_page, box)}
    stats = {("calibrated", "players.1.photo_sha256"): (histogram, pixels)}
    return (
        snapshot,
        pages,
        {"blank_profile": retained[2]["blank_profile"]},
        person,
        "players.1.",
        rects,
        stats,
        "calibrated",
    )


@pytest.mark.parametrize("fault", [None, "source_slot", "metadata", "roi"])
def test_player_photo_uses_actual_snapshot_calibration_not_cached_crop(
    retained, monkeypatch, fault
):
    args = _player_photo_arguments(retained, monkeypatch)
    person, rects = args[3], args[5]
    photo_page, box = rects[("calibrated", "players.1.photo_sha256")]
    if fault == "source_slot":
        person["photo_evidence"]["slot_ref"] = "page:1:jugador_3"
    elif fault == "metadata":
        person["photo"]["sha256"] = "f" * 64
    elif fault == "roi":
        rects[("calibrated", "players.1.photo_sha256")] = (
            photo_page,
            (box[0] + 10, box[1], box[2], box[3]),
        )
    if fault is None:
        inputs._person_photo(*args)
    else:
        with pytest.raises(BatchDenied):
            inputs._person_photo(*args)


@pytest.mark.parametrize(
    "signal", ["photo", "uncertain", "partial_grid", "ambiguous_row"]
)
def test_blank_profile_region_cannot_hide_a_detectable_native_photo(
    retained, monkeypatch, signal
):
    from dataclasses import replace

    from devnous.tournaments.core import ctt_batch_photo_alignment as alignment

    args = _player_photo_arguments(retained, monkeypatch)
    snapshot, person, rects, stats = args[0], args[3], args[5], args[6]
    raw = snapshot.page_assets[next(iter(snapshot.page_assets))]
    person.update(photo=None, photo_evidence=None)
    # The cache points to a blank name-area rectangle instead of the native
    # occupied picture. A profile-only blank check would incorrectly accept it.
    box = (140, 350, 200, 390)
    rects[("calibrated", "players.1.photo_sha256")] = (1, box)
    with Image.open(BytesIO(raw)) as decoded:
        with decoded.crop(box).convert("L") as gray:
            stats[("calibrated", "players.1.photo_sha256")] = (
                gray.histogram(),
                gray.width * gray.height,
            )
    if signal == "uncertain":
        monkeypatch.setattr(alignment, "_face_present", lambda _image: False)
    elif signal == "partial_grid":
        monkeypatch.setattr(alignment, "prepare_batch_page", lambda *_args: None)
    elif signal == "ambiguous_row":
        original = alignment.prepare_batch_page

        def ambiguous(image, page_index):
            grid = original(image, page_index)
            scores = list(grid.boundary_scores)
            scores[1] = 0.1
            return replace(grid, boundary_scores=tuple(scores))

        monkeypatch.setattr(alignment, "prepare_batch_page", ambiguous)
    with pytest.raises(BatchDenied):
        inputs._person_photo(*args)


def test_native_missing_photo_can_use_only_its_actual_calibrated_region(
    retained, monkeypatch
):
    args = _player_photo_arguments(retained, monkeypatch)
    snapshot, person, rects, stats = args[0], args[3], args[5], args[6]
    raw = snapshot.page_assets[next(iter(snapshot.page_assets))]
    person.update(photo=None, photo_evidence=None, slot_ref="page:1:jugador_2")
    page, box = inputs._player_photo_box(raw, person["slot_ref"])
    rects[("calibrated", "players.1.photo_sha256")] = (page, box)
    with Image.open(BytesIO(raw)) as decoded:
        with decoded.crop(box).convert("L") as gray:
            stats[("calibrated", "players.1.photo_sha256")] = (
                gray.histogram(),
                gray.width * gray.height,
            )
    inputs._person_photo(*args)
