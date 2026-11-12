"""Replay reader inputs from pinned profile rectangles and immutable source pixels.

These checks establish input provenance and printed-slot coverage, not OCR
accuracy or authority. No provider, credential, database or network is used.
"""

from __future__ import annotations

import hashlib
import math
import re
from io import BytesIO
from typing import NoReturn
from uuid import UUID

from PIL import Image

from .staff_batch_contracts import canonical, digest, strict_loads
from .staff_batch_store import BatchDenied
from .staff_batch_verification import normalize_observation
from .staff_photo_store import derive_rectangular_review_photo

_SLOT = re.compile(r"page:([1-4]):jugador_([1-9]|[12][0-9]|3[0-5])\Z")
_METHOD = re.compile(r"[A-Za-z0-9._:-]{1,80}\Z")
_BOX_KEYS = {"page_index", "x", "y", "width", "height"}
_MAX_PAGE_BYTES = 16 * 1024 * 1024
_MAX_PAGE_PIXELS = 25_000_000
_MAX_TOTAL_PIXELS = 100_000_000


def _deny() -> NoReturn:
    raise BatchDenied("REVIEW_BLOCKED")


def _slots(profile):
    if type(profile) is not dict:
        _deny()
    slots = profile.get("physical_slots")
    if type(slots) is not list or not 1 <= len(slots) <= 35:
        _deny()
    refs, fields, indexes = set(), set(), set()
    for slot in slots:
        if type(slot) is not dict or set(slot) not in (
            {"slot_ref", "presence_field"},
            {"slot_ref", "presence_field", "person_index"},
        ):
            _deny()
        raw_ref = slot.get("slot_ref")
        match = _SLOT.fullmatch(raw_ref) if type(raw_ref) is str else None
        if match is None:
            _deny()
        page, number = map(int, match.groups())
        if slot["presence_field"] != f"slots.{page}.{number}.presence":
            _deny()
        if slot["slot_ref"] in refs or slot["presence_field"] in fields:
            _deny()
        if "person_index" in slot:
            index = slot["person_index"]
            if type(index) is not int or not 1 <= index <= 35 or index in indexes:
                _deny()
            indexes.add(index)
        refs.add(slot["slot_ref"])
        fields.add(slot["presence_field"])
    return slots


def slot_presence_fields(profile):
    """Return the mandatory source-slot field IDs of the admitted profile."""
    return tuple(slot["presence_field"] for slot in _slots(profile))


def _pixel_box(spec, images):
    if type(spec) is not dict or set(spec) != _BOX_KEYS:
        _deny()
    page = spec["page_index"]
    if type(page) is not int or page not in images:
        _deny()
    values = [spec[key] for key in ("x", "y", "width", "height")]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        _deny()
    x, y, width, height = values
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        _deny()
    image = images[page]
    box = (
        round(x * image.width),
        round(y * image.height),
        round((x + width) * image.width),
        round((y + height) * image.height),
    )
    if box[0] >= box[2] or box[1] >= box[3]:
        _deny()
    return page, box


def _contains(container, field):
    page, outer = container
    other_page, inner = field
    return (
        page == other_page
        and outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _overlap(first, second):
    page, a = first
    other_page, b = second
    return (
        page == other_page
        and max(a[0], b[0]) < min(a[2], b[2])
        and max(a[1], b[1]) < min(a[3], b[3])
    )


def _actual_box(value, expected):
    if type(value) is not dict or set(value) != _BOX_KEYS:
        _deny()
    page, box = expected
    coordinates = [value[k] for k in ("x", "y", "width", "height")]
    if (
        type(value["page_index"]) is not int
        or value["page_index"] != page
        or any(
            type(n) not in (int, float)
            or not math.isfinite(n)
            or not float(n).is_integer()
            for n in coordinates
        )
        or coordinates != [box[0], box[1], box[2] - box[0], box[3] - box[1]]
    ):
        _deny()


def _person_geometry(person, prefix, presence_rect, rectangles, method):
    if (
        type(person.get("source_page_index")) is not int
        or person["source_page_index"] != presence_rect[0]
    ):
        _deny()
    fields = {
        key: rect
        for (reader, key), rect in rectangles.items()
        if reader == method and key.startswith(prefix)
    }
    if not fields or any(
        not _contains(presence_rect, rect) for rect in fields.values()
    ):
        _deny()
    if person.get("presence") == "ABSENT":
        _actual_box(person.get("absence_box"), presence_rect)
        return
    first, last = fields[prefix + "first_name"], fields[prefix + "last_name"]
    if first[0] != last[0]:
        _deny()
    union = (
        first[0],
        (
            min(first[1][0], last[1][0]),
            min(first[1][1], last[1][1]),
            max(first[1][2], last[1][2]),
            max(first[1][3], last[1][3]),
        ),
    )
    boxes = person["boxes"]
    _actual_box(boxes.get("full_name"), union)
    _actual_box(boxes.get("birth_date"), fields[prefix + "birth_date"])
    if person.get("curp") is not None:
        _actual_box(boxes.get("curp"), fields[prefix + "curp"])


def _player_photo_box(raw, slot_ref, *, prepared=None):
    """Replay bounds of the unchanged calibrated printed-slot photo algorithm.

    The actual photo still comes from SourceSnapshotV2.derive_player_photo.
    These bounds only constrain the admitted profile ROI to that same source
    area; they never replace, resize or publish the calibrated crop.
    """
    from devnous.tournaments.core.ctt_batch_photo_alignment import prepare_batch_page

    match = _SLOT.fullmatch(slot_ref)
    if match is None:
        _deny()
    page_index, slot = map(int, match.groups())
    if not (
        (page_index == 1 and 1 <= slot <= 8) or (page_index == 2 and 9 <= slot <= 20)
    ):
        _deny()
    grid = prepared
    if grid is None:
        with Image.open(BytesIO(raw)) as decoded:
            with decoded.convert("RGB") as image:
                grid = prepare_batch_page(image, page_index)
    if grid is None:
        _deny()
    row = (slot - 1) // 2 + 1 if page_index == 1 else (slot - 9) // 2
    column = (slot - 1) % 2 if page_index == 1 else (slot - 9) % 2
    if min(grid.boundary_scores[row : row + 2]) < 0.4:
        _deny()
    top, bottom = grid.boundaries[row : row + 2]
    middle = (top + bottom) / 2
    left_rail, right_rail = grid.rails[column * 2 : column * 2 + 2]
    left = int(left_rail[0] * middle + left_rail[1]) + 2
    right = int(right_rail[0] * middle + right_rail[1]) - 2
    if row == len(grid.boundaries) - 2:
        bottom = min(grid.height, bottom + int(grid.height * 0.012))
    top, bottom = top + 2, bottom - 2
    if not (0 <= left < right <= grid.width and 0 <= top < bottom <= grid.height):
        _deny()
    return page_index, (left, top, right, bottom)


def _native_photo_check(raw, slot_ref, cache):
    """A visible native grid may not be downgraded to a profile-only blank."""
    import numpy as np

    from devnous.tournaments.core.ctt_batch_photo_alignment import (
        _horizontal_grid,
        _vertical_rails,
        extract_batch_photo,
        prepare_batch_page,
    )

    match = _SLOT.fullmatch(slot_ref)
    if match is None:
        _deny()
    page_index, slot = map(int, match.groups())
    if page_index not in (1, 2):
        return None
    key = (hashlib.sha256(raw).hexdigest(), page_index)
    with Image.open(BytesIO(raw)) as decoded:
        with decoded.convert("RGB") as image:
            if key not in cache:
                grid = prepare_batch_page(image, page_index)
                if grid is None:
                    # Reuse the normative native detectors to distinguish a
                    # nongrid admitted template from a partially visible grid.
                    with image.convert("L") as gray:
                        dark = ((np.asarray(gray) < 200) * 255).astype(np.uint8)
                    if (
                        _vertical_rails(dark) is not None
                        or _horizontal_grid(dark, page_index) is not None
                    ):
                        _deny()
                cache[key] = grid
            grid = cache[key]
            if grid is None:
                return None
            rectangle = _player_photo_box(raw, slot_ref, prepared=grid)
            crop, status = extract_batch_photo(
                image, page_index=page_index, slot=slot, prepared=grid
            )
            if crop is not None:
                crop.close()
            return rectangle, status


def _person_photo(
    snapshot,
    source_pages,
    profile,
    person,
    prefix,
    rectangles,
    stats,
    method,
    calibration_cache=None,
):
    field_id = prefix + "photo_sha256"
    photo_rect = rectangles[(method, field_id)]
    page_index, _ = photo_rect
    photo = person.get("photo")
    evidence = person.get("photo_evidence")
    blank = _blank(stats[(method, field_id)], profile.get("blank_profile"))
    page = next(p for p in source_pages if p["page_index"] == page_index)
    raw = snapshot.page_assets[UUID(page["asset_id"])]
    if photo is None:
        native_missing = False
        if prefix.startswith("players."):
            native = _native_photo_check(
                raw,
                person["slot_ref"],
                calibration_cache if calibration_cache is not None else {},
            )
            if native is not None:
                rectangle, status = native
                if not _contains(photo_rect, rectangle) or status != "missing":
                    _deny()
                native_missing = True
        if not (blank or native_missing) or evidence is not None:
            _deny()
        return
    if (
        person.get("presence") != "PRESENT"
        or blank
        or type(photo) is not dict
        or set(photo) != {"photo_path", "sha256", "ahash"}
        or type(evidence) is not dict
    ):
        _deny()
    if prefix.startswith("players."):
        if (
            set(evidence) != {"mode", "slot_ref"}
            or evidence["mode"] != "BATCH_PHYSICAL_SLOT"
            or evidence["slot_ref"] != person["slot_ref"]
        ):
            _deny()
        actual = snapshot.derive_player_photo(person["slot_ref"])
        calibrated = _player_photo_box(raw, person["slot_ref"])
        if not _contains(photo_rect, calibrated):
            _deny()
    else:
        if (
            set(evidence) != {"mode", "geometry"}
            or evidence["mode"] != "SOURCE_RECTANGLE"
        ):
            _deny()
        geometry = evidence["geometry"]
        _actual_box(
            {"page_index": page_index, **geometry["field_coordinates"]}, photo_rect
        )
        actual = derive_rectangular_review_photo(
            raw, geometry, max_page_bytes=_MAX_PAGE_BYTES
        )
    if any(
        photo[key] != getattr(actual, key) for key in ("photo_path", "sha256", "ahash")
    ):
        _deny()


def _derive(snapshot, source_pages, profile, expected_fields):
    if (
        type(profile) is not dict
        or type(source_pages) is not list
        or not 1 <= len(source_pages) <= 4
        or type(expected_fields) not in (list, tuple)
        or not expected_fields
        or any(type(f) is not str for f in expected_fields)
        or len(set(expected_fields)) != len(expected_fields)
    ):
        _deny()
    slots = _slots(profile)
    presence = {s["presence_field"] for s in slots}
    if not presence.issubset(expected_fields):
        _deny()
    views = profile.get("observation_views")
    if type(views) is not list or len(views) != 2:
        _deny()
    snapshot.verify_unchanged()
    images, pages = {}, {}
    try:
        for page in source_pages:
            index = page["page_index"]
            if type(index) is not int or index in images or not 1 <= index <= 4:
                _deny()
            raw = snapshot.page_assets[UUID(page["asset_id"])]
            if (
                type(raw) is not bytes
                or not 0 < len(raw) <= _MAX_PAGE_BYTES
                or hashlib.sha256(raw).hexdigest() != page["image_sha256"]
            ):
                _deny()
            with Image.open(BytesIO(raw)) as decoded:
                if (
                    decoded.format != "PNG"
                    or decoded.width * decoded.height > _MAX_PAGE_PIXELS
                    or decoded.size != (page["width"], page["height"])
                ):
                    _deny()
                images[index] = decoded.convert("RGB")
            pages[index] = page
        if set(images) != set(range(1, len(images) + 1)):
            _deny()
        if any(int(s["slot_ref"].split(":")[1]) not in images for s in slots):
            _deny()
        output, manifests, methods, stats, rectangles = [], [], set(), {}, {}
        field_inventory = None
        for view in views:
            if type(view) is not dict or set(view) != {"method", "fields"}:
                _deny()
            method, fields = view["method"], view["fields"]
            if (
                type(method) is not str
                or not _METHOD.fullmatch(method)
                or method in methods
                or type(fields) is not dict
                or not 1 <= len(fields) <= 512
                or any(type(f) is not str or not f for f in fields)
                or not set(expected_fields).issubset(fields)
            ):
                _deny()
            if field_inventory is not None and field_inventory != set(fields):
                _deny()
            field_inventory = set(fields)
            methods.add(method)
            crops, total, used_pages = [], 0, set()
            for field_id in sorted(fields):
                page_index, box = _pixel_box(fields[field_id], images)
                rectangles[(method, field_id)] = (page_index, box)
                used_pages.add(page_index)
                total += (box[2] - box[0]) * (box[3] - box[1])
                if total > _MAX_TOTAL_PIXELS:
                    _deny()
                with images[page_index].crop(box) as crop:
                    crop_hash = hashlib.sha256(
                        canonical({"mode": "RGB", "size": list(crop.size)})
                        + crop.tobytes()
                    ).hexdigest()
                    if field_id.endswith((".presence", ".photo_sha256")):
                        with crop.convert("L") as gray:
                            stats[(method, field_id)] = (
                                gray.histogram(),
                                crop.width * crop.height,
                            )
                crops.append(
                    {
                        "field_id": field_id,
                        "page_index": page_index,
                        "source_image_sha256": pages[page_index]["image_sha256"],
                        "coordinates": list(box),
                        "crop_sha256": crop_hash,
                    }
                )
            if used_pages != set(images):
                _deny()
            manifests.append(digest(crops))
            output.append(
                {
                    "method": method,
                    "input_hash": digest(
                        {
                            "schema": "ctt.batch.reader-input.v1",
                            "profile_hash": digest(profile),
                            "source_binding_hash": digest(source_pages),
                            "method": method,
                            "crops": crops,
                        }
                    ),
                }
            )
        if manifests[0] == manifests[1]:
            _deny()
        if field_inventory is None:
            _deny()
        card_fields = presence | {
            f for f in field_inventory if re.fullmatch(r"staff\.[12]\.presence", f)
        }
        cards = [
            rectangles[(output[0]["method"], field_id)]
            for field_id in sorted(card_fields)
        ]
        if any(
            _overlap(card, other)
            for index, card in enumerate(cards)
            for other in cards[index + 1 :]
        ):
            _deny()
        for field_id in field_inventory:
            if not _contains(
                rectangles[(output[1]["method"], field_id)],
                rectangles[(output[0]["method"], field_id)],
            ):
                _deny()
        snapshot.verify_unchanged()
        return output, stats, rectangles
    except BatchDenied:
        raise
    except Exception:
        _deny()
    finally:
        for image in images.values():
            image.close()


def derive_input_hashes(snapshot, source_pages, profile, expected_fields):
    """Derive both exact input identities, covering every configured crop.

    A template may configure crops for absent identity fields too. They remain
    hashed even when absent people do not occur in ``expected_fields``.
    """
    return _derive(snapshot, source_pages, profile, expected_fields)[0]


def _blank(stats, blank_profile):
    if type(blank_profile) is not dict or set(blank_profile) != {
        "white_threshold",
        "max_nonwhite_fraction",
    }:
        _deny()
    threshold, maximum = (
        blank_profile["white_threshold"],
        blank_profile["max_nonwhite_fraction"],
    )
    if (
        type(threshold) is not int
        or not 245 <= threshold <= 255
        or type(maximum) not in (int, float)
        or not math.isfinite(maximum)
        or not 0 <= maximum <= 0.001
    ):
        _deny()
    histogram, count = stats
    return sum(histogram[:threshold]) <= count * maximum


def verify_observation_inputs(snapshot, source_pages, profile, analysis):
    """Reject unrelated reader hashes or an omitted occupied printed slot."""
    from .staff_batch_plan_builder import semantic_fields

    try:
        evidence = analysis["verification_input"]
        expected = evidence["expected_fields"]
        proposed = evidence["proposed_fields"]
        semantic = semantic_fields(analysis)
        slots = _slots(profile)
        presence_fields = {s["presence_field"] for s in slots}
        if (
            type(evidence) is not dict
            or type(expected) is not list
            or set(expected) != set(semantic) | presence_fields
            or type(proposed) is not dict
            or set(proposed) != set(expected)
            or evidence.get("source_binding_hash") != digest(source_pages)
            or analysis.get("source_binding_hash") != digest(source_pages)
            or analysis.get("source_physical_slots") != [s["slot_ref"] for s in slots]
        ):
            _deny()
        for field_id, actual in semantic.items():
            if normalize_observation(
                proposed[field_id], field=field_id
            ) != normalize_observation(actual, field=field_id):
                _deny()
        inputs, stats, rectangles = _derive(snapshot, source_pages, profile, expected)
        readings = evidence["readings"]
        if type(readings) is not list or len(readings) != 2:
            _deny()
        for actual, derived in zip(readings, inputs):
            if (
                type(actual) is not dict
                or set(actual) != {"method", "input_hash", "fields"}
                or actual["method"] != derived["method"]
                or actual["input_hash"] != derived["input_hash"]
                or type(actual["fields"]) is not dict
                or set(actual["fields"]) != set(expected)
            ):
                _deny()
        players = analysis["players"]
        if type(players) is not list or any(type(p) is not dict for p in players):
            _deny()
        by_slot = {p["slot_ref"]: (index, p) for index, p in enumerate(players, 1)}
        if len(by_slot) != len(players) or set(by_slot) - {
            s["slot_ref"] for s in slots
        }:
            _deny()
        calibration_cache: dict[tuple[str, int], object | None] = {}
        for slot in slots:
            field_id = slot["presence_field"]
            value = proposed[field_id]
            if value not in {"PRESENT", "ABSENT"} or any(
                r["fields"][field_id] != value for r in readings
            ):
                _deny()
            person = by_slot.get(slot["slot_ref"])
            if value == "PRESENT":
                if person is None or person[1].get("presence") != "PRESENT":
                    _deny()
                if slot.get("person_index") != person[0]:
                    _deny()
                _person_geometry(
                    person[1],
                    f"players.{person[0]}.",
                    rectangles[(inputs[0]["method"], field_id)],
                    rectangles,
                    inputs[0]["method"],
                )
                _person_photo(
                    snapshot,
                    source_pages,
                    profile,
                    person[1],
                    f"players.{person[0]}.",
                    rectangles,
                    stats,
                    inputs[0]["method"],
                    calibration_cache,
                )
            else:
                if person is not None:
                    index, placeholder = person
                    if (
                        slot.get("person_index") != index
                        or placeholder.get("presence") != "ABSENT"
                        or _known_original_person(analysis, "players", index)
                    ):
                        _deny()
                    _person_geometry(
                        placeholder,
                        f"players.{index}.",
                        rectangles[(inputs[0]["method"], field_id)],
                        rectangles,
                        inputs[0]["method"],
                    )
                if not all(
                    _blank(stats[(v["method"], field_id)], profile.get("blank_profile"))
                    for v in inputs
                ):
                    _deny()
        for index, person in enumerate(analysis["staff"], 1):
            field_id = f"staff.{index}.presence"
            _person_geometry(
                person,
                f"staff.{index}.",
                rectangles[(inputs[0]["method"], field_id)],
                rectangles,
                inputs[0]["method"],
            )
            _person_photo(
                snapshot,
                source_pages,
                profile,
                person,
                f"staff.{index}.",
                rectangles,
                stats,
                inputs[0]["method"],
            )
            if person.get("presence") == "ABSENT":
                if not all(
                    _blank(stats[(v["method"], field_id)], profile.get("blank_profile"))
                    for v in inputs
                ):
                    _deny()
        return strict_loads(canonical(evidence))
    except BatchDenied:
        raise
    except Exception:
        _deny()


def _manual_blank(snapshot, source_pages, rectangle, profile):
    """Use the admitted blank calibration on original pixels, never a label."""
    page_index, box = rectangle
    page = next(p for p in source_pages if p["page_index"] == page_index)
    raw = snapshot.page_assets[UUID(page["asset_id"])]
    with Image.open(BytesIO(raw)) as image:
        with image.crop(box).convert("L") as crop:
            return _blank(
                (crop.histogram(), crop.width * crop.height), profile["blank_profile"]
            )


def _known_original_person(analysis, kind, index):
    person = analysis[kind][index - 1]
    prefix = f"{kind}.{index}."
    if person.get("photo_evidence") is not None or any(
        person.get(k) not in (None, "")
        for k in ("first_name", "last_name", "birth_date", "curp", "photo")
    ):
        return True
    return any(
        r["fields"].get(prefix + name) not in (None, "")
        for r in analysis["verification_input"]["readings"]
        for name in ("first_name", "last_name", "birth_date", "curp", "photo_sha256")
    )


def _manual_geometry(snapshot, source_pages, box):
    from types import SimpleNamespace

    from .staff_review_plan_builder import ReviewPlanBuilder

    page = next(p for p in source_pages if p["page_index"] == box["page_index"])
    return ReviewPlanBuilder._geometry(
        SimpleNamespace(_max_page_bytes=_MAX_PAGE_BYTES),
        box,
        SimpleNamespace(
            **{
                **page,
                "asset_id": UUID(page["asset_id"]),
                "image_sha256": "sha256:" + page["image_sha256"],
            }
        ),
        snapshot,
        minimum_pixels=1,
    )


def apply_manual_presence_or_photo(
    result, original, field_id, value, snapshot, source_pages, profile
):
    """Derive only an explicit ledger-selected card interpretation from pixels."""
    from .staff_batch_corrections import (
        field_parts,
        _box,
        _card_id,
        _profile_rectangles,
        _tuple,
        validate_edit_value,
    )

    validate_edit_value(field_id, value)
    kind, index_text, name = field_parts(field_id)
    index = int(index_text)
    person, before = result[kind][index - 1], original[kind][index - 1]
    prefix = f"{kind}.{index}."
    rectangles = _profile_rectangles(profile, source_pages)
    card_id = _card_id(kind, index, before, profile)
    card = rectangles[card_id]
    if before.get("source_page_index") != card[0]:
        _deny()
    proposed = result["verification_input"]["proposed_fields"]
    presence = value["presence"] if type(value) is dict else value
    photo_roi = rectangles[prefix + "photo_sha256"]
    if name == "photo":
        rect = _tuple(value["rectangle"])
        if not _contains(card, rect):
            _deny()
        if presence == "PRESENT":
            if _manual_blank(snapshot, source_pages, rect, profile):
                _deny()
            geometry = _manual_geometry(snapshot, source_pages, value["rectangle"])
            page = next(p for p in source_pages if p["page_index"] == rect[0])
            photo = derive_rectangular_review_photo(
                snapshot.page_assets[UUID(page["asset_id"])],
                geometry,
                max_page_bytes=_MAX_PAGE_BYTES,
            )
            person["photo"] = {
                key: getattr(photo, key) for key in ("photo_path", "sha256", "ahash")
            }
            person["photo_evidence"] = {
                "mode": "SOURCE_RECTANGLE",
                "geometry": geometry,
            }
        else:
            # Known supplied content cannot be relabelled absent. Both the
            # requested ROI and whole pinned photo ROI must be blank.
            if (
                before.get("photo") is not None
                or before.get("photo_evidence") is not None
                or person.get("photo") is not None
                or person.get("photo_evidence") is not None
                or any(
                    r["fields"].get(prefix + "photo_sha256")
                    for r in original["verification_input"]["readings"]
                )
                or not _contains(rect, photo_roi)
                or not _manual_blank(snapshot, source_pages, rect, profile)
            ):
                _deny()
            _manual_no_native_photo(snapshot, source_pages, before, kind)
            person["photo"], person["photo_evidence"] = None, None
        proposed[prefix + "photo_sha256"] = (person.get("photo") or {}).get("sha256")
    else:
        if presence == "ABSENT":
            if _known_original_person(original, kind, index) or any(
                person.get(k) not in (None, "")
                for k in ("first_name", "last_name", "birth_date", "curp", "photo")
            ):
                _deny()
            for name in (
                "first_name",
                "last_name",
                "birth_date",
                "curp",
                "photo_sha256",
            ):
                if not _manual_blank(
                    snapshot, source_pages, rectangles[prefix + name], profile
                ):
                    _deny()
            _manual_no_native_photo(snapshot, source_pages, before, kind)
            person["absence_box"] = _box(card)
        person["presence"] = presence
        proposed[prefix + "presence"] = presence
        if kind == "players":
            proposed[card_id] = presence
    snapshot.verify_unchanged()


def _manual_no_native_photo(snapshot, source_pages, person, kind):
    if kind != "players":
        return
    page = next(
        p for p in source_pages if p["page_index"] == person["source_page_index"]
    )
    try:
        native = _native_photo_check(
            snapshot.page_assets[UUID(page["asset_id"])], person["slot_ref"], {}
        )
    except BatchDenied:
        # Only callers that already proved a blank pinned ROI use this path.
        native = None
    if native is not None and native[1] == "photo":
        _deny()


def _verify_manual_observation_inputs(
    snapshot, source_pages, profile, original, *, records, principal_id
):
    """Replay exact pending overrides; automatic verification stays unchanged.

    Reader hashes, raw values, source inventory and every unedited field/card
    follow the automatic rules. Only ledger-selected interpretations differ.
    """
    from copy import deepcopy

    from .staff_batch_corrections import (
        field_parts,
        _card_id,
        _tuple,
        apply_proposals,
        edit_overrides,
    )
    from .staff_batch_plan_builder import semantic_fields

    corrected = apply_proposals(
        original,
        records=records,
        principal_id=principal_id,
        source_pages=source_pages,
        profile=profile,
        snapshot=snapshot,
    )
    evidence = corrected["verification_input"]
    expected, proposed = evidence["expected_fields"], evidence["proposed_fields"]
    slots = _slots(profile)
    presence_fields = {s["presence_field"] for s in slots}
    semantic = semantic_fields(corrected)
    if (
        set(expected) != set(semantic) | presence_fields
        or set(proposed) != set(expected)
        or corrected.get("source_binding_hash") != digest(source_pages)
        or evidence.get("source_binding_hash") != digest(source_pages)
        or corrected.get("source_physical_slots") != [s["slot_ref"] for s in slots]
    ):
        _deny()
    derived, stats, rectangles = _derive(snapshot, source_pages, profile, expected)
    readings = evidence["readings"]
    for reading, identity in zip(readings, derived):
        if (
            set(reading) != {"method", "input_hash", "fields"}
            or reading["method"] != identity["method"]
            or reading["input_hash"] != identity["input_hash"]
            or set(reading["fields"]) != set(expected)
        ):
            _deny()
    overrides = edit_overrides(records, proposed)
    edits = {e["field_id"]: e["value"] for record in records for e in record["edits"]}
    for field in edits:
        if field.startswith("players.") and field.endswith(".presence"):
            _, position, _ = field_parts(field)
            overrides[
                _card_id(
                    "players",
                    int(position),
                    corrected["players"][int(position) - 1],
                    profile,
                )
            ] = proposed[field]
    for field in expected:
        actual = normalize_observation(proposed[field], field=field)
        if field in semantic and actual != normalize_observation(
            semantic[field], field=field
        ):
            _deny()
        if field not in overrides and any(
            normalize_observation(r["fields"][field], field=field) != actual
            for r in readings
        ):
            _deny()
    # Retain input hashes for ORIGINAL crop views. Only exact source-field
    # geometry overrides replace interpreted first-view evidence bounds.
    geometry_rectangles = deepcopy(rectangles)
    method = derived[0]["method"]
    for field, value in edits.items():
        if type(value) is dict and value.get("kind") == "source_field":
            geometry_rectangles[(method, field)] = _tuple(value["rectangle"])
    by_slot = {p["slot_ref"]: (i, p) for i, p in enumerate(corrected["players"], 1)}
    if len(by_slot) != len(corrected["players"]) or set(by_slot) - {
        s["slot_ref"] for s in slots
    }:
        _deny()
    cache: dict = {}
    for slot in slots:
        field, person = slot["presence_field"], by_slot.get(slot["slot_ref"])
        if person is None:
            if (
                proposed[field] != "ABSENT"
                or any(r["fields"][field] != "ABSENT" for r in readings)
                or not all(
                    _blank(stats[(v["method"], field)], profile["blank_profile"])
                    for v in derived
                )
            ):
                _deny()
            continue
        index, p = person
        if p.get("presence") == "ABSENT" and _known_original_person(
            original, "players", index
        ):
            _deny()
        if slot.get("person_index") != index or proposed[field] != p.get("presence"):
            _deny()
        _verify_manual_person(
            snapshot,
            source_pages,
            profile,
            p,
            original["players"][index - 1],
            f"players.{index}.",
            rectangles[(method, field)],
            geometry_rectangles,
            stats,
            method,
            edits,
            cache,
        )
    if len(corrected["staff"]) != len(original["staff"]):
        _deny()
    for index, p in enumerate(corrected["staff"], 1):
        if p.get("presence") == "ABSENT" and _known_original_person(
            original, "staff", index
        ):
            _deny()
        prefix = f"staff.{index}."
        _verify_manual_person(
            snapshot,
            source_pages,
            profile,
            p,
            original["staff"][index - 1],
            prefix,
            rectangles[(method, prefix + "presence")],
            geometry_rectangles,
            stats,
            method,
            edits,
            cache,
        )
    snapshot.verify_unchanged()
    return corrected, strict_loads(canonical(evidence))


def _verify_manual_person(
    snapshot,
    pages,
    profile,
    person,
    before,
    prefix,
    card,
    rectangles,
    stats,
    method,
    edits,
    cache,
):
    if person.get("presence") not in ("PRESENT", "ABSENT"):
        _deny()
    if person["presence"] == "ABSENT" and any(
        person.get(k) not in (None, "")
        for k in ("first_name", "last_name", "birth_date", "curp", "photo")
    ):
        _deny()
    if person["presence"] == "PRESENT" and any(
        not normalize_observation(person.get(k), field=prefix + k)
        for k in ("first_name", "last_name", "birth_date")
    ):
        _deny()
    _person_geometry(person, prefix, card, rectangles, method)
    if prefix + "photo" not in edits and not (
        prefix + "presence" in edits and person["presence"] == "ABSENT"
    ):
        _person_photo(
            snapshot, pages, profile, person, prefix, rectangles, stats, method, cache
        )
    if (
        person["presence"] == "ABSENT"
        and prefix + "presence" not in edits
        and not all(
            _blank(stats[(m, prefix + "presence")], profile["blank_profile"])
            for m in (v["method"] for v in profile["observation_views"])
        )
    ):
        _deny()


def verify_manual_observation_inputs(
    snapshot, source_pages, profile, original, *, records, principal_id
):
    """Bound malformed private inputs to the same closed review denial."""
    try:
        return _verify_manual_observation_inputs(
            snapshot,
            source_pages,
            profile,
            original,
            records=records,
            principal_id=principal_id,
        )
    except BatchDenied:
        raise
    except Exception:
        _deny()
