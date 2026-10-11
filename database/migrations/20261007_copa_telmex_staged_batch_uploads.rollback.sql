-- Staging-only rollback. Refuse removal while any upload metadata remains.
BEGIN;
DO $rollback$
BEGIN
  IF EXISTS (SELECT 1 FROM copa_telmex_registration_batch_uploads)
     OR EXISTS (SELECT 1 FROM copa_telmex_registration_batch_upload_files) THEN
    RAISE EXCEPTION 'Staged batch upload data exists; owner cleanup review required';
  END IF;
END
$rollback$;
DROP TABLE IF EXISTS copa_telmex_registration_batch_upload_files RESTRICT;
DROP TABLE IF EXISTS copa_telmex_registration_batch_uploads RESTRICT;
COMMIT;
