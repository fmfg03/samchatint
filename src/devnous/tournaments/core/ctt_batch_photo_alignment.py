"""Conservative, source-slot-based photo crops for admitted CTT batches.

The form is letter-sized, but an uploaded scan may be cropped or skewed.  The
proportional grid is accepted only where printed table lines support it.  A
missing line, slot, page or local face detector leaves that photo unresolved.
No identity or photo binding is persisted by this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Any, Optional

import cv2
import numpy as np
from PIL import Image, ImageOps

_SLOT = re.compile(r"^page:(\d+):jugador_(\d+)$")
_FACE_LOCK = Lock()


def parse_batch_slot(
    slot_ref: object, source_pages: object
) -> Optional[tuple[int, int]]:
    """Resolve a declared physical PDF page and printed slot; never use order."""
    if not isinstance(slot_ref, str) or not isinstance(source_pages, list):
        return None
    match = _SLOT.fullmatch(slot_ref)
    if not match or len(source_pages) != 2:
        return None
    if any(type(page) is not int or page < 1 for page in source_pages):
        return None
    pages = source_pages
    if pages != sorted(set(pages)):
        return None
    page, slot = (int(value) for value in match.groups())
    if page == pages[0] and 1 <= slot <= 8:
        return 1, slot
    if page == pages[1] and 9 <= slot <= 20:
        return 2, slot
    return None


@dataclass(frozen=True)
class _Grid:
    width: int
    height: int
    page_index: int
    boundaries: tuple[int, ...]
    boundary_scores: tuple[float, ...]
    rails: tuple[tuple[float, float], ...]


def _vertical_rails(dark: np.ndarray) -> Optional[tuple[tuple[float, float], ...]]:
    height, width = dark.shape
    edges = cv2.Canny(dark, 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 360,
        threshold=max(24, int(height * 0.15)),
        minLineLength=int(height * 0.28),
        maxLineGap=int(height * 0.065),
    )
    if lines is None:
        return None
    groups: list[list[tuple[float, float, float]]] = [[], [], [], []]
    ranges = ((0.035, 0.105), (0.15, 0.22), (0.47, 0.54), (0.59, 0.67))
    for line in lines[:, 0]:
        x1, y1, x2, y2 = (int(v) for v in line)
        dy, dx = y2 - y1, x2 - x1
        if abs(dy) < height * 0.28 or abs(dx) > max(8, abs(dy) * 0.04):
            continue
        slope = dx / dy
        intercept = x1 - slope * y1
        middle = slope * height * 0.5 + intercept
        for index, (lo, hi) in enumerate(ranges):
            if lo * width <= middle <= hi * width:
                groups[index].append((abs(dy), slope, intercept))
                break
    chosen = [max(group, default=None) for group in groups]
    # Three physical rails establish the fourth via equal photo-column widths.
    if (
        sum(item is not None for item in chosen) < 3
        or chosen[1] is None
        or chosen[2] is None
    ):
        return None
    if chosen[0] is None and chosen[3] is not None:
        chosen[0] = (
            0,
            chosen[1][1] - chosen[3][1] + chosen[2][1],
            chosen[1][2] - chosen[3][2] + chosen[2][2],
        )
    if chosen[3] is None and chosen[0] is not None:
        chosen[3] = (
            0,
            chosen[2][1] + chosen[1][1] - chosen[0][1],
            chosen[2][2] + chosen[1][2] - chosen[0][2],
        )
    if any(item is None for item in chosen):
        return None
    rails = tuple(
        (float(item[1]), float(item[2])) for item in chosen if item is not None
    )
    for y in (height * 0.2, height * 0.5, height * 0.85):
        xs = [a * y + b for a, b in rails]
        left, right = xs[1] - xs[0], xs[3] - xs[2]
        if not (0 <= xs[0] < xs[1] < xs[2] < xs[3] <= width):
            return None
        if not (
            0.075 * width <= left <= 0.17 * width
            and 0.075 * width <= right <= 0.17 * width
            and abs(left - right) <= 0.035 * width
        ):
            return None
    return rails


def _horizontal_grid(
    dark: np.ndarray, page_index: int
) -> Optional[tuple[tuple[int, ...], tuple[float, ...]]]:
    height, width = dark.shape
    # Text lies to the right of each photo. Table separators cross both columns;
    # require a repeated pattern at the proportions of the printed letter form.
    binary = dark > 0
    left = (
        binary[:, int(width * 0.20) : int(width * 0.47)].mean(axis=1).astype(np.float32)
    )
    right = (
        binary[:, int(width * 0.65) : int(width * 0.90)].mean(axis=1).astype(np.float32)
    )
    kernel: Any = np.ones((7, 1), dtype=np.uint8)
    score = np.minimum(
        cv2.dilate(left[:, None], kernel).ravel(),
        cv2.dilate(right[:, None], kernel).ravel(),
    )
    rows = 5 if page_index == 1 else 6
    limits = (0.25, 0.34) if page_index == 1 else (0.10, 0.20)
    best = None
    for first in np.arange(
        height * limits[0], height * limits[1], max(1, height / 400)
    ):
        for step in np.arange(height * 0.105, height * 0.14, max(1, height / 792)):
            points = np.rint(first + np.arange(rows + 1) * step).astype(int)
            if points[-1] >= height * 0.92:
                continue
            values, actual = [], []
            for point in points:
                radius = max(3, int(height * 0.007))
                lo, hi = max(0, point - radius), min(height, point + radius + 1)
                part = score[lo:hi]
                offset = int(np.argmax(part))
                values.append(float(part[offset]))
                actual.append(lo + offset)
            rank = (
                sum(v > 0.52 for v in values),
                sum(values),
                -float(np.std(np.diff(actual))),
            )
            if best is None or rank > best[0]:
                best = (rank, actual, values)
    if best is None or best[0][0] < rows - 1:
        return None
    bounds, scores = best[1], best[2]
    if any(b - a < height * 0.085 for a, b in zip(bounds, bounds[1:])):
        return None
    return tuple(bounds), tuple(scores)


def prepare_batch_page(image: Image.Image, page_index: int) -> Optional[_Grid]:
    """Detect a letter-form grid; fail closed if the page cannot be aligned."""
    if page_index not in (1, 2):
        return None
    rgb = ImageOps.exif_transpose(image).convert("RGB")
    width, height = rgb.size
    if width < 250 or height < 300 or not 0.62 <= width / height <= 0.92:
        return None
    gray = np.asarray(rgb.convert("L"))
    dark = ((gray < 200) * 255).astype(np.uint8)
    rails = _vertical_rails(dark)
    horizontal = _horizontal_grid(dark, page_index)
    if rails is None or horizontal is None:
        return None
    bounds, scores = horizontal
    return _Grid(width, height, page_index, bounds, scores, rails)


@lru_cache(maxsize=1)
def _face_detector():
    data = getattr(cv2, "data", None)
    if data is None:
        return None
    path = data.haarcascades + "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(path)
    return None if detector.empty() else detector


def _face_present(image: Image.Image) -> bool:
    """A local face signal helps reject blank cells and printed text; not ID."""
    detector = _face_detector()
    if detector is None:
        return False
    gray = np.asarray(image.convert("L"))
    enlarged = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    with _FACE_LOCK:
        faces = detector.detectMultiScale(
            enlarged, scaleFactor=1.08, minNeighbors=5, minSize=(80, 80)
        )
    return _face_geometry_is_safe(faces, enlarged.shape[1], enlarged.shape[0])


def _face_geometry_is_safe(faces: Any, width: int, height: int) -> bool:
    """Reject faces cut at the bottom or displaced toward the next row."""
    if len(faces) != 1 or width <= 0 or height <= 0:
        return False
    x, y, face_width, face_height = (int(value) for value in faces[0])
    return (
        face_width >= width * 0.35
        and face_height >= height * 0.30
        and y <= height * 0.42
        and y + face_height <= height * 0.94
        and x >= 0
        and x + face_width <= width
    )


def extract_batch_photo(
    image: Image.Image,
    *,
    page_index: int,
    slot: int,
    prepared: Optional[_Grid] = None,
) -> tuple[Optional[Image.Image], str]:
    """Return a crop only for a supported grid/slot and a plausible local face."""
    if not (
        (page_index == 1 and 1 <= slot <= 8) or (page_index == 2 and 9 <= slot <= 20)
    ):
        return None, "uncertain"
    grid = prepared if prepared is not None else prepare_batch_page(image, page_index)
    if (
        grid is None
        or grid.page_index != page_index
        or image.size != (grid.width, grid.height)
    ):
        return None, "uncertain"
    row = (slot - 1) // 2 + 1 if page_index == 1 else (slot - 9) // 2
    column = (slot - 1) % 2 if page_index == 1 else (slot - 9) % 2
    if min(grid.boundary_scores[row : row + 2]) < 0.4:
        return None, "uncertain"
    top, bottom = grid.boundaries[row : row + 2]
    middle = (top + bottom) / 2
    left_rail, right_rail = grid.rails[column * 2 : column * 2 + 2]
    left = int(left_rail[0] * middle + left_rail[1]) + 2
    right = int(right_rail[0] * middle + right_rail[1]) - 2
    # The final printed row has no following player row; retain the lower edge.
    if row == len(grid.boundaries) - 2:
        bottom = min(grid.height, bottom + int(grid.height * 0.012))
    top, bottom = top + 2, bottom - 2
    if not (0 <= left < right <= grid.width and 0 <= top < bottom <= grid.height):
        return None, "uncertain"
    crop = image.convert("RGB").crop((left, top, right, bottom))
    gray = np.asarray(crop.convert("L"))
    margin_y, margin_x = max(2, gray.shape[0] // 12), max(2, gray.shape[1] // 12)
    interior = gray[margin_y:-margin_y, margin_x:-margin_x]
    ink = float((interior < 180).mean())
    if ink < 0.003:
        return None, "missing"
    if not _face_present(crop):
        return None, "uncertain"
    return crop, "photo"
