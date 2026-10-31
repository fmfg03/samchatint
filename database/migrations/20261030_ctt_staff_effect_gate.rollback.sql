-- No silent loss of configuration or accepted-write evidence.
BEGIN;
LOCK TABLE public.copa_telmex_team_staff IN ACCESS EXCLUSIVE MODE;
LOCK TABLE ctt_staff_private.effect_keys,ctt_staff_private.effect_writers,
  ctt_staff_private.effect_staff_writes IN ACCESS EXCLUSIVE MODE;
DO $safe$
BEGIN
  IF EXISTS(SELECT 1 FROM ctt_staff_private.effect_keys)
    OR EXISTS(SELECT 1 FROM ctt_staff_private.effect_writers)
    OR EXISTS(SELECT 1 FROM ctt_staff_private.effect_staff_writes)
    OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_payloads)
    OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_results) THEN
    RAISE EXCEPTION 'CTT effect rollback refuses configured or evidentiary state';
  END IF;
END
$safe$;
CREATE OR REPLACE FUNCTION ctt_staff_private.deny_staff_write()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
AS $function$
BEGIN
  RAISE EXCEPTION 'CTT staff governance not enabled' USING ERRCODE='42501';
END
$function$;
ALTER FUNCTION ctt_staff_private.deny_staff_write() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.deny_staff_write() FROM PUBLIC,devnous_user;
DROP TABLE ctt_staff_private.effect_staff_writes;
DROP TABLE ctt_staff_private.effect_writers;
DROP TABLE ctt_staff_private.effect_keys;
DROP FUNCTION ctt_staff_private.verify_effect_hmac(bytea,bytea,bytea);
COMMIT;
