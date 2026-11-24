-- Preserve observed history. Revert the application separately when cuts exist.
BEGIN;

DO $$
BEGIN
    IF to_regclass('direction_dossier_roster_daily_cuts') IS NOT NULL THEN
        LOCK TABLE direction_dossier_roster_daily_cuts IN ACCESS EXCLUSIVE MODE;
        IF EXISTS (SELECT 1 FROM direction_dossier_roster_daily_cuts) THEN
            RAISE EXCEPTION 'Preserve observed Direction dossier roster cuts';
        END IF;
    END IF;
END;
$$;

DROP TABLE IF EXISTS direction_dossier_roster_daily_cuts;

COMMIT;
