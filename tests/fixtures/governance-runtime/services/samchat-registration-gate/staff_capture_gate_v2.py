"""Preauthorize an exact protected review and issue its final effect proof.

These methods are internal gate operations, not HTTP request handlers. Their
scope, immutable plan/evidence, challenge times and source snapshot must be
resolved from the protected service state. The service and writer independently
revalidate current faculty, revocation, source admission and single use. A
receipt here proves an authorized attempt, never a committed team or eligibility.

Private evidence format: ``ctt.capture.identity-evidence.v1`` has ``members``
in plan-player order followed by staff slots 1 and 2, and ``incident_state``
containing a nonnegative ``blocking_incident_count``. Each member has exactly
kind, row_id, slot, presence, source_page_index, fields and absence_geometry.
PRESENT fields map the three configured identity fields to complete geometry;
ABSENT staff has no row or fields and has explicit verified absence geometry.
Values are always read from the reviewed rows, never from evidence parameters.
A row with photo_path requires photo_evidence: SOURCE_RECTANGLE with geometry,
or PLAYER-only BATCH_PLAYER_SLOT with its exact reviewed physical slot_ref.
Photo hashes/path/optional base64 are derived again from the retained sources.
Rows without photos and absent staff reject photo evidence. Required photo kinds
are optional explicit policy configuration, never an operational default.
"""

from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import time
from types import MappingProxyType
from typing import Any, Mapping, NoReturn
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex import staff_photo_store
from devnous.copa_telmex.staff_capture_authorization import (
    DOMAIN,
    canonical_authorization,
)
from devnous.copa_telmex.staff_capture_effect import (
    DATABASE_DERIVED_FIELDS,
    LATE_AUTHORITY_FIELDS,
    bind_capture_effect,
    bind_review_evidence,
    bind_review_plan,
    check_effect_follows_review,
    freeze_review_plan,
)
from devnous.copa_telmex.staff_capture_geometry import verify_staff_geometry
from devnous.copa_telmex.staff_capture_source_adapter import SourceSnapshot
from devnous.copa_telmex.staff_governance_receipt import verify_staff_evidence_receipt
from devnous.copa_telmex.staff_review_challenge import (
    ReviewScope,
    canonical_scope_bytes,
    scope_hash,
)
from devnous.copa_telmex.staff_signed_assertion import verify_assertion
from registration_gate import Policy
from staff_capture_finality_gate_v2 import StaffCaptureFinalityMixin

MAX_PRIVATE_BYTES = 4194304
FIELDS = ("full_name", "birth_date", "curp")
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")
_KID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_RESULT_KEYS = {
    "authorized",
    "contract_version",
    "player_field_decisions",
    "staff_field_decisions",
    "roster_decision",
    "preauthorization_receipt",
    "authority_metadata",
}


class StaffPreauthorizationDenied(ValueError):
    def __init__(self, reason_code="STAFF_SCOPE_INVALID"):
        self.reason_code = reason_code
        super().__init__("staff preauthorization denied")


def _deny(reason="STAFF_SCOPE_INVALID") -> NoReturn:
    raise StaffPreauthorizationDenied(reason) from None


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _mac(key: bytes, value: Any) -> str:
    return "hmac-sha256:" + hmac.new(key, _canonical(value), hashlib.sha256).hexdigest()


def _json(raw: bytes) -> dict:
    if type(raw) is not bytes or not 2 <= len(raw) <= MAX_PRIVATE_BYTES:
        _deny()
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 24:
                _deny()
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                _deny()
    if quoted or depth != 0:
        _deny()

    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                _deny()
            result[name] = value
        return result

    def constant(_value):
        _deny()

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if type(value) is not dict or _canonical(value) != raw:
        _deny()
    return value


def _public_keys(value):
    if not isinstance(value, Mapping) or not value:
        _deny()
    keys = dict(value)
    if any(
        type(kid) is not str
        or not _KID.fullmatch(kid)
        or type(key) is not bytes
        or len(key) != 32
        for kid, key in keys.items()
    ):
        _deny()
    return MappingProxyType(keys)


def _operation_id(event):
    # The same canonical operation identity as the existing durable v2 bus.
    return _sha(
        {
            key: event[key]
            for key in (
                "event_type",
                "tenant_id",
                "session_id",
                "draft_id",
                "team_id",
                "tournament_edition_id",
                "attempt_id",
                "draft_version",
            )
        }
    )


def _rows(plan):
    return plan["players"] + [slot["row"] for slot in plan["staff"] if slot["row"]]


class StaffCaptureGateV2(StaffCaptureFinalityMixin):
    """Configured internal producer; there are no operational policy defaults."""

    def __init__(
        self,
        *,
        identity_key,
        review_key,
        effect_key,
        signing_kid,
        signing_key,
        assertion_public_keys,
        receipt_public_keys,
        policy_artifact,
        expected_policy_hash,
        publisher,
        max_page_bytes,
    ):
        try:
            keys = (identity_key, review_key, effect_key)
            if (
                any(type(key) is not bytes or len(key) < 32 for key in keys)
                or len(set(keys)) != 3
                or type(signing_kid) is not str
                or not _KID.fullmatch(signing_kid)
                or not isinstance(signing_key, Ed25519PrivateKey)
                or not callable(publisher)
                or type(max_page_bytes) is not int
                or max_page_bytes < 1
            ):
                _deny()
            self._assertion_keys = _public_keys(assertion_public_keys)
            self._receipt_keys = _public_keys(receipt_public_keys)
            policy = copy.deepcopy(dict(policy_artifact))
            self._validate_policy(policy, expected_policy_hash)
            self._identity_key, self._review_key, self._effect_key = keys
            self._kid, self._signer = signing_kid, signing_key
            self._policy = policy
            self._policy_hash = expected_policy_hash
            self._publish, self._max_page_bytes = publisher, max_page_bytes
        except Exception:
            _deny()

    @staticmethod
    def _validate_policy(policy, expected_hash):
        required_policy_keys = {
            "workflow_id",
            "policy_id",
            "policy_version",
            "effective_sal",
            "field_order",
            "minimum_effective_pixels",
            "allowed_roles",
            "minimum_roster_size",
            "maximum_roster_size",
            "required_staff_slots",
            "policy_hash",
        }
        if not required_policy_keys <= set(policy) or set(
            policy
        ) - required_policy_keys - {"required_photo_kinds"}:
            _deny()
        photo_kinds = policy.get("required_photo_kinds", [])
        if (
            type(photo_kinds) is not list
            or any(
                type(kind) is not str or kind not in ("PLAYER", "STAFF")
                for kind in photo_kinds
            )
            or photo_kinds != sorted(set(photo_kinds))
        ):
            _deny()
        roles, required = policy["allowed_roles"], policy["required_staff_slots"]
        if (
            policy["workflow_id"] != "samchat_registration_identity_v2"
            or policy["policy_id"] != "samchat_registration_identity_policy_v2"
            or policy["effective_sal"] != "SAL-3"
            or type(policy["policy_version"]) is not str
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", policy["policy_version"])
            or policy["field_order"] != list(FIELDS)
            or type(policy["minimum_effective_pixels"]) is not int
            or policy["minimum_effective_pixels"] < Policy().minimum_effective_pixels
            or type(roles) is not list
            or not roles
            or any(type(role) is not str or not _SLUG.fullmatch(role) for role in roles)
            or roles != sorted(set(roles))
            or type(required) is not list
            or any(type(slot) is not int or slot not in (1, 2) for slot in required)
            or required != sorted(set(required))
            or type(policy["minimum_roster_size"]) is not int
            or type(policy["maximum_roster_size"]) is not int
            or not 1
            <= policy["minimum_roster_size"]
            <= policy["maximum_roster_size"]
            <= 35
            or type(expected_hash) is not str
            or not _SHA.fullmatch(expected_hash)
            or policy["policy_hash"] != expected_hash
            or _sha({k: v for k, v in policy.items() if k != "policy_hash"})
            != expected_hash
        ):
            _deny()

    def _review(
        self, scope, attempt_id, review_bytes, signed_review, issued_at, expires_at
    ):
        if (
            type(scope) is not ReviewScope
            or scope.decision_hash is not None
            or scope.review_receipt_id is not None
            or not isinstance(attempt_id, UUID)
            or type(issued_at) is not int
            or type(expires_at) is not int
            or not 0 < expires_at - issued_at <= 300
            or not issued_at <= int(time.time()) < expires_at
            or scope.policy_hash != self._policy_hash
            or scope.actor_role not in self._policy["allowed_roles"]
            or not hmac.compare_digest(
                bind_review_plan(review_bytes, self._review_key),
                scope.staff_and_changes_binding,
            )
        ):
            _deny()
        proof = verify_assertion(signed_review, public_keys=self._assertion_keys)
        if (
            proof.attempt_id != attempt_id
            or proof.principal_id != scope.actor_id
            or proof.scope_hash != scope_hash(scope)
            or proof.expires_at != expires_at
        ):
            _deny()
        plan = _json(review_bytes)
        if (
            set(plan)
            != {
                "schema",
                "review_context",
                "column_schema_hash",
                "team",
                "players",
                "staff",
                "player_sources",
            }
            or plan["schema"] != "ctt.capture.review-plan.v1"
        ):
            _deny()
        team = dict(plan["team"]) | {field: None for field in DATABASE_DERIVED_FIELDS}
        players = [
            dict(row)
            | {field: None for field in LATE_AUTHORITY_FIELDS | DATABASE_DERIVED_FIELDS}
            for row in plan["players"]
        ]
        staff = [
            dict(slot)
            | {
                "row": (
                    (
                        dict(slot["row"])
                        | {field: None for field in LATE_AUTHORITY_FIELDS}
                    )
                    if slot["row"]
                    else None
                )
            }
            for slot in plan["staff"]
        ]
        rebuilt = freeze_review_plan(
            scope=scope,
            team=team,
            players=players,
            staff=staff,
            player_source_refs=[
                f"page:{item['pdf_page']}:jugador_{item['source_slot']}"
                for item in plan["player_sources"]
            ],
        )
        if (
            rebuilt != review_bytes
            or not self._policy["minimum_roster_size"]
            <= len(players)
            <= self._policy["maximum_roster_size"]
        ):
            _deny()
        if any(
            plan["staff"][slot - 1]["presence"] != "PRESENT"
            for slot in self._policy["required_staff_slots"]
        ):
            _deny("STAFF_REVIEW_REQUIRED")
        return plan

    def _geometry(self, page, geometry, snapshot):
        if (
            not verify_staff_geometry(
                {"source_page_hash": page.image_sha256},
                geometry,
                snapshot.page_assets[page.asset_id],
                max_page_bytes=self._max_page_bytes,
            )
            or geometry["effective_pixels"] < self._policy["minimum_effective_pixels"]
        ):
            _deny("STAFF_GEOMETRY_INVALID")

    def _verify_photo(self, kind, row, member, plan, index, page, snapshot):
        photo_path = row["photo_path"] if row else None
        if photo_path is None:
            if "photo_evidence" in member or (
                row is not None and kind in self._policy.get("required_photo_kinds", [])
            ):
                _deny("STAFF_PHOTO_EVIDENCE_MISSING")
            return
        photo = member.get("photo_evidence")
        if type(photo) is not dict:
            _deny("STAFF_PHOTO_EVIDENCE_MISSING")
        if photo.get("mode") == "SOURCE_RECTANGLE" and set(photo) == {
            "mode",
            "geometry",
        }:
            prepared = staff_photo_store.derive_rectangular_review_photo(
                snapshot.page_assets[page.asset_id],
                photo["geometry"],
                max_page_bytes=self._max_page_bytes,
            )
        elif (
            photo.get("mode") == "BATCH_PLAYER_SLOT"
            and set(photo) == {"mode", "slot_ref"}
            and kind == "PLAYER"
        ):
            source = plan["player_sources"][index]
            expected_ref = f"page:{source['pdf_page']}:jugador_{source['source_slot']}"
            if photo["slot_ref"] != expected_ref:
                _deny("STAFF_PHOTO_SOURCE_MISMATCH")
            prepared = staff_photo_store.derive_batch_review_photo(
                snapshot, expected_ref
            )
        else:
            _deny("STAFF_PHOTO_EVIDENCE_INVALID")
        if (
            prepared.photo_path != photo_path
            or ("photo_sha256" in row and row["photo_sha256"] != prepared.sha256)
            or ("photo_ahash" in row and row["photo_ahash"] != prepared.ahash)
            or (
                row.get("photo_data") is not None
                and row["photo_data"] != base64.b64encode(prepared.data).decode("ascii")
            )
        ):
            _deny("STAFF_PHOTO_SOURCE_MISMATCH")

    def _field_decisions(self, scope, plan, evidence, snapshot):
        if (
            set(evidence) != {"schema", "members", "incident_state"}
            or evidence["schema"] != "ctt.capture.identity-evidence.v1"
        ):
            _deny()
        incident = evidence["incident_state"]
        if (
            type(incident) is not dict
            or set(incident) != {"blocking_incident_count"}
            or type(incident["blocking_incident_count"]) is not int
            or incident["blocking_incident_count"] < 0
        ):
            _deny()
        if incident["blocking_incident_count"]:
            _deny("BLOCKING_INCIDENT_PRESENT")
        expected = [
            ("PLAYER", row, row["roster_index"], "PRESENT") for row in plan["players"]
        ]
        expected += [
            ("STAFF", slot["row"], slot["slot"], slot["presence"])
            for slot in plan["staff"]
        ]
        members = evidence["members"]
        if type(members) is not list or len(members) != len(expected):
            _deny()
        pages = {page.page_index: page for page in scope.source_pages}
        decisions, staff_slots = [], []
        for index, (member, (kind, row, slot, presence)) in enumerate(
            zip(members, expected)
        ):
            if (
                type(member) is not dict
                or set(member) - {"photo_evidence"}
                != {
                    "kind",
                    "row_id",
                    "slot",
                    "presence",
                    "source_page_index",
                    "fields",
                    "absence_geometry",
                }
                or member["kind"] != kind
                or member["row_id"] != (row["id"] if row else None)
                or type(member["slot"]) is not int
                or member["slot"] != slot
                or member["presence"] != presence
                or type(member["source_page_index"]) is not int
                or member["source_page_index"] not in pages
            ):
                _deny()
            page = pages[member["source_page_index"]]
            if kind == "PLAYER" and (
                str(page.asset_id) != plan["player_sources"][index]["asset_id"]
                or page.pdf_page != plan["player_sources"][index]["pdf_page"]
            ):
                _deny("STAFF_SOURCE_BINDING_MISMATCH")
            self._verify_photo(kind, row, member, plan, index, page, snapshot)
            fields = member["fields"]
            member_decisions = []
            if presence == "ABSENT":
                if kind != "STAFF" or fields != {}:
                    _deny()
                self._geometry(page, member["absence_geometry"], snapshot)
            else:
                if (
                    type(fields) is not dict
                    or set(fields) != set(FIELDS)
                    or member["absence_geometry"] is not None
                ):
                    _deny("STAFF_FIELD_EVIDENCE_MISSING")
                for field in FIELDS:
                    value = (
                        " ".join(
                            str(row[name] or "").strip()
                            for name in ("first_name", "last_name")
                        ).strip()
                        if field == "full_name"
                        else row[field]
                    )
                    if type(value) is not str or not value.strip():
                        _deny("STAFF_FIELD_EVIDENCE_MISSING")
                    self._geometry(page, fields[field], snapshot)
                    decision = {
                        "schema": "ctt.capture.field-decision.v2",
                        "tenant_id": scope.tenant_id,
                        "kind": kind,
                        "row_id": row["id"],
                        "slot": slot,
                        "field": field,
                        "source_page_hash": page.image_sha256,
                        "policy_hash": scope.policy_hash,
                        "value_binding": _mac(
                            self._identity_key,
                            {
                                "domain": "ctt.capture.field-value.v2",
                                "tenant_id": scope.tenant_id,
                                "role": row["role"] if kind == "STAFF" else "player",
                                "kind": kind,
                                "row_id": row["id"],
                                "slot": slot,
                                "field": field,
                                "value": value,
                            },
                        ),
                        "geometry_hash": _sha(fields[field]),
                        "decision": "ACCEPT_CANDIDATE",
                    }
                    decision["decision_hash"] = _sha(decision)
                    decisions.append(decision)
                    member_decisions.append(decision["decision_hash"])
            if kind == "STAFF":
                staff_slots.append(
                    {
                        "slot": slot,
                        "role": "director_tecnico" if slot == 1 else "auxiliar",
                        "staff_entry_binding": _mac(
                            self._identity_key,
                            {
                                "domain": "ctt.capture.staff-entry.v2",
                                "tenant_id": scope.tenant_id,
                                "team_id": str(scope.team_id),
                                "slot": slot,
                                "row": row,
                            },
                        ),
                        "presence": presence,
                        "source_page_hash": page.image_sha256,
                        "evidence_binding": _mac(
                            self._identity_key,
                            {
                                "domain": "ctt.capture.member-evidence.v2",
                                "tenant_id": scope.tenant_id,
                                "member": member,
                            },
                        ),
                        "review_decision_hash": _sha(
                            {
                                "scope_hash": scope_hash(scope),
                                "slot": slot,
                                "presence": presence,
                                "field_decision_hashes": member_decisions,
                            }
                        ),
                        "field_decision_hashes": member_decisions,
                    }
                )
        return decisions, staff_slots

    def _roster_binding(self, event, scope, signed_review):
        return _mac(
            self._identity_key,
            {
                "domain": "ctt.capture.roster-authorization.v2",
                "scope_hash": scope_hash(scope),
                "review_plan_binding": scope.staff_and_changes_binding,
                "field_evidence_binding": scope.field_evidence_binding,
                "signed_review_hash": "sha256:"
                + hashlib.sha256(signed_review).hexdigest(),
                "event": {
                    key: value
                    for key, value in event.items()
                    if key
                    not in {"roster_draft_binding", "decision_id", "decision_hash"}
                },
            },
        )

    @staticmethod
    def _metadata(plan, event, receipt):
        return {
            row["id"]: {
                "governance_decision_id": event["decision_id"],
                "roster_draft_binding": event["roster_draft_binding"],
                "preauthorization_receipt_id": receipt["receipt_id"],
                "finality_receipt_id": None,
            }
            for row in _rows(plan)
        }

    def preauthorize(
        self,
        *,
        scope,
        attempt_id,
        review_bytes,
        evidence_bytes,
        signed_review,
        snapshot,
        issued_at,
        expires_at,
    ):
        """Verify protected review/source evidence and publish one signed receipt."""
        try:
            plan = self._review(
                scope, attempt_id, review_bytes, signed_review, issued_at, expires_at
            )
            if not hmac.compare_digest(
                bind_review_evidence(evidence_bytes, self._review_key),
                scope.field_evidence_binding,
            ):
                _deny()
            evidence = _json(evidence_bytes)
            if type(
                snapshot
            ) is not SourceSnapshot or snapshot.scope_bytes != canonical_scope_bytes(
                scope
            ):
                _deny("STAFF_SOURCE_BINDING_MISMATCH")
            snapshot.verify_unchanged()
            decisions, staff_slots = self._field_decisions(
                scope, plan, evidence, snapshot
            )
            event = {
                "event_type": "samchat_registration_roster_decision_v2",
                "tenant_id": scope.tenant_id,
                "tournament_edition_id": str(scope.edition_id),
                "tournament_slug": scope.tournament_slug,
                "team_id": str(scope.team_id),
                "session_id": str(scope.session_id),
                "draft_id": str(scope.draft_id),
                "draft_version": scope.draft_version,
                "timestamp": datetime.fromtimestamp(issued_at, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "policy_hash": scope.policy_hash,
                "workflow_id": self._policy["workflow_id"],
                "effective_sal": "SAL-3",
                "policy_id": self._policy["policy_id"],
                "policy_version": self._policy["policy_version"],
                "attempt_id": _sha(
                    {"domain": "ctt.capture.attempt.v2", "attempt_id": str(attempt_id)}
                ),
                "ordered_player_slots": [
                    row["roster_index"] for row in plan["players"]
                ],
                "ordered_player_field_decision_hashes": [
                    item["decision_hash"]
                    for item in decisions
                    if item["kind"] == "PLAYER"
                ],
                "ordered_staff_slots": staff_slots,
                "source_page_bindings": [
                    page.image_sha256 for page in scope.source_pages
                ],
                "incident_state_hash": _sha(evidence["incident_state"]),
                "decision": "AUTHORIZE_PENDING_MATERIALIZATION",
                "reason_codes": ["CANDIDATE_POLICY_SATISFIED"],
            }
            event["operation_id"] = _operation_id(event)
            event["roster_draft_binding"] = self._roster_binding(
                event, scope, signed_review
            )
            event["decision_id"] = _sha(
                {
                    "domain": "ctt.capture.roster-decision.v2",
                    "operation_id": event["operation_id"],
                    "roster_draft_binding": event["roster_draft_binding"],
                }
            )
            event["decision_hash"] = _sha(event)
            receipt = self._publish(copy.deepcopy(event))
            if not verify_staff_evidence_receipt(
                receipt, event, public_keys=self._receipt_keys, max_age_seconds=300
            ):
                _deny("EVIDENCE_WRITE_FAILED_FAIL_CLOSED")
            snapshot.verify_unchanged()
            self._review(
                scope, attempt_id, review_bytes, signed_review, issued_at, expires_at
            )
            return {
                "authorized": True,
                "contract_version": "ctt-registration-staff-v2",
                "player_field_decisions": [
                    item for item in decisions if item["kind"] == "PLAYER"
                ],
                "staff_field_decisions": [
                    item for item in decisions if item["kind"] == "STAFF"
                ],
                "roster_decision": event,
                "preauthorization_receipt": copy.deepcopy(dict(receipt)),
                "authority_metadata": self._metadata(plan, event, receipt),
            }
        except StaffPreauthorizationDenied:
            raise
        except Exception:
            _deny()

    def authorize_effect(
        self,
        *,
        scope,
        attempt_id,
        nonce,
        review_bytes,
        effect_bytes,
        signed_review,
        preauthorization,
        issued_at,
        expires_at,
    ) -> bytes:
        """Reverify preauthorization and sign only its exact reviewed effect."""
        try:
            plan = self._review(
                scope, attempt_id, review_bytes, signed_review, issued_at, expires_at
            )
            if (
                not isinstance(nonce, UUID)
                or type(preauthorization) is not dict
                or set(preauthorization) != _RESULT_KEYS
                or preauthorization["authorized"] is not True
                or preauthorization["contract_version"] != "ctt-registration-staff-v2"
            ):
                _deny()
            event, receipt = (
                preauthorization["roster_decision"],
                preauthorization["preauthorization_receipt"],
            )
            if not verify_staff_evidence_receipt(
                receipt, event, public_keys=self._receipt_keys, max_age_seconds=300
            ):
                _deny("PREAUTHORIZATION_RECEIPT_INVALID")
            if (
                event["timestamp"]
                != datetime.fromtimestamp(issued_at, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
                or event["roster_draft_binding"]
                != self._roster_binding(event, scope, signed_review)
                or event["operation_id"] != _operation_id(event)
                or event["decision_hash"]
                != _sha({k: v for k, v in event.items() if k != "decision_hash"})
                or event["decision_id"]
                != _sha(
                    {
                        "domain": "ctt.capture.roster-decision.v2",
                        "operation_id": event["operation_id"],
                        "roster_draft_binding": event["roster_draft_binding"],
                    }
                )
            ):
                _deny()
            for group, expected in (
                (
                    preauthorization["player_field_decisions"],
                    event["ordered_player_field_decision_hashes"],
                ),
                (
                    preauthorization["staff_field_decisions"],
                    [
                        digest
                        for slot in event["ordered_staff_slots"]
                        for digest in slot["field_decision_hashes"]
                    ],
                ),
            ):
                if [item["decision_hash"] for item in group] != expected or any(
                    item["decision_hash"]
                    != _sha({k: v for k, v in item.items() if k != "decision_hash"})
                    for item in group
                ):
                    _deny()
            metadata = self._metadata(plan, event, receipt)
            if preauthorization["authority_metadata"] != metadata:
                _deny()
            check_effect_follows_review(
                review_bytes=review_bytes,
                effect_bytes=effect_bytes,
                scope=scope,
                review_key=self._review_key,
                authority_metadata=metadata,
            )
            body = {
                "schema": "ctt.capture.authorization.v1",
                "kid": self._kid,
                "scope_hash": scope_hash(scope),
                "attempt_id": str(attempt_id),
                "nonce": str(nonce),
                "review_plan_binding": bind_review_plan(review_bytes, self._review_key),
                "effect_binding": bind_capture_effect(effect_bytes, self._effect_key),
                "issued_at": issued_at,
                "expires_at": expires_at,
                "authority_metadata": metadata,
            }
            signature = self._signer.sign(DOMAIN + canonical_authorization(body))
            return canonical_authorization(
                {"body": body, "signature": base64.b64encode(signature).decode("ascii")}
            )
        except StaffPreauthorizationDenied:
            raise
        except Exception:
            _deny()
