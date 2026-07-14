"""Create readable, slot-scoped evidence images for CTT registration OCR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from .ocr_integrity import clamp_box, normalize_ctt_template_image

PLAYER_CARD_PREFIX = "jugador_"
PAGE_SIDES = ("front", "back", "back")


@dataclass(frozen=True)
class CttSlotCrop:
    """One complete player card and its deterministic document coordinates."""

    page: int
    slot: int
    box: Tuple[int, int, int, int]
    image: Image.Image
    source_page: Optional[int] = None
    source_slot: Optional[int] = None

    @property
    def physical_page(self) -> int:
        return self.source_page or self.page

    @property
    def physical_slot(self) -> int:
        return self.source_slot or self.slot

    @property
    def label(self) -> str:
        return f"P{self.page} jugador_{self.slot}"


@dataclass(frozen=True)
class CttSlotBatch:
    """A bounded set of complete player cards prepared for one OCR call."""

    slots: Tuple[CttSlotCrop, ...]
    montage: Image.Image


def _slot_number(card_name: str) -> int:
    try:
        return int(card_name[len(PLAYER_CARD_PREFIX) :])
    except ValueError as exc:
        raise ValueError(f"Invalid CTT player card name: {card_name}") from exc


def _field_box(
    field: Mapping[str, Any], image_size: Tuple[int, int]
) -> Tuple[int, int, int, int]:
    width, height = image_size
    left = int(float(field.get("x") or 0.0) * width)
    top = int(float(field.get("y") or 0.0) * height)
    right = int((float(field.get("x") or 0.0) + float(field.get("w") or 0.0)) * width)
    bottom = int((float(field.get("y") or 0.0) + float(field.get("h") or 0.0)) * height)
    return clamp_box((left, top, right, bottom), image_size)


def player_card_box(
    fields: Mapping[str, Mapping[str, Any]],
    image_size: Tuple[int, int],
    *,
    photo_extension_ratio: float = 0.17,
    horizontal_margin_ratio: float = 0.008,
    vertical_margin_ratio: float = 0.012,
    vertical_offset_ratio: float = 0.0,
) -> Tuple[int, int, int, int]:
    """Return a card box that keeps the photo and all text fields together."""
    boxes = [
        _field_box(field, image_size)
        for field in fields.values()
        if isinstance(field, Mapping)
    ]
    if not boxes:
        raise ValueError("CTT player card has no field coordinates")

    width, height = image_size
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    bottom = max(box[3] for box in boxes)

    horizontal_margin = int(width * horizontal_margin_ratio)
    vertical_margin = int(height * vertical_margin_ratio)
    vertical_offset = int(height * vertical_offset_ratio)
    photo_extension = int(width * photo_extension_ratio)
    return clamp_box(
        (
            left - photo_extension - horizontal_margin,
            top - vertical_margin + vertical_offset,
            right + horizontal_margin,
            bottom + vertical_margin + vertical_offset,
        ),
        image_size,
    )


def extract_slots_from_normalized_page(
    image: Image.Image,
    *,
    page: int,
    page_layout: Mapping[str, Any],
) -> List[CttSlotCrop]:
    """Crop complete player cards from a page already in canonical geometry."""
    cards = page_layout.get("cards") or {}
    player_cards = [
        (name, fields)
        for name, fields in cards.items()
        if str(name).startswith(PLAYER_CARD_PREFIX) and isinstance(fields, Mapping)
    ]
    player_cards.sort(key=lambda item: _slot_number(str(item[0])))
    crop_options = page_layout.get("slot_crop") or {}
    photo_extension_ratio = float(crop_options.get("photo_extension_ratio", 0.17))
    vertical_offset_ratio = float(crop_options.get("vertical_offset_ratio", 0.0))

    slots: List[CttSlotCrop] = []
    for card_name, fields in player_cards:
        slot = _slot_number(str(card_name))
        box = player_card_box(
            fields,
            image.size,
            photo_extension_ratio=photo_extension_ratio,
            vertical_offset_ratio=vertical_offset_ratio,
        )
        slots.append(CttSlotCrop(page=page, slot=slot, box=box, image=image.crop(box)))
    return slots


def extract_ctt_player_slots(
    page_images: Sequence[Image.Image],
    layout: Mapping[str, Any],
) -> List[CttSlotCrop]:
    """Normalize up to three physical pages and return their printed slots."""
    pages: Mapping[str, Any] = layout.get("pages") or {}
    slots: List[CttSlotCrop] = []
    for page_index, page_image in enumerate(page_images):
        if page_index >= len(PAGE_SIDES):
            break
        side = PAGE_SIDES[page_index]
        page_layout = pages.get(side) or {}
        normalized, _metadata = normalize_ctt_template_image(page_image)
        slots.extend(
            extract_slots_from_normalized_page(
                normalized,
                page=page_index + 1,
                page_layout=page_layout,
            )
        )
    return slots


def _resize_to_width(image: Image.Image, width: int) -> Image.Image:
    if image.width == width:
        return image.copy()
    height = max(1, round(image.height * (width / max(image.width, 1))))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def build_slot_montage(
    slots: Sequence[CttSlotCrop],
    *,
    columns: int = 2,
    cell_width: int = 1120,
    label_height: int = 54,
    gap: int = 18,
) -> Image.Image:
    """Build a compact grid without collapsing handwriting into a tall strip."""
    if not slots:
        raise ValueError("Cannot build a CTT montage without slots")
    if columns < 1:
        raise ValueError("columns must be at least 1")

    resized = [
        _resize_to_width(slot.image.convert("RGB"), cell_width) for slot in slots
    ]
    cell_height = max(image.height for image in resized) + label_height
    rows = (len(slots) + columns - 1) // columns
    canvas_width = (columns * cell_width) + ((columns + 1) * gap)
    canvas_height = (rows * cell_height) + ((rows + 1) * gap)
    montage = Image.new("RGB", (canvas_width, canvas_height), "white")
    draw = ImageDraw.Draw(montage)

    for index, (slot, image) in enumerate(zip(slots, resized)):
        row, column = divmod(index, columns)
        left = gap + (column * (cell_width + gap))
        top = gap + (row * (cell_height + gap))
        draw.text((left + 12, top + 17), slot.label, fill="black")
        image_top = top + label_height
        montage.paste(image, (left, image_top))
        draw.rectangle(
            (left, image_top, left + image.width - 1, image_top + image.height - 1),
            outline="gray",
        )
    return montage


def build_ctt_slot_batches(
    slots: Iterable[CttSlotCrop],
    *,
    max_slots: int = 4,
) -> List[CttSlotBatch]:
    """Group slots into bounded OCR units while preserving document order."""
    if max_slots < 1 or max_slots > 4:
        raise ValueError("max_slots must be between 1 and 4")

    ordered = sorted(slots, key=lambda item: (item.page, item.slot))
    batches: List[CttSlotBatch] = []
    for start in range(0, len(ordered), max_slots):
        batch_slots = tuple(ordered[start : start + max_slots])
        batches.append(
            CttSlotBatch(
                slots=batch_slots,
                montage=build_slot_montage(batch_slots),
            )
        )
    return batches
