-- Approved AMEX direct historical-credit settlement. Owner-run, no backfill.
BEGIN;

CREATE TABLE amex_direct_completion_receipts (
	id UUID NOT NULL,
	import_id UUID NOT NULL,
	context_id UUID NOT NULL,
	context_version INTEGER NOT NULL,
	source_version INTEGER NOT NULL,
	source_hash VARCHAR(64) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	actor_id UUID NOT NULL,
	snapshot_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (import_id),
	FOREIGN KEY(import_id) REFERENCES amex_statement_imports (id) ON DELETE RESTRICT,
	FOREIGN KEY(context_id) REFERENCES amex_statement_direct_contexts (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;

CREATE TABLE amex_direct_prior_coverages (
	id UUID NOT NULL,
	import_id UUID NOT NULL,
	direct_item_id UUID NOT NULL,
	imported_expense_id UUID NOT NULL,
	consumption_id UUID NOT NULL,
	representation_id UUID NOT NULL,
	original_cut_id UUID,
	original_direct_cut_id UUID,
	accounting_poliza_id UUID NOT NULL,
	card_account_id UUID NOT NULL,
	context_id UUID NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	cut_id UUID,
	completion_receipt_id UUID,
	source_hash VARCHAR(64) NOT NULL,
	proof_json JSONB NOT NULL,
	actor_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CHECK (amount > 0 AND currency = 'MXN'),
	CHECK ((cut_id IS NULL) <> (completion_receipt_id IS NULL)),
	FOREIGN KEY(import_id) REFERENCES amex_statement_imports (id) ON DELETE RESTRICT,
	UNIQUE (direct_item_id),
	FOREIGN KEY(direct_item_id) REFERENCES amex_direct_items (id) ON DELETE RESTRICT,
	UNIQUE (imported_expense_id),
	FOREIGN KEY(imported_expense_id) REFERENCES expense_reports (id) ON DELETE RESTRICT,
	UNIQUE (consumption_id),
	FOREIGN KEY(consumption_id) REFERENCES amex_recognition_consumptions (id) ON DELETE RESTRICT,
	FOREIGN KEY(representation_id) REFERENCES amex_recognition_representations (id) ON DELETE RESTRICT,
	FOREIGN KEY(original_cut_id) REFERENCES amex_accounting_cuts (id) ON DELETE RESTRICT,
	FOREIGN KEY(original_direct_cut_id) REFERENCES amex_direct_accounting_cuts (id) ON DELETE RESTRICT,
	FOREIGN KEY(accounting_poliza_id) REFERENCES accounting_polizas (id) ON DELETE RESTRICT,
	FOREIGN KEY(card_account_id) REFERENCES amex_card_accounts (id) ON DELETE RESTRICT,
	FOREIGN KEY(context_id) REFERENCES amex_statement_direct_contexts (id) ON DELETE RESTRICT,
	FOREIGN KEY(cut_id) REFERENCES amex_direct_accounting_cuts (id) ON DELETE RESTRICT,
	FOREIGN KEY(completion_receipt_id) REFERENCES amex_direct_completion_receipts (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;

CREATE TABLE amex_expense_account_settlements (
	id UUID NOT NULL,
	import_id UUID NOT NULL,
	direct_item_id UUID NOT NULL,
	imported_expense_id UUID NOT NULL,
	context_id UUID NOT NULL,
	card_account_id UUID NOT NULL,
	cut_id UUID NOT NULL,
	accounting_poliza_id UUID NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	source_hash VARCHAR(64) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	actor_id UUID NOT NULL,
	reason VARCHAR(1000) NOT NULL,
	proof_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CHECK (amount > 0 AND currency = 'MXN'),
	FOREIGN KEY(import_id) REFERENCES amex_statement_imports (id) ON DELETE RESTRICT,
	UNIQUE (direct_item_id),
	FOREIGN KEY(direct_item_id) REFERENCES amex_direct_items (id) ON DELETE RESTRICT,
	UNIQUE (imported_expense_id),
	FOREIGN KEY(imported_expense_id) REFERENCES expense_reports (id) ON DELETE RESTRICT,
	FOREIGN KEY(context_id) REFERENCES amex_statement_direct_contexts (id) ON DELETE RESTRICT,
	FOREIGN KEY(card_account_id) REFERENCES amex_card_accounts (id) ON DELETE RESTRICT,
	FOREIGN KEY(cut_id) REFERENCES amex_direct_accounting_cuts (id) ON DELETE RESTRICT,
	FOREIGN KEY(accounting_poliza_id) REFERENCES accounting_polizas (id) ON DELETE RESTRICT,
	FOREIGN KEY(actor_id) REFERENCES empleados (id) ON DELETE RESTRICT
)

;

CREATE TABLE amex_expense_account_settlement_allocations (
	id UUID NOT NULL,
	settlement_id UUID NOT NULL,
	origin_poliza_id UUID NOT NULL,
	origin_line_id UUID NOT NULL,
	origin_expense_id UUID NOT NULL,
	origin_document_id UUID NOT NULL,
	origin_cuenta_gastos_id UUID NOT NULL,
	origin_account_id UUID NOT NULL,
	origin_account_code VARCHAR(100) NOT NULL,
	destination_debit_line_id UUID NOT NULL,
	destination_credit_line_id UUID NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	proof_json JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CHECK (amount > 0),
	FOREIGN KEY(settlement_id) REFERENCES amex_expense_account_settlements (id) ON DELETE RESTRICT,
	FOREIGN KEY(origin_poliza_id) REFERENCES accounting_polizas (id) ON DELETE RESTRICT,
	FOREIGN KEY(origin_line_id) REFERENCES accounting_poliza_lines (id) ON DELETE RESTRICT,
	FOREIGN KEY(origin_expense_id) REFERENCES expense_reports (id) ON DELETE RESTRICT,
	FOREIGN KEY(origin_document_id) REFERENCES documentos (id) ON DELETE RESTRICT,
	FOREIGN KEY(origin_cuenta_gastos_id) REFERENCES cuentas_de_gastos (id) ON DELETE RESTRICT,
	FOREIGN KEY(origin_account_id) REFERENCES cuentas_contables (id) ON DELETE RESTRICT,
	UNIQUE (destination_debit_line_id),
	FOREIGN KEY(destination_debit_line_id) REFERENCES accounting_poliza_lines (id) ON DELETE RESTRICT,
	UNIQUE (destination_credit_line_id),
	FOREIGN KEY(destination_credit_line_id) REFERENCES accounting_poliza_lines (id) ON DELETE RESTRICT
)

;

-- Canonical exact credit identity: metadata references are verified against the
-- actual expense/document/account relationships, never trusted on their own.
CREATE VIEW amex_expense_account_credit_sources AS
SELECT p.id origin_poliza_id,l.id origin_line_id,e.id origin_expense_id,
 d.id origin_document_id,c.id origin_cuenta_gastos_id,a.id origin_account_id,
 l.cuenta_codigo origin_account_code,l.haber::numeric credit_amount,
 l.haber::numeric-COALESCE((SELECT sum(x.amount) FROM amex_expense_account_settlement_allocations x WHERE x.origin_line_id=l.id),0) residual_amount,
 upper(cf.receptor_rfc) receiver_rfc,upper(e.currency) currency,
 cf.cfdi_uuid,cf.id origin_cfdi_id,
 encode(sha256(convert_to(cf.xml_raw,'UTF8')),'hex') origin_xml_hash,
 jsonb_build_object('uuid',cf.cfdi_uuid,'date',cf.fecha::text,'rfc_emisor',cf.emisor_rfc,
 'rfc_receptor',cf.receptor_rfc,'total',cf.total::text,'folio',cf.folio,
 'nombre_emisor',cf.emisor_nombre,'uso_cfdi',cf.receptor_uso_cfdi) origin_cfdi_json,l.concepto,coalesce(c.beneficiario_empleado_id,c.empleado_id) employee_id,
 emp.nombre employee_name,c.torneo_id tournament_id,e.proyecto project_code,
 extract(year FROM e.fecha)::integer edition_year,
 EXISTS(SELECT 1 FROM accounting_poliza_lines other JOIN accounting_polizas op ON op.id=other.poliza_id
 WHERE other.cuenta_contable_id=l.cuenta_contable_id AND other.debe>0
 AND other.raw_row_json->>'cuenta_gastos_id'=c.id::text
 AND NOT EXISTS(SELECT 1 FROM amex_expense_account_settlement_allocations ax WHERE ax.destination_debit_line_id=other.id)
 AND NOT EXISTS(SELECT 1 FROM amex_expense_account_settlements sh WHERE sh.accounting_poliza_id=other.poliza_id AND other.raw_row_json->>'expense_id'=sh.imported_expense_id::text AND other.raw_row_json->>'origin_line_id'=l.id::text)) ambiguous_liquidation,
 EXISTS(SELECT 1 FROM amex_recognition_representations rp JOIN amex_recognition_consumptions rc ON rc.id=rp.consumption_id WHERE rp.expense_id=e.id AND rc.accounting_poliza_id IS NOT NULL) prior_amex_recognition
FROM accounting_poliza_lines l JOIN accounting_polizas p ON p.id=l.poliza_id
JOIN expense_reports e ON e.id::text=l.raw_row_json->>'expense_id'
JOIN documentos d ON d.id::text=l.raw_row_json->>'documento_id'
JOIN cuentas_de_gastos c ON c.id=e.cuenta_gastos_id AND c.id::text=l.raw_row_json->>'cuenta_gastos_id'
JOIN cuentas_contables a ON a.id=l.cuenta_contable_id AND a.codigo=l.cuenta_codigo
JOIN empleados emp ON emp.id=coalesce(c.beneficiario_empleado_id,c.empleado_id)
JOIN cfdi_reports cf ON cf.id=e.cfdi_report_id
WHERE p.origen='deudores_comprobacion' AND d.tipo='INFORME'
 AND d.estado='aprobado' AND d.aprobado_en IS NOT NULL
 AND (d.cuenta_gastos_id=c.id OR EXISTS(SELECT 1 FROM documentos parent WHERE parent.id=d.informe_origen_id AND parent.cuenta_gastos_id=c.id))
 AND (e.informe_documento_id IS NULL OR e.informe_documento_id=d.id)
 AND e.estado_gasto='activo' AND coalesce(e.coi_estado,'')!='reversar'
 AND coalesce(l.debe,0)=0 AND l.haber>0 AND l.haber::numeric=round(l.haber::numeric,2)
 AND l.raw_row_json->>'movement'='haber_contrapartida'
 AND (a.codigo LIKE '1170-001-%' OR a.codigo LIKE '1170-002-%')
 AND a.activo IS TRUE AND cf.xml_raw IS NOT NULL AND upper(e.currency)='MXN';

CREATE FUNCTION amex_settlement_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE credit amex_expense_account_credit_sources%ROWTYPE; h amex_expense_account_settlements%ROWTYPE;
 item amex_direct_effective_sources%ROWTYPE; ctx amex_statement_direct_contexts%ROWTYPE;
 debit accounting_poliza_lines%ROWTYPE; credit_line accounting_poliza_lines%ROWTYPE;
 card amex_card_accounts%ROWTYPE; liability cuentas_contables%ROWTYPE;
BEGIN
 IF TG_OP!='INSERT' THEN RAISE EXCEPTION 'Settlement evidence is append-only' USING ERRCODE='23514'; END IF;
 SELECT * INTO h FROM amex_expense_account_settlements WHERE id=NEW.settlement_id;
 SELECT * INTO item FROM amex_direct_effective_sources WHERE id=h.direct_item_id;
 SELECT * INTO ctx FROM amex_statement_direct_contexts WHERE id=h.context_id;
 PERFORM 1 FROM accounting_poliza_lines WHERE id=NEW.origin_line_id FOR UPDATE;
 PERFORM pg_advisory_xact_lock(hashtextextended('amex-settlement-source-seal-v1',0));
 PERFORM pg_advisory_xact_lock(hashtextextended('amex-credit:'||NEW.origin_cuenta_gastos_id::text||':'||NEW.origin_account_id::text,0));
 SELECT * INTO credit FROM amex_expense_account_credit_sources WHERE origin_line_id=NEW.origin_line_id;
 IF credit.origin_line_id IS NULL OR credit.ambiguous_liquidation OR credit.prior_amex_recognition OR credit.receiver_rfc IS DISTINCT FROM ctx.receiver_rfc OR credit.currency IS DISTINCT FROM ctx.currency
 OR (credit.origin_poliza_id,credit.origin_expense_id,credit.origin_document_id,credit.origin_cuenta_gastos_id,credit.origin_account_id,credit.origin_account_code) IS DISTINCT FROM (NEW.origin_poliza_id,NEW.origin_expense_id,NEW.origin_document_id,NEW.origin_cuenta_gastos_id,NEW.origin_account_id,NEW.origin_account_code)
 OR NEW.amount>credit.residual_amount OR NEW.amount IS DISTINCT FROM h.amount THEN RAISE EXCEPTION 'Historical credit identity/residual not proven' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM amex_direct_events ev WHERE ev.item_id=item.id AND ev.source_version=item.source_version AND ev.action='expense_account_source_selected'
 AND ev.payload_json->>'coverage_kind'='settlement' AND ev.payload_json->>'origin_line_id'=NEW.origin_line_id::text AND ev.payload_json->>'origin_expense_id'=NEW.origin_expense_id::text
 AND ev.payload_json->>'origin_account_id'=NEW.origin_account_id::text AND ev.payload_json->>'origin_document_id'=NEW.origin_document_id::text AND ev.payload_json->>'origin_poliza_id'=NEW.origin_poliza_id::text
 AND ev.payload_json->>'origin_cuenta_gastos_id'=NEW.origin_cuenta_gastos_id::text AND trim(ev.payload_json->>'reason')!='') THEN RAISE EXCEPTION 'Settlement requires current explicit source decision' USING ERRCODE='23514'; END IF;
 IF h.proof_json->>'origin_xml_hash' IS DISTINCT FROM credit.origin_xml_hash OR h.proof_json->'origin_cfdi_json' IS DISTINCT FROM credit.origin_cfdi_json
 OR NOT EXISTS(SELECT 1 FROM amex_direct_cut_items ci WHERE ci.cut_id=h.cut_id AND ci.direct_item_id=item.id AND ci.snapshot_json->'cfdi'=credit.origin_cfdi_json) THEN RAISE EXCEPTION 'Settlement must freeze original fiscal evidence without new recognition' USING ERRCODE='23514'; END IF;
 SELECT * INTO debit FROM accounting_poliza_lines WHERE id=NEW.destination_debit_line_id;
 SELECT * INTO credit_line FROM accounting_poliza_lines WHERE id=NEW.destination_credit_line_id;
 SELECT * INTO card FROM amex_card_accounts WHERE id=h.card_account_id;
 SELECT * INTO liability FROM cuentas_contables WHERE id=card.liability_cuenta_contable_id;
 IF (debit.poliza_id,debit.cuenta_contable_id,debit.cuenta_codigo,debit.debe::numeric,coalesce(debit.haber,0)::numeric) IS DISTINCT FROM (h.accounting_poliza_id,NEW.origin_account_id,NEW.origin_account_code,NEW.amount,0::numeric)
 OR (credit_line.poliza_id,credit_line.cuenta_contable_id,credit_line.cuenta_codigo,coalesce(credit_line.debe,0)::numeric,credit_line.haber::numeric) IS DISTINCT FROM (h.accounting_poliza_id,liability.id,liability.codigo,0::numeric,NEW.amount)
 OR card.active IS NOT TRUE OR liability.activo IS NOT TRUE
 OR liability.codigo NOT IN ('2120-002-062','2120-002-063','2120-002-064','2120-002-065','2120-002-066','2120-002-067','2120-002-100')
 OR debit.raw_row_json->>'expense_id' IS DISTINCT FROM item.imported_expense_id::text
 OR debit.raw_row_json->>'origin_line_id' IS DISTINCT FROM NEW.origin_line_id::text
 OR debit.raw_row_json->>'cuenta_gastos_id' IS DISTINCT FROM NEW.origin_cuenta_gastos_id::text
 OR debit.raw_row_json->>'coverage_kind' IS DISTINCT FROM 'settlement'
 OR credit_line.raw_row_json->>'expense_id' IS DISTINCT FROM item.imported_expense_id::text
 OR credit_line.raw_row_json->>'coverage_kind' IS DISTINCT FROM 'settlement'
 THEN RAISE EXCEPTION 'Settlement requires exact historical debit and card credit' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION amex_settlement_header_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item amex_direct_effective_sources%ROWTYPE; h amex_statement_imports%ROWTYPE;
 cut amex_direct_accounting_cuts%ROWTYPE; ctx amex_statement_direct_contexts%ROWTYPE;
BEGIN
 SELECT * INTO h FROM amex_statement_imports WHERE id=NEW.import_id FOR UPDATE;
 SELECT * INTO item FROM amex_direct_effective_sources WHERE id=NEW.direct_item_id;
 SELECT * INTO cut FROM amex_direct_accounting_cuts WHERE id=NEW.cut_id;
 SELECT * INTO ctx FROM amex_statement_direct_contexts WHERE id=NEW.context_id;
 IF (item.import_id,item.imported_expense_id,item.context_id,h.card_account_id,abs(item.raw_signed_amount),ctx.currency,cut.import_id,cut.accounting_poliza_id)
 IS DISTINCT FROM (NEW.import_id,NEW.imported_expense_id,NEW.context_id,NEW.card_account_id,NEW.amount,NEW.currency,NEW.import_id,NEW.accounting_poliza_id)
 OR item.movement_kind!='debit' OR NEW.source_hash IS DISTINCT FROM item.source_hash
 OR EXISTS(SELECT 1 FROM amex_recognition_consumptions rc JOIN amex_recognition_representations rp ON rp.consumption_id=rc.id WHERE rp.expense_id=item.imported_expense_id)
 OR EXISTS(SELECT 1 FROM amex_direct_prior_coverages pc WHERE pc.direct_item_id=item.id)
 THEN RAISE EXCEPTION 'Settlement bank identity conflict' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION amex_settlement_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE sid uuid; s amex_expense_account_settlements%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='amex_expense_account_settlements' THEN sid:=NEW.id; ELSE sid:=NEW.settlement_id; END IF;
 SELECT * INTO s FROM amex_expense_account_settlements WHERE id=sid;
 IF s.amount IS DISTINCT FROM (SELECT sum(amount) FROM amex_expense_account_settlement_allocations WHERE settlement_id=s.id)
 OR NOT EXISTS(SELECT 1 FROM amex_direct_cut_items ci WHERE ci.cut_id=s.cut_id AND ci.direct_item_id=s.direct_item_id AND ci.coverage_kind='settlement' AND ci.accounting_poliza_id=s.accounting_poliza_id AND ci.consumption_id IS NULL)
 OR (SELECT count(*) FROM accounting_poliza_lines WHERE poliza_id=s.accounting_poliza_id AND raw_row_json->>'expense_id'=s.imported_expense_id::text)<>2
 THEN RAISE EXCEPTION 'Settlement requires complete exact receipt' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

CREATE VIEW amex_direct_prior_sources AS
SELECT rc.id consumption_id,rp.id representation_id,ac.id original_cut_id,
 rc.accounting_poliza_id origin_poliza_id,l.id origin_line_id,e.id origin_expense_id,
 d.id origin_document_id,c.id origin_cuenta_gastos_id,l.cuenta_contable_id origin_account_id,
 l.cuenta_codigo origin_account_code,rc.amount credit_amount,rc.amount residual_amount,
 upper(cf.receptor_rfc) receiver_rfc,rc.currency,cf.cfdi_uuid,cf.id origin_cfdi_id,
 encode(sha256(convert_to(cf.xml_raw,'UTF8')),'hex') origin_xml_hash,
 jsonb_build_object('uuid',cf.cfdi_uuid,'date',cf.fecha::text,'rfc_emisor',cf.emisor_rfc,
 'rfc_receptor',cf.receptor_rfc,'total',cf.total::text,'folio',cf.folio,
 'nombre_emisor',cf.emisor_nombre,'uso_cfdi',cf.receptor_uso_cfdi) origin_cfdi_json,l.concepto,
 rc.card_account_id,rc.economic_date,c.torneo_id tournament_id,e.proyecto project_code,
 extract(year FROM e.fecha)::integer edition_year,coalesce(c.beneficiario_empleado_id,c.empleado_id) employee_id,
 emp.nombre employee_name,false ambiguous_liquidation,false prior_amex_recognition
FROM amex_recognition_consumptions rc
JOIN amex_recognition_representations rp ON rp.consumption_id=rc.id AND rp.role='report'
JOIN amex_accounting_cuts ac ON ac.accounting_poliza_id=rc.accounting_poliza_id AND ac.snapshot_json->'consumption_ids' ? rc.id::text
JOIN expense_reports e ON e.id=rc.source_report_expense_id AND e.id=rp.expense_id
JOIN documentos d ON d.id=ac.informe_id
JOIN cuentas_de_gastos c ON c.id=e.cuenta_gastos_id
JOIN empleados emp ON emp.id=coalesce(c.beneficiario_empleado_id,c.empleado_id)
JOIN cfdi_reports cf ON cf.id=e.cfdi_report_id
JOIN accounting_poliza_lines l ON l.poliza_id=rc.accounting_poliza_id AND l.cuenta_contable_id=rc.liability_account_id AND l.cuenta_codigo=rc.liability_code AND l.haber::numeric=rc.amount AND coalesce(l.debe,0)=0 AND l.raw_row_json->>'expense_id'=e.id::text
WHERE rc.source_kind='report' AND rc.binding_mode='report' AND rc.currency='MXN'
 AND e.estado_gasto='activo' AND coalesce(e.coi_estado,'')!='reversar'
 AND e.pagado_con_amex_empresa IS TRUE AND e.ultimos_4_digitos=(SELECT last4 FROM amex_card_accounts WHERE id=rc.card_account_id)
 AND d.estado='aprobado' AND d.aprobado_en IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM amex_direct_prior_coverages pc WHERE pc.consumption_id=rc.id)
 AND NOT EXISTS(SELECT 1 FROM amex_report_bank_associations ba WHERE ba.report_consumption_id=rc.id);

CREATE FUNCTION amex_direct_prior_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item amex_direct_effective_sources%ROWTYPE; h amex_statement_imports%ROWTYPE;
 c amex_recognition_consumptions%ROWTYPE; r amex_recognition_representations%ROWTYPE;
 ctx amex_statement_direct_contexts%ROWTYPE; source expense_reports%ROWTYPE;
BEGIN
 SELECT * INTO h FROM amex_statement_imports WHERE id=NEW.import_id FOR UPDATE;
 SELECT * INTO item FROM amex_direct_effective_sources WHERE id=NEW.direct_item_id;
 SELECT * INTO c FROM amex_recognition_consumptions WHERE id=NEW.consumption_id FOR UPDATE;
 SELECT * INTO r FROM amex_recognition_representations WHERE id=NEW.representation_id;
 SELECT * INTO ctx FROM amex_statement_direct_contexts WHERE id=NEW.context_id;
 SELECT * INTO source FROM expense_reports WHERE id=r.expense_id;
 IF h.workflow_mode IS DISTINCT FROM 'direct' OR item.movement_kind IS DISTINCT FROM 'debit'
 OR (item.import_id,item.imported_expense_id,item.context_id,h.card_account_id,abs(item.raw_signed_amount),item.source_hash)
 IS DISTINCT FROM (NEW.import_id,NEW.imported_expense_id,NEW.context_id,NEW.card_account_id,NEW.amount,NEW.source_hash)
 OR (c.id,c.accounting_poliza_id,c.card_account_id,c.amount,c.currency)
 IS DISTINCT FROM (r.consumption_id,NEW.accounting_poliza_id,NEW.card_account_id,NEW.amount,NEW.currency)
 OR c.binding_mode='report_bridge' OR c.accounting_poliza_id IS NULL
 OR (NEW.original_cut_id IS NULL)=(NEW.original_direct_cut_id IS NULL)
 OR source.estado_gasto IS DISTINCT FROM 'activo' OR coalesce(source.coi_estado,'')='reversar'
 OR (SELECT upper(receptor_rfc) FROM cfdi_reports WHERE id=source.cfdi_report_id) IS DISTINCT FROM ctx.receiver_rfc
 OR EXISTS(SELECT 1 FROM amex_expense_account_settlements es WHERE es.direct_item_id=item.id)
 THEN RAISE EXCEPTION 'Prior coverage requires exact canonical bank and fiscal identity' USING ERRCODE='23514'; END IF;
 IF NEW.original_cut_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM amex_accounting_cuts ac WHERE ac.id=NEW.original_cut_id AND ac.accounting_poliza_id=c.accounting_poliza_id AND ac.snapshot_json->'consumption_ids' ? c.id::text) THEN RAISE EXCEPTION 'Prior report cut missing' USING ERRCODE='23514'; END IF;
 IF NEW.original_direct_cut_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_accounting_cuts dc ON dc.id=ci.cut_id WHERE dc.id=NEW.original_direct_cut_id AND dc.accounting_poliza_id=c.accounting_poliza_id AND ci.consumption_id=c.id AND ci.coverage_kind='new') THEN RAISE EXCEPTION 'Prior direct cut missing' USING ERRCODE='23514'; END IF;
 -- A pre-existing representation is sufficient; otherwise require the exact,
 -- audited explicit selection tied to the genuine report liability ledger line.
 IF r.expense_id IS DISTINCT FROM item.imported_expense_id AND NOT EXISTS(
 SELECT 1 FROM amex_direct_events ev JOIN accounting_poliza_lines l ON l.id::text=ev.payload_json->>'origin_line_id'
 WHERE ev.item_id=item.id AND ev.action='expense_account_source_selected' AND ev.source_version=item.source_version
 AND ev.payload_json->>'coverage_kind'='prior' AND ev.payload_json->>'origin_expense_id'=r.expense_id::text
 AND ev.payload_json->>'origin_poliza_id'=c.accounting_poliza_id::text
 AND l.poliza_id=c.accounting_poliza_id AND l.cuenta_contable_id=c.liability_account_id
 AND l.cuenta_codigo=c.liability_code AND l.haber::numeric=c.amount AND coalesce(l.debe,0)=0
 AND l.raw_row_json->>'expense_id'=r.expense_id::text
 ) THEN RAISE EXCEPTION 'Prior report source requires explicit audited bank selection' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION amex_direct_completion_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE receipt_id uuid; receipt amex_direct_completion_receipts%ROWTYPE;
 h amex_statement_imports%ROWTYPE; state amex_statement_direct_state%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='amex_direct_completion_receipts' THEN receipt_id:=NEW.id;
 ELSE receipt_id:=NEW.completion_receipt_id; IF receipt_id IS NULL THEN
 IF NOT EXISTS(SELECT 1 FROM amex_direct_cut_items ci WHERE ci.cut_id=NEW.cut_id AND ci.direct_item_id=NEW.direct_item_id AND ci.coverage_kind='prior' AND ci.consumption_id=NEW.consumption_id AND ci.accounting_poliza_id=NEW.accounting_poliza_id) THEN RAISE EXCEPTION 'Prior mixed membership missing' USING ERRCODE='23514'; END IF;
 RETURN NULL; END IF; END IF;
 SELECT * INTO receipt FROM amex_direct_completion_receipts WHERE id=receipt_id;
 SELECT * INTO h FROM amex_statement_imports WHERE id=receipt.import_id FOR UPDATE;
 SELECT * INTO state FROM amex_statement_direct_state WHERE import_id=h.id;
 IF h.workflow_mode IS DISTINCT FROM 'direct' OR (state.context_id,state.context_version,state.source_version) IS DISTINCT FROM (receipt.context_id,receipt.context_version,receipt.source_version)
 OR EXISTS(SELECT 1 FROM amex_direct_accounting_cuts WHERE import_id=h.id)
 OR NOT EXISTS(SELECT 1 FROM empleados WHERE id=receipt.actor_id AND activo IS TRUE AND rol IN('finanzas','admin','superadmin','super_admin'))
 OR NOT EXISTS(SELECT 1 FROM amex_direct_prior_coverages WHERE completion_receipt_id=receipt.id)
 OR (SELECT count(*) FROM amex_direct_effective_sources WHERE import_id=h.id)<>h.total_rows
 OR jsonb_array_length(receipt.snapshot_json->'partidas') IS DISTINCT FROM h.total_rows
 OR EXISTS(SELECT 1 FROM amex_direct_effective_sources es WHERE es.import_id=h.id AND NOT (
 (es.movement_kind='credit' AND es.credit_excluded IS TRUE) OR
 (es.movement_kind='debit' AND EXISTS(SELECT 1 FROM amex_direct_prior_coverages pc WHERE pc.completion_receipt_id=receipt.id AND pc.direct_item_id=es.id AND pc.import_id=h.id))))
 OR EXISTS(SELECT 1 FROM amex_direct_prior_coverages pc WHERE pc.completion_receipt_id=receipt.id AND (pc.import_id IS DISTINCT FROM h.id OR pc.context_id IS DISTINCT FROM receipt.context_id))
 OR EXISTS(SELECT 1 FROM amex_direct_effective_sources es WHERE es.import_id=h.id AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(receipt.snapshot_json->'partidas') f WHERE f->>'direct_item_id'=es.id::text AND f->>'source_hash'=es.source_hash AND f->'source'=es.source_json AND ((es.movement_kind='credit' AND f->>'coverage_kind'='credit_exclusion') OR (es.movement_kind='debit' AND f->>'coverage_kind'='prior' AND EXISTS(SELECT 1 FROM amex_direct_prior_coverages pc WHERE pc.completion_receipt_id=receipt.id AND pc.direct_item_id=es.id AND f->>'consumption_id'=pc.consumption_id::text AND f->>'accounting_poliza_id'=pc.accounting_poliza_id::text)))))
 THEN RAISE EXCEPTION 'Completion requires whole prior-only statement and no journal' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION amex_settlement_frozen_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE frozen boolean:=false; relevant boolean:=false; cid text; aid uuid;
BEGIN
 -- Seal only genuine candidate origins, including candidates not yet allocated.
 -- Do not serialize unrelated ledger/employee/document traffic.
 IF TG_TABLE_NAME='accounting_poliza_lines' AND TG_OP='INSERT' THEN
 relevant:=NEW.raw_row_json->>'coverage_kind'='settlement' OR (NEW.debe>0 AND EXISTS(
 SELECT 1 FROM accounting_poliza_lines original JOIN accounting_polizas op ON op.id=original.poliza_id
 WHERE op.origen='deudores_comprobacion' AND original.haber>0
 AND original.cuenta_contable_id=NEW.cuenta_contable_id
 AND original.raw_row_json->>'cuenta_gastos_id'=NEW.raw_row_json->>'cuenta_gastos_id'));
 ELSIF TG_TABLE_NAME='accounting_poliza_lines' THEN
 SELECT EXISTS(SELECT 1 FROM accounting_polizas op WHERE op.id=OLD.poliza_id AND op.origen='deudores_comprobacion') INTO relevant;
 ELSIF TG_TABLE_NAME='accounting_polizas' THEN relevant:=OLD.origen='deudores_comprobacion';
 ELSIF TG_TABLE_NAME='expense_reports' THEN
 SELECT EXISTS(SELECT 1 FROM accounting_poliza_lines original JOIN accounting_polizas op ON op.id=original.poliza_id WHERE op.origen='deudores_comprobacion' AND original.haber>0 AND original.raw_row_json->>'expense_id'=OLD.id::text) INTO relevant;
 ELSIF TG_TABLE_NAME='cfdi_reports' THEN
 SELECT EXISTS(SELECT 1 FROM accounting_poliza_lines original JOIN accounting_polizas op ON op.id=original.poliza_id JOIN expense_reports er ON er.id::text=original.raw_row_json->>'expense_id' WHERE op.origen='deudores_comprobacion' AND original.haber>0 AND er.cfdi_report_id=OLD.id) INTO relevant;
 ELSIF TG_TABLE_NAME='documentos' THEN
 SELECT EXISTS(SELECT 1 FROM accounting_poliza_lines original JOIN accounting_polizas op ON op.id=original.poliza_id WHERE op.origen='deudores_comprobacion' AND original.haber>0 AND original.raw_row_json->>'documento_id'=OLD.id::text) INTO relevant;
 ELSIF TG_TABLE_NAME='cuentas_de_gastos' THEN
 SELECT EXISTS(SELECT 1 FROM accounting_poliza_lines original JOIN accounting_polizas op ON op.id=original.poliza_id WHERE op.origen='deudores_comprobacion' AND original.haber>0 AND original.raw_row_json->>'cuenta_gastos_id'=OLD.id::text) INTO relevant;
 END IF;
 IF relevant THEN PERFORM pg_advisory_xact_lock(hashtextextended('amex-settlement-source-seal-v1',0)); END IF;
 IF TG_TABLE_NAME IN('amex_direct_items','amex_statement_direct_state','amex_statement_direct_contexts','amex_direct_coverage_revisions') THEN
 IF TG_OP='DELETE' THEN cid:=OLD.import_id::text; ELSE cid:=NEW.import_id::text; END IF;
 SELECT EXISTS(SELECT 1 FROM amex_direct_completion_receipts WHERE import_id::text=cid) INTO frozen;
 ELSIF TG_TABLE_NAME='expense_reports' THEN
 SELECT EXISTS(SELECT 1 FROM amex_expense_account_settlement_allocations WHERE origin_expense_id=OLD.id)
 OR EXISTS(SELECT 1 FROM amex_direct_prior_coverages WHERE imported_expense_id=OLD.id OR representation_id IN(SELECT id FROM amex_recognition_representations WHERE expense_id=OLD.id)) INTO frozen;
 ELSIF TG_TABLE_NAME='cfdi_reports' THEN
 SELECT EXISTS(SELECT 1 FROM amex_expense_account_settlement_allocations ax JOIN expense_reports er ON er.id=ax.origin_expense_id WHERE er.cfdi_report_id=OLD.id) INTO frozen;
 ELSIF TG_TABLE_NAME='cuentas_de_gastos' THEN
 SELECT EXISTS(SELECT 1 FROM amex_expense_account_settlement_allocations WHERE origin_cuenta_gastos_id=OLD.id) INTO frozen;
 ELSIF TG_TABLE_NAME='documentos' THEN
 SELECT EXISTS(SELECT 1 FROM amex_expense_account_settlement_allocations WHERE origin_document_id=OLD.id) INTO frozen;
 ELSIF TG_TABLE_NAME='accounting_polizas' THEN
 SELECT EXISTS(SELECT 1 FROM amex_expense_account_settlement_allocations WHERE origin_poliza_id=OLD.id) INTO frozen;
 ELSIF TG_TABLE_NAME='accounting_poliza_lines' THEN
 IF TG_OP='INSERT' THEN
 cid:=NEW.raw_row_json->>'cuenta_gastos_id'; aid:=NEW.cuenta_contable_id;
 IF cid IS NOT NULL AND aid IS NOT NULL AND relevant THEN
 PERFORM pg_advisory_xact_lock(hashtextextended('amex-credit:'||cid||':'||aid::text,0));
 IF NEW.debe>0 AND EXISTS(SELECT 1 FROM amex_expense_account_settlement_allocations ax WHERE ax.origin_cuenta_gastos_id::text=cid AND ax.origin_account_id=aid)
 AND coalesce(NEW.raw_row_json->>'coverage_kind','')!='settlement' THEN RAISE EXCEPTION 'Settled historical credit cannot acquire an unattributed liquidation' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
 ELSE SELECT EXISTS(SELECT 1 FROM amex_expense_account_settlement_allocations WHERE origin_line_id=OLD.id) INTO frozen; END IF;
 END IF;
 IF frozen AND (TG_OP IN('INSERT','DELETE') OR NEW IS DISTINCT FROM OLD) THEN RAISE EXCEPTION 'Completed AMEX source is frozen' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
END $$;
CREATE TRIGGER aesc_allocation BEFORE INSERT ON amex_expense_account_settlement_allocations FOR EACH ROW EXECUTE FUNCTION amex_settlement_source_guard();
CREATE TRIGGER aesc_header BEFORE INSERT ON amex_expense_account_settlements FOR EACH ROW EXECUTE FUNCTION amex_settlement_header_guard();
CREATE TRIGGER aesc_prior BEFORE INSERT ON amex_direct_prior_coverages FOR EACH ROW EXECUTE FUNCTION amex_direct_prior_guard();
CREATE CONSTRAINT TRIGGER aesc_settlement_seal AFTER INSERT ON amex_expense_account_settlements DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_settlement_check();
CREATE CONSTRAINT TRIGGER aesc_allocation_seal AFTER INSERT ON amex_expense_account_settlement_allocations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_settlement_check();
CREATE CONSTRAINT TRIGGER aesc_completion_seal AFTER INSERT ON amex_direct_completion_receipts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_completion_check();
CREATE CONSTRAINT TRIGGER aesc_prior_seal AFTER INSERT ON amex_direct_prior_coverages DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_completion_check();
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['amex_direct_completion_receipts','amex_direct_prior_coverages','amex_expense_account_settlements','amex_expense_account_settlement_allocations'] LOOP
 EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION amex_direct_immutable()','aesc_immutable_'||name,name);
 END LOOP;
 FOREACH name IN ARRAY ARRAY['amex_direct_items','amex_statement_direct_state','amex_statement_direct_contexts','amex_direct_coverage_revisions'] LOOP
 EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION amex_settlement_frozen_guard()','aesc_frozen_'||name,name);
 END LOOP;
 FOREACH name IN ARRAY ARRAY['expense_reports','cfdi_reports','cuentas_de_gastos','documentos','accounting_polizas','accounting_poliza_lines'] LOOP
 EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION amex_settlement_frozen_guard()','aesc_origin_'||name,name);
 END LOOP;
END $$;
CREATE TRIGGER aesc_debtor_new_line BEFORE INSERT ON accounting_poliza_lines FOR EACH ROW EXECUTE FUNCTION amex_settlement_frozen_guard();
CREATE OR REPLACE FUNCTION amex_direct_cut_item_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item amex_direct_effective_sources%ROWTYPE; cut amex_direct_accounting_cuts%ROWTYPE; header amex_statement_imports%ROWTYPE;
BEGIN
 SELECT * INTO cut FROM amex_direct_accounting_cuts WHERE id=NEW.cut_id;
 SELECT * INTO header FROM amex_statement_imports WHERE id=cut.import_id FOR UPDATE;
 SELECT * INTO item FROM amex_direct_effective_sources WHERE id=NEW.direct_item_id;
 IF (item.import_id,item.import_item_id,item.imported_expense_id,item.source_hash) IS DISTINCT FROM (cut.import_id,NEW.import_item_id,NEW.imported_expense_id,NEW.source_hash) OR (SELECT count(*) FROM amex_direct_cut_items WHERE cut_id=NEW.cut_id)>=header.total_rows THEN RAISE EXCEPTION 'Direct cut membership is invalid or sealed' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind NOT IN('new','settlement','prior','credit_exclusion') OR (item.movement_kind='credit') IS DISTINCT FROM (NEW.coverage_kind='credit_exclusion') OR item.movement_kind NOT IN('debit','credit') THEN RAISE EXCEPTION 'Direct cut requires typed economic coverage' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind='credit_exclusion' THEN
 IF item.credit_excluded IS NOT TRUE OR NEW.consumption_id IS NOT NULL OR NEW.accounting_poliza_id IS NOT NULL THEN RAISE EXCEPTION 'Credit exclusion cannot be posted' USING ERRCODE='23514'; END IF;
 ELSIF NEW.coverage_kind='settlement' THEN
 IF NEW.consumption_id IS NOT NULL OR NEW.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id THEN RAISE EXCEPTION 'Settlement has own journal proof, not fiscal consumption' USING ERRCODE='23514'; END IF;
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
 IF EXISTS(SELECT 1 FROM amex_direct_completion_receipts WHERE import_id=NEW.import_id) THEN RAISE EXCEPTION 'Completed statement cannot create cut' USING ERRCODE='23514'; END IF;
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
 IF (SELECT count(*) FROM amex_direct_cut_items WHERE cut_id=cut.id)<>header.total_rows OR NOT EXISTS(SELECT 1 FROM amex_direct_cut_items WHERE cut_id=cut.id AND coverage_kind IN('new','settlement')) THEN RAISE EXCEPTION 'Direct cut must include complete membership and new economic activity' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_coverage_revisions cr WHERE cr.import_id=cut.import_id AND (cut.snapshot_json->'recovery_revision_ids' IS DISTINCT FROM jsonb_build_array(cr.id::text) OR cut.snapshot_json->'original_period' IS DISTINCT FROM jsonb_build_object('start',cr.original_period_start::text,'end',cr.original_period_end::text) OR cut.snapshot_json->'effective_period' IS DISTINCT FROM jsonb_build_object('start',cr.period_start::text,'end',cr.period_end::text))) THEN RAISE EXCEPTION 'Direct cut recovery coverage proof mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources es ON es.id=ci.direct_item_id WHERE ci.cut_id=cut.id AND es.recovery_id IS NOT NULL AND (ci.snapshot_json->>'recovery_revision_id' IS DISTINCT FROM es.recovery_revision_id::text OR ci.snapshot_json->>'recovery_proof_hash' IS DISTINCT FROM es.recovery_proof_hash OR ci.snapshot_json->'source' IS DISTINCT FROM es.source_json)) THEN RAISE EXCEPTION 'Direct cut recovered source proof mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id WHERE ci.cut_id=cut.id AND (i.context_id IS DISTINCT FROM cut.context_id OR i.context_version IS DISTINCT FROM cut.context_version OR i.movement_kind IN('unknown','skipped') OR (ci.coverage_kind='new' AND (i.cfdi_id IS NULL OR i.budget_assignment_id IS NULL OR i.status_receipt_id IS NULL)))) THEN RAISE EXCEPTION 'Direct cut contains unresolved fiscal/budget source' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (c.binding_mode!='direct' OR c.imported_expense_id IS DISTINCT FROM i.imported_expense_id OR c.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id OR ci.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id OR c.amount IS DISTINCT FROM abs(i.raw_signed_amount))) THEN RAISE EXCEPTION 'Direct cut economic journal mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci WHERE ci.cut_id=cut.id AND ci.coverage_kind='prior' AND NOT EXISTS(SELECT 1 FROM amex_direct_prior_coverages pc WHERE pc.cut_id=cut.id AND pc.direct_item_id=ci.direct_item_id AND pc.consumption_id=ci.consumption_id AND pc.accounting_poliza_id=ci.accounting_poliza_id)) THEN RAISE EXCEPTION 'Prior coverage requires canonical association receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci WHERE ci.cut_id=cut.id AND ci.coverage_kind='settlement' AND NOT EXISTS(SELECT 1 FROM amex_expense_account_settlements es WHERE es.cut_id=cut.id AND es.direct_item_id=ci.direct_item_id AND es.accounting_poliza_id=ci.accounting_poliza_id)) THEN RAISE EXCEPTION 'Settlement coverage requires allocation receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_cfdi_status_receipts sr ON sr.id=i.status_receipt_id JOIN cfdi_reports cf ON cf.id=i.cfdi_id JOIN amex_statement_direct_contexts ctx ON ctx.id=i.context_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (lower(sr.observed_status)!='vigente' OR sr.observed_at<clock_timestamp()-INTERVAL '24 hours' OR sr.observed_at>clock_timestamp() OR sr.receiver_rfc IS DISTINCT FROM ctx.receiver_rfc OR cf.receptor_rfc IS DISTINCT FROM ctx.receiver_rfc OR upper(cf.moneda) IS DISTINCT FROM ctx.currency OR upper(cf.cfdi_uuid) IS DISTINCT FROM upper(sr.fiscal_uuid) OR upper(cf.emisor_rfc) IS DISTINCT FROM upper(sr.issuer_rfc) OR round(cf.total::numeric,2) IS DISTINCT FROM sr.amount OR cf.origen IS DISTINCT FROM 'sat' OR cf.xml_raw IS NULL OR sr.xml_hash IS DISTINCT FROM encode(sha256(convert_to(cf.xml_raw,'UTF8')),'hex'))) THEN RAISE EXCEPTION 'Direct cut requires current exact fiscal status receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN expense_reports e ON e.id=i.imported_expense_id LEFT JOIN budget_versions v ON v.id=i.budget_version_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (i.edition_year IS DISTINCT FROM extract(year FROM e.fecha)::integer OR i.edition_year IS DISTINCT FROM extract(year FROM cut.accounting_date)::integer OR v.edition_year IS DISTINCT FROM i.edition_year)) THEN RAISE EXCEPTION 'Direct fiscal and accounting dates require budget edition' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id JOIN amex_card_accounts card ON card.id=c.card_account_id JOIN cuentas_contables liability ON liability.id=c.liability_account_id JOIN budget_concepts bc ON bc.id=i.budget_concept_id JOIN expense_reports expense ON expense.id=i.imported_expense_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND (card.active IS DISTINCT FROM TRUE OR liability.activo IS DISTINCT FROM TRUE OR card.liability_cuenta_contable_id IS DISTINCT FROM c.liability_account_id OR liability.codigo IS DISTINCT FROM c.liability_code OR bc.active IS DISTINCT FROM TRUE OR bc.budget_direction IS DISTINCT FROM 'expense' OR bc.cuenta_contable_id IS NULL OR expense.cuenta_contable_id IS DISTINCT FROM bc.cuenta_contable_id OR NOT EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=bc.cuenta_contable_id AND l.debe>0))) THEN RAISE EXCEPTION 'Direct cut account mapping changed or absent' USING ERRCODE='23514'; END IF;
 -- Card credits are bank net. Additional credits require exact canonical
 -- withholding evidence, frozen per source; an arbitrary balancing credit fails.
 IF EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.haber>0 AND NOT EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND c.liability_account_id=l.cuenta_contable_id) AND NOT EXISTS(SELECT 1 FROM amex_expense_account_settlements es JOIN amex_card_accounts ca ON ca.id=es.card_account_id WHERE es.cut_id=cut.id AND ca.liability_cuenta_contable_id=l.cuenta_contable_id) AND NOT EXISTS(
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
 IF EXISTS(SELECT 1 FROM (SELECT account,sum(amount) amount FROM (SELECT c.liability_account_id account,c.amount FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' UNION ALL SELECT ca.liability_cuenta_contable_id,es.amount FROM amex_expense_account_settlements es JOIN amex_card_accounts ca ON ca.id=es.card_account_id WHERE es.cut_id=cut.id) amounts GROUP BY account) expected WHERE expected.amount IS DISTINCT FROM (SELECT sum(l.haber::numeric) FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=expected.account)) THEN RAISE EXCEPTION 'Direct card liability must equal exact bank net' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN expense_reports own_expense ON own_expense.id=i.imported_expense_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new' AND EXISTS(SELECT 1 FROM expense_reports other_expense WHERE other_expense.cfdi_report_id=i.cfdi_id AND other_expense.estado_gasto='activo' AND other_expense.id!=i.imported_expense_id AND (own_expense.cfdi_compartido_confirmado IS DISTINCT FROM TRUE OR other_expense.cfdi_compartido_confirmado IS DISTINCT FROM TRUE))) THEN RAISE EXCEPTION 'Direct fiscal identity has incompatible global usage' USING ERRCODE='23514'; END IF;
 SELECT sum(c.amount) INTO new_total FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='new';
 new_total:=coalesce(new_total,0)+coalesce((SELECT sum(amount) FROM amex_expense_account_settlements WHERE cut_id=cut.id),0);
 SELECT sum(COALESCE(debe,0)::numeric),sum(COALESCE(haber,0)::numeric) INTO debit_total,credit_total FROM accounting_poliza_lines WHERE poliza_id=cut.accounting_poliza_id;
 IF new_total IS NULL OR new_total<=0 OR debit_total IS NULL OR debit_total<=0 OR round(debit_total,2) IS DISTINCT FROM round(credit_total,2) OR EXISTS(SELECT 1 FROM accounting_poliza_lines WHERE poliza_id=cut.accounting_poliza_id AND (COALESCE(debe,0)<0 OR COALESCE(haber,0)<0 OR (COALESCE(debe,0)>0 AND COALESCE(haber,0)>0))) THEN RAISE EXCEPTION 'Direct journal must balance exact positive economic recognition' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION amex_settlement_liquidation_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.debe>0 AND EXISTS(SELECT 1 FROM amex_expense_account_settlement_allocations ax WHERE ax.origin_cuenta_gastos_id::text=NEW.raw_row_json->>'cuenta_gastos_id' AND ax.origin_account_id=NEW.cuenta_contable_id)
 AND NOT EXISTS(SELECT 1 FROM amex_expense_account_settlement_allocations ax WHERE ax.destination_debit_line_id=NEW.id) THEN RAISE EXCEPTION 'Settled credit requires attributed liquidation receipt' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER aesc_liquidation_seal AFTER INSERT ON accounting_poliza_lines DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_settlement_liquidation_check();
COMMIT;
