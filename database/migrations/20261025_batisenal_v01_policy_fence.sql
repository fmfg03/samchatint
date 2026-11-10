-- Isolated candidate / owner-run only. No activation or permission grants.
-- Apply to the existing SamChat schema after its OAuth and pilot migrations.
-- Provision the ONE guard row explicitly before any v0.1 consumer is enabled.
-- Missing row means NO v0.1 guarantees; required consumers must fail closed.
BEGIN;

DO $$
DECLARE name text; resolved_schema text;
BEGIN
  FOREACH name IN ARRAY ARRAY['empleados', 'access_profiles',
    'empleado_access_profiles', 'access_control_rules', 'private_oauth_links',
    'private_oauth_grants', 'support_batisenal_pilot_fixtures',
    'support_batisenal_pilots'] LOOP
    IF to_regclass(name) IS NULL THEN
      RAISE EXCEPTION 'V01_POLICY_PREREQUISITE_REQUIRED';
    END IF;
    SELECT n.nspname INTO resolved_schema FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=to_regclass(name);
    IF resolved_schema IS DISTINCT FROM current_schema() THEN
      RAISE EXCEPTION 'V01_POLICY_SCHEMA_MISMATCH';
    END IF;
  END LOOP;
END $$;

CREATE TABLE support_batisenal_v01_policy_guard (
  singleton boolean PRIMARY KEY CHECK (singleton = true),
  epoch bigint NOT NULL DEFAULT 0 CHECK (epoch >= 0)
);

CREATE FUNCTION support_batisenal_v01_policy_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- UPDATE acquires the common row fence BEFORE the DML statement's row locks.
  -- Rollback undoes epoch. No row is inserted and no permission is changed.
  EXECUTE format('UPDATE %I.support_batisenal_v01_policy_guard '
    'SET epoch = epoch + 1 WHERE singleton = true', TG_TABLE_SCHEMA);
  RETURN NULL;
END $$;

CREATE FUNCTION support_batisenal_v01_guard_monotonic() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP <> 'UPDATE' THEN
    RAISE EXCEPTION 'V01_POLICY_GUARD_IMMUTABLE';
  END IF;
  IF NEW.singleton IS DISTINCT FROM OLD.singleton OR NEW.epoch <= OLD.epoch THEN
    RAISE EXCEPTION 'V01_POLICY_GUARD_IMMUTABLE';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER batisenal_v01_guard_monotonic BEFORE UPDATE OR DELETE
ON support_batisenal_v01_policy_guard FOR EACH ROW
EXECUTE FUNCTION support_batisenal_v01_guard_monotonic();
CREATE TRIGGER batisenal_v01_guard_no_truncate BEFORE TRUNCATE
ON support_batisenal_v01_policy_guard FOR EACH STATEMENT
EXECUTE FUNCTION support_batisenal_v01_guard_monotonic();

DO $$
DECLARE name text;
BEGIN
  FOREACH name IN ARRAY ARRAY['empleados', 'access_profiles',
    'empleado_access_profiles', 'access_control_rules', 'private_oauth_links',
    'private_oauth_grants', 'support_batisenal_pilot_fixtures',
    'support_batisenal_pilots'] LOOP
    EXECUTE format(
      'CREATE TRIGGER batisenal_v01_policy_changed '
      'BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I '
      'FOR EACH STATEMENT EXECUTE FUNCTION support_batisenal_v01_policy_changed()',
      name);
  END LOOP;
END $$;

-- No guard row, client, credential, grant, profile or pilot is created here.
-- SECURITY INVOKER is intentional. The effective runtime role must already be
-- authorized to SELECT and UPDATE this guard and EXECUTE the trigger functions
-- before installing triggers in a runtime. Owner-only tests do NOT prove those
-- privileges. This migration grants nothing. A separate runtime role without
-- those privileges fails closed, including while the guard is empty.
-- IMPORTANT: TRUNCATE acquires ACCESS EXCLUSIVE before BEFORE STATEMENT.
-- Its owner must explicitly acquire the policy fence BEFORE TRUNCATE. Same for
-- prior DDL/SELECT FOR UPDATE. These triggers alone DO NOT solve that order.
-- Rollback: remove v0.1 consumers first, then these triggers; preserve evidence
-- and epoch. Do not reset/delete this row while snapshots can remain live.
COMMIT;
