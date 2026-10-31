"""Deterministic fail-closed gate for SamChat registration identity writes.

The public events produced here contain bindings only. Plain identity values are
accepted as transient inputs solely to compute tenant-scoped HMAC bindings.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


WORKFLOW_ID = "samchat_registration_identity_v1"
POLICY_ID = "samchat_registration_identity_policy_v1"
EFFECTIVE_SAL = "SAL-3"
FIELD_ORDER = ("full_name", "birth_date", "curp")
DRAFT_WORKFLOW_ID = "samchat_registration_draft_version_v1"
DRAFT_POLICY_ID = "samchat_registration_draft_append_only_policy_v1"
REPROCESS_WORKFLOW_ID = "samchat_registration_reprocess_v1"
REPROCESS_POLICY_ID = "samchat_registration_reprocess_policy_v1"
REPROCESS_BATCH_THRESHOLD = 3
REPROCESS_BATCH_RATIO_PERMILLE = 400
PAGE_COMPOSITION_WORKFLOW_ID = "samchat_registration_page_composition_v1"
PAGE_COMPOSITION_POLICY_ID = "samchat_registration_page_composition_policy_v1"
HUMAN_FIELD_EDIT_WORKFLOW_ID = "samchat_registration_human_field_edit_v1"
HUMAN_FIELD_EDIT_POLICY_ID = "samchat_registration_human_field_edit_policy_v1"
POSTCOMMIT_MUTATION_WORKFLOW_ID = "samchat_registration_postcommit_mutation_v1"
POSTCOMMIT_MUTATION_POLICY_ID = (
    "samchat_registration_postcommit_mutation_policy_v1"
)


class GateClosedError(RuntimeError):
    """The governed write cannot continue."""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def canonical_bytes(value: Any) -> bytes:
    """Return JCS-compatible bytes for this contract's integer/string domain.

    RFC 8785 number normalization is deliberately avoided by rejecting floats;
    all governed numeric inputs are integers. Contract keys are ASCII, so Python
    key ordering is identical to JCS UTF-16 ordering for this domain.
    """

    def reject_floats(node: Any) -> None:
        if isinstance(node, float):
            raise ValueError("floating-point values are outside the canonical domain")
        if isinstance(node, Mapping):
            for key, item in node.items():
                if not isinstance(key, str):
                    raise ValueError("canonical object keys must be strings")
                reject_floats(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                reject_floats(item)

    reject_floats(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_binding(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def hmac_binding(key: bytes, value: Any) -> str:
    return (
        "hmac-sha256:"
        + hmac.new(key, canonical_bytes(value), hashlib.sha256).hexdigest()
    )


def _without(document: Mapping[str, Any], *keys: str) -> Dict[str, Any]:
    return {key: value for key, value in document.items() if key not in keys}


def utc_timestamp(now: Optional[datetime] = None) -> str:
    value = now or datetime.now(timezone.utc)
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Policy:
    policy_version: str = "1.0.0"
    minimum_effective_pixels: int = 12000
    page_bulk_rewrite_threshold: int = 2
    batch_bulk_rewrite_threshold: int = 3

    def artifact(self) -> Dict[str, Any]:
        artifact: Dict[str, Any] = {
            "event_type": POLICY_ID,
            "policy_id": POLICY_ID,
            "policy_version": self.policy_version,
            "workflow_id": WORKFLOW_ID,
            "effective_sal": EFFECTIVE_SAL,
            "canonicalization": "RFC8785-JCS",
            "identity_binding_algorithm": "HMAC-SHA-256",
            "field_order": list(FIELD_ORDER),
            "minimum_effective_pixels": self.minimum_effective_pixels,
            "page_bulk_rewrite_threshold": self.page_bulk_rewrite_threshold,
            "batch_bulk_rewrite_threshold": self.batch_bulk_rewrite_threshold,
            "evidence_bus_required": True,
            "post_execution_attestation_required": True,
        }
        artifact["policy_hash"] = sha256_binding(artifact)
        return artifact


def _base_event(
    request: Mapping[str, Any], policy: Policy, now: Optional[datetime]
) -> Dict[str, Any]:
    policy_artifact = policy.artifact()
    return {
        "timestamp": utc_timestamp(now),
        "tenant_id": str(request["tenant_id"]),
        "workflow_id": WORKFLOW_ID,
        "effective_sal": EFFECTIVE_SAL,
        "policy_id": POLICY_ID,
        "policy_version": policy.policy_version,
        "policy_hash": policy_artifact["policy_hash"],
    }


def _valid_geometry(geometry: Any) -> bool:
    if not isinstance(geometry, Mapping):
        return False
    required = {
        "original_image_hash",
        "normalized_image_hash",
        "normalization_version",
        "normalization_transform_hash",
        "coordinate_frame",
        "coordinate_frame_image_hash",
        "field_coordinates",
        "crop_derivation_version",
        "crop_hash",
        "effective_pixels",
    }
    if not required.issubset(geometry):
        return False
    coordinates = geometry.get("field_coordinates")
    if not isinstance(coordinates, Mapping):
        return False
    try:
        dimensions_valid = all(
            int(coordinates[name]) >= (1 if name in {"width", "height"} else 0)
            for name in ("x", "y", "width", "height")
        )
        pixels_valid = int(geometry["effective_pixels"]) > 0
    except (KeyError, TypeError, ValueError):
        return False
    frame = geometry.get("coordinate_frame")
    expected_hash = (
        geometry.get("original_image_hash")
        if frame == "ORIGINAL"
        else geometry.get("normalized_image_hash")
    )
    return (
        dimensions_valid
        and pixels_valid
        and frame in {"ORIGINAL", "NORMALIZED"}
        and geometry.get("coordinate_frame_image_hash") == expected_hash
    )


def approval_matches(
    approval: Optional[Mapping[str, Any]],
    candidate: Mapping[str, Any],
    *,
    previous_binding: Optional[str],
    proposed_binding: str,
    policy_hash: str,
    now: Optional[datetime] = None,
) -> tuple[bool, str]:
    if not approval:
        return False, "HUMAN_APPROVAL_REQUIRED"
    if (
        approval.get("consumed_at") is not None
        or approval.get("one_time_use") is not True
    ):
        return False, "HUMAN_APPROVAL_ALREADY_USED"
    current = now or datetime.now(timezone.utc)
    expires = str(approval.get("expires_at") or "").replace("Z", "+00:00")
    try:
        if datetime.fromisoformat(expires) <= current.astimezone(timezone.utc):
            return False, "HUMAN_APPROVAL_EXPIRED"
    except ValueError:
        return False, "HUMAN_APPROVAL_EXPIRED"
    geometry = candidate.get("geometry") or {}
    scoped = {
        "tenant_id": candidate.get("tenant_id"),
        "draft_id": candidate.get("draft_id"),
        "draft_version": candidate.get("draft_version"),
        "player_slot": candidate.get("player_slot"),
        "field": candidate.get("field"),
        "previous_value_binding": previous_binding,
        "proposed_value_binding": proposed_binding,
        "original_image_hash": geometry.get("original_image_hash"),
        "normalized_image_hash": geometry.get("normalized_image_hash"),
        "crop_hash": geometry.get("crop_hash"),
        "policy_hash": policy_hash,
    }
    if any(approval.get(key) != value for key, value in scoped.items()):
        return False, "HUMAN_APPROVAL_SCOPE_MISMATCH"
    return True, "CANDIDATE_POLICY_SATISFIED"


def evaluate_field(
    candidate: Mapping[str, Any],
    *,
    identity_key: bytes,
    policy: Policy,
    consume_approval: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    field = str(candidate.get("field") or "")
    if field not in FIELD_ORDER:
        raise GateClosedError(
            "FIELD_EVIDENCE_MISSING", f"unsupported identity field: {field}"
        )
    proposed = candidate.get("proposed_value")
    previous = candidate.get("previous_value")
    proposed_binding = hmac_binding(identity_key, {"field": field, "value": proposed})
    previous_binding = (
        None
        if previous in (None, "")
        else hmac_binding(identity_key, {"field": field, "value": previous})
    )
    base = _base_event(candidate, policy, now)
    event: Dict[str, Any] = {
        **base,
        "event_type": "samchat_registration_field_decision_v1",
        "decision_id": sha256_binding(
            {
                "draft_id": candidate["draft_id"],
                "draft_version": candidate["draft_version"],
                "player_slot": candidate["player_slot"],
                "field": field,
                "proposed_value_binding": proposed_binding,
            }
        ),
        "draft_id": str(candidate["draft_id"]),
        "draft_version": int(candidate["draft_version"]),
        "player_slot": int(candidate["player_slot"]),
        "source_page": int(candidate["source_page"]),
        "field": field,
        "previous_value_binding": previous_binding,
        "proposed_value_binding": proposed_binding,
        "secure_value_ref": candidate.get("secure_value_ref"),
        "candidate_sources": list(candidate.get("candidate_sources") or []),
        "geometry": candidate.get("geometry"),
        "human_approval_id": None,
    }
    if (
        proposed in (None, "")
        or not event["candidate_sources"]
        or not event["secure_value_ref"]
    ):
        event.update(
            decision="DENY_MATERIALIZATION", reason_codes=["FIELD_EVIDENCE_MISSING"]
        )
    elif not _valid_geometry(event["geometry"]):
        event.update(
            decision="DENY_MATERIALIZATION",
            reason_codes=["GEOMETRY_PROVENANCE_MISSING_OR_INVALID"],
        )
    elif int(event["geometry"]["effective_pixels"]) < policy.minimum_effective_pixels:
        event.update(
            decision="REQUIRE_FIELD_REVIEW",
            reason_codes=["SOURCE_RESOLUTION_INSUFFICIENT"],
        )
    elif previous not in (None, "") and previous != proposed:
        approved, reason = approval_matches(
            candidate.get("human_approval"),
            candidate,
            previous_binding=previous_binding,
            proposed_binding=proposed_binding,
            policy_hash=base["policy_hash"],
            now=now,
        )
        if (
            approved
            and consume_approval is not None
            and consume_approval(candidate["human_approval"])
        ):
            event["human_approval_id"] = candidate["human_approval"]["approval_id"]
            event.update(
                decision="ACCEPT_CANDIDATE", reason_codes=["CANDIDATE_POLICY_SATISFIED"]
            )
        else:
            approval_reason = (
                reason
                if not approved
                else (
                    "HUMAN_APPROVAL_REQUIRED"
                    if consume_approval is None
                    else "HUMAN_APPROVAL_ALREADY_USED"
                )
            )
            event.update(
                decision="REQUIRE_FIELD_REVIEW",
                reason_codes=["MATERIAL_IDENTITY_REWRITE", approval_reason],
            )
    else:
        event.update(
            decision="ACCEPT_CANDIDATE", reason_codes=["CANDIDATE_POLICY_SATISFIED"]
        )
    event["input_hash"] = sha256_binding(
        _without(event, "decision", "reason_codes", "input_hash", "decision_hash")
    )
    event["decision_hash"] = sha256_binding(_without(event, "decision_hash"))
    return event


def evaluate_roster(
    request: Mapping[str, Any],
    field_decisions: Iterable[Mapping[str, Any]],
    *,
    identity_key: bytes,
    policy: Policy,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    decisions = sorted(
        field_decisions,
        key=lambda item: (
            int(item["player_slot"]),
            FIELD_ORDER.index(str(item["field"])),
        ),
    )
    if not decisions:
        raise GateClosedError(
            "FIELD_EVIDENCE_MISSING", "roster has no governed field decisions"
        )
    source_page_bindings = list(request.get("source_page_bindings") or [])
    if not source_page_bindings or any(
        not isinstance(binding, str)
        or not binding.startswith("sha256:")
        or len(binding) != 71
        for binding in source_page_bindings
    ):
        raise GateClosedError(
            "FIELD_EVIDENCE_MISSING", "source page bindings are missing or invalid"
        )
    if request.get("incident_state") is not None:
        computed_incident_hash = sha256_binding(request["incident_state"])
        if request.get("incident_state_hash") != computed_incident_hash:
            raise GateClosedError(
                "STALE_GOVERNANCE_DECISION", "incident state binding mismatch"
            )

    def geometry_matches_declared_page(item: Mapping[str, Any]) -> bool:
        page = int(item.get("source_page") or 0)
        if page < 1 or page > len(source_page_bindings):
            return False
        return (
            (item.get("geometry") or {}).get("original_image_hash")
            == source_page_bindings[page - 1]
        )

    if any(not geometry_matches_declared_page(item) for item in decisions):
        raise GateClosedError(
            "GEOMETRY_PROVENANCE_MISSING_OR_INVALID",
            "field geometry is not bound to a source page",
        )
    rewrite_by_page: Dict[int, int] = {}
    for decision in decisions:
        if "MATERIAL_IDENTITY_REWRITE" in decision.get("reason_codes", []):
            page = int(decision["source_page"])
            rewrite_by_page[page] = rewrite_by_page.get(page, 0) + 1
    rewrite_total = sum(rewrite_by_page.values())
    if any(item["decision"] == "DENY_MATERIALIZATION" for item in decisions):
        decision, reasons = "DENY_ROSTER_MATERIALIZATION", ["FIELD_EVIDENCE_MISSING"]
    elif rewrite_total >= policy.batch_bulk_rewrite_threshold:
        decision, reasons = "DENY_ROSTER_MATERIALIZATION", [
            "BATCH_BULK_IDENTITY_REWRITE"
        ]
    elif any(
        count >= policy.page_bulk_rewrite_threshold
        for count in rewrite_by_page.values()
    ):
        decision, reasons = "REQUIRE_ROSTER_REVIEW", ["PAGE_BULK_IDENTITY_REWRITE"]
    elif any(item["decision"] != "ACCEPT_CANDIDATE" for item in decisions):
        decision, reasons = "REQUIRE_ROSTER_REVIEW", ["HUMAN_APPROVAL_REQUIRED"]
    else:
        decision, reasons = "AUTHORIZE_PENDING_MATERIALIZATION", [
            "CANDIDATE_POLICY_SATISFIED"
        ]
    slots = sorted({int(item["player_slot"]) for item in decisions})
    binding_input = {
        "tournament_slug": str(request["tournament_slug"]),
        "team_id": str(request["team_id"]),
        "draft_id": str(request["draft_id"]),
        "draft_version": int(request["draft_version"]),
        "ordered_player_slots": slots,
        "ordered_field_decision_hashes": [
            str(item["decision_hash"]) for item in decisions
        ],
        "incident_state_hash": str(request["incident_state_hash"]),
        "source_page_bindings": source_page_bindings,
        "policy_hash": policy.artifact()["policy_hash"],
    }
    event = {
        **_base_event(request, policy, now),
        "event_type": "samchat_registration_roster_decision_v1",
        "decision_id": sha256_binding(binding_input),
        "draft_id": binding_input["draft_id"],
        "draft_version": binding_input["draft_version"],
        "team_id": binding_input["team_id"],
        "tournament_slug": binding_input["tournament_slug"],
        "ordered_player_slots": slots,
        "ordered_field_decision_hashes": binding_input["ordered_field_decision_hashes"],
        "source_page_bindings": binding_input["source_page_bindings"],
        "incident_state_hash": binding_input["incident_state_hash"],
        "roster_draft_binding": hmac_binding(identity_key, binding_input),
        "decision": decision,
        "reason_codes": reasons,
    }
    event["decision_hash"] = sha256_binding(_without(event, "decision_hash"))
    return event


def verify_evidence_receipt(
    receipt: Mapping[str, Any], event: Mapping[str, Any], key: bytes
) -> bool:
    required = {
        "receipt_type",
        "receipt_id",
        "event_hash",
        "event_type",
        "tenant_id",
        "accepted_at",
        "verified",
        "issuer",
        "kid",
        "alg",
        "signature",
    }
    if (
        not required.issubset(receipt)
        or receipt.get("receipt_type") != "EvidenceReceipt.v1"
        or receipt.get("verified") is not True
        or receipt.get("alg") not in {"HS256", "Ed25519"}
        or receipt.get("issuer") != "zaubern-evidence-bus"
        or not receipt.get("kid")
    ):
        return False
    try:
        accepted_at = datetime.fromisoformat(
            str(receipt["accepted_at"]).replace("Z", "+00:00")
        )
        age_seconds = abs(
            (
                datetime.now(timezone.utc) - accepted_at.astimezone(timezone.utc)
            ).total_seconds()
        )
        if age_seconds > 60:
            return False
    except (TypeError, ValueError):
        return False
    if (
        receipt.get("event_hash") != sha256_binding(event)
        or receipt.get("event_type") != event.get("event_type")
        or receipt.get("tenant_id") != event.get("tenant_id")
    ):
        return False
    unsigned = _without(receipt, "receipt_id", "signature")
    try:
        signature = base64.b64decode(str(receipt.get("signature")).encode("ascii"))
    except Exception:
        return False
    if receipt.get("alg") == "HS256":
        expected_signature = hmac.new(
            key, canonical_bytes(unsigned), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            return False
    else:
        try:
            configured = json.loads(
                os.getenv("EVIDENCE_RECEIPT_ED25519_PUBLIC_KEYS_JSON", "{}")
            )
            encoded_key = configured[str(receipt["kid"])]
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(str(encoded_key).encode("ascii"))
            )
            public_key.verify(signature, canonical_bytes(unsigned))
        except (InvalidSignature, KeyError, TypeError, ValueError):
            return False
        except Exception:
            return False
    expected_id = sha256_binding(_without(receipt, "receipt_id"))
    return hmac.compare_digest(str(receipt.get("receipt_id")), expected_id)


def authorize_draft_version(
    request: Mapping[str, Any],
    *,
    identity_key: Optional[bytes] = None,
    receipt_key: bytes,
    publish: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Authorize exactly one append-only successor and bind it to Evidence Bus."""

    def valid_sha256(value: Any) -> bool:
        return (
            isinstance(value, str)
            and value.startswith("sha256:")
            and len(value) == 71
        )

    previous_version = int(request.get("previous_draft_version", -1))
    new_version = int(request.get("new_draft_version", -1))
    previous_id = request.get("previous_draft_id")
    previous_hash = request.get("previous_content_hash")
    initial = previous_version == 0
    if new_version != previous_version + 1 or new_version < 1:
        raise GateClosedError(
            "STALE_DRAFT_VERSION", "draft version is not the immediate successor"
        )
    if initial:
        if previous_id is not None or previous_hash is not None:
            raise GateClosedError(
                "DRAFT_CHAIN_INVALID", "initial draft declared a predecessor"
            )
    elif not previous_id or not valid_sha256(previous_hash):
        raise GateClosedError(
            "DRAFT_CHAIN_INVALID", "successor is missing its predecessor binding"
        )
    if not valid_sha256(request.get("new_content_hash")):
        raise GateClosedError(
            "DRAFT_CONTENT_HASH_INVALID", "new draft content hash is invalid"
        )
    required_strings = (
        "tenant_id",
        "session_id",
        "new_draft_id",
        "mutation_type",
        "operation_id",
    )
    if any(not str(request.get(key) or "").strip() for key in required_strings):
        raise GateClosedError(
            "DRAFT_EVIDENCE_MISSING", "draft version binding is incomplete"
        )
    actor = request.get("actor_binding")
    if actor is not None and not valid_sha256(actor):
        raise GateClosedError(
            "DRAFT_ACTOR_BINDING_INVALID", "actor binding is invalid"
        )
    parent_authorization = request.get("parent_authorization") or {}
    if str(request.get("mutation_type")) == "ocr_reprocessed":
        parent_decision = parent_authorization.get("decision") or {}
        parent_receipt = parent_authorization.get("receipt") or {}
        if (
            parent_decision.get("event_type")
            != "samchat_registration_reprocess_decision_v1"
            or parent_decision.get("decision")
            != "ACCEPT_NON_CONFLICTING_REPROCESS"
            or parent_decision.get("base_draft_id") != str(previous_id)
            or int(parent_decision.get("base_draft_version", -1))
            != previous_version
            or parent_decision.get("base_content_hash") != previous_hash
            or parent_decision.get("proposed_successor_draft_id")
            != str(request["new_draft_id"])
            or parent_decision.get("proposed_snapshot_hash")
            != str(request["new_content_hash"])
            or not verify_evidence_receipt(
                parent_receipt, parent_decision, receipt_key
            )
        ):
            raise GateClosedError(
                "REPROCESS_AUTHORIZATION_INVALID",
                "OCR reprocess successor lacks a valid REG-S03 authorization",
            )
        try:
            expires_at = datetime.fromisoformat(
                str(parent_decision["expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GateClosedError(
                "REPROCESS_AUTHORIZATION_EXPIRED",
                "REG-S03 authorization expiry is invalid",
            ) from exc
        current = now or datetime.now(timezone.utc)
        if expires_at <= current.astimezone(timezone.utc):
            raise GateClosedError(
                "REPROCESS_AUTHORIZATION_EXPIRED",
                "REG-S03 authorization is no longer current",
            )
    if str(request.get("mutation_type")) == "pages_appended":
        parent_decision = parent_authorization.get("decision") or {}
        parent_receipt = parent_authorization.get("receipt") or {}
        if (
            parent_decision.get("event_type")
            != "samchat_registration_page_composition_decision_v1"
            or parent_decision.get("decision")
            != "ACCEPT_NON_CONFLICTING_PAGE_APPEND"
            or parent_decision.get("base_draft_id") != str(previous_id)
            or int(parent_decision.get("base_draft_version", -1))
            != previous_version
            or parent_decision.get("base_content_hash") != previous_hash
            or parent_decision.get("proposed_successor_draft_id")
            != str(request["new_draft_id"])
            or parent_decision.get("proposed_snapshot_hash")
            != str(request["new_content_hash"])
            or not verify_evidence_receipt(
                parent_receipt, parent_decision, receipt_key
            )
        ):
            raise GateClosedError(
                "PAGE_COMPOSITION_AUTHORIZATION_INVALID",
                "page append successor lacks a valid REG-S04 authorization",
            )
        try:
            expires_at = datetime.fromisoformat(
                str(parent_decision["expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GateClosedError(
                "PAGE_COMPOSITION_AUTHORIZATION_EXPIRED",
                "REG-S04 authorization expiry is invalid",
            ) from exc
        current = now or datetime.now(timezone.utc)
        if expires_at <= current.astimezone(timezone.utc):
            raise GateClosedError(
                "PAGE_COMPOSITION_AUTHORIZATION_EXPIRED",
                "REG-S04 authorization is no longer current",
            )
    if str(request.get("mutation_type")) == "human_field_edit":
        parent_decision = parent_authorization.get("decision") or {}
        parent_receipt = parent_authorization.get("receipt") or {}
        if (
            parent_decision.get("event_type")
            != "samchat_registration_human_field_edit_decision_v1"
            or parent_decision.get("decision")
            != "AUTHORIZE_FIELD_EDIT_SUCCESSOR"
            or parent_decision.get("base_draft_id") != str(previous_id)
            or int(parent_decision.get("base_draft_version", -1))
            != previous_version
            or parent_decision.get("base_draft_hash") != previous_hash
            or parent_decision.get("proposed_successor_draft_id")
            != str(request["new_draft_id"])
            or parent_decision.get("proposed_successor_hash")
            != str(request["new_content_hash"])
            or parent_decision.get("operation_id")
            != str(request["operation_id"])
            or parent_receipt.get("alg") != "Ed25519"
            or not verify_evidence_receipt(
                parent_receipt, parent_decision, receipt_key
            )
        ):
            raise GateClosedError(
                "HUMAN_FIELD_EDIT_AUTHORIZATION_INVALID",
                "human edit successor lacks a valid REG-S05 authorization",
            )
        try:
            expires_at = datetime.fromisoformat(
                str(parent_decision["expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GateClosedError(
                "HUMAN_FIELD_EDIT_AUTHORIZATION_EXPIRED",
                "REG-S05 authorization expiry is invalid",
            ) from exc
        current = now or datetime.now(timezone.utc)
        if expires_at <= current.astimezone(timezone.utc):
            raise GateClosedError(
                "HUMAN_FIELD_EDIT_AUTHORIZATION_EXPIRED",
                "REG-S05 authorization is no longer current",
            )

    policy = {
        "policy_id": DRAFT_POLICY_ID,
        "policy_version": "1.0.0",
        "workflow_id": DRAFT_WORKFLOW_ID,
        "append_only": True,
        "immediate_successor_required": True,
        "evidence_bus_required": True,
    }
    policy["policy_hash"] = sha256_binding(policy)
    binding = {
        "tenant_id": str(request["tenant_id"]),
        "session_id": str(request["session_id"]),
        "previous_draft_id": str(previous_id) if previous_id else None,
        "previous_draft_version": previous_version,
        "previous_content_hash": previous_hash,
        "new_draft_id": str(request["new_draft_id"]),
        "new_draft_version": new_version,
        "new_content_hash": str(request["new_content_hash"]),
        "mutation_type": str(request["mutation_type"]),
        "actor_binding": actor,
        "operation_id": str(request["operation_id"]),
        "parent_decision_id": (
            (parent_authorization.get("decision") or {}).get("decision_id")
        ),
        "parent_receipt_id": (
            (parent_authorization.get("receipt") or {}).get("receipt_id")
        ),
        "policy_hash": policy["policy_hash"],
    }
    event = {
        "timestamp": utc_timestamp(now),
        "tenant_id": binding["tenant_id"],
        "workflow_id": DRAFT_WORKFLOW_ID,
        "effective_sal": EFFECTIVE_SAL,
        "policy_id": DRAFT_POLICY_ID,
        "policy_version": policy["policy_version"],
        "policy_hash": policy["policy_hash"],
        "event_type": "samchat_registration_draft_version_decision_v1",
        "decision_id": sha256_binding(binding),
        **{key: value for key, value in binding.items() if key != "policy_hash"},
        "decision": "AUTHORIZE_DRAFT_VERSION",
        "reason_codes": ["APPEND_ONLY_SUCCESSOR_VALID"],
    }
    event["decision_hash"] = sha256_binding(_without(event, "decision_hash"))
    try:
        receipt = publish(event)
    except Exception as exc:
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED", "Evidence Bus unavailable"
        ) from exc
    if not verify_evidence_receipt(receipt, event, receipt_key):
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED", "draft EvidenceReceipt rejected"
        )
    return {
        "authorized": True,
        "draft_decision": event,
        "draft_receipt": dict(receipt),
    }


def adjudicate_reprocess(
    request: Mapping[str, Any],
    *,
    identity_key: Optional[bytes] = None,
    receipt_key: bytes,
    publish: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Adjudicate a field-bound OCR diff without accepting model confidence."""

    def valid_sha256(value: Any) -> bool:
        return (
            isinstance(value, str)
            and value.startswith("sha256:")
            and len(value) == 71
        )

    hashes = (
        "base_content_hash",
        "expected_current_content_hash",
        "proposed_snapshot_hash",
        "field_diff_set_hash",
        "previous_evidence_set_hash",
        "new_evidence_set_hash",
        "geometry_binding_hash",
        "input_page_set_hash",
        "reprocess_operation_id",
    )
    if any(not valid_sha256(request.get(key)) for key in hashes):
        raise GateClosedError(
            "REPROCESS_BINDING_INVALID", "reprocess hash bindings are incomplete"
        )
    required_ids = (
        "tenant_id",
        "session_id",
        "base_draft_id",
        "expected_current_draft_id",
        "proposed_successor_draft_id",
        "reprocess_request_id",
        "new_ocr_run_id",
    )
    if any(not str(request.get(key) or "").strip() for key in required_ids):
        raise GateClosedError(
            "REPROCESS_BINDING_INVALID", "reprocess identity bindings are incomplete"
        )
    diffs = list(request.get("field_diffs") or [])
    if not diffs:
        raise GateClosedError(
            "REPROCESS_DIFF_INVALID", "field diff set cannot be empty"
        )
    if request.get("field_diff_set_hash") != sha256_binding(diffs):
        raise GateClosedError(
            "REPROCESS_DIFF_SET_CHANGED", "field diff set hash mismatch"
        )
    if int(request.get("field_diff_count", -1)) != len(diffs):
        raise GateClosedError(
            "REPROCESS_DIFF_SET_CHANGED", "field diff count mismatch"
        )
    allowed = {
        "UNCHANGED",
        "EMPTY_TO_VALUE",
        "VALUE_TO_EMPTY",
        "MATERIAL_CHANGE",
        "NORMALIZATION_ONLY_CHANGE",
        "EVIDENCE_BINDING_CHANGED",
    }
    required_diff_bindings = (
        "previous_value_binding",
        "proposed_value_binding",
        "previous_normalized_value_binding",
        "proposed_normalized_value_binding",
        "previous_evidence_binding",
        "new_evidence_binding",
    )
    if any(
        not str(item.get("field_path") or "").strip()
        or item.get("classification") not in allowed
        or any(not valid_sha256(item.get(key)) for key in required_diff_bindings)
        for item in diffs
    ):
        raise GateClosedError(
            "REPROCESS_DIFF_INVALID", "field diff contains an invalid binding"
        )
    field_paths = [str(item["field_path"]) for item in diffs]
    if len(field_paths) != len(set(field_paths)):
        raise GateClosedError(
            "REPROCESS_DIFF_INVALID", "field diff set contains duplicate fields"
        )
    derived_classifications = []
    for item in diffs:
        field_path = str(item["field_path"])
        previous_present = item.get("previous_value_present")
        proposed_present = item.get("proposed_value_present")
        if not isinstance(previous_present, bool) or not isinstance(
            proposed_present, bool
        ):
            raise GateClosedError(
                "REPROCESS_DIFF_INVALID",
                "field presence bindings must be boolean",
            )
        null_value_binding = sha256_binding(
            {"field_path": field_path, "value": None}
        )
        null_normalized_binding = sha256_binding(
            {
                "field_path": field_path,
                "normalized_value": None,
                "normalization_policy": "samchat-registration-field-normalization-v1",
            }
        )
        if (
            not previous_present
            and (
                item["previous_value_binding"] != null_value_binding
                or item["previous_normalized_value_binding"]
                != null_normalized_binding
            )
        ) or (
            not proposed_present
            and (
                item["proposed_value_binding"] != null_value_binding
                or item["proposed_normalized_value_binding"]
                != null_normalized_binding
            )
        ):
            raise GateClosedError(
                "REPROCESS_DIFF_CLASSIFICATION_INVALID",
                "empty-value declaration does not match canonical null bindings",
            )
        evidence_changed = (
            item["previous_evidence_binding"] != item["new_evidence_binding"]
        )
        if bool(item.get("evidence_binding_changed")) != evidence_changed:
            raise GateClosedError(
                "REPROCESS_DIFF_CLASSIFICATION_INVALID",
                "evidence change flag does not match evidence bindings",
            )
        if not previous_present and not proposed_present:
            derived = "EVIDENCE_BINDING_CHANGED" if evidence_changed else "UNCHANGED"
        elif not previous_present:
            derived = "EMPTY_TO_VALUE"
        elif not proposed_present:
            derived = "VALUE_TO_EMPTY"
        elif item["previous_value_binding"] == item["proposed_value_binding"]:
            derived = "EVIDENCE_BINDING_CHANGED" if evidence_changed else "UNCHANGED"
        elif (
            item["previous_normalized_value_binding"]
            == item["proposed_normalized_value_binding"]
        ):
            derived = "NORMALIZATION_ONLY_CHANGE"
        else:
            derived = "MATERIAL_CHANGE"
        if item.get("classification") != derived:
            raise GateClosedError(
                "REPROCESS_DIFF_CLASSIFICATION_INVALID",
                "declared classification does not match deterministic bindings",
            )
        expected_review = derived in {
            "MATERIAL_CHANGE",
            "VALUE_TO_EMPTY",
            "EVIDENCE_BINDING_CHANGED",
        }
        if bool(item.get("requires_review")) != expected_review:
            raise GateClosedError(
                "REPROCESS_DIFF_CLASSIFICATION_INVALID",
                "review flag does not match deterministic classification",
            )
        derived_classifications.append(derived)
    actual_material = sum(
        classification in {"MATERIAL_CHANGE", "VALUE_TO_EMPTY"}
        for classification in derived_classifications
    )
    if actual_material != int(request.get("material_change_count", -1)):
        raise GateClosedError(
            "REPROCESS_DIFF_SET_CHANGED", "material change count mismatch"
        )
    try:
        issued_at = datetime.fromisoformat(
            str(request["issued_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        expires_at = datetime.fromisoformat(
            str(request["expires_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise GateClosedError(
            "REPROCESS_AUTHORIZATION_TIME_INVALID",
            "reprocess authorization window is invalid",
        ) from exc
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires_at <= issued_at or (expires_at - issued_at).total_seconds() > 300:
        raise GateClosedError(
            "REPROCESS_AUTHORIZATION_TIME_INVALID",
            "reprocess authorization window exceeds policy",
        )

    if int(request.get("batch_threshold", -1)) != REPROCESS_BATCH_THRESHOLD or int(
        request.get("batch_ratio_permille", -1)
    ) != REPROCESS_BATCH_RATIO_PERMILLE:
        raise GateClosedError(
            "REPROCESS_POLICY_OVERRIDE_DENIED",
            "client cannot override reprocess batch policy",
        )
    policy = {
        "policy_id": REPROCESS_POLICY_ID,
        "policy_version": "1.0.0",
        "workflow_id": REPROCESS_WORKFLOW_ID,
        "normalization_policy": "samchat-registration-field-normalization-v1",
        "material_confidence_override_allowed": False,
        "batch_threshold": REPROCESS_BATCH_THRESHOLD,
        "batch_ratio_permille": REPROCESS_BATCH_RATIO_PERMILLE,
        "evidence_bus_required": True,
        "accepted_successor_requires_regs02": True,
    }
    policy["policy_hash"] = sha256_binding(policy)
    stale = (
        str(request.get("base_draft_id"))
        != str(request.get("expected_current_draft_id"))
        or int(request.get("base_draft_version", -1))
        != int(request.get("expected_current_version", -2))
        or request.get("base_content_hash")
        != request.get("expected_current_content_hash")
    )
    batch_threshold = int(policy["batch_threshold"])
    ratio_threshold = int(policy["batch_ratio_permille"])
    batch_material = actual_material >= batch_threshold or (
        bool(diffs) and actual_material * 1000 >= len(diffs) * ratio_threshold
    )
    evidence_changed = any(
        item.get("classification") == "EVIDENCE_BINDING_CHANGED"
        or bool(item.get("evidence_binding_changed"))
        for item in diffs
    )
    if current >= expires_at:
        decision = "DENY_REPROCESS_SUCCESSOR"
        reasons = ["REPROCESS_AUTHORIZATION_EXPIRED"]
    elif stale:
        decision = "DENY_REPROCESS_SUCCESSOR"
        reasons = ["STALE_REPROCESS_BASE"]
    elif batch_material:
        decision = "REQUIRE_ROSTER_REVIEW"
        reasons = ["BATCH_MATERIAL_IDENTITY_CHANGE"]
    elif actual_material:
        decision = "REQUIRE_FIELD_REVIEW"
        reasons = ["MATERIAL_IDENTITY_CHANGE"]
    elif evidence_changed:
        decision = "REQUIRE_FIELD_REVIEW"
        reasons = ["EVIDENCE_BINDING_CHANGED"]
    else:
        decision = "ACCEPT_NON_CONFLICTING_REPROCESS"
        reasons = ["NO_MATERIAL_CONTRADICTION"]

    binding = {
        key: request.get(key)
        for key in (
            "tenant_id",
            "session_id",
            "base_draft_id",
            "base_draft_version",
            "base_content_hash",
            "expected_current_draft_id",
            "expected_current_version",
            "expected_current_content_hash",
            "proposed_successor_draft_id",
            "proposed_snapshot_hash",
            "previous_ocr_run_id",
            "reprocess_request_id",
            "new_ocr_run_id",
            "reprocess_operation_id",
            "field_diff_set_hash",
            "field_diff_count",
            "material_change_count",
            "previous_evidence_set_hash",
            "new_evidence_set_hash",
            "geometry_binding_hash",
            "input_page_set_hash",
            "issued_at",
            "expires_at",
        )
    }
    binding["policy_hash"] = policy["policy_hash"]
    binding["decision"] = decision
    binding["reason_codes"] = reasons
    event = {
        "timestamp": utc_timestamp(now),
        "issued_at": str(request["issued_at"]),
        "expires_at": str(request["expires_at"]),
        "tenant_id": str(request["tenant_id"]),
        "workflow_id": REPROCESS_WORKFLOW_ID,
        "effective_sal": EFFECTIVE_SAL,
        "policy_id": REPROCESS_POLICY_ID,
        "policy_version": policy["policy_version"],
        "policy_hash": policy["policy_hash"],
        "event_type": "samchat_registration_reprocess_decision_v1",
        "decision_id": sha256_binding(binding),
        **{
            key: value
            for key, value in binding.items()
            if key not in {"policy_hash", "decision", "reason_codes", "issued_at", "expires_at"}
        },
        "decision": decision,
        "reason_codes": reasons,
    }
    event["decision_hash"] = sha256_binding(_without(event, "decision_hash"))
    try:
        receipt = publish(event)
    except Exception as exc:
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED", "Evidence Bus unavailable"
        ) from exc
    if not verify_evidence_receipt(receipt, event, receipt_key):
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
            "reprocess EvidenceReceipt rejected",
        )
    return {
        "successor_authorized": decision == "ACCEPT_NON_CONFLICTING_REPROCESS",
        "reprocess_decision": event,
        "reprocess_receipt": dict(receipt),
    }


def adjudicate_page_composition(
    request: Mapping[str, Any],
    *,
    identity_key: bytes,
    receipt_key: bytes,
    publish: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Adjudicate an exact append-only page manifest and roster composition."""

    def valid_sha256(value: Any) -> bool:
        return (
            isinstance(value, str)
            and value.startswith("sha256:")
            and len(value) == 71
        )

    def normalized_text(value: Any) -> Optional[str]:
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

    def normalized_team(value: Any) -> Dict[str, Optional[str]]:
        value = value if isinstance(value, Mapping) else {}
        return {
            "name": normalized_text(value.get("name")),
            "category": normalized_text(value.get("category")),
        }

    def normalized_player(value: Any) -> Dict[str, Optional[str]]:
        value = value if isinstance(value, Mapping) else {}
        return {
            "curp": re.sub(r"\s+", "", str(value.get("curp") or "")).upper()
            or None,
            "name": normalized_text(value.get("name")),
            "birth_date": normalized_text(value.get("birth_date")),
        }

    def identity_binding(kind: str, value: Any) -> str:
        return hmac_binding(
            identity_key,
            {
                "tenant_id": str(request["tenant_id"]),
                "kind": kind,
                "value": value,
            },
        )

    hash_fields = (
        "base_content_hash",
        "expected_current_content_hash",
        "operation_id",
        "proposed_snapshot_hash",
        "existing_page_manifest_hash",
        "appended_page_manifest_hash",
        "proposed_page_manifest_hash",
        "base_player_set_hash",
        "incoming_player_set_hash",
        "proposed_player_set_hash",
    )
    if any(not valid_sha256(request.get(key)) for key in hash_fields):
        raise GateClosedError(
            "PAGE_COMPOSITION_BINDING_INVALID",
            "page composition hash bindings are incomplete",
        )
    declared_manifest_hash = request.get("declared_base_page_manifest_hash")
    if declared_manifest_hash is not None and not valid_sha256(
        declared_manifest_hash
    ):
        raise GateClosedError(
            "PAGE_COMPOSITION_BINDING_INVALID",
            "declared base page manifest hash is invalid",
        )
    required_ids = (
        "tenant_id",
        "session_id",
        "base_draft_id",
        "expected_current_draft_id",
        "page_append_request_id",
        "append_ocr_run_id",
        "proposed_successor_draft_id",
    )
    if any(not str(request.get(key) or "").strip() for key in required_ids):
        raise GateClosedError(
            "PAGE_COMPOSITION_BINDING_INVALID",
            "page composition identity bindings are incomplete",
        )

    existing = list(request.get("existing_page_manifest") or [])
    appended = list(request.get("appended_page_manifest") or [])
    proposed = list(request.get("proposed_page_manifest") or [])
    if not appended:
        raise GateClosedError(
            "PAGE_COMPOSITION_EMPTY_APPEND", "at least one appended page is required"
        )
    if (
        request["existing_page_manifest_hash"] != sha256_binding(existing)
        or request["appended_page_manifest_hash"] != sha256_binding(appended)
        or request["proposed_page_manifest_hash"] != sha256_binding(proposed)
    ):
        raise GateClosedError(
            "PAGE_COMPOSITION_MANIFEST_CHANGED",
            "page manifest hash mismatch",
        )
    if proposed != existing + appended:
        raise GateClosedError(
            "PAGE_COMPOSITION_REPLACEMENT_ATTEMPT",
            "proposed manifest is not the exact append-only composition",
        )
    if declared_manifest_hash is not None and declared_manifest_hash != request[
        "existing_page_manifest_hash"
    ]:
        raise GateClosedError(
            "MIXED_BASE_PAGE_MANIFEST",
            "current draft page manifest does not match the existing pages",
        )

    required_page_keys = {
        "asset_id",
        "session_id",
        "page_index",
        "image_hash",
        "width",
        "height",
        "source_base_draft_id",
        "source_base_content_hash",
        "source_ocr_run_ref",
        "admission_operation_id",
    }
    if any(
        set(item) != required_page_keys
        or not valid_sha256(item.get("image_hash"))
        or not valid_sha256(item.get("source_base_content_hash"))
        or int(item.get("page_index") or 0) < 1
        or int(item.get("width") or 0) < 1
        or int(item.get("height") or 0) < 1
        or str(item.get("session_id")) != str(request["session_id"])
        for item in proposed
    ):
        raise GateClosedError(
            "PAGE_COMPOSITION_MANIFEST_INVALID",
            "page manifest contains invalid or cross-session entries",
        )
    proposed_indices = [int(item["page_index"]) for item in proposed]
    if proposed_indices != list(range(1, len(proposed) + 1)):
        raise GateClosedError(
            "PAGE_COMPOSITION_PAGE_ORDER_INVALID",
            "page indices must be contiguous and append-only",
        )
    existing_count = len(existing)
    if [int(item["page_index"]) for item in appended] != list(
        range(existing_count + 1, len(proposed) + 1)
    ):
        raise GateClosedError(
            "PAGE_COMPOSITION_PAGE_ORDER_INVALID",
            "appended pages do not follow the existing manifest",
        )
    if any(
        str(item["source_base_draft_id"]) != str(request["base_draft_id"])
        or item["source_base_content_hash"] != request["base_content_hash"]
        or str(item["source_ocr_run_ref"]) != str(request["append_ocr_run_id"])
        or item["admission_operation_id"] != request["operation_id"]
        for item in appended
    ):
        raise GateClosedError(
            "MIXED_APPEND_BASE",
            "appended pages are not bound to one base draft and OCR run",
        )
    asset_ids = [str(item["asset_id"]) for item in proposed]
    image_hashes = [str(item["image_hash"]) for item in proposed]
    duplicate_asset = len(asset_ids) != len(set(asset_ids))
    duplicate_image = len(image_hashes) != len(set(image_hashes))

    base_players = [
        normalized_player(item)
        for item in list(request.get("base_player_identities") or [])
    ]
    incoming_players = [
        normalized_player(item)
        for item in list(request.get("incoming_player_identities") or [])
    ]
    if (
        request["base_player_set_hash"] != sha256_binding(base_players)
        or request["incoming_player_set_hash"] != sha256_binding(incoming_players)
        or request["proposed_player_set_hash"]
        != sha256_binding(base_players + incoming_players)
    ):
        raise GateClosedError(
            "PAGE_COMPOSITION_ROSTER_CHANGED",
            "player identity sets do not match the append composition",
        )
    base_bindings = [
        identity_binding("player", player) for player in base_players
    ]
    incoming_bindings = [
        identity_binding("player", player) for player in incoming_players
    ]
    missing_player_identity = any(
        not any(player.values()) for player in incoming_players
    )
    duplicate_player = (
        len(incoming_bindings) != len(set(incoming_bindings))
        or bool(set(base_bindings) & set(incoming_bindings))
    )

    base_assignments = [
        int(value)
        for value in list(request.get("base_player_page_assignments") or [])
    ]
    proposed_assignments = [
        int(value)
        for value in list(request.get("proposed_player_page_assignments") or [])
    ]
    appended_indices = {int(item["page_index"]) for item in appended}
    layout_invalid = (
        len(base_assignments) != len(base_players)
        or len(proposed_assignments) != len(base_players) + len(incoming_players)
        or proposed_assignments[: len(base_players)] != base_assignments
        or any(
            page_index not in appended_indices
            for page_index in proposed_assignments[len(base_players) :]
        )
    )

    base_team = normalized_team(request.get("base_team_identity"))
    incoming_team = normalized_team(request.get("incoming_team_identity"))
    team_mismatch = any(
        incoming_team.get(field)
        and base_team.get(field)
        and incoming_team[field] != base_team[field]
        for field in ("name", "category")
    )
    base_team_binding = identity_binding("team", base_team)
    incoming_team_binding = identity_binding("team", incoming_team)

    try:
        issued_at = datetime.fromisoformat(
            str(request["issued_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        expires_at = datetime.fromisoformat(
            str(request["expires_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise GateClosedError(
            "PAGE_COMPOSITION_TIME_INVALID",
            "page composition authorization window is invalid",
        ) from exc
    if expires_at <= issued_at or (expires_at - issued_at).total_seconds() > 300:
        raise GateClosedError(
            "PAGE_COMPOSITION_TIME_INVALID",
            "page composition authorization window exceeds policy",
        )
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stale = (
        str(request["base_draft_id"])
        != str(request["expected_current_draft_id"])
        or int(request.get("base_draft_version", -1))
        != int(request.get("expected_current_version", -2))
        or request["base_content_hash"]
        != request["expected_current_content_hash"]
    )
    if current >= expires_at:
        decision = "DENY_PAGE_COMPOSITION"
        reasons = ["PAGE_COMPOSITION_AUTHORIZATION_EXPIRED"]
    elif stale:
        decision = "DENY_PAGE_COMPOSITION"
        reasons = ["STALE_PAGE_COMPOSITION_BASE"]
    elif duplicate_asset or duplicate_image:
        decision = "DENY_PAGE_COMPOSITION"
        reasons = ["DUPLICATE_PAGE_EVIDENCE"]
    elif team_mismatch:
        decision = "DENY_PAGE_COMPOSITION"
        reasons = ["CROSS_TEAM_PAGE_COMPOSITION"]
    elif duplicate_player:
        decision = "DENY_PAGE_COMPOSITION"
        reasons = ["DUPLICATE_PLAYER_COMPOSITION"]
    elif layout_invalid:
        decision = "DENY_PAGE_COMPOSITION"
        reasons = ["PLAYER_PAGE_BINDING_CHANGED"]
    elif not incoming_players or missing_player_identity:
        decision = "REQUIRE_PAGE_COMPOSITION_REVIEW"
        reasons = ["INCOMING_PAGE_IDENTITY_INCOMPLETE"]
    else:
        decision = "ACCEPT_NON_CONFLICTING_PAGE_APPEND"
        reasons = ["APPEND_ONLY_PAGE_COMPOSITION_VALID"]

    policy = {
        "policy_id": PAGE_COMPOSITION_POLICY_ID,
        "policy_version": "1.0.0",
        "workflow_id": PAGE_COMPOSITION_WORKFLOW_ID,
        "append_only_manifest": True,
        "duplicate_page_denied": True,
        "cross_team_composition_denied": True,
        "mixed_base_denied": True,
        "duplicate_player_denied": True,
        "evidence_bus_required": True,
        "accepted_successor_requires_regs02": True,
    }
    policy["policy_hash"] = sha256_binding(policy)
    binding = {
        key: request.get(key)
        for key in (
            "tenant_id",
            "session_id",
            "tournament_slug",
            "base_draft_id",
            "base_draft_version",
            "base_content_hash",
            "declared_base_page_manifest_hash",
            "expected_current_draft_id",
            "expected_current_version",
            "expected_current_content_hash",
            "page_append_request_id",
            "append_ocr_run_id",
            "operation_id",
            "proposed_successor_draft_id",
            "proposed_snapshot_hash",
            "existing_page_manifest_hash",
            "appended_page_manifest_hash",
            "proposed_page_manifest_hash",
            "base_player_set_hash",
            "incoming_player_set_hash",
            "proposed_player_set_hash",
            "issued_at",
            "expires_at",
        )
    }
    binding.update(
        {
            "base_team_binding": base_team_binding,
            "incoming_team_binding": incoming_team_binding,
            "base_player_binding_set_hash": sha256_binding(base_bindings),
            "incoming_player_binding_set_hash": sha256_binding(incoming_bindings),
            "base_player_page_assignment_hash": sha256_binding(base_assignments),
            "proposed_player_page_assignment_hash": sha256_binding(
                proposed_assignments
            ),
            "policy_hash": policy["policy_hash"],
            "decision": decision,
            "reason_codes": reasons,
        }
    )
    event = {
        "timestamp": utc_timestamp(now),
        "issued_at": str(request["issued_at"]),
        "expires_at": str(request["expires_at"]),
        "tenant_id": str(request["tenant_id"]),
        "workflow_id": PAGE_COMPOSITION_WORKFLOW_ID,
        "effective_sal": EFFECTIVE_SAL,
        "policy_id": PAGE_COMPOSITION_POLICY_ID,
        "policy_version": policy["policy_version"],
        "policy_hash": policy["policy_hash"],
        "event_type": "samchat_registration_page_composition_decision_v1",
        "decision_id": sha256_binding(binding),
        **{
            key: value
            for key, value in binding.items()
            if key
            not in {
                "policy_hash",
                "decision",
                "reason_codes",
                "issued_at",
                "expires_at",
            }
        },
        "decision": decision,
        "reason_codes": reasons,
    }
    event["decision_hash"] = sha256_binding(_without(event, "decision_hash"))
    try:
        receipt = publish(event)
    except Exception as exc:
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED", "Evidence Bus unavailable"
        ) from exc
    if not verify_evidence_receipt(receipt, event, receipt_key):
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
            "page composition EvidenceReceipt rejected",
        )
    return {
        "successor_authorized": decision
        == "ACCEPT_NON_CONFLICTING_PAGE_APPEND",
        "page_composition_decision": event,
        "page_composition_receipt": dict(receipt),
    }


def preauthorize(
    request: Mapping[str, Any],
    *,
    identity_key: bytes,
    receipt_key: bytes,
    publish: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    consume_approval: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    policy: Policy = Policy(),
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    field_decisions = [
        evaluate_field(
            {
                **candidate,
                "tenant_id": request["tenant_id"],
                "draft_id": request["draft_id"],
                "draft_version": request["draft_version"],
            },
            identity_key=identity_key,
            policy=policy,
            consume_approval=consume_approval,
            now=now,
        )
        for candidate in request.get("field_candidates", [])
    ]
    roster = evaluate_roster(
        request, field_decisions, identity_key=identity_key, policy=policy, now=now
    )
    if roster["decision"] != "AUTHORIZE_PENDING_MATERIALIZATION":
        raise GateClosedError(roster["reason_codes"][0], roster["decision"])
    try:
        receipt = publish(roster)
    except Exception as exc:
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED", "Evidence Bus unavailable"
        ) from exc
    if not verify_evidence_receipt(receipt, roster, receipt_key):
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED", "EvidenceReceipt rejected"
        )
    return {
        "authorized": True,
        "field_decisions": field_decisions,
        "roster_decision": roster,
        "preauthorization_receipt": dict(receipt),
    }


def finalize(
    request: Mapping[str, Any],
    *,
    identity_key: Optional[bytes] = None,
    receipt_key: bytes,
    publish: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    policy: Policy = Policy(),
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    provisional_row_binding = request.get("provisional_row_binding")
    post_execution_row_binding = request.get("post_execution_row_binding")
    if identity_key and request.get("provisional_row") is not None:
        provisional_row_binding = hmac_binding(identity_key, request["provisional_row"])
    if identity_key and request.get("post_execution_row") is not None:
        post_execution_row_binding = hmac_binding(
            identity_key, request["post_execution_row"]
        )
    active = (
        bool(request.get("cas_succeeded"))
        and int(request.get("blocking_incident_count", 0)) == 0
        and bool(post_execution_row_binding)
        and bool(request.get("preauthorization_receipt_id"))
    )
    reason = (
        "CANDIDATE_POLICY_SATISFIED"
        if active
        else (
            "STALE_GOVERNANCE_DECISION"
            if not request.get("cas_succeeded")
            else (
                "BLOCKING_INCIDENT_PRESENT"
                if int(request.get("blocking_incident_count", 0))
                else (
                    "PREAUTHORIZATION_RECEIPT_MISSING"
                    if not request.get("preauthorization_receipt_id")
                    else "POST_EXECUTION_ATTESTATION_MISSING"
                )
            )
        )
    )
    event = {
        **_base_event(request, policy, now),
        "event_type": "samchat_registration_finality_attestation_v1",
        "attestation_id": sha256_binding(
            {
                "draft_id": request.get("draft_id"),
                "draft_version": request.get("draft_version"),
                "player_slot": request.get("player_slot"),
                "player_id": request.get("player_id"),
                "post_execution_row_binding": post_execution_row_binding,
            }
        ),
        "draft_id": str(request["draft_id"]),
        "draft_version": int(request["draft_version"]),
        "player_slot": int(request["player_slot"]),
        "player_id": str(request["player_id"]),
        "roster_draft_binding": str(request["roster_draft_binding"]),
        "preauthorization_receipt_id": str(
            request.get("preauthorization_receipt_id") or ""
        ),
        "cas_succeeded": bool(request.get("cas_succeeded")),
        "database_transaction_id": str(request["database_transaction_id"]),
        "provisional_row_binding": str(provisional_row_binding or ""),
        "post_execution_row_binding": post_execution_row_binding,
        "blocking_incident_count": int(request.get("blocking_incident_count", 0)),
        "decision": "ACTIVATE_PLAYER" if active else "DENY_PLAYER_ACTIVATION",
        "reason_codes": [reason],
    }
    event["attestation_hash"] = sha256_binding(_without(event, "attestation_hash"))
    if not active:
        raise GateClosedError(reason, "finality prerequisites not satisfied")
    try:
        receipt = publish(event)
    except Exception as exc:
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED", "Evidence Bus unavailable"
        ) from exc
    if not verify_evidence_receipt(receipt, event, receipt_key):
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED", "finality EvidenceReceipt rejected"
        )
    return {"activate": True, "attestation": event, "finality_receipt": dict(receipt)}


def adjudicate_human_field_edit(
    request: Mapping[str, Any],
    *,
    identity_key: bytes,
    receipt_key: bytes,
    publish: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Authorize one exact, field-bound and single-use human resolution set."""

    def valid_sha256(value: Any) -> bool:
        return (
            isinstance(value, str)
            and value.startswith("sha256:")
            and len(value) == 71
        )

    def normalized_text(value: Any) -> Optional[str]:
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
        return normalized_text(value)

    required_ids = (
        "tenant_id",
        "session_id",
        "tournament_slug",
        "registration_subject_binding",
        "proposal_id",
        "edit_request_id",
        "base_draft_id",
        "expected_current_draft_id",
        "proposed_successor_draft_id",
        "consuming_principal_id",
        "operation_id",
    )
    if any(not str(request.get(key) or "").strip() for key in required_ids):
        raise GateClosedError(
            "HUMAN_FIELD_EDIT_BINDING_INVALID",
            "human field edit identity bindings are incomplete",
        )
    hash_fields = (
        "base_draft_hash",
        "expected_current_draft_hash",
        "proposed_successor_hash",
        "field_resolution_set_hash",
        "required_blocking_diff_set_hash",
        "approval_set_hash",
        "operation_id",
    )
    if any(not valid_sha256(request.get(key)) for key in hash_fields):
        raise GateClosedError(
            "HUMAN_FIELD_EDIT_BINDING_INVALID",
            "human field edit hash bindings are incomplete",
        )

    raw_resolutions = list(request.get("resolutions") or [])
    if not raw_resolutions:
        raise GateClosedError(
            "HUMAN_FIELD_EDIT_EMPTY",
            "at least one exact field resolution is required",
        )
    allowed_simple = {
        "team.name",
        "team.category",
        "team.gender",
        "team.league",
        "team.municipality",
        "team.state",
        "manager.name",
        "manager.role",
        "manager.phone",
        "manager.email",
        "notes",
    }
    player_pattern = re.compile(
        r"^players\.[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\.(name|birth_date|curp)$"
    )
    clearable = {
        "team.category",
        "team.gender",
        "team.league",
        "team.municipality",
        "team.state",
        "manager.role",
        "manager.phone",
        "manager.email",
        "notes",
    }
    allowed_roles = {"coordinador", "admin", "superadmin", "super_admin"}
    sensitive_roles = {"admin", "superadmin", "super_admin"}
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    canonical_resolutions: List[Dict[str, Any]] = []
    approval_bindings: List[Dict[str, str]] = []
    reasons: List[str] = []
    approval_ids: List[str] = []
    nonces: List[str] = []
    paths: List[str] = []
    blocking_diff_ids: List[str] = []

    for raw in raw_resolutions:
        if not isinstance(raw, Mapping):
            raise GateClosedError(
                "HUMAN_FIELD_EDIT_BINDING_INVALID",
                "field resolution must be an object",
            )
        field_path = str(raw.get("field_path") or "").strip().lower()
        if field_path not in allowed_simple and not player_pattern.fullmatch(field_path):
            reasons.append("FIELD_PATH_NOT_EDITABLE")
        approval_id = str(raw.get("approval_id") or "").strip()
        nonce = str(raw.get("nonce") or "").strip()
        roster_entry_id = (
            str(raw.get("roster_entry_id") or "").strip().lower() or None
        )
        if field_path.startswith("players.") and (
            not roster_entry_id or roster_entry_id not in field_path
        ):
            reasons.append("ROSTER_ENTRY_BINDING_INVALID")
        resolution_type = str(raw.get("resolution_type") or "").strip().upper()
        if resolution_type not in {
            "KEEP_PREVIOUS_VALUE",
            "ACCEPT_REPROCESS_CANDIDATE",
            "ENTER_CORRECTED_VALUE",
            "CLEAR_FIELD",
        }:
            reasons.append("RESOLUTION_TYPE_INVALID")
        previous_present = bool(raw.get("previous_value_present"))
        proposed_present = bool(raw.get("proposed_value_present"))
        previous_value = raw.get("previous_value") if previous_present else None
        proposed_value = raw.get("proposed_value") if proposed_present else None
        candidate_present = bool(raw.get("ocr_candidate_value_present"))
        candidate_value = (
            raw.get("ocr_candidate_value") if candidate_present else None
        )
        previous_normalized = normalized_value(field_path, previous_value)
        proposed_normalized = normalized_value(field_path, proposed_value)
        candidate_normalized = normalized_value(field_path, candidate_value)
        if (
            raw.get("previous_normalized_value")
            != previous_normalized
            or raw.get("proposed_normalized_value")
            != proposed_normalized
        ):
            reasons.append("NORMALIZATION_BINDING_INVALID")
        if resolution_type == "KEEP_PREVIOUS_VALUE" and (
            previous_present != proposed_present
            or previous_normalized != proposed_normalized
        ):
            reasons.append("KEEP_PREVIOUS_VALUE_MISMATCH")
        if resolution_type == "ACCEPT_REPROCESS_CANDIDATE" and (
            not raw.get("field_diff_id")
            or proposed_present != candidate_present
            or proposed_normalized != candidate_normalized
        ):
            reasons.append("REPROCESS_CANDIDATE_MISMATCH")
        if resolution_type == "ENTER_CORRECTED_VALUE" and not proposed_present:
            reasons.append("CORRECTED_VALUE_MISSING")
        if resolution_type == "CLEAR_FIELD" and (
            proposed_present or field_path not in clearable
        ):
            reasons.append("FIELD_CLEAR_NOT_ALLOWED")
        if proposed_present and field_path.endswith(".birth_date"):
            try:
                parsed_birth_date = datetime.strptime(
                    str(proposed_value), "%Y-%m-%d"
                ).date()
                if parsed_birth_date >= current.date():
                    reasons.append("FIELD_VALUE_INVALID")
            except (TypeError, ValueError):
                reasons.append("FIELD_VALUE_INVALID")
        if proposed_present and field_path.endswith(".curp") and not re.fullmatch(
            r"[A-Z0-9]{18}", str(proposed_value or "")
        ):
            reasons.append("FIELD_VALUE_INVALID")
        if (
            proposed_present
            and field_path == "manager.email"
            and not re.fullmatch(
                r"[^@\s]+@[^@\s]+\.[^@\s]+", str(proposed_value or "")
            )
        ):
            reasons.append("FIELD_VALUE_INVALID")

        evidence_class = str(raw.get("evidence_class") or "").strip().upper()
        if evidence_class not in {
            "S03_DIFF_RESOLUTION",
            "DIRECT_DOCUMENT_EVIDENCE_EDIT",
            "ADMINISTRATIVE_METADATA_EDIT",
        }:
            reasons.append("EVIDENCE_CLASS_INVALID")
        if evidence_class != "ADMINISTRATIVE_METADATA_EDIT":
            evidence_hash_fields = (
                "source_page_hash",
                "normalized_page_hash",
                "coordinate_frame_hash",
                "crop_hash",
            )
            crop = raw.get("crop_coordinates")
            crop_valid = (
                isinstance(crop, Mapping)
                and all(
                    isinstance(crop.get(key), int) and int(crop[key]) >= 0
                    for key in ("x", "y")
                )
                and all(
                    isinstance(crop.get(key), int) and int(crop[key]) > 0
                    for key in ("width", "height")
                )
            )
            if (
                not str(raw.get("source_page_artifact_id") or "").strip()
                or any(not valid_sha256(raw.get(key)) for key in evidence_hash_fields)
                or not crop_valid
            ):
                reasons.append("FIELD_EVIDENCE_BINDING_MISSING")
        if evidence_class == "S03_DIFF_RESOLUTION" and not all(
            str(raw.get(key) or "").strip()
            for key in ("ocr_run_id", "reprocess_decision_id", "field_diff_id")
        ):
            reasons.append("S03_DIFF_BINDING_MISSING")

        approver = raw.get("approver") or {}
        if not isinstance(approver, Mapping):
            reasons.append("APPROVER_BINDING_INVALID")
            approver = {}
        principal_id = str(approver.get("principal_id") or "").strip()
        role = str(approver.get("role") or "").strip().lower()
        if (
            not principal_id
            or principal_id != str(request["consuming_principal_id"])
            or approver.get("role_current") is not True
            or not str(approver.get("role_assignment_id") or "").strip()
            or not str(approver.get("authorization_epoch") or "").strip()
            or not str(approver.get("authentication_method") or "").strip()
            or not str(approver.get("auth_context_id") or "").strip()
            or int(approver.get("authentication_assurance_level") or 0) < 1
        ):
            reasons.append("APPROVER_BINDING_INVALID")
        if role not in allowed_roles:
            reasons.append("APPROVER_ROLE_NOT_ALLOWED")
        if (
            field_path.endswith(".curp")
            or field_path.endswith(".birth_date")
        ) and role not in sensitive_roles:
            reasons.append("ADDITIONAL_REVIEW_REQUIRED")

        try:
            issued_at = datetime.fromisoformat(
                str(raw["issued_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            not_before = datetime.fromisoformat(
                str(raw["not_before"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            expires_at = datetime.fromisoformat(
                str(raw["expires_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            reasons.append("HUMAN_APPROVAL_TIME_INVALID")
            issued_at = not_before = expires_at = current
        if not_before < issued_at or expires_at <= not_before:
            reasons.append("HUMAN_APPROVAL_TIME_INVALID")
        if (expires_at - issued_at).total_seconds() > 600:
            reasons.append("HUMAN_APPROVAL_TIME_INVALID")
        if current < not_before:
            reasons.append("HUMAN_APPROVAL_NOT_ACTIVE")
        if current >= expires_at:
            reasons.append("HUMAN_APPROVAL_EXPIRED")

        approval_ids.append(approval_id)
        nonces.append(nonce)
        paths.append(field_path)
        if raw.get("field_diff_id"):
            blocking_diff_ids.append(str(raw["field_diff_id"]))
        canonical = {
            "approval_id": approval_id,
            "nonce": nonce,
            "roster_entry_id": roster_entry_id,
            "player_slot": (
                int(raw["player_slot"])
                if raw.get("player_slot") is not None
                else None
            ),
            "field_path": field_path,
            "resolution_type": resolution_type,
            "evidence_class": evidence_class,
            "previous_value_present": previous_present,
            "previous_value": previous_value,
            "previous_normalized_value": previous_normalized,
            "proposed_value_present": proposed_present,
            "proposed_value": proposed_value,
            "proposed_normalized_value": proposed_normalized,
            "ocr_candidate_value_present": candidate_present,
            "ocr_candidate_value": candidate_value,
            "source_page_artifact_id": raw.get("source_page_artifact_id"),
            "source_page_hash": raw.get("source_page_hash"),
            "normalized_page_hash": raw.get("normalized_page_hash"),
            "coordinate_frame_hash": raw.get("coordinate_frame_hash"),
            "crop_coordinates": raw.get("crop_coordinates"),
            "crop_hash": raw.get("crop_hash"),
            "ocr_run_id": raw.get("ocr_run_id"),
            "reprocess_decision_id": raw.get("reprocess_decision_id"),
            "field_diff_id": raw.get("field_diff_id"),
            "classification": raw.get("classification"),
            "approver": dict(approver),
            "issued_at": str(raw.get("issued_at")),
            "not_before": str(raw.get("not_before")),
            "expires_at": str(raw.get("expires_at")),
        }
        canonical_resolutions.append(canonical)
        approval_bindings.append(
            {
                "approval_id": approval_id,
                "nonce": nonce,
                "approver_binding": hmac_binding(
                    identity_key,
                    {
                        "tenant_id": str(request["tenant_id"]),
                        "principal_id": principal_id,
                        "role": role,
                        "role_assignment_id": approver.get("role_assignment_id"),
                        "authorization_epoch": approver.get(
                            "authorization_epoch"
                        ),
                        "auth_context_id": approver.get("auth_context_id"),
                    },
                ),
                "previous_value_binding": hmac_binding(
                    identity_key,
                    {
                        "tenant_id": str(request["tenant_id"]),
                        "field_path": field_path,
                        "value": previous_normalized,
                        "present": previous_present,
                    },
                ),
                "proposed_value_binding": hmac_binding(
                    identity_key,
                    {
                        "tenant_id": str(request["tenant_id"]),
                        "field_path": field_path,
                        "value": proposed_normalized,
                        "present": proposed_present,
                    },
                ),
            }
        )

    if (
        not all(approval_ids)
        or not all(nonces)
        or len(approval_ids) != len(set(approval_ids))
        or len(nonces) != len(set(nonces))
        or len(paths) != len(set(paths))
    ):
        reasons.append("HUMAN_APPROVAL_REPLAY_OR_SCOPE_DUPLICATE")
    canonical_resolutions.sort(key=lambda item: item["field_path"])
    approval_bindings.sort(key=lambda item: item["approval_id"])
    if request["field_resolution_set_hash"] != sha256_binding(
        canonical_resolutions
    ):
        raise GateClosedError(
            "FIELD_RESOLUTION_SET_CHANGED",
            "field resolution set does not match its canonical binding",
        )
    if request["approval_set_hash"] != sha256_binding(
        [
            {"approval_id": item["approval_id"], "nonce": item["nonce"]}
            for item in approval_bindings
        ]
    ):
        raise GateClosedError(
            "HUMAN_APPROVAL_SET_CHANGED",
            "approval set does not match its canonical binding",
        )
    required_blocking = sorted(
        str(value) for value in list(request.get("required_blocking_diff_ids") or [])
    )
    if request["required_blocking_diff_set_hash"] != sha256_binding(
        required_blocking
    ):
        raise GateClosedError(
            "BLOCKING_DIFF_SET_CHANGED",
            "required blocking diff set hash mismatch",
        )
    if sorted(blocking_diff_ids) != required_blocking:
        reasons.append("UNRESOLVED_BLOCKING_REPROCESS_DIFFS")
    stale = (
        str(request["base_draft_id"])
        != str(request["expected_current_draft_id"])
        or int(request.get("base_draft_version", -1))
        != int(request.get("expected_current_draft_version", -2))
        or request["base_draft_hash"] != request["expected_current_draft_hash"]
    )
    if stale:
        reasons.append("STALE_HUMAN_APPROVAL")

    policy = {
        "policy_id": HUMAN_FIELD_EDIT_POLICY_ID,
        "policy_version": "1.0.0",
        "workflow_id": HUMAN_FIELD_EDIT_WORKFLOW_ID,
        "sal_level": "SAL-3",
        "field_scope": "exact",
        "approval_ttl_seconds": 600,
        "single_use_required": True,
        "ed25519_receipt_required": True,
        "blocking_diff_set_must_be_complete": True,
    }
    policy["policy_hash"] = sha256_binding(policy)
    if "ADDITIONAL_REVIEW_REQUIRED" in reasons:
        decision = "REQUIRE_ADDITIONAL_REVIEW"
    elif reasons:
        decision = "DENY_FIELD_EDIT"
    else:
        decision = "AUTHORIZE_FIELD_EDIT_SUCCESSOR"
        reasons = ["EXACT_FIELD_RESOLUTION_SET_VALID"]
    event = {
        "schema_version": "samchat.registration_human_field_edit_decision.v1",
        "workflow_id": HUMAN_FIELD_EDIT_WORKFLOW_ID,
        "policy_id": HUMAN_FIELD_EDIT_POLICY_ID,
        "policy_hash": policy["policy_hash"],
        "effective_sal": "SAL-3",
        "event_type": "samchat_registration_human_field_edit_decision_v1",
        "tenant_id": str(request["tenant_id"]),
        "session_id": str(request["session_id"]),
        "tournament_slug": str(request["tournament_slug"]),
        "registration_subject_binding": str(
            request["registration_subject_binding"]
        ),
        "proposal_id": str(request["proposal_id"]),
        "edit_request_id": str(request["edit_request_id"]),
        "base_draft_id": str(request["base_draft_id"]),
        "base_draft_version": int(request["base_draft_version"]),
        "base_draft_hash": str(request["base_draft_hash"]),
        "proposed_successor_draft_id": str(
            request["proposed_successor_draft_id"]
        ),
        "proposed_successor_hash": str(request["proposed_successor_hash"]),
        "field_resolution_set_hash": str(request["field_resolution_set_hash"]),
        "required_blocking_diff_set_hash": str(
            request["required_blocking_diff_set_hash"]
        ),
        "approval_set_hash": str(request["approval_set_hash"]),
        "approval_bindings": approval_bindings,
        "operation_id": str(request["operation_id"]),
        "issued_at": utc_timestamp(current),
        "expires_at": utc_timestamp(
            current.replace(microsecond=0) + timedelta(minutes=5)
        ),
        "decision": decision,
        "reason_codes": sorted(set(reasons)),
    }
    event["decision_id"] = sha256_binding(_without(event, "decision_id"))
    try:
        receipt = publish(event)
    except Exception as exc:
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
            "Evidence Bus unavailable",
        ) from exc
    if (
        not verify_evidence_receipt(receipt, event, receipt_key)
        or receipt.get("alg") != "Ed25519"
    ):
        raise GateClosedError(
            "EVIDENCE_RECEIPT_ASYMMETRIC_REQUIRED",
            "SAL-3 human edit requires a verified Ed25519 EvidenceReceipt",
        )
    return {
        "successor_authorized": decision == "AUTHORIZE_FIELD_EDIT_SUCCESSOR",
        "human_field_edit_decision": event,
        "human_field_edit_receipt": dict(receipt),
    }


def adjudicate_postcommit_mutation(
    request: Mapping[str, Any],
    *,
    identity_key: bytes,
    receipt_key: bytes,
    publish: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Authorize one exact successor of a committed Team or Player row."""

    def valid_sha256(value: Any) -> bool:
        return (
            isinstance(value, str)
            and value.startswith("sha256:")
            and len(value) == 71
        )

    required_ids = (
        "tenant_id",
        "proposal_id",
        "mutation_request_id",
        "entity_type",
        "entity_id",
        "team_id",
        "mutation_type",
    )
    if any(not str(request.get(key) or "").strip() for key in required_ids):
        raise GateClosedError(
            "POSTCOMMIT_BINDING_INVALID",
            "post-commit identity bindings are incomplete",
        )
    hash_fields = (
        "base_snapshot_hash",
        "expected_current_snapshot_hash",
        "proposed_snapshot_hash",
        "field_change_set_hash",
    )
    if any(not valid_sha256(request.get(key)) for key in hash_fields):
        raise GateClosedError(
            "POSTCOMMIT_BINDING_INVALID",
            "post-commit hash bindings are incomplete",
        )
    entity_type = str(request["entity_type"]).upper()
    mutation_type = str(request["mutation_type"]).upper()
    allowed_by_mutation = {
        "EDIT_TEAM": {
            "name",
            "category",
            "gender",
            "league",
            "league_phone",
            "league_address",
            "representative_name",
            "contact_email",
            "contact_phone",
            "state",
            "municipality",
        },
        "EDIT_PLAYER": {
            "first_name",
            "last_name",
            "birth_date",
            "curp",
            "email",
        },
        "VERIFY_PLAYER": {
            "verified_by_human",
            "needs_review",
            "verification_notes",
        },
    }
    expected_entity = {
        "EDIT_TEAM": "TEAM",
        "EDIT_PLAYER": "PLAYER",
        "VERIFY_PLAYER": "PLAYER",
    }
    if (
        mutation_type not in allowed_by_mutation
        or expected_entity[mutation_type] != entity_type
    ):
        raise GateClosedError(
            "POSTCOMMIT_MUTATION_TYPE_DENIED",
            "physical deletion and unsupported committed-state mutations are denied",
        )
    try:
        base_revision = int(request["base_revision"])
        proposed_revision = int(request["proposed_revision"])
        expected_revision = int(request["expected_current_revision"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GateClosedError(
            "POSTCOMMIT_REVISION_INVALID",
            "post-commit revision bindings are invalid",
        ) from exc
    stale = (
        expected_revision != base_revision
        or request["expected_current_snapshot_hash"]
        != request["base_snapshot_hash"]
    )
    if base_revision < 1 or proposed_revision != base_revision + 1:
        raise GateClosedError(
            "POSTCOMMIT_REVISION_INVALID",
            "post-commit successor must be the immediate next revision",
        )
    raw_changes = list(request.get("field_changes") or [])
    if (
        not raw_changes
        or request["field_change_set_hash"] != sha256_binding(raw_changes)
    ):
        raise GateClosedError(
            "POSTCOMMIT_CHANGE_SET_CHANGED",
            "field change set is empty or does not match its binding",
        )
    paths = [str(item.get("field_path") or "") for item in raw_changes]
    if (
        len(paths) != len(set(paths))
        or any(path not in allowed_by_mutation[mutation_type] for path in paths)
        or any(
            set(item) != {"field_path", "previous_value", "proposed_value"}
            for item in raw_changes
        )
    ):
        raise GateClosedError(
            "POSTCOMMIT_FIELD_SCOPE_DENIED",
            "post-commit change set contains duplicate or unsupported fields",
        )
    if any(
        item.get("previous_value") == item.get("proposed_value")
        for item in raw_changes
    ):
        raise GateClosedError(
            "POSTCOMMIT_CHANGE_SET_CHANGED",
            "unchanged values cannot be presented as a mutation",
        )
    if mutation_type == "VERIFY_PLAYER":
        proposed = {
            str(item["field_path"]): item.get("proposed_value")
            for item in raw_changes
        }
        projection = request.get("verification_projection") or {}
        if (
            projection.get("verified_by_human") is not True
            or projection.get("needs_review") is not False
            or (
                "verified_by_human" in proposed
                and proposed["verified_by_human"] is not True
            )
            or (
                "needs_review" in proposed
                and proposed["needs_review"] is not False
            )
        ):
            raise GateClosedError(
                "POSTCOMMIT_VERIFICATION_INVALID",
                "verification must set verified_by_human and clear needs_review",
            )
    actor = request.get("actor") or {}
    role = str(actor.get("role") or "").strip().lower()
    required_actor = (
        "principal_id",
        "role_assignment_id",
        "authorization_epoch",
        "authentication_method",
        "authentication_assurance_level",
        "auth_context_id",
    )
    if (
        role not in {"coordinador", "admin", "superadmin", "super_admin"}
        or any(not str(actor.get(key) or "").strip() for key in required_actor)
    ):
        raise GateClosedError(
            "POSTCOMMIT_ACTOR_NOT_AUTHORIZED",
            "current actor authority is incomplete or insufficient",
        )
    reason = re.sub(r"\s+", " ", str(request.get("mutation_reason") or "").strip())
    if len(reason) < 5 or len(reason) > 500:
        raise GateClosedError(
            "POSTCOMMIT_REASON_REQUIRED",
            "a bounded mutation reason is required",
        )
    expected_reason_binding = sha256_binding(
        {"domain": "REG-S07", "reason": reason}
    )
    if request.get("mutation_reason_binding") != expected_reason_binding:
        raise GateClosedError(
            "POSTCOMMIT_REASON_BINDING_CHANGED",
            "mutation reason does not match its binding",
        )
    source_binding = str(request.get("source_evidence_binding") or "")
    if not source_binding.startswith("hmac-sha256:") or len(source_binding) != 76:
        raise GateClosedError(
            "POSTCOMMIT_SOURCE_EVIDENCE_MISSING",
            "committed source evidence binding is required",
        )

    reasons: List[str] = []
    if stale:
        reasons.append("STALE_POSTCOMMIT_BASE")
    if request["base_snapshot_hash"] == request["proposed_snapshot_hash"]:
        reasons.append("POSTCOMMIT_SUCCESSOR_UNCHANGED")
    if (
        mutation_type == "EDIT_PLAYER"
        and role == "coordinador"
        and {"birth_date", "curp"} & set(paths)
    ):
        reasons.append("ADDITIONAL_REVIEW_REQUIRED")
    policy = {
        "policy_id": POSTCOMMIT_MUTATION_POLICY_ID,
        "policy_version": "1.0.0",
        "workflow_id": POSTCOMMIT_MUTATION_WORKFLOW_ID,
        "sal_level": "SAL-3",
        "immediate_successor_required": True,
        "physical_delete_allowed": False,
        "ed25519_receipt_required": True,
        "post_execution_attestation_required": True,
        "current_role_revalidation_required": True,
    }
    policy["policy_hash"] = sha256_binding(policy)
    if "ADDITIONAL_REVIEW_REQUIRED" in reasons:
        decision = "REQUIRE_ADDITIONAL_REVIEW"
    elif reasons:
        decision = "DENY_POSTCOMMIT_MUTATION"
    else:
        decision = "AUTHORIZE_POSTCOMMIT_MUTATION"
        reasons = ["EXACT_POSTCOMMIT_SUCCESSOR_VALID"]
    field_bindings = [
        {
            "field_path": str(item["field_path"]),
            "previous_value_binding": hmac_binding(
                identity_key,
                {
                    "entity_type": entity_type,
                    "entity_id": str(request["entity_id"]),
                    "field_path": str(item["field_path"]),
                    "value": item.get("previous_value"),
                },
            ),
            "proposed_value_binding": hmac_binding(
                identity_key,
                {
                    "entity_type": entity_type,
                    "entity_id": str(request["entity_id"]),
                    "field_path": str(item["field_path"]),
                    "value": item.get("proposed_value"),
                },
            ),
        }
        for item in sorted(raw_changes, key=lambda value: value["field_path"])
    ]
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event = {
        "schema_version": "samchat.registration_postcommit_mutation_decision.v1",
        "workflow_id": POSTCOMMIT_MUTATION_WORKFLOW_ID,
        "policy_id": POSTCOMMIT_MUTATION_POLICY_ID,
        "policy_hash": policy["policy_hash"],
        "effective_sal": "SAL-3",
        "event_type": "samchat_registration_postcommit_mutation_decision_v1",
        "tenant_id": str(request["tenant_id"]),
        "proposal_id": str(request["proposal_id"]),
        "mutation_request_id": str(request["mutation_request_id"]),
        "entity_type": entity_type,
        "entity_id": str(request["entity_id"]),
        "team_id": str(request["team_id"]),
        "mutation_type": mutation_type,
        "base_revision": base_revision,
        "proposed_revision": proposed_revision,
        "base_snapshot_hash": str(request["base_snapshot_hash"]),
        "proposed_snapshot_hash": str(request["proposed_snapshot_hash"]),
        "field_change_set_hash": str(request["field_change_set_hash"]),
        "field_bindings": field_bindings,
        "mutation_reason_binding": expected_reason_binding,
        "source_evidence_binding": source_binding,
        "actor_binding": hmac_binding(identity_key, actor),
        "issued_at": utc_timestamp(current),
        "expires_at": utc_timestamp(
            current.replace(microsecond=0) + timedelta(minutes=5)
        ),
        "decision": decision,
        "reason_codes": sorted(set(reasons)),
    }
    event["decision_id"] = sha256_binding(_without(event, "decision_id"))
    try:
        receipt = publish(event)
    except Exception as exc:
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
            "Evidence Bus unavailable",
        ) from exc
    if (
        not verify_evidence_receipt(receipt, event, receipt_key)
        or receipt.get("alg") != "Ed25519"
    ):
        raise GateClosedError(
            "EVIDENCE_RECEIPT_ASYMMETRIC_REQUIRED",
            "REG-S07 requires a verified Ed25519 EvidenceReceipt",
        )
    return {
        "mutation_authorized": decision == "AUTHORIZE_POSTCOMMIT_MUTATION",
        "postcommit_mutation_decision": event,
        "postcommit_mutation_receipt": dict(receipt),
    }


def finalize_postcommit_mutation(
    request: Mapping[str, Any],
    *,
    identity_key: bytes,
    receipt_key: bytes,
    publish: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Attest the exact in-transaction projection before persistence flush."""

    decision = dict(request.get("preauthorization_decision") or {})
    receipt = dict(request.get("preauthorization_receipt") or {})
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        expires_at = datetime.fromisoformat(
            str(decision["expires_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise GateClosedError(
            "POSTCOMMIT_PREAUTHORIZATION_INVALID",
            "post-commit preauthorization window is invalid",
        ) from exc
    exact = (
        decision.get("event_type")
        == "samchat_registration_postcommit_mutation_decision_v1"
        and decision.get("decision") == "AUTHORIZE_POSTCOMMIT_MUTATION"
        and decision.get("proposal_id") == str(request.get("proposal_id"))
        and decision.get("entity_type") == str(request.get("entity_type"))
        and decision.get("entity_id") == str(request.get("entity_id"))
        and decision.get("team_id") == str(request.get("team_id"))
        and decision.get("mutation_type") == str(request.get("mutation_type"))
        and int(decision.get("base_revision") or 0)
        == int(request.get("base_revision") or -1)
        and int(decision.get("proposed_revision") or 0)
        == int(request.get("proposed_revision") or -1)
        and decision.get("base_snapshot_hash")
        == request.get("base_snapshot_hash")
        and decision.get("proposed_snapshot_hash")
        == request.get("proposed_snapshot_hash")
        and decision.get("field_change_set_hash")
        == request.get("field_change_set_hash")
        and verify_evidence_receipt(receipt, decision, receipt_key)
        and receipt.get("alg") == "Ed25519"
    )
    if not exact:
        raise GateClosedError(
            "POSTCOMMIT_PREAUTHORIZATION_INVALID",
            "finality request is not bound to the exact preauthorization",
        )
    actual_hash = str(request.get("actual_projection_hash") or "")
    if (
        not bool(request.get("cas_succeeded"))
        or current >= expires_at
        or actual_hash != request.get("proposed_snapshot_hash")
        or not str(request.get("database_transaction_id") or "").strip()
    ):
        reason = (
            "STALE_POSTCOMMIT_BASE"
            if not request.get("cas_succeeded")
            else (
                "POSTCOMMIT_PREAUTHORIZATION_EXPIRED"
                if current >= expires_at
                else "POSTCOMMIT_PROJECTION_MISMATCH"
            )
        )
        raise GateClosedError(
            reason,
            "post-execution projection cannot be attested",
        )
    event = {
        "schema_version": "samchat.registration_postcommit_attestation.v1",
        "workflow_id": POSTCOMMIT_MUTATION_WORKFLOW_ID,
        "policy_id": POSTCOMMIT_MUTATION_POLICY_ID,
        "policy_hash": str(decision["policy_hash"]),
        "effective_sal": "SAL-3",
        "event_type": "samchat_registration_postcommit_attestation_v1",
        "tenant_id": str(request["tenant_id"]),
        "proposal_id": str(request["proposal_id"]),
        "preauthorization_decision_id": str(decision["decision_id"]),
        "preauthorization_receipt_id": str(receipt["receipt_id"]),
        "entity_type": str(request["entity_type"]),
        "entity_id": str(request["entity_id"]),
        "team_id": str(request["team_id"]),
        "mutation_type": str(request["mutation_type"]),
        "base_revision": int(request["base_revision"]),
        "proposed_revision": int(request["proposed_revision"]),
        "base_snapshot_hash": str(request["base_snapshot_hash"]),
        "proposed_snapshot_hash": str(request["proposed_snapshot_hash"]),
        "actual_projection_hash": actual_hash,
        "field_change_set_hash": str(request["field_change_set_hash"]),
        "database_transaction_id": str(request["database_transaction_id"]),
        "cas_succeeded": True,
        "decision": "ATTEST_POSTCOMMIT_MUTATION",
        "reason_codes": ["EXACT_POSTCOMMIT_PROJECTION_ATTESTED"],
        "issued_at": utc_timestamp(current),
    }
    event["attestation_id"] = sha256_binding(
        _without(event, "attestation_id", "attestation_hash")
    )
    event["attestation_hash"] = sha256_binding(
        _without(event, "attestation_hash")
    )
    try:
        finality_receipt = publish(event)
    except Exception as exc:
        raise GateClosedError(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
            "Evidence Bus unavailable",
        ) from exc
    if (
        not verify_evidence_receipt(finality_receipt, event, receipt_key)
        or finality_receipt.get("alg") != "Ed25519"
    ):
        raise GateClosedError(
            "EVIDENCE_RECEIPT_ASYMMETRIC_REQUIRED",
            "REG-S07 finality requires a verified Ed25519 EvidenceReceipt",
        )
    return {
        "attested": True,
        "postcommit_mutation_attestation": event,
        "postcommit_finality_receipt": dict(finality_receipt),
    }



def validate_staff_review_candidate_v2(
    resolution: Mapping[str, Any],
    *,
    expected_scope: Mapping[str, Any],
    identity_key: bytes,
    verify_geometry: Callable[[Mapping[str, Any], Mapping[str, Any]], bool],
    policy_artifact: Mapping[str, Any],
    expected_policy_hash: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Validate an internal review candidate without issuing authority.

    `expected_scope`, `verify_geometry`, `policy_artifact`, and its independently
    resolved expected hash must be built from trusted server state. This function
    does not authenticate an actor, consume an approval,
    emit evidence, or authorize materialization. An integration must recheck
    the actor's current authority and atomically consume the exact approval.
    """

    def denied(code: str) -> None:
        raise GateClosedError(code, "staff review candidate cannot be validated")

    if not isinstance(resolution, Mapping) or not isinstance(expected_scope, Mapping):
        denied("STAFF_SCOPE_INVALID")
    if not isinstance(policy_artifact, Mapping):
        denied("STAFF_POLICY_INVALID")
    try:
        resolution = copy.deepcopy(dict(resolution))
        expected_scope = copy.deepcopy(dict(expected_scope))
        policy_artifact = copy.deepcopy(dict(policy_artifact))
    except Exception:
        denied("STAFF_SCOPE_INVALID")
    if not isinstance(identity_key, bytes) or len(identity_key) < 32:
        denied("STAFF_SCOPE_INVALID")
    if (
        not isinstance(policy_artifact, Mapping)
        or policy_artifact.get("workflow_id") != "samchat_registration_identity_v2"
        or policy_artifact.get("policy_id") != "samchat_registration_identity_policy_v2"
        or policy_artifact.get("effective_sal") != "SAL-3"
        or not isinstance(policy_artifact.get("policy_version"), str)
        or not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+", policy_artifact["policy_version"]
        )
        or type(policy_artifact.get("minimum_effective_pixels")) is not int
        or policy_artifact["minimum_effective_pixels"] < 1
        or not isinstance(expected_policy_hash, str)
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", expected_policy_hash)
        or policy_artifact.get("policy_hash") != expected_policy_hash
    ):
        denied("STAFF_POLICY_INVALID")
    try:
        actual_policy_hash = sha256_binding(_without(policy_artifact, "policy_hash"))
    except (TypeError, ValueError, OverflowError):
        denied("STAFF_POLICY_INVALID")
    if not hmac.compare_digest(actual_policy_hash, expected_policy_hash):
        denied("STAFF_POLICY_INVALID")
    scope_keys = {
        "tenant_id",
        "tournament_edition_id",
        "tournament_slug",
        "team_id",
        "session_id",
        "draft_id",
        "draft_version",
        "staff_entry_binding",
        "slot",
        "role",
        "presence",
        "source_page",
        "source_page_hash",
        "evidence_binding",
        "field_decision_hashes",
    }
    scope = resolution.get("scope")
    if (
        not isinstance(scope, Mapping)
        or set(scope) != scope_keys
        or set(expected_scope) != scope_keys
        or dict(scope) != dict(expected_scope)
    ):
        denied("STAFF_SCOPE_INVALID")
    for key in (
        "tenant_id",
        "tournament_edition_id",
        "tournament_slug",
        "team_id",
        "session_id",
        "draft_id",
    ):
        if not isinstance(scope[key], str) or not scope[key].strip():
            denied("STAFF_SCOPE_INVALID")
    if (
        type(scope["draft_version"]) is not int
        or scope["draft_version"] < 1
        or type(scope["source_page"]) is not int
        or scope["source_page"] < 1
        or type(scope["slot"]) is not int
        or scope["slot"] not in (1, 2)
        or scope["role"] != ("director_tecnico" if scope["slot"] == 1 else "auxiliar")
        or scope["presence"] not in ("PRESENT", "ABSENT")
    ):
        denied("STAFF_SCOPE_INVALID")
    if not all(
        isinstance(scope[k], str)
        and re.fullmatch(r"hmac-sha256:[a-f0-9]{64}", scope[k])
        for k in ("staff_entry_binding", "evidence_binding")
    ):
        denied("STAFF_SCOPE_INVALID")
    if not isinstance(scope["source_page_hash"], str) or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", scope["source_page_hash"]
    ):
        denied("STAFF_SOURCE_BINDING_MISMATCH")
    hashes = scope["field_decision_hashes"]
    if (
        not isinstance(hashes, list)
        or any(
            not isinstance(x, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", x)
            for x in hashes
        )
        or len(hashes) != len(set(hashes))
        or (scope["presence"] == "PRESENT" and not hashes)
        or (scope["presence"] == "ABSENT" and hashes)
    ):
        denied("STAFF_FIELD_EVIDENCE_MISSING")
    if set(resolution) != {
        "scope",
        "approval_id",
        "nonce",
        "approver",
        "issued_at",
        "not_before",
        "expires_at",
        "geometry",
    }:
        denied("STAFF_REVIEW_REQUIRED")
    for key in ("approval_id", "nonce"):
        if not isinstance(resolution[key], str) or not resolution[key].strip():
            denied("STAFF_REVIEW_REQUIRED")
    actor = resolution["approver"]
    actor_keys = {
        "principal_id",
        "role",
        "role_assignment_id",
        "authorization_epoch",
        "authentication_method",
        "authentication_assurance_level",
        "auth_context_id",
    }
    if not isinstance(actor, Mapping) or set(actor) != actor_keys:
        denied("STAFF_REVIEW_REQUIRED")
    for key in actor_keys - {"authentication_assurance_level"}:
        if not isinstance(actor[key], str) or not actor[key].strip():
            denied("STAFF_REVIEW_REQUIRED")
    if (
        actor["role"] not in {"coordinador", "admin", "superadmin", "super_admin"}
        or type(actor["authentication_assurance_level"]) is not int
        or actor["authentication_assurance_level"] < 1
    ):
        denied("STAFF_REVIEW_REQUIRED")
    try:
        if any(
            not isinstance(resolution[k], str)
            for k in ("issued_at", "not_before", "expires_at")
        ):
            denied("STAFF_REVIEW_REQUIRED")
        times = [
            datetime.fromisoformat(str(resolution[k]).replace("Z", "+00:00"))
            for k in ("issued_at", "not_before", "expires_at")
        ]
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or any(t.tzinfo is None for t in times):
            denied("STAFF_REVIEW_REQUIRED")
        issued, start, expires = (t.astimezone(timezone.utc) for t in times)
        current = current.astimezone(timezone.utc)
        if (
            start < issued
            or expires <= start
            or (expires - issued).total_seconds() > 600
            or current < start
            or current >= expires
        ):
            denied("STAFF_REVIEW_EXPIRED")
    except (TypeError, ValueError, OverflowError):
        denied("STAFF_REVIEW_REQUIRED")
    geometry = resolution["geometry"]
    if not _valid_geometry(geometry):
        denied("STAFF_GEOMETRY_INVALID")
    coords = geometry["field_coordinates"]
    if (
        set(geometry)
        != {
            "original_image_hash",
            "normalized_image_hash",
            "normalization_version",
            "normalization_transform_hash",
            "coordinate_frame",
            "coordinate_frame_image_hash",
            "field_coordinates",
            "crop_derivation_version",
            "crop_hash",
            "effective_pixels",
        }
        or set(coords) != {"x", "y", "width", "height"}
        or any(
            not isinstance(geometry.get(k), str) or not geometry[k].strip()
            for k in ("normalization_version", "crop_derivation_version")
        )
        or any(type(coords.get(k)) is not int for k in ("x", "y", "width", "height"))
        or type(geometry.get("effective_pixels")) is not int
        or geometry["effective_pixels"] != coords["width"] * coords["height"]
        or geometry["effective_pixels"] < policy_artifact["minimum_effective_pixels"]
        or geometry.get("original_image_hash") != scope["source_page_hash"]
        or any(
            not isinstance(geometry.get(k), str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", geometry[k])
            for k in (
                "original_image_hash",
                "normalized_image_hash",
                "normalization_transform_hash",
                "coordinate_frame_image_hash",
                "crop_hash",
            )
        )
    ):
        denied("STAFF_GEOMETRY_INVALID")
    try:
        geometry_scope = copy.deepcopy(dict(scope))
        geometry_candidate = copy.deepcopy(dict(geometry))
        verified_geometry = (
            verify_geometry(geometry_scope, geometry_candidate) is True
            and geometry_scope == scope
            and geometry_candidate == geometry
        )
    except Exception:
        verified_geometry = False
    if not verified_geometry:
        denied("STAFF_GEOMETRY_INVALID")
    # Values here are bound by HMAC; no personal fields or actor data leave.
    binding = hmac_binding(
        identity_key,
        {
            "domain": "samchat-staff-review-candidate-v2",
            "policy_hash": expected_policy_hash,
            "scope": dict(scope),
            "approval_id": resolution["approval_id"],
            "nonce": resolution["nonce"],
            "approver": dict(actor),
            "issued_at": resolution["issued_at"],
            "not_before": resolution["not_before"],
            "expires_at": resolution["expires_at"],
            "geometry": dict(geometry),
        },
    )
    return {
        "candidate_binding": binding,
        "actor_revalidation_required": True,
        "single_use_consumption_required": True,
        "materialization_authorized": False,
    }
