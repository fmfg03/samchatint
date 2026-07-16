BEGIN;

ALTER TABLE copa_telmex_registration_review_drafts
    ADD COLUMN IF NOT EXISTS page_manifest_hash varchar(71);

CREATE TABLE IF NOT EXISTS copa_telmex_registration_page_append_attempts (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL
        REFERENCES copa_telmex_registration_review_sessions(id),
    page_append_request_id uuid NOT NULL UNIQUE,
    base_draft_id uuid NOT NULL
        REFERENCES copa_telmex_registration_review_drafts(id),
    base_draft_version integer NOT NULL,
    base_content_hash varchar(71) NOT NULL,
    declared_base_page_manifest_hash varchar(71),
    operation_id varchar(71) NOT NULL UNIQUE,
    append_ocr_run_id uuid NOT NULL UNIQUE,
    pipeline_version varchar(80) NOT NULL,
    provider varchar(50) NOT NULL,
    model_identity json NOT NULL,
    prompt_config_hash varchar(71) NOT NULL,
    existing_page_manifest json NOT NULL,
    existing_page_manifest_hash varchar(71) NOT NULL,
    appended_page_manifest json NOT NULL,
    appended_page_manifest_hash varchar(71) NOT NULL,
    proposed_page_manifest json NOT NULL,
    proposed_page_manifest_hash varchar(71) NOT NULL,
    proposed_snapshot_hash varchar(71) NOT NULL,
    base_player_set_hash varchar(71) NOT NULL,
    incoming_player_set_hash varchar(71) NOT NULL,
    proposed_player_set_hash varchar(71) NOT NULL,
    incoming_extraction json NOT NULL,
    incoming_ocr_raw json NOT NULL,
    incoming_layout_regions json NOT NULL,
    proposed_extraction json NOT NULL,
    proposed_ocr_raw json NOT NULL,
    proposed_layout_regions json NOT NULL,
    proposed_validation json NOT NULL,
    staged_assets json NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_registration_page_append_base_version
        CHECK (base_draft_version > 0)
);

CREATE INDEX IF NOT EXISTS ix_registration_page_append_session
    ON copa_telmex_registration_page_append_attempts(session_id);
CREATE INDEX IF NOT EXISTS ix_registration_page_append_base
    ON copa_telmex_registration_page_append_attempts(base_draft_id);

CREATE TABLE IF NOT EXISTS copa_telmex_registration_page_append_decisions (
    id uuid PRIMARY KEY,
    page_append_attempt_id uuid NOT NULL UNIQUE
        REFERENCES copa_telmex_registration_page_append_attempts(id),
    successor_draft_id uuid
        REFERENCES copa_telmex_registration_review_drafts(id),
    decision_id varchar(71) NOT NULL UNIQUE,
    policy_hash varchar(71) NOT NULL,
    decision varchar(60) NOT NULL,
    reason_codes json NOT NULL,
    receipt_id varchar(120) NOT NULL,
    event_hash varchar(71) NOT NULL,
    issued_at timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_registration_page_append_decision
        CHECK (
            decision IN (
                'ACCEPT_NON_CONFLICTING_PAGE_APPEND',
                'REQUIRE_PAGE_COMPOSITION_REVIEW',
                'DENY_PAGE_COMPOSITION'
            )
        ),
    CONSTRAINT ck_registration_page_append_successor
        CHECK (
            (
                decision = 'ACCEPT_NON_CONFLICTING_PAGE_APPEND'
                AND successor_draft_id IS NOT NULL
            )
            OR
            (
                decision <> 'ACCEPT_NON_CONFLICTING_PAGE_APPEND'
                AND successor_draft_id IS NULL
            )
        ),
    CONSTRAINT ck_registration_page_append_window
        CHECK (expires_at > issued_at)
);

CREATE INDEX IF NOT EXISTS ix_registration_page_append_decision_attempt
    ON copa_telmex_registration_page_append_decisions(page_append_attempt_id);

ALTER TABLE copa_telmex_registration_review_assets
    ADD COLUMN IF NOT EXISTS page_append_attempt_id uuid
        REFERENCES copa_telmex_registration_page_append_attempts(id),
    ADD COLUMN IF NOT EXISTS admitted_draft_id uuid
        REFERENCES copa_telmex_registration_review_drafts(id),
    ADD COLUMN IF NOT EXISTS source_base_draft_id uuid,
    ADD COLUMN IF NOT EXISTS source_base_content_hash varchar(71),
    ADD COLUMN IF NOT EXISTS source_ocr_run_ref varchar(120),
    ADD COLUMN IF NOT EXISTS admission_operation_id varchar(120),
    ADD COLUMN IF NOT EXISTS admission_decision_id varchar(71),
    ADD COLUMN IF NOT EXISTS admission_receipt_id varchar(120);

CREATE INDEX IF NOT EXISTS ix_registration_review_asset_append_attempt
    ON copa_telmex_registration_review_assets(page_append_attempt_id);
CREATE INDEX IF NOT EXISTS ix_registration_review_asset_admitted_draft
    ON copa_telmex_registration_review_assets(admitted_draft_id);

WITH initial_drafts AS (
    SELECT DISTINCT ON (session_id)
        session_id,
        id,
        content_hash,
        mutation_operation_id,
        mutation_decision_id,
        mutation_receipt_id
    FROM copa_telmex_registration_review_drafts
    ORDER BY session_id, draft_version ASC, created_at ASC
)
UPDATE copa_telmex_registration_review_assets AS asset
SET
    admitted_draft_id = initial.id,
    source_base_draft_id = initial.id,
    source_base_content_hash = initial.content_hash,
    source_ocr_run_ref = 'legacy-initial:' || asset.id::text,
    admission_operation_id = initial.mutation_operation_id,
    admission_decision_id = initial.mutation_decision_id,
    admission_receipt_id = initial.mutation_receipt_id
FROM initial_drafts AS initial
WHERE initial.session_id = asset.session_id
  AND asset.admitted_draft_id IS NULL;

CREATE OR REPLACE FUNCTION reject_registration_page_composition_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'REG-S04: page append attempts and decisions are immutable';
END
$$;

DROP TRIGGER IF EXISTS trg_registration_page_append_attempt_immutable
    ON copa_telmex_registration_page_append_attempts;
CREATE TRIGGER trg_registration_page_append_attempt_immutable
BEFORE UPDATE OR DELETE ON copa_telmex_registration_page_append_attempts
FOR EACH ROW EXECUTE FUNCTION reject_registration_page_composition_mutation();

DROP TRIGGER IF EXISTS trg_registration_page_append_decision_immutable
    ON copa_telmex_registration_page_append_decisions;
CREATE TRIGGER trg_registration_page_append_decision_immutable
BEFORE UPDATE OR DELETE ON copa_telmex_registration_page_append_decisions
FOR EACH ROW EXECUTE FUNCTION reject_registration_page_composition_mutation();

CREATE OR REPLACE FUNCTION guard_registration_review_asset_immutability()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.admitted_draft_id IS NULL
           AND NEW.admitted_draft_id IS NOT NULL
           AND NEW.source_base_draft_id IS NOT NULL
           AND NEW.source_base_content_hash IS NOT NULL
           AND NEW.source_ocr_run_ref IS NOT NULL
           AND NEW.admission_operation_id IS NOT NULL
           AND NEW.admission_decision_id IS NOT NULL
           AND NEW.admission_receipt_id IS NOT NULL
           AND OLD.id = NEW.id
           AND OLD.session_id = NEW.session_id
           AND OLD.page_index = NEW.page_index
           AND OLD.image_path = NEW.image_path
           AND OLD.sha256 IS NOT DISTINCT FROM NEW.sha256
           AND OLD.width IS NOT DISTINCT FROM NEW.width
           AND OLD.height IS NOT DISTINCT FROM NEW.height
        THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION
            'REG-S04: admitted page assets cannot be replaced or rebound';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM copa_telmex_registration_review_sessions
        WHERE id = OLD.session_id
    ) THEN
        RAISE EXCEPTION
            'REG-S04: admitted page assets cannot be deleted independently';
    END IF;
    RETURN OLD;
END
$$;

DROP TRIGGER IF EXISTS trg_registration_review_asset_immutable
    ON copa_telmex_registration_review_assets;
CREATE TRIGGER trg_registration_review_asset_immutable
BEFORE UPDATE OR DELETE ON copa_telmex_registration_review_assets
FOR EACH ROW EXECUTE FUNCTION guard_registration_review_asset_immutability();

COMMENT ON TABLE copa_telmex_registration_page_append_attempts IS
    'REG-S04 immutable existing, appended and proposed page composition manifest.';
COMMENT ON TABLE copa_telmex_registration_page_append_decisions IS
    'REG-S04 receipt-bound page composition decision; only ACCEPT may create a REG-S02 successor.';

COMMIT;
