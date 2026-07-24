"""Deterministic CTT layout crops and legacy-review projection."""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from PIL import Image

from .ctt_ocr_provider import (
    CttCanonicalProviderField,
    CttFieldCrop,
    CttOcrCoordinator,
    CttOcrMode,
    CttProviderExecution,
    CttProviderRawField,
    CttValidationDecision,
)
from .ocr_integrity import normalize_ctt_template_image

HEADER_MAP = {
    "equipo_nombre": "team_name",
    "rama": "gender",
    "categoria": "category",
    "representante_nombre": "representative_name",
    "liga": "league",
    "correo": "email",
    "estado": "state",
    "municipio": "municipality",
}
CARD_MAP = {
    "nombre": "given_names",
    "apellidos": "surnames",
    "nacimiento": "birth_date",
    "curp": "curp",
}


def _pixel_sha256(image: Image.Image) -> str:
    rgb = image.convert("RGB")
    prefix = f"{rgb.width}x{rgb.height}:RGB:".encode("ascii")
    return hashlib.sha256(prefix + rgb.tobytes()).hexdigest()


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _box(
    image: Image.Image,
    spec: Mapping[str, Any],
    padding: int = 8,
    *,
    y_offset: float = 0.0,
) -> tuple[int, int, int, int]:
    left = round(float(spec["x"]) * image.width) - padding
    top = round((float(spec["y"]) + y_offset) * image.height) - padding
    right = round((float(spec["x"]) + float(spec["w"])) * image.width) + padding
    bottom = (
        round((float(spec["y"]) + y_offset + float(spec["h"])) * image.height) + padding
    )
    return (
        max(0, left),
        max(0, top),
        min(image.width, right),
        min(image.height, bottom),
    )


def _document_hash(page_payloads: Sequence[bytes]) -> str:
    digest = hashlib.sha256()
    for number, payload in enumerate(page_payloads, start=1):
        digest.update(number.to_bytes(1, "big"))
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _expanded_context_box(
    image: Image.Image,
    boxes: Sequence[tuple[int, int, int, int]],
    *,
    left: int = 32,
    top: int = 24,
    right: int = 24,
    bottom: int = 24,
) -> tuple[int, int, int, int]:
    """Create deterministic label-bearing context around one logical record."""
    return (
        max(0, min(box[0] for box in boxes) - left),
        max(0, min(box[1] for box in boxes) - top),
        min(image.width, max(box[2] for box in boxes) + right),
        min(image.height, max(box[3] for box in boxes) + bottom),
    )


@dataclass(frozen=True)
class CttCropPlan:
    document_sha256: str
    normalized_page_sha256: Tuple[str, ...]
    crops: Tuple[CttFieldCrop, ...]


def build_ctt_crop_plan(
    page_images: Sequence[Image.Image], layout: Mapping[str, Any]
) -> CttCropPlan:
    """Bind two physical pages to fixed header and slots 1-20."""
    if len(page_images) != 2:
        raise ValueError("CTT crop extraction requires exactly two pages")
    pages_layout = layout.get("pages") or {}
    normalized: List[Image.Image] = []
    page_payloads: List[bytes] = []
    for image in page_images:
        page, _metadata = normalize_ctt_template_image(image)
        normalized.append(page)
        page_payloads.append(_png_bytes(page))
    document_sha256 = _document_hash(page_payloads)
    page_hashes = tuple(_pixel_sha256(page) for page in normalized)
    crops: List[CttFieldCrop] = []

    def add(
        *,
        page_index: int,
        field_name: str,
        region: str,
        spec: Mapping[str, Any],
        slot: Optional[int] = None,
        y_offset: float = 0.0,
        context_box: Optional[tuple[int, int, int, int]] = None,
    ) -> None:
        image = normalized[page_index]
        payload = _png_bytes(image.crop(_box(image, spec, y_offset=y_offset)))
        context_payload = (
            _png_bytes(image.crop(context_box)) if context_box is not None else None
        )
        crop_id = f"p{page_index + 1}:{region}:{field_name}"
        crops.append(
            CttFieldCrop(
                crop_id=crop_id,
                document_sha256=document_sha256,
                normalized_page_sha256=page_hashes[page_index],
                crop_sha256=hashlib.sha256(payload).hexdigest(),
                field_name=field_name,
                source_page=page_index + 1,
                source_region=region,
                slot=slot,
                image_bytes=payload,
                context_image_bytes=context_payload,
                context_sha256=(
                    hashlib.sha256(context_payload).hexdigest()
                    if context_payload is not None
                    else None
                ),
            )
        )

    front = pages_layout.get("front") or {}
    for layout_name, spec in (front.get("header_fields") or {}).items():
        if layout_name in HEADER_MAP:
            # Chandra is a document parser, so preserve enough row height and
            # the complete printed label instead of sending an extreme strip.
            header_box = _box(normalized[0], spec, padding=0, y_offset=-0.040)
            add(
                page_index=0,
                field_name=HEADER_MAP[layout_name],
                region=f"header:{layout_name}",
                spec=spec,
                # The operational photographed CTT sheet has a compact header
                # relative to the canonical PDF used to author the layout.
                y_offset=-0.040,
                context_box=_expanded_context_box(
                    normalized[0], [header_box], left=490, top=76, right=24, bottom=27
                ),
            )

    for page_index, side in enumerate(("front", "back")):
        page_layout = pages_layout.get(side) or {}
        for card_name, fields in (page_layout.get("cards") or {}).items():
            match = re.fullmatch(r"jugador_(\d+)", card_name)
            if not match:
                continue
            slot = int(match.group(1))
            expected_page = 1 if slot <= 8 else 2
            if expected_page != page_index + 1:
                raise ValueError("layout assigns a player slot to the wrong page")
            y_offset = -0.058 if page_index == 0 else 0.0
            field_boxes = [
                _box(normalized[page_index], spec, y_offset=y_offset)
                for layout_name, spec in fields.items()
                if layout_name in CARD_MAP
            ]
            context_box = _expanded_context_box(
                normalized[page_index],
                field_boxes,
                left=110,
                top=28,
                right=180,
                bottom=28,
            )
            for layout_name, spec in fields.items():
                if layout_name in CARD_MAP:
                    add(
                        page_index=page_index,
                        field_name=CARD_MAP[layout_name],
                        region=f"slot-{slot}:{layout_name}",
                        spec=spec,
                        slot=slot,
                        # The front photographed sheet's player grid begins
                        # about 5.8% of page height above the reference PDF.
                        # Back-page coordinates already match the reference.
                        y_offset=y_offset,
                        context_box=context_box,
                    )
    expected = {(slot, field) for slot in range(1, 21) for field in CARD_MAP.values()}
    actual = {(crop.slot, crop.field_name) for crop in crops if crop.slot is not None}
    if actual != expected:
        raise ValueError("layout must bind all four fields for slots 1-20")
    return CttCropPlan(document_sha256, page_hashes, tuple(crops))


def validate_ctt_crop(
    crop: CttFieldCrop, observation: CttProviderRawField
) -> CttValidationDecision:
    """Conservative deterministic field validation with no model confidence."""
    value = re.sub(r"\s+", " ", observation.raw_text or "").strip()
    value = value.strip("`*_#| ")
    codes: List[str] = []
    optional = crop.field_name in {
        "curp",
        "email",
        "league",
        "representative_name",
        "state",
        "municipality",
    }
    if not value:
        return CttValidationDecision(
            normalized_value=None,
            validation_codes=[] if optional else ["MISSING_REQUIRED_FIELD"],
            accepted=optional,
        )
    if observation.candidates:
        codes.append("AMBIGUOUS_TRANSCRIPTION")
    normalized: Optional[str] = value
    if crop.field_name == "birth_date":
        parsed = None
        for pattern in (
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%d.%m.%Y",
            "%d/%m/%y",
            "%d-%m-%y",
            "%d.%m.%y",
        ):
            try:
                parsed = datetime.strptime(value, pattern)
                break
            except ValueError:
                pass
        if parsed is None or parsed.year < 1990 or parsed.year > datetime.now().year:
            codes.append("INVALID_BIRTH_DATE")
        else:
            normalized = parsed.strftime("%d/%m/%Y")
    elif crop.field_name == "curp":
        normalized = re.sub(r"[^A-Z0-9]", "", value.upper())
        if not re.fullmatch(r"[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d", normalized):
            codes.append("INVALID_CURP")
    elif crop.field_name == "email":
        normalized = value.casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            codes.append("INVALID_EMAIL")
    elif crop.field_name in {
        "given_names",
        "surnames",
        "team_name",
        "representative_name",
    }:
        if len(value) < 2 or any(ch.isdigit() for ch in value):
            codes.append("INVALID_NAME_TEXT")
    return CttValidationDecision(
        normalized_value=normalized,
        validation_codes=sorted(set(codes)),
        accepted=not codes,
    )


def _field_by_name(
    execution: CttProviderExecution,
    *,
    field_name: str,
    slot: Optional[int] = None,
) -> Optional[CttCanonicalProviderField]:
    matches = [
        field
        for field in execution.fields.values()
        if field.slot == slot
        and field.source_region.endswith(
            ":"
            + (
                {
                    "given_names": "nombre",
                    "surnames": "apellidos",
                    "birth_date": "nacimiento",
                    "curp": "curp",
                }.get(field_name, field_name)
            )
        )
    ]
    if slot is None:
        header_layout_name = next(
            (
                crop_name
                for crop_name, canonical in HEADER_MAP.items()
                if canonical == field_name
            ),
            "",
        )
        matches = [
            field
            for field in execution.fields.values()
            if field.slot is None
            and field.source_region == f"header:{header_layout_name}"
        ]
    return matches[0] if len(matches) == 1 else None


def _identity_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", without_marks.casefold())


def mark_duplicate_player_identities(players: List[Dict[str, Any]]) -> List[List[int]]:
    """Mark deterministic same-CURP or same-name-and-birth roster duplicates."""
    groups: Dict[Tuple[str, ...], List[int]] = {}
    by_slot = {int(player["visible_player_number"]): player for player in players}
    for player in players:
        slot = int(player["visible_player_number"])
        curp = _identity_text(player.get("curp"))
        name = _identity_text(player.get("name"))
        birth = _identity_text(player.get("birth_date"))
        if curp:
            groups.setdefault(("curp", curp), []).append(slot)
        if name and birth:
            groups.setdefault(("name_birth", name, birth), []).append(slot)
    related: Dict[int, set[int]] = {}
    exact_relationships: set[frozenset[int]] = set()
    for slots in groups.values():
        unique = sorted(set(slots))
        if len(unique) < 2:
            continue
        for slot in unique:
            related.setdefault(slot, set()).update(unique)
        exact_relationships.add(frozenset(unique))
    for index, left in enumerate(players):
        left_slot = int(left["visible_player_number"])
        left_name = _identity_text(left.get("name"))
        left_birth = _identity_text(left.get("birth_date"))
        if not left_name or not left_birth:
            continue
        for right in players[index + 1 :]:
            right_slot = int(right["visible_player_number"])
            right_name = _identity_text(right.get("name"))
            right_birth = _identity_text(right.get("birth_date"))
            if left_birth != right_birth or not right_name:
                continue
            if SequenceMatcher(None, left_name, right_name).ratio() < 0.70:
                continue
            related.setdefault(left_slot, set()).update((left_slot, right_slot))
            related.setdefault(right_slot, set()).update((left_slot, right_slot))
    duplicate_groups: List[Tuple[int, ...]] = []
    visited: set[int] = set()
    for start in sorted(related):
        if start in visited:
            continue
        pending = [start]
        component: set[int] = set()
        while pending:
            slot = pending.pop()
            if slot in component:
                continue
            component.add(slot)
            pending.extend(related.get(slot, set()) - component)
        visited.update(component)
        if len(component) > 1:
            duplicate_groups.append(tuple(sorted(component)))
    for duplicate_slots in duplicate_groups:
        exact = any(
            relationship.issubset(duplicate_slots)
            for relationship in exact_relationships
        )
        code = (
            "DUPLICATE_PLAYER_IDENTITY"
            if exact
            else "POSSIBLE_DUPLICATE_PLAYER_IDENTITY"
        )
        for slot in duplicate_slots:
            player = by_slot[slot]
            player["needs_review"] = True
            player["confidence"] = 0.0
            player["duplicate_identity_slots"] = list(duplicate_slots)
            codes = set(player.get("validation_codes") or [])
            codes.add(code)
            player["validation_codes"] = sorted(codes)
    return [list(slots) for slots in duplicate_groups]


def project_ctt_review_extraction(
    plan: CttCropPlan, execution: CttProviderExecution
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Project canonical receipts to the current review DTO without writes."""
    header = {
        name: _field_by_name(execution, field_name=name) for name in HEADER_MAP.values()
    }
    players: List[Dict[str, Any]] = []
    for slot in range(1, 21):
        fields = {
            name: _field_by_name(execution, field_name=name, slot=slot)
            for name in CARD_MAP.values()
        }
        occupied = any(
            item is not None and bool(item.raw_text)
            for item in (
                fields["given_names"],
                fields["surnames"],
                fields["birth_date"],
            )
        )
        if not occupied:
            continue
        given = fields["given_names"]
        surnames = fields["surnames"]
        birth = fields["birth_date"]
        curp = fields["curp"]
        receipts = [item for item in fields.values() if item is not None]
        name = " ".join(
            part
            for part in (
                given.normalized_value if given else None,
                surnames.normalized_value if surnames else None,
            )
            if part
        ).strip()
        players.append(
            {
                "visible_player_number": slot,
                "continuous_player_number": slot,
                "source_page_number": 1 if slot <= 8 else 2,
                "name": name or None,
                "birth_date": birth.normalized_value if birth else None,
                "curp": curp.normalized_value if curp else None,
                "confidence": (
                    1.0
                    if receipts and not any(item.requires_review for item in receipts)
                    else 0.0
                ),
                "needs_review": not name
                or not birth
                or any(item.requires_review for item in receipts),
                "photo_region": None,
            }
        )
    duplicate_identity_groups = mark_duplicate_player_identities(players)
    team_review = any(
        item.requires_review for item in header.values() if item is not None
    )
    extraction = {
        "team": {
            "name": header["team_name"].normalized_value if header["team_name"] else "",
            "category": (
                header["category"].normalized_value if header["category"] else None
            ),
            "gender": header["gender"].normalized_value if header["gender"] else None,
            "league": header["league"].normalized_value if header["league"] else None,
            "municipality": (
                header["municipality"].normalized_value
                if header["municipality"]
                else None
            ),
            "state": header["state"].normalized_value if header["state"] else None,
            "confidence": 0.0 if team_review else 1.0,
        },
        "manager": {
            "name": (
                header["representative_name"].normalized_value
                if header["representative_name"]
                else ""
            ),
            "role": "delegado",
            "phone": None,
            "email": header["email"].normalized_value if header["email"] else None,
            "confidence": 0.0 if team_review else 1.0,
        },
        "responsables": [],
        "players": players,
        "is_front": True,
        "overall_confidence": (
            0.0 if team_review or any(p["needs_review"] for p in players) else 1.0
        ),
        "needs_review": team_review or any(p["needs_review"] for p in players),
        "notes": "CTT provider draft; no Players materialized before commit.",
        "duplicate_identity_groups": duplicate_identity_groups,
    }
    raw = {
        "provider_contract": "ctt.provider_field.v1",
        "mode": execution.mode.value,
        "document_sha256": plan.document_sha256,
        "normalized_page_sha256": list(plan.normalized_page_sha256),
        "provider_calls": execution.provider_calls,
        "cache_hits": execution.cache_hits,
        "shadow": [item.model_dump(mode="json") for item in execution.shadow],
        "field_receipts": {
            crop_id: field.model_dump(mode="json")
            for crop_id, field in execution.fields.items()
        },
    }
    raw["canonical_hash"] = hashlib.sha256(
        json.dumps(raw["field_receipts"], sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return extraction, raw


async def extract_ctt_with_providers(
    page_images: Sequence[Image.Image],
    layout: Mapping[str, Any],
    coordinator: CttOcrCoordinator,
    *,
    mode: CttOcrMode,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    plan = build_ctt_crop_plan(page_images, layout)
    execution = await coordinator.extract(plan.crops, mode=mode)
    return project_ctt_review_extraction(plan, execution)
