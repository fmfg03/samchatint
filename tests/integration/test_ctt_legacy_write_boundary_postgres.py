"""Selective legacy SQL boundary on the restored, data-free operational schema.

Synthetic SQL evidence tests structural v1 compatibility, not accreditation of
receipts or people. Python capability verification is tested separately.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from uuid import UUID, uuid4

import asyncpg
import pytest

from devnous.copa_telmex.models import (
    Player,
    Team,
    RegistrationPostcommitMutationProposal,
    RegistrationPostcommitMutationDecision,
    RegistrationPostcommitMutationExecution,
)
from test_ctt_staff_security_postgres import isolated_pg as _isolated_pg, PORT
from test_ctt_capture_anchors_postgres import sample, insert_attempt

isolated_pg = _isolated_pg
ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "database/migrations"
BIN = "/usr/lib/postgresql/16/bin/"


def command(args):
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        pytest.fail(result.stderr.decode("utf-8", "replace")[:2500])


@pytest.fixture
def boundary_pg(isolated_pg):
    value = os.environ.get("CTT_SCHEMA_ONLY_DIRECTORY")
    if not value:
        pytest.skip("Set CTT_SCHEMA_ONLY_DIRECTORY to reviewed schema-only export")
    source = Path(value).resolve(strict=True)
    assert source.is_relative_to(Path("/tmp"))
    receipt = json.loads((source / "receipt.json").read_text())
    assert receipt["schema_only"] is True
    for name, expected in receipt["hashes"].items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == expected
    database = "ctt_legacy_boundary"

    async def setup():
        admin = await asyncpg.connect(
            host=isolated_pg, port=int(PORT), user="postgres", database="postgres"
        )
        try:
            for role in json.loads((source / "role-names.json").read_text()):
                if not await admin.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role
                ):
                    await admin.execute(
                        'CREATE ROLE "' + role.replace('"', '""') + '" NOLOGIN'
                    )
            await admin.execute("CREATE DATABASE " + database)
            conn = await asyncpg.connect(
                host=isolated_pg, port=int(PORT), user="postgres", database=database
            )
            try:
                await conn.execute("CREATE EXTENSION pgcrypto")
                base = ["-h", isolated_pg, "-p", PORT, "-U", "postgres", "-d", database]
                command(
                    [
                        BIN + "pg_restore",
                        "--exit-on-error",
                        "--section=pre-data",
                        *base,
                        str(source / "ctt.dump"),
                    ]
                )
                command(
                    [
                        BIN + "psql",
                        "-X",
                        "-v",
                        "ON_ERROR_STOP=1",
                        *base,
                        "-c",
                        "SET search_path=public,pg_catalog",
                        "-f",
                        str(source / "functions.sql"),
                    ]
                )
                command(
                    [
                        BIN + "pg_restore",
                        "--exit-on-error",
                        "--section=post-data",
                        *base,
                        str(source / "ctt.dump"),
                    ]
                )
                for name in [
                    "20261028_ctt_staff_security",
                    "20261028_ctt_staff_review_state",
                    "20261030_ctt_capture_anchors",
                    "20261030_ctt_signed_assertion_guard",
                    "20261030_ctt_capture_payloads",
                    "20261030_ctt_staff_effect_gate",
                ]:
                    await conn.execute((MIGRATIONS / (name + ".sql")).read_text())
                await conn.execute(
                    (MIGRATIONS / "20261030_ctt_legacy_write_boundary.sql").read_text()
                )
            finally:
                await conn.close()
        finally:
            await admin.close()

    asyncio.run(setup())
    yield isolated_pg, database

    async def cleanup():
        admin = await asyncpg.connect(
            host=isolated_pg, port=int(PORT), user="postgres", database="postgres"
        )
        try:
            await admin.execute("DROP DATABASE " + database)
        finally:
            await admin.close()

    asyncio.run(cleanup())


async def connect(context, user="postgres"):
    return await asyncpg.connect(
        host=context[0], port=int(PORT), database=context[1], user=user
    )


def row(model):
    value = {}
    for column in model.__table__.columns:
        if column.nullable:
            value[column.name] = None
            continue
        kind = column.type.python_type
        if kind is UUID:
            value[column.name] = str(uuid4())
        elif kind is int:
            value[column.name] = 1
        elif kind is bool:
            value[column.name] = False
        elif kind is dict:
            value[column.name] = {}
        elif kind is str:
            value[column.name] = "synthetic"
        else:
            value[column.name] = (
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            )
    if "postcommit_snapshot_hash" in value:
        value["postcommit_snapshot_hash"] = "sha256:" + "0" * 64
    return value


async def insert_v1(conn):
    team = row(Team)
    result = json.loads(
        await conn.fetchval(
            "SELECT public.legacy_insert_team($1::jsonb)", json.dumps(team)
        )
    )
    player = row(Player)
    player.update(
        team_id=team["id"],
        governance_state="PENDING_FINALITY",
        postcommit_revision=0,
        governance_draft_id=str(uuid4()),
        governance_draft_version=1,
        governance_decision_id="sha256:" + "a" * 64,
        roster_draft_binding="hmac-sha256:" + "b" * 64,
        preauthorization_receipt_id="sha256:" + "c" * 64,
    )
    p = json.loads(
        await conn.fetchval(
            "SELECT public.legacy_insert_player($1::jsonb)", json.dumps(player)
        )
    )
    return result, p


def test_v1_inserts_finality_and_genesis_remain_functional(boundary_pg):
    async def run():
        app = await connect(boundary_pg, "devnous_user")
        try:
            team, player = await insert_v1(app)
            assert (
                team["postcommit_revision"] == 1 and player["postcommit_revision"] == 0
            )
            receipt = "sha256:" + "d" * 64
            finalized = json.loads(
                await app.fetchval(
                    "SELECT public.legacy_finalize_player($1,$2)",
                    UUID(player["id"]),
                    receipt,
                )
            )
            assert (
                finalized["governance_state"] == "ACTIVE"
                and finalized["postcommit_revision"] == 1
            )
            assert finalized["finality_receipt_id"] == receipt
            assert (
                json.loads(
                    await app.fetchval(
                        "SELECT public.legacy_finalize_player($1,$2)",
                        UUID(player["id"]),
                        receipt,
                    )
                )
                == finalized
            )
            assert (
                await app.fetchval(
                    (
                        "SELECT count(*) FROM "
                        "public.copa_telmex_registration_postcommit_entity_vers"
                        "ions"
                    )
                )
                == 2
            )
        finally:
            await app.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "attack",
    [
        "insert",
        "update",
        "delete",
        "truncate",
        "copy",
        "alter",
        "drop",
        "version",
        "trigger",
        "private",
    ],
)
def test_direct_application_mutations_and_ddl_are_denied(boundary_pg, attack):
    async def run():
        app = await connect(boundary_pg, "devnous_user")
        try:
            queries = {
                "insert": (
                    "INSERT INTO public.copa_telmex_teams(id,name,created_a"
                    "t) VALUES(gen_random_uuid(),'synthetic',now())"
                ),
                "update": "UPDATE public.copa_telmex_teams SET name='forged'",
                "delete": "DELETE FROM public.copa_telmex_players",
                "truncate": "TRUNCATE public.copa_telmex_teams CASCADE",
                "copy": "COPY public.copa_telmex_teams FROM STDIN",
                "alter": "ALTER TABLE public.copa_telmex_teams DISABLE TRIGGER ALL",
                "drop": "DROP TABLE public.copa_telmex_teams CASCADE",
                "version": (
                    "INSERT INTO public.copa_telmex_registration_postcommit"
                    "_entity_versions(id) VALUES(gen_random_uuid())"
                ),
                "trigger": (
                    "CREATE TRIGGER forged BEFORE INSERT ON "
                    "public.copa_telmex_teams FOR EACH ROW EXECUTE "
                    "FUNCTION public.regs07_initialize_team()"
                ),
                "private": "SELECT * FROM ctt_staff_private.legacy_v2_reservations",
            }
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.execute(queries[attack])
        finally:
            await app.close()

    asyncio.run(run())


def test_source_functions_and_table_ownership_are_protected(boundary_pg):
    async def run():
        conn = await connect(boundary_pg)
        try:
            assert (
                await conn.fetchval(
                    (
                        "SELECT count(*) FROM "
                        "ctt_staff_private.legacy_boundary_baseline WHERE "
                        "object_kind='table'"
                    )
                )
                == 19
            )
            assert (
                await conn.fetchval(
                    (
                        "SELECT count(*) FROM "
                        "ctt_staff_private.legacy_boundary_baseline WHERE "
                        "object_kind='function'"
                    )
                )
                == 17
            )
            assert not await conn.fetchval(
                (
                    "SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN "
                    "pg_namespace n ON n.oid=p.pronamespace WHERE "
                    "n.nspname='public' AND p.proname LIKE 'regs07_%' AND "
                    "(p.proconfig IS DISTINCT FROM "
                    "ARRAY['search_path=pg_catalog']::text[] OR NOT "
                    "p.prosecdef))"
                )
            )
        finally:
            await conn.close()

    asyncio.run(run())


def test_v2_reservation_denies_legacy_entities_and_session_children(boundary_pg):
    async def run():
        admin, app = await connect(boundary_pg), await connect(
            boundary_pg, "devnous_user"
        )
        try:
            scope, challenge, attempt = await sample(admin)
            await source_review_rows(app, scope)
            await insert_attempt(admin, scope, challenge, attempt)
            team = row(Team)
            team["id"] = str(scope.team_id)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.fetchval(
                    "SELECT public.legacy_insert_team($1::jsonb)", json.dumps(team)
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.execute(
                    (
                        "INSERT INTO public.copa_telmex_registration_review_ses"
                        "sions(id,status,source,provider,started_at) "
                        "VALUES($1,'uploaded','web','local',now())"
                    ),
                    scope.session_id,
                )
            with pytest.raises(asyncpg.RaiseError, match="rollback refused"):
                await admin.execute(
                    (
                        MIGRATIONS / "20261030_ctt_legacy_write_boundary.rollback.sql"
                    ).read_text()
                )
            await admin.execute("ROLLBACK")
        finally:
            await app.close()
            await admin.close()

    asyncio.run(run())


def test_rollback_restores_original_owners_and_v1_rows(boundary_pg):
    async def run():
        app, admin = await connect(boundary_pg, "devnous_user"), await connect(
            boundary_pg
        )
        try:
            await insert_v1(app)
            await admin.execute(
                (
                    MIGRATIONS / "20261030_ctt_legacy_write_boundary.rollback.sql"
                ).read_text()
            )
            assert (
                await app.fetchval("SELECT count(*) FROM public.copa_telmex_teams") == 1
            )
            assert await app.fetchval(
                (
                    "SELECT has_table_privilege(current_user,'public.copa_t"
                    "elmex_teams','INSERT,UPDATE,DELETE')"
                )
            )
            assert await admin.fetchval(
                "SELECT to_regprocedure('public.legacy_insert_team(jsonb)') IS NULL"
            )
        finally:
            await app.close()
            await admin.close()

    asyncio.run(run())


async def insert_model(conn, model, values):
    table = model.__tablename__
    await conn.execute(
        f"INSERT INTO public.{table} SELECT "
        f"(jsonb_populate_record(NULL::public.{table},$1::jsonb)).*",
        json.dumps(values),
    )


def test_cohort_guard_follows_fk_parents_and_both_update_sides(boundary_pg):
    from devnous.copa_telmex.models import (
        RegistrationReviewSession,
        RegistrationReviewDraft,
        RegistrationHumanFieldEditProposal,
        RegistrationHumanFieldApproval,
    )

    async def run():
        admin, app = await connect(boundary_pg), await connect(
            boundary_pg, "devnous_user"
        )
        try:
            scope, challenge, attempt = await sample(admin)
            session = row(RegistrationReviewSession)
            session.update(
                id=str(scope.session_id),
                status="uploaded",
                source="web",
                provider="local",
            )
            draft = row(RegistrationReviewDraft)
            draft.update(
                id=str(scope.draft_id),
                session_id=session["id"],
                draft_version=1,
                content_hash=scope.draft_content_hash,
                mutation_type="initial",
                mutation_operation_id="sha256:" + "8" * 64,
            )
            proposal = row(RegistrationHumanFieldEditProposal)
            proposal.update(
                session_id=session["id"],
                base_draft_id=draft["id"],
                base_draft_version=1,
            )
            for model, value in [
                (RegistrationReviewSession, session),
                (RegistrationReviewDraft, draft),
                (RegistrationHumanFieldEditProposal, proposal),
            ]:
                await insert_model(app, model, value)
            await source_review_rows(app, scope)
            await insert_attempt(admin, scope, challenge, attempt)
            approval = row(RegistrationHumanFieldApproval)
            approval["proposal_id"] = proposal["id"]
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await insert_model(app, RegistrationHumanFieldApproval, approval)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.execute(
                    (
                        "UPDATE public.copa_telmex_registration_review_sessions"
                        " SET status='rejected' WHERE id=$1"
                    ),
                    scope.session_id,
                )
            # Rebinding a legacy session to a reserved v2 team is also denied
            # before the nonexistent-team FK could become its authority.
            unrelated = row(RegistrationReviewSession)
            unrelated.update(status="uploaded", source="web", provider="local")
            await insert_model(app, RegistrationReviewSession, unrelated)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.execute(
                    (
                        "UPDATE public.copa_telmex_registration_review_sessions"
                        " SET committed_team_id=$1 WHERE id=$2"
                    ),
                    scope.team_id,
                    UUID(unrelated["id"]),
                )
        finally:
            await app.close()
            await admin.close()

    asyncio.run(run())


def test_copadb_postgres_adapters_keep_real_capability_checks(boundary_pg):
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from devnous.copa_telmex.database import CopaTelmexDB
    from devnous.copa_telmex.persistence_authority import (
        issue_registration_persistence_capability,
        semantic_event_hash,
        PersistenceAuthorityDenied,
    )

    async def run():
        engine = create_async_engine(
            URL.create(
                "postgresql+asyncpg",
                username="devnous_user",
                host=boundary_pg[0],
                port=int(PORT),
                database=boundary_pg[1],
            )
        )
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                db = CopaTelmexDB(session)
                team_id = uuid4()
                tenant = "synthetic"
                draft = "draft-legacy-structural-test"
                event = dict(
                    event_type="samchat_registration_roster_decision_v1",
                    tenant_id=tenant,
                    draft_id=draft,
                    draft_version=1,
                    team_id=str(team_id),
                    decision_id="sha256:" + "1" * 64,
                    roster_draft_binding="hmac-sha256:" + "2" * 64,
                    ordered_player_slots=[1],
                    decision="AUTHORIZE_PENDING_MATERIALIZATION",
                )
                receipt = dict(
                    receipt_type="EvidenceReceipt.v1",
                    receipt_id="sha256:" + "3" * 64,
                    event_hash=semantic_event_hash(event),
                    event_type=event["event_type"],
                    tenant_id=tenant,
                    verified=True,
                )
                cap = issue_registration_persistence_capability(
                    dict(
                        authorized=True,
                        roster_decision=event,
                        preauthorization_receipt=receipt,
                    ),
                    tenant_id=tenant,
                    draft_id=draft,
                    draft_version=1,
                    team_id=str(team_id),
                )
                db.bind_persistence_authority(cap)
                team = await db.create_team(
                    name="Synthetic team", telegram_chat_id=1, team_id=team_id
                )
                player = await db.create_player(
                    team_id=team.id,
                    first_name="Synthetic",
                    last_name="Player",
                    roster_index=1,
                    governance_state="PENDING_FINALITY",
                    governance_draft_id=draft,
                    governance_draft_version=1,
                    governance_decision_id=event["decision_id"],
                    roster_draft_binding=event["roster_draft_binding"],
                    preauthorization_receipt_id=receipt["receipt_id"],
                )
                attestation = dict(
                    event_type="samchat_registration_finality_attestation_v1",
                    tenant_id=tenant,
                    draft_id=draft,
                    draft_version=1,
                    player_id=str(player.id),
                    player_slot=1,
                    roster_draft_binding=event["roster_draft_binding"],
                    preauthorization_receipt_id=receipt["receipt_id"],
                    decision="ACTIVATE_PLAYER",
                )
                final_receipt = dict(
                    receipt_type="EvidenceReceipt.v1",
                    receipt_id="sha256:" + "4" * 64,
                    event_hash=semantic_event_hash(attestation),
                    event_type=attestation["event_type"],
                    tenant_id=tenant,
                    verified=True,
                )
                with pytest.raises(PersistenceAuthorityDenied):
                    await db.finalize_player(
                        player,
                        dict(
                            activate=True,
                            attestation=attestation,
                            finality_receipt={
                                **final_receipt,
                                "event_hash": "sha256:" + "f" * 64,
                            },
                        ),
                    )
                assert player.governance_state == "PENDING_FINALITY"
                await db.finalize_player(
                    player,
                    dict(
                        activate=True,
                        attestation=attestation,
                        finality_receipt=final_receipt,
                    ),
                )
                assert (
                    player.governance_state == "ACTIVE"
                    and player.postcommit_revision == 1
                )
                assert not session.new and not session.dirty
                await session.commit()
            admin = await connect(boundary_pg)
            try:
                assert (
                    await admin.fetchval(
                        (
                            "SELECT count(*) FROM "
                            "public.copa_telmex_registration_postcommit_entity_vers"
                            "ions"
                        )
                    )
                    == 2
                )
            finally:
                await admin.close()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_regs07_exact_successor_works_through_fixed_entrypoint(boundary_pg):
    async def run():
        app = await connect(boundary_pg, "devnous_user")
        try:
            team, _ = await insert_v1(app)
            base = json.loads(
                await app.fetchval(
                    (
                        "SELECT public.regs07_team_snapshot(t) FROM "
                        "public.copa_telmex_teams t WHERE id=$1"
                    ),
                    UUID(team["id"]),
                )
            )
            proposed = {**base, "name": "Synthetic successor"}
            new_hash = await app.fetchval(
                "SELECT public.regs07_snapshot_hash($1::jsonb)", json.dumps(proposed)
            )
            prop = row(RegistrationPostcommitMutationProposal)
            prop.update(
                entity_type="TEAM",
                entity_id=team["id"],
                team_id=team["id"],
                mutation_type="EDIT_TEAM",
                base_revision=team["postcommit_revision"],
                proposed_revision=team["postcommit_revision"] + 1,
                base_snapshot=base,
                base_snapshot_hash=team["postcommit_snapshot_hash"],
                proposed_snapshot=proposed,
                proposed_snapshot_hash=new_hash,
            )
            decision = row(RegistrationPostcommitMutationDecision)
            decision.update(
                proposal_id=prop["id"],
                decision="AUTHORIZE_POSTCOMMIT_MUTATION",
                receipt_alg="Ed25519",
                expires_at="2099-01-01T00:00:00",
            )
            execution = row(RegistrationPostcommitMutationExecution)
            execution.update(
                proposal_id=prop["id"],
                decision_id=decision["id"],
                finality_receipt_alg="Ed25519",
                finality_event_document=dict(
                    decision="ATTEST_POSTCOMMIT_MUTATION",
                    proposal_id=prop["id"],
                    actual_projection_hash=new_hash,
                ),
            )
            async with app.transaction():
                for model, values in [
                    (RegistrationPostcommitMutationProposal, prop),
                    (RegistrationPostcommitMutationDecision, decision),
                    (RegistrationPostcommitMutationExecution, execution),
                ]:
                    await insert_model(app, model, values)
                await app.execute(
                    "SELECT set_config('samchat.regs07_execution_id',$1,true)",
                    execution["id"],
                )
                updated = json.loads(
                    await app.fetchval(
                        (
                            "SELECT public.legacy_apply_postcommit_projection($1,$2"
                            ",$3,$4,$5::jsonb)"
                        ),
                        "TEAM",
                        UUID(team["id"]),
                        team["postcommit_revision"],
                        team["postcommit_snapshot_hash"],
                        json.dumps({"name": "Synthetic successor"}),
                    )
                )
                assert (
                    updated["name"] == "Synthetic successor"
                    and updated["postcommit_revision"] == 2
                    and updated["postcommit_snapshot_hash"] == new_hash
                )
            assert (
                await app.fetchval(
                    (
                        "SELECT count(*) FROM "
                        "public.copa_telmex_registration_postcommit_entity_vers"
                        "ions"
                    )
                )
                == 2
            )
        finally:
            await app.close()

    asyncio.run(run())


async def source_review_rows(conn, scope):
    from devnous.copa_telmex.models import (
        RegistrationReviewSession,
        RegistrationReviewDraft,
        RegistrationReviewAsset,
    )

    if not await conn.fetchval(
        (
            "SELECT EXISTS(SELECT 1 FROM "
            "public.copa_telmex_registration_review_sessions WHERE "
            "id=$1)"
        ),
        scope.session_id,
    ):
        session = row(RegistrationReviewSession)
        session.update(
            id=str(scope.session_id), status="uploaded", source="web", provider="local"
        )
        await insert_model(conn, RegistrationReviewSession, session)
    if not await conn.fetchval(
        (
            "SELECT EXISTS(SELECT 1 FROM "
            "public.copa_telmex_registration_review_drafts WHERE "
            "id=$1)"
        ),
        scope.draft_id,
    ):
        draft = row(RegistrationReviewDraft)
        draft.update(
            id=str(scope.draft_id),
            session_id=str(scope.session_id),
            draft_version=scope.draft_version,
            content_hash=scope.draft_content_hash,
            mutation_type="initial",
            mutation_operation_id="sha256:" + str(uuid4()).replace("-", "") * 2,
        )
        await insert_model(conn, RegistrationReviewDraft, draft)
    for page in scope.source_pages:
        asset = row(RegistrationReviewAsset)
        asset.update(
            id=str(page.asset_id),
            session_id=str(scope.session_id),
            page_index=page.page_index,
            sha256=page.image_sha256.removeprefix("sha256:"),
            image_path="synthetic-private-source.png",
            width=800,
            height=1000,
        )
        await insert_model(conn, RegistrationReviewAsset, asset)


def test_reservation_rejects_locked_source_without_deadlock_and_retries(boundary_pg):
    async def run():
        admin, app = await connect(boundary_pg), await connect(
            boundary_pg, "devnous_user"
        )
        try:
            scope, challenge, attempt = await sample(admin)
            await source_review_rows(app, scope)
            async with app.transaction():
                await app.fetchrow(
                    (
                        "SELECT id FROM public.copa_telmex_registration_review_"
                        "sessions WHERE id=$1 FOR UPDATE"
                    ),
                    scope.session_id,
                )
                with pytest.raises(
                    asyncpg.InsufficientPrivilegeError, match="source busy"
                ):
                    await asyncio.wait_for(
                        insert_attempt(admin, scope, challenge, attempt), timeout=1
                    )
                assert not await admin.fetchval(
                    (
                        "SELECT EXISTS(SELECT 1 FROM "
                        "ctt_staff_private.capture_attempt_anchors WHERE "
                        "attempt_id=$1)"
                    ),
                    attempt,
                )
            await insert_attempt(admin, scope, challenge, attempt)
            assert await admin.fetchval(
                (
                    "SELECT EXISTS(SELECT 1 FROM "
                    "ctt_staff_private.legacy_v2_reservations WHERE "
                    "reserved_id=$1)"
                ),
                scope.session_id,
            )
        finally:
            await app.close()
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize("attack", ["draft", "asset"])
def test_reservation_requires_current_draft_and_exact_review_asset(boundary_pg, attack):
    async def run():
        admin, app = await connect(boundary_pg), await connect(
            boundary_pg, "devnous_user"
        )
        try:
            scope, challenge, attempt = await sample(admin)
            await source_review_rows(app, scope)
            if attack == "draft":
                from devnous.copa_telmex.models import RegistrationReviewDraft

                later = row(RegistrationReviewDraft)
                later.update(
                    session_id=str(scope.session_id),
                    draft_version=2,
                    predecessor_draft_id=str(scope.draft_id),
                    predecessor_content_hash=scope.draft_content_hash,
                    content_hash="sha256:" + "9" * 64,
                    mutation_type="review",
                    mutation_operation_id="sha256:" + "8" * 64,
                )
                await insert_model(app, RegistrationReviewDraft, later)
            else:
                # A privileged fixture corrupts legacy metadata to exercise
                # the independent reservation check, not an app write bypass.
                await admin.execute(
                    (
                        "ALTER TABLE public.copa_telmex_registration_review_ass"
                        "ets DISABLE TRIGGER USER"
                    )
                )
                await admin.execute(
                    (
                        "UPDATE public.copa_telmex_registration_review_assets "
                        "SET sha256=$1 WHERE id=$2"
                    ),
                    "f" * 64,
                    scope.source_pages[0].asset_id,
                )
                await admin.execute(
                    (
                        "ALTER TABLE public.copa_telmex_registration_review_ass"
                        "ets ENABLE TRIGGER USER"
                    )
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await insert_attempt(admin, scope, challenge, attempt)
            assert not await admin.fetchval(
                (
                    "SELECT EXISTS(SELECT 1 FROM "
                    "ctt_staff_private.capture_attempt_anchors WHERE "
                    "attempt_id=$1)"
                ),
                attempt,
            )
        finally:
            await app.close()
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "attack",
    [
        "function",
        "private_guard",
        "unreviewed_writer",
        "extra_trigger",
        "table_default",
        "disabled_trigger",
        "inheritance",
        "db_owner",
    ],
)
def test_cutover_preflight_rejects_unreviewed_source_or_privileges(boundary_pg, attack):
    async def run():
        admin = await connect(boundary_pg)
        try:
            await admin.execute(
                (
                    MIGRATIONS / "20261030_ctt_legacy_write_boundary.rollback.sql"
                ).read_text()
            )
            if attack == "function":
                await admin.execute(
                    (
                        "CREATE OR REPLACE FUNCTION "
                        "public.regs07_snapshot_hash(snapshot jsonb) RETURNS "
                        "varchar LANGUAGE sql IMMUTABLE AS $$ SELECT "
                        "'forged'::varchar $$"
                    )
                )
            elif attack == "private_guard":
                await admin.execute(
                    (
                        "ALTER TABLE ctt_staff_private.capture_payloads "
                        "DISABLE TRIGGER ctt_capture_payload_immutable"
                    )
                )
            elif attack == "extra_trigger":
                await admin.execute(
                    (
                        "CREATE FUNCTION public.ctt_untrusted_trigger() "
                        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN "
                        "NEW; END $$"
                    )
                )
                await admin.execute(
                    (
                        "CREATE TRIGGER zz_untrusted BEFORE INSERT ON "
                        "public.copa_telmex_teams FOR EACH ROW EXECUTE "
                        "FUNCTION public.ctt_untrusted_trigger()"
                    )
                )
            elif attack == "table_default":
                await admin.execute(
                    (
                        "CREATE FUNCTION public.ctt_untrusted_default() "
                        "RETURNS timestamp LANGUAGE sql AS $$ SELECT "
                        "now()::timestamp $$"
                    )
                )
                await admin.execute(
                    (
                        "ALTER TABLE public.copa_telmex_teams ALTER created_at "
                        "SET DEFAULT public.ctt_untrusted_default()"
                    )
                )
            elif attack == "disabled_trigger":
                await admin.execute(
                    (
                        "ALTER TABLE public.copa_telmex_teams DISABLE TRIGGER "
                        "trg_regs07_team_initialize"
                    )
                )
            elif attack == "inheritance":
                await admin.execute("CREATE TABLE public.ctt_untrusted_parent()")
                await admin.execute(
                    (
                        "ALTER TABLE public.copa_telmex_teams INHERIT "
                        "public.ctt_untrusted_parent"
                    )
                )
            elif attack == "db_owner":
                await admin.execute(
                    "ALTER DATABASE " + boundary_pg[1] + " OWNER TO devnous_user"
                )
            else:
                await admin.execute("CREATE ROLE ctt_unreviewed_writer NOLOGIN")
                await admin.execute(
                    "GRANT INSERT ON public.copa_telmex_teams TO ctt_unreviewed_writer"
                )
            with pytest.raises(asyncpg.RaiseError):
                await admin.execute(
                    (MIGRATIONS / "20261030_ctt_legacy_write_boundary.sql").read_text()
                )
            await admin.execute("ROLLBACK")
            assert (
                await admin.fetchval(
                    (
                        "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE "
                        "oid='public.copa_telmex_teams'::regclass"
                    )
                )
                == "devnous_user"
            )
            assert await admin.fetchval(
                "SELECT to_regprocedure('public.legacy_insert_team(jsonb)') IS NULL"
            )
            if attack == "unreviewed_writer":
                await admin.execute(
                    (
                        "REVOKE INSERT ON public.copa_telmex_teams FROM "
                        "ctt_unreviewed_writer"
                    )
                )
                await admin.execute("DROP ROLE ctt_unreviewed_writer")
        finally:
            await admin.close()

    asyncio.run(run())


def test_empty_rollback_can_be_reapplied_without_source_drift(boundary_pg):
    async def run():
        admin = await connect(boundary_pg)
        try:
            await admin.execute(
                (
                    MIGRATIONS / "20261030_ctt_legacy_write_boundary.rollback.sql"
                ).read_text()
            )
            assert not await admin.fetchval(
                (
                    "SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN "
                    "pg_namespace n ON n.oid=p.pronamespace WHERE "
                    "n.nspname='public' AND p.proname LIKE 'regs07_%' AND "
                    "(p.prosecdef OR p.proconfig IS NOT NULL))"
                )
            )
            await admin.execute(
                (MIGRATIONS / "20261030_ctt_legacy_write_boundary.sql").read_text()
            )
            assert (
                await admin.fetchval(
                    (
                        "SELECT count(*) FROM "
                        "ctt_staff_private.legacy_boundary_baseline WHERE "
                        "object_kind='table'"
                    )
                )
                == 19
            )
        finally:
            await admin.close()

    asyncio.run(run())


def test_accidental_grant_cannot_enable_unregistered_entity_writer(boundary_pg):
    async def run():
        admin = await connect(boundary_pg)
        writer = None
        try:
            await admin.execute("CREATE ROLE ctt_unregistered_insert LOGIN")
            await admin.execute(
                "GRANT INSERT, SELECT ON public.copa_telmex_teams "
                "TO ctt_unregistered_insert"
            )
            writer = await connect(boundary_pg, "ctt_unregistered_insert")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await writer.execute(
                    "INSERT INTO public.copa_telmex_teams SELECT "
                    "(jsonb_populate_record(NULL::public.copa_telmex_teams, "
                    "$1::jsonb)).*",
                    json.dumps(row(Team)),
                )
            assert (
                await admin.fetchval("SELECT count(*) FROM public.copa_telmex_teams")
                == 0
            )
        finally:
            if writer is not None:
                await writer.close()
            await admin.execute(
                "REVOKE INSERT, SELECT ON public.copa_telmex_teams "
                "FROM ctt_unregistered_insert"
            )
            await admin.execute("DROP ROLE ctt_unregistered_insert")
            await admin.close()

    asyncio.run(run())
