BEGIN;

CREATE TABLE IF NOT EXISTS copa_telmex_registration_human_field_edit_proposals (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL
        REFERENCES copa_telmex_registration_review_sessions(id),
    edit_request_id uuid NOT NULL UNIQUE,
    base_draft_id uuid NOT NULL
        REFERENCES copa_telmex_registration_review_drafts(id),
    base_draft_version integer NOT NULL,
    base_draft_hash varchar(71) NOT NULL,
    proposed_successor_draft_id uuid NOT NULL UNIQUE,
    proposed_successor_hash varchar(71) NOT NULL,
    operation_id varchar(71) NOT NULL UNIQUE,
    tournament_slug varchar(80) NOT NULL,
    registration_subject_binding varchar(80) NOT NULL,
    proposed_values json NOT NULL,
    resolutions json NOT NULL,
    field_resolution_set_hash varchar(71) NOT NULL,
    required_blocking_diff_ids json NOT NULL,
    required_blocking_diff_set_hash varchar(71) NOT NULL,
    approval_set_hash varchar(71) NOT NULL,
    proposer_principal_id varchar(120) NOT NULL,
    proposer_role varchar(60) NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_registration_human_edit_base_version
        CHECK (base_draft_version > 0)
);

CREATE INDEX IF NOT EXISTS ix_registration_human_edit_proposal_session
    ON copa_telmex_registration_human_field_edit_proposals(session_id);
CREATE INDEX IF NOT EXISTS ix_registration_human_edit_proposal_base
    ON copa_telmex_registration_human_field_edit_proposals(base_draft_id);

CREATE TABLE IF NOT EXISTS copa_telmex_registration_human_field_approvals (
    id uuid PRIMARY KEY,
    proposal_id uuid NOT NULL
        REFERENCES copa_telmex_registration_human_field_edit_proposals(id),
    nonce varchar(120) NOT NULL UNIQUE,
    roster_entry_id uuid,
    player_slot integer,
    field_path varchar(200) NOT NULL,
    resolution_type varchar(60) NOT NULL,
    evidence_class varchar(60) NOT NULL,
    previous_value_binding varchar(80) NOT NULL,
    previous_normalized_value_binding varchar(80) NOT NULL,
    proposed_value_binding varchar(80) NOT NULL,
    proposed_normalized_value_binding varchar(80) NOT NULL,
    source_page_artifact_id uuid
        REFERENCES copa_telmex_registration_review_assets(id),
    source_page_hash varchar(71),
    normalized_page_hash varchar(71),
    coordinate_frame_hash varchar(71),
    crop_coordinates json,
    crop_hash varchar(71),
    ocr_run_id uuid
        REFERENCES copa_telmex_registration_ocr_runs(id),
    reprocess_decision_id uuid
        REFERENCES copa_telmex_registration_ocr_reprocess_decisions(id),
    field_diff_id uuid
        REFERENCES copa_telmex_registration_ocr_field_diffs(id),
    classification varchar(60),
    approver_principal_id varchar(120) NOT NULL,
    approver_role varchar(60) NOT NULL,
    role_assignment_id varchar(160) NOT NULL,
    authorization_epoch varchar(160) NOT NULL,
    authentication_method varchar(80) NOT NULL,
    authentication_assurance_level integer NOT NULL,
    auth_context_id varchar(160) NOT NULL,
    issued_at timestamp without time zone NOT NULL,
    not_before timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT uq_registration_human_approval_proposal_field
        UNIQUE (proposal_id, field_path),
    CONSTRAINT ck_registration_human_approval_window
        CHECK (
            not_before >= issued_at
            AND expires_at > not_before
            AND expires_at <= issued_at + interval '10 minutes'
        ),
    CONSTRAINT ck_registration_human_approval_resolution
        CHECK (
            resolution_type IN (
                'KEEP_PREVIOUS_VALUE',
                'ACCEPT_REPROCESS_CANDIDATE',
                'ENTER_CORRECTED_VALUE',
                'CLEAR_FIELD'
            )
        )
);

CREATE INDEX IF NOT EXISTS ix_registration_human_approval_proposal
    ON copa_telmex_registration_human_field_approvals(proposal_id);
CREATE INDEX IF NOT EXISTS ix_registration_human_approval_roster_entry
    ON copa_telmex_registration_human_field_approvals(roster_entry_id);

CREATE TABLE IF NOT EXISTS copa_telmex_registration_human_field_edit_decisions (
    id uuid PRIMARY KEY,
    proposal_id uuid NOT NULL UNIQUE
        REFERENCES copa_telmex_registration_human_field_edit_proposals(id),
    decision_id varchar(71) NOT NULL UNIQUE,
    policy_hash varchar(71) NOT NULL,
    decision varchar(60) NOT NULL,
    reason_codes json NOT NULL,
    receipt_id varchar(120) NOT NULL,
    receipt_alg varchar(30) NOT NULL,
    event_hash varchar(71) NOT NULL,
    decision_document json NOT NULL,
    receipt_document json NOT NULL,
    issued_at timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_registration_human_edit_decision
        CHECK (
            decision IN (
                'AUTHORIZE_FIELD_EDIT_SUCCESSOR',
                'REQUIRE_ADDITIONAL_REVIEW',
                'DENY_FIELD_EDIT'
            )
        ),
    CONSTRAINT ck_registration_human_edit_receipt_alg
        CHECK (receipt_alg = 'Ed25519'),
    CONSTRAINT ck_registration_human_edit_decision_window
        CHECK (expires_at > issued_at)
);

CREATE TABLE IF NOT EXISTS copa_telmex_registration_human_field_edit_executions (
    id uuid PRIMARY KEY,
    proposal_id uuid NOT NULL UNIQUE
        REFERENCES copa_telmex_registration_human_field_edit_proposals(id),
    decision_id uuid NOT NULL UNIQUE
        REFERENCES copa_telmex_registration_human_field_edit_decisions(id),
    successor_draft_id uuid NOT NULL UNIQUE
        REFERENCES copa_telmex_registration_review_drafts(id)
        DEFERRABLE INITIALLY DEFERRED,
    successor_draft_version integer NOT NULL,
    successor_hash varchar(71) NOT NULL,
    parent_decision_id varchar(71) NOT NULL,
    parent_receipt_id varchar(120) NOT NULL,
    executed_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_registration_human_edit_execution_successor
    ON copa_telmex_registration_human_field_edit_executions(successor_draft_id);

CREATE TABLE IF NOT EXISTS copa_telmex_registration_human_field_approval_consumptions (
    id uuid PRIMARY KEY,
    approval_id uuid NOT NULL UNIQUE
        REFERENCES copa_telmex_registration_human_field_approvals(id),
    execution_id uuid NOT NULL
        REFERENCES copa_telmex_registration_human_field_edit_executions(id),
    consumed_by_principal_id varchar(120) NOT NULL,
    consumed_at timestamp without time zone NOT NULL DEFAULT now(),
    consumed_by_draft_version integer NOT NULL,
    consumed_by_successor_hash varchar(71) NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_registration_human_approval_consumption_execution
    ON copa_telmex_registration_human_field_approval_consumptions(execution_id);

CREATE OR REPLACE FUNCTION reject_registration_human_field_authority_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'REG-S05: proposals, approvals, decisions, executions and consumptions are immutable';
END
$$;

DROP TRIGGER IF EXISTS trg_registration_human_edit_proposal_immutable
    ON copa_telmex_registration_human_field_edit_proposals;
CREATE TRIGGER trg_registration_human_edit_proposal_immutable
BEFORE UPDATE OR DELETE
ON copa_telmex_registration_human_field_edit_proposals
FOR EACH ROW
EXECUTE FUNCTION reject_registration_human_field_authority_mutation();

DROP TRIGGER IF EXISTS trg_registration_human_approval_immutable
    ON copa_telmex_registration_human_field_approvals;
CREATE TRIGGER trg_registration_human_approval_immutable
BEFORE UPDATE OR DELETE
ON copa_telmex_registration_human_field_approvals
FOR EACH ROW
EXECUTE FUNCTION reject_registration_human_field_authority_mutation();

DROP TRIGGER IF EXISTS trg_registration_human_edit_decision_immutable
    ON copa_telmex_registration_human_field_edit_decisions;
CREATE TRIGGER trg_registration_human_edit_decision_immutable
BEFORE UPDATE OR DELETE
ON copa_telmex_registration_human_field_edit_decisions
FOR EACH ROW
EXECUTE FUNCTION reject_registration_human_field_authority_mutation();

DROP TRIGGER IF EXISTS trg_registration_human_edit_execution_immutable
    ON copa_telmex_registration_human_field_edit_executions;
CREATE TRIGGER trg_registration_human_edit_execution_immutable
BEFORE UPDATE OR DELETE
ON copa_telmex_registration_human_field_edit_executions
FOR EACH ROW
EXECUTE FUNCTION reject_registration_human_field_authority_mutation();

DROP TRIGGER IF EXISTS trg_registration_human_approval_consumption_immutable
    ON copa_telmex_registration_human_field_approval_consumptions;
CREATE TRIGGER trg_registration_human_approval_consumption_immutable
BEFORE UPDATE OR DELETE
ON copa_telmex_registration_human_field_approval_consumptions
FOR EACH ROW
EXECUTE FUNCTION reject_registration_human_field_authority_mutation();

CREATE OR REPLACE FUNCTION enforce_registration_human_edit_atomicity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    draft_row copa_telmex_registration_review_drafts%ROWTYPE;
    execution_row copa_telmex_registration_human_field_edit_executions%ROWTYPE;
    proposal_row copa_telmex_registration_human_field_edit_proposals%ROWTYPE;
    decision_row copa_telmex_registration_human_field_edit_decisions%ROWTYPE;
    approval_count integer;
    consumption_count integer;
BEGIN
    IF TG_TABLE_NAME = 'copa_telmex_registration_review_drafts' THEN
        IF NEW.mutation_type <> 'human_field_edit' THEN
            RETURN NEW;
        END IF;
        SELECT *
        INTO execution_row
        FROM copa_telmex_registration_human_field_edit_executions
        WHERE successor_draft_id = NEW.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'REG-S05: human field edit successor has no atomic execution';
        END IF;
        IF execution_row.successor_hash IS DISTINCT FROM NEW.content_hash
           OR execution_row.successor_draft_version IS DISTINCT FROM NEW.draft_version
           OR execution_row.parent_decision_id IS DISTINCT FROM NEW.parent_decision_id
           OR execution_row.parent_receipt_id IS DISTINCT FROM NEW.parent_receipt_id THEN
            RAISE EXCEPTION
                'REG-S05: human field edit execution does not bind the exact successor';
        END IF;
    ELSE
        SELECT *
        INTO proposal_row
        FROM copa_telmex_registration_human_field_edit_proposals
        WHERE id = NEW.proposal_id;
        SELECT *
        INTO decision_row
        FROM copa_telmex_registration_human_field_edit_decisions
        WHERE id = NEW.decision_id;
        IF proposal_row.id IS NULL
           OR decision_row.id IS NULL
           OR decision_row.proposal_id <> NEW.proposal_id
           OR decision_row.decision <> 'AUTHORIZE_FIELD_EDIT_SUCCESSOR'
           OR decision_row.receipt_alg <> 'Ed25519'
           OR decision_row.expires_at <= NEW.executed_at
           OR NEW.parent_decision_id <> decision_row.decision_id
           OR NEW.parent_receipt_id <> decision_row.receipt_id
           OR NEW.successor_draft_id <> proposal_row.proposed_successor_draft_id
           OR NEW.successor_hash <> proposal_row.proposed_successor_hash THEN
            RAISE EXCEPTION
                'REG-S05: execution is not bound to an active authorized proposal';
        END IF;
        SELECT *
        INTO draft_row
        FROM copa_telmex_registration_review_drafts
        WHERE id = NEW.successor_draft_id;
        IF NOT FOUND
           OR draft_row.mutation_type <> 'human_field_edit'
           OR draft_row.content_hash IS DISTINCT FROM NEW.successor_hash
           OR draft_row.draft_version IS DISTINCT FROM NEW.successor_draft_version
           OR draft_row.predecessor_draft_id IS DISTINCT FROM proposal_row.base_draft_id
           OR draft_row.predecessor_content_hash IS DISTINCT FROM proposal_row.base_draft_hash
           OR draft_row.draft_version IS DISTINCT FROM proposal_row.base_draft_version + 1
           OR draft_row.mutation_operation_id IS DISTINCT FROM proposal_row.operation_id
           OR draft_row.parent_decision_id IS DISTINCT FROM NEW.parent_decision_id
           OR draft_row.parent_receipt_id IS DISTINCT FROM NEW.parent_receipt_id THEN
            RAISE EXCEPTION
                'REG-S05: approval execution has no exact REG-S02 successor';
        END IF;
        SELECT count(*)
        INTO approval_count
        FROM copa_telmex_registration_human_field_approvals
        WHERE proposal_id = NEW.proposal_id;
        SELECT count(*)
        INTO consumption_count
        FROM copa_telmex_registration_human_field_approval_consumptions
        WHERE execution_id = NEW.id;
        IF approval_count = 0 OR consumption_count <> approval_count THEN
            RAISE EXCEPTION
                'REG-S05: approval set was not consumed exactly once with successor';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM copa_telmex_registration_human_field_approval_consumptions AS c
            JOIN copa_telmex_registration_human_field_approvals AS a
              ON a.id = c.approval_id
            WHERE c.execution_id = NEW.id
              AND (
                  a.proposal_id <> NEW.proposal_id
                  OR c.consumed_by_principal_id <> a.approver_principal_id
                  OR c.consumed_by_draft_version <> NEW.successor_draft_version
                  OR c.consumed_by_successor_hash <> NEW.successor_hash
              )
        ) THEN
            RAISE EXCEPTION
                'REG-S05: approval consumption binding does not match execution';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_registration_human_edit_draft_atomicity
    ON copa_telmex_registration_review_drafts;
CREATE CONSTRAINT TRIGGER trg_registration_human_edit_draft_atomicity
AFTER INSERT ON copa_telmex_registration_review_drafts
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
WHEN (NEW.mutation_type = 'human_field_edit')
EXECUTE FUNCTION enforce_registration_human_edit_atomicity();

DROP TRIGGER IF EXISTS trg_registration_human_edit_execution_atomicity
    ON copa_telmex_registration_human_field_edit_executions;
CREATE CONSTRAINT TRIGGER trg_registration_human_edit_execution_atomicity
AFTER INSERT ON copa_telmex_registration_human_field_edit_executions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION enforce_registration_human_edit_atomicity();

COMMENT ON TABLE copa_telmex_registration_human_field_edit_proposals IS
    'REG-S05 immutable exact human field edit proposal and successor binding.';
COMMENT ON TABLE copa_telmex_registration_human_field_approval_consumptions IS
    'REG-S05 one-time approval consumption committed atomically with REG-S02 successor.';

COMMIT;
