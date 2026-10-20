-- Approved AMEX direct Sin CFDI. Owner-run only; no startup DDL/backfill.
BEGIN;

CREATE TABLE amex_direct_no_cfdi_decisions (
	id UUID NOT NULL,
	item_id UUID NOT NULL,
	import_id UUID NOT NULL,
	imported_expense_id UUID NOT NULL,
	context_id UUID NOT NULL,
	context_version INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	action VARCHAR(10) NOT NULL,
	previous_decision_id UUID,
	treatment VARCHAR(40) NOT NULL,
	reason TEXT NOT NULL,
	actor_id UUID NOT NULL,
	request_id VARCHAR(160) NOT NULL,
	idempotency_key VARCHAR(64) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	economic_source_hash VARCHAR(64) NOT NULL,
	versions_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (item_id, revision),
	UNIQUE (item_id, idempotency_key),
	UNIQUE (previous_decision_id),
	CHECK (revision > 0),
	CHECK (action IN ('declare','revoke')),
	CHECK (treatment = 'full_amount_expense_no_tax'),
	CHECK (length(trim(reason)) BETWEEN 1 AND 1000),
	CHECK (length(payload_hash) = 64),
	CHECK (length(economic_source_hash) = 64),
	CHECK (length(idempotency_key) = 64),
	CHECK (length(trim(request_id)) BETWEEN 1 AND 160),
	FOREIGN KEY(item_id) REFERENCES amex_direct_items (id) ON DELETE RESTRICT,
	FOREIGN KEY(import_id) REFERENCES amex_statement_imports (id) ON DELETE RESTRICT,
	FOREIGN KEY(imported_expense_id) REFERENCES expense_reports (id) ON DELETE RESTRICT,
	FOREIGN KEY(context_id) REFERENCES amex_statement_direct_contexts (id) ON DELETE RESTRICT,
	FOREIGN KEY(previous_decision_id) REFERENCES amex_direct_no_cfdi_decisions (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;
ALTER TABLE amex_direct_items ADD COLUMN no_cfdi_decision_id uuid REFERENCES amex_direct_no_cfdi_decisions(id) ON DELETE RESTRICT;
ALTER TABLE amex_direct_cut_items ADD COLUMN no_cfdi_decision_id uuid REFERENCES amex_direct_no_cfdi_decisions(id) ON DELETE RESTRICT;

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
  IF NEW.no_cfdi_decision_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM amex_direct_no_cfdi_decisions d WHERE d.id=NEW.no_cfdi_decision_id AND d.item_id=NEW.id) THEN RAISE EXCEPTION 'No CFDI head must belong to same item' USING ERRCODE='23514'; END IF;
  IF TG_OP='UPDATE' AND NEW.no_cfdi_decision_id IS DISTINCT FROM OLD.no_cfdi_decision_id AND (NEW.no_cfdi_decision_id IS NULL OR NOT EXISTS(SELECT 1 FROM amex_direct_no_cfdi_decisions d WHERE d.id=NEW.no_cfdi_decision_id AND d.previous_decision_id IS NOT DISTINCT FROM OLD.no_cfdi_decision_id)) THEN RAISE EXCEPTION 'No CFDI head requires next append-only decision' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION amex_direct_cut_item_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item amex_direct_effective_sources%ROWTYPE; cut amex_direct_accounting_cuts%ROWTYPE; header amex_statement_imports%ROWTYPE;
BEGIN
 SELECT * INTO cut FROM amex_direct_accounting_cuts WHERE id=NEW.cut_id;
 SELECT * INTO header FROM amex_statement_imports WHERE id=cut.import_id FOR UPDATE;
 SELECT * INTO item FROM amex_direct_effective_sources WHERE id=NEW.direct_item_id;
 IF (item.import_id,item.import_item_id,item.imported_expense_id,item.source_hash) IS DISTINCT FROM (cut.import_id,NEW.import_item_id,NEW.imported_expense_id,NEW.source_hash) OR (SELECT count(*) FROM amex_direct_cut_items WHERE cut_id=NEW.cut_id)>=header.total_rows THEN RAISE EXCEPTION 'Direct cut membership is invalid or sealed' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind NOT IN('new','new_no_cfdi','settlement','prior','credit_exclusion') OR (item.movement_kind='credit') IS DISTINCT FROM (NEW.coverage_kind='credit_exclusion') OR item.movement_kind NOT IN('debit','credit') THEN RAISE EXCEPTION 'Direct cut requires typed economic coverage' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind='credit_exclusion' THEN
 IF item.credit_excluded IS NOT TRUE OR NEW.consumption_id IS NOT NULL OR NEW.accounting_poliza_id IS NOT NULL THEN RAISE EXCEPTION 'Credit exclusion cannot be posted' USING ERRCODE='23514'; END IF;
 ELSIF NEW.coverage_kind='settlement' THEN
 IF NEW.consumption_id IS NOT NULL OR NEW.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id THEN RAISE EXCEPTION 'Settlement has own journal proof, not fiscal consumption' USING ERRCODE='23514'; END IF;
 ELSE
 IF NEW.consumption_id IS NULL OR NEW.accounting_poliza_id IS NULL THEN RAISE EXCEPTION 'Debit coverage requires journal receipt' USING ERRCODE='23514'; END IF;
 END IF;
 IF (NEW.coverage_kind='new_no_cfdi') IS DISTINCT FROM (NEW.no_cfdi_decision_id IS NOT NULL) THEN RAISE EXCEPTION 'No CFDI coverage requires exact decision receipt only' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind='new_no_cfdi' AND NOT EXISTS(SELECT 1 FROM amex_direct_items i JOIN amex_direct_no_cfdi_decisions d ON d.id=i.no_cfdi_decision_id WHERE i.id=NEW.direct_item_id AND d.id=NEW.no_cfdi_decision_id AND d.action='declare') THEN RAISE EXCEPTION 'No CFDI cut requires active current declaration' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
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
 PERFORM amex_direct_no_cfdi_assert(ci.direct_item_id) FROM amex_direct_cut_items ci WHERE ci.cut_id=cut.id AND ci.coverage_kind='new_no_cfdi';
 RETURN NULL;
END $$;
CREATE FUNCTION amex_direct_no_cfdi_source_hash(item_uuid uuid) RETURNS text LANGUAGE sql STABLE AS $$
 SELECT encode(sha256(convert_to(concat_ws('|',h.id::text,h.file_sha256,h.card_account_id::text,i.import_item_id::text,COALESCE(i.imported_expense_id::text,''),i.movement_kind,to_char(i.raw_signed_amount,'FM999999999999999990.00'),COALESCE(i.source_json->>'currency',''),COALESCE(i.source_json->>'date',''),COALESCE(i.source_json->>'description',''),COALESCE(i.recovery_id::text,''),COALESCE(i.recovery_revision_id::text,''),COALESCE(i.recovery_proof_hash,''),COALESCE(i.context_id::text,''),COALESCE(i.context_version::text,''),i.period_start::text,i.period_end::text),'UTF8')),'hex')
 FROM amex_direct_effective_sources i JOIN amex_statement_imports h ON h.id=i.import_id WHERE i.id=item_uuid
$$;

CREATE FUNCTION amex_direct_no_cfdi_assert(item_uuid uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE i amex_direct_effective_sources%ROWTYPE; d amex_direct_no_cfdi_decisions%ROWTYPE; e expense_reports%ROWTYPE;
BEGIN
 SELECT * INTO i FROM amex_direct_effective_sources WHERE id=item_uuid;
 SELECT receipt.* INTO d FROM amex_direct_items b JOIN amex_direct_no_cfdi_decisions receipt ON receipt.id=b.no_cfdi_decision_id WHERE b.id=item_uuid;
 IF d.id IS NULL OR d.action='revoke' THEN RETURN; END IF;
 SELECT * INTO e FROM expense_reports WHERE id=i.imported_expense_id;
 IF (d.item_id,d.import_id,d.imported_expense_id,d.context_id,d.context_version,d.economic_source_hash) IS DISTINCT FROM (i.id,i.import_id,i.imported_expense_id,i.context_id,i.context_version,amex_direct_no_cfdi_source_hash(i.id))
 OR i.movement_kind IS DISTINCT FROM 'debit' OR i.raw_signed_amount IS NULL OR i.raw_signed_amount=0 OR i.context_id IS NULL
 OR e.id IS NULL OR e.estado_gasto IS DISTINCT FROM 'activo' OR upper(e.currency) IS DISTINCT FROM 'MXN'
 OR e.fecha::date IS DISTINCT FROM (i.source_json->>'date')::date
 OR i.source_json->>'currency' IS DISTINCT FROM 'MXN' OR round(e.gasto_cantidad::numeric,2) IS DISTINCT FROM abs(i.raw_signed_amount)
 OR NOT EXISTS(SELECT 1 FROM amex_statement_direct_state ds WHERE ds.import_id=i.import_id AND (ds.context_id,ds.context_version) IS NOT DISTINCT FROM (i.context_id,i.context_version))

 OR NULLIF(trim(i.source_json->>'declared_uuid'),'') IS NOT NULL
 OR i.cfdi_id IS NOT NULL OR i.declared_uuid IS NOT NULL OR i.status_receipt_id IS NOT NULL OR i.match_proof IS NOT NULL
 OR e.cfdi_report_id IS NOT NULL OR NULLIF(trim(e.cfdi_uuid_manual),'') IS NOT NULL OR e.cfdi_compartido_confirmado IS TRUE
 OR NULLIF(trim(e.cfdi_compartido_motivo),'') IS NOT NULL OR e.cuenta_iva_id IS NOT NULL OR NULLIF(trim(e.hospedaje_entidad_fiscal),'') IS NOT NULL OR COALESCE(e.hospedaje_tasa_impuesto,0)<>0
 OR COALESCE(e.iva,0)<>0 OR COALESCE(e.propina_no_deducible,0)<>0 OR COALESCE(e.hospedaje_impuesto_monto,0)<>0
 OR e.hospedaje_impuesto_confirmado IS TRUE OR COALESCE(e.retencion_cuentas_json,'{}'::jsonb)<>'{}'::jsonb
 OR EXISTS(SELECT 1 FROM amex_direct_events ev WHERE ev.item_id=i.id AND ev.action='expense_account_source_selected' AND ev.id=(SELECT ee.id FROM amex_direct_events ee WHERE ee.item_id=i.id AND ee.action='expense_account_source_selected' ORDER BY ee.created_at DESC,ee.id DESC LIMIT 1) AND ev.payload_json->>'coverage_kind' IN('prior','settlement'))
 THEN RAISE EXCEPTION 'No CFDI declaration requires current exclusive unfalsified bank source' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION amex_direct_no_cfdi_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE i amex_direct_effective_sources%ROWTYPE; b amex_direct_items%ROWTYPE; p amex_direct_no_cfdi_decisions%ROWTYPE;
BEGIN
 SELECT * INTO i FROM amex_direct_effective_sources WHERE id=NEW.item_id;
 PERFORM 1 FROM amex_statement_imports WHERE id=i.import_id FOR UPDATE;
 PERFORM pg_advisory_xact_lock(hashtext('samchat:amex:shared-recognition'));
 SELECT * INTO b FROM amex_direct_items WHERE id=NEW.item_id FOR UPDATE;
 SELECT * INTO p FROM amex_direct_no_cfdi_decisions WHERE id=b.no_cfdi_decision_id;
 IF EXISTS(SELECT 1 FROM amex_direct_accounting_cuts WHERE import_id=i.import_id) OR EXISTS(SELECT 1 FROM amex_direct_completion_receipts WHERE import_id=i.import_id)
 OR i.budget_status IN('sent','submitted','assigned') OR i.budget_assignment_id IS NOT NULL
 OR EXISTS(SELECT 1 FROM amex_recognition_representations WHERE expense_id=i.imported_expense_id)
 OR NOT EXISTS(SELECT 1 FROM empleados WHERE id=NEW.actor_id AND activo IS TRUE AND rol IN('finanzas','admin','superadmin','super_admin'))
 OR NEW.versions_json->'confirmed' IS DISTINCT FROM 'true'::jsonb
 OR (NEW.versions_json->'expected_versions'->>NEW.item_id::text)::integer IS DISTINCT FROM b.source_version
 OR NEW.previous_decision_id IS DISTINCT FROM b.no_cfdi_decision_id
 OR NEW.revision IS DISTINCT FROM COALESCE(p.revision,0)+1
 OR (p.id IS NULL AND NEW.action!='declare') OR (p.id IS NOT NULL AND NEW.action=p.action)
 OR (NEW.item_id,NEW.import_id,NEW.imported_expense_id) IS DISTINCT FROM (i.id,i.import_id,i.imported_expense_id)
 THEN RAISE EXCEPTION 'No CFDI decision requires authorized unfrozen sequential transition before budget' USING ERRCODE='23514'; END IF;
 IF NEW.action='declare' AND (NEW.context_id,NEW.context_version,NEW.economic_source_hash) IS DISTINCT FROM (i.context_id,i.context_version,amex_direct_no_cfdi_source_hash(i.id)) THEN RAISE EXCEPTION 'No CFDI declaration source mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.action='revoke' AND (NEW.context_id,NEW.context_version,NEW.economic_source_hash) IS DISTINCT FROM (p.context_id,p.context_version,p.economic_source_hash) THEN RAISE EXCEPTION 'No CFDI revocation preserves original source' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION amex_direct_no_cfdi_seal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item_uuid uuid; d amex_direct_no_cfdi_decisions%ROWTYPE; head uuid;
BEGIN
 IF TG_TABLE_NAME='amex_direct_no_cfdi_decisions' THEN
 item_uuid:=NEW.item_id; d:=NEW;
 SELECT no_cfdi_decision_id INTO head FROM amex_direct_items WHERE id=item_uuid;
 IF head IS DISTINCT FROM d.id AND NOT EXISTS(SELECT 1 FROM amex_direct_no_cfdi_decisions WHERE previous_decision_id=d.id) THEN RAISE EXCEPTION 'No CFDI evidence must be current head or linked predecessor' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM amex_direct_events ev WHERE ev.item_id=d.item_id AND ev.actor_id=d.actor_id AND ev.action='no_cfdi_'||d.action AND ev.payload_json->>'request_payload_hash'=d.payload_hash AND ev.payload_json->>'no_cfdi_decision_id'=d.id::text AND ev.payload_json->'confirmed'='true'::jsonb AND ev.payload_json->>'reason'=d.reason AND ev.source_version=(d.versions_json->'expected_versions'->>d.item_id::text)::integer+1) THEN RAISE EXCEPTION 'No CFDI decision requires matching confirmed audit event' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='amex_direct_events' THEN item_uuid:=NEW.item_id;
 ELSE item_uuid:=NEW.id;
 END IF;
 PERFORM amex_direct_no_cfdi_assert(item_uuid);
 RETURN NULL;
END $$;

CREATE FUNCTION amex_direct_no_cfdi_expense_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM amex_direct_items b JOIN amex_direct_effective_sources i ON i.id=b.id JOIN amex_direct_no_cfdi_decisions d ON d.id=b.no_cfdi_decision_id WHERE i.imported_expense_id=OLD.id AND d.action='declare') THEN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Active No CFDI bank anchor cannot be deleted' USING ERRCODE='23514'; END IF;
 IF (NEW.cfdi_report_id,NEW.cfdi_uuid_manual,NEW.cfdi_compartido_confirmado,NEW.iva,NEW.propina_no_deducible,NEW.hospedaje_impuesto_monto,NEW.hospedaje_impuesto_confirmado,NEW.retencion_cuentas_json,NEW.cuenta_iva_id,NEW.hospedaje_entidad_fiscal,NEW.hospedaje_tasa_impuesto,NEW.cfdi_compartido_motivo,NEW.fecha,NEW.currency,NEW.gasto_cantidad,NEW.estado_gasto) IS DISTINCT FROM (OLD.cfdi_report_id,OLD.cfdi_uuid_manual,OLD.cfdi_compartido_confirmado,OLD.iva,OLD.propina_no_deducible,OLD.hospedaje_impuesto_monto,OLD.hospedaje_impuesto_confirmado,OLD.retencion_cuentas_json,OLD.cuenta_iva_id,OLD.hospedaje_entidad_fiscal,OLD.hospedaje_tasa_impuesto,OLD.cfdi_compartido_motivo,OLD.fecha,OLD.currency,OLD.gasto_cantidad,OLD.estado_gasto) THEN RAISE EXCEPTION 'Active No CFDI requires explicit revocation before fiscal/source changes' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;

CREATE FUNCTION amex_direct_no_cfdi_context_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='amex_statement_direct_state' THEN
 IF (NEW.context_id,NEW.context_version) IS NOT DISTINCT FROM (OLD.context_id,OLD.context_version) THEN RETURN NEW; END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_items i JOIN amex_direct_no_cfdi_decisions d ON d.id=i.no_cfdi_decision_id WHERE i.import_id=NEW.import_id AND d.action='declare') THEN RAISE EXCEPTION 'Active No CFDI requires revocation before context or recovery' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ad_no_cfdi_insert BEFORE INSERT ON amex_direct_no_cfdi_decisions FOR EACH ROW EXECUTE FUNCTION amex_direct_no_cfdi_insert_guard();
CREATE TRIGGER ad_no_cfdi_immutable BEFORE UPDATE OR DELETE ON amex_direct_no_cfdi_decisions FOR EACH ROW EXECUTE FUNCTION amex_direct_immutable();
CREATE CONSTRAINT TRIGGER ad_no_cfdi_decision_seal AFTER INSERT ON amex_direct_no_cfdi_decisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_no_cfdi_seal();
CREATE CONSTRAINT TRIGGER ad_no_cfdi_event_seal AFTER INSERT ON amex_direct_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_no_cfdi_seal();
CREATE CONSTRAINT TRIGGER ad_no_cfdi_item_seal AFTER INSERT OR UPDATE ON amex_direct_items DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_no_cfdi_seal();
CREATE TRIGGER ad_no_cfdi_expense BEFORE UPDATE OR DELETE ON expense_reports FOR EACH ROW EXECUTE FUNCTION amex_direct_no_cfdi_expense_guard();
CREATE TRIGGER ad_no_cfdi_context BEFORE INSERT ON amex_statement_direct_contexts FOR EACH ROW EXECUTE FUNCTION amex_direct_no_cfdi_context_guard();
CREATE TRIGGER ad_no_cfdi_state BEFORE UPDATE ON amex_statement_direct_state FOR EACH ROW EXECUTE FUNCTION amex_direct_no_cfdi_context_guard();
CREATE TRIGGER ad_no_cfdi_recovery BEFORE INSERT ON amex_direct_coverage_revisions FOR EACH ROW EXECUTE FUNCTION amex_direct_no_cfdi_context_guard();

COMMIT;
