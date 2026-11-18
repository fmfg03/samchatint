-- Disable the new UI before rollback. Preserve any granted scopes for audit.
BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.copa_telmex_edition_access)
        OR EXISTS (SELECT 1 FROM public.copa_telmex_report_settings WHERE enabled) THEN
        RAISE EXCEPTION 'Conserve el catálogo de alcances CTT antes de revertir';
    END IF;
END $$;
DROP TABLE public.copa_telmex_edition_access, public.copa_telmex_report_settings;
COMMIT;
