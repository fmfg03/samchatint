-- Explicit owner-run rollback; never erase recovery evidence.
BEGIN;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM amex_direct_coverage_revisions) OR EXISTS(SELECT 1 FROM amex_direct_recovered_rows) THEN
  RAISE EXCEPTION 'Recovery evidence exists; downgrade is prohibited';
 END IF;
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
DROP TRIGGER ad_recovered_expense_identity ON expense_reports;
DROP VIEW amex_direct_effective_sources RESTRICT;
DROP TABLE amex_direct_recovered_rows RESTRICT;
DROP TABLE amex_direct_coverage_revisions RESTRICT;
DROP FUNCTION amex_direct_recovery_guard() RESTRICT;
DROP FUNCTION amex_direct_recovery_seal() RESTRICT;
DROP FUNCTION amex_direct_recovered_expense_guard() RESTRICT;
COMMIT;
