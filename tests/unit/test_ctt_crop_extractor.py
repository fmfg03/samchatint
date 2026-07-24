import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from devnous.tournaments.core.ctt_crop_extractor import (
    build_ctt_crop_plan,
    extract_ctt_with_providers,
    mark_duplicate_player_identities,
    validate_ctt_crop,
)
from devnous.tournaments.core.ctt_ocr_provider import (
    CttOcrCoordinator,
    CttOcrMode,
    CttProviderFieldCache,
    CttProviderRawField,
)


ROOT = Path(__file__).resolve().parents[2]


class _Provider:
    pipeline_version = "test.v1"
    revision_pinned = True

    def __init__(self, name, values):
        self.name = name
        self.model_revision = f"{name}-r1"
        self.values = values
        self.calls = []

    async def transcribe(self, crops):
        self.calls.append(tuple(crop.crop_id for crop in crops))
        return {
            crop.crop_id: CttProviderRawField(
                crop_id=crop.crop_id,
                crop_sha256=crop.crop_sha256,
                raw_text=self.values.get((crop.slot, crop.field_name)),
                candidates=[],
            )
            for crop in crops
        }


def _layout():
    return json.loads((ROOT / "config" / "layout_ctt_2026.json").read_text())


def _pages():
    # Canonical-size inputs avoid testing perspective correction here.
    return [
        Image.new("RGB", (2550, 3300), "white"),
        Image.new("RGB", (2550, 3300), "white"),
    ]


def test_crop_plan_binds_header_and_all_twenty_slots():
    plan = build_ctt_crop_plan(_pages(), _layout())

    player_crops = [crop for crop in plan.crops if crop.slot is not None]
    assert len(player_crops) == 20 * 4
    assert {crop.slot for crop in player_crops} == set(range(1, 21))
    assert all(
        crop.source_page == (1 if crop.slot <= 8 else 2) for crop in player_crops
    )
    assert all(
        hashlib.sha256(crop.image_bytes).hexdigest() == crop.crop_sha256
        for crop in plan.crops
    )
    assert all(crop.context_image_bytes for crop in plan.crops)
    assert all(
        hashlib.sha256(crop.context_image_bytes or b"").hexdigest()
        == crop.context_sha256
        for crop in plan.crops
    )
    for slot in range(1, 21):
        slot_crops = [crop for crop in player_crops if crop.slot == slot]
        assert len({crop.context_sha256 for crop in slot_crops}) == 1


@pytest.mark.asyncio
async def test_projection_materializes_sixteen_not_empty_slots(tmp_path):
    values = {(None, "team_name"): "Deportivo Estrellas"}
    for slot in range(1, 17):
        values[(slot, "given_names")] = f"Nombre {slot}".replace(" 1", " Uno")
        values[(slot, "surnames")] = "Rodriguez Linares"
        values[(slot, "birth_date")] = "28/10/2004"
        values[(slot, "curp")] = None
    # Header keys are addressed by crop field with slot=None.
    openai = _Provider("openai", values)
    chandra = _Provider("chandra", values)
    coordinator = CttOcrCoordinator(
        openai=openai,
        chandra=chandra,
        cache=CttProviderFieldCache(tmp_path),
        validator=validate_ctt_crop,
    )

    extraction, raw = await extract_ctt_with_providers(
        _pages(), _layout(), coordinator, mode=CttOcrMode.CHANDRA_PRIMARY
    )

    assert extraction["team"]["name"] == "Deportivo Estrellas"
    assert len(extraction["players"]) == 16
    assert [
        player["visible_player_number"] for player in extraction["players"]
    ] == list(range(1, 17))
    assert not any(
        player["visible_player_number"] in {17, 18, 19, 20}
        for player in extraction["players"]
    )
    assert all(player["photo_region"] is None for player in extraction["players"])
    assert raw["mode"] == "chandra_primary"
    assert len(raw["canonical_hash"]) == 64


def test_invalid_date_is_not_accepted_from_provider():
    plan = build_ctt_crop_plan(_pages(), _layout())
    crop = next(
        item
        for item in plan.crops
        if item.slot == 4 and item.field_name == "birth_date"
    )
    decision = validate_ctt_crop(
        crop,
        CttProviderRawField(
            crop_id=crop.crop_id,
            crop_sha256=crop.crop_sha256,
            raw_text="38/19/2099",
            candidates=[],
        ),
    )
    assert decision.accepted is False
    assert "INVALID_BIRTH_DATE" in decision.validation_codes


def test_six_digit_ctt_birth_date_is_canonicalized():
    plan = build_ctt_crop_plan(_pages(), _layout())
    crop = next(
        item
        for item in plan.crops
        if item.slot == 4 and item.field_name == "birth_date"
    )
    decision = validate_ctt_crop(
        crop,
        CttProviderRawField(
            crop_id=crop.crop_id,
            crop_sha256=crop.crop_sha256,
            raw_text="08/10/04",
            candidates=[],
        ),
    )

    assert decision.accepted is True
    assert decision.normalized_value == "08/10/2004"


def test_duplicate_name_and_birth_marks_both_slots_for_review():
    players = [
        {
            "visible_player_number": 1,
            "name": "Axel Antonio Soto Ramírez",
            "birth_date": "18/08/2011",
            "curp": None,
            "confidence": 1.0,
            "needs_review": False,
        },
        {
            "visible_player_number": 6,
            "name": "AXEL ANTONIO SOTO RAMIREZ",
            "birth_date": "18/08/2011",
            "curp": None,
            "confidence": 1.0,
            "needs_review": False,
        },
    ]

    groups = mark_duplicate_player_identities(players)

    assert groups == [[1, 6]]
    assert all(player["needs_review"] for player in players)
    assert all(player["confidence"] == 0.0 for player in players)
    assert all(
        player["validation_codes"] == ["DUPLICATE_PLAYER_IDENTITY"]
        for player in players
    )


def test_similar_name_and_same_birth_marks_possible_duplicate():
    players = [
        {
            "visible_player_number": 1,
            "name": "Axel Antonio Soto Ramirez",
            "birth_date": "18/08/2011",
            "curp": "FIRST-OCR",
            "confidence": 1.0,
            "needs_review": False,
        },
        {
            "visible_player_number": 6,
            "name": "Axel Antonio Soto Ruviera",
            "birth_date": "18/08/2011",
            "curp": "SECOND-OCR",
            "confidence": 1.0,
            "needs_review": False,
        },
    ]

    assert mark_duplicate_player_identities(players) == [[1, 6]]
    assert all(
        player["validation_codes"] == ["POSSIBLE_DUPLICATE_PLAYER_IDENTITY"]
        for player in players
    )
