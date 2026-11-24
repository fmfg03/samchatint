-- Revert application first. Keep extended data and audit if any captures exist.
BEGIN;
DO $$
BEGIN
    IF to_regclass('direction_dossier_extended_records') IS NOT NULL THEN
        LOCK TABLE direction_dossier_extended_records IN ACCESS EXCLUSIVE MODE;
        IF EXISTS (SELECT 1 FROM direction_dossier_extended_records) THEN
            RAISE EXCEPTION 'Preserve the extended dossier schema while captures exist.';
        END IF;
    END IF;
END $$;
DROP TABLE IF EXISTS direction_dossier_extended_revisions;
DROP TABLE IF EXISTS direction_dossier_extended_records;
COMMIT;
