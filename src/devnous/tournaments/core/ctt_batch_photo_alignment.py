"""Conservative, source-slot-based photo crops for admitted CTT batches.

The form is letter-sized, but an uploaded scan may be cropped or skewed.  The
proportional grid is accepted only where printed table lines support it.  A
missing line, slot, page or local face detector leaves that photo unresolved.
No identity or photo binding is persisted by this module.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Any, Optional

import cv2
import numpy as np
from numpy.typing import NDArray
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


@dataclass(frozen=True)
class _CardPage:
    width: int
    height: int
    page_index: int
    source_hash: str
    cards: tuple[Optional[tuple[int, int, int, int]], ...]


def _card_rgb(image: Image.Image) -> Optional[Image.Image]:
    # A transformed EXIF frame must never be labelled ORIGINAL.
    if (
        image.getexif().get(274, 1) != 1
        or image.width < 250
        or image.height < 300
        or image.width > 8000
        or image.height > 8000
        or image.width * image.height > 25_000_000
        or not 0.62 <= image.width / image.height <= 0.92
    ):
        return None
    return image.convert("RGB")


def _edge_score(dark, start, end, band):
    """Require a continuous printed edge, allowing only small raster gaps."""
    count = int(math.ceil(max(abs(end[0] - start[0]), abs(end[1] - start[1])))) + 1
    points = np.linspace(start, end, count)
    vertical = abs(end[1] - start[1]) > abs(end[0] - start[0])
    xs = np.rint(points[:, 0]).astype(int)
    ys = np.rint(points[:, 1]).astype(int)
    if (
        min(xs) < 0
        or max(xs) >= dark.shape[1]
        or min(ys) < 0
        or max(ys) >= dark.shape[0]
    ):
        return 0.0
    support: NDArray[np.bool_] = np.zeros(count, dtype=bool)
    for offset in range(-band, band + 1):
        x = np.clip(xs + (offset if vertical else 0), 0, dark.shape[1] - 1)
        y = np.clip(ys + (0 if vertical else offset), 0, dark.shape[0] - 1)
        support |= dark[y, x] > 0
    longest = gap = 0
    for present in support:
        gap = 0 if present else gap + 1
        longest = max(longest, gap)
    return float(support.mean()) if longest <= max(3, band * 3) else 0.0


def _edge_supported(dark, start, end, band):
    return _edge_score(dark, start, end, band) >= 0.90


def _source_line_verified(gray, start, end, band):
    """Check coherent line contrast against symmetric ORIGINAL neighbour bands.

    Identical estimators in all bands prevent noise gaining apparent contrast
    from wider sampling; symmetric neighbours cancel a lighting gradient.
    """
    count = int(math.ceil(max(abs(end[0] - start[0]), abs(end[1] - start[1])))) + 1
    points = np.linspace(start, end, count)
    vertical = abs(end[1] - start[1]) > abs(end[0] - start[0])
    xs = np.rint(points[:, 0]).astype(int)
    ys = np.rint(points[:, 1]).astype(int)
    if count < 30:
        return False
    # A narrow printed gutter can be smaller than the geometric sampling
    # band. Test an additional single-pixel estimator for vertical borders;
    # all three bands still use the identical estimator (never min vs mean).
    for sample_band in ((band, 0) if vertical else (band,)):
        distance = (
            sample_band * 2 + 4 if sample_band else max(2, round(gray.shape[1] / 306))
        )
        samples = []
        for centre in (0, -distance, distance):
            pixels = []
            for offset in range(-sample_band, sample_band + 1):
                x = xs + (centre + offset if vertical else 0)
                y = ys + (0 if vertical else centre + offset)
                if (
                    min(x) < 0
                    or max(x) >= gray.shape[1]
                    or min(y) < 0
                    or max(y) >= gray.shape[0]
                ):
                    return False
                pixels.append(gray[y, x])
            samples.append(np.min(pixels, axis=0).astype(np.float64))
        contrasts = [(samples[1] + samples[2]) / 2 - samples[0]]
        if vertical:
            contrasts.extend((samples[1] - samples[2], samples[2] - samples[1]))
        for contrast in contrasts:
            segments = np.array_split(contrast, 6)
            means = np.asarray([float(part.mean()) for part in segments])
            errors = np.asarray(
                [float(part.std()) / math.sqrt(len(part)) for part in segments]
            )
            coherent = int((means > np.maximum(0.5, errors * 3)).sum())
            if coherent >= 4 and np.median(means) > max(
                0.5, float(np.median(errors)) * 6
            ):
                return True
    return False


def _column_edge(mask, target, bounds, width, gray, band) -> Optional[int]:
    """Fit a physical column across several rows, not a photograph's edge."""
    lo, hi = int(bounds[0]), int(bounds[-1])
    candidates = []
    for x in range(
        math.floor(target - width * 0.015), math.ceil(target + width * 0.015) + 1
    ):
        if x < 1 or x + 1 >= mask.shape[1]:
            continue
        support = (mask[lo:hi, x - 1 : x + 2] > 0).any(axis=1).mean()
        if support >= 0.65 and _source_line_verified(gray, (x, lo), (x, hi), band):
            candidates.append((x, float(support)))
    return (
        min(candidates, key=lambda item: (-item[1], abs(item[0] - target)))[0]
        if candidates
        else None
    )


def _horizontal_strength(gray, left, right, y, band):
    """Locate the measured ridge's centre, not its dilated sampling halo."""
    gap = band * 2 + 4
    if y - gap - band < 0 or y + gap + band >= gray.shape[0]:
        return 0.0
    means = [
        float(gray[y + offset - band : y + offset + band + 1, left : right + 1].mean())
        for offset in (0, -gap, gap)
    ]
    return (means[1] + means[2]) / 2 - means[0]


_CardRows = tuple[Optional[int], ...]
_CardPaths = list[tuple[float, _CardRows]]
_CardColumn = tuple[int, int, _CardRows]


def _row_model(mask, left, right, bounds, height, band, gray) -> Optional[_CardPaths]:
    """Choose a monotone, repeated full-card row grid, never local CURP lines."""
    step = float(np.median(np.diff(bounds)))
    options: list[list[tuple[Optional[int], float]]] = []
    for index, expected in enumerate(bounds):
        radius = height * (0.012 if index == 0 else 0.025)
        candidates = []
        for scan_y in range(
            math.floor(expected - radius), math.ceil(expected + radius) + 1
        ):
            score = _edge_score(mask, (left, scan_y), (right, scan_y), band)
            if score >= 0.90 and _source_line_verified(
                gray, (left, scan_y), (right, scan_y), band
            ):
                candidates.append(
                    (
                        scan_y,
                        score,
                        _horizontal_strength(gray, left, right, scan_y, band),
                    )
                )
        # Collapse a raster-thickness plateau to one measured separator.
        groups: list[list[tuple[int, float, float]]] = []
        for item in candidates:
            if not groups or item[0] > groups[-1][-1][0] + 1:
                groups.append([])
            groups[-1].append(item)
        peaks = [
            min(group, key=lambda item: (-item[1], -item[2], abs(item[0] - expected)))
            for group in groups
        ]
        options.append([(item[0], item[1]) for item in peaks] or [(None, 0.0)])
    if sum(points[0][0] is not None for points in options) < len(bounds) - 1:
        return None
    states: list[tuple[float, _CardRows, Optional[int], Optional[int]]] = [
        (0.0, (), None, None)
    ]
    for index, points in enumerate(options):
        next_states: list[tuple[float, _CardRows, Optional[int], Optional[int]]] = []
        for y, score in points:
            valid: list[tuple[float, _CardRows, Optional[int], Optional[int]]] = []
            for total, path, previous, previous_index in states:
                if (
                    y is not None
                    and previous is not None
                    and previous_index is not None
                ):
                    distance = (index - previous_index) * step
                    if not distance * 0.90 <= y - previous <= distance * 1.12:
                        continue
                penalty = abs(y - bounds[index]) * 0.001 if y is not None else 0
                valid.append(
                    (
                        total + score - penalty,
                        path + (y,),
                        y if y is not None else previous,
                        index if y is not None else previous_index,
                    )
                )
            if valid:
                next_states.append(max(valid, key=lambda state: state[0]))
        states = next_states
        if not states:
            return None
    return sorted(((state[0], state[1]) for state in states), reverse=True)


def prepare_batch_cards(image: Image.Image, page_index: int) -> Optional[_CardPage]:
    """Fit full card columns and a repeated row grid in ORIGINAL coordinates.

    A missing edge stays unresolved. Local thin-line contrast is used only to
    measure the source; it changes neither source pixels nor photo derivation.
    """
    if type(page_index) is not int or page_index not in (1, 2):
        return None
    rgb = _card_rgb(image)
    if rgb is None:
        return None
    grid = prepare_batch_page(rgb, page_index)
    if grid is None:
        return None
    width, height = rgb.size
    gray = np.asarray(rgb.convert("L"))
    band = max(2, round(width / 306))
    horizontal_strokes = cv2.morphologyEx(
        gray, cv2.MORPH_BLACKHAT, np.ones((2 * band + 5, 1), dtype=np.uint8)
    )
    vertical_strokes = cv2.morphologyEx(
        gray, cv2.MORPH_BLACKHAT, np.ones((1, 2 * band + 5), dtype=np.uint8)
    )
    horizontal_raw = (horizontal_strokes >= 3).astype(np.uint8) * 255
    vertical_raw = (vertical_strokes >= 3).astype(np.uint8) * 255
    horizontal_mask = cv2.morphologyEx(
        cv2.dilate(
            cv2.morphologyEx(
                horizontal_raw,
                cv2.MORPH_CLOSE,
                np.ones((1, 2 * band + 3), dtype=np.uint8),
            ),
            np.ones((3, 1), dtype=np.uint8),
        ),
        cv2.MORPH_OPEN,
        np.ones((1, max(30, round(width * 0.15))), dtype=np.uint8),
    )
    vertical_mask = cv2.morphologyEx(
        cv2.dilate(
            cv2.morphologyEx(
                vertical_raw,
                cv2.MORPH_CLOSE,
                np.ones((2 * band + 3, 1), dtype=np.uint8),
            ),
            np.ones((1, 3), dtype=np.uint8),
        ),
        cv2.MORPH_OPEN,
        np.ones((max(20, round(height * 0.045)), 1), dtype=np.uint8),
    )
    middle = (grid.boundaries[0] + grid.boundaries[-1]) / 2
    first = grid.rails[0][0] * middle + grid.rails[0][1]
    centre = grid.rails[2][0] * middle + grid.rails[2][1]
    targets = ((first, centre - width * 0.01), (centre, width * 0.925))
    fits: list[Optional[tuple[int, int, _CardPaths]]] = []
    for left_target, right_target in targets:
        left = _column_edge(
            vertical_mask, left_target, grid.boundaries, width, gray, band
        )
        right = _column_edge(
            vertical_mask, right_target, grid.boundaries, width, gray, band
        )
        if (
            left is None
            or right is None
            or not width * 0.30 <= right - left <= width * 0.50
        ):
            fits.append(None)
            continue
        row_paths = _row_model(
            horizontal_mask, left, right, grid.boundaries, height, band, gray
        )
        fits.append((left, right, row_paths) if row_paths is not None else None)
    # Jointly select co-linear separator paths from both physical columns;
    # a locally plausible CURP line cannot displace one column's last row.
    columns: list[Optional[_CardColumn]]
    left_fit, right_fit = fits
    if left_fit is not None and right_fit is not None:
        compatible = [
            (left_score + right_score, left_rows, right_rows)
            for left_score, left_rows in left_fit[2]
            for right_score, right_rows in right_fit[2]
            if all(
                left_y is None
                or right_y is None
                or abs(left_y - right_y) <= height * 0.012
                for left_y, right_y in zip(left_rows, right_rows)
            )
        ]
        if not compatible:
            return None
        _, left_rows, right_rows = max(compatible, key=lambda item: item[0])
        columns = [
            (left_fit[0], left_fit[1], left_rows),
            (right_fit[0], right_fit[1], right_rows),
        ]
    else:
        columns = [
            (column[0], column[1], column[2][0][1]) if column is not None else None
            for column in fits
        ]
    # A genuine separator belongs to the whole repeated physical grid. A
    # strong displaced text rule is not a complete card boundary even if its
    # stroke is continuous and co-linear in both columns. Keep rejected points
    # distinct from merely occluded thin-line masks: they cannot be recovered.
    rejected = set()
    frame_limits = {}
    for column_index, column in enumerate(columns):
        if column is None:
            continue
        left, right, rows_tuple = column
        known = [
            (index, value)
            for index, value in enumerate(rows_tuple)
            if value is not None
        ]
        if len(known) < 3:
            continue
        slope = float(
            np.median([(b - a) / (j - i) for i, a in known for j, b in known if j > i])
        )
        intercept = float(np.median([value - index * slope for index, value in known]))
        rows = list(rows_tuple)
        for index, value in known:
            if 0 < index < len(rows) - 1:
                previous, following = rows[index - 1], rows[index + 1]
                if previous is not None and following is not None:
                    # Moving one separator produces opposite changes in its
                    # neighbouring row heights. Bound that curvature against
                    # the robust global measured row height, not a local rule.
                    invalid = abs((following - value) - (value - previous)) > max(
                        3, slope * 0.115
                    )
                else:
                    invalid = abs(value - (intercept + index * slope)) > max(
                        2, slope * 0.06
                    )
            else:
                expected = grid.boundaries[index]
                radius = round(height * 0.03)
                lo, hi = max(0, expected - radius), min(height, expected + radius + 1)
                left_support = (
                    vertical_mask[lo:hi, max(0, left - band) : left + band + 1] > 0
                ).any(axis=1)
                right_support = (
                    vertical_mask[lo:hi, max(0, right - band) : right + band + 1] > 0
                ).any(axis=1)
                endpoints = []
                for support in (left_support, right_support):
                    measured = np.flatnonzero(support) + lo
                    endpoints.append(
                        (int(measured[0]) if index == 0 else int(measured[-1]))
                        if len(measured)
                        else None
                    )
                interior = [(i, v) for i, v in known if 0 < i < len(rows) - 1]
                steps = [
                    (b - a) / (j - i) for (i, a), (j, b) in zip(interior, interior[1:])
                ]
                if len(steps) < 2:
                    invalid = True
                else:
                    fitted_step = float(np.median(steps))
                    fitted_origin = float(
                        np.median([v - i * fitted_step for i, v in interior])
                    )
                    # Endpoints cannot establish their own spacing. Estimate
                    # uncertainty only from independent interior row heights;
                    # a regular grid does not license a shifted outer rule.
                    tolerance = max(
                        3 * width / 612, np.ceil(2.5 * (max(steps) - min(steps)))
                    )
                    invalid = (
                        abs(value - fitted_origin - index * fitted_step) > tolerance
                    )
                invalid = invalid or any(endpoint is None for endpoint in endpoints)
                if not invalid:
                    measured_endpoints = [e for e in endpoints if e is not None]
                    first_endpoint, last_endpoint = (
                        min(measured_endpoints),
                        max(measured_endpoints),
                    )
                    # Raster endpoints vary across a slightly skewed page.
                    # Permit that measured span, then intersect the ROI with
                    # both endpoints instead of treating their mean as a frame.
                    invalid = not (
                        first_endpoint - (band + 3) <= value <= first_endpoint + band
                        if index == 0
                        else last_endpoint - (band + 3) <= value <= last_endpoint + band
                    )
                    frame_limits[column_index, index] = (first_endpoint, last_endpoint)
            if invalid:
                rows[index] = None
                rejected.add((column_index, index))
        columns[column_index] = (left, right, tuple(rows))
    # The outer frame is shared by both columns. If one side contradicts
    # its independent interior spacing, neither adjacent outer card is safe.
    for endpoint_index in (0, 5 if page_index == 1 else 6):
        if any((column_index, endpoint_index) in rejected for column_index in (0, 1)):
            for column_index, column in enumerate(columns):
                if column is not None:
                    left, right, rows_tuple = column
                    rows = list(rows_tuple)
                    rows[endpoint_index] = None
                    columns[column_index] = (left, right, tuple(rows))
                    rejected.add((column_index, endpoint_index))
    # A photograph can obscure the thin-line mask at a real row border.
    # Recover only a missing separator supported by the other column's global
    # model AND continuous original ink across this card, never by position alone.
    recovered: set[tuple[int, int]] = set()
    # Recovery still requires measured contrast. A fixed grey cutoff would
    # mistake grey paper for ink after a separator was erased. The wide
    # horizontal closing covers a photo column; the narrow vertical closing
    # covers a printed horizontal stroke, both relative to nearby paper.
    photo_contrast = cv2.morphologyEx(
        gray,
        cv2.MORPH_BLACKHAT,
        np.ones((1, max(31, round(width * 0.20))), dtype=np.uint8),
    )
    recovery_ink = ((horizontal_strokes >= 3) | (photo_contrast >= 3)).astype(
        np.uint8
    ) * 255
    original_dark = cv2.morphologyEx(
        recovery_ink,
        cv2.MORPH_OPEN,
        np.ones((1, max(9, round(width * 0.025))), dtype=np.uint8),
    )
    left_column, right_column = columns
    if left_column is not None and right_column is not None:
        for column_index in (0, 1):
            column = columns[column_index]
            peer_column = columns[1 - column_index]
            if column is None or peer_column is None:
                continue
            left, right, rows_tuple = column
            rows = list(rows_tuple)
            peer_rows = peer_column[2]
            for index, (y, peer) in enumerate(zip(rows, peer_rows)):
                if y is not None or peer is None or (column_index, index) in rejected:
                    continue
                radius = max(3, round(height * 0.012))
                candidates = [
                    (
                        candidate,
                        _edge_score(
                            original_dark, (left, candidate), (right, candidate), band
                        ),
                    )
                    for candidate in range(peer - radius, peer + radius + 1)
                ]
                candidates = [
                    (candidate, score)
                    for candidate, score in candidates
                    if score >= 0.90
                    and _source_line_verified(
                        gray, (left, candidate), (right, candidate), band
                    )
                ]
                if candidates:
                    candidate = min(
                        candidates, key=lambda item: (-item[1], abs(item[0] - peer))
                    )[0]
                    step = float(np.median(np.diff(grid.boundaries)))
                    previous_row = rows[index - 1] if index else None
                    following_row = rows[index + 1] if index + 1 < len(rows) else None
                    if (
                        previous_row is None
                        or step * 0.90 <= candidate - previous_row <= step * 1.12
                    ) and (
                        following_row is None
                        or step * 0.90 <= following_row - candidate <= step * 1.12
                    ):
                        rows[index] = candidate
                        recovered.add((column_index, index))
            columns[column_index] = (left, right, tuple(rows))
    cards = []
    padding = band + 2  # Directional dilation1 + measurement band +1.
    for row in range(5 if page_index == 1 else 6):
        for column_index, column in enumerate(columns):
            box = None
            if column is not None:
                left, right, final_rows = column
                top, bottom = final_rows[row : row + 2]
                if (
                    top is not None
                    and bottom is not None
                    and all(
                        (
                            _edge_supported(mask, start, end, band)
                            and _source_line_verified(gray, start, end, band)
                        )
                        for mask, start, end in (
                            (
                                (
                                    original_dark
                                    if (column_index, row) in recovered
                                    else horizontal_mask
                                ),
                                (left, top),
                                (right, top),
                            ),
                            (
                                (
                                    original_dark
                                    if (column_index, row + 1) in recovered
                                    else horizontal_mask
                                ),
                                (left, bottom),
                                (right, bottom),
                            ),
                            (vertical_mask, (left, top), (left, bottom)),
                            (vertical_mask, (right, top), (right, bottom)),
                        )
                    )
                ):
                    x, y = left + padding, top + padding
                    end_x, end_y = right - padding, bottom - padding
                    if row == 0 and (column_index, 0) in frame_limits:
                        y = max(y, frame_limits[column_index, 0][1] + 1)
                    if (
                        row + 1 == len(rows) - 1
                        and (column_index, row + 1) in frame_limits
                    ):
                        end_y = min(end_y, frame_limits[column_index, row + 1][0] - 1)
                    if (
                        0 <= x < end_x <= width
                        and 0 <= y < end_y <= height
                        and (end_x - x) * (end_y - y) >= 12000
                    ):
                        box = (x, y, end_x - x, end_y - y)
            cards.append(box)
    return _CardPage(
        width,
        height,
        page_index,
        hashlib.sha256(rgb.tobytes()).hexdigest(),
        tuple(cards),
    )


def resolve_batch_card(
    image: Image.Image,
    *,
    page_index: int,
    role: str,
    slot: int,
    prepared: Optional[_CardPage | _Grid] = None,
) -> Optional[dict[str, int]]:
    """Resolve a physical card in ORIGINAL pixels; not semantic/human proof.

    PLAYER uses front slots1..8 or back9..20. Technical staff uses only the
    front's first row: director_tecnico slot1 and auxiliar slot2. The caller
    must already bind the physical PDF reference to the protected scope.
    """
    if type(page_index) is not int or type(slot) is not int:
        return None
    if role == "player" and (
        (page_index == 1 and 1 <= slot <= 8) or (page_index == 2 and 9 <= slot <= 20)
    ):
        index = slot + 1 if page_index == 1 else slot - 9
    elif page_index == 1 and (role, slot) in (("director_tecnico", 1), ("auxiliar", 2)):
        index = slot - 1
    else:
        return None
    rgb = _card_rgb(image)
    if rgb is None:
        return None
    if isinstance(prepared, _Grid):
        # Existing photo hints have no source-byte binding; remeasure cards.
        if prepared.page_index != page_index or rgb.size != (
            prepared.width,
            prepared.height,
        ):
            return None
        prepared = None
    cards = prepare_batch_cards(rgb, page_index) if prepared is None else prepared
    if (
        not isinstance(cards, _CardPage)
        or cards.page_index != page_index
        or rgb.size != (cards.width, cards.height)
        or hashlib.sha256(rgb.tobytes()).hexdigest() != cards.source_hash
    ):
        return None
    box = cards.cards[index]
    return dict(zip(("x", "y", "width", "height"), box)) if box is not None else None


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
