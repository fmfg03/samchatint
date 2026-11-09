"""Finality SQL on a restored empty schema; no person or operational authority."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import time
from uuid import UUID, uuid4

import asyncpg
import pytest

from test_ctt_legacy_write_boundary_postgres import (
    boundary_pg as _boundary_pg,
    connect,
    insert_model,
    row,
    source_review_rows,
)
from test_ctt_capture_anchors_postgres import sample, insert_attempt
from test_ctt_staff_security_postgres import isolated_pg as _isolated_pg
from devnous.copa_telmex.models import Team, Player, TeamStaff
from devnous.copa_telmex.staff_review_challenge import canonical_scope_bytes, scope_hash

isolated_pg = _isolated_pg
boundary_pg = _boundary_pg
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261031_ctt_capture_finality.sql"
ROLLBACK = ROOT / "database/migrations/20261031_ctt_capture_finality.rollback.sql"
WRITER = "ctt_sql_finality_writer"
KEY = b"isolated-finality-effect-key-32-bytes"
SHA = "sha256:" + "a" * 64


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def binding(domain, raw):
    return (
        "hmac-sha256:" + hmac.new(KEY, domain + b"\0" + raw, hashlib.sha256).hexdigest()
    )


async def configure_and_seed(context):
    """Synthetic authority setup; signatures are exercised by full runtime tests."""
    admin = await connect(context)
    app = await connect(context, "devnous_user")
    try:
        if not await admin.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", WRITER
        ):
            await admin.execute(f"CREATE ROLE {WRITER} LOGIN")
        await admin.execute(
            "INSERT INTO ctt_staff_private.effect_keys VALUES"
            "('test-finality',$1,now()-interval '1 minute',"
            "now()+interval '1 hour',false)",
            KEY,
        )
        await admin.execute(
            "INSERT INTO ctt_staff_private.effect_writers VALUES($1,'test-finality')",
            WRITER,
        )
        await admin.execute(
            "INSERT INTO ctt_staff_private.finality_database_identity"
            "(database_identity) VALUES('isolated-finality')"
        )
        await admin.execute(f"GRANT USAGE ON SCHEMA ctt_staff_private TO {WRITER}")
        for table in (Team, Player, TeamStaff):
            await admin.execute(
                f"GRANT SELECT,INSERT,UPDATE ON public.{table.__tablename__} "
                f"TO {WRITER}"
            )
        for signature in (
            "consume_review_challenge(uuid,uuid,bytea,text,bigint,bigint)",
            "record_signed_assertion_guard(uuid,text,bigint)",
            "record_capture_result(uuid,uuid,text)",
            "record_capture_finality(uuid,bytea,bytea)",
            "load_capture_finality_context(uuid)",
            "project_capture_finality_player(jsonb)",
        ):
            await admin.execute(
                f"GRANT EXECUTE ON FUNCTION ctt_staff_private.{signature} TO {WRITER}"
            )
        scope, challenge, attempt = await sample(admin)
        await source_review_rows(app, scope)
        await insert_attempt(admin, scope, challenge, attempt)
        team = row(Team)
        team.update(id=str(scope.team_id), postcommit_revision=1)
        common = dict(
            governance_state="PENDING_FINALITY",
            governance_draft_id=str(scope.draft_id),
            governance_draft_version=scope.draft_version,
            governance_decision_id=SHA,
            roster_draft_binding="hmac-sha256:" + "b" * 64,
            preauthorization_receipt_id=SHA,
            finality_receipt_id=None,
        )
        player = row(Player)
        player.update(
            **common, team_id=str(scope.team_id), roster_index=1, postcommit_revision=0
        )
        staff = row(TeamStaff)
        staff.update(
            **common,
            team_id=str(scope.team_id),
            staff_slot=1,
            role="director_tecnico",
            evidence={},
        )
        for table, values, kind in ((Team, team, "team"), (Player, player, "player")):
            values["postcommit_snapshot_hash"] = await admin.fetchval(
                f"SELECT public.regs07_snapshot_hash(public.regs07_{kind}_snapshot("
                f"jsonb_populate_record(NULL::public.{table.__tablename__},"
                "$1::jsonb)))",
                json.dumps(values),
            )
        scope_json = json.loads(canonical_scope_bytes(scope))
        context_json = {
            k: v
            for k, v in scope_json.items()
            if k
            not in (
                "staff_and_changes_binding",
                "decision_hash",
                "review_receipt_id",
                "operation_id",
            )
        }
        pending = {
            "schema": "ctt.capture.effect.v1",
            "review_context": context_json,
            "team": team,
            "players": [player],
            "staff": [
                {
                    "slot": 1,
                    "role": "director_tecnico",
                    "presence": "PRESENT",
                    "row": staff,
                },
                {"slot": 2, "role": "auxiliar", "presence": "ABSENT", "row": None},
            ],
        }
        expiry = await admin.fetchval(
            "SELECT floor(extract(epoch FROM expires_at))::bigint "
            "FROM ctt_staff_private.review_challenges WHERE challenge_id=$1",
            challenge,
        )
        issued = await admin.fetchval(
            "SELECT floor(extract(epoch FROM issued_at))::bigint "
            "FROM ctt_staff_private.review_challenges WHERE challenge_id=$1",
            challenge,
        )
        nonce = uuid4()
        envelope = {
            "body": dict(
                schema="ctt.capture.authorization.v1",
                kid="test-finality",
                attempt_id=str(attempt),
                nonce=str(nonce),
                scope_hash=scope_hash(scope),
                review_plan_binding=scope.staff_and_changes_binding,
                effect_binding=binding(b"ctt.capture.effect.v1", canonical(pending)),
                issued_at=issued,
                expires_at=expiry,
            ),
            "signature": "synthetic-not-cryptographic-authority",
        }
        await admin.execute(
            "INSERT INTO ctt_staff_private.capture_payloads VALUES($1,$2,$3,$4,$5)",
            attempt,
            b"{}",
            canonical(pending),
            canonical(envelope),
            nonce,
        )
        preauthorization = dict(
            roster_decision=dict(synthetic_preauthorization=True),
            preauthorization_receipt=dict(receipt_id=SHA),
        )
        await admin.execute(
            "INSERT INTO ctt_staff_private.finality_inputs VALUES($1,$2,$3)",
            attempt,
            canonical(preauthorization),
            b"{}",
        )
        credential = await admin.fetchval(
            "SELECT credential_id FROM ctt_staff_private.review_challenges "
            "WHERE challenge_id=$1",
            challenge,
        )
        return dict(
            scope=scope,
            challenge=challenge,
            attempt=attempt,
            pending=pending,
            envelope=envelope,
            credential=credential,
            expiry=expiry,
            preauthorization=preauthorization,
        )
    finally:
        await app.close()
        await admin.close()


async def begin_pending(conn, case):
    scope = case["scope"]
    assert await conn.fetchval(
        "SELECT ctt_staff_private.consume_review_challenge($1,$2,$3,$4,0,1)",
        case["challenge"],
        scope.actor_id,
        case["credential"],
        scope_hash(scope),
    )
    await conn.execute(
        "SELECT ctt_staff_private.record_signed_assertion_guard($1,$2,$3)",
        case["challenge"],
        SHA,
        case["expiry"],
    )
    for table, values in (
        (Team, case["pending"]["team"]),
        (Player, case["pending"]["players"][0]),
        (TeamStaff, case["pending"]["staff"][0]["row"]),
    ):
        await insert_model(conn, table, values)


async def finality_payload(conn, case):
    context = await conn.fetchrow(
        "SELECT * FROM ctt_staff_private.load_capture_finality_context($1)",
        case["attempt"],
    )
    tx = (
        "samchat-tx-"
        + hashlib.sha256(
            b"ctt.capture.database-transaction.v1\0"
            + context["database_identity"].encode()
            + b"\0"
            + str(context["database_oid"]).encode()
            + b"\0"
            + context["pg_top_xid"].encode()
        ).hexdigest()[:24]
    )
    event_attempt = digest(
        dict(
            domain="ctt.capture.finality-attempt.v1",
            review_attempt_id=str(case["attempt"]),
            database_transaction_id=tx,
        )
    )
    effect = dict(
        schema="ctt.capture.finality-effect.v1",
        scope_hash=scope_hash(case["scope"]),
        review_attempt_id=str(case["attempt"]),
        database_transaction_id=tx,
        event_attempt_id=event_attempt,
        database_identity=context["database_identity"],
        database_oid=context["database_oid"],
        pg_top_xid=context["pg_top_xid"],
        pending_effect_binding=case["envelope"]["body"]["effect_binding"],
        column_schema="ctt.capture.modeled-columns.v1",
        team=case["pending"]["team"],
        players=[],
        staff=[],
    )
    refs = []
    for kind, table, source in (
        ("PLAYER", Player, case["pending"]["players"][0]),
        ("STAFF", TeamStaff, case["pending"]["staff"][0]["row"]),
    ):
        active = deepcopy(source)
        receipt_id = digest(dict(kind=kind, row_id=source["id"]))
        active.update(
            governance_state="ACTIVE",
            finality_receipt_id=receipt_id,
            updated_at=datetime.fromtimestamp(
                context["issued_at_seconds"], timezone.utc
            )
            .replace(tzinfo=None)
            .isoformat(),
        )
        if kind == "STAFF":
            active["updated_at"] += "+00:00"
        scope = case["scope"]
        event = dict(
            event_type=f"samchat_registration_{kind.lower()}_finality_attestation_v2",
            decision=f"ACTIVATE_{kind}",
            database_transaction_id=tx,
            attempt_id=event_attempt,
            preauthorization_receipt_id=SHA,
            team_id=source["team_id"],
            cas_succeeded=True,
            blocking_incident_count=0,
            tenant_id="synthetic",
            operation_id=digest(dict(kind=kind, row_id=source["id"], tx=tx)),
            tournament_edition_id=str(scope.edition_id),
            tournament_slug=scope.tournament_slug,
            session_id=str(scope.session_id),
            draft_id=str(scope.draft_id),
            draft_version=scope.draft_version,
            policy_hash=scope.policy_hash,
            workflow_id="samchat_registration_identity_v2",
            effective_sal="SAL-3",
            policy_id="samchat_registration_identity_policy_v2",
            roster_draft_binding=source["roster_draft_binding"],
            timestamp=datetime.fromtimestamp(context["issued_at_seconds"], timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        )
        if kind == "STAFF":
            event["staff_role"] = "director_tecnico"
        event[kind.lower() + "_id"] = source["id"]
        event[kind.lower() + "_slot"] = 1
        receipt = dict(
            receipt_id=receipt_id,
            event_type=event["event_type"],
            tenant_id="synthetic",
            event_hash=digest(event),
            issuer="zaubern-evidence-bus",
            alg="Ed25519",
            verified=True,
        )
        if kind == "PLAYER":
            projected = await conn.fetchval(
                "SELECT ctt_staff_private.project_capture_finality_player($1::jsonb)",
                json.dumps(active),
            )
            active = json.loads(projected)
        item = dict(
            row_id=source["id"],
            slot=1,
            pending_row=source,
            active_row=active,
            event=event,
            receipt=receipt,
        )
        if kind == "PLAYER":
            item["kind"] = kind
            effect["players"].append(item)
        else:
            item.update(role="director_tecnico", presence="PRESENT")
            effect["staff"].append(item)
        refs.append(
            dict(
                kind=kind,
                row_id=source["id"],
                slot=1,
                event_hash=receipt["event_hash"],
                receipt_id=receipt_id,
                operation_id=event["operation_id"],
            )
        )
    effect["staff"].append(
        dict(
            slot=2,
            role="auxiliar",
            presence="ABSENT",
            row_id=None,
            pending_row=None,
            active_row=None,
            event=None,
            receipt=None,
        )
    )
    body = dict(
        schema="ctt.capture.finality-authorization.v1",
        kid="test-finality",
        scope_hash=scope_hash(case["scope"]),
        review_attempt_id=str(case["attempt"]),
        signed_review_hash=SHA,
        preauthorization_event_hash=digest(case["preauthorization"]["roster_decision"]),
        preauthorization_receipt_id=SHA,
        database_transaction_id=tx,
        event_attempt_id=event_attempt,
        nonce=str(uuid4()),
        review_plan_binding=case["scope"].staff_and_changes_binding,
        pending_effect_binding=effect["pending_effect_binding"],
        finality_effect_binding=binding(
            b"ctt.capture.finality-effect.v1", canonical(effect)
        ),
        ordered_finality_receipts=refs,
        issued_at=context["issued_at_seconds"],
        expires_at=case["expiry"],
    )
    return effect, dict(body=body, signature="synthetic-not-cryptographic-authority")


@pytest.fixture
def finality_pg(boundary_pg):
    async def setup():
        conn = await connect(boundary_pg)
        try:
            await conn.execute(
                (
                    ROOT / "database/migrations/20261030_ctt_review_plan_scope.sql"
                ).read_text()
            )
            try:
                await conn.execute(MIGRATION.read_text())
            except asyncpg.PostgresError as error:
                pytest.fail(
                    f"Finality migration: {error}; position={error.position}; "
                    f"internal_position={error.internal_position}; "
                    f"internal_query={error.internal_query}; context={error.context}"
                )
        finally:
            await conn.close()

    asyncio.run(setup())
    return boundary_pg


def test_empty_migration_and_rollback_preserve_provisional_guards(finality_pg):
    async def run():
        conn = await connect(finality_pg)
        try:
            baseline = dict(
                (r["signature"], r["original_hash"])
                for r in await conn.fetch(
                    "SELECT * FROM ctt_staff_private.finality_migration_baseline"
                )
            )
            assert len(baseline) == 2
            await conn.execute(ROLLBACK.read_text())
            for signature, digest in baseline.items():
                assert (
                    await conn.fetchval(
                        "SELECT md5(pg_get_functiondef($1::regprocedure))", signature
                    )
                    == digest
                )
            await conn.execute(MIGRATION.read_text())
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM ctt_staff_private.capture_finalities",
        "SELECT * FROM ctt_staff_private.finality_inputs",
        "SELECT * FROM ctt_staff_private.finality_database_identity",
        "SELECT ctt_staff_private.load_capture_finality_context(gen_random_uuid())",
        "SELECT ctt_staff_private.record_capture_finality(gen_random_uuid(),"
        "'{}'::bytea,'{}'::bytea)",
        "ALTER TABLE ctt_staff_private.capture_finalities DISABLE TRIGGER ALL",
        "TRUNCATE ctt_staff_private.capture_finalities CASCADE",
    ],
)
def test_application_cannot_read_or_mutate_finality_objects(finality_pg, query):
    async def run():
        conn = await connect(finality_pg, "devnous_user")
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(query)
        finally:
            await conn.close()

    asyncio.run(run())


def test_rollback_refuses_configured_identity(finality_pg):
    async def run():
        conn = await connect(finality_pg)
        try:
            await conn.execute(
                "INSERT INTO ctt_staff_private.finality_database_identity"
                "(database_identity) VALUES('isolated-finality')"
            )
            with pytest.raises(asyncpg.RaiseError, match="refuses configured"):
                await conn.execute(ROLLBACK.read_text())
            await conn.execute("ROLLBACK")
            assert (
                await conn.fetchval(
                    "SELECT database_identity "
                    "FROM ctt_staff_private.finality_database_identity"
                )
                == "isolated-finality"
            )
        finally:
            await conn.close()

    asyncio.run(run())


async def record_finality(conn, case, effect, envelope):
    await conn.execute(
        "SELECT ctt_staff_private.record_capture_finality($1,$2,$3)",
        case["attempt"],
        canonical(effect),
        canonical(envelope),
    )


async def activate(conn, effect):
    for model, items in ((Player, effect["players"]), (TeamStaff, effect["staff"])):
        fields = ["governance_state", "finality_receipt_id", "updated_at"]
        if model is Player:
            fields += ["postcommit_revision", "postcommit_snapshot_hash"]
        columns = ",".join(fields)
        for item in items:
            if item["active_row"] is None:
                continue
            result = await conn.execute(
                f"UPDATE public.{model.__tablename__} t SET ({columns})="
                f"(SELECT {columns} FROM jsonb_populate_record("
                f"NULL::public.{model.__tablename__},$1::jsonb)) WHERE t.id=$2",
                json.dumps(item["active_row"]),
                UUID(item["row_id"]),
            )
            assert result == "UPDATE 1"


async def result(conn, case):
    await conn.execute(
        "SELECT ctt_staff_private.record_capture_result($1,$2,$3)",
        case["attempt"],
        uuid4(),
        "sha256:" + hashlib.sha256(canonical(case["envelope"])).hexdigest(),
    )


def test_exact_sql_finality_commits_all_rows_and_real_genesis(finality_pg):
    async def run():
        case = await configure_and_seed(finality_pg)
        conn = await connect(finality_pg, WRITER)
        try:
            async with conn.transaction():
                await begin_pending(conn, case)
                effect, envelope = await finality_payload(conn, case)
                await record_finality(conn, case, effect, envelope)
                await activate(conn, effect)
                await result(conn, case)
            assert (
                await conn.fetchval(
                    "SELECT governance_state FROM public.copa_telmex_players"
                )
                == "ACTIVE"
            )
            assert (
                await conn.fetchval(
                    "SELECT governance_state FROM public.copa_telmex_team_staff"
                )
                == "ACTIVE"
            )
            admin = await connect(finality_pg)
            try:
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM "
                        "public.copa_telmex_registration_postcommit_entity_versions"
                    )
                    == 2
                )
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM ctt_staff_private.capture_results"
                    )
                    == 1
                )
            finally:
                await admin.close()
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "attack",
    [
        "effect_mac",
        "scope",
        "transaction",
        "database",
        "review",
        "material",
        "missing_staff",
        "receipt",
        "event_scope",
        "time",
        "extra_field",
    ],
)
def test_valid_mac_does_not_allow_wrong_finality_context_or_rows(finality_pg, attack):
    async def run():
        case = await configure_and_seed(finality_pg)
        conn = await connect(finality_pg, WRITER)
        try:
            tx = conn.transaction()
            await tx.start()
            try:
                await begin_pending(conn, case)
                effect, envelope = await finality_payload(conn, case)
                if attack == "effect_mac":
                    envelope["body"]["finality_effect_binding"] = (
                        "hmac-sha256:" + "0" * 64
                    )
                elif attack == "scope":
                    effect["scope_hash"] = SHA
                elif attack == "transaction":
                    effect["pg_top_xid"] = str(int(effect["pg_top_xid"]) + 1)
                elif attack == "database":
                    effect["database_oid"] += 1
                elif attack == "review":
                    envelope["body"]["signed_review_hash"] = "sha256:" + "b" * 64
                elif attack == "material":
                    effect["players"][0]["active_row"]["first_name"] = "forged"
                elif attack == "missing_staff":
                    effect["staff"] = effect["staff"][1:]
                elif attack == "receipt":
                    effect["players"][0]["receipt"]["receipt_id"] = "sha256:" + "f" * 64
                elif attack == "event_scope":
                    effect["players"][0]["event"]["tenant_id"] = "other"
                elif attack == "time":
                    envelope["body"]["issued_at"] += 10
                elif attack == "extra_field":
                    effect["players"][0]["invented"] = True
                if attack != "effect_mac":
                    envelope["body"]["finality_effect_binding"] = binding(
                        b"ctt.capture.finality-effect.v1", canonical(effect)
                    )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await record_finality(conn, case, effect, envelope)
            finally:
                await tx.rollback()
            assert (
                await conn.fetchval("SELECT count(*) FROM public.copa_telmex_teams")
                == 0
            )
            assert (
                await conn.fetchval("SELECT count(*) FROM public.copa_telmex_players")
                == 0
            )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "attack",
    [
        "subtransaction",
        "early_result",
        "partial_activation",
        "missing_result",
        "extra_update",
    ],
)
def test_finality_cannot_commit_partial_or_subtransaction_evidence(finality_pg, attack):
    async def run():
        case = await configure_and_seed(finality_pg)
        conn = await connect(finality_pg, WRITER)
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await begin_pending(conn, case)
                    effect, envelope = await finality_payload(conn, case)
                    if attack == "subtransaction":
                        async with conn.transaction():
                            await record_finality(conn, case, effect, envelope)
                    elif attack == "early_result":
                        await result(conn, case)
                    else:
                        await record_finality(conn, case, effect, envelope)
                        if attack == "partial_activation":
                            effect["staff"] = []
                        await activate(conn, effect)
                        if attack == "extra_update":
                            await conn.execute(
                                "UPDATE public.copa_telmex_players "
                                "SET first_name='forged'"
                            )
                        if attack != "missing_result":
                            await result(conn, case)
            assert (
                await conn.fetchval("SELECT count(*) FROM public.copa_telmex_teams")
                == 0
            )
            admin = await connect(finality_pg)
            try:
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM ctt_staff_private.capture_finalities"
                    )
                    == 0
                )
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM "
                        "ctt_staff_private.review_challenge_consumptions"
                    )
                    == 0
                )
            finally:
                await admin.close()
        finally:
            await conn.close()

    asyncio.run(run())


def test_finality_from_rolled_back_transaction_cannot_authorize_retry(finality_pg):
    async def run():
        case = await configure_and_seed(finality_pg)
        conn = await connect(finality_pg, WRITER)
        try:
            tx = conn.transaction()
            await tx.start()
            await begin_pending(conn, case)
            old_effect, old_envelope = await finality_payload(conn, case)
            await tx.rollback()
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await begin_pending(conn, case)
                    fresh_effect, _ = await finality_payload(conn, case)
                    assert (
                        fresh_effect["database_transaction_id"]
                        != old_effect["database_transaction_id"]
                    )
                    assert (
                        fresh_effect["event_attempt_id"]
                        != old_effect["event_attempt_id"]
                    )
                    await record_finality(conn, case, old_effect, old_envelope)
            assert (
                await conn.fetchval("SELECT count(*) FROM public.copa_telmex_players")
                == 0
            )
        finally:
            await conn.close()

    asyncio.run(run())


def test_expired_finality_cannot_commit_even_after_rows_activated(finality_pg):
    async def run():
        case = await configure_and_seed(finality_pg)
        conn = await connect(finality_pg, WRITER)
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await begin_pending(conn, case)
                    effect, envelope = await finality_payload(conn, case)
                    deadline = int(time.time()) + 2
                    envelope["body"]["expires_at"] = deadline
                    await record_finality(conn, case, effect, envelope)
                    await activate(conn, effect)
                    await result(conn, case)
                    await asyncio.sleep(max(0, deadline - time.time()) + 0.02)
            assert (
                await conn.fetchval("SELECT count(*) FROM public.copa_telmex_players")
                == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM public.copa_telmex_team_staff"
                )
                == 0
            )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "attack", ["private_select", "column_grant", "body_drift", "disabled_guard"]
)
def test_finality_cutover_refuses_drifted_protected_dependencies(finality_pg, attack):
    async def run():
        conn = await connect(finality_pg)
        try:
            await conn.execute(ROLLBACK.read_text())
            if attack == "private_select":
                await conn.execute(
                    "GRANT SELECT ON ctt_staff_private.effect_keys TO devnous_user"
                )
            elif attack == "column_grant":
                await conn.execute(
                    "GRANT SELECT(secret) ON ctt_staff_private.effect_keys "
                    "TO devnous_user"
                )
            elif attack == "body_drift":
                definition = await conn.fetchval(
                    "SELECT pg_get_functiondef("
                    "'ctt_staff_private.deny_staff_write()'::regprocedure)"
                )
                await conn.execute(
                    definition.replace(
                        "CTT staff effect denied", "CTT changed effect denied"
                    )
                )
            else:
                await conn.execute(
                    "ALTER TABLE public.copa_telmex_team_staff "
                    "DISABLE TRIGGER ctt_staff_write_closed"
                )
            with pytest.raises(asyncpg.RaiseError):
                await conn.execute(MIGRATION.read_text())
            await conn.execute("ROLLBACK")
            assert await conn.fetchval(
                "SELECT to_regclass('ctt_staff_private.capture_finalities') IS NULL"
            )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["TABLES", "FUNCTIONS"])
def test_finality_postflight_rejects_unknown_default_privileges(finality_pg, kind):
    async def run():
        conn = await connect(finality_pg)
        role = "ctt_finality_default_reader"
        try:
            await conn.execute(ROLLBACK.read_text())
            if not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role
            ):
                await conn.execute(f"CREATE ROLE {role} NOLOGIN")
            privilege = "SELECT" if kind == "TABLES" else "EXECUTE"
            await conn.execute(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE postgres "
                f"GRANT {privilege} ON {kind} TO {role}"
            )
            with pytest.raises(asyncpg.RaiseError, match="installed.*ACL invalid"):
                await conn.execute(MIGRATION.read_text())
            await conn.execute("ROLLBACK")
            assert await conn.fetchval(
                "SELECT to_regclass('ctt_staff_private.capture_finalities') IS NULL"
            )
            # Transactional DDL restored both old function bodies as well.
            assert (
                await conn.fetchval(
                    "SELECT md5(prosrc) FROM pg_proc WHERE oid="
                    "'ctt_staff_private.deny_staff_write()'::regprocedure"
                )
                == "7fdb5522a19627554872e2f3a71f5236"
            )
        finally:
            await conn.close()

    asyncio.run(run())
