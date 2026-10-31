"""Verify candidate staff crop derivation from already verified page bytes.

This is a byte/geometry check only, not a staff decision or capture authority.
The caller must independently bind the page bytes to the protected source and
must use the verified crop bytes, rather than re-read a mutable asset later.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from io import BytesIO
from typing import Any, Mapping

from PIL import Image


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def verify_staff_geometry(
    scope: Mapping[str, Any],
    geometry: Mapping[str, Any],
    page_bytes: bytes,
    *,
    max_page_bytes: int,
) -> bool:
    """Recompute the established identity-normalized RGB crop binding."""
    try:
        keys = {
            "original_image_hash",
            "normalized_image_hash",
            "normalization_version",
            "normalization_transform_hash",
            "coordinate_frame",
            "coordinate_frame_image_hash",
            "field_coordinates",
            "crop_derivation_version",
            "crop_hash",
            "effective_pixels",
        }
        if (
            not isinstance(scope, Mapping)
            or not isinstance(geometry, Mapping)
            or set(geometry) != keys
            or type(page_bytes) is not bytes
            or type(max_page_bytes) is not int
            or max_page_bytes < 1
            or not 0 < len(page_bytes) <= max_page_bytes
        ):
            return False
        original = _digest(page_bytes)
        if not isinstance(
            scope.get("source_page_hash"), str
        ) or not hmac.compare_digest(original, scope["source_page_hash"]):
            return False
        if any(
            geometry.get(key) != original
            for key in (
                "original_image_hash",
                "normalized_image_hash",
                "coordinate_frame_image_hash",
            )
        ):
            return False
        transform = {
            "operation": "identity",
            "source_hash": original,
            "version": "samchat-identity-normalization-v1",
        }
        if (
            geometry.get("normalization_version") != "samchat-identity-normalization-v1"
            or geometry.get("normalization_transform_hash")
            != _digest(_canonical(transform))
            or geometry.get("coordinate_frame") != "ORIGINAL"
            or geometry.get("crop_derivation_version") != "samchat-rgb-field-crop-v1"
        ):
            return False
        coords = geometry.get("field_coordinates")
        if not isinstance(coords, Mapping) or set(coords) != {
            "x",
            "y",
            "width",
            "height",
        }:
            return False
        if any(type(coords[key]) is not int for key in coords):
            return False
        x, y, width, height = (coords[k] for k in ("x", "y", "width", "height"))
        if (
            x < 0
            or y < 0
            or width < 1
            or height < 1
            or type(geometry.get("effective_pixels")) is not int
            or geometry["effective_pixels"] != width * height
        ):
            return False
        with Image.open(BytesIO(page_bytes)) as image:
            if (
                image.width > 8000
                or image.height > 8000
                or image.width * image.height > 25_000_000
                or x + width > image.width
                or y + height > image.height
            ):
                return False
            crop = image.convert("RGB").crop((x, y, x + width, y + height))
            payload = (
                _canonical({"mode": crop.mode, "size": list(crop.size)})
                + crop.tobytes()
            )
        return isinstance(geometry.get("crop_hash"), str) and hmac.compare_digest(
            _digest(payload), geometry["crop_hash"]
        )
    except Exception:
        return False
