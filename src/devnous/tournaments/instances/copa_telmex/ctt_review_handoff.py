"""Persist a quarantined canonical CTT review bundle beside the legacy draft.

The handoff never replaces the operator-facing extraction or writes Team/Player
rows.  It stores the canonical draft and normalized photo previews under the
existing temporary review session so both pipelines can be compared safely.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from uuid import UUID

from PIL import Image
from sqlalchemy import select

from devnous.copa_telmex.models import RegistrationReviewDraft
from devnous.tournaments.core.ctt_canary import CttCanaryExecution
from devnous.tournaments.core.ctt_ocr_contract import (
    CttFieldObservation,
    CttRegistrationDraft,
    CttSlotDraft,
)
from devnous.tournaments.core.ocr_integrity import (
    clamp_box,
    normalize_ctt_template_image,
)

CANONICAL_REVIEW_SCHEMA = "ctt.canonical_review.v1"


def _observation_value(observation: CttFieldObservation) -> Optional[str]:
    return observation.normalized_value or (
        observation.raw_text.strip() if observation.raw_text else None
    )


def _slot_source(slot: CttSlotDraft) -> Tuple[int, int]:
    evidence = slot.fields.given_names.evidence
    return int(evidence.source_page), int(evidence.source_slot or slot.slot)


def _slot_name(slot: CttSlotDraft) -> str:
    values = (
        _observation_value(slot.fields.given_names),
        _observation_value(slot.fields.paternal_surname),
        _observation_value(slot.fields.maternal_surname),
    )
    return " ".join(value for value in values if value)


def _slot_confidence(slot: CttSlotDraft) -> float:
    observations = [
        observation
        for observation in slot.fields.observations()
        if observation.raw_text or observation.normalized_value
    ]
    if not observations:
        return 0.0
    return round(
        sum(float(observation.confidence) for observation in observations)
        / len(observations),
        4,
    )


def _slot_validation_codes(slot: CttSlotDraft) -> list[str]:
    return sorted(
        {code.value for code in slot.validation_codes}
        | {
            code.value
            for observation in slot.fields.observations()
            for code in observation.validation_codes
        }
    )


def _normalized_field_box(
    field: Mapping[str, Any], image_size: Tuple[int, int]
) -> Tuple[int, int, int, int]:
    width, height = image_size
    left = int(float(field.get("x") or 0.0) * width)
    top = int(float(field.get("y") or 0.0) * height)
    right = int((float(field.get("x") or 0.0) + float(field.get("w") or 0.0)) * width)
    bottom = int((float(field.get("y") or 0.0) + float(field.get("h") or 0.0)) * height)
    return clamp_box((left, top, right, bottom), image_size)


def canonical_photo_box(
    fields: Mapping[str, Mapping[str, Any]],
    image_size: Tuple[int, int],
    *,
    photo_extension_ratio: float,
    vertical_offset_ratio: float = 0.0,
) -> Tuple[int, int, int, int]:
    """Derive the printed photo rectangle from normalized template anchors."""
    boxes = [
        _normalized_field_box(field, image_size)
        for field in fields.values()
        if isinstance(field, Mapping)
    ]
    if not boxes:
        raise ValueError("CTT player card has no field coordinates")

    width, height = image_size
    text_left = min(box[0] for box in boxes)
    vertical_offset = int(height * vertical_offset_ratio)
    top = min(box[1] for box in boxes) - int(height * 0.005) + vertical_offset
    bottom = max(box[3] for box in boxes) + int(height * 0.005) + vertical_offset
    left = text_left - int(width * photo_extension_ratio)
    right = text_left - int(width * 0.012)
    return clamp_box((left, top, right, bottom), image_size)


def build_canonical_photo_crops(
    page_payloads: Sequence[bytes],
    layout: Mapping[str, Any],
    draft: CttRegistrationDraft,
) -> Dict[int, Tuple[Image.Image, Dict[str, Any]]]:
    """Crop occupied player photos after canonical page normalization."""
    normalized_pages: Dict[int, Image.Image] = {}
    try:
        for page_number, payload in enumerate(page_payloads, start=1):
            with Image.open(io.BytesIO(payload)) as source:
                normalized, _metadata = normalize_ctt_template_image(source)
            normalized_pages[page_number] = normalized.convert("RGB")

        pages = layout.get("pages") or {}
        crops: Dict[int, Tuple[Image.Image, Dict[str, Any]]] = {}
        for slot in draft.slots:
            if not slot.occupied:
                continue
            source_page, source_slot = _slot_source(slot)
            source_image = normalized_pages.get(source_page)
            if source_image is None:
                continue
            side = "front" if source_page == 1 else "back"
            page_layout = pages.get(side) or {}
            card_fields = (page_layout.get("cards") or {}).get(f"jugador_{source_slot}")
            if not isinstance(card_fields, Mapping):
                continue
            photo_extension_ratio = float(
                (page_layout.get("slot_crop") or {}).get("photo_extension_ratio", 0.17)
            )
            vertical_offset_ratio = float(
                (page_layout.get("slot_crop") or {}).get("vertical_offset_ratio", 0.0)
            )
            box = canonical_photo_box(
                card_fields,
                source_image.size,
                photo_extension_ratio=photo_extension_ratio,
                vertical_offset_ratio=vertical_offset_ratio,
            )
            crops[int(slot.slot)] = (
                source_image.crop(box),
                {
                    "source_page": source_page,
                    "source_slot": source_slot,
                    "normalized_box": {
                        "x": box[0],
                        "y": box[1],
                        "width": box[2] - box[0],
                        "height": box[3] - box[1],
                    },
                    "normalized_page_size": {
                        "width": source_image.width,
                        "height": source_image.height,
                    },
                },
            )
        return crops
    finally:
        for image in normalized_pages.values():
            image.close()


def build_canonical_review_payload(
    execution: CttCanaryExecution,
    preview_metadata: Mapping[int, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Translate the canonical contract into a quarantined comparison payload."""
    if execution.draft is None:
        raise ValueError("canonical review handoff requires a draft")
    draft = execution.draft
    team_fields = draft.team.fields
    players = []
    for slot in draft.slots:
        if not slot.occupied:
            continue
        source_page, source_slot = _slot_source(slot)
        players.append(
            {
                "slot": int(slot.slot),
                "page": int(slot.page),
                "source_page": source_page,
                "source_slot": source_slot,
                "name": _slot_name(slot),
                "birth_date": _observation_value(slot.fields.birth_date),
                "curp": _observation_value(slot.fields.curp),
                "confidence": _slot_confidence(slot),
                "requires_review": bool(slot.requires_review),
                "validation_codes": _slot_validation_codes(slot),
                "field_evidence": {
                    observation.field_name.value: observation.evidence.model_dump(
                        mode="json"
                    )
                    for observation in slot.fields.observations()
                },
                "photo_preview": dict(preview_metadata.get(int(slot.slot)) or {}),
            }
        )

    representative_name = _observation_value(team_fields.representative_name)
    representative_email = _observation_value(team_fields.email)
    return {
        "schema_version": CANONICAL_REVIEW_SCHEMA,
        "accepted": bool(execution.report.accepted),
        "authoritative": False,
        "canonical_hash": draft.canonical_hash(),
        "document_sha256": draft.document_sha256,
        "team": {
            "name": _observation_value(team_fields.name),
            "category": _observation_value(team_fields.category),
            "gender": _observation_value(team_fields.gender),
            "league": _observation_value(team_fields.league),
            "state": _observation_value(team_fields.state),
            "municipality": _observation_value(team_fields.municipality),
            "requires_review": bool(draft.team.requires_review),
            "validation_codes": [code.value for code in draft.team.validation_codes],
        },
        "manager": (
            {
                "name": representative_name,
                "email": representative_email,
                "requires_review": bool(
                    team_fields.representative_name.requires_review
                    or team_fields.email.requires_review
                ),
            }
            if representative_name or representative_email
            else None
        ),
        "players": players,
        "canonical_draft": draft.model_dump(mode="json"),
        "report": execution.report.model_dump(mode="json"),
    }


class CttCanonicalReviewSink:
    """Store a private comparison bundle without changing the legacy draft."""

    def __init__(self, *, session_maker: Any, photos_base_dir: Path) -> None:
        self.session_maker = session_maker
        self.photos_base_dir = Path(photos_base_dir)

    async def persist(
        self,
        review_session_id: str,
        execution: CttCanaryExecution,
        page_payloads: Sequence[bytes],
        layout: Mapping[str, Any],
    ) -> bool:
        if (
            execution.draft is None
            or not execution.report.accepted
            or execution.report.use_canonical_result
        ):
            return False

        async with self.session_maker() as session:
            result = await session.execute(
                select(RegistrationReviewDraft).where(
                    RegistrationReviewDraft.session_id == UUID(str(review_session_id))
                )
            )
            review_draft = result.scalar_one_or_none()
            if review_draft is None:
                return False

            preview_dir = (
                self.photos_base_dir
                / "review_sessions"
                / str(review_session_id)
                / "canonical_shadow"
            )
            preview_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(preview_dir, 0o700)

            crops = build_canonical_photo_crops(page_payloads, layout, execution.draft)
            preview_metadata: Dict[int, Dict[str, Any]] = {}
            expected_files = set()
            try:
                for slot, (crop, crop_metadata) in crops.items():
                    filename = f"player_{slot:02d}.jpg"
                    expected_files.add(filename)
                    final_path = preview_dir / filename
                    temporary_path = preview_dir / f".{filename}.tmp"
                    crop.save(temporary_path, format="JPEG", quality=94)
                    os.chmod(temporary_path, 0o600)
                    os.replace(temporary_path, final_path)
                    preview_metadata[slot] = {
                        **crop_metadata,
                        "relative_path": str(
                            final_path.relative_to(self.photos_base_dir)
                        ),
                    }
            finally:
                for crop, _metadata in crops.values():
                    crop.close()

            for stale_path in preview_dir.glob("player_*.jpg"):
                if stale_path.name not in expected_files:
                    stale_path.unlink()

            canonical_payload = build_canonical_review_payload(
                execution, preview_metadata
            )
            ocr_raw = dict(review_draft.ocr_raw or {})
            ocr_raw["canonical_shadow"] = canonical_payload

            validation = dict(review_draft.validation or {})
            audit = dict(validation.get("audit") or {})
            audit["canonical_shadow"] = {
                "schema_version": CANONICAL_REVIEW_SCHEMA,
                "accepted": True,
                "authoritative": False,
                "canonical_hash": execution.draft.canonical_hash(),
                "player_count": len(canonical_payload["players"]),
                "review_count": int(execution.report.review_count),
                "preview_count": len(preview_metadata),
            }
            validation["audit"] = audit

            review_draft.ocr_raw = ocr_raw
            review_draft.validation = validation
            await session.commit()
        return True
