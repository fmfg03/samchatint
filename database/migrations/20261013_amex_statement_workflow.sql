-- Owner-run additive statement workflow migration. No startup DDL.
BEGIN;

CREATE TABLE amex_statement_imports (
	id UUID NOT NULL,
	card_account_id UUID NOT NULL,
	employee_id UUID NOT NULL,
	period_start DATE NOT NULL,
	period_end DATE NOT NULL,
	file_sha256 VARCHAR(64) NOT NULL,
	filename VARCHAR(200) NOT NULL,
	parser_version VARCHAR(50) NOT NULL,
	actor_id UUID NOT NULL,
	total_rows INTEGER NOT NULL,
	imported_count INTEGER NOT NULL,
	skipped_count INTEGER NOT NULL,
	summary_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_amex_statement_identity UNIQUE (file_sha256, card_account_id, employee_id, period_start, period_end, parser_version),
	CONSTRAINT ck_amex_statement_period CHECK (period_end >= period_start),
	CONSTRAINT ck_amex_statement_counts CHECK (imported_count > 0 AND skipped_count >= 0 AND total_rows = imported_count + skipped_count),
	CONSTRAINT ck_amex_statement_hash CHECK (length(file_sha256) = 64),
	FOREIGN KEY(card_account_id) REFERENCES amex_card_accounts (id) ON DELETE RESTRICT,
	FOREIGN KEY(employee_id) REFERENCES empleados (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;

CREATE TABLE amex_statement_import_items (
	id UUID NOT NULL,
	import_id UUID NOT NULL,
	row_number INTEGER NOT NULL,
	imported_expense_id UUID,
	status VARCHAR(20) NOT NULL,
	reason_code VARCHAR(50),
	normalized_json JSONB NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_amex_statement_row UNIQUE (import_id, row_number),
	CONSTRAINT uq_amex_statement_expense UNIQUE (imported_expense_id),
	CONSTRAINT ck_amex_statement_row_number CHECK (row_number >= 2),
	CONSTRAINT ck_amex_statement_item_status CHECK ((status = 'imported' AND imported_expense_id IS NOT NULL AND reason_code IS NULL) OR (status = 'skipped' AND imported_expense_id IS NULL AND reason_code IS NOT NULL)),
	FOREIGN KEY(import_id) REFERENCES amex_statement_imports (id) ON DELETE RESTRICT,
	FOREIGN KEY(imported_expense_id) REFERENCES expense_reports (id) ON DELETE RESTRICT
)

;

CREATE TABLE amex_statement_cut_links (
	id UUID NOT NULL,
	import_id UUID NOT NULL,
	cut_id UUID NOT NULL,
	actor_id UUID NOT NULL,
	request_payload_hash VARCHAR(64) NOT NULL,
	request_payload_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_amex_statement_cut UNIQUE (import_id, cut_id),
	CONSTRAINT ck_amex_statement_link_hash CHECK (length(request_payload_hash) = 64),
	FOREIGN KEY(import_id) REFERENCES amex_statement_imports (id) ON DELETE RESTRICT,
	FOREIGN KEY(cut_id) REFERENCES amex_accounting_cuts (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;

CREATE FUNCTION amex_statement_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'AMEX statement evidence is immutable'; END $$;
CREATE TRIGGER amex_statement_import_guard BEFORE UPDATE OR DELETE ON amex_statement_imports FOR EACH ROW EXECUTE FUNCTION amex_statement_immutable();
CREATE TRIGGER amex_statement_item_guard BEFORE UPDATE OR DELETE ON amex_statement_import_items FOR EACH ROW EXECUTE FUNCTION amex_statement_immutable();
CREATE TRIGGER amex_statement_link_guard BEFORE UPDATE OR DELETE ON amex_statement_cut_links FOR EACH ROW EXECUTE FUNCTION amex_statement_immutable();

CREATE FUNCTION amex_statement_item_owner() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE receipt amex_statement_imports%ROWTYPE; expense expense_reports%ROWTYPE; card amex_card_accounts%ROWTYPE;
BEGIN
 SELECT * INTO receipt FROM amex_statement_imports WHERE id=NEW.import_id FOR UPDATE;
 -- Declared membership seals the intake independently of transaction/subXID.
 IF (SELECT count(*) FROM amex_statement_import_items WHERE import_id=NEW.import_id) >= receipt.total_rows THEN
  RAISE EXCEPTION 'AMEX statement rows cannot be added after intake';
 END IF;
 IF NEW.status='imported' THEN
  SELECT * INTO expense FROM expense_reports WHERE id=NEW.imported_expense_id FOR UPDATE;
  SELECT * INTO card FROM amex_card_accounts WHERE id=receipt.card_account_id;
  IF expense.id IS NULL OR expense.origen IS DISTINCT FROM 'amex_batch'
   OR expense.empleado_id IS DISTINCT FROM receipt.employee_id
   OR expense.ultimos_4_digitos IS DISTINCT FROM card.last4
   OR expense.pagado_con_amex_empresa IS DISTINCT FROM true THEN
   RAISE EXCEPTION 'AMEX statement expense ownership mismatch';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER amex_statement_item_owner_guard BEFORE INSERT ON amex_statement_import_items FOR EACH ROW EXECUTE FUNCTION amex_statement_item_owner();

CREATE FUNCTION amex_statement_counts() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE CONSTRAINT TRIGGER amex_statement_counts_guard AFTER INSERT ON amex_statement_imports DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_statement_counts();
CREATE CONSTRAINT TRIGGER amex_statement_item_counts_guard AFTER INSERT ON amex_statement_import_items DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_statement_counts();

CREATE FUNCTION amex_statement_expense_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM amex_statement_import_items WHERE imported_expense_id=OLD.id)
  AND (NEW.origen IS DISTINCT FROM OLD.origen OR NEW.empleado_id IS DISTINCT FROM OLD.empleado_id
   OR NEW.ultimos_4_digitos IS DISTINCT FROM OLD.ultimos_4_digitos
   OR NEW.pagado_con_amex_empresa IS DISTINCT FROM OLD.pagado_con_amex_empresa) THEN
  RAISE EXCEPTION 'AMEX imported expense identity is immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER amex_statement_expense_owner_guard BEFORE UPDATE ON expense_reports FOR EACH ROW EXECUTE FUNCTION amex_statement_expense_owner();
COMMIT;
