-- Owner-only candidate rollback. Refuse any retained business/audit evidence.
BEGIN;
DO $empty$
DECLARE t text;n bigint;
BEGIN
 FOREACH t IN ARRAY ARRAY['review_batch_services','review_batch_capabilities','review_batch_runs','review_batch_capture_identities','review_batch_members','review_batch_qualifications','review_batch_verifications','review_batch_blocks','review_batch_block_members','review_batch_jobs','review_batch_events','review_batch_budget_reservations','review_batch_preparations','review_batch_analysis_jobs','review_batch_preparation_rows'] LOOP
 EXECUTE format('SELECT count(*) FROM ctt_staff_private.%I',t) INTO n;IF n<>0 THEN RAISE EXCEPTION 'CTT batch rollback retains evidence in %',t;END IF;END LOOP;
END $empty$;
DO $acl$ DECLARE r record;BEGIN FOR r IN SELECT * FROM ctt_staff_private.review_batch_preparation_acl WHERE NOT was_granted LOOP EXECUTE format('REVOKE INSERT ON public.%I FROM samchat_ctt_staff_owner',r.table_name);END LOOP;END $acl$;
DO $functions$ DECLARE item record;BEGIN FOR item IN SELECT p.oid::regprocedure signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='ctt_staff_private' AND p.proname=ANY(ARRAY['batch_account_context','batch_analysis_heartbeat','batch_assert_source_pages','batch_block_analysis','batch_catalog','batch_claim_analysis','batch_complete_preparation','batch_create_run','batch_guard_preparation_claims','batch_json','batch_materialize_review','batch_preparation_context','batch_preparation_row_allowed','batch_private_run','batch_protect_frozen_state','batch_read_run','batch_reconcile_budget','batch_register_sample','batch_require_keys','batch_require_run','batch_require_service','batch_reserve_budget','batch_uuid','batch_verifier_context']) LOOP EXECUTE format('DROP FUNCTION %s CASCADE',item.signature);END LOOP;END $functions$;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_preparation_acl CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_preparation_rows CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_analysis_jobs CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_preparations CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_budget_reservations CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_events CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_dispatch_slot CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_jobs CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_block_members CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_blocks CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_verifications CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_qualifications CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_members CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_capture_identities CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_runs CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_capabilities CASCADE;
DROP TABLE IF EXISTS ctt_staff_private.review_batch_services CASCADE;
COMMIT;
