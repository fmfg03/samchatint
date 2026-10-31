BEGIN;
LOCK TABLE ctt_staff_private.transport_nonces IN ACCESS EXCLUSIVE MODE;
DO $rollback$
BEGIN
  IF EXISTS (SELECT 1 FROM ctt_staff_private.transport_nonces) THEN
    RAISE EXCEPTION 'CTT transport nonce rollback requires empty state';
  END IF;
END
$rollback$;
DROP FUNCTION ctt_staff_private.claim_transport_nonce(uuid,bigint) RESTRICT;
DROP TABLE ctt_staff_private.transport_nonces RESTRICT;
COMMIT;
