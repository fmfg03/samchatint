"""Verify the separate gate capability for exact in-transaction activation."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .staff_capture_effect import bind_capture_effect, bind_review_plan
from .staff_capture_finality_effect import (
    BatchFinalityContext,
    FinalityContext,
    bind_finality_effect,
    canonical,
    deny,
    digest,
    freeze_finality_effect,
    ordered_receipts,
    parse,
    pending_rows,
    verify_finality_bundle,
)
from .staff_governance_receipt import verify_staff_evidence_receipt
from .staff_review_challenge import scope_hash as _individual_scope_hash
from .staff_signed_assertion import verify_assertion

DOMAIN = b"samchat.ctt.capture-finality-authorization.v1\x00"
BODY_KEYS = frozenset(
    {
        "schema",
        "kid",
        "scope_hash",
        "review_attempt_id",
        "signed_review_hash",
        "preauthorization_event_hash",
        "preauthorization_receipt_id",
        "database_transaction_id",
        "event_attempt_id",
        "nonce",
        "review_plan_binding",
        "pending_effect_binding",
        "finality_effect_binding",
        "ordered_finality_receipts",
        "issued_at",
        "expires_at",
    }
)


def verify_pre_authorization(
    *,
    preauthorization,
    scope,
    attempt_id,
    pending_effect_bytes,
    receipt_public_keys,
    signed_review,
    assertion_public_keys,
    now_seconds,
):
    """Authenticate original issuance/roster; never trust a verified flag alone."""
    try:
        batch = _is_batch(scope)
        if (
            type(preauthorization) is not dict
            or set(preauthorization)
            != {
                "authorized",
                "contract_version",
                "player_field_decisions",
                "staff_field_decisions",
                "roster_decision",
                "preauthorization_receipt",
                "authority_metadata",
            }
            | ({"batch_context"} if batch else set())
            or preauthorization["authorized"] is not True
            or preauthorization["contract_version"]
            != (
                "ctt-registration-staff-batch-v3"
                if batch
                else "ctt-registration-staff-v2"
            )
        ):
            deny()
        if batch:
            from .staff_batch_authorization import BatchDecisionVerifier

            proof = BatchDecisionVerifier(
                assertion_public_keys, clock=lambda: now_seconds
            ).verify(signed_review)
        else:
            proof = verify_assertion(signed_review, public_keys=assertion_public_keys)
        if (
            proof.attempt_id != attempt_id
            or proof.scope_hash != scope_hash(scope)
            or proof.principal_id != scope.actor_id
            or proof.expires_at <= now_seconds
        ):
            deny()
        event = preauthorization["roster_decision"]
        receipt = preauthorization["preauthorization_receipt"]
        if not verify_staff_evidence_receipt(
            receipt,
            event,
            public_keys=receipt_public_keys,
            now=datetime.fromtimestamp(now_seconds, timezone.utc),
            max_age_seconds=300,
        ):
            deny()
        for name, value in {
            "event_type": (
                "samchat_registration_roster_decision_v3"
                if batch
                else "samchat_registration_roster_decision_v2"
            ),
            "tenant_id": scope.tenant_id,
            "tournament_edition_id": str(scope.edition_id),
            "tournament_slug": scope.tournament_slug,
            "team_id": str(scope.team_id),
            "session_id": str(scope.session_id),
            "draft_id": str(scope.draft_id),
            "draft_version": scope.draft_version,
            "policy_hash": scope.policy_hash,
            "decision": "AUTHORIZE_PENDING_MATERIALIZATION",
            "attempt_id": digest(
                {"domain": "ctt.capture.attempt.v2", "attempt_id": str(attempt_id)}
            ),
        }.items():
            if canonical(event[name]) != canonical(value):
                deny()
        if batch and (
            event.get("authorization_id") != str(proof.authorization_id)
            or event.get("member_proof_hash") != proof.proof_sha256
            or event.get("batch_context_hash")
            != digest(preauthorization["batch_context"])
        ):
            deny()
        pending = parse(pending_effect_bytes)
        if canonical(event["ordered_player_slots"]) != canonical(
            [r["roster_index"] for r in pending["players"]]
        ):
            deny()
        if len(event["ordered_staff_slots"]) != 2:
            deny()
        for group, expected in (
            (
                preauthorization["player_field_decisions"],
                event["ordered_player_field_decision_hashes"],
            ),
            (
                preauthorization["staff_field_decisions"],
                [
                    value
                    for slot in event["ordered_staff_slots"]
                    for value in slot["field_decision_hashes"]
                ],
            ),
        ):
            if (
                type(group) is not list
                or canonical([item["decision_hash"] for item in group])
                != canonical(expected)
                or any(
                    item["decision_hash"]
                    != digest({k: v for k, v in item.items() if k != "decision_hash"})
                    for item in group
                )
            ):
                deny()
        metadata = {}
        for original, slot in zip(pending["staff"], event["ordered_staff_slots"]):
            if canonical(
                (original["slot"], original["role"], original["presence"])
            ) != canonical(
                (
                    slot["slot"],
                    slot["role"],
                    slot["presence"],
                )
            ):
                deny()
        for row in pending["players"] + [
            s["row"] for s in pending["staff"] if s["row"] is not None
        ]:
            if (
                row["governance_decision_id"] != event["decision_id"]
                or row["roster_draft_binding"] != event["roster_draft_binding"]
                or row["preauthorization_receipt_id"] != receipt["receipt_id"]
                or row["finality_receipt_id"] is not None
                or row["governance_state"] != "PENDING_FINALITY"
            ):
                deny()
            metadata[row["id"]] = {
                "governance_decision_id": event["decision_id"],
                "roster_draft_binding": event["roster_draft_binding"],
                "preauthorization_receipt_id": receipt["receipt_id"],
                "finality_receipt_id": None,
            }
        if canonical(preauthorization["authority_metadata"]) != canonical(metadata):
            deny()
        return proof
    except Exception:
        deny()


def finality_authorization_body(
    *,
    kid,
    scope,
    protected_context,
    review_bytes,
    pending_effect_bytes,
    finality_effect_bytes,
    signed_review,
    preauthorization,
    records,
    review_key,
    effect_key,
):
    return {
        "schema": (
            "ctt.capture.finality-authorization.v3"
            if _is_batch(scope)
            else "ctt.capture.finality-authorization.v1"
        ),
        "kid": kid,
        "scope_hash": scope_hash(scope),
        "review_attempt_id": str(protected_context.review_attempt_id),
        "signed_review_hash": "sha256:" + hashlib.sha256(signed_review).hexdigest(),
        "preauthorization_event_hash": digest(preauthorization["roster_decision"]),
        "preauthorization_receipt_id": preauthorization["preauthorization_receipt"][
            "receipt_id"
        ],
        "database_transaction_id": protected_context.database_transaction_id,
        "event_attempt_id": protected_context.event_attempt_id,
        "nonce": str(protected_context.nonce),
        "review_plan_binding": bind_review_plan(review_bytes, review_key),
        "pending_effect_binding": bind_capture_effect(pending_effect_bytes, effect_key),
        "finality_effect_binding": bind_finality_effect(
            finality_effect_bytes, effect_key
        ),
        "ordered_finality_receipts": ordered_receipts(records),
        "issued_at": protected_context.issued_at,
        "expires_at": protected_context.expires_at,
    }


def check_finality_effect(
    *,
    scope,
    protected_context,
    review_bytes,
    pending_effect_bytes,
    finality_effect_bytes,
    signed_review,
    preauthorization,
    verified_finalities,
    review_key,
    effect_key,
    receipt_public_keys,
    assertion_public_keys,
    now_seconds,
):
    """Recompute effect and references from immutable input and signed receipts."""
    try:
        if type(protected_context) is not (
            BatchFinalityContext if _is_batch(scope) else FinalityContext
        ):
            deny()
        protected_context.verify(
            scope, protected_context.review_attempt_id, now_seconds=now_seconds
        )
        proof = verify_pre_authorization(
            preauthorization=preauthorization,
            scope=scope,
            attempt_id=protected_context.review_attempt_id,
            pending_effect_bytes=pending_effect_bytes,
            receipt_public_keys=receipt_public_keys,
            signed_review=signed_review,
            assertion_public_keys=assertion_public_keys,
            now_seconds=now_seconds,
        )
        if protected_context.expires_at != proof.expires_at:
            deny()
        if (
            bind_review_plan(review_bytes, review_key)
            != scope.staff_and_changes_binding
        ):
            deny()
        # Reuse the existing complete material comparison with the verified
        # metadata; finality never weakens the original PENDING authority.
        from .staff_capture_effect import (
            check_batch_effect_follows_review,
            check_effect_follows_review,
        )

        event, receipt = (
            preauthorization["roster_decision"],
            preauthorization["preauthorization_receipt"],
        )
        pending = parse(pending_effect_bytes)
        metadata = {
            row["id"]: {
                "governance_decision_id": event["decision_id"],
                "roster_draft_binding": event["roster_draft_binding"],
                "preauthorization_receipt_id": receipt["receipt_id"],
                "finality_receipt_id": None,
            }
            for row in pending["players"]
            + [s["row"] for s in pending["staff"] if s["row"] is not None]
        }
        (
            check_batch_effect_follows_review
            if _is_batch(scope)
            else check_effect_follows_review
        )(
            review_bytes=review_bytes,
            effect_bytes=pending_effect_bytes,
            scope=scope,
            review_key=review_key,
            authority_metadata=metadata,
        )
        records = verify_finality_bundle(
            bundle=verified_finalities,
            scope=scope,
            context=protected_context,
            pending_effect_bytes=pending_effect_bytes,
            preauthorization=preauthorization,
            effect_key=effect_key,
            receipt_public_keys=receipt_public_keys,
            now_seconds=now_seconds,
        )
        effect = parse(finality_effect_bytes)
        if set(effect) != {
            "schema",
            "scope_hash",
            "review_attempt_id",
            "database_transaction_id",
            "event_attempt_id",
            "database_identity",
            "database_oid",
            "pg_top_xid",
            "pending_effect_binding",
            "column_schema",
            "team",
            "players",
            "staff",
        }:
            deny()
        projection = {
            "team": effect["team"],
            "players": [r["active_row"] for r in effect["players"]],
            "staff": [
                {
                    "slot": original["slot"],
                    "role": original["role"],
                    "presence": original["presence"],
                    "row": item["active_row"],
                }
                for original, item in zip(pending["staff"], effect["staff"])
            ],
        }
        # Preserve any existing staff slot metadata from the pending contract.
        projection["staff"] = [
            dict(original) | {"row": item["active_row"]}
            for original, item in zip(pending["staff"], effect["staff"])
        ]
        rebuilt = freeze_finality_effect(
            scope=scope,
            protected_context=protected_context,
            pending_effect_bytes=pending_effect_bytes,
            observed_pending_rows=pending_rows(pending),
            verified_finalities=verified_finalities,
            active_projection_db_derived=projection,
            effect_key=effect_key,
            preauthorization=preauthorization,
            receipt_public_keys=receipt_public_keys,
        )
        if rebuilt != finality_effect_bytes:
            deny()
        return records
    except Exception:
        deny()


def verify_capture_finality_authorization(
    *,
    envelope,
    public_keys,
    scope,
    protected_context,
    attempt_id,
    nonce,
    review_bytes,
    pending_effect_bytes,
    finality_effect_bytes,
    signed_review,
    preauthorization,
    verified_finalities,
    review_key,
    effect_key,
    receipt_public_keys,
    assertion_public_keys,
    now_seconds,
) -> str:
    """Verify gate signature and all source commitments before the SQL door."""
    try:
        if type(envelope) is not bytes or not 2 <= len(envelope) <= 262144:
            deny()
        if (
            not isinstance(attempt_id, UUID)
            or protected_context.review_attempt_id != attempt_id
            or protected_context.nonce != nonce
        ):
            deny()
        obj = parse(envelope)
        if (
            set(obj) != {"body", "signature"}
            or type(obj["body"]) is not dict
            or set(obj["body"]) != BODY_KEYS
        ):
            deny()
        body = obj["body"]
        signature = base64.b64decode(obj["signature"], validate=True)
        if (
            len(signature) != 64
            or base64.b64encode(signature).decode() != obj["signature"]
        ):
            deny()
        Ed25519PublicKey.from_public_bytes(public_keys[body["kid"]]).verify(
            signature, finality_authorization_domain(scope) + canonical(body)
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
            review_key=review_key,
            effect_key=effect_key,
            receipt_public_keys=receipt_public_keys,
            assertion_public_keys=assertion_public_keys,
            now_seconds=now_seconds,
        )
        expected = finality_authorization_body(
            kid=body["kid"],
            scope=scope,
            protected_context=protected_context,
            review_bytes=review_bytes,
            pending_effect_bytes=pending_effect_bytes,
            finality_effect_bytes=finality_effect_bytes,
            signed_review=signed_review,
            preauthorization=preauthorization,
            records=records,
            review_key=review_key,
            effect_key=effect_key,
        )
        if canonical(body) != canonical(expected):
            deny()
        return "sha256:" + hashlib.sha256(envelope).hexdigest()
    except Exception:
        deny()


def _is_batch(scope):
    from .staff_batch_authorization import BatchReviewScope

    return type(scope) is BatchReviewScope


def scope_hash(scope):
    if _is_batch(scope):
        from .staff_batch_authorization import batch_scope_hash

        return batch_scope_hash(scope)
    return _individual_scope_hash(scope)


def finality_authorization_domain(scope):
    return (
        b"samchat.ctt.capture-finality-authorization.v3\0"
        if _is_batch(scope)
        else DOMAIN
    )
