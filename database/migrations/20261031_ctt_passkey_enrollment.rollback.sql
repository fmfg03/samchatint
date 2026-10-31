-- Refuse loss of an invitation, account binding, credential or operator history.
BEGIN;
DO $safe$
DECLARE relation text;
BEGIN
 FOREACH relation IN ARRAY ARRAY['review_services','review_enrollment_operators','review_account_links','review_enrollment_invitations','review_enrollment_attempts','review_enrollment_candidates','review_enrollment_activations','review_enrollment_audit'] LOOP
 EXECUTE 'LOCK TABLE ctt_staff_private.'||quote_ident(relation)||' IN ACCESS EXCLUSIVE MODE';
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid=to_regclass('ctt_staff_private.'||relation)) THEN
 DECLARE occupied boolean; BEGIN
 EXECUTE 'SELECT EXISTS(SELECT 1 FROM ctt_staff_private.'||quote_ident(relation)||')' INTO occupied;
 IF occupied THEN RAISE EXCEPTION 'CTT enrollment rollback refuses configured or evidentiary state'; END IF;
 END;
 END IF;
 END LOOP;
END
$safe$;
DROP FUNCTION ctt_staff_private.revoke_enrolled_credential(uuid,uuid,text);
DROP FUNCTION ctt_staff_private.inspect_passkey_enrollment(uuid);
DROP FUNCTION ctt_staff_private.activate_passkey_enrollment(uuid,text,uuid,uuid,text,uuid,text,text,timestamptz,timestamptz,text);
DROP FUNCTION ctt_staff_private.record_passkey_enrollment_candidate(uuid,uuid,text,bytea,bytea,bigint,boolean,boolean,text);
DROP FUNCTION ctt_staff_private.begin_passkey_enrollment(uuid,text,uuid,uuid,bytea,integer);
DROP FUNCTION ctt_staff_private.load_passkey_enrollment(uuid,uuid);
DROP FUNCTION ctt_staff_private.admit_passkey_enrollment_invitation(uuid,uuid,uuid,text,uuid,text,bytea,timestamptz,timestamptz,text);
DROP FUNCTION ctt_staff_private.current_enrollment_operator();
DROP FUNCTION ctt_staff_private.current_review_service();
DROP FUNCTION ctt_staff_private.check_review_service_identity();
DROP TABLE ctt_staff_private.review_enrollment_activations;
DROP TABLE ctt_staff_private.review_enrollment_candidates;
DROP TABLE ctt_staff_private.review_enrollment_attempts;
DROP TABLE ctt_staff_private.review_enrollment_invitations;
DROP TABLE ctt_staff_private.review_account_links;
DROP TABLE ctt_staff_private.review_enrollment_operators;
DROP TABLE ctt_staff_private.review_services;
DROP FUNCTION ctt_staff_private.guard_review_service_configuration();
DROP TABLE ctt_staff_private.review_enrollment_audit;
COMMIT;
