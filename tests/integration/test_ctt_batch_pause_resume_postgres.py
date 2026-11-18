"""Recover paused jobs without losing their canonical attempt or lease fencing."""

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
import test_ctt_batch_acceptance_postgres as acceptance
from test_ctt_full_runtime_postgres import full_pg as _full_pg

from devnous.copa_telmex.staff_batch_authorization import BatchAuthorizationDenied
from devnous.copa_telmex.staff_batch_store import BatchDenied

pytest_plugins = ("test_ctt_staff_security_postgres",)
full_pg = _full_pg


@pytest.mark.parametrize("outcome", ["resume", "revoke", "expire", "committed"])
def test_paused_recovery_preserves_attempt_and_terminal_fences(
    full_pg, tmp_path, monkeypatch, outcome
):
    """Use real retained sources, consent signatures, child proofs and SQL roles."""
    original_capture = acceptance.canonical_captures
    original_qualify = acceptance.qualify_and_consent

    async def qualify(case, analyzed):
        if outcome == "expire":
            # Bound the genuinely signed parent by a short authority deadline.
            await case["admin"].execute(
                "UPDATE ctt_staff_private.review_batch_capabilities SET "
                "valid_until=clock_timestamp()+interval '8 seconds'"
            )
        return await original_qualify(case, analyzed)

    async def scenario(consent_case, capture_path, **kwargs):
        case = consent_case["case"]
        admin, store = case["admin"], case["store"]
        account = UUID(case["context"]["account_id"])
        block = UUID(consent_case["block"]["block_id"])
        job, worker = consent_case["job"], consent_case["worker_id"]
        attempt, member = UUID(job["attempt_id"]), UUID(job["member_id"])
        old_body = json.loads(consent_case["child"])["body"]
        worker_store = consent_case["worker_store"]

        if outcome == "committed":
            await original_capture(consent_case, capture_path, **kwargs)
            result = await admin.fetchrow(
                "SELECT result_id,team_id FROM ctt_staff_private.capture_results "
                "WHERE attempt_id=$1",
                attempt,
            )
            assert result is not None
            # Lose only the mutable dispatch projection, keeping actual immutable
            # canonical results/consumptions. Recovery must reconcile these first.
            epoch = await admin.fetchval(
                "UPDATE ctt_staff_private.review_batch_dispatch_slot SET "
                "member_id=$1,worker_id=$2,lease_until=NULL,"
                "lease_epoch=lease_epoch+1 WHERE singleton RETURNING lease_epoch",
                member,
                worker,
            )
            recovered = await worker_store.recover_job(uuid4(), attempt, epoch)
            assert recovered["status"] == "committed"
            assert recovered["result_id"] == str(result["result_id"])
            assert recovered["team_id"] == str(result["team_id"])
            assert (
                await admin.fetchval("SELECT count(*) FROM public.copa_telmex_teams")
                == 5
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_batch_consumptions"
                )
                == 5
            )
            return

        current = await store.private_block(account, block)
        paused = await store.transition(account, block, current["version"], "pause")
        with pytest.raises(BatchAuthorizationDenied):
            await consent_case["issuer"].issue_child(
                UUID(job["consent_id"]), member, worker, job["lease_epoch"]
            )
        async with case["pools"]["writer"].acquire() as writer:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await writer.fetchval(
                    "SELECT ctt_staff_private.batch_lock_child($1,$2,$3,$4)",
                    attempt,
                    UUID(old_body["authorization_id"]),
                    worker,
                    job["lease_epoch"],
                )

        if outcome == "revoke":
            await store.transition(account, block, paused["version"], "revoke")
        elif outcome == "expire":
            deadline = await admin.fetchval(
                "SELECT expires_at FROM ctt_staff_private.review_batch_consents "
                "WHERE consent_id=$1",
                UUID(job["consent_id"]),
            )
            from datetime import datetime, timezone

            await asyncio.sleep(
                max(0, (deadline - datetime.now(timezone.utc)).total_seconds()) + 0.05
            )

        epoch = await admin.fetchval(
            "SELECT lease_epoch FROM ctt_staff_private.review_batch_dispatch_slot "
            "WHERE singleton"
        )
        recovered = await worker_store.recover_job(uuid4(), attempt, epoch)
        assert recovered == {
            "status": "recovered_uncommitted",
            "attempt_id": str(attempt),
        }
        state = await admin.fetchrow(
            "SELECT j.status AS job_status,m.status AS member_status "
            "FROM ctt_staff_private.review_batch_jobs j "
            "JOIN ctt_staff_private.review_batch_members m USING(member_id) "
            "WHERE j.member_id=$1",
            member,
        )
        assert state["job_status"] == ("queued" if outcome == "resume" else "failed")
        assert state["member_status"] == ("pending" if outcome == "resume" else "ready")
        assert (await worker_store.claim(uuid4()))["status"] == "idle"
        assert (
            await admin.fetchval("SELECT count(*) FROM public.copa_telmex_teams") == 0
        )
        assert (
            await admin.fetchval(
                "SELECT member_id FROM ctt_staff_private.review_batch_dispatch_slot "
                "WHERE singleton"
            )
            is None
        )
        current = await store.private_block(account, block)
        if outcome != "resume":
            with pytest.raises(BatchDenied):
                await store.transition(account, block, current["version"], "resume")
            return

        await store.transition(account, block, current["version"], "resume")
        new_worker = uuid4()
        renewed = await worker_store.claim(new_worker)
        assert renewed["status"] == "leased"
        assert renewed["lease_epoch"] > job["lease_epoch"]
        proof = await consent_case["issuer"].issue_child(
            UUID(renewed["consent_id"]),
            UUID(renewed["member_id"]),
            new_worker,
            renewed["lease_epoch"],
        )
        consent_case.update(job=renewed, child=proof, worker_id=new_worker)
        await original_capture(consent_case, capture_path, **kwargs)
        target = await admin.fetchrow(
            "SELECT proof_bytes FROM "
            "ctt_staff_private.review_batch_child_authorizations "
            "WHERE member_id=$1 ORDER BY authorization_generation DESC LIMIT 1",
            member,
        )
        proof = bytes(target["proof_bytes"])
        body = json.loads(proof)["body"]
        assert body["attempt_id"] == str(attempt)
        assert body["member_id"] == str(member)
        assert body["operation_id"] == old_body["operation_id"]
        assert body["authorization_generation"] > old_body["authorization_generation"]
        assert body["dispatch_generation"] > old_body["dispatch_generation"]
        assert body["authorization_id"] != old_body["authorization_id"]
        with pytest.raises(BatchAuthorizationDenied):
            await consent_case["issuer"].issue_child(
                UUID(job["consent_id"]), member, worker, job["lease_epoch"]
            )
        async with case["pools"]["writer"].acquire() as writer:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await writer.fetchval(
                    "SELECT ctt_staff_private.batch_lock_child($1,$2,$3,$4)",
                    attempt,
                    UUID(old_body["authorization_id"]),
                    worker,
                    job["lease_epoch"],
                )

    monkeypatch.setattr(acceptance, "qualify_and_consent", qualify)
    monkeypatch.setattr(acceptance, "canonical_captures", scenario)
    acceptance.test_actual_source_run_analysis_lease_budget_and_exception_completion(
        full_pg, tmp_path, "capture"
    )
