-- Owner-run account destination evidence. No backfill or financial mutation.
BEGIN;
CREATE TABLE amex_direct_account_treatment_policies (
 id uuid PRIMARY KEY,
 config_hash varchar(64) NOT NULL CHECK(length(config_hash)=64),
 base_config_hash varchar(64) NOT NULL CHECK(length(base_config_hash)=64),
 config_text text NOT NULL,
 base_config_text text NOT NULL,
 revision integer NOT NULL CHECK(revision>0),
 previous_policy_id uuid UNIQUE REFERENCES amex_direct_account_treatment_policies(id) ON DELETE RESTRICT,
 actor_id uuid NOT NULL REFERENCES empleados(id) ON DELETE RESTRICT,
 reason text NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
 request_id varchar(160) NOT NULL UNIQUE CHECK(length(trim(request_id)) BETWEEN 1 AND 160),
 payload_hash varchar(64) NOT NULL CHECK(length(payload_hash)=64),
 created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(base_config_hash,revision)
);
-- Provisioning requires explicit owner DDL in one transaction; runtime DML is
-- never allowed to self-certify a new authorization policy.
CREATE TRIGGER ad_account_treatment_policy_dml BEFORE INSERT OR UPDATE OR DELETE ON amex_direct_account_treatment_policies FOR EACH ROW EXECUTE FUNCTION amex_direct_immutable();
CREATE TRIGGER ad_account_treatment_policy_truncate BEFORE TRUNCATE ON amex_direct_account_treatment_policies FOR EACH STATEMENT EXECUTE FUNCTION amex_direct_immutable();
CREATE TABLE amex_direct_account_treatment_decisions (
 policy_id uuid NOT NULL REFERENCES amex_direct_account_treatment_policies(id) ON DELETE RESTRICT,
 id uuid PRIMARY KEY,
 item_id uuid NOT NULL REFERENCES amex_direct_items(id) ON DELETE RESTRICT,
 import_id uuid NOT NULL REFERENCES amex_statement_imports(id) ON DELETE RESTRICT,
 imported_expense_id uuid NOT NULL REFERENCES expense_reports(id) ON DELETE RESTRICT,
 context_id uuid NOT NULL REFERENCES amex_statement_direct_contexts(id) ON DELETE RESTRICT,
 context_version integer NOT NULL CHECK(context_version>0),
 revision integer NOT NULL CHECK(revision>0),
 action varchar(10) NOT NULL CHECK(action IN('set','revoke')),
 previous_decision_id uuid UNIQUE REFERENCES amex_direct_account_treatment_decisions(id) ON DELETE RESTRICT,
 treatment varchar(30) NOT NULL CHECK(treatment IN('project_expense','benefit_expense','partner_receivable')),
 debtor_account_id uuid REFERENCES cuentas_contables(id) ON DELETE RESTRICT,
 account_snapshot_json jsonb NOT NULL,
 authorization_json jsonb NOT NULL,
 reason text NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
 actor_id uuid NOT NULL REFERENCES empleados(id) ON DELETE RESTRICT,
 request_id varchar(160) NOT NULL CHECK(length(trim(request_id)) BETWEEN 1 AND 160),
 idempotency_key varchar(64) NOT NULL CHECK(length(idempotency_key)=64),
 payload_hash varchar(64) NOT NULL CHECK(length(payload_hash)=64),
 economic_source_hash varchar(64) NOT NULL CHECK(length(economic_source_hash)=64),
 source_version integer NOT NULL CHECK(source_version>0),
 source_hash varchar(64) NOT NULL CHECK(length(source_hash)=64),
 versions_json jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(item_id,revision), UNIQUE(item_id,idempotency_key),
 CHECK((treatment='partner_receivable')=(debtor_account_id IS NOT NULL))
);
ALTER TABLE amex_direct_items ADD COLUMN account_treatment_decision_id uuid REFERENCES amex_direct_account_treatment_decisions(id) ON DELETE RESTRICT;
ALTER TABLE amex_direct_cut_items ADD COLUMN account_treatment_decision_id uuid REFERENCES amex_direct_account_treatment_decisions(id) ON DELETE RESTRICT;
ALTER TABLE amex_direct_no_cfdi_decisions DROP CONSTRAINT amex_direct_no_cfdi_decisions_treatment_check;
ALTER TABLE amex_direct_no_cfdi_decisions ADD CONSTRAINT amex_direct_no_cfdi_decisions_treatment_check CHECK(treatment IN('full_amount_expense_no_tax','full_amount_partner_receivable_no_tax'));
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
 sr.revision_id AS sign_revision_id, sr.id AS sign_row_id, sr.proof_hash AS sign_proof_hash,
 d.account_treatment_decision_id AS account_treatment_decision_id
FROM amex_direct_items d
JOIN amex_statement_imports h ON h.id=d.import_id
LEFT JOIN amex_direct_recovered_rows rr ON rr.direct_item_id=d.id
LEFT JOIN amex_direct_coverage_revisions cr ON cr.import_id=d.import_id
LEFT JOIN amex_direct_sign_rows sr ON sr.direct_item_id=d.id;


-- Hash the exact canonical config bytes supplied by the owner, then derive grants
-- from that proven document. Derived authorization arrays are not authority.
CREATE FUNCTION amex_direct_account_treatment_assert(item_uuid uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE i amex_direct_effective_sources%ROWTYPE; d amex_direct_account_treatment_decisions%ROWTYPE;
 ctx amex_statement_direct_contexts%ROWTYPE; e expense_reports%ROWTYPE;
 policy amex_direct_account_treatment_policies%ROWTYPE; base_cfg jsonb;
 card amex_card_accounts%ROWTYPE; liability cuentas_contables%ROWTYPE; debtor cuentas_contables%ROWTYPE;
 cfg jsonb; actor_scope jsonb; budget_scope jsonb; a budget_movement_assignments%ROWTYPE;
 l budget_lines%ROWTYPE; v budget_versions%ROWTYPE; bc budget_concepts%ROWTYPE;
BEGIN
 SELECT * INTO i FROM amex_direct_effective_sources WHERE id=item_uuid;
 SELECT * INTO d FROM amex_direct_account_treatment_decisions WHERE id=i.account_treatment_decision_id;
 IF i.account_treatment_decision_id IS NULL THEN RETURN; END IF;
 IF d.id IS NULL OR d.item_id IS DISTINCT FROM i.id THEN RAISE EXCEPTION 'Account treatment head must identify its real item' USING ERRCODE='23514'; END IF;
 IF d.action='revoke' THEN RETURN; END IF;
 SELECT * INTO ctx FROM amex_statement_direct_contexts WHERE id=d.context_id;
 SELECT * INTO e FROM expense_reports WHERE id=i.imported_expense_id;
 SELECT * INTO card FROM amex_card_accounts WHERE id=(SELECT card_account_id FROM amex_statement_imports WHERE id=i.import_id);
 SELECT * INTO liability FROM cuentas_contables WHERE id=card.liability_cuenta_contable_id;
 SELECT * INTO policy FROM amex_direct_account_treatment_policies WHERE id=d.policy_id;
 cfg:=policy.config_text::jsonb;
 base_cfg:=(cfg-ARRAY['benefit_budget_line_ids','benefit_budget_pairs','partner_debtor_account_ids']) || jsonb_build_object('actor_scopes',(SELECT COALESCE(jsonb_agg(scope-ARRAY['benefit_budget_line_ids','benefit_budget_pairs','partner_debtor_account_ids'] ORDER BY ord),'[]'::jsonb) FROM jsonb_array_elements(cfg->'actor_scopes') WITH ORDINALITY AS scopes(scope,ord)));

 IF (d.item_id,d.import_id,d.imported_expense_id,d.context_id,d.context_version,d.economic_source_hash) IS DISTINCT FROM (i.id,i.import_id,i.imported_expense_id,i.context_id,i.context_version,amex_direct_no_cfdi_source_hash(i.id))
 OR i.movement_kind IS DISTINCT FROM 'debit' OR e.id IS NULL OR e.estado_gasto IS DISTINCT FROM 'activo' OR abs(i.raw_signed_amount) IS DISTINCT FROM round(e.gasto_cantidad::numeric,2)
 OR e.fecha::date IS DISTINCT FROM (i.source_json->>'date')::date OR upper(e.currency) IS DISTINCT FROM ctx.currency OR upper(i.source_json->>'currency') IS DISTINCT FROM ctx.currency
 OR NOT EXISTS(SELECT 1 FROM amex_statement_direct_state s WHERE s.import_id=i.import_id AND (s.context_id,s.context_version) IS NOT DISTINCT FROM (i.context_id,i.context_version))
 OR NOT EXISTS(SELECT 1 FROM empleados WHERE id=d.actor_id AND activo IS TRUE AND rol IN('finanzas','admin','superadmin','super_admin'))
 OR policy.id IS NULL
 OR d.authorization_json->>'policy_id' IS DISTINCT FROM policy.id::text
 OR d.authorization_json->>'config_hash' IS DISTINCT FROM policy.config_hash
 OR d.authorization_json->>'config_text' IS DISTINCT FROM policy.config_text
 OR d.authorization_json->>'base_config_hash' IS DISTINCT FROM policy.base_config_hash
 OR d.authorization_json->>'base_config_text' IS DISTINCT FROM policy.base_config_text
 OR encode(sha256(convert_to(policy.config_text,'UTF8')),'hex') IS DISTINCT FROM policy.config_hash
 OR encode(sha256(convert_to(policy.base_config_text,'UTF8')),'hex') IS DISTINCT FROM policy.base_config_hash
 OR policy.base_config_text::jsonb IS DISTINCT FROM base_cfg
 OR NOT (ctx.config_hash IN(policy.config_hash,policy.base_config_hash) OR EXISTS(SELECT 1 FROM amex_direct_account_treatment_policies prior_policy WHERE prior_policy.config_hash=ctx.config_hash AND prior_policy.base_config_hash=policy.base_config_hash))
 OR d.authorization_json->>'actor_id' IS DISTINCT FROM d.actor_id::text
 OR d.authorization_json->>'context_id' IS DISTINCT FROM d.context_id::text
 OR (d.authorization_json->>'context_version')::integer IS DISTINCT FROM d.context_version
 THEN RAISE EXCEPTION 'Account treatment requires exact current bank/config/actor evidence' USING ERRCODE='23514'; END IF;
 IF cfg->>'entity_code' IS DISTINCT FROM ctx.entity_code OR cfg->>'company_code' IS DISTINCT FROM ctx.company_code OR upper(cfg->>'receiver_rfc') IS DISTINCT FROM ctx.receiver_rfc
 OR NOT COALESCE(cfg->'card_account_ids' ? card.id::text,false)
 OR (SELECT count(*) FROM jsonb_array_elements(COALESCE(cfg->'actor_scopes','[]')) x WHERE x->>'actor_id'=d.actor_id::text)<>1
 THEN RAISE EXCEPTION 'Account treatment fiscal scope mismatch' USING ERRCODE='23514'; END IF;
 SELECT x INTO actor_scope FROM jsonb_array_elements(cfg->'actor_scopes') x WHERE x->>'actor_id'=d.actor_id::text;
 IF card.active IS DISTINCT FROM TRUE OR liability.activo IS DISTINCT FROM TRUE OR liability.codigo NOT IN('2120-002-062','2120-002-063','2120-002-064','2120-002-065','2120-002-066','2120-002-067','2120-002-100')
 OR d.account_snapshot_json->'liability' IS DISTINCT FROM jsonb_build_object('id',liability.id::text,'code',liability.codigo,'name',liability.nombre,'active',liability.activo,'type',liability.tipo)
 THEN RAISE EXCEPTION 'Account treatment liability account changed' USING ERRCODE='23514'; END IF;
 IF d.treatment IN('benefit_expense','partner_receivable') AND (i.tournament_id IS NOT NULL OR i.project_code IS NOT NULL OR NULLIF(trim(e.proyecto),'') IS NOT NULL) THEN RAISE EXCEPTION 'Non-project account treatment cannot invent operational project' USING ERRCODE='23514'; END IF;
 IF d.treatment='partner_receivable' THEN
  SELECT * INTO debtor FROM cuentas_contables WHERE id=d.debtor_account_id;
  IF debtor.id IS NULL OR debtor.activo IS DISTINCT FROM TRUE OR debtor.codigo !~ '^1170-002-[0-9]{3}$'
  OR NOT COALESCE(cfg->'partner_debtor_account_ids' ? debtor.id::text,false) OR NOT COALESCE(actor_scope->'partner_debtor_account_ids' ? debtor.id::text,false)
  OR d.account_snapshot_json->'debtor' IS DISTINCT FROM jsonb_build_object('id',debtor.id::text,'code',debtor.codigo,'name',debtor.nombre,'active',debtor.activo,'type',debtor.tipo)
  OR e.cuenta_contable_id IS DISTINCT FROM debtor.id OR e.contra_cuenta_contable_id IS DISTINCT FROM liability.id
  OR i.budget_status IS DISTINCT FROM 'not_applicable' OR i.budget_assignment_id IS NOT NULL OR i.budget_concept_id IS NOT NULL OR i.budget_line_id IS NOT NULL OR i.budget_version_id IS NOT NULL OR i.edition_year IS NOT NULL OR e.budget_concept_id IS NOT NULL
  THEN RAISE EXCEPTION 'Partner treatment requires exact authorized debtor and no budget expense' USING ERRCODE='23514'; END IF;
 ELSIF d.treatment='benefit_expense' THEN
  IF NOT ((jsonb_array_length(COALESCE(cfg->'benefit_budget_line_ids','[]'))>0 AND jsonb_array_length(COALESCE(actor_scope->'benefit_budget_line_ids','[]'))>0) OR (jsonb_array_length(COALESCE(cfg->'benefit_budget_pairs','[]'))>0 AND jsonb_array_length(COALESCE(actor_scope->'benefit_budget_pairs','[]'))>0))
  OR d.account_snapshot_json->'debtor' IS DISTINCT FROM 'null'::jsonb
  OR (i.budget_status='assigned') IS DISTINCT FROM (i.budget_assignment_id IS NOT NULL)
  THEN RAISE EXCEPTION 'Benefit requires explicit budget scope and coherent assignment stage' USING ERRCODE='23514'; END IF;
  IF i.budget_assignment_id IS NOT NULL THEN
   SELECT * INTO a FROM budget_movement_assignments WHERE id=i.budget_assignment_id;
   SELECT * INTO l FROM budget_lines WHERE id=i.budget_line_id;
   SELECT * INTO v FROM budget_versions WHERE id=i.budget_version_id;
   SELECT * INTO bc FROM budget_concepts WHERE id=i.budget_concept_id;
   IF (a.movement_key,a.budget_line_id,a.budget_version_id,a.budget_concept_id,l.budget_version_id,l.budget_concept_id,v.edition_year) IS DISTINCT FROM ('expense:'||i.imported_expense_id::text,i.budget_line_id,i.budget_version_id,i.budget_concept_id,i.budget_version_id,i.budget_concept_id,i.edition_year)
   OR l.line_direction IS DISTINCT FROM 'expense' OR bc.active IS DISTINCT FROM TRUE OR bc.budget_direction IS DISTINCT FROM 'expense' OR bc.cuenta_contable_id IS NULL
   OR e.budget_concept_id IS DISTINCT FROM bc.id OR e.cuenta_contable_id IS DISTINCT FROM bc.cuenta_contable_id OR e.contra_cuenta_contable_id IS DISTINCT FROM liability.id
   OR v.source='distribution_revision' OR NOT (v.status IN('approved','frozen') OR (cfg->'allow_draft_reforecast'='true'::jsonb AND v.status IN('draft','reforecast')))
   OR NOT COALESCE(cfg->'budget_actor_ids' ? a.assigned_by_empleado_id::text,false)
   THEN RAISE EXCEPTION 'Benefit assignment requires exact canonical budget/account mapping' USING ERRCODE='23514'; END IF;
   SELECT x INTO budget_scope FROM jsonb_array_elements(cfg->'actor_scopes') x WHERE x->>'actor_id'=a.assigned_by_empleado_id::text;
   IF NOT ((COALESCE(cfg->'benefit_budget_line_ids' ? l.id::text,false) AND COALESCE(budget_scope->'benefit_budget_line_ids' ? l.id::text,false)) OR (COALESCE(cfg->'benefit_budget_pairs' @> jsonb_build_array(jsonb_build_object('budget_version_id',v.id::text,'budget_concept_id',bc.id::text)),false) AND COALESCE(budget_scope->'benefit_budget_pairs' @> jsonb_build_array(jsonb_build_object('budget_version_id',v.id::text,'budget_concept_id',bc.id::text)),false)))
   OR NOT ((COALESCE(cfg->'benefit_budget_line_ids' ? l.id::text,false) AND COALESCE(actor_scope->'benefit_budget_line_ids' ? l.id::text,false)) OR (COALESCE(cfg->'benefit_budget_pairs' @> jsonb_build_array(jsonb_build_object('budget_version_id',v.id::text,'budget_concept_id',bc.id::text)),false) AND COALESCE(actor_scope->'benefit_budget_pairs' @> jsonb_build_array(jsonb_build_object('budget_version_id',v.id::text,'budget_concept_id',bc.id::text)),false)))
   THEN RAISE EXCEPTION 'Benefit budget line is outside confirmed actor grants' USING ERRCODE='23514'; END IF;
  ELSIF i.budget_line_id IS NOT NULL OR i.budget_version_id IS NOT NULL OR i.budget_concept_id IS NOT NULL OR i.edition_year IS NOT NULL THEN RAISE EXCEPTION 'Unassigned benefit cannot carry partial budget mapping' USING ERRCODE='23514'; END IF;
 ELSIF d.account_snapshot_json->'debtor' IS DISTINCT FROM 'null'::jsonb THEN RAISE EXCEPTION 'Project treatment cannot carry debtor account' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION amex_direct_account_treatment_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE i amex_direct_effective_sources%ROWTYPE; b amex_direct_items%ROWTYPE; p amex_direct_account_treatment_decisions%ROWTYPE;
BEGIN
 SELECT * INTO i FROM amex_direct_effective_sources WHERE id=NEW.item_id;
 PERFORM 1 FROM amex_statement_imports WHERE id=i.import_id FOR UPDATE;
 PERFORM pg_advisory_xact_lock(hashtext('samchat:amex:shared-recognition'));
 SELECT * INTO b FROM amex_direct_items WHERE id=NEW.item_id FOR UPDATE;
 SELECT * INTO p FROM amex_direct_account_treatment_decisions WHERE id=b.account_treatment_decision_id;
 IF EXISTS(SELECT 1 FROM amex_direct_accounting_cuts WHERE import_id=i.import_id) OR EXISTS(SELECT 1 FROM amex_direct_completion_receipts WHERE import_id=i.import_id)
 OR i.budget_status IN('sent','submitted','assigned') OR i.budget_assignment_id IS NOT NULL OR i.credit_excluded IS TRUE OR i.accounting_status='recognized'
 OR EXISTS(SELECT 1 FROM amex_recognition_representations WHERE expense_id=i.imported_expense_id)
 OR EXISTS(SELECT 1 FROM amex_recognition_consumptions WHERE imported_expense_id=i.imported_expense_id OR source_report_expense_id=i.imported_expense_id)
 OR NOT EXISTS(SELECT 1 FROM empleados WHERE id=NEW.actor_id AND activo IS TRUE AND rol IN('finanzas','admin','superadmin','super_admin'))
 OR NEW.versions_json->'confirmed' IS DISTINCT FROM 'true'::jsonb
 OR (NEW.versions_json->'expected_versions'->>NEW.item_id::text)::integer IS DISTINCT FROM b.source_version
 OR NEW.source_version IS DISTINCT FROM b.source_version OR NEW.source_hash IS DISTINCT FROM b.source_hash
 OR (NEW.action='set' AND EXISTS(SELECT 1 FROM amex_direct_account_treatment_policies selected JOIN amex_direct_account_treatment_policies later ON later.base_config_hash=selected.base_config_hash AND later.revision>selected.revision WHERE selected.id=NEW.policy_id))
 OR NEW.previous_decision_id IS DISTINCT FROM b.account_treatment_decision_id OR NEW.revision IS DISTINCT FROM COALESCE(p.revision,0)+1
 OR (p.id IS NULL AND NEW.action!='set') OR (NEW.action='revoke' AND (p.id IS NULL OR p.action!='set' OR NEW.treatment IS DISTINCT FROM p.treatment OR NEW.debtor_account_id IS DISTINCT FROM p.debtor_account_id))
 OR (NEW.item_id,NEW.import_id,NEW.imported_expense_id) IS DISTINCT FROM (i.id,i.import_id,i.imported_expense_id)
 OR i.movement_kind IS DISTINCT FROM 'debit'
 THEN RAISE EXCEPTION 'Account treatment requires authorized unfrozen sequential confirmed transition' USING ERRCODE='23514'; END IF;
 IF NEW.action='set' AND (NEW.context_id,NEW.context_version,NEW.economic_source_hash) IS DISTINCT FROM (i.context_id,i.context_version,amex_direct_no_cfdi_source_hash(i.id)) THEN RAISE EXCEPTION 'Account treatment source proof mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.action='revoke' AND NOT EXISTS(
  SELECT 1 FROM amex_direct_account_treatment_policies current_policy
  JOIN amex_direct_account_treatment_policies original_policy ON original_policy.id=p.policy_id AND original_policy.base_config_hash=current_policy.base_config_hash
  WHERE NOT EXISTS(SELECT 1 FROM amex_direct_account_treatment_policies later WHERE later.base_config_hash=current_policy.base_config_hash AND later.revision>current_policy.revision)
  AND (SELECT count(*) FROM jsonb_array_elements(current_policy.config_text::jsonb->'actor_scopes') scope WHERE scope->>'actor_id'=NEW.actor_id::text)=1
 ) THEN RAISE EXCEPTION 'Account treatment revocation requires currently configured Finance actor' USING ERRCODE='23514'; END IF;
 IF NEW.action='revoke' AND (NEW.context_id,NEW.context_version,NEW.economic_source_hash,NEW.account_snapshot_json,NEW.authorization_json,NEW.policy_id) IS DISTINCT FROM (p.context_id,p.context_version,p.economic_source_hash,p.account_snapshot_json,p.authorization_json,p.policy_id) THEN RAISE EXCEPTION 'Account treatment revocation preserves original decision proof' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION amex_direct_account_treatment_projection_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE old_decision amex_direct_account_treatment_decisions%ROWTYPE;
 next_decision amex_direct_account_treatment_decisions%ROWTYPE;
 current_policy amex_direct_account_treatment_policies%ROWTYPE;
 old_assignment budget_movement_assignments%ROWTYPE; source amex_direct_effective_sources%ROWTYPE;
BEGIN
 IF NEW.account_treatment_decision_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM amex_direct_account_treatment_decisions WHERE id=NEW.account_treatment_decision_id AND item_id=NEW.id) THEN RAISE EXCEPTION 'Account treatment head belongs to another item' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' THEN
  IF NEW.account_treatment_decision_id IS DISTINCT FROM OLD.account_treatment_decision_id AND (NEW.account_treatment_decision_id IS NULL OR NOT EXISTS(SELECT 1 FROM amex_direct_account_treatment_decisions WHERE id=NEW.account_treatment_decision_id AND previous_decision_id IS NOT DISTINCT FROM OLD.account_treatment_decision_id)) THEN RAISE EXCEPTION 'Account treatment head requires append-only transition' USING ERRCODE='23514'; END IF;
  SELECT * INTO old_decision FROM amex_direct_account_treatment_decisions WHERE id=OLD.account_treatment_decision_id;
  IF old_decision.action='set' THEN
   SELECT p.* INTO current_policy FROM amex_direct_account_treatment_policies p WHERE p.base_config_hash=(SELECT base_config_hash FROM amex_direct_account_treatment_policies WHERE id=old_decision.policy_id) ORDER BY p.revision DESC LIMIT 1;
   IF current_policy.id IS DISTINCT FROM old_decision.policy_id THEN
    IF NEW.account_treatment_decision_id IS DISTINCT FROM OLD.account_treatment_decision_id THEN
     SELECT * INTO next_decision FROM amex_direct_account_treatment_decisions WHERE id=NEW.account_treatment_decision_id;
     IF next_decision.action IS DISTINCT FROM 'revoke' OR next_decision.previous_decision_id IS DISTINCT FROM old_decision.id
     OR (to_jsonb(NEW)-ARRAY['account_treatment_decision_id','source_version','source_hash','updated_at','budget_status','tournament_id','project_code','edition_year','budget_concept_id','budget_line_id','budget_version_id','budget_assignment_id']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['account_treatment_decision_id','source_version','source_hash','updated_at','budget_status','tournament_id','project_code','edition_year','budget_concept_id','budget_line_id','budget_version_id','budget_assignment_id'])
     THEN RAISE EXCEPTION 'Stale account policy permits only explicit original treatment revocation' USING ERRCODE='23514'; END IF;
    ELSIF NEW.no_cfdi_decision_id IS DISTINCT FROM OLD.no_cfdi_decision_id THEN
     IF NOT EXISTS(SELECT 1 FROM amex_direct_no_cfdi_decisions nd WHERE nd.id=NEW.no_cfdi_decision_id AND nd.item_id=NEW.id AND nd.action='revoke' AND nd.previous_decision_id=OLD.no_cfdi_decision_id)
     OR (to_jsonb(NEW)-ARRAY['no_cfdi_decision_id','source_version','source_hash','updated_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['no_cfdi_decision_id','source_version','source_hash','updated_at'])
     THEN RAISE EXCEPTION 'Stale account policy permits only exact fiscal revocation cleanup' USING ERRCODE='23514'; END IF;
    ELSIF OLD.budget_status IN('sent','assigned') AND NEW.budget_status='classified' THEN
     IF NEW.budget_assignment_id IS NOT NULL OR NEW.budget_line_id IS NOT NULL OR NEW.budget_version_id IS NOT NULL
     OR (old_decision.treatment='benefit_expense' AND (NEW.budget_concept_id IS NOT NULL OR NEW.edition_year IS NOT NULL))
     OR (to_jsonb(NEW)-ARRAY['source_version','source_hash','updated_at','budget_status','budget_concept_id','edition_year','budget_line_id','budget_version_id','budget_assignment_id']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['source_version','source_hash','updated_at','budget_status','budget_concept_id','edition_year','budget_line_id','budget_version_id','budget_assignment_id'])
     THEN RAISE EXCEPTION 'Stale account policy permits only exact budget return cleanup' USING ERRCODE='23514'; END IF;
     IF OLD.budget_assignment_id IS NOT NULL THEN
      SELECT * INTO source FROM amex_direct_effective_sources WHERE id=OLD.id;
      SELECT * INTO old_assignment FROM budget_movement_assignments WHERE id=OLD.budget_assignment_id;
      IF old_assignment.id IS NULL OR (old_assignment.budget_line_id,old_assignment.budget_version_id,old_assignment.budget_concept_id,old_assignment.movement_key) IS DISTINCT FROM (OLD.budget_line_id,OLD.budget_version_id,OLD.budget_concept_id,'expense:'||source.imported_expense_id::text) THEN RAISE EXCEPTION 'Budget return must preserve exact original assignment proof' USING ERRCODE='23514'; END IF;
     END IF;
    ELSE RAISE EXCEPTION 'Stale account policy cannot authorize new source or budget work' USING ERRCODE='23514';
    END IF;
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION amex_direct_account_treatment_seal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item_uuid uuid; d amex_direct_account_treatment_decisions%ROWTYPE; head uuid;
BEGIN
 IF TG_TABLE_NAME='amex_direct_account_treatment_decisions' THEN
  item_uuid:=NEW.item_id; d:=NEW;
  SELECT account_treatment_decision_id INTO head FROM amex_direct_items WHERE id=item_uuid;
  IF head IS DISTINCT FROM d.id AND NOT EXISTS(SELECT 1 FROM amex_direct_account_treatment_decisions WHERE previous_decision_id=d.id) THEN RAISE EXCEPTION 'Account treatment evidence must be head or linked predecessor' USING ERRCODE='23514'; END IF;
  IF NOT EXISTS(SELECT 1 FROM amex_direct_events ev WHERE ev.item_id=d.item_id AND ev.actor_id=d.actor_id AND ev.action='account_treatment_'||d.action AND ev.payload_json->>'account_treatment_decision_id'=d.id::text AND ev.payload_json->>'request_payload_hash'=d.payload_hash AND ev.payload_json->'confirmed'='true'::jsonb AND ev.payload_json->>'reason'=d.reason AND ev.source_version=d.source_version+1) THEN RAISE EXCEPTION 'Account treatment requires exact confirmed audit event' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='amex_direct_events' THEN item_uuid:=NEW.item_id;
 ELSE
  item_uuid:=NEW.id;
  IF TG_OP='UPDATE' THEN
   IF OLD.account_treatment_decision_id IS NOT NULL AND OLD.budget_status IN('sent','assigned') AND NEW.budget_status='classified' AND NEW.account_treatment_decision_id=OLD.account_treatment_decision_id THEN
    IF NOT EXISTS(SELECT 1 FROM amex_direct_events ev JOIN empleados actor ON actor.id=ev.actor_id JOIN amex_direct_account_treatment_decisions decision ON decision.id=NEW.account_treatment_decision_id JOIN amex_direct_account_treatment_policies policy ON policy.base_config_hash=(SELECT base_config_hash FROM amex_direct_account_treatment_policies WHERE id=decision.policy_id) AND NOT EXISTS(SELECT 1 FROM amex_direct_account_treatment_policies later WHERE later.base_config_hash=policy.base_config_hash AND later.revision>policy.revision) WHERE ev.item_id=NEW.id AND ev.action='budget_return' AND ev.source_version=NEW.source_version AND length(trim(ev.payload_json->>'reason'))>0 AND actor.activo IS TRUE AND COALESCE(policy.config_text::jsonb->'budget_actor_ids' ? ev.actor_id::text,false) AND ev.payload_json->'prior'->>'line_id' IS NOT DISTINCT FROM OLD.budget_line_id::text AND ev.payload_json->'prior'->>'version_id' IS NOT DISTINCT FROM OLD.budget_version_id::text AND ev.payload_json->'prior'->>'assignment_id' IS NOT DISTINCT FROM OLD.budget_assignment_id::text) THEN RAISE EXCEPTION 'Budget return requires current controller and exact original allocation audit' USING ERRCODE='23514'; END IF;
   END IF;
  END IF;
 END IF;
 PERFORM amex_direct_account_treatment_assert(item_uuid);
 RETURN NULL;
END $$;

CREATE FUNCTION amex_direct_account_treatment_context_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='amex_statement_direct_state' THEN
  IF (NEW.context_id,NEW.context_version) IS NOT DISTINCT FROM (OLD.context_id,OLD.context_version) THEN RETURN NEW; END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_items i JOIN amex_direct_account_treatment_decisions d ON d.id=i.account_treatment_decision_id WHERE i.import_id=NEW.import_id AND d.action='set') THEN RAISE EXCEPTION 'Active account treatment requires revocation before context/recovery/sign change' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ad_account_treatment_insert BEFORE INSERT ON amex_direct_account_treatment_decisions FOR EACH ROW EXECUTE FUNCTION amex_direct_account_treatment_insert_guard();
CREATE TRIGGER ad_account_treatment_immutable BEFORE UPDATE OR DELETE ON amex_direct_account_treatment_decisions FOR EACH ROW EXECUTE FUNCTION amex_direct_immutable();
CREATE TRIGGER ad_account_treatment_projection BEFORE INSERT OR UPDATE ON amex_direct_items FOR EACH ROW EXECUTE FUNCTION amex_direct_account_treatment_projection_guard();
CREATE CONSTRAINT TRIGGER ad_account_treatment_decision_seal AFTER INSERT ON amex_direct_account_treatment_decisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_account_treatment_seal();
CREATE CONSTRAINT TRIGGER ad_account_treatment_item_seal AFTER INSERT OR UPDATE ON amex_direct_items DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_account_treatment_seal();
CREATE CONSTRAINT TRIGGER ad_account_treatment_event_seal AFTER INSERT ON amex_direct_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_account_treatment_seal();
CREATE TRIGGER ad_account_treatment_context BEFORE INSERT ON amex_statement_direct_contexts FOR EACH ROW EXECUTE FUNCTION amex_direct_account_treatment_context_guard();
CREATE TRIGGER ad_account_treatment_state BEFORE UPDATE ON amex_statement_direct_state FOR EACH ROW EXECUTE FUNCTION amex_direct_account_treatment_context_guard();
CREATE TRIGGER ad_account_treatment_recovery BEFORE INSERT ON amex_direct_coverage_revisions FOR EACH ROW EXECUTE FUNCTION amex_direct_account_treatment_context_guard();
CREATE TRIGGER ad_account_treatment_sign BEFORE INSERT ON amex_direct_sign_revisions FOR EACH ROW EXECUTE FUNCTION amex_direct_account_treatment_context_guard();

CREATE FUNCTION amex_direct_account_treatment_expense_seal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item_uuid uuid;
BEGIN
 FOR item_uuid IN SELECT id FROM amex_direct_effective_sources WHERE imported_expense_id=NEW.id LOOP
  PERFORM amex_direct_account_treatment_assert(item_uuid);
 END LOOP;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ad_account_treatment_expense_seal AFTER UPDATE ON expense_reports DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION amex_direct_account_treatment_expense_seal();
CREATE OR REPLACE FUNCTION amex_direct_cut_item_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item amex_direct_effective_sources%ROWTYPE; cut amex_direct_accounting_cuts%ROWTYPE; header amex_statement_imports%ROWTYPE;
BEGIN
 SELECT * INTO cut FROM amex_direct_accounting_cuts WHERE id=NEW.cut_id;
 SELECT * INTO header FROM amex_statement_imports WHERE id=cut.import_id FOR UPDATE;
 SELECT * INTO item FROM amex_direct_effective_sources WHERE id=NEW.direct_item_id;
 IF (item.import_id,item.import_item_id,item.imported_expense_id,item.source_hash) IS DISTINCT FROM (cut.import_id,NEW.import_item_id,NEW.imported_expense_id,NEW.source_hash) OR (SELECT count(*) FROM amex_direct_cut_items WHERE cut_id=NEW.cut_id)>=header.total_rows THEN RAISE EXCEPTION 'Direct cut membership is invalid or sealed' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind NOT IN('new','new_no_cfdi','partner_receivable','settlement','prior','credit_exclusion') OR (item.movement_kind='credit') IS DISTINCT FROM (NEW.coverage_kind='credit_exclusion') OR item.movement_kind NOT IN('debit','credit') THEN RAISE EXCEPTION 'Direct cut requires typed economic coverage' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind='credit_exclusion' THEN
 IF item.credit_excluded IS NOT TRUE OR NEW.consumption_id IS NOT NULL OR NEW.accounting_poliza_id IS NOT NULL THEN RAISE EXCEPTION 'Credit exclusion cannot be posted' USING ERRCODE='23514'; END IF;
 ELSIF NEW.coverage_kind='settlement' THEN
 IF NEW.consumption_id IS NOT NULL OR NEW.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id THEN RAISE EXCEPTION 'Settlement has own journal proof, not fiscal consumption' USING ERRCODE='23514'; END IF;
 ELSE
 IF NEW.consumption_id IS NULL OR NEW.accounting_poliza_id IS NULL THEN RAISE EXCEPTION 'Debit coverage requires journal receipt' USING ERRCODE='23514'; END IF;
 END IF;
 IF (NEW.coverage_kind='new_no_cfdi' OR (NEW.coverage_kind='partner_receivable' AND item.cfdi_id IS NULL)) IS DISTINCT FROM (NEW.no_cfdi_decision_id IS NOT NULL) THEN RAISE EXCEPTION 'No CFDI coverage requires exact decision receipt only' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind IN('new_no_cfdi','partner_receivable') AND NEW.no_cfdi_decision_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM amex_direct_items i JOIN amex_direct_no_cfdi_decisions d ON d.id=i.no_cfdi_decision_id WHERE i.id=NEW.direct_item_id AND d.id=NEW.no_cfdi_decision_id AND d.action='declare') THEN RAISE EXCEPTION 'No CFDI cut requires active current declaration' USING ERRCODE='23514'; END IF;
 IF NEW.account_treatment_decision_id IS DISTINCT FROM item.account_treatment_decision_id THEN RAISE EXCEPTION 'Cut must bind current account treatment head' USING ERRCODE='23514'; END IF;
 IF NEW.coverage_kind='partner_receivable' AND NEW.account_treatment_decision_id IS NULL THEN RAISE EXCEPTION 'Partner coverage requires explicit account decision' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION amex_direct_no_cfdi_assert(item_uuid uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE i amex_direct_effective_sources%ROWTYPE; d amex_direct_no_cfdi_decisions%ROWTYPE; e expense_reports%ROWTYPE;
BEGIN
 SELECT * INTO i FROM amex_direct_effective_sources WHERE id=item_uuid;
 SELECT receipt.* INTO d FROM amex_direct_items b JOIN amex_direct_no_cfdi_decisions receipt ON receipt.id=b.no_cfdi_decision_id WHERE b.id=item_uuid;
 IF d.id IS NULL OR d.action='revoke' THEN RETURN; END IF;
 SELECT * INTO e FROM expense_reports WHERE id=i.imported_expense_id;
 IF (d.treatment='full_amount_partner_receivable_no_tax') IS DISTINCT FROM EXISTS(SELECT 1 FROM amex_direct_account_treatment_decisions ad WHERE ad.id=i.account_treatment_decision_id AND ad.action='set' AND ad.treatment='partner_receivable') THEN RAISE EXCEPTION 'No CFDI declaration requires matching account treatment' USING ERRCODE='23514'; END IF;
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
CREATE OR REPLACE FUNCTION amex_direct_check_cut() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cut_uuid uuid; cut amex_direct_accounting_cuts%ROWTYPE; header amex_statement_imports%ROWTYPE; debit_total numeric; credit_total numeric; new_total numeric;
BEGIN
 IF TG_TABLE_NAME='amex_direct_accounting_cuts' THEN cut_uuid:=NEW.id;
 ELSIF TG_TABLE_NAME='amex_direct_cut_items' THEN cut_uuid:=NEW.cut_id;
 ELSIF TG_TABLE_NAME='amex_recognition_consumptions' THEN
 IF NEW.binding_mode!='direct' THEN RETURN NULL; END IF;
 SELECT cut_id INTO cut_uuid FROM amex_direct_cut_items WHERE consumption_id=NEW.id AND coverage_kind IN('new','new_no_cfdi','partner_receivable');
 IF cut_uuid IS NULL THEN RAISE EXCEPTION 'Direct consumption requires complete cut receipt' USING ERRCODE='23514'; END IF;
 ELSE
 SELECT id INTO cut_uuid FROM amex_direct_accounting_cuts WHERE accounting_poliza_id=NEW.poliza_id;
 IF cut_uuid IS NULL THEN RETURN NULL; END IF;
 END IF;
 SELECT * INTO cut FROM amex_direct_accounting_cuts WHERE id=cut_uuid;
 SELECT * INTO header FROM amex_statement_imports WHERE id=cut.import_id;
 IF (SELECT count(*) FROM amex_direct_cut_items WHERE cut_id=cut.id)<>header.total_rows OR NOT EXISTS(SELECT 1 FROM amex_direct_cut_items WHERE cut_id=cut.id AND coverage_kind IN('new','new_no_cfdi','partner_receivable','settlement')) THEN RAISE EXCEPTION 'Direct cut must include complete membership and new economic activity' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_coverage_revisions cr WHERE cr.import_id=cut.import_id AND (cut.snapshot_json->'recovery_revision_ids' IS DISTINCT FROM jsonb_build_array(cr.id::text) OR cut.snapshot_json->'original_period' IS DISTINCT FROM jsonb_build_object('start',cr.original_period_start::text,'end',cr.original_period_end::text) OR cut.snapshot_json->'effective_period' IS DISTINCT FROM jsonb_build_object('start',cr.period_start::text,'end',cr.period_end::text))) THEN RAISE EXCEPTION 'Direct cut recovery coverage proof mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources es ON es.id=ci.direct_item_id WHERE ci.cut_id=cut.id AND es.recovery_id IS NOT NULL AND (ci.snapshot_json->>'recovery_revision_id' IS DISTINCT FROM es.recovery_revision_id::text OR ci.snapshot_json->>'recovery_proof_hash' IS DISTINCT FROM es.recovery_proof_hash OR ci.snapshot_json->'source' IS DISTINCT FROM es.source_json)) THEN RAISE EXCEPTION 'Direct cut recovered source proof mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id WHERE ci.cut_id=cut.id AND (i.context_id IS DISTINCT FROM cut.context_id OR i.context_version IS DISTINCT FROM cut.context_version OR i.movement_kind IN('unknown','skipped') OR (ci.coverage_kind='new' AND (i.cfdi_id IS NULL OR i.budget_assignment_id IS NULL OR i.status_receipt_id IS NULL)) OR (ci.coverage_kind='partner_receivable' AND ((i.cfdi_id IS NOT NULL AND i.status_receipt_id IS NULL) OR (i.cfdi_id IS NULL AND ci.no_cfdi_decision_id IS NULL))) OR (ci.coverage_kind='new_no_cfdi' AND (i.budget_assignment_id IS NULL OR i.budget_concept_id IS NULL OR i.budget_version_id IS NULL OR i.budget_line_id IS NULL OR i.edition_year IS NULL)))) THEN RAISE EXCEPTION 'Direct cut contains unresolved fiscal/budget source' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi','partner_receivable') AND (c.binding_mode!='direct' OR c.imported_expense_id IS DISTINCT FROM i.imported_expense_id OR c.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id OR ci.accounting_poliza_id IS DISTINCT FROM cut.accounting_poliza_id OR c.amount IS DISTINCT FROM abs(i.raw_signed_amount))) THEN RAISE EXCEPTION 'Direct cut economic journal mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci WHERE ci.cut_id=cut.id AND ci.coverage_kind='prior' AND NOT EXISTS(SELECT 1 FROM amex_direct_prior_coverages pc WHERE pc.cut_id=cut.id AND pc.direct_item_id=ci.direct_item_id AND pc.consumption_id=ci.consumption_id AND pc.accounting_poliza_id=ci.accounting_poliza_id)) THEN RAISE EXCEPTION 'Prior coverage requires canonical association receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci WHERE ci.cut_id=cut.id AND ci.coverage_kind='settlement' AND NOT EXISTS(SELECT 1 FROM amex_expense_account_settlements es WHERE es.cut_id=cut.id AND es.direct_item_id=ci.direct_item_id AND es.accounting_poliza_id=ci.accounting_poliza_id)) THEN RAISE EXCEPTION 'Settlement coverage requires allocation receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_cfdi_status_receipts sr ON sr.id=i.status_receipt_id JOIN cfdi_reports cf ON cf.id=i.cfdi_id JOIN amex_statement_direct_contexts ctx ON ctx.id=i.context_id WHERE ci.cut_id=cut.id AND (ci.coverage_kind='new' OR (ci.coverage_kind='partner_receivable' AND i.cfdi_id IS NOT NULL)) AND (lower(sr.observed_status)!='vigente' OR sr.observed_at<clock_timestamp()-INTERVAL '24 hours' OR sr.observed_at>clock_timestamp() OR sr.receiver_rfc IS DISTINCT FROM ctx.receiver_rfc OR cf.receptor_rfc IS DISTINCT FROM ctx.receiver_rfc OR upper(cf.moneda) IS DISTINCT FROM ctx.currency OR upper(cf.cfdi_uuid) IS DISTINCT FROM upper(sr.fiscal_uuid) OR upper(cf.emisor_rfc) IS DISTINCT FROM upper(sr.issuer_rfc) OR round(cf.total::numeric,2) IS DISTINCT FROM sr.amount OR cf.origen IS DISTINCT FROM 'sat' OR cf.xml_raw IS NULL OR sr.xml_hash IS DISTINCT FROM encode(sha256(convert_to(cf.xml_raw,'UTF8')),'hex'))) THEN RAISE EXCEPTION 'Direct cut requires current exact fiscal status receipt' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN expense_reports e ON e.id=i.imported_expense_id LEFT JOIN budget_versions v ON v.id=i.budget_version_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi') AND (i.edition_year IS DISTINCT FROM extract(year FROM e.fecha)::integer OR i.edition_year IS DISTINCT FROM extract(year FROM cut.accounting_date)::integer OR v.edition_year IS DISTINCT FROM i.edition_year)) THEN RAISE EXCEPTION 'Direct fiscal and accounting dates require budget edition' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id JOIN amex_card_accounts card ON card.id=c.card_account_id JOIN cuentas_contables liability ON liability.id=c.liability_account_id JOIN budget_concepts bc ON bc.id=i.budget_concept_id JOIN expense_reports expense ON expense.id=i.imported_expense_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi') AND (card.active IS DISTINCT FROM TRUE OR liability.activo IS DISTINCT FROM TRUE OR card.liability_cuenta_contable_id IS DISTINCT FROM c.liability_account_id OR liability.codigo IS DISTINCT FROM c.liability_code OR bc.active IS DISTINCT FROM TRUE OR bc.budget_direction IS DISTINCT FROM 'expense' OR bc.cuenta_contable_id IS NULL OR expense.cuenta_contable_id IS DISTINCT FROM bc.cuenta_contable_id OR NOT EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=bc.cuenta_contable_id AND l.debe>0))) THEN RAISE EXCEPTION 'Direct cut account mapping changed or absent' USING ERRCODE='23514'; END IF;
 -- Card credits are bank net. Additional credits require exact canonical
 -- withholding evidence, frozen per source; an arbitrary balancing credit fails.
 IF EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.haber>0 AND NOT EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi','partner_receivable') AND c.liability_account_id=l.cuenta_contable_id) AND NOT EXISTS(SELECT 1 FROM amex_expense_account_settlements es JOIN amex_card_accounts ca ON ca.id=es.card_account_id WHERE es.cut_id=cut.id AND ca.liability_cuenta_contable_id=l.cuenta_contable_id) AND NOT EXISTS(
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
 IF EXISTS(SELECT 1 FROM (SELECT account,sum(amount) amount FROM (SELECT c.liability_account_id account,c.amount FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi','partner_receivable') UNION ALL SELECT ca.liability_cuenta_contable_id,es.amount FROM amex_expense_account_settlements es JOIN amex_card_accounts ca ON ca.id=es.card_account_id WHERE es.cut_id=cut.id) amounts GROUP BY account) expected WHERE expected.amount IS DISTINCT FROM (SELECT sum(l.haber::numeric) FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=expected.account)) THEN RAISE EXCEPTION 'Direct card liability must equal exact bank net' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN expense_reports own_expense ON own_expense.id=i.imported_expense_id WHERE ci.cut_id=cut.id AND (ci.coverage_kind='new' OR (ci.coverage_kind='partner_receivable' AND i.cfdi_id IS NOT NULL)) AND EXISTS(SELECT 1 FROM expense_reports other_expense WHERE other_expense.cfdi_report_id=i.cfdi_id AND other_expense.estado_gasto='activo' AND other_expense.id!=i.imported_expense_id AND (own_expense.cfdi_compartido_confirmado IS DISTINCT FROM TRUE OR other_expense.cfdi_compartido_confirmado IS DISTINCT FROM TRUE))) THEN RAISE EXCEPTION 'Direct fiscal identity has incompatible global usage' USING ERRCODE='23514'; END IF;
 SELECT sum(c.amount) INTO new_total FROM amex_direct_cut_items ci JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id WHERE ci.cut_id=cut.id AND ci.coverage_kind IN('new','new_no_cfdi','partner_receivable');
 new_total:=coalesce(new_total,0)+coalesce((SELECT sum(amount) FROM amex_expense_account_settlements WHERE cut_id=cut.id),0);
 SELECT sum(COALESCE(debe,0)::numeric),sum(COALESCE(haber,0)::numeric) INTO debit_total,credit_total FROM accounting_poliza_lines WHERE poliza_id=cut.accounting_poliza_id;
 IF new_total IS NULL OR new_total<=0 OR debit_total IS NULL OR debit_total<=0 OR round(debit_total,2) IS DISTINCT FROM round(credit_total,2) OR EXISTS(SELECT 1 FROM accounting_poliza_lines WHERE poliza_id=cut.accounting_poliza_id AND (COALESCE(debe,0)<0 OR COALESCE(haber,0)<0 OR (COALESCE(debe,0)>0 AND COALESCE(haber,0)>0))) THEN RAISE EXCEPTION 'Direct journal must balance exact positive economic recognition' USING ERRCODE='23514'; END IF;

 -- Freeze exactly the canonical SQL membership, including multiplicity.
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items WHERE cut_id=cut.id AND coverage_kind IN('new_no_cfdi','partner_receivable')) AND EXISTS(
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
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items WHERE cut_id=cut.id AND coverage_kind IN('new_no_cfdi','partner_receivable')) AND EXISTS(
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

 -- Every new typed destination is tied to its immutable decision, policy and
 -- full frozen member, not a nullable-budget shortcut.
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id LEFT JOIN amex_direct_account_treatment_decisions d ON d.id=i.account_treatment_decision_id WHERE ci.cut_id=cut.id AND (
 ci.account_treatment_decision_id IS DISTINCT FROM i.account_treatment_decision_id
 OR (i.account_treatment_decision_id IS NULL AND NULLIF(ci.snapshot_json->'account_treatment','null'::jsonb) IS NOT NULL)
 OR (i.account_treatment_decision_id IS NOT NULL AND (
 d.action IS DISTINCT FROM 'set' OR ci.coverage_kind NOT IN('new','new_no_cfdi','partner_receivable')
 OR EXISTS(SELECT 1 FROM amex_direct_account_treatment_policies selected JOIN amex_direct_account_treatment_policies later ON later.base_config_hash=selected.base_config_hash AND later.revision>selected.revision WHERE selected.id=d.policy_id)
 OR (d.treatment='partner_receivable') IS DISTINCT FROM (ci.coverage_kind='partner_receivable')
 OR ci.snapshot_json->>'account_treatment_decision_id' IS DISTINCT FROM d.id::text
 OR ((ci.snapshot_json->'account_treatment')-'created_at') IS DISTINCT FROM (to_jsonb(d)-ARRAY['created_at','previous_decision_id','request_id','idempotency_key','versions_json'])
 OR (ci.snapshot_json->'account_treatment'->>'created_at')::timestamptz IS DISTINCT FROM d.created_at
 )))) THEN RAISE EXCEPTION 'Direct cut account treatment proof mismatch' USING ERRCODE='23514'; END IF;
 PERFORM amex_direct_account_treatment_assert(ci.direct_item_id) FROM amex_direct_cut_items ci WHERE ci.cut_id=cut.id AND ci.account_treatment_decision_id IS NOT NULL;
 IF EXISTS(SELECT 1 FROM amex_direct_cut_items ci JOIN amex_direct_effective_sources i ON i.id=ci.direct_item_id JOIN amex_direct_account_treatment_decisions d ON d.id=ci.account_treatment_decision_id JOIN amex_recognition_consumptions c ON c.id=ci.consumption_id JOIN expense_reports e ON e.id=i.imported_expense_id WHERE ci.cut_id=cut.id AND ci.coverage_kind='partner_receivable' AND (
 d.treatment IS DISTINCT FROM 'partner_receivable' OR d.action IS DISTINCT FROM 'set'
 OR i.budget_assignment_id IS NOT NULL OR i.budget_status IS DISTINCT FROM 'not_applicable'
 OR (i.cfdi_id IS NULL) IS DISTINCT FROM (ci.no_cfdi_decision_id IS NOT NULL)
 OR ci.snapshot_json->>'cfdi_id' IS DISTINCT FROM i.cfdi_id::text
 OR (i.cfdi_id IS NULL) IS DISTINCT FROM (NULLIF(ci.snapshot_json->'cfdi','null'::jsonb) IS NULL)
 OR NULLIF(ci.snapshot_json->'taxes','null'::jsonb) IS NOT NULL OR COALESCE(ci.snapshot_json->'fiscal_retention_proof','[]'::jsonb)<>'[]'::jsonb
 OR NOT COALESCE(c.classification_json @> jsonb_build_object('treatment','partner_receivable','debtor_account_id',d.debtor_account_id::text,'context_id',i.context_id::text,'account_treatment_decision_id',d.id::text),false)
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(cut.snapshot_json->'partidas') f WHERE f=ci.snapshot_json)
 OR jsonb_array_length(COALESCE(ci.snapshot_json->'journal_lines','[]'::jsonb))<>2
 OR (SELECT count(*) FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'direct_item_id'=i.id::text)<>2
 OR (SELECT count(*) FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'expense_id'=i.imported_expense_id::text)<>2
 OR NOT EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=d.debtor_account_id AND l.debe::numeric=abs(i.raw_signed_amount) AND COALESCE(l.haber,0)=0 AND l.raw_row_json->>'movement'='debe_deudor_socio_amex' AND l.raw_row_json->>'direct_item_id'=i.id::text AND l.raw_row_json->>'expense_id'=i.imported_expense_id::text AND l.raw_row_json->>'account_treatment_decision_id'=d.id::text AND l.raw_row_json->>'treatment'='partner_receivable' AND l.raw_row_json->>'no_cfdi_decision_id' IS NOT DISTINCT FROM ci.no_cfdi_decision_id::text)
 OR NOT EXISTS(SELECT 1 FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.cuenta_contable_id=c.liability_account_id AND l.haber::numeric=abs(i.raw_signed_amount) AND COALESCE(l.debe,0)=0 AND l.raw_row_json->>'movement'='haber_amex' AND l.raw_row_json->>'direct_item_id'=i.id::text AND l.raw_row_json->>'expense_id'=i.imported_expense_id::text AND l.raw_row_json->>'account_treatment_decision_id'=d.id::text AND l.raw_row_json->>'treatment'='partner_receivable' AND l.raw_row_json->>'no_cfdi_decision_id' IS NOT DISTINCT FROM ci.no_cfdi_decision_id::text)
 OR EXISTS(
  (SELECT f->>'cuenta_contable_id',f->>'cuenta_codigo',f->>'concepto',COALESCE((f->>'debe')::numeric,0),COALESCE((f->>'haber')::numeric,0),f->'raw_row_json' FROM jsonb_array_elements(ci.snapshot_json->'journal_lines') f EXCEPT ALL SELECT l.cuenta_contable_id::text,l.cuenta_codigo,l.concepto,COALESCE(l.debe,0)::numeric,COALESCE(l.haber,0)::numeric,l.raw_row_json FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'direct_item_id'=i.id::text)
  UNION ALL
  (SELECT l.cuenta_contable_id::text,l.cuenta_codigo,l.concepto,COALESCE(l.debe,0)::numeric,COALESCE(l.haber,0)::numeric,l.raw_row_json FROM accounting_poliza_lines l WHERE l.poliza_id=cut.accounting_poliza_id AND l.raw_row_json->>'direct_item_id'=i.id::text EXCEPT ALL SELECT f->>'cuenta_contable_id',f->>'cuenta_codigo',f->>'concepto',COALESCE((f->>'debe')::numeric,0),COALESCE((f->>'haber')::numeric,0),f->'raw_row_json' FROM jsonb_array_elements(ci.snapshot_json->'journal_lines') f)
 )
 OR EXISTS(
  (SELECT f FROM jsonb_array_elements(ci.snapshot_json->'journal_lines') f EXCEPT ALL SELECT f FROM jsonb_array_elements(cut.snapshot_json->'lines') f WHERE f->'raw_row_json'->>'direct_item_id'=i.id::text)
  UNION ALL
  (SELECT f FROM jsonb_array_elements(cut.snapshot_json->'lines') f WHERE f->'raw_row_json'->>'direct_item_id'=i.id::text EXCEPT ALL SELECT f FROM jsonb_array_elements(ci.snapshot_json->'journal_lines') f)
 )
 OR (ci.no_cfdi_decision_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM amex_direct_no_cfdi_decisions nd WHERE nd.id=ci.no_cfdi_decision_id AND nd.id=(SELECT no_cfdi_decision_id FROM amex_direct_items WHERE id=i.id) AND nd.action='declare' AND nd.treatment='full_amount_partner_receivable_no_tax' AND nd.economic_source_hash=amex_direct_no_cfdi_source_hash(i.id) AND ci.snapshot_json->>'no_cfdi_decision_id'=nd.id::text AND COALESCE(ci.snapshot_json->'no_cfdi_decision' @> jsonb_build_object('id',nd.id::text,'action',nd.action::text,'reason',nd.reason::text,'treatment',nd.treatment::text,'actor_id',nd.actor_id::text,'revision',nd.revision::text,'economic_source_hash',nd.economic_source_hash::text,'context_id',nd.context_id::text,'context_version',nd.context_version::text,'imported_expense_id',nd.imported_expense_id::text),false) AND (ci.snapshot_json->'no_cfdi_decision'->>'created_at')::timestamptz=nd.created_at))
 )) THEN RAISE EXCEPTION 'Partner cut requires exact full debtor/card movements and complete typed source proof' USING ERRCODE='23514'; END IF;
 PERFORM amex_direct_no_cfdi_assert(ci.direct_item_id) FROM amex_direct_cut_items ci WHERE ci.cut_id=cut.id AND ci.coverage_kind='partner_receivable' AND ci.no_cfdi_decision_id IS NOT NULL;
 RETURN NULL;
END $$;
COMMIT;
