-- Owner-only candidate rollback. Refuse any retained business/audit evidence.
BEGIN;
DO $empty$
DECLARE t text;n bigint;
BEGIN
 FOREACH t IN ARRAY ARRAY['review_batch_challenges','review_batch_consents','review_batch_qualification_activations','review_batch_child_authorizations','review_batch_consumptions','review_batch_corrections','review_batch_page_consumptions','batch_capture_payloads','batch_writer_contexts'] LOOP
 EXECUTE format('SELECT count(*) FROM ctt_staff_private.%I',t) INTO n;IF n<>0 THEN RAISE EXCEPTION 'CTT batch rollback retains evidence in %',t;END IF;END LOOP;
END $empty$;
DO $acl$ BEGIN IF NOT (SELECT was_granted FROM ctt_staff_private.batch_source_read_baseline) THEN REVOKE SELECT ON public.copa_telmex_registration_batch_documents FROM samchat_ctt_staff_owner;END IF;END $acl$;
DO $restore$
DECLARE item record;
BEGIN
 FOR item IN SELECT * FROM ctt_staff_private.batch_writer_baseline LOOP
 IF md5(pg_get_functiondef(to_regprocedure(item.signature))) IS DISTINCT FROM item.installed_hash THEN RAISE EXCEPTION 'CTT batch rollback function drift: %',item.signature;END IF;
 EXECUTE item.original_definition;
 END LOOP;
END $restore$;
DROP TRIGGER ctt_capture_attempt_typed_registry ON ctt_staff_private.capture_attempt_anchors;
DO $fk$
DECLARE item record;
BEGIN
 FOR item IN SELECT c.conname,c.conrelid::regclass relation FROM pg_constraint c WHERE c.contype='f' AND c.confrelid='ctt_staff_private.capture_attempt_registry'::regclass AND c.conrelid IN('ctt_staff_private.capture_payloads'::regclass,'ctt_staff_private.legacy_v2_reservations'::regclass) LOOP
 EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',item.relation,item.conname);
 EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY(attempt_id) REFERENCES ctt_staff_private.capture_attempt_anchors(attempt_id) ON DELETE RESTRICT',item.relation,item.conname);
 END LOOP;
END $fk$;
DO $functions$ DECLARE item record;BEGIN FOR item IN SELECT p.oid::regprocedure signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='ctt_staff_private' AND p.proname=ANY(ARRAY['batch_validate_edit_rectangle','batch_validate_edit_value','batch_attempt_status','batch_capture_context','batch_check_capture_finality_proof','batch_claim','batch_commit_review_session','batch_consume_child','batch_create_challenge','batch_current_writer_input','batch_exact_capture_row','batch_expire_blocks','batch_issue_child','batch_legacy_capture_identity','batch_load_child_context','batch_load_consent_context','batch_load_finality_context','batch_load_finality_gate_context','batch_lock_capture_source_pages','batch_lock_child','batch_lock_legacy_capture_identity','batch_mark_uncertain','batch_member_detail','batch_pause','batch_prepare_block','batch_private_block','batch_private_result','batch_project_capture_rows','batch_propose_member_corrections','batch_recheck_parent','batch_record_canonical_result','batch_record_page_consumption','batch_record_result','batch_record_result_source_pages','batch_recover_consent','batch_recover_job','batch_refresh_verifications','batch_register_consent','batch_register_individual_attempt','batch_report_quality_issue','batch_require_canonical_consumption','batch_resolve_legacy_source','batch_resume','batch_revoke','batch_session_commit_authorized','batch_source_asset_context','batch_source_context','batch_stage_capture_payload','batch_transition','batch_validate_capture_finality_rows']) LOOP EXECUTE format('DROP FUNCTION %s CASCADE',item.signature);END LOOP;END $functions$;
DROP TABLE IF EXISTS ctt_staff_private.batch_writer_baseline CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.batch_writer_contexts CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.batch_capture_payloads CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.capture_attempt_registry CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_page_consumptions CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_corrections CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.batch_source_read_baseline CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_consumptions CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_child_authorizations CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_qualification_activations CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_consents CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_challenges CASCADE;
COMMIT;
