-- Owner-run only, after support_batisenal + agent and reviewed private OAuth migrations.
-- Never startup DDL. No grants, credentials, fixture attestations or backfill here.
BEGIN;
CREATE TABLE support_batisenal_pilot_fixtures (
 ticket_id uuid PRIMARY KEY REFERENCES support_batisenal_cases(ticket_id),
 attestation_sha256 varchar(64) NOT NULL CHECK (attestation_sha256 ~ '^[a-f0-9]{64}$'),
 active boolean NOT NULL,
 starts_at bigint, expires_at bigint,
 CHECK ((starts_at IS NULL AND expires_at IS NULL) OR
        (starts_at IS NOT NULL AND expires_at IS NOT NULL AND expires_at = starts_at + 3600))
);
CREATE TABLE support_batisenal_pilots (
 id varchar(36) PRIMARY KEY,
 ticket_id uuid NOT NULL REFERENCES support_batisenal_pilot_fixtures(ticket_id),
 employee varchar(36) NOT NULL, profile_id varchar(128) NOT NULL,
 issuer varchar(512) NOT NULL, installation_id varchar(128) NOT NULL,
 organization varchar(64) NOT NULL, project varchar(64) NOT NULL,
 agent_id varchar(36) NOT NULL,
 source_kind varchar(24) NOT NULL CHECK (source_kind IN ('synthetic_stub','ai_agent')),
 starts_at bigint NOT NULL, expires_at bigint NOT NULL, revoked boolean NOT NULL,
 UNIQUE (ticket_id,employee,profile_id,issuer,installation_id),
 CHECK (starts_at >= 0 AND expires_at > starts_at AND expires_at <= starts_at + 3600)
);
CREATE TABLE support_batisenal_pilot_receipts (
 id varchar(36) PRIMARY KEY, pilot_id varchar(36) NOT NULL REFERENCES support_batisenal_pilots(id),
 actor varchar(36) NOT NULL, kind varchar(16) NOT NULL, created_at bigint NOT NULL
);
CREATE TABLE support_batisenal_pilot_consents (
 draft_hash varchar(64) PRIMARY KEY, employee varchar(36) NOT NULL,
 scope_hash varchar(64) NOT NULL, oauth_fingerprint varchar(64) NOT NULL,
 fingerprint varchar(64) NOT NULL, expires_at bigint NOT NULL, used boolean NOT NULL
);
CREATE FUNCTION support_batisenal_pilot_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (to_jsonb(NEW)-'revoked') IS DISTINCT FROM (to_jsonb(OLD)-'revoked')
    OR OLD.revoked AND NOT NEW.revoked THEN
   RAISE EXCEPTION 'PILOT_IMMUTABLE';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER support_batisenal_pilot_immutable BEFORE UPDATE ON support_batisenal_pilots
 FOR EACH ROW EXECUTE FUNCTION support_batisenal_pilot_immutable();
CREATE FUNCTION support_batisenal_fixture_window_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.starts_at IS NOT NULL AND (NEW.starts_at IS DISTINCT FROM OLD.starts_at
     OR NEW.expires_at IS DISTINCT FROM OLD.expires_at) THEN
   RAISE EXCEPTION 'PILOT_WINDOW_IMMUTABLE';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER support_batisenal_fixture_window_immutable BEFORE UPDATE ON support_batisenal_pilot_fixtures
 FOR EACH ROW EXECUTE FUNCTION support_batisenal_fixture_window_immutable();
COMMIT;
-- Rollback: disable composition/revoke pilot; retain tables, cases, drafts and audit. No DROP.
