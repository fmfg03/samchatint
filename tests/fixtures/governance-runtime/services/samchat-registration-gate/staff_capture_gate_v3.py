"""Explicit batch branch of the protected CTT gate; no individual-proof coercion."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import time
from datetime import datetime, timezone
from uuid import UUID

from devnous.copa_telmex.staff_batch_authorization import (
    CAPTURE_SCHEMA,
    CONSENT_SCHEMA,
    MEMBER_SCHEMA,
    VERIFICATION_SCHEMA,
    BatchReviewScope,
    batch_scope_hash,
    canonical_batch_scope_bytes,
    sign_batch_proof,
    verify_batch_proof,
)
from devnous.copa_telmex.staff_batch_source_adapter import SourceSnapshotV2
from devnous.copa_telmex.staff_capture_effect import (
    DATABASE_DERIVED_FIELDS,
    LATE_AUTHORITY_FIELDS,
    bind_capture_effect,
    bind_review_evidence,
    bind_review_plan,
    check_batch_effect_follows_review,
    freeze_review_batch,
)
from devnous.copa_telmex.staff_capture_geometry import verify_staff_geometry
from devnous.copa_telmex.staff_governance_receipt import verify_staff_evidence_receipt
from staff_capture_gate_v2 import _RESULT_KEYS as _V2_RESULT_KEYS
from staff_capture_gate_v2 import (
    FIELDS,
    StaffCaptureGateV2,
    StaffPreauthorizationDenied,
    _deny,
    _json,
    _mac,
    _operation_id,
    _sha,
)

_RESULT_KEYS = _V2_RESULT_KEYS | {"batch_context"}


class StaffCaptureGateV3(StaffCaptureGateV2):
    """Byte-bound machine checks plus real aggregate human consent.

    Configuration pins issuer/verifier keys and policy. Protected SQL must
    independently revalidate their admissions, current state and single use.
    A signed verifier claim alone never authorizes materialization.
    """

    def __init__(self, *, verifier_public_keys, **kwargs):
        super().__init__(**kwargs)
        if not verifier_public_keys or any(
            type(v) is not bytes or len(v) != 32 for v in verifier_public_keys.values()
        ):
            _deny()
        self._verifier_keys = dict(verifier_public_keys)

    def _review_batch(
        self,
        scope,
        attempt_id,
        review_bytes,
        signed_review,
        parent_consent,
        verification,
        root_bytes,
        issued_at,
        expires_at,
    ):
        if (
            type(scope) is not BatchReviewScope
            or type(attempt_id) is not UUID
            or type(issued_at) is not int
            or type(expires_at) is not int
            or not 0 < expires_at - issued_at <= 300
            or not issued_at <= int(time.time()) < expires_at
            or "sha256:" + scope.policy_hash != self._policy_hash
            or not self._scope_matches_policy(scope)
            or scope.actor_role not in self._policy["allowed_roles"]
            or not hmac.compare_digest(
                bind_review_plan(review_bytes, self._review_key), scope.staff_and_changes_binding
            )
        ):
            _deny()
        now = int(time.time())
        child = verify_batch_proof(
            signed_review, schema=MEMBER_SCHEMA, public_keys=self._assertion_keys, now=now
        )
        parent = verify_batch_proof(
            parent_consent, schema=CONSENT_SCHEMA, public_keys=self._assertion_keys, now=now
        )
        checked = verify_batch_proof(
            verification, schema=VERIFICATION_SCHEMA, public_keys=self._verifier_keys, now=now
        )
        root = _json(root_bytes)
        if (
            hashlib.sha256(root_bytes).hexdigest() != parent["root_hash"]
            or child["root_hash"] != parent["root_hash"]
            or child["consent_id"] != parent["consent_id"]
            or child["principal_id"] != parent["principal_id"]
            or child["principal_id"] != str(scope.actor_id)
            or child["attempt_id"] != str(attempt_id)
            or child["member_id"] != str(scope.member_id)
            or child["operation_id"] != str(scope.operation_id)
            or child["scope_hash"] != batch_scope_hash(scope)
            or child["expires_at"] != expires_at
            or child["issued_at"] > issued_at
            or child["expires_at"] > parent["expires_at"]
            or child["verification_hash"] != hashlib.sha256(verification).hexdigest()
            or checked["scope_hash"] != child["scope_hash"]
            or checked["qualification_hash"] != parent["qualification_hash"]
            or checked["qualification_hash"] != child["qualification_hash"]
            or checked["document_id"] != scope.document_id
            or checked["manifest_sha256"] != scope.manifest_sha256
            or checked["policy_hash"] != scope.policy_hash
            or checked["payload_hash"] != hashlib.sha256(review_bytes).hexdigest()
        ):
            _deny()
        if (
            root.get("schema") != "ctt.batch.review-root.v1"
            or root.get("run_id") != str(scope.run_id)
            or root.get("principal_id") != str(scope.actor_id)
            or root.get("tenant_id") != scope.tenant_id
            or root.get("edition_id") != str(scope.edition_id)
            or root.get("manifest_sha256") != scope.manifest_sha256
            or root.get("policy_hash") != scope.policy_hash
            or root.get("profile_hash") != checked["profile_hash"]
            or root.get("qualification_hash") != parent["qualification_hash"]
            or type(root.get("max_execution_seconds")) is not int
            or root["max_execution_seconds"] != 3600
        ):
            _deny()
        members = root.get("members")
        if (
            type(members) is not list
            or not 1 <= len(members) <= 50
            or len({m.get("member_id") for m in members if type(m) is dict}) != len(members)
        ):
            _deny()
        matching = [
            m for m in members if type(m) is dict and m.get("member_id") == child["member_id"]
        ]
        if len(matching) != 1 or any(
            matching[0].get(k) != v
            for k, v in {
                "scope_hash": child["scope_hash"],
                "verification_hash": child["verification_hash"],
                "payload_hash": checked["payload_hash"],
                "human_edits_hash": checked["human_edits_hash"],
                "document_id": scope.document_id,
            }.items()
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
            or plan["schema"] != "ctt.capture.review-plan.v3"
        ):
            _deny()
        team = dict(plan["team"]) | {f: None for f in DATABASE_DERIVED_FIELDS}
        players = [
            dict(r) | {f: None for f in LATE_AUTHORITY_FIELDS | DATABASE_DERIVED_FIELDS}
            for r in plan["players"]
        ]
        staff = [
            dict(s)
            | {
                "row": (
                    dict(s["row"]) | {f: None for f in LATE_AUTHORITY_FIELDS} if s["row"] else None
                )
            }
            for s in plan["staff"]
        ]
        rebuilt = freeze_review_batch(
            scope=scope,
            team=team,
            players=players,
            staff=staff,
            player_source_refs=[
                f"page:{r['page_index']}:jugador_{r['source_slot']}" for r in plan["player_sources"]
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
        return plan, child

    def _geometry(self, page, geometry, snapshot):
        if (
            not verify_staff_geometry(
                {"source_page_hash": "sha256:" + page.image_sha256},
                geometry,
                snapshot.page_assets[page.asset_id],
                max_page_bytes=self._max_page_bytes,
            )
            or geometry["effective_pixels"] < self._policy["minimum_effective_pixels"]
        ):
            _deny("STAFF_GEOMETRY_INVALID")

    def _verify_photo(self, kind, row, member, plan, index, page, snapshot):
        photo = member.get("photo_evidence")
        if photo and photo.get("mode") == "BATCH_PLAYER_SLOT":
            if kind != "PLAYER" or set(photo) != {"mode", "slot_ref"}:
                _deny("STAFF_PHOTO_EVIDENCE_INVALID")
            source = plan["player_sources"][index]
            if photo["slot_ref"] != f"page:{source['page_index']}:jugador_{source['source_slot']}":
                _deny("STAFF_PHOTO_SOURCE_MISMATCH")
            prepared = snapshot.derive_player_photo(photo["slot_ref"])
            if (
                prepared.photo_path != row["photo_path"]
                or prepared.sha256 != row["photo_sha256"]
                or prepared.ahash != row["photo_ahash"]
            ):
                _deny("STAFF_PHOTO_SOURCE_MISMATCH")
            if (
                row.get("photo_data") is not None
                and row["photo_data"] != base64.b64encode(prepared.data).decode()
            ):
                _deny("STAFF_PHOTO_SOURCE_MISMATCH")
        else:
            super()._verify_photo(kind, row, member, plan, index, page, snapshot)

    def _present_field_decisions(self, *, scope, kind, row, slot, member, page, snapshot):
        fields = member["fields"]
        decisions = []
        required_fields = tuple(
            field
            for field in FIELDS
            if not (
                field == "curp"
                and row["curp"] is None
                and kind in self._policy.get("optional_curp_kinds", [])
            )
        )
        if (
            type(fields) is not dict
            or set(fields) != set(required_fields)
            or member["absence_geometry"] is not None
        ):
            _deny("STAFF_FIELD_EVIDENCE_MISSING")
        for field in required_fields:
            value = (
                " ".join(
                    str(row[name] or "").strip() for name in ("first_name", "last_name")
                ).strip()
                if field == "full_name"
                else row[field]
            )
            if type(value) is not str or not value.strip():
                _deny("STAFF_FIELD_EVIDENCE_MISSING")
            if field == "curp" and (len(value) != 18 or not value.isalnum()):
                _deny("STAFF_FIELD_EVIDENCE_MISSING")
            self._geometry(page, fields[field], snapshot)
            decision = {
                "schema": "ctt.capture.field-decision.v3",
                "tenant_id": scope.tenant_id,
                "kind": kind,
                "row_id": row["id"],
                "slot": slot,
                "field": field,
                "source_page_hash": "sha256:" + page.image_sha256,
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
        return decisions

    @staticmethod
    def _expected_members(plan):
        expected = [("PLAYER", row, row["roster_index"], "PRESENT") for row in plan["players"]]
        expected += [
            ("STAFF", slot["row"], slot["slot"], slot["presence"]) for slot in plan["staff"]
        ]
        return expected

    @staticmethod
    def _check_incident_evidence(evidence):
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

    def _field_decisions(self, scope, plan, evidence, snapshot):
        self._check_incident_evidence(evidence)
        expected = self._expected_members(plan)
        members = evidence["members"]
        if type(members) is not list or len(members) != len(expected):
            _deny()
        pages = {page.page_index: page for page in scope.source_pages}
        decisions, staff_slots = [], []
        for index, (member, (kind, row, slot, presence)) in enumerate(zip(members, expected)):
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
                present = self._present_field_decisions(
                    scope=scope,
                    kind=kind,
                    row=row,
                    slot=slot,
                    member=member,
                    page=page,
                    snapshot=snapshot,
                )
                decisions.extend(present)
                member_decisions.extend(item["decision_hash"] for item in present)
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
                        "source_page_hash": "sha256:" + page.image_sha256,
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
                                "scope_hash": batch_scope_hash(scope),
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
                "domain": "ctt.capture.roster-authorization.v3",
                "scope_hash": batch_scope_hash(scope),
                "review_plan_binding": scope.staff_and_changes_binding,
                "field_evidence_binding": scope.field_evidence_binding,
                "signed_member_hash": hashlib.sha256(signed_review).hexdigest(),
                "event": {
                    k: v
                    for k, v in event.items()
                    if k not in {"roster_draft_binding", "decision_id", "decision_hash"}
                },
            },
        )

    def preauthorize(
        self,
        *,
        scope,
        attempt_id,
        review_bytes,
        evidence_bytes,
        signed_review,
        snapshot,
        parent_consent,
        verification,
        root_bytes,
        issued_at,
        expires_at,
    ):
        """Verify protected review/source evidence and publish one signed receipt."""
        try:
            plan, child = self._review_batch(
                scope,
                attempt_id,
                review_bytes,
                signed_review,
                parent_consent,
                verification,
                root_bytes,
                issued_at,
                expires_at,
            )
            if not hmac.compare_digest(
                bind_review_evidence(evidence_bytes, self._review_key),
                scope.field_evidence_binding,
            ):
                _deny()
            evidence = _json(evidence_bytes)
            if type(
                snapshot
            ) is not SourceSnapshotV2 or snapshot.scope_bytes != canonical_batch_scope_bytes(scope):
                _deny("STAFF_SOURCE_BINDING_MISMATCH")
            snapshot.verify_unchanged()
            decisions, staff_slots = self._field_decisions(scope, plan, evidence, snapshot)
            event = {
                "event_type": "samchat_registration_roster_decision_v3",
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
                "ordered_player_slots": [row["roster_index"] for row in plan["players"]],
                "ordered_player_field_decision_hashes": [
                    item["decision_hash"] for item in decisions if item["kind"] == "PLAYER"
                ],
                "ordered_staff_slots": staff_slots,
                "source_page_bindings": [
                    "sha256:" + page.image_sha256 for page in scope.source_pages
                ],
                "incident_state_hash": _sha(evidence["incident_state"]),
                "decision": "AUTHORIZE_PENDING_MATERIALIZATION",
                "reason_codes": ["CANDIDATE_POLICY_SATISFIED"],
            }
            batch_context = {
                "parent_consent": base64.b64encode(parent_consent).decode(),
                "verification": base64.b64encode(verification).decode(),
                "root_bytes": base64.b64encode(root_bytes).decode(),
            }
            event["batch_context_hash"] = _sha(batch_context)
            event["authorization_id"] = child["authorization_id"]
            event["member_proof_hash"] = hashlib.sha256(signed_review).hexdigest()
            event["operation_id"] = _operation_id(event)
            event["roster_draft_binding"] = self._roster_binding(event, scope, signed_review)
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
            self._review_batch(
                scope,
                attempt_id,
                review_bytes,
                signed_review,
                parent_consent,
                verification,
                root_bytes,
                issued_at,
                expires_at,
            )
            return {
                "authorized": True,
                "contract_version": "ctt-registration-staff-batch-v3",
                "player_field_decisions": [item for item in decisions if item["kind"] == "PLAYER"],
                "staff_field_decisions": [item for item in decisions if item["kind"] == "STAFF"],
                "roster_decision": event,
                "preauthorization_receipt": copy.deepcopy(dict(receipt)),
                "authority_metadata": self._metadata(plan, event, receipt),
                "batch_context": batch_context,
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
        parent_consent,
        verification,
        root_bytes,
    ):
        try:
            plan, child = self._review_batch(
                scope,
                attempt_id,
                review_bytes,
                signed_review,
                parent_consent,
                verification,
                root_bytes,
                issued_at,
                expires_at,
            )
            if (
                type(nonce) is not UUID
                or str(nonce) != child["nonce"]
                or type(preauthorization) is not dict
                or set(preauthorization) != _RESULT_KEYS
                or preauthorization["authorized"] is not True
                or preauthorization["contract_version"] != "ctt-registration-staff-batch-v3"
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
            batch_context = {
                "parent_consent": base64.b64encode(parent_consent).decode(),
                "verification": base64.b64encode(verification).decode(),
                "root_bytes": base64.b64encode(root_bytes).decode(),
            }
            if (
                preauthorization["batch_context"] != batch_context
                or event["batch_context_hash"] != _sha(batch_context)
                or event["authorization_id"] != child["authorization_id"]
                or event["member_proof_hash"] != hashlib.sha256(signed_review).hexdigest()
                or event["roster_draft_binding"]
                != self._roster_binding(event, scope, signed_review)
                or event["operation_id"] != _operation_id(event)
                or event["decision_hash"]
                != _sha({k: v for k, v in event.items() if k != "decision_hash"})
            ):
                _deny()
            metadata = self._metadata(plan, event, receipt)
            if preauthorization["authority_metadata"] != metadata:
                _deny()
            check_batch_effect_follows_review(
                review_bytes=review_bytes,
                effect_bytes=effect_bytes,
                scope=scope,
                review_key=self._review_key,
                authority_metadata=metadata,
            )
            body = {
                "schema": CAPTURE_SCHEMA,
                "kid": self._kid,
                "scope_hash": batch_scope_hash(scope),
                "attempt_id": str(attempt_id),
                "authorization_id": child["authorization_id"],
                "nonce": str(nonce),
                "member_proof_hash": hashlib.sha256(signed_review).hexdigest(),
                "review_plan_binding": bind_review_plan(review_bytes, self._review_key),
                "effect_binding": bind_capture_effect(effect_bytes, self._effect_key),
                "issued_at": issued_at,
                "expires_at": expires_at,
                "authority_metadata": metadata,
            }
            return sign_batch_proof(body, signing_key=self._signer)
        except Exception:
            _deny()

    def _validate_finality_inputs(
        self,
        *,
        protected_context,
        scope,
        review_attempt_id,
        review_bytes,
        pending_effect_bytes,
        signed_review,
        preauthorization,
    ):
        from devnous.copa_telmex.staff_capture_finality_effect import BatchFinalityContext

        if type(protected_context) is not BatchFinalityContext:
            _deny()
        protected_context.verify(scope, review_attempt_id)
        context = preauthorization["batch_context"]
        if type(context) is not dict or set(context) != {
            "parent_consent",
            "verification",
            "root_bytes",
        }:
            _deny()
        decoded = {key: base64.b64decode(value, validate=True) for key, value in context.items()}
        if any(base64.b64encode(decoded[key]).decode() != context[key] for key in context):
            _deny()
        child = verify_batch_proof(
            signed_review,
            schema=MEMBER_SCHEMA,
            public_keys=self._assertion_keys,
            now=protected_context.issued_at,
        )
        original_time = int(
            datetime.fromisoformat(
                preauthorization["roster_decision"]["timestamp"].replace("Z", "+00:00")
            ).timestamp()
        )
        if original_time > protected_context.issued_at:
            _deny()
        self.authorize_effect(
            scope=scope,
            attempt_id=review_attempt_id,
            nonce=UUID(child["nonce"]),
            review_bytes=review_bytes,
            effect_bytes=pending_effect_bytes,
            signed_review=signed_review,
            preauthorization=preauthorization,
            issued_at=original_time,
            expires_at=child["expires_at"],
            **decoded,
        )

    def authorize_finality_effect(
        self,
        *,
        protected_context,
        scope,
        review_attempt_id,
        review_bytes,
        pending_effect_bytes,
        finality_effect_bytes,
        signed_review,
        preauthorization,
        verified_finalities,
        nonce=None,
        issued_at=None,
        expires_at=None,
    ):
        from devnous.copa_telmex.staff_capture_finality_authorization import (
            check_finality_effect,
            finality_authorization_body,
            finality_authorization_domain,
        )
        from devnous.copa_telmex.staff_capture_finality_effect import canonical

        try:
            if (
                nonce is not None
                and nonce != protected_context.nonce
                or issued_at is not None
                and (type(issued_at) is not int or issued_at != protected_context.issued_at)
                or expires_at is not None
                and (type(expires_at) is not int or expires_at != protected_context.expires_at)
            ):
                _deny()
            self._validate_finality_inputs(
                protected_context=protected_context,
                scope=scope,
                review_attempt_id=review_attempt_id,
                review_bytes=review_bytes,
                pending_effect_bytes=pending_effect_bytes,
                signed_review=signed_review,
                preauthorization=preauthorization,
            )
            records = check_finality_effect(
                scope=scope,
                protected_context=protected_context,
                review_bytes=review_bytes,
                pending_effect_bytes=pending_effect_bytes,
                finality_effect_bytes=finality_effect_bytes,
                signed_review=signed_review,
                preauthorization=preauthorization,
                verified_finalities=verified_finalities,
                review_key=self._review_key,
                effect_key=self._effect_key,
                receipt_public_keys=self._receipt_keys,
                assertion_public_keys=self._assertion_keys,
                now_seconds=int(time.time()),
            )
            body = finality_authorization_body(
                kid=self._kid,
                scope=scope,
                protected_context=protected_context,
                review_bytes=review_bytes,
                pending_effect_bytes=pending_effect_bytes,
                finality_effect_bytes=finality_effect_bytes,
                signed_review=signed_review,
                preauthorization=preauthorization,
                records=records,
                review_key=self._review_key,
                effect_key=self._effect_key,
            )
            signature = self._signer.sign(finality_authorization_domain(scope) + canonical(body))
            protected_context.verify(scope, review_attempt_id)
            return canonical({"body": body, "signature": base64.b64encode(signature).decode()})
        except Exception:
            _deny()
