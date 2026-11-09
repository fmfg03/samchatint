BEGIN;
LOCK TABLE ctt_staff_private.capture_payloads,ctt_staff_private.capture_results IN ACCESS EXCLUSIVE MODE;
DO $rollback$
BEGIN
 IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_payloads) OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_results) THEN
   RAISE EXCEPTION 'CTT capture payload rollback requires empty state';
 END IF;
END
$rollback$;
DROP FUNCTION ctt_staff_private.record_capture_result(uuid,uuid,text) RESTRICT;
DROP FUNCTION ctt_staff_private.get_capture_result(uuid) RESTRICT;
DROP FUNCTION ctt_staff_private.load_capture_payload(uuid) RESTRICT;
DROP TABLE ctt_staff_private.capture_results RESTRICT;
DROP TABLE ctt_staff_private.capture_payloads RESTRICT;
COMMIT;
