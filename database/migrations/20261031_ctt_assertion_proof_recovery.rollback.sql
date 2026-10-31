-- Roll back schema only, after proposal rollback; preserve every retained proof.
BEGIN;
LOCK TABLE ctt_staff_private.issuer_proofs IN ACCESS EXCLUSIVE MODE;
DO $rollback$
BEGIN
 IF EXISTS(SELECT 1 FROM ctt_staff_private.issuer_proofs) THEN
 RAISE EXCEPTION 'CTT issuer proof rollback refuses used evidence'; END IF;
END
$rollback$;
DROP FUNCTION ctt_staff_private.record_assertion_proof(uuid,bytea);
DROP TABLE ctt_staff_private.issuer_proofs;
COMMIT;
