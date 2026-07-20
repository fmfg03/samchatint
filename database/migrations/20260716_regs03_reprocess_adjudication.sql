BEGIN;

ALTER TABLE copa_telmex_registration_review_drafts
    ADD COLUMN IF NOT EXISTS parent_decision_id varchar(71),
    ADD COLUMN IF NOT EXISTS parent_receipt_id varchar(120);

CREATE TABLE IF NOT EXISTS copa_telmex_registration_ocr_runs (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL
        REFERENCES copa_telmex_registration_review_sessions(id),
    base_draft_id uuid NOT NULL
        REFERENCES copa_telmex_registration_review_drafts(id),
    base_draft_version integer NOT NULL,
    base_content_hash varchar(71) NOT NULL,
    reprocess_request_id uuid NOT NULL UNIQUE,
    operation_id varchar(71) NOT NULL UNIQUE,
    run_fingerprint varchar(71) NOT NULL,
    pipeline_version varchar(80) NOT NULL,
    provider varchar(50) NOT NULL,
    model_identity json NOT NULL,
    prompt_config_hash varchar(71) NOT NULL,
    input_page_bindings json NOT NULL,
    input_page_set_hash varchar(71) NOT NULL,
    geometry_binding_hash varchar(71) NOT NULL,
    previous_evidence_set_hash varchar(71) NOT NULL,
    new_evidence_set_hash varchar(71) NOT NULL,
    proposed_snapshot_hash varchar(71) NOT NULL,
    field_diff_set_hash varchar(71) NOT NULL,
    field_diff_count integer NOT NULL,
    material_change_count integer NOT NULL,
    proposed_extraction json NOT NULL,
    proposed_ocr_raw json NOT NULL,
    proposed_layout_regions json NOT NULL,
    proposed_validation json NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_registration_ocr_run_base_version
        CHECK (base_draft_version > 0),
    CONSTRAINT ck_registration_ocr_run_diff_counts
        CHECK (
            field_diff_count >= 0
            AND material_change_count >= 0
            AND material_change_count <= field_diff_count
        )
);

CREATE INDEX IF NOT EXISTS ix_registration_ocr_run_session
    ON copa_telmex_registration_ocr_runs(session_id);
CREATE INDEX IF NOT EXISTS ix_registration_ocr_run_base_draft
    ON copa_telmex_registration_ocr_runs(base_draft_id);

CREATE TABLE IF NOT EXISTS copa_telmex_registration_ocr_field_diffs (
    id uuid PRIMARY KEY,
    ocr_run_id uuid NOT NULL
        REFERENCES copa_telmex_registration_ocr_runs(id),
    field_path varchar(160) NOT NULL,
    player_slot integer,
    source_page integer,
    classification varchar(50) NOT NULL,
    previous_value json,
    proposed_value json,
    previous_value_present boolean NOT NULL,
    proposed_value_present boolean NOT NULL,
    previous_value_binding varchar(71) NOT NULL,
    proposed_value_binding varchar(71) NOT NULL,
    previous_normalized_value_binding varchar(71) NOT NULL,
    proposed_normalized_value_binding varchar(71) NOT NULL,
    previous_evidence_binding varchar(71) NOT NULL,
    new_evidence_binding varchar(71) NOT NULL,
    evidence_binding_changed boolean NOT NULL DEFAULT false,
    requires_review boolean NOT NULL DEFAULT false,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT uq_registration_ocr_diff_run_field
        UNIQUE (ocr_run_id, field_path),
    CONSTRAINT ck_registration_ocr_diff_classification
        CHECK (
            classification IN (
                'UNCHANGED',
                'EMPTY_TO_VALUE',
                'VALUE_TO_EMPTY',
                'MATERIAL_CHANGE',
                'NORMALIZATION_ONLY_CHANGE',
                'EVIDENCE_BINDING_CHANGED'
            )
        )
);

CREATE INDEX IF NOT EXISTS ix_registration_ocr_diff_run
    ON copa_telmex_registration_ocr_field_diffs(ocr_run_id);

CREATE TABLE IF NOT EXISTS copa_telmex_registration_ocr_reprocess_decisions (
    id uuid PRIMARY KEY,
    ocr_run_id uuid NOT NULL UNIQUE
        REFERENCES copa_telmex_registration_ocr_runs(id),
    successor_draft_id uuid
        REFERENCES copa_telmex_registration_review_drafts(id),
    decision_id varchar(71) NOT NULL UNIQUE,
    policy_hash varchar(71) NOT NULL,
    decision varchar(50) NOT NULL,
    reason_codes json NOT NULL,
    receipt_id varchar(120) NOT NULL,
    event_hash varchar(71) NOT NULL,
    issued_at timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_registration_ocr_reprocess_decision
        CHECK (
            decision IN (
                'ACCEPT_NON_CONFLICTING_REPROCESS',
                'REQUIRE_FIELD_REVIEW',
                'REQUIRE_ROSTER_REVIEW',
                'DENY_REPROCESS_SUCCESSOR'
            )
        ),
    CONSTRAINT ck_registration_ocr_reprocess_successor
        CHECK (
            (
                decision = 'ACCEPT_NON_CONFLICTING_REPROCESS'
                AND successor_draft_id IS NOT NULL
            )
            OR
            (
                decision <> 'ACCEPT_NON_CONFLICTING_REPROCESS'
                AND successor_draft_id IS NULL
            )
        ),
    CONSTRAINT ck_registration_ocr_reprocess_window
        CHECK (expires_at > issued_at)
);

CREATE OR REPLACE FUNCTION reject_registration_ocr_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'REG-S03: OCR runs, field diffs and reprocess decisions are immutable';
END
$$;

DROP TRIGGER IF EXISTS trg_registration_ocr_run_immutable
    ON copa_telmex_registration_ocr_runs;
CREATE TRIGGER trg_registration_ocr_run_immutable
BEFORE UPDATE OR DELETE ON copa_telmex_registration_ocr_runs
FOR EACH ROW EXECUTE FUNCTION reject_registration_ocr_evidence_mutation();

DROP TRIGGER IF EXISTS trg_registration_ocr_diff_immutable
    ON copa_telmex_registration_ocr_field_diffs;
CREATE TRIGGER trg_registration_ocr_diff_immutable
BEFORE UPDATE OR DELETE ON copa_telmex_registration_ocr_field_diffs
FOR EACH ROW EXECUTE FUNCTION reject_registration_ocr_evidence_mutation();

DROP TRIGGER IF EXISTS trg_registration_ocr_decision_immutable
    ON copa_telmex_registration_ocr_reprocess_decisions;
CREATE TRIGGER trg_registration_ocr_decision_immutable
BEFORE UPDATE OR DELETE ON copa_telmex_registration_ocr_reprocess_decisions
FOR EACH ROW EXECUTE FUNCTION reject_registration_ocr_evidence_mutation();

COMMENT ON TABLE copa_telmex_registration_ocr_runs IS
    'REG-S03 immutable identity and output for each OCR reprocess execution.';
COMMENT ON TABLE copa_telmex_registration_ocr_field_diffs IS
    'REG-S03 field-level old/new values, evidence bindings and deterministic classification.';
COMMENT ON TABLE copa_telmex_registration_ocr_reprocess_decisions IS
    'REG-S03 receipt-bound decision; only ACCEPT may reference a REG-S02 successor.';

COMMIT;
