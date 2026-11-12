-- Refuse removal after any real source or bridge has been admitted.
BEGIN;
LOCK TABLE ctt_staff_private.review_source_manifests_v2,ctt_staff_private.review_source_legacy_bridges_v2 IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_source_manifests_v2)
 OR EXISTS(SELECT 1 FROM ctt_staff_private.review_source_legacy_bridges_v2) THEN
 RAISE EXCEPTION 'CTT batch source rollback denied: evidence exists'; END IF;
END $$;
DROP FUNCTION ctt_staff_private.batch_assert_source_document(uuid,text,text,text);
DROP FUNCTION ctt_staff_private.register_batch_source_admission(uuid,bytea,bytea,bytea,bytea,jsonb,jsonb);
DROP FUNCTION ctt_staff_private.batch_source_catalog(uuid,text[]);
DROP FUNCTION ctt_staff_private.batch_source_documents(uuid,text[]);
DROP TABLE ctt_staff_private.review_source_pages_v2;
DROP TABLE ctt_staff_private.review_source_documents_v2;
DROP TABLE ctt_staff_private.review_source_manifests_v2;
DROP TABLE ctt_staff_private.review_source_legacy_bridges_v2;
COMMIT;
