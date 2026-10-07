import hashlib
from pathlib import Path
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


class _Upload:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


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
