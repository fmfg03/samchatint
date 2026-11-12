"""Fixed, transaction-owned PostgreSQL calls for protected batch review.

The public app receives no pool or table privileges. These calls are available
only to independently admitted private services; JSON does not select SQL or
confer authority.
"""

from __future__ import annotations

import base64
import json
from typing import Any, cast
from uuid import UUID

from .staff_capture_executor import CaptureDenied


class BatchDenied(CaptureDenied):
    """A bounded, public-safe denial; never carry database/source details."""

    def __init__(self, code: str = "SCOPE_DENIED") -> None:
        self.code = code
        super().__init__(code)


class BatchConflict(BatchDenied):
    def __init__(self) -> None:
        super().__init__("STALE_PROPOSAL")


def _json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (ValueError, TypeError) as exc:
        raise BatchDenied() from exc


def _decode(value: Any) -> Any:
    if type(value) is str:
        value = json.loads(value)
    if type(value) is not dict:
        raise BatchDenied()
    return value


class PostgresBatchReviewStore:
    """Private SQL adapter with one local transaction per fixed operation."""

    def __init__(self, pool, *, backend_pin: str):
        if type(backend_pin) is not str or not backend_pin:
            raise ValueError("batch backend pin required")
        self.pool = pool
        self.backend_pin = backend_pin

    async def _call(self, sql: str, *args) -> dict:
        try:
            async with self.pool.acquire() as conn:
                if conn.is_in_transaction():
                    raise BatchDenied()
                async with conn.transaction():
                    value = _decode(await conn.fetchval(sql, *args))
                    if value.get("backend_pin", self.backend_pin) != self.backend_pin:
                        raise BatchDenied()
                    return cast(dict, value)
        except BatchDenied:
            raise
        except Exception as exc:
            if getattr(exc, "sqlstate", None) in {"40001", "23505"}:
                raise BatchConflict() from exc
            if getattr(exc, "sqlstate", None) == "55000":
                raise BatchDenied("REVIEW_BLOCKED") from exc
            raise BatchDenied() from exc

    async def catalog(self, account_id: UUID) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_catalog($1)", account_id
        )

    async def preparation_context(
        self, account_id: UUID, edition_id: UUID, manifests: list[str]
    ) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_source_context($1,$2,$3::jsonb)",
            account_id,
            edition_id,
            _json(manifests),
        )

    async def create_run(self, account_id: UUID, run_id: UUID, payload: dict) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_create_run($1,$2,$3::jsonb)",
            account_id,
            run_id,
            _json(payload),
        )

    async def view(self, account_id: UUID, run_id: UUID, offset=0, limit=50) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_read_run($1,$2,$3,$4)",
            account_id,
            run_id,
            offset,
            limit,
        )

    async def private_run(self, account_id: UUID, run_id: UUID) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_private_run($1,$2)", account_id, run_id
        )

    async def register_sample(
        self,
        account_id: UUID,
        run_id: UUID,
        expected_version: int,
        candidate: bytes,
        attestations: list[dict],
    ) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_register_sample($1,$2,$3,$4,$5::jsonb)",
            account_id,
            run_id,
            expected_version,
            candidate,
            _json(attestations),
        )

    async def prepare_block(
        self,
        account_id: UUID,
        run_id: UUID,
        block_id: UUID,
        expected_version: int,
        member_ids: list[UUID],
        root_bytes: bytes,
    ) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_prepare_block($1,$2,$3,$4,$5::jsonb,$6)",
            account_id,
            run_id,
            block_id,
            expected_version,
            _json([str(value) for value in member_ids]),
            root_bytes,
        )

    async def private_block(self, account_id: UUID, block_id: UUID) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_private_block($1,$2)",
            account_id,
            block_id,
        )

    async def create_challenge(
        self,
        account_id: UUID,
        block_id: UUID,
        expected_version: int,
        challenge_id: UUID,
        challenge: bytes,
    ) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_create_challenge($1,$2,$3,$4,$5)",
            account_id,
            block_id,
            expected_version,
            challenge_id,
            challenge,
        )

    async def consent_context(self, challenge_id: UUID) -> dict:
        value = await self._call(
            "SELECT ctt_staff_private.batch_load_consent_context($1)", challenge_id
        )
        for name in (
            "root_bytes",
            "credential_id",
            "attested_credential",
            "user_handle",
            "challenge_bytes",
            "proof_bytes",
        ):
            if value.get(name) is not None:
                value[name] = base64.b64decode(value[name], validate=True)
        return value

    async def record_consent(self, challenge_id: UUID, proof: bytes) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_register_consent($1,$2)",
            challenge_id,
            proof,
        )

    async def recover_consent(self, account_id: UUID, block_id: UUID) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_recover_consent($1,$2)",
            account_id,
            block_id,
        )

    async def transition(
        self, account_id: UUID, block_id: UUID, expected_version: int, action: str
    ) -> dict:
        if action not in {"pause", "resume", "revoke"}:
            raise BatchDenied()
        sql = {
            "pause": "SELECT ctt_staff_private.batch_pause($1,$2,$3)",
            "resume": "SELECT ctt_staff_private.batch_resume($1,$2,$3)",
            "revoke": "SELECT ctt_staff_private.batch_revoke($1,$2,$3)",
        }[action]
        return await self._call(sql, account_id, block_id, expected_version)

    async def attempt(self, account_id: UUID, block_id: UUID, attempt_id: UUID) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_attempt_status($1,$2,$3)",
            account_id,
            block_id,
            attempt_id,
        )

    async def claim(self, worker_id: UUID) -> dict:
        return await self._call("SELECT ctt_staff_private.batch_claim($1)", worker_id)

    async def recover_job(self, worker_id: UUID, attempt_id: UUID, epoch: int) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_recover_job($1,$2,$3)",
            worker_id,
            attempt_id,
            epoch,
        )

    async def record_child(
        self,
        consent_id: UUID,
        member_id: UUID,
        worker_id: UUID,
        epoch: int,
        proof: bytes,
    ) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_issue_child($1,$2,$3,$4,$5)",
            consent_id,
            member_id,
            worker_id,
            epoch,
            proof,
        )

    async def budget_reserve(
        self, run_id: UUID, reservation_id: UUID, units: int
    ) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_reserve_budget($1,$2,$3)",
            run_id,
            reservation_id,
            units,
        )

    async def source_preparation_admit(
        self,
        preparation_id: UUID,
        inventory_bytes: bytes,
        profile_bytes: bytes,
        quote_bytes: bytes,
        budget_bytes: bytes,
        approval_ref: str,
    ) -> dict:
        """Operator-only admission of exact observation inputs and shared ceiling."""
        return await self._call(
            "SELECT ctt_staff_private.source_preparation_admit($1,$2,$3,$4,$5,$6)",
            preparation_id,
            inventory_bytes,
            profile_bytes,
            quote_bytes,
            budget_bytes,
            approval_ref,
        )

    async def source_preparation_context(self, preparation_id: UUID) -> dict:
        """Load a currently admitted private observation context, never a member."""
        return await self._call(
            "SELECT ctt_staff_private.source_preparation_context($1)",
            preparation_id,
        )

    async def source_preparation_budget_report(self, preparation_id: UUID) -> dict:
        """Audit shared incurred and held consumption without enabling requests."""
        return await self._call(
            "SELECT ctt_staff_private.source_preparation_budget_report($1)",
            preparation_id,
        )

    async def source_preparation_reserve(
        self, preparation_id: UUID, reservation_id: UUID, units: int
    ) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.source_preparation_reserve($1,$2,$3)",
            preparation_id,
            reservation_id,
            units,
        )

    async def source_preparation_finish(
        self,
        preparation_id: UUID,
        observations_bytes: bytes,
        report_bytes: bytes,
    ) -> dict:
        """Retain immutable source-bound observations without admitting documents."""
        return await self._call(
            "SELECT ctt_staff_private.source_preparation_finish($1,$2,$3)",
            preparation_id,
            observations_bytes,
            report_bytes,
        )

    async def source_preparation_bind_run(
        self, preparation_id: UUID, run_id: UUID, field_profile_bytes: bytes
    ) -> dict:
        """Bind funding to the exact admitted extraction profile and its quote."""
        return await self._call(
            "SELECT ctt_staff_private.source_preparation_bind_run($1,$2,$3)",
            preparation_id,
            run_id,
            field_profile_bytes,
        )

    async def source_preparation_revoke(self, preparation_id: UUID) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.source_preparation_revoke($1)",
            preparation_id,
        )

    async def source_budget_revoke(self, budget_id: UUID) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.source_budget_revoke($1)", budget_id
        )

    async def budget_reconcile(
        self, reservation_id: UUID, actual_units: int, receipt_hash: str
    ) -> dict:
        return await self._call(
            "SELECT ctt_staff_private.batch_reconcile_budget($1,$2,$3)",
            reservation_id,
            actual_units,
            receipt_hash,
        )

    async def verifier_context(self, kid, profile_hash):
        return await self._call(
            "SELECT ctt_staff_private.batch_verifier_context($1,$2)", kid, profile_hash
        )

    async def claim_analysis(self, worker_id):
        return await self._call(
            "SELECT ctt_staff_private.batch_claim_analysis($1)", worker_id
        )

    async def analysis_heartbeat(self, run_id, worker_id, epoch):
        return await self._call(
            "SELECT ctt_staff_private.batch_analysis_heartbeat($1,$2,$3)",
            run_id,
            worker_id,
            epoch,
        )

    async def complete_preparation(
        self, account_id, run_id, version, worker_id, epoch, payload
    ):
        return await self._call(
            "SELECT "
            "ctt_staff_private.batch_complete_preparation($1,$2,$3,$4,$5,$6::jsonb)",
            account_id,
            run_id,
            version,
            worker_id,
            epoch,
            _json(payload),
        )

    async def block_analysis(self, run_id, worker_id, epoch, code):
        return await self._call(
            "SELECT ctt_staff_private.batch_block_analysis($1,$2,$3,$4)",
            run_id,
            worker_id,
            epoch,
            code,
        )

    async def source_asset_context(self, account_id, session_id, asset_id):
        return await self._call(
            "SELECT ctt_staff_private.batch_source_asset_context($1,$2,$3)",
            account_id,
            session_id,
            asset_id,
        )

    async def private_capture_context(self, attempt_id):
        return await self._call(
            "SELECT ctt_staff_private.batch_capture_context($1)", attempt_id
        )

    async def private_result(self, attempt_id):
        return await self._call(
            "SELECT ctt_staff_private.batch_private_result($1)", attempt_id
        )

    async def project_rows(self, attempt_id, team, players):
        return await self._call(
            "SELECT "
            "ctt_staff_private.batch_project_capture_rows($1,$2::jsonb,$3::jsonb)",
            attempt_id,
            _json(team),
            _json(players),
        )

    async def stage_capture_payload(
        self,
        attempt_id,
        authorization_id,
        review,
        effect,
        evidence,
        preauth,
        gate,
        nonce,
    ):
        return await self._call(
            "SELECT "
            "ctt_staff_private.batch_stage_capture_payload($1,$2,$3,$4,$5,$6,$7,$8)",
            attempt_id,
            authorization_id,
            review,
            effect,
            evidence,
            preauth,
            gate,
            nonce,
        )

    async def mark_uncertain(
        self, worker_id, attempt_id, epoch, code="CAPTURE_UNCERTAIN"
    ):
        return await self._call(
            "SELECT ctt_staff_private.batch_mark_uncertain($1,$2,$3,$4)",
            worker_id,
            attempt_id,
            epoch,
            code,
        )

    async def member(self, account_id, run_id, member_id):
        return await self._call(
            "SELECT ctt_staff_private.batch_member_detail($1,$2,$3)",
            account_id,
            run_id,
            member_id,
        )

    async def propose_corrections(self, account_id, run_id, member_id, version, edits):
        return await self._call(
            "SELECT "
            "ctt_staff_private.batch_propose_member_corrections($1,$2,$3,$4,$5::jsonb)",
            account_id,
            run_id,
            member_id,
            version,
            _json(edits),
        )

    async def report_quality_issue(
        self, account_id, run_id, member_id, version, reason
    ):
        return await self._call(
            "SELECT ctt_staff_private.batch_report_quality_issue($1,$2,$3,$4,$5)",
            account_id,
            run_id,
            member_id,
            version,
            reason,
        )

    async def refresh_verifications(self, account_id, run_id, proofs):
        return await self._call(
            "SELECT ctt_staff_private.batch_refresh_verifications($1,$2,$3::jsonb)",
            account_id,
            run_id,
            _json(proofs),
        )

    async def load_finality_gate_context(self, attempt_id: UUID) -> dict:
        """Committed MVCC projection without waiting on writer-held row locks."""
        value = await self._call(
            "SELECT ctt_staff_private.batch_load_finality_gate_context($1)", attempt_id
        )
        for key in (
            "scope_bytes",
            "review_bytes",
            "pending_effect_bytes",
            "signed_review",
            "preauthorization_bytes",
            "evidence_bytes",
            "validation_bytes",
        ):
            value[key] = base64.b64decode(value[key], validate=True)
        return value
