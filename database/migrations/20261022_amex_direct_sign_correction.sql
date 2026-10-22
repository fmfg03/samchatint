-- Owner-run only. No backfill; physical sources and receipts remain immutable.
BEGIN;
CREATE TABLE amex_direct_sign_revisions (
 id uuid PRIMARY KEY,
 import_id uuid NOT NULL UNIQUE REFERENCES amex_statement_imports(id) ON DELETE RESTRICT,
 source_file_id uuid NOT NULL REFERENCES amex_statement_source_files(id) ON DELETE RESTRICT,
 file_sha256 varchar(64) NOT NULL CHECK(length(file_sha256)=64),
 parser_version varchar(30) NOT NULL,
 original_profile varchar(30) NOT NULL,
 profile varchar(30) NOT NULL,
 context_id uuid NOT NULL REFERENCES amex_statement_direct_contexts(id) ON DELETE RESTRICT,
 context_version integer NOT NULL CHECK(context_version>0),
 expected_source_version integer NOT NULL CHECK(expected_source_version>0),
 expected_count integer NOT NULL CHECK(expected_count>0),
 expected_item_ids jsonb NOT NULL,
 original_counts jsonb NOT NULL,
 effective_counts jsonb NOT NULL,
 original_totals jsonb NOT NULL,
 effective_totals jsonb NOT NULL,
 actor_id uuid NOT NULL REFERENCES empleados(id) ON DELETE RESTRICT,
 reason text NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
 idempotency_key varchar(200) NOT NULL CHECK(length(trim(idempotency_key))>0),
 request_hash varchar(64) NOT NULL CHECK(length(request_hash)=64),
 preview_hash varchar(64) NOT NULL CHECK(length(preview_hash)=64),
 membership_digest varchar(64) NOT NULL CHECK(length(membership_digest)=64),
 created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(import_id,idempotency_key),
 CHECK(original_profile IN('debit_positive','debit_negative') AND profile IN('debit_positive','debit_negative') AND profile<>original_profile)
);
CREATE TABLE amex_direct_sign_rows (
 id uuid PRIMARY KEY,
 revision_id uuid NOT NULL REFERENCES amex_direct_sign_revisions(id) ON DELETE RESTRICT,
 direct_item_id uuid NOT NULL UNIQUE REFERENCES amex_direct_items(id) ON DELETE RESTRICT,
 import_item_id uuid NOT NULL UNIQUE REFERENCES amex_statement_import_items(id) ON DELETE RESTRICT,
 row_number integer NOT NULL CHECK(row_number>0),
 movement_kind varchar(20) NOT NULL CHECK(movement_kind IN('debit','credit')),
 profile varchar(30) NOT NULL CHECK(profile IN('debit_positive','debit_negative')),
 effective_source_json jsonb NOT NULL,
 raw_signed_amount numeric(18,2) NOT NULL CHECK(raw_signed_amount<>0),
 imported_expense_id uuid UNIQUE REFERENCES expense_reports(id) ON DELETE RESTRICT,
 original_expense_id uuid REFERENCES expense_reports(id) ON DELETE RESTRICT,
 proof_hash varchar(64) NOT NULL CHECK(length(proof_hash)=64),
 created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(revision_id,row_number),
 CHECK((movement_kind='debit')=(imported_expense_id IS NOT NULL)),
 CHECK((movement_kind='debit')=((raw_signed_amount>0)=(profile='debit_positive')))
);
CREATE OR REPLACE VIEW amex_direct_effective_sources AS
SELECT d.id AS id,
 d.import_id AS import_id,
 d.import_item_id AS import_item_id,
 CASE WHEN sr.id IS NOT NULL THEN sr.imported_expense_id ELSE COALESCE(rr.imported_expense_id,d.imported_expense_id) END AS imported_expense_id,
 CASE WHEN sr.id IS NOT NULL THEN sr.movement_kind ELSE COALESCE(rr.movement_kind,d.movement_kind) END AS movement_kind,
 CASE WHEN sr.id IS NOT NULL THEN sr.raw_signed_amount ELSE COALESCE(rr.raw_signed_amount,d.raw_signed_amount) END AS raw_signed_amount,
 COALESCE(rr.raw_amount,d.raw_amount) AS raw_amount,
 COALESCE(rr.amount_column,d.amount_column) AS amount_column,
 CASE WHEN sr.id IS NOT NULL THEN sr.profile ELSE COALESCE(rr.profile,d.profile) END AS profile,
 d.profile_version AS profile_version,
 CASE WHEN sr.id IS NOT NULL THEN sr.effective_source_json ELSE COALESCE(rr.effective_source_json,d.source_json) END AS source_json,
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
 COALESCE(cr.period_end,h.period_end) AS period_end,
 sr.revision_id AS sign_revision_id, sr.id AS sign_row_id, sr.proof_hash AS sign_proof_hash
FROM amex_direct_items d
JOIN amex_statement_imports h ON h.id=d.import_id
LEFT JOIN amex_direct_recovered_rows rr ON rr.direct_item_id=d.id
LEFT JOIN amex_direct_coverage_revisions cr ON cr.import_id=d.import_id
LEFT JOIN amex_direct_sign_rows sr ON sr.direct_item_id=d.id;

CREATE FUNCTION amex_direct_sign_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rev amex_direct_sign_revisions%ROWTYPE; h amex_statement_imports%ROWTYPE;
 f amex_statement_source_files%ROWTYPE; i amex_direct_items%ROWTYPE;
 receipt amex_statement_import_items%ROWTYPE; e expense_reports%ROWTYPE;
 ctx amex_statement_direct_contexts%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='amex_direct_sign_revisions' THEN rev:=NEW;
 ELSE SELECT * INTO rev FROM amex_direct_sign_revisions WHERE id=NEW.revision_id; END IF;
 SELECT * INTO h FROM amex_statement_imports WHERE id=rev.import_id FOR UPDATE;
 PERFORM pg_advisory_xact_lock(hashtext('samchat:amex:shared-recognition'));
 IF h.id IS NULL OR h.workflow_mode IS DISTINCT FROM 'direct'
 OR EXISTS(SELECT 1 FROM amex_direct_accounting_cuts WHERE import_id=h.id)
 OR EXISTS(SELECT 1 FROM amex_direct_completion_receipts WHERE import_id=h.id)
 OR EXISTS(SELECT 1 FROM amex_direct_coverage_revisions WHERE import_id=h.id)
 THEN RAISE EXCEPTION 'Sign correction requires unfrozen direct source without recovery' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='amex_direct_sign_revisions' THEN
  SELECT * INTO f FROM amex_statement_source_files WHERE id=rev.source_file_id;
  SELECT * INTO ctx FROM amex_statement_direct_contexts WHERE id=rev.context_id;
  IF NOT EXISTS(SELECT 1 FROM empleados WHERE id=rev.actor_id AND activo IS TRUE AND rol IN('finanzas','admin','superadmin','super_admin')) THEN RAISE EXCEPTION 'Sign correction requires active Finance actor' USING ERRCODE='23514'; END IF;
  IF (f.import_id,f.file_sha256,encode(sha256(f.content),'hex'),h.file_sha256,h.parser_version,h.summary_json->>'profile') IS DISTINCT FROM (rev.import_id,rev.file_sha256,rev.file_sha256,rev.file_sha256,rev.parser_version,rev.original_profile) OR rev.parser_version!='amex-direct-csv-v1' THEN RAISE EXCEPTION 'Sign correction original proof mismatch' USING ERRCODE='23514'; END IF;
  IF (ctx.import_id,ctx.version,ctx.profile,ctx.currency) IS DISTINCT FROM (h.id,rev.context_version,rev.original_profile,'MXN'::varchar)
  OR NOT EXISTS(SELECT 1 FROM amex_statement_direct_state s WHERE s.import_id=h.id AND (s.context_id,s.context_version,s.source_version) IS NOT DISTINCT FROM (rev.context_id,rev.context_version,rev.expected_source_version)) THEN RAISE EXCEPTION 'Sign correction context or version changed' USING ERRCODE='23514'; END IF;
  IF rev.expected_count<>h.total_rows OR rev.expected_item_ids IS DISTINCT FROM (SELECT jsonb_agg(d.id::text ORDER BY d.id::text) FROM amex_direct_items d WHERE d.import_id=h.id) OR jsonb_array_length(rev.expected_item_ids)<>rev.expected_count THEN RAISE EXCEPTION 'Sign correction expected membership mismatch' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM amex_direct_items d WHERE d.import_id=h.id AND (d.movement_kind NOT IN('debit','credit') OR d.profile IS DISTINCT FROM rev.original_profile OR d.context_id IS DISTINCT FROM rev.context_id OR d.context_version IS DISTINCT FROM rev.context_version OR d.cfdi_id IS NOT NULL OR d.status_receipt_id IS NOT NULL OR d.match_proof IS NOT NULL OR d.declared_uuid IS NOT NULL OR d.issuer_rfc IS NOT NULL OR d.merchant_alias_id IS NOT NULL OR d.match_status!='pending' OR d.tournament_id IS NOT NULL OR d.project_code IS NOT NULL OR d.edition_year IS NOT NULL OR d.budget_concept_id IS NOT NULL OR d.budget_line_id IS NOT NULL OR d.budget_version_id IS NOT NULL OR d.budget_assignment_id IS NOT NULL OR d.budget_status!='unclassified' OR d.accounting_status!='pending' OR d.credit_excluded IS TRUE OR d.no_cfdi_decision_id IS NOT NULL)) THEN RAISE EXCEPTION 'Sign correction source has incompatible decisions' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM amex_direct_items d JOIN expense_reports old_expense ON old_expense.id=d.imported_expense_id WHERE d.import_id=h.id AND (old_expense.estado_gasto!='activo' OR old_expense.cfdi_report_id IS NOT NULL OR NULLIF(trim(old_expense.cfdi_uuid_manual),'') IS NOT NULL OR old_expense.documento_id IS NOT NULL OR old_expense.solicitud_documento_id IS NOT NULL OR old_expense.informe_documento_id IS NOT NULL OR old_expense.cuenta_gastos_id IS NOT NULL OR old_expense.cuenta_contable_id IS NOT NULL OR old_expense.contra_cuenta_contable_id IS NOT NULL OR old_expense.cuenta_iva_id IS NOT NULL OR old_expense.fecha_reembolso IS NOT NULL OR old_expense.estado_reembolso='pagado' OR old_expense.coi_estado='contabilizado'))
  OR EXISTS(SELECT 1 FROM amex_direct_items d JOIN amex_recognition_consumptions c ON c.imported_expense_id=d.imported_expense_id OR c.source_report_expense_id=d.imported_expense_id WHERE d.import_id=h.id)
  OR EXISTS(SELECT 1 FROM amex_direct_items d JOIN amex_recognition_representations r ON r.expense_id=d.imported_expense_id WHERE d.import_id=h.id)
  OR EXISTS(SELECT 1 FROM amex_direct_items d JOIN amex_direct_events ev ON ev.item_id=d.id WHERE d.import_id=h.id AND ev.action='expense_account_source_selected')
  THEN RAISE EXCEPTION 'Sign correction expenses have incompatible dependencies' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO i FROM amex_direct_items WHERE id=NEW.direct_item_id;
  SELECT * INTO receipt FROM amex_statement_import_items WHERE id=NEW.import_item_id;
  IF (SELECT count(*) FROM amex_direct_sign_rows WHERE revision_id=rev.id)>=rev.expected_count OR NOT (rev.expected_item_ids ? NEW.direct_item_id::text)
  OR (i.import_id,i.import_item_id,i.imported_expense_id,receipt.import_id,receipt.row_number,receipt.normalized_json) IS DISTINCT FROM (rev.import_id,NEW.import_item_id,NEW.original_expense_id,rev.import_id,NEW.row_number,i.source_json)
  OR NEW.raw_signed_amount IS DISTINCT FROM i.raw_signed_amount OR NEW.profile IS DISTINCT FROM rev.profile OR NEW.movement_kind=i.movement_kind
  THEN RAISE EXCEPTION 'Sign correction requires exact opposite original row membership' USING ERRCODE='23514'; END IF;
  IF (NEW.effective_source_json - ARRAY['profile','movement_kind','status','reason','amount']) IS DISTINCT FROM (i.source_json - ARRAY['profile','movement_kind','status','reason','amount'])
  OR NEW.effective_source_json->>'profile' IS DISTINCT FROM rev.profile
  OR NEW.effective_source_json->>'movement_kind' IS DISTINCT FROM NEW.movement_kind
  OR NEW.effective_source_json->>'status' IS DISTINCT FROM (CASE WHEN NEW.movement_kind='debit' THEN 'imported' ELSE 'credit' END)
  OR NEW.effective_source_json->>'reason' IS NOT NULL
  OR (NEW.effective_source_json->>'amount')::numeric IS DISTINCT FROM (CASE WHEN NEW.movement_kind='debit' THEN abs(NEW.raw_signed_amount) ELSE NULL END)
  OR (NEW.effective_source_json->>'date')::timestamp::date NOT BETWEEN h.period_start AND h.period_end
  OR NEW.effective_source_json->>'currency' IS DISTINCT FROM 'MXN'
  OR NEW.proof_hash IS DISTINCT FROM encode(sha256(convert_to(NEW.effective_source_json::text,'UTF8')),'hex') THEN RAISE EXCEPTION 'Sign correction source interpretation proof mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.movement_kind='debit' THEN
   SELECT * INTO e FROM expense_reports WHERE id=NEW.imported_expense_id;
   IF (e.empleado_id,e.fecha::date,upper(e.currency),round(e.gasto_cantidad::numeric,2),e.origen,e.pagado_con_amex_empresa,e.ultimos_4_digitos,e.estado_gasto) IS DISTINCT FROM (h.employee_id,(NEW.effective_source_json->>'date')::timestamp::date,'MXN'::text,abs(NEW.raw_signed_amount),'amex_batch'::varchar,TRUE,(SELECT last4 FROM amex_card_accounts WHERE id=h.card_account_id),'activo'::varchar)
   OR e.created_at<(rev.created_at AT TIME ZONE 'UTC') OR e.cfdi_report_id IS NOT NULL
   OR EXISTS(SELECT 1 FROM amex_direct_items WHERE imported_expense_id=e.id)
   OR EXISTS(SELECT 1 FROM amex_statement_import_items WHERE imported_expense_id=e.id)
   OR EXISTS(SELECT 1 FROM amex_recognition_representations WHERE expense_id=e.id)
   OR EXISTS(SELECT 1 FROM amex_recognition_consumptions WHERE imported_expense_id=e.id OR source_report_expense_id=e.id)
   THEN RAISE EXCEPTION 'Sign correction requires fresh exact debit anchor' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION amex_direct_sign_seal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rev amex_direct_sign_revisions%ROWTYPE; actual_ids jsonb; actual_digest text;
 original_counts jsonb; effective_counts jsonb; original_totals jsonb; effective_totals jsonb;
BEGIN
 IF TG_TABLE_NAME='amex_direct_sign_revisions' THEN rev:=NEW;
 ELSE SELECT * INTO rev FROM amex_direct_sign_revisions WHERE id=NEW.revision_id; END IF;
 SELECT jsonb_agg(direct_item_id::text ORDER BY direct_item_id::text),encode(sha256(convert_to(string_agg(direct_item_id::text,',' ORDER BY direct_item_id::text),'UTF8')),'hex') INTO actual_ids,actual_digest FROM amex_direct_sign_rows WHERE revision_id=rev.id;
 IF actual_ids IS DISTINCT FROM rev.expected_item_ids OR actual_digest IS DISTINCT FROM rev.membership_digest OR (SELECT count(*) FROM amex_direct_sign_rows WHERE revision_id=rev.id)<>rev.expected_count THEN RAISE EXCEPTION 'Sign correction must seal complete exact membership' USING ERRCODE='23514'; END IF;
 SELECT jsonb_build_object('debit',count(*) FILTER(WHERE movement_kind='debit'),'credit',count(*) FILTER(WHERE movement_kind='credit'),'unknown',count(*) FILTER(WHERE movement_kind='unknown'),'skipped',count(*) FILTER(WHERE movement_kind='skipped')),jsonb_build_object('debit',to_char(COALESCE(sum(abs(raw_signed_amount)) FILTER(WHERE movement_kind='debit'),0),'FM999999999999999990.00'),'credit',to_char(COALESCE(sum(abs(raw_signed_amount)) FILTER(WHERE movement_kind='credit'),0),'FM999999999999999990.00')) INTO original_counts,original_totals FROM amex_direct_items WHERE import_id=rev.import_id;
 SELECT jsonb_build_object('debit',count(*) FILTER(WHERE movement_kind='debit'),'credit',count(*) FILTER(WHERE movement_kind='credit'),'unknown',count(*) FILTER(WHERE movement_kind='unknown'),'skipped',count(*) FILTER(WHERE movement_kind='skipped')),jsonb_build_object('debit',to_char(COALESCE(sum(abs(raw_signed_amount)) FILTER(WHERE movement_kind='debit'),0),'FM999999999999999990.00'),'credit',to_char(COALESCE(sum(abs(raw_signed_amount)) FILTER(WHERE movement_kind='credit'),0),'FM999999999999999990.00')) INTO effective_counts,effective_totals FROM amex_direct_effective_sources WHERE import_id=rev.import_id;
 IF (original_counts,effective_counts,original_totals,effective_totals) IS DISTINCT FROM (rev.original_counts,rev.effective_counts,rev.original_totals,rev.effective_totals)
 OR (SELECT source_version FROM amex_statement_direct_state WHERE import_id=rev.import_id)<=rev.expected_source_version THEN RAISE EXCEPTION 'Sign correction counts totals or version incomplete' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_sign_rows r JOIN expense_reports e ON e.id=r.original_expense_id WHERE r.revision_id=rev.id AND (e.estado_gasto IS DISTINCT FROM 'cancelado' OR e.cancelado_por_id IS DISTINCT FROM rev.actor_id OR e.cancelado_en IS NULL OR e.motivo_cancelacion IS DISTINCT FROM rev.reason)) THEN RAISE EXCEPTION 'Sign correction requires audited cancellation of superseded anchors' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_sign_rows r JOIN amex_direct_items i ON i.id=r.direct_item_id WHERE r.revision_id=rev.id AND NOT EXISTS(SELECT 1 FROM amex_direct_events ev WHERE ev.item_id=r.direct_item_id AND ev.actor_id=rev.actor_id AND ev.action='sign_corrected' AND ev.source_version=i.source_version AND ev.payload_json->>'revision_id'=rev.id::text AND ev.payload_json->>'proof_hash'=r.proof_hash AND ev.payload_json->>'reason'=rev.reason AND (ev.payload_json->>'original_expense_id') IS NOT DISTINCT FROM r.original_expense_id::text AND (ev.payload_json->>'imported_expense_id') IS NOT DISTINCT FROM r.imported_expense_id::text)) THEN RAISE EXCEPTION 'Sign correction requires complete per-row audit evidence' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER ad_sign_revision BEFORE INSERT ON amex_direct_sign_revisions FOR EACH ROW EXECUTE FUNCTION amex_direct_sign_guard();
CREATE TRIGGER ad_sign_row BEFORE INSERT ON amex_direct_sign_rows FOR EACH ROW EXECUTE FUNCTION amex_direct_sign_guard();
CREATE TRIGGER ad_immutable_amex_direct_sign_revisions BEFORE UPDATE OR DELETE ON amex_direct_sign_revisions FOR EACH ROW EXECUTE FUNCTION amex_direct_immutable();
CREATE TRIGGER ad_immutable_amex_direct_sign_rows BEFORE UPDATE OR DELETE ON amex_direct_sign_rows FOR EACH ROW EXECUTE FUNCTION amex_direct_immutable();
CREATE CONSTRAINT TRIGGER ad_sign_seal_revision AFTER INSERT ON amex_direct_sign_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_sign_seal();
CREATE CONSTRAINT TRIGGER ad_sign_seal_row AFTER INSERT ON amex_direct_sign_rows DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_sign_seal();

-- Preserve the complete current owner cut/No-CFDI contract; add revision proof.
CREATE OR REPLACE FUNCTION amex_direct_check_cut() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cut_uuid uuid; cut amex_direct_accounting_cuts%ROWTYPE; header amex_statement_imports%ROWTYPE; debit_total numeric; credit_total numeric; new_total numeric;
BEGIN
 IF TG_TABLE_NAME='amex_direct_accounting_cuts' THEN cut_uuid:=NEW.id;
 ELSIF TG_TABLE_NAME='amex_direct_cut_items' THEN cut_uuid:=NEW.cut_id;
 ELSIF TG_TABLE_NAME='amex_recognition_consumptions' THEN
 IF NEW.binding_mode!='direct' THEN RETURN NULL; END IF;
 SELECT cut_id INTO cut_uuid FROM amex_direct_cut_items WHERE consumption_id=NEW.id AND coverage_kind IN('new','new_no_cfdi');
 IF cut_uuid IS NULL THEN RAISE EXCEPTION 'Direct consumption requires complete cut receipt' USING ERRCODE='23514'; END IF;
 ELSE
 SELECT id INTO cut_uuid FROM amex_direct_accounting_cuts WHERE accounting_poliza_id=NEW.poliza_id;
 IF cut_uuid IS NULL THEN RETURN NULL; END IF;
 END IF;
 SELECT * INTO cut FROM amex_direct_accounting_cuts WHERE id=cut_uuid;
 SELECT * INTO header FROM amex_statement_imports WHERE id=cut.import_id;
 IF (SELECT count(*) FROM amex_direct_cut_items WHERE cut_id=cut.id)<>header.total_rows OR NOT EXISTS(SELECT 1 FROM amex_direct_cut_items WHERE cut_id=cut.id AND coverage_kind IN('new','new_no_cfdi','settlement')) THEN RAISE EXCEPTION 'Direct cut must include complete membership and new economic activity' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_coverage_revisions cr WHERE cr.import_id=cut.import_id AND (cut.snapshot_json->'recovery_revision_ids' IS DISTINCT FROM jsonb_build_array(cr.id::text) OR cut.snapshot_json->'original_period' IS DISTINCT FROM jsonb_build_object('start',cr.original_period_start::text,'end',cr.original_period_end::text) OR cut.snapshot_json->'effective_period' IS DISTINCT FROM jsonb_build_object('start',cr.period_start::text,'end',cr.period_end::text))) THEN RAISE EXCEPTION 'Direct cut recovery coverage proof mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources es ON es.id=ci.direct_item_id WHERE ci.cut_id=cut.id AND es.recovery_id IS NOT NULL AND (ci.snapshot_json->>'recovery_revision_id' IS DISTINCT FROM es.recovery_revision_id::text OR ci.snapshot_json->>'recovery_proof_hash' IS DISTINCT FROM es.recovery_proof_hash OR ci.snapshot_json->'source' IS DISTINCT FROM es.source_json)) THEN RAISE EXCEPTION 'Direct cut recovered source proof mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id WHERE ci.cut_id=cut.id AND (i.context_id IS DISTINCT FROM cut.context_id OR i.context_version IS DISTINCT FROM cut.context_version OR i.movement_kind IN('unknown','skipped') OR (ci.coverage_kind='new' AND (i.cfdi_id IS NULL OR i.budget_assignment_id IS NULL OR i.status_receipt_id IS NULL)) OR (ci.coverage_kind='new_no_cfdi' AND (i.budget_assignment_id IS NULL OR i.budget_concept_id IS NULL OR i.budget_version_id IS NULL OR i.budget_line_id IS NULL OR i.edition_year IS NULL)))) THEN RAISE EXCEPTION 'Direct cut contains unresolved fiscal/budget source' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi') AND (c.binding_mode!='direct' OR c.imported_expense_id IS DISTINCT FROM i.imported_expense_id OR c.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id OR ci.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id OR c.amount IS DISTINCT FROM abs(i.raw_signed_amount))) THEN RAISE EXCEPTION 'Direct cut economic journal mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci WHERE ci.cut_id=cut.id AND ci.coverage_kind='prior' AND NOT EXISTS(SELECT 1 FROM amex_direct_prior_coverages pc WHERE pc.cut_id=cut.id AND pc.direct_item_id=ci.direct_item_id AND pc.consumption_id=ci.consumption_id AND pc.accounting_poliza_id=ci.accounting_poliza_id)) THEN RAISE EXCEPTION 'Prior coverage requires canonical association receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci WHERE ci.cut_id=cut.id AND ci.coverage_kind='settlement' AND NOT EXISTS(SELECT 1 FROM amex_expense_account_settlements es WHERE es.cut_id=cut.id AND es.direct_item_id=ci.direct_item_id AND es.accounting_poliza_id=ci.accounting_poliza_id)) THEN RAISE EXCEPTION 'Settlement coverage requires allocation receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_cfdi_status_receipts sr ON sr.id=i.status_receipt_id JOIN cfdi_reports cf ON cf.id=i.cfdi_id JOIN amex_statement_direct_contexts ctx ON ctx.id=i.context_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (lower(sr.observed_status)!='vigente' OR sr.observed_at<clock_timestamp()-INTERVAL '24 hours' OR sr.observed_at>clock_timestamp() OR sr.receiver_rfc IS DISTINCT FROM ctx.receiver_rfc OR cf.receptor_rfc IS DISTINCT FROM ctx.receiver_rfc OR upper(cf.moneda) IS DISTINCT FROM ctx.currency OR upper(cf.cfdi_uuid) IS DISTINCT FROM upper(sr.fiscal_uuid) OR upper(cf.emisor_rfc) IS DISTINCT FROM upper(sr.issuer_rfc) OR round(cf.total::numeric,2) IS DISTINCT FROM sr.amount OR cf.origen IS DISTINCT FROM 'sat' OR cf.xml_raw IS NULL OR sr.xml_hash IS DISTINCT FROM encode(sha256(convert_to(cf.xml_raw,'UTF8')),'hex'))) THEN RAISE EXCEPTION 'Direct cut requires current exact fiscal status receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN expense_reports e ON e.id=i.imported_expense_id LEFT JOIN budget_versions v ON v.id=i.budget_version_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi') AND (i.edition_year IS DISTINCT FROM extract(year FROM e.fecha)::integer OR i.edition_year IS DISTINCT FROM extract(year FROM cut.accounting_date)::integer OR v.edition_year IS DISTINCT FROM i.edition_year)) THEN RAISE EXCEPTION 'Direct fiscal and accounting dates require budget edition' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id JOIN amex_card_accounts card ON card.id=c.card_account_id JOIN cuentas_contables liability ON liability.id=c.liability_account_id JOIN budget_concepts bc ON bc.id=i.budget_concept_id JOIN expense_reports expense ON expense.id=i.imported_expense_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi') AND (card.active IS DISTINCT FROM TRUE OR liability.activo IS DISTINCT FROM TRUE OR card.liability_cuenta_contable_id IS DISTINCT FROM c.liability_account_id OR liability.codigo IS DISTINCT FROM c.liability_code OR bc.active IS DISTINCT FROM TRUE OR bc.budget_direction IS DISTINCT FROM 'expense' OR bc.cuenta_contable_id IS NULL OR expense.cuenta_contable_id IS DISTINCT FROM bc.cuenta_contable_id OR NOT EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=bc.cuenta_contable_id AND l.debe>0))) THEN RAISE EXCEPTION 'Direct cut account mapping changed or absent' USING ERRCODE='23514'; END IF;
 -- Card credits are bank net. Additional credits require exact canonical
 -- withholding evidence, frozen per source; an arbitrary balancing credit fails.
 IF EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.haber>0 AND NOT EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi') AND c.liability_account_id=l.cuenta_contable_id) AND NOT EXISTS(SELECT 1 FROM amex_expense_account_settlements es JOIN amex_card_accounts ca ON ca.id=es.card_account_id WHERE es.cut_id=cut.id AND ca.liability_cuenta_contable_id=l.cuenta_contable_id) AND NOT EXISTS(
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
 IF EXISTS(SELECT 1 FROM (SELECT account,sum(amount) amount FROM (SELECT c.liability_account_id account,c.amount FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi') UNION ALL SELECT ca.liability_cuenta_contable_id,es.amount FROM amex_expense_account_settlements es JOIN amex_card_accounts ca ON ca.id=es.card_account_id WHERE es.cut_id=cut.id) amounts GROUP BY account) expected WHERE expected.amount IS DISTINCT FROM (SELECT sum(l.haber::numeric) FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=expected.account)) THEN RAISE EXCEPTION 'Direct card liability must equal exact bank net' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN expense_reports own_expense ON own_expense.id=i.imported_expense_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND EXISTS(SELECT 1 FROM expense_reports other_expense WHERE other_expense.cfdi_report_id=i.cfdi_id AND other_expense.estado_gasto='activo' AND other_expense.id!=i.imported_expense_id AND (own_expense.cfdi_compartido_confirmado IS DISTINCT FROM TRUE OR other_expense.cfdi_compartido_confirmado IS DISTINCT FROM TRUE))) THEN RAISE EXCEPTION 'Direct fiscal identity has incompatible global usage' USING ERRCODE='23514'; END IF;
 SELECT sum(c.amount) INTO new_total FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi');
 new_total:=coalesce(new_total,0)+coalesce((SELECT sum(amount) FROM amex_expense_account_settlements WHERE cut_id=cut.id),0);
 SELECT sum(COALESCE(debe,0)::numeric),sum(COALESCE(haber,0)::numeric) INTO debit_total,credit_total FROM accounting_poliza_lines WHERE poliza_id=cut.accounting_poliza_id;
 IF new_total IS NULL OR new_total<=0 OR debit_total IS NULL OR debit_total<=0 OR round(debit_total,2) IS DISTINCT FROM round(credit_total,2) OR EXISTS(SELECT 1 FROM accounting_poliza_lines WHERE poliza_id=cut.accounting_poliza_id AND (COALESCE(debe,0)<0 OR COALESCE(haber,0)<0 OR (COALESCE(debe,0)>0 AND COALESCE(haber,0)>0))) THEN RAISE EXCEPTION 'Direct journal must balance exact positive economic recognition' USING ERRCODE='23514'; END IF;

 -- Freeze exactly the canonical SQL membership, including multiplicity.
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items WHERE cut_id=cut.id AND coverage_kind='new_no_cfdi') AND EXISTS(
  (SELECT f FROM jsonb_array_elements(cut.snapshot_json->'partidas') f
   EXCEPT ALL
   SELECT member.snapshot_json FROM amex_direct_cut_items member WHERE member.cut_id=cut.id)
  UNION ALL
  (SELECT member.snapshot_json FROM amex_direct_cut_items member WHERE member.cut_id=cut.id
   EXCEPT ALL
   SELECT f FROM jsonb_array_elements(cut.snapshot_json->'partidas') f)
 ) THEN RAISE EXCEPTION 'No CFDI cut requires exact full amount canonical header membership' USING ERRCODE='23514'; END IF;

 -- A cut containing the new subtype must remain exportable from its frozen
 -- root movements, including rows without any declared item identity anchors.
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items WHERE cut_id=cut.id AND coverage_kind='new_no_cfdi') AND EXISTS(
  (SELECT f FROM jsonb_array_elements(cut.snapshot_json->'lines') f
   EXCEPT ALL
   SELECT f FROM amex_direct_cut_items member CROSS JOIN LATERAL jsonb_array_elements(member.snapshot_json->'journal_lines') f WHERE member.cut_id=cut.id)
  UNION ALL
  (SELECT f FROM amex_direct_cut_items member CROSS JOIN LATERAL jsonb_array_elements(member.snapshot_json->'journal_lines') f WHERE member.cut_id=cut.id
   EXCEPT ALL
   SELECT f FROM jsonb_array_elements(cut.snapshot_json->'lines') f)
 ) THEN RAISE EXCEPTION 'No CFDI cut requires exact full amount snapshot movement multiset' USING ERRCODE='23514'; END IF;

 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_items b ON b.id=ci.direct_item_id JOIN amex_direct_effective_sources i ON i.id=b.id JOIN amex_direct_no_cfdi_decisions d ON d.id=ci.no_cfdi_decision_id JOIN expense_reports e ON e.id=i.imported_expense_id JOIN budget_concepts bc ON bc.id=i.budget_concept_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new_no_cfdi' AND (
 b.no_cfdi_decision_id IS DISTINCT FROM d.id OR d.action IS DISTINCT FROM 'declare' OR d.economic_source_hash IS DISTINCT FROM amex_direct_no_cfdi_source_hash(i.id)
 OR i.budget_assignment_id IS NULL OR i.budget_status IS DISTINCT FROM 'assigned'
 OR ci.snapshot_json->>'no_cfdi_decision_id' IS DISTINCT FROM d.id::text
 OR NOT COALESCE(ci.snapshot_json->'no_cfdi_decision' @> jsonb_build_object('id',d.id::text,'action',d.action::text,'reason',d.reason::text,'treatment',d.treatment::text,'actor_id',d.actor_id::text,'revision',d.revision::text,'economic_source_hash',d.economic_source_hash::text,'context_id',d.context_id::text,'context_version',d.context_version::text,'imported_expense_id',d.imported_expense_id::text),false)
 OR (ci.snapshot_json->'no_cfdi_decision'->>'created_at')::timestamptz IS DISTINCT FROM d.created_at
 OR ci.snapshot_json->>'cfdi_id' IS NOT NULL OR NULLIF(ci.snapshot_json->'cfdi','null'::jsonb) IS NOT NULL
 OR NULLIF(ci.snapshot_json->'taxes','null'::jsonb) IS NOT NULL OR COALESCE(ci.snapshot_json->'fiscal_retention_proof','[]'::jsonb)<>'[]'::jsonb
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(cut.snapshot_json->'partidas') frozen_item WHERE frozen_item->>'direct_item_id'=i.id::text AND frozen_item=ci.snapshot_json)

 OR (SELECT count(*) FROM jsonb_array_elements(cut.snapshot_json->'partidas') frozen_item WHERE frozen_item->>'direct_item_id'=i.id::text)<>1
 OR EXISTS(
  (SELECT f FROM jsonb_array_elements(ci.snapshot_json->'journal_lines') f
   EXCEPT ALL
   SELECT f FROM jsonb_array_elements(cut.snapshot_json->'lines') f WHERE f->'raw_row_json'->>'direct_item_id'=i.id::text OR f->'raw_row_json'->>'expense_id'=i.imported_expense_id::text OR f->'raw_row_json'->>'no_cfdi_decision_id'=d.id::text)
  UNION ALL
  (SELECT f FROM jsonb_array_elements(cut.snapshot_json->'lines') f WHERE f->'raw_row_json'->>'direct_item_id'=i.id::text OR f->'raw_row_json'->>'expense_id'=i.imported_expense_id::text OR f->'raw_row_json'->>'no_cfdi_decision_id'=d.id::text
   EXCEPT ALL
   SELECT f FROM jsonb_array_elements(ci.snapshot_json->'journal_lines') f)
 )
 OR EXISTS(
  (SELECT f->>'cuenta_contable_id',f->>'cuenta_codigo',f->>'concepto',COALESCE((f->>'debe')::numeric,0),COALESCE((f->>'haber')::numeric,0),f->'raw_row_json' FROM jsonb_array_elements(ci.snapshot_json->'journal_lines') f
   EXCEPT ALL
   SELECT l.cuenta_contable_id::text,l.cuenta_codigo,l.concepto,COALESCE(l.debe,0)::numeric,COALESCE(l.haber,0)::numeric,l.raw_row_json FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'direct_item_id'=i.id::text)
  UNION ALL
  (SELECT l.cuenta_contable_id::text,l.cuenta_codigo,l.concepto,COALESCE(l.debe,0)::numeric,COALESCE(l.haber,0)::numeric,l.raw_row_json FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'direct_item_id'=i.id::text
   EXCEPT ALL
   SELECT f->>'cuenta_contable_id',f->>'cuenta_codigo',f->>'concepto',COALESCE((f->>'debe')::numeric,0),COALESCE((f->>'haber')::numeric,0),f->'raw_row_json' FROM jsonb_array_elements(ci.snapshot_json->'journal_lines') f)
 )
 OR (SELECT count(*) FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'direct_item_id'=i.id::text)<>2
 OR (SELECT count(*) FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'expense_id'=i.imported_expense_id::text)<>2
 OR NOT EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'direct_item_id'=i.id::text AND l.cuenta_contable_id=bc.cuenta_contable_id AND l.debe::numeric=abs(i.raw_signed_amount) AND COALESCE(l.haber,0)=0 AND l.raw_row_json->>'movement'='debe_gasto_sin_cfdi' AND l.raw_row_json->>'treatment'=d.treatment AND l.raw_row_json->>'expense_id'=i.imported_expense_id::text AND l.raw_row_json->>'no_cfdi_decision_id'=d.id::text AND l.raw_row_json->>'budget_assignment_id'=i.budget_assignment_id::text)
 OR NOT EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'direct_item_id'=i.id::text AND l.cuenta_contable_id=c.liability_account_id AND l.haber::numeric=abs(i.raw_signed_amount) AND COALESCE(l.debe,0)=0 AND l.raw_row_json->>'movement'='haber_amex' AND l.raw_row_json->>'treatment'=d.treatment AND l.raw_row_json->>'expense_id'=i.imported_expense_id::text AND l.raw_row_json->>'no_cfdi_decision_id'=d.id::text AND l.raw_row_json->>'budget_assignment_id'=i.budget_assignment_id::text)
 OR jsonb_array_length(COALESCE(ci.snapshot_json->'journal_lines','[]'::jsonb))<>2
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(ci.snapshot_json->'journal_lines') frozen_line WHERE NOT COALESCE(cut.snapshot_json->'lines' @> jsonb_build_array(frozen_line),false))
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(ci.snapshot_json->'journal_lines') f WHERE NOT EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'direct_item_id'=i.id::text AND l.cuenta_contable_id::text=f->>'cuenta_contable_id' AND l.debe::numeric=COALESCE((f->>'debe')::numeric,0) AND l.haber::numeric=COALESCE((f->>'haber')::numeric,0) AND l.cuenta_codigo=f->>'cuenta_codigo' AND l.raw_row_json=f->'raw_row_json'))
 )) THEN RAISE EXCEPTION 'No CFDI cut requires exact full amount expense/card movements and frozen declaration' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources es ON es.id=ci.direct_item_id WHERE ci.cut_id=cut.id AND es.sign_revision_id IS NOT NULL AND (ci.snapshot_json->>'sign_revision_id' IS DISTINCT FROM es.sign_revision_id::text OR ci.snapshot_json->>'sign_row_id' IS DISTINCT FROM es.sign_row_id::text OR ci.snapshot_json->>'sign_proof_hash' IS DISTINCT FROM es.sign_proof_hash OR ci.snapshot_json->'source' IS DISTINCT FROM es.source_json OR (ci.snapshot_json->>'expense_id') IS DISTINCT FROM es.imported_expense_id::text)) THEN RAISE EXCEPTION 'Direct cut corrected source proof mismatch' USING ERRCODE='23514'; END IF;
 PERFORM amex_direct_no_cfdi_assert(ci.direct_item_id) FROM amex_direct_cut_items ci WHERE ci.cut_id=cut.id AND ci.coverage_kind='new_no_cfdi';
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION amex_direct_no_cfdi_source_hash(item_uuid uuid) RETURNS text LANGUAGE sql STABLE AS $$
 SELECT encode(sha256(convert_to(concat_ws('|',h.id::text,h.file_sha256,h.card_account_id::text,i.import_item_id::text,COALESCE(i.imported_expense_id::text,''),i.movement_kind,to_char(i.raw_signed_amount,'FM999999999999999990.00'),COALESCE(i.source_json->>'currency',''),COALESCE(i.source_json->>'date',''),COALESCE(i.source_json->>'description',''),COALESCE(i.recovery_id::text,''),COALESCE(i.recovery_revision_id::text,''),COALESCE(i.recovery_proof_hash,''),COALESCE(i.context_id::text,''),COALESCE(i.context_version::text,''),i.period_start::text,i.period_end::text)||CASE WHEN i.sign_revision_id IS NOT NULL THEN concat('|',i.sign_revision_id::text,'|',i.sign_row_id::text,'|',i.sign_proof_hash) ELSE '' END,'UTF8')),'hex')
 FROM amex_direct_effective_sources i JOIN amex_statement_imports h ON h.id=i.import_id WHERE i.id=item_uuid
$$;

CREATE FUNCTION amex_direct_sign_recovery_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM 1 FROM amex_statement_imports WHERE id=NEW.import_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM amex_direct_sign_revisions WHERE import_id=NEW.import_id) THEN RAISE EXCEPTION 'Sign correction cannot combine with period recovery' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ad_sign_no_recovery BEFORE INSERT ON amex_direct_coverage_revisions FOR EACH ROW EXECUTE FUNCTION amex_direct_sign_recovery_guard();

CREATE FUNCTION amex_direct_sign_expense_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM amex_direct_sign_rows WHERE original_expense_id=OLD.id) THEN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Superseded sign anchor is permanent evidence' USING ERRCODE='23514'; END IF;
  IF (NEW.estado_gasto,NEW.cancelado_en,NEW.cancelado_por_id,NEW.motivo_cancelacion,NEW.empleado_id,NEW.fecha,NEW.currency,NEW.gasto_cantidad,NEW.origen,NEW.pagado_con_amex_empresa,NEW.ultimos_4_digitos) IS DISTINCT FROM (OLD.estado_gasto,OLD.cancelado_en,OLD.cancelado_por_id,OLD.motivo_cancelacion,OLD.empleado_id,OLD.fecha,OLD.currency,OLD.gasto_cantidad,OLD.origen,OLD.pagado_con_amex_empresa,OLD.ultimos_4_digitos)
  AND NOT (OLD.estado_gasto='activo' AND NEW.estado_gasto='cancelado' AND EXISTS(SELECT 1 FROM amex_direct_sign_rows r JOIN amex_direct_sign_revisions rev ON rev.id=r.revision_id WHERE r.original_expense_id=OLD.id AND rev.created_at=transaction_timestamp() AND NEW.cancelado_por_id=rev.actor_id AND NEW.cancelado_en IS NOT NULL AND NEW.motivo_cancelacion=rev.reason) AND (NEW.empleado_id,NEW.fecha,NEW.currency,NEW.gasto_cantidad,NEW.origen,NEW.pagado_con_amex_empresa,NEW.ultimos_4_digitos) IS NOT DISTINCT FROM (OLD.empleado_id,OLD.fecha,OLD.currency,OLD.gasto_cantidad,OLD.origen,OLD.pagado_con_amex_empresa,OLD.ultimos_4_digitos))
  THEN RAISE EXCEPTION 'Superseded sign anchor cancellation and economics are immutable' USING ERRCODE='23514'; END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_sign_rows WHERE imported_expense_id=OLD.id) THEN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Corrected sign anchor cannot be deleted' USING ERRCODE='23514'; END IF;
  IF (NEW.empleado_id,NEW.fecha,NEW.currency,NEW.gasto_cantidad,NEW.origen,NEW.pagado_con_amex_empresa,NEW.ultimos_4_digitos,NEW.estado_gasto) IS DISTINCT FROM (OLD.empleado_id,OLD.fecha,OLD.currency,OLD.gasto_cantidad,OLD.origen,OLD.pagado_con_amex_empresa,OLD.ultimos_4_digitos,OLD.estado_gasto) THEN RAISE EXCEPTION 'Corrected sign bank economics are immutable' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
CREATE TRIGGER ad_sign_expense BEFORE UPDATE OR DELETE ON expense_reports FOR EACH ROW EXECUTE FUNCTION amex_direct_sign_expense_guard();

COMMIT;
