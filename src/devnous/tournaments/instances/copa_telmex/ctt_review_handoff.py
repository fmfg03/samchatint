"""Adjudicate canonical CTT extraction as an immutable REG-S03 OCR run."""

from __future__ import annotations

import io
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from uuid import NAMESPACE_URL, UUID, uuid5

from PIL import Image
from sqlalchemy import select

from devnous.copa_telmex.draft_versioning import (
    append_draft_version,
    build_successor_values,
)
from devnous.copa_telmex.models import (
    RegistrationOcrRun,
    RegistrationReviewAsset,
    RegistrationReviewDraft,
    RegistrationReviewSession,
)
from devnous.copa_telmex.registration_governance import (
    RegistrationGovernanceClient,
    RegistrationGovernanceDenied,
)
from devnous.copa_telmex.reprocess_governance import (
    build_gate_request as build_reprocess_gate_request,
    build_ocr_run,
    decision_row as build_reprocess_decision_row,
    parent_authorization as reprocess_parent_authorization,
    sha256_binding,
)
from devnous.tournaments.core.ctt_canary import CttCanaryExecution
from devnous.tournaments.core.ctt_ocr_contract import (
    CttFieldObservation,
    CttRegistrationDraft,
    CttSlotDraft,
)
from devnous.tournaments.core.ctt_responses_extractor import (
    CTT_RESPONSES_PIPELINE_VERSION,
)
from devnous.tournaments.core.ctt_slot_montage import player_photo_box
from devnous.tournaments.core.ocr_integrity import (
    clamp_box,
    normalize_ctt_template_image,
)

CANONICAL_REVIEW_SCHEMA = "ctt.canonical_review.v1"
CANONICAL_COORDINATE_FRAME = "normalized-template-pixels"
CANONICAL_TRANSFORM_CONTRACT = "ctt-normalize-template-v1"
CANONICAL_PAGE_SIZE = (2550, 3300)


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


def _slot_has_identity(slot: CttSlotDraft) -> bool:
    return any(
        _observation_value(observation)
        for observation in slot.fields.observations()
    )


def _field_union_box(
    fields: Mapping[str, Mapping[str, Any]],
    keys: Sequence[str],
) -> Optional[dict[str, int]]:
    boxes = [
        _normalized_field_box(fields[key], CANONICAL_PAGE_SIZE)
        for key in keys
        if isinstance(fields.get(key), Mapping)
    ]
    if not boxes:
        return None
    return {
        "x": min(box[0] for box in boxes),
        "y": min(box[1] for box in boxes),
        "width": max(box[2] for box in boxes) - min(box[0] for box in boxes),
        "height": max(box[3] for box in boxes) - min(box[1] for box in boxes),
    }


def build_canonical_layout(
    layout: Mapping[str, Any], draft: CttRegistrationDraft
) -> Dict[str, Any]:
    """Bind every canonical player field and photo to normalized evidence."""
    pages: Dict[str, list[dict[str, Any]]] = {}
    page_map: Dict[str, int] = {}
    photo_bindings: Dict[str, Dict[str, Any]] = {}
    layout_pages = layout.get("pages") or {}
    for slot in draft.slots:
        source_page, source_slot = _slot_source(slot)
        side = "front" if source_page == 1 else "back"
        page_layout = layout_pages.get(side) or {}
        fields = (page_layout.get("cards") or {}).get(
            f"jugador_{source_slot}"
        ) or {}
        page_map[str(slot.slot)] = source_page
        for field_key, keys in (
            ("name", ("nombre", "apellidos")),
            ("birth_date", ("nacimiento",)),
            ("curp", ("curp",)),
        ):
            box = _field_union_box(fields, keys)
            if box is not None:
                pages.setdefault(str(source_page), []).append(
                    {
                        "player_index": int(slot.slot),
                        "field_key": field_key,
                        **box,
                    }
                )
        try:
            photo_box = player_photo_box(
                page_layout, source_slot, CANONICAL_PAGE_SIZE
            )
        except ValueError:
            continue
        photo_bindings[str(slot.slot)] = {
            "source_page": source_page,
            "source_slot": source_slot,
            "x": photo_box[0],
            "y": photo_box[1],
            "width": photo_box[2] - photo_box[0],
            "height": photo_box[3] - photo_box[1],
        }
    return {
        "coordinate_frame": CANONICAL_COORDINATE_FRAME,
        "transformation_contract": CANONICAL_TRANSFORM_CONTRACT,
        "normalized_page_size": {
            "width": CANONICAL_PAGE_SIZE[0],
            "height": CANONICAL_PAGE_SIZE[1],
        },
        "pages": pages,
        "player_page_map": page_map,
        "photo_bindings": photo_bindings,
    }


def build_canonical_proposed_extraction(
    base_draft: RegistrationReviewDraft,
    canonical_draft: CttRegistrationDraft,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Adapt canonical slots without adding a player from occupancy alone."""
    extraction = deepcopy(
        base_draft.review_edits or base_draft.extraction or {}
    )
    team = dict(extraction.get("team") or {})
    manager = dict(extraction.get("manager") or {})
    players = [dict(player or {}) for player in extraction.get("players") or []]
    team_fields = canonical_draft.team.fields
    team.update(
        {
            "name": _observation_value(team_fields.name),
            "category": _observation_value(team_fields.category),
            "gender": _observation_value(team_fields.gender),
            "league": _observation_value(team_fields.league),
            "state": _observation_value(team_fields.state),
            "municipality": _observation_value(team_fields.municipality),
        }
    )
    manager.update(
        {
            "name": _observation_value(team_fields.representative_name),
            "email": _observation_value(team_fields.email),
        }
    )

    by_slot = {int(slot.slot): slot for slot in canonical_draft.slots}
    occupied_identity_slots: list[int] = []
    occupied_without_identity: list[int] = []
    excluded_identity_slots: list[int] = []
    confidences: list[float] = []
    for slot_number, slot in sorted(by_slot.items()):
        has_identity = _slot_has_identity(slot)
        if slot.occupied and not has_identity:
            occupied_without_identity.append(slot_number)
        if has_identity:
            occupied_identity_slots.append(slot_number)
        if slot_number > len(players):
            if has_identity:
                excluded_identity_slots.append(slot_number)
            continue
        player = players[slot_number - 1]
        player.update(
            {
                "name": _slot_name(slot) or None,
                "birth_date": _observation_value(slot.fields.birth_date),
                "curp": _observation_value(slot.fields.curp),
                "confidence": _slot_confidence(slot),
                "needs_review": bool(slot.requires_review),
            }
        )
        if has_identity:
            confidences.append(_slot_confidence(slot))

    overall_confidence = (
        round(sum(confidences) / len(confidences), 4) if confidences else 0.0
    )
    extraction.update(
        {
            "team": team,
            "manager": manager,
            "players": players,
            "overall_confidence": overall_confidence,
        }
    )
    occupancy = {
        "base_player_count": len(players),
        "occupied_identity_slots": occupied_identity_slots,
        "occupied_without_identity": occupied_without_identity,
        "excluded_identity_slots": excluded_identity_slots,
        "auto_materialized_slots": [],
        "requires_review": bool(
            occupied_without_identity or excluded_identity_slots
        ),
    }
    return extraction, occupancy


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
            try:
                box = player_photo_box(
                    page_layout,
                    source_slot,
                    source_image.size,
                )
            except ValueError:
                continue
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
    team_field_evidence = {
        observation.field_name.value: observation.evidence.model_dump(mode="json")
        for observation in team_fields.observations()
    }
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
            "field_evidence": team_field_evidence,
        },
        "manager": (
            {
                "name": representative_name,
                "email": representative_email,
                "requires_review": bool(
                    team_fields.representative_name.requires_review
                    or team_fields.email.requires_review
                ),
                "field_evidence": {
                    key: team_field_evidence[key]
                    for key in ("representative_name", "email")
                },
            }
            if representative_name or representative_email
            else None
        ),
        "players": players,
        "canonical_draft": draft.model_dump(mode="json"),
        "report": execution.report.model_dump(mode="json"),
    }


class CttCanonicalReviewSink:
    """Persist and adjudicate one canonical run against an explicit base draft."""

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
            session_id = UUID(str(review_session_id))
            session_result = await session.execute(
                select(RegistrationReviewSession)
                .where(RegistrationReviewSession.id == session_id)
                .with_for_update()
            )
            review_session = session_result.scalar_one_or_none()
            if review_session is None:
                return False
            result = await session.execute(
                select(RegistrationReviewDraft)
                .where(RegistrationReviewDraft.session_id == session_id)
                .order_by(RegistrationReviewDraft.draft_version.desc())
                .limit(1)
            )
            review_draft = result.scalar_one_or_none()
            if review_draft is None:
                return False

            request_id = uuid5(
                NAMESPACE_URL,
                "|".join(
                    (
                        str(session_id),
                        str(review_draft.id),
                        str(review_draft.content_hash),
                        execution.draft.canonical_hash(),
                        CTT_RESPONSES_PIPELINE_VERSION,
                    )
                ),
            )
            existing_result = await session.execute(
                select(RegistrationOcrRun).where(
                    RegistrationOcrRun.reprocess_request_id == request_id
                )
            )
            if existing_result.scalar_one_or_none() is not None:
                return True

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
            model = execution.report.model or "unreported"
            ocr_raw = {
                "pages": [
                    {
                        "page_index": page_number,
                        "raw": {
                            "provider": "openai",
                            "model": model,
                            "model_version": "unreported",
                            "pipeline_version": CTT_RESPONSES_PIPELINE_VERSION,
                        },
                    }
                    for page_number in range(1, len(page_payloads) + 1)
                ],
                "canonical_run": canonical_payload,
            }

            extraction, occupancy = build_canonical_proposed_extraction(
                review_draft, execution.draft
            )
            canonical_layout = build_canonical_layout(layout, execution.draft)

            validation = dict(review_draft.validation or {})
            audit = dict(validation.get("audit") or {})
            audit["canonical_run"] = {
                "schema_version": CANONICAL_REVIEW_SCHEMA,
                "accepted": True,
                "authoritative_after_reg_s03": True,
                "canonical_hash": execution.draft.canonical_hash(),
                "player_count": len(canonical_payload["players"]),
                "review_count": int(execution.report.review_count),
                "preview_count": len(preview_metadata),
                "occupancy": occupancy,
            }
            validation["audit"] = audit
            validation["canonical_occupancy"] = occupancy
            validation["needs_review"] = bool(
                execution.draft.requires_review or occupancy["requires_review"]
            )

            proposed_values = build_successor_values(
                review_draft,
                ocr_raw=ocr_raw,
                extraction=extraction,
                review_edits=extraction,
                validation=validation,
                layout_regions=canonical_layout,
                overall_confidence=float(
                    extraction.get("overall_confidence") or 0.0
                ),
                needs_review=bool(validation["needs_review"]),
            )
            assets_result = await session.execute(
                select(RegistrationReviewAsset)
                .where(RegistrationReviewAsset.session_id == session_id)
                .order_by(RegistrationReviewAsset.page_index)
            )
            assets = list(assets_result.scalars().all())
            run, field_rows, public_diffs = build_ocr_run(
                tenant_id=os.getenv("ZAUBERN_TENANT_ID", "samchat-prod"),
                session_id=session_id,
                reprocess_request_id=request_id,
                base_draft=review_draft,
                assets=assets,
                proposed_values=proposed_values,
                provider="openai",
                prompt_config_hash=sha256_binding(
                    {
                        "pipeline_version": CTT_RESPONSES_PIPELINE_VERSION,
                        "model": model,
                        "layout_template": layout.get("template"),
                        "layout_hash": sha256_binding(layout),
                    }
                ),
            )
            successor_draft_id = uuid5(request_id, "successor")
            client = RegistrationGovernanceClient.from_environment()
            gate_response = await client.adjudicate_reprocess(
                build_reprocess_gate_request(
                    tenant_id=os.getenv("ZAUBERN_TENANT_ID", "samchat-prod"),
                    run=run,
                    current_draft=review_draft,
                    public_diffs=public_diffs,
                    successor_draft_id=successor_draft_id,
                )
            )
            event = gate_response.get("reprocess_decision") or {}
            receipt = gate_response.get("reprocess_receipt") or {}
            if (
                receipt.get("verified") is not True
                or event.get("decision")
                not in {
                    "ACCEPT_NON_CONFLICTING_REPROCESS",
                    "REQUIRE_FIELD_REVIEW",
                    "REQUIRE_ROSTER_REVIEW",
                    "DENY_REPROCESS_SUCCESSOR",
                }
                or not event.get("decision_id")
                or not receipt.get("receipt_id")
            ):
                raise RegistrationGovernanceDenied(
                    "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
                    "Zaubern returned an incomplete reprocess adjudication",
                )

            session.add(run)
            for field_row in field_rows:
                session.add(field_row)

            if gate_response.get("successor_authorized") is True:
                successor = await append_draft_version(
                    session,
                    review_session,
                    mutation_type="ocr_reprocessed",
                    actor_id="ctt-canary",
                    expected_draft=review_draft,
                    operation_id=run.operation_id,
                    new_draft_id=successor_draft_id,
                    parent_authorization=reprocess_parent_authorization(
                        gate_response
                    ),
                    governance_client=client,
                    ocr_raw=run.proposed_ocr_raw,
                    extraction=run.proposed_extraction,
                    review_edits=run.proposed_extraction,
                    validation=run.proposed_validation,
                    layout_regions=run.proposed_layout_regions,
                    overall_confidence=float(
                        run.proposed_extraction.get("overall_confidence") or 0.0
                    ),
                    needs_review=bool(
                        run.proposed_validation.get("needs_review")
                    ),
                )
                if successor.content_hash != run.proposed_snapshot_hash:
                    raise RegistrationGovernanceDenied(
                        "REPROCESS_SUCCESSOR_HASH_MISMATCH",
                        "REG-S02 successor does not match the adjudicated run",
                    )
            session.add(
                build_reprocess_decision_row(
                    run=run,
                    successor_draft_id=successor_draft_id,
                    response=gate_response,
                )
            )
            review_session.status = (
                "ready"
                if gate_response.get("successor_authorized") is True
                else "review"
            )
            await session.commit()
        return True
