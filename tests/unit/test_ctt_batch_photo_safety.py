"""Fail-closed cases for photo provenance, geometry and detector failures."""

from types import SimpleNamespace

import numpy as np
from PIL import Image

from devnous.tournaments.core import ctt_batch_photo_alignment as align


def test_untrusted_page_and_slot_references_are_rejected():
    for slot, pages in [
        (None, [1, 2]),
        ("page:1:jugador_1", None),
        ("page:1:jugador_1", [1]),
        ("page:1:jugador_1", [True, 2]),
        ("page:1:jugador_1", [0, 2]),
        ("page:1:jugador_1", [2, 1]),
        ("page:1:jugador_1", [1, 1]),
        ("page:1:jugador_1:extra", [1, 2]),
        ("page:2:jugador_8", [1, 2]),
    ]:
        assert align.parse_batch_slot(slot, pages) is None


def test_detector_failure_and_unusable_geometry_are_pending(monkeypatch):
    monkeypatch.setattr(align, "_face_detector", lambda: None)
    assert not align._face_present(Image.new("RGB", (80, 100), "black"))
    assert align.prepare_batch_page(Image.new("RGB", (100, 100), "white"), 1) is None
    assert align.prepare_batch_page(Image.new("RGB", (612, 792), "white"), 3) is None
    for page, slot in [(1, 0), (1, 9), (2, 8), (2, 21), (3, 3)]:
        crop, status = align.extract_batch_photo(
            Image.new("RGB", (612, 792), "white"), page_index=page, slot=slot
        )
        assert crop is None and status == "uncertain"


def test_missing_local_classifier_data_fails_closed(monkeypatch):
    align._face_detector.cache_clear()
    try:
        monkeypatch.setattr(align.cv2, "data", None)
        assert align._face_detector() is None
    finally:
        align._face_detector.cache_clear()


def test_face_detector_requires_exactly_one_complete_centered_face(monkeypatch):
    image = Image.new("RGB", (80, 100), "white")
    good = np.array([[50, 80, 200, 200]])
    monkeypatch.setattr(
        align,
        "_face_detector",
        lambda: SimpleNamespace(detectMultiScale=lambda *args, **kwargs: good),
    )
    assert align._face_present(image)
    bad = np.array([[50, 240, 200, 155]])
    monkeypatch.setattr(
        align,
        "_face_detector",
        lambda: SimpleNamespace(detectMultiScale=lambda *args, **kwargs: bad),
    )
    assert not align._face_present(image)
    monkeypatch.setattr(
        align,
        "_face_detector",
        lambda: SimpleNamespace(
            detectMultiScale=lambda *args, **kwargs: np.vstack([good, good])
        ),
    )
    assert not align._face_present(image)


def test_out_of_bounds_and_wrong_prepared_page_are_pending():
    grid = align._Grid(
        612,
        792,
        1,
        (200, 300, 400, 500, 600, 700),
        (1.0,) * 6,
        ((0, -10), (0, 40), (0, 300), (0, 370)),
    )
    crop, status = align.extract_batch_photo(
        Image.new("RGB", (612, 792), "white"), page_index=1, slot=1, prepared=grid
    )
    assert crop is None and status == "uncertain"
    crop, status = align.extract_batch_photo(
        Image.new("RGB", (612, 792), "white"), page_index=2, slot=9, prepared=grid
    )
    assert crop is None and status == "uncertain"
