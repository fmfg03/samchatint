import json
from pathlib import Path

import pytest
from PIL import Image

from devnous.tournaments.core.ctt_slot_montage import (
    CttSlotCrop,
    build_ctt_slot_batches,
    build_slot_montage,
    extract_ctt_player_slots,
    extract_slots_from_normalized_page,
    player_card_box,
)
from devnous.tournaments.core.ocr_integrity import normalize_ctt_template_image

ROOT = Path(__file__).resolve().parents[2]


def _field(x: float, y: float, width: float, height: float) -> dict:
    return {"x": x, "y": y, "w": width, "h": height}


def _slot(slot: int, page: int = 1) -> CttSlotCrop:
    image = Image.new("RGB", (1000, 380), (slot, page, 240))
    return CttSlotCrop(
        page=page,
        slot=slot,
        box=(0, 0, image.width, image.height),
        image=image,
    )


def test_player_card_box_includes_photo_context_left_of_fields() -> None:
    fields = {
        "nombre": _field(0.24, 0.43, 0.25, 0.03),
        "apellidos": _field(0.24, 0.46, 0.25, 0.03),
        "nacimiento": _field(0.39, 0.49, 0.10, 0.03),
        "curp": _field(0.22, 0.52, 0.27, 0.03),
    }

    box = player_card_box(fields, (2550, 3300))

    assert box[0] < int(0.10 * 2550)
    assert box[1] < int(0.43 * 3300)
    assert box[2] > int(0.48 * 2550)
    assert box[3] > int(0.54 * 3300)


def test_real_layout_maps_slots_to_their_source_page() -> None:
    layout = json.loads((ROOT / "config" / "layout_ctt_2026.json").read_text())
    page = Image.new("RGB", (2550, 3300), "white")

    front = extract_slots_from_normalized_page(
        page,
        page=1,
        page_layout=layout["pages"]["front"],
    )
    back = extract_slots_from_normalized_page(
        page,
        page=2,
        page_layout=layout["pages"]["back"],
    )

    assert [(slot.page, slot.slot) for slot in front] == [
        (1, slot) for slot in range(1, 9)
    ]
    assert [(slot.page, slot.slot) for slot in back] == [
        (2, slot) for slot in range(9, 21)
    ]


def test_page_layout_can_calibrate_slot_vertical_offset() -> None:
    page = Image.new("RGB", (1000, 1000), "white")
    fields = {"nombre": _field(0.30, 0.40, 0.20, 0.10)}
    base_layout = {"cards": {"jugador_1": fields}}
    shifted_layout = {
        "slot_crop": {"vertical_offset_ratio": -0.04},
        "cards": {"jugador_1": fields},
    }

    base = extract_slots_from_normalized_page(
        page,
        page=1,
        page_layout=base_layout,
    )[0]
    shifted = extract_slots_from_normalized_page(
        page,
        page=1,
        page_layout=shifted_layout,
    )[0]

    assert shifted.box[1] == base.box[1] - 40
    assert shifted.box[3] == base.box[3] - 40


def test_batches_contain_at_most_four_complete_slots_without_tall_strip() -> None:
    slots = [_slot(slot, 1 if slot <= 8 else 2) for slot in range(1, 21)]

    batches = build_ctt_slot_batches(reversed(slots))

    assert [len(batch.slots) for batch in batches] == [4, 4, 4, 4, 4]
    assert [slot.slot for batch in batches for slot in batch.slots] == list(
        range(1, 21)
    )
    for batch in batches:
        assert batch.montage.width >= 2200
        assert batch.montage.height < batch.montage.width


def test_batch_size_cannot_recreate_the_megamontage() -> None:
    with pytest.raises(ValueError, match="between 1 and 4"):
        build_ctt_slot_batches([_slot(1)], max_slots=5)


def test_invalid_or_empty_card_layout_fails_closed() -> None:
    page = Image.new("RGB", (1000, 1000), "white")

    with pytest.raises(ValueError, match="Invalid CTT player card name"):
        extract_slots_from_normalized_page(
            page,
            page=1,
            page_layout={
                "cards": {"jugador_bad": {"nombre": _field(0.2, 0.2, 0.2, 0.1)}}
            },
        )
    with pytest.raises(ValueError, match="no field coordinates"):
        extract_slots_from_normalized_page(
            page,
            page=1,
            page_layout={"cards": {"jugador_1": {}}},
        )


def test_top_level_extraction_normalizes_optional_back_copy() -> None:
    layout = {
        "pages": {
            "front": {"cards": {"jugador_1": {"nombre": _field(0.2, 0.2, 0.2, 0.1)}}},
            "back": {"cards": {"jugador_9": {"nombre": _field(0.2, 0.2, 0.2, 0.1)}}},
        }
    }
    pages = [Image.new("RGB", (100, 200), "white") for _ in range(3)]

    slots = extract_ctt_player_slots(pages, layout)

    assert [(slot.page, slot.slot) for slot in slots] == [
        (1, 1),
        (2, 9),
        (3, 9),
    ]
    assert all(slot.image.width > 1 for slot in slots)


def test_remapped_crop_preserves_physical_coordinates() -> None:
    source = _slot(9, page=2)
    remapped = CttSlotCrop(
        page=3,
        slot=21,
        box=source.box,
        image=source.image,
        source_page=source.page,
        source_slot=source.slot,
    )

    assert remapped.physical_page == 2
    assert remapped.physical_slot == 9


def test_montage_rejects_empty_or_invalid_grid() -> None:
    with pytest.raises(ValueError, match="without slots"):
        build_slot_montage([])
    with pytest.raises(ValueError, match="at least 1"):
        build_slot_montage([_slot(1)], columns=0)


def test_normalization_outputs_canonical_portrait_page() -> None:
    image = Image.new("RGB", (1280, 960), "white")

    normalized, metadata = normalize_ctt_template_image(image)

    assert normalized.size == (2550, 3300)
    assert metadata["method"] == "resize_only"


def test_normalization_is_idempotent_for_canonical_page() -> None:
    image = Image.new("RGB", (2550, 3300), "white")
    image.putpixel((120, 180), (12, 34, 56))

    normalized, metadata = normalize_ctt_template_image(image)

    assert normalized.size == image.size
    assert normalized.getpixel((120, 180)) == (12, 34, 56)
    assert metadata["method"] == "already_canonical"
