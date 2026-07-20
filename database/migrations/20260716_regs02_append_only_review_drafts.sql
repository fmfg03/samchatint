BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE copa_telmex_registration_review_drafts
    ADD COLUMN IF NOT EXISTS predecessor_draft_id uuid,
    ADD COLUMN IF NOT EXISTS predecessor_content_hash varchar(71),
    ADD COLUMN IF NOT EXISTS content_hash varchar(71),
    ADD COLUMN IF NOT EXISTS mutation_type varchar(80),
    ADD COLUMN IF NOT EXISTS mutation_actor_binding varchar(71),
    ADD COLUMN IF NOT EXISTS mutation_operation_id varchar(80),
    ADD COLUMN IF NOT EXISTS mutation_decision_id varchar(71),
    ADD COLUMN IF NOT EXISTS mutation_receipt_id varchar(120);

UPDATE copa_telmex_registration_review_drafts
SET
    content_hash = 'sha256:' || encode(
        digest(
            convert_to(
                jsonb_build_object(
                    'ocr_raw', ocr_raw,
                    'extraction', extraction,
                    'validation', validation,
                    'review_edits', review_edits,
                    'layout_regions', layout_regions,
                    'overall_confidence', COALESCE(overall_confidence, 0.0),
                    'needs_review', COALESCE(needs_review, true)
                )::text,
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    ),
    mutation_type = COALESCE(mutation_type, 'legacy_snapshot_imported'),
    mutation_operation_id = COALESCE(
        mutation_operation_id,
        'legacy-' || replace(id::text, '-', '')
    ),
    mutation_decision_id = COALESCE(
        mutation_decision_id,
        'sha256:' || encode(digest(convert_to(id::text, 'UTF8'), 'sha256'), 'hex')
    ),
    mutation_receipt_id = COALESCE(
        mutation_receipt_id,
        'legacy-import:' || id::text
    )
WHERE
    content_hash IS NULL
    OR mutation_type IS NULL
    OR mutation_operation_id IS NULL
    OR mutation_decision_id IS NULL
    OR mutation_receipt_id IS NULL;

DO $$
DECLARE
    constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'copa_telmex_registration_review_drafts'::regclass
          AND contype = 'u'
          AND pg_get_constraintdef(oid) = 'UNIQUE (session_id)'
    LOOP
        EXECUTE format(
            'ALTER TABLE copa_telmex_registration_review_drafts DROP CONSTRAINT %I',
            constraint_name
        );
    END LOOP;
END
$$;

DROP INDEX IF EXISTS ix_copa_telmex_registration_review_drafts_session_id;

ALTER TABLE copa_telmex_registration_review_drafts
    ALTER COLUMN content_hash SET NOT NULL,
    ALTER COLUMN mutation_type SET NOT NULL,
    ALTER COLUMN mutation_operation_id SET NOT NULL,
    ALTER COLUMN mutation_decision_id SET NOT NULL,
    ALTER COLUMN mutation_receipt_id SET NOT NULL;

ALTER TABLE copa_telmex_registration_review_drafts
    ADD CONSTRAINT fk_registration_review_draft_predecessor
        FOREIGN KEY (predecessor_draft_id)
        REFERENCES copa_telmex_registration_review_drafts(id),
    ADD CONSTRAINT uq_registration_review_draft_session_version
        UNIQUE (session_id, draft_version),
    ADD CONSTRAINT uq_registration_review_draft_operation
        UNIQUE (mutation_operation_id),
    ADD CONSTRAINT ck_registration_review_draft_version_positive
        CHECK (draft_version > 0),
    ADD CONSTRAINT ck_registration_review_draft_content_hash
        CHECK (content_hash ~ '^sha256:[0-9a-f]{64}$'),
    ADD CONSTRAINT ck_registration_review_draft_predecessor
        CHECK (
            (draft_version = 1 AND predecessor_draft_id IS NULL
                AND predecessor_content_hash IS NULL)
            OR
            (draft_version > 1 AND predecessor_draft_id IS NOT NULL
                AND predecessor_content_hash ~ '^sha256:[0-9a-f]{64}$')
        );

CREATE OR REPLACE FUNCTION enforce_registration_review_draft_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    previous_row copa_telmex_registration_review_drafts%ROWTYPE;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        RAISE EXCEPTION
            'REG-S02: registration review drafts are immutable; append a successor';
    END IF;

    PERFORM 1
    FROM copa_telmex_registration_review_sessions
    WHERE id = NEW.session_id
    FOR UPDATE;

    SELECT *
    INTO previous_row
    FROM copa_telmex_registration_review_drafts
    WHERE session_id = NEW.session_id
    ORDER BY draft_version DESC, created_at DESC
    LIMIT 1;

    IF NOT FOUND THEN
        IF NEW.draft_version <> 1
           OR NEW.predecessor_draft_id IS NOT NULL
           OR NEW.predecessor_content_hash IS NOT NULL THEN
            RAISE EXCEPTION
                'REG-S02: initial draft must be version 1 without predecessor';
        END IF;
    ELSIF NEW.draft_version <> previous_row.draft_version + 1
       OR NEW.predecessor_draft_id IS DISTINCT FROM previous_row.id
       OR NEW.predecessor_content_hash IS DISTINCT FROM previous_row.content_hash THEN
        RAISE EXCEPTION
            'REG-S02: stale or discontinuous draft successor';
    END IF;

    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_registration_review_draft_append_only
    ON copa_telmex_registration_review_drafts;

CREATE TRIGGER trg_registration_review_draft_append_only
BEFORE INSERT OR UPDATE OR DELETE
ON copa_telmex_registration_review_drafts
FOR EACH ROW
EXECUTE FUNCTION enforce_registration_review_draft_append_only();

COMMENT ON TABLE copa_telmex_registration_review_drafts IS
    'REG-S02 append-only evidence chain: every authorized write inserts a new version.';

COMMIT;
