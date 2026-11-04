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


def test_card_covers_each_physical_slot_and_staff_at_original_scale():
    from devnous.tournaments.core.ctt_batch_photo_alignment import (
        prepare_batch_cards,
        resolve_batch_card,
    )

    for scale in (1, 2):
        for page_index in (1, 2):
            image = _page(back=page_index == 2, scale=scale)
            prepared = prepare_batch_cards(image, page_index)
            assert prepared is not None
            slots = range(1, 9) if page_index == 1 else range(9, 21)
            for slot in slots:
                card = resolve_batch_card(
                    image,
                    page_index=page_index,
                    role="player",
                    slot=slot,
                    prepared=prepared,
                )
                assert card is not None
                col = (slot - (1 if page_index == 1 else 9)) % 2
                row = (slot - 1) // 2 + 1 if page_index == 1 else (slot - 9) // 2
                top = (238 if page_index == 1 else 115) * scale
                assert card["y"] >= top + row * 97 * scale
                assert (
                    card["y"] + card["height"]
                    <= top + (row + 1) * 97 * scale + 2 * scale
                )
                lo, hi = (46, 311) if col == 0 else (311, 565)
                assert (
                    lo * scale
                    <= card["x"]
                    < card["x"] + card["width"]
                    <= (hi + 2) * scale
                )
                assert card["width"] * card["height"] >= 12000
                assert all(type(v) is int for v in card.values())
            if page_index == 1:
                for slot, role in ((1, "director_tecnico"), (2, "auxiliar")):
                    card = resolve_batch_card(
                        image, page_index=1, role=role, slot=slot, prepared=prepared
                    )
                    assert card and card["y"] < 335 * scale


def test_card_rejects_wrong_page_role_slot_orientation_and_size():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    image = _page()
    cases = [
        (True, "player", 1),
        (1, "player", True),
        (1, "player", 9),
        (2, "player", 1),
        (2, "director_tecnico", 1),
        (1, "auxiliar", 1),
        (1, "director_tecnico", 2),
        (1, "unknown", 1),
        (3, "player", 1),
    ]
    for page, role, slot in cases:
        assert resolve_batch_card(image, page_index=page, role=role, slot=slot) is None
    image.getexif()[274] = 6
    assert resolve_batch_card(image, page_index=1, role="player", slot=1) is None
    small = _page().resize((306, 396))
    assert resolve_batch_card(small, page_index=1, role="player", slot=1) is None
    assert (
        resolve_batch_card(
            Image.new("RGB", (612, 792), "white"), page_index=1, role="player", slot=1
        )
        is None
    )


def test_card_requires_all_four_local_borders_and_does_not_fill_missing_rail():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    for rectangle in (
        (560, 336, 570, 430),
        (300, 427, 570, 433),
        (300, 332, 570, 338),
        (307, 340, 316, 420),
    ):
        image = _page()
        ImageDraw.Draw(image).rectangle(rectangle, fill="white")
        assert resolve_batch_card(image, page_index=1, role="player", slot=2) is None
    image = _page()
    ImageDraw.Draw(image).rectangle((560, 0, 570, 792), fill="white")
    assert resolve_batch_card(image, page_index=1, role="player", slot=1)
    assert resolve_batch_card(image, page_index=1, role="player", slot=2) is None


def test_card_geometry_is_independent_of_photo_content_and_preserves_photo(monkeypatch):
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    monkeypatch.setattr(
        "devnous.tournaments.core.ctt_batch_photo_alignment._face_present",
        lambda _image: True,
    )
    image = _page()
    before, status = extract_batch_photo(image, page_index=1, slot=1)
    assert status == "photo"
    assert resolve_batch_card(image, page_index=1, role="player", slot=1)
    assert resolve_batch_card(image, page_index=1, role="player", slot=2)
    after, after_status = extract_batch_photo(image, page_index=1, slot=1)
    assert after_status == status and before.tobytes() == after.tobytes()


def test_card_prepared_page_is_bound_to_exact_pixels_and_page():
    from devnous.tournaments.core.ctt_batch_photo_alignment import (
        prepare_batch_cards,
        resolve_batch_card,
    )

    image = _page()
    prepared = prepare_batch_cards(image, 1)
    assert resolve_batch_card(
        image, page_index=1, role="player", slot=3, prepared=prepared
    )
    altered = image.copy()
    altered.putpixel((0, 0), (0, 0, 0))
    assert (
        resolve_batch_card(
            altered, page_index=1, role="player", slot=3, prepared=prepared
        )
        is None
    )
    assert (
        resolve_batch_card(
            image, page_index=2, role="player", slot=9, prepared=prepared
        )
        is None
    )


def test_card_does_not_guess_a_strong_perspective_transform():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    source = np.asarray(_page())
    height, width = source.shape[:2]
    corners = np.float32(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]]
    )
    shifted = np.float32(
        [[40, 0], [width - 50, 40], [width - 1, height - 30], [0, height - 1]]
    )
    warped = cv2.warpPerspective(
        source,
        cv2.getPerspectiveTransform(corners, shifted),
        (width, height),
        borderValue=(255, 255, 255),
    )
    assert (
        resolve_batch_card(Image.fromarray(warped), page_index=1, role="player", slot=2)
        is None
    )


def test_card_tracks_translation_without_reindexing_slots():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    image = _page().transform(
        (612, 792), Image.Transform.AFFINE, (1, 0, -8, 0, 1, -7), fillcolor="white"
    )
    left = resolve_batch_card(image, page_index=1, role="player", slot=1)
    right = resolve_batch_card(image, page_index=1, role="player", slot=2)
    assert left and right
    assert left["x"] >= 54 and left["x"] + left["width"] <= 321
    assert left["y"] >= 342 and left["y"] + left["height"] <= 441
    assert left["x"] + left["width"] < right["x"]


def test_card_safe_rectangle_stays_inside_mildly_sloping_source():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    matrix = np.float32([[1, 0.003, 0], [0.003, 1, 0]])
    warped = cv2.warpAffine(
        np.asarray(_page()), matrix, (612, 792), borderValue=(255, 255, 255)
    )
    card = resolve_batch_card(
        Image.fromarray(warped), page_index=1, role="player", slot=1
    )
    assert card
    inverse = cv2.invertAffineTransform(matrix)
    for x in (card["x"], card["x"] + card["width"]):
        for y in (card["y"], card["y"] + card["height"]):
            original = inverse @ np.array([x, y, 1])
            assert 46 <= original[0] <= 313
            assert 335 <= original[1] <= 434


def test_card_rejects_half_turn_on_both_sides():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    for page in (1, 2):
        assert (
            resolve_batch_card(
                _page(back=page == 2).rotate(180),
                page_index=page,
                role="player",
                slot=1 if page == 1 else 9,
            )
            is None
        )


def test_blank_gradient_and_noise_are_not_source_contours():
    from devnous.tournaments.core.ctt_batch_photo_alignment import prepare_batch_cards

    gradient = np.tile(np.linspace(220, 255, 612, dtype=np.uint8), (792, 1))
    noise = np.random.default_rng(47).integers(
        220, 256, size=(792, 612), dtype=np.uint8
    )
    for raw in (np.full((792, 612), 255, dtype=np.uint8), gradient, noise):
        assert prepare_batch_cards(Image.fromarray(raw).convert("RGB"), 1) is None


def test_gradient_paper_does_not_supply_an_erased_border():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    image = _page()
    raw = np.asarray(image).copy()
    paper = np.tile(np.linspace(240, 255, 612, dtype=np.uint8), (792, 1))
    white = (raw == 255).all(axis=2)
    raw[white] = np.repeat(paper[:, :, None], 3, axis=2)[white]
    raw[339:426, 559:572] = np.repeat(paper[339:426, 559:572, None], 3, axis=2)
    image = Image.fromarray(raw)
    assert resolve_batch_card(image, page_index=1, role="player", slot=2) is None


def test_dark_photos_and_internal_curp_lines_cannot_replace_row_separator():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    image = _page()
    draw = ImageDraw.Draw(image)
    for row in range(5):
        y = 238 + row * 97
        for left, right in ((46, 311), (311, 565)):
            draw.rectangle((left + 5, y + 5, left + 68, y + 90), fill=(40, 40, 40))
            draw.line((left + 77, y + 80, right - 3, y + 80), fill="black", width=3)
    # A legible source marker close to the text's right/lower bounds must be
    # retained, rather than choosing the much stronger internal CURP stroke.
    marker = (294, 416, 302, 422)
    draw.rectangle(marker, fill="black")
    card = resolve_batch_card(image, page_index=1, role="player", slot=1)
    assert card
    assert card["x"] <= marker[0] and card["y"] <= marker[1]
    assert card["x"] + card["width"] > marker[2]
    assert card["y"] + card["height"] > marker[3]
    assert card["y"] + card["height"] <= 434


def test_erased_separator_on_grey_paper_never_becomes_source_ink():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    raw = np.asarray(_page()).copy()
    raw[(raw == 255).all(axis=2)] = (245, 245, 245)
    raw[426:435, 46:308] = (245, 245, 245)
    image = Image.fromarray(raw)
    assert resolve_batch_card(image, page_index=1, role="player", slot=3) is None


def test_erased_separator_on_noisy_gradient_requires_a_real_line():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    raw = np.asarray(_page()).copy()
    rng = np.random.default_rng(63)
    paper = np.tile(np.linspace(240, 250, 612, dtype=np.float32), (792, 1))
    paper += rng.integers(-1, 2, size=paper.shape)
    paper = np.clip(paper, 0, 255).astype(np.uint8)
    rgb = np.repeat(paper[:, :, None], 3, axis=2)
    raw[(raw == 255).all(axis=2)] = rgb[(raw == 255).all(axis=2)]
    raw[421:443, 46:308] = rgb[421:443, 46:308]
    image = Image.fromarray(raw)
    assert resolve_batch_card(image, page_index=1, role="player", slot=3) is None


def test_random_noise_cannot_recover_an_erased_separator_at_several_amplitudes():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    for amplitude in (2, 3, 5, 10):
        for seed in (902, 47, 197):
            raw = np.asarray(_page()).copy()
            paper = np.tile(np.linspace(242, 247, 612, dtype=np.float32), (792, 1))
            paper += np.random.default_rng(seed).integers(
                -amplitude, amplitude + 1, size=paper.shape
            )
            paper = np.clip(paper, 0, 255).astype(np.uint8)
            rgb = np.repeat(paper[:, :, None], 3, axis=2)
            raw[(raw == 255).all(axis=2)] = rgb[(raw == 255).all(axis=2)]
            raw[426:435, 46:308] = rgb[426:435, 46:308]
            assert (
                resolve_batch_card(
                    Image.fromarray(raw), page_index=1, role="player", slot=3
                )
                is None
            ), (amplitude, seed)


def test_displaced_full_width_rule_cannot_truncate_a_card():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    for offset in (-12, -9, -6, 6, 9, 12):
        image = _page()
        draw = ImageDraw.Draw(image)
        draw.rectangle((46, 426, 566, 437), fill="white")
        draw.line((46, 432 + offset, 566, 432 + offset), fill="black", width=2)
        for slot in (1, 2, 3, 4):
            assert (
                resolve_batch_card(image, page_index=1, role="player", slot=slot)
                is None
            ), (offset, slot)


def test_outer_separators_must_match_both_vertical_frame_endpoints():
    from devnous.tournaments.core.ctt_batch_photo_alignment import resolve_batch_card

    for top in (True, False):
        for offset in (-12, -9, -6, 6, 9, 12):
            image = _page()
            draw = ImageDraw.Draw(image)
            boundary = 238 if top else 723
            draw.rectangle((49, boundary - 3, 562, boundary + 4), fill="white")
            draw.line(
                (46, boundary + offset, 565, boundary + offset), fill="black", width=2
            )
            roles = (
                (("director_tecnico", 1), ("auxiliar", 2))
                if top
                else (("player", 7), ("player", 8))
            )
            for role, slot in roles:
                assert (
                    resolve_batch_card(image, page_index=1, role=role, slot=slot)
                    is None
                ), (top, offset, role, slot)


def test_outer_frame_with_erased_rail_intersections_stays_pending():
    """A relocated rule cannot become its own physical endpoint evidence."""
    from devnous.tournaments.core.ctt_batch_photo_alignment import (
        prepare_batch_cards,
        resolve_batch_card,
    )

    for back in (False, True):
        page_index = 2 if back else 1
        for top in (True, False):
            boundary = (115 if top else 697) if back else (238 if top else 723)
            for offset in (-12, -9, -6, 6, 9, 12):
                image = _page(back=back)
                draw = ImageDraw.Draw(image)
                draw.rectangle((46, boundary - 5, 566, boundary + 5), fill="white")
                draw.line(
                    (46, boundary + offset, 565, boundary + offset),
                    fill="black",
                    width=2,
                )
                prepared = prepare_batch_cards(image, page_index)
                refs = (
                    (("player", 9), ("player", 10))
                    if back and top
                    else (
                        (("player", 19), ("player", 20))
                        if back
                        else (
                            (("director_tecnico", 1), ("auxiliar", 2))
                            if top
                            else (("player", 7), ("player", 8))
                        )
                    )
                )
                for role, slot in refs:
                    assert (
                        resolve_batch_card(
                            image,
                            page_index=page_index,
                            role=role,
                            slot=slot,
                            prepared=prepared,
                        )
                        is None
                    ), (back, top, offset, role, slot)


def test_pdf_raster_antialias_endpoints_and_overlapping_footer():
    """PDF antialias shifts a ridge by2px; real overlapping text stays unsafe."""
    import fitz

    from devnous.tournaments.core.ctt_batch_photo_alignment import prepare_batch_cards

    for footer in (False, True):
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        for x in (46, 122, 311, 385, 565):
            page.draw_line((x, 238), (x, 723), width=2)
        for row in range(6):
            page.draw_line((46, 238 + row * 97), (565, 238 + row * 97), width=2)
        for row in range(5):
            for left in (46, 311):
                for offset, text in (
                    (20, "Persona Apellido"),
                    (35, "2000-01-01"),
                    (48, "CURP: no proporcionada"),
                ):
                    page.insert_text(
                        (left + 80, 238 + row * 97 + offset), text, fontsize=6
                    )
        if footer:
            page.insert_text(
                (60, 730), "Tecnico Apellido1 1980-01-01 CURP: no proporcionada"
            )
        pixels = page.get_pixmap(alpha=False)
        image = Image.frombytes("RGB", (pixels.width, pixels.height), pixels.samples)
        prepared = prepare_batch_cards(image, 1)
        assert prepared is not None
        for card in prepared.cards[-2:]:
            if footer:
                assert card is None
            else:
                assert card is not None
                assert card[1] + card[3] <= 723
        document.close()
