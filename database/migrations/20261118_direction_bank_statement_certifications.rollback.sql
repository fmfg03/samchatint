-- Restoring the previous application release preserves the audit history.
-- For authorized schema removal with existing rows, export and verify the
-- complete archive first, then set samchat.direction_bank_archive_verified = 'on'.
BEGIN;

DO $$
BEGIN
    IF to_regclass('public.direction_bank_statement_certifications') IS NULL THEN
        RETURN;
    END IF;

    LOCK TABLE public.direction_bank_statement_certifications IN ACCESS EXCLUSIVE MODE;
    IF EXISTS (SELECT 1 FROM public.direction_bank_statement_certifications)
       AND current_setting('samchat.direction_bank_archive_verified', true) IS DISTINCT FROM 'on'
    THEN
        RAISE EXCEPTION 'Archive and verify Direction bank certifications before schema removal';
    END IF;

    DROP TABLE public.direction_bank_statement_certifications;
END
$$;

DROP FUNCTION IF EXISTS public.direction_bank_cert_uuid_set(uuid[]);
DROP FUNCTION IF EXISTS public.direction_bank_cert_scope_digest(uuid[]);

COMMIT;
