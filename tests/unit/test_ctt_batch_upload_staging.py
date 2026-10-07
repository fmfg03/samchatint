import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from uuid import uuid4

import pytest

from devnous.copa_telmex.batch_admission import sha256_value, validate_manifest
from devnous.copa_telmex.batch_upload_staging import (
    BatchUploadStorageError,
    StagedUploadFile,
    initialize_upload_directory,
    load_manifest,
    purge_upload,
    staged_pdf_path,
    store_pdf,
)
from devnous.copa_telmex.models import Base
from devnous.gastos import schema_guard
from devnous.gastos.amex_schema_policy import (
    CTT_OWNER_MIGRATION_TABLES,
    runtime_managed_tables,
)

os.environ.setdefault(
    "SESSION_SECRET_KEY", "test-only-session-secret-key-0123456789abcdef"
)

import copa_telmex_dashboard as dashboard  # noqa: E402


class _Upload:
    def __init__(
        self,
        payload: bytes,
        filename: str = "source.pdf",
        content_type: str = "application/pdf",
    ):
        self.payload = payload
        self.offset = 0
        self.filename = filename
        self.content_type = content_type

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    async def seek(self, offset: int) -> None:
        self.offset = offset


class _Request:
    def __init__(self, form=None):
        self._form = form or {}

    async def form(self):
        return self._form


class _Result:
    def __init__(self, *, mapping=None, scalar=None, rows=None):
        self.mapping = mapping
        self.scalar = scalar
        self.rows = list(rows or [])

    def mappings(self):
        return self

    def one_or_none(self):
        return self.mapping

    def scalar_one_or_none(self):
        return self.scalar

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, *, results=None, scalar_values=None):
        self.results = list(results or [])
        self.scalar_values = list(scalar_values or [])
        self.added = []
        self.deleted = []
        self.commits = 0
        self.execute_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, *_args, **_kwargs):
        self.execute_calls += 1
        return self.results.pop(0) if self.results else _Result()

    async def scalar(self, *_args, **_kwargs):
        return self.scalar_values.pop(0)

    def add(self, value):
        self.added.append(value)

    async def delete(self, value):
        self.deleted.append(value)

    async def commit(self):
        self.commits += 1


class _SessionFactory:
    def __init__(self, *sessions):
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


def _upload_envelope(digest: str, *, status: str = "pending"):
    upload = dashboard.RegistrationBatchUpload(
        id=uuid4(),
        tournament_edition_id=uuid4(),
        manifest_sha256="a" * 64,
        document_count=1,
        file_count=1,
        status="staging",
        created_by_user_id="operator-1",
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    upload.files = [
        dashboard.RegistrationBatchUploadFile(
            id=uuid4(),
            source_filename="source.pdf",
            expected_sha256=digest,
            status=status,
        )
    ]
    return upload


def _staff(slot: int, role: str) -> dict:
    evidence = {"first_name": {"value": None, "status": "missing"}}
    identity = {"role": role, "slot": slot, "slot_ref": role, "evidence": evidence}
    return {
        "staff_entry_id": sha256_value(identity),
        "slot": slot,
        "role": role,
        "slot_ref": role,
        "first_name": None,
        "evidence": evidence,
    }


def _manifest(pdf_sha256: str) -> dict:
    return validate_manifest(
        {
            "schema_version": "ctt.registration.batch-admission.v1",
            "edition": {
                "tournament_id": "67c556df-f953-4f99-8cab-87ed4d111b05",
                "edition_year": 2026,
                "roster_slug": "copa-telmex-2026",
            },
            "documents": [
                {
                    "document_id": "a" * 64,
                    "pdf": "source.pdf",
                    "pdf_sha256": pdf_sha256,
                    "pages": [1],
                    "extraction": {
                        "team": {"name": "Equipo"},
                        "players": [],
                        "staff": [
                            _staff(1, "director_tecnico"),
                            _staff(2, "auxiliar"),
                        ],
                    },
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_staged_pdf_is_streamed_hashed_and_reopened(tmp_path):
    payload = b"%PDF-1.7\nprivate reviewed source"
    digest = hashlib.sha256(payload).hexdigest()
    upload_id = uuid4()
    manifest = _manifest(digest)
    directory = initialize_upload_directory(tmp_path, upload_id, manifest)

    path, byte_count = await store_pdf(
        root=tmp_path,
        upload_id=upload_id,
        upload=_Upload(payload),
        expected_sha256=digest,
    )

    assert directory.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert byte_count == len(payload)
    assert path == staged_pdf_path(tmp_path, upload_id, digest)
    assert load_manifest(tmp_path, upload_id)["manifest_sha256"] == (
        manifest["manifest_sha256"]
    )
    reader = StagedUploadFile(path, "source.pdf")
    assert await reader.read() == payload
    await reader.close()


@pytest.mark.asyncio
async def test_staged_pdf_rejects_hash_mismatch_without_replacing_good_file(tmp_path):
    good = b"%PDF-1.7\ngood"
    digest = hashlib.sha256(good).hexdigest()
    upload_id = uuid4()
    initialize_upload_directory(tmp_path, upload_id, _manifest(digest))
    await store_pdf(
        root=tmp_path,
        upload_id=upload_id,
        upload=_Upload(good),
        expected_sha256=digest,
    )

    with pytest.raises(BatchUploadStorageError) as exc_info:
        await store_pdf(
            root=tmp_path,
            upload_id=upload_id,
            upload=_Upload(b"%PDF-1.7\nwrong"),
            expected_sha256=digest,
        )

    assert exc_info.value.code == "source_pdf_hash_mismatch"
    assert staged_pdf_path(tmp_path, upload_id, digest).read_bytes() == good


@pytest.mark.asyncio
async def test_staged_upload_rejects_non_pdf_and_cleans_part_file(tmp_path):
    payload = b"not a pdf"
    digest = hashlib.sha256(payload).hexdigest()
    upload_id = uuid4()
    initialize_upload_directory(tmp_path, upload_id, _manifest(digest))

    with pytest.raises(BatchUploadStorageError) as exc_info:
        await store_pdf(
            root=tmp_path,
            upload_id=upload_id,
            upload=_Upload(payload),
            expected_sha256=digest,
        )

    assert exc_info.value.code == "invalid_file_type"
    assert not list((tmp_path / str(upload_id) / "files").glob("*.part"))


def test_staged_manifest_rejects_symlink_and_purge_is_bounded(tmp_path):
    upload_id = uuid4()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / str(upload_id)
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(BatchUploadStorageError, match="seguro"):
        initialize_upload_directory(tmp_path, upload_id, {})

    purge_upload(tmp_path, upload_id)
    assert outside.is_dir()
    assert not link.exists()


def test_staged_upload_tables_are_owner_migrated_and_readiness_required():
    managed = {table.name for table in runtime_managed_tables(Base.metadata)}
    expected_tables = {
        "copa_telmex_registration_batch_uploads",
        "copa_telmex_registration_batch_upload_files",
    }
    assert expected_tables <= CTT_OWNER_MIGRATION_TABLES
    assert not (expected_tables & managed)
    required_columns = {
        (item.table, item.column) for item in schema_guard.REQUIRED_COLUMNS
    }
    assert (
        "copa_telmex_registration_batch_uploads",
        "manifest_sha256",
    ) in required_columns
    assert (
        "copa_telmex_registration_batch_upload_files",
        "expected_sha256",
    ) in required_columns
    model_indexes = {
        index.name
        for table_name in expected_tables
        for index in Base.metadata.tables[table_name].indexes
    }
    assert {
        "ix_ctt_batch_uploads_status",
        "ix_ctt_batch_uploads_expires",
        "ix_ctt_batch_upload_files_upload",
    } <= model_indexes


def test_batch_ui_handles_api_errors_inside_form_and_exposes_progress():
    template = (
        Path(__file__).parents[2] / "templates/registration_review_new.html"
    ).read_text(encoding="utf-8")
    assert 'id="batch-error" role="alert"' in template
    assert 'id="batch-progress"' in template
    assert 'event.preventDefault()' in template
    assert "payload?.detail?.message" in template
    assert "admit-next" in template
    assert "JSON.stringify" not in template
    assert 'action="/api/registration-review/batches"' not in template


def test_staged_upload_migration_and_rollback_are_guarded():
    root = Path(__file__).parents[2]
    migration = (
        root
        / "database/migrations/20261007_copa_telmex_staged_batch_uploads.sql"
    ).read_text(encoding="utf-8")
    rollback = (
        root
        / "database/migrations/20261007_copa_telmex_staged_batch_uploads.rollback.sql"
    ).read_text(encoding="utf-8")
    assert "67108864" in migration
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in migration
    assert "Staged batch upload data exists" in rollback
    assert rollback.index("RAISE EXCEPTION") < rollback.index("DROP TABLE")


def _allow_operator(monkeypatch):
    monkeypatch.setattr(
        dashboard, "_ensure_registration_review_access", lambda _request: None
    )
    monkeypatch.setattr(
        dashboard,
        "_review_session_actor",
        lambda _request: {"user_id": "operator-1", "role": "admin"},
    )


@pytest.mark.asyncio
async def test_create_staged_batch_upload_returns_resumable_status(
    monkeypatch, tmp_path
):
    payload = b"%PDF-1.7\nreviewed source"
    normalized = _manifest(hashlib.sha256(payload).hexdigest())
    edition_id = uuid4()
    session = _Session(
        results=[
            _Result(mapping={"id": str(edition_id)}),
            _Result(scalar=None),
        ]
    )
    _allow_operator(monkeypatch)
    monkeypatch.setattr(dashboard, "async_session_maker", _SessionFactory(session))
    monkeypatch.setattr(dashboard, "_purge_expired_batch_uploads", _async_value(0))
    monkeypatch.setattr(dashboard, "_batch_staging_root", lambda: tmp_path)

    response = await dashboard.create_registration_batch_upload(
        _Request(
            {
                "manifest": _Upload(
                    json.dumps(normalized).encode(),
                    "manifest.json",
                    "application/json",
                )
            }
        )
    )
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["expected_files"] == 1
    assert body["uploaded_files"] == 0
    assert body["expected_documents"] == 1
    assert session.commits == 1
    assert len(session.added) == 1
    assert (tmp_path / body["upload_id"] / "manifest.json").is_file()


def _async_value(value):
    async def result(*_args, **_kwargs):
        return value

    return result


@pytest.mark.asyncio
async def test_upload_and_status_endpoints_report_verified_file(
    monkeypatch, tmp_path
):
    payload = b"%PDF-1.7\nreviewed source"
    digest = hashlib.sha256(payload).hexdigest()
    upload = _upload_envelope(digest)
    session = _Session()
    _allow_operator(monkeypatch)
    monkeypatch.setattr(dashboard, "async_session_maker", _SessionFactory(session))
    monkeypatch.setattr(
        dashboard, "_batch_upload_for_actor", _async_value(upload)
    )
    stored_path = tmp_path / f"{digest}.pdf"
    monkeypatch.setattr(
        dashboard,
        "store_staged_batch_pdf",
        _async_value((stored_path, len(payload))),
    )

    uploaded = await dashboard.upload_registration_batch_file(
        upload.id, _Request({"file": _Upload(payload)})
    )
    uploaded_body = json.loads(uploaded.body)

    assert uploaded_body["status"] == "ready"
    assert uploaded_body["uploaded_files"] == 1
    assert upload.files[0].stored_sha256 == digest
    assert upload.files[0].storage_key == stored_path.name
    assert session.commits == 1

    status_session = _Session()
    monkeypatch.setattr(
        dashboard, "async_session_maker", _SessionFactory(status_session)
    )
    status = await dashboard.registration_batch_upload_status(
        upload.id, _Request()
    )
    assert json.loads(status.body)["uploaded_files"] == 1


@pytest.mark.asyncio
async def test_batch_upload_actor_lookup_and_expiry_cleanup(monkeypatch, tmp_path):
    upload = _upload_envelope("b" * 64)
    lookup_session = _Session(results=[_Result(scalar=upload)])
    found = await dashboard._batch_upload_for_actor(
        lookup_session,
        upload_id=upload.id,
        actor_id="operator-1",
        lock=True,
    )
    assert found is upload

    cleanup_session = _Session(results=[_Result(rows=[upload])])
    purged = []
    monkeypatch.setattr(
        dashboard, "async_session_maker", _SessionFactory(cleanup_session)
    )
    monkeypatch.setattr(dashboard, "_batch_staging_root", lambda: tmp_path)
    monkeypatch.setattr(
        dashboard,
        "purge_staged_batch_upload",
        lambda _root, upload_id: purged.append(upload_id),
    )

    assert await dashboard._purge_expired_batch_uploads() == 1
    assert purged == [upload.id]
    assert cleanup_session.deleted == [upload]
    assert cleanup_session.commits == 1


class _Reader:
    def __init__(self, _path, filename, payload):
        self.filename = filename
        self.payload = payload
        self.offset = 0
        self.closed = False

    async def read(self, size=-1):
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    async def seek(self, offset):
        self.offset = offset

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_admit_next_completes_and_purges_staging(monkeypatch, tmp_path):
    payload = b"%PDF-1.7\nreviewed source"
    digest = hashlib.sha256(payload).hexdigest()
    manifest = _manifest(digest)
    upload = _upload_envelope(digest, status="uploaded")
    upload.manifest_sha256 = manifest["manifest_sha256"]
    batch_id = uuid4()
    lock_session = _Session(scalar_values=[True, 1])
    cleanup_session = _Session()
    purged = []
    readers = []
    _allow_operator(monkeypatch)
    monkeypatch.setattr(
        dashboard,
        "async_session_maker",
        _SessionFactory(lock_session, cleanup_session),
    )
    monkeypatch.setattr(
        dashboard, "_batch_upload_for_actor", _async_value(upload)
    )
    monkeypatch.setattr(dashboard, "_batch_staging_root", lambda: tmp_path)
    monkeypatch.setattr(
        dashboard, "load_staged_batch_manifest", lambda *_args: manifest
    )

    def reader_factory(path, filename):
        reader = _Reader(path, filename, payload)
        readers.append(reader)
        return reader

    monkeypatch.setattr(dashboard, "StagedUploadFile", reader_factory)
    monkeypatch.setattr(
        dashboard,
        "_admit_validated_registration_review_batch",
        _async_value({"batch_id": str(batch_id)}),
    )
    monkeypatch.setattr(
        dashboard,
        "purge_staged_batch_upload",
        lambda _root, upload_id: purged.append(upload_id),
    )

    response = await dashboard.admit_next_registration_batch_document(
        upload.id, _Request()
    )
    body = json.loads(response.body)

    assert body["status"] == "admitted"
    assert body["admitted_documents"] == 1
    assert upload.batch_id == UUID(str(batch_id))
    assert upload.files[0].status == "purged"
    assert purged == [upload.id]
    assert readers[0].closed is True
    assert lock_session.commits == 1
    assert cleanup_session.commits == 1


@pytest.mark.asyncio
async def test_cancel_staged_batch_without_admissions(monkeypatch, tmp_path):
    upload = _upload_envelope("b" * 64)
    session = _Session()
    purged = []
    _allow_operator(monkeypatch)
    monkeypatch.setattr(dashboard, "async_session_maker", _SessionFactory(session))
    monkeypatch.setattr(
        dashboard, "_batch_upload_for_actor", _async_value(upload)
    )
    monkeypatch.setattr(dashboard, "_batch_staging_root", lambda: tmp_path)
    monkeypatch.setattr(
        dashboard,
        "purge_staged_batch_upload",
        lambda _root, upload_id: purged.append(upload_id),
    )

    response = await dashboard.cancel_registration_batch_upload(
        upload.id, _Request()
    )

    assert json.loads(response.body) == {"ok": True, "status": "cancelled"}
    assert purged == [upload.id]
    assert session.execute_calls == 1
    assert session.commits == 1


class _ReadyConnection:
    async def execute(self, *_args, **_kwargs):
        return None


class _ReadyBegin:
    async def __aenter__(self):
        return _ReadyConnection()

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_readyz_requires_schema_and_private_staging(monkeypatch, tmp_path):
    engine = SimpleNamespace(begin=lambda: _ReadyBegin())
    monkeypatch.setattr(dashboard, "db_engine", engine)
    monkeypatch.setattr(
        dashboard, "check_schema_health", _async_value({"ok": True})
    )
    monkeypatch.setattr(dashboard, "_batch_staging_root", lambda: tmp_path)

    response = await dashboard.readyz()
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["batch_staging_health"] == {"ok": True, "status": "ready"}
