-- Candidate SQL effect gate only. No writer, key, or operational grant is seeded.
-- The trusted writer must first verify Ed25519/WebAuthn and the full review plan.
-- SQL independently checks exact effect HMAC, row, protected context and current
-- top-transaction evidence. This is not independent human-signature verification.
BEGIN;
DO $preflight$
DECLARE item record; owner_id oid;
BEGIN
  SELECT oid INTO owner_id FROM pg_catalog.pg_roles
  WHERE rolname='samchat_ctt_staff_owner' AND NOT rolcanlogin
    AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
    AND NOT rolreplication AND NOT rolbypassrls;
  IF owner_id IS NULL OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace
    WHERE nspname='ctt_staff_private' AND nspowner=owner_id
  ) OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE') THEN
    RAISE EXCEPTION 'CTT effect owner isolation invalid';
  END IF;
  FOR item IN SELECT * FROM (VALUES
    ('ctt_staff_private.capture_payloads'),('ctt_staff_private.capture_results'),
    ('ctt_staff_private.capture_attempt_anchors'),('ctt_staff_private.manifest_anchors'),
    ('ctt_staff_private.source_anchors'),('ctt_staff_private.review_challenges'),
    ('ctt_staff_private.review_challenge_consumptions'),
    ('ctt_staff_private.signed_assertion_guards')
  ) v(name) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_class c
      WHERE c.oid=pg_catalog.to_regclass(item.name) AND c.relkind='r'
        AND c.relowner=owner_id AND NOT c.relrowsecurity
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
          WHERE a.grantee<>c.relowner
        ) AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_attribute a
          WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
        )
    ) THEN RAISE EXCEPTION 'CTT effect dependency isolation invalid: %',item.name; END IF;
  END LOOP;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_depend d
    JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid AND e.extname='pgcrypto'
    JOIN pg_catalog.pg_roles r ON r.oid=e.extowner AND r.rolsuper
    JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace AND n.nspname='public'
    JOIN pg_catalog.pg_proc p ON p.oid=d.objid AND p.proowner=e.extowner
    WHERE d.classid='pg_catalog.pg_proc'::regclass AND d.deptype='e'
      AND d.refclassid='pg_catalog.pg_extension'::regclass
      AND p.oid=pg_catalog.to_regprocedure('public.hmac(bytea,bytea,text)')
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid='public.copa_telmex_team_staff'::regclass
      AND c.relowner=owner_id AND NOT c.relrowsecurity
  ) OR pg_catalog.has_table_privilege('devnous_user','public.copa_telmex_team_staff','INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')
    OR pg_catalog.has_any_column_privilege('devnous_user','public.copa_telmex_team_staff','INSERT,UPDATE,REFERENCES')
    OR NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
      WHERE t.tgrelid='public.copa_telmex_team_staff'::regclass
        AND t.tgname='ctt_staff_write_closed' AND t.tgenabled IN ('O','A')
        AND p.oid=pg_catalog.to_regprocedure('ctt_staff_private.deny_staff_write()')
        AND p.proowner=owner_id AND p.prosecdef
        AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
    ) THEN RAISE EXCEPTION 'CTT effect staff/crypto prerequisites invalid'; END IF;
  -- Fail closed on missing, disabled, replaced, or publicly executable guards.
  FOR item IN SELECT * FROM (VALUES
    ('capture_payloads','ctt_capture_payload_immutable','reject_review_evidence_mutation'),
    ('capture_payloads','ctt_capture_payload_no_truncate','reject_review_evidence_mutation'),
    ('capture_results','ctt_capture_result_immutable','reject_review_evidence_mutation'),
    ('capture_results','ctt_capture_result_no_truncate','reject_review_evidence_mutation'),
    ('capture_attempt_anchors','ctt_capture_anchor_validate','validate_capture_anchor'),
    ('capture_attempt_anchors','ctt_capture_anchor_immutable','reject_review_evidence_mutation'),
    ('capture_attempt_anchors','ctt_capture_anchor_no_truncate','reject_review_evidence_mutation'),
    ('review_challenge_consumptions','ctt_review_consumption_immutable','reject_review_evidence_mutation'),
    ('review_challenge_consumptions','ctt_review_consumption_no_truncate','reject_review_evidence_mutation'),
    ('review_challenge_consumptions','ctt_review_consumption_commit_guard','validate_review_consumption_at_commit'),
    ('manifest_anchors','ctt_manifest_anchor_immutable','reject_review_evidence_mutation'),
    ('manifest_anchors','ctt_manifest_anchor_no_truncate','reject_review_evidence_mutation'),
    ('source_anchors','ctt_source_anchor_immutable','reject_review_evidence_mutation'),
    ('source_anchors','ctt_source_anchor_no_truncate','reject_review_evidence_mutation'),
    ('review_challenges','ctt_review_challenge_immutable','reject_review_evidence_mutation'),
    ('review_challenges','ctt_review_challenge_no_truncate','reject_review_evidence_mutation'),
    ('signed_assertion_guards','ctt_assertion_guard_initial','check_signed_assertion_expiry'),
    ('signed_assertion_guards','ctt_assertion_guard_commit','check_signed_assertion_expiry'),
    ('signed_assertion_guards','ctt_assertion_guard_immutable','reject_review_evidence_mutation'),
    ('signed_assertion_guards','ctt_assertion_guard_no_truncate','reject_review_evidence_mutation')
  ) v(tab,trig,fn) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
      WHERE t.tgrelid=pg_catalog.to_regclass('ctt_staff_private.'||item.tab)
        AND t.tgname=item.trig AND t.tgenabled IN ('O','A')
        AND p.oid=pg_catalog.to_regprocedure('ctt_staff_private.'||item.fn||'()')
        AND p.proowner=owner_id AND p.prosecdef
        AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
          WHERE a.grantee<>p.proowner
        )
    ) THEN RAISE EXCEPTION 'CTT effect guard invalid: %',item.trig; END IF;
  END LOOP;
END
$preflight$;

CREATE TABLE ctt_staff_private.effect_keys (
  kid text PRIMARY KEY CHECK(length(kid) BETWEEN 1 AND 120),
  secret bytea NOT NULL CHECK(octet_length(secret)>=32),
  not_before timestamptz NOT NULL,
  not_after timestamptz NOT NULL CHECK(not_after>not_before),
  revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE ctt_staff_private.effect_writers (
  writer_role name PRIMARY KEY,
  kid text NOT NULL REFERENCES ctt_staff_private.effect_keys(kid) ON DELETE RESTRICT
);
CREATE TABLE ctt_staff_private.effect_staff_writes (
  staff_id uuid PRIMARY KEY REFERENCES public.copa_telmex_team_staff(id) DEFERRABLE INITIALLY DEFERRED,
  attempt_id uuid NOT NULL REFERENCES ctt_staff_private.capture_payloads(attempt_id) ON DELETE RESTRICT,
  writer_role name NOT NULL REFERENCES ctt_staff_private.effect_writers(writer_role) ON DELETE RESTRICT,
  kid text NOT NULL REFERENCES ctt_staff_private.effect_keys(kid) ON DELETE RESTRICT,
  authorized_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE ctt_staff_private.effect_keys OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.effect_writers OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.effect_staff_writes OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.effect_keys,ctt_staff_private.effect_writers,
  ctt_staff_private.effect_staff_writes FROM PUBLIC,devnous_user;
CREATE TRIGGER ctt_effect_write_immutable BEFORE UPDATE OR DELETE
ON ctt_staff_private.effect_staff_writes FOR EACH ROW
EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_effect_write_no_truncate BEFORE TRUNCATE
ON ctt_staff_private.effect_staff_writes FOR EACH STATEMENT
EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();

CREATE FUNCTION ctt_staff_private.verify_effect_hmac(p_secret bytea,p_effect bytea,p_tag bytea)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT SECURITY DEFINER
SET search_path=pg_catalog AS $function$
DECLARE actual bytea; candidate bytea; difference integer:=0; i integer;
BEGIN
  IF octet_length(p_secret)<32 OR octet_length(p_effect)>4194304 THEN RETURN false; END IF;
  actual:=public.hmac(convert_to('ctt.capture.effect.v1','UTF8')||decode('00','hex')||p_effect,p_secret,'sha256'::text);
  IF octet_length(p_tag)=32 THEN candidate:=p_tag;
  ELSE candidate:=decode(repeat('00',32),'hex'); difference:=1; END IF;
  FOR i IN 0..31 LOOP
    difference:=difference | (get_byte(actual,i) # get_byte(candidate,i));
  END LOOP;
  RETURN difference=0;
END
$function$;
ALTER FUNCTION ctt_staff_private.verify_effect_hmac(bytea,bytea,bytea) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.verify_effect_hmac(bytea,bytea,bytea) FROM PUBLIC,devnous_user;

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
      OR scope->>'schema' IS DISTINCT FROM 'ctt.staff-review.challenge-scope.v1'
      OR scope->>'purpose' IS DISTINCT FROM 'capture_staff_v2'
      OR effect->'review_context' IS DISTINCT FROM (scope-ARRAY['staff_and_changes_binding','decision_hash','review_receipt_id','operation_id'])
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
DECLARE item record; private_owner oid;
BEGIN
  SELECT oid INTO private_owner FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner';
  FOR item IN SELECT * FROM (VALUES ('effect_keys'),('effect_writers'),('effect_staff_writes')) t(name) LOOP
    IF EXISTS (
      SELECT 1 FROM pg_catalog.pg_class c CROSS JOIN LATERAL
        pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
      WHERE c.oid=pg_catalog.to_regclass('ctt_staff_private.'||item.name)
        AND (c.relowner<>private_owner OR a.grantee<>private_owner)
    ) THEN RAISE EXCEPTION 'CTT effect ACL invalid'; END IF;
  END LOOP;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid IN (
      pg_catalog.to_regprocedure('ctt_staff_private.verify_effect_hmac(bytea,bytea,bytea)'),
      pg_catalog.to_regprocedure('ctt_staff_private.deny_staff_write()')
    ) AND (p.proowner<>private_owner OR NOT p.prosecdef
      OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']::text[]
      OR EXISTS (
        SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
        WHERE a.grantee<>p.proowner
      ))
  ) THEN RAISE EXCEPTION 'CTT effect function ACL invalid'; END IF;
END
$postflight$;
COMMIT;
