"""Private deterministic checks and byte-bound machine attestations.

This component is configured inside the private workflow. It cannot accredit
itself, grant capture authority, or turn a browser boolean into human review.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
import unicodedata
from datetime import date
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .staff_batch_authorization import sign_batch_proof
from .staff_batch_contracts import digest
from .staff_batch_store import BatchDenied

_SHA = re.compile(r"[0-9a-f]{64}\Z")


def normalize_observation(value: Any, *, field: str) -> Any:
    """No name repair, identity inference, ambiguous dates, or photo omission."""
    if value is None:
        return None
    if type(value) is not str:
        raise BatchDenied("REVIEW_BLOCKED")
    normalized = " ".join(unicodedata.normalize("NFC", value).split())
    if not normalized:
        return None
    if field.endswith("birth_date"):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
            try:
                return date.fromisoformat(normalized).isoformat()
            except ValueError as exc:
                raise BatchDenied("REVIEW_BLOCKED") from exc
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", normalized):
            day, month, year = map(int, normalized.split("/"))
            try:
                return date(year, month, day).isoformat()
            except ValueError as exc:
                raise BatchDenied("REVIEW_BLOCKED") from exc
        raise BatchDenied("REVIEW_BLOCKED")
    return normalized


class BatchPrivateVerifier:
    """Sign exact retained checks only under a currently admitted verifier key."""

    def __init__(
        self, *, source_reader, store, signing_key, kid, profile_hash, clock=time.time
    ):
        if (
            not isinstance(signing_key, Ed25519PrivateKey)
            or type(kid) is not str
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", kid)
            or type(profile_hash) is not str
            or not _SHA.fullmatch(profile_hash)
            or not callable(getattr(source_reader, "verification_input", None))
        ):
            raise ValueError("private batch verifier configuration required")
        self._reader, self._store, self._key = source_reader, store, signing_key
        self.kid, self.profile_hash, self._clock = kid, profile_hash, clock

    def validate_channel_key(self, channel_key: bytes) -> None:
        private = self._key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        if hmac.compare_digest(private, channel_key):
            raise ValueError("separate batch channel key required")

    async def attest_members(self, run: dict, candidate: dict) -> list[dict]:
        if run["profile_hash"] != self.profile_hash:
            raise BatchDenied("REVIEW_BLOCKED")
        admitted = await self._store.verifier_context(self.kid, self.profile_hash)
        own_public = self._key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        if not hmac.compare_digest(
            own_public, base64.b64decode(admitted["public_key"], validate=True)
        ):
            raise BatchDenied()
        qualification_hash = digest(candidate)
        output = []
        for member in run["members"]:
            if member["status"] != "ready":
                continue
            # Reader opens its CAS snapshots, checks before/after hashes and
            # returns retained observations. It never accepts browser fields.
            # The verifier selects the admitted profile. A cached snapshot may
            # not nominate a different profile for replaying its own evidence.
            evidence = await self._reader.verification_input(
                {**member, "verification_profile_hash": self.profile_hash}
            )
            readings = evidence.get("readings")
            expected = evidence.get("expected_fields")
            proposed = evidence.get("proposed_fields")
            if (
                type(readings) is not list
                or len(readings) != 2
                or type(expected) is not list
                or not expected
                or any(type(field) is not str for field in expected)
                or len(set(expected)) != len(expected)
                or type(proposed) is not dict
                or set(proposed) != set(expected)
            ):
                raise BatchDenied("REVIEW_BLOCKED")
            if any(
                type(r) is not dict
                or set(r) != {"method", "input_hash", "fields"}
                or type(r["method"]) is not str
                or not r["method"]
                or type(r["input_hash"]) is not str
                or not _SHA.fullmatch(r["input_hash"])
                or type(r["fields"]) is not dict
                or set(r["fields"]) != set(expected)
                for r in readings
            ):
                raise BatchDenied("REVIEW_BLOCKED")
            if readings[0]["input_hash"] == readings[1]["input_hash"]:
                raise BatchDenied("REVIEW_BLOCKED")
            from .staff_batch_corrections import edit_overrides

            human_edits = member.get("human_edits", [])
            if type(human_edits) is not list:
                raise BatchDenied("REVIEW_BLOCKED")
            for record in human_edits:
                if (
                    type(record) is not dict
                    or record.get("principal_id") != run.get("principal_id")
                    or record.get("source_pages") != member["source_pages"]
                    or type(record.get("edits")) is not list
                ):
                    raise BatchDenied("REVIEW_BLOCKED")
            overrides = edit_overrides(human_edits, proposed)
            for field in list(overrides):
                if field.startswith("players.") and field.endswith(".presence"):
                    index = int(field.split(".")[1])
                    analysis = member.get(
                        "frozen_snapshot", member.get("snapshot", {})
                    ).get("analysis", {})
                    slot = analysis.get("players", [])[index - 1]["slot_ref"]
                    match = re.fullmatch(r"page:([1-4]):jugador_([1-9][0-9]*)", slot)
                    if match is None:
                        raise BatchDenied("REVIEW_BLOCKED")
                    overrides[f"slots.{match[1]}.{match[2]}.presence"] = overrides[
                        field
                    ]
            if set(overrides) - set(expected):
                raise BatchDenied("REVIEW_BLOCKED")
            checks = []
            for field in expected:
                actual = normalize_observation(proposed[field], field=field)
                if field in overrides:
                    if actual != normalize_observation(overrides[field], field=field):
                        raise BatchDenied("REVIEW_BLOCKED")
                else:
                    values = [
                        normalize_observation(r["fields"][field], field=field)
                        for r in readings
                    ]
                    if values[0] != values[1] or actual != values[0]:
                        raise BatchDenied("REVIEW_BLOCKED")
                if field in evidence.get("required_fields", ()) and actual is None:
                    raise BatchDenied("REVIEW_BLOCKED")
                checks.append({"field": field, "value_hash": digest(actual)})
            pages_hash = digest(member["source_pages"])
            if evidence.get("source_binding_hash") != pages_hash:
                raise BatchDenied("SOURCE_UNAVAILABLE")
            now = int(self._clock())
            expiry = min(now + 3600, admitted["not_after_seconds"])
            if expiry <= now:
                raise BatchDenied("REVIEW_EXPIRED")
            body = {
                "schema": "ctt.batch.verification.v1",
                "kid": self.kid,
                "document_id": member["document_id"],
                "manifest_sha256": member["manifest_sha256"],
                "policy_hash": member["policy_hash"],
                "profile_hash": self.profile_hash,
                "scope_hash": member["scope_hash"],
                "payload_hash": member["payload_hash"],
                "source_binding_hash": pages_hash,
                "human_edits_hash": digest(member.get("human_edits", [])),
                "qualification_hash": qualification_hash,
                "checks_hash": digest({"checks": checks, "readings": readings}),
                "issued_at": now,
                "expires_at": expiry,
                "verified_by_human": False,
            }
            proof = sign_batch_proof(body, signing_key=self._key)
            output.append(
                {
                    "body": body,
                    "proof_hash": hashlib.sha256(proof).hexdigest(),
                    "proof_base64": base64.b64encode(proof).decode("ascii"),
                }
            )
        return output
