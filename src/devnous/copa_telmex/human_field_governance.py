"""REG-S05 exact field review, approval, evidence and consumption helpers."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from PIL import Image

from .models import (
    RegistrationHumanFieldApproval,
    RegistrationHumanFieldApprovalConsumption,
    RegistrationHumanFieldEditDecision,
    RegistrationHumanFieldEditExecution,
    RegistrationHumanFieldEditProposal,
)


PLAYER_FIELDS = ("name", "birth_date", "curp")
TEAM_FIELDS = ("name", "category", "gender", "league", "municipality", "state")
MANAGER_FIELDS = ("name", "role", "phone", "email")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def sha256_binding(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _binding_key() -> bytes:
    value = os.getenv("SAMCHAT_HUMAN_FIELD_BINDING_KEY", "").encode("utf-8")
    if len(value) < 32:
        raise ValueError("SAMCHAT_HUMAN_FIELD_BINDING_KEY is missing or too short")
    return value


def hmac_binding(value: Any) -> str:
    return "hmac-sha256:" + hmac.new(
        _binding_key(), canonical_bytes(value), hashlib.sha256
    ).hexdigest()


def _normalized_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    if not text:
        return None
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).casefold()


def normalized_value(field_path: str, value: Any) -> Optional[str]:
    if value is None:
        return None
    if field_path.endswith(".curp"):
        return re.sub(r"\s+", "", str(value)).upper() or None
    return _normalized_text(value)


def ensure_roster_entry_ids(
    extraction: Mapping[str, Any], session_id: UUID
) -> dict[str, Any]:
    """Preserve IDs and deterministically derive them for legacy snapshots."""
    result = copy.deepcopy(dict(extraction or {}))
    players = []
    for slot, raw in enumerate(result.get("players") or [], 1):
        player = dict(raw or {})
        roster_entry_id = str(player.get("roster_entry_id") or "").strip()
        try:
            stable_id = UUID(roster_entry_id)
        except (TypeError, ValueError):
            stable_id = uuid5(
                NAMESPACE_URL,
                f"samchat-registration-roster-entry:{session_id}:{slot}",
            )
        player["roster_entry_id"] = str(stable_id)
        players.append(player)
    result["players"] = players
    return result


def proposal_id_for(session_id: UUID, edit_request_id: UUID) -> UUID:
    return uuid5(
        NAMESPACE_URL, f"samchat-human-field-edit:{session_id}:{edit_request_id}"
    )


def _flatten(extraction: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    flattened: dict[str, dict[str, Any]] = {}
    team = extraction.get("team") if isinstance(extraction.get("team"), Mapping) else {}
    manager = (
        extraction.get("manager")
        if isinstance(extraction.get("manager"), Mapping)
        else {}
    )
    for field in TEAM_FIELDS:
        flattened[f"team.{field}"] = {
            "value": team.get(field),
            "player_slot": None,
            "roster_entry_id": None,
        }
    for field in MANAGER_FIELDS:
        flattened[f"manager.{field}"] = {
            "value": manager.get(field),
            "player_slot": None,
            "roster_entry_id": None,
        }
    flattened["notes"] = {
        "value": extraction.get("notes"),
        "player_slot": None,
        "roster_entry_id": None,
    }
    for slot, player in enumerate(extraction.get("players") or [], 1):
        player = player if isinstance(player, Mapping) else {}
        roster_entry_id = str(player["roster_entry_id"]).lower()
        for field in PLAYER_FIELDS:
            flattened[f"players.{roster_entry_id}.{field}"] = {
                "value": player.get(field),
                "player_slot": slot,
                "roster_entry_id": roster_entry_id,
            }
    return flattened


def _field_key(field_path: str) -> str:
    return field_path.rsplit(".", 1)[-1]


def _source_page(
    player_slot: Optional[int], layout_regions: Mapping[str, Any]
) -> int:
    if player_slot is None:
        return 1
    page_map = layout_regions.get("player_page_map") or {}
    try:
        return int(page_map.get(str(player_slot)) or 1)
    except (TypeError, ValueError):
        return 1


def _overlay(
    field_path: str,
    player_slot: Optional[int],
    source_page: int,
    layout_regions: Mapping[str, Any],
) -> Optional[dict[str, int]]:
    if player_slot is None:
        return None
    pages = layout_regions.get("pages") or {}
    for item in pages.get(str(source_page), []) or []:
        if (
            int(item.get("player_index") or 0) == player_slot
            and str(item.get("field_key") or "") == _field_key(field_path)
        ):
            return {
                name: int(item.get(name) or 0)
                for name in ("x", "y", "width", "height")
            }
    return None


def _asset_value(asset: Any, name: str, default: Any = None) -> Any:
    return asset.get(name, default) if isinstance(asset, Mapping) else getattr(
        asset, name, default
    )


def _evidence(
    *,
    field_path: str,
    player_slot: Optional[int],
    assets: Iterable[Any],
    layout_regions: Mapping[str, Any],
) -> dict[str, Any]:
    source_page = _source_page(player_slot, layout_regions)
    asset = next(
        (
            candidate
            for candidate in assets
            if int(_asset_value(candidate, "page_index", 0)) == source_page
        ),
        None,
    )
    if asset is None:
        return {
            "source_page_artifact_id": None,
            "source_page_hash": None,
            "normalized_page_hash": None,
            "coordinate_frame_hash": None,
            "crop_coordinates": None,
            "crop_hash": None,
        }
    recorded_hash = str(_asset_value(asset, "sha256") or "")
    source_hash = (
        recorded_hash
        if recorded_hash.startswith("sha256:")
        else f"sha256:{recorded_hash}"
    )
    geometry = _overlay(field_path, player_slot, source_page, layout_regions)
    width = int(_asset_value(asset, "width", 0) or 0)
    height = int(_asset_value(asset, "height", 0) or 0)
    coordinates = geometry or {
        "x": 0,
        "y": 0,
        "width": width,
        "height": height,
    }
    crop_hash = sha256_binding(
        {
            "source_page_hash": source_hash,
            "coordinates": coordinates,
            "derivation": "hash-and-coordinate-fallback-v1",
        }
    )
    try:
        image_payload = Path(str(_asset_value(asset, "image_path"))).read_bytes()
        actual_hash = "sha256:" + hashlib.sha256(image_payload).hexdigest()
        if source_hash == "sha256:" or actual_hash != source_hash:
            raise ValueError("source image hash mismatch")
        with Image.open(BytesIO(image_payload)) as image:
            rgb = image.convert("RGB")
            x = coordinates["x"]
            y = coordinates["y"]
            right = x + coordinates["width"]
            bottom = y + coordinates["height"]
            if (
                coordinates["width"] > 0
                and coordinates["height"] > 0
                and x >= 0
                and y >= 0
                and right <= rgb.width
                and bottom <= rgb.height
            ):
                crop = rgb.crop((x, y, right, bottom))
                crop_hash = "sha256:" + hashlib.sha256(
                    canonical_bytes({"mode": crop.mode, "size": list(crop.size)})
                    + crop.tobytes()
                ).hexdigest()
    except (OSError, TypeError, ValueError):
        pass
    return {
        "source_page_artifact_id": str(_asset_value(asset, "id")),
        "source_page_hash": source_hash,
        "normalized_page_hash": sha256_binding(
            {
                "source_page_hash": source_hash,
                "width": width,
                "height": height,
                "normalization": "samchat-page-identity-v1",
            }
        ),
        "coordinate_frame_hash": sha256_binding(
            {
                "frame": "ORIGINAL",
                "source_page_hash": source_hash,
                "width": width,
                "height": height,
            }
        ),
        "crop_coordinates": coordinates,
        "crop_hash": crop_hash,
    }


def _s03_path_to_field_path(
    path: str, base_extraction: Mapping[str, Any]
) -> str:
    parts = str(path).split(".")
    if len(parts) == 3 and parts[0] == "players":
        slot = int(parts[1])
        player = list(base_extraction.get("players") or [])[slot - 1]
        return f"players.{str(player['roster_entry_id']).lower()}.{parts[2]}"
    return str(path)


def build_resolution_set(
    *,
    tenant_id: str,
    session_id: UUID,
    proposal_id: UUID,
    base_extraction: Mapping[str, Any],
    proposed_extraction: Mapping[str, Any],
    assets: Sequence[Any],
    layout_regions: Mapping[str, Any],
    blocking_diffs: Sequence[Any],
    actor: Mapping[str, Any],
    issued_at: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    base_fields = _flatten(base_extraction)
    proposed_fields = _flatten(proposed_extraction)
    blocking_by_path = {
        _s03_path_to_field_path(str(diff.field_path), base_extraction): diff
        for diff in blocking_diffs
    }
    paths = sorted(
        {
            path
            for path in set(base_fields) | set(proposed_fields)
            if base_fields.get(path, {}).get("value")
            != proposed_fields.get(path, {}).get("value")
        }
        | set(blocking_by_path)
    )
    resolutions: list[dict[str, Any]] = []
    required_diff_ids: list[str] = []
    role = str(actor.get("role") or "").strip().lower()
    principal_id = str(actor.get("user_id") or "").strip()
    approver = {
        "principal_id": principal_id,
        "role": role,
        "role_current": True,
        "role_assignment_id": str(actor["role_assignment_id"]),
        "authorization_epoch": str(actor["authorization_epoch"]),
        "authentication_method": str(actor["authentication_method"]),
        "authentication_assurance_level": int(
            actor["authentication_assurance_level"]
        ),
        "auth_context_id": str(actor["auth_context_id"]),
    }
    expires_at = issued_at + timedelta(minutes=8)
    for path in paths:
        before = base_fields.get(path) or {
            "value": None,
            "player_slot": None,
            "roster_entry_id": None,
        }
        after = proposed_fields.get(path) or before
        diff = blocking_by_path.get(path)
        previous_value = before.get("value")
        proposed_value = after.get("value")
        previous_present = previous_value not in (None, "")
        proposed_present = proposed_value not in (None, "")
        candidate_value = diff.proposed_value if diff is not None else None
        candidate_present = bool(
            diff.proposed_value_present if diff is not None else False
        )
        if not proposed_present:
            resolution_type = "CLEAR_FIELD"
        elif diff is not None and (
            proposed_present == bool(diff.previous_value_present)
            and normalized_value(path, proposed_value)
            == normalized_value(path, diff.previous_value)
        ):
            resolution_type = "KEEP_PREVIOUS_VALUE"
        elif diff is not None and (
            proposed_present == candidate_present
            and normalized_value(path, proposed_value)
            == normalized_value(path, candidate_value)
        ):
            resolution_type = "ACCEPT_REPROCESS_CANDIDATE"
        else:
            resolution_type = "ENTER_CORRECTED_VALUE"
        evidence_class = (
            "S03_DIFF_RESOLUTION"
            if diff is not None
            else (
                "DIRECT_DOCUMENT_EVIDENCE_EDIT"
                if path.startswith("players.") or path in {"team.name", "manager.name"}
                else "ADMINISTRATIVE_METADATA_EDIT"
            )
        )
        evidence = (
            _evidence(
                field_path=path,
                player_slot=before.get("player_slot"),
                assets=assets,
                layout_regions=layout_regions,
            )
            if evidence_class != "ADMINISTRATIVE_METADATA_EDIT"
            else {
                "source_page_artifact_id": None,
                "source_page_hash": None,
                "normalized_page_hash": None,
                "coordinate_frame_hash": None,
                "crop_coordinates": None,
                "crop_hash": None,
            }
        )
        approval_id = uuid5(proposal_id, f"approval:{path}")
        nonce = base64.urlsafe_b64encode(
            hashlib.sha256(
                canonical_bytes(
                    {
                        "proposal_id": str(proposal_id),
                        "field_path": path,
                        "issued_at": issued_at.isoformat(),
                        "random": str(uuid4()),
                    }
                )
            ).digest()
        ).decode("ascii").rstrip("=")
        if diff is not None:
            required_diff_ids.append(str(diff.id))
        resolutions.append(
            {
                "approval_id": str(approval_id),
                "nonce": nonce,
                "roster_entry_id": before.get("roster_entry_id"),
                "player_slot": before.get("player_slot"),
                "field_path": path,
                "resolution_type": resolution_type,
                "evidence_class": evidence_class,
                "previous_value_present": previous_present,
                "previous_value": previous_value if previous_present else None,
                "previous_normalized_value": normalized_value(path, previous_value),
                "proposed_value_present": proposed_present,
                "proposed_value": proposed_value if proposed_present else None,
                "proposed_normalized_value": normalized_value(path, proposed_value),
                "ocr_candidate_value_present": candidate_present,
                "ocr_candidate_value": candidate_value if candidate_present else None,
                **evidence,
                "ocr_run_id": str(diff.ocr_run_id) if diff is not None else None,
                "reprocess_decision_id": (
                    str(diff.ocr_run.decision.id)
                    if diff is not None
                    and diff.ocr_run is not None
                    and diff.ocr_run.decision is not None
                    else None
                ),
                "field_diff_id": str(diff.id) if diff is not None else None,
                "classification": diff.classification if diff is not None else None,
                "approver": approver,
                "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
                "not_before": issued_at.isoformat().replace("+00:00", "Z"),
                "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
            }
        )
    return sorted(resolutions, key=lambda item: item["field_path"]), sorted(
        required_diff_ids
    )


def build_proposal(
    *,
    tenant_id: str,
    session_id: UUID,
    edit_request_id: UUID,
    base_draft: Any,
    tournament_slug: str,
    proposed_successor_draft_id: UUID,
    proposed_values: Mapping[str, Any],
    resolutions: Sequence[Mapping[str, Any]],
    required_blocking_diff_ids: Sequence[str],
    actor: Mapping[str, Any],
) -> RegistrationHumanFieldEditProposal:
    proposal_id = proposal_id_for(session_id, edit_request_id)
    canonical_resolutions = [dict(item) for item in resolutions]
    approval_set = sorted(
        [
            {"approval_id": item["approval_id"], "nonce": item["nonce"]}
            for item in canonical_resolutions
        ],
        key=lambda item: item["approval_id"],
    )
    operation_id = sha256_binding(
        {
            "workflow": "samchat_registration_human_field_edit_v1",
            "proposal_id": str(proposal_id),
            "base_draft_id": str(base_draft.id),
            "base_draft_hash": base_draft.content_hash,
            "proposed_successor_draft_id": str(proposed_successor_draft_id),
            "proposed_successor_hash": proposed_values["content_hash"],
        }
    )
    subject_binding = hmac_binding(
        {
            "tenant_id": tenant_id,
            "session_id": str(session_id),
            "team": (proposed_values.get("review_edits") or {}).get("team"),
        }
    )
    return RegistrationHumanFieldEditProposal(
        id=proposal_id,
        session_id=session_id,
        edit_request_id=edit_request_id,
        base_draft_id=base_draft.id,
        base_draft_version=int(base_draft.draft_version),
        base_draft_hash=base_draft.content_hash,
        proposed_successor_draft_id=proposed_successor_draft_id,
        proposed_successor_hash=str(proposed_values["content_hash"]),
        operation_id=operation_id,
        tournament_slug=tournament_slug,
        registration_subject_binding=subject_binding,
        proposed_values=dict(proposed_values),
        resolutions=canonical_resolutions,
        field_resolution_set_hash=sha256_binding(canonical_resolutions),
        required_blocking_diff_ids=list(required_blocking_diff_ids),
        required_blocking_diff_set_hash=sha256_binding(
            sorted(required_blocking_diff_ids)
        ),
        approval_set_hash=sha256_binding(approval_set),
        proposer_principal_id=str(actor["user_id"]),
        proposer_role=str(actor["role"]),
    )


def approval_rows(
    proposal: RegistrationHumanFieldEditProposal,
) -> list[RegistrationHumanFieldApproval]:
    rows = []
    for resolution in proposal.resolutions:
        previous_normalized = resolution.get("previous_normalized_value")
        proposed_normalized = resolution.get("proposed_normalized_value")
        field_path = str(resolution["field_path"])
        approver = resolution["approver"]
        rows.append(
            RegistrationHumanFieldApproval(
                id=UUID(str(resolution["approval_id"])),
                proposal_id=proposal.id,
                nonce=str(resolution["nonce"]),
                roster_entry_id=(
                    UUID(str(resolution["roster_entry_id"]))
                    if resolution.get("roster_entry_id")
                    else None
                ),
                player_slot=resolution.get("player_slot"),
                field_path=field_path,
                resolution_type=str(resolution["resolution_type"]),
                evidence_class=str(resolution["evidence_class"]),
                previous_value_binding=hmac_binding(
                    {
                        "field_path": field_path,
                        "value": resolution.get("previous_value"),
                        "present": resolution["previous_value_present"],
                    }
                ),
                previous_normalized_value_binding=hmac_binding(
                    {
                        "field_path": field_path,
                        "value": previous_normalized,
                        "present": resolution["previous_value_present"],
                    }
                ),
                proposed_value_binding=hmac_binding(
                    {
                        "field_path": field_path,
                        "value": resolution.get("proposed_value"),
                        "present": resolution["proposed_value_present"],
                    }
                ),
                proposed_normalized_value_binding=hmac_binding(
                    {
                        "field_path": field_path,
                        "value": proposed_normalized,
                        "present": resolution["proposed_value_present"],
                    }
                ),
                source_page_artifact_id=(
                    UUID(str(resolution["source_page_artifact_id"]))
                    if resolution.get("source_page_artifact_id")
                    else None
                ),
                source_page_hash=resolution.get("source_page_hash"),
                normalized_page_hash=resolution.get("normalized_page_hash"),
                coordinate_frame_hash=resolution.get("coordinate_frame_hash"),
                crop_coordinates=resolution.get("crop_coordinates"),
                crop_hash=resolution.get("crop_hash"),
                ocr_run_id=(
                    UUID(str(resolution["ocr_run_id"]))
                    if resolution.get("ocr_run_id")
                    else None
                ),
                reprocess_decision_id=(
                    UUID(str(resolution["reprocess_decision_id"]))
                    if resolution.get("reprocess_decision_id")
                    else None
                ),
                field_diff_id=(
                    UUID(str(resolution["field_diff_id"]))
                    if resolution.get("field_diff_id")
                    else None
                ),
                classification=resolution.get("classification"),
                approver_principal_id=str(approver["principal_id"]),
                approver_role=str(approver["role"]),
                role_assignment_id=str(approver["role_assignment_id"]),
                authorization_epoch=str(approver["authorization_epoch"]),
                authentication_method=str(approver["authentication_method"]),
                authentication_assurance_level=int(
                    approver["authentication_assurance_level"]
                ),
                auth_context_id=str(approver["auth_context_id"]),
                issued_at=datetime.fromisoformat(
                    str(resolution["issued_at"]).replace("Z", "+00:00")
                ).replace(tzinfo=None),
                not_before=datetime.fromisoformat(
                    str(resolution["not_before"]).replace("Z", "+00:00")
                ).replace(tzinfo=None),
                expires_at=datetime.fromisoformat(
                    str(resolution["expires_at"]).replace("Z", "+00:00")
                ).replace(tzinfo=None),
            )
        )
    return rows


def build_gate_request(
    *,
    tenant_id: str,
    proposal: RegistrationHumanFieldEditProposal,
    current_draft: Any,
    consuming_principal_id: str,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "session_id": str(proposal.session_id),
        "tournament_slug": proposal.tournament_slug,
        "registration_subject_binding": proposal.registration_subject_binding,
        "proposal_id": str(proposal.id),
        "edit_request_id": str(proposal.edit_request_id),
        "base_draft_id": str(proposal.base_draft_id),
        "base_draft_version": int(proposal.base_draft_version),
        "base_draft_hash": proposal.base_draft_hash,
        "expected_current_draft_id": str(current_draft.id),
        "expected_current_draft_version": int(current_draft.draft_version),
        "expected_current_draft_hash": current_draft.content_hash,
        "proposed_successor_draft_id": str(
            proposal.proposed_successor_draft_id
        ),
        "proposed_successor_hash": proposal.proposed_successor_hash,
        "field_resolution_set_hash": proposal.field_resolution_set_hash,
        "required_blocking_diff_ids": list(
            proposal.required_blocking_diff_ids
        ),
        "required_blocking_diff_set_hash": (
            proposal.required_blocking_diff_set_hash
        ),
        "approval_set_hash": proposal.approval_set_hash,
        "resolutions": list(proposal.resolutions),
        "consuming_principal_id": consuming_principal_id,
        "operation_id": proposal.operation_id,
    }


def decision_row(
    proposal: RegistrationHumanFieldEditProposal,
    gate_response: Mapping[str, Any],
) -> RegistrationHumanFieldEditDecision:
    decision = gate_response["human_field_edit_decision"]
    receipt = gate_response["human_field_edit_receipt"]
    return RegistrationHumanFieldEditDecision(
        proposal_id=proposal.id,
        decision_id=str(decision["decision_id"]),
        policy_hash=str(decision["policy_hash"]),
        decision=str(decision["decision"]),
        reason_codes=list(decision["reason_codes"]),
        receipt_id=str(receipt["receipt_id"]),
        receipt_alg=str(receipt["alg"]),
        event_hash=str(receipt["event_hash"]),
        decision_document=dict(decision),
        receipt_document=dict(receipt),
        issued_at=datetime.fromisoformat(
            str(decision["issued_at"]).replace("Z", "+00:00")
        ).replace(tzinfo=None),
        expires_at=datetime.fromisoformat(
            str(decision["expires_at"]).replace("Z", "+00:00")
        ).replace(tzinfo=None),
    )


def parent_authorization(gate_response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "decision": dict(gate_response["human_field_edit_decision"]),
        "receipt": dict(gate_response["human_field_edit_receipt"]),
    }


def execution_rows(
    *,
    proposal: RegistrationHumanFieldEditProposal,
    decision: RegistrationHumanFieldEditDecision,
    successor: Any,
    principal_id: str,
) -> tuple[
    RegistrationHumanFieldEditExecution,
    list[RegistrationHumanFieldApprovalConsumption],
]:
    execution_id = uuid4()
    execution = RegistrationHumanFieldEditExecution(
        id=execution_id,
        proposal_id=proposal.id,
        decision_id=decision.id,
        successor_draft_id=successor.id,
        successor_draft_version=int(successor.draft_version),
        successor_hash=successor.content_hash,
        parent_decision_id=decision.decision_id,
        parent_receipt_id=decision.receipt_id,
    )
    consumptions = [
        RegistrationHumanFieldApprovalConsumption(
            approval_id=approval.id,
            execution_id=execution_id,
            consumed_by_principal_id=principal_id,
            consumed_by_draft_version=int(successor.draft_version),
            consumed_by_successor_hash=successor.content_hash,
        )
        for approval in proposal.approvals
    ]
    return execution, consumptions
