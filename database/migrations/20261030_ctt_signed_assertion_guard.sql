-- Candidate only: proof expiry guard, not signature/identity verification.
-- Requires trusted writer to verify signature and use this in the same tx.
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
  IF pg_catalog.current_setting('max_prepared_transactions')::integer <> 0
    OR pg_catalog.to_regclass('ctt_staff_private.review_challenge_consumptions') IS NULL
    OR NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
      WHERE c.oid=pg_catalog.to_regclass('ctt_staff_private.review_challenge_consumptions')
        AND c.relkind='r' AND r.rolname='samchat_ctt_staff_owner'
        AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
          WHERE a.grantee <> c.relowner
        ) AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid
            AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
        )
    ) OR (
      SELECT count(*) FROM pg_catalog.pg_trigger t
      WHERE t.tgrelid=pg_catalog.to_regclass('ctt_staff_private.review_challenge_consumptions')
        AND t.tgname IN ('ctt_review_consumption_immutable','ctt_review_consumption_no_truncate')
        AND t.tgenabled IN ('O','A')
        AND t.tgfoid=pg_catalog.to_regprocedure('ctt_staff_private.reject_review_evidence_mutation()')
    ) <> 2 THEN
      RAISE EXCEPTION 'CTT assertion protected dependencies invalid';
  END IF;
END
$preflight$;
CREATE TABLE ctt_staff_private.signed_assertion_guards (
  challenge_id uuid PRIMARY KEY REFERENCES ctt_staff_private.review_challenge_consumptions(challenge_id) ON DELETE RESTRICT,
  proof_sha256 text NOT NULL CHECK (proof_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  expires_at_seconds bigint NOT NULL CHECK (expires_at_seconds BETWEEN 1 AND 253402300799)
);
ALTER TABLE ctt_staff_private.signed_assertion_guards OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.signed_assertion_guards FROM PUBLIC, devnous_user;

CREATE FUNCTION ctt_staff_private.check_signed_assertion_expiry()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $guard$
DECLARE db_expiry timestamptz;
BEGIN
  SELECT c.expires_at INTO db_expiry FROM ctt_staff_private.review_challenges c
    WHERE c.challenge_id=NEW.challenge_id;
  IF NOT FOUND OR NEW.expires_at_seconds <> floor(extract(epoch FROM db_expiry))::bigint
     OR pg_catalog.clock_timestamp() >= pg_catalog.to_timestamp(NEW.expires_at_seconds::double precision)
  THEN RAISE EXCEPTION 'CTT signed assertion expired or mismatched' USING ERRCODE='42501'; END IF;
  RETURN NEW;
END
$guard$;
ALTER FUNCTION ctt_staff_private.check_signed_assertion_expiry() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.check_signed_assertion_expiry() FROM PUBLIC, devnous_user;
CREATE TRIGGER ctt_assertion_guard_initial BEFORE INSERT ON ctt_staff_private.signed_assertion_guards
  FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.check_signed_assertion_expiry();
CREATE CONSTRAINT TRIGGER ctt_assertion_guard_commit AFTER INSERT ON ctt_staff_private.signed_assertion_guards
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.check_signed_assertion_expiry();
CREATE TRIGGER ctt_assertion_guard_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.signed_assertion_guards
  FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_assertion_guard_no_truncate BEFORE TRUNCATE ON ctt_staff_private.signed_assertion_guards
  FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
-- A future dedicated writer may receive EXECUTE, never direct table writes.
-- This function does not verify the signature; only a trusted writer can call.
CREATE FUNCTION ctt_staff_private.record_signed_assertion_guard(
  p_challenge_id uuid, p_proof_sha256 text, p_expires_at_seconds bigint
) RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $record$
  INSERT INTO ctt_staff_private.signed_assertion_guards
    (challenge_id,proof_sha256,expires_at_seconds)
    VALUES (p_challenge_id,p_proof_sha256,p_expires_at_seconds)
$record$;
ALTER FUNCTION ctt_staff_private.record_signed_assertion_guard(uuid,text,bigint) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.record_signed_assertion_guard(uuid,text,bigint) FROM PUBLIC, devnous_user;
DO $postflight$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
    WHERE c.oid='ctt_staff_private.signed_assertion_guards'::regclass AND a.grantee <> c.relowner
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_attribute a
    WHERE a.attrelid='ctt_staff_private.signed_assertion_guards'::regclass
      AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p
    CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
    WHERE p.oid IN ('ctt_staff_private.check_signed_assertion_expiry()'::regprocedure,
      'ctt_staff_private.record_signed_assertion_guard(uuid,text,bigint)'::regprocedure)
      AND a.grantee <> p.proowner
  ) THEN RAISE EXCEPTION 'CTT signed assertion guard ACL invalid'; END IF;
END
$postflight$;
COMMIT;
