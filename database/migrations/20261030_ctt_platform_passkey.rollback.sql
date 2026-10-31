BEGIN;
LOCK TABLE ctt_staff_private.review_credentials,
  ctt_staff_private.review_challenge_consumptions,
  ctt_staff_private.review_authenticator_audit IN ACCESS EXCLUSIVE MODE;
DO $safe$
BEGIN
  IF EXISTS(SELECT 1 FROM ctt_staff_private.review_authenticator_audit)
    OR EXISTS(SELECT 1 FROM ctt_staff_private.review_credentials
      WHERE auth_profile<>'device-counter-v1' OR backup_eligible OR backup_state OR auth_policy_hash IS NOT NULL)
    OR EXISTS(SELECT 1 FROM ctt_staff_private.review_challenge_consumptions
      WHERE auth_profile<>'device-counter-v1' OR backup_eligible OR backup_state
        OR auth_policy_hash IS NOT NULL OR new_counter<=prior_counter) THEN
    RAISE EXCEPTION 'CTT platform rollback refuses authenticator evidence';
  END IF;
END
$safe$;
DROP TRIGGER ctt_authenticator_consumption_initial ON ctt_staff_private.review_challenge_consumptions;
DROP TRIGGER ctt_authenticator_consumption_commit ON ctt_staff_private.review_challenge_consumptions;
DROP TRIGGER ctt_authenticator_profile_version ON ctt_staff_private.review_credentials;
DROP TRIGGER ctt_authenticator_profile_audit ON ctt_staff_private.review_credentials;
DROP FUNCTION ctt_staff_private.consume_review_challenge_v2(uuid,uuid,bytea,text,bigint,bigint,text,boolean,boolean,text);
DROP FUNCTION ctt_staff_private.validate_authenticator_consumption();
DROP FUNCTION ctt_staff_private.guard_authenticator_profile();
DROP FUNCTION ctt_staff_private.audit_authenticator_profile();
DROP TABLE ctt_staff_private.review_authenticator_audit;
ALTER TABLE ctt_staff_private.review_challenge_consumptions
  DROP CONSTRAINT ctt_consumption_auth_profile,
  DROP COLUMN auth_profile, DROP COLUMN backup_eligible, DROP COLUMN backup_state,
  DROP COLUMN auth_policy_hash,
  ADD CONSTRAINT review_challenge_consumptions_new_counter_check CHECK(new_counter BETWEEN 1 AND 4294967295),
  ADD CONSTRAINT review_challenge_consumptions_check CHECK(new_counter>prior_counter);
ALTER TABLE ctt_staff_private.review_credentials
  DROP CONSTRAINT ctt_credential_auth_profile,
  DROP COLUMN auth_profile, DROP COLUMN backup_eligible, DROP COLUMN backup_state,
  DROP COLUMN auth_policy_hash;
COMMIT;
