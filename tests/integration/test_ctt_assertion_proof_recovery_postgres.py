"""Exact proof retention in issuer transaction on disposable PostgreSQL."""

import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from test_ctt_full_runtime_postgres import full_pg as _full_pg
from test_ctt_staff_security_postgres import PORT
from test_ctt_assertion_issuer_postgres import record
from test_ctt_capture_anchors_postgres import sample, insert_attempt
from test_ctt_full_runtime_postgres import public_review_source
from devnous.copa_telmex.staff_review_challenge import scope_hash

pytest_plugins = ("test_ctt_staff_security_postgres",)
full_pg = _full_pg
ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "database/migrations"
RECORD_SIGNATURE = (
    "record_review_assertion_issuance(uuid,text,bigint,bigint,text,"
    "boolean,boolean,text,bigint)"
)
RAW = b'{"synthetic":"exact-signed-envelope-bytes"}'
PROOF_HASH = "sha256:" + hashlib.sha256(RAW).hexdigest()


async def admin_connection(full_pg):
    return await asyncpg.connect(
        host=full_pg[0], port=int(PORT), user="postgres", database="ctt_full_runtime"
    )


async def state(admin):
    """Admitted synthetic source, with every real anchor guard enabled."""
    scope, challenge, attempt = await sample(admin)
    scope = await public_review_source(
        admin, scope, {"documents": [{"extraction": {}}]}, Path("/tmp"), range(1, 17)
    )
    original_challenge, challenge = challenge, uuid4()
    await admin.execute(
        "INSERT INTO ctt_staff_private.review_challenges "
        "SELECT $1,principal_id,credential_id,tenant_id,edition_id,faculty,"
        "principal_authority_version,faculty_authority_version,"
        "credential_authority_version,$2,$4,issued_at,expires_at "
        "FROM ctt_staff_private.review_challenges WHERE challenge_id=$3",
        challenge,
        scope_hash(scope),
        original_challenge,
        uuid4().bytes + uuid4().bytes,
    )
    await admin.execute(
        "INSERT INTO ctt_staff_private.source_admissions "
        "VALUES($1,$2,$3,$4,$5,$6,1,2,clock_timestamp(),session_user)",
        uuid4(),
        scope.manifest_sha256,
        "sha256:" + "f" * 64,
        b"{}",
        "sha256:" + "9" * 64,
        b"{}",
    )
    await admin.execute(
        "INSERT INTO ctt_staff_private.source_admission_documents "
        "VALUES($1,$2,$3,$4,$5)",
        scope.manifest_sha256,
        scope.document_id,
        scope.pdf_sha256,
        "1" * 64,
        "2" * 64,
    )
    for page in scope.source_pages:
        await admin.execute(
            "INSERT INTO ctt_staff_private.source_admission_pages "
            "VALUES($1,$2,$3,$4,$5)",
            scope.manifest_sha256,
            scope.document_id,
            page.page_index,
            page.pdf_page,
            page.image_sha256,
        )
    await insert_attempt(admin, scope, challenge, attempt)
    context = await admin.fetchrow(
        "SELECT * FROM ctt_staff_private.load_review_assertion_context($1)", attempt
    )
    return scope, challenge, attempt, context


async def before_recovery(admin):
    for signature in ("load_review_assertion_context(uuid)", RECORD_SIGNATURE):
        await admin.execute(
            f"REVOKE EXECUTE ON FUNCTION ctt_staff_private.{signature} "
            "FROM ctt_full_issuer"
        )


async def setup(admin):
    role = "ctt_recovery_" + uuid4().hex
    await admin.execute(
        (MIGRATIONS / "20261031_ctt_passkey_enrollment.sql").read_text()
    )
    await before_recovery(admin)
    await admin.execute(
        (MIGRATIONS / "20261031_ctt_assertion_proof_recovery.sql").read_text()
    )
    await admin.execute(f"CREATE ROLE {role} LOGIN")
    await admin.execute(f"GRANT USAGE ON SCHEMA ctt_staff_private TO {role}")
    for signature in [
        "load_review_assertion_context(uuid)",
        RECORD_SIGNATURE,
        "record_assertion_proof(uuid,bytea)",
    ]:
        await admin.execute(
            f"GRANT EXECUTE ON FUNCTION ctt_staff_private.{signature} TO {role}"
        )

    return role


@pytest.mark.parametrize(
    "case",
    [
        "atomic",
        "wronghash",
        "late",
        "rollback",
        "oversize",
        "missing",
        "nogrant",
        "broad",
    ],
)
def test_recovery_exact_atomic_and_fail_closed(full_pg, case):
    async def run():
        admin = await admin_connection(full_pg)
        issuer = None
        try:
            role = await setup(admin)
            _, _, attempt, context = await state(admin)
            issuer = await asyncpg.connect(
                host=full_pg[0], port=int(PORT), user=role, database="ctt_full_runtime"
            )
            if case == "nogrant":
                await admin.execute(
                    "REVOKE EXECUTE ON FUNCTION ctt_staff_private."
                    f"load_review_assertion_context(uuid) FROM {role}"
                )
            if case == "broad":
                await admin.execute(f"GRANT pg_read_all_data TO {role}")
            if case == "late":
                async with issuer.transaction():
                    await record(issuer, attempt, context, proof=PROOF_HASH)
            transaction = issuer.transaction()
            await transaction.start()
            try:
                if case not in {"late", "missing"}:
                    # Record audit on the isolated issuer connection, same TX.
                    # nogrant/broad cases exercise the new function's own guard,
                    # so insert audited bytes via disposable administrator instead.
                    if case in {"nogrant", "broad"}:
                        await record(admin, attempt, context, proof=PROOF_HASH)
                    else:
                        await record(issuer, attempt, context, proof=PROOF_HASH)
                raw = b"wrong" if case == "wronghash" else RAW
                if case == "oversize":
                    raw = b"x" * 8193
                if case in {
                    "wronghash",
                    "late",
                    "oversize",
                    "missing",
                    "nogrant",
                    "broad",
                }:
                    with pytest.raises(asyncpg.PostgresError):
                        await issuer.fetchval(
                            "SELECT ctt_staff_private." "record_assertion_proof($1,$2)",
                            attempt,
                            raw,
                        )
                    await transaction.rollback()
                else:
                    assert (
                        await issuer.fetchval(
                            "SELECT ctt_staff_private." "record_assertion_proof($1,$2)",
                            attempt,
                            raw,
                        )
                        is True
                    )
                    if case == "rollback":
                        await transaction.rollback()
                    else:
                        await transaction.commit()
                        # Read-only exact retry after the original transaction.
                        assert (
                            await issuer.fetchval(
                                "SELECT ctt_staff_private."
                                "record_assertion_proof($1,$2)",
                                attempt,
                                raw,
                            )
                            is True
                        )
                actual = await admin.fetchval(
                    "SELECT signed_review FROM ctt_staff_private.issuer_proofs "
                    "WHERE attempt_id=$1",
                    attempt,
                )
                assert actual == (RAW if case == "atomic" else None)
                if case == "atomic":
                    with pytest.raises(
                        asyncpg.PostgresError, match="refuses used evidence"
                    ):
                        await admin.execute(
                            (
                                MIGRATIONS
                                / "20261031_ctt_assertion_proof_recovery.rollback.sql"
                            ).read_text()
                        )
                    await admin.execute("ROLLBACK")
                    with pytest.raises(asyncpg.PostgresError):
                        await admin.execute(
                            "UPDATE ctt_staff_private.issuer_proofs "
                            "SET signed_review=$1",
                            b"other",
                        )
            except BaseException:
                if issuer.is_in_transaction():
                    await transaction.rollback()
                raise
        finally:
            if issuer:
                await issuer.close()
            await admin.close()

    asyncio.run(run())


def test_recovery_empty_rollback(full_pg):
    async def run():
        admin = await admin_connection(full_pg)
        try:
            await setup(admin)
            assert not await admin.fetchval(
                "SELECT has_table_privilege('devnous_user',"
                "'ctt_staff_private.issuer_proofs','SELECT')"
            )
            assert not await admin.fetchval(
                "SELECT has_function_privilege('devnous_user',"
                "'ctt_staff_private.record_assertion_proof(uuid,bytea)','EXECUTE')"
            )
            await admin.execute(
                (
                    MIGRATIONS / "20261031_ctt_assertion_proof_recovery.rollback.sql"
                ).read_text()
            )
            assert (
                await admin.fetchval(
                    "SELECT to_regclass('ctt_staff_private.issuer_proofs')"
                )
                is None
            )
        finally:
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["TABLES", "FUNCTIONS"])
def test_recovery_default_acl_fail_closed(full_pg, kind):
    async def run():
        admin = await admin_connection(full_pg)
        try:
            await admin.execute(
                (MIGRATIONS / "20261031_ctt_passkey_enrollment.sql").read_text()
            )
            await before_recovery(admin)
            unknown = "ctt_recovery_unknown_" + uuid4().hex
            await admin.execute(f"CREATE ROLE {unknown}")
            privilege = "SELECT" if kind == "TABLES" else "EXECUTE"
            await admin.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA ctt_staff_private "
                f"GRANT {privilege} ON {kind} TO {unknown}"
            )
            with pytest.raises(asyncpg.PostgresError, match="installed ACL invalid"):
                await admin.execute(
                    (
                        MIGRATIONS / "20261031_ctt_assertion_proof_recovery.sql"
                    ).read_text()
                )
            await admin.execute("ROLLBACK")
            assert (
                await admin.fetchval(
                    "SELECT to_regclass('ctt_staff_private.issuer_proofs')"
                )
                is None
            )
        finally:
            await admin.close()

    asyncio.run(run())
