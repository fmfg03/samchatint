-- Owner-run migration. Back up before applying; never run from web startup.
BEGIN;
ALTER TABLE cuentas_de_gastos ADD COLUMN IF NOT EXISTS comprobacion_parcial boolean NOT NULL DEFAULT false;
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS informe_origen_id uuid REFERENCES documentos(id) ON DELETE RESTRICT;
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS motivo_comprobacion_parcial text;
ALTER TABLE reembolsos ADD COLUMN IF NOT EXISTS client_submission_id uuid;
CREATE INDEX IF NOT EXISTS ix_documentos_informe_origen_id ON documentos(informe_origen_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_partial_lot_submission ON documentos(informe_origen_id, client_submission_id) WHERE informe_origen_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_partial_return_submission ON reembolsos(cuenta_gastos_id, client_submission_id) WHERE client_submission_id IS NOT NULL;

-- v1.0.24 installations may name the active-settlement index differently.
-- Replace only unique, single-column, conditional cuenta indexes whose
-- predicate excludes cancelled rows. Unrelated indexes are never touched.
DO $$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT i.indexrelid::regclass AS index_name
    FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid
      AND a.attnum = i.indkey[0]
    WHERE i.indrelid = 'reembolsos'::regclass AND i.indisunique
      AND i.indnkeyatts = 1 AND a.attname = 'cuenta_gastos_id'
      AND i.indpred IS NOT NULL
      AND pg_get_expr(i.indpred, i.indrelid) LIKE '%cancelado%'
  LOOP
    EXECUTE format('DROP INDEX %s', item.index_name);
  END LOOP;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_cuenta_reimbursement ON reembolsos(cuenta_gastos_id)
  WHERE estado <> 'cancelado' AND tipo <> 'devolucion';

-- A lot cannot acquire its own advance/payment account or lose its origin.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='documentos'::regclass AND conname='ck_partial_lot_origin') THEN
    ALTER TABLE documentos ADD CONSTRAINT ck_partial_lot_origin CHECK
      (informe_origen_id IS NULL OR (tipo='INFORME' AND cuenta_gastos_id IS NULL AND informe_origen_id<>id AND client_submission_id IS NOT NULL AND length(trim(motivo_comprobacion_parcial)) > 0 AND motivo_comprobacion_parcial IS NOT NULL));
  END IF;
END $$;

CREATE OR REPLACE FUNCTION protect_partial_advance_expense() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lot documentos%ROWTYPE; origin_account uuid; partial_case boolean; case_state text;
BEGIN
  IF TG_OP <> 'INSERT' AND OLD.informe_documento_id IS NOT NULL THEN
    SELECT * INTO lot FROM documentos WHERE id=OLD.informe_documento_id FOR UPDATE;
    IF lot.informe_origen_id IS NOT NULL THEN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Partial comprobación evidence cannot be deleted'; END IF;
      IF (NEW.documento_id, NEW.informe_documento_id, NEW.cuenta_gastos_id)
         IS DISTINCT FROM (OLD.documento_id, OLD.informe_documento_id, OLD.cuenta_gastos_id) THEN
        RAISE EXCEPTION 'Partial comprobación membership is immutable';
      END IF;
      IF lot.estado='aprobado' AND
        (to_jsonb(NEW)-ARRAY['coi_estado','coi_exported_at','coi_exported_by_id','coi_status_updated_at','coi_status_updated_by_id','updated_at'])
        IS DISTINCT FROM
        (to_jsonb(OLD)-ARRAY['coi_estado','coi_exported_at','coi_exported_by_id','coi_status_updated_at','coi_status_updated_by_id','updated_at']) THEN
        RAISE EXCEPTION 'Approved partial comprobación requires accounting reversal, not mutation';
      END IF;
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  IF NEW.informe_documento_id IS NOT NULL THEN
    SELECT * INTO lot FROM documentos WHERE id=NEW.informe_documento_id FOR UPDATE;
    IF lot.informe_origen_id IS NOT NULL THEN
      SELECT cuenta_gastos_id INTO origin_account FROM documentos WHERE id=lot.informe_origen_id;
      IF NEW.cuenta_gastos_id IS DISTINCT FROM origin_account OR NEW.documento_id IS DISTINCT FROM lot.id THEN
        RAISE EXCEPTION 'Partial comprobación expense must belong to its original account';
      END IF;
      IF (TG_OP='INSERT' OR OLD.informe_documento_id IS DISTINCT FROM NEW.informe_documento_id) AND lot.estado<>'borrador' THEN
        RAISE EXCEPTION 'Cannot add expenses to a submitted partial comprobación';
      END IF;
    END IF;
  END IF;
  IF NEW.cuenta_gastos_id IS NOT NULL THEN
    SELECT comprobacion_parcial, estado INTO partial_case, case_state
      FROM cuentas_de_gastos WHERE id=NEW.cuenta_gastos_id FOR UPDATE;
    IF partial_case AND (
      (to_jsonb(NEW)->>'pagado_con_amex_empresa')::boolean IS TRUE OR
      ((to_jsonb(NEW)->>'pagado_con_amex_empresa') IS NULL AND to_jsonb(NEW)->>'origen'='amex_batch')
    ) THEN RAISE EXCEPTION 'Partial advances cannot contain company AMEX'; END IF;
    IF partial_case AND case_state<>'abierta' AND
       (TG_OP='INSERT' OR NEW.gasto_cantidad IS DISTINCT FROM OLD.gasto_cantidad OR NEW.estado_gasto IS DISTINCT FROM OLD.estado_gasto) THEN
      RAISE EXCEPTION 'Partial advance case is closed';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS protect_partial_advance_expense ON expense_reports;
CREATE TRIGGER protect_partial_advance_expense BEFORE INSERT OR UPDATE OR DELETE ON expense_reports
  FOR EACH ROW EXECUTE FUNCTION protect_partial_advance_expense();

CREATE OR REPLACE FUNCTION protect_partial_advance_case() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.comprobacion_parcial AND
    (NOT NEW.comprobacion_parcial OR (NEW.empleado_id, NEW.beneficiario_empleado_id,
      NEW.beneficiario_proveedor_cliente_id, NEW.beneficiario_alterno_tipo, NEW.currency, NEW.torneo_id)
     IS DISTINCT FROM (OLD.empleado_id, OLD.beneficiario_empleado_id,
      OLD.beneficiario_proveedor_cliente_id, OLD.beneficiario_alterno_tipo, OLD.currency, OLD.torneo_id)) THEN
    RAISE EXCEPTION 'Partial advance case identity cannot change';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS protect_partial_advance_case ON cuentas_de_gastos;
CREATE TRIGGER protect_partial_advance_case BEFORE UPDATE ON cuentas_de_gastos
  FOR EACH ROW EXECUTE FUNCTION protect_partial_advance_case();

CREATE OR REPLACE FUNCTION protect_partial_advance_document() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.cuenta_gastos_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM cuentas_de_gastos WHERE id=OLD.cuenta_gastos_id AND comprobacion_parcial
  ) THEN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Partial advance original cannot be deleted'; END IF;
    IF (NEW.cuenta_gastos_id, NEW.tipo, NEW.empleado_id, NEW.beneficiario_empleado_id,
        NEW.beneficiario_proveedor_cliente_id, NEW.beneficiario_alterno_tipo, NEW.currency, NEW.torneo_id)
      IS DISTINCT FROM
       (OLD.cuenta_gastos_id, OLD.tipo, OLD.empleado_id, OLD.beneficiario_empleado_id,
        OLD.beneficiario_proveedor_cliente_id, OLD.beneficiario_alterno_tipo, OLD.currency, OLD.torneo_id) THEN
      RAISE EXCEPTION 'Partial advance original identity cannot change';
    END IF;
    IF OLD.tipo='INFORME' AND (NEW.informe_origen_id IS NOT NULL OR NEW.estado NOT IN ('borrador','cerrado')) THEN
      RAISE EXCEPTION 'Partial advance original is a case container, not another approval';
    END IF;
  END IF;
  IF OLD.informe_origen_id IS NOT NULL THEN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Partial comprobación history cannot be deleted'; END IF;
    IF (NEW.informe_origen_id, NEW.cuenta_gastos_id, NEW.tipo, NEW.empleado_id,
        NEW.beneficiario_empleado_id, NEW.beneficiario_proveedor_cliente_id,
        NEW.beneficiario_alterno_tipo, NEW.currency, NEW.torneo_id, NEW.client_submission_id, NEW.motivo_comprobacion_parcial)
      IS DISTINCT FROM
       (OLD.informe_origen_id, OLD.cuenta_gastos_id, OLD.tipo, OLD.empleado_id,
        OLD.beneficiario_empleado_id, OLD.beneficiario_proveedor_cliente_id,
        OLD.beneficiario_alterno_tipo, OLD.currency, OLD.torneo_id, OLD.client_submission_id, OLD.motivo_comprobacion_parcial) THEN
      RAISE EXCEPTION 'Partial comprobación identity is immutable';
    END IF;
    IF OLD.estado='aprobado' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
      RAISE EXCEPTION 'Approved partial comprobación is immutable';
    END IF;
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS protect_partial_advance_document ON documentos;
CREATE TRIGGER protect_partial_advance_document BEFORE UPDATE OR DELETE ON documentos
  FOR EACH ROW EXECUTE FUNCTION protect_partial_advance_document();
CREATE OR REPLACE FUNCTION protect_partial_return() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.client_submission_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM cuentas_de_gastos WHERE id=OLD.cuenta_gastos_id AND comprobacion_parcial
  ) THEN
    IF TG_OP='DELETE' OR to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
      RAISE EXCEPTION 'Posted partial return requires accounting reversal';
    END IF;
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS protect_partial_return ON reembolsos;
CREATE TRIGGER protect_partial_return BEFORE UPDATE OR DELETE ON reembolsos
  FOR EACH ROW EXECUTE FUNCTION protect_partial_return();

CREATE OR REPLACE FUNCTION protect_partial_proof() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE expense_id uuid; expense_ids uuid[]; doc documentos%ROWTYPE;
BEGIN
  IF TG_OP='INSERT' THEN expense_ids=ARRAY[NEW.gasto_id];
  ELSIF TG_OP='DELETE' THEN expense_ids=ARRAY[OLD.gasto_id];
  ELSE expense_ids=ARRAY[OLD.gasto_id,NEW.gasto_id]; END IF;
  FOREACH expense_id IN ARRAY expense_ids LOOP
    SELECT d.* INTO doc FROM expense_reports e JOIN documentos d ON d.id=e.informe_documento_id
      WHERE e.id=expense_id FOR UPDATE OF d;
    IF doc.informe_origen_id IS NOT NULL AND doc.estado='aprobado' THEN
      RAISE EXCEPTION 'Approved partial expense proof requires accounting reversal';
    END IF;
  END LOOP;
  IF TG_OP<>'INSERT' AND EXISTS (
    SELECT 1 FROM reembolsos r JOIN cuentas_de_gastos c ON c.id=r.cuenta_gastos_id
    WHERE r.id=OLD.reembolso_id AND r.client_submission_id IS NOT NULL AND c.comprobacion_parcial
  ) THEN RAISE EXCEPTION 'Posted partial return proof requires accounting reversal'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS protect_partial_proof ON adjuntos;
CREATE TRIGGER protect_partial_proof BEFORE INSERT OR UPDATE OR DELETE ON adjuntos
  FOR EACH ROW EXECUTE FUNCTION protect_partial_proof();
COMMIT;
