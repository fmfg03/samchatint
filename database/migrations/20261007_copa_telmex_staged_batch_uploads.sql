-- Owner-run migration. Back up before applying; never run from web startup.
BEGIN;

CREATE TABLE IF NOT EXISTS copa_telmex_registration_batch_uploads (
    id UUID PRIMARY KEY,
    tournament_edition_id UUID NOT NULL,
    manifest_sha256 VARCHAR(64) NOT NULL,
    document_count INTEGER NOT NULL,
    file_count INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'staging',
    created_by_user_id VARCHAR(80) NOT NULL,
    batch_id UUID REFERENCES copa_telmex_registration_batches(id) ON DELETE RESTRICT,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ctt_batch_upload_actor_manifest UNIQUE (
        tournament_edition_id, manifest_sha256, created_by_user_id
    ),
    CONSTRAINT ck_ctt_batch_upload_status CHECK (
        status IN ('staging','ready','admitting','admitted','cancelled','expired','failed')
    ),
    CONSTRAINT ck_ctt_batch_upload_counts CHECK (
        document_count > 0 AND file_count > 0
    )
);

CREATE TABLE IF NOT EXISTS copa_telmex_registration_batch_upload_files (
    id UUID PRIMARY KEY,
    upload_id UUID NOT NULL REFERENCES copa_telmex_registration_batch_uploads(id) ON DELETE CASCADE,
    source_filename VARCHAR(255) NOT NULL,
    expected_sha256 VARCHAR(64) NOT NULL,
    stored_sha256 VARCHAR(64),
    byte_count BIGINT,
    storage_key VARCHAR(160),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    uploaded_at TIMESTAMPTZ,
    CONSTRAINT uq_ctt_batch_upload_file_name UNIQUE (upload_id, source_filename),
    CONSTRAINT ck_ctt_batch_upload_file_status CHECK (
        status IN ('pending','uploaded','purged')
    ),
    CONSTRAINT ck_ctt_batch_upload_file_size CHECK (
        byte_count IS NULL OR (byte_count > 0 AND byte_count <= 67108864)
    )
);

CREATE INDEX IF NOT EXISTS ix_ctt_batch_uploads_edition
    ON copa_telmex_registration_batch_uploads(tournament_edition_id);
CREATE INDEX IF NOT EXISTS ix_ctt_batch_uploads_actor
    ON copa_telmex_registration_batch_uploads(created_by_user_id);
CREATE INDEX IF NOT EXISTS ix_ctt_batch_uploads_status
    ON copa_telmex_registration_batch_uploads(status);
CREATE INDEX IF NOT EXISTS ix_ctt_batch_uploads_expires
    ON copa_telmex_registration_batch_uploads(expires_at);
CREATE INDEX IF NOT EXISTS ix_ctt_batch_upload_files_upload
    ON copa_telmex_registration_batch_upload_files(upload_id);

DO $grants$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'devnous_user') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE
      ON copa_telmex_registration_batch_uploads TO devnous_user;
    GRANT SELECT, INSERT, UPDATE, DELETE
      ON copa_telmex_registration_batch_upload_files TO devnous_user;
  END IF;
END
$grants$;

COMMIT;
