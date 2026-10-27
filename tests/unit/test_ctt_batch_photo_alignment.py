"""Synthetic geometry and identity boundaries for admitted CTT batch photos."""

import cv2
import numpy as np
from PIL import Image, ImageDraw

from devnous.tournaments.core.ctt_batch_photo_alignment import (
    _face_geometry_is_safe,
    extract_batch_photo,
    parse_batch_slot,
)


def _page(*, back=False, scale=1):
    width, height = 612 * scale, 792 * scale
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    top = (115 if back else 238) * scale
    step = 97 * scale
    rows = 6 if back else 5
    # Cell boundaries and the name column deliberately cross both halves.
    for x in (46, 122, 311, 385, 565):
        draw.line(
            (x * scale, top, x * scale, top + rows * step),
            fill="black",
            width=2 * scale,
        )
    for r in range(rows + 1):
        y = top + r * step
        draw.line((46 * scale, y, 565 * scale, y), fill="black", width=2 * scale)
    # Simulated photos use distinct uniform colors; text strokes alone must not
    # count as a photo. Recognition is injected in integration tests below.
    draw.rectangle(
        (51 * scale, top + step + 5 * scale, 115 * scale, top + 2 * step - 5 * scale),
        fill=(50, 50, 50),
    )
    return image


def test_slot_reference_uses_physical_page_and_printed_slot():
    assert parse_batch_slot("page:5:jugador_3", [5, 6]) == (1, 3)
    assert parse_batch_slot("page:6:jugador_9", [5, 6]) == (2, 9)
    assert parse_batch_slot("page:5:jugador_9", [5, 6]) is None
    assert parse_batch_slot("page:6:jugador_21", [5, 6]) is None
    assert parse_batch_slot("page:7:jugador_1", [5, 6]) is None
    assert parse_batch_slot("page:5:jugador_1", [5.0, 6]) is None


def test_crop_tracks_letter_grid_at_multiple_scales(monkeypatch):
    monkeypatch.setattr(
        "devnous.tournaments.core.ctt_batch_photo_alignment._face_present",
        lambda _image: True,
    )
    for scale in (1, 2):
        crop, status = extract_batch_photo(_page(scale=scale), page_index=1, slot=1)
        assert status == "photo" and crop is not None
        assert crop.width > 55 * scale and crop.height > 70 * scale
        assert crop.getpixel((crop.width // 2, crop.height // 2))[0] < 80


def test_incomplete_grid_or_blank_cell_never_returns_a_photo(monkeypatch):
    monkeypatch.setattr(
        "devnous.tournaments.core.ctt_batch_photo_alignment._face_present",
        lambda _image: True,
    )
    crop, status = extract_batch_photo(
        Image.new("RGB", (612, 792), "white"), page_index=1, slot=1
    )
    assert crop is None and status == "uncertain"
    crop, status = extract_batch_photo(_page(), page_index=1, slot=2)
    assert crop is None and status in {"missing", "uncertain"}


def test_contrast_or_text_without_face_is_not_a_photo(monkeypatch):
    monkeypatch.setattr(
        "devnous.tournaments.core.ctt_batch_photo_alignment._face_present",
        lambda _image: False,
    )
    crop, status = extract_batch_photo(_page(), page_index=1, slot=1)
    assert crop is None and status == "uncertain"


def test_partial_or_neighbor_faces_are_not_accepted():
    assert _face_geometry_is_safe([(40, 80, 230, 190)], 300, 400)
    assert not _face_geometry_is_safe([(40, 210, 230, 185)], 300, 400)
    assert not _face_geometry_is_safe([(40, 80, 230, 320)], 300, 400)
    assert not _face_geometry_is_safe([(40, 80, 230, 190), (2, 2, 50, 50)], 300, 400)


def test_letter_grid_handles_mild_perspective_without_switching_slots(monkeypatch):
    monkeypatch.setattr(
        "devnous.tournaments.core.ctt_batch_photo_alignment._face_present",
        lambda _image: True,
    )
    source = np.asarray(_page())
    height, width = source.shape[:2]
    corners = np.float32(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]]
    )
    shifted = np.float32(
        [[7, 0], [width - 8, 7], [width - 1, height - 8], [0, height - 1]]
    )
    warped = cv2.warpPerspective(
        source,
        cv2.getPerspectiveTransform(corners, shifted),
        (width, height),
        borderValue=(255, 255, 255),
    )
    crop, status = extract_batch_photo(Image.fromarray(warped), page_index=1, slot=1)
    assert status == "photo" and crop is not None
    assert crop.getpixel((crop.width // 2, crop.height // 2))[0] < 80
