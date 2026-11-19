-- Integration records only, synthetic schema only. No startup DDL or grants.
BEGIN;
DO $$ BEGIN
 IF current_schema() !~ '^bs_test_v01_[a-f0-9]{32}$' THEN
  RAISE EXCEPTION 'V01_SYNTHETIC_SCHEMA_REQUIRED';
 END IF;
END $$;
CREATE TABLE support_batisenal_v01_snapshots (
 id uuid PRIMARY KEY,
 ticket_id uuid NOT NULL REFERENCES support_batisenal_v01_case_binding(ticket_id),
 actor_id uuid NOT NULL REFERENCES empleados(id),
 organization_namespace text NOT NULL,
 tournament_id uuid NOT NULL REFERENCES tournaments(id),
 grant_id text NOT NULL REFERENCES private_oauth_grants(grant_id),
 client_id text NOT NULL,
 revision text NOT NULL CHECK(length(revision)=64),
 captured_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,
 response_json text NOT NULL CHECK(octet_length(response_json)<=32768),
 response_sha256 text NOT NULL CHECK(length(response_sha256)=64)
);
CREATE TABLE support_batisenal_v01_operations (
 id uuid PRIMARY KEY,
 ticket_id uuid NOT NULL REFERENCES support_batisenal_v01_case_binding(ticket_id),
 actor_id uuid NOT NULL REFERENCES empleados(id),
 organization_namespace text NOT NULL,
 tournament_id uuid NOT NULL REFERENCES tournaments(id),
 client_id text NOT NULL,
 action text NOT NULL CHECK(action='submit_case_draft'),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 request_hash text NOT NULL CHECK(length(request_hash)=64),
 snapshot_id uuid NOT NULL REFERENCES support_batisenal_v01_snapshots(id),
 draft_id varchar(36) NOT NULL UNIQUE REFERENCES support_batisenal_agent_drafts(id),
 audit_id uuid NOT NULL UNIQUE REFERENCES agent_action_receipts(id),
 receipt_json text NOT NULL CHECK(octet_length(receipt_json)<=2048),
 created_at timestamptz NOT NULL,
 UNIQUE(actor_id,organization_namespace,tournament_id,ticket_id,action,idempotency_key)
);
CREATE FUNCTION support_batisenal_v01_immutable_record() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'V01_RECORD_IMMUTABLE'; END $$;
DO $$
DECLARE name text;
BEGIN
 FOREACH name IN ARRAY ARRAY['support_batisenal_v01_snapshots','support_batisenal_v01_operations'] LOOP
  EXECUTE format('CREATE TRIGGER aa_v01_writer_required BEFORE INSERT ON %I '
   'FOR EACH STATEMENT EXECUTE FUNCTION support_batisenal_v01_assert_writer()',name);
  EXECUTE format('CREATE TRIGGER v01_record_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON %I '
   'FOR EACH STATEMENT EXECUTE FUNCTION support_batisenal_v01_immutable_record()',name);
 END LOOP;
END $$;
COMMIT;
-- No purge/backfill, retention migration or destructive rollback. Disable
-- consumers to stop use and preserve immutable records for review.
