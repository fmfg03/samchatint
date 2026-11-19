-- Revert application code first. Preserve records and audit when capture has begun.
BEGIN;
DO $$
BEGIN
    IF to_regclass('direction_dossier_records') IS NOT NULL THEN
        LOCK TABLE direction_dossier_records IN ACCESS EXCLUSIVE MODE;
        IF EXISTS (SELECT 1 FROM direction_dossier_records) THEN
            RAISE EXCEPTION 'Dossier records exist. Preserve the tables when reverting the application.';
        END IF;
    END IF;
END $$;

DROP TABLE IF EXISTS direction_dossier_revisions;
DROP TABLE IF EXISTS direction_dossier_records;
COMMIT;
