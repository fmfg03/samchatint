-- Refuse rollback once any batch document or staff record exists.
BEGIN;
DO $rollback$
BEGIN
  IF EXISTS (SELECT 1 FROM copa_telmex_registration_batch_documents)
     OR EXISTS (SELECT 1 FROM copa_telmex_team_staff) THEN
    RAISE EXCEPTION 'Batch admission or staff data exists; owner review required';
  END IF;
END
$rollback$;
DROP TABLE IF EXISTS copa_telmex_registration_batch_documents RESTRICT;
DROP TABLE IF EXISTS copa_telmex_registration_batches RESTRICT;
DROP TABLE IF EXISTS copa_telmex_team_staff RESTRICT;
COMMIT;
