-- Candidate selective legacy boundary. No operational writer/key is seeded.
-- Existing v1 authority remains v1; no legacy entrypoint authorizes v2 rows.
BEGIN;
SET LOCAL search_path=pg_catalog;
DO $preflight$
DECLARE obj record; owner_id oid;
BEGIN
  SELECT oid INTO owner_id FROM pg_catalog.pg_roles
  WHERE rolname='samchat_ctt_staff_owner' AND NOT rolcanlogin AND NOT rolsuper
    AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls;
  IF owner_id IS NULL OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id)
    OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE')
    OR pg_catalog.pg_has_role('devnous_user',(SELECT datdba FROM pg_catalog.pg_database WHERE datname=current_database()),'MEMBER')
    OR pg_catalog.pg_has_role('devnous_user',(SELECT nspowner FROM pg_catalog.pg_namespace WHERE nspname='public'),'MEMBER')
    OR pg_catalog.current_setting('max_prepared_transactions')::int<>0
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='devnous_user' AND (rolsuper OR rolcreaterole OR rolreplication OR rolbypassrls))
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname='ctt_staff_private' AND nspowner=owner_id)
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname IN ('pg_read_all_data','pg_write_all_data','pg_read_server_files','pg_write_server_files','pg_execute_server_program') AND pg_catalog.pg_has_role('devnous_user',r.oid,'MEMBER'))
    OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_anchors)
  THEN RAISE EXCEPTION 'CTT legacy boundary prerequisites invalid'; END IF;
  FOR obj IN SELECT * FROM (VALUES
    ('capture_attempt_anchors'),('capture_payloads'),('capture_results'),
    ('effect_keys'),('effect_writers'),('review_challenges'),
    ('review_challenge_consumptions'),('signed_assertion_guards')
  ) t(name) LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c
      WHERE c.oid=pg_catalog.to_regclass('ctt_staff_private.'||obj.name)
        AND c.relowner=owner_id AND NOT c.relrowsecurity
        AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a WHERE a.grantee<>c.relowner)
        AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL)) THEN
      RAISE EXCEPTION 'CTT private dependency invalid'; END IF;
  END LOOP;
  FOR obj IN SELECT * FROM (VALUES
    ('capture_attempt_anchors','ctt_capture_anchor_validate'),
    ('capture_attempt_anchors','ctt_capture_anchor_immutable'),
    ('capture_payloads','ctt_capture_payload_immutable'),
    ('review_challenge_consumptions','ctt_review_consumption_commit_guard'),
    ('signed_assertion_guards','ctt_assertion_guard_initial'),
    ('signed_assertion_guards','ctt_assertion_guard_commit')
  ) t(tab,trig) LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
      WHERE t.tgrelid=pg_catalog.to_regclass('ctt_staff_private.'||obj.tab)
        AND t.tgname=obj.trig AND t.tgenabled IN ('O','A') AND p.proowner=owner_id
        AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
        AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner)) THEN
      RAISE EXCEPTION 'CTT private guard invalid'; END IF;
  END LOOP;
  FOR obj IN SELECT * FROM (VALUES ('copa_telmex_teams'),('copa_telmex_players'),('copa_telmex_registration_review_sessions'),('copa_telmex_registration_review_drafts'),('copa_telmex_registration_review_assets'),('copa_telmex_registration_ocr_runs'),('copa_telmex_registration_ocr_field_diffs'),('copa_telmex_registration_ocr_reprocess_decisions'),('copa_telmex_registration_page_append_attempts'),('copa_telmex_registration_page_append_decisions'),('copa_telmex_registration_human_field_edit_proposals'),('copa_telmex_registration_human_field_approvals'),('copa_telmex_registration_human_field_edit_decisions'),('copa_telmex_registration_human_field_edit_executions'),('copa_telmex_registration_human_field_approval_consumptions'),('copa_telmex_registration_postcommit_mutation_proposals'),('copa_telmex_registration_postcommit_mutation_decisions'),('copa_telmex_registration_postcommit_mutation_executions'),('copa_telmex_registration_postcommit_entity_versions')) v(name) LOOP
    IF pg_catalog.to_regclass('public.'||obj.name) IS NULL OR EXISTS(
      SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid=pg_catalog.to_regclass('public.'||obj.name)
      AND attnum>0 AND NOT attisdropped AND attacl IS NOT NULL
    ) THEN RAISE EXCEPTION 'CTT legacy table prerequisites invalid'; END IF;
    IF EXISTS(SELECT 1 FROM pg_catalog.pg_class c
      CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
      WHERE c.oid=pg_catalog.to_regclass('public.'||obj.name) AND a.grantee NOT IN (0,c.relowner,(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='devnous_user'))
        AND a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE','TRIGGER')) THEN
      RAISE EXCEPTION 'CTT unreviewed legacy writer grant'; END IF;
    EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE',obj.name);
  END LOOP;
  FOR obj IN SELECT * FROM (VALUES ('public.regs07_canonical_jsonb(jsonb)','4d811ef8ca4f0d7b063717e60f75338f'),('public.regs07_team_snapshot(public.copa_telmex_teams)','175de29833d0de6f806f68ac80c8ec01'),('public.regs07_player_snapshot(public.copa_telmex_players)','021367786216c130baf11f5da9ddc20e'),('public.regs07_snapshot_hash(jsonb)','56a68b03366f9812dfa25b22473563db'),('public.enforce_registration_human_edit_atomicity()','2d540a3967cadaab0f6cfa3f8867de1d'),('public.enforce_registration_review_draft_append_only()','14b06045f320e1272e8724c5843077b8'),('public.enforce_regs07_execution_atomicity()','fa5052cd61855061a343c932964fa723'),('public.guard_registration_review_asset_immutability()','15bc30f7ddfe00b43ab15698e612ae85'),('public.regs07_append_committed_version()','68533d630dd721c0b991559bf8460f7a'),('public.regs07_append_genesis()','6fe6e6fe0a7099800028c9bc7b7bd1c9'),('public.regs07_guard_committed_mutation()','4326ae2f559d4b09f1d8311e5457365b'),('public.regs07_initialize_player()','952adc71ec4aabd91d231e6a23505066'),('public.regs07_initialize_team()','060adbe40968796fd0b81bf77437fde3'),('public.reject_registration_human_field_authority_mutation()','012edcd0bdcbdd6d0a74cb40bf89843a'),('public.reject_registration_ocr_evidence_mutation()','15558be641bc584a275692a3639e2b77'),('public.reject_registration_page_composition_mutation()','79d346d4395b4d6c14963bab22fa8c99'),('public.reject_regs07_authority_mutation()','a62c3ba0aa43cb8551400a7f5e0091cc')) v(signature,source_hash) LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc WHERE oid=pg_catalog.to_regprocedure(obj.signature)
      AND md5(prosrc)=obj.source_hash AND prokind='f' AND NOT prosecdef AND proconfig IS NULL) THEN
      RAISE EXCEPTION 'CTT legacy function source differs: %',obj.signature;
    END IF;
  END LOOP;
END
$preflight$;

DO $catalogue$
DECLARE obj record; actual text;
BEGIN
  -- Catalog ordering is portable; application/data collation remains unchanged.
  FOR obj IN SELECT * FROM (VALUES ('copa_telmex_players','17df17682b4743ef8250de9485e867c4'),('copa_telmex_registration_human_field_approval_consumptions','832f66d6b94b392b434e2348ab72a00d'),('copa_telmex_registration_human_field_approvals','a37ab71e70483b0d788af97624686727'),('copa_telmex_registration_human_field_edit_decisions','13f6f06d22d67a4f9a5782e11b3160cd'),('copa_telmex_registration_human_field_edit_executions','8ad71df2bb22ad0ad591e78ed3eb0e54'),('copa_telmex_registration_human_field_edit_proposals','0b1acb027933a63813be225d7dbe4d98'),('copa_telmex_registration_ocr_field_diffs','a3420d12ce2228d757a4f4824dd0e31d'),('copa_telmex_registration_ocr_reprocess_decisions','016a9b5ac6e4fadadf2215e518b29f65'),('copa_telmex_registration_ocr_runs','3bf977f5ea115594208598025a8dadd6'),('copa_telmex_registration_page_append_attempts','cc6ac72c543a0ff2996a6da896887d5c'),('copa_telmex_registration_page_append_decisions','98ec235f81382e7ee8740d1b8727d547'),('copa_telmex_registration_postcommit_entity_versions','1bc3a185c733e611e82cdaa94807893b'),('copa_telmex_registration_postcommit_mutation_decisions','b8da1720538490406edcdd7410d1a8c1'),('copa_telmex_registration_postcommit_mutation_executions','692f21459722e8b16b97f7abe40a66db'),('copa_telmex_registration_postcommit_mutation_proposals','633370de1dc573c780f92db0f1e19f05'),('copa_telmex_registration_review_assets','c939e9ced102f9070d01856ccc99c1e1'),('copa_telmex_registration_review_drafts','8cce55d3f98aea67b4149e04e4aeef58'),('copa_telmex_registration_review_sessions','29c6693cd07b713f47dc84a3a190e10a'),('copa_telmex_teams','304f7b8fd78398212fb42e03f582d296')) expected(name,digest) LOOP
    SELECT md5(jsonb_build_object(
      'kind',c.relkind,'partition',c.relispartition,'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,
      'attributes',(SELECT jsonb_agg(jsonb_build_array(a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attgenerated,a.attidentity,a.attcollation::regcollation::text,coalesce(pg_get_expr(d.adbin,d.adrelid),'')) ORDER BY a.attnum)
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
      'constraints',(SELECT jsonb_agg(jsonb_build_array(k.conname,k.contype,pg_get_constraintdef(k.oid)) ORDER BY k.conname::text COLLATE "C") FROM pg_constraint k WHERE k.conrelid=c.oid),
      'indexes',(SELECT jsonb_agg(pg_get_indexdef(i.indexrelid) ORDER BY i.indexrelid::regclass::text COLLATE "C") FROM pg_index i WHERE i.indrelid=c.oid)
    )::text) INTO actual FROM pg_class c WHERE c.oid=to_regclass('public.'||obj.name);
    IF actual IS DISTINCT FROM obj.digest
      OR EXISTS(SELECT 1 FROM pg_rewrite WHERE ev_class=to_regclass('public.'||obj.name))
      OR EXISTS(SELECT 1 FROM pg_policy WHERE polrelid=to_regclass('public.'||obj.name))
      OR EXISTS(SELECT 1 FROM pg_inherits WHERE inhrelid=to_regclass('public.'||obj.name) OR inhparent=to_regclass('public.'||obj.name)) THEN
      RAISE EXCEPTION 'CTT legacy table definition differs: %',obj.name;
    END IF;
  END LOOP;
  FOR obj IN SELECT * FROM (VALUES ('copa_telmex_players','trg_regs07_player_genesis','bb94cb54802fb08d5301b86316c242be'),('copa_telmex_players','trg_regs07_player_guard','2214266fa58537609b64d1599aac080f'),('copa_telmex_players','trg_regs07_player_initialize','7df8327c2a2befd8c57a916cd0aa28c7'),('copa_telmex_players','trg_regs07_player_version','f96f0bdc26fc7ebad757d0f6e8e00f5d'),('copa_telmex_registration_human_field_approval_consumptions','trg_registration_human_approval_consumption_immutable','7ee87c6caefb7bb3e7da24851f464980'),('copa_telmex_registration_human_field_approvals','trg_registration_human_approval_immutable','d4b0029d8406987dacea3295e3b43109'),('copa_telmex_registration_human_field_edit_decisions','trg_registration_human_edit_decision_immutable','261bf943785a60f50e36f41804dbccb1'),('copa_telmex_registration_human_field_edit_executions','trg_registration_human_edit_execution_atomicity','ce5e02ec35ae24de55fda9e4323b7010'),('copa_telmex_registration_human_field_edit_executions','trg_registration_human_edit_execution_immutable','f0c4c367c344788eb91956a6c9e8cb7b'),('copa_telmex_registration_human_field_edit_proposals','trg_registration_human_edit_proposal_immutable','db41a1d17add82e1dffa258ad99d2803'),('copa_telmex_registration_ocr_field_diffs','trg_registration_ocr_diff_immutable','248a64b7dd39ba4456681613ee1bf3b0'),('copa_telmex_registration_ocr_reprocess_decisions','trg_registration_ocr_decision_immutable','47ad4cfc513bc0eb1667c334c8e88176'),('copa_telmex_registration_ocr_runs','trg_registration_ocr_run_immutable','e5a71f9b6a7673f19e629f695b393839'),('copa_telmex_registration_page_append_attempts','trg_registration_page_append_attempt_immutable','44ed2a07dca078e5bb3a48356195b5e2'),('copa_telmex_registration_page_append_decisions','trg_registration_page_append_decision_immutable','88375e0eed8f89428cbb17ec84584b97'),('copa_telmex_registration_postcommit_entity_versions','trg_regs07_version_immutable','210e307528b55b4cb56adbf7839de988'),('copa_telmex_registration_postcommit_mutation_decisions','trg_regs07_decision_immutable','74a3ea9bbc6bc39e58628485a4973c21'),('copa_telmex_registration_postcommit_mutation_executions','trg_regs07_execution_atomicity','57a55a9a7af695fb234e59a365505b36'),('copa_telmex_registration_postcommit_mutation_executions','trg_regs07_execution_immutable','706bdaf2413c310ec4f954ece04886b4'),('copa_telmex_registration_postcommit_mutation_proposals','trg_regs07_proposal_immutable','99bdd35cf63388a4551b70cc41f437c7'),('copa_telmex_registration_review_assets','trg_registration_review_asset_immutable','373439e08ac3310bd46850fe6a65964c'),('copa_telmex_registration_review_drafts','trg_registration_human_edit_draft_atomicity','f35a4075f1a7f4173c1e3665d924152f'),('copa_telmex_registration_review_drafts','trg_registration_review_draft_append_only','74b3f1cb04d68eab3a401031b830c132'),('copa_telmex_teams','trg_regs07_team_genesis','294e722d98a8867e65ba27acb8abe39f'),('copa_telmex_teams','trg_regs07_team_guard','76e51c59ef4b099b32157aef70213966'),('copa_telmex_teams','trg_regs07_team_initialize','2e3f549a0dfd6d850837376b7d4ef2df'),('copa_telmex_teams','trg_regs07_team_version','03384df2e4687af4570510cc2cb74c3c')) expected(tab,name,digest) LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=to_regclass('public.'||obj.tab)
      AND t.tgname=obj.name AND NOT t.tgisinternal AND t.tgenabled IN ('O','A')
      AND md5(pg_get_triggerdef(t.oid))=obj.digest) THEN
      RAISE EXCEPTION 'CTT legacy original trigger differs: %',obj.name;
    END IF;
  END LOOP;
  IF (SELECT count(*) FROM pg_trigger t WHERE NOT t.tgisinternal AND t.tgrelid IN (
    SELECT to_regclass('public.'||name)::oid FROM (VALUES ('copa_telmex_players','17df17682b4743ef8250de9485e867c4'),('copa_telmex_registration_human_field_approval_consumptions','832f66d6b94b392b434e2348ab72a00d'),('copa_telmex_registration_human_field_approvals','a37ab71e70483b0d788af97624686727'),('copa_telmex_registration_human_field_edit_decisions','13f6f06d22d67a4f9a5782e11b3160cd'),('copa_telmex_registration_human_field_edit_executions','8ad71df2bb22ad0ad591e78ed3eb0e54'),('copa_telmex_registration_human_field_edit_proposals','0b1acb027933a63813be225d7dbe4d98'),('copa_telmex_registration_ocr_field_diffs','a3420d12ce2228d757a4f4824dd0e31d'),('copa_telmex_registration_ocr_reprocess_decisions','016a9b5ac6e4fadadf2215e518b29f65'),('copa_telmex_registration_ocr_runs','3bf977f5ea115594208598025a8dadd6'),('copa_telmex_registration_page_append_attempts','cc6ac72c543a0ff2996a6da896887d5c'),('copa_telmex_registration_page_append_decisions','98ec235f81382e7ee8740d1b8727d547'),('copa_telmex_registration_postcommit_entity_versions','1bc3a185c733e611e82cdaa94807893b'),('copa_telmex_registration_postcommit_mutation_decisions','b8da1720538490406edcdd7410d1a8c1'),('copa_telmex_registration_postcommit_mutation_executions','692f21459722e8b16b97f7abe40a66db'),('copa_telmex_registration_postcommit_mutation_proposals','633370de1dc573c780f92db0f1e19f05'),('copa_telmex_registration_review_assets','c939e9ced102f9070d01856ccc99c1e1'),('copa_telmex_registration_review_drafts','8cce55d3f98aea67b4149e04e4aeef58'),('copa_telmex_registration_review_sessions','29c6693cd07b713f47dc84a3a190e10a'),('copa_telmex_teams','304f7b8fd78398212fb42e03f582d296')) expected(name,digest)))<>27 THEN
    RAISE EXCEPTION 'CTT legacy unexpected trigger'; END IF;
END
$catalogue$;
CREATE TABLE ctt_staff_private.legacy_boundary_baseline(
  object_kind text NOT NULL CHECK(object_kind IN ('table','function')),
  object_name text NOT NULL,
  original_owner name NOT NULL,
  original_definition text,
  original_acl jsonb NOT NULL,
  PRIMARY KEY(object_kind,object_name)
);
ALTER TABLE ctt_staff_private.legacy_boundary_baseline OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.legacy_boundary_baseline FROM PUBLIC,devnous_user;
DO $save$
DECLARE obj record;
BEGIN
  FOR obj IN SELECT * FROM (VALUES ('copa_telmex_teams'),('copa_telmex_players'),('copa_telmex_registration_review_sessions'),('copa_telmex_registration_review_drafts'),('copa_telmex_registration_review_assets'),('copa_telmex_registration_ocr_runs'),('copa_telmex_registration_ocr_field_diffs'),('copa_telmex_registration_ocr_reprocess_decisions'),('copa_telmex_registration_page_append_attempts'),('copa_telmex_registration_page_append_decisions'),('copa_telmex_registration_human_field_edit_proposals'),('copa_telmex_registration_human_field_approvals'),('copa_telmex_registration_human_field_edit_decisions'),('copa_telmex_registration_human_field_edit_executions'),('copa_telmex_registration_human_field_approval_consumptions'),('copa_telmex_registration_postcommit_mutation_proposals'),('copa_telmex_registration_postcommit_mutation_decisions'),('copa_telmex_registration_postcommit_mutation_executions'),('copa_telmex_registration_postcommit_entity_versions')) v(name) LOOP
    INSERT INTO ctt_staff_private.legacy_boundary_baseline
    SELECT 'table',obj.name,r.rolname,NULL,coalesce((SELECT jsonb_agg(jsonb_build_object(
      'role',CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,
      'privilege',a.privilege_type,'grantable',a.is_grantable))
      FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a),'[]'::jsonb)
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
    WHERE c.oid=pg_catalog.to_regclass('public.'||obj.name);
  END LOOP;
  FOR obj IN SELECT * FROM (VALUES ('public.regs07_canonical_jsonb(jsonb)','4d811ef8ca4f0d7b063717e60f75338f'),('public.regs07_team_snapshot(public.copa_telmex_teams)','175de29833d0de6f806f68ac80c8ec01'),('public.regs07_player_snapshot(public.copa_telmex_players)','021367786216c130baf11f5da9ddc20e'),('public.regs07_snapshot_hash(jsonb)','56a68b03366f9812dfa25b22473563db'),('public.enforce_registration_human_edit_atomicity()','2d540a3967cadaab0f6cfa3f8867de1d'),('public.enforce_registration_review_draft_append_only()','14b06045f320e1272e8724c5843077b8'),('public.enforce_regs07_execution_atomicity()','fa5052cd61855061a343c932964fa723'),('public.guard_registration_review_asset_immutability()','15bc30f7ddfe00b43ab15698e612ae85'),('public.regs07_append_committed_version()','68533d630dd721c0b991559bf8460f7a'),('public.regs07_append_genesis()','6fe6e6fe0a7099800028c9bc7b7bd1c9'),('public.regs07_guard_committed_mutation()','4326ae2f559d4b09f1d8311e5457365b'),('public.regs07_initialize_player()','952adc71ec4aabd91d231e6a23505066'),('public.regs07_initialize_team()','060adbe40968796fd0b81bf77437fde3'),('public.reject_registration_human_field_authority_mutation()','012edcd0bdcbdd6d0a74cb40bf89843a'),('public.reject_registration_ocr_evidence_mutation()','15558be641bc584a275692a3639e2b77'),('public.reject_registration_page_composition_mutation()','79d346d4395b4d6c14963bab22fa8c99'),('public.reject_regs07_authority_mutation()','a62c3ba0aa43cb8551400a7f5e0091cc')) v(signature,source_hash) LOOP
    INSERT INTO ctt_staff_private.legacy_boundary_baseline
    SELECT 'function',obj.signature,r.rolname,pg_catalog.pg_get_functiondef(p.oid),coalesce((
      SELECT jsonb_agg(jsonb_build_object('role',CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,
      'privilege',a.privilege_type,'grantable',a.is_grantable))
      FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a),'[]'::jsonb)
    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
    WHERE p.oid=pg_catalog.to_regprocedure(obj.signature);
  END LOOP;
END
$save$;
CREATE TABLE ctt_staff_private.legacy_v2_reservations(
  reserved_id uuid NOT NULL,
  attempt_id uuid NOT NULL REFERENCES ctt_staff_private.capture_attempt_anchors(attempt_id) ON DELETE RESTRICT,
  PRIMARY KEY(reserved_id,attempt_id)
);
ALTER TABLE ctt_staff_private.legacy_v2_reservations OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.legacy_v2_reservations FROM PUBLIC,devnous_user;
CREATE TRIGGER ctt_legacy_reservation_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.legacy_v2_reservations
FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_legacy_reservation_no_truncate BEFORE TRUNCATE ON ctt_staff_private.legacy_v2_reservations
FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE OR REPLACE FUNCTION public.regs07_canonical_jsonb(value jsonb)
 RETURNS text
 LANGUAGE plpgsql
 IMMUTABLE PARALLEL SAFE STRICT
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
DECLARE
    rendered text;
BEGIN
    CASE jsonb_typeof(value)
        WHEN 'object' THEN
            SELECT '{' || COALESCE(
                string_agg(
                    to_jsonb(entry.key)::text || ':' ||
                        public.regs07_canonical_jsonb(entry.value),
                    ',' ORDER BY convert_to(entry.key, 'UTF8')
                ),
                ''
            ) || '}'
            INTO rendered
            FROM jsonb_each(value) AS entry;
            RETURN rendered;
        WHEN 'array' THEN
            SELECT '[' || COALESCE(
                string_agg(
                    public.regs07_canonical_jsonb(item.value),
                    ',' ORDER BY item.ordinality
                ),
                ''
            ) || ']'
            INTO rendered
            FROM jsonb_array_elements(value)
                WITH ORDINALITY AS item(value, ordinality);
            RETURN rendered;
        ELSE
            RETURN value::text;
    END CASE;
END
$function$
;
ALTER FUNCTION public.regs07_canonical_jsonb(jsonb) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.regs07_canonical_jsonb(jsonb) FROM PUBLIC,devnous_user;
GRANT EXECUTE ON FUNCTION public.regs07_canonical_jsonb(jsonb) TO devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_team_snapshot(row_value public.copa_telmex_teams)
 RETURNS jsonb
 LANGUAGE sql
 IMMUTABLE
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
    SELECT jsonb_build_object(
        'entity_type', 'TEAM',
        'id', row_value.id::text,
        'name', row_value.name,
        'tournament_slug', row_value.tournament_slug,
        'gender', row_value.gender,
        'category', row_value.category,
        'league', row_value.league,
        'league_phone', row_value.league_phone,
        'league_address', row_value.league_address,
        'representative_name', row_value.representative_name,
        'contact_email', row_value.contact_email,
        'contact_phone', row_value.contact_phone,
        'state', row_value.state,
        'municipality', row_value.municipality,
        'roster_image_path', row_value.roster_image_path,
        'telegram_chat_id', row_value.telegram_chat_id,
        'telegram_user_id', row_value.telegram_user_id
    )
$function$
;
ALTER FUNCTION public.regs07_team_snapshot(public.copa_telmex_teams) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.regs07_team_snapshot(public.copa_telmex_teams) FROM PUBLIC,devnous_user;
GRANT EXECUTE ON FUNCTION public.regs07_team_snapshot(public.copa_telmex_teams) TO devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_player_snapshot(row_value public.copa_telmex_players)
 RETURNS jsonb
 LANGUAGE sql
 IMMUTABLE
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
    SELECT jsonb_build_object(
        'entity_type', 'PLAYER',
        'id', row_value.id::text,
        'team_id', row_value.team_id::text,
        'first_name', row_value.first_name,
        'last_name', row_value.last_name,
        'birth_date', CASE
            WHEN row_value.birth_date IS NULL THEN NULL
            ELSE to_char(row_value.birth_date, 'YYYY-MM-DD')
        END,
        'curp', row_value.curp,
        'email', row_value.email,
        'photo_path', row_value.photo_path,
        'photo_data_hash', 'sha256:' || encode(
            public.digest(convert_to(coalesce(row_value.photo_data, ''), 'UTF8'), 'sha256'),
            'hex'
        ),
        'photo_sha256', row_value.photo_sha256,
        'photo_ahash', row_value.photo_ahash,
        'curp_valid', coalesce(row_value.curp_valid, false),
        'curp_validation_date', CASE
            WHEN row_value.curp_validation_date IS NULL THEN NULL
            ELSE to_char(
                row_value.curp_validation_date,
                'YYYY-MM-DD"T"HH24:MI:SS.US'
            )
        END,
        'curp_validation_errors', row_value.curp_validation_errors,
        'ocr_confidence', row_value.ocr_confidence,
        'needs_review', coalesce(row_value.needs_review, false),
        'verified_by_human', coalesce(row_value.verified_by_human, false),
        'verification_notes', row_value.verification_notes,
        'roster_index', row_value.roster_index,
        'governance_state', row_value.governance_state,
        'governance_draft_id', row_value.governance_draft_id,
        'governance_draft_version', row_value.governance_draft_version,
        'governance_decision_id', row_value.governance_decision_id,
        'roster_draft_binding', row_value.roster_draft_binding,
        'preauthorization_receipt_id',
            row_value.preauthorization_receipt_id,
        'finality_receipt_id', row_value.finality_receipt_id
    )
$function$
;
ALTER FUNCTION public.regs07_player_snapshot(public.copa_telmex_players) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.regs07_player_snapshot(public.copa_telmex_players) FROM PUBLIC,devnous_user;
GRANT EXECUTE ON FUNCTION public.regs07_player_snapshot(public.copa_telmex_players) TO devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_snapshot_hash(snapshot jsonb)
 RETURNS character varying
 LANGUAGE sql
 IMMUTABLE
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
    SELECT 'sha256:' || encode(
        public.digest(
            convert_to(public.regs07_canonical_jsonb(snapshot), 'UTF8'),
            'sha256'
        ),
        'hex'
    )
$function$
;
ALTER FUNCTION public.regs07_snapshot_hash(jsonb) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.regs07_snapshot_hash(jsonb) FROM PUBLIC,devnous_user;
GRANT EXECUTE ON FUNCTION public.regs07_snapshot_hash(jsonb) TO devnous_user;
CREATE OR REPLACE FUNCTION public.enforce_registration_human_edit_atomicity()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
DECLARE
    draft_row public.copa_telmex_registration_review_drafts%ROWTYPE;
    execution_row public.copa_telmex_registration_human_field_edit_executions%ROWTYPE;
    proposal_row public.copa_telmex_registration_human_field_edit_proposals%ROWTYPE;
    decision_row public.copa_telmex_registration_human_field_edit_decisions%ROWTYPE;
    approval_count integer;
    consumption_count integer;
BEGIN
    IF TG_TABLE_NAME = 'copa_telmex_registration_review_drafts' THEN
        IF NEW.mutation_type <> 'human_field_edit' THEN
            RETURN NEW;
        END IF;
        SELECT *
        INTO execution_row
        FROM public.copa_telmex_registration_human_field_edit_executions
        WHERE successor_draft_id = NEW.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'REG-S05: human field edit successor has no atomic execution';
        END IF;
        IF execution_row.successor_hash IS DISTINCT FROM NEW.content_hash
           OR execution_row.successor_draft_version IS DISTINCT FROM NEW.draft_version
           OR execution_row.parent_decision_id IS DISTINCT FROM NEW.parent_decision_id
           OR execution_row.parent_receipt_id IS DISTINCT FROM NEW.parent_receipt_id THEN
            RAISE EXCEPTION
                'REG-S05: human field edit execution does not bind the exact successor';
        END IF;
    ELSE
        SELECT *
        INTO proposal_row
        FROM public.copa_telmex_registration_human_field_edit_proposals
        WHERE id = NEW.proposal_id;
        SELECT *
        INTO decision_row
        FROM public.copa_telmex_registration_human_field_edit_decisions
        WHERE id = NEW.decision_id;
        IF proposal_row.id IS NULL
           OR decision_row.id IS NULL
           OR decision_row.proposal_id <> NEW.proposal_id
           OR decision_row.decision <> 'AUTHORIZE_FIELD_EDIT_SUCCESSOR'
           OR decision_row.receipt_alg <> 'Ed25519'
           OR decision_row.expires_at <= NEW.executed_at
           OR NEW.parent_decision_id <> decision_row.decision_id
           OR NEW.parent_receipt_id <> decision_row.receipt_id
           OR NEW.successor_draft_id <> proposal_row.proposed_successor_draft_id
           OR NEW.successor_hash <> proposal_row.proposed_successor_hash THEN
            RAISE EXCEPTION
                'REG-S05: execution is not bound to an active authorized proposal';
        END IF;
        SELECT *
        INTO draft_row
        FROM public.copa_telmex_registration_review_drafts
        WHERE id = NEW.successor_draft_id;
        IF NOT FOUND
           OR draft_row.mutation_type <> 'human_field_edit'
           OR draft_row.content_hash IS DISTINCT FROM NEW.successor_hash
           OR draft_row.draft_version IS DISTINCT FROM NEW.successor_draft_version
           OR draft_row.predecessor_draft_id IS DISTINCT FROM proposal_row.base_draft_id
           OR draft_row.predecessor_content_hash IS DISTINCT FROM proposal_row.base_draft_hash
           OR draft_row.draft_version IS DISTINCT FROM proposal_row.base_draft_version + 1
           OR draft_row.mutation_operation_id IS DISTINCT FROM proposal_row.operation_id
           OR draft_row.parent_decision_id IS DISTINCT FROM NEW.parent_decision_id
           OR draft_row.parent_receipt_id IS DISTINCT FROM NEW.parent_receipt_id THEN
            RAISE EXCEPTION
                'REG-S05: approval execution has no exact REG-S02 successor';
        END IF;
        SELECT count(*)
        INTO approval_count
        FROM public.copa_telmex_registration_human_field_approvals
        WHERE proposal_id = NEW.proposal_id;
        SELECT count(*)
        INTO consumption_count
        FROM public.copa_telmex_registration_human_field_approval_consumptions
        WHERE execution_id = NEW.id;
        IF approval_count = 0 OR consumption_count <> approval_count THEN
            RAISE EXCEPTION
                'REG-S05: approval set was not consumed exactly once with successor';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM public.copa_telmex_registration_human_field_approval_consumptions AS c
            JOIN public.copa_telmex_registration_human_field_approvals AS a
              ON a.id = c.approval_id
            WHERE c.execution_id = NEW.id
              AND (
                  a.proposal_id <> NEW.proposal_id
                  OR c.consumed_by_principal_id <> a.approver_principal_id
                  OR c.consumed_by_draft_version <> NEW.successor_draft_version
                  OR c.consumed_by_successor_hash <> NEW.successor_hash
              )
        ) THEN
            RAISE EXCEPTION
                'REG-S05: approval consumption binding does not match execution';
        END IF;
    END IF;
    RETURN NEW;
END
$function$
;
ALTER FUNCTION public.enforce_registration_human_edit_atomicity() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.enforce_registration_human_edit_atomicity() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.enforce_registration_review_draft_append_only()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
DECLARE
    previous_row public.copa_telmex_registration_review_drafts%ROWTYPE;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        RAISE EXCEPTION
            'REG-S02: registration review drafts are immutable; append a successor';
    END IF;

    PERFORM 1
    FROM public.copa_telmex_registration_review_sessions
    WHERE id = NEW.session_id
    FOR UPDATE;

    SELECT *
    INTO previous_row
    FROM public.copa_telmex_registration_review_drafts
    WHERE session_id = NEW.session_id
    ORDER BY draft_version DESC, created_at DESC
    LIMIT 1;

    IF NOT FOUND THEN
        IF NEW.draft_version <> 1
           OR NEW.predecessor_draft_id IS NOT NULL
           OR NEW.predecessor_content_hash IS NOT NULL THEN
            RAISE EXCEPTION
                'REG-S02: initial draft must be version 1 without predecessor';
        END IF;
    ELSIF NEW.draft_version <> previous_row.draft_version + 1
       OR NEW.predecessor_draft_id IS DISTINCT FROM previous_row.id
       OR NEW.predecessor_content_hash IS DISTINCT FROM previous_row.content_hash THEN
        RAISE EXCEPTION
            'REG-S02: stale or discontinuous draft successor';
    END IF;

    RETURN NEW;
END
$function$
;
ALTER FUNCTION public.enforce_registration_review_draft_append_only() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.enforce_registration_review_draft_append_only() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.enforce_regs07_execution_atomicity()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
DECLARE
    proposal_row
        public.copa_telmex_registration_postcommit_mutation_proposals%ROWTYPE;
    version_count integer;
    current_revision integer;
    current_hash text;
BEGIN
    SELECT * INTO proposal_row
    FROM public.copa_telmex_registration_postcommit_mutation_proposals
    WHERE id = NEW.proposal_id;
    SELECT count(*) INTO version_count
    FROM public.copa_telmex_registration_postcommit_entity_versions
    WHERE execution_id = NEW.id
      AND entity_type = proposal_row.entity_type
      AND entity_id = proposal_row.entity_id
      AND revision = proposal_row.proposed_revision
      AND snapshot_hash = proposal_row.proposed_snapshot_hash;
    IF proposal_row.entity_type = 'TEAM' THEN
        SELECT postcommit_revision, postcommit_snapshot_hash
        INTO current_revision, current_hash
        FROM public.copa_telmex_teams
        WHERE id = proposal_row.entity_id;
    ELSE
        SELECT postcommit_revision, postcommit_snapshot_hash
        INTO current_revision, current_hash
        FROM public.copa_telmex_players
        WHERE id = proposal_row.entity_id;
    END IF;
    IF version_count <> 1
       OR current_revision <> proposal_row.proposed_revision
       OR current_hash <> proposal_row.proposed_snapshot_hash THEN
        RAISE EXCEPTION
            'REG-S07: execution has no exact atomic committed-state version';
    END IF;
    RETURN NEW;
END
$function$
;
ALTER FUNCTION public.enforce_regs07_execution_atomicity() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.enforce_regs07_execution_atomicity() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.guard_registration_review_asset_immutability()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.admitted_draft_id IS NULL
           AND NEW.admitted_draft_id IS NOT NULL
           AND NEW.source_base_draft_id IS NOT NULL
           AND NEW.source_base_content_hash IS NOT NULL
           AND NEW.source_ocr_run_ref IS NOT NULL
           AND NEW.admission_operation_id IS NOT NULL
           AND NEW.admission_decision_id IS NOT NULL
           AND NEW.admission_receipt_id IS NOT NULL
           AND OLD.id = NEW.id
           AND OLD.session_id = NEW.session_id
           AND OLD.page_index = NEW.page_index
           AND OLD.image_path = NEW.image_path
           AND OLD.sha256 IS NOT DISTINCT FROM NEW.sha256
           AND OLD.width IS NOT DISTINCT FROM NEW.width
           AND OLD.height IS NOT DISTINCT FROM NEW.height
        THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION
            'REG-S04: admitted page assets cannot be replaced or rebound';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.copa_telmex_registration_review_sessions
        WHERE id = OLD.session_id
    ) THEN
        RAISE EXCEPTION
            'REG-S04: admitted page assets cannot be deleted independently';
    END IF;
    RETURN OLD;
END
$function$
;
ALTER FUNCTION public.guard_registration_review_asset_immutability() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.guard_registration_review_asset_immutability() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_append_committed_version()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
DECLARE
    execution_uuid uuid;
    proposal_row
        public.copa_telmex_registration_postcommit_mutation_proposals%ROWTYPE;
    snapshot_value jsonb;
    entity_kind text;
    entity_team_id uuid;
BEGIN
    IF TG_TABLE_NAME = 'copa_telmex_players'
       AND OLD.postcommit_revision = 0
       AND NEW.postcommit_revision = 1 THEN
        INSERT INTO public.copa_telmex_registration_postcommit_entity_versions (
            id, entity_type, entity_id, team_id, revision, snapshot,
            snapshot_hash, predecessor_snapshot_hash, mutation_type
        ) VALUES (
            gen_random_uuid(), 'PLAYER', NEW.id, NEW.team_id, 1,
            public.regs07_player_snapshot(NEW), NEW.postcommit_snapshot_hash,
            NULL, 'GENESIS'
        );
        RETURN NEW;
    END IF;

    execution_uuid := nullif(
        current_setting('samchat.regs07_execution_id', true), ''
    )::uuid;
    SELECT * INTO proposal_row
    FROM public.copa_telmex_registration_postcommit_mutation_proposals
    WHERE id = (
        SELECT proposal_id
        FROM public.copa_telmex_registration_postcommit_mutation_executions
        WHERE id = execution_uuid
    );
    IF TG_TABLE_NAME = 'copa_telmex_teams' THEN
        entity_kind := 'TEAM';
        entity_team_id := NEW.id;
        snapshot_value := public.regs07_team_snapshot(NEW);
    ELSE
        entity_kind := 'PLAYER';
        entity_team_id := NEW.team_id;
        snapshot_value := public.regs07_player_snapshot(NEW);
    END IF;
    INSERT INTO public.copa_telmex_registration_postcommit_entity_versions (
        id, entity_type, entity_id, team_id, revision, snapshot,
        snapshot_hash, predecessor_snapshot_hash, mutation_type, execution_id
    ) VALUES (
        gen_random_uuid(), entity_kind, NEW.id, entity_team_id,
        NEW.postcommit_revision, snapshot_value,
        NEW.postcommit_snapshot_hash, OLD.postcommit_snapshot_hash,
        proposal_row.mutation_type, execution_uuid
    );
    RETURN NEW;
END
$function$
;
ALTER FUNCTION public.regs07_append_committed_version() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.regs07_append_committed_version() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_append_genesis()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
DECLARE
    snapshot_value jsonb;
BEGIN
    IF TG_TABLE_NAME = 'copa_telmex_teams' THEN
        snapshot_value := public.regs07_team_snapshot(NEW);
        INSERT INTO public.copa_telmex_registration_postcommit_entity_versions (
            id, entity_type, entity_id, team_id, revision, snapshot,
            snapshot_hash, predecessor_snapshot_hash, mutation_type
        ) VALUES (
            gen_random_uuid(), 'TEAM', NEW.id, NEW.id, 1, snapshot_value,
            NEW.postcommit_snapshot_hash, NULL, 'GENESIS'
        );
    ELSIF NEW.postcommit_revision = 1 THEN
        snapshot_value := public.regs07_player_snapshot(NEW);
        INSERT INTO public.copa_telmex_registration_postcommit_entity_versions (
            id, entity_type, entity_id, team_id, revision, snapshot,
            snapshot_hash, predecessor_snapshot_hash, mutation_type
        ) VALUES (
            gen_random_uuid(), 'PLAYER', NEW.id, NEW.team_id, 1,
            snapshot_value, NEW.postcommit_snapshot_hash, NULL, 'GENESIS'
        );
    END IF;
    RETURN NEW;
END
$function$
;
ALTER FUNCTION public.regs07_append_genesis() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.regs07_append_genesis() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_guard_committed_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
DECLARE
    execution_uuid uuid;
    proposal_row
        public.copa_telmex_registration_postcommit_mutation_proposals%ROWTYPE;
    decision_row
        public.copa_telmex_registration_postcommit_mutation_decisions%ROWTYPE;
    execution_row
        public.copa_telmex_registration_postcommit_mutation_executions%ROWTYPE;
    old_snapshot jsonb;
    new_snapshot jsonb;
    entity_kind text;
    entity_team_id uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'REG-S07: physical deletion of committed Team/Player evidence is denied';
    END IF;

    IF TG_TABLE_NAME = 'copa_telmex_players' THEN
        IF OLD.governance_state = 'PENDING_FINALITY'
           AND NEW.governance_state = 'ACTIVE'
           AND OLD.postcommit_revision = 0
           AND (
                to_jsonb(OLD) - ARRAY[
                    'governance_state',
                    'finality_receipt_id',
                    'updated_at',
                    'postcommit_revision',
                    'postcommit_snapshot_hash'
                ]
           ) = (
                to_jsonb(NEW) - ARRAY[
                    'governance_state',
                    'finality_receipt_id',
                    'updated_at',
                    'postcommit_revision',
                    'postcommit_snapshot_hash'
                ]
           ) THEN
            NEW.postcommit_revision := 1;
            NEW.postcommit_snapshot_hash :=
                public.regs07_snapshot_hash(public.regs07_player_snapshot(NEW));
            RETURN NEW;
        END IF;
    END IF;

    BEGIN
        execution_uuid := nullif(
            current_setting('samchat.regs07_execution_id', true), ''
        )::uuid;
    EXCEPTION WHEN OTHERS THEN
        execution_uuid := NULL;
    END;
    IF execution_uuid IS NULL THEN
        RAISE EXCEPTION
            'REG-S07: committed Team/Player update lacks execution authority';
    END IF;

    SELECT * INTO execution_row
    FROM public.copa_telmex_registration_postcommit_mutation_executions
    WHERE id = execution_uuid;
    SELECT * INTO proposal_row
    FROM public.copa_telmex_registration_postcommit_mutation_proposals
    WHERE id = execution_row.proposal_id;
    SELECT * INTO decision_row
    FROM public.copa_telmex_registration_postcommit_mutation_decisions
    WHERE id = execution_row.decision_id;

    IF TG_TABLE_NAME = 'copa_telmex_teams' THEN
        entity_kind := 'TEAM';
        entity_team_id := NEW.id;
        old_snapshot := public.regs07_team_snapshot(OLD);
        new_snapshot := public.regs07_team_snapshot(NEW);
    ELSE
        entity_kind := 'PLAYER';
        entity_team_id := NEW.team_id;
        old_snapshot := public.regs07_player_snapshot(OLD);
        new_snapshot := public.regs07_player_snapshot(NEW);
    END IF;

    IF execution_row.id IS NULL
       OR proposal_row.id IS NULL
       OR decision_row.id IS NULL
       OR proposal_row.entity_type <> entity_kind
       OR proposal_row.entity_id <> NEW.id
       OR proposal_row.team_id <> entity_team_id
       OR proposal_row.base_revision <> OLD.postcommit_revision
       OR proposal_row.proposed_revision <> OLD.postcommit_revision + 1
       OR proposal_row.base_snapshot_hash <> OLD.postcommit_snapshot_hash
       OR proposal_row.base_snapshot <> old_snapshot
       OR proposal_row.proposed_snapshot <> new_snapshot
       OR proposal_row.proposed_snapshot_hash <>
            public.regs07_snapshot_hash(new_snapshot)
       OR decision_row.proposal_id <> proposal_row.id
       OR decision_row.decision <> 'AUTHORIZE_POSTCOMMIT_MUTATION'
       OR decision_row.receipt_alg <> 'Ed25519'
       OR decision_row.expires_at <= execution_row.executed_at
       OR execution_row.finality_receipt_alg <> 'Ed25519'
       OR execution_row.finality_event_document->>'decision' <>
            'ATTEST_POSTCOMMIT_MUTATION'
       OR execution_row.finality_event_document->>'proposal_id' <>
            proposal_row.id::text
       OR execution_row.finality_event_document->>'actual_projection_hash' <>
            proposal_row.proposed_snapshot_hash THEN
        RAISE EXCEPTION
            'REG-S07: update is not bound to an exact live double-receipt successor';
    END IF;

    NEW.postcommit_revision := proposal_row.proposed_revision;
    NEW.postcommit_snapshot_hash := proposal_row.proposed_snapshot_hash;
    RETURN NEW;
END
$function$
;
ALTER FUNCTION public.regs07_guard_committed_mutation() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.regs07_guard_committed_mutation() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_initialize_player()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
BEGIN
    NEW.postcommit_revision := CASE
        WHEN NEW.governance_state = 'PENDING_FINALITY' THEN 0
        ELSE 1
    END;
    NEW.postcommit_snapshot_hash :=
        public.regs07_snapshot_hash(public.regs07_player_snapshot(NEW));
    RETURN NEW;
END
$function$
;
ALTER FUNCTION public.regs07_initialize_player() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.regs07_initialize_player() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.regs07_initialize_team()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
BEGIN
    NEW.postcommit_revision := 1;
    NEW.postcommit_snapshot_hash :=
        public.regs07_snapshot_hash(public.regs07_team_snapshot(NEW));
    RETURN NEW;
END
$function$
;
ALTER FUNCTION public.regs07_initialize_team() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.regs07_initialize_team() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.reject_registration_human_field_authority_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
BEGIN
    RAISE EXCEPTION
        'REG-S05: proposals, approvals, decisions, executions and consumptions are immutable';
END
$function$
;
ALTER FUNCTION public.reject_registration_human_field_authority_mutation() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.reject_registration_human_field_authority_mutation() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.reject_registration_ocr_evidence_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
BEGIN
    RAISE EXCEPTION
        'REG-S03: OCR runs, field diffs and reprocess decisions are immutable';
END
$function$
;
ALTER FUNCTION public.reject_registration_ocr_evidence_mutation() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.reject_registration_ocr_evidence_mutation() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.reject_registration_page_composition_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
BEGIN
    RAISE EXCEPTION
        'REG-S04: page append attempts and decisions are immutable';
END
$function$
;
ALTER FUNCTION public.reject_registration_page_composition_mutation() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.reject_registration_page_composition_mutation() FROM PUBLIC,devnous_user;
CREATE OR REPLACE FUNCTION public.reject_regs07_authority_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog
AS $function$
BEGIN
    RAISE EXCEPTION
        'REG-S07: proposals, decisions, executions and versions are immutable';
END
$function$
;
ALTER FUNCTION public.reject_regs07_authority_mutation() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.reject_regs07_authority_mutation() FROM PUBLIC,devnous_user;

DO $owners$
DECLARE obj record;
BEGIN
  FOR obj IN SELECT * FROM (VALUES ('copa_telmex_teams'),('copa_telmex_players'),('copa_telmex_registration_review_sessions'),('copa_telmex_registration_review_drafts'),('copa_telmex_registration_review_assets'),('copa_telmex_registration_ocr_runs'),('copa_telmex_registration_ocr_field_diffs'),('copa_telmex_registration_ocr_reprocess_decisions'),('copa_telmex_registration_page_append_attempts'),('copa_telmex_registration_page_append_decisions'),('copa_telmex_registration_human_field_edit_proposals'),('copa_telmex_registration_human_field_approvals'),('copa_telmex_registration_human_field_edit_decisions'),('copa_telmex_registration_human_field_edit_executions'),('copa_telmex_registration_human_field_approval_consumptions'),('copa_telmex_registration_postcommit_mutation_proposals'),('copa_telmex_registration_postcommit_mutation_decisions'),('copa_telmex_registration_postcommit_mutation_executions'),('copa_telmex_registration_postcommit_entity_versions')) v(name) LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO samchat_ctt_staff_owner',obj.name);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,devnous_user',obj.name);
    EXECUTE format('GRANT SELECT ON public.%I TO devnous_user',obj.name);
    IF obj.name NOT IN ('copa_telmex_teams','copa_telmex_players','copa_telmex_registration_postcommit_entity_versions') THEN
      EXECUTE format('GRANT INSERT ON public.%I TO devnous_user',obj.name);
    END IF;
    IF obj.name IN ('copa_telmex_registration_review_sessions','copa_telmex_registration_review_assets') THEN
      EXECUTE format('GRANT UPDATE ON public.%I TO devnous_user',obj.name);
    END IF;
  END LOOP;
END
$owners$;

CREATE FUNCTION ctt_staff_private.legacy_row_reference_ids(p_table oid,p_row jsonb,p_seen oid[] DEFAULT ARRAY[]::oid[])
RETURNS uuid[] LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE ids uuid[]:=ARRAY[]::uuid[]; value text; parent record; parent_row jsonb;
BEGIN
  IF p_row IS NULL OR p_table=ANY(p_seen) THEN RETURN ids; END IF;
  IF cardinality(p_seen)>20 OR NOT EXISTS(SELECT 1 FROM ctt_staff_private.legacy_boundary_baseline b
    WHERE b.object_kind='table' AND pg_catalog.to_regclass('public.'||b.object_name)::oid=p_table) THEN
    RAISE EXCEPTION 'CTT legacy reference invalid' USING ERRCODE='42501';
  END IF;
  FOR value IN SELECT v FROM jsonb_each_text(p_row) x(k,v) WHERE v ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' LOOP
    ids:=array_append(ids,value::uuid);
  END LOOP;
  FOR parent IN SELECT c.confrelid,src.attname source_name,dst.attname target_name
    FROM pg_catalog.pg_constraint c
    JOIN pg_catalog.pg_attribute src ON src.attrelid=c.conrelid AND src.attnum=c.conkey[1]
    JOIN pg_catalog.pg_attribute dst ON dst.attrelid=c.confrelid AND dst.attnum=c.confkey[1]
    WHERE c.contype='f' AND c.conrelid=p_table AND cardinality(c.conkey)=1
      AND EXISTS(SELECT 1 FROM ctt_staff_private.legacy_boundary_baseline b
        WHERE b.object_kind='table' AND pg_catalog.to_regclass('public.'||b.object_name)::oid=c.confrelid)
  LOOP
    value:=p_row->>parent.source_name;
    IF value IS NOT NULL THEN
      EXECUTE format('SELECT to_jsonb(p) FROM %s p WHERE p.%I::text=$1',parent.confrelid::regclass,parent.target_name)
      INTO parent_row USING value;
      IF parent_row IS NOT NULL THEN
        ids:=ids||ctt_staff_private.legacy_row_reference_ids(parent.confrelid,parent_row,p_seen||p_table);
      END IF;
    END IF;
  END LOOP;
  RETURN ARRAY(SELECT DISTINCT v FROM unnest(ids) v ORDER BY v);
END
$fn$;

CREATE FUNCTION ctt_staff_private.lock_legacy_reference_ids(p_ids uuid[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE item uuid;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock_shared(1129600050,0);
  FOR item IN SELECT DISTINCT v FROM unnest(p_ids) v ORDER BY v LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(1129600050,pg_catalog.hashtext(item::text));
  END LOOP;
END
$fn$;

CREATE FUNCTION ctt_staff_private.reserve_v2_legacy_ids()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE scope jsonb; effect jsonb; ids uuid[]; item jsonb; anchor_id uuid; team_uuid uuid; current_draft record;
BEGIN
  IF NOT pg_catalog.pg_try_advisory_xact_lock(1129600050,0) THEN
    RAISE EXCEPTION 'CTT v2 reservation busy' USING ERRCODE='42501'; END IF;
  anchor_id:=NEW.attempt_id;
  IF TG_TABLE_NAME='capture_attempt_anchors' THEN
    scope:=convert_from(NEW.scope_bytes,'UTF8')::jsonb;
    ids:=ARRAY[(scope->>'team_id')::uuid,(scope->>'session_id')::uuid,(scope->>'draft_id')::uuid];
    team_uuid:=(scope->>'team_id')::uuid;
    FOR item IN SELECT value FROM jsonb_array_elements(scope->'source_pages') LOOP
      ids:=array_append(ids,(item->>'asset_id')::uuid);
    END LOOP;
  ELSE
    effect:=convert_from(NEW.effect_bytes,'UTF8')::jsonb;
    team_uuid:=(effect->'team'->>'id')::uuid;
    ids:=ARRAY[team_uuid];
    FOR item IN SELECT value FROM jsonb_array_elements(effect->'players') LOOP ids:=array_append(ids,(item->>'id')::uuid); END LOOP;
    FOR item IN SELECT value FROM jsonb_array_elements(effect->'staff') WHERE value->'row'<>'null'::jsonb LOOP
      ids:=array_append(ids,(item->'row'->>'id')::uuid);
    END LOOP;
  END IF;
  PERFORM ctt_staff_private.lock_legacy_reference_ids(ids);
  IF TG_TABLE_NAME='capture_attempt_anchors' THEN
    PERFORM 1 FROM public.copa_telmex_registration_review_sessions s
      WHERE s.id=(scope->>'session_id')::uuid AND s.committed_team_id IS NULL FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION 'CTT review source session unavailable' USING ERRCODE='42501'; END IF;
    SELECT d.id,d.session_id,d.draft_version,d.content_hash INTO current_draft
      FROM public.copa_telmex_registration_review_drafts d
      WHERE d.session_id=(scope->>'session_id')::uuid
      ORDER BY d.draft_version DESC,d.created_at DESC LIMIT 1 FOR SHARE NOWAIT;
    IF current_draft IS NULL OR current_draft.id::text IS DISTINCT FROM scope->>'draft_id'
      OR current_draft.draft_version::text IS DISTINCT FROM scope->>'draft_version'
      OR current_draft.content_hash IS DISTINCT FROM scope->>'draft_content_hash' THEN
      RAISE EXCEPTION 'CTT review source draft stale' USING ERRCODE='42501'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(scope->'source_pages') LOOP
      PERFORM 1 FROM public.copa_telmex_registration_review_assets a
        WHERE a.id=(item->>'asset_id')::uuid AND a.session_id=(scope->>'session_id')::uuid
          AND a.page_index::text=item->>'page_index'
          AND 'sha256:'||a.sha256=item->>'image_sha256'
          AND (a.width IS NULL OR a.width>0) AND (a.height IS NULL OR a.height>0)
        FOR SHARE NOWAIT;
      IF NOT FOUND THEN RAISE EXCEPTION 'CTT review source asset mismatch' USING ERRCODE='42501'; END IF;
    END LOOP;
  END IF;
  -- Do not adopt or overwrite an existing committed legacy entity/history.
  IF EXISTS(SELECT 1 FROM public.copa_telmex_teams WHERE id=team_uuid)
    OR EXISTS(SELECT 1 FROM public.copa_telmex_players WHERE id=ANY(ids))
    OR EXISTS(SELECT 1 FROM public.copa_telmex_registration_postcommit_entity_versions WHERE team_id=team_uuid OR entity_id=ANY(ids))
    OR EXISTS(SELECT 1 FROM public.copa_telmex_registration_postcommit_mutation_proposals WHERE team_id=team_uuid OR entity_id=ANY(ids)) THEN
    RAISE EXCEPTION 'CTT v2 reservation conflicts with legacy state' USING ERRCODE='42501';
  END IF;
  INSERT INTO ctt_staff_private.legacy_v2_reservations(reserved_id,attempt_id)
    SELECT DISTINCT v,anchor_id FROM unnest(ids) v ON CONFLICT DO NOTHING;
  RETURN NEW;
EXCEPTION WHEN lock_not_available THEN
  RAISE EXCEPTION 'CTT review source busy' USING ERRCODE='42501';
END
$fn$;
-- AFTER INSERT permits the reservation FK to reference the newly inserted anchor.
-- The advisory lock is acquired before any caller can commit that reservation.
CREATE TRIGGER ctt_v2_legacy_anchor_reserve AFTER INSERT ON ctt_staff_private.capture_attempt_anchors
FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reserve_v2_legacy_ids();
CREATE TRIGGER ctt_v2_legacy_payload_reserve AFTER INSERT ON ctt_staff_private.capture_payloads
FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reserve_v2_legacy_ids();

CREATE FUNCTION ctt_staff_private.current_exact_capture_row(p_table text,p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE candidate record; writer record; effect jsonb; envelope jsonb; scope jsonb;
  proposed jsonb; answer jsonb; hits integer:=0; top_xid bigint; now_at timestamptz;
BEGIN
  IF p_table NOT IN ('copa_telmex_teams','copa_telmex_players') THEN RETURN NULL; END IF;
  SELECT w.writer_role,w.kid,k.secret,k.not_before,k.not_after,k.revoked INTO writer
  FROM ctt_staff_private.effect_writers w JOIN ctt_staff_private.effect_keys k ON k.kid=w.kid
  JOIN pg_catalog.pg_roles r ON r.rolname=w.writer_role
  WHERE w.writer_role=session_user AND r.rolname NOT IN ('devnous_user','samchat_ctt_staff_owner')
    AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
    AND NOT r.rolreplication AND NOT r.rolbypassrls
    AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.roleid=r.oid OR m.member=r.oid)
  FOR SHARE OF w,k;
  now_at:=clock_timestamp();
  IF writer IS NULL OR writer.revoked OR now_at<writer.not_before OR now_at>=writer.not_after THEN RETURN NULL; END IF;
  top_xid:=mod(pg_catalog.pg_current_xact_id()::text::numeric,4294967296)::bigint;
  FOR candidate IN SELECT p.attempt_id,p.effect_bytes,p.gate_envelope,p.nonce,a.scope_bytes,a.scope_hash,
    a.tenant_id,a.edition_id,c.principal_id,c.issued_at,c.expires_at,g.expires_at_seconds
    FROM ctt_staff_private.capture_payloads p JOIN ctt_staff_private.capture_attempt_anchors a USING(attempt_id)
    JOIN ctt_staff_private.review_challenges c USING(challenge_id)
    JOIN ctt_staff_private.review_challenge_consumptions used USING(challenge_id)
    JOIN ctt_staff_private.signed_assertion_guards g USING(challenge_id)
    WHERE used.xmin::text::bigint=top_xid AND g.xmin::text::bigint=top_xid
      AND used.credential_id=c.credential_id
      AND NOT EXISTS(SELECT 1 FROM ctt_staff_private.capture_results result WHERE result.attempt_id=p.attempt_id)
    FOR UPDATE OF p
  LOOP
    now_at:=clock_timestamp();
    IF now_at>=candidate.expires_at OR now_at>=to_timestamp(candidate.expires_at_seconds)
      OR writer.not_after<to_timestamp(candidate.expires_at_seconds)
      OR candidate.expires_at_seconds<>floor(extract(epoch FROM candidate.expires_at))::bigint THEN CONTINUE; END IF;
    IF NOT(convert_from(candidate.effect_bytes,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
      OR NOT(convert_from(candidate.gate_envelope,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
      OR NOT(convert_from(candidate.scope_bytes,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS) THEN CONTINUE; END IF;
    effect:=convert_from(candidate.effect_bytes,'UTF8')::jsonb;
    envelope:=convert_from(candidate.gate_envelope,'UTF8')::jsonb;
    scope:=convert_from(candidate.scope_bytes,'UTF8')::jsonb;
    IF effect->>'schema' IS DISTINCT FROM 'ctt.capture.effect.v1'
      OR scope->>'purpose' IS DISTINCT FROM 'capture_staff_v2'
      OR scope->>'schema' NOT IN ('ctt.staff-review.challenge-scope.v1','ctt.staff-review.challenge-scope.v2')
      OR effect->'review_context' IS DISTINCT FROM (CASE WHEN scope->>'schema'='ctt.staff-review.challenge-scope.v2'
        THEN scope-ARRAY['staff_and_changes_binding'] ELSE scope-ARRAY['staff_and_changes_binding','decision_hash','review_receipt_id','operation_id'] END)
      OR scope->>'tenant_id' IS DISTINCT FROM candidate.tenant_id
      OR scope->>'edition_id' IS DISTINCT FROM candidate.edition_id::text
      OR scope->>'actor_id' IS DISTINCT FROM candidate.principal_id::text
      OR scope->>'team_id' IS DISTINCT FROM effect->'team'->>'id'
      OR envelope->'body'->>'review_plan_binding' IS DISTINCT FROM scope->>'staff_and_changes_binding'
      OR envelope->'body'->>'attempt_id' IS DISTINCT FROM candidate.attempt_id::text
      OR envelope->'body'->>'nonce' IS DISTINCT FROM candidate.nonce::text
      OR envelope->'body'->>'scope_hash' IS DISTINCT FROM candidate.scope_hash
      OR envelope->'body'->>'expires_at' IS DISTINCT FROM candidate.expires_at_seconds::text
      OR NOT coalesce((envelope->'body'->>'effect_binding') ~ '^hmac-sha256:[0-9a-f]{64}$',false)
      OR NOT coalesce(ctt_staff_private.verify_effect_hmac(writer.secret,candidate.effect_bytes,
        decode(substr(envelope->'body'->>'effect_binding',13),'hex')),false) THEN CONTINUE; END IF;
    IF p_table='copa_telmex_teams' THEN proposed:=effect->'team';
    ELSE SELECT value INTO proposed FROM jsonb_array_elements(effect->'players') WHERE value->>'id'=p_id::text; END IF;
    IF proposed->>'id' IS DISTINCT FROM p_id::text THEN CONTINUE; END IF;
    IF p_table='copa_telmex_players' AND (proposed->>'governance_state' IS DISTINCT FROM 'PENDING_FINALITY'
      OR proposed->'finality_receipt_id' IS DISTINCT FROM 'null'::jsonb) THEN CONTINUE; END IF;
    answer:=proposed; hits:=hits+1;
  END LOOP;
  IF hits<>1 THEN RETURN NULL; END IF;
  RETURN answer;
END
$fn$;

CREATE FUNCTION ctt_staff_private.guard_legacy_v2_boundary()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE row_value jsonb; ids uuid[]:=ARRAY[]::uuid[]; expected jsonb; typed jsonb; columns text[]; supplied text[];
  registered boolean; entity record;
BEGIN
  IF TG_OP='TRUNCATE' THEN RAISE EXCEPTION 'CTT truncation denied' USING ERRCODE='42501'; END IF;
  IF TG_OP<>'INSERT' THEN ids:=ids||ctt_staff_private.legacy_row_reference_ids(TG_RELID,to_jsonb(OLD)); END IF;
  IF TG_OP<>'DELETE' THEN ids:=ids||ctt_staff_private.legacy_row_reference_ids(TG_RELID,to_jsonb(NEW)); END IF;
  PERFORM ctt_staff_private.lock_legacy_reference_ids(ids);
  SELECT EXISTS(SELECT 1 FROM ctt_staff_private.effect_writers WHERE writer_role=session_user) INTO registered;
  IF TG_TABLE_NAME IN ('copa_telmex_teams','copa_telmex_players')
    AND NOT registered AND session_user<>'devnous_user'
    AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=session_user AND rolsuper) THEN
    RAISE EXCEPTION 'CTT unregistered entity writer denied' USING ERRCODE='42501';
  END IF;
  IF registered AND TG_OP='INSERT' AND TG_TABLE_NAME IN ('copa_telmex_teams','copa_telmex_players') THEN
    expected:=ctt_staff_private.current_exact_capture_row(TG_TABLE_NAME,NEW.id);
    IF expected IS NULL THEN RAISE EXCEPTION 'CTT exact capture row denied' USING ERRCODE='42501'; END IF;
    SELECT array_agg(attname::text ORDER BY attname) INTO columns FROM pg_catalog.pg_attribute
    WHERE attrelid=TG_RELID AND attnum>0 AND NOT attisdropped;
    SELECT array_agg(k ORDER BY k) INTO supplied FROM jsonb_object_keys(expected) k;
    IF columns IS DISTINCT FROM supplied THEN RAISE EXCEPTION 'CTT exact capture columns denied' USING ERRCODE='42501'; END IF;
    EXECUTE format('SELECT to_jsonb(jsonb_populate_record(NULL::%s,$1))',TG_RELID::regclass) INTO typed USING expected;
    IF typed IS DISTINCT FROM to_jsonb(NEW) THEN RAISE EXCEPTION 'CTT exact capture values denied' USING ERRCODE='42501'; END IF;
    RETURN NEW;
  END IF;
  IF EXISTS(SELECT 1 FROM ctt_staff_private.legacy_v2_reservations WHERE reserved_id=ANY(ids)) THEN
    -- Only the existing protected genesis trigger may append an exact v2 history row.
    IF registered AND TG_OP='INSERT' AND TG_TABLE_NAME='copa_telmex_registration_postcommit_entity_versions' THEN
      IF NEW.mutation_type='GENESIS' AND NEW.execution_id IS NULL AND NEW.predecessor_snapshot_hash IS NULL
      AND NEW.revision=1 THEN
      IF NEW.entity_type='TEAM' THEN
        SELECT t.id,t.postcommit_snapshot_hash,public.regs07_team_snapshot(t) snapshot INTO entity
        FROM public.copa_telmex_teams t WHERE t.id=NEW.entity_id AND t.id=NEW.team_id;
        expected:=ctt_staff_private.current_exact_capture_row('copa_telmex_teams',NEW.entity_id);
      ELSIF NEW.entity_type='PLAYER' THEN
        SELECT p.id,p.postcommit_snapshot_hash,public.regs07_player_snapshot(p) snapshot INTO entity
        FROM public.copa_telmex_players p WHERE p.id=NEW.entity_id AND p.team_id=NEW.team_id;
        expected:=ctt_staff_private.current_exact_capture_row('copa_telmex_players',NEW.entity_id);
      END IF;
      IF expected IS NOT NULL AND entity IS NOT NULL AND NEW.snapshot::jsonb=entity.snapshot
        AND NEW.snapshot_hash=entity.postcommit_snapshot_hash THEN RETURN NEW; END IF;
      END IF;
    END IF;
    RAISE EXCEPTION 'CTT legacy cannot mutate protected v2 evidence' USING ERRCODE='42501';
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END
$fn$;
DO $guards$
DECLARE obj record;
BEGIN
  FOR obj IN SELECT object_name FROM ctt_staff_private.legacy_boundary_baseline WHERE object_kind='table' LOOP
    EXECUTE format('CREATE TRIGGER ctt_legacy_v2_boundary BEFORE INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.guard_legacy_v2_boundary()',obj.object_name);
    EXECUTE format('CREATE TRIGGER ctt_legacy_no_truncate BEFORE TRUNCATE ON public.%I FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.guard_legacy_v2_boundary()',obj.object_name);
  END LOOP;
END
$guards$;

CREATE FUNCTION ctt_staff_private.check_legacy_insert_columns(p_table oid,p_value jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE supplied text[]; expected text[];
BEGIN
  IF p_value IS NULL OR jsonb_typeof(p_value)<>'object' OR octet_length(p_value::text)>4194304 THEN
    RAISE EXCEPTION 'CTT legacy row invalid' USING ERRCODE='42501'; END IF;
  SELECT array_agg(k ORDER BY k) INTO supplied FROM jsonb_object_keys(p_value) k;
  SELECT array_agg(attname::text ORDER BY attname) INTO expected FROM pg_catalog.pg_attribute
  WHERE attrelid=p_table AND attnum>0 AND NOT attisdropped;
  IF supplied IS DISTINCT FROM expected THEN RAISE EXCEPTION 'CTT legacy columns invalid' USING ERRCODE='42501'; END IF;
END
$fn$;
CREATE FUNCTION public.legacy_insert_team(p_value jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE row_value public.copa_telmex_teams;
BEGIN
  PERFORM ctt_staff_private.check_legacy_insert_columns('public.copa_telmex_teams'::regclass,p_value);
  row_value:=jsonb_populate_record(NULL::public.copa_telmex_teams,p_value);
  INSERT INTO public.copa_telmex_teams SELECT (row_value).* RETURNING * INTO row_value;
  RETURN to_jsonb(row_value);
END
$fn$;
CREATE FUNCTION public.legacy_insert_player(p_value jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE row_value public.copa_telmex_players;
BEGIN
  PERFORM ctt_staff_private.check_legacy_insert_columns('public.copa_telmex_players'::regclass,p_value);
  row_value:=jsonb_populate_record(NULL::public.copa_telmex_players,p_value);
  INSERT INTO public.copa_telmex_players SELECT (row_value).* RETURNING * INTO row_value;
  RETURN to_jsonb(row_value);
END
$fn$;
CREATE FUNCTION public.legacy_finalize_player(p_id uuid,p_receipt text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE row_value public.copa_telmex_players;
BEGIN
  IF p_receipt IS NULL OR p_receipt !~ '^sha256:[a-f0-9]{64}$' THEN
    RAISE EXCEPTION 'CTT legacy finality invalid' USING ERRCODE='42501'; END IF;
  SELECT * INTO row_value FROM public.copa_telmex_players WHERE id=p_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'CTT legacy player unavailable' USING ERRCODE='42501'; END IF;
  IF row_value.governance_state='ACTIVE' AND row_value.finality_receipt_id=p_receipt THEN RETURN to_jsonb(row_value); END IF;
  IF row_value.governance_state<>'PENDING_FINALITY' OR row_value.finality_receipt_id IS NOT NULL THEN
    RAISE EXCEPTION 'CTT legacy finality state invalid' USING ERRCODE='42501'; END IF;
  UPDATE public.copa_telmex_players SET governance_state='ACTIVE',finality_receipt_id=p_receipt,
    updated_at=timezone('UTC',clock_timestamp()) WHERE id=p_id RETURNING * INTO row_value;
  RETURN to_jsonb(row_value);
END
$fn$;
CREATE FUNCTION public.legacy_apply_postcommit_projection(p_kind text,p_id uuid,p_revision bigint,p_hash text,p_updates jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE team public.copa_telmex_teams; player public.copa_telmex_players; allowed text[]; item text;
BEGIN
  IF p_updates IS NULL OR jsonb_typeof(p_updates)<>'object' OR p_updates='{}'::jsonb
    OR octet_length(p_updates::text)>1048576 THEN RAISE EXCEPTION 'CTT legacy updates invalid' USING ERRCODE='42501'; END IF;
  IF p_kind='TEAM' THEN
    allowed:=ARRAY['name','category','gender','league','league_phone','league_address','representative_name','contact_email','contact_phone','state','municipality'];
  ELSIF p_kind='PLAYER' THEN
    allowed:=ARRAY['first_name','last_name','birth_date','curp','email','verified_by_human','needs_review','verification_notes'];
  ELSE RAISE EXCEPTION 'CTT legacy entity invalid' USING ERRCODE='42501'; END IF;
  FOR item IN SELECT jsonb_object_keys(p_updates) LOOP
    IF NOT item=ANY(allowed) THEN RAISE EXCEPTION 'CTT legacy field denied' USING ERRCODE='42501'; END IF;
  END LOOP;
  IF p_kind='TEAM' THEN
    SELECT * INTO team FROM public.copa_telmex_teams WHERE id=p_id FOR UPDATE;
    IF team.id IS NULL OR team.postcommit_revision IS DISTINCT FROM p_revision OR team.postcommit_snapshot_hash IS DISTINCT FROM p_hash THEN
      RAISE EXCEPTION 'CTT legacy successor stale' USING ERRCODE='42501'; END IF;
    team:=jsonb_populate_record(team,p_updates);
    UPDATE public.copa_telmex_teams SET name=team.name,category=team.category,gender=team.gender,
      league=team.league,league_phone=team.league_phone,league_address=team.league_address,
      representative_name=team.representative_name,contact_email=team.contact_email,
      contact_phone=team.contact_phone,state=team.state,municipality=team.municipality,
      updated_at=timezone('UTC',clock_timestamp()) WHERE id=p_id RETURNING * INTO team;
    RETURN to_jsonb(team);
  END IF;
  SELECT * INTO player FROM public.copa_telmex_players WHERE id=p_id FOR UPDATE;
  IF player.id IS NULL OR player.postcommit_revision IS DISTINCT FROM p_revision OR player.postcommit_snapshot_hash IS DISTINCT FROM p_hash THEN
    RAISE EXCEPTION 'CTT legacy successor stale' USING ERRCODE='42501'; END IF;
  player:=jsonb_populate_record(player,p_updates);
  UPDATE public.copa_telmex_players SET first_name=player.first_name,last_name=player.last_name,
    birth_date=player.birth_date,curp=player.curp,email=player.email,
    verified_by_human=player.verified_by_human,needs_review=player.needs_review,
    verification_notes=player.verification_notes,updated_at=timezone('UTC',clock_timestamp())
    WHERE id=p_id RETURNING * INTO player;
  RETURN to_jsonb(player);
END
$fn$;

DO $secure$
DECLARE item record;
BEGIN
  FOR item IN SELECT * FROM (VALUES
    ('ctt_staff_private.legacy_row_reference_ids(oid,jsonb,oid[])'),
    ('ctt_staff_private.lock_legacy_reference_ids(uuid[])'),
    ('ctt_staff_private.reserve_v2_legacy_ids()'),
    ('ctt_staff_private.current_exact_capture_row(text,uuid)'),
    ('ctt_staff_private.guard_legacy_v2_boundary()'),
    ('ctt_staff_private.check_legacy_insert_columns(oid,jsonb)'),
    ('public.legacy_insert_team(jsonb)'),('public.legacy_insert_player(jsonb)'),
    ('public.legacy_finalize_player(uuid,text)'),
    ('public.legacy_apply_postcommit_projection(text,uuid,bigint,text,jsonb)')
  ) f(signature) LOOP
    EXECUTE 'ALTER FUNCTION '||item.signature||' OWNER TO samchat_ctt_staff_owner';
    EXECUTE 'REVOKE ALL ON FUNCTION '||item.signature||' FROM PUBLIC,devnous_user';
    IF item.signature LIKE 'public.%' THEN EXECUTE 'GRANT EXECUTE ON FUNCTION '||item.signature||' TO devnous_user'; END IF;
  END LOOP;
END
$secure$;
DO $postflight$
DECLARE item record;
BEGIN
  FOR item IN SELECT object_name FROM ctt_staff_private.legacy_boundary_baseline WHERE object_kind='table' LOOP
    IF pg_catalog.pg_has_role('devnous_user',(SELECT relowner FROM pg_catalog.pg_class WHERE oid=pg_catalog.to_regclass('public.'||item.object_name)),'MEMBER')
      OR pg_catalog.has_table_privilege('devnous_user','public.'||item.object_name,'TRUNCATE,TRIGGER') THEN
      RAISE EXCEPTION 'CTT legacy owner boundary invalid'; END IF;
  END LOOP;
  IF pg_catalog.has_table_privilege('devnous_user','public.copa_telmex_teams','INSERT,UPDATE,DELETE')
    OR pg_catalog.has_table_privilege('devnous_user','public.copa_telmex_players','INSERT,UPDATE,DELETE')
    OR pg_catalog.has_table_privilege('devnous_user','public.copa_telmex_registration_postcommit_entity_versions','INSERT,UPDATE,DELETE')
    OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE') THEN
    RAISE EXCEPTION 'CTT legacy DML boundary invalid'; END IF;
END
$postflight$;
COMMIT;
