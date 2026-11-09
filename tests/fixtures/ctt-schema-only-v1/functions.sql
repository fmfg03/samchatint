CREATE OR REPLACE FUNCTION public.regs07_canonical_jsonb(value jsonb)
 RETURNS text
 LANGUAGE plpgsql
 IMMUTABLE PARALLEL SAFE STRICT
AS $function$
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
$function$
;
ALTER FUNCTION public.regs07_canonical_jsonb(jsonb) OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_team_snapshot(row_value copa_telmex_teams)
 RETURNS jsonb
 LANGUAGE sql
 IMMUTABLE
AS $function$
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
$function$
;
ALTER FUNCTION public.regs07_team_snapshot(copa_telmex_teams) OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_player_snapshot(row_value copa_telmex_players)
 RETURNS jsonb
 LANGUAGE sql
 IMMUTABLE
AS $function$
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
$function$
;
ALTER FUNCTION public.regs07_player_snapshot(copa_telmex_players) OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_snapshot_hash(snapshot jsonb)
 RETURNS character varying
 LANGUAGE sql
 IMMUTABLE
AS $function$
    SELECT 'sha256:' || encode(
        digest(
            convert_to(regs07_canonical_jsonb(snapshot), 'UTF8'),
            'sha256'
        ),
        'hex'
    )
$function$
;
ALTER FUNCTION public.regs07_snapshot_hash(jsonb) OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.enforce_registration_human_edit_atomicity()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$
;
ALTER FUNCTION public.enforce_registration_human_edit_atomicity() OWNER TO postgres;
CREATE OR REPLACE FUNCTION public.enforce_registration_review_draft_append_only()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$
;
ALTER FUNCTION public.enforce_registration_review_draft_append_only() OWNER TO postgres;
CREATE OR REPLACE FUNCTION public.enforce_regs07_execution_atomicity()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$
;
ALTER FUNCTION public.enforce_regs07_execution_atomicity() OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.guard_registration_review_asset_immutability()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$
;
ALTER FUNCTION public.guard_registration_review_asset_immutability() OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_append_committed_version()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$
;
ALTER FUNCTION public.regs07_append_committed_version() OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_append_genesis()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$
;
ALTER FUNCTION public.regs07_append_genesis() OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_guard_committed_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$
;
ALTER FUNCTION public.regs07_guard_committed_mutation() OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_initialize_player()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.postcommit_revision := CASE
        WHEN NEW.governance_state = 'PENDING_FINALITY' THEN 0
        ELSE 1
    END;
    NEW.postcommit_snapshot_hash :=
        regs07_snapshot_hash(regs07_player_snapshot(NEW));
    RETURN NEW;
END
$function$
;
ALTER FUNCTION public.regs07_initialize_player() OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_initialize_team()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.postcommit_revision := 1;
    NEW.postcommit_snapshot_hash :=
        regs07_snapshot_hash(regs07_team_snapshot(NEW));
    RETURN NEW;
END
$function$
;
ALTER FUNCTION public.regs07_initialize_team() OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.reject_registration_human_field_authority_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION
        'REG-S05: proposals, approvals, decisions, executions and consumptions are immutable';
END
$function$
;
ALTER FUNCTION public.reject_registration_human_field_authority_mutation() OWNER TO postgres;
CREATE OR REPLACE FUNCTION public.reject_registration_ocr_evidence_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION
        'REG-S03: OCR runs, field diffs and reprocess decisions are immutable';
END
$function$
;
ALTER FUNCTION public.reject_registration_ocr_evidence_mutation() OWNER TO postgres;
CREATE OR REPLACE FUNCTION public.reject_registration_page_composition_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION
        'REG-S04: page append attempts and decisions are immutable';
END
$function$
;
ALTER FUNCTION public.reject_registration_page_composition_mutation() OWNER TO devnous_user;
CREATE OR REPLACE FUNCTION public.reject_regs07_authority_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION
        'REG-S07: proposals, decisions, executions and versions are immutable';
END
$function$
;
ALTER FUNCTION public.reject_regs07_authority_mutation() OWNER TO devnous_user;
