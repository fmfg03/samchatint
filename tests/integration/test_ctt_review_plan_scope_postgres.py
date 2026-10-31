"""SQL pre-receipt scopes; synthetic authority and disposable PostgreSQL only."""

import asyncio
from datetime import date, datetime
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from uuid import UUID, uuid4

import asyncpg
import pytest

from devnous.copa_telmex.staff_review_challenge import canonical_scope_bytes
from test_ctt_capture_anchors_postgres import (
    insert_attempt,
    remove_synthetic,
    sample,
)
from test_ctt_staff_effect_gate_postgres import KEY, WRITER
from test_ctt_staff_effect_gate_postgres import effect_pg as _effect_fixture
from test_ctt_staff_review_state_postgres import connect

pytest_plugins = ("test_ctt_staff_effect_gate_postgres",)
effect_pg = _effect_fixture
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261030_ctt_review_plan_scope.sql"
ROLLBACK = ROOT / "database/migrations/20261030_ctt_review_plan_scope.rollback.sql"
V1 = "ctt.staff-review.challenge-scope.v1"
V2 = "ctt.staff-review.challenge-scope.v2"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@pytest.fixture(scope="module")
def plan_scope_pg(effect_pg):
    async def apply():
        conn = await connect(effect_pg)
        try:
            await conn.execute(MIGRATION.read_text())
        finally:
            await conn.close()

    asyncio.run(apply())
    yield effect_pg

    async def rollback():
        conn = await connect(effect_pg)
        try:
            await conn.execute(ROLLBACK.read_text())
        finally:
            await conn.close()

    asyncio.run(rollback())


async def context(admin, *, version=V2, changes=None, omit=None):
    scope, old_challenge, attempt = await sample(admin)
    value = json.loads(canonical_scope_bytes(scope))
    if version == V2:
        del value["decision_hash"]
        del value["review_receipt_id"]
        value["field_evidence_binding"] = "hmac-sha256:" + "d" * 64
    value["schema"] = version
    value.update(changes or {})
    if omit:
        del value[omit]
    blob = canonical(value)
    digest = "sha256:" + hashlib.sha256(blob).hexdigest()
    challenge = uuid4()
    await admin.execute(
        "INSERT INTO ctt_staff_private.review_challenges "
        "SELECT $1,principal_id,credential_id,tenant_id,edition_id,faculty,"
        "principal_authority_version,faculty_authority_version,"
        "credential_authority_version,$2,$3,issued_at,expires_at "
        "FROM ctt_staff_private.review_challenges WHERE challenge_id=$4",
        challenge,
        digest,
        secrets.token_bytes(32),
        old_challenge,
    )
    return scope, challenge, attempt, blob, digest


async def add_anchor(admin, state):
    scope, challenge, attempt, blob, digest = state
    await insert_attempt(admin, scope, challenge, attempt, blob=blob, digest=digest)


@pytest.mark.parametrize("version", [V1, V2])
def test_scope_versions_bind_exact_anchor(plan_scope_pg, version):
    async def run():
        admin = await connect(plan_scope_pg)
        try:
            state = await context(admin, version=version)
            await add_anchor(admin, state)
            stored = await admin.fetchval(
                "SELECT scope_bytes FROM ctt_staff_private.capture_attempt_anchors "
                "WHERE attempt_id=$1",
                state[2],
            )
            assert stored == state[3]
            assert json.loads(stored)["schema"] == version
            if version == V2:
                assert "decision_hash" not in json.loads(stored)
                assert "review_receipt_id" not in json.loads(stored)
                assert len(json.loads(stored)) == 21
                assert json.loads(stored)["field_evidence_binding"] == (
                    "hmac-sha256:" + "d" * 64
                )
            else:
                assert "field_evidence_binding" not in json.loads(stored)
        finally:
            await remove_synthetic(admin)
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes,omit",
    [
        ({"decision_hash": "sha256:" + "a" * 64}, None),
        ({"decision_hash": None}, None),
        ({"review_receipt_id": "sha256:" + "a" * 64}, None),
        ({"review_receipt_id": None}, None),
        ({"schema": V1}, None),
        ({"schema": "ctt.staff-review.challenge-scope.v3"}, None),
        ({"extra": "ignored-data"}, None),
        ({"operation_id": None}, None),
        ({"operation_id": "pending"}, None),
        ({}, "operation_id"),
        ({"field_evidence_binding": None}, None),
        ({"field_evidence_binding": "sha256:" + "a" * 64}, None),
        ({"field_evidence_binding": "hmac-sha256:" + "D" * 64}, None),
        ({}, "field_evidence_binding"),
    ],
)
def test_pre_receipt_scope_rejects_future_fields_and_version_spoof(
    plan_scope_pg, changes, omit
):
    async def run():
        admin = await connect(plan_scope_pg)
        try:
            state = await context(admin, changes=changes, omit=omit)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await add_anchor(admin, state)
        finally:
            await remove_synthetic(admin)
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize("binding", [None, "hmac-sha256:" + "d" * 64])
def test_v1_rejects_v2_field_evidence_property(plan_scope_pg, binding):
    async def run():
        admin = await connect(plan_scope_pg)
        try:
            state = await context(
                admin, version=V1, changes={"field_evidence_binding": binding}
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await add_anchor(admin, state)
        finally:
            await remove_synthetic(admin)
            await admin.close()

    asyncio.run(run())


async def payload(admin, version, variant):
    state = await context(admin, version=version)
    await add_anchor(admin, state)
    scope, challenge, attempt, blob, digest = state
    await admin.execute(
        "INSERT INTO copa_telmex_teams VALUES($1,'Synthetic pre-receipt')",
        scope.team_id,
    )
    original = await admin.fetchrow(
        "SELECT * FROM copa_telmex_team_staff "
        "WHERE id='22222222-2222-4222-8222-222222222222'::uuid"
    )
    row = {}
    for name, value in dict(original).items():
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, (date, datetime)):
            value = value.isoformat()
        elif name == "evidence" and type(value) is str:
            value = json.loads(value)
        row[name] = value
    row.update(
        id=str(uuid4()),
        team_id=str(scope.team_id),
        governance_draft_id=str(scope.draft_id),
        governance_draft_version=scope.draft_version,
    )
    review_context = json.loads(blob)
    excluded = ["staff_and_changes_binding"]
    if version == V1:
        excluded += ["decision_hash", "review_receipt_id", "operation_id"]
    for name in excluded:
        del review_context[name]
    if variant == "changed_operation":
        review_context["operation_id"] = "sha256:" + "f" * 64
    elif variant == "omitted_operation":
        del review_context["operation_id"]
    elif variant == "changed_evidence":
        review_context["field_evidence_binding"] = "hmac-sha256:" + "e" * 64
    elif variant == "omitted_evidence":
        del review_context["field_evidence_binding"]
    effect = dict(
        schema="ctt.capture.effect.v1",
        review_context=review_context,
        column_schema_hash="sha256:" + "d" * 64,
        team={"id": str(scope.team_id)},
        players=[],
        player_sources=[],
        staff=[
            dict(slot=1, role="director_tecnico", presence="PRESENT", row=row),
            dict(slot=2, role="auxiliar", presence="ABSENT", row=None),
        ],
    )
    frozen = canonical(effect)
    expiry = await admin.fetchval(
        "SELECT floor(extract(epoch FROM expires_at))::bigint "
        "FROM ctt_staff_private.review_challenges WHERE challenge_id=$1",
        challenge,
    )
    nonce = uuid4()
    issued = await admin.fetchval(
        "SELECT floor(extract(epoch FROM clock_timestamp()))::bigint"
    )
    envelope = canonical(
        dict(
            body=dict(
                schema="ctt.capture.authorization.v1",
                kid="synthetic-ed25519",
                scope_hash=digest,
                attempt_id=str(attempt),
                nonce=str(nonce),
                issued_at=issued,
                expires_at=expiry,
                effect_binding="hmac-sha256:"
                + hmac.new(
                    KEY, b"ctt.capture.effect.v1\x00" + frozen, hashlib.sha256
                ).hexdigest(),
            ),
            signature="synthetic",
        )
    )
    await admin.execute(
        "INSERT INTO ctt_staff_private.capture_payloads VALUES($1,$2,$3,$4,$5)",
        attempt,
        b"{}",
        frozen,
        envelope,
        nonce,
    )
    credential = await admin.fetchval(
        "SELECT credential_id FROM ctt_staff_private.review_challenges "
        "WHERE challenge_id=$1",
        challenge,
    )
    return state, row, credential, expiry


@pytest.mark.parametrize(
    "version,variant",
    [
        (V1, "success"),
        (V2, "success"),
        (V2, "changed_operation"),
        (V2, "omitted_operation"),
        (V2, "changed_evidence"),
        (V2, "omitted_evidence"),
    ],
)
def test_staff_gate_preserves_versioned_review_context(plan_scope_pg, version, variant):
    async def run():
        admin, writer = await connect(plan_scope_pg), await connect(
            plan_scope_pg, WRITER
        )
        state = None
        try:
            state, row, credential, expiry = await payload(admin, version, variant)
            scope, challenge, _, _, digest = state
            outer = writer.transaction()
            await outer.start()
            try:
                await writer.execute(
                    "SELECT ctt_staff_private.consume_review_challenge("
                    "$1,$2,$3,$4,0,1)",
                    challenge,
                    scope.actor_id,
                    credential,
                    digest,
                )
                await writer.execute(
                    "SELECT ctt_staff_private.record_signed_assertion_guard($1,$2,$3)",
                    challenge,
                    "sha256:" + "e" * 64,
                    expiry,
                )
                if variant == "success":
                    await writer.execute(
                        "INSERT INTO copa_telmex_team_staff SELECT "
                        "(jsonb_populate_record("
                        "NULL::copa_telmex_team_staff,$1::jsonb)).*",
                        json.dumps(row),
                    )
                    assert (
                        await writer.fetchval(
                            "SELECT count(*) FROM copa_telmex_team_staff "
                            "WHERE id=$1::uuid",
                            row["id"],
                        )
                        == 1
                    )
                else:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        async with writer.transaction():
                            await writer.execute(
                                "INSERT INTO copa_telmex_team_staff SELECT "
                                "(jsonb_populate_record("
                                "NULL::copa_telmex_team_staff,$1::jsonb)).*",
                                json.dumps(row),
                            )
            finally:
                await outer.rollback()
        finally:
            await writer.close()
            await admin.execute(
                "ALTER TABLE ctt_staff_private.capture_payloads "
                "DISABLE TRIGGER ctt_capture_payload_immutable"
            )
            await admin.execute("DELETE FROM ctt_staff_private.capture_payloads")
            await admin.execute(
                "ALTER TABLE ctt_staff_private.capture_payloads "
                "ENABLE TRIGGER ctt_capture_payload_immutable"
            )
            await remove_synthetic(admin)
            if state:
                await admin.execute(
                    "DELETE FROM copa_telmex_teams WHERE id=$1", state[0].team_id
                )
            await admin.close()

    asyncio.run(run())


def test_rollback_refuses_even_unconsumed_v2_anchor(plan_scope_pg):
    async def run():
        admin = await connect(plan_scope_pg)
        try:
            state = await context(admin)
            await add_anchor(admin, state)
            with pytest.raises(asyncpg.RaiseError, match="v2 evidence exists"):
                await admin.execute(ROLLBACK.read_text())
            await admin.execute("ROLLBACK")
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_attempt_anchors"
                )
                == 1
            )
        finally:
            await remove_synthetic(admin)
            await admin.close()

    asyncio.run(run())


def test_rollback_preserves_v1_anchor_and_can_upgrade_again(plan_scope_pg):
    async def run():
        admin = await connect(plan_scope_pg)
        try:
            state = await context(admin, version=V1)
            await add_anchor(admin, state)
            await admin.execute(ROLLBACK.read_text())
            assert (
                await admin.fetchval(
                    "SELECT scope_bytes FROM ctt_staff_private.capture_attempt_anchors "
                    "WHERE attempt_id=$1",
                    state[2],
                )
                == state[3]
            )
            await admin.execute(MIGRATION.read_text())
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_attempt_anchors"
                )
                == 1
            )
        finally:
            await remove_synthetic(admin)
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize("tamper", ["disabled_trigger", "public_function"])
def test_preflight_fails_closed_and_restored_controls_allow_apply(
    plan_scope_pg, tamper
):
    async def run():
        admin = await connect(plan_scope_pg)
        try:
            await admin.execute(ROLLBACK.read_text())
            if tamper == "disabled_trigger":
                await admin.execute(
                    "ALTER TABLE ctt_staff_private.capture_attempt_anchors "
                    "DISABLE TRIGGER ctt_capture_anchor_validate"
                )
            else:
                await admin.execute(
                    "GRANT EXECUTE ON FUNCTION "
                    "ctt_staff_private.validate_capture_anchor() TO PUBLIC"
                )
            with pytest.raises(asyncpg.RaiseError):
                await admin.execute(MIGRATION.read_text())
            await admin.execute("ROLLBACK")
            if tamper == "disabled_trigger":
                await admin.execute(
                    "ALTER TABLE ctt_staff_private.capture_attempt_anchors "
                    "ENABLE TRIGGER ctt_capture_anchor_validate"
                )
            else:
                await admin.execute(
                    "REVOKE EXECUTE ON FUNCTION "
                    "ctt_staff_private.validate_capture_anchor() FROM PUBLIC"
                )
            await admin.execute(MIGRATION.read_text())
        finally:
            await admin.close()

    asyncio.run(run())
