-- Owner-run rollback refuses any persisted evidence.
BEGIN;
LOCK TABLE amex_statement_imports, amex_statement_import_items, amex_statement_cut_links IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM amex_statement_imports) OR EXISTS(SELECT 1 FROM amex_statement_import_items) OR EXISTS(SELECT 1 FROM amex_statement_cut_links) THEN
 RAISE EXCEPTION 'Cannot remove populated AMEX statement evidence'; END IF;
END $$;
DROP TRIGGER amex_statement_expense_owner_guard ON expense_reports;
-- Table removal also drops both deferred header/item completeness guards.
DROP TABLE amex_statement_cut_links,amex_statement_import_items,amex_statement_imports;
DROP FUNCTION amex_statement_immutable(),amex_statement_item_owner(),amex_statement_counts(),amex_statement_expense_owner();
COMMIT;
