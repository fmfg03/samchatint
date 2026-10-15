-- Owner-run only. No startup DDL, backfill or production execution.
BEGIN;

CREATE TABLE IF NOT EXISTS amex_statement_source_files (
	id UUID NOT NULL,
	import_id UUID NOT NULL,
	content BYTEA NOT NULL,
	file_sha256 VARCHAR(64) NOT NULL,
	byte_size INTEGER NOT NULL,
	filename VARCHAR(200) NOT NULL,
	mime_type VARCHAR(100) NOT NULL,
	actor_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CHECK (byte_size > 0 AND byte_size <= 10485760),
	CHECK (byte_size = octet_length(content)),
	CHECK (length(file_sha256) = 64),
	UNIQUE (import_id),
	FOREIGN KEY(import_id) REFERENCES amex_statement_imports (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;

CREATE TABLE IF NOT EXISTS amex_statement_direct_contexts (
	id UUID NOT NULL,
	import_id UUID NOT NULL,
	version INTEGER NOT NULL,
	entity_code VARCHAR(40) NOT NULL,
	company_code VARCHAR(20) NOT NULL,
	receiver_rfc VARCHAR(13) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	profile VARCHAR(30),
	config_hash VARCHAR(64) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	actor_id UUID NOT NULL,
	reason TEXT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (import_id, version),
	CHECK (version > 0),
	CHECK (currency = 'MXN'),
	FOREIGN KEY(import_id) REFERENCES amex_statement_imports (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;

CREATE TABLE IF NOT EXISTS amex_statement_direct_state (
	import_id UUID NOT NULL,
	context_id UUID,
	context_version INTEGER,
	source_version INTEGER NOT NULL,
	progress_json JSONB,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (import_id),
	FOREIGN KEY(import_id) REFERENCES amex_statement_imports (id) ON DELETE RESTRICT,
	FOREIGN KEY(context_id) REFERENCES amex_statement_direct_contexts (id) ON DELETE RESTRICT
)

;

CREATE TABLE IF NOT EXISTS amex_merchant_identity_aliases (
	id UUID NOT NULL,
	entity_code VARCHAR(40) NOT NULL,
	card_account_id UUID NOT NULL,
	descriptor TEXT NOT NULL,
	issuer_rfc VARCHAR(13) NOT NULL,
	version INTEGER NOT NULL,
	active BOOLEAN NOT NULL,
	actor_id UUID NOT NULL,
	reason TEXT NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (entity_code, card_account_id, descriptor, version),
	CHECK (version > 0),
	FOREIGN KEY(card_account_id) REFERENCES amex_card_accounts (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;

CREATE TABLE IF NOT EXISTS amex_cfdi_status_receipts (
	id UUID NOT NULL,
	cfdi_id UUID NOT NULL,
	fiscal_uuid VARCHAR(36) NOT NULL,
	issuer_rfc VARCHAR(13) NOT NULL,
	receiver_rfc VARCHAR(13) NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	xml_hash VARCHAR(64) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	observed_status VARCHAR(30) NOT NULL,
	response_json JSONB NOT NULL,
	response_hash VARCHAR(64) NOT NULL,
	actor_id UUID NOT NULL,
	observed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(cfdi_id) REFERENCES cfdi_reports (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;

CREATE TABLE IF NOT EXISTS amex_direct_items (
	id UUID NOT NULL,
	import_id UUID NOT NULL,
	import_item_id UUID NOT NULL,
	imported_expense_id UUID,
	movement_kind VARCHAR(20) NOT NULL,
	raw_signed_amount NUMERIC(18, 2),
	raw_amount TEXT,
	amount_column VARCHAR(100),
	profile VARCHAR(30),
	profile_version VARCHAR(30) NOT NULL,
	source_json JSONB NOT NULL,
	context_id UUID,
	context_version INTEGER,
	source_version INTEGER NOT NULL,
	source_hash VARCHAR(64) NOT NULL,
	match_status VARCHAR(30) NOT NULL,
	match_reason VARCHAR(80),
	match_proof JSONB,
	cfdi_id UUID,
	status_receipt_id UUID,
	merchant_alias_id UUID,
	declared_uuid VARCHAR(36),
	issuer_rfc VARCHAR(13),
	tournament_id UUID,
	project_code VARCHAR(100),
	edition_year INTEGER,
	budget_concept_id UUID,
	budget_line_id UUID,
	budget_version_id UUID,
	budget_assignment_id UUID,
	budget_status VARCHAR(30) NOT NULL,
	accounting_status VARCHAR(30) NOT NULL,
	credit_excluded BOOLEAN NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CHECK (source_version > 0),
	CHECK (movement_kind IN ('debit','credit','unknown','skipped')),
	CHECK ((movement_kind='debit' AND imported_expense_id IS NOT NULL) OR (movement_kind!='debit' AND imported_expense_id IS NULL)),
	FOREIGN KEY(import_id) REFERENCES amex_statement_imports (id) ON DELETE RESTRICT,
	UNIQUE (import_item_id),
	FOREIGN KEY(import_item_id) REFERENCES amex_statement_import_items (id) ON DELETE RESTRICT,
	UNIQUE (imported_expense_id),
	FOREIGN KEY(imported_expense_id) REFERENCES expense_reports (id) ON DELETE RESTRICT,
	FOREIGN KEY(context_id) REFERENCES amex_statement_direct_contexts (id) ON DELETE RESTRICT,
	FOREIGN KEY(cfdi_id) REFERENCES cfdi_reports (id) ON DELETE RESTRICT,
	FOREIGN KEY(status_receipt_id) REFERENCES amex_cfdi_status_receipts (id) ON DELETE RESTRICT,
	FOREIGN KEY(merchant_alias_id) REFERENCES amex_merchant_identity_aliases (id) ON DELETE RESTRICT,
	FOREIGN KEY(tournament_id) REFERENCES tournaments (id) ON DELETE RESTRICT,
	FOREIGN KEY(budget_concept_id) REFERENCES budget_concepts (id) ON DELETE RESTRICT
)

;

CREATE TABLE IF NOT EXISTS amex_direct_events (
	id UUID NOT NULL,
	item_id UUID NOT NULL,
	action VARCHAR(40) NOT NULL,
	actor_id UUID NOT NULL,
	source_version INTEGER NOT NULL,
	source_hash VARCHAR(64) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	payload_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(item_id) REFERENCES amex_direct_items (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;

CREATE TABLE IF NOT EXISTS amex_direct_accounting_cuts (
	id UUID NOT NULL,
	import_id UUID NOT NULL,
	context_id UUID NOT NULL,
	context_version INTEGER NOT NULL,
	source_version INTEGER NOT NULL,
	source_hash VARCHAR(64) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	accounting_poliza_id UUID NOT NULL,
	accounting_date DATE NOT NULL,
	actor_id UUID NOT NULL,
	snapshot_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (import_id),
	FOREIGN KEY(import_id) REFERENCES amex_statement_imports (id) ON DELETE RESTRICT,
	FOREIGN KEY(context_id) REFERENCES amex_statement_direct_contexts (id) ON DELETE RESTRICT,
	UNIQUE (accounting_poliza_id),
	FOREIGN KEY(accounting_poliza_id) REFERENCES accounting_polizas (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;

CREATE TABLE IF NOT EXISTS amex_direct_cut_items (
	id UUID NOT NULL,
	cut_id UUID NOT NULL,
	direct_item_id UUID NOT NULL,
	import_item_id UUID NOT NULL,
	imported_expense_id UUID,
	consumption_id UUID,
	accounting_poliza_id UUID,
	coverage_kind VARCHAR(30) NOT NULL,
	source_hash VARCHAR(64) NOT NULL,
	snapshot_json JSONB NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (cut_id, direct_item_id),
	FOREIGN KEY(cut_id) REFERENCES amex_direct_accounting_cuts (id) ON DELETE RESTRICT,
	UNIQUE (direct_item_id),
	FOREIGN KEY(direct_item_id) REFERENCES amex_direct_items (id) ON DELETE RESTRICT,
	FOREIGN KEY(import_item_id) REFERENCES amex_statement_import_items (id) ON DELETE RESTRICT,
	FOREIGN KEY(imported_expense_id) REFERENCES expense_reports (id) ON DELETE RESTRICT,
	FOREIGN KEY(consumption_id) REFERENCES amex_recognition_consumptions (id) ON DELETE RESTRICT,
	FOREIGN KEY(accounting_poliza_id) REFERENCES accounting_polizas (id) ON DELETE RESTRICT
)

;

ALTER TABLE amex_statement_imports ADD COLUMN IF NOT EXISTS workflow_mode VARCHAR(10) NOT NULL DEFAULT 'legacy', ADD COLUMN IF NOT EXISTS credit_count INTEGER NOT NULL DEFAULT 0, ADD COLUMN IF NOT EXISTS unknown_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE amex_statement_import_items ADD COLUMN IF NOT EXISTS workflow_mode VARCHAR(10) NOT NULL DEFAULT 'legacy';
ALTER TABLE amex_statement_imports DROP CONSTRAINT IF EXISTS ck_amex_statement_counts;
ALTER TABLE amex_statement_imports ADD CONSTRAINT ck_amex_statement_counts CHECK (
 (workflow_mode='legacy' AND imported_count>0 AND skipped_count>=0 AND credit_count=0 AND unknown_count=0 AND total_rows=imported_count+skipped_count) OR
 (workflow_mode='direct' AND total_rows>0 AND imported_count>=0 AND skipped_count>=0 AND credit_count>=0 AND unknown_count>=0 AND total_rows=imported_count+skipped_count+credit_count+unknown_count));
ALTER TABLE amex_statement_import_items DROP CONSTRAINT IF EXISTS ck_amex_statement_item_status;
ALTER TABLE amex_statement_import_items ADD CONSTRAINT ck_amex_statement_item_status CHECK (
 (status='imported' AND imported_expense_id IS NOT NULL AND reason_code IS NULL) OR
 (status='skipped' AND imported_expense_id IS NULL AND reason_code IS NOT NULL) OR
 (workflow_mode='direct' AND status='credit' AND imported_expense_id IS NULL) OR
 (workflow_mode='direct' AND status='unknown' AND imported_expense_id IS NULL AND reason_code IS NOT NULL));
ALTER TABLE amex_recognition_consumptions DROP CONSTRAINT IF EXISTS ck_amex_consumption_source_anchor;
ALTER TABLE amex_recognition_consumptions ADD CONSTRAINT ck_amex_consumption_source_anchor CHECK (
 (source_kind='statement' AND imported_expense_id IS NOT NULL AND source_report_expense_id IS NULL AND binding_mode IN ('legacy','report_bridge','direct')) OR
 (source_kind='report' AND imported_expense_id IS NULL AND source_report_expense_id IS NOT NULL AND binding_mode='report'));
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='amex_statement_imports'::regclass AND conname='uq_amex_statement_mode') THEN
 ALTER TABLE amex_statement_imports ADD CONSTRAINT uq_amex_statement_mode UNIQUE(id,workflow_mode); END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='amex_statement_import_items'::regclass AND conname='fk_amex_statement_item_mode') THEN
 ALTER TABLE amex_statement_import_items ADD CONSTRAINT fk_amex_statement_item_mode FOREIGN KEY(import_id,workflow_mode) REFERENCES amex_statement_imports(id,workflow_mode) ON DELETE RESTRICT; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='amex_direct_items'::regclass AND conname='fk_amex_direct_budget_line') THEN
 ALTER TABLE amex_direct_items ADD CONSTRAINT fk_amex_direct_budget_line FOREIGN KEY(budget_line_id) REFERENCES budget_lines(id) ON DELETE RESTRICT;
 ALTER TABLE amex_direct_items ADD CONSTRAINT fk_amex_direct_budget_version FOREIGN KEY(budget_version_id) REFERENCES budget_versions(id) ON DELETE RESTRICT;
 ALTER TABLE amex_direct_items ADD CONSTRAINT fk_amex_direct_budget_assignment FOREIGN KEY(budget_assignment_id) REFERENCES budget_movement_assignments(id) ON DELETE RESTRICT;
 END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS ux_amex_merchant_active ON amex_merchant_identity_aliases(entity_code,card_account_id,descriptor) WHERE active;

CREATE OR REPLACE FUNCTION amex_statement_counts() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE valid_rows integer; omitted_rows integer; credits integer; unknowns integer; receipt amex_statement_imports%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='amex_statement_imports' THEN receipt:=NEW; ELSE SELECT * INTO receipt FROM amex_statement_imports WHERE id=NEW.import_id; END IF;
 SELECT count(*) FILTER(WHERE status='imported'),count(*) FILTER(WHERE status='skipped'),count(*) FILTER(WHERE status='credit'),count(*) FILTER(WHERE status='unknown') INTO valid_rows,omitted_rows,credits,unknowns FROM amex_statement_import_items WHERE import_id=receipt.id;
 IF (valid_rows,omitted_rows,credits,unknowns) IS DISTINCT FROM (receipt.imported_count,receipt.skipped_count,receipt.credit_count,receipt.unknown_count) THEN RAISE EXCEPTION 'AMEX statement row counts mismatch' USING ERRCODE='23514'; END IF;
 IF receipt.workflow_mode='direct' AND (NOT EXISTS(SELECT 1 FROM amex_statement_source_files WHERE import_id=receipt.id) OR (SELECT count(*) FROM amex_direct_items WHERE import_id=receipt.id)<>receipt.total_rows) THEN RAISE EXCEPTION 'Direct intake requires exact original and typed membership' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION amex_direct_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'AMEX direct evidence is immutable' USING ERRCODE='23514'; END $$;
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['amex_statement_source_files','amex_statement_direct_contexts','amex_cfdi_status_receipts','amex_direct_events','amex_direct_accounting_cuts','amex_direct_cut_items'] LOOP
 EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I','ad_immutable_'||name,name);
 EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION amex_direct_immutable()','ad_immutable_'||name,name);
 END LOOP;
END $$;
CREATE OR REPLACE FUNCTION amex_direct_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE header amex_statement_imports%ROWTYPE;
BEGIN
 SELECT * INTO header FROM amex_statement_imports WHERE id=NEW.import_id FOR UPDATE;
 IF header.workflow_mode!='direct' OR header.file_sha256 IS DISTINCT FROM NEW.file_sha256 OR NEW.file_sha256 IS DISTINCT FROM encode(sha256(NEW.content),'hex') OR NEW.byte_size<>octet_length(NEW.content) THEN RAISE EXCEPTION 'Original direct source hash/size mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_direct_source_guard ON amex_statement_source_files;
CREATE TRIGGER amex_direct_source_guard BEFORE INSERT ON amex_statement_source_files FOR EACH ROW EXECUTE FUNCTION amex_direct_source_guard();

CREATE OR REPLACE FUNCTION amex_direct_context_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM 1 FROM amex_statement_imports WHERE id=NEW.import_id AND workflow_mode='direct' FOR UPDATE;
 IF NOT FOUND OR EXISTS(SELECT 1 FROM amex_direct_accounting_cuts WHERE import_id=NEW.import_id) THEN RAISE EXCEPTION 'Invalid or frozen direct context' USING ERRCODE='23514'; END IF;
 IF NEW.version<>COALESCE((SELECT max(version)+1 FROM amex_statement_direct_contexts WHERE import_id=NEW.import_id),1) OR NEW.receiver_rfc<>upper(trim(NEW.receiver_rfc)) OR length(NEW.receiver_rfc) NOT IN(12,13) OR length(NEW.config_hash)<>64 OR length(NEW.payload_hash)<>64 THEN RAISE EXCEPTION 'Invalid direct context version/hash/RFC' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_direct_context_guard ON amex_statement_direct_contexts;
CREATE TRIGGER amex_direct_context_guard BEFORE INSERT ON amex_statement_direct_contexts FOR EACH ROW EXECUTE FUNCTION amex_direct_context_guard();

CREATE OR REPLACE FUNCTION amex_direct_projection_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE import_uuid uuid; ctx amex_statement_direct_contexts%ROWTYPE; receipt amex_statement_import_items%ROWTYPE; src expense_reports%ROWTYPE;
BEGIN
 import_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.import_id ELSE NEW.import_id END;
 PERFORM 1 FROM amex_statement_imports WHERE id=import_uuid AND workflow_mode='direct' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Projection requires direct import' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_accounting_cuts WHERE import_id=import_uuid) THEN RAISE EXCEPTION 'Recognized direct projection is frozen' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Direct projection cannot be deleted' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND NEW.import_id IS DISTINCT FROM OLD.import_id THEN RAISE EXCEPTION 'Direct projection source membership is immutable' USING ERRCODE='23514'; END IF;
 IF NEW.source_version<1 OR (NEW.context_id IS NULL)<>(NEW.context_version IS NULL) THEN RAISE EXCEPTION 'Invalid source/context version' USING ERRCODE='23514'; END IF;
 IF NEW.context_id IS NOT NULL THEN
 SELECT * INTO ctx FROM amex_statement_direct_contexts WHERE id=NEW.context_id;
 IF (ctx.import_id,ctx.version) IS DISTINCT FROM (NEW.import_id,NEW.context_version) THEN RAISE EXCEPTION 'Direct context membership mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_OP='UPDATE' AND NEW.source_version<OLD.source_version THEN RAISE EXCEPTION 'Direct source version cannot regress' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='amex_direct_items' THEN
  SELECT * INTO receipt FROM amex_statement_import_items WHERE id=NEW.import_item_id;
  IF (receipt.import_id,receipt.imported_expense_id,receipt.status) IS DISTINCT FROM (NEW.import_id,NEW.imported_expense_id,CASE WHEN NEW.movement_kind='debit' THEN 'imported' ELSE NEW.movement_kind END) THEN RAISE EXCEPTION 'Direct item anchor mismatch' USING ERRCODE='23514'; END IF;
  IF TG_OP='UPDATE' AND (NEW.import_id,NEW.import_item_id,NEW.imported_expense_id,NEW.movement_kind,NEW.raw_signed_amount,NEW.raw_amount,NEW.amount_column,NEW.profile,NEW.profile_version,NEW.source_json) IS DISTINCT FROM (OLD.import_id,OLD.import_item_id,OLD.imported_expense_id,OLD.movement_kind,OLD.raw_signed_amount,OLD.raw_amount,OLD.amount_column,OLD.profile,OLD.profile_version,OLD.source_json) THEN RAISE EXCEPTION 'Direct physical source is immutable' USING ERRCODE='23514'; END IF;
  IF NEW.movement_kind IN('debit','credit') AND (NEW.raw_signed_amount IS NULL OR NEW.raw_signed_amount=0 OR NEW.profile IS NULL OR NEW.profile NOT IN('debit_positive','debit_negative','separate_columns') OR (NEW.profile!='separate_columns' AND (NEW.movement_kind='debit') IS DISTINCT FROM ((NEW.raw_signed_amount>0)=(NEW.profile='debit_positive'))) OR (NEW.profile='separate_columns' AND (NEW.raw_signed_amount<=0 OR NEW.amount_column IS NULL OR (NEW.movement_kind='debit') IS DISTINCT FROM (lower(trim(NEW.amount_column)) IN('cargo','debit','debits')) OR lower(trim(NEW.amount_column)) NOT IN('cargo','debit','debits','abono','credit','credits')))) THEN RAISE EXCEPTION 'Direct source sign/profile mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.movement_kind='debit' THEN
   SELECT * INTO src FROM expense_reports WHERE id=NEW.imported_expense_id;
   IF round(src.gasto_cantidad::numeric,2) IS DISTINCT FROM abs(NEW.raw_signed_amount) THEN RAISE EXCEPTION 'Direct amount anchor mismatch' USING ERRCODE='23514'; END IF;
  END IF;
  IF NEW.context_id IS NULL AND (NEW.cfdi_id IS NOT NULL OR NEW.budget_line_id IS NOT NULL OR NEW.accounting_status!='pending') THEN RAISE EXCEPTION 'Direct effects require fiscal context' USING ERRCODE='23514'; END IF;
  IF NEW.status_receipt_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM amex_cfdi_status_receipts WHERE id=NEW.status_receipt_id AND cfdi_id=NEW.cfdi_id) THEN RAISE EXCEPTION 'Status receipt CFDI mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.budget_assignment_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM budget_movement_assignments WHERE id=NEW.budget_assignment_id AND budget_line_id=NEW.budget_line_id AND budget_version_id=NEW.budget_version_id AND budget_concept_id=NEW.budget_concept_id) THEN RAISE EXCEPTION 'Direct budget assignment mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['amex_statement_direct_state','amex_direct_items'] LOOP
 EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I','ad_projection_'||name,name);
 EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION amex_direct_projection_guard()','ad_projection_'||name,name);
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION amex_direct_consumption_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item amex_direct_items%ROWTYPE; header amex_statement_imports%ROWTYPE; src expense_reports%ROWTYPE;
BEGIN
 IF TG_OP!='INSERT' AND OLD.binding_mode='direct' THEN
  IF TG_OP='DELETE' OR NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'Direct recognized consumption is immutable' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF TG_OP='UPDATE' AND NEW.binding_mode IS DISTINCT FROM OLD.binding_mode AND (NEW.binding_mode='direct' OR OLD.binding_mode='direct') THEN RAISE EXCEPTION 'Cannot convert existing AMEX owner to direct' USING ERRCODE='23514'; END IF;
 IF NEW.binding_mode='direct' THEN
  SELECT * INTO item FROM amex_direct_items WHERE imported_expense_id=NEW.imported_expense_id;
  SELECT * INTO header FROM amex_statement_imports WHERE id=item.import_id;
  SELECT * INTO src FROM expense_reports WHERE id=NEW.imported_expense_id;
  IF src.currency IS DISTINCT FROM 'MXN' OR NEW.currency IS DISTINCT FROM 'MXN' OR src.fecha IS NULL OR src.fecha::date<header.period_start OR src.fecha::date>header.period_end THEN RAISE EXCEPTION 'Direct recognition bank anchor period/currency is unsupported' USING ERRCODE='23514'; END IF;
  IF item.id IS NULL OR item.movement_kind!='debit' OR NEW.accounting_poliza_id IS NULL OR (header.card_account_id,abs(item.raw_signed_amount),src.fecha::date,upper(src.currency)) IS DISTINCT FROM (NEW.card_account_id,NEW.amount,NEW.economic_date,NEW.currency) THEN RAISE EXCEPTION 'Direct recognition requires exact bank anchor and journal' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_direct_consumption_guard ON amex_recognition_consumptions;
CREATE TRIGGER amex_direct_consumption_guard BEFORE INSERT OR UPDATE OR DELETE ON amex_recognition_consumptions FOR EACH ROW EXECUTE FUNCTION amex_direct_consumption_guard();
CREATE OR REPLACE FUNCTION amex_direct_representation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP!='DELETE' AND EXISTS(SELECT 1 FROM amex_recognition_consumptions WHERE id=NEW.consumption_id AND binding_mode='direct') AND NEW.role!='statement' THEN RAISE EXCEPTION 'Direct consumption cannot acquire report representations' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_direct_representation_guard ON amex_recognition_representations;
CREATE TRIGGER amex_direct_representation_guard BEFORE INSERT OR UPDATE OR DELETE ON amex_recognition_representations FOR EACH ROW EXECUTE FUNCTION amex_direct_representation_guard();

CREATE OR REPLACE FUNCTION amex_direct_cut_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE state amex_statement_direct_state%ROWTYPE; header amex_statement_imports%ROWTYPE;
BEGIN
 SELECT * INTO header FROM amex_statement_imports WHERE id=NEW.import_id FOR UPDATE;
 PERFORM 1 FROM cfdi_reports WHERE id IN(SELECT cfdi_id FROM amex_direct_items WHERE import_id=NEW.import_id AND cfdi_id IS NOT NULL) ORDER BY id FOR UPDATE;
 SELECT * INTO state FROM amex_statement_direct_state WHERE import_id=NEW.import_id;
 IF header.workflow_mode!='direct' OR (state.context_id,state.context_version,state.source_version) IS DISTINCT FROM (NEW.context_id,NEW.context_version,NEW.source_version) OR NEW.accounting_date<header.period_start OR NEW.accounting_date>header.period_end OR length(NEW.source_hash)<>64 OR length(NEW.payload_hash)<>64 THEN RAISE EXCEPTION 'Direct cut source/context/period mismatch' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM empleados WHERE id=NEW.actor_id AND activo IS TRUE AND rol IN('finanzas','admin','superadmin','super_admin')) THEN RAISE EXCEPTION 'Direct cut requires active Finance actor' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_direct_cut_insert_guard ON amex_direct_accounting_cuts;
CREATE TRIGGER amex_direct_cut_insert_guard BEFORE INSERT ON amex_direct_accounting_cuts FOR EACH ROW EXECUTE FUNCTION amex_direct_cut_insert_guard();
CREATE OR REPLACE FUNCTION amex_direct_cut_item_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item amex_direct_items%ROWTYPE; cut amex_direct_accounting_cuts%ROWTYPE; header amex_statement_imports%ROWTYPE;
BEGIN
 SELECT * INTO cut FROM amex_direct_accounting_cuts WHERE id=NEW.cut_id;
 SELECT * INTO header FROM amex_statement_imports WHERE id=cut.import_id FOR UPDATE;
 SELECT * INTO item FROM amex_direct_items WHERE id=NEW.direct_item_id;
 IF (item.import_id,item.import_item_id,item.imported_expense_id,item.source_hash) IS DISTINCT FROM (cut.import_id,NEW.import_item_id,NEW.imported_expense_id,NEW.source_hash) OR (SELECT count(*) FROM amex_direct_cut_items WHERE cut_id=NEW.cut_id)>=header.total_rows THEN RAISE EXCEPTION 'Direct cut membership is invalid or sealed' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind NOT IN('new','prior','credit_exclusion') OR (item.movement_kind='credit') IS DISTINCT FROM (NEW.coverage_kind='credit_exclusion') OR item.movement_kind NOT IN('debit','credit') THEN RAISE EXCEPTION 'Direct cut requires typed economic coverage' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind='credit_exclusion' THEN
 IF item.credit_excluded IS NOT TRUE OR NEW.consumption_id IS NOT NULL OR NEW.accounting_poliza_id IS NOT NULL THEN RAISE EXCEPTION 'Credit exclusion cannot be posted' USING ERRCODE='23514'; END IF;
 ELSE
 IF NEW.consumption_id IS NULL OR NEW.accounting_poliza_id IS NULL THEN RAISE EXCEPTION 'Debit coverage requires journal receipt' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_direct_cut_item_guard ON amex_direct_cut_items;
CREATE TRIGGER amex_direct_cut_item_guard BEFORE INSERT ON amex_direct_cut_items FOR EACH ROW EXECUTE FUNCTION amex_direct_cut_item_guard();

CREATE OR REPLACE FUNCTION amex_direct_check_cut() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cut_uuid uuid; cut amex_direct_accounting_cuts%ROWTYPE; header amex_statement_imports%ROWTYPE; debit_total numeric; credit_total numeric; new_total numeric;
BEGIN
 IF TG_TABLE_NAME='amex_direct_accounting_cuts' THEN cut_uuid:=NEW.id;
 ELSIF TG_TABLE_NAME='amex_direct_cut_items' THEN cut_uuid:=NEW.cut_id;
 ELSIF TG_TABLE_NAME='amex_recognition_consumptions' THEN
 IF NEW.binding_mode!='direct' THEN RETURN NULL; END IF;
 SELECT cut_id INTO cut_uuid FROM amex_direct_cut_items WHERE consumption_id=NEW.id AND coverage_kind='new';
 IF cut_uuid IS NULL THEN RAISE EXCEPTION 'Direct consumption requires complete cut receipt' USING ERRCODE='23514'; END IF;
 ELSE
 SELECT id INTO cut_uuid FROM amex_direct_accounting_cuts WHERE accounting_poliza_id=NEW.poliza_id;
 IF cut_uuid IS NULL THEN RETURN NULL; END IF;
 END IF;
 SELECT * INTO cut FROM amex_direct_accounting_cuts WHERE id=cut_uuid;
 SELECT * INTO header FROM amex_statement_imports WHERE id=cut.import_id;
 IF (SELECT count(*) FROM amex_direct_cut_items WHERE cut_id=cut.id)<>header.total_rows OR NOT EXISTS(SELECT 1 FROM amex_direct_cut_items WHERE cut_id=cut.id AND coverage_kind='new') THEN RAISE EXCEPTION 'Direct cut must include complete membership and new economic activity' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_items i ON i.id=ci.direct_item_id WHERE ci.cut_id=cut.id AND (i.context_id IS DISTINCT FROM cut.context_id OR i.context_version IS DISTINCT FROM cut.context_version OR i.movement_kind IN('unknown','skipped') OR (ci.coverage_kind='new' AND (i.cfdi_id IS NULL OR i.budget_assignment_id IS NULL OR i.status_receipt_id IS NULL)))) THEN RAISE EXCEPTION 'Direct cut contains unresolved fiscal/budget source' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_items i ON i.id=ci.direct_item_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (c.binding_mode!='direct' OR c.imported_expense_id IS DISTINCT FROM i.imported_expense_id OR c.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id OR ci.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id OR c.amount IS DISTINCT FROM abs(i.raw_signed_amount))) THEN RAISE EXCEPTION 'Direct cut economic journal mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='prior' AND (c.binding_mode='report_bridge' OR c.accounting_poliza_id IS NULL OR c.accounting_poliza_id IS DISTINCT FROM ci.accounting_poliza_id OR NOT EXISTS(SELECT 1 FROM amex_recognition_representations r WHERE r.consumption_id=c.id AND r.expense_id=ci.imported_expense_id) OR c.amount IS DISTINCT FROM abs((SELECT raw_signed_amount FROM amex_direct_items WHERE id=ci.direct_item_id)) OR c.card_account_id IS DISTINCT FROM header.card_account_id
 OR ci.snapshot_json->>'consumption_id' IS DISTINCT FROM c.id::text OR ci.snapshot_json->>'accounting_poliza_id' IS DISTINCT FROM c.accounting_poliza_id::text OR ci.snapshot_json->>'expense_id' IS DISTINCT FROM ci.imported_expense_id::text
 OR NOT (EXISTS(SELECT 1 FROM amex_accounting_cuts ac WHERE ac.accounting_poliza_id=c.accounting_poliza_id AND ac.snapshot_json->'consumption_ids' ? c.id::text)
 OR EXISTS(SELECT 1 FROM amex_direct_cut_items old_ci JOIN amex_direct_accounting_cuts old_cut ON old_cut.id=old_ci.cut_id WHERE old_ci.consumption_id=c.id AND old_ci.coverage_kind='new' AND old_ci.cut_id<>cut.id AND old_ci.accounting_poliza_id=c.accounting_poliza_id AND old_cut.accounting_poliza_id=c.accounting_poliza_id)))) THEN RAISE EXCEPTION 'Prior coverage requires canonical historical receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_items i ON i.id=ci.direct_item_id JOIN amex_cfdi_status_receipts sr ON sr.id=i.status_receipt_id JOIN cfdi_reports cf ON cf.id=i.cfdi_id JOIN amex_statement_direct_contexts ctx ON ctx.id=i.context_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (lower(sr.observed_status)!='vigente' OR sr.observed_at<clock_timestamp()-INTERVAL '24 hours' OR sr.observed_at>clock_timestamp() OR sr.receiver_rfc IS DISTINCT FROM ctx.receiver_rfc OR cf.receptor_rfc IS DISTINCT FROM ctx.receiver_rfc OR upper(cf.moneda) IS DISTINCT FROM ctx.currency OR upper(cf.cfdi_uuid) IS DISTINCT FROM upper(sr.fiscal_uuid) OR upper(cf.emisor_rfc) IS DISTINCT FROM upper(sr.issuer_rfc) OR round(cf.total::numeric,2) IS DISTINCT FROM sr.amount OR cf.origen IS DISTINCT FROM 'sat' OR cf.xml_raw IS NULL OR sr.xml_hash IS DISTINCT FROM encode(sha256(convert_to(cf.xml_raw,'UTF8')),'hex'))) THEN RAISE EXCEPTION 'Direct cut requires current exact fiscal status receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_items i ON i.id=ci.direct_item_id JOIN expense_reports e ON e.id=i.imported_expense_id LEFT JOIN budget_versions v ON v.id=i.budget_version_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (i.edition_year IS DISTINCT FROM extract(year FROM e.fecha)::integer OR i.edition_year IS DISTINCT FROM extract(year FROM cut.accounting_date)::integer OR v.edition_year IS DISTINCT FROM i.edition_year)) THEN RAISE EXCEPTION 'Direct fiscal and accounting dates require budget edition' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_items i ON i.id=ci.direct_item_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id JOIN amex_card_accounts card ON card.id=c.card_account_id JOIN cuentas_contables liability ON liability.id=c.liability_account_id JOIN budget_concepts bc ON bc.id=i.budget_concept_id JOIN expense_reports expense ON expense.id=i.imported_expense_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (card.active IS DISTINCT FROM TRUE OR liability.activo IS DISTINCT FROM TRUE OR card.liability_cuenta_contable_id IS DISTINCT FROM c.liability_account_id OR liability.codigo IS DISTINCT FROM c.liability_code OR bc.active IS DISTINCT FROM TRUE OR bc.budget_direction IS DISTINCT FROM 'expense' OR bc.cuenta_contable_id IS NULL OR expense.cuenta_contable_id IS DISTINCT FROM bc.cuenta_contable_id OR NOT EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=bc.cuenta_contable_id AND l.debe>0))) THEN RAISE EXCEPTION 'Direct cut account mapping changed or absent' USING ERRCODE='23514'; END IF;
 -- Card credits are bank net. Additional credits require exact canonical
 -- withholding evidence, frozen per source; an arbitrary balancing credit fails.
 IF EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.haber>0 AND NOT EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND c.liability_account_id=l.cuenta_contable_id) AND NOT EXISTS(
 SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_items i ON i.id=ci.direct_item_id JOIN expense_reports e ON e.id=i.imported_expense_id JOIN cuentas_contables account ON account.id=l.cuenta_contable_id,
 LATERAL jsonb_array_elements(COALESCE(ci.snapshot_json->'fiscal_retention_proof','[]'::jsonb)) proof
 WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND account.activo IS TRUE AND account.codigo=proof->>'cuenta_codigo'
 AND ((NULLIF(e.retencion_cuentas_json->>(proof->>'impuesto'),'') IS NOT NULL AND e.retencion_cuentas_json->>(proof->>'impuesto')=l.cuenta_contable_id::text) OR (NULLIF(e.retencion_cuentas_json->>(proof->>'impuesto'),'') IS NULL AND account.codigo=CASE proof->>'impuesto' WHEN '001' THEN '2150-001-002' WHEN '002' THEN '2150-002-001' ELSE NULL END))
 AND proof->>'cuenta_contable_id'=l.cuenta_contable_id::text AND (proof->>'importe')::numeric=l.haber::numeric
 AND l.raw_row_json->>'movement'='haber_retencion' AND l.raw_row_json->>'expense_id'=i.imported_expense_id::text
 AND EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(ci.snapshot_json->'journal_lines','[]'::jsonb)) frozen WHERE frozen->>'cuenta_contable_id'=l.cuenta_contable_id::text AND (frozen->>'haber')::numeric=l.haber::numeric AND COALESCE((frozen->>'debe')::numeric,0)=0)
 )) THEN RAISE EXCEPTION 'Direct journal credit requires card or exact fiscal withholding' USING ERRCODE='23514'; END IF;
 -- Prove each source's retained amount by tax code, not by a supplied journal
 -- total. Shared invoices use the same explicit, cent-rounded fiscal allocation.
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_items i ON i.id=ci.direct_item_id JOIN expense_reports e ON e.id=i.imported_expense_id JOIN cfdi_reports cf ON cf.id=i.cfdi_id
 WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (
 EXISTS(SELECT 1 FROM (
 SELECT proof->>'impuesto' tax, sum((proof->>'importe')::numeric) amount FROM jsonb_array_elements(COALESCE(ci.snapshot_json->'fiscal_retention_proof','[]'::jsonb)) proof GROUP BY proof->>'impuesto'
 ) frozen FULL JOIN (
 SELECT tax->>'impuesto' tax, sum(round((tax->>'importe')::numeric * ((round(e.gasto_cantidad::numeric,2)-round(COALESCE(e.propina_no_deducible,0)::numeric,2)) / NULLIF(round(cf.total::numeric,2),0)),2)) amount FROM jsonb_array_elements(COALESCE(cf.impuestos_detalle->'retenciones','[]'::jsonb)) tax WHERE (tax->>'importe')::numeric>0 GROUP BY tax->>'impuesto'
 ) canonical USING(tax) WHERE COALESCE(frozen.amount,0) IS DISTINCT FROM COALESCE(canonical.amount,0))
 OR COALESCE((SELECT sum(l.haber::numeric) FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'expense_id'=e.id::text AND l.raw_row_json->>'movement'='haber_retencion'),0) IS DISTINCT FROM COALESCE((SELECT sum((proof->>'importe')::numeric) FROM jsonb_array_elements(COALESCE(ci.snapshot_json->'fiscal_retention_proof','[]'::jsonb)) proof),0)
 )) THEN RAISE EXCEPTION 'Direct withholding must match canonical fiscal evidence' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM (SELECT c.liability_account_id account, sum(c.amount) amount FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' GROUP BY c.liability_account_id) expected WHERE expected.amount IS DISTINCT FROM (SELECT sum(l.haber::numeric) FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=expected.account)) THEN RAISE EXCEPTION 'Direct card liability must equal exact bank net' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_items i ON i.id=ci.direct_item_id JOIN expense_reports own_expense ON own_expense.id=i.imported_expense_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND EXISTS(SELECT 1 FROM expense_reports other_expense WHERE other_expense.cfdi_report_id=i.cfdi_id AND other_expense.estado_gasto='activo' AND other_expense.id!=i.imported_expense_id AND (own_expense.cfdi_compartido_confirmado IS DISTINCT FROM TRUE OR other_expense.cfdi_compartido_confirmado IS DISTINCT FROM TRUE))) THEN RAISE EXCEPTION 'Direct fiscal identity has incompatible global usage' USING ERRCODE='23514'; END IF;
 SELECT sum(c.amount) INTO new_total FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new';
 SELECT sum(COALESCE(debe,0)::numeric),sum(COALESCE(haber,0)::numeric) INTO debit_total,credit_total FROM accounting_poliza_lines WHERE poliza_id=cut.accounting_poliza_id;
 IF new_total IS NULL OR new_total<=0 OR debit_total IS NULL OR debit_total<=0 OR round(debit_total,2) IS DISTINCT FROM round(credit_total,2) OR EXISTS(SELECT 1 FROM accounting_poliza_lines WHERE poliza_id=cut.accounting_poliza_id AND (COALESCE(debe,0)<0 OR COALESCE(haber,0)<0 OR (COALESCE(debe,0)>0 AND COALESCE(haber,0)>0))) THEN RAISE EXCEPTION 'Direct journal must balance exact positive economic recognition' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
DO $$ DECLARE name text; event text; BEGIN
 FOREACH name IN ARRAY ARRAY['amex_direct_accounting_cuts','amex_direct_cut_items','amex_recognition_consumptions','accounting_poliza_lines'] LOOP
 EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I','ad_cut_proof_'||name,name);
 event:=CASE WHEN name IN('amex_recognition_consumptions','accounting_poliza_lines') THEN 'INSERT OR UPDATE' ELSE 'INSERT' END;
 EXECUTE format('CREATE CONSTRAINT TRIGGER %I AFTER %s ON %I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_check_cut()','ad_cut_proof_'||name,event,name);
 END LOOP;
END $$;
CREATE OR REPLACE FUNCTION amex_direct_frozen_reference_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE frozen boolean;
BEGIN
 IF TG_TABLE_NAME='expense_reports' THEN
 SELECT EXISTS(SELECT 1 FROM amex_direct_cut_items WHERE imported_expense_id=OLD.id) INTO frozen;
 ELSIF TG_TABLE_NAME='cfdi_reports' THEN
 SELECT EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_items i ON i.id=ci.direct_item_id WHERE i.cfdi_id=OLD.id) INTO frozen;
 ELSIF TG_TABLE_NAME='budget_movement_assignments' THEN
 SELECT EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_items i ON i.id=ci.direct_item_id WHERE i.budget_assignment_id=OLD.id) INTO frozen;
 ELSIF TG_TABLE_NAME='accounting_polizas' THEN
 SELECT EXISTS(SELECT 1 FROM amex_direct_accounting_cuts WHERE accounting_poliza_id=OLD.id) INTO frozen;
 ELSE
 SELECT EXISTS(SELECT 1 FROM amex_direct_accounting_cuts WHERE accounting_poliza_id=OLD.poliza_id) INTO frozen;
 END IF;
 IF frozen AND (TG_OP='DELETE' OR NEW IS DISTINCT FROM OLD) THEN RAISE EXCEPTION 'Direct recognized reference is immutable' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
END $$;
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['expense_reports','budget_movement_assignments','accounting_polizas','accounting_poliza_lines'] LOOP
 EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I','ad_frozen_'||name,name);
 EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION amex_direct_frozen_reference_guard()','ad_frozen_'||name,name);
 END LOOP;
END $$;
CREATE OR REPLACE FUNCTION amex_direct_alias_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Merchant identity evidence is immutable' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM amex_card_accounts WHERE id=NEW.card_account_id FOR UPDATE;
 IF TG_OP='UPDATE' THEN
 IF OLD.active IS DISTINCT FROM TRUE OR NEW.active IS DISTINCT FROM FALSE OR (to_jsonb(NEW)-'active') IS DISTINCT FROM (to_jsonb(OLD)-'active') THEN RAISE EXCEPTION 'Only audited merchant alias retirement is permitted' USING ERRCODE='23514'; END IF;
 ELSE
 IF NEW.version<>COALESCE((SELECT max(version)+1 FROM amex_merchant_identity_aliases WHERE entity_code=NEW.entity_code AND card_account_id=NEW.card_account_id AND descriptor=NEW.descriptor),1) OR trim(NEW.reason)='' OR length(NEW.payload_hash)<>64 THEN RAISE EXCEPTION 'Invalid merchant identity revision' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_direct_alias_guard ON amex_merchant_identity_aliases;
CREATE TRIGGER amex_direct_alias_guard BEFORE INSERT OR UPDATE OR DELETE ON amex_merchant_identity_aliases FOR EACH ROW EXECUTE FUNCTION amex_direct_alias_guard();
CREATE OR REPLACE FUNCTION amex_direct_alias_revision_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND NOT EXISTS(SELECT 1 FROM amex_merchant_identity_aliases a WHERE a.entity_code=OLD.entity_code AND a.card_account_id=OLD.card_account_id AND a.descriptor=OLD.descriptor AND a.version>OLD.version AND a.active AND trim(a.reason)!='') THEN RAISE EXCEPTION 'Alias retirement requires a real replacement revision' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS amex_direct_alias_revision_check ON amex_merchant_identity_aliases;
CREATE CONSTRAINT TRIGGER amex_direct_alias_revision_check AFTER UPDATE ON amex_merchant_identity_aliases DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_alias_revision_check();
CREATE OR REPLACE FUNCTION amex_direct_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item amex_direct_items%ROWTYPE;
BEGIN
 SELECT * INTO item FROM amex_direct_items WHERE id=NEW.item_id;
 IF (NEW.source_version,NEW.source_hash) IS DISTINCT FROM (item.source_version,item.source_hash) OR length(NEW.payload_hash)<>64 OR trim(NEW.action)='' THEN RAISE EXCEPTION 'Direct audit event source mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS amex_direct_event_guard ON amex_direct_events;
CREATE TRIGGER amex_direct_event_guard BEFORE INSERT ON amex_direct_events FOR EACH ROW EXECUTE FUNCTION amex_direct_event_guard();

CREATE OR REPLACE FUNCTION amex_direct_cross_owner_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='accounting_poliza_lines' THEN
 IF EXISTS(SELECT 1 FROM amex_direct_accounting_cuts WHERE accounting_poliza_id=NEW.poliza_id) THEN RAISE EXCEPTION 'Frozen direct journal cannot gain new lines' USING ERRCODE='23514'; END IF;
 ELSE
 IF EXISTS(SELECT 1 FROM amex_recognition_consumptions c WHERE c.binding_mode='direct' AND NEW.snapshot_json->'consumption_ids' ? c.id::text) THEN RAISE EXCEPTION 'Direct recognition cannot join an INFORME cut' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS ad_cross_owner_lines ON accounting_poliza_lines;
CREATE TRIGGER ad_cross_owner_lines BEFORE INSERT ON accounting_poliza_lines FOR EACH ROW EXECUTE FUNCTION amex_direct_cross_owner_guard();
DROP TRIGGER IF EXISTS ad_cross_owner_informe ON amex_accounting_cuts;
CREATE TRIGGER ad_cross_owner_informe BEFORE INSERT ON amex_accounting_cuts FOR EACH ROW EXECUTE FUNCTION amex_direct_cross_owner_guard();

CREATE OR REPLACE FUNCTION amex_direct_cfdi_usage_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE fiscal_id uuid; expense_id uuid;
BEGIN
 IF TG_TABLE_NAME='expense_reports' THEN fiscal_id:=NEW.cfdi_report_id; expense_id:=NEW.id;
 ELSE fiscal_id:=NEW.cfdi_id; expense_id:=NEW.imported_expense_id; END IF;
 IF fiscal_id IS NOT NULL THEN
 PERFORM 1 FROM cfdi_reports WHERE id=fiscal_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM amex_direct_items i JOIN amex_direct_cut_items ci ON ci.direct_item_id=i.id WHERE i.cfdi_id=fiscal_id AND i.imported_expense_id IS DISTINCT FROM expense_id) AND NOT EXISTS(SELECT 1 FROM amex_direct_cut_items ci WHERE ci.imported_expense_id=expense_id) THEN RAISE EXCEPTION 'Recognized direct CFDI usage cannot acquire a new economic owner' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS ad_cfdi_usage_expense ON expense_reports;
CREATE TRIGGER ad_cfdi_usage_expense BEFORE INSERT OR UPDATE ON expense_reports FOR EACH ROW EXECUTE FUNCTION amex_direct_cfdi_usage_guard();
DROP TRIGGER IF EXISTS ad_cfdi_usage_item ON amex_direct_items;
CREATE TRIGGER ad_cfdi_usage_item BEFORE INSERT OR UPDATE ON amex_direct_items FOR EACH ROW EXECUTE FUNCTION amex_direct_cfdi_usage_guard();

COMMIT;
