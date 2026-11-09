"""Exercise geometry using the existing SamChat derivation, not fake hashes."""

import copy
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from devnous.copa_telmex.registration_governance import _derive_geometry
from devnous.copa_telmex.staff_capture_geometry import verify_staff_geometry


@pytest.fixture
def candidate(tmp_path):
    image = Image.new("RGB", (120, 160), "white")
    image.paste("red", (10, 20, 40, 70))
    stream = BytesIO()
    image.save(stream, format="PNG")
    raw = stream.getvalue()
    path = tmp_path / "page.png"
    path.write_bytes(raw)
    asset = SimpleNamespace(image_path=path, sha256=sha256(raw).hexdigest())
    geometry = _derive_geometry(asset, {"x": 10, "y": 20, "width": 30, "height": 50})
    assert geometry is not None
    return (
        {"source_page_hash": "sha256:" + sha256(raw).hexdigest()},
        geometry,
        raw,
    )


def test_existing_derivation_is_verified(candidate):
    scope, geometry, raw = candidate
    assert verify_staff_geometry(scope, geometry, raw, max_page_bytes=100000)


@pytest.mark.parametrize(
    "change", ["page", "scope", "crop", "coordinate", "frame", "size"]
)
def test_tampering_and_out_of_bounds_are_rejected(candidate, change):
    scope, geometry, raw = copy.deepcopy(candidate)
    if change == "page":
        raw += b"tampered"
    elif change == "scope":
        scope["source_page_hash"] = "sha256:" + "f" * 64
    elif change == "crop":
        geometry["crop_hash"] = "sha256:" + "f" * 64
    elif change == "coordinate":
        geometry["field_coordinates"]["x"] = 10000
    elif change == "frame":
        geometry["coordinate_frame"] = "NORMALIZED"
    else:
        geometry["effective_pixels"] += 1
    assert not verify_staff_geometry(scope, geometry, raw, max_page_bytes=100000)
