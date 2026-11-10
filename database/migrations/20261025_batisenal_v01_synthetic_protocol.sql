-- Synthetic schema ONLY. No deployment, credentials, grants or activation here.
-- Composed writers cooperate; this is not a barrier against a database owner.
BEGIN;
DO $$ BEGIN
 IF current_schema() !~ '^bs_test_v01_[a-f0-9]{32}$' THEN
  RAISE EXCEPTION 'V01_SYNTHETIC_SCHEMA_REQUIRED';
 END IF;
END $$;

CREATE TABLE support_batisenal_v01_case_binding (
 singleton boolean PRIMARY KEY CHECK(singleton),
 ticket_id uuid NOT NULL UNIQUE REFERENCES support_batisenal_cases(ticket_id),
 report_id uuid NOT NULL UNIQUE REFERENCES documentos(id),
 actor_id uuid NOT NULL REFERENCES empleados(id),
 tournament_id uuid NOT NULL REFERENCES tournaments(id),
 installation_id text NOT NULL CHECK(length(installation_id) BETWEEN 1 AND 128),
 organization_namespace text NOT NULL CHECK(length(organization_namespace) BETWEEN 1 AND 64),
 support_project text NOT NULL CHECK(length(support_project) BETWEEN 1 AND 64),
 attestation_sha256 text NOT NULL CHECK(attestation_sha256 ~ '^[a-f0-9]{64}$'),
 synthetic_only boolean NOT NULL CHECK(synthetic_only),
 revision bigint NOT NULL DEFAULT 0 CHECK(revision >= 0)
);
-- An xid8 marker is only a protocol assertion for the closed composition.
-- SECURITY INVOKER: no assertion of tamper resistance from an owner DB role.
CREATE TABLE support_batisenal_v01_transactions (
 transaction_id xid8 PRIMARY KEY,
 ticket_id uuid NOT NULL REFERENCES support_batisenal_v01_case_binding(ticket_id),
 policy_epoch bigint NOT NULL,
 began_at timestamptz NOT NULL
);

CREATE FUNCTION support_batisenal_v01_begin(expected_case uuid, no_wait boolean)
RETURNS TABLE(revision bigint, observed_at timestamptz)
LANGUAGE plpgsql AS $$
DECLARE policy bigint; bound support_batisenal_v01_case_binding%ROWTYPE;
BEGIN
 IF current_schema() !~ '^bs_test_v01_[a-f0-9]{32}$' THEN
  RAISE EXCEPTION 'V01_SYNTHETIC_SCHEMA_REQUIRED';
 END IF;
 IF current_setting('transaction_isolation') <> 'read committed' THEN
  RAISE EXCEPTION 'V01_READ_COMMITTED_REQUIRED';
 END IF;
 -- This function must be invoked before the first SQL of the outer transaction.
 -- It does not prove that an arbitrary caller has not already acquired locks.
 IF no_wait THEN
  SELECT epoch INTO policy FROM support_batisenal_v01_policy_guard
   WHERE singleton FOR UPDATE NOWAIT;
  SELECT * INTO bound FROM support_batisenal_v01_case_binding
   WHERE singleton FOR UPDATE NOWAIT;
 ELSE
  SELECT epoch INTO policy FROM support_batisenal_v01_policy_guard
   WHERE singleton FOR UPDATE;
  SELECT * INTO bound FROM support_batisenal_v01_case_binding
   WHERE singleton FOR UPDATE;
 END IF;
 IF policy IS NULL OR bound.ticket_id IS DISTINCT FROM expected_case
    OR bound.synthetic_only IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'V01_BINDING_UNAVAILABLE';
 END IF;
 INSERT INTO support_batisenal_v01_transactions
  VALUES(pg_current_xact_id(), bound.ticket_id, policy, clock_timestamp())
  ON CONFLICT(transaction_id) DO NOTHING;
 RETURN QUERY SELECT bound.revision, clock_timestamp();
END $$;

CREATE FUNCTION support_batisenal_v01_assert_writer() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE permitted boolean;
BEGIN
 EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.support_batisenal_v01_transactions '
  'WHERE transaction_id = pg_current_xact_id_if_assigned())', TG_TABLE_SCHEMA)
  INTO permitted;
 IF permitted IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'V01_WRITER_PROTOCOL_REQUIRED';
 END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION support_batisenal_v01_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 -- Conservative invalidation of the ENTIRE small synthetic graph, not a
 -- global production-report lock or an OLD/NEW join that can miss reparenting.
 EXECUTE format('UPDATE %I.support_batisenal_v01_case_binding '
  'SET revision=revision+1 WHERE singleton', TG_TABLE_SCHEMA);
 RETURN NULL;
END $$;

CREATE FUNCTION support_batisenal_v01_binding_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP <> 'UPDATE' OR (to_jsonb(NEW)-'revision') IS DISTINCT FROM (to_jsonb(OLD)-'revision')
    OR NEW.revision <> OLD.revision+1 THEN
  RAISE EXCEPTION 'V01_BINDING_IMMUTABLE';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER v01_binding_immutable BEFORE UPDATE OR DELETE
 ON support_batisenal_v01_case_binding FOR EACH ROW
 EXECUTE FUNCTION support_batisenal_v01_binding_immutable();
CREATE TRIGGER v01_binding_no_truncate BEFORE TRUNCATE
 ON support_batisenal_v01_case_binding FOR EACH STATEMENT
 EXECUTE FUNCTION support_batisenal_v01_binding_immutable();

DO $$
DECLARE name text; resolved_schema text;
 content_tables text[] := ARRAY['documentos','expense_reports','cuentas_de_gastos',
  'adjuntos','cfdi_reports','aprobaciones','budget_concepts','cuentas_contables',
  'proveedores_clientes','reembolsos','tournaments','copa_telmex_teams',
  'authorization_positions','authorization_position_assignments',
  'project_authorization_rules','documento_authorization_routes',
  'support_batisenal_cases','support_tickets','support_ticket_comments','support_batisenal_events'];
 policy_tables text[] := ARRAY['empleados','access_profiles','empleado_access_profiles',
  'access_control_rules','private_oauth_links','private_oauth_grants',
  'support_batisenal_pilot_fixtures','support_batisenal_pilots'];
BEGIN
 FOREACH name IN ARRAY content_tables || policy_tables LOOP
  SELECT n.nspname INTO resolved_schema FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE c.oid=to_regclass(name) AND c.relkind='r';
  IF resolved_schema IS DISTINCT FROM current_schema() THEN
   RAISE EXCEPTION 'V01_SOURCE_SCHEMA_OR_TABLE_UNPROVEN';
  END IF;
  -- A prefixed name ensures our assertion runs BEFORE legacy DML triggers.
  EXECUTE format('CREATE TRIGGER aa_v01_writer_required BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I '
   'FOR EACH STATEMENT EXECUTE FUNCTION support_batisenal_v01_assert_writer()',name);
  IF name=ANY(content_tables) THEN
   EXECUTE format('CREATE TRIGGER v01_source_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I '
    'FOR EACH STATEMENT EXECUTE FUNCTION support_batisenal_v01_changed()',name);
  END IF;
 END LOOP;
END $$;
COMMIT;
-- Provisioning a binding is separate synthetic fixture preparation, never startup.
-- The strict schema restriction intentionally prevents use on installed schemas.
-- Removing consumers alone will not permit ungated source writes: remove this
-- protocol only as a coordinated synthetic teardown; preserve history/artifacts.
