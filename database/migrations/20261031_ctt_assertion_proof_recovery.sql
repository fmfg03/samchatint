-- Retain the exact signed proof atomically with its existing issuance audit.
-- No operational roles, keys, credentials or grants are seeded.
BEGIN;
DO $preflight$
DECLARE owner_id oid; obj record;
BEGIN
 SELECT oid INTO owner_id FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner'
 AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
 AND NOT rolreplication AND NOT rolbypassrls;
 IF owner_id IS NULL OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id)
 OR has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE')
 OR current_setting('max_prepared_transactions')::int<>0 THEN RAISE EXCEPTION 'CTT proof recovery isolation invalid'; END IF;
 FOR obj IN SELECT * FROM (VALUES('review_assertion_issuances'),('capture_attempt_anchors')) v(name) LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=to_regclass('ctt_staff_private.'||obj.name)
 AND c.relowner=owner_id AND c.relkind='r' AND NOT c.relrowsecurity
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a WHERE a.grantee<>owner_id)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL)) THEN
 RAISE EXCEPTION 'CTT proof recovery dependency invalid'; END IF; END LOOP;
 FOR obj IN SELECT * FROM (VALUES('check_review_service_identity()'),('load_review_assertion_context(uuid)'),
 ('record_review_assertion_issuance(uuid,text,bigint,bigint,text,boolean,boolean,text,bigint)'),('reject_review_evidence_mutation()')) v(signature) LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure('ctt_staff_private.'||obj.signature)
 AND p.proowner=owner_id AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a WHERE a.grantee<>owner_id)) THEN
 RAISE EXCEPTION 'CTT proof recovery function dependency invalid'; END IF; END LOOP;
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
 JOIN pg_catalog.pg_roles r ON r.oid=e.extowner JOIN pg_catalog.pg_proc p ON p.oid=d.objid
 JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pgcrypto' AND r.rolsuper
 AND n.nspname='public' AND p.proowner=e.extowner AND d.objid='public.digest(bytea,text)'::regprocedure AND d.deptype='e') THEN
 RAISE EXCEPTION 'CTT proof recovery digest invalid'; END IF;
END
$preflight$;
CREATE TABLE ctt_staff_private.issuer_proofs(
 attempt_id uuid PRIMARY KEY REFERENCES ctt_staff_private.review_assertion_issuances(attempt_id) ON DELETE RESTRICT
 REFERENCES ctt_staff_private.capture_attempt_anchors(attempt_id) ON DELETE RESTRICT,
 signed_review bytea NOT NULL CHECK(octet_length(signed_review) BETWEEN 2 AND 8192)
);
ALTER TABLE ctt_staff_private.issuer_proofs OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.issuer_proofs FROM PUBLIC,devnous_user;
CREATE TRIGGER ctt_issuer_proof_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.issuer_proofs
 FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_issuer_proof_no_truncate BEFORE TRUNCATE ON ctt_staff_private.issuer_proofs
 FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE FUNCTION ctt_staff_private.record_assertion_proof(p_attempt uuid,p_proof bytea)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE issuance record; old bytea;
BEGIN
 PERFORM ctt_staff_private.check_review_service_identity();
 IF NOT has_function_privilege(session_user,'ctt_staff_private.load_review_assertion_context(uuid)','EXECUTE')
 OR NOT has_function_privilege(session_user,'ctt_staff_private.record_review_assertion_issuance(uuid,text,bigint,bigint,text,boolean,boolean,text,bigint)','EXECUTE')
 OR p_attempt IS NULL OR p_proof IS NULL OR octet_length(p_proof) NOT BETWEEN 2 AND 8192 THEN
 RAISE EXCEPTION 'CTT issuer proof denied' USING ERRCODE='42501'; END IF;
 SELECT i.proof_sha256,i.xmin::text AS issuance_xid INTO issuance
 FROM ctt_staff_private.review_assertion_issuances i WHERE i.attempt_id=p_attempt FOR SHARE;
 IF issuance.proof_sha256 IS NULL OR issuance.proof_sha256 IS DISTINCT FROM 'sha256:'||encode(public.digest(p_proof,'sha256'),'hex') THEN
 RAISE EXCEPTION 'CTT issuer proof audit mismatch' USING ERRCODE='42501'; END IF;
 SELECT signed_review INTO old FROM ctt_staff_private.issuer_proofs WHERE attempt_id=p_attempt;
 IF old IS NOT NULL THEN
 IF old IS DISTINCT FROM p_proof THEN RAISE EXCEPTION 'CTT issuer proof retry differs' USING ERRCODE='42501'; END IF;
 RETURN true; END IF;
 IF issuance.issuance_xid::bigint<>mod(pg_current_xact_id()::text::numeric,4294967296)::bigint THEN
 RAISE EXCEPTION 'CTT issuer proof must be atomic' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.issuer_proofs(attempt_id,signed_review) VALUES(p_attempt,p_proof);
 RETURN true;
END
$fn$;
ALTER FUNCTION ctt_staff_private.record_assertion_proof(uuid,bytea) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.record_assertion_proof(uuid,bytea) FROM PUBLIC,devnous_user;
DO $postflight$
DECLARE owner_id oid;
BEGIN
 SELECT oid INTO owner_id FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner';
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_class c CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
 WHERE c.oid='ctt_staff_private.issuer_proofs'::regclass AND (c.relowner<>owner_id OR c.relkind<>'r' OR c.relrowsecurity OR a.grantee<>owner_id
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute att WHERE att.attrelid=c.oid AND att.attnum>0 AND NOT att.attisdropped AND att.attacl IS NOT NULL)))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE p.oid='ctt_staff_private.record_assertion_proof(uuid,bytea)'::regprocedure AND
 (p.proowner<>owner_id OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']::text[] OR a.grantee<>owner_id)) THEN
 RAISE EXCEPTION 'CTT issuer proof installed ACL invalid'; END IF;
END
$postflight$;
COMMIT;
