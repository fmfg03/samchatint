-- Administrator-run: separate append-only runtime grants from FK ownership.
-- Run with search_path=public in production; tests use an isolated schema.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $migration$
DECLARE
    target_schema TEXT := current_schema();
    table_name TEXT;
    relation REGCLASS;
    owner_name TEXT;
    owner_role RECORD;
BEGIN
    IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
        RAISE EXCEPTION 'CTT ownership correction requires an administrator';
    END IF;
    IF target_schema IS NULL OR NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'devnous_user' AND NOT rolsuper AND NOT rolbypassrls
    ) THEN
        RAISE EXCEPTION 'CTT runtime role or target schema is incompatible';
    END IF;

    SELECT * INTO owner_role FROM pg_roles
    WHERE rolname = 'samchat_ctt_admission_owner';
    IF FOUND THEN
        IF owner_role.rolcanlogin OR owner_role.rolsuper
            OR owner_role.rolcreatedb OR owner_role.rolcreaterole
            OR owner_role.rolreplication OR owner_role.rolbypassrls
            OR owner_role.rolinherit
            OR EXISTS (
                SELECT 1 FROM pg_auth_members
                WHERE roleid = owner_role.oid OR member = owner_role.oid
            ) THEN
            RAISE EXCEPTION 'Existing CTT owner role is incompatible';
        END IF;
    ELSE
        CREATE ROLE samchat_ctt_admission_owner
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS NOINHERIT;
    END IF;

    FOREACH table_name IN ARRAY ARRAY[
        'copa_telmex_registration_batches',
        'copa_telmex_registration_batch_documents'
    ] LOOP
        relation := to_regclass(format('%I.%I', target_schema, table_name));
        SELECT pg_get_userbyid(relowner) INTO owner_name
        FROM pg_class WHERE oid = relation AND relkind = 'r';
        IF NOT FOUND OR owner_name NOT IN (
            'devnous_user', 'samchat_ctt_admission_owner'
        ) THEN
            RAISE EXCEPTION 'Unexpected CTT table or owner: %', table_name;
        END IF;
        IF NOT has_table_privilege('devnous_user', relation, 'SELECT')
            OR NOT has_table_privilege('devnous_user', relation, 'INSERT')
            OR has_table_privilege('devnous_user', relation, 'UPDATE')
            OR has_table_privilege('devnous_user', relation, 'DELETE') THEN
            RAISE EXCEPTION 'Unexpected CTT runtime ACL: %', table_name;
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = to_regclass(format(
            '%I.copa_telmex_registration_batch_documents', target_schema
        ))
        AND confrelid = to_regclass(format(
            '%I.copa_telmex_registration_batches', target_schema
        ))
        AND conname = 'fk_ctt_registration_document_batch_edition'
        AND contype = 'f' AND convalidated
    ) THEN
        RAISE EXCEPTION 'Expected validated CTT batch-edition FK is missing';
    END IF;

    EXECUTE format(
        'GRANT USAGE ON SCHEMA %I TO samchat_ctt_admission_owner', target_schema
    );
    FOREACH table_name IN ARRAY ARRAY[
        'copa_telmex_registration_batches',
        'copa_telmex_registration_batch_documents'
    ] LOOP
        EXECUTE format(
            'LOCK TABLE %I.%I IN ACCESS EXCLUSIVE MODE', target_schema, table_name
        );
        EXECUTE format(
            'ALTER TABLE %I.%I OWNER TO samchat_ctt_admission_owner',
            target_schema, table_name
        );
        -- ALTER OWNER can carry the previous owner's revoked privileges.
        EXECUTE format(
            'GRANT ALL PRIVILEGES ON TABLE %I.%I TO samchat_ctt_admission_owner',
            target_schema, table_name
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM devnous_user',
            target_schema, table_name
        );
        EXECUTE format(
            'GRANT SELECT, INSERT ON TABLE %I.%I TO devnous_user',
            target_schema, table_name
        );
        relation := to_regclass(format('%I.%I', target_schema, table_name));
        IF has_table_privilege('devnous_user', relation, 'UPDATE')
            OR has_table_privilege('devnous_user', relation, 'DELETE')
            OR has_table_privilege('devnous_user', relation, 'TRUNCATE')
            OR has_table_privilege('devnous_user', relation, 'REFERENCES')
            OR has_table_privilege('devnous_user', relation, 'TRIGGER')
            OR pg_has_role('devnous_user', 'samchat_ctt_admission_owner', 'MEMBER')
        THEN
            RAISE EXCEPTION 'CTT runtime has unexpected effective privileges';
        END IF;
    END LOOP;
END
$migration$;
COMMIT;
