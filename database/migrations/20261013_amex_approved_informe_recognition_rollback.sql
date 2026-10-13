-- Owner-run only. Fail closed rather than erase financial history.
BEGIN;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM amex_recognition_consumptions WHERE source_kind!='statement' OR binding_mode!='legacy')
  OR EXISTS(SELECT 1 FROM amex_report_source_evidences)
  OR EXISTS(SELECT 1 FROM amex_report_bank_associations)
  OR EXISTS(SELECT 1 FROM amex_accounting_reviews WHERE source_evidence_id IS NOT NULL)
 THEN RAISE EXCEPTION 'Report-first history exists: use compatible-code rollback; destructive downgrade denied'; END IF;
END $$;
DROP TRIGGER IF EXISTS amex_cut_history_guard ON amex_accounting_cuts;
DROP FUNCTION IF EXISTS amex_guard_cut_history();
DROP TRIGGER IF EXISTS amex_bridged_expense_guard ON expense_reports;
DROP TRIGGER IF EXISTS amex_approval_proof_guard ON aprobaciones;
DROP TRIGGER IF EXISTS amex_report_anchor_consumption ON amex_recognition_consumptions;
DROP TRIGGER IF EXISTS amex_consumption_guard ON amex_recognition_consumptions;
DROP TRIGGER IF EXISTS amex_report_anchor_representation ON amex_recognition_representations;
DROP TRIGGER IF EXISTS amex_representation_guard ON amex_recognition_representations;
ALTER TABLE amex_accounting_reviews DROP COLUMN source_evidence_id;
DROP TABLE amex_report_bank_associations;
DROP TABLE amex_report_source_evidences;
DROP FUNCTION IF EXISTS amex_guard_bridged_expense();
DROP FUNCTION IF EXISTS amex_guard_approval_proof();
DROP FUNCTION IF EXISTS amex_check_bank_group();
DROP FUNCTION IF EXISTS amex_guard_bank_association();
DROP FUNCTION IF EXISTS amex_check_report_anchor();
DROP FUNCTION IF EXISTS amex_guard_representation();
DROP FUNCTION IF EXISTS amex_guard_consumption();
DROP FUNCTION IF EXISTS amex_guard_source_evidence();
ALTER TABLE amex_recognition_consumptions DROP CONSTRAINT ck_amex_bridge_never_posted;
ALTER TABLE amex_recognition_consumptions DROP CONSTRAINT ck_amex_consumption_source_anchor;
ALTER TABLE amex_recognition_consumptions DROP COLUMN source_report_expense_id;
ALTER TABLE amex_recognition_consumptions DROP COLUMN source_kind;
ALTER TABLE amex_recognition_consumptions DROP COLUMN binding_mode;
ALTER TABLE amex_recognition_consumptions ALTER COLUMN imported_expense_id SET NOT NULL;
COMMIT;
