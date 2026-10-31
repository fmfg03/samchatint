"""Isolated protected proposal installation and authority boundaries."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

import asyncpg
import pytest
from test_ctt_full_runtime_postgres import full_pg as _full_pg
from test_ctt_staff_security_postgres import PORT

pytest_plugins = ("test_ctt_staff_security_postgres",)
full_pg = _full_pg
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261031_ctt_review_proposals.sql"
ENROLLMENT = ROOT / "database/migrations/20261031_ctt_passkey_enrollment.sql"
RECOVERY = ROOT / "database/migrations/20261031_ctt_assertion_proof_recovery.sql"


async def install_recovery(conn):
    """Install before synthetic issuer grants; preserve the exact fixture grants."""
    signatures = (
        "load_review_assertion_context(uuid)",
        "record_review_assertion_issuance(uuid,text,bigint,bigint,text,"
        "boolean,boolean,text,bigint)",
    )
    for signature in signatures:
        await conn.execute(
            f"REVOKE EXECUTE ON FUNCTION ctt_staff_private.{signature} "
            "FROM ctt_full_issuer"
        )
    await conn.execute(RECOVERY.read_text())
    for signature in signatures:
        await conn.execute(
            f"GRANT EXECUTE ON FUNCTION ctt_staff_private.{signature} "
            "TO ctt_full_issuer"
        )


def test_proposal_install_and_uuid5(full_pg):
    async def run():
        conn = await asyncpg.connect(
            host=full_pg[0],
            port=int(PORT),
            user="postgres",
            database="ctt_full_runtime",
        )
        try:
            await conn.execute(ENROLLMENT.read_text())
            await install_recovery(conn)
            try:
                await conn.execute(MIGRATION.read_text())
            except asyncpg.PostgresError as exc:
                print("POSITION", exc.position, "CONTEXT", exc.context)
                raise
            edition = uuid4()
            actual = await conn.fetchval(
                "SELECT ctt_staff_private.review_document_session($1,$2)",
                edition,
                "Oaxaca varonil 103 PT1",
            )
            assert actual == uuid5(
                NAMESPACE_URL,
                f"ctt-registration-document:{edition}:Oaxaca varonil 103 PT1",
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_proposals"
                )
                == 0
            )
            assert not await conn.fetchval(
                (
                    "SELECT has_function_privilege('devnous_user','ctt_staff_priv"
                    "ate.resolve_review_source_scope(uuid)','EXECUTE')"
                )
            )
        finally:
            await conn.close()

    asyncio.run(run())


async def seed_proposal_case(conn):
    (
        "Synthetic protected source/identity only; "
        "proposal/challenge use service APIs."
    )
    import json
    from dataclasses import replace
    from pathlib import Path
    from test_ctt_capture_anchors_postgres import sample
    from test_ctt_full_runtime_postgres import public_review_source, canonical
    from devnous.copa_telmex.staff_review_challenge import canonical_scope_bytes

    scope, _, _ = await sample(conn)
    scope = replace(
        scope,
        session_id=uuid5(
            NAMESPACE_URL,
            f"ctt-registration-document:{scope.edition_id}:{scope.document_id}",
        ),
        tournament_slug="synthetic",
        decision_hash=None,
        review_receipt_id=None,
        field_evidence_binding="hmac-sha256:" + "f" * 64,
    )
    scope = await public_review_source(
        conn, scope, {"documents": [{"extraction": {}}]}, Path("/tmp"), range(1, 17)
    )
    await conn.execute(
        (
            "INSERT INTO ctt_staff_private.source_admissions "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,clock_timestamp(),session_use"
            "r)"
        ),
        uuid4(),
        scope.manifest_sha256,
        "sha256:" + "f" * 64,
        b"{}",
        "sha256:" + "9" * 64,
        b"{}",
        1,
        2,
    )
    await conn.execute(
        (
            "INSERT INTO ctt_staff_private.source_admission_documents "
            "VALUES($1,$2,$3,$4,$5)"
        ),
        scope.manifest_sha256,
        scope.document_id,
        scope.pdf_sha256,
        "1" * 64,
        "2" * 64,
    )
    for page in scope.source_pages:
        await conn.execute(
            "INSERT INTO ctt_staff_private.source_admission_pages "
            "VALUES($1,$2,$3,$4,$5)",
            scope.manifest_sha256,
            scope.document_id,
            page.page_index,
            page.pdf_page,
            page.image_sha256,
        )
    account = uuid4()
    await conn.execute(
        (
            "DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE "
            "rolname='ctt_proposal_service') THEN CREATE ROLE "
            "ctt_proposal_service LOGIN; END IF; END $$"
        )
    )
    await conn.execute(
        (
            "INSERT INTO ctt_staff_private.review_services(service_role,b"
            "ackend_pin,not_before,not_after,active) "
            "VALUES('ctt_proposal_service','test-backend',now()-interval "
            "'1 minute',now()+interval '1 hour',true)"
        )
    )
    await conn.execute(
        (
            "INSERT INTO ctt_staff_private.review_account_links(backend_p"
            "in,account_id,tenant_id,edition_id,principal_id,actor_role,a"
            "ctive) VALUES('test-backend',$1,$2,$3,$4,$5,true)"
        ),
        account,
        scope.tenant_id,
        scope.edition_id,
        scope.actor_id,
        scope.actor_role,
    )
    await conn.execute(
        "GRANT USAGE ON SCHEMA ctt_staff_private TO ctt_proposal_service"
    )
    for signature in (
        "resolve_review_source_scope(uuid)",
        "load_review_proposal_context(uuid,uuid)",
        (
            "register_review_proposal(uuid,uuid,uuid,uuid,bytea,bytea,byt"
            "ea,bytea,bytea,bytea,uuid)"
        ),
        "read_review_proposal(uuid,uuid,uuid)",
        "create_proposal_challenge(uuid,uuid,uuid,bytea,bigint,bigint)",
        "cancel_review_proposal(uuid,uuid,uuid)",
        "review_proposal_status(uuid,uuid,uuid)",
        "project_review_capture_rows(uuid,uuid,uuid,jsonb,jsonb)",
        "load_review_finality_gate_context(uuid)",
        "register_proposal_capture_payload(uuid,uuid,uuid,bytea,bytea,uuid,"
        "bytea,bytea,bytea)",
    ):
        await conn.execute(
            f"GRANT EXECUTE ON FUNCTION ctt_staff_private.{signature} "
            "TO ctt_proposal_service"
        )
    players = [
        {
            "id": str(uuid4()),
            "roster_index": i,
            "photo_sha256": str(i),
            "photo_ahash": None,
        }
        for i in range(1, 17)
    ]
    ctx = json.loads(canonical_scope_bytes(scope))
    ctx.pop("staff_and_changes_binding")
    plan = {
        "schema": "ctt.capture.review-plan.v1",
        "review_context": ctx,
        "team": {"id": str(scope.team_id), "tournament_slug": scope.tournament_slug},
        "players": players,
    }
    validation = {
        "blockers": [],
        "incident_policy": {
            "minimum_roster": 16,
            "player_results": [
                {
                    "player_index": i,
                    "eligibility_status": "CLEARED",
                    "incidents": [],
                    "counts_toward_minimum": True,
                }
                for i in range(1, 17)
            ],
        },
    }
    return (
        scope,
        account,
        canonical_scope_bytes(scope),
        canonical(plan),
        canonical(validation),
    )


@pytest.mark.parametrize("expiry", [False, True])
def test_proposal_service_cas_and_cancellation(full_pg, expiry):
    async def run():
        import secrets
        import time
        from devnous.copa_telmex.staff_review_proposal_store import (
            PostgresReviewProposalStore,
            ProposalConflict,
        )

        admin = await asyncpg.connect(
            host=full_pg[0],
            port=int(PORT),
            user="postgres",
            database="ctt_full_runtime",
        )
        pool = None
        try:
            await admin.execute(ENROLLMENT.read_text())
            await install_recovery(admin)
            await admin.execute(MIGRATION.read_text())
            scope, account, scope_raw, plan, validation = await seed_proposal_case(
                admin
            )
            pool = await asyncpg.create_pool(
                host=full_pg[0],
                port=int(PORT),
                user="ctt_proposal_service",
                database="ctt_full_runtime",
                min_size=1,
                max_size=2,
            )
            store = PostgresReviewProposalStore(pool, backend_pin="test-backend")
            context = await store.load_context(account, scope.session_id)
            from test_ctt_full_runtime_postgres import canonical

            raw = canonical(context["raw_snapshot"])
            proposal = uuid4()
            nonce = uuid4()
            result = await store.register_proposal(
                account,
                scope.session_id,
                None,
                proposal,
                scope_raw,
                plan,
                b"{}",
                b'{"ready_to_approve":true}',
                validation,
                raw,
                nonce,
            )
            assert result["is_current"] is True
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_attempt_anchors"
                )
                == 0
            )
            attempt = await store.create_challenge(
                account,
                scope.session_id,
                proposal,
                secrets.token_bytes(32),
                int(time.time()),
                int(time.time()) + (2 if expiry else 200),
            )
            assert attempt["credential_data"] == b"synthetic-credential"
            import json

            modeled = json.loads(plan)
            projected = await store.project_capture_rows(
                account,
                scope.session_id,
                UUID(attempt["attempt_id"]),
                modeled["team"],
                [
                    {
                        **p,
                        "team_id": str(scope.team_id),
                        "governance_state": "PENDING_FINALITY",
                    }
                    for p in modeled["players"]
                ],
            )
            assert projected["team"]["id"] == str(scope.team_id)
            assert projected["team"]["postcommit_revision"] == 1
            assert [p["id"] for p in projected["players"]] == [
                p["id"] for p in modeled["players"]
            ]

            status = await store.status(
                account, scope.session_id, UUID(attempt["attempt_id"])
            )
            assert status["challenge_id"] == attempt["challenge_id"]
            assert status["payload_admitted"] is False
            assert status["expired"] is False
            # An identity reply may be lost before payload admission. Retain the
            # actual proof in the issuer audit TX, then recover it through status.
            import hashlib

            retained = b'{"synthetic_signed_review":true}'
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION "
                "ctt_staff_private.record_assertion_proof(uuid,bytea) "
                "TO ctt_full_issuer"
            )
            issuer = await asyncpg.connect(
                host=full_pg[0],
                port=int(PORT),
                user="ctt_full_issuer",
                database="ctt_full_runtime",
            )
            try:
                async with issuer.transaction():
                    assert (
                        await issuer.fetchval(
                            "SELECT ctt_staff_private.record_review_assertion_issuance("
                            "$1,$2,0,1,'device-counter-v1',false,false,NULL,$3)",
                            UUID(attempt["attempt_id"]),
                            "sha256:" + hashlib.sha256(retained).hexdigest(),
                            attempt["expires_at"],
                        )
                        is True
                    )
                    assert (
                        await issuer.fetchval(
                            "SELECT ctt_staff_private.record_assertion_proof($1,$2)",
                            UUID(attempt["attempt_id"]),
                            retained,
                        )
                        is True
                    )
            finally:
                await issuer.close()
            recovered = await store.status(
                account, scope.session_id, UUID(attempt["attempt_id"])
            )
            assert recovered["signed_review"] == retained
            assert recovered["payload_admitted"] is False
            from devnous.copa_telmex.staff_capture_executor import CaptureDenied

            with pytest.raises(CaptureDenied):
                await store.load_finality_gate_context(UUID(attempt["attempt_id"]))
            # Context-only synthetic producer exercise; no Team/Player/Staff
            # rows are inserted. Full E2E independently verifies real signatures.
            from devnous.copa_telmex.staff_capture_effect import bind_capture_effect
            from devnous.copa_telmex.staff_review_challenge import scope_hash

            key = b"context-only-effect-key-32-bytes!!"
            await admin.execute(
                "INSERT INTO ctt_staff_private.effect_keys "
                "VALUES('context-only',$1,now()-interval '1 minute',"
                "now()+interval '1 hour',false)",
                key,
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.finality_database_identity"
                "(database_identity) VALUES('context-only') ON CONFLICT DO NOTHING"
            )
            context_value = json.loads(scope_raw)
            context_value.pop("staff_and_changes_binding")
            effect = canonical(
                {
                    "schema": "ctt.capture.effect.v1",
                    "review_context": context_value,
                    "team": modeled["team"],
                }
            )
            envelope = canonical(
                {
                    "body": {
                        "schema": "ctt.capture.authorization.v1",
                        "scope_hash": scope_hash(scope),
                        "attempt_id": attempt["attempt_id"],
                        "nonce": str(nonce),
                        "review_plan_binding": scope.staff_and_changes_binding,
                        "expires_at": attempt["expires_at"],
                        "effect_binding": bind_capture_effect(effect, key),
                    },
                    "signature": "synthetic-context-only",
                }
            )
            await store.register_payload(
                account,
                scope.session_id,
                UUID(attempt["attempt_id"]),
                effect,
                envelope,
                nonce,
                b"{}",
                b"{}",
                retained,
            )
            await admin.execute("BEGIN")
            await admin.execute("SELECT pg_advisory_xact_lock(1129600050,0)")
            await admin.execute(
                "SELECT 1 FROM ctt_staff_private.review_credentials FOR UPDATE"
            )
            await admin.execute(
                "SELECT 1 FROM ctt_staff_private.capture_attempt_anchors FOR UPDATE"
            )
            await admin.execute(
                "SELECT 1 FROM public.copa_telmex_registration_review_sessions "
                "FOR UPDATE"
            )
            try:
                gate_context = await asyncio.wait_for(
                    store.load_finality_gate_context(UUID(attempt["attempt_id"])), 1
                )
                assert gate_context["database_identity"] == await admin.fetchval(
                    "SELECT database_identity FROM "
                    "ctt_staff_private.finality_database_identity"
                )
                assert gate_context["scope_bytes"] == scope_raw
                assert gate_context["pending_effect_bytes"] == effect
                assert gate_context["signed_review"] == retained
                assert gate_context["validation_bytes"] == validation
                assert gate_context["source_context"]["edition_id"] == str(
                    scope.edition_id
                )
            finally:
                await admin.execute("ROLLBACK")

            await admin.execute("BEGIN")
            await admin.execute("SELECT pg_advisory_xact_lock(1129600050,0)")
            waiting = asyncio.create_task(
                store.status(account, scope.session_id, UUID(attempt["attempt_id"]))
            )
            await asyncio.sleep(2.1 if expiry else 0.05)
            assert not waiting.done()
            await admin.execute("COMMIT")
            settled = await waiting
            assert settled["payload_admitted"] is True
            assert settled["expired"] is expiry

            with pytest.raises(ProposalConflict):
                await store.register_proposal(
                    account,
                    scope.session_id,
                    None,
                    uuid4(),
                    scope_raw,
                    plan,
                    b"{}",
                    b"{}",
                    validation,
                    raw,
                    uuid4(),
                )
            await store.cancel(account, scope.session_id, proposal)
            with pytest.raises(CaptureDenied):
                await store.load_finality_gate_context(UUID(attempt["attempt_id"]))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await admin.fetchval(
                    "SELECT ctt_staff_private.load_review_assertion_context($1)",
                    UUID(attempt["attempt_id"]),
                )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_attempt_anchors"
                )
                == 1
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_payloads"
                )
                == 1
            )
        finally:
            if pool:
                await pool.close()
            await admin.close()

    asyncio.run(run())


def test_proposal_empty_rollback_restores_guards(full_pg):
    async def run():
        conn = await asyncpg.connect(
            host=full_pg[0],
            port=int(PORT),
            user="postgres",
            database="ctt_full_runtime",
        )
        try:
            await conn.execute(ENROLLMENT.read_text())
            await install_recovery(conn)
            before = await conn.fetch(
                (
                    "SELECT oid::regprocedure::text "
                    "signature,pg_get_functiondef(oid) definition FROM pg_proc "
                    "WHERE oid=ANY(ARRAY['ctt_staff_private.load_review_assertion"
                    "_context(uuid)'::regprocedure,'ctt_staff_private.load_captur"
                    "e_finality_context(uuid)'::regprocedure,'ctt_staff_private.g"
                    "uard_legacy_v2_boundary()'::regprocedure])"
                )
            )
            await conn.execute(MIGRATION.read_text())
            await conn.execute(
                (
                    ROOT
                    / "database/migrations"
                    / "20261031_ctt_review_proposals.rollback.sql"
                ).read_text()
            )
            for item in before:
                assert (
                    await conn.fetchval(
                        "SELECT pg_get_functiondef($1::regprocedure)", item["signature"]
                    )
                    == item["definition"]
                )
            assert (
                await conn.fetchval(
                    "SELECT to_regclass('ctt_staff_private.review_proposals')"
                )
                is None
            )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["table", "function"])
def test_proposal_rejects_unexpected_default_acl(full_pg, kind):
    async def run():
        conn = await asyncpg.connect(
            host=full_pg[0],
            port=int(PORT),
            user="postgres",
            database="ctt_full_runtime",
        )
        try:
            await conn.execute(ENROLLMENT.read_text())
            await install_recovery(conn)
            await conn.execute(
                (
                    "DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE "
                    "rolname='ctt_unexpected_proposal_reader') THEN CREATE ROLE "
                    "ctt_unexpected_proposal_reader; END IF; END $$"
                )
            )
            await conn.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA ctt_staff_private GRANT "
                + ("SELECT ON TABLES" if kind == "table" else "EXECUTE ON FUNCTIONS")
                + " TO ctt_unexpected_proposal_reader"
            )
            with pytest.raises(asyncpg.PostgresError, match="ACL invalid"):
                await conn.execute(MIGRATION.read_text())
            await conn.execute("ROLLBACK")
            assert (
                await conn.fetchval(
                    "SELECT to_regclass('ctt_staff_private.review_proposals')"
                )
                is None
            )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        "minimum15",
        "minimum17",
        "slotboolean",
        "missingstatus",
        "blocking",
        "duplicates",
        "busy",
        "wrongaccount",
        "rawforgery",
    ],
)
def test_proposal_policy_and_capture_attacks(full_pg, mutation):
    async def run():
        import json
        import secrets
        import time
        from test_ctt_full_runtime_postgres import canonical
        from devnous.copa_telmex.staff_review_proposal_store import (
            PostgresReviewProposalStore,
        )
        from devnous.copa_telmex.staff_capture_executor import CaptureDenied

        admin = await asyncpg.connect(
            host=full_pg[0],
            port=int(PORT),
            user="postgres",
            database="ctt_full_runtime",
        )
        pool = None
        blocker = None
        try:
            await admin.execute(ENROLLMENT.read_text())
            await install_recovery(admin)
            await admin.execute(MIGRATION.read_text())
            scope, account, scope_raw, plan_raw, validation_raw = (
                await seed_proposal_case(admin)
            )
            pool = await asyncpg.create_pool(
                host=full_pg[0],
                port=int(PORT),
                user="ctt_proposal_service",
                database="ctt_full_runtime",
                min_size=1,
                max_size=2,
            )
            store = PostgresReviewProposalStore(pool)
            context = await store.load_context(account, scope.session_id)
            raw = canonical(context["raw_snapshot"])
            validation = json.loads(validation_raw)
            plan = json.loads(plan_raw)
            if mutation == "minimum15":
                validation["incident_policy"]["minimum_roster"] = 15
            if mutation == "minimum17":
                validation["incident_policy"]["minimum_roster"] = 17
            if mutation == "slotboolean":
                validation["incident_policy"]["player_results"][0][
                    "player_index"
                ] = True
            if mutation == "missingstatus":
                del validation["incident_policy"]["player_results"][0][
                    "eligibility_status"
                ]
            if mutation == "blocking":
                validation["incident_policy"]["player_results"][0]["incidents"] = [
                    {
                        "blocks_team_registration": True,
                        "blocks_player_eligibility": False,
                    }
                ]
            if mutation == "duplicates":
                plan["players"][1]["photo_sha256"] = plan["players"][0]["photo_sha256"]
            if mutation == "rawforgery":
                forged = dict(context["raw_snapshot"])
                forged["extraction"] = {"changed": True}
                with pytest.raises(CaptureDenied, match="raw draft hash"):
                    await store.register_proposal(
                        account,
                        scope.session_id,
                        None,
                        uuid4(),
                        scope_raw,
                        canonical(plan),
                        b"{}",
                        b'{"ready_to_approve":true}',
                        canonical(validation),
                        canonical(forged),
                        uuid4(),
                    )
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM ctt_staff_private.review_proposals"
                    )
                    == 0
                )
                return
            if mutation == "wrongaccount":
                with pytest.raises(CaptureDenied):
                    await store.load_context(uuid4(), scope.session_id)
                return
            proposal = uuid4()
            await store.register_proposal(
                account,
                scope.session_id,
                None,
                proposal,
                scope_raw,
                canonical(plan),
                b"{}",
                b'{"ready_to_approve":true}',
                canonical(validation),
                raw,
                uuid4(),
            )
            if mutation in {"duplicates", "busy"}:
                attempt = await store.create_challenge(
                    account,
                    scope.session_id,
                    proposal,
                    secrets.token_bytes(32),
                    int(time.time()),
                    int(time.time()) + 200,
                )
                if mutation == "busy":
                    blocker = await asyncpg.connect(
                        host=full_pg[0],
                        port=int(PORT),
                        user="postgres",
                        database="ctt_full_runtime",
                    )
                    await blocker.execute("BEGIN")
                    await blocker.execute(
                        "SELECT pg_advisory_xact_lock_shared(1129600050,0)"
                    )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await admin.execute(
                        "SELECT ctt_staff_private.assert_current_review_proposal("
                        "$1,false,true)",
                        UUID(attempt["attempt_id"]),
                    )
            else:
                with pytest.raises(CaptureDenied):
                    await store.create_challenge(
                        account,
                        scope.session_id,
                        proposal,
                        secrets.token_bytes(32),
                        int(time.time()),
                        int(time.time()) + 200,
                    )
            with pytest.raises(asyncpg.PostgresError, match="refuses used data"):
                await admin.execute(
                    (
                        ROOT
                        / "database/migrations"
                        / "20261031_ctt_review_proposals.rollback.sql"
                    ).read_text()
                )
            await admin.execute("ROLLBACK")
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_proposals"
                )
                == 1
            )
        finally:
            if blocker:
                await blocker.close()
            if pool:
                await pool.close()
            await admin.close()

    asyncio.run(run())


def test_proposal_rejects_dependency_fingerprint_drift(full_pg):
    async def run():
        conn = await asyncpg.connect(
            host=full_pg[0],
            port=int(PORT),
            user="postgres",
            database="ctt_full_runtime",
        )
        try:
            await conn.execute(ENROLLMENT.read_text())
            await install_recovery(conn)
            definition = await conn.fetchval(
                (
                    "SELECT pg_get_functiondef('ctt_staff_private.load_review_ass"
                    "ertion_context(uuid)'::regprocedure)"
                )
            )
            await conn.execute(
                definition.replace("BEGIN\n", "BEGIN\n -- synthetic drift\n", 1)
            )
            with pytest.raises(asyncpg.PostgresError, match="fingerprint invalid"):
                await conn.execute(MIGRATION.read_text())
            await conn.execute("ROLLBACK")
            assert (
                await conn.fetchval(
                    "SELECT to_regclass('ctt_staff_private.review_proposals')"
                )
                is None
            )
        finally:
            await conn.close()

    asyncio.run(run())


def test_proposal_app_cannot_admit_or_inspect_private_state(full_pg):
    async def run():
        admin = await asyncpg.connect(
            host=full_pg[0],
            port=int(PORT),
            user="postgres",
            database="ctt_full_runtime",
        )
        app = None
        try:
            await admin.execute(ENROLLMENT.read_text())
            await install_recovery(admin)
            await admin.execute(MIGRATION.read_text())
            app = await asyncpg.connect(
                host=full_pg[0],
                port=int(PORT),
                user="devnous_user",
                database="ctt_full_runtime",
            )
            await app.execute(
                "SELECT set_config('samchat.ctt.authorized','true',false)"
            )
            for sql in (
                "SELECT * FROM ctt_staff_private.review_proposals",
                (
                    "SELECT ctt_staff_private.resolve_review_source_scope('000000"
                    "00-0000-0000-0000-000000000001')"
                ),
                (
                    "INSERT INTO ctt_staff_private.review_proposal_heads(session_"
                    "id) VALUES('00000000-0000-0000-0000-000000000001')"
                ),
                (
                    "SELECT ctt_staff_private.check_review_proposal_policy('00000"
                    "000-0000-0000-0000-000000000001',false)"
                ),
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.execute(sql)
        finally:
            if app:
                await app.close()
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "ready,blockers,extra",
    [
        (False, ["staff unknown"], False),
        (True, ["staff unknown"], False),
        (False, [], False),
        (False, ["staff unknown"], True),
    ],
)
def test_incomplete_preview_never_creates_challenge(full_pg, ready, blockers, extra):
    async def run():
        import json
        import secrets
        import time
        from test_ctt_full_runtime_postgres import canonical
        from devnous.copa_telmex.staff_review_proposal_store import (
            PostgresReviewProposalStore,
        )
        from devnous.copa_telmex.staff_capture_executor import CaptureDenied

        admin = await asyncpg.connect(
            host=full_pg[0],
            port=int(PORT),
            user="postgres",
            database="ctt_full_runtime",
        )
        pool = None
        try:
            await admin.execute(ENROLLMENT.read_text())
            await install_recovery(admin)
            await admin.execute(MIGRATION.read_text())
            scope, account, scope_raw, plan_raw, _ = await seed_proposal_case(admin)
            plan = json.loads(plan_raw)
            incomplete = {
                "schema": "ctt.review.incomplete-proposal.v1",
                "review_context": plan["review_context"],
                "team": plan["team"],
                "observations": {
                    "team": {},
                    "players": [],
                    "staff": [{"presence": "UNKNOWN"}],
                },
            }
            if extra:
                incomplete["players"] = plan["players"]
            pool = await asyncpg.create_pool(
                host=full_pg[0],
                port=int(PORT),
                user="ctt_proposal_service",
                database="ctt_full_runtime",
                min_size=1,
                max_size=1,
            )
            store = PostgresReviewProposalStore(pool)
            ctx = await store.load_context(account, scope.session_id)
            proposal = uuid4()
            args = (
                account,
                scope.session_id,
                None,
                proposal,
                scope_raw,
                canonical(incomplete),
                b"{}",
                canonical({"ready_to_approve": ready}),
                canonical({"blockers": blockers}),
                canonical(ctx["raw_snapshot"]),
                uuid4(),
            )
            if ready or not blockers or extra:
                with pytest.raises(CaptureDenied):
                    await store.register_proposal(*args)
            else:
                result = await store.register_proposal(*args)
                assert result["is_current"] is True
                with pytest.raises(CaptureDenied):
                    await store.create_challenge(
                        account,
                        scope.session_id,
                        proposal,
                        secrets.token_bytes(32),
                        int(time.time()),
                        int(time.time()) + 100,
                    )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_attempt_anchors"
                )
                == 0
            )
        finally:
            if pool:
                await pool.close()
            await admin.close()

    asyncio.run(run())
