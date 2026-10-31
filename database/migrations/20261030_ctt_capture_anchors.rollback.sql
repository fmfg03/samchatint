-- Only empty candidate anchors can be removed; preserve recorded evidence.
BEGIN;
LOCK TABLE ctt_staff_private.manifest_anchors, ctt_staff_private.source_anchors,
  ctt_staff_private.capture_attempt_anchors IN ACCESS EXCLUSIVE MODE;
DO $rollback$
BEGIN
  IF EXISTS (SELECT 1 FROM ctt_staff_private.manifest_anchors)
    OR EXISTS (SELECT 1 FROM ctt_staff_private.source_anchors)
    OR EXISTS (SELECT 1 FROM ctt_staff_private.capture_attempt_anchors) THEN
    RAISE EXCEPTION 'CTT anchor rollback requires empty state';
  END IF;
END
$rollback$;
DROP FUNCTION ctt_staff_private.lock_capture_anchor(uuid) RESTRICT;
DROP TABLE ctt_staff_private.capture_attempt_anchors RESTRICT;
DROP TABLE ctt_staff_private.source_anchors RESTRICT;
DROP TABLE ctt_staff_private.manifest_anchors RESTRICT;
DROP FUNCTION ctt_staff_private.validate_capture_anchor() RESTRICT;
COMMIT;
