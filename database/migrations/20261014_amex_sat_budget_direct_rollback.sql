-- Owner-run rollback refuses persisted evidence; replay after removal is a no-op.
BEGIN;
DO $rollback$ BEGIN
 IF to_regclass('amex_direct_items') IS NULL THEN RETURN; END IF;
 EXECUTE $ad_cleanup$
LOCK TABLE amex_statement_imports,amex_statement_import_items,amex_recognition_consumptions IN ACCESS EXCLUSIVE MODE;
DO $$ DECLARE name text; populated boolean; BEGIN
 FOREACH name IN ARRAY ARRAY['amex_statement_source_files','amex_statement_direct_contexts','amex_statement_direct_state','amex_merchant_identity_aliases','amex_cfdi_status_receipts','amex_direct_items','amex_direct_events','amex_direct_accounting_cuts','amex_direct_cut_items'] LOOP
 EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I)',name) INTO populated;
 IF populated THEN RAISE EXCEPTION 'Cannot remove populated AMEX direct evidence'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM amex_statement_imports WHERE workflow_mode='direct') OR EXISTS(SELECT 1 FROM amex_recognition_consumptions WHERE binding_mode='direct') THEN RAISE EXCEPTION 'Cannot remove populated AMEX direct origins'; END IF;
END $$;
DROP TRIGGER IF EXISTS ad_cfdi_usage_expense ON expense_reports;
DROP TRIGGER IF EXISTS ad_cross_owner_lines ON accounting_poliza_lines;
DROP TRIGGER IF EXISTS ad_cross_owner_informe ON amex_accounting_cuts;
DROP TRIGGER IF EXISTS amex_direct_consumption_guard ON amex_recognition_consumptions;
DROP TRIGGER IF EXISTS amex_direct_representation_guard ON amex_recognition_representations;
DROP TRIGGER IF EXISTS ad_cut_proof_amex_recognition_consumptions ON amex_recognition_consumptions;
DROP TRIGGER IF EXISTS ad_cut_proof_accounting_poliza_lines ON accounting_poliza_lines;
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['expense_reports','cfdi_reports','budget_movement_assignments','accounting_polizas','accounting_poliza_lines'] LOOP
 EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I','ad_frozen_'||name,name);
 END LOOP;
END $$;
DROP TABLE IF EXISTS amex_direct_cut_items,amex_direct_accounting_cuts,amex_direct_events,amex_direct_items,amex_statement_direct_state,amex_statement_direct_contexts,amex_cfdi_status_receipts,amex_merchant_identity_aliases,amex_statement_source_files;
ALTER TABLE amex_statement_import_items DROP CONSTRAINT IF EXISTS fk_amex_statement_item_mode;
ALTER TABLE amex_statement_imports DROP CONSTRAINT IF EXISTS uq_amex_statement_mode;
ALTER TABLE amex_statement_import_items DROP CONSTRAINT IF EXISTS ck_amex_statement_item_status;
ALTER TABLE amex_statement_import_items ADD CONSTRAINT ck_amex_statement_item_status CHECK ((status='imported' AND imported_expense_id IS NOT NULL AND reason_code IS NULL) OR (status='skipped' AND imported_expense_id IS NULL AND reason_code IS NOT NULL));
ALTER TABLE amex_statement_imports DROP CONSTRAINT IF EXISTS ck_amex_statement_counts;
ALTER TABLE amex_statement_imports ADD CONSTRAINT ck_amex_statement_counts CHECK(imported_count>0 AND skipped_count>=0 AND total_rows=imported_count+skipped_count);
ALTER TABLE amex_statement_import_items DROP COLUMN IF EXISTS workflow_mode;
ALTER TABLE amex_statement_imports DROP COLUMN IF EXISTS workflow_mode,DROP COLUMN IF EXISTS credit_count,DROP COLUMN IF EXISTS unknown_count;
ALTER TABLE amex_recognition_consumptions DROP CONSTRAINT IF EXISTS ck_amex_consumption_source_anchor;
ALTER TABLE amex_recognition_consumptions ADD CONSTRAINT ck_amex_consumption_source_anchor CHECK((source_kind='statement' AND imported_expense_id IS NOT NULL AND source_report_expense_id IS NULL AND binding_mode IN('legacy','report_bridge')) OR (source_kind='report' AND imported_expense_id IS NULL AND source_report_expense_id IS NOT NULL AND binding_mode='report'));
CREATE OR REPLACE FUNCTION amex_statement_counts() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE valid_rows integer; omitted_rows integer; receipt amex_statement_imports%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='amex_statement_imports' THEN
  receipt := NEW;
 ELSE
  SELECT * INTO receipt FROM amex_statement_imports WHERE id=NEW.import_id;
 END IF;
 SELECT count(*) FILTER(WHERE status='imported'), count(*) FILTER(WHERE status='skipped') INTO valid_rows,omitted_rows FROM amex_statement_import_items WHERE import_id=receipt.id;
 IF valid_rows<>receipt.imported_count OR omitted_rows<>receipt.skipped_count THEN
  RAISE EXCEPTION 'AMEX statement row counts mismatch';
 END IF;
 RETURN NULL;
END $$;
DROP FUNCTION IF EXISTS amex_direct_immutable(),amex_direct_source_guard(),amex_direct_context_guard(),amex_direct_projection_guard(),amex_direct_consumption_guard(),amex_direct_representation_guard(),amex_direct_cut_insert_guard(),amex_direct_cut_item_guard(),amex_direct_check_cut(),amex_direct_frozen_reference_guard(),amex_direct_alias_guard(),amex_direct_alias_revision_check(),amex_direct_event_guard(),amex_direct_cross_owner_guard(),amex_direct_cfdi_usage_guard();

$ad_cleanup$;
END $rollback$;
COMMIT;
