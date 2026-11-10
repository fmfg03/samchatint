-- Additive owner-run provenance for the approved isolated synthetic pilot.
-- No activation or historical promotion to ai_agent occurs here.
BEGIN;
DO $$ BEGIN
 IF current_schema() !~ '^bs_test_v01_[a-f0-9]{32}$' THEN
  RAISE EXCEPTION 'V01_SYNTHETIC_SCHEMA_REQUIRED';
 END IF;
END $$;
ALTER TABLE support_batisenal_v01_case_binding
 ADD COLUMN agent_kind text NOT NULL DEFAULT 'synthetic_stub',
 ADD COLUMN agent_client_id text,
 ADD COLUMN agent_issuer text,
 ADD COLUMN agent_resource text,
 ADD COLUMN agent_registration_sha256 text,
 ADD CONSTRAINT v01_agent_provenance CHECK (
  (agent_kind = 'synthetic_stub' AND agent_client_id IS NULL
   AND agent_issuer IS NULL AND agent_resource IS NULL
   AND agent_registration_sha256 IS NULL)
  OR (agent_kind = 'ai_agent'
   AND agent_client_id IS NOT NULL AND length(btrim(agent_client_id)) > 0
   AND agent_issuer IS NOT NULL AND length(btrim(agent_issuer)) > 0
   AND agent_resource IS NOT NULL AND length(btrim(agent_resource)) > 0
   AND agent_registration_sha256 IS NOT NULL
   AND agent_registration_sha256 ~ '^[a-f0-9]{64}$')
 );
-- Existing v01_binding_immutable compares the complete row except revision;
-- the new columns are therefore immutable, including after revoke/rollback.
COMMIT;
