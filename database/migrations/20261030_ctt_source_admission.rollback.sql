BEGIN;
LOCK TABLE ctt_staff_private.source_admissions,ctt_staff_private.source_admission_documents,
 ctt_staff_private.source_admission_pages,ctt_staff_private.capture_attempt_anchors IN ACCESS EXCLUSIVE MODE;
DO $rollback$
BEGIN
 IF EXISTS(SELECT 1 FROM ctt_staff_private.source_admissions)
   OR EXISTS(SELECT 1 FROM ctt_staff_private.source_admission_documents)
   OR EXISTS(SELECT 1 FROM ctt_staff_private.source_admission_pages)
   OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_anchors) THEN
   RAISE EXCEPTION 'CTT source admission rollback requires empty state';
 END IF;
END
$rollback$;
DROP TRIGGER ctt_capture_anchor_source_admission ON ctt_staff_private.capture_attempt_anchors;
DROP FUNCTION ctt_staff_private.check_capture_source_admission() RESTRICT;
DROP FUNCTION ctt_staff_private.register_source_admission(uuid,bytea,text,bigint,bigint,bytea) RESTRICT;
DROP TABLE ctt_staff_private.source_admission_pages RESTRICT;
DROP TABLE ctt_staff_private.source_admission_documents RESTRICT;
DROP TABLE ctt_staff_private.source_admissions RESTRICT;
COMMIT;
