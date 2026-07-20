BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE copa_telmex_teams
    ADD COLUMN IF NOT EXISTS postcommit_revision integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS postcommit_snapshot_hash varchar(71)
        NOT NULL DEFAULT ('sha256:' || repeat('0', 64));

ALTER TABLE copa_telmex_players
    ADD COLUMN IF NOT EXISTS postcommit_revision integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS postcommit_snapshot_hash varchar(71)
        NOT NULL DEFAULT ('sha256:' || repeat('0', 64));

CREATE OR REPLACE FUNCTION regs07_team_snapshot(row_value copa_telmex_teams)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT jsonb_build_object(
        'entity_type', 'TEAM',
        'id', row_value.id::text,
        'name', row_value.name,
        'tournament_slug', row_value.tournament_slug,
        'gender', row_value.gender,
        'category', row_value.category,
        'league', row_value.league,
        'league_phone', row_value.league_phone,
        'league_address', row_value.league_address,
        'representative_name', row_value.representative_name,
        'contact_email', row_value.contact_email,
        'contact_phone', row_value.contact_phone,
        'state', row_value.state,
        'municipality', row_value.municipality,
        'roster_image_path', row_value.roster_image_path,
        'telegram_chat_id', row_value.telegram_chat_id,
        'telegram_user_id', row_value.telegram_user_id
    )
$$;

CREATE OR REPLACE FUNCTION regs07_player_snapshot(row_value copa_telmex_players)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT jsonb_build_object(
        'entity_type', 'PLAYER',
        'id', row_value.id::text,
        'team_id', row_value.team_id::text,
        'first_name', row_value.first_name,
        'last_name', row_value.last_name,
        'birth_date', CASE
            WHEN row_value.birth_date IS NULL THEN NULL
            ELSE to_char(row_value.birth_date, 'YYYY-MM-DD')
        END,
        'curp', row_value.curp,
        'email', row_value.email,
        'photo_path', row_value.photo_path,
        'photo_data_hash', 'sha256:' || encode(
            digest(convert_to(coalesce(row_value.photo_data, ''), 'UTF8'), 'sha256'),
            'hex'
        ),
        'photo_sha256', row_value.photo_sha256,
        'photo_ahash', row_value.photo_ahash,
        'curp_valid', coalesce(row_value.curp_valid, false),
        'curp_validation_date', CASE
            WHEN row_value.curp_validation_date IS NULL THEN NULL
            ELSE to_char(
                row_value.curp_validation_date,
                'YYYY-MM-DD"T"HH24:MI:SS.US'
            )
        END,
        'curp_validation_errors', row_value.curp_validation_errors,
        'ocr_confidence', row_value.ocr_confidence,
        'needs_review', coalesce(row_value.needs_review, false),
        'verified_by_human', coalesce(row_value.verified_by_human, false),
        'verification_notes', row_value.verification_notes,
        'roster_index', row_value.roster_index,
        'governance_state', row_value.governance_state,
        'governance_draft_id', row_value.governance_draft_id,
        'governance_draft_version', row_value.governance_draft_version,
        'governance_decision_id', row_value.governance_decision_id,
        'roster_draft_binding', row_value.roster_draft_binding,
        'preauthorization_receipt_id',
            row_value.preauthorization_receipt_id,
        'finality_receipt_id', row_value.finality_receipt_id
    )
$$;

CREATE OR REPLACE FUNCTION regs07_canonical_jsonb(value jsonb)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
DECLARE
    rendered text;
BEGIN
    CASE jsonb_typeof(value)
        WHEN 'object' THEN
            SELECT '{' || COALESCE(
                string_agg(
                    to_jsonb(entry.key)::text || ':' ||
                        regs07_canonical_jsonb(entry.value),
                    ',' ORDER BY convert_to(entry.key, 'UTF8')
                ),
                ''
            ) || '}'
            INTO rendered
            FROM jsonb_each(value) AS entry;
            RETURN rendered;
        WHEN 'array' THEN
            SELECT '[' || COALESCE(
                string_agg(
                    regs07_canonical_jsonb(item.value),
                    ',' ORDER BY item.ordinality
                ),
                ''
            ) || ']'
            INTO rendered
            FROM jsonb_array_elements(value)
                WITH ORDINALITY AS item(value, ordinality);
            RETURN rendered;
        ELSE
            RETURN value::text;
    END CASE;
END
$$;

CREATE OR REPLACE FUNCTION regs07_snapshot_hash(snapshot jsonb)
RETURNS varchar(71)
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT 'sha256:' || encode(
        digest(
            convert_to(regs07_canonical_jsonb(snapshot), 'UTF8'),
            'sha256'
        ),
        'hex'
    )
$$;

CREATE TABLE IF NOT EXISTS
copa_telmex_registration_postcommit_mutation_proposals (
    id uuid PRIMARY KEY,
    mutation_request_id uuid NOT NULL UNIQUE,
    entity_type varchar(20) NOT NULL,
    entity_id uuid NOT NULL,
    team_id uuid NOT NULL,
    mutation_type varchar(40) NOT NULL,
    base_revision integer NOT NULL,
    proposed_revision integer NOT NULL,
    base_snapshot jsonb NOT NULL,
    base_snapshot_hash varchar(71) NOT NULL,
    proposed_snapshot jsonb NOT NULL,
    proposed_snapshot_hash varchar(71) NOT NULL,
    field_changes jsonb NOT NULL,
    field_change_set_hash varchar(71) NOT NULL,
    mutation_reason text NOT NULL,
    mutation_reason_binding varchar(80) NOT NULL,
    source_evidence_binding varchar(80) NOT NULL,
    proposer_principal_id varchar(120) NOT NULL,
    proposer_role varchar(60) NOT NULL,
    role_assignment_id varchar(160) NOT NULL,
    authorization_epoch varchar(160) NOT NULL,
    authentication_method varchar(80) NOT NULL,
    authentication_assurance_level integer NOT NULL,
    auth_context_id varchar(160) NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_registration_postcommit_entity_type
        CHECK (entity_type IN ('TEAM', 'PLAYER')),
    CONSTRAINT ck_registration_postcommit_mutation_type
        CHECK (
            mutation_type IN ('EDIT_TEAM', 'EDIT_PLAYER', 'VERIFY_PLAYER')
        ),
    CONSTRAINT ck_registration_postcommit_revision
        CHECK (
            base_revision >= 1
            AND proposed_revision = base_revision + 1
        ),
    CONSTRAINT ck_registration_postcommit_reason
        CHECK (
            length(btrim(mutation_reason)) BETWEEN 5 AND 500
        )
);

CREATE INDEX IF NOT EXISTS ix_registration_postcommit_proposal_entity
    ON copa_telmex_registration_postcommit_mutation_proposals(
        entity_type, entity_id, base_revision
    );
CREATE INDEX IF NOT EXISTS ix_registration_postcommit_proposal_team
    ON copa_telmex_registration_postcommit_mutation_proposals(team_id);

CREATE TABLE IF NOT EXISTS
copa_telmex_registration_postcommit_mutation_decisions (
    id uuid PRIMARY KEY,
    proposal_id uuid NOT NULL UNIQUE
        REFERENCES copa_telmex_registration_postcommit_mutation_proposals(id),
    decision_id varchar(71) NOT NULL UNIQUE,
    policy_hash varchar(71) NOT NULL,
    decision varchar(60) NOT NULL,
    reason_codes jsonb NOT NULL,
    receipt_id varchar(120) NOT NULL,
    receipt_alg varchar(30) NOT NULL,
    event_hash varchar(71) NOT NULL,
    decision_document jsonb NOT NULL,
    receipt_document jsonb NOT NULL,
    issued_at timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_registration_postcommit_decision
        CHECK (
            decision IN (
                'AUTHORIZE_POSTCOMMIT_MUTATION',
                'REQUIRE_ADDITIONAL_REVIEW',
                'DENY_POSTCOMMIT_MUTATION'
            )
        ),
    CONSTRAINT ck_registration_postcommit_decision_receipt_alg
        CHECK (receipt_alg = 'Ed25519'),
    CONSTRAINT ck_registration_postcommit_decision_window
        CHECK (expires_at > issued_at)
);

CREATE TABLE IF NOT EXISTS
copa_telmex_registration_postcommit_mutation_executions (
    id uuid PRIMARY KEY,
    proposal_id uuid NOT NULL UNIQUE
        REFERENCES copa_telmex_registration_postcommit_mutation_proposals(id),
    decision_id uuid NOT NULL UNIQUE
        REFERENCES copa_telmex_registration_postcommit_mutation_decisions(id),
    database_transaction_id varchar(120) NOT NULL UNIQUE,
    attestation_id varchar(71) NOT NULL UNIQUE,
    attestation_hash varchar(71) NOT NULL,
    finality_receipt_id varchar(120) NOT NULL,
    finality_receipt_alg varchar(30) NOT NULL,
    finality_event_document jsonb NOT NULL,
    finality_receipt_document jsonb NOT NULL,
    executed_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT ck_registration_postcommit_finality_receipt_alg
        CHECK (finality_receipt_alg = 'Ed25519')
);

CREATE TABLE IF NOT EXISTS
copa_telmex_registration_postcommit_entity_versions (
    id uuid PRIMARY KEY,
    entity_type varchar(20) NOT NULL,
    entity_id uuid NOT NULL,
    team_id uuid NOT NULL,
    revision integer NOT NULL,
    snapshot jsonb NOT NULL,
    snapshot_hash varchar(71) NOT NULL,
    predecessor_snapshot_hash varchar(71),
    mutation_type varchar(40) NOT NULL,
    execution_id uuid UNIQUE
        REFERENCES copa_telmex_registration_postcommit_mutation_executions(id),
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT uq_registration_postcommit_entity_revision
        UNIQUE (entity_type, entity_id, revision),
    CONSTRAINT ck_registration_postcommit_version_entity_type
        CHECK (entity_type IN ('TEAM', 'PLAYER')),
    CONSTRAINT ck_registration_postcommit_version_revision
        CHECK (revision >= 1)
);

CREATE INDEX IF NOT EXISTS ix_registration_postcommit_version_entity
    ON copa_telmex_registration_postcommit_entity_versions(
        entity_type, entity_id, revision
    );
CREATE INDEX IF NOT EXISTS ix_registration_postcommit_version_team
    ON copa_telmex_registration_postcommit_entity_versions(team_id);

UPDATE copa_telmex_teams
SET postcommit_revision = 1,
    postcommit_snapshot_hash = regs07_snapshot_hash(
        regs07_team_snapshot(copa_telmex_teams)
    );

UPDATE copa_telmex_players
SET postcommit_revision = CASE
        WHEN governance_state = 'PENDING_FINALITY' THEN 0
        ELSE 1
    END,
    postcommit_snapshot_hash = regs07_snapshot_hash(
        regs07_player_snapshot(copa_telmex_players)
    );

INSERT INTO copa_telmex_registration_postcommit_entity_versions (
    id,
    entity_type,
    entity_id,
    team_id,
    revision,
    snapshot,
    snapshot_hash,
    predecessor_snapshot_hash,
    mutation_type,
    execution_id
)
SELECT
    gen_random_uuid(),
    'TEAM',
    team.id,
    team.id,
    1,
    regs07_team_snapshot(team),
    team.postcommit_snapshot_hash,
    NULL,
    'GENESIS',
    NULL
FROM copa_telmex_teams AS team
ON CONFLICT (entity_type, entity_id, revision) DO NOTHING;

INSERT INTO copa_telmex_registration_postcommit_entity_versions (
    id,
    entity_type,
    entity_id,
    team_id,
    revision,
    snapshot,
    snapshot_hash,
    predecessor_snapshot_hash,
    mutation_type,
    execution_id
)
SELECT
    gen_random_uuid(),
    'PLAYER',
    player.id,
    player.team_id,
    1,
    regs07_player_snapshot(player),
    player.postcommit_snapshot_hash,
    NULL,
    'GENESIS',
    NULL
FROM copa_telmex_players AS player
WHERE player.postcommit_revision = 1
ON CONFLICT (entity_type, entity_id, revision) DO NOTHING;

CREATE OR REPLACE FUNCTION reject_regs07_authority_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'REG-S07: proposals, decisions, executions and versions are immutable';
END
$$;

DROP TRIGGER IF EXISTS trg_regs07_proposal_immutable
    ON copa_telmex_registration_postcommit_mutation_proposals;
CREATE TRIGGER trg_regs07_proposal_immutable
BEFORE UPDATE OR DELETE
ON copa_telmex_registration_postcommit_mutation_proposals
FOR EACH ROW EXECUTE FUNCTION reject_regs07_authority_mutation();

DROP TRIGGER IF EXISTS trg_regs07_decision_immutable
    ON copa_telmex_registration_postcommit_mutation_decisions;
CREATE TRIGGER trg_regs07_decision_immutable
BEFORE UPDATE OR DELETE
ON copa_telmex_registration_postcommit_mutation_decisions
FOR EACH ROW EXECUTE FUNCTION reject_regs07_authority_mutation();

DROP TRIGGER IF EXISTS trg_regs07_execution_immutable
    ON copa_telmex_registration_postcommit_mutation_executions;
CREATE TRIGGER trg_regs07_execution_immutable
BEFORE UPDATE OR DELETE
ON copa_telmex_registration_postcommit_mutation_executions
FOR EACH ROW EXECUTE FUNCTION reject_regs07_authority_mutation();

DROP TRIGGER IF EXISTS trg_regs07_version_immutable
    ON copa_telmex_registration_postcommit_entity_versions;
CREATE TRIGGER trg_regs07_version_immutable
BEFORE UPDATE OR DELETE
ON copa_telmex_registration_postcommit_entity_versions
FOR EACH ROW EXECUTE FUNCTION reject_regs07_authority_mutation();

CREATE OR REPLACE FUNCTION regs07_initialize_team()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.postcommit_revision := 1;
    NEW.postcommit_snapshot_hash :=
        regs07_snapshot_hash(regs07_team_snapshot(NEW));
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION regs07_initialize_player()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.postcommit_revision := CASE
        WHEN NEW.governance_state = 'PENDING_FINALITY' THEN 0
        ELSE 1
    END;
    NEW.postcommit_snapshot_hash :=
        regs07_snapshot_hash(regs07_player_snapshot(NEW));
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION regs07_append_genesis()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    snapshot_value jsonb;
BEGIN
    IF TG_TABLE_NAME = 'copa_telmex_teams' THEN
        snapshot_value := regs07_team_snapshot(NEW);
        INSERT INTO copa_telmex_registration_postcommit_entity_versions (
            id, entity_type, entity_id, team_id, revision, snapshot,
            snapshot_hash, predecessor_snapshot_hash, mutation_type
        ) VALUES (
            gen_random_uuid(), 'TEAM', NEW.id, NEW.id, 1, snapshot_value,
            NEW.postcommit_snapshot_hash, NULL, 'GENESIS'
        );
    ELSIF NEW.postcommit_revision = 1 THEN
        snapshot_value := regs07_player_snapshot(NEW);
        INSERT INTO copa_telmex_registration_postcommit_entity_versions (
            id, entity_type, entity_id, team_id, revision, snapshot,
            snapshot_hash, predecessor_snapshot_hash, mutation_type
        ) VALUES (
            gen_random_uuid(), 'PLAYER', NEW.id, NEW.team_id, 1,
            snapshot_value, NEW.postcommit_snapshot_hash, NULL, 'GENESIS'
        );
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION regs07_guard_committed_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    execution_uuid uuid;
    proposal_row
        copa_telmex_registration_postcommit_mutation_proposals%ROWTYPE;
    decision_row
        copa_telmex_registration_postcommit_mutation_decisions%ROWTYPE;
    execution_row
        copa_telmex_registration_postcommit_mutation_executions%ROWTYPE;
    old_snapshot jsonb;
    new_snapshot jsonb;
    entity_kind text;
    entity_team_id uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'REG-S07: physical deletion of committed Team/Player evidence is denied';
    END IF;

    IF TG_TABLE_NAME = 'copa_telmex_players' THEN
        IF OLD.governance_state = 'PENDING_FINALITY'
           AND NEW.governance_state = 'ACTIVE'
           AND OLD.postcommit_revision = 0
           AND (
                to_jsonb(OLD) - ARRAY[
                    'governance_state',
                    'finality_receipt_id',
                    'updated_at',
                    'postcommit_revision',
                    'postcommit_snapshot_hash'
                ]
           ) = (
                to_jsonb(NEW) - ARRAY[
                    'governance_state',
                    'finality_receipt_id',
                    'updated_at',
                    'postcommit_revision',
                    'postcommit_snapshot_hash'
                ]
           ) THEN
            NEW.postcommit_revision := 1;
            NEW.postcommit_snapshot_hash :=
                regs07_snapshot_hash(regs07_player_snapshot(NEW));
            RETURN NEW;
        END IF;
    END IF;

    BEGIN
        execution_uuid := nullif(
            current_setting('samchat.regs07_execution_id', true), ''
        )::uuid;
    EXCEPTION WHEN OTHERS THEN
        execution_uuid := NULL;
    END;
    IF execution_uuid IS NULL THEN
        RAISE EXCEPTION
            'REG-S07: committed Team/Player update lacks execution authority';
    END IF;

    SELECT * INTO execution_row
    FROM copa_telmex_registration_postcommit_mutation_executions
    WHERE id = execution_uuid;
    SELECT * INTO proposal_row
    FROM copa_telmex_registration_postcommit_mutation_proposals
    WHERE id = execution_row.proposal_id;
    SELECT * INTO decision_row
    FROM copa_telmex_registration_postcommit_mutation_decisions
    WHERE id = execution_row.decision_id;

    IF TG_TABLE_NAME = 'copa_telmex_teams' THEN
        entity_kind := 'TEAM';
        entity_team_id := NEW.id;
        old_snapshot := regs07_team_snapshot(OLD);
        new_snapshot := regs07_team_snapshot(NEW);
    ELSE
        entity_kind := 'PLAYER';
        entity_team_id := NEW.team_id;
        old_snapshot := regs07_player_snapshot(OLD);
        new_snapshot := regs07_player_snapshot(NEW);
    END IF;

    IF execution_row.id IS NULL
       OR proposal_row.id IS NULL
       OR decision_row.id IS NULL
       OR proposal_row.entity_type <> entity_kind
       OR proposal_row.entity_id <> NEW.id
       OR proposal_row.team_id <> entity_team_id
       OR proposal_row.base_revision <> OLD.postcommit_revision
       OR proposal_row.proposed_revision <> OLD.postcommit_revision + 1
       OR proposal_row.base_snapshot_hash <> OLD.postcommit_snapshot_hash
       OR proposal_row.base_snapshot <> old_snapshot
       OR proposal_row.proposed_snapshot <> new_snapshot
       OR proposal_row.proposed_snapshot_hash <>
            regs07_snapshot_hash(new_snapshot)
       OR decision_row.proposal_id <> proposal_row.id
       OR decision_row.decision <> 'AUTHORIZE_POSTCOMMIT_MUTATION'
       OR decision_row.receipt_alg <> 'Ed25519'
       OR decision_row.expires_at <= execution_row.executed_at
       OR execution_row.finality_receipt_alg <> 'Ed25519'
       OR execution_row.finality_event_document->>'decision' <>
            'ATTEST_POSTCOMMIT_MUTATION'
       OR execution_row.finality_event_document->>'proposal_id' <>
            proposal_row.id::text
       OR execution_row.finality_event_document->>'actual_projection_hash' <>
            proposal_row.proposed_snapshot_hash THEN
        RAISE EXCEPTION
            'REG-S07: update is not bound to an exact live double-receipt successor';
    END IF;

    NEW.postcommit_revision := proposal_row.proposed_revision;
    NEW.postcommit_snapshot_hash := proposal_row.proposed_snapshot_hash;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION regs07_append_committed_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    execution_uuid uuid;
    proposal_row
        copa_telmex_registration_postcommit_mutation_proposals%ROWTYPE;
    snapshot_value jsonb;
    entity_kind text;
    entity_team_id uuid;
BEGIN
    IF TG_TABLE_NAME = 'copa_telmex_players'
       AND OLD.postcommit_revision = 0
       AND NEW.postcommit_revision = 1 THEN
        INSERT INTO copa_telmex_registration_postcommit_entity_versions (
            id, entity_type, entity_id, team_id, revision, snapshot,
            snapshot_hash, predecessor_snapshot_hash, mutation_type
        ) VALUES (
            gen_random_uuid(), 'PLAYER', NEW.id, NEW.team_id, 1,
            regs07_player_snapshot(NEW), NEW.postcommit_snapshot_hash,
            NULL, 'GENESIS'
        );
        RETURN NEW;
    END IF;

    execution_uuid := nullif(
        current_setting('samchat.regs07_execution_id', true), ''
    )::uuid;
    SELECT * INTO proposal_row
    FROM copa_telmex_registration_postcommit_mutation_proposals
    WHERE id = (
        SELECT proposal_id
        FROM copa_telmex_registration_postcommit_mutation_executions
        WHERE id = execution_uuid
    );
    IF TG_TABLE_NAME = 'copa_telmex_teams' THEN
        entity_kind := 'TEAM';
        entity_team_id := NEW.id;
        snapshot_value := regs07_team_snapshot(NEW);
    ELSE
        entity_kind := 'PLAYER';
        entity_team_id := NEW.team_id;
        snapshot_value := regs07_player_snapshot(NEW);
    END IF;
    INSERT INTO copa_telmex_registration_postcommit_entity_versions (
        id, entity_type, entity_id, team_id, revision, snapshot,
        snapshot_hash, predecessor_snapshot_hash, mutation_type, execution_id
    ) VALUES (
        gen_random_uuid(), entity_kind, NEW.id, entity_team_id,
        NEW.postcommit_revision, snapshot_value,
        NEW.postcommit_snapshot_hash, OLD.postcommit_snapshot_hash,
        proposal_row.mutation_type, execution_uuid
    );
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_regs07_team_initialize
    ON copa_telmex_teams;
CREATE TRIGGER trg_regs07_team_initialize
BEFORE INSERT ON copa_telmex_teams
FOR EACH ROW EXECUTE FUNCTION regs07_initialize_team();

DROP TRIGGER IF EXISTS trg_regs07_player_initialize
    ON copa_telmex_players;
CREATE TRIGGER trg_regs07_player_initialize
BEFORE INSERT ON copa_telmex_players
FOR EACH ROW EXECUTE FUNCTION regs07_initialize_player();

DROP TRIGGER IF EXISTS trg_regs07_team_genesis
    ON copa_telmex_teams;
CREATE TRIGGER trg_regs07_team_genesis
AFTER INSERT ON copa_telmex_teams
FOR EACH ROW EXECUTE FUNCTION regs07_append_genesis();

DROP TRIGGER IF EXISTS trg_regs07_player_genesis
    ON copa_telmex_players;
CREATE TRIGGER trg_regs07_player_genesis
AFTER INSERT ON copa_telmex_players
FOR EACH ROW EXECUTE FUNCTION regs07_append_genesis();

DROP TRIGGER IF EXISTS trg_regs07_team_guard
    ON copa_telmex_teams;
CREATE TRIGGER trg_regs07_team_guard
BEFORE UPDATE OR DELETE ON copa_telmex_teams
FOR EACH ROW EXECUTE FUNCTION regs07_guard_committed_mutation();

DROP TRIGGER IF EXISTS trg_regs07_player_guard
    ON copa_telmex_players;
CREATE TRIGGER trg_regs07_player_guard
BEFORE UPDATE OR DELETE ON copa_telmex_players
FOR EACH ROW EXECUTE FUNCTION regs07_guard_committed_mutation();

DROP TRIGGER IF EXISTS trg_regs07_team_version
    ON copa_telmex_teams;
CREATE TRIGGER trg_regs07_team_version
AFTER UPDATE ON copa_telmex_teams
FOR EACH ROW EXECUTE FUNCTION regs07_append_committed_version();

DROP TRIGGER IF EXISTS trg_regs07_player_version
    ON copa_telmex_players;
CREATE TRIGGER trg_regs07_player_version
AFTER UPDATE ON copa_telmex_players
FOR EACH ROW EXECUTE FUNCTION regs07_append_committed_version();

CREATE OR REPLACE FUNCTION enforce_regs07_execution_atomicity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    proposal_row
        copa_telmex_registration_postcommit_mutation_proposals%ROWTYPE;
    version_count integer;
    current_revision integer;
    current_hash text;
BEGIN
    SELECT * INTO proposal_row
    FROM copa_telmex_registration_postcommit_mutation_proposals
    WHERE id = NEW.proposal_id;
    SELECT count(*) INTO version_count
    FROM copa_telmex_registration_postcommit_entity_versions
    WHERE execution_id = NEW.id
      AND entity_type = proposal_row.entity_type
      AND entity_id = proposal_row.entity_id
      AND revision = proposal_row.proposed_revision
      AND snapshot_hash = proposal_row.proposed_snapshot_hash;
    IF proposal_row.entity_type = 'TEAM' THEN
        SELECT postcommit_revision, postcommit_snapshot_hash
        INTO current_revision, current_hash
        FROM copa_telmex_teams
        WHERE id = proposal_row.entity_id;
    ELSE
        SELECT postcommit_revision, postcommit_snapshot_hash
        INTO current_revision, current_hash
        FROM copa_telmex_players
        WHERE id = proposal_row.entity_id;
    END IF;
    IF version_count <> 1
       OR current_revision <> proposal_row.proposed_revision
       OR current_hash <> proposal_row.proposed_snapshot_hash THEN
        RAISE EXCEPTION
            'REG-S07: execution has no exact atomic committed-state version';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_regs07_execution_atomicity
    ON copa_telmex_registration_postcommit_mutation_executions;
CREATE CONSTRAINT TRIGGER trg_regs07_execution_atomicity
AFTER INSERT
ON copa_telmex_registration_postcommit_mutation_executions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION enforce_regs07_execution_atomicity();

COMMENT ON TABLE
copa_telmex_registration_postcommit_entity_versions IS
    'REG-S07 append-only committed Team/Player state history.';
COMMENT ON TABLE
copa_telmex_registration_postcommit_mutation_executions IS
    'REG-S07 exact projection with Ed25519 post-execution attestation.';

COMMIT;
