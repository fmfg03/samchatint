"""Fail-closed client and evidence builder for Zaubern registration governance."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib import error, request as urlrequest

from PIL import Image


class RegistrationGovernanceDenied(RuntimeError):
    """Zaubern did not authorize the governed state transition."""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _digest_hex(value: str) -> str:
    return value[7:] if value.startswith("sha256:") else value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _asset_value(asset: Any, name: str, default: Any = None) -> Any:
    return (
        asset.get(name, default)
        if isinstance(asset, Mapping)
        else getattr(asset, name, default)
    )


def _overlay_for(
    layout_regions: Mapping[str, Any], player_slot: int, field_key: str, page_index: int
) -> Optional[Mapping[str, Any]]:
    pages = layout_regions.get("pages") or {}
    for overlay in pages.get(str(page_index), []) or []:
        if (
            int(overlay.get("player_index") or 0) == player_slot
            and overlay.get("field_key") == field_key
        ):
            return overlay
    return None


def _governance_incident_state(policy: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the deterministic, non-PII incident subset bound by Zaubern."""
    summary = policy.get("summary") or {}
    return {
        "schema_version": str(policy.get("schema_version") or "unknown"),
        "team_decision": str(policy.get("team_decision") or "unknown"),
        "summary": {
            key: int(summary.get(key) or 0)
            for key in (
                "cleared_players",
                "pending_nonblocking_players",
                "pending_blocking_players",
                "rejected_players",
                "eligible_players",
                "incident_count",
            )
        },
        "player_results": [
            {
                "player_index": int(result.get("player_index") or 0),
                "eligibility_status": str(
                    result.get("eligibility_status") or "unknown"
                ),
                "counts_toward_minimum": bool(result.get("counts_toward_minimum")),
                "incidents": [
                    {
                        "incident_type": str(
                            incident.get("incident_type") or "unknown"
                        ),
                        "blocks_player_eligibility": bool(
                            incident.get("blocks_player_eligibility")
                        ),
                        "blocks_team_registration": bool(
                            incident.get("blocks_team_registration")
                        ),
                    }
                    for incident in (result.get("incidents") or [])
                ],
            }
            for result in (policy.get("player_results") or [])
        ],
    }


def _derive_geometry(
    asset: Any, overlay: Optional[Mapping[str, Any]]
) -> Optional[Dict[str, Any]]:
    if not overlay:
        return None
    try:
        coordinates = {
            name: int(overlay[name]) for name in ("x", "y", "width", "height")
        }
        if coordinates["width"] <= 0 or coordinates["height"] <= 0:
            return None
        image_path = Path(str(_asset_value(asset, "image_path")))
        image_bytes = image_path.read_bytes()
        original_hash = _sha256_bytes(image_bytes)
        recorded_hash = str(_asset_value(asset, "sha256") or "")
        if recorded_hash and original_hash != f"sha256:{_digest_hex(recorded_hash)}":
            return None
        with Image.open(BytesIO(image_bytes)) as image:
            rgb = image.convert("RGB")
            x, y = coordinates["x"], coordinates["y"]
            right, bottom = x + coordinates["width"], y + coordinates["height"]
            if x < 0 or y < 0 or right > rgb.width or bottom > rgb.height:
                return None
            crop = rgb.crop((x, y, right, bottom))
            crop_payload = (
                _canonical({"mode": crop.mode, "size": list(crop.size)})
                + crop.tobytes()
            )
        identity_transform = {
            "operation": "identity",
            "source_hash": original_hash,
            "version": "samchat-identity-normalization-v1",
        }
        return {
            "original_image_hash": original_hash,
            "normalized_image_hash": original_hash,
            "normalization_version": "samchat-identity-normalization-v1",
            "normalization_transform_hash": _sha256_bytes(
                _canonical(identity_transform)
            ),
            "coordinate_frame": "ORIGINAL",
            "coordinate_frame_image_hash": original_hash,
            "field_coordinates": coordinates,
            "crop_derivation_version": "samchat-rgb-field-crop-v1",
            "crop_hash": _sha256_bytes(crop_payload),
            "effective_pixels": coordinates["width"] * coordinates["height"],
        }
    except (KeyError, OSError, TypeError, ValueError):
        return None


def build_preauthorization_request(
    *,
    tenant_id: str,
    draft_id: str,
    draft_version: int,
    team_id: str,
    tournament_slug: str,
    original_extraction: Mapping[str, Any],
    proposed_extraction: Mapping[str, Any],
    assets: Iterable[Any],
    layout_regions: Mapping[str, Any],
    incident_policy: Mapping[str, Any],
) -> Dict[str, Any]:
    asset_by_page = {
        int(_asset_value(asset, "page_index", 0)): asset for asset in assets
    }
    page_map = layout_regions.get("player_page_map") or {}
    original_players = list(original_extraction.get("players") or [])
    proposed_players = list(proposed_extraction.get("players") or [])
    blocked_slots = {
        int(result.get("player_index") or 0)
        for result in (incident_policy.get("player_results") or [])
        if any(
            bool(incident.get("blocks_player_eligibility"))
            for incident in (result.get("incidents") or [])
        )
    }
    fields = (
        ("full_name", "name", "name"),
        ("birth_date", "birth_date", "birth_date"),
        ("curp", "curp", "curp"),
    )
    candidates: List[Dict[str, Any]] = []
    for slot, proposed in enumerate(proposed_players, 1):
        if slot in blocked_slots:
            continue
        original = original_players[slot - 1] if slot <= len(original_players) else {}
        page_index = int(page_map.get(str(slot)) or 0)
        asset = asset_by_page.get(page_index)
        for governed_field, payload_key, overlay_key in fields:
            proposed_value = str(proposed.get(payload_key) or "").strip()
            if not proposed_value:
                continue
            previous_value = str(original.get(payload_key) or "").strip() or None
            overlay = _overlay_for(layout_regions, slot, overlay_key, page_index)
            candidates.append(
                {
                    "player_slot": slot,
                    "source_page": page_index,
                    "field": governed_field,
                    "previous_value": previous_value,
                    "proposed_value": proposed_value,
                    "secure_value_ref": f"samchat-dossier://{draft_id}/{draft_version}/{slot}/{governed_field}",
                    "candidate_sources": [
                        (
                            "operator_edit"
                            if previous_value not in (None, proposed_value)
                            else "individual_crop"
                        )
                    ],
                    "geometry": (
                        _derive_geometry(asset, overlay) if asset is not None else None
                    ),
                }
            )
    source_page_bindings = []
    for page, asset in sorted(asset_by_page.items()):
        digest = str(_asset_value(asset, "sha256") or "")
        source_page_bindings.append(
            f"sha256:{_digest_hex(digest)}"
            if digest
            else _sha256_bytes(
                Path(str(_asset_value(asset, "image_path"))).read_bytes()
            )
        )
    incident_state = _governance_incident_state(incident_policy)
    return {
        "tenant_id": tenant_id,
        "draft_id": draft_id,
        "draft_version": int(draft_version),
        "team_id": team_id,
        "tournament_slug": tournament_slug,
        "source_page_bindings": source_page_bindings,
        "incident_state_hash": _sha256_bytes(_canonical(incident_state)),
        "incident_state": incident_state,
        "field_candidates": candidates,
    }


@dataclass
class RegistrationGovernanceClient:
    gate_url: str
    timeout_seconds: float = 8.0

    @classmethod
    def from_environment(cls) -> "RegistrationGovernanceClient":
        gate_url = os.getenv("ZAUBERN_REGISTRATION_GATE_URL", "").strip()
        if not gate_url:
            raise RegistrationGovernanceDenied(
                "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
                "ZAUBERN_REGISTRATION_GATE_URL is not configured",
            )
        return cls(
            gate_url.rstrip("/"),
            float(os.getenv("ZAUBERN_REGISTRATION_GATE_TIMEOUT_SECONDS", "8")),
        )

    async def preauthorize(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._post, "/v1/preauthorize", payload)

    async def finalize(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._post, "/v1/finalize", payload)

    async def authorize_draft_version(
        self, payload: Mapping[str, Any]
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self._post, "/v1/draft-version", payload)

    def _post(self, path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        req = urlrequest.Request(
            self.gate_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
                detail = body.get("detail") or {}
            except (ValueError, UnicodeDecodeError):
                detail = {}
            raise RegistrationGovernanceDenied(
                str(detail.get("reason_code") or "EVIDENCE_WRITE_FAILED_FAIL_CLOSED"),
                str(detail.get("message") or "Zaubern rejected registration"),
            ) from exc
        except (error.URLError, TimeoutError, ValueError) as exc:
            raise RegistrationGovernanceDenied(
                "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
                "Zaubern gate unavailable or returned invalid data",
            ) from exc
        return result


def governed_player_row(player: Any) -> Dict[str, Any]:
    return {
        "player_id": str(player.id),
        "team_id": str(player.team_id),
        "player_slot": int(player.roster_index or 0),
        "first_name": player.first_name,
        "last_name": player.last_name,
        "birth_date": player.birth_date.isoformat() if player.birth_date else None,
        "curp": player.curp,
        "governance_state": player.governance_state,
        "roster_draft_binding": player.roster_draft_binding,
    }
