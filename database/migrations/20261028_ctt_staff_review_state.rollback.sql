-- Never drop enrolled identities, challenges or audit records.
BEGIN;
-- Serialize the emptiness check with all enrollment and challenge writes.
LOCK TABLE ctt_staff_private.review_principals,
    ctt_staff_private.review_credentials,
    ctt_staff_private.review_faculties,
    ctt_staff_private.review_challenges,
    ctt_staff_private.review_challenge_consumptions,
    ctt_staff_private.review_authority_audit IN ACCESS EXCLUSIVE MODE;
DO $rollback$
BEGIN
    IF EXISTS (SELECT 1 FROM ctt_staff_private.review_challenge_consumptions)
       OR EXISTS (SELECT 1 FROM ctt_staff_private.review_challenges)
       OR EXISTS (SELECT 1 FROM ctt_staff_private.review_faculties)
       OR EXISTS (SELECT 1 FROM ctt_staff_private.review_credentials)
       OR EXISTS (SELECT 1 FROM ctt_staff_private.review_principals)
       OR EXISTS (SELECT 1 FROM ctt_staff_private.review_authority_audit) THEN
        RAISE EXCEPTION 'CTT protected review rollback requires empty state';
    END IF;
END
$rollback$;
DROP TRIGGER ctt_review_consumption_commit_guard ON ctt_staff_private.review_challenge_consumptions;
DROP FUNCTION ctt_staff_private.validate_review_consumption_at_commit() RESTRICT;
DROP FUNCTION ctt_staff_private.consume_review_challenge(uuid,uuid,bytea,text,bigint,bigint) RESTRICT;
DROP TRIGGER ctt_review_challenge_immutable ON ctt_staff_private.review_challenges;
DROP TRIGGER ctt_review_challenge_no_truncate ON ctt_staff_private.review_challenges;
DROP TRIGGER ctt_review_consumption_immutable ON ctt_staff_private.review_challenge_consumptions;
DROP TRIGGER ctt_review_consumption_no_truncate ON ctt_staff_private.review_challenge_consumptions;
DROP TRIGGER ctt_review_audit_immutable ON ctt_staff_private.review_authority_audit;
DROP TRIGGER ctt_review_audit_no_truncate ON ctt_staff_private.review_authority_audit;
DROP TRIGGER ctt_review_principal_no_delete ON ctt_staff_private.review_principals;
DROP TRIGGER ctt_review_principal_no_truncate ON ctt_staff_private.review_principals;
DROP TRIGGER ctt_review_faculty_no_delete ON ctt_staff_private.review_faculties;
DROP TRIGGER ctt_review_faculty_no_truncate ON ctt_staff_private.review_faculties;
DROP TRIGGER ctt_review_credential_no_delete ON ctt_staff_private.review_credentials;
DROP TRIGGER ctt_review_credential_no_truncate ON ctt_staff_private.review_credentials;
DROP TRIGGER ctt_review_principal_audit ON ctt_staff_private.review_principals;
DROP TRIGGER ctt_review_faculty_audit ON ctt_staff_private.review_faculties;
DROP TRIGGER ctt_review_credential_audit ON ctt_staff_private.review_credentials;
DROP FUNCTION ctt_staff_private.audit_review_authority_change() RESTRICT;
DROP FUNCTION ctt_staff_private.reject_review_evidence_mutation() RESTRICT;
DROP TRIGGER ctt_review_principal_version ON ctt_staff_private.review_principals;
DROP TRIGGER ctt_review_faculty_version ON ctt_staff_private.review_faculties;
DROP TRIGGER ctt_review_credential_version ON ctt_staff_private.review_credentials;
DROP FUNCTION ctt_staff_private.guard_review_authority_version() RESTRICT;
DROP TABLE ctt_staff_private.review_challenge_consumptions RESTRICT;
DROP TABLE ctt_staff_private.review_challenges RESTRICT;
DROP TABLE ctt_staff_private.review_authority_audit RESTRICT;
DROP TABLE ctt_staff_private.review_faculties RESTRICT;
DROP TABLE ctt_staff_private.review_credentials RESTRICT;
DROP TABLE ctt_staff_private.review_principals RESTRICT;
COMMIT;
