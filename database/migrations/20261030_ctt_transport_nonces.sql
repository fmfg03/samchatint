-- Isolated candidate: durable channel replay protection, not capture approval.
BEGIN;
DO $preflight$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
    WHERE n.nspname='ctt_staff_private' AND r.rolname='samchat_ctt_staff_owner'
      AND NOT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreaterole
      AND NOT r.rolbypassrls AND NOT r.rolcreatedb AND NOT r.rolreplication
  ) OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','CREATE,USAGE')
    OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.rolname='samchat_ctt_staff_owner'
               WHERE m.roleid=r.oid OR m.member=r.oid)
    OR NOT EXISTS (
      SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
      WHERE p.oid=to_regprocedure('ctt_staff_private.reject_review_evidence_mutation()')
        AND r.rolname='samchat_ctt_staff_owner' AND p.prosecdef
        AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
        AND NOT EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner)
    ) THEN RAISE EXCEPTION 'CTT transport nonce prerequisites failed'; END IF;
END
$preflight$;
CREATE TABLE ctt_staff_private.transport_nonces (
  nonce uuid PRIMARY KEY,
  expires_at bigint NOT NULL CHECK(expires_at>=0),
  consumed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE ctt_staff_private.transport_nonces OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.transport_nonces FROM PUBLIC,devnous_user;
CREATE FUNCTION ctt_staff_private.claim_transport_nonce(p_nonce uuid,p_expires_at bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $claim$
DECLARE changed bigint;
BEGIN
  IF p_nonce IS NULL OR p_expires_at IS NULL OR p_expires_at<0
    OR p_expires_at < floor(extract(epoch FROM clock_timestamp()))
    OR p_expires_at > floor(extract(epoch FROM clock_timestamp()))+120 THEN
    RETURN false;
  END IF;
  INSERT INTO ctt_staff_private.transport_nonces(nonce,expires_at)
    VALUES(p_nonce,p_expires_at) ON CONFLICT(nonce) DO NOTHING;
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$claim$;
ALTER FUNCTION ctt_staff_private.claim_transport_nonce(uuid,bigint) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.claim_transport_nonce(uuid,bigint) FROM PUBLIC,devnous_user;
CREATE TRIGGER ctt_transport_nonce_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.transport_nonces
  FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_transport_nonce_no_truncate BEFORE TRUNCATE ON ctt_staff_private.transport_nonces
  FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
DO $postflight$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
      WHERE c.oid='ctt_staff_private.transport_nonces'::regclass AND a.grantee<>c.relowner)
    OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='ctt_staff_private.transport_nonces'::regclass AND attacl IS NOT NULL)
    OR EXISTS (SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
      WHERE p.oid='ctt_staff_private.claim_transport_nonce(uuid,bigint)'::regprocedure AND a.grantee<>p.proowner)
  THEN RAISE EXCEPTION 'CTT transport nonce ACL invalid'; END IF;
END
$postflight$;
COMMIT;
