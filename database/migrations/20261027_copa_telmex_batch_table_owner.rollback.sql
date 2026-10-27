-- Administrator-only rollback to the verified pre-correction ownership/ACL.
-- This deliberately restores the known FK failure. Prefer roll-forward.
-- The NOLOGIN role remains; never drop a role with unrelated dependencies.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
DO $rollback$
DECLARE
    target_schema TEXT := current_schema();
    table_name TEXT;
    relation REGCLASS;
BEGIN
    IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
        RAISE EXCEPTION 'CTT ownership rollback requires an administrator';
    END IF;
    FOREACH table_name IN ARRAY ARRAY[
        'copa_telmex_registration_batches',
        'copa_telmex_registration_batch_documents'
    ] LOOP
        relation := to_regclass(format('%I.%I', target_schema, table_name));
        IF relation IS NULL OR (
            SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = relation
        ) IS DISTINCT FROM 'samchat_ctt_admission_owner' THEN
            RAISE EXCEPTION 'Unexpected CTT rollback owner: %', table_name;
        END IF;
        EXECUTE format(
            'LOCK TABLE %I.%I IN ACCESS EXCLUSIVE MODE', target_schema, table_name
        );
        EXECUTE format(
            'ALTER TABLE %I.%I OWNER TO devnous_user', target_schema, table_name
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM devnous_user',
            target_schema, table_name
        );
        EXECUTE format(
            'GRANT SELECT, INSERT, TRUNCATE, REFERENCES, TRIGGER '
            'ON TABLE %I.%I TO devnous_user', target_schema, table_name
        );
    END LOOP;
END
$rollback$;
COMMIT;
