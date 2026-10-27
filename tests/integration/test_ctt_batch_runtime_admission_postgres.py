"""Real CTT admission with runtime grants; only external Zaubern is simulated."""

import asyncio
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault(
    "SESSION_SECRET_KEY", "test-only-session-secret-key-0123456789abcdef"
)

import copa_telmex_dashboard as dashboard  # noqa: E402

from devnous.copa_telmex import draft_versioning  # noqa: E402
from devnous.copa_telmex.batch_admission import (  # noqa: E402
    sha256_value,
    validate_manifest,
)


class _Pdf:
    def __init__(self, payload):
        self.payload = payload

    async def read(self, _size=-1):
        return self.payload

    async def seek(self, _offset):
        pass


class _Gate:
    """Simulate only the external authorization; persist real draft versions."""

    def __init__(self):
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = None
        self.fail_once = False

    async def authorize_draft_version(self, payload):
        self.calls += 1
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("synthetic external gate outage")
        return {
            "authorized": True,
            "draft_decision": {
                "decision": "AUTHORIZE_DRAFT_VERSION",
                "new_draft_id": payload["new_draft_id"],
                "new_content_hash": payload["new_content_hash"],
                "decision_id": "sha256:" + "a" * 64,
            },
            "draft_receipt": {
                "verified": True,
                "receipt_id": "isolated-test-receipt-" + payload["operation_id"],
            },
        }


@pytest.fixture(scope="module")
def runtime_postgres():
    """Use the explicit test socket, or the existing PG16 CI isolation pattern."""
    socket = os.environ.get("SAMCHAT_TEST_PG_SOCKET", "")
    port = int(os.environ.get("SAMCHAT_TEST_PG_PORT", "55439"))
    if socket:
        assert socket.startswith("/tmp/samchat-ctt-pg-")
        yield socket, port
        return
    if os.environ.get("CI") != "true":
        pytest.skip("Explicit ephemeral PostgreSQL socket required")
    # The repository's readonly PostgreSQL CI job already requires these tools.
    binary = Path("/usr/lib/postgresql/16/bin")
    assert (binary / "initdb").is_file(), "CI requires the existing PostgreSQL 16 tools"
    cluster = Path(tempfile.mkdtemp(prefix="samchat-ctt-pg-runtime-ci-"))
    data = cluster / "data"
    prefix = []
    if os.geteuid() == 0:
        import pwd

        owner = pwd.getpwnam("postgres")
        os.chown(cluster, owner.pw_uid, owner.pw_gid)
        prefix = ["runuser", "-u", "postgres", "--"]
    try:
        subprocess.run(
            prefix
            + [
                str(binary / "initdb"),
                "-D",
                str(data),
                "-U",
                "postgres",
                "--encoding=UTF8",
                "--locale=C",
                "--auth-local=trust",
                "--auth-host=reject",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            prefix
            + [
                str(binary / "pg_ctl"),
                "-D",
                str(data),
                "-l",
                str(cluster / "server.log"),
                "-o",
                f"-k {cluster} -p {port} -c listen_addresses=''",
                "-w",
                "start",
            ],
            check=True,
            capture_output=True,
        )
        yield str(cluster), port
    finally:
        if (data / "postmaster.pid").exists():
            subprocess.run(
                prefix
                + [str(binary / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
                check=True,
                capture_output=True,
            )
        shutil.rmtree(cluster)


@pytest_asyncio.fixture
async def admission_db(monkeypatch, tmp_path, runtime_postgres, request):
    socket, port = runtime_postgres
    schema = "test_runtime_admission_" + uuid4().hex
    admin = await asyncpg.connect(
        host=socket, port=port, user="postgres", database="postgres"
    )
    options = {
        "host": socket,
        "port": port,
        "server_settings": {"search_path": schema, "statement_timeout": "5000"},
    }
    owner_engine = create_async_engine(
        "postgresql+asyncpg://postgres@/postgres", connect_args=options
    )
    runtime_engine = create_async_engine(
        "postgresql+asyncpg://devnous_user@/postgres", connect_args=options
    )

    try:
        await admin.execute("CREATE ROLE devnous_user LOGIN")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}"')
        await admin.execute(
            "CREATE TABLE tournaments(id UUID PRIMARY KEY, name TEXT, active BOOLEAN)"
        )
        root = Path(__file__).resolve().parents[2]
        await admin.execute(
            (
                root
                / "database/migrations/20261005_copa_telmex_tournament_editions.sql"
            ).read_text()
        )
        owner_tables = {
            "copa_telmex_registration_batches",
            "copa_telmex_registration_batch_documents",
            "copa_telmex_team_staff",
            "copa_telmex_registration_batch_uploads",
            "copa_telmex_registration_batch_upload_files",
        }
        tables = [
            table
            for table in dashboard.Base.metadata.tables.values()
            if table.name not in owner_tables
        ]
        async with owner_engine.begin() as connection:
            await connection.run_sync(
                lambda sync: dashboard.Base.metadata.create_all(sync, tables=tables)
            )
        await admin.execute(
            (
                root / "database/migrations/20261006_copa_telmex_batch_admission.sql"
            ).read_text()
        )
        # Production installed these tables as the runtime role, then revoked
        # UPDATE. PostgreSQL's native FK check locks as the referenced owner.
        for table in (
            "copa_telmex_registration_batches",
            "copa_telmex_registration_batch_documents",
        ):
            await admin.execute(f"ALTER TABLE {table} OWNER TO devnous_user")
            await admin.execute(f"GRANT ALL ON {table} TO devnous_user")
            await admin.execute(f"REVOKE UPDATE, DELETE ON {table} FROM devnous_user")
        if getattr(request, "param", True):
            await admin.execute(_owner_migration())
        for filename, function, trigger, kind in [
            (
                "20260716_regs02_append_only_review_drafts.sql",
                "enforce_registration_review_draft_append_only",
                "trg_registration_review_draft_append_only",
                "CREATE TRIGGER",
            ),
            (
                "20260716_regs04_page_composition.sql",
                "guard_registration_review_asset_immutability",
                "trg_registration_review_asset_immutable",
                "CREATE TRIGGER",
            ),
            (
                "20260716_regs05_human_field_authority.sql",
                "enforce_registration_human_edit_atomicity",
                "trg_registration_human_edit_draft_atomicity",
                "CREATE CONSTRAINT TRIGGER",
            ),
        ]:
            migration = (root / "database/migrations" / filename).read_text()
            start = migration.index(f"CREATE OR REPLACE FUNCTION {function}()")
            end = migration.index("\n$$;", start) + len("\n$$;")
            await admin.execute(migration[start:end])
            start = migration.index(f"{kind} {trigger}")
            tail = f"EXECUTE FUNCTION {function}();"
            end = migration.index(tail, start) + len(tail)
            await admin.execute(migration[start:end])
        await admin.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO devnous_user')
        await admin.execute(
            "GRANT SELECT ON tournaments, copa_telmex_tournament_editions "
            "TO devnous_user"
        )
        await admin.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON "
            "copa_telmex_registration_review_sessions, "
            "copa_telmex_registration_review_assets, "
            "copa_telmex_registration_review_drafts TO devnous_user"
        )
        tournament_id, edition_id = uuid4(), uuid4()
        await admin.execute(
            "INSERT INTO tournaments VALUES ($1, 'Isolated Copa', TRUE)", tournament_id
        )
        await admin.execute(
            "INSERT INTO copa_telmex_tournament_editions "
            "(id,tournament_id,edition_year,roster_slug) "
            "VALUES ($1,$2,2026,'isolated-copa')",
            edition_id,
            tournament_id,
        )
        factory = async_sessionmaker(runtime_engine, expire_on_commit=False)
        monkeypatch.setattr(dashboard, "async_session_maker", factory)
        monkeypatch.setattr(dashboard, "review_uploads_dir", tmp_path / "reviews")
        gate = _Gate()
        monkeypatch.setattr(
            draft_versioning.RegistrationGovernanceClient,
            "from_environment",
            classmethod(lambda cls: gate),
        )
        yield admin, factory, tournament_id, gate
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()
        await admin.execute("SET search_path TO public")
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.execute("DROP ROLE IF EXISTS samchat_ctt_admission_owner")
        await admin.execute("DROP ROLE IF EXISTS devnous_user")
        await admin.close()


def _owner_migration(*, rollback=False):
    suffix = ".rollback.sql" if rollback else ".sql"
    return (
        Path(__file__).resolve().parents[2]
        / "database/migrations"
        / ("20261027_copa_telmex_batch_table_owner" + suffix)
    ).read_text()


def _sources(tournament_id, documents=1):
    if dashboard.fitz is None:
        pytest.skip("PyMuPDF is required for real source rendering")
    pdf = dashboard.fitz.open()
    pdf.new_page(width=200, height=200)
    pdf.new_page(width=200, height=200)
    payload = pdf.tobytes()
    pdf.close()
    staff = []
    for slot, role in ((1, "director_tecnico"), (2, "auxiliar")):
        evidence = {"first_name": {"value": None, "status": "missing"}}
        identity = {"role": role, "slot": slot, "slot_ref": role, "evidence": evidence}
        staff.append({**identity, "staff_entry_id": sha256_value(identity)})
    rows = []
    for index in range(documents):
        rows.append(
            {
                "document_id": hashlib.sha256(str(index).encode()).hexdigest(),
                "pdf": f"source-{index}.pdf",
                "pdf_sha256": hashlib.sha256(payload).hexdigest(),
                "pages": [1, 2],
                "extraction": {
                    "team": {"name": f"Isolated team {index}"},
                    "players": [],
                    "staff": copy.deepcopy(staff),
                },
            }
        )
    # Each document needs a distinct source identity, even for synthetic PDFs.
    for index, row in enumerate(rows):
        row["pdf_sha256"] = hashlib.sha256(
            payload + f"\n% {index}".encode()
        ).hexdigest()
    manifest = validate_manifest(
        {
            "schema_version": "ctt.registration.batch-admission.v1",
            "edition": {
                "tournament_id": str(tournament_id),
                "edition_year": 2026,
                "roster_slug": "isolated-copa",
            },
            "documents": rows,
        }
    )
    uploads = {
        row["pdf"]: _Pdf(payload + f"\n% {index}".encode())
        for index, row in enumerate(rows)
    }
    return manifest, uploads


async def _admit(manifest, uploads, **kwargs):
    return await dashboard._admit_validated_registration_review_batch(
        manifest=manifest,
        upload_files=uploads,
        actor_id=kwargs.pop("actor_id", "isolated-operator"),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_runtime_role_admits_real_draft_and_recovers_retry(admission_db):
    admin, factory, tournament_id, gate = admission_db
    async with factory() as session:
        assert await session.scalar(text("SELECT current_user")) == "devnous_user"
        assert await session.scalar(text("SELECT session_user")) == "devnous_user"
        assert not await session.scalar(
            text(
                "SELECT has_table_privilege(current_user, "
                "'copa_telmex_registration_batches', 'UPDATE')"
            )
        )
    async with factory() as session:
        assert await session.scalar(text("SELECT current_user")) == "devnous_user"
    manifest, uploads = _sources(tournament_id)
    first = await _admit(manifest, uploads)
    second = await _admit(manifest, uploads)
    assert first["documents"][0]["status"] == "ADMITTED_FOR_REVIEW"
    assert second["documents"][0]["status"] == "RECOVERED"
    assert gate.calls == 1
    for table in [
        "copa_telmex_registration_batches",
        "copa_telmex_registration_batch_documents",
        "copa_telmex_registration_review_sessions",
        "copa_telmex_registration_review_drafts",
    ]:
        assert await admin.fetchval("SELECT count(*) FROM " + table) == 1
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM copa_telmex_registration_review_assets"
        )
        == 2
    )
    assert await admin.fetchval("SELECT count(*) FROM copa_telmex_teams") == 0
    assert await admin.fetchval("SELECT count(*) FROM copa_telmex_players") == 0
    draft = await admin.fetchval(
        "SELECT validation::text FROM copa_telmex_registration_review_drafts"
    )
    assert "STAFF_GOVERNANCE_CONTRACT_REQUIRED" in draft
    raw = json.loads(
        await admin.fetchval(
            "SELECT ocr_raw::text FROM copa_telmex_registration_review_drafts"
        )
    )
    assert raw["human_reviewed"] is False
    assert raw["canonical_import_ready"] is False
    assert raw["extraction_metadata"]["source_asset_count"] == 2
    assert raw["extraction_metadata"]["page_count"] == 2
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM copa_telmex_registration_review_assets a "
            "JOIN copa_telmex_registration_review_drafts d ON d.id=a.admitted_draft_id "
            "WHERE a.admission_operation_id=d.mutation_operation_id "
            "AND a.admission_decision_id=d.mutation_decision_id "
            "AND a.admission_receipt_id=d.mutation_receipt_id"
        )
        == 2
    )
    assert first["committed_teams"] == 0 and first["grants_eligibility"] is False


@pytest.mark.parametrize("admission_db", [False], indirect=True)
@pytest.mark.asyncio
async def test_legacy_runtime_ownership_reproduces_fk_failure(admission_db):
    admin, _factory, tournament_id, _gate = admission_db
    manifest, uploads = _sources(tournament_id)
    with pytest.raises(DBAPIError, match="permission denied for table .*batches"):
        await _admit(manifest, uploads)
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM copa_telmex_registration_batch_documents"
        )
        == 0
    )
    await admin.execute(_owner_migration())
    result = await _admit(manifest, uploads)
    assert result["documents"][0]["status"] == "ADMITTED_FOR_REVIEW"


@pytest.mark.asyncio
async def test_owner_migration_is_idempotent_and_runtime_remains_append_only(
    admission_db,
):
    admin, factory, tournament_id, _gate = admission_db
    await admin.execute(_owner_migration())
    role = await admin.fetchrow(
        "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,"
        "rolbypassrls,rolinherit FROM pg_roles "
        "WHERE rolname='samchat_ctt_admission_owner'"
    )
    assert role is not None and not any(role.values())
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM pg_auth_members WHERE roleid="
            "'samchat_ctt_admission_owner'::regrole OR member="
            "'samchat_ctt_admission_owner'::regrole"
        )
        == 0
    )
    for table in (
        "copa_telmex_registration_batches",
        "copa_telmex_registration_batch_documents",
    ):
        assert (
            await admin.fetchval(
                "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=$1::regclass",
                table,
            )
            == "samchat_ctt_admission_owner"
        )
        async with factory() as session:
            for privilege in ("SELECT", "INSERT"):
                assert await session.scalar(
                    text("SELECT has_table_privilege(current_user,:table,:privilege)"),
                    {"table": table, "privilege": privilege},
                )
            for privilege in ("UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                assert not await session.scalar(
                    text("SELECT has_table_privilege(current_user,:table,:privilege)"),
                    {"table": table, "privilege": privilege},
                )
        for statement in (
            f"UPDATE {table} SET id=id WHERE false",
            f"DELETE FROM {table} WHERE false",
            f"TRUNCATE {table}",
        ):
            async with factory() as session:
                with pytest.raises(DBAPIError, match="permission denied"):
                    await session.execute(text(statement))
                await session.rollback()
    async with factory() as session:
        with pytest.raises(DBAPIError, match="permission denied to set role"):
            await session.execute(text("SET ROLE samchat_ctt_admission_owner"))
        await session.rollback()
    manifest, uploads = _sources(tournament_id)
    await _admit(manifest, uploads)
    # Check the native composite FK using its real trigger and a missing parent.
    tx = admin.transaction()
    await tx.start()
    try:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await admin.execute(
                "UPDATE copa_telmex_registration_batch_documents SET batch_id=$1",
                uuid4(),
            )
    finally:
        await tx.rollback()


@pytest.mark.asyncio
async def test_owner_rollback_and_reapply_preserve_admitted_drafts(admission_db):
    admin, _factory, tournament_id, _gate = admission_db
    manifest, uploads = _sources(tournament_id)
    await _admit(manifest, uploads)
    before = await admin.fetchval(
        "SELECT jsonb_agg(to_jsonb(d))::text "
        "FROM copa_telmex_registration_batch_documents d"
    )
    await admin.execute(_owner_migration(rollback=True))
    assert (
        await admin.fetchval(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE oid='copa_telmex_registration_batches'::regclass"
        )
        == "devnous_user"
    )
    await admin.execute(_owner_migration())
    result = await _admit(manifest, uploads)
    assert result["documents"][0]["status"] == "RECOVERED"
    assert (
        await admin.fetchval(
            "SELECT jsonb_agg(to_jsonb(d))::text "
            "FROM copa_telmex_registration_batch_documents d"
        )
        == before
    )


@pytest.mark.asyncio
async def test_owner_migration_rejects_incompatible_existing_role_atomically(
    admission_db,
):
    admin, _factory, _tournament_id, _gate = admission_db
    await admin.execute("ALTER ROLE samchat_ctt_admission_owner LOGIN")
    try:
        with pytest.raises(asyncpg.RaiseError, match="owner role is incompatible"):
            await admin.execute(_owner_migration())
        await admin.execute("ROLLBACK")
        assert (
            await admin.fetchval(
                "SELECT pg_get_userbyid(relowner) FROM pg_class "
                "WHERE oid='copa_telmex_registration_batches'::regclass"
            )
            == "samchat_ctt_admission_owner"
        )
    finally:
        await admin.execute("ALTER ROLE samchat_ctt_admission_owner NOLOGIN")


@pytest.mark.asyncio
async def test_runtime_cannot_apply_owner_migration(admission_db):
    _admin, factory, _tournament_id, _gate = admission_db
    async with factory() as session:
        # SQLAlchemy prepares one statement at a time; asyncpg can execute the
        # administrator migration verbatim on the same authenticated connection.
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        with pytest.raises(asyncpg.RaiseError, match="requires an administrator"):
            await raw.driver_connection.execute(_owner_migration())
        await raw.driver_connection.execute("ROLLBACK")


@pytest.mark.asyncio
async def test_runtime_concurrent_retry_waits_and_creates_one_draft(admission_db):
    admin, factory, tournament_id, gate = admission_db
    manifest, uploads = _sources(tournament_id)
    gate.release = asyncio.Event()
    first = asyncio.create_task(_admit(manifest, uploads))
    try:
        await asyncio.wait_for(gate.entered.wait(), timeout=5)
        second = asyncio.create_task(
            _admit(manifest, uploads, actor_id="other-isolated-operator")
        )
        try:
            for _ in range(100):
                if await admin.fetchval(
                    "SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                    "AND NOT granted AND classid=$1",
                    0x43545442,
                ):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("The concurrent retry never waited for its batch lock")
            assert not second.done()
            async with factory() as session:
                await asyncio.wait_for(
                    dashboard._lock_registration_batch(session, uuid4()), timeout=1
                )
            gate.release.set()
            results = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
        finally:
            if not second.done():
                second.cancel()
                await asyncio.gather(second, return_exceptions=True)
    finally:
        gate.release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    assert sorted(r["documents"][0]["status"] for r in results) == [
        "ADMITTED_FOR_REVIEW",
        "RECOVERED",
    ]
    assert gate.calls == 1
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM copa_telmex_registration_batch_documents"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_runtime_rollback_releases_lock_for_retry(admission_db):
    admin, _factory, tournament_id, gate = admission_db
    manifest, uploads = _sources(tournament_id)
    gate.fail_once = True
    with pytest.raises(RuntimeError, match="synthetic external gate outage"):
        await _admit(manifest, uploads)
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM copa_telmex_registration_batch_documents"
        )
        == 0
    )
    result = await asyncio.wait_for(_admit(manifest, uploads), timeout=5)
    assert result["documents"][0]["status"] == "ADMITTED_FOR_REVIEW"
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM copa_telmex_registration_review_drafts"
        )
        == 1
    )


@pytest.mark.parametrize("one_at_a_time", [False, True])
@pytest.mark.asyncio
async def test_eleven_dossiers_with_runtime_role(admission_db, one_at_a_time):
    admin, factory, tournament_id, gate = admission_db
    manifest, uploads = _sources(tournament_id, documents=11)
    if one_at_a_time:
        for _ in range(11):
            result = await _admit(manifest, uploads, max_new_documents=1)
            assert (
                sum(
                    item["status"] == "ADMITTED_FOR_REVIEW"
                    for item in result["documents"]
                )
                == 1
            )
    else:
        await _admit(manifest, uploads)
    recovered = await _admit(manifest, uploads)
    assert len(recovered["documents"]) == 11
    assert {row["status"] for row in recovered["documents"]} == {"RECOVERED"}
    assert gate.calls == 11
    for table in [
        "copa_telmex_registration_batch_documents",
        "copa_telmex_registration_review_sessions",
        "copa_telmex_registration_review_drafts",
    ]:
        assert await admin.fetchval("SELECT count(*) FROM " + table) == 11
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM copa_telmex_registration_review_assets"
        )
        == 22
    )
    assert await admin.fetchval("SELECT count(*) FROM copa_telmex_teams") == 0
    assert await admin.fetchval("SELECT count(*) FROM copa_telmex_players") == 0
    async with factory() as session:
        for table in [
            "copa_telmex_registration_batches",
            "copa_telmex_registration_batch_documents",
        ]:
            assert not await session.scalar(
                text(f"SELECT has_table_privilege(current_user, '{table}', 'UPDATE')")
            )
            assert not await session.scalar(
                text(f"SELECT has_table_privilege(current_user, '{table}', 'DELETE')")
            )


@pytest.mark.asyncio
async def test_runtime_role_rejects_foreign_edition_without_creating_batch(
    admission_db,
):
    admin, _factory, _tournament_id, gate = admission_db
    manifest, uploads = _sources(uuid4())
    with pytest.raises(dashboard.HTTPException) as error:
        await _admit(manifest, uploads)
    assert error.value.detail["error"] == "edition_scope_missing"
    assert gate.calls == 0
    assert (
        await admin.fetchval("SELECT count(*) FROM copa_telmex_registration_batches")
        == 0
    )


@pytest.mark.asyncio
async def test_native_guards_still_reject_page_rebinding_and_draft_edits(admission_db):
    admin, factory, tournament_id, _gate = admission_db
    manifest, uploads = _sources(tournament_id)
    await _admit(manifest, uploads)
    for statement, reason in [
        (
            "UPDATE copa_telmex_registration_review_assets "
            "SET image_path=image_path || '.changed'",
            "REG-S04",
        ),
        (
            "UPDATE copa_telmex_registration_review_drafts SET needs_review=false",
            "REG-S02",
        ),
    ]:
        async with factory() as session:
            with pytest.raises(DBAPIError, match=reason):
                await session.execute(text(statement))
            await session.rollback()
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM copa_telmex_registration_review_assets "
            "WHERE image_path LIKE '%.changed'"
        )
        == 0
    )
    assert (
        await admin.fetchval(
            "SELECT bool_and(needs_review) FROM copa_telmex_registration_review_drafts"
        )
        is True
    )
