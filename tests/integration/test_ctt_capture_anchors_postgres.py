"""Immutable anchor tests on disposable PostgreSQL; no admission authority."""

import asyncio
import hashlib
import secrets
from pathlib import Path
from dataclasses import replace
from uuid import uuid4

import asyncpg
import pytest

from devnous.copa_telmex.staff_capture_anchor_reader import load_locked_anchor
from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_review_challenge import (
    ReviewScope,
    SourcePage,
    canonical_scope_bytes,
    scope_hash,
)
from test_ctt_staff_review_state_postgres import (
    clean,
    connect,
    synthetic_state,
)

pytest_plugins = ("test_ctt_staff_review_state_postgres",)
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261030_ctt_capture_anchors.sql"
ROLLBACK = ROOT / "database/migrations/20261030_ctt_capture_anchors.rollback.sql"


@pytest.fixture(scope="module")
def anchor_pg(state_pg):
    async def apply():
        conn = await connect(state_pg)
        try:
            await conn.execute(MIGRATION.read_text())
        finally:
            await conn.close()

    asyncio.run(apply())
    yield state_pg

    async def rollback():
        conn = await connect(state_pg)
        try:
            await conn.execute(ROLLBACK.read_text())
        finally:
            await conn.close()

    asyncio.run(rollback())


async def sample(conn):
    principal, edition, old_challenge, credential = await synthetic_state(conn)
    bare = "a" * 64
    sha = "sha256:" + "b" * 64
    mac = "hmac-sha256:" + "c" * 64
    scope = ReviewScope(
        tenant_id="synthetic",
        tournament_slug="sample",
        edition_id=edition,
        session_id=uuid4(),
        draft_id=uuid4(),
        draft_version=1,
        draft_content_hash=sha,
        team_id=uuid4(),
        manifest_sha256=bare,
        document_id="d" * 64,
        pdf_sha256="e" * 64,
        source_pages=(SourcePage(1, uuid4(), sha, 1),),
        actor_id=principal,
        actor_role="coordinador",
        authority_binding=mac,
        staff_and_changes_binding=mac,
        decision_hash=sha,
        review_receipt_id=sha,
        policy_hash=sha,
        operation_id=sha,
    )
    challenge, attempt = uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO ctt_staff_private.review_challenges "
        "SELECT $1,principal_id,credential_id,tenant_id,edition_id,faculty,"
        "principal_authority_version,faculty_authority_version,"
        "credential_authority_version,$2,$3,issued_at,expires_at FROM "
        "ctt_staff_private.review_challenges WHERE challenge_id=$4",
        challenge,
        scope_hash(scope),
        secrets.token_bytes(32),
        old_challenge,
    )
    await conn.execute(
        "INSERT INTO ctt_staff_private.manifest_anchors"
        "(manifest_sha256,tenant_id,edition_id,tournament_id,edition_year,"
        "roster_slug) VALUES ($1,$2,$3,$4,$5,$6)",
        scope.manifest_sha256,
        scope.tenant_id,
        scope.edition_id,
        uuid4(),
        2026,
        "synthetic",
    )
    await conn.execute(
        "INSERT INTO ctt_staff_private.source_anchors VALUES($1,$2,$3)",
        scope.manifest_sha256,
        scope.document_id,
        scope.pdf_sha256,
    )
    return scope, challenge, attempt


async def insert_attempt(
    conn, scope, challenge, attempt, *, blob=None, pdf=None, digest=None
):
    await conn.execute(
        "INSERT INTO ctt_staff_private.capture_attempt_anchors"
        "(attempt_id,challenge_id,manifest_sha256,document_id,pdf_sha256,"
        "tenant_id,edition_id,scope_hash,scope_bytes) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        attempt,
        challenge,
        scope.manifest_sha256,
        scope.document_id,
        pdf or scope.pdf_sha256,
        scope.tenant_id,
        scope.edition_id,
        digest or scope_hash(scope),
        blob if blob is not None else canonical_scope_bytes(scope),
    )


async def remove_synthetic(conn):
    # Fixtures use superuser exclusively to remove disposable synthetic rows.
    pairs = (
        ("capture_attempt_anchors", "ctt_capture_anchor_immutable"),
        ("source_anchors", "ctt_source_anchor_immutable"),
        ("manifest_anchors", "ctt_manifest_anchor_immutable"),
    )
    for table, trigger in pairs:
        await conn.execute(
            f"ALTER TABLE ctt_staff_private.{table} DISABLE TRIGGER {trigger}"
        )
    try:
        for table, _ in pairs:
            await conn.execute(f"DELETE FROM ctt_staff_private.{table}")
    finally:
        for table, trigger in pairs:
            await conn.execute(
                (f"ALTER TABLE ctt_staff_private.{table} " f"ENABLE TRIGGER {trigger}")
            )
    await clean(conn)


def test_exact_anchor_immutable_and_application_has_no_access(anchor_pg):
    async def run():
        admin, app = await connect(anchor_pg), await connect(anchor_pg, "devnous_user")
        try:
            scope, challenge, attempt = await sample(admin)
            await insert_attempt(admin, scope, challenge, attempt)
            for table in (
                "manifest_anchors",
                "source_anchors",
                "capture_attempt_anchors",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.execute(f"SELECT * FROM ctt_staff_private.{table}")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.execute(f"TRUNCATE ctt_staff_private.{table}")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await admin.execute(f"TRUNCATE ctt_staff_private.{table} CASCADE")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await admin.execute(
                    "DELETE FROM ctt_staff_private.capture_attempt_anchors"
                )
            with pytest.raises(asyncpg.RaiseError):
                await admin.execute(ROLLBACK.read_text())
            await admin.execute("ROLLBACK")
        finally:
            await remove_synthetic(admin)
            await app.close()
            await admin.close()

    asyncio.run(run())


def test_modified_bytes_or_document_are_rejected(anchor_pg):
    async def run():
        admin = await connect(anchor_pg)
        try:
            scope, challenge, attempt = await sample(admin)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await insert_attempt(admin, scope, challenge, attempt, blob=b"{}")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await insert_attempt(admin, scope, challenge, attempt, pdf="f" * 64)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await insert_attempt(
                    admin, replace(scope, draft_version=2), challenge, attempt
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await insert_attempt(
                    admin, replace(scope, actor_id=uuid4()), challenge, attempt
                )
            await insert_attempt(admin, scope, challenge, attempt)
            with pytest.raises(asyncpg.UniqueViolationError):
                await insert_attempt(admin, scope, challenge, uuid4())
        finally:
            await remove_synthetic(admin)
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize("attack", ["grant", "trigger"])
def test_preflight_rejects_changed_dependencies(anchor_pg, attack):
    async def run():
        conn = await connect(anchor_pg)
        try:
            await conn.execute(ROLLBACK.read_text())
            if attack == "grant":
                await conn.execute(
                    "GRANT SELECT ON ctt_staff_private.review_challenges "
                    "TO devnous_user"
                )
            else:
                await conn.execute(
                    "ALTER TABLE ctt_staff_private.review_challenges "
                    "DISABLE TRIGGER ctt_review_challenge_immutable"
                )
            with pytest.raises(asyncpg.RaiseError, match="dependencies invalid"):
                await conn.execute(MIGRATION.read_text())
            await conn.execute("ROLLBACK")
        finally:
            await conn.execute(
                "REVOKE ALL ON ctt_staff_private.review_challenges " "FROM devnous_user"
            )
            await conn.execute(
                "ALTER TABLE ctt_staff_private.review_challenges "
                "ENABLE TRIGGER ctt_review_challenge_immutable"
            )
            await conn.execute(MIGRATION.read_text())
            await conn.close()

    asyncio.run(run())


def test_application_cannot_write_or_change_anchor_structure(anchor_pg):
    async def run():
        app = await connect(anchor_pg, "devnous_user")
        try:
            statements = [
                "INSERT INTO ctt_staff_private.manifest_anchors " "DEFAULT VALUES",
                "UPDATE ctt_staff_private.manifest_anchors " "SET tenant_id='fake'",
                "DELETE FROM ctt_staff_private.manifest_anchors",
                "COPY ctt_staff_private.manifest_anchors TO STDOUT",
                "ALTER TABLE ctt_staff_private.manifest_anchors " "ADD COLUMN x int",
                "DROP TABLE ctt_staff_private.manifest_anchors",
                "SET ROLE samchat_ctt_staff_owner",
                "CREATE OR REPLACE FUNCTION "
                "ctt_staff_private.validate_capture_anchor() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
            ]
            for statement in statements:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.execute(statement)
        finally:
            await app.close()

    asyncio.run(run())


def test_duplicate_json_keys_denied_even_with_matching_challenge(anchor_pg):
    async def run():
        conn = await connect(anchor_pg)
        try:
            scope, original, attempt = await sample(conn)
            blob = canonical_scope_bytes(scope).replace(
                b'"tenant_id":"synthetic"',
                b'"tenant_id":"synthetic","tenant_id":"synthetic"',
            )
            digest = "sha256:" + hashlib.sha256(blob).hexdigest()
            challenge = uuid4()
            await conn.execute(
                "INSERT INTO ctt_staff_private.review_challenges "
                "SELECT $1,principal_id,credential_id,tenant_id,edition_id,"
                "faculty,principal_authority_version,"
                "faculty_authority_version,"
                "credential_authority_version,$2,$3,issued_at,expires_at "
                "FROM "
                "ctt_staff_private.review_challenges WHERE challenge_id=$4",
                challenge,
                digest,
                secrets.token_bytes(32),
                original,
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await insert_attempt(
                    conn, scope, challenge, attempt, blob=blob, digest=digest
                )
        finally:
            await remove_synthetic(conn)
            await conn.close()

    asyncio.run(run())


def test_reader_requires_transaction_and_loads_exact_binding(anchor_pg):
    async def run():
        conn = await connect(anchor_pg)
        try:
            scope, challenge, attempt = await sample(conn)
            await insert_attempt(conn, scope, challenge, attempt)
            with pytest.raises(CaptureDenied):
                await load_locked_anchor(conn, attempt)
            async with conn.transaction():
                loaded = await load_locked_anchor(conn, attempt)
                assert loaded.attempt_id == attempt
                assert loaded.challenge_id == challenge
                assert loaded.principal_id == scope.actor_id
                assert loaded.scope_hash == scope_hash(scope)
                with pytest.raises(CaptureDenied):
                    await load_locked_anchor(conn, uuid4())
        finally:
            await remove_synthetic(conn)
            await conn.close()

    asyncio.run(run())


def test_limited_test_writer_reads_only_via_function(anchor_pg):
    async def run():
        admin = await connect(anchor_pg)
        writer = None
        try:
            scope, challenge, attempt = await sample(admin)
            await insert_attempt(admin, scope, challenge, attempt)
            await admin.execute("CREATE ROLE ctt_anchor_test_writer LOGIN")
            await admin.execute(
                "GRANT USAGE ON SCHEMA ctt_staff_private " "TO ctt_anchor_test_writer"
            )
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION "
                "ctt_staff_private.lock_capture_anchor(uuid) "
                "TO ctt_anchor_test_writer"
            )
            writer = await connect(anchor_pg, "ctt_anchor_test_writer")
            async with writer.transaction():
                loaded = await load_locked_anchor(writer, attempt)
                assert loaded.challenge_id == challenge
                assert loaded.scope_hash == scope_hash(scope)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await writer.execute(
                    "SELECT * FROM ctt_staff_private.capture_attempt_anchors"
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await writer.execute(
                    "UPDATE ctt_staff_private.capture_attempt_anchors "
                    "SET scope_hash='fake'"
                )
        finally:
            if writer is not None:
                await writer.close()
            await remove_synthetic(admin)
            await admin.execute("DROP OWNED BY ctt_anchor_test_writer")
            await admin.execute("DROP ROLE ctt_anchor_test_writer")
            await admin.close()

    asyncio.run(run())


def test_concurrent_real_anchor_and_consumption_are_serialized(anchor_pg):
    from contextlib import asynccontextmanager

    from devnous.copa_telmex.staff_capture_executor import (
        StaffCaptureExecutor,
        VerifiedDecision,
    )
    from test_ctt_staff_capture_executor_postgres import Transaction, Verifier

    class AnchorTransaction(Transaction):
        async def lock_attempt(self, attempt_id):
            return await load_locked_anchor(self.conn, attempt_id)

    class AnchorStore:
        @asynccontextmanager
        async def transaction(self):
            conn = await connect(anchor_pg, "ctt_anchor_test_writer")
            try:
                async with conn.transaction():
                    yield AnchorTransaction(conn, None, None)
            finally:
                await conn.close()

    async def run():
        admin = await connect(anchor_pg)
        try:
            scope, challenge, attempt = await sample(admin)
            await insert_attempt(admin, scope, challenge, attempt)
            credential = await admin.fetchval(
                "SELECT credential_id FROM ctt_staff_private."
                "review_challenges WHERE challenge_id=$1",
                challenge,
            )
            await admin.execute("CREATE ROLE ctt_anchor_test_writer LOGIN")
            await admin.execute(
                "GRANT USAGE ON SCHEMA ctt_staff_private " "TO ctt_anchor_test_writer"
            )
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION "
                "ctt_staff_private.lock_capture_anchor(uuid), "
                "ctt_staff_private.consume_review_challenge("
                "uuid,uuid,bytea,text,bigint,bigint) "
                "TO ctt_anchor_test_writer"
            )
            await admin.execute(
                "CREATE TABLE ctt_test_effect (attempt_id uuid PRIMARY KEY, "
                "result_id uuid NOT NULL)"
            )
            await admin.execute(
                "GRANT SELECT, INSERT ON ctt_test_effect " "TO ctt_anchor_test_writer"
            )
            decision = VerifiedDecision(
                attempt,
                challenge,
                scope.actor_id,
                credential,
                scope_hash(scope),
                0,
                1,
            )
            executor = StaffCaptureExecutor(AnchorStore(), Verifier(decision))
            first, second = await asyncio.gather(
                executor.execute(attempt, b"synthetic"),
                executor.execute(attempt, b"synthetic"),
            )
            assert first == second
            assert await admin.fetchval("SELECT count(*) FROM ctt_test_effect") == 1
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private."
                    "review_challenge_consumptions"
                )
                == 1
            )
        finally:
            await admin.execute("DROP TABLE IF EXISTS ctt_test_effect")
            await remove_synthetic(admin)
            await admin.execute("DROP OWNED BY ctt_anchor_test_writer")
            await admin.execute("DROP ROLE ctt_anchor_test_writer")
            await admin.close()

    asyncio.run(run())


def test_attempt_lock_excludes_concurrent_transaction(anchor_pg):
    async def run():
        admin = await connect(anchor_pg)
        first = await connect(anchor_pg)
        second = await connect(anchor_pg)
        first_tx = first.transaction()
        try:
            scope, challenge, attempt = await sample(admin)
            await insert_attempt(admin, scope, challenge, attempt)
            await first_tx.start()
            await load_locked_anchor(first, attempt)
            async with second.transaction():
                await second.execute("SET LOCAL lock_timeout='150ms'")
                with pytest.raises(asyncpg.LockNotAvailableError):
                    await load_locked_anchor(second, attempt)
            await first_tx.rollback()
            async with second.transaction():
                assert (await load_locked_anchor(second, attempt)).attempt_id == attempt
        finally:
            if first.is_in_transaction():
                await first_tx.rollback()
            await first.close()
            await second.close()
            await remove_synthetic(admin)
            await admin.close()

    asyncio.run(run())


def test_protected_binding_through_reader_and_manifest_precheck(anchor_pg):
    import importlib.util
    from dataclasses import replace
    from io import BytesIO

    import fitz

    from devnous.copa_telmex.batch_admission import validate_manifest
    from devnous.copa_telmex.staff_capture_source_check import (
        check_manifest_source,
        check_pdf_page_images,
        check_stored_page_assets,
    )
    from devnous.copa_telmex.staff_review_challenge import (
        parse_canonical_scope,
    )

    spec = importlib.util.spec_from_file_location(
        "ctt_synthetic_source",
        ROOT / "tests/unit/test_ctt_staff_capture_source_check.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def run():
        conn = await connect(anchor_pg)
        try:
            principal, edition, original, _ = await synthetic_state(conn)
            manifest, scope, _, binding = module.sample()
            doc = fitz.open()
            doc.new_page()
            doc.new_page()
            pdf_bytes = doc.tobytes()
            page_images = [
                doc.load_page(index).get_pixmap(alpha=False).tobytes("png")
                for index in range(2)
            ]
            page_hashes = [
                "sha256:" + hashlib.sha256(data).hexdigest() for data in page_images
            ]
            doc.close()
            scope = replace(
                scope,
                source_pages=tuple(
                    replace(page, image_sha256=page_hashes[index])
                    for index, page in enumerate(scope.source_pages)
                ),
            )
            pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
            manifest["documents"][0]["pdf_sha256"] = pdf_sha
            scope = replace(
                scope,
                actor_id=principal,
                edition_id=edition,
                pdf_sha256=pdf_sha,
                manifest_sha256=validate_manifest(manifest)["manifest_sha256"],
            )
            binding = replace(binding, edition_id=edition)
            challenge, attempt = uuid4(), uuid4()
            await conn.execute(
                "INSERT INTO ctt_staff_private.review_challenges "
                "SELECT $1,principal_id,credential_id,tenant_id,edition_id,"
                "faculty,principal_authority_version,"
                "faculty_authority_version,"
                "credential_authority_version,$2,$3,issued_at,expires_at "
                "FROM ctt_staff_private.review_challenges "
                "WHERE challenge_id=$4",
                challenge,
                scope_hash(scope),
                secrets.token_bytes(32),
                original,
            )
            await conn.execute(
                "INSERT INTO ctt_staff_private.manifest_anchors "
                "(manifest_sha256,tenant_id,edition_id,tournament_id,"
                "edition_year,roster_slug) VALUES($1,$2,$3,$4,$5,$6)",
                scope.manifest_sha256,
                binding.tenant_id,
                binding.edition_id,
                binding.tournament_id,
                binding.edition_year,
                binding.roster_slug,
            )
            await conn.execute(
                "INSERT INTO ctt_staff_private.source_anchors " "VALUES($1,$2,$3)",
                scope.manifest_sha256,
                scope.document_id,
                scope.pdf_sha256,
            )
            await insert_attempt(conn, scope, challenge, attempt)
            async with conn.transaction():
                locked = await load_locked_anchor(conn, attempt)
                assert locked.edition_binding == binding
                check_manifest_source(
                    manifest,
                    parse_canonical_scope(locked.scope_bytes),
                    BytesIO(pdf_bytes),
                    locked.edition_binding,
                    max_source_bytes=1_000_000,
                )
                check_pdf_page_images(
                    BytesIO(pdf_bytes),
                    parse_canonical_scope(locked.scope_bytes),
                    max_source_bytes=1_000_000,
                )
                check_stored_page_assets(
                    parse_canonical_scope(locked.scope_bytes),
                    {
                        page.asset_id: BytesIO(page_images[index])
                        for index, page in enumerate(scope.source_pages)
                    },
                    max_asset_bytes=1_000_000,
                )
                with pytest.raises(CaptureDenied):
                    check_manifest_source(
                        manifest,
                        parse_canonical_scope(locked.scope_bytes),
                        BytesIO(pdf_bytes + b"changed"),
                        locked.edition_binding,
                        max_source_bytes=1_000_000,
                    )
        finally:
            await remove_synthetic(conn)
            await conn.close()

    asyncio.run(run())
