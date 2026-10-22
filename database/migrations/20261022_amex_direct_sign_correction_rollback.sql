-- Empty-layer rollback only. Applied corrections require compatible forward repair.
BEGIN;
-- Serialize the empty-layer decision with confirmations before inspecting it.
LOCK TABLE amex_direct_sign_revisions, amex_direct_sign_rows IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM amex_direct_sign_revisions) OR EXISTS(SELECT 1 FROM amex_direct_sign_rows) THEN RAISE EXCEPTION 'Sign correction evidence prevents rollback'; END IF;
END $$;
DROP TRIGGER ad_sign_no_recovery ON amex_direct_coverage_revisions;
DROP FUNCTION amex_direct_sign_recovery_guard();
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
CREATE OR REPLACE FUNCTION amex_direct_no_cfdi_source_hash(item_uuid uuid) RETURNS text LANGUAGE sql STABLE AS $$
 SELECT encode(sha256(convert_to(concat_ws('|',h.id::text,h.file_sha256,h.card_account_id::text,i.import_item_id::text,COALESCE(i.imported_expense_id::text,''),i.movement_kind,to_char(i.raw_signed_amount,'FM999999999999999990.00'),COALESCE(i.source_json->>'currency',''),COALESCE(i.source_json->>'date',''),COALESCE(i.source_json->>'description',''),COALESCE(i.recovery_id::text,''),COALESCE(i.recovery_revision_id::text,''),COALESCE(i.recovery_proof_hash,''),COALESCE(i.context_id::text,''),COALESCE(i.context_version::text,''),i.period_start::text,i.period_end::text),'UTF8')),'hex')
 FROM amex_direct_effective_sources i JOIN amex_statement_imports h ON h.id=i.import_id WHERE i.id=item_uuid
$$;
DROP TRIGGER ad_sign_revision ON amex_direct_sign_revisions;
DROP TRIGGER ad_sign_row ON amex_direct_sign_rows;
DROP TRIGGER ad_immutable_amex_direct_sign_revisions ON amex_direct_sign_revisions;
DROP TRIGGER ad_immutable_amex_direct_sign_rows ON amex_direct_sign_rows;
DROP TRIGGER ad_sign_seal_revision ON amex_direct_sign_revisions;
DROP TRIGGER ad_sign_seal_row ON amex_direct_sign_rows;
DROP TRIGGER ad_sign_expense ON expense_reports;
DROP FUNCTION amex_direct_sign_expense_guard();
DROP FUNCTION amex_direct_sign_guard();
DROP FUNCTION amex_direct_sign_seal();
DROP VIEW amex_direct_effective_sources RESTRICT;
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
DROP TABLE amex_direct_sign_rows;
DROP TABLE amex_direct_sign_revisions;
COMMIT;
