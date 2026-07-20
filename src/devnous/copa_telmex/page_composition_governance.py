"""Immutable, receipt-bound governance for page append and draft composition."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence
from uuid import UUID, uuid4

from .models import (
    RegistrationPageAppendAttempt,
    RegistrationPageAppendDecision,
    RegistrationReviewAsset,
    RegistrationReviewDraft,
)


PIPELINE_VERSION = "samchat-registration-page-composition-v1"
POLICY_VERSION = "1.0.0"
DECISION_TTL_SECONDS = 300


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def sha256_binding(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _normalized_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    if not text:
        return None
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return re.sub(r"[^\w]+", " ", without_marks.casefold()).strip() or None


def _team_identity(extraction: Mapping[str, Any]) -> dict[str, Optional[str]]:
    team = extraction.get("team") if isinstance(extraction.get("team"), Mapping) else {}
    return {
        "name": _normalized_text(team.get("name")),
        "category": _normalized_text(team.get("category")),
    }


def _player_identities(extraction: Mapping[str, Any]) -> list[dict[str, Optional[str]]]:
    identities = []
    for player in extraction.get("players") or []:
        player = player if isinstance(player, Mapping) else {}
        identities.append(
            {
                "curp": re.sub(r"\s+", "", str(player.get("curp") or "")).upper()
                or None,
                "name": _normalized_text(player.get("name")),
                "birth_date": _normalized_text(player.get("birth_date")),
            }
        )
    return identities


def _player_page_assignments(
    extraction: Mapping[str, Any], layout: Mapping[str, Any]
) -> list[int]:
    page_map = layout.get("player_page_map") if isinstance(layout, Mapping) else {}
    values = []
    for slot in range(1, len(extraction.get("players") or []) + 1):
        try:
            values.append(int((page_map or {}).get(str(slot)) or 0))
        except (TypeError, ValueError):
            values.append(0)
    return values


def _asset_value(asset: Any, key: str, default: Any = None) -> Any:
    return (
        asset.get(key, default)
        if isinstance(asset, Mapping)
        else getattr(asset, key, default)
    )


def _image_hash(asset: Any) -> str:
    digest = str(_asset_value(asset, "sha256") or "")
    return digest if digest.startswith("sha256:") else "sha256:" + digest


def existing_page_manifest(
    *,
    session_id: Any,
    base_draft: RegistrationReviewDraft,
    assets: Iterable[RegistrationReviewAsset],
) -> list[dict[str, Any]]:
    manifest = []
    for asset in sorted(assets, key=lambda item: int(item.page_index)):
        manifest.append(
            {
                "asset_id": str(asset.id),
                "session_id": str(session_id),
                "page_index": int(asset.page_index),
                "image_hash": _image_hash(asset),
                "width": int(asset.width or 0),
                "height": int(asset.height or 0),
                "source_base_draft_id": str(
                    asset.source_base_draft_id
                    or asset.admitted_draft_id
                    or base_draft.id
                ),
                "source_base_content_hash": (
                    asset.source_base_content_hash or base_draft.content_hash
                ),
                "source_ocr_run_ref": (
                    asset.source_ocr_run_ref or f"legacy-initial:{asset.id}"
                ),
                "admission_operation_id": (
                    asset.admission_operation_id or f"legacy-initial:{asset.id}"
                ),
            }
        )
    return manifest


def staged_page_manifest(
    *,
    session_id: Any,
    base_draft: RegistrationReviewDraft,
    page_append_request_id: Any,
    stored_assets: Sequence[Mapping[str, Any]],
) -> tuple[UUID, str, list[dict[str, Any]], list[dict[str, Any]]]:
    append_ocr_run_id = uuid4()
    operation_id = sha256_binding(
        {
            "policy_version": POLICY_VERSION,
            "session_id": str(session_id),
            "base_draft_id": str(base_draft.id),
            "base_content_hash": base_draft.content_hash,
            "page_append_request_id": str(page_append_request_id),
        }
    )
    staged_rows = []
    manifest = []
    for stored in sorted(stored_assets, key=lambda item: int(item["page_index"])):
        asset_id = uuid4()
        row = {
            **dict(stored),
            "id": str(asset_id),
        }
        staged_rows.append(row)
        manifest.append(
            {
                "asset_id": str(asset_id),
                "session_id": str(session_id),
                "page_index": int(stored["page_index"]),
                "image_hash": _image_hash(stored),
                "width": int(stored.get("width") or 0),
                "height": int(stored.get("height") or 0),
                "source_base_draft_id": str(base_draft.id),
                "source_base_content_hash": base_draft.content_hash,
                "source_ocr_run_ref": str(append_ocr_run_id),
                "admission_operation_id": operation_id,
            }
        )
    return append_ocr_run_id, operation_id, staged_rows, manifest


def proposed_page_manifest(
    existing: Sequence[Mapping[str, Any]],
    appended: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [dict(item) for item in existing] + [dict(item) for item in appended]


def _model_identity(raw_payload: Mapping[str, Any], provider: str) -> dict[str, Any]:
    pages = []
    for page in raw_payload.get("pages") or []:
        raw = page.get("raw") if isinstance(page, Mapping) else {}
        raw = raw if isinstance(raw, Mapping) else {}
        backend = raw.get("backend") if isinstance(raw.get("backend"), Mapping) else {}
        pages.append(
            {
                "provider": str(
                    raw.get("provider")
                    or backend.get("provider")
                    or provider
                    or "unknown"
                ),
                "model": str(
                    raw.get("model")
                    or raw.get("model_name")
                    or backend.get("model")
                    or "unreported"
                ),
                "model_version": str(
                    raw.get("model_version")
                    or backend.get("model_version")
                    or "unreported"
                ),
            }
        )
    return {"pages": pages or [{"provider": provider or "unknown", "model": "unreported", "model_version": "unreported"}]}


def build_page_append_attempt(
    *,
    session_id: Any,
    page_append_request_id: Any,
    base_draft: RegistrationReviewDraft,
    provider: str,
    prompt_config_hash: str,
    append_ocr_run_id: UUID,
    operation_id: str,
    existing_manifest: Sequence[Mapping[str, Any]],
    appended_manifest: Sequence[Mapping[str, Any]],
    staged_assets: Sequence[Mapping[str, Any]],
    incoming_extraction: Mapping[str, Any],
    incoming_ocr_raw: Mapping[str, Any],
    incoming_layout_regions: Mapping[str, Any],
    proposed_values: Mapping[str, Any],
) -> RegistrationPageAppendAttempt:
    proposed_manifest = proposed_page_manifest(existing_manifest, appended_manifest)
    proposed_manifest_hash = sha256_binding(proposed_manifest)
    if proposed_values.get("page_manifest_hash") != proposed_manifest_hash:
        raise ValueError("proposed draft is not bound to the page manifest")
    base_extraction = base_draft.review_edits or base_draft.extraction or {}
    proposed_extraction = proposed_values.get("extraction") or {}
    base_players = _player_identities(base_extraction)
    incoming_players = _player_identities(incoming_extraction)
    proposed_players = _player_identities(proposed_extraction)
    return RegistrationPageAppendAttempt(
        id=uuid4(),
        session_id=session_id,
        page_append_request_id=page_append_request_id,
        base_draft_id=base_draft.id,
        base_draft_version=int(base_draft.draft_version),
        base_content_hash=base_draft.content_hash,
        declared_base_page_manifest_hash=base_draft.page_manifest_hash,
        operation_id=operation_id,
        append_ocr_run_id=append_ocr_run_id,
        pipeline_version=PIPELINE_VERSION,
        provider=str(provider or "unknown"),
        model_identity=_model_identity(incoming_ocr_raw, provider),
        prompt_config_hash=prompt_config_hash,
        existing_page_manifest=list(existing_manifest),
        existing_page_manifest_hash=sha256_binding(existing_manifest),
        appended_page_manifest=list(appended_manifest),
        appended_page_manifest_hash=sha256_binding(appended_manifest),
        proposed_page_manifest=proposed_manifest,
        proposed_page_manifest_hash=proposed_manifest_hash,
        proposed_snapshot_hash=str(proposed_values["content_hash"]),
        base_player_set_hash=sha256_binding(base_players),
        incoming_player_set_hash=sha256_binding(incoming_players),
        proposed_player_set_hash=sha256_binding(proposed_players),
        incoming_extraction=dict(incoming_extraction),
        incoming_ocr_raw=dict(incoming_ocr_raw),
        incoming_layout_regions=dict(incoming_layout_regions),
        proposed_extraction=proposed_extraction,
        proposed_ocr_raw=proposed_values.get("ocr_raw") or {},
        proposed_layout_regions=proposed_values.get("layout_regions") or {},
        proposed_validation=proposed_values.get("validation") or {},
        staged_assets=list(staged_assets),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def build_gate_request(
    *,
    tenant_id: str,
    tournament_slug: Optional[str],
    attempt: RegistrationPageAppendAttempt,
    current_draft: RegistrationReviewDraft,
    base_extraction: Mapping[str, Any],
    successor_draft_id: Any,
) -> dict[str, Any]:
    issued = attempt.created_at.replace(tzinfo=timezone.utc)
    expires = issued + timedelta(seconds=DECISION_TTL_SECONDS)
    return {
        "tenant_id": tenant_id,
        "session_id": str(attempt.session_id),
        "tournament_slug": str(tournament_slug or ""),
        "base_draft_id": str(attempt.base_draft_id),
        "base_draft_version": int(attempt.base_draft_version),
        "base_content_hash": attempt.base_content_hash,
        "declared_base_page_manifest_hash": (
            attempt.declared_base_page_manifest_hash
        ),
        "expected_current_draft_id": str(current_draft.id),
        "expected_current_version": int(current_draft.draft_version),
        "expected_current_content_hash": current_draft.content_hash,
        "page_append_request_id": str(attempt.page_append_request_id),
        "append_ocr_run_id": str(attempt.append_ocr_run_id),
        "operation_id": attempt.operation_id,
        "proposed_successor_draft_id": str(successor_draft_id),
        "proposed_snapshot_hash": attempt.proposed_snapshot_hash,
        "existing_page_manifest": attempt.existing_page_manifest,
        "existing_page_manifest_hash": attempt.existing_page_manifest_hash,
        "appended_page_manifest": attempt.appended_page_manifest,
        "appended_page_manifest_hash": attempt.appended_page_manifest_hash,
        "proposed_page_manifest": attempt.proposed_page_manifest,
        "proposed_page_manifest_hash": attempt.proposed_page_manifest_hash,
        "base_player_set_hash": attempt.base_player_set_hash,
        "incoming_player_set_hash": attempt.incoming_player_set_hash,
        "proposed_player_set_hash": attempt.proposed_player_set_hash,
        "base_team_identity": _team_identity(base_extraction),
        "incoming_team_identity": _team_identity(attempt.incoming_extraction or {}),
        "base_player_identities": _player_identities(base_extraction),
        "incoming_player_identities": _player_identities(
            attempt.incoming_extraction or {}
        ),
        "base_player_page_assignments": _player_page_assignments(
            base_extraction,
            current_draft.layout_regions or {},
        ),
        "proposed_player_page_assignments": _player_page_assignments(
            attempt.proposed_extraction or {},
            attempt.proposed_layout_regions or {},
        ),
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }


def decision_row(
    *,
    attempt: RegistrationPageAppendAttempt,
    successor_draft_id: Any,
    response: Mapping[str, Any],
) -> RegistrationPageAppendDecision:
    event = response.get("page_composition_decision") or {}
    receipt = response.get("page_composition_receipt") or {}
    return RegistrationPageAppendDecision(
        page_append_attempt_id=attempt.id,
        successor_draft_id=(
            successor_draft_id
            if event.get("decision") == "ACCEPT_NON_CONFLICTING_PAGE_APPEND"
            else None
        ),
        decision_id=str(event["decision_id"]),
        policy_hash=str(event["policy_hash"]),
        decision=str(event["decision"]),
        reason_codes=list(event.get("reason_codes") or []),
        receipt_id=str(receipt["receipt_id"]),
        event_hash=str(receipt["event_hash"]),
        issued_at=datetime.fromisoformat(
            str(event["issued_at"]).replace("Z", "+00:00")
        ).replace(tzinfo=None),
        expires_at=datetime.fromisoformat(
            str(event["expires_at"]).replace("Z", "+00:00")
        ).replace(tzinfo=None),
    )


def parent_authorization(response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "decision": dict(response.get("page_composition_decision") or {}),
        "receipt": dict(response.get("page_composition_receipt") or {}),
    }


def admitted_asset_rows(
    *,
    attempt: RegistrationPageAppendAttempt,
    successor_draft_id: Any,
    response: Mapping[str, Any],
) -> list[RegistrationReviewAsset]:
    event = response.get("page_composition_decision") or {}
    receipt = response.get("page_composition_receipt") or {}
    return [
        RegistrationReviewAsset(
            id=UUID(str(item["id"])),
            session_id=attempt.session_id,
            page_index=int(item["page_index"]),
            image_path=str(item["image_path"]),
            sha256=str(item["sha256"]),
            width=int(item.get("width") or 0),
            height=int(item.get("height") or 0),
            page_append_attempt_id=attempt.id,
            admitted_draft_id=successor_draft_id,
            source_base_draft_id=attempt.base_draft_id,
            source_base_content_hash=attempt.base_content_hash,
            source_ocr_run_ref=str(attempt.append_ocr_run_id),
            admission_operation_id=attempt.operation_id,
            admission_decision_id=str(event["decision_id"]),
            admission_receipt_id=str(receipt["receipt_id"]),
        )
        for item in attempt.staged_assets or []
    ]
