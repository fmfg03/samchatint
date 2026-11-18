"""Private exact finality projection; no source of identity or SQL authority.

Only the trusted writer constructs FinalityContext from its locked connection.
An event/receipt is evidence of an attempted transition, never durable commit.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, NoReturn
from uuid import UUID

from .staff_capture_effect import bind_capture_effect
from .staff_capture_executor import CaptureDenied
from .staff_capture_source_adapter import SourceSnapshot
from .staff_governance_receipt import verify_staff_evidence_receipt
from .staff_review_challenge import (
    canonical_scope_bytes,
)
from .staff_review_challenge import scope_hash as _individual_scope_hash

MAX_BYTES = 8 * 1024 * 1024
EFFECT_DOMAIN = b"ctt.capture.finality-effect.v1\x00"
SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")


def deny() -> NoReturn:
    raise CaptureDenied("capture finality denied") from None


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def parse(raw: bytes) -> dict:
    try:
        if type(raw) is not bytes or not 2 <= len(raw) <= MAX_BYTES:
            deny()

        def pairs(items):
            result = {}
            for name, value in items:
                if name in result:
                    deny()
                result[name] = value
            return result

        value = json.loads(raw, object_pairs_hook=pairs)
        if type(value) is not dict or canonical(value) != raw:
            deny()
        return value
    except Exception:
        deny()


def bind_finality_effect(raw: bytes, key: bytes) -> str:
    if type(key) is not bytes or len(key) < 32:
        deny()
    parse(raw)
    return (
        "hmac-sha256:" + hmac.new(key, EFFECT_DOMAIN + raw, hashlib.sha256).hexdigest()
    )


def database_transaction_id(identity: str, oid: int, xid: int) -> str:
    if (
        type(identity) is not str
        or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", identity)
        or type(oid) is not int
        or not 0 < oid < 2**32
        or type(xid) is not int
        or not 0 < xid < 2**64
    ):
        deny()
    frame = (
        b"ctt.capture.database-transaction.v1\0"
        + identity.encode("ascii")
        + b"\0"
        + str(oid).encode("ascii")
        + b"\0"
        + str(xid).encode("ascii")
    )
    return "samchat-tx-" + hashlib.sha256(frame).hexdigest()[:24]


def finality_attempt_id(review_attempt_id: UUID, transaction_id: str) -> str:
    if not isinstance(review_attempt_id, UUID) or not re.fullmatch(
        r"samchat-tx-[0-9a-f]{24}", transaction_id
    ):
        deny()
    return digest(
        {
            "domain": "ctt.capture.finality-attempt.v1",
            "review_attempt_id": str(review_attempt_id),
            "database_transaction_id": transaction_id,
        }
    )


@dataclass(frozen=True, repr=False)
class FinalityContext:
    """Trusted in-process state; this object is never accepted from HTTP JSON."""

    database_identity: str
    database_oid: int
    pg_top_xid: int
    review_attempt_id: UUID
    issued_at: int
    expires_at: int
    nonce: UUID
    snapshot: SourceSnapshot = field(repr=False)
    blocking_incident_count: int
    cas_succeeded: bool

    @property
    def database_transaction_id(self) -> str:
        return database_transaction_id(
            self.database_identity, self.database_oid, self.pg_top_xid
        )

    @property
    def event_attempt_id(self) -> str:
        return finality_attempt_id(self.review_attempt_id, self.database_transaction_id)

    def verify(self, scope, attempt_id, *, now_seconds=None) -> None:
        from time import time

        now = int(time()) if now_seconds is None else now_seconds
        if (
            type(self) is not FinalityContext
            or not isinstance(self.review_attempt_id, UUID)
            or self.review_attempt_id != attempt_id
            or not isinstance(self.nonce, UUID)
            or type(self.issued_at) is not int
            or type(self.expires_at) is not int
            or not 0 < self.expires_at - self.issued_at <= 300
            or type(now) is not int
            or not self.issued_at <= now < self.expires_at
            or self.cas_succeeded is not True
            or type(self.blocking_incident_count) is not int
            or self.blocking_incident_count != 0
            or type(self.snapshot) is not SourceSnapshot
            or self.snapshot.scope_bytes != canonical_scope_bytes(scope)
        ):
            deny()
        self.database_transaction_id
        self.snapshot.verify_unchanged()


@dataclass(frozen=True, repr=False)
class VerifiedFinalityBundle:
    """Immutable bytes, always cryptographically reverified before consumption."""

    payload_bytes: bytes = field(repr=False)

    @property
    def records(self):
        return parse(self.payload_bytes)["records"]


def pending_rows(effect: dict) -> dict:
    return {
        "team": effect["team"],
        "players": effect["players"],
        "staff": effect["staff"],
    }


def members(effect: dict):
    for row in effect["players"]:
        if type(row["roster_index"]) is not int or not 1 <= row["roster_index"] <= 35:
            deny()
        yield "PLAYER", row["roster_index"], None, row
    for slot in effect["staff"]:
        if type(slot["slot"]) is not int or slot["slot"] not in (1, 2):
            deny()
        if slot["row"] is not None:
            yield "STAFF", slot["slot"], slot["role"], slot["row"]


def operation_id(event: dict) -> str:
    keys = [
        "event_type",
        "tenant_id",
        "session_id",
        "draft_id",
        "team_id",
        "tournament_edition_id",
        "attempt_id",
        "draft_version",
    ]
    subject = (
        "player_id"
        if event["event_type"]
        in (
            "samchat_registration_player_finality_attestation_v2",
            "samchat_registration_player_finality_attestation_v3",
        )
        else "staff_id"
    )
    return digest({key: event[key] for key in keys + [subject]})


def _mac(key: bytes, domain: str, value: Any) -> str:
    return (
        "hmac-sha256:"
        + hmac.new(
            key, domain.encode() + b"\0" + canonical(value), hashlib.sha256
        ).hexdigest()
    )


def finality_event(
    *, scope, context, pending, kind, slot, role, row, preauthorization, effect_key
) -> dict:
    """Derive the entire non-PII event from exact private inputs."""
    roster = preauthorization["roster_decision"]
    receipt = preauthorization["preauthorization_receipt"]
    protected_scope = {
        "tenant_id": scope.tenant_id,
        "tournament_edition_id": str(scope.edition_id),
        "tournament_slug": scope.tournament_slug,
        "team_id": str(scope.team_id),
        "session_id": str(scope.session_id),
        "draft_id": str(scope.draft_id),
        "draft_version": scope.draft_version,
        "policy_hash": scope.policy_hash,
    }
    if any(
        canonical(roster[key]) != canonical(value)
        for key, value in protected_scope.items()
    ):
        deny()
    base = {
        key: roster[key]
        for key in [
            "tenant_id",
            "tournament_edition_id",
            "tournament_slug",
            "team_id",
            "session_id",
            "draft_id",
            "draft_version",
            "policy_hash",
            "workflow_id",
            "effective_sal",
            "policy_id",
            "policy_version",
        ]
    }
    base.update(
        event_type="samchat_registration_"
        + kind.lower()
        + (
            "_finality_attestation_v3"
            if _is_batch(scope)
            else "_finality_attestation_v2"
        ),
        timestamp=datetime.fromtimestamp(context.issued_at, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        attempt_id=context.event_attempt_id,
        roster_draft_binding=roster["roster_draft_binding"],
        preauthorization_receipt_id=receipt["receipt_id"],
        database_transaction_id=context.database_transaction_id,
        cas_succeeded=True,
        blocking_incident_count=0,
        provisional_row_binding=_mac(
            effect_key, "ctt.capture.finality-provisional-row.v1", row
        ),
        post_execution_row_binding=_mac(
            effect_key, "ctt.capture.finality-observed-row.v1", row
        ),
        decision="ACTIVATE_" + kind,
        reason_codes=["CANDIDATE_POLICY_SATISFIED"],
    )
    if kind == "PLAYER":
        source = next(
            item for item in pending["player_sources"] if item["player_id"] == row["id"]
        )
        base.update(
            player_id=row["id"],
            player_slot=slot,
            player_entry_binding=_mac(
                effect_key,
                "ctt.capture.player-entry.v2",
                {
                    "scope_hash": scope_hash(scope),
                    "tenant_id": scope.tenant_id,
                    "row_id": row["id"],
                    "roster_index": slot,
                    "row": row,
                    "source": source,
                },
            ),
        )
    else:
        source_slot = next(
            item
            for item in roster["ordered_staff_slots"]
            if type(item["slot"]) is int and item["slot"] == slot
        )
        if source_slot["presence"] != "PRESENT" or source_slot["role"] != role:
            deny()
        base.update(
            staff_id=row["id"],
            staff_slot=slot,
            staff_role=role,
            staff_entry_binding=source_slot["staff_entry_binding"],
        )
    base["operation_id"] = operation_id(base)
    base["attestation_id"] = digest(
        {
            "domain": "ctt.capture.finality-attestation.v1",
            "operation_id": base["operation_id"],
            "row_binding": base["post_execution_row_binding"],
        }
    )
    base["attestation_hash"] = digest(base)
    return base


def verify_finality_bundle(
    *,
    bundle,
    scope,
    context,
    pending_effect_bytes,
    preauthorization,
    effect_key,
    receipt_public_keys,
    now_seconds=None,
) -> list:
    try:
        context.verify(scope, context.review_attempt_id, now_seconds=now_seconds)
        if type(bundle) is not VerifiedFinalityBundle:
            deny()
        obj = parse(bundle.payload_bytes)
        if (
            set(obj) != {"schema", "records"}
            or obj["schema"] != "ctt.capture.finality-bundle.v1"
            or type(obj["records"]) is not list
        ):
            deny()
        pending = parse(pending_effect_bytes)
        expected = list(members(pending))
        if len(obj["records"]) != len(expected):
            deny()
        now = (
            datetime.fromtimestamp(now_seconds, timezone.utc)
            if now_seconds is not None
            else None
        )
        for record, (kind, slot, role, row) in zip(obj["records"], expected):
            if (
                type(record) is not dict
                or set(record) != {"kind", "row_id", "slot", "event", "receipt"}
                or type(record["slot"]) is not int
                or canonical((record["kind"], record["row_id"], record["slot"]))
                != canonical((kind, row["id"], slot))
            ):
                deny()
            event = finality_event(
                scope=scope,
                context=context,
                pending=pending,
                kind=kind,
                slot=slot,
                role=role,
                row=row,
                preauthorization=preauthorization,
                effect_key=effect_key,
            )
            if canonical(record["event"]) != canonical(
                event
            ) or not verify_staff_evidence_receipt(
                record["receipt"],
                event,
                public_keys=receipt_public_keys,
                now=now,
                max_age_seconds=300,
            ):
                deny()
        return obj["records"]
    except Exception:
        deny()


def ordered_receipts(records: list) -> list:
    return [
        {
            "kind": r["kind"],
            "row_id": r["row_id"],
            "slot": r["slot"],
            "event_hash": r["receipt"]["event_hash"],
            "receipt_id": r["receipt"]["receipt_id"],
            "operation_id": r["event"]["operation_id"],
        }
        for r in records
    ]


def freeze_finality_effect(
    *,
    scope,
    protected_context,
    pending_effect_bytes,
    observed_pending_rows,
    verified_finalities,
    active_projection_db_derived,
    effect_key,
    preauthorization,
    receipt_public_keys,
) -> bytes:
    """Freeze only exact allowed transitions, after verifying every final receipt."""
    try:
        pending = parse(pending_effect_bytes)
        if canonical(observed_pending_rows) != canonical(pending_rows(pending)):
            deny()
        records = verify_finality_bundle(
            bundle=verified_finalities,
            scope=scope,
            context=protected_context,
            pending_effect_bytes=pending_effect_bytes,
            preauthorization=preauthorization,
            effect_key=effect_key,
            receipt_public_keys=receipt_public_keys,
        )
        active = active_projection_db_derived
        if (
            type(active) is not dict
            or set(active) != {"team", "players", "staff"}
            or canonical(active["team"]) != canonical(pending["team"])
            or len(active["players"]) != len(pending["players"])
            or len(active["staff"]) != len(pending["staff"])
        ):
            deny()
        result = {
            "schema": (
                "ctt.capture.finality-effect.v3"
                if _is_batch(scope)
                else "ctt.capture.finality-effect.v1"
            ),
            "scope_hash": scope_hash(scope),
            "review_attempt_id": str(protected_context.review_attempt_id),
            "database_transaction_id": protected_context.database_transaction_id,
            "event_attempt_id": protected_context.event_attempt_id,
            "database_identity": protected_context.database_identity,
            "database_oid": protected_context.database_oid,
            "pg_top_xid": str(protected_context.pg_top_xid),
            "pending_effect_binding": bind_capture_effect(
                pending_effect_bytes, effect_key
            ),
            "column_schema": "ctt.capture.modeled-columns.v1",
            "team": pending["team"],
            "players": [],
            "staff": [],
        }
        by_id = {r["row_id"]: r for r in records}
        for kind, slot, role, row in members(pending):
            proposed = (
                next((r for r in active["players"] if r["id"] == row["id"]), None)
                if kind == "PLAYER"
                else active["staff"][slot - 1]["row"]
            )
            record = by_id[row["id"]]
            if type(proposed) is not dict or set(proposed) != set(row):
                deny()
            allowed = {"governance_state", "finality_receipt_id", "updated_at"}
            if kind == "PLAYER":
                allowed |= {"postcommit_revision", "postcommit_snapshot_hash"}
                if (
                    type(proposed["postcommit_revision"]) is not int
                    or proposed["postcommit_revision"] != row["postcommit_revision"] + 1
                    or not SHA.fullmatch(proposed["postcommit_snapshot_hash"])
                ):
                    deny()
            if (
                row["governance_state"] != "PENDING_FINALITY"
                or row["finality_receipt_id"] is not None
                or proposed["governance_state"] != "ACTIVE"
                or proposed["finality_receipt_id"] != record["receipt"]["receipt_id"]
                or canonical({k: row[k] for k in row if k not in allowed})
                != canonical({k: proposed[k] for k in proposed if k not in allowed})
            ):
                deny()
            updated = datetime.fromtimestamp(protected_context.issued_at, timezone.utc)
            if kind == "PLAYER":
                updated = updated.replace(tzinfo=None)
            if "updated_at" in row and proposed["updated_at"] != updated.isoformat():
                deny()
            item = {
                "row_id": row["id"],
                "slot": slot,
                "pending_row": row,
                "active_row": proposed,
                "event": record["event"],
                "receipt": record["receipt"],
            }
            if kind == "PLAYER":
                item["kind"] = kind
                result["players"].append(item)
            else:
                item.update(role=role, presence="PRESENT")
                result["staff"].append(item)
        if [r["id"] for r in active["players"]] != [
            r["id"] for r in pending["players"]
        ]:
            deny()
        for original, projected in zip(pending["staff"], active["staff"]):
            if canonical(
                {k: v for k, v in original.items() if k != "row"}
            ) != canonical({k: v for k, v in projected.items() if k != "row"}):
                deny()
            if original["presence"] == "ABSENT":
                if projected["row"] is not None:
                    deny()
                result["staff"].append(
                    {
                        "slot": original["slot"],
                        "role": original["role"],
                        "presence": "ABSENT",
                        "row_id": None,
                        "pending_row": None,
                        "active_row": None,
                        "event": None,
                        "receipt": None,
                    }
                )
        result["staff"].sort(key=lambda r: r["slot"])
        raw = canonical(result)
        parse(raw)
        return raw
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


@dataclass(frozen=True, repr=False)
class BatchFinalityContext(FinalityContext):
    """Transaction-bound source-bundle context; never accepted as JSON input."""

    def verify(self, scope, attempt_id, *, now_seconds=None):
        from time import time

        from .staff_batch_authorization import canonical_batch_scope_bytes
        from .staff_batch_source_adapter import SourceSnapshotV2

        now = int(time()) if now_seconds is None else now_seconds
        if (
            type(self) is not BatchFinalityContext
            or not _is_batch(scope)
            or type(self.review_attempt_id) is not UUID
            or self.review_attempt_id != attempt_id
            or type(self.nonce) is not UUID
            or type(self.issued_at) is not int
            or type(self.expires_at) is not int
            or not 0 < self.expires_at - self.issued_at <= 300
            or type(now) is not int
            or not self.issued_at <= now < self.expires_at
            or self.cas_succeeded is not True
            or type(self.blocking_incident_count) is not int
            or self.blocking_incident_count != 0
            or type(self.snapshot) is not SourceSnapshotV2
            or self.snapshot.scope_bytes != canonical_batch_scope_bytes(scope)
        ):
            deny()
        self.database_transaction_id
        self.snapshot.verify_unchanged()
