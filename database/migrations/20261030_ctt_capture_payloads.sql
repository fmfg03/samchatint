-- Candidate protected payload/result store. No operational grants or issuer.
BEGIN;
DO $preflight$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace n
    JOIN pg_catalog.pg_roles r ON r.oid=n.nspowner
    WHERE n.nspname='ctt_staff_private'
      AND r.rolname='samchat_ctt_staff_owner'
      AND NOT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb
      AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members m
    JOIN pg_catalog.pg_roles r ON r.rolname='samchat_ctt_staff_owner'
    WHERE m.roleid=r.oid OR m.member=r.oid
  ) OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','CREATE,USAGE')
    OR NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_extension e
      JOIN pg_catalog.pg_roles r ON r.oid=e.extowner
      JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
      WHERE e.extname='pgcrypto' AND n.nspname='public' AND r.rolsuper
    ) OR pg_catalog.to_regclass('ctt_staff_private.review_challenges') IS NULL THEN
    RAISE EXCEPTION 'CTT anchor prerequisites failed';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_depend d
    JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid AND e.extname='pgcrypto'
    JOIN pg_catalog.pg_proc proc ON proc.oid=d.objid AND proc.proowner=e.extowner
    WHERE d.classid='pg_catalog.pg_proc'::regclass
      AND d.refclassid='pg_catalog.pg_extension'::regclass
      AND d.objid=pg_catalog.to_regprocedure('public.digest(bytea,text)')
      AND d.deptype='e'
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
    WHERE c.oid='ctt_staff_private.review_challenges'::regclass
      AND c.relkind='r' AND r.rolname='samchat_ctt_staff_owner'
      AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
        WHERE a.grantee <> c.relowner
      )
      AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
        WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
      )
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
    WHERE p.oid=pg_catalog.to_regprocedure('ctt_staff_private.reject_review_evidence_mutation()')
      AND r.rolname='samchat_ctt_staff_owner' AND p.prosecdef
      AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
      AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
        WHERE a.grantee <> p.proowner
      )
  ) OR (
    SELECT count(*) FROM pg_catalog.pg_trigger t
    WHERE t.tgrelid='ctt_staff_private.review_challenges'::regclass
      AND t.tgname IN ('ctt_review_challenge_immutable','ctt_review_challenge_no_truncate')
      AND t.tgenabled IN ('O','A')
      AND t.tgfoid=pg_catalog.to_regprocedure('ctt_staff_private.reject_review_evidence_mutation()')
  ) <> 2 THEN
    RAISE EXCEPTION 'CTT anchor protected dependencies invalid';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
    WHERE c.oid=to_regclass('ctt_staff_private.capture_attempt_anchors')
      AND r.rolname='samchat_ctt_staff_owner'
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
    WHERE c.oid=to_regclass('ctt_staff_private.signed_assertion_guards')
      AND r.rolname='samchat_ctt_staff_owner'
  ) OR EXISTS (
    SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
    WHERE c.oid IN (to_regclass('ctt_staff_private.capture_attempt_anchors'),to_regclass('ctt_staff_private.signed_assertion_guards'))
      AND a.grantee<>c.relowner
  ) OR EXISTS (
    SELECT 1 FROM pg_attribute a WHERE a.attrelid IN (to_regclass('ctt_staff_private.capture_attempt_anchors'),to_regclass('ctt_staff_private.signed_assertion_guards'))
      AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
  ) THEN RAISE EXCEPTION 'CTT payload protected dependencies invalid'; END IF;
  IF EXISTS (
    SELECT 1 FROM (VALUES
      ('capture_attempt_anchors','ctt_capture_anchor_validate','validate_capture_anchor'),
      ('capture_attempt_anchors','ctt_capture_anchor_immutable','reject_review_evidence_mutation'),
      ('capture_attempt_anchors','ctt_capture_anchor_no_truncate','reject_review_evidence_mutation'),
      ('signed_assertion_guards','ctt_assertion_guard_initial','check_signed_assertion_expiry'),
      ('signed_assertion_guards','ctt_assertion_guard_commit','check_signed_assertion_expiry'),
      ('signed_assertion_guards','ctt_assertion_guard_immutable','reject_review_evidence_mutation'),
      ('signed_assertion_guards','ctt_assertion_guard_no_truncate','reject_review_evidence_mutation')
    ) expected(table_name,trigger_name,function_name)
    WHERE NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_trigger t
      JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
      JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
      JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      WHERE t.tgrelid=pg_catalog.to_regclass('ctt_staff_private.'||expected.table_name)
        AND t.tgname=expected.trigger_name AND t.tgenabled IN ('O','A')
        AND n.nspname='ctt_staff_private' AND p.proname=expected.function_name
        AND r.rolname='samchat_ctt_staff_owner' AND p.prosecdef
        AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
          WHERE a.grantee<>p.proowner
        )
    )
  ) THEN RAISE EXCEPTION 'CTT payload trigger dependencies invalid'; END IF;
END
$preflight$;
CREATE TABLE ctt_staff_private.capture_payloads (
  attempt_id uuid PRIMARY KEY REFERENCES ctt_staff_private.capture_attempt_anchors(attempt_id) ON DELETE RESTRICT,
  review_bytes bytea NOT NULL CHECK(octet_length(review_bytes) BETWEEN 2 AND 4194304),
  effect_bytes bytea NOT NULL CHECK(octet_length(effect_bytes) BETWEEN 2 AND 4194304),
  gate_envelope bytea NOT NULL CHECK(octet_length(gate_envelope) BETWEEN 2 AND 262144),
  nonce uuid NOT NULL UNIQUE
);
CREATE TABLE ctt_staff_private.capture_results (
  attempt_id uuid PRIMARY KEY REFERENCES ctt_staff_private.capture_payloads(attempt_id) ON DELETE RESTRICT,
  result_id uuid NOT NULL UNIQUE,
  team_id uuid NOT NULL REFERENCES public.copa_telmex_teams(id) ON DELETE RESTRICT,
  authorization_hash text NOT NULL CHECK(authorization_hash ~ '^sha256:[0-9a-f]{64}$'),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE ctt_staff_private.capture_payloads OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.capture_results OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.capture_payloads,ctt_staff_private.capture_results FROM PUBLIC,devnous_user;
CREATE FUNCTION ctt_staff_private.load_capture_payload(p_attempt_id uuid)
RETURNS TABLE(review_bytes bytea,effect_bytes bytea,gate_envelope bytea,nonce uuid,expires_at_seconds bigint)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $load$
 SELECT p.review_bytes,p.effect_bytes,p.gate_envelope,p.nonce,
        floor(extract(epoch FROM c.expires_at))::bigint
 FROM ctt_staff_private.capture_payloads p
 JOIN ctt_staff_private.capture_attempt_anchors a ON a.attempt_id=p.attempt_id
 JOIN ctt_staff_private.review_challenges c ON c.challenge_id=a.challenge_id
 WHERE p.attempt_id=p_attempt_id FOR UPDATE OF p
$load$;
CREATE FUNCTION ctt_staff_private.get_capture_result(p_attempt_id uuid)
RETURNS uuid LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $get$
 SELECT r.result_id FROM ctt_staff_private.capture_results r WHERE r.attempt_id=p_attempt_id
$get$;
CREATE FUNCTION ctt_staff_private.record_capture_result(p_attempt_id uuid,p_result_id uuid,p_authorization_hash text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $record$
DECLARE expected_hash text; expected_team uuid;
BEGIN
 SELECT 'sha256:'||encode(public.digest(p.gate_envelope,'sha256'::text),'hex'),
        (convert_from(a.scope_bytes,'UTF8')::jsonb->>'team_id')::uuid
 INTO expected_hash,expected_team
 FROM ctt_staff_private.capture_payloads p
 JOIN ctt_staff_private.capture_attempt_anchors a ON a.attempt_id=p.attempt_id
 JOIN ctt_staff_private.review_challenge_consumptions c ON c.challenge_id=a.challenge_id
 JOIN ctt_staff_private.signed_assertion_guards g ON g.challenge_id=a.challenge_id
 WHERE p.attempt_id=p_attempt_id;
 IF NOT FOUND OR p_authorization_hash IS DISTINCT FROM expected_hash THEN
   RAISE EXCEPTION 'CTT capture result denied' USING ERRCODE='42501';
 END IF;
 INSERT INTO ctt_staff_private.capture_results(attempt_id,result_id,team_id,authorization_hash)
 VALUES(p_attempt_id,p_result_id,expected_team,p_authorization_hash);
END
$record$;
ALTER FUNCTION ctt_staff_private.load_capture_payload(uuid) OWNER TO samchat_ctt_staff_owner;
ALTER FUNCTION ctt_staff_private.get_capture_result(uuid) OWNER TO samchat_ctt_staff_owner;
ALTER FUNCTION ctt_staff_private.record_capture_result(uuid,uuid,text) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.load_capture_payload(uuid),ctt_staff_private.get_capture_result(uuid),ctt_staff_private.record_capture_result(uuid,uuid,text) FROM PUBLIC,devnous_user;
CREATE TRIGGER ctt_capture_payload_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.capture_payloads FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_capture_payload_no_truncate BEFORE TRUNCATE ON ctt_staff_private.capture_payloads FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_capture_result_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.capture_results FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_capture_result_no_truncate BEFORE TRUNCATE ON ctt_staff_private.capture_results FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
DO $postflight$
BEGIN
 IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a WHERE n.nspname='ctt_staff_private' AND c.relname IN('capture_payloads','capture_results') AND a.grantee<>c.relowner)
 OR EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid IN ('ctt_staff_private.capture_payloads'::regclass,'ctt_staff_private.capture_results'::regclass) AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL)
 OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE n.nspname='ctt_staff_private' AND p.proname IN('load_capture_payload','get_capture_result','record_capture_result') AND a.grantee<>p.proowner)
 THEN RAISE EXCEPTION 'CTT capture payload ACL invalid'; END IF;
END
$postflight$;
COMMIT;
