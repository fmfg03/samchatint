"""PostgreSQL contracts for private resumable Copa Telmex batch staging."""

import os
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_staged_batch_upload_migration_constraints_grants_and_rollback():
    socket = os.environ.get("SAMCHAT_TEST_PG_SOCKET")
    if not socket:
        pytest.skip("Explicit ephemeral PostgreSQL socket required")
    assert socket.startswith("/tmp/samchat-ctt-pg-"), "Only isolated temporary sockets"

    import asyncpg

    port = int(os.environ.get("SAMCHAT_TEST_PG_PORT", "55439"))
    schema = "test_staged_batch_upload_" + uuid4().hex
    root = Path(__file__).resolve().parents[2]
    migration = (
        root
        / "database/migrations/20261007_copa_telmex_staged_batch_uploads.sql"
    ).read_text()
    rollback = (
        root
        / "database/migrations/20261007_copa_telmex_staged_batch_uploads.rollback.sql"
    ).read_text()
    admin = await asyncpg.connect(
        host=socket, port=port, user="postgres", database="postgres"
    )
    try:
        await admin.execute("CREATE ROLE devnous_user NOLOGIN")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}"')
        await admin.execute(
            "CREATE TABLE copa_telmex_registration_batches(id UUID PRIMARY KEY)"
        )
        await admin.execute(migration)
        await admin.execute(migration)
        assert await admin.fetchval(
            "SELECT has_table_privilege('devnous_user', "
            "'copa_telmex_registration_batch_uploads', 'UPDATE')"
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('devnous_user', "
            "'copa_telmex_registration_batch_upload_files', 'DELETE')"
        )

        upload_id, edition_id = uuid4(), uuid4()
        await admin.execute(
            """INSERT INTO copa_telmex_registration_batch_uploads
               (id, tournament_edition_id, manifest_sha256, document_count,
                file_count, status, created_by_user_id, expires_at)
               VALUES ($1, $2, $3, 11, 7, 'staging', 'operator-1',
                       NOW() + INTERVAL '1 day')""",
            upload_id,
            edition_id,
            "a" * 64,
        )
        await admin.execute(
            """INSERT INTO copa_telmex_registration_batch_upload_files
               (id, upload_id, source_filename, expected_sha256, byte_count, status)
               VALUES ($1, $2, 'source.pdf', $3, 1024, 'uploaded')""",
            uuid4(),
            upload_id,
            "b" * 64,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await admin.execute(
                """INSERT INTO copa_telmex_registration_batch_upload_files
                   (id, upload_id, source_filename, expected_sha256, status)
                   VALUES ($1, $2, 'source.pdf', $3, 'pending')""",
                uuid4(),
                upload_id,
                "c" * 64,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await admin.execute(
                """INSERT INTO copa_telmex_registration_batch_upload_files
                   (id, upload_id, source_filename, expected_sha256, byte_count, status)
                   VALUES ($1, $2, 'oversize.pdf', $3, 67108865, 'uploaded')""",
                uuid4(),
                upload_id,
                "d" * 64,
            )
        with pytest.raises(asyncpg.RaiseError, match="owner cleanup review required"):
            await admin.execute(rollback)
        await admin.execute("ROLLBACK")
        assert await admin.fetchval(
            "SELECT to_regclass('copa_telmex_registration_batch_uploads') IS NOT NULL"
        )

        await admin.execute("DELETE FROM copa_telmex_registration_batch_upload_files")
        await admin.execute("DELETE FROM copa_telmex_registration_batch_uploads")
        await admin.execute(rollback)
        assert not await admin.fetchval(
            "SELECT to_regclass('copa_telmex_registration_batch_uploads') IS NOT NULL"
        )
    finally:
        await admin.execute("SET search_path TO public")
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.execute("DROP ROLE IF EXISTS devnous_user")
        await admin.close()
