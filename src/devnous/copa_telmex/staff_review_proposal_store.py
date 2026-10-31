"""Private, transaction-owned storage for source-bound review proposals.

Only a separately registered review service may use this adapter. It does not
accept browser authority, register identities, or grant capture privileges.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from uuid import UUID

from .draft_versioning import draft_content_hash
from .staff_capture_executor import CaptureDenied
from .staff_review_challenge import parse_canonical_scope


class ProposalConflict(CaptureDenied):
    """The proposal or draft head changed; a fresh preview is required."""

    code = "STALE_PROPOSAL"


class ProposalAuthorityUnavailable(CaptureDenied):
    """Independent reviewer accreditation or enrollment is still required."""

    code = "ENROLLMENT_REQUIRED"


def _canonical(raw: bytes) -> Any:
    if type(raw) is not bytes or not 2 <= len(raw) <= 4_194_304:
        raise CaptureDenied("CTT proposal bytes denied")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        if type(value) is not dict or encoded != raw:
            raise ValueError("canonical")
        return value
    except (ValueError, TypeError, UnicodeError) as exc:
        raise CaptureDenied("CTT proposal bytes denied") from exc


def _decode(value: Any) -> dict | None:
    if value is None:
        return None
    if type(value) is str:
        value = json.loads(value)
    if type(value) is not dict:
        raise CaptureDenied("CTT proposal response denied")
    result = dict(value)
    for key in tuple(result):
        if (
            key.endswith("_bytes")
            or key in {"signed_review", "credential_data", "user_handle"}
        ) and result[key] is not None:
            if type(result[key]) is not str:
                raise CaptureDenied("CTT proposal response denied")
            result[key] = base64.b64decode("".join(result[key].split()), validate=True)
    return result


class PostgresReviewProposalStore:
    """Execute fixed SQL admissions in independent PostgreSQL transactions."""

    def __init__(self, pool, *, backend_pin: str | None = None):
        self._pool = pool
        self._backend_pin = backend_pin

    async def _call(self, sql: str, *args) -> dict | None:
        try:
            async with self._pool.acquire() as conn:
                if conn.is_in_transaction():
                    raise CaptureDenied("CTT proposal transaction denied")
                async with conn.transaction():
                    result = _decode(await conn.fetchval(sql, *args))
                    if (
                        result
                        and self._backend_pin is not None
                        and "backend_pin" in result
                        and result["backend_pin"] != self._backend_pin
                    ):
                        raise CaptureDenied("CTT proposal backend denied")
                    return result
        except CaptureDenied:
            raise
        except Exception as exc:
            if (
                sql == "SELECT ctt_staff_private.load_review_proposal_context($1,$2)"
                and getattr(exc, "sqlstate", None) == "42501"
                and getattr(exc, "message", None)
                in {
                    "CTT review authority unavailable",
                    "CTT review credential unavailable",
                }
            ):
                raise ProposalAuthorityUnavailable(
                    "CTT review enrollment required"
                ) from exc
            if getattr(exc, "sqlstate", None) == "40001":
                raise ProposalConflict("CTT proposal head changed") from exc
            raise CaptureDenied("CTT proposal storage denied") from exc

    async def _required_call(self, sql: str, *args) -> dict:
        result = await self._call(sql, *args)
        if result is None:
            raise CaptureDenied("CTT proposal required response unavailable")
        return result

    async def resolve_source_scope(self, session_id: UUID) -> dict:
        return await self._required_call(
            "SELECT ctt_staff_private.resolve_review_source_scope($1)", session_id
        )

    async def load_context(self, account_id: UUID, session_id: UUID) -> dict:
        context = await self._call(
            "SELECT ctt_staff_private.load_review_proposal_context($1,$2)",
            account_id,
            session_id,
        )
        if (
            not context
            or draft_content_hash(context["raw_snapshot"])
            != context["draft"]["content_hash"]
        ):
            raise CaptureDenied("CTT proposal raw draft hash denied")
        if (
            "existing_photo_records" not in context
            or type(context["existing_photo_records"]) is not list
        ):
            raise CaptureDenied("CTT proposal photo context denied")
        return context

    async def register_proposal(
        self,
        account_id: UUID,
        session_id: UUID,
        expected_head: UUID | None,
        proposal_id: UUID,
        scope_bytes: bytes,
        review_bytes: bytes,
        evidence_bytes: bytes,
        preview_bytes: bytes,
        validation_bytes: bytes,
        raw_snapshot_bytes: bytes,
        capture_nonce: UUID,
    ) -> dict:
        scope = parse_canonical_scope(scope_bytes)
        for raw in (review_bytes, evidence_bytes, preview_bytes, validation_bytes):
            _canonical(raw)
        snapshot = _canonical(raw_snapshot_bytes)
        if draft_content_hash(snapshot) != scope.draft_content_hash:
            raise CaptureDenied("CTT proposal raw draft hash denied")
        return await self._required_call(
            (
                "SELECT ctt_staff_private.register_review_proposal($1,$2,$3,$"
                "4,$5,$6,$7,$8,$9,$10,$11)"
            ),
            account_id,
            session_id,
            expected_head,
            proposal_id,
            scope_bytes,
            review_bytes,
            evidence_bytes,
            preview_bytes,
            validation_bytes,
            raw_snapshot_bytes,
            capture_nonce,
        )

    async def read_proposal(
        self, account_id: UUID, session_id: UUID, proposal_id: UUID | None = None
    ) -> dict | None:
        result = await self._call(
            "SELECT ctt_staff_private.read_review_proposal($1,$2,$3)",
            account_id,
            session_id,
            proposal_id,
        )
        if result:
            scope = parse_canonical_scope(result["scope_bytes"])
            if (
                draft_content_hash(_canonical(result["raw_snapshot_bytes"]))
                != scope.draft_content_hash
            ):
                raise CaptureDenied("CTT proposal raw draft hash denied")
        return result

    async def create_challenge(
        self,
        account_id: UUID,
        session_id: UUID,
        proposal_id: UUID,
        random32: bytes,
        issued_at: int,
        expires_at: int,
    ) -> dict:
        if (
            type(random32) is not bytes
            or len(random32) != 32
            or type(issued_at) is not int
            or type(expires_at) is not int
        ):
            raise CaptureDenied("CTT proposal challenge denied")
        return await self._required_call(
            "SELECT ctt_staff_private.create_proposal_challenge($1,$2,$3,$4,$5,$6)",
            account_id,
            session_id,
            proposal_id,
            random32,
            issued_at,
            expires_at,
        )

    async def cancel(
        self, account_id: UUID, session_id: UUID, expected_proposal_id: UUID
    ) -> None:
        await self._call(
            "SELECT ctt_staff_private.cancel_review_proposal($1,$2,$3)",
            account_id,
            session_id,
            expected_proposal_id,
        )

    async def register_payload(
        self,
        account_id: UUID,
        session_id: UUID,
        attempt_id: UUID,
        effect_bytes: bytes,
        envelope_bytes: bytes,
        nonce: UUID,
        preauth_bytes: bytes,
        evidence_bytes: bytes,
        signed_review_bytes: bytes,
    ) -> None:
        for raw in (
            effect_bytes,
            envelope_bytes,
            preauth_bytes,
            evidence_bytes,
            signed_review_bytes,
        ):
            _canonical(raw)
        await self._call(
            (
                "SELECT ctt_staff_private.register_proposal_capture_payload($"
                "1,$2,$3,$4,$5,$6,$7,$8,$9)"
            ),
            account_id,
            session_id,
            attempt_id,
            effect_bytes,
            envelope_bytes,
            nonce,
            preauth_bytes,
            evidence_bytes,
            signed_review_bytes,
        )

    async def project_capture_rows(
        self,
        account_id: UUID,
        session_id: UUID,
        attempt_id: UUID,
        team: dict,
        players: list[dict],
    ) -> dict:
        return await self._required_call(
            (
                "SELECT ctt_staff_private.project_review_capture_rows($1,$2,$"
                "3,$4::jsonb,$5::jsonb)"
            ),
            account_id,
            session_id,
            attempt_id,
            json.dumps(team, ensure_ascii=False, allow_nan=False),
            json.dumps(players, ensure_ascii=False, allow_nan=False),
        )

    async def status(
        self, account_id: UUID, session_id: UUID, attempt_id: UUID
    ) -> dict:
        """Return private recovery data; callers must omit signed_review publicly."""
        return await self._required_call(
            "SELECT ctt_staff_private.review_proposal_status($1,$2,$3)",
            account_id,
            session_id,
            attempt_id,
        )

    async def load_finality_gate_context(self, attempt_id: UUID) -> dict:
        """Read committed Gate inputs without waiting on capture row locks."""
        return await self._required_call(
            "SELECT ctt_staff_private.load_review_finality_gate_context($1)",
            attempt_id,
        )

    async def load_signed_proof(
        self, account_id: UUID, session_id: UUID, attempt_id: UUID
    ) -> bytes:
        result = await self.status(account_id, session_id, attempt_id)
        proof: object = result.get("signed_review")
        if type(proof) is not bytes:
            raise CaptureDenied("CTT proposal proof unavailable")
        return proof
