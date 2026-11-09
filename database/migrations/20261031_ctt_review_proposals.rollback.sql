-- Schema rollback only: never discard admitted review or recovery evidence.
BEGIN;
LOCK TABLE ctt_staff_private.review_proposals,ctt_staff_private.review_proposal_attempts,
 ctt_staff_private.review_proposal_heads,ctt_staff_private.review_proposal_events,
 ctt_staff_private.review_materializations,ctt_staff_private.review_proposal_baseline IN ACCESS EXCLUSIVE MODE;
DO $rollback$
DECLARE item record; names text[]:=ARRAY['review_document_session','resolve_review_source_scope','review_raw_snapshot',
 'review_existing_photos','load_review_proposal_context','review_proposal_json','register_review_proposal',
 'review_photo_duplicate','check_review_proposal_policy','assert_current_review_proposal','read_review_proposal',
 'create_proposal_challenge','cancel_review_proposal','register_proposal_capture_payload','review_proposal_status',
 'check_proposal_consumption_current','review_session_commit_authorized','commit_proposal_review_session','project_review_capture_rows','load_review_finality_gate_context'];
BEGIN
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_proposals) OR EXISTS(SELECT 1 FROM ctt_staff_private.review_proposal_attempts)
 OR EXISTS(SELECT 1 FROM ctt_staff_private.review_proposal_heads) OR EXISTS(SELECT 1 FROM ctt_staff_private.review_proposal_events)
 OR EXISTS(SELECT 1 FROM ctt_staff_private.review_materializations) OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_anchors)
 OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_payloads) THEN RAISE EXCEPTION 'CTT proposal rollback refuses used data'; END IF;
 IF (SELECT count(*) FROM ctt_staff_private.review_proposal_baseline)<>3 THEN RAISE EXCEPTION 'CTT proposal rollback baseline invalid'; END IF;
 FOR item IN SELECT * FROM ctt_staff_private.review_proposal_baseline LOOP
 IF item.original_hash IS DISTINCT FROM md5(item.original_definition)
 OR item.installed_hash IS DISTINCT FROM md5(pg_get_functiondef(to_regprocedure(item.signature))) THEN
 RAISE EXCEPTION 'CTT proposal rollback function drift'; END IF;
 END LOOP;
 FOR item IN SELECT * FROM ctt_staff_private.review_proposal_baseline LOOP EXECUTE item.original_definition; END LOOP;
 DROP TRIGGER ctt_proposal_consumption_initial ON ctt_staff_private.review_challenge_consumptions;
 DROP TRIGGER ctt_proposal_consumption_commit ON ctt_staff_private.review_challenge_consumptions;
 DROP TRIGGER ctt_proposal_session_commit ON ctt_staff_private.capture_results;
 -- SQL-language helpers are removed before their table/function dependencies.
 FOR item IN SELECT p.oid::regprocedure signature FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='ctt_staff_private' AND p.proname=ANY(names)
 ORDER BY CASE WHEN p.proname='review_proposal_json' THEN 0 ELSE 1 END LOOP
 EXECUTE 'DROP FUNCTION '||item.signature; END LOOP;
END
$rollback$;
DROP TABLE ctt_staff_private.review_materializations;
DROP TABLE ctt_staff_private.review_proposal_heads;
DROP TABLE ctt_staff_private.review_proposal_attempts;
DROP TABLE ctt_staff_private.review_proposal_events;
DROP TABLE ctt_staff_private.review_proposals;
DROP TABLE ctt_staff_private.review_proposal_baseline;
COMMIT;
