-- Candidate only: hard-close staff writes pending trusted v2 integration.
-- Run only as a separately approved owner migration; no operational key is set.
-- This does not protect Team DDL, nor employees/session/draft identity authority.
BEGIN;

DO $preflight$
DECLARE
    staff_oid oid := 'public.copa_telmex_team_staff'::regclass;
    app_oid oid := (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = 'devnous_user');
    hmac_oid oid := pg_catalog.to_regprocedure('public.hmac(bytea,bytea,text)');
    created_owner boolean := false;
BEGIN
    IF app_oid IS NULL THEN RAISE EXCEPTION 'CTT staff app role missing'; END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = 'ctt_staff_private') THEN
        RAISE EXCEPTION 'CTT staff security schema already exists';
    END IF;
    IF (SELECT relowner FROM pg_catalog.pg_class WHERE oid=staff_oid) <> app_oid THEN
        RAISE EXCEPTION 'CTT staff owner precondition failed';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c,
          LATERAL pg_catalog.aclexplode(COALESCE(c.relacl, pg_catalog.acldefault('r', c.relowner))) a
        WHERE c.oid=staff_oid AND a.grantee <> c.relowner
    ) THEN RAISE EXCEPTION 'CTT staff ACL requires separate reconciliation'; END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=staff_oid
               AND c.relacl IS NOT NULL
               AND c.relacl <> pg_catalog.acldefault('r', c.relowner)) THEN
        RAISE EXCEPTION 'CTT staff owner ACL requires separate reconciliation';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
               WHERE a.attrelid=staff_oid AND a.attnum > 0 AND NOT a.attisdropped
                 AND a.attacl IS NOT NULL) THEN
        RAISE EXCEPTION 'CTT staff column ACL requires separate reconciliation';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_trigger WHERE tgrelid=staff_oid AND NOT tgisinternal) THEN
        RAISE EXCEPTION 'CTT staff triggers require separate reconciliation';
    END IF;
    IF hmac_oid IS NULL OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_depend d
        JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid AND e.extname='pgcrypto'
        JOIN pg_catalog.pg_proc p ON p.oid=d.objid AND p.proowner=e.extowner
        WHERE d.classid='pg_catalog.pg_proc'::regclass AND d.objid=hmac_oid
          AND d.refclassid='pg_catalog.pg_extension'::regclass AND d.deptype='e'
          AND NOT pg_catalog.pg_has_role(app_oid, e.extowner, 'MEMBER')
    ) THEN RAISE EXCEPTION 'CTT staff trusted pgcrypto unavailable'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner') THEN
        CREATE ROLE samchat_ctt_staff_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
        created_owner := true;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner'
               AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
                  JOIN pg_catalog.pg_roles r ON r.oid IN (m.roleid, m.member)
                  WHERE r.rolname='samchat_ctt_staff_owner') THEN
        RAISE EXCEPTION 'CTT staff owner role is not isolated';
    END IF;
    EXECUTE 'CREATE SCHEMA ctt_staff_private AUTHORIZATION samchat_ctt_staff_owner';
    EXECUTE 'CREATE TABLE ctt_staff_private.migration_metadata (original_owner name NOT NULL, original_acl text, created_owner boolean NOT NULL)';
    EXECUTE 'ALTER TABLE ctt_staff_private.migration_metadata OWNER TO samchat_ctt_staff_owner';
    INSERT INTO ctt_staff_private.migration_metadata
        SELECT pg_catalog.pg_get_userbyid(c.relowner), c.relacl::text, created_owner
        FROM pg_catalog.pg_class c WHERE c.oid=staff_oid;
END
$preflight$;

REVOKE ALL ON SCHEMA ctt_staff_private FROM PUBLIC, devnous_user;
ALTER TABLE public.copa_telmex_team_staff OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON TABLE public.copa_telmex_team_staff FROM PUBLIC, devnous_user;
GRANT SELECT ON TABLE public.copa_telmex_team_staff TO devnous_user;
REVOKE ALL ON TABLE ctt_staff_private.migration_metadata FROM PUBLIC, devnous_user;

CREATE TABLE ctt_staff_private.verification_keys (
    kid text PRIMARY KEY CHECK (kid ~ '^[A-Za-z0-9._-]{1,80}$'),
    key_material bytea NOT NULL CHECK (pg_catalog.octet_length(key_material) >= 32),
    not_before timestamptz NOT NULL,
    not_after timestamptz NOT NULL,
    revoked boolean NOT NULL DEFAULT false,
    CHECK (not_after > not_before)
);
ALTER TABLE ctt_staff_private.verification_keys OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON TABLE ctt_staff_private.verification_keys FROM PUBLIC, devnous_user;

CREATE FUNCTION ctt_staff_private.frame_v2(values_array text[]) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog AS $frame$
DECLARE
    answer bytea := pg_catalog.convert_to('ctt-staff-sql-proof-v2', 'UTF8');
    piece bytea;
    idx integer;
BEGIN
    IF pg_catalog.array_ndims(values_array) <> 1 OR pg_catalog.array_lower(values_array,1) <> 1
       OR pg_catalog.cardinality(values_array) <> 32 THEN
        RAISE EXCEPTION 'CTT staff framing invalid';
    END IF;
    FOR idx IN 1..32 LOOP
        IF values_array[idx] IS NULL THEN
            IF idx <> 26 THEN RAISE EXCEPTION 'CTT staff framing invalid'; END IF;
            answer := answer || pg_catalog.int4send((-1)::integer);
        ELSE
            piece := pg_catalog.convert_to(values_array[idx], 'UTF8');
            IF pg_catalog.octet_length(piece) > 1048576 THEN RAISE EXCEPTION 'CTT staff framing invalid'; END IF;
            answer := answer || pg_catalog.int4send(pg_catalog.octet_length(piece)) || piece;
        END IF;
        IF pg_catalog.octet_length(answer) > 1048576 THEN
            RAISE EXCEPTION 'CTT staff framing invalid';
        END IF;
    END LOOP;
    RETURN answer;
END
$frame$;
ALTER FUNCTION ctt_staff_private.frame_v2(text[]) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.frame_v2(text[]) FROM PUBLIC, devnous_user;

-- Private primitive only: never a SQL writing or authorization endpoint.
CREATE FUNCTION ctt_staff_private.verify_hmac_v2(p_kid text, p_message bytea, p_tag bytea)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $verify$
DECLARE
    secret bytea;
    expected bytea;
    supplied bytea;
    active_key boolean := false;
    diff integer := 0;
    idx integer;
BEGIN
    SELECT k.key_material, true INTO secret, active_key
    FROM ctt_staff_private.verification_keys AS k
    WHERE k.kid=p_kid AND NOT k.revoked
      AND k.not_before <= pg_catalog.clock_timestamp()
      AND k.not_after > pg_catalog.clock_timestamp();
    IF NOT COALESCE(active_key, false) THEN secret := pg_catalog.decode(pg_catalog.repeat('00',32),'hex'); END IF;
    IF p_message IS NULL OR pg_catalog.octet_length(p_message) > 1048576 THEN
        RAISE EXCEPTION 'CTT staff proof invalid';
    END IF;
    expected := public.hmac(p_message::bytea, secret::bytea, 'sha256'::text);
    IF p_tag IS NOT NULL AND pg_catalog.octet_length(p_tag)=32 THEN supplied := p_tag;
    ELSE supplied := pg_catalog.decode(pg_catalog.repeat('00',32),'hex'); diff := 1; END IF;
    FOR idx IN 0..31 LOOP
        diff := diff | (pg_catalog.get_byte(expected, idx) # pg_catalog.get_byte(supplied, idx));
    END LOOP;
    RETURN COALESCE(active_key, false) AND diff=0;
END
$verify$;
ALTER FUNCTION ctt_staff_private.verify_hmac_v2(text,bytea,bytea) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.verify_hmac_v2(text,bytea,bytea) FROM PUBLIC, devnous_user;

CREATE FUNCTION ctt_staff_private.deny_staff_write()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $deny$
BEGIN
    RAISE EXCEPTION 'CTT staff governance not enabled' USING ERRCODE='42501';
END
$deny$;
ALTER FUNCTION ctt_staff_private.deny_staff_write() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.deny_staff_write() FROM PUBLIC, devnous_user;
CREATE TRIGGER ctt_staff_write_closed BEFORE INSERT OR UPDATE OR DELETE
    ON public.copa_telmex_team_staff FOR EACH ROW
    EXECUTE FUNCTION ctt_staff_private.deny_staff_write();

DO $postflight$
BEGIN
    IF pg_catalog.pg_has_role('devnous_user','samchat_ctt_staff_owner','MEMBER')
       OR pg_catalog.has_table_privilege('devnous_user','public.copa_telmex_team_staff','INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')
       OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE')
       OR pg_catalog.has_function_privilege('devnous_user','ctt_staff_private.verify_hmac_v2(text,bytea,bytea)','EXECUTE')
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
                  WHERE a.attrelid='public.copa_telmex_team_staff'::regclass
                    AND a.attnum > 0 AND NOT a.attisdropped
                    AND pg_catalog.has_column_privilege('devnous_user', a.attrelid, a.attnum,
                                                       'INSERT,UPDATE,REFERENCES')) THEN
        RAISE EXCEPTION 'CTT staff privilege isolation failed';
    END IF;
END
$postflight$;
COMMIT;
