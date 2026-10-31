BEGIN;
LOCK TABLE ctt_staff_private.signed_assertion_guards IN ACCESS EXCLUSIVE MODE;
DO $rollback$
BEGIN
  IF EXISTS (SELECT 1 FROM ctt_staff_private.signed_assertion_guards) THEN
    RAISE EXCEPTION 'CTT signed assertion guard rollback requires empty state';
  END IF;
END
$rollback$;
DROP FUNCTION ctt_staff_private.record_signed_assertion_guard(uuid,text,bigint) RESTRICT;
DROP TABLE ctt_staff_private.signed_assertion_guards RESTRICT;
DROP FUNCTION ctt_staff_private.check_signed_assertion_expiry() RESTRICT;
COMMIT;
