"""Deterministic, append-only adjudication inputs for OCR reprocessing."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence
from uuid import uuid4

from .models import (
    RegistrationOcrFieldDiff,
    RegistrationOcrReprocessDecision,
    RegistrationOcrRun,
    RegistrationReviewDraft,
)


PIPELINE_VERSION = "samchat-registration-reprocess-v1"
NORMALIZATION_POLICY_VERSION = "samchat-registration-field-normalization-v1"
REPROCESS_POLICY_VERSION = "1.0.0"
REPROCESS_DECISION_TTL_SECONDS = 300
MATERIAL_BATCH_THRESHOLD = 3
MATERIAL_BATCH_RATIO_PERMILLE = 400

TEAM_FIELDS = ("name", "category", "gender", "league", "municipality", "state")
MANAGER_FIELDS = ("name", "role", "phone", "email")
PLAYER_FIELDS = ("name", "birth_date", "curp")
MATERIAL_CLASSIFICATIONS = {"MATERIAL_CHANGE", "VALUE_TO_EMPTY"}
REVIEW_CLASSIFICATIONS = MATERIAL_CLASSIFICATIONS | {"EVIDENCE_BINDING_CHANGED"}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def sha256_binding(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _asset_value(asset: Any, key: str, default: Any = None) -> Any:
    return asset.get(key, default) if isinstance(asset, Mapping) else getattr(asset, key, default)


def _page_bindings(assets: Iterable[Any]) -> list[dict[str, Any]]:
    values = []
    for asset in sorted(assets, key=lambda item: int(_asset_value(item, "page_index", 0))):
        digest = str(_asset_value(asset, "sha256") or "")
        if digest and not digest.startswith("sha256:"):
            digest = "sha256:" + digest
        values.append(
            {
                "page_index": int(_asset_value(asset, "page_index", 0)),
                "image_hash": digest,
                "width": int(_asset_value(asset, "width", 0) or 0),
                "height": int(_asset_value(asset, "height", 0) or 0),
            }
        )
    return values


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    return text or None


def _normalized_value(field_path: str, value: Any) -> Optional[str]:
    text = _clean_text(value)
    if text is None:
        return None
    if field_path.endswith(".curp"):
        return re.sub(r"\s+", "", text).upper()
    if field_path.endswith(".birth_date"):
        pieces = re.split(r"[/.\-]", text)
        if len(pieces) == 3 and all(piece.isdigit() for piece in pieces):
            first, second, third = pieces
            if len(first) == 4:
                return f"{int(first):04d}-{int(second):02d}-{int(third):02d}"
            if len(third) == 4:
                return f"{int(third):04d}-{int(second):02d}-{int(first):02d}"
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return re.sub(r"[^\w]+", " ", without_marks.casefold()).strip()


def _flatten_extraction(extraction: Mapping[str, Any]) -> dict[str, tuple[Any, Optional[int]]]:
    flattened: dict[str, tuple[Any, Optional[int]]] = {}
    team = extraction.get("team") if isinstance(extraction.get("team"), Mapping) else {}
    manager = (
        extraction.get("manager")
        if isinstance(extraction.get("manager"), Mapping)
        else {}
    )
    for field in TEAM_FIELDS:
        flattened[f"team.{field}"] = (team.get(field), None)
    for field in MANAGER_FIELDS:
        flattened[f"manager.{field}"] = (manager.get(field), None)
    for slot, player in enumerate(extraction.get("players") or [], 1):
        player = player if isinstance(player, Mapping) else {}
        for field in PLAYER_FIELDS:
            flattened[f"players.{slot}.{field}"] = (player.get(field), slot)
    return flattened


def _field_key(field_path: str) -> str:
    field = field_path.rsplit(".", 1)[-1]
    return "name" if field == "name" else field


def _source_page(
    field_path: str, player_slot: Optional[int], layout: Mapping[str, Any]
) -> int:
    if player_slot is None:
        return 1
    page_map = layout.get("player_page_map") if isinstance(layout, Mapping) else {}
    try:
        return int((page_map or {}).get(str(player_slot)) or 1)
    except (TypeError, ValueError):
        return 1


def _geometry(
    field_path: str,
    player_slot: Optional[int],
    source_page: int,
    layout: Mapping[str, Any],
) -> Optional[dict[str, int]]:
    if player_slot is None:
        return None
    pages = layout.get("pages") if isinstance(layout, Mapping) else {}
    for overlay in (pages or {}).get(str(source_page), []) or []:
        if (
            int(overlay.get("player_index") or 0) == player_slot
            and str(overlay.get("field_key") or "") == _field_key(field_path)
        ):
            return {
                key: int(overlay.get(key) or 0)
                for key in ("x", "y", "width", "height")
            }
    return None


def _evidence_binding(
    field_path: str,
    player_slot: Optional[int],
    layout: Mapping[str, Any],
    page_bindings: Sequence[Mapping[str, Any]],
) -> tuple[str, int]:
    page = _source_page(field_path, player_slot, layout)
    page_binding = next(
        (item for item in page_bindings if int(item.get("page_index") or 0) == page),
        {"page_index": page, "image_hash": None, "width": 0, "height": 0},
    )
    return (
        sha256_binding(
            {
                "field_path": field_path,
                "source_page": page,
                "source_image_hash": page_binding.get("image_hash"),
                "geometry": _geometry(field_path, player_slot, page, layout),
            }
        ),
        page,
    )


def _classification(
    field_path: str,
    previous: Any,
    proposed: Any,
    *,
    evidence_changed: bool,
) -> str:
    previous_text = _clean_text(previous)
    proposed_text = _clean_text(proposed)
    if previous_text is None and proposed_text is None:
        return "UNCHANGED"
    if previous_text is None:
        return "EMPTY_TO_VALUE"
    if proposed_text is None:
        return "VALUE_TO_EMPTY"
    if previous_text == proposed_text:
        return "EVIDENCE_BINDING_CHANGED" if evidence_changed else "UNCHANGED"
    if _normalized_value(field_path, previous_text) == _normalized_value(
        field_path, proposed_text
    ):
        return "NORMALIZATION_ONLY_CHANGE"
    return "MATERIAL_CHANGE"


def build_field_diffs(
    previous_extraction: Mapping[str, Any],
    proposed_extraction: Mapping[str, Any],
    *,
    assets: Sequence[Any],
    previous_layout: Mapping[str, Any],
    new_layout: Mapping[str, Any],
) -> list[dict[str, Any]]:
    previous = _flatten_extraction(previous_extraction)
    proposed = _flatten_extraction(proposed_extraction)
    page_bindings = _page_bindings(assets)
    diffs = []
    for field_path in sorted(set(previous) | set(proposed)):
        previous_value, previous_slot = previous.get(field_path, (None, None))
        proposed_value, proposed_slot = proposed.get(field_path, (None, None))
        player_slot = previous_slot or proposed_slot
        previous_evidence, previous_page = _evidence_binding(
            field_path, player_slot, previous_layout, page_bindings
        )
        new_evidence, new_page = _evidence_binding(
            field_path, player_slot, new_layout, page_bindings
        )
        evidence_changed = previous_evidence != new_evidence
        classification = _classification(
            field_path,
            previous_value,
            proposed_value,
            evidence_changed=evidence_changed,
        )
        source_page = new_page or previous_page
        diffs.append(
            {
                "field_path": field_path,
                "player_slot": player_slot,
                "source_page": source_page,
                "classification": classification,
                "previous_value": previous_value,
                "proposed_value": proposed_value,
                "previous_value_present": _clean_text(previous_value) is not None,
                "proposed_value_present": _clean_text(proposed_value) is not None,
                "previous_value_binding": sha256_binding(
                    {"field_path": field_path, "value": previous_value}
                ),
                "proposed_value_binding": sha256_binding(
                    {"field_path": field_path, "value": proposed_value}
                ),
                "previous_normalized_value_binding": sha256_binding(
                    {
                        "field_path": field_path,
                        "normalized_value": _normalized_value(
                            field_path, previous_value
                        ),
                        "normalization_policy": NORMALIZATION_POLICY_VERSION,
                    }
                ),
                "proposed_normalized_value_binding": sha256_binding(
                    {
                        "field_path": field_path,
                        "normalized_value": _normalized_value(
                            field_path, proposed_value
                        ),
                        "normalization_policy": NORMALIZATION_POLICY_VERSION,
                    }
                ),
                "previous_evidence_binding": previous_evidence,
                "new_evidence_binding": new_evidence,
                "evidence_binding_changed": evidence_changed,
                "requires_review": classification in REVIEW_CLASSIFICATIONS,
            }
        )
    return diffs


def _public_diff(diff: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: diff.get(key)
        for key in (
            "field_path",
            "player_slot",
            "source_page",
            "classification",
            "previous_value_present",
            "proposed_value_present",
            "previous_value_binding",
            "proposed_value_binding",
            "previous_normalized_value_binding",
            "proposed_normalized_value_binding",
            "previous_evidence_binding",
            "new_evidence_binding",
            "evidence_binding_changed",
            "requires_review",
        )
    }


def public_field_diffs(
    diffs: Iterable[RegistrationOcrFieldDiff],
) -> list[dict[str, Any]]:
    return [
        _public_diff(
            {
                key: getattr(diff, key)
                for key in (
                    "field_path",
                    "player_slot",
                    "source_page",
                    "classification",
                    "previous_value_present",
                    "proposed_value_present",
                    "previous_value_binding",
                    "proposed_value_binding",
                    "previous_normalized_value_binding",
                    "proposed_normalized_value_binding",
                    "previous_evidence_binding",
                    "new_evidence_binding",
                    "evidence_binding_changed",
                    "requires_review",
                )
            }
        )
        for diff in diffs
    ]


def _model_identity(raw_payload: Mapping[str, Any], provider: str) -> dict[str, Any]:
    identities = []
    for page in raw_payload.get("pages") or []:
        raw = page.get("raw") if isinstance(page, Mapping) else {}
        raw = raw if isinstance(raw, Mapping) else {}
        backend = raw.get("backend") if isinstance(raw.get("backend"), Mapping) else {}
        identities.append(
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
    if not identities:
        identities.append(
            {
                "provider": str(provider or "unknown"),
                "model": "unreported",
                "model_version": "unreported",
            }
        )
    return {"pages": identities}


def build_ocr_run(
    *,
    tenant_id: str,
    session_id: Any,
    reprocess_request_id: Any,
    base_draft: RegistrationReviewDraft,
    assets: Sequence[Any],
    proposed_values: Mapping[str, Any],
    provider: str,
    prompt_config_hash: str,
) -> tuple[RegistrationOcrRun, list[RegistrationOcrFieldDiff], list[dict[str, Any]]]:
    extraction = proposed_values.get("extraction") or {}
    layout = proposed_values.get("layout_regions") or {}
    raw_payload = proposed_values.get("ocr_raw") or {}
    previous_layout = base_draft.layout_regions or {}
    previous_extraction = base_draft.review_edits or base_draft.extraction or {}
    page_bindings = _page_bindings(assets)
    diffs = build_field_diffs(
        previous_extraction,
        extraction,
        assets=assets,
        previous_layout=previous_layout,
        new_layout=layout,
    )
    public_diffs = [_public_diff(diff) for diff in diffs]
    field_diff_set_hash = sha256_binding(public_diffs)
    previous_evidence_set_hash = sha256_binding(
        [item["previous_evidence_binding"] for item in public_diffs]
    )
    new_evidence_set_hash = sha256_binding(
        [item["new_evidence_binding"] for item in public_diffs]
    )
    input_page_set_hash = sha256_binding(page_bindings)
    geometry_binding_hash = sha256_binding(
        {
            "coordinate_frame": layout.get(
                "coordinate_frame", "source-image-pixels"
            ),
            "transformation_contract": layout.get(
                "transformation_contract",
                "page-native-no-hidden-transform-v1",
            ),
            "layout": layout,
        }
    )
    proposed_snapshot_hash = str(proposed_values["content_hash"])
    run_fingerprint = sha256_binding(
        {
            "pipeline_version": PIPELINE_VERSION,
            "model_identity": _model_identity(raw_payload, provider),
            "prompt_config_hash": prompt_config_hash,
            "input_page_set_hash": input_page_set_hash,
            "geometry_binding_hash": geometry_binding_hash,
            "base_draft_id": str(base_draft.id),
            "base_draft_version": int(base_draft.draft_version),
            "base_content_hash": base_draft.content_hash,
            "proposed_snapshot_hash": proposed_snapshot_hash,
            "field_diff_set_hash": field_diff_set_hash,
        }
    )
    operation_id = sha256_binding(
        {
            "tenant_id": tenant_id,
            "reprocess_request_id": str(reprocess_request_id),
            "base_draft_id": str(base_draft.id),
            "base_content_hash": base_draft.content_hash,
            "policy_version": REPROCESS_POLICY_VERSION,
        }
    )
    created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run = RegistrationOcrRun(
        id=uuid4(),
        session_id=session_id,
        base_draft_id=base_draft.id,
        base_draft_version=int(base_draft.draft_version),
        base_content_hash=base_draft.content_hash,
        reprocess_request_id=reprocess_request_id,
        operation_id=operation_id,
        run_fingerprint=run_fingerprint,
        pipeline_version=PIPELINE_VERSION,
        provider=str(provider or "unknown"),
        model_identity=_model_identity(raw_payload, provider),
        prompt_config_hash=prompt_config_hash,
        input_page_bindings=page_bindings,
        input_page_set_hash=input_page_set_hash,
        geometry_binding_hash=geometry_binding_hash,
        previous_evidence_set_hash=previous_evidence_set_hash,
        new_evidence_set_hash=new_evidence_set_hash,
        proposed_snapshot_hash=proposed_snapshot_hash,
        field_diff_set_hash=field_diff_set_hash,
        field_diff_count=len(diffs),
        material_change_count=sum(
            diff["classification"] in MATERIAL_CLASSIFICATIONS for diff in diffs
        ),
        proposed_extraction=extraction,
        proposed_ocr_raw=raw_payload,
        proposed_layout_regions=layout,
        proposed_validation=proposed_values.get("validation") or {},
        created_at=created_at,
    )
    rows = [
        RegistrationOcrFieldDiff(ocr_run_id=run.id, **diff) for diff in diffs
    ]
    return run, rows, public_diffs


def build_gate_request(
    *,
    tenant_id: str,
    run: RegistrationOcrRun,
    current_draft: RegistrationReviewDraft,
    public_diffs: Sequence[Mapping[str, Any]],
    successor_draft_id: Any,
    previous_ocr_run_id: Optional[Any] = None,
) -> dict[str, Any]:
    issued = run.created_at.replace(tzinfo=timezone.utc)
    expires = issued + timedelta(seconds=REPROCESS_DECISION_TTL_SECONDS)
    return {
        "tenant_id": tenant_id,
        "session_id": str(run.session_id),
        "base_draft_id": str(run.base_draft_id),
        "base_draft_version": int(run.base_draft_version),
        "base_content_hash": run.base_content_hash,
        "expected_current_draft_id": str(current_draft.id),
        "expected_current_version": int(current_draft.draft_version),
        "expected_current_content_hash": current_draft.content_hash,
        "proposed_successor_draft_id": str(successor_draft_id),
        "proposed_snapshot_hash": run.proposed_snapshot_hash,
        "previous_ocr_run_id": (
            str(previous_ocr_run_id) if previous_ocr_run_id else None
        ),
        "reprocess_request_id": str(run.reprocess_request_id),
        "new_ocr_run_id": str(run.id),
        "reprocess_operation_id": run.operation_id,
        "field_diffs": list(public_diffs),
        "field_diff_set_hash": run.field_diff_set_hash,
        "field_diff_count": int(run.field_diff_count),
        "material_change_count": int(run.material_change_count),
        "previous_evidence_set_hash": run.previous_evidence_set_hash,
        "new_evidence_set_hash": run.new_evidence_set_hash,
        "geometry_binding_hash": run.geometry_binding_hash,
        "input_page_set_hash": run.input_page_set_hash,
        "batch_threshold": MATERIAL_BATCH_THRESHOLD,
        "batch_ratio_permille": MATERIAL_BATCH_RATIO_PERMILLE,
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }


def decision_row(
    *,
    run: RegistrationOcrRun,
    successor_draft_id: Any,
    response: Mapping[str, Any],
) -> RegistrationOcrReprocessDecision:
    event = response.get("reprocess_decision") or {}
    receipt = response.get("reprocess_receipt") or {}
    return RegistrationOcrReprocessDecision(
        ocr_run_id=run.id,
        successor_draft_id=successor_draft_id
        if event.get("decision") == "ACCEPT_NON_CONFLICTING_REPROCESS"
        else None,
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
        "decision": dict(response.get("reprocess_decision") or {}),
        "receipt": dict(response.get("reprocess_receipt") or {}),
    }
