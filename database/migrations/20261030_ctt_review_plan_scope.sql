-- Candidate repair of the approved two-commitment sequence. No operational grants.
-- Valid canonical v1 inputs keep their legacy behavior. Scope v2 is signed before receipts:
-- no future decision/receipt fields; operation and field-evidence commitment stay in plan.
BEGIN;
DO $preflight$
DECLARE item record; owner_id oid;
BEGIN
  SELECT oid INTO owner_id FROM pg_catalog.pg_roles
  WHERE rolname='samchat_ctt_staff_owner' AND NOT rolcanlogin AND NOT rolsuper
    AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls;
  IF owner_id IS NULL OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname='ctt_staff_private' AND nspowner=owner_id
  ) OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE')
    OR pg_catalog.current_setting('max_prepared_transactions')::integer<>0 THEN
    RAISE EXCEPTION 'CTT review-plan owner isolation invalid';
  END IF;
  FOR item IN SELECT * FROM (VALUES
    ('capture_attempt_anchors'),('manifest_anchors'),('source_anchors'),
    ('capture_payloads'),('capture_results'),('review_challenges'),
    ('review_challenge_consumptions'),('signed_assertion_guards'),
    ('effect_keys'),('effect_writers'),('effect_staff_writes')
  ) t(name) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_class c
      WHERE c.oid=pg_catalog.to_regclass('ctt_staff_private.'||item.name)
        AND c.relowner=owner_id AND c.relkind='r' AND NOT c.relrowsecurity
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
          WHERE a.grantee<>owner_id
        ) AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid
            AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
        )
    ) THEN RAISE EXCEPTION 'CTT review-plan protected table invalid'; END IF;
  END LOOP;
  FOR item IN SELECT * FROM (VALUES
    ('validate_capture_anchor()', '9331d6cc596bc2792fdf0fd69fe409c4'),
    ('deny_staff_write()', '0d090dc7e2cc93a8961d69b910b3a1b2'),
    ('verify_effect_hmac(bytea,bytea,bytea)', '56e1ceb6ec1263d5bd273f03228566ca')
  ) t(signature,body_hash) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_proc p
      WHERE p.oid=pg_catalog.to_regprocedure('ctt_staff_private.'||item.signature)
        AND p.proowner=owner_id AND p.prosecdef
        AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
        AND pg_catalog.md5(p.prosrc)=item.body_hash
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
          WHERE a.grantee<>owner_id
        )
    ) THEN RAISE EXCEPTION 'CTT review-plan protected function invalid'; END IF;
  END LOOP;
  FOR item IN SELECT * FROM (VALUES ('public.digest(bytea,text)'),('public.hmac(bytea,bytea,text)')) t(signature) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_depend d
      JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid AND e.extname='pgcrypto'
      JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace AND n.nspname='public'
      JOIN pg_catalog.pg_roles r ON r.oid=e.extowner AND r.rolsuper
      JOIN pg_catalog.pg_proc p ON p.oid=d.objid AND p.proowner=e.extowner
      WHERE d.classid='pg_catalog.pg_proc'::regclass AND d.deptype='e'
        AND d.refclassid='pg_catalog.pg_extension'::regclass
        AND p.oid=pg_catalog.to_regprocedure(item.signature)
    ) THEN RAISE EXCEPTION 'CTT review-plan crypto dependency invalid'; END IF;
  END LOOP;
  FOR item IN SELECT * FROM (VALUES
    ('ctt_staff_private.capture_attempt_anchors','ctt_capture_anchor_validate','validate_capture_anchor'),
    ('ctt_staff_private.capture_attempt_anchors','ctt_capture_anchor_immutable','reject_review_evidence_mutation'),
    ('ctt_staff_private.capture_attempt_anchors','ctt_capture_anchor_no_truncate','reject_review_evidence_mutation'),
    ('ctt_staff_private.capture_payloads','ctt_capture_payload_immutable','reject_review_evidence_mutation'),
    ('ctt_staff_private.capture_payloads','ctt_capture_payload_no_truncate','reject_review_evidence_mutation'),
    ('ctt_staff_private.signed_assertion_guards','ctt_assertion_guard_initial','check_signed_assertion_expiry'),
    ('ctt_staff_private.signed_assertion_guards','ctt_assertion_guard_commit','check_signed_assertion_expiry'),
    ('ctt_staff_private.review_challenge_consumptions','ctt_review_consumption_commit_guard','validate_review_consumption_at_commit'),
    ('public.copa_telmex_team_staff','ctt_staff_write_closed','deny_staff_write')
  ) t(tab,trig,fn) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
      WHERE t.tgrelid=pg_catalog.to_regclass(item.tab) AND t.tgname=item.trig
        AND t.tgenabled IN ('O','A') AND NOT t.tgisinternal
        AND p.oid=pg_catalog.to_regprocedure('ctt_staff_private.'||item.fn||'()')
        AND p.proowner=owner_id AND p.prosecdef
        AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
          WHERE a.grantee<>owner_id
        )
    ) THEN RAISE EXCEPTION 'CTT review-plan protected trigger invalid'; END IF;
  END LOOP;
END
$preflight$;
CREATE OR REPLACE FUNCTION ctt_staff_private.validate_capture_anchor()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $anchor$
DECLARE scope jsonb; scope_text text; challenge ctt_staff_private.review_challenges%ROWTYPE;
BEGIN
  IF NEW.scope_hash <> 'sha256:' || pg_catalog.encode(public.digest(NEW.scope_bytes,'sha256'::text),'hex') THEN
    RAISE EXCEPTION 'CTT capture anchor denied' USING ERRCODE='42501';
  END IF;
  BEGIN
    scope_text := pg_catalog.convert_from(NEW.scope_bytes,'UTF8');
    IF scope_text IS NOT JSON OBJECT WITH UNIQUE KEYS THEN
      RAISE EXCEPTION 'CTT capture anchor denied' USING ERRCODE='42501';
    END IF;
    scope := scope_text::jsonb;
  EXCEPTION WHEN data_exception THEN
    RAISE EXCEPTION 'CTT capture anchor denied' USING ERRCODE='42501';
  END;
  SELECT * INTO challenge FROM ctt_staff_private.review_challenges
    WHERE challenge_id=NEW.challenge_id;
  IF NOT FOUND OR challenge.scope_hash IS DISTINCT FROM NEW.scope_hash
    OR challenge.tenant_id IS DISTINCT FROM NEW.tenant_id
    OR challenge.edition_id IS DISTINCT FROM NEW.edition_id
    OR NOT coalesce((
      (scope->>'schema' = 'ctt.staff-review.challenge-scope.v1'
        AND NOT (scope ? 'field_evidence_binding')
        AND coalesce((scope->>'decision_hash') ~ '^sha256:[0-9a-f]{64}$',false)
        AND coalesce((scope->>'review_receipt_id') ~ '^sha256:[0-9a-f]{64}$',false))
      OR (scope->>'schema' = 'ctt.staff-review.challenge-scope.v2'
        AND NOT (scope ?| ARRAY['decision_hash','review_receipt_id'])
        AND scope ?& ARRAY['schema','tenant_id','tournament_slug','edition_id','session_id','draft_id','draft_version','draft_content_hash','team_id','manifest_sha256','document_id','pdf_sha256','source_pages','actor_id','actor_role','authority_binding','staff_and_changes_binding','field_evidence_binding','policy_hash','operation_id','purpose']::text[]
        AND scope-ARRAY['schema','tenant_id','tournament_slug','edition_id','session_id','draft_id','draft_version','draft_content_hash','team_id','manifest_sha256','document_id','pdf_sha256','source_pages','actor_id','actor_role','authority_binding','staff_and_changes_binding','field_evidence_binding','policy_hash','operation_id','purpose']::text[] = '{}'::jsonb
        AND coalesce((scope->>'field_evidence_binding') ~ '^hmac-sha256:[0-9a-f]{64}$',false)
        AND coalesce((scope->>'operation_id') ~ '^sha256:[0-9a-f]{64}$',false))
    ),false)
    OR scope->>'purpose' IS DISTINCT FROM 'capture_staff_v2'
    OR scope->>'manifest_sha256' IS DISTINCT FROM NEW.manifest_sha256
    OR scope->>'document_id' IS DISTINCT FROM NEW.document_id
    OR scope->>'pdf_sha256' IS DISTINCT FROM NEW.pdf_sha256
    OR scope->>'tenant_id' IS DISTINCT FROM NEW.tenant_id
    OR scope->>'edition_id' IS DISTINCT FROM NEW.edition_id::text
    OR scope->>'actor_id' IS DISTINCT FROM challenge.principal_id::text THEN
    RAISE EXCEPTION 'CTT capture anchor denied' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END
$anchor$;
ALTER FUNCTION ctt_staff_private.validate_capture_anchor() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.validate_capture_anchor() FROM PUBLIC,devnous_user;

CREATE OR REPLACE FUNCTION ctt_staff_private.deny_staff_write()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
AS $function$
DECLARE candidate record; writer record; effect jsonb; envelope jsonb; scope jsonb;
  item jsonb; expected public.copa_telmex_team_staff; matched integer:=0;
  now_at timestamptz; top_xid bigint; required_columns text[]; supplied_columns text[];
BEGIN
  IF TG_OP<>'INSERT' OR NEW.governance_state IS DISTINCT FROM 'PENDING_FINALITY'
    OR NEW.finality_receipt_id IS NOT NULL THEN
    RAISE EXCEPTION 'CTT staff effect denied' USING ERRCODE='42501';
  END IF;
  SELECT w.writer_role,w.kid,k.secret,k.not_before,k.not_after,k.revoked INTO writer
  FROM ctt_staff_private.effect_writers w
  JOIN ctt_staff_private.effect_keys k ON k.kid=w.kid
  JOIN pg_catalog.pg_roles r ON r.rolname=w.writer_role
  WHERE w.writer_role=session_user AND r.rolname NOT IN ('devnous_user','samchat_ctt_staff_owner')
    AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
    AND NOT r.rolreplication AND NOT r.rolbypassrls
    AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.roleid=r.oid OR m.member=r.oid)
  FOR SHARE OF w,k;
  now_at:=clock_timestamp();
  IF writer IS NULL OR writer.revoked OR now_at<writer.not_before OR now_at>=writer.not_after THEN
    RAISE EXCEPTION 'CTT staff effect denied' USING ERRCODE='42501';
  END IF;
  -- xmin is the inserting (sub)transaction, not a request flag. Evidence must
  -- originate in this top transaction BEFORE the writer's insertion savepoint.
  -- Subtransaction-created or previously committed evidence fails closed.
  top_xid:=mod(pg_catalog.pg_current_xact_id()::text::numeric,4294967296)::bigint;
  SELECT array_agg(a.attname::text ORDER BY a.attname) INTO required_columns
  FROM pg_catalog.pg_attribute a WHERE a.attrelid=TG_RELID AND a.attnum>0 AND NOT a.attisdropped;
  FOR candidate IN
    SELECT p.attempt_id,p.effect_bytes,p.gate_envelope,p.nonce,a.scope_bytes,a.scope_hash,
      a.tenant_id,a.edition_id,c.principal_id,c.issued_at,c.expires_at,g.expires_at_seconds
    FROM ctt_staff_private.capture_payloads p
    JOIN ctt_staff_private.capture_attempt_anchors a USING(attempt_id)
    JOIN ctt_staff_private.review_challenges c USING(challenge_id)
    JOIN ctt_staff_private.review_challenge_consumptions used USING(challenge_id)
    JOIN ctt_staff_private.signed_assertion_guards g USING(challenge_id)
    WHERE used.xmin::text::bigint=top_xid AND g.xmin::text::bigint=top_xid
      AND used.credential_id=c.credential_id
      AND NOT EXISTS (SELECT 1 FROM ctt_staff_private.capture_results result WHERE result.attempt_id=p.attempt_id)
    FOR UPDATE OF p
  LOOP
    now_at:=clock_timestamp();
    IF now_at>=candidate.expires_at OR now_at>=to_timestamp(candidate.expires_at_seconds)
      OR writer.not_after<to_timestamp(candidate.expires_at_seconds)
      OR candidate.expires_at_seconds<>floor(extract(epoch FROM candidate.expires_at))::bigint THEN CONTINUE; END IF;
    IF NOT (convert_from(candidate.effect_bytes,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
      OR NOT (convert_from(candidate.gate_envelope,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
      OR NOT (convert_from(candidate.scope_bytes,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS) THEN CONTINUE; END IF;
    effect:=convert_from(candidate.effect_bytes,'UTF8')::jsonb;
    envelope:=convert_from(candidate.gate_envelope,'UTF8')::jsonb;
    scope:=convert_from(candidate.scope_bytes,'UTF8')::jsonb;
    IF effect->>'schema' IS DISTINCT FROM 'ctt.capture.effect.v1'
      OR NOT coalesce((
      (scope->>'schema' = 'ctt.staff-review.challenge-scope.v1'
        AND NOT (scope ? 'field_evidence_binding')
        AND coalesce((scope->>'decision_hash') ~ '^sha256:[0-9a-f]{64}$',false)
        AND coalesce((scope->>'review_receipt_id') ~ '^sha256:[0-9a-f]{64}$',false))
      OR (scope->>'schema' = 'ctt.staff-review.challenge-scope.v2'
        AND NOT (scope ?| ARRAY['decision_hash','review_receipt_id'])
        AND scope ?& ARRAY['schema','tenant_id','tournament_slug','edition_id','session_id','draft_id','draft_version','draft_content_hash','team_id','manifest_sha256','document_id','pdf_sha256','source_pages','actor_id','actor_role','authority_binding','staff_and_changes_binding','field_evidence_binding','policy_hash','operation_id','purpose']::text[]
        AND scope-ARRAY['schema','tenant_id','tournament_slug','edition_id','session_id','draft_id','draft_version','draft_content_hash','team_id','manifest_sha256','document_id','pdf_sha256','source_pages','actor_id','actor_role','authority_binding','staff_and_changes_binding','field_evidence_binding','policy_hash','operation_id','purpose']::text[] = '{}'::jsonb
        AND coalesce((scope->>'field_evidence_binding') ~ '^hmac-sha256:[0-9a-f]{64}$',false)
        AND coalesce((scope->>'operation_id') ~ '^sha256:[0-9a-f]{64}$',false))
    ),false)
      OR scope->>'purpose' IS DISTINCT FROM 'capture_staff_v2'
      OR effect->'review_context' IS DISTINCT FROM (CASE WHEN scope->>'schema'='ctt.staff-review.challenge-scope.v2'
        THEN scope-ARRAY['staff_and_changes_binding']
        ELSE scope-ARRAY['staff_and_changes_binding','decision_hash','review_receipt_id','operation_id'] END)
      OR scope->>'tenant_id' IS DISTINCT FROM candidate.tenant_id
      OR scope->>'edition_id' IS DISTINCT FROM candidate.edition_id::text
      OR scope->>'actor_id' IS DISTINCT FROM candidate.principal_id::text
      OR scope->>'team_id' IS DISTINCT FROM NEW.team_id::text
      OR effect->'team'->>'id' IS DISTINCT FROM NEW.team_id::text
      OR envelope->'body'->>'schema' IS DISTINCT FROM 'ctt.capture.authorization.v1'
      OR envelope->'body'->>'attempt_id' IS DISTINCT FROM candidate.attempt_id::text
      OR envelope->'body'->>'nonce' IS DISTINCT FROM candidate.nonce::text
      OR envelope->'body'->>'scope_hash' IS DISTINCT FROM candidate.scope_hash
      OR envelope->'body'->>'expires_at' IS DISTINCT FROM candidate.expires_at_seconds::text
      OR NOT coalesce((envelope->'body'->>'issued_at') ~ '^[0-9]{1,12}$',false)
      OR (envelope->'body'->>'issued_at')::bigint>floor(extract(epoch FROM now_at))::bigint
      OR (envelope->'body'->>'issued_at')::bigint<floor(extract(epoch FROM candidate.issued_at))::bigint
      OR candidate.expires_at_seconds-(envelope->'body'->>'issued_at')::bigint NOT BETWEEN 1 AND 300
      OR NOT coalesce((envelope->'body'->>'effect_binding') ~ '^hmac-sha256:[0-9a-f]{64}$',false)
      OR NOT coalesce(ctt_staff_private.verify_effect_hmac(writer.secret,candidate.effect_bytes,
        decode(substr(envelope->'body'->>'effect_binding',13),'hex')),false)
      OR jsonb_typeof(effect->'staff') IS DISTINCT FROM 'array'
      OR jsonb_array_length(effect->'staff')<>2
      OR NEW.governance_draft_id IS DISTINCT FROM scope->>'draft_id'
      OR NEW.governance_draft_version::text IS DISTINCT FROM scope->>'draft_version'
    THEN CONTINUE; END IF;
    IF (SELECT count(*) FROM jsonb_array_elements(effect->'staff') s
      WHERE s->'row'->>'id'=NEW.id::text)<>1 THEN CONTINUE; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(effect->'staff') LOOP
      IF item->'row'->>'id' IS DISTINCT FROM NEW.id::text THEN CONTINUE; END IF;
      IF item->>'presence' IS DISTINCT FROM 'PRESENT'
        OR item->>'slot' IS DISTINCT FROM NEW.staff_slot::text
        OR item->>'role' IS DISTINCT FROM NEW.role
        OR NEW.role IS DISTINCT FROM (CASE NEW.staff_slot WHEN 1 THEN 'director_tecnico' WHEN 2 THEN 'auxiliar' END)
        OR jsonb_typeof(item->'row') IS DISTINCT FROM 'object'
      THEN CONTINUE; END IF;
      SELECT array_agg(key ORDER BY key) INTO supplied_columns FROM jsonb_object_keys(item->'row') key;
      IF supplied_columns IS DISTINCT FROM required_columns THEN CONTINUE; END IF;
      expected:=jsonb_populate_record(NULL::public.copa_telmex_team_staff,item->'row');
      IF to_jsonb(expected) IS DISTINCT FROM to_jsonb(NEW) THEN CONTINUE; END IF;
      matched:=matched+1;
      IF matched<>1 THEN RAISE EXCEPTION 'CTT staff effect denied' USING ERRCODE='42501'; END IF;
      INSERT INTO ctt_staff_private.effect_staff_writes(staff_id,attempt_id,writer_role,kid)
      VALUES(NEW.id,candidate.attempt_id,writer.writer_role,writer.kid);
    END LOOP;
  END LOOP;
  IF matched<>1 THEN RAISE EXCEPTION 'CTT staff effect denied' USING ERRCODE='42501'; END IF;
  RETURN NEW;
EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value OR character_not_in_repertoire OR numeric_value_out_of_range OR datetime_field_overflow THEN
  RAISE EXCEPTION 'CTT staff effect denied' USING ERRCODE='42501';
END
$function$;
ALTER FUNCTION ctt_staff_private.deny_staff_write() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.deny_staff_write() FROM PUBLIC,devnous_user;
DO $postflight$
DECLARE item record; owner_id oid;
BEGIN
  SELECT oid INTO owner_id FROM pg_catalog.pg_roles
  WHERE rolname='samchat_ctt_staff_owner' AND NOT rolcanlogin AND NOT rolsuper
    AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls;
  IF owner_id IS NULL OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname='ctt_staff_private' AND nspowner=owner_id
  ) OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE')
    OR pg_catalog.current_setting('max_prepared_transactions')::integer<>0 THEN
    RAISE EXCEPTION 'CTT review-plan owner isolation invalid';
  END IF;
  FOR item IN SELECT * FROM (VALUES
    ('capture_attempt_anchors'),('manifest_anchors'),('source_anchors'),
    ('capture_payloads'),('capture_results'),('review_challenges'),
    ('review_challenge_consumptions'),('signed_assertion_guards'),
    ('effect_keys'),('effect_writers'),('effect_staff_writes')
  ) t(name) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_class c
      WHERE c.oid=pg_catalog.to_regclass('ctt_staff_private.'||item.name)
        AND c.relowner=owner_id AND c.relkind='r' AND NOT c.relrowsecurity
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
          WHERE a.grantee<>owner_id
        ) AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid
            AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
        )
    ) THEN RAISE EXCEPTION 'CTT review-plan protected table invalid'; END IF;
  END LOOP;
  FOR item IN SELECT * FROM (VALUES
    ('validate_capture_anchor()', '2309ca16339522b5567ab96624812440'),
    ('deny_staff_write()', '7fdb5522a19627554872e2f3a71f5236'),
    ('verify_effect_hmac(bytea,bytea,bytea)', '56e1ceb6ec1263d5bd273f03228566ca')
  ) t(signature,body_hash) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_proc p
      WHERE p.oid=pg_catalog.to_regprocedure('ctt_staff_private.'||item.signature)
        AND p.proowner=owner_id AND p.prosecdef
        AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
        AND pg_catalog.md5(p.prosrc)=item.body_hash
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
          WHERE a.grantee<>owner_id
        )
    ) THEN RAISE EXCEPTION 'CTT review-plan protected function invalid'; END IF;
  END LOOP;
  FOR item IN SELECT * FROM (VALUES ('public.digest(bytea,text)'),('public.hmac(bytea,bytea,text)')) t(signature) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_depend d
      JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid AND e.extname='pgcrypto'
      JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace AND n.nspname='public'
      JOIN pg_catalog.pg_roles r ON r.oid=e.extowner AND r.rolsuper
      JOIN pg_catalog.pg_proc p ON p.oid=d.objid AND p.proowner=e.extowner
      WHERE d.classid='pg_catalog.pg_proc'::regclass AND d.deptype='e'
        AND d.refclassid='pg_catalog.pg_extension'::regclass
        AND p.oid=pg_catalog.to_regprocedure(item.signature)
    ) THEN RAISE EXCEPTION 'CTT review-plan crypto dependency invalid'; END IF;
  END LOOP;
  FOR item IN SELECT * FROM (VALUES
    ('ctt_staff_private.capture_attempt_anchors','ctt_capture_anchor_validate','validate_capture_anchor'),
    ('ctt_staff_private.capture_attempt_anchors','ctt_capture_anchor_immutable','reject_review_evidence_mutation'),
    ('ctt_staff_private.capture_attempt_anchors','ctt_capture_anchor_no_truncate','reject_review_evidence_mutation'),
    ('ctt_staff_private.capture_payloads','ctt_capture_payload_immutable','reject_review_evidence_mutation'),
    ('ctt_staff_private.capture_payloads','ctt_capture_payload_no_truncate','reject_review_evidence_mutation'),
    ('ctt_staff_private.signed_assertion_guards','ctt_assertion_guard_initial','check_signed_assertion_expiry'),
    ('ctt_staff_private.signed_assertion_guards','ctt_assertion_guard_commit','check_signed_assertion_expiry'),
    ('ctt_staff_private.review_challenge_consumptions','ctt_review_consumption_commit_guard','validate_review_consumption_at_commit'),
    ('public.copa_telmex_team_staff','ctt_staff_write_closed','deny_staff_write')
  ) t(tab,trig,fn) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
      WHERE t.tgrelid=pg_catalog.to_regclass(item.tab) AND t.tgname=item.trig
        AND t.tgenabled IN ('O','A') AND NOT t.tgisinternal
        AND p.oid=pg_catalog.to_regprocedure('ctt_staff_private.'||item.fn||'()')
        AND p.proowner=owner_id AND p.prosecdef
        AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
          WHERE a.grantee<>owner_id
        )
    ) THEN RAISE EXCEPTION 'CTT review-plan protected trigger invalid'; END IF;
  END LOOP;
END
$postflight$;
COMMIT;
