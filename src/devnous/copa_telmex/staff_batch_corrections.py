"""Typed pending interpretations of exact admitted cards; never human authority."""

from __future__ import annotations

import re
from copy import deepcopy
from types import SimpleNamespace
from typing import NoReturn

from .staff_batch_contracts import digest
from .staff_batch_store import BatchDenied
from .staff_batch_verification import normalize_observation

FIELD = re.compile(
    r"^(players|staff)\.([1-9][0-9]*)\."
    r"(first_name|last_name|birth_date|curp|photo|presence)$"
)
BOX_KEYS = {"page_index", "x", "y", "width", "height"}
IDENTITY = {"first_name", "last_name", "birth_date", "curp"}


def _deny() -> NoReturn:
    raise BatchDenied("REVIEW_BLOCKED")


def field_parts(field_id) -> tuple[str, str, str]:
    match = FIELD.fullmatch(field_id) if type(field_id) is str else None
    if match is None:
        _deny()
    return match[1], match[2], match[3]


def validate_rectangle(value):
    """Only integer original-pixel rectangles; page/card binding is private."""
    if (
        type(value) is not dict
        or set(value) != BOX_KEYS
        or any(type(v) is not int for v in value.values())
        or not 1 <= value["page_index"] <= 4
        or min(value["x"], value["y"]) < 0
        or min(value["width"], value["height"]) < 1
    ):
        _deny()
    return deepcopy(value)


def validate_edit_value(field_id, value):
    """Validate the same closed value union at HTTP, workflow and replay."""
    match = FIELD.fullmatch(field_id) if type(field_id) is str else None
    if match is None:
        _deny()
    name = match[3]
    if name in IDENTITY:
        if type(value) is dict:
            if (
                set(value) != {"kind", "value", "rectangle"}
                or value["kind"] != "source_field"
            ):
                _deny()
            validate_rectangle(value["rectangle"])
            text = value["value"]
        else:
            text = value
        if text is not None and (
            type(text) is not str or len(text) > 500 or any(ord(c) < 32 for c in text)
        ):
            _deny()
    elif name == "photo":
        if (
            type(value) is not dict
            or set(value) != {"kind", "presence", "rectangle"}
            or value["kind"] != "photo"
            or value["presence"] not in ("PRESENT", "ABSENT")
        ):
            _deny()
        validate_rectangle(value["rectangle"])
    else:
        if type(value) is dict:
            if set(value) != {"kind", "presence"} or value["kind"] != "presence":
                _deny()
            value = value["presence"]
        if value not in ("PRESENT", "ABSENT"):
            _deny()
    return deepcopy(value)


def _profile_rectangles(profile, source_pages):
    from .staff_batch_observation_inputs import _pixel_box

    images = {
        p["page_index"]: SimpleNamespace(width=p["width"], height=p["height"])
        for p in source_pages
    }
    return {
        field: _pixel_box(spec, images)
        for field, spec in profile["observation_views"][0]["fields"].items()
    }


def _box(rect):
    page, (left, top, right, bottom) = rect
    return dict(page_index=page, x=left, y=top, width=right - left, height=bottom - top)


def _tuple(box):
    return box["page_index"], (
        box["x"],
        box["y"],
        box["x"] + box["width"],
        box["y"] + box["height"],
    )


def _card_id(kind, index, person, profile):
    if kind == "staff":
        return f"staff.{index}.presence"
    matches = [
        s
        for s in profile["physical_slots"]
        if s.get("person_index") == index and s["slot_ref"] == person.get("slot_ref")
    ]
    if len(matches) != 1:
        _deny()
    return matches[0]["presence_field"]


def editable_fields(analysis, *, profile=None, source_pages=None):
    """Current values and fixed source/card bounds, with no caller-chosen slots."""
    expected = analysis.get("verification_input", {}).get("expected_fields", [])
    rectangles = (
        _profile_rectangles(profile, source_pages) if profile and source_pages else {}
    )
    fields = []
    for kind in ("players", "staff"):
        for index, person in enumerate(analysis.get(kind, []), 1):
            prefix = f"{kind}.{index}."
            card = (
                rectangles.get(_card_id(kind, index, person, profile))
                if rectangles
                else None
            )
            for name in (
                "first_name",
                "last_name",
                "birth_date",
                "curp",
                "photo",
                "presence",
            ):
                field_id = prefix + name
                semantic = prefix + ("photo_sha256" if name == "photo" else name)
                geometry_name = (
                    "full_name" if name in ("first_name", "last_name") else name
                )
                rect = rectangles.get(semantic)
                if semantic not in expected or (
                    not rectangles
                    and (
                        name not in IDENTITY
                        or person.get("presence") != "PRESENT"
                        or not person.get("boxes", {}).get(geometry_name)
                    )
                ):
                    continue
                if rectangles and (card is None or rect is None):
                    continue
                value = person.get(name)
                label = {
                    "first_name": "Nombre",
                    "last_name": "Apellidos",
                    "birth_date": "Fecha de nacimiento",
                    "curp": "CURP",
                    "photo": "Fotografía",
                    "presence": "Presencia",
                }[name]
                person_label = (
                    f"Jugador {index}"
                    if kind == "players"
                    else "Director técnico" if index == 1 else "Auxiliar"
                )
                if name == "photo":
                    value = "PRESENT" if person.get("photo") else "ABSENT"
                readings = analysis.get("verification_input", {}).get("readings", [])
                known_photo = (
                    bool(person.get("photo"))
                    or person.get("photo_evidence") is not None
                    or any(
                        r.get("fields", {}).get(prefix + "photo_sha256")
                        for r in readings
                    )
                )
                known_identity = (
                    known_photo
                    or any(person.get(k) for k in IDENTITY)
                    or any(
                        r.get("fields", {}).get(prefix + k)
                        for r in readings
                        for k in IDENTITY
                    )
                )
                fields.append(
                    {
                        "field_id": field_id,
                        "label": f"{person_label} · {label}",
                        "value": value,
                        "editable": True,
                        "source_page_index": person["source_page_index"],
                        **(
                            {
                                "kind": "source_field" if name in IDENTITY else name,
                                "rectangle": _box(rect),
                                "card_bounds": _box(card),
                                "allow_absent": (
                                    not known_photo
                                    if name == "photo"
                                    else (
                                        not known_identity
                                        if name == "presence"
                                        else False
                                    )
                                ),
                            }
                            if rectangles
                            else {}
                        ),
                    }
                )
    return fields


def edit_overrides(records, proposed):
    """Map explicit pending records to exact semantic fields, not other claims."""
    overrides = {}
    for record in records:
        for edit in record["edits"]:
            field, value = edit["field_id"], edit["value"]
            validate_edit_value(field, value)
            if field.endswith(".photo"):
                semantic = field.removesuffix("photo") + "photo_sha256"
                if semantic not in proposed:
                    _deny()
                overrides[semantic] = proposed[semantic]
            else:
                overrides[field] = (
                    value.get("value")
                    if type(value) is dict and value.get("kind") == "source_field"
                    else value.get("presence") if type(value) is dict else value
                )
    return overrides


def apply_proposals(
    analysis, *, records, principal_id, source_pages, profile=None, snapshot=None
):
    """Apply ledger-bound proposals; full source replay is still mandatory."""
    if not records:
        return analysis
    result = deepcopy(analysis)
    allowed = {
        f["field_id"]: f
        for f in editable_fields(result, profile=profile, source_pages=source_pages)
    }
    evidence = result.get("verification_input", {})
    readings, expected, proposed = (
        evidence.get("readings", []),
        evidence.get("expected_fields", []),
        evidence.get("proposed_fields", {}),
    )
    if (
        len(readings) != 2
        or not expected
        or set(proposed) != set(expected)
        or readings[0].get("input_hash") == readings[1].get("input_hash")
    ):
        _deny()
    edited_rectangles = {}
    for record in records:
        if (
            record.get("principal_id") != principal_id
            or record.get("source_pages") != source_pages
        ):
            _deny()
        for edit in record.get("edits", []):
            if type(edit) is not dict or set(edit) != {"field_id", "value"}:
                _deny()
            field, value = edit["field_id"], edit["value"]
            validate_edit_value(field, value)
            if field not in allowed:
                _deny()
            kind, position, name = field_parts(field)
            person = result[kind][int(position) - 1]
            if name in IDENTITY:
                text = value["value"] if type(value) is dict else value
                person[name] = text
                proposed[field] = text
                if type(value) is dict:
                    if not profile or snapshot is None:
                        _deny()
                    from .staff_batch_observation_inputs import _contains

                    if not _contains(
                        _tuple(allowed[field]["card_bounds"]),
                        _tuple(value["rectangle"]),
                    ):
                        _deny()
                    edited_rectangles[field] = value["rectangle"]
            else:
                if not profile or snapshot is None:
                    _deny()
                from .staff_batch_observation_inputs import (
                    apply_manual_presence_or_photo,
                )

                apply_manual_presence_or_photo(
                    result, analysis, field, value, snapshot, source_pages, profile
                )
    if edited_rectangles:
        rectangles = _profile_rectangles(profile, source_pages)
        rectangles.update({k: _tuple(v) for k, v in edited_rectangles.items()})
        for kind in ("players", "staff"):
            for index, person in enumerate(result[kind], 1):
                prefix = f"{kind}.{index}."
                for name in ("birth_date", "curp"):
                    if prefix + name in edited_rectangles:
                        person["boxes"][name] = edited_rectangles[prefix + name]
                if any(
                    prefix + n in edited_rectangles for n in ("first_name", "last_name")
                ):
                    first, last = (
                        rectangles[prefix + "first_name"],
                        rectangles[prefix + "last_name"],
                    )
                    person["boxes"]["full_name"] = _box(
                        (
                            first[0],
                            (
                                min(first[1][0], last[1][0]),
                                min(first[1][1], last[1][1]),
                                max(first[1][2], last[1][2]),
                                max(first[1][3], last[1][3]),
                            ),
                        )
                    )
    overrides = edit_overrides(records, proposed)
    # Presence also binds the already admitted printed slot claim.
    for record in records:
        for edit in record["edits"]:
            if edit["field_id"].startswith("players.") and edit["field_id"].endswith(
                ".presence"
            ):
                _, position, _ = field_parts(edit["field_id"])
                person = result["players"][int(position) - 1]
                overrides[_card_id("players", int(position), person, profile)] = person[
                    "presence"
                ]
    for field in expected:
        actual = normalize_observation(proposed[field], field=field)
        if field not in overrides:
            values = [
                normalize_observation(r["fields"].get(field), field=field)
                for r in readings
            ]
            if values[0] != values[1] or actual != values[0]:
                _deny()
        if field in evidence.get("required_fields", []) and actual is None:
            _deny()
    if not _resolved_issues(
        result.get("issues", []), overrides, records, snapshot is not None
    ):
        _deny()
    result.update(
        auto_ready=True,
        issues=[],
        human_edits=deepcopy(records),
        human_edits_hash=digest(records),
    )
    return result


def _resolved_issues(issues, overrides, records, source_replayed):
    edited = {e["field_id"]: e["value"] for r in records for e in r["edits"]}
    presence_edits = [field for field in edited if field.endswith(".presence")]
    for issue in issues:
        code = issue.get("code") if type(issue) is dict else issue
        if type(code) is not str:
            return False
        if code in {
            "READER_DISAGREEMENT",
            "FIELD_MISSING",
            "FIELD_DISAGREEMENT",
            "READINGS_DISAGREE",
        }:
            continue  # Every unedited value has already passed dual readings.
        if source_replayed and code in {
            "OBSERVATION_INPUTS_INVALID",
            "BATCH_PLAN_REJECTED",
            "SOURCE_OBSERVATION_REPLAY_FAILED",
        }:
            continue  # The complete private replay/plan is still required.
        kind, sep, field = code.partition(":")
        if (
            sep
            and kind
            in {
                "INVALID_FIELD",
                "AMBIGUOUS_FIELD",
                "MISSING_FIELD",
                "PRESENCE_UNCERTAIN",
            }
            and field in overrides
        ):
            continue
        if source_replayed and sep and kind in {"PHOTO_UNCERTAIN", "PHOTO_REQUIRED"}:
            value = edited.get(field + "photo")
            if type(value) is dict and (
                kind != "PHOTO_REQUIRED" or value["presence"] == "PRESENT"
            ):
                continue
        if source_replayed and sep and kind == "ABSENT_WITH_IDENTITY":
            prefix = field.rsplit(".", 1)[0] + ".presence"
            if overrides.get(prefix) == "PRESENT":
                continue
        if (
            source_replayed
            and code == "STAFF_REQUIRED"
            and any(
                f.startswith("staff.") and overrides.get(f) == "PRESENT"
                for f in presence_edits
            )
        ):
            continue
        if (
            source_replayed
            and code == "ROSTER_SIZE_INVALID"
            and any(f.startswith("players.") for f in presence_edits)
        ):
            continue
        if (
            source_replayed
            and code in {"PHOTO_UNCERTAIN", "PHOTO_MISSING", "PHOTO_GEOMETRY"}
            and any(f.endswith(".photo") for f in edited)
        ):
            continue
        if (
            source_replayed
            and code == "GEOMETRY_UNCERTAIN"
            and any(
                type(v) is dict and v.get("kind") == "source_field"
                for v in edited.values()
            )
        ):
            continue
        if source_replayed and code == "PRESENCE_UNCERTAIN" and presence_edits:
            continue
        return False
    return True
