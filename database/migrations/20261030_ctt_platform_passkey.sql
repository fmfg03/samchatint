-- Candidate protected platform-passkey profile. No enrollment or grants.
-- Verified WebAuthn assertion remains a prerequisite in the trusted writer.
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
ALTER TABLE ctt_staff_private.review_credentials
  ADD COLUMN auth_profile text NOT NULL DEFAULT 'device-counter-v1',
  ADD COLUMN backup_eligible boolean NOT NULL DEFAULT false,
  ADD COLUMN backup_state boolean NOT NULL DEFAULT false,
  ADD COLUMN auth_policy_hash text;
ALTER TABLE ctt_staff_private.review_credentials ADD CONSTRAINT ctt_credential_auth_profile CHECK (
  (auth_profile='device-counter-v1' AND NOT backup_eligible AND NOT backup_state AND auth_policy_hash IS NULL)
  OR (auth_profile='platform-passkey-v1' AND (backup_eligible OR NOT backup_state)
    AND auth_policy_hash IS NOT NULL AND auth_policy_hash='sha256:e0a2e1ce21182519a8739ffa11aba21164d565871bf62909cf9746861dfa48d3')
);
ALTER TABLE ctt_staff_private.review_challenge_consumptions
  ADD COLUMN auth_profile text NOT NULL DEFAULT 'device-counter-v1',
  ADD COLUMN backup_eligible boolean NOT NULL DEFAULT false,
  ADD COLUMN backup_state boolean NOT NULL DEFAULT false,
  ADD COLUMN auth_policy_hash text,
  DROP CONSTRAINT review_challenge_consumptions_new_counter_check,
  DROP CONSTRAINT review_challenge_consumptions_check;
ALTER TABLE ctt_staff_private.review_challenge_consumptions ADD CONSTRAINT ctt_consumption_auth_profile CHECK (
  new_counter BETWEEN 0 AND 4294967295 AND (
    (auth_profile='device-counter-v1' AND new_counter>prior_counter
      AND NOT backup_eligible AND NOT backup_state AND auth_policy_hash IS NULL)
    OR (auth_profile='platform-passkey-v1' AND (backup_eligible OR NOT backup_state)
      AND auth_policy_hash IS NOT NULL AND auth_policy_hash='sha256:e0a2e1ce21182519a8739ffa11aba21164d565871bf62909cf9746861dfa48d3'
      AND ((prior_counter=0 AND new_counter=0) OR new_counter>prior_counter))
  )
);
CREATE TABLE ctt_staff_private.review_authenticator_audit (
  audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  credential_record_id uuid NOT NULL REFERENCES ctt_staff_private.review_credentials(credential_record_id) ON DELETE RESTRICT,
  old_profile text, new_profile text NOT NULL,
  old_policy_hash text, new_policy_hash text,
  old_backup_eligible boolean, new_backup_eligible boolean NOT NULL,
  old_backup_state boolean, new_backup_state boolean NOT NULL,
  authority_version bigint NOT NULL CHECK(authority_version>0),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  recorded_by name NOT NULL DEFAULT session_user
);
ALTER TABLE ctt_staff_private.review_authenticator_audit OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.review_authenticator_audit FROM PUBLIC,devnous_user;
REVOKE ALL ON SEQUENCE ctt_staff_private.review_authenticator_audit_audit_id_seq FROM PUBLIC,devnous_user;
CREATE TRIGGER ctt_authenticator_audit_immutable BEFORE UPDATE OR DELETE
ON ctt_staff_private.review_authenticator_audit FOR EACH ROW
EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_authenticator_audit_no_truncate BEFORE TRUNCATE
ON ctt_staff_private.review_authenticator_audit FOR EACH STATEMENT
EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();

CREATE FUNCTION ctt_staff_private.guard_authenticator_profile()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $function$
BEGIN
  IF (NEW.auth_profile IS DISTINCT FROM OLD.auth_profile
    OR NEW.auth_policy_hash IS DISTINCT FROM OLD.auth_policy_hash
    OR NEW.backup_eligible IS DISTINCT FROM OLD.backup_eligible)
    AND NEW.authority_version<=OLD.authority_version THEN
    RAISE EXCEPTION 'CTT authenticator authority version invalid' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END
$function$;
CREATE TRIGGER ctt_authenticator_profile_version BEFORE UPDATE
ON ctt_staff_private.review_credentials FOR EACH ROW
EXECUTE FUNCTION ctt_staff_private.guard_authenticator_profile();
CREATE FUNCTION ctt_staff_private.audit_authenticator_profile()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $function$
BEGIN
  IF TG_OP='UPDATE' AND NEW.auth_profile IS NOT DISTINCT FROM OLD.auth_profile
    AND NEW.auth_policy_hash IS NOT DISTINCT FROM OLD.auth_policy_hash
    AND NEW.backup_eligible IS NOT DISTINCT FROM OLD.backup_eligible
    AND NEW.backup_state IS NOT DISTINCT FROM OLD.backup_state THEN RETURN NEW; END IF;
  INSERT INTO ctt_staff_private.review_authenticator_audit(
    credential_record_id,old_profile,new_profile,old_policy_hash,new_policy_hash,
    old_backup_eligible,new_backup_eligible,old_backup_state,new_backup_state,authority_version)
  VALUES(NEW.credential_record_id,CASE WHEN TG_OP='UPDATE' THEN OLD.auth_profile END,NEW.auth_profile,
    CASE WHEN TG_OP='UPDATE' THEN OLD.auth_policy_hash END,NEW.auth_policy_hash,
    CASE WHEN TG_OP='UPDATE' THEN OLD.backup_eligible END,NEW.backup_eligible,
    CASE WHEN TG_OP='UPDATE' THEN OLD.backup_state END,NEW.backup_state,NEW.authority_version);
  RETURN NEW;
END
$function$;
CREATE TRIGGER ctt_authenticator_profile_audit AFTER INSERT OR UPDATE
ON ctt_staff_private.review_credentials FOR EACH ROW
EXECUTE FUNCTION ctt_staff_private.audit_authenticator_profile();

-- The unchanged v1 consumer inserts device defaults. This guard prevents it
-- from consuming platform credentials, including with a positive counter.
CREATE FUNCTION ctt_staff_private.validate_authenticator_consumption()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $function$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM ctt_staff_private.review_credentials cr
    WHERE cr.credential_id=NEW.credential_id AND cr.auth_profile=NEW.auth_profile
      AND cr.auth_policy_hash IS NOT DISTINCT FROM NEW.auth_policy_hash
      AND cr.backup_eligible=NEW.backup_eligible AND cr.backup_state=NEW.backup_state
  ) THEN RAISE EXCEPTION 'CTT authenticator consumption denied' USING ERRCODE='42501'; END IF;
  RETURN NEW;
END
$function$;
CREATE TRIGGER ctt_authenticator_consumption_initial BEFORE INSERT
ON ctt_staff_private.review_challenge_consumptions FOR EACH ROW
EXECUTE FUNCTION ctt_staff_private.validate_authenticator_consumption();
CREATE CONSTRAINT TRIGGER ctt_authenticator_consumption_commit
AFTER INSERT ON ctt_staff_private.review_challenge_consumptions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION ctt_staff_private.validate_authenticator_consumption();
CREATE FUNCTION ctt_staff_private.consume_review_challenge_v2(
    p_challenge_id uuid, p_principal_id uuid, p_credential_id bytea,
    p_scope_hash text, p_prior_counter bigint, p_new_counter bigint,
    p_auth_profile text, p_backup_eligible boolean, p_backup_state boolean, p_auth_policy_hash text
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $consume$
DECLARE
    item ctt_staff_private.review_challenges%ROWTYPE;
    principal ctt_staff_private.review_principals%ROWTYPE;
    credential ctt_staff_private.review_credentials%ROWTYPE;
    faculty ctt_staff_private.review_faculties%ROWTYPE;
    checked_at timestamptz;
BEGIN
    SELECT * INTO item FROM ctt_staff_private.review_challenges
      WHERE challenge_id=p_challenge_id FOR UPDATE;
    IF NOT FOUND OR item.principal_id IS DISTINCT FROM p_principal_id
       OR item.credential_id IS DISTINCT FROM p_credential_id
       OR item.scope_hash IS DISTINCT FROM p_scope_hash
       OR p_prior_counter IS NULL OR p_new_counter IS NULL
       OR p_prior_counter < 0 OR p_new_counter < 0
       OR NOT ((p_prior_counter=0 AND p_new_counter=0) OR p_new_counter>p_prior_counter)
       OR p_auth_profile IS DISTINCT FROM 'platform-passkey-v1'
       OR p_auth_policy_hash IS DISTINCT FROM 'sha256:e0a2e1ce21182519a8739ffa11aba21164d565871bf62909cf9746861dfa48d3'
       OR p_backup_eligible IS NULL OR p_backup_state IS NULL
       OR (p_backup_state AND NOT p_backup_eligible)
       OR p_new_counter > 4294967295
       OR EXISTS (SELECT 1 FROM ctt_staff_private.review_challenge_consumptions
                  WHERE challenge_id=p_challenge_id) THEN
        RAISE EXCEPTION 'CTT review challenge denied' USING ERRCODE='42501';
    END IF;
    SELECT * INTO principal FROM ctt_staff_private.review_principals
      WHERE principal_id=item.principal_id FOR UPDATE;
    SELECT f.* INTO faculty FROM ctt_staff_private.review_faculties AS f
      WHERE f.principal_id=item.principal_id AND f.tenant_id=item.tenant_id
        AND f.edition_id=item.edition_id AND f.faculty=item.faculty FOR UPDATE;
    SELECT * INTO credential FROM ctt_staff_private.review_credentials
      WHERE credential_id=item.credential_id AND principal_id=item.principal_id FOR UPDATE;
    -- Re-read time after all potentially blocking row locks have completed.
    checked_at := pg_catalog.clock_timestamp();
    IF principal.principal_id IS NULL OR NOT principal.active
       OR principal.authority_version <> item.principal_authority_version
       OR faculty.principal_id IS NULL OR faculty.revoked_at IS NOT NULL
       OR faculty.authority_version <> item.faculty_authority_version
       OR checked_at < item.issued_at OR checked_at >= item.expires_at
       OR checked_at < faculty.valid_from OR checked_at >= faculty.valid_until
       OR credential.credential_id IS NULL OR credential.revoked_at IS NOT NULL
       OR credential.authority_version <> item.credential_authority_version
       OR credential.sign_count IS DISTINCT FROM p_prior_counter
       OR credential.auth_profile IS DISTINCT FROM p_auth_profile
       OR credential.auth_policy_hash IS DISTINCT FROM p_auth_policy_hash
       OR credential.backup_eligible IS DISTINCT FROM p_backup_eligible THEN
        RAISE EXCEPTION 'CTT review challenge denied' USING ERRCODE='42501';
    END IF;
    UPDATE ctt_staff_private.review_credentials SET sign_count=p_new_counter,backup_state=p_backup_state
      WHERE credential_id=item.credential_id;
    INSERT INTO ctt_staff_private.review_challenge_consumptions
      (challenge_id,credential_id,prior_counter,new_counter,consumed_at,
       auth_profile,backup_eligible,backup_state,auth_policy_hash)
      VALUES (p_challenge_id,p_credential_id,p_prior_counter,p_new_counter,checked_at,
        p_auth_profile,p_backup_eligible,p_backup_state,p_auth_policy_hash);
    RETURN true;
END
$consume$;
ALTER FUNCTION ctt_staff_private.consume_review_challenge_v2(uuid,uuid,bytea,text,bigint,bigint,text,boolean,boolean,text) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.consume_review_challenge_v2(uuid,uuid,bytea,text,bigint,bigint,text,boolean,boolean,text) FROM PUBLIC, devnous_user;


DO $secure$
DECLARE fname text; private_owner oid;
BEGIN
  SELECT oid INTO private_owner FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner';
  FOREACH fname IN ARRAY ARRAY['guard_authenticator_profile()', 'audit_authenticator_profile()',
    'validate_authenticator_consumption()'] LOOP
    EXECUTE 'ALTER FUNCTION ctt_staff_private.'||fname||' OWNER TO samchat_ctt_staff_owner';
    EXECUTE 'REVOKE ALL ON FUNCTION ctt_staff_private.'||fname||' FROM PUBLIC,devnous_user';
  END LOOP;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c CROSS JOIN LATERAL
      pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault(CASE WHEN c.relkind='S' THEN 'S'::"char" ELSE 'r'::"char" END,c.relowner))) a
    WHERE c.oid IN ('ctt_staff_private.review_authenticator_audit'::regclass,
      'ctt_staff_private.review_authenticator_audit_audit_id_seq'::regclass)
      AND (c.relowner<>private_owner OR a.grantee<>private_owner)
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL
      pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
    WHERE p.pronamespace='ctt_staff_private'::regnamespace AND p.proname IN (
      'consume_review_challenge_v2','guard_authenticator_profile',
      'audit_authenticator_profile','validate_authenticator_consumption')
      AND (p.proowner<>private_owner OR a.grantee<>private_owner OR NOT p.prosecdef
        OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']::text[])
  ) THEN RAISE EXCEPTION 'CTT platform ACL invalid'; END IF;
END
$secure$;
COMMIT;
