-- Owner-run migration for governed batch admission and technical staff.
BEGIN;

CREATE TABLE IF NOT EXISTS copa_telmex_registration_batches (
    id UUID PRIMARY KEY,
    tournament_edition_id UUID NOT NULL REFERENCES copa_telmex_tournament_editions(id) ON DELETE RESTRICT,
    manifest_sha256 VARCHAR(64) NOT NULL,
    document_count INTEGER NOT NULL CHECK (document_count > 0),
    admitted_by_user_id VARCHAR(80) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ctt_registration_batch_id_edition UNIQUE (id, tournament_edition_id),
    CONSTRAINT uq_ctt_registration_batch_manifest UNIQUE (tournament_edition_id, manifest_sha256),
    CONSTRAINT ck_ctt_registration_manifest_sha256_hex CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS copa_telmex_registration_batch_documents (
    id UUID PRIMARY KEY,
    batch_id UUID NOT NULL,
    tournament_edition_id UUID NOT NULL REFERENCES copa_telmex_tournament_editions(id) ON DELETE RESTRICT,
    document_id VARCHAR(64) NOT NULL,
    source_filename VARCHAR(255) NOT NULL,
    pdf_sha256 VARCHAR(64) NOT NULL,
    source_pages JSONB NOT NULL,
    source_pages_sha256 VARCHAR(64) NOT NULL,
    payload_sha256 VARCHAR(64) NOT NULL,
    review_session_id UUID NOT NULL UNIQUE REFERENCES copa_telmex_registration_review_sessions(id) ON DELETE RESTRICT,
    admission_receipt JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ctt_registration_document_identity UNIQUE (tournament_edition_id, document_id),
    CONSTRAINT uq_ctt_registration_document_source UNIQUE (tournament_edition_id, pdf_sha256, source_pages_sha256),
    CONSTRAINT fk_ctt_registration_document_batch_edition
        FOREIGN KEY (batch_id, tournament_edition_id)
        REFERENCES copa_telmex_registration_batches(id, tournament_edition_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_ctt_registration_document_id_hex CHECK (document_id ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_ctt_registration_pdf_sha256_hex CHECK (pdf_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_ctt_registration_source_pages_sha256_hex CHECK (source_pages_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_ctt_registration_payload_sha256_hex CHECK (payload_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS copa_telmex_team_staff (
    id UUID PRIMARY KEY,
    team_id UUID NOT NULL REFERENCES copa_telmex_teams(id) ON DELETE CASCADE,
    staff_slot INTEGER NOT NULL CHECK (staff_slot IN (1, 2)),
    role VARCHAR(40) NOT NULL CHECK (role IN ('director_tecnico', 'auxiliar')),
    first_name VARCHAR(100),
    last_name VARCHAR(200),
    birth_date DATE,
    curp VARCHAR(18),
    photo_path VARCHAR(500),
    evidence JSONB NOT NULL,
    governance_state VARCHAR(30) NOT NULL CHECK (governance_state IN ('PENDING_FINALITY', 'ACTIVE')),
    governance_draft_id VARCHAR(80) NOT NULL,
    governance_draft_version INTEGER NOT NULL,
    governance_decision_id VARCHAR(80) NOT NULL,
    roster_draft_binding VARCHAR(80) NOT NULL,
    preauthorization_receipt_id VARCHAR(120) NOT NULL,
    finality_receipt_id VARCHAR(120),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ctt_team_staff_slot UNIQUE (team_id, staff_slot),
    CONSTRAINT ck_ctt_team_staff_slot_role CHECK (
        (staff_slot = 1 AND role = 'director_tecnico')
        OR (staff_slot = 2 AND role = 'auxiliar')
    ),
    CONSTRAINT ck_ctt_team_staff_draft_version CHECK (governance_draft_version > 0),
    CONSTRAINT ck_ctt_team_staff_finality CHECK (
        governance_state = 'PENDING_FINALITY'
        OR finality_receipt_id IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS ix_ctt_registration_batches_edition ON copa_telmex_registration_batches(tournament_edition_id);
CREATE INDEX IF NOT EXISTS ix_ctt_registration_documents_batch ON copa_telmex_registration_batch_documents(batch_id);
CREATE INDEX IF NOT EXISTS ix_ctt_registration_documents_edition ON copa_telmex_registration_batch_documents(tournament_edition_id);
CREATE INDEX IF NOT EXISTS ix_ctt_team_staff_team ON copa_telmex_team_staff(team_id);

DO $grants$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'devnous_user') THEN
    REVOKE UPDATE, DELETE ON copa_telmex_registration_batches FROM devnous_user;
    REVOKE UPDATE, DELETE ON copa_telmex_registration_batch_documents FROM devnous_user;
    GRANT SELECT, INSERT ON copa_telmex_registration_batches TO devnous_user;
    GRANT SELECT, INSERT ON copa_telmex_registration_batch_documents TO devnous_user;
    GRANT SELECT, INSERT, UPDATE ON copa_telmex_team_staff TO devnous_user;
  END IF;
END
$grants$;

COMMIT;
