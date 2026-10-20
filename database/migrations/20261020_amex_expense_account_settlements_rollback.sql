BEGIN;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM amex_expense_account_settlements) OR EXISTS(SELECT 1 FROM amex_direct_prior_coverages) OR EXISTS(SELECT 1 FROM amex_direct_completion_receipts) THEN RAISE EXCEPTION 'Settlement evidence prevents rollback'; END IF; END $$;
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
DROP TRIGGER aesc_frozen_amex_direct_items ON amex_direct_items;
DROP TRIGGER aesc_frozen_amex_statement_direct_state ON amex_statement_direct_state;
DROP TRIGGER aesc_frozen_amex_statement_direct_contexts ON amex_statement_direct_contexts;
DROP TRIGGER aesc_frozen_amex_direct_coverage_revisions ON amex_direct_coverage_revisions;
DROP TRIGGER aesc_origin_expense_reports ON expense_reports;
DROP TRIGGER aesc_origin_documentos ON documentos;
DROP TRIGGER aesc_origin_accounting_polizas ON accounting_polizas;
DROP TRIGGER aesc_origin_accounting_poliza_lines ON accounting_poliza_lines;
DROP TRIGGER aesc_debtor_new_line ON accounting_poliza_lines;
DROP TRIGGER aesc_liquidation_seal ON accounting_poliza_lines;
DROP TRIGGER aesc_origin_cfdi_reports ON cfdi_reports;
DROP TRIGGER aesc_origin_cuentas_de_gastos ON cuentas_de_gastos;
DROP VIEW amex_direct_prior_sources;
DROP VIEW amex_expense_account_credit_sources;
DROP TABLE amex_expense_account_settlement_allocations;
DROP TABLE amex_expense_account_settlements;
DROP TABLE amex_direct_prior_coverages;
DROP TABLE amex_direct_completion_receipts;
DROP FUNCTION amex_settlement_source_guard();
DROP FUNCTION amex_settlement_header_guard();
DROP FUNCTION amex_settlement_check();
DROP FUNCTION amex_direct_prior_guard();
DROP FUNCTION amex_direct_completion_check();
DROP FUNCTION amex_settlement_frozen_guard();
DROP FUNCTION amex_settlement_liquidation_check();
COMMIT;
