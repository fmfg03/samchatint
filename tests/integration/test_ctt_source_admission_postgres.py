"""Protected source membership on disposable PostgreSQL, no real pilot data."""

import asyncio
import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import asyncpg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.batch_admission import sha256_value
from test_ctt_capture_anchors_postgres import (
    anchor_pg as _anchor_fixture,
    insert_attempt,
    remove_synthetic,
    sample,
)
from test_ctt_staff_review_state_postgres import connect

pytest_plugins = ("test_ctt_staff_review_state_postgres",)
anchor_pg = _anchor_fixture
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261030_ctt_source_admission.sql"
ROLLBACK = ROOT / "database/migrations/20261030_ctt_source_admission.rollback.sql"
TEST_KEY = Ed25519PrivateKey.generate()


@pytest.fixture(scope="module")
def source_pg(anchor_pg):
    async def setup():
        conn = await connect(anchor_pg)
        try:
            await conn.execute(MIGRATION.read_text())
            await conn.execute("CREATE ROLE ctt_source_test LOGIN")
            await conn.execute(
                "GRANT USAGE ON SCHEMA ctt_staff_private TO ctt_source_test"
            )
            await conn.execute(
                "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                "register_source_admission(uuid,bytea,text,bigint,bigint,bytea) "
                "TO ctt_source_test"
            )
        finally:
            await conn.close()

    asyncio.run(setup())
    yield anchor_pg

    async def teardown():
        conn = await connect(anchor_pg)
        try:
            await clear(conn)
            await conn.execute(ROLLBACK.read_text())
            await conn.execute("DROP OWNED BY ctt_source_test")
            await conn.execute("DROP ROLE ctt_source_test")
        finally:
            await conn.close()

    asyncio.run(teardown())


async def clear(conn):
    for table in ("capture_attempt_anchors",):
        await conn.execute(
            f"ALTER TABLE ctt_staff_private.{table} "
            "DISABLE TRIGGER ctt_capture_anchor_immutable"
        )
        await conn.execute(f"DELETE FROM ctt_staff_private.{table}")
        await conn.execute(
            f"ALTER TABLE ctt_staff_private.{table} "
            "ENABLE TRIGGER ctt_capture_anchor_immutable"
        )
    for table in (
        "source_admission_pages",
        "source_admission_documents",
        "source_admissions",
    ):
        await conn.execute(
            f"ALTER TABLE ctt_staff_private.{table} "
            "DISABLE TRIGGER ctt_source_admission_immutable"
        )
        await conn.execute(f"DELETE FROM ctt_staff_private.{table}")
        await conn.execute(
            f"ALTER TABLE ctt_staff_private.{table} "
            "ENABLE TRIGGER ctt_source_admission_immutable"
        )
    await remove_synthetic(conn)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


async def candidate(conn):
    scope, challenge, attempt = await sample(conn)
    edition = await conn.fetchrow(
        "SELECT * FROM ctt_staff_private.manifest_anchors WHERE manifest_sha256=$1",
        scope.manifest_sha256,
    )
    plan = {
        "schema": "ctt.source-admission.plan.v1",
        "tenant_id": scope.tenant_id,
        "edition_id": str(scope.edition_id),
        "tournament_id": str(edition["tournament_id"]),
        "edition_year": edition["edition_year"],
        "roster_slug": edition["roster_slug"],
        "manifest_sha256": scope.manifest_sha256,
        "documents": [
            {
                "document_id": scope.document_id,
                "pdf_sha256": scope.pdf_sha256,
                "payload_sha256": "f" * 64,
                "source_pages_sha256": sha256_value(
                    [page.pdf_page for page in scope.source_pages]
                ),
                "pages": [
                    {
                        "page_index": page.page_index,
                        "pdf_page": page.pdf_page,
                        "image_sha256": page.image_sha256,
                    }
                    for page in scope.source_pages
                ],
            }
        ],
        "pdfs": [{"pdf_sha256": scope.pdf_sha256, "byte_length": 123}],
    }
    return scope, challenge, attempt, plan


async def register(
    conn, plan, *, admission=None, proof=None, issued=None, expires=None
):
    now = int(time.time()) if issued is None else issued
    admission = admission or uuid4()
    expires = now + 180 if expires is None else expires
    plan_bytes = canonical(plan)
    body = {
        "schema": "ctt.source-admission.authorization.v1",
        "kid": "test",
        "admission_id": str(admission),
        "plan_sha256": "sha256:" + hashlib.sha256(plan_bytes).hexdigest(),
        "issued_at": now,
        "expires_at": expires,
    }
    envelope = canonical(
        {
            "body": body,
            "signature": base64.b64encode(
                TEST_KEY.sign(b"samchat.ctt.source-admission.v1\x00" + canonical(body))
            ).decode("ascii"),
        }
    )
    return await conn.fetchval(
        "SELECT ctt_staff_private.register_source_admission($1,$2,$3,$4,$5,$6)",
        admission,
        plan_bytes,
        proof or "sha256:" + hashlib.sha256(envelope).hexdigest(),
        now,
        expires,
        envelope,
    )


def test_exact_admission_membership_retry_and_application_denied(source_pg):
    async def run():
        admin = await connect(source_pg)
        service = await connect(source_pg, "ctt_source_test")
        app = await connect(source_pg, "devnous_user")
        try:
            scope, challenge, attempt, plan = await candidate(admin)
            admission, issued = uuid4(), int(time.time())
            assert await register(service, plan, admission=admission, issued=issued)
            assert await register(service, plan, admission=admission, issued=issued)
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.source_admissions"
                )
                == 1
            )
            await insert_attempt(admin, scope, challenge, attempt)
            for connection in (app, service):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.fetchval(
                        "SELECT count(*) FROM ctt_staff_private.source_admissions"
                    )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await register(app, plan)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await admin.execute(
                    "TRUNCATE ctt_staff_private.source_admissions CASCADE"
                )
            with pytest.raises(asyncpg.RaiseError):
                await admin.execute(ROLLBACK.read_text())
            await admin.execute("ROLLBACK")
        finally:
            await clear(admin)
            await app.close()
            await service.close()
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "attack", ["none", "image", "pdf_page", "page_index", "extra_page", "asset_null"]
)
def test_capture_requires_exact_admitted_page_mapping(source_pg, attack):
    async def run():
        admin = await connect(source_pg)
        try:
            scope, challenge, attempt, plan = await candidate(admin)
            if attack != "none":
                await register(admin, plan)
            blob = None
            if attack != "none":
                from devnous.copa_telmex.staff_review_challenge import (
                    canonical_scope_bytes,
                )

                value = json.loads(canonical_scope_bytes(scope))
                page = value["source_pages"][0]
                if attack == "image":
                    page["image_sha256"] = "sha256:" + "f" * 64
                elif attack == "pdf_page":
                    page["pdf_page"] = 2
                elif attack == "page_index":
                    page["page_index"] = 2
                elif attack == "asset_null":
                    page["asset_id"] = None
                elif attack == "extra_page":
                    value["source_pages"].append(dict(page, page_index=2, pdf_page=2))
                blob = canonical(value)
                digest = "sha256:" + hashlib.sha256(blob).hexdigest()
                await admin.execute(
                    "ALTER TABLE ctt_staff_private.review_challenges "
                    "DISABLE TRIGGER ctt_review_challenge_immutable"
                )
                await admin.execute(
                    "UPDATE ctt_staff_private.review_challenges "
                    "SET scope_hash=$1 WHERE challenge_id=$2",
                    digest,
                    challenge,
                )
                await admin.execute(
                    "ALTER TABLE ctt_staff_private.review_challenges "
                    "ENABLE TRIGGER ctt_review_challenge_immutable"
                )
            else:
                digest = None
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await insert_attempt(
                    admin, scope, challenge, attempt, blob=blob, digest=digest
                )
        finally:
            await clear(admin)
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "attack",
    [
        "extra_doc",
        "extra_pdf",
        "pages_hash",
        "duplicate_page",
        "wrong_edition",
        "expired",
        "future",
        "ttl",
        "proof",
        "id_conflict",
        "manifest_type",
        "year_bool",
        "extra_existing_source",
    ],
)
def test_malformed_conflicting_or_stale_source_plan_is_rejected(source_pg, attack):
    async def run():
        admin = await connect(source_pg)
        try:
            scope, challenge, attempt, plan = await candidate(admin)
            options = {}
            if attack == "extra_doc":
                plan["documents"].append(dict(plan["documents"][0]))
            elif attack == "extra_pdf":
                plan["pdfs"].append({"pdf_sha256": "f" * 64, "byte_length": 12})
            elif attack == "pages_hash":
                plan["documents"][0]["source_pages_sha256"] = "f" * 64
            elif attack == "duplicate_page":
                plan["documents"][0]["pages"].append(
                    dict(plan["documents"][0]["pages"][0])
                )
            elif attack == "wrong_edition":
                plan["edition_id"] = str(uuid4())
            elif attack == "expired":
                options = {
                    "issued": int(time.time()) - 20,
                    "expires": int(time.time()) - 1,
                }
            elif attack == "future":
                options = {"issued": int(time.time()) + 20}
            elif attack == "ttl":
                options = {"expires": int(time.time()) + 1000}
            elif attack == "proof":
                options = {"proof": "false"}
            elif attack == "id_conflict":
                admission, issued = uuid4(), int(time.time())
                await register(admin, plan, admission=admission, issued=issued)
                options = {
                    "admission": admission,
                    "issued": issued,
                    "proof": "sha256:" + "d" * 64,
                }
            elif attack == "manifest_type":
                plan["manifest_sha256"] = int("1" * 64)
            elif attack == "year_bool":
                plan["edition_year"] = True
            elif attack == "extra_existing_source":
                await admin.execute(
                    "INSERT INTO ctt_staff_private.source_anchors " "VALUES($1,$2,$3)",
                    scope.manifest_sha256,
                    "f" * 64,
                    scope.pdf_sha256,
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await register(admin, plan, **options)
        finally:
            await clear(admin)
            await admin.close()

    asyncio.run(run())


def test_source_audit_and_membership_are_immutable(source_pg):
    async def run():
        admin = await connect(source_pg)
        try:
            _, _, _, plan = await candidate(admin)
            await register(admin, plan)
            for table in (
                "source_admissions",
                "source_admission_documents",
                "source_admission_pages",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await admin.execute(f"DELETE FROM ctt_staff_private.{table}")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await admin.execute(f"TRUNCATE ctt_staff_private.{table} CASCADE")
        finally:
            await clear(admin)
            await admin.close()

    asyncio.run(run())


def test_concurrent_retry_keeps_one_verifiable_signed_audit(source_pg):
    async def run():
        from devnous.copa_telmex.staff_source_admission import verify_source_admission

        admin = await connect(source_pg)
        one = await connect(source_pg, "ctt_source_test")
        two = await connect(source_pg, "ctt_source_test")
        try:
            _, _, _, plan = await candidate(admin)
            admission, issued = uuid4(), int(time.time())
            assert await asyncio.gather(
                register(one, plan, admission=admission, issued=issued),
                register(two, plan, admission=admission, issued=issued),
            ) == [True, True]
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.source_admissions"
                )
                == 1
            )
            row = await admin.fetchrow(
                "SELECT * FROM ctt_staff_private.source_admissions"
            )
            verified = verify_source_admission(
                row["plan_bytes"],
                row["authorization_bytes"],
                public_keys={"test": TEST_KEY.public_key().public_bytes_raw()},
                now_seconds=issued,
            )
            assert verified["proof_sha256"] == row["proof_sha256"]
        finally:
            await clear(admin)
            await one.close()
            await two.close()
            await admin.close()

    asyncio.run(run())


def test_application_login_cannot_admit_even_after_inheriting_service_role(source_pg):
    async def run():
        admin = await connect(source_pg)
        app = await connect(source_pg, "devnous_user")
        try:
            _, _, _, plan = await candidate(admin)
            await admin.execute("GRANT ctt_source_test TO devnous_user")
            for switched in (False, True):
                if switched:
                    await app.execute("SET ROLE ctt_source_test")
                with pytest.raises(
                    asyncpg.InsufficientPrivilegeError,
                    match="CTT source admission denied",
                ):
                    await register(app, plan)
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.source_admissions"
                )
                == 0
            )
        finally:
            await app.execute("RESET ROLE")
            await admin.execute("REVOKE ctt_source_test FROM devnous_user")
            await clear(admin)
            await app.close()
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize("attack", ["disabled_guard", "existing_attempt"])
def test_source_migration_refuses_unprotected_or_prior_attempt_state(source_pg, attack):
    async def run():
        admin = await connect(source_pg)
        try:
            await clear(admin)
            await admin.execute(ROLLBACK.read_text())
            if attack == "disabled_guard":
                await admin.execute(
                    "ALTER TABLE ctt_staff_private.source_anchors "
                    "DISABLE TRIGGER ctt_source_anchor_immutable"
                )
                expected = "CTT source protected guard invalid"
            else:
                scope, challenge, attempt = await sample(admin)
                await insert_attempt(admin, scope, challenge, attempt)
                expected = "CTT source admission requires empty attempt state"
            with pytest.raises(asyncpg.RaiseError, match=expected):
                await admin.execute(MIGRATION.read_text())
            await admin.execute("ROLLBACK")
        finally:
            await admin.execute(
                "ALTER TABLE ctt_staff_private.source_anchors "
                "ENABLE TRIGGER ctt_source_anchor_immutable"
            )
            await remove_synthetic(admin)
            await admin.execute(MIGRATION.read_text())
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                "register_source_admission(uuid,bytea,text,bigint,bigint,bytea) "
                "TO ctt_source_test"
            )
            await admin.close()

    asyncio.run(run())


def test_signed_preparation_cas_sql_and_private_provider_together(source_pg, tmp_path):
    async def run():
        import secrets
        import fitz
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from devnous.copa_telmex.staff_capture_edition import TrustedEditionBinding
        from devnous.copa_telmex.staff_capture_executor import LockedAttempt
        from devnous.copa_telmex.staff_capture_private_sources import (
            PrivateAttemptSourceProvider,
        )
        from devnous.copa_telmex.staff_capture_source_adapter import SourceCheckAdapter
        from devnous.copa_telmex.staff_review_challenge import (
            canonical_scope_bytes,
            scope_hash,
        )
        from devnous.copa_telmex.staff_source_admission import (
            SourceAdmissionLimits,
            admit_prepared_sources,
            prepare_source_admission,
            sign_source_admission,
        )
        from test_ctt_staff_security_postgres import PORT

        admin = await connect(source_pg)
        pool = None
        try:
            scope, challenge, attempt = await sample(admin)
            edition = await admin.fetchrow(
                "SELECT * FROM ctt_staff_private.manifest_anchors"
            )
            binding = TrustedEditionBinding(
                scope.tenant_id,
                scope.edition_id,
                edition["tournament_id"],
                2026,
                "synthetic",
            )
            originals, private = tmp_path / "originals", tmp_path / "private"
            originals.mkdir(mode=0o700)
            private.mkdir(mode=0o700)
            with fitz.open() as pdf:
                pdf.new_page().insert_text((72, 72), "synthetic original")
                pdf_bytes = pdf.tobytes()
            path = originals / "source.pdf"
            path.write_bytes(pdf_bytes)
            path.chmod(0o600)
            staff = []
            for slot, role in ((1, "director_tecnico"), (2, "auxiliar")):
                identity = {
                    "slot": slot,
                    "role": role,
                    "slot_ref": "staff:" + str(slot),
                    "evidence": {},
                }
                staff.append(identity | {"staff_entry_id": sha256_value(identity)})
            document_id = hashlib.sha256(b"synthetic-document").hexdigest()
            manifest = {
                "schema_version": "ctt.registration.batch-admission.v1",
                "edition": {
                    "tournament_id": str(binding.tournament_id),
                    "edition_year": 2026,
                    "roster_slug": "synthetic",
                },
                "documents": [
                    {
                        "document_id": document_id,
                        "pdf": "source.pdf",
                        "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                        "pages": [1],
                        "extraction": {"team": {}, "players": [], "staff": staff},
                    }
                ],
            }
            limits = SourceAdmissionLimits(
                1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000
            )
            key = Ed25519PrivateKey.generate()
            pool = await asyncpg.create_pool(
                host=source_pg,
                port=int(PORT),
                user="ctt_source_test",
                database="postgres",
                min_size=1,
                max_size=1,
            )
            with prepare_source_admission(
                canonical(manifest), originals, binding, limits
            ) as prepared:
                now = int(time.time())
                envelope = sign_source_admission(
                    prepared.plan_bytes,
                    admission_id=uuid4(),
                    issued_at=now,
                    expires_at=now + 180,
                    kid="synthetic",
                    private_key=key,
                )
                assert (
                    await admit_prepared_sources(
                        prepared,
                        envelope,
                        public_keys={"synthetic": key.public_key().public_bytes_raw()},
                        private_root=private,
                        pool=pool,
                    )
                    == prepared.plan_sha256
                )
                plan = json.loads(prepared.plan_bytes)
                scope = replace(
                    scope,
                    tournament_slug="synthetic",
                    manifest_sha256=plan["manifest_sha256"],
                    document_id=document_id,
                    pdf_sha256=manifest["documents"][0]["pdf_sha256"],
                    source_pages=(
                        replace(
                            scope.source_pages[0],
                            image_sha256=plan["documents"][0]["pages"][0][
                                "image_sha256"
                            ],
                        ),
                    ),
                )
            new_challenge = uuid4()
            await admin.execute(
                "INSERT INTO ctt_staff_private.review_challenges "
                "SELECT $1,principal_id,credential_id,tenant_id,edition_id,faculty,"
                "principal_authority_version,faculty_authority_version,"
                "credential_authority_version,$2,$3,issued_at,expires_at "
                "FROM ctt_staff_private.review_challenges WHERE challenge_id=$4",
                new_challenge,
                scope_hash(scope),
                secrets.token_bytes(32),
                challenge,
            )
            await insert_attempt(admin, scope, new_challenge, attempt)
            locked = LockedAttempt(
                attempt,
                new_challenge,
                scope.actor_id,
                b"synthetic",
                scope_hash(scope),
                canonical_scope_bytes(scope),
                binding,
            )
            provider = PrivateAttemptSourceProvider(
                locked, private, max_manifest_bytes=1_000_000
            )
            adapter = SourceCheckAdapter(
                provider,
                max_source_bytes=1_000_000,
                max_asset_bytes=1_000_000,
                max_manifest_bytes=1_000_000,
            )
            adapter._check_candidate(locked)
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.source_admissions"
                )
                == 1
            )
        finally:
            if pool is not None:
                await pool.close()
            await clear(admin)
            await admin.close()

    asyncio.run(run())
