"""SQL exact-effect gate, with real staff-security and synthetic keys only.

This proves the SQL boundary; Ed25519/human verification belongs to the trusted
writer. No deployment, operational credential, or authority grant is performed.
"""

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from devnous.copa_telmex.staff_review_challenge import (
    canonical_scope_bytes,
    scope_hash,
)
from test_ctt_capture_anchors_postgres import anchor_pg as _anchor_fixture
from test_ctt_capture_anchors_postgres import insert_attempt, remove_synthetic, sample
from test_ctt_staff_review_state_postgres import connect

pytest_plugins = ("test_ctt_capture_anchors_postgres",)
anchor_pg = _anchor_fixture
ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "database/migrations"
KEY = b"synthetic-effect-key-independent-0001"
WRITER = "ctt_effect_synthetic_writer"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@pytest.fixture(scope="module")
def effect_pg(anchor_pg):
    async def setup():
        admin = await connect(anchor_pg)
        try:
            for name in (
                "20261030_ctt_signed_assertion_guard",
                "20261030_ctt_capture_payloads",
                "20261030_ctt_staff_effect_gate",
            ):
                await admin.execute((MIGRATIONS / f"{name}.sql").read_text())
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.effect_writers"
                )
                == 0
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.effect_keys"
                )
                == 0
            )
            await admin.execute(f"CREATE ROLE {WRITER} LOGIN")
            await admin.execute(
                f"GRANT INSERT,SELECT ON public.copa_telmex_team_staff TO {WRITER}"
            )
            await admin.execute(f"GRANT USAGE ON SCHEMA ctt_staff_private TO {WRITER}")
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION "
                "ctt_staff_private.consume_review_challenge("
                "uuid,uuid,bytea,text,bigint,bigint),"
                "ctt_staff_private.record_signed_assertion_guard(uuid,text,bigint) "
                f"TO {WRITER}"
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.effect_keys VALUES"
                "('synthetic-effect',$1,clock_timestamp()-interval '1 minute',"
                "clock_timestamp()+interval '1 hour',false)",
                KEY,
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.effect_writers "
                "VALUES($1,'synthetic-effect')",
                WRITER,
            )
        finally:
            await admin.close()

    asyncio.run(setup())
    yield anchor_pg

    async def teardown():
        admin = await connect(anchor_pg)
        try:
            await admin.execute("DELETE FROM ctt_staff_private.effect_writers")
            await admin.execute("DELETE FROM ctt_staff_private.effect_keys")
            await admin.execute(
                (MIGRATIONS / "20261030_ctt_staff_effect_gate.rollback.sql").read_text()
            )
            # Safe rollback restores absolute closure, even for an INSERT grant.
            writer = await connect(anchor_pg, WRITER)
            try:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await writer.execute(
                        "INSERT INTO copa_telmex_team_staff "
                        "SELECT * FROM copa_telmex_team_staff LIMIT 1"
                    )
            finally:
                await writer.close()
            await admin.execute(
                "GRANT SELECT ON ctt_staff_private.capture_payloads TO devnous_user"
            )
            with pytest.raises(asyncpg.RaiseError):
                await admin.execute(
                    (MIGRATIONS / "20261030_ctt_staff_effect_gate.sql").read_text()
                )
            await admin.execute("ROLLBACK")
            await admin.execute(
                "REVOKE SELECT ON ctt_staff_private.capture_payloads FROM devnous_user"
            )
            for name in (
                "20261030_ctt_capture_payloads",
                "20261030_ctt_signed_assertion_guard",
            ):
                await admin.execute((MIGRATIONS / f"{name}.rollback.sql").read_text())
        finally:
            await admin.close()

    asyncio.run(teardown())


async def setup_payload(admin, variant):
    scope, challenge, attempt = await sample(admin)
    team = scope.team_id
    await admin.execute("INSERT INTO copa_telmex_teams VALUES($1,'Synthetic')", team)
    await insert_attempt(admin, scope, challenge, attempt)
    now = datetime.now(timezone.utc).isoformat()
    row = dict(
        id=str(uuid4()),
        team_id=str(team),
        staff_slot=1,
        role="director_tecnico",
        first_name="Synthetic",
        last_name="Staff",
        birth_date=None,
        curp=None,
        photo_path=None,
        evidence={},
        governance_state="PENDING_FINALITY",
        governance_draft_id=str(scope.draft_id),
        governance_draft_version=scope.draft_version,
        governance_decision_id="sha256:" + "a" * 64,
        roster_draft_binding="hmac-sha256:" + "b" * 64,
        preauthorization_receipt_id="sha256:" + "c" * 64,
        finality_receipt_id=None,
        created_at=now,
        updated_at=now,
    )
    context = json.loads(canonical_scope_bytes(scope))
    for key in (
        "staff_and_changes_binding",
        "decision_hash",
        "review_receipt_id",
        "operation_id",
    ):
        context.pop(key)
    effect = dict(
        schema="ctt.capture.effect.v1",
        review_context=context,
        column_schema_hash="sha256:" + "d" * 64,
        team={"id": str(team)},
        players=[],
        player_sources=[],
        staff=[
            dict(slot=1, role="director_tecnico", presence="PRESENT", row=row),
            dict(slot=2, role="auxiliar", presence="ABSENT", row=None),
        ],
    )
    if variant == "context":
        effect["review_context"]["actor_id"] = str(uuid4())
    if variant == "missing_column":
        effect["staff"][0]["row"] = dict(row)
        effect["staff"][0]["row"].pop("birth_date")
    frozen = canonical(effect)
    expiry = await admin.fetchval(
        "SELECT floor(extract(epoch FROM expires_at))::bigint FROM "
        "ctt_staff_private.review_challenges WHERE challenge_id=$1",
        challenge,
    )
    nonce = uuid4()
    body = dict(
        schema="ctt.capture.authorization.v1",
        kid="synthetic-ed25519",
        scope_hash=scope_hash(scope),
        attempt_id=str(attempt),
        nonce=str(nonce),
        issued_at=int(datetime.now(timezone.utc).timestamp()),
        expires_at=expiry,
        effect_binding="hmac-sha256:"
        + hmac.new(
            KEY, b"ctt.capture.effect.v1\0" + frozen, hashlib.sha256
        ).hexdigest(),
    )
    if variant == "mac":
        body["effect_binding"] = "hmac-sha256:" + "0" * 64
    if variant == "expiry":
        body["expires_at"] += 1
    if variant == "attempt":
        body["attempt_id"] = str(uuid4())
    if variant == "changed_effect_after_mac":
        effect["players"] = [{"unexpected": "tampered"}]
        frozen = canonical(effect)
    await admin.execute(
        "INSERT INTO ctt_staff_private.capture_payloads VALUES($1,$2,$3,$4,$5)",
        attempt,
        b"{}",
        frozen,
        canonical(dict(body=body, signature="synthetic")),
        nonce,
    )
    credential = await admin.fetchval(
        "SELECT credential_id FROM ctt_staff_private.review_challenges "
        "WHERE challenge_id=$1",
        challenge,
    )
    return scope, challenge, attempt, row, credential, expiry


async def evidence(writer, payload):
    scope, challenge, _, _, credential, expiry = payload
    await writer.execute(
        "SELECT ctt_staff_private.consume_review_challenge($1,$2,$3,$4,0,1)",
        challenge,
        scope.actor_id,
        credential,
        scope_hash(scope),
    )
    await writer.execute(
        "SELECT ctt_staff_private.record_signed_assertion_guard($1,$2,$3)",
        challenge,
        "sha256:" + "e" * 64,
        expiry,
    )


async def insert(writer, row):
    await writer.execute(
        "INSERT INTO public.copa_telmex_team_staff "
        "SELECT (jsonb_populate_record("
        "NULL::public.copa_telmex_team_staff,$1::jsonb)).*",
        json.dumps(row),
    )


@pytest.mark.parametrize(
    "variant",
    [
        "success",
        "changed_row",
        "changed_effect_after_mac",
        "mac",
        "context",
        "missing_column",
        "expiry",
        "attempt",
        "no_evidence",
        "committed_evidence",
        "subtransaction_evidence",
        "result",
        "revoked_key",
        "expired_key",
        "short_key",
        "revoked_writer",
        "super_writer",
        "member_writer",
        "app",
        "set_role",
    ],
)
def test_exact_effect_and_current_transaction(effect_pg, variant):
    async def run():
        admin = await connect(effect_pg)
        writer = await connect(effect_pg, WRITER)
        payload = await setup_payload(admin, variant)
        scope, challenge, attempt, row, _, _ = payload
        try:
            if variant == "revoked_key":
                await admin.execute(
                    "UPDATE ctt_staff_private.effect_keys SET revoked=true"
                )
            if variant == "expired_key":
                await admin.execute(
                    "UPDATE ctt_staff_private.effect_keys "
                    "SET not_before=clock_timestamp()-interval '2 minutes',"
                    "not_after=clock_timestamp()-interval '1 minute'"
                )
            if variant == "short_key":
                await admin.execute(
                    "UPDATE ctt_staff_private.effect_keys "
                    "SET not_after=clock_timestamp()+interval '1 minute'"
                )
            if variant == "super_writer":
                await admin.execute(f"ALTER ROLE {WRITER} CREATEDB")
            if variant == "member_writer":
                await admin.execute(f"GRANT {WRITER} TO devnous_user")
            if variant == "revoked_writer":
                await admin.execute("DELETE FROM ctt_staff_private.effect_writers")
            if variant == "committed_evidence":
                async with writer.transaction():
                    await evidence(writer, payload)
            transaction = writer.transaction()
            await transaction.start()
            try:
                if variant not in ("no_evidence", "committed_evidence"):
                    if variant == "subtransaction_evidence":
                        async with writer.transaction():
                            await evidence(writer, payload)
                    else:
                        await evidence(writer, payload)
                if variant == "result":
                    # SQL-only synthetic result; same transaction before insertion.
                    await writer.execute("SELECT 1")
                    # Use the administrator in a separate committed transaction.
                    await admin.execute(
                        "INSERT INTO ctt_staff_private.capture_results "
                        "VALUES($1,$2,$3,$4,now())",
                        attempt,
                        uuid4(),
                        scope.team_id,
                        "sha256:" + "f" * 64,
                    )
                if variant == "changed_row":
                    row["first_name"] = "Changed"
                if variant == "success":
                    top = await writer.fetchval(
                        "SELECT mod(pg_current_xact_id()::text::numeric,"
                        "4294967296)::bigint"
                    )
                    # Insert savepoint follows top-level evidence.
                    async with writer.transaction():
                        await insert(writer, row)
                    assert (
                        await writer.fetchval(
                            "SELECT count(*) FROM public.copa_telmex_team_staff "
                            "WHERE id=$1::uuid",
                            row["id"],
                        )
                        == 1
                    )
                    assert await admin.fetchval("SELECT 1") == 1
                    assert top is not None
                    for operation in ("UPDATE", "DELETE"):
                        # Synthetic grant proves trigger denial.
                        await admin.execute(
                            f"GRANT {operation} ON public.copa_telmex_team_staff "
                            f"TO {WRITER}"
                        )
                        async with writer.transaction():
                            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                                async with writer.transaction():
                                    if operation == "UPDATE":
                                        await writer.execute(
                                            "UPDATE public.copa_telmex_team_staff "
                                            "SET first_name='x'"
                                        )
                                    else:
                                        await writer.execute(
                                            "DELETE FROM "
                                            "public.copa_telmex_team_staff"
                                        )
                        await admin.execute(
                            f"REVOKE {operation} ON public.copa_telmex_team_staff "
                            f"FROM {WRITER}"
                        )
                elif variant in ("app", "set_role"):
                    app = await connect(effect_pg, "devnous_user")
                    try:
                        with pytest.raises(asyncpg.InsufficientPrivilegeError):
                            if variant == "set_role":
                                await app.execute(f"SET ROLE {WRITER}")
                            else:
                                await insert(app, row)
                        for table in (
                            "effect_keys",
                            "effect_writers",
                            "effect_staff_writes",
                        ):
                            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                                await app.fetch(
                                    f"SELECT * FROM ctt_staff_private.{table}"
                                )
                    finally:
                        await app.close()
                else:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        async with writer.transaction():
                            await insert(writer, row)
            finally:
                await transaction.rollback()
            # Rollback refuses configuration/evidence even with zero accepted writes.
            with pytest.raises(asyncpg.RaiseError):
                await admin.execute(
                    (
                        MIGRATIONS / "20261030_ctt_staff_effect_gate.rollback.sql"
                    ).read_text()
                )
            await admin.execute("ROLLBACK")
        finally:
            await writer.close()
            await admin.execute(f"ALTER ROLE {WRITER} NOCREATEDB")
            await admin.execute(f"REVOKE {WRITER} FROM devnous_user")
            # Explicit test-only cleanup of immutable synthetic evidence.
            for table, trigger in (
                ("capture_results", "ctt_capture_result_immutable"),
                ("signed_assertion_guards", "ctt_assertion_guard_immutable"),
                ("capture_payloads", "ctt_capture_payload_immutable"),
            ):
                await admin.execute(
                    f"ALTER TABLE ctt_staff_private.{table} DISABLE TRIGGER {trigger}"
                )
                await admin.execute(f"DELETE FROM ctt_staff_private.{table}")
                await admin.execute(
                    f"ALTER TABLE ctt_staff_private.{table} ENABLE TRIGGER {trigger}"
                )
            await remove_synthetic(admin)
            await admin.execute(
                "DELETE FROM copa_telmex_teams WHERE id=$1", scope.team_id
            )
            await admin.execute(
                "UPDATE ctt_staff_private.effect_keys SET revoked=false,"
                "not_after=clock_timestamp()+interval '1 hour'"
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.effect_writers "
                "VALUES($1,'synthetic-effect') ON CONFLICT DO NOTHING",
                WRITER,
            )
            await admin.close()

    asyncio.run(run())
