-- Safe only before any issued assertion or consumption guard exists.
BEGIN;
LOCK TABLE ctt_staff_private.review_assertion_issuances,
  ctt_staff_private.signed_assertion_guards IN ACCESS EXCLUSIVE MODE;
DO $safe$
BEGIN
  IF EXISTS(SELECT 1 FROM ctt_staff_private.review_assertion_issuances)
    OR EXISTS(SELECT 1 FROM ctt_staff_private.signed_assertion_guards) THEN
    RAISE EXCEPTION 'CTT issuer rollback denied: evidence exists';
  END IF;
END
$safe$;
CREATE OR REPLACE FUNCTION ctt_staff_private.record_signed_assertion_guard(
  p_challenge_id uuid, p_proof_sha256 text, p_expires_at_seconds bigint
) RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $record$
  INSERT INTO ctt_staff_private.signed_assertion_guards
    (challenge_id,proof_sha256,expires_at_seconds)
    VALUES (p_challenge_id,p_proof_sha256,p_expires_at_seconds)
$record$;
ALTER FUNCTION ctt_staff_private.record_signed_assertion_guard(uuid,text,bigint) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.record_signed_assertion_guard(uuid,text,bigint) FROM PUBLIC,devnous_user;
DROP FUNCTION ctt_staff_private.record_review_assertion_issuance(uuid,text,bigint,bigint,text,boolean,boolean,text,bigint);
DROP FUNCTION ctt_staff_private.load_review_assertion_context(uuid);
DROP TABLE ctt_staff_private.review_assertion_issuances;
COMMIT;
