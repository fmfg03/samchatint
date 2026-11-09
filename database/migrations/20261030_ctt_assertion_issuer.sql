-- Candidate identity assertion issuer only; no reviewer enrollment or grants.
-- Loading and recording issuance never consume a challenge or authorize a team.
BEGIN;
DO $preflight$
DECLARE obj record; private_owner oid;
BEGIN
  SELECT oid INTO private_owner FROM pg_catalog.pg_roles
  WHERE rolname='samchat_ctt_staff_owner' AND NOT rolcanlogin AND NOT rolsuper
    AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls;
  IF private_owner IS NULL OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=private_owner OR member=private_owner
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname='ctt_staff_private' AND nspowner=private_owner
  ) OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE')
    OR pg_catalog.current_setting('max_prepared_transactions')::integer<>0 THEN
    RAISE EXCEPTION 'CTT platform owner isolation invalid';
  END IF;
  FOR obj IN SELECT * FROM (VALUES ('review_credentials'),('review_principals'),
    ('review_faculties'),('review_challenges'),('review_challenge_consumptions'),
    ('review_authority_audit')) t(name) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass('ctt_staff_private.'||obj.name)
        AND c.relowner=private_owner AND c.relkind='r' AND NOT c.relrowsecurity
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
          WHERE a.grantee<>c.relowner
        ) AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid
            AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
        )
    ) THEN RAISE EXCEPTION 'CTT platform protected dependency invalid'; END IF;
  END LOOP;
  FOR obj IN SELECT * FROM (VALUES
    ('review_credentials','ctt_review_credential_version','guard_review_authority_version'),
    ('review_credentials','ctt_review_credential_audit','audit_review_authority_change'),
    ('review_challenges','ctt_review_challenge_immutable','reject_review_evidence_mutation'),
    ('review_challenges','ctt_review_challenge_no_truncate','reject_review_evidence_mutation'),
    ('review_challenge_consumptions','ctt_review_consumption_immutable','reject_review_evidence_mutation'),
    ('review_challenge_consumptions','ctt_review_consumption_no_truncate','reject_review_evidence_mutation'),
    ('review_challenge_consumptions','ctt_review_consumption_commit_guard','validate_review_consumption_at_commit')
  ) t(tab,trig,fn) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
      WHERE t.tgrelid=pg_catalog.to_regclass('ctt_staff_private.'||obj.tab)
        AND t.tgname=obj.trig AND t.tgenabled IN ('O','A')
        AND p.oid=pg_catalog.to_regprocedure('ctt_staff_private.'||obj.fn||'()')
        AND p.proowner=private_owner AND p.prosecdef
        AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
          WHERE a.grantee<>p.proowner
        )
    ) THEN RAISE EXCEPTION 'CTT platform protected guard invalid'; END IF;
  END LOOP;
END
$preflight$;
DO $dependencies$
DECLARE obj record; owner_oid oid;
BEGIN
  SELECT oid INTO owner_oid FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner';
  FOR obj IN SELECT * FROM (VALUES ('capture_attempt_anchors'),('manifest_anchors'),
    ('source_anchors'),('signed_assertion_guards')) t(name) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass('ctt_staff_private.'||obj.name)
        AND c.relkind='r' AND c.relowner=owner_oid AND NOT c.relrowsecurity
        AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a WHERE a.grantee<>owner_oid)
        AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL)
    ) THEN RAISE EXCEPTION 'CTT issuer protected dependency invalid'; END IF;
  END LOOP;
  FOR obj IN SELECT * FROM (VALUES
    ('lock_capture_anchor(uuid)'),('record_signed_assertion_guard(uuid,text,bigint)'),
    ('validate_authenticator_consumption()'),('check_signed_assertion_expiry()')) t(signature) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=pg_catalog.to_regprocedure('ctt_staff_private.'||obj.signature)
        AND p.proowner=owner_oid AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
        AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a WHERE a.grantee<>owner_oid)
    ) THEN RAISE EXCEPTION 'CTT issuer protected function invalid'; END IF;
  END LOOP;
  FOR obj IN SELECT * FROM (VALUES
    ('capture_attempt_anchors','ctt_capture_anchor_immutable','reject_review_evidence_mutation()'),
    ('capture_attempt_anchors','ctt_capture_anchor_no_truncate','reject_review_evidence_mutation()'),
    ('capture_attempt_anchors','ctt_capture_anchor_validate','validate_capture_anchor()'),
    ('manifest_anchors','ctt_manifest_anchor_immutable','reject_review_evidence_mutation()'),
    ('source_anchors','ctt_source_anchor_immutable','reject_review_evidence_mutation()'),
    ('manifest_anchors','ctt_manifest_anchor_no_truncate','reject_review_evidence_mutation()'),
    ('source_anchors','ctt_source_anchor_no_truncate','reject_review_evidence_mutation()'),
    ('signed_assertion_guards','ctt_assertion_guard_immutable','reject_review_evidence_mutation()'),
    ('signed_assertion_guards','ctt_assertion_guard_no_truncate','reject_review_evidence_mutation()'),
    ('review_challenge_consumptions','ctt_authenticator_consumption_initial','validate_authenticator_consumption()'),
    ('review_challenge_consumptions','ctt_authenticator_consumption_commit','validate_authenticator_consumption()'),
    ('signed_assertion_guards','ctt_assertion_guard_initial','check_signed_assertion_expiry()'),
    ('signed_assertion_guards','ctt_assertion_guard_commit','check_signed_assertion_expiry()')) t(tab,trig,signature) LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
      WHERE t.tgrelid=pg_catalog.to_regclass('ctt_staff_private.'||obj.tab)
        AND t.tgname=obj.trig AND t.tgenabled IN ('O','A')
        AND t.tgfoid=pg_catalog.to_regprocedure('ctt_staff_private.'||obj.signature)
        AND p.proowner=owner_oid AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
    ) THEN RAISE EXCEPTION 'CTT issuer protected trigger invalid'; END IF;
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
      JOIN pg_catalog.pg_proc p ON p.oid=d.objid JOIN pg_catalog.pg_roles r ON r.oid=e.extowner
      WHERE e.extname='pgcrypto' AND r.rolsuper AND p.proowner=e.extowner AND d.deptype='e'
        AND d.classid='pg_catalog.pg_proc'::regclass AND d.refclassid='pg_catalog.pg_extension'::regclass
        AND p.oid=pg_catalog.to_regprocedure('public.digest(bytea,text)')) THEN
    RAISE EXCEPTION 'CTT issuer digest dependency invalid';
  END IF;
END
$dependencies$;
CREATE TABLE ctt_staff_private.review_assertion_issuances (
  challenge_id uuid PRIMARY KEY REFERENCES ctt_staff_private.review_challenges(challenge_id) ON DELETE RESTRICT,
  attempt_id uuid NOT NULL UNIQUE REFERENCES ctt_staff_private.capture_attempt_anchors(attempt_id) ON DELETE RESTRICT,
  proof_sha256 text NOT NULL CHECK(proof_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  prior_counter bigint NOT NULL CHECK(prior_counter BETWEEN 0 AND 4294967295),
  new_counter bigint NOT NULL CHECK(new_counter BETWEEN 0 AND 4294967295),
  auth_profile text NOT NULL,
  backup_eligible boolean NOT NULL,
  backup_state boolean NOT NULL,
  auth_policy_hash text,
  expires_at_seconds bigint NOT NULL CHECK(expires_at_seconds BETWEEN 1 AND 253402300799),
  recorded_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
  CHECK (
    (auth_profile='device-counter-v1' AND NOT backup_eligible AND NOT backup_state AND auth_policy_hash IS NULL AND new_counter>prior_counter)
    OR (auth_profile='platform-passkey-v1' AND (backup_eligible OR NOT backup_state)
      AND auth_policy_hash IS NOT NULL AND auth_policy_hash='sha256:e0a2e1ce21182519a8739ffa11aba21164d565871bf62909cf9746861dfa48d3'
      AND ((new_counter=0 AND prior_counter=0) OR new_counter>prior_counter))
  )
);
ALTER TABLE ctt_staff_private.review_assertion_issuances OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.review_assertion_issuances FROM PUBLIC,devnous_user;
CREATE TRIGGER ctt_assertion_issuance_immutable BEFORE UPDATE OR DELETE
ON ctt_staff_private.review_assertion_issuances FOR EACH ROW
EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_assertion_issuance_no_truncate BEFORE TRUNCATE
ON ctt_staff_private.review_assertion_issuances FOR EACH STATEMENT
EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();

CREATE FUNCTION ctt_staff_private.load_review_assertion_context(p_attempt_id uuid)
RETURNS TABLE(attempt_id uuid,challenge_id uuid,principal_id uuid,credential_id bytea,
  scope_hash text,scope_bytes bytea,challenge_bytes bytea,issued_at_seconds bigint,expires_at_seconds bigint,
  attested_credential bytea,user_handle bytea,sign_count bigint,auth_profile text,
  backup_eligible boolean,backup_state boolean,auth_policy_hash text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $load$
DECLARE a record; c ctt_staff_private.review_challenges%ROWTYPE;
  p ctt_staff_private.review_principals%ROWTYPE;
  f ctt_staff_private.review_faculties%ROWTYPE;
  cr ctt_staff_private.review_credentials%ROWTYPE;
  checked_at timestamptz; scope jsonb; scope_text text;
BEGIN
  IF pg_catalog.current_setting('max_prepared_transactions')::integer<>0 THEN
    RAISE EXCEPTION 'CTT issuer context denied' USING ERRCODE='42501';
  END IF;
  SELECT * INTO a FROM ctt_staff_private.lock_capture_anchor(p_attempt_id);
  IF NOT FOUND THEN RAISE EXCEPTION 'CTT issuer context denied' USING ERRCODE='42501'; END IF;
  SELECT * INTO c FROM ctt_staff_private.review_challenges ch WHERE ch.challenge_id=a.challenge_id FOR SHARE;
  SELECT * INTO p FROM ctt_staff_private.review_principals pr WHERE pr.principal_id=c.principal_id FOR SHARE;
  SELECT * INTO f FROM ctt_staff_private.review_faculties fa WHERE fa.principal_id=c.principal_id
    AND fa.tenant_id=c.tenant_id AND fa.edition_id=c.edition_id AND fa.faculty='capture_staff_v2' FOR SHARE;
  SELECT * INTO cr FROM ctt_staff_private.review_credentials ce WHERE ce.credential_id=c.credential_id
    AND ce.principal_id=c.principal_id FOR SHARE;
  checked_at:=pg_catalog.clock_timestamp();
  IF c.challenge_id IS NULL OR p.principal_id IS NULL OR f.principal_id IS NULL OR cr.credential_id IS NULL
    OR c.faculty IS DISTINCT FROM 'capture_staff_v2' OR NOT p.active
    OR p.authority_version IS DISTINCT FROM c.principal_authority_version
    OR f.authority_version IS DISTINCT FROM c.faculty_authority_version OR f.revoked_at IS NOT NULL
    OR cr.authority_version IS DISTINCT FROM c.credential_authority_version OR cr.revoked_at IS NOT NULL
    OR checked_at<c.issued_at OR checked_at>=c.expires_at OR checked_at<f.valid_from OR checked_at>=f.valid_until
    OR c.expires_at>c.issued_at+interval '5 minutes'
    OR floor(extract(epoch FROM c.expires_at))::bigint<=floor(extract(epoch FROM checked_at))::bigint
    OR EXISTS(SELECT 1 FROM ctt_staff_private.review_challenge_consumptions co WHERE co.challenge_id=c.challenge_id)
    OR a.scope_hash IS DISTINCT FROM c.scope_hash
    OR a.scope_hash IS DISTINCT FROM 'sha256:'||pg_catalog.encode(public.digest(a.scope_bytes,'sha256'::text),'hex') THEN
    RAISE EXCEPTION 'CTT issuer context denied' USING ERRCODE='42501';
  END IF;
  scope_text:=pg_catalog.convert_from(a.scope_bytes,'UTF8');
  IF scope_text IS NOT JSON OBJECT WITH UNIQUE KEYS THEN
    RAISE EXCEPTION 'CTT issuer context denied' USING ERRCODE='42501';
  END IF;
  scope:=scope_text::jsonb;
  IF scope->>'actor_id' IS DISTINCT FROM c.principal_id::text
    OR scope->>'tenant_id' IS DISTINCT FROM c.tenant_id OR scope->>'edition_id' IS DISTINCT FROM c.edition_id::text
    OR scope->>'purpose' IS DISTINCT FROM 'capture_staff_v2' THEN
    RAISE EXCEPTION 'CTT issuer context denied' USING ERRCODE='42501';
  END IF;
  RETURN QUERY SELECT a.attempt_id,c.challenge_id,c.principal_id,c.credential_id,a.scope_hash,a.scope_bytes,
    c.challenge_bytes,floor(extract(epoch FROM c.issued_at))::bigint,floor(extract(epoch FROM c.expires_at))::bigint,
    cr.attested_credential,cr.user_handle,cr.sign_count,cr.auth_profile,cr.backup_eligible,cr.backup_state,cr.auth_policy_hash;
END
$load$;
CREATE FUNCTION ctt_staff_private.record_review_assertion_issuance(
  p_attempt_id uuid,p_proof_sha256 text,p_prior_counter bigint,p_new_counter bigint,
  p_auth_profile text,p_backup_eligible boolean,p_backup_state boolean,p_auth_policy_hash text,p_expires_at_seconds bigint
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $record$
DECLARE ctx record; old ctt_staff_private.review_assertion_issuances%ROWTYPE;
BEGIN
  SELECT * INTO ctx FROM ctt_staff_private.load_review_assertion_context(p_attempt_id);
  IF NOT FOUND OR p_proof_sha256 IS NULL OR p_proof_sha256 !~ '^sha256:[0-9a-f]{64}$'
    OR p_prior_counter IS DISTINCT FROM ctx.sign_count OR p_new_counter IS NULL
    OR p_new_counter<0 OR p_new_counter>4294967295
    OR NOT ((p_auth_profile='platform-passkey-v1' AND p_prior_counter=0 AND p_new_counter=0) OR p_new_counter>p_prior_counter)
    OR p_auth_profile IS DISTINCT FROM ctx.auth_profile OR p_backup_eligible IS DISTINCT FROM ctx.backup_eligible
    OR p_backup_state IS NULL OR (p_backup_state AND NOT p_backup_eligible)
    OR (p_auth_profile='device-counter-v1' AND p_backup_state)
    OR p_auth_policy_hash IS DISTINCT FROM ctx.auth_policy_hash
    OR p_expires_at_seconds IS DISTINCT FROM ctx.expires_at_seconds THEN
    RAISE EXCEPTION 'CTT assertion issuance denied' USING ERRCODE='42501';
  END IF;
  SELECT * INTO old FROM ctt_staff_private.review_assertion_issuances i WHERE i.challenge_id=ctx.challenge_id;
  IF FOUND THEN
    IF old.attempt_id IS DISTINCT FROM p_attempt_id OR old.proof_sha256 IS DISTINCT FROM p_proof_sha256
      OR old.prior_counter IS DISTINCT FROM p_prior_counter OR old.new_counter IS DISTINCT FROM p_new_counter
      OR old.auth_profile IS DISTINCT FROM p_auth_profile OR old.backup_eligible IS DISTINCT FROM p_backup_eligible
      OR old.backup_state IS DISTINCT FROM p_backup_state OR old.auth_policy_hash IS DISTINCT FROM p_auth_policy_hash
      OR old.expires_at_seconds IS DISTINCT FROM p_expires_at_seconds THEN
      RAISE EXCEPTION 'CTT assertion issuance conflict' USING ERRCODE='42501';
    END IF;
    RETURN true;
  END IF;
  INSERT INTO ctt_staff_private.review_assertion_issuances
    (challenge_id,attempt_id,proof_sha256,prior_counter,new_counter,auth_profile,backup_eligible,backup_state,auth_policy_hash,expires_at_seconds)
  VALUES(ctx.challenge_id,p_attempt_id,p_proof_sha256,p_prior_counter,p_new_counter,p_auth_profile,p_backup_eligible,p_backup_state,p_auth_policy_hash,p_expires_at_seconds);
  RETURN true;
END
$record$;
CREATE OR REPLACE FUNCTION ctt_staff_private.record_signed_assertion_guard(
  p_challenge_id uuid,p_proof_sha256 text,p_expires_at_seconds bigint
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM ctt_staff_private.review_assertion_issuances i
    JOIN ctt_staff_private.review_challenge_consumptions co ON co.challenge_id=i.challenge_id
    WHERE i.challenge_id=p_challenge_id AND i.proof_sha256=p_proof_sha256
      AND i.expires_at_seconds=p_expires_at_seconds AND i.prior_counter=co.prior_counter AND i.new_counter=co.new_counter
      AND i.auth_profile=co.auth_profile AND i.backup_eligible=co.backup_eligible AND i.backup_state=co.backup_state
      AND i.auth_policy_hash IS NOT DISTINCT FROM co.auth_policy_hash
  ) THEN RAISE EXCEPTION 'CTT assertion issuance missing or mismatched' USING ERRCODE='42501'; END IF;
  INSERT INTO ctt_staff_private.signed_assertion_guards(challenge_id,proof_sha256,expires_at_seconds)
  VALUES(p_challenge_id,p_proof_sha256,p_expires_at_seconds);
END
$guard$;
DO $secure$
DECLARE signature text; owner_oid oid;
BEGIN
  SELECT oid INTO owner_oid FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner';
  FOREACH signature IN ARRAY ARRAY['load_review_assertion_context(uuid)',
    'record_review_assertion_issuance(uuid,text,bigint,bigint,text,boolean,boolean,text,bigint)',
    'record_signed_assertion_guard(uuid,text,bigint)'] LOOP
    EXECUTE 'ALTER FUNCTION ctt_staff_private.'||signature||' OWNER TO samchat_ctt_staff_owner';
    EXECUTE 'REVOKE ALL ON FUNCTION ctt_staff_private.'||signature||' FROM PUBLIC,devnous_user';
  END LOOP;
  IF EXISTS(SELECT 1 FROM pg_catalog.aclexplode((SELECT relacl FROM pg_catalog.pg_class
    WHERE oid='ctt_staff_private.review_assertion_issuances'::regclass)) a WHERE a.grantee<>owner_oid)
    OR EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid='ctt_staff_private.review_assertion_issuances'::regclass
      AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL)
    OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL
      pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
      WHERE p.oid IN ('ctt_staff_private.load_review_assertion_context(uuid)'::regprocedure,
        'ctt_staff_private.record_review_assertion_issuance(uuid,text,bigint,bigint,text,boolean,boolean,text,bigint)'::regprocedure,
        'ctt_staff_private.record_signed_assertion_guard(uuid,text,bigint)'::regprocedure)
      AND (p.proowner<>owner_oid OR a.grantee<>owner_oid OR NOT p.prosecdef
        OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']::text[])) THEN
    RAISE EXCEPTION 'CTT issuer ACL invalid';
  END IF;
END
$secure$;
COMMIT;
