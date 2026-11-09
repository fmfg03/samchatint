-- Safe only before any protected staff or operational keys exist.
BEGIN;
-- Serialize the emptiness check with staff and key changes.
LOCK TABLE public.copa_telmex_team_staff,
    ctt_staff_private.verification_keys IN ACCESS EXCLUSIVE MODE;
DO $rollback$
DECLARE
    original name;
    original_acl_text text;
    created_owner boolean;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname='ctt_staff_private') THEN
        RAISE EXCEPTION 'CTT staff security schema missing';
    END IF;
    IF EXISTS (SELECT 1 FROM public.copa_telmex_team_staff)
       OR EXISTS (SELECT 1 FROM ctt_staff_private.verification_keys) THEN
        RAISE EXCEPTION 'CTT staff rollback forbidden while records or keys exist';
    END IF;
    IF pg_catalog.to_regclass('ctt_staff_private.review_principals') IS NOT NULL THEN
        RAISE EXCEPTION 'CTT staff review state must be removed separately';
    END IF;
    SELECT original_owner, original_acl, migration_metadata.created_owner
       INTO STRICT original, original_acl_text, created_owner
       FROM ctt_staff_private.migration_metadata;
    IF original <> 'devnous_user' THEN RAISE EXCEPTION 'CTT staff rollback owner mismatch'; END IF;
    IF pg_catalog.pg_get_userbyid((SELECT relowner FROM pg_catalog.pg_class
                                   WHERE oid='public.copa_telmex_team_staff'::regclass)) <> 'samchat_ctt_staff_owner' THEN
        RAISE EXCEPTION 'CTT staff current owner mismatch';
    END IF;
    DROP TRIGGER ctt_staff_write_closed ON public.copa_telmex_team_staff;
    ALTER TABLE public.copa_telmex_team_staff OWNER TO devnous_user;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_class c
               WHERE c.oid='public.copa_telmex_team_staff'::regclass
                 AND COALESCE(c.relacl, pg_catalog.acldefault('r', c.relowner))
                     IS DISTINCT FROM COALESCE(original_acl_text::aclitem[],
                                               pg_catalog.acldefault('r', c.relowner))) THEN
        RAISE EXCEPTION 'CTT staff rollback ACL mismatch';
    END IF;
    -- RESTRICT prevents rollback from silently deleting unrelated dependencies.
    DROP FUNCTION ctt_staff_private.deny_staff_write();
    DROP FUNCTION ctt_staff_private.verify_hmac_v2(text,bytea,bytea);
    DROP FUNCTION ctt_staff_private.frame_v2(text[]);
    DROP TABLE ctt_staff_private.verification_keys;
    DROP TABLE ctt_staff_private.migration_metadata;
    DROP SCHEMA ctt_staff_private;
    IF created_owner THEN DROP ROLE samchat_ctt_staff_owner; END IF;
END
$rollback$;
COMMIT;
