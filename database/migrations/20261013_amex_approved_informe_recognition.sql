-- Owner-run only. No production execution is authorized by committing this DDL.
-- Legacy statement anchors/receipts are preserved; no business-data backfill.
BEGIN;
ALTER TABLE amex_recognition_consumptions
    ADD COLUMN IF NOT EXISTS source_kind VARCHAR(20) NOT NULL DEFAULT 'statement',
    ADD COLUMN IF NOT EXISTS binding_mode VARCHAR(20) NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS source_report_expense_id UUID NULL;
ALTER TABLE amex_recognition_consumptions ALTER COLUMN imported_expense_id DROP NOT NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='amex_recognition_consumptions'::regclass AND conname='fk_amex_report_anchor') THEN
        ALTER TABLE amex_recognition_consumptions ADD CONSTRAINT fk_amex_report_anchor FOREIGN KEY(source_report_expense_id) REFERENCES expense_reports(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='amex_recognition_consumptions'::regclass AND conname='uq_amex_report_anchor') THEN
        ALTER TABLE amex_recognition_consumptions ADD CONSTRAINT uq_amex_report_anchor UNIQUE(source_report_expense_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='amex_recognition_consumptions'::regclass AND conname='ck_amex_consumption_source_anchor') THEN
        ALTER TABLE amex_recognition_consumptions ADD CONSTRAINT ck_amex_consumption_source_anchor CHECK (
          (source_kind='statement' AND imported_expense_id IS NOT NULL AND source_report_expense_id IS NULL AND binding_mode IN ('legacy','report_bridge')) OR
          (source_kind='report' AND imported_expense_id IS NULL AND source_report_expense_id IS NOT NULL AND binding_mode='report'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='amex_recognition_consumptions'::regclass AND conname='ck_amex_bridge_never_posted') THEN
        ALTER TABLE amex_recognition_consumptions ADD CONSTRAINT ck_amex_bridge_never_posted CHECK(binding_mode!='report_bridge' OR accounting_poliza_id IS NULL);
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS amex_report_source_evidences (
 id UUID PRIMARY KEY,
 informe_id UUID NOT NULL REFERENCES documentos(id) ON DELETE RESTRICT,
 kind VARCHAR(30) NOT NULL,
 approval_id UUID NULL UNIQUE REFERENCES aprobaciones(id) ON DELETE RESTRICT,
 operational_evidence_id UUID NULL REFERENCES amex_report_source_evidences(id) ON DELETE RESTRICT,
 actor_id UUID NOT NULL REFERENCES empleados(id) ON DELETE RESTRICT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 version INTEGER NOT NULL CHECK(version>0),
 source_key VARCHAR(64) NOT NULL,
 snapshot_json JSONB NOT NULL,
 decisions_json JSONB NULL,
 request_payload_hash VARCHAR(64) NULL,
 reason TEXT NULL,
 UNIQUE(informe_id,kind,version),
 CONSTRAINT ck_amex_source_evidence_origin CHECK (
  (kind='operational_approval' AND approval_id IS NOT NULL AND decisions_json IS NULL AND request_payload_hash IS NULL) OR
  (kind='finance_confirmation' AND approval_id IS NULL AND decisions_json IS NOT NULL AND request_payload_hash IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_amex_report_source_evidences_informe_id ON amex_report_source_evidences(informe_id);
CREATE TABLE IF NOT EXISTS amex_report_bank_associations (
 statement_consumption_id UUID NOT NULL REFERENCES amex_recognition_consumptions(id) ON DELETE RESTRICT,
 report_consumption_id UUID NOT NULL UNIQUE REFERENCES amex_recognition_consumptions(id) ON DELETE RESTRICT,
 actor_id UUID NOT NULL REFERENCES empleados(id) ON DELETE RESTRICT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(statement_consumption_id,report_consumption_id),
 CHECK(statement_consumption_id!=report_consumption_id)
);
ALTER TABLE amex_accounting_reviews ADD COLUMN IF NOT EXISTS source_evidence_id UUID NULL;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='amex_accounting_reviews'::regclass AND conname='fk_amex_review_source_evidence') THEN
  ALTER TABLE amex_accounting_reviews ADD CONSTRAINT fk_amex_review_source_evidence FOREIGN KEY(source_evidence_id) REFERENCES amex_report_source_evidences(id) ON DELETE RESTRICT;
 END IF;
END $$;

CREATE OR REPLACE FUNCTION amex_guard_source_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE doc documentos%ROWTYPE; approval aprobaciones%ROWTYPE; original amex_report_source_evidences%ROWTYPE;
BEGIN
 IF TG_OP!='INSERT' THEN RAISE EXCEPTION 'AMEX evidence is immutable' USING ERRCODE='23514'; END IF;
 SELECT * INTO doc FROM documentos WHERE id=NEW.informe_id FOR UPDATE;
 IF NOT FOUND OR doc.tipo!='INFORME' OR doc.estado!='aprobado' THEN RAISE EXCEPTION 'AMEX evidence requires approved INFORME' USING ERRCODE='23514'; END IF;
 IF NEW.kind='operational_approval' THEN
  SELECT * INTO approval FROM aprobaciones WHERE id=NEW.approval_id FOR UPDATE;
  IF NOT FOUND OR approval.tipo_entidad!='documento' OR approval.entidad_id!=NEW.informe_id OR approval.accion!='aprobar'
   OR approval.aprobador_id!=NEW.actor_id OR approval.fecha IS DISTINCT FROM NEW.created_at
   OR doc.aprobado_en IS DISTINCT FROM approval.fecha
   OR EXISTS(SELECT 1 FROM aprobaciones a WHERE a.tipo_entidad='documento' AND a.entidad_id=NEW.informe_id AND a.accion='aprobar' AND a.fecha>approval.fecha)
  THEN RAISE EXCEPTION 'AMEX approval proof is not final authoritative event' USING ERRCODE='23514'; END IF;
  IF NEW.operational_evidence_id IS NOT NULL THEN RAISE EXCEPTION 'Operational evidence cannot adopt another event' USING ERRCODE='23514'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM empleados WHERE id=NEW.actor_id AND activo IS TRUE AND rol IN ('finanzas','admin','superadmin','super_admin')) THEN
   RAISE EXCEPTION 'AMEX financial evidence requires active Finance actor' USING ERRCODE='23514'; END IF;
  IF NEW.operational_evidence_id IS NOT NULL THEN
   SELECT * INTO original FROM amex_report_source_evidences WHERE id=NEW.operational_evidence_id;
   IF NOT FOUND OR original.kind!='operational_approval' OR original.informe_id!=NEW.informe_id THEN RAISE EXCEPTION 'Invalid operational evidence adoption' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_source_evidence_guard ON amex_report_source_evidences;
CREATE TRIGGER amex_source_evidence_guard BEFORE INSERT OR UPDATE OR DELETE ON amex_report_source_evidences FOR EACH ROW EXECUTE FUNCTION amex_guard_source_evidence();

CREATE OR REPLACE FUNCTION amex_guard_approval_proof() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM amex_report_source_evidences WHERE approval_id=OLD.id) THEN
  RAISE EXCEPTION 'Approval backing AMEX evidence is immutable' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_approval_proof_guard ON aprobaciones;
CREATE TRIGGER amex_approval_proof_guard BEFORE UPDATE OR DELETE ON aprobaciones FOR EACH ROW EXECUTE FUNCTION amex_guard_approval_proof();

CREATE OR REPLACE FUNCTION amex_guard_consumption() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE anchor expense_reports%ROWTYPE;
BEGIN
 IF TG_OP!='INSERT' THEN
  IF OLD.binding_mode='report_bridge' OR EXISTS(SELECT 1 FROM amex_report_bank_associations WHERE report_consumption_id=OLD.id) THEN
   IF TG_OP='DELETE' OR NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'Bridged AMEX identity is immutable' USING ERRCODE='23514'; END IF;
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  IF (OLD.source_kind,OLD.imported_expense_id,OLD.source_report_expense_id) IS DISTINCT FROM (NEW.source_kind,NEW.imported_expense_id,NEW.source_report_expense_id) THEN
   RAISE EXCEPTION 'AMEX source anchors are immutable' USING ERRCODE='23514'; END IF;
 END IF;
 IF NEW.binding_mode='report_bridge' AND (
  NEW.accounting_poliza_id IS NOT NULL OR EXISTS(SELECT 1 FROM amex_recognition_representations WHERE consumption_id=NEW.id AND role='report') OR EXISTS(SELECT 1 FROM amex_accounting_cuts WHERE snapshot_json->'consumption_ids' ? NEW.id::text)) THEN
  RAISE EXCEPTION 'Bridge header cannot post or own legacy report representations' USING ERRCODE='23514'; END IF;
 SELECT * INTO anchor FROM expense_reports WHERE id=COALESCE(NEW.imported_expense_id,NEW.source_report_expense_id) FOR UPDATE;
 IF NOT FOUND OR (NEW.source_kind='statement' AND anchor.origen IS DISTINCT FROM 'amex_batch') OR (NEW.source_kind='report' AND anchor.origen='amex_batch') THEN
  RAISE EXCEPTION 'AMEX anchor origin is invalid' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' AND NEW.source_kind='report' AND EXISTS(SELECT 1 FROM amex_recognition_representations WHERE expense_id=NEW.source_report_expense_id AND consumption_id!=NEW.id) THEN
  RAISE EXCEPTION 'AMEX report anchor is already owned' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_consumption_guard ON amex_recognition_consumptions;
CREATE TRIGGER amex_consumption_guard BEFORE INSERT OR UPDATE OR DELETE ON amex_recognition_consumptions FOR EACH ROW EXECUTE FUNCTION amex_guard_consumption();

CREATE OR REPLACE FUNCTION amex_guard_representation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner amex_recognition_consumptions%ROWTYPE; row_id UUID; owner_id UUID;
BEGIN
 row_id:=CASE WHEN TG_OP='DELETE' THEN OLD.expense_id ELSE NEW.expense_id END;
 owner_id:=CASE WHEN TG_OP='DELETE' THEN OLD.consumption_id ELSE NEW.consumption_id END;
 PERFORM 1 FROM expense_reports WHERE id=row_id FOR UPDATE;
 SELECT * INTO owner FROM amex_recognition_consumptions WHERE id=owner_id FOR UPDATE;
 IF TG_OP!='INSERT' THEN
  IF owner.binding_mode='report_bridge' OR EXISTS(SELECT 1 FROM amex_report_bank_associations WHERE report_consumption_id=owner_id) THEN
   RAISE EXCEPTION 'Bridged representations are immutable' USING ERRCODE='23514'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  IF (NEW.expense_id,NEW.consumption_id,NEW.role) IS DISTINCT FROM (OLD.expense_id,OLD.consumption_id,OLD.role) THEN
   RAISE EXCEPTION 'AMEX representation ownership is immutable' USING ERRCODE='23514'; END IF;
 END IF;
 IF (owner.source_kind='report' AND (NEW.role!='report' OR NEW.expense_id!=owner.source_report_expense_id)) OR
    (owner.source_kind='statement' AND NEW.role='statement' AND NEW.expense_id!=owner.imported_expense_id) OR
    (owner.binding_mode='report_bridge' AND NEW.role!='statement') THEN
  RAISE EXCEPTION 'AMEX representation does not match its real anchor' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_recognition_consumptions WHERE source_kind='report' AND source_report_expense_id=NEW.expense_id AND id!=NEW.consumption_id) THEN
  RAISE EXCEPTION 'Report source ownership conflicts with another consumption' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_representation_guard ON amex_recognition_representations;
CREATE TRIGGER amex_representation_guard BEFORE INSERT OR UPDATE OR DELETE ON amex_recognition_representations FOR EACH ROW EXECUTE FUNCTION amex_guard_representation();

CREATE OR REPLACE FUNCTION amex_check_report_anchor() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner amex_recognition_consumptions%ROWTYPE; owner_id UUID;
BEGIN
 IF TG_TABLE_NAME='amex_recognition_consumptions' THEN owner_id:=NEW.id;
 ELSE owner_id:=CASE WHEN TG_OP='DELETE' THEN OLD.consumption_id ELSE NEW.consumption_id END; END IF;
 SELECT * INTO owner FROM amex_recognition_consumptions WHERE id=owner_id;
 IF FOUND AND owner.source_kind='report' AND NOT EXISTS(SELECT 1 FROM amex_recognition_representations WHERE consumption_id=owner.id AND expense_id=owner.source_report_expense_id AND role='report') THEN
  RAISE EXCEPTION 'Report consumption requires its own report representation' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS amex_report_anchor_consumption ON amex_recognition_consumptions;
CREATE CONSTRAINT TRIGGER amex_report_anchor_consumption AFTER INSERT OR UPDATE ON amex_recognition_consumptions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_check_report_anchor();
DROP TRIGGER IF EXISTS amex_report_anchor_representation ON amex_recognition_representations;
CREATE CONSTRAINT TRIGGER amex_report_anchor_representation AFTER INSERT OR UPDATE OR DELETE ON amex_recognition_representations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_check_report_anchor();

CREATE OR REPLACE FUNCTION amex_guard_bank_association() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent amex_recognition_consumptions%ROWTYPE; child amex_recognition_consumptions%ROWTYPE;
 bank_source expense_reports%ROWTYPE; report_source expense_reports%ROWTYPE; suffix TEXT;
BEGIN
 IF TG_OP!='INSERT' THEN RAISE EXCEPTION 'AMEX bank membership is INSERT-only' USING ERRCODE='23514'; END IF;
 -- Anchor ownership cannot change. Lock physical sources before consumptions,
 -- the same order used by canonical binding, to serialize source UPDATE races.
 PERFORM 1 FROM expense_reports WHERE id IN (
  SELECT COALESCE(imported_expense_id,source_report_expense_id)
  FROM amex_recognition_consumptions
  WHERE id IN(NEW.statement_consumption_id,NEW.report_consumption_id)) ORDER BY id FOR UPDATE;
 PERFORM 1 FROM amex_recognition_consumptions WHERE id IN(NEW.statement_consumption_id,NEW.report_consumption_id) ORDER BY id FOR UPDATE;
 SELECT * INTO parent FROM amex_recognition_consumptions WHERE id=NEW.statement_consumption_id;
 SELECT * INTO child FROM amex_recognition_consumptions WHERE id=NEW.report_consumption_id;
 SELECT * INTO bank_source FROM expense_reports WHERE id=parent.imported_expense_id;
 SELECT * INTO report_source FROM expense_reports WHERE id=child.source_report_expense_id;
 SELECT last4 INTO suffix FROM amex_card_accounts WHERE id=parent.card_account_id;
 IF bank_source.id IS NULL OR report_source.id IS NULL OR suffix IS NULL
  OR bank_source.origen IS DISTINCT FROM 'amex_batch' OR report_source.origen='amex_batch'
  OR bank_source.estado_gasto IS DISTINCT FROM 'activo' OR report_source.estado_gasto IS DISTINCT FROM 'activo'
  OR (round(bank_source.gasto_cantidad::numeric,2),bank_source.fecha::date,upper(bank_source.currency),bank_source.ultimos_4_digitos)
     IS DISTINCT FROM (parent.amount,parent.economic_date,parent.currency,suffix)
  OR (round(report_source.gasto_cantidad::numeric,2),report_source.fecha::date,upper(report_source.currency),report_source.ultimos_4_digitos)
     IS DISTINCT FROM (child.amount,child.economic_date,child.currency,suffix)
 THEN RAISE EXCEPTION 'AMEX bridge physical source changed' USING ERRCODE='23514'; END IF;
 IF parent.source_kind!='statement' OR parent.binding_mode!='report_bridge' OR parent.accounting_poliza_id IS NOT NULL OR child.source_kind!='report' OR child.binding_mode!='report' OR child.accounting_poliza_id IS NULL THEN
  RAISE EXCEPTION 'AMEX bridge requires unposted header and recognized report target' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_recognition_representations WHERE consumption_id=parent.id AND role='report') OR
  EXISTS(SELECT 1 FROM amex_accounting_cuts WHERE snapshot_json->'consumption_ids' ? parent.id::text) OR
  NOT EXISTS(SELECT 1 FROM amex_accounting_cuts cut
    JOIN documentos doc ON doc.id=cut.informe_id
    JOIN expense_reports source ON source.id=child.source_report_expense_id
    WHERE cut.kind='initial' AND cut.accounting_poliza_id=child.accounting_poliza_id
     AND (source.informe_documento_id=doc.id OR
          (source.informe_documento_id IS NULL AND source.cuenta_gastos_id=doc.cuenta_gastos_id))
     AND cut.snapshot_json->'consumption_ids' ? child.id::text
     AND EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(cut.snapshot_json->'partidas','[]'::jsonb)) partida
       WHERE partida->>'expense_id'=child.source_report_expense_id::text
         AND partida->>'consumption_id'=child.id::text)) THEN
  RAISE EXCEPTION 'AMEX bridge target must have complete cut receipt' USING ERRCODE='23514'; END IF;
 IF (parent.card_account_id,parent.currency,parent.economic_date,parent.liability_account_id,parent.liability_code) IS DISTINCT FROM (child.card_account_id,child.currency,child.economic_date,child.liability_account_id,child.liability_code) THEN
  RAISE EXCEPTION 'AMEX bridge economic identity mismatch' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM empleados WHERE id=NEW.actor_id AND activo IS TRUE AND rol IN('finanzas','admin','superadmin','super_admin')) THEN
  RAISE EXCEPTION 'AMEX bridge requires active Finance actor' USING ERRCODE='23514'; END IF;
 -- Complete-group immutability follows from positive target amounts,
 -- immutable header/targets, INSERT-only membership, and deferred exact sum.
 -- No xmin/top-XID comparison: savepoints have distinct subtransaction XIDs.
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_bank_association_guard ON amex_report_bank_associations;
CREATE TRIGGER amex_bank_association_guard BEFORE INSERT OR UPDATE OR DELETE ON amex_report_bank_associations FOR EACH ROW EXECUTE FUNCTION amex_guard_bank_association();

CREATE OR REPLACE FUNCTION amex_check_bank_group() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent amex_recognition_consumptions%ROWTYPE; total NUMERIC;
BEGIN
 SELECT * INTO parent FROM amex_recognition_consumptions WHERE id=NEW.statement_consumption_id;
 SELECT SUM(c.amount) INTO total FROM amex_report_bank_associations a JOIN amex_recognition_consumptions c ON c.id=a.report_consumption_id WHERE a.statement_consumption_id=parent.id;
 IF total IS DISTINCT FROM parent.amount THEN RAISE EXCEPTION 'AMEX bridge must cover the exact complete bank amount' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS amex_bank_group_exact ON amex_report_bank_associations;
CREATE CONSTRAINT TRIGGER amex_bank_group_exact AFTER INSERT ON amex_report_bank_associations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_check_bank_group();

CREATE OR REPLACE FUNCTION amex_guard_bridged_expense() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM amex_recognition_consumptions c WHERE
  (c.imported_expense_id=OLD.id AND c.binding_mode='report_bridge') OR
  (c.source_report_expense_id=OLD.id AND EXISTS(SELECT 1 FROM amex_report_bank_associations a WHERE a.report_consumption_id=c.id))) THEN
  IF TG_OP='DELETE' OR (NEW.gasto_cantidad,NEW.fecha,NEW.currency,NEW.ultimos_4_digitos,NEW.origen,NEW.estado_gasto,NEW.cuenta_gastos_id,NEW.informe_documento_id,NEW.cfdi_report_id) IS DISTINCT FROM
   (OLD.gasto_cantidad,OLD.fecha,OLD.currency,OLD.ultimos_4_digitos,OLD.origen,OLD.estado_gasto,OLD.cuenta_gastos_id,OLD.informe_documento_id,OLD.cfdi_report_id) THEN
   RAISE EXCEPTION 'Bridged economic source cannot change or be deleted' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_bridged_expense_guard ON expense_reports;
CREATE TRIGGER amex_bridged_expense_guard BEFORE UPDATE OR DELETE ON expense_reports FOR EACH ROW EXECUTE FUNCTION amex_guard_bridged_expense();
CREATE OR REPLACE FUNCTION amex_guard_cut_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'AMEX accounting cut history is immutable' USING ERRCODE='23514';
END $$;
DROP TRIGGER IF EXISTS amex_cut_history_guard ON amex_accounting_cuts;
CREATE TRIGGER amex_cut_history_guard BEFORE UPDATE OR DELETE ON amex_accounting_cuts FOR EACH ROW EXECUTE FUNCTION amex_guard_cut_history();
COMMIT;
