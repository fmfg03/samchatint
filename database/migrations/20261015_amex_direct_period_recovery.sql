-- Owner-run only. Original imports, receipts and physical membership never change.
-- Install with a compatible release during the approved coordinated pause.
BEGIN;
CREATE TABLE amex_direct_coverage_revisions (
 id uuid PRIMARY KEY,
 import_id uuid NOT NULL UNIQUE REFERENCES amex_statement_imports(id) ON DELETE RESTRICT,
 source_file_id uuid NOT NULL REFERENCES amex_statement_source_files(id) ON DELETE RESTRICT,
 file_sha256 varchar(64) NOT NULL,
 parser_version varchar(30) NOT NULL,
 profile varchar(30) NOT NULL,
 original_period_start date NOT NULL,
 original_period_end date NOT NULL,
 period_start date NOT NULL,
 period_end date NOT NULL,
 original_counts jsonb NOT NULL,
 effective_counts jsonb NOT NULL,
 expected_item_ids jsonb NOT NULL,
 expected_count integer NOT NULL CHECK(expected_count>0),
 expected_source_version integer NOT NULL CHECK(expected_source_version>0),
 actor_id uuid NOT NULL REFERENCES empleados(id) ON DELETE RESTRICT,
 reason text NOT NULL CHECK(length(trim(reason))>0),
 idempotency_key varchar(200) NOT NULL,
 request_hash varchar(64) NOT NULL CHECK(length(request_hash)=64),
 preview_hash varchar(64) NOT NULL CHECK(length(preview_hash)=64),
 membership_digest varchar(64) NOT NULL CHECK(length(membership_digest)=64),
 created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK(period_start<=original_period_start AND period_end>=original_period_end)
);
CREATE TABLE amex_direct_recovered_rows (
 id uuid PRIMARY KEY,
 revision_id uuid NOT NULL REFERENCES amex_direct_coverage_revisions(id) ON DELETE RESTRICT,
 direct_item_id uuid NOT NULL UNIQUE REFERENCES amex_direct_items(id) ON DELETE RESTRICT,
 import_item_id uuid NOT NULL UNIQUE REFERENCES amex_statement_import_items(id) ON DELETE RESTRICT,
 imported_expense_id uuid NOT NULL UNIQUE REFERENCES expense_reports(id) ON DELETE RESTRICT,
 row_number integer NOT NULL,
 movement_kind varchar(20) NOT NULL CHECK(movement_kind='debit'),
 economic_date date NOT NULL,
 currency varchar(3) NOT NULL CHECK(currency='MXN'),
 raw_signed_amount numeric(18,2) NOT NULL CHECK(raw_signed_amount<>0),
 raw_amount text NOT NULL,
 amount_column varchar(100) NOT NULL,
 profile varchar(30) NOT NULL,
 effective_source_json jsonb NOT NULL,
 proof_hash varchar(64) NOT NULL CHECK(length(proof_hash)=64),
 created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE VIEW amex_direct_effective_sources AS
SELECT d.id AS id,
 d.import_id AS import_id,
 d.import_item_id AS import_item_id,
 COALESCE(rr.imported_expense_id,d.imported_expense_id) AS imported_expense_id,
 COALESCE(rr.movement_kind,d.movement_kind) AS movement_kind,
 COALESCE(rr.raw_signed_amount,d.raw_signed_amount) AS raw_signed_amount,
 COALESCE(rr.raw_amount,d.raw_amount) AS raw_amount,
 COALESCE(rr.amount_column,d.amount_column) AS amount_column,
 COALESCE(rr.profile,d.profile) AS profile,
 d.profile_version AS profile_version,
 COALESCE(rr.effective_source_json,d.source_json) AS source_json,
 d.context_id AS context_id,
 d.context_version AS context_version,
 d.source_version AS source_version,
 d.source_hash AS source_hash,
 d.match_status AS match_status,
 d.match_reason AS match_reason,
 d.match_proof AS match_proof,
 d.cfdi_id AS cfdi_id,
 d.status_receipt_id AS status_receipt_id,
 d.merchant_alias_id AS merchant_alias_id,
 d.declared_uuid AS declared_uuid,
 d.issuer_rfc AS issuer_rfc,
 d.tournament_id AS tournament_id,
 d.project_code AS project_code,
 d.edition_year AS edition_year,
 d.budget_concept_id AS budget_concept_id,
 d.budget_line_id AS budget_line_id,
 d.budget_version_id AS budget_version_id,
 d.budget_assignment_id AS budget_assignment_id,
 d.budget_status AS budget_status,
 d.accounting_status AS accounting_status,
 d.credit_excluded AS credit_excluded,
 d.updated_at AS updated_at,
 rr.id AS recovery_id, rr.revision_id AS recovery_revision_id,
 rr.proof_hash AS recovery_proof_hash,
 COALESCE(cr.period_start,h.period_start) AS period_start,
 COALESCE(cr.period_end,h.period_end) AS period_end
FROM amex_direct_items d
JOIN amex_statement_imports h ON h.id=d.import_id
LEFT JOIN amex_direct_recovered_rows rr ON rr.direct_item_id=d.id
LEFT JOIN amex_direct_coverage_revisions cr ON cr.import_id=d.import_id;

CREATE FUNCTION amex_direct_recovery_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rev amex_direct_coverage_revisions%ROWTYPE; h amex_statement_imports%ROWTYPE;
 f amex_statement_source_files%ROWTYPE; i amex_direct_items%ROWTYPE;
 receipt amex_statement_import_items%ROWTYPE; e expense_reports%ROWTYPE; original jsonb;
BEGIN
 IF TG_TABLE_NAME='amex_direct_coverage_revisions' THEN
  rev:=NEW;
 ELSE
  SELECT * INTO rev FROM amex_direct_coverage_revisions WHERE id=NEW.revision_id;
 END IF;
 SELECT * INTO h FROM amex_statement_imports WHERE id=rev.import_id FOR UPDATE;
 PERFORM pg_advisory_xact_lock(hashtext('samchat:amex:shared-recognition'));
 IF h.id IS NULL OR h.workflow_mode!='direct' OR EXISTS(SELECT 1 FROM amex_direct_accounting_cuts WHERE import_id=h.id) THEN RAISE EXCEPTION 'Recovery requires unfrozen direct source' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_effective_sources ds JOIN amex_recognition_representations rp ON rp.expense_id=ds.imported_expense_id WHERE ds.import_id=h.id) OR EXISTS(SELECT 1 FROM amex_direct_effective_sources ds JOIN amex_recognition_consumptions rc ON rc.imported_expense_id=ds.imported_expense_id WHERE ds.import_id=h.id) THEN RAISE EXCEPTION 'Recovery cannot change recognized sources' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='amex_direct_coverage_revisions' THEN
  SELECT * INTO f FROM amex_statement_source_files WHERE id=NEW.source_file_id;
  IF NOT EXISTS(SELECT 1 FROM empleados WHERE id=NEW.actor_id AND activo IS TRUE AND rol IN('finanzas','admin','superadmin','super_admin')) THEN RAISE EXCEPTION 'Recovery requires active Finance actor' USING ERRCODE='23514'; END IF;
  IF (f.import_id,f.file_sha256,encode(sha256(f.content),'hex'),h.file_sha256,h.parser_version,h.period_start,h.period_end,h.summary_json->>'profile') IS DISTINCT FROM (NEW.import_id,NEW.file_sha256,NEW.file_sha256,NEW.file_sha256,NEW.parser_version,NEW.original_period_start,NEW.original_period_end,NEW.profile) OR NEW.parser_version!='amex-direct-csv-v1' THEN RAISE EXCEPTION 'Recovery original proof mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.expected_source_version IS DISTINCT FROM (SELECT source_version FROM amex_statement_direct_state WHERE import_id=h.id) THEN RAISE EXCEPTION 'Recovery source version changed' USING ERRCODE='23514'; END IF;
  IF NEW.original_counts IS DISTINCT FROM jsonb_build_object('debit',h.imported_count,'credit',h.credit_count,'unknown',h.unknown_count,'skipped',h.skipped_count) THEN RAISE EXCEPTION 'Recovery original counts mismatch' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM amex_direct_items di WHERE di.import_id=h.id AND di.movement_kind IN('unknown','skipped') AND NOT (di.movement_kind='skipped' AND di.source_json->>'reason'='row_outside_declared_period')) THEN RAISE EXCEPTION 'Recovery only supports period omissions' USING ERRCODE='23514'; END IF;
  IF NEW.expected_item_ids IS DISTINCT FROM (SELECT jsonb_agg(di.id::text ORDER BY di.id::text) FROM amex_direct_items di WHERE di.import_id=h.id AND di.movement_kind='skipped' AND di.source_json->>'reason'='row_outside_declared_period') OR jsonb_array_length(NEW.expected_item_ids)<>NEW.expected_count THEN RAISE EXCEPTION 'Recovery expected membership mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO i FROM amex_direct_items WHERE id=NEW.direct_item_id;
  SELECT * INTO receipt FROM amex_statement_import_items WHERE id=NEW.import_item_id;
  SELECT * INTO e FROM expense_reports WHERE id=NEW.imported_expense_id;
  original:=i.source_json;
  IF (SELECT count(*) FROM amex_direct_recovered_rows WHERE revision_id=rev.id)>=rev.expected_count OR NOT (rev.expected_item_ids ? NEW.direct_item_id::text) THEN RAISE EXCEPTION 'Recovery membership sealed or foreign' USING ERRCODE='23514'; END IF;
  IF (i.import_id,i.import_item_id,i.movement_kind,i.imported_expense_id,receipt.import_id,receipt.row_number,receipt.status,receipt.reason_code) IS DISTINCT FROM (rev.import_id,NEW.import_item_id,'skipped'::varchar,NULL::uuid,rev.import_id,NEW.row_number,'skipped'::varchar,'row_outside_declared_period'::varchar) OR receipt.normalized_json IS DISTINCT FROM original THEN RAISE EXCEPTION 'Recovery requires exact original omitted receipt' USING ERRCODE='23514'; END IF;
  IF i.cfdi_id IS NOT NULL OR i.status_receipt_id IS NOT NULL OR i.budget_assignment_id IS NOT NULL OR i.budget_line_id IS NOT NULL OR i.accounting_status!='pending' THEN RAISE EXCEPTION 'Recovery source has contradictory existing effects' USING ERRCODE='23514'; END IF;
  IF (original->>'file_sha256',original->>'profile_version',original->>'profile',original->>'raw_amount',original->>'amount_column',(original->>'raw_signed_amount')::numeric,(original->>'date')::timestamp::date,original->>'currency') IS DISTINCT FROM (rev.file_sha256,rev.parser_version,NEW.profile,NEW.raw_amount,NEW.amount_column,NEW.raw_signed_amount,NEW.economic_date,NEW.currency) OR NEW.profile IS DISTINCT FROM rev.profile THEN RAISE EXCEPTION 'Recovery source economics proof mismatch' USING ERRCODE='23514'; END IF;
  IF (original->'cells'->>NEW.amount_column) IS DISTINCT FROM NEW.raw_amount OR NEW.raw_signed_amount IS DISTINCT FROM replace(replace(replace(replace(trim(NEW.raw_amount),'$',''),' ',''),',',''),'+','')::numeric THEN RAISE EXCEPTION 'Recovery signed cell proof mismatch' USING ERRCODE='23514'; END IF;
  IF NOT ((NEW.profile='debit_negative' AND NEW.raw_signed_amount<0) OR (NEW.profile='debit_positive' AND NEW.raw_signed_amount>0) OR (NEW.profile='separate_columns' AND NEW.raw_signed_amount>0 AND lower(trim(NEW.amount_column)) IN('cargo','debit','debits'))) THEN RAISE EXCEPTION 'Recovery requires demonstrated debit sign' USING ERRCODE='23514'; END IF;
  IF NEW.economic_date<rev.period_start OR NEW.economic_date>rev.period_end OR NEW.economic_date BETWEEN rev.original_period_start AND rev.original_period_end THEN RAISE EXCEPTION 'Recovery corrected period mismatch' USING ERRCODE='23514'; END IF;
  IF (NEW.effective_source_json - ARRAY['status','reason','movement_kind','amount']) IS DISTINCT FROM (original - ARRAY['status','reason','movement_kind','amount']) OR NEW.effective_source_json->>'status'!='imported' OR NEW.effective_source_json->>'movement_kind'!='debit' OR NEW.effective_source_json->>'reason' IS NOT NULL OR (NEW.effective_source_json->>'amount')::numeric IS DISTINCT FROM abs(NEW.raw_signed_amount) OR NEW.proof_hash IS DISTINCT FROM encode(sha256(convert_to(NEW.effective_source_json::text,'UTF8')),'hex') THEN RAISE EXCEPTION 'Recovery effective source proof mismatch' USING ERRCODE='23514'; END IF;
  IF (e.empleado_id,e.fecha::date,upper(e.currency),round(e.gasto_cantidad::numeric,2),e.origen,e.pagado_con_amex_empresa,e.ultimos_4_digitos,e.estado_gasto) IS DISTINCT FROM (h.employee_id,NEW.economic_date,'MXN'::text,abs(NEW.raw_signed_amount),'amex_batch'::varchar,TRUE,(SELECT last4 FROM amex_card_accounts WHERE id=h.card_account_id),'activo'::varchar) OR e.created_at<(rev.created_at AT TIME ZONE 'UTC') OR e.cfdi_report_id IS NOT NULL OR EXISTS(SELECT 1 FROM amex_direct_items WHERE imported_expense_id=e.id) OR EXISTS(SELECT 1 FROM amex_statement_import_items WHERE imported_expense_id=e.id) OR EXISTS(SELECT 1 FROM amex_recognition_representations WHERE expense_id=e.id) OR EXISTS(SELECT 1 FROM amex_recognition_consumptions WHERE imported_expense_id=e.id OR source_report_expense_id=e.id) THEN RAISE EXCEPTION 'Recovery requires fresh exact bank anchor' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION amex_direct_recovery_seal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rev amex_direct_coverage_revisions%ROWTYPE; actual_ids jsonb; actual_digest text; effective jsonb;
BEGIN
 IF TG_TABLE_NAME='amex_direct_coverage_revisions' THEN rev:=NEW;
 ELSE SELECT * INTO rev FROM amex_direct_coverage_revisions WHERE id=NEW.revision_id; END IF;
 SELECT jsonb_agg(direct_item_id::text ORDER BY direct_item_id::text),encode(sha256(convert_to(string_agg(direct_item_id::text,',' ORDER BY direct_item_id::text),'UTF8')),'hex') INTO actual_ids,actual_digest FROM amex_direct_recovered_rows WHERE revision_id=rev.id;
 IF actual_ids IS DISTINCT FROM rev.expected_item_ids OR actual_digest IS DISTINCT FROM rev.membership_digest OR (SELECT count(*) FROM amex_direct_recovered_rows WHERE revision_id=rev.id)<>rev.expected_count THEN RAISE EXCEPTION 'Recovery must seal complete exact membership' USING ERRCODE='23514'; END IF;
 SELECT jsonb_build_object('debit',count(*) FILTER(WHERE movement_kind='debit'),'credit',count(*) FILTER(WHERE movement_kind='credit'),'unknown',count(*) FILTER(WHERE movement_kind='unknown'),'skipped',count(*) FILTER(WHERE movement_kind='skipped')) INTO effective FROM amex_direct_effective_sources WHERE import_id=rev.import_id;
 IF effective IS DISTINCT FROM rev.effective_counts OR (effective->>'unknown')::integer<>0 OR (effective->>'skipped')::integer<>0 OR (SELECT count(*) FROM amex_direct_effective_sources WHERE import_id=rev.import_id) IS DISTINCT FROM (SELECT total_rows FROM amex_statement_imports WHERE id=rev.import_id) OR (SELECT source_version FROM amex_statement_direct_state WHERE import_id=rev.import_id)<=rev.expected_source_version THEN RAISE EXCEPTION 'Recovery effective coverage/version is incomplete' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_effective_sources WHERE import_id=rev.import_id AND (source_json->>'date')::timestamp::date NOT BETWEEN rev.period_start AND rev.period_end) THEN RAISE EXCEPTION 'Recovery coverage excludes source dates' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER ad_recovery_revision BEFORE INSERT ON amex_direct_coverage_revisions FOR EACH ROW EXECUTE FUNCTION amex_direct_recovery_guard();
CREATE TRIGGER ad_recovery_row BEFORE INSERT ON amex_direct_recovered_rows FOR EACH ROW EXECUTE FUNCTION amex_direct_recovery_guard();
CREATE TRIGGER ad_immutable_amex_direct_coverage_revisions BEFORE UPDATE OR DELETE ON amex_direct_coverage_revisions FOR EACH ROW EXECUTE FUNCTION amex_direct_immutable();
CREATE TRIGGER ad_immutable_amex_direct_recovered_rows BEFORE UPDATE OR DELETE ON amex_direct_recovered_rows FOR EACH ROW EXECUTE FUNCTION amex_direct_immutable();
CREATE CONSTRAINT TRIGGER ad_recovery_seal_revision AFTER INSERT ON amex_direct_coverage_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_recovery_seal();
CREATE CONSTRAINT TRIGGER ad_recovery_seal_row AFTER INSERT ON amex_direct_recovered_rows DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_recovery_seal();

-- Preserve every pre-existing economic/fiscal guard, resolve one canonical source.
CREATE OR REPLACE FUNCTION amex_direct_consumption_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item amex_direct_effective_sources%ROWTYPE; header amex_statement_imports%ROWTYPE; src expense_reports%ROWTYPE;
BEGIN
 IF TG_OP!='INSERT' AND OLD.binding_mode='direct' THEN
  IF TG_OP='DELETE' OR NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'Direct recognized consumption is immutable' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF TG_OP='UPDATE' AND NEW.binding_mode IS DISTINCT FROM OLD.binding_mode AND (NEW.binding_mode='direct' OR OLD.binding_mode='direct') THEN RAISE EXCEPTION 'Cannot convert existing AMEX owner to direct' USING ERRCODE='23514'; END IF;
 IF NEW.binding_mode='direct' THEN
  SELECT * INTO item FROM amex_direct_effective_sources WHERE imported_expense_id=NEW.imported_expense_id;
  SELECT * INTO header FROM amex_statement_imports WHERE id=item.import_id;
  SELECT * INTO src FROM expense_reports WHERE id=NEW.imported_expense_id;
  IF src.currency IS DISTINCT FROM 'MXN' OR NEW.currency IS DISTINCT FROM 'MXN' OR src.fecha IS NULL OR src.fecha::date<item.period_start OR src.fecha::date>item.period_end THEN RAISE EXCEPTION 'Direct recognition bank anchor period/currency is unsupported' USING ERRCODE='23514'; END IF;
  IF item.id IS NULL OR item.movement_kind!='debit' OR NEW.accounting_poliza_id IS NULL OR (header.card_account_id,abs(item.raw_signed_amount),src.fecha::date,upper(src.currency)) IS DISTINCT FROM (NEW.card_account_id,NEW.amount,NEW.economic_date,NEW.currency) THEN RAISE EXCEPTION 'Direct recognition requires exact bank anchor and journal' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- Preserve every pre-existing economic/fiscal guard, resolve one canonical source.
CREATE OR REPLACE FUNCTION amex_direct_cut_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE state amex_statement_direct_state%ROWTYPE; header amex_statement_imports%ROWTYPE;
BEGIN
 SELECT * INTO header FROM amex_statement_imports WHERE id=NEW.import_id FOR UPDATE;
 PERFORM 1 FROM cfdi_reports WHERE id IN(SELECT cfdi_id FROM amex_direct_effective_sources WHERE import_id=NEW.import_id AND cfdi_id IS NOT NULL) ORDER BY id FOR UPDATE;
 SELECT * INTO state FROM amex_statement_direct_state WHERE import_id=NEW.import_id;
 IF header.workflow_mode!='direct' OR (state.context_id,state.context_version,state.source_version) IS DISTINCT FROM (NEW.context_id,NEW.context_version,NEW.source_version) OR NEW.accounting_date<COALESCE((SELECT period_start FROM amex_direct_coverage_revisions WHERE import_id=NEW.import_id),header.period_start) OR NEW.accounting_date>COALESCE((SELECT period_end FROM amex_direct_coverage_revisions WHERE import_id=NEW.import_id),header.period_end) OR length(NEW.source_hash)<>64 OR length(NEW.payload_hash)<>64 THEN RAISE EXCEPTION 'Direct cut source/context/period mismatch' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM empleados WHERE id=NEW.actor_id AND activo IS TRUE AND rol IN('finanzas','admin','superadmin','super_admin')) THEN RAISE EXCEPTION 'Direct cut requires active Finance actor' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- Preserve every pre-existing economic/fiscal guard, resolve one canonical source.
CREATE OR REPLACE FUNCTION amex_direct_cut_item_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item amex_direct_effective_sources%ROWTYPE; cut amex_direct_accounting_cuts%ROWTYPE; header amex_statement_imports%ROWTYPE;
BEGIN
 SELECT * INTO cut FROM amex_direct_accounting_cuts WHERE id=NEW.cut_id;
 SELECT * INTO header FROM amex_statement_imports WHERE id=cut.import_id FOR UPDATE;
 SELECT * INTO item FROM amex_direct_effective_sources WHERE id=NEW.direct_item_id;
 IF (item.import_id,item.import_item_id,item.imported_expense_id,item.source_hash) IS DISTINCT FROM (cut.import_id,NEW.import_item_id,NEW.imported_expense_id,NEW.source_hash) OR (SELECT count(*) FROM amex_direct_cut_items WHERE cut_id=NEW.cut_id)>=header.total_rows THEN RAISE EXCEPTION 'Direct cut membership is invalid or sealed' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind NOT IN('new','prior','credit_exclusion') OR (item.movement_kind='credit') IS DISTINCT FROM (NEW.coverage_kind='credit_exclusion') OR item.movement_kind NOT IN('debit','credit') THEN RAISE EXCEPTION 'Direct cut requires typed economic coverage' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind='credit_exclusion' THEN
 IF item.credit_excluded IS NOT TRUE OR NEW.consumption_id IS NOT NULL OR NEW.accounting_poliza_id IS NOT NULL THEN RAISE EXCEPTION 'Credit exclusion cannot be posted' USING ERRCODE='23514'; END IF;
 ELSE
 IF NEW.consumption_id IS NULL OR NEW.accounting_poliza_id IS NULL THEN RAISE EXCEPTION 'Debit coverage requires journal receipt' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- Preserve every pre-existing economic/fiscal guard, resolve one canonical source.
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
 IF EXISTS(SELECT 1 FROM amex_direct_coverage_revisions cr WHERE cr.import_id=cut.import_id AND (cut.snapshot_json->'recovery_revision_ids' IS DISTINCT FROM jsonb_build_array(cr.id::text) OR cut.snapshot_json->'original_period' IS DISTINCT FROM jsonb_build_object('start',cr.original_period_start::text,'end',cr.original_period_end::text) OR cut.snapshot_json->'effective_period' IS DISTINCT FROM jsonb_build_object('start',cr.period_start::text,'end',cr.period_end::text))) THEN RAISE EXCEPTION 'Direct cut recovery coverage proof mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources es ON es.id=ci.direct_item_id WHERE ci.cut_id=cut.id AND es.recovery_id IS NOT NULL AND (ci.snapshot_json->>'recovery_revision_id' IS DISTINCT FROM es.recovery_revision_id::text OR ci.snapshot_json->>'recovery_proof_hash' IS DISTINCT FROM es.recovery_proof_hash OR ci.snapshot_json->'source' IS DISTINCT FROM es.source_json)) THEN RAISE EXCEPTION 'Direct cut recovered source proof mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id WHERE ci.cut_id=cut.id AND (i.context_id IS DISTINCT FROM cut.context_id OR i.context_version IS DISTINCT FROM cut.context_version OR i.movement_kind IN('unknown','skipped') OR (ci.coverage_kind='new' AND (i.cfdi_id IS NULL OR i.budget_assignment_id IS NULL OR i.status_receipt_id IS NULL)))) THEN RAISE EXCEPTION 'Direct cut contains unresolved fiscal/budget source' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (c.binding_mode!='direct' OR c.imported_expense_id IS DISTINCT FROM i.imported_expense_id OR c.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id OR ci.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id OR c.amount IS DISTINCT FROM abs(i.raw_signed_amount))) THEN RAISE EXCEPTION 'Direct cut economic journal mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='prior' AND (c.binding_mode='report_bridge' OR c.accounting_poliza_id IS NULL OR c.accounting_poliza_id IS DISTINCT FROM ci.accounting_poliza_id OR NOT EXISTS(SELECT 1 FROM amex_recognition_representations r WHERE r.consumption_id=c.id AND r.expense_id=ci.imported_expense_id) OR c.amount IS DISTINCT FROM abs((SELECT raw_signed_amount FROM amex_direct_effective_sources WHERE id=ci.direct_item_id)) OR c.card_account_id IS DISTINCT FROM header.card_account_id
 OR ci.snapshot_json->>'consumption_id' IS DISTINCT FROM c.id::text OR ci.snapshot_json->>'accounting_poliza_id' IS DISTINCT FROM c.accounting_poliza_id::text OR ci.snapshot_json->>'expense_id' IS DISTINCT FROM ci.imported_expense_id::text
 OR NOT (EXISTS(SELECT 1 FROM amex_accounting_cuts ac WHERE ac.accounting_poliza_id=c.accounting_poliza_id AND ac.snapshot_json->'consumption_ids' ? c.id::text)
 OR EXISTS(SELECT 1 FROM amex_direct_cut_items old_ci JOIN amex_direct_accounting_cuts old_cut ON old_cut.id=old_ci.cut_id WHERE old_ci.consumption_id=c.id AND old_ci.coverage_kind='new' AND old_ci.cut_id<>cut.id AND old_ci.accounting_poliza_id=c.accounting_poliza_id AND old_cut.accounting_poliza_id=c.accounting_poliza_id)))) THEN RAISE EXCEPTION 'Prior coverage requires canonical historical receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_cfdi_status_receipts sr ON sr.id=i.status_receipt_id JOIN cfdi_reports cf ON cf.id=i.cfdi_id JOIN amex_statement_direct_contexts ctx ON ctx.id=i.context_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (lower(sr.observed_status)!='vigente' OR sr.observed_at<clock_timestamp()-INTERVAL '24 hours' OR sr.observed_at>clock_timestamp() OR sr.receiver_rfc IS DISTINCT FROM ctx.receiver_rfc OR cf.receptor_rfc IS DISTINCT FROM ctx.receiver_rfc OR upper(cf.moneda) IS DISTINCT FROM ctx.currency OR upper(cf.cfdi_uuid) IS DISTINCT FROM upper(sr.fiscal_uuid) OR upper(cf.emisor_rfc) IS DISTINCT FROM upper(sr.issuer_rfc) OR round(cf.total::numeric,2) IS DISTINCT FROM sr.amount OR cf.origen IS DISTINCT FROM 'sat' OR cf.xml_raw IS NULL OR sr.xml_hash IS DISTINCT FROM encode(sha256(convert_to(cf.xml_raw,'UTF8')),'hex'))) THEN RAISE EXCEPTION 'Direct cut requires current exact fiscal status receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN expense_reports e ON e.id=i.imported_expense_id LEFT JOIN budget_versions v ON v.id=i.budget_version_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (i.edition_year IS DISTINCT FROM extract(year FROM e.fecha)::integer OR i.edition_year IS DISTINCT FROM extract(year FROM cut.accounting_date)::integer OR v.edition_year IS DISTINCT FROM i.edition_year)) THEN RAISE EXCEPTION 'Direct fiscal and accounting dates require budget edition' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id JOIN amex_card_accounts card ON card.id=c.card_account_id JOIN cuentas_contables liability ON liability.id=c.liability_account_id JOIN budget_concepts bc ON bc.id=i.budget_concept_id JOIN expense_reports expense ON expense.id=i.imported_expense_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (card.active IS DISTINCT FROM TRUE OR liability.activo IS DISTINCT FROM TRUE OR card.liability_cuenta_contable_id IS DISTINCT FROM c.liability_account_id OR liability.codigo IS DISTINCT FROM c.liability_code OR bc.active IS DISTINCT FROM TRUE OR bc.budget_direction IS DISTINCT FROM 'expense' OR bc.cuenta_contable_id IS NULL OR expense.cuenta_contable_id IS DISTINCT FROM bc.cuenta_contable_id OR NOT EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=bc.cuenta_contable_id AND l.debe>0))) THEN RAISE EXCEPTION 'Direct cut account mapping changed or absent' USING ERRCODE='23514'; END IF;
 -- Card credits are bank net. Additional credits require exact canonical
 -- withholding evidence, frozen per source; an arbitrary balancing credit fails.
 IF EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.haber>0 AND NOT EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND c.liability_account_id=l.cuenta_contable_id) AND NOT EXISTS(
 SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN expense_reports e ON e.id=i.imported_expense_id JOIN cuentas_contables account ON account.id=l.cuenta_contable_id,
 LATERAL jsonb_array_elements(COALESCE(ci.snapshot_json->'fiscal_retention_proof','[]'::jsonb)) proof
 WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND account.activo IS TRUE AND account.codigo=proof->>'cuenta_codigo'
 AND ((NULLIF(e.retencion_cuentas_json->>(proof->>'impuesto'),'') IS NOT NULL AND e.retencion_cuentas_json->>(proof->>'impuesto')=l.cuenta_contable_id::text) OR (NULLIF(e.retencion_cuentas_json->>(proof->>'impuesto'),'') IS NULL AND account.codigo=CASE proof->>'impuesto' WHEN '001' THEN '2150-001-002' WHEN '002' THEN '2150-002-001' ELSE NULL END))
 AND proof->>'cuenta_contable_id'=l.cuenta_contable_id::text AND (proof->>'importe')::numeric=l.haber::numeric
 AND l.raw_row_json->>'movement'='haber_retencion' AND l.raw_row_json->>'expense_id'=i.imported_expense_id::text
 AND EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(ci.snapshot_json->'journal_lines','[]'::jsonb)) frozen WHERE frozen->>'cuenta_contable_id'=l.cuenta_contable_id::text AND (frozen->>'haber')::numeric=l.haber::numeric AND COALESCE((frozen->>'debe')::numeric,0)=0)
 )) THEN RAISE EXCEPTION 'Direct journal credit requires card or exact fiscal withholding' USING ERRCODE='23514'; END IF;
 -- Prove each source's retained amount by tax code, not by a supplied journal
 -- total. Shared invoices use the same explicit, cent-rounded fiscal allocation.
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN expense_reports e ON e.id=i.imported_expense_id JOIN cfdi_reports cf ON cf.id=i.cfdi_id
 WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (
 EXISTS(SELECT 1 FROM (
 SELECT proof->>'impuesto' tax, sum((proof->>'importe')::numeric) amount FROM jsonb_array_elements(COALESCE(ci.snapshot_json->'fiscal_retention_proof','[]'::jsonb)) proof GROUP BY proof->>'impuesto'
 ) frozen FULL JOIN (
 SELECT tax->>'impuesto' tax, sum(round((tax->>'importe')::numeric * ((round(e.gasto_cantidad::numeric,2)-round(COALESCE(e.propina_no_deducible,0)::numeric,2)) / NULLIF(round(cf.total::numeric,2),0)),2)) amount FROM jsonb_array_elements(COALESCE(cf.impuestos_detalle->'retenciones','[]'::jsonb)) tax WHERE (tax->>'importe')::numeric>0 GROUP BY tax->>'impuesto'
 ) canonical USING(tax) WHERE COALESCE(frozen.amount,0) IS DISTINCT FROM COALESCE(canonical.amount,0))
 OR COALESCE((SELECT sum(l.haber::numeric) FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'expense_id'=e.id::text AND l.raw_row_json->>'movement'='haber_retencion'),0) IS DISTINCT FROM COALESCE((SELECT sum((proof->>'importe')::numeric) FROM jsonb_array_elements(COALESCE(ci.snapshot_json->'fiscal_retention_proof','[]'::jsonb)) proof),0)
 )) THEN RAISE EXCEPTION 'Direct withholding must match canonical fiscal evidence' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM (SELECT c.liability_account_id account, sum(c.amount) amount FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' GROUP BY c.liability_account_id) expected WHERE expected.amount IS DISTINCT FROM (SELECT sum(l.haber::numeric) FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=expected.account)) THEN RAISE EXCEPTION 'Direct card liability must equal exact bank net' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN expense_reports own_expense ON own_expense.id=i.imported_expense_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND EXISTS(SELECT 1 FROM expense_reports other_expense WHERE other_expense.cfdi_report_id=i.cfdi_id AND other_expense.estado_gasto='activo' AND other_expense.id!=i.imported_expense_id AND (own_expense.cfdi_compartido_confirmado IS DISTINCT FROM TRUE OR other_expense.cfdi_compartido_confirmado IS DISTINCT FROM TRUE))) THEN RAISE EXCEPTION 'Direct fiscal identity has incompatible global usage' USING ERRCODE='23514'; END IF;
 SELECT sum(c.amount) INTO new_total FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new';
 SELECT sum(COALESCE(debe,0)::numeric),sum(COALESCE(haber,0)::numeric) INTO debit_total,credit_total FROM accounting_poliza_lines WHERE poliza_id=cut.accounting_poliza_id;
 IF new_total IS NULL OR new_total<=0 OR debit_total IS NULL OR debit_total<=0 OR round(debit_total,2) IS DISTINCT FROM round(credit_total,2) OR EXISTS(SELECT 1 FROM accounting_poliza_lines WHERE poliza_id=cut.accounting_poliza_id AND (COALESCE(debe,0)<0 OR COALESCE(haber,0)<0 OR (COALESCE(debe,0)>0 AND COALESCE(haber,0)>0))) THEN RAISE EXCEPTION 'Direct journal must balance exact positive economic recognition' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

-- Preserve every pre-existing economic/fiscal guard, resolve one canonical source.
CREATE OR REPLACE FUNCTION amex_direct_cfdi_usage_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE fiscal_id uuid; expense_id uuid;
BEGIN
 IF TG_TABLE_NAME='expense_reports' THEN fiscal_id:=NEW.cfdi_report_id; expense_id:=NEW.id;
 ELSE fiscal_id:=NEW.cfdi_id; SELECT imported_expense_id INTO expense_id FROM amex_direct_effective_sources WHERE id=NEW.id; expense_id:=COALESCE(expense_id,NEW.imported_expense_id); END IF;
 IF fiscal_id IS NOT NULL THEN
 PERFORM 1 FROM cfdi_reports WHERE id=fiscal_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM amex_direct_effective_sources i JOIN amex_direct_cut_items ci ON ci.direct_item_id=i.id WHERE i.cfdi_id=fiscal_id AND i.imported_expense_id IS DISTINCT FROM expense_id) AND NOT EXISTS(SELECT 1 FROM amex_direct_cut_items ci WHERE ci.imported_expense_id=expense_id) THEN RAISE EXCEPTION 'Recognized direct CFDI usage cannot acquire a new economic owner' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

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
  IF EXISTS(SELECT 1 FROM amex_direct_effective_sources es JOIN expense_reports er ON er.id=es.imported_expense_id WHERE es.id=NEW.id AND es.recovery_id IS NOT NULL AND round(er.gasto_cantidad::numeric,2) IS DISTINCT FROM abs(es.raw_signed_amount)) THEN RAISE EXCEPTION 'Recovered amount anchor mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.context_id IS NULL AND (NEW.cfdi_id IS NOT NULL OR NEW.budget_line_id IS NOT NULL OR NEW.accounting_status!='pending') THEN RAISE EXCEPTION 'Direct effects require fiscal context' USING ERRCODE='23514'; END IF;
  IF NEW.status_receipt_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM amex_cfdi_status_receipts WHERE id=NEW.status_receipt_id AND cfdi_id=NEW.cfdi_id) THEN RAISE EXCEPTION 'Status receipt CFDI mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.budget_assignment_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM budget_movement_assignments WHERE id=NEW.budget_assignment_id AND budget_line_id=NEW.budget_line_id AND budget_version_id=NEW.budget_version_id AND budget_concept_id=NEW.budget_concept_id) THEN RAISE EXCEPTION 'Direct budget assignment mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION amex_direct_recovered_expense_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE row_source amex_direct_recovered_rows%ROWTYPE; h amex_statement_imports%ROWTYPE;
BEGIN
 SELECT rr.* INTO row_source FROM amex_direct_recovered_rows rr WHERE rr.imported_expense_id=OLD.id;
 IF row_source.id IS NULL THEN IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF; END IF;
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Recovered bank anchor cannot be deleted' USING ERRCODE='23514'; END IF;
 SELECT hd.* INTO h FROM amex_direct_coverage_revisions cr JOIN amex_statement_imports hd ON hd.id=cr.import_id WHERE cr.id=row_source.revision_id;
 IF (NEW.empleado_id,NEW.fecha::date,NEW.currency,round(NEW.gasto_cantidad::numeric,2),NEW.origen,NEW.pagado_con_amex_empresa,NEW.ultimos_4_digitos) IS DISTINCT FROM (h.employee_id,row_source.economic_date,row_source.currency,abs(row_source.raw_signed_amount),'amex_batch'::varchar,TRUE,(SELECT last4 FROM amex_card_accounts WHERE id=h.card_account_id)) THEN RAISE EXCEPTION 'Recovered bank anchor identity is immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ad_recovered_expense_identity BEFORE UPDATE OR DELETE ON expense_reports FOR EACH ROW EXECUTE FUNCTION amex_direct_recovered_expense_guard();

COMMIT;
