-- Isolated candidate: exact finality in the trusted writer's existing transaction.
-- Ed25519/event verification remains mandatory in the trusted consumer. SQL
-- independently verifies the HMAC, protected state, real transaction and rows.
-- No operational identity, credential, key, grant or database pin is seeded.
BEGIN;
DO $preflight$
DECLARE owner_id oid; item record;
BEGIN
 SELECT oid INTO owner_id FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner'
 AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
 AND NOT rolreplication AND NOT rolbypassrls;
 IF owner_id IS NULL OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id)
 OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE')
 OR pg_catalog.current_setting('max_prepared_transactions')::integer<>0 THEN
   RAISE EXCEPTION 'CTT finality isolation invalid'; END IF;
 FOR item IN SELECT * FROM (VALUES
 ('ctt_staff_private.capture_payloads'),('ctt_staff_private.capture_results'),
 ('ctt_staff_private.capture_attempt_anchors'),('ctt_staff_private.review_challenges'),
 ('ctt_staff_private.review_challenge_consumptions'),('ctt_staff_private.signed_assertion_guards'),
 ('ctt_staff_private.effect_writers'),('ctt_staff_private.effect_keys'),
 ('public.copa_telmex_teams'),('public.copa_telmex_players'),('public.copa_telmex_team_staff'),
 ('public.copa_telmex_registration_postcommit_entity_versions')) t(name) LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(item.name)
 AND c.relowner=owner_id AND c.relkind='r' AND NOT c.relrowsecurity)
 OR pg_catalog.has_table_privilege('devnous_user',item.name,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')
 OR pg_catalog.has_any_column_privilege('devnous_user',item.name,'INSERT,UPDATE')
 OR (item.name LIKE 'ctt_staff_private.%' AND EXISTS(
 SELECT 1 FROM pg_catalog.pg_class c CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
 WHERE c.oid=pg_catalog.to_regclass(item.name) AND a.grantee<>owner_id))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=pg_catalog.to_regclass(item.name)
 AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL) THEN
   RAISE EXCEPTION 'CTT finality protected relation invalid'; END IF;
 END LOOP;
 FOR item IN SELECT * FROM (VALUES
 ('public.copa_telmex_teams','ctt_legacy_v2_boundary','ctt_staff_private.guard_legacy_v2_boundary()'),
 ('public.copa_telmex_players','ctt_legacy_v2_boundary','ctt_staff_private.guard_legacy_v2_boundary()'),
 ('public.copa_telmex_team_staff','ctt_staff_write_closed','ctt_staff_private.deny_staff_write()'),
 ('ctt_staff_private.capture_results','ctt_capture_result_immutable','ctt_staff_private.reject_review_evidence_mutation()'),
 ('ctt_staff_private.capture_results','ctt_capture_result_no_truncate','ctt_staff_private.reject_review_evidence_mutation()')) t(relation,trigger_name,signature) LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger g WHERE g.tgrelid=pg_catalog.to_regclass(item.relation)
 AND g.tgname=item.trigger_name AND g.tgenabled IN('O','A') AND NOT g.tgisinternal
 AND g.tgfoid=pg_catalog.to_regprocedure(item.signature)) THEN
 RAISE EXCEPTION 'CTT finality protected trigger invalid'; END IF;
 END LOOP;
 FOR item IN SELECT * FROM (VALUES
 ('ctt_staff_private.deny_staff_write()','7fdb5522a19627554872e2f3a71f5236'),
 ('ctt_staff_private.guard_legacy_v2_boundary()','f9073d40b0e467433816b4825df497f7')) t(signature,body_hash) LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=pg_catalog.to_regprocedure(item.signature)
 AND md5(p.prosrc)=item.body_hash AND NOT EXISTS(
 SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE a.grantee<>owner_id)) THEN RAISE EXCEPTION 'CTT finality baseline drift'; END IF;
 END LOOP;
 FOR item IN SELECT * FROM (VALUES('public.digest(bytea,text)'),('public.hmac(bytea,bytea,text)')) t(signature) LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
 JOIN pg_catalog.pg_roles r ON r.oid=e.extowner JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
 JOIN pg_catalog.pg_proc p ON p.oid=d.objid
 WHERE e.extname='pgcrypto' AND r.rolsuper AND n.nspname='public' AND p.proowner=e.extowner
 AND d.classid='pg_catalog.pg_proc'::regclass AND d.refclassid='pg_catalog.pg_extension'::regclass
 AND d.deptype='e' AND p.oid=pg_catalog.to_regprocedure(item.signature)) THEN
 RAISE EXCEPTION 'CTT finality crypto dependency invalid'; END IF;
 END LOOP;
 FOR item IN SELECT * FROM (VALUES
 ('ctt_staff_private.deny_staff_write()'),('ctt_staff_private.guard_legacy_v2_boundary()'),
 ('ctt_staff_private.record_capture_result(uuid,uuid,text)'),
 ('public.regs07_guard_committed_mutation()'),('public.regs07_player_snapshot(public.copa_telmex_players)'),
 ('public.regs07_snapshot_hash(jsonb)'),('public.regs07_canonical_jsonb(jsonb)')) t(signature) LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=pg_catalog.to_regprocedure(item.signature)
 AND p.proowner=owner_id AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]) THEN
   RAISE EXCEPTION 'CTT finality protected function invalid'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_results) THEN
   RAISE EXCEPTION 'CTT finality cutover refuses provisional results'; END IF;
END
$preflight$;

CREATE TABLE ctt_staff_private.finality_migration_baseline(
 signature text PRIMARY KEY, original_definition text NOT NULL, original_hash text NOT NULL,
 installed_hash text
);
CREATE TABLE ctt_staff_private.finality_database_identity(
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 database_identity text NOT NULL CHECK(database_identity ~ '^[a-z0-9][a-z0-9_-]{0,79}$')
);
CREATE TABLE ctt_staff_private.finality_inputs(
 attempt_id uuid PRIMARY KEY REFERENCES ctt_staff_private.capture_payloads(attempt_id) ON DELETE RESTRICT,
 preauthorization_bytes bytea NOT NULL CHECK(octet_length(preauthorization_bytes) BETWEEN 2 AND 4194304),
 evidence_bytes bytea NOT NULL CHECK(octet_length(evidence_bytes) BETWEEN 2 AND 4194304)
);
CREATE TABLE ctt_staff_private.capture_finalities(
 attempt_id uuid PRIMARY KEY REFERENCES ctt_staff_private.capture_payloads(attempt_id) ON DELETE RESTRICT,
 database_identity text NOT NULL, database_oid oid NOT NULL, pg_top_xid xid8 NOT NULL,
 database_transaction_id text NOT NULL CHECK(database_transaction_id ~ '^samchat-tx-[0-9a-f]{24}$'),
 event_attempt_id text NOT NULL CHECK(event_attempt_id ~ '^sha256:[0-9a-f]{64}$'),
 nonce uuid NOT NULL UNIQUE, scope_hash text NOT NULL, review_plan_binding text NOT NULL,
 pending_effect_binding text NOT NULL, finality_effect_binding text NOT NULL,
 finality_effect_bytes bytea NOT NULL CHECK(octet_length(finality_effect_bytes) BETWEEN 2 AND 8388608),
 finality_envelope bytea NOT NULL CHECK(octet_length(finality_envelope) BETWEEN 2 AND 262144),
 issued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 writer_role name NOT NULL REFERENCES ctt_staff_private.effect_writers(writer_role) ON DELETE RESTRICT,
 kid text NOT NULL REFERENCES ctt_staff_private.effect_keys(kid) ON DELETE RESTRICT,
 gate_kid text NOT NULL CHECK(gate_kid ~ '^[A-Za-z0-9_-]{1,64}$'),
 UNIQUE(attempt_id,pg_top_xid), CHECK(expires_at>issued_at)
);
DO $tables$
DECLARE name text;
BEGIN
 FOREACH name IN ARRAY ARRAY['finality_migration_baseline','finality_database_identity','finality_inputs','capture_finalities'] LOOP
 EXECUTE format('ALTER TABLE ctt_staff_private.%I OWNER TO samchat_ctt_staff_owner',name);
 EXECUTE format('REVOKE ALL ON ctt_staff_private.%I FROM PUBLIC,devnous_user',name);
 IF name<>'finality_migration_baseline' THEN
 EXECUTE format('CREATE TRIGGER ctt_finality_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.%I FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',name);
 EXECUTE format('CREATE TRIGGER ctt_finality_no_truncate BEFORE TRUNCATE ON ctt_staff_private.%I FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',name);
 END IF;
 END LOOP;
END
$tables$;

CREATE FUNCTION ctt_staff_private.current_finality_writer()
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE answer text;
BEGIN
 SELECT w.kid INTO answer FROM ctt_staff_private.effect_writers w
 JOIN ctt_staff_private.effect_keys k ON k.kid=w.kid
 JOIN pg_catalog.pg_roles r ON r.rolname=w.writer_role
 WHERE w.writer_role=session_user AND r.rolname NOT IN('devnous_user','samchat_ctt_staff_owner')
 AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
 AND NOT r.rolreplication AND NOT r.rolbypassrls
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.roleid=r.oid OR m.member=r.oid)
 AND NOT k.revoked AND clock_timestamp()>=k.not_before AND clock_timestamp()<k.not_after
 FOR SHARE OF w,k;
 IF answer IS NULL OR current_setting('max_prepared_transactions')::integer<>0 THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 RETURN answer;
END
$fn$;

CREATE FUNCTION ctt_staff_private.load_capture_finality_inputs(p_attempt_id uuid)
RETURNS TABLE(preauthorization_bytes bytea,evidence_bytes bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
BEGIN
 PERFORM ctt_staff_private.current_finality_writer();
 RETURN QUERY SELECT i.preauthorization_bytes,i.evidence_bytes
 FROM ctt_staff_private.finality_inputs i WHERE i.attempt_id=p_attempt_id FOR SHARE;
END
$fn$;

CREATE FUNCTION ctt_staff_private.load_capture_finality_context(p_attempt_id uuid)
RETURNS TABLE(database_identity text,database_oid bigint,pg_top_xid text,
 issued_at_seconds bigint,expires_at_seconds bigint,validation jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE anchor record; scope jsonb; draft record; pin text;
BEGIN
 PERFORM ctt_staff_private.current_finality_writer();
 SELECT a.scope_bytes,c.expires_at INTO anchor FROM ctt_staff_private.capture_attempt_anchors a
 JOIN ctt_staff_private.review_challenges c USING(challenge_id)
 WHERE a.attempt_id=p_attempt_id FOR UPDATE OF a FOR SHARE OF c;
 IF anchor IS NULL OR clock_timestamp()>=anchor.expires_at
 OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_results WHERE attempt_id=p_attempt_id) THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 scope:=convert_from(anchor.scope_bytes,'UTF8')::jsonb;
 PERFORM 1 FROM public.copa_telmex_registration_review_sessions s
 WHERE s.id=(scope->>'session_id')::uuid AND s.committed_team_id IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 SELECT d.id,d.draft_version,d.content_hash,d.validation INTO draft
 FROM public.copa_telmex_registration_review_drafts d WHERE d.session_id=(scope->>'session_id')::uuid
 ORDER BY d.draft_version DESC,d.created_at DESC LIMIT 1 FOR SHARE;
 IF draft IS NULL OR draft.id::text IS DISTINCT FROM scope->>'draft_id'
 OR draft.draft_version::text IS DISTINCT FROM scope->>'draft_version'
 OR draft.content_hash IS DISTINCT FROM scope->>'draft_content_hash' THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 SELECT f.database_identity INTO pin FROM ctt_staff_private.finality_database_identity f WHERE singleton FOR SHARE;
 IF pin IS NULL THEN RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT pin,d.oid::bigint,pg_current_xact_id()::text,
 floor(extract(epoch FROM clock_timestamp()))::bigint,
 floor(extract(epoch FROM anchor.expires_at))::bigint,draft.validation::jsonb
 FROM pg_catalog.pg_database d WHERE d.datname=current_database();
END
$fn$;

CREATE FUNCTION ctt_staff_private.project_capture_finality_player(p_row jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE proposed public.copa_telmex_players;
BEGIN
 PERFORM ctt_staff_private.current_finality_writer();
 proposed:=jsonb_populate_record(NULL::public.copa_telmex_players,p_row);
 IF proposed.governance_state IS DISTINCT FROM 'ACTIVE' OR proposed.finality_receipt_id IS NULL THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 proposed.postcommit_revision:=1;
 proposed.postcommit_snapshot_hash:=public.regs07_snapshot_hash(public.regs07_player_snapshot(proposed));
 RETURN to_jsonb(proposed);
END
$fn$;

CREATE FUNCTION ctt_staff_private.check_capture_finality_proof(p_attempt_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE f ctt_staff_private.capture_finalities%ROWTYPE; payload record; writer record;
 effect jsonb; envelope jsonb; body jsonb; scope jsonb; pin text; db_oid oid; tx_name text;
 expected_attempt text; actual bytea; candidate bytea; diff integer:=0; idx integer;
BEGIN
 SELECT * INTO f FROM ctt_staff_private.capture_finalities WHERE attempt_id=p_attempt_id;
 IF f IS NULL OR f.writer_role IS DISTINCT FROM session_user
 OR f.kid IS DISTINCT FROM ctt_staff_private.current_finality_writer()
 OR f.pg_top_xid IS DISTINCT FROM pg_current_xact_id()
 OR (SELECT xmin::text::bigint FROM ctt_staff_private.capture_finalities WHERE attempt_id=p_attempt_id)
 IS DISTINCT FROM mod(pg_current_xact_id()::text::numeric,4294967296)::bigint THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 SELECT k.* INTO writer FROM ctt_staff_private.effect_keys k WHERE k.kid=f.kid FOR SHARE;
 SELECT d.oid INTO db_oid FROM pg_catalog.pg_database d WHERE d.datname=current_database();
 SELECT p.database_identity INTO pin FROM ctt_staff_private.finality_database_identity p WHERE singleton FOR SHARE;
 SELECT p.effect_bytes,p.gate_envelope,a.scope_bytes,a.scope_hash,c.issued_at,c.expires_at,i.preauthorization_bytes,
 g.proof_sha256,g.expires_at_seconds INTO payload
 FROM ctt_staff_private.capture_payloads p JOIN ctt_staff_private.capture_attempt_anchors a USING(attempt_id)
 JOIN ctt_staff_private.finality_inputs i USING(attempt_id)
 JOIN ctt_staff_private.review_challenges c USING(challenge_id)
 JOIN ctt_staff_private.review_challenge_consumptions used USING(challenge_id)
 JOIN ctt_staff_private.signed_assertion_guards g USING(challenge_id)
 WHERE p.attempt_id=p_attempt_id
 AND used.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint
 AND g.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint
 AND used.credential_id=c.credential_id FOR UPDATE OF p FOR SHARE OF c;
 IF payload IS NULL OR pin IS NULL OR f.database_identity IS DISTINCT FROM pin
 OR f.database_oid IS DISTINCT FROM db_oid OR clock_timestamp()>=f.expires_at
 OR f.expires_at>payload.expires_at OR f.expires_at>to_timestamp(payload.expires_at_seconds)
 OR f.expires_at>writer.not_after OR f.issued_at<date_trunc('second',payload.issued_at)
 OR f.issued_at>clock_timestamp() OR f.expires_at-f.issued_at>interval '300 seconds' THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 tx_name:='samchat-tx-'||substr(encode(public.digest(convert_to('ctt.capture.database-transaction.v1','UTF8')||decode('00','hex')||convert_to(pin,'UTF8')||decode('00','hex')||convert_to(db_oid::text,'UTF8')||decode('00','hex')||convert_to(pg_current_xact_id()::text,'UTF8'),'sha256'),'hex'),1,24);
 expected_attempt:='sha256:'||encode(public.digest(convert_to(public.regs07_canonical_jsonb(jsonb_build_object('domain','ctt.capture.finality-attempt.v1','review_attempt_id',p_attempt_id::text,'database_transaction_id',tx_name)),'UTF8'),'sha256'),'hex');
 IF f.database_transaction_id IS DISTINCT FROM tx_name OR f.event_attempt_id IS DISTINCT FROM expected_attempt
 OR NOT(convert_from(f.finality_effect_bytes,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(f.finality_envelope,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS) THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 effect:=convert_from(f.finality_effect_bytes,'UTF8')::jsonb;
 envelope:=convert_from(f.finality_envelope,'UTF8')::jsonb; body:=envelope->'body';
 scope:=convert_from(payload.scope_bytes,'UTF8')::jsonb;
 IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(effect) key) IS DISTINCT FROM
 ARRAY['column_schema','database_identity','database_oid','database_transaction_id','event_attempt_id','pending_effect_binding','pg_top_xid','players','review_attempt_id','schema','scope_hash','staff','team']::text[]
 OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(envelope) key) IS DISTINCT FROM ARRAY['body','signature']::text[]
 OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(body) key) IS DISTINCT FROM ARRAY['database_transaction_id','event_attempt_id','expires_at','finality_effect_binding','issued_at','kid','nonce','ordered_finality_receipts','pending_effect_binding','preauthorization_event_hash','preauthorization_receipt_id','review_attempt_id','review_plan_binding','schema','scope_hash','signed_review_hash']::text[]
 OR effect->>'schema' IS DISTINCT FROM 'ctt.capture.finality-effect.v1'
 OR effect->>'column_schema' IS DISTINCT FROM 'ctt.capture.modeled-columns.v1'
 OR effect->>'scope_hash' IS DISTINCT FROM payload.scope_hash
 OR effect->>'review_attempt_id' IS DISTINCT FROM p_attempt_id::text
 OR effect->>'database_transaction_id' IS DISTINCT FROM tx_name
 OR effect->>'event_attempt_id' IS DISTINCT FROM expected_attempt
 OR effect->>'database_identity' IS DISTINCT FROM pin
 OR effect->>'database_oid' IS DISTINCT FROM db_oid::text
 OR effect->>'pg_top_xid' IS DISTINCT FROM pg_current_xact_id()::text
 OR body->>'schema' IS DISTINCT FROM 'ctt.capture.finality-authorization.v1'
 OR body->>'kid' IS DISTINCT FROM f.gate_kid OR body->>'scope_hash' IS DISTINCT FROM payload.scope_hash
 OR body->>'review_attempt_id' IS DISTINCT FROM p_attempt_id::text
 OR body->>'database_transaction_id' IS DISTINCT FROM tx_name
 OR body->>'event_attempt_id' IS DISTINCT FROM expected_attempt
 OR body->>'nonce' IS DISTINCT FROM f.nonce::text
 OR body->>'signed_review_hash' IS DISTINCT FROM payload.proof_sha256
 OR body->>'preauthorization_event_hash' IS DISTINCT FROM 'sha256:'||encode(public.digest(convert_to(public.regs07_canonical_jsonb(convert_from(payload.preauthorization_bytes,'UTF8')::jsonb->'roster_decision'),'UTF8'),'sha256'),'hex')
 OR body->>'preauthorization_receipt_id' IS DISTINCT FROM (convert_from(payload.preauthorization_bytes,'UTF8')::jsonb->'preauthorization_receipt'->>'receipt_id')
 OR body->>'review_plan_binding' IS DISTINCT FROM scope->>'staff_and_changes_binding'
 OR body->>'review_plan_binding' IS DISTINCT FROM f.review_plan_binding
 OR body->>'pending_effect_binding' IS DISTINCT FROM (convert_from(payload.gate_envelope,'UTF8')::jsonb->'body'->>'effect_binding')
 OR body->>'pending_effect_binding' IS DISTINCT FROM f.pending_effect_binding
 OR effect->>'pending_effect_binding' IS DISTINCT FROM f.pending_effect_binding
 OR body->>'finality_effect_binding' IS DISTINCT FROM f.finality_effect_binding
 OR body->>'issued_at' IS DISTINCT FROM floor(extract(epoch FROM f.issued_at))::bigint::text
 OR body->>'expires_at' IS DISTINCT FROM floor(extract(epoch FROM f.expires_at))::bigint::text
 OR NOT coalesce((body->>'preauthorization_event_hash')~'^sha256:[0-9a-f]{64}$',false)
 OR NOT coalesce((body->>'preauthorization_receipt_id')~'^sha256:[0-9a-f]{64}$',false)
 OR NOT coalesce(f.finality_effect_binding~'^hmac-sha256:[0-9a-f]{64}$',false)
 OR jsonb_typeof(effect->'players') IS DISTINCT FROM 'array'
 OR jsonb_typeof(effect->'staff') IS DISTINCT FROM 'array'
 OR jsonb_array_length(effect->'staff')<>2 THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 actual:=public.hmac(convert_to('ctt.capture.finality-effect.v1','UTF8')||decode('00','hex')||f.finality_effect_bytes,writer.secret,'sha256');
 candidate:=decode(substr(f.finality_effect_binding,13),'hex');
 FOR idx IN 0..31 LOOP diff:=diff|(get_byte(actual,idx)#get_byte(candidate,idx)); END LOOP;
 IF diff<>0 THEN RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 RETURN effect;
EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value OR character_not_in_repertoire
 OR numeric_value_out_of_range OR datetime_field_overflow OR null_value_not_allowed THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501';
END
$fn$;

CREATE FUNCTION ctt_staff_private.validate_capture_finality_rows(p_attempt_id uuid,p_complete boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE effect jsonb; pending jsonb; body jsonb; item jsonb; original jsonb; expected jsonb; observed jsonb;
 event jsonb; receipt jsonb; scope jsonb; refs jsonb:='[]'::jsonb; typed_pending jsonb; typed_active jsonb;
 kind text; tab regclass; item_id uuid; slot integer; ordinal bigint; columns text[]; present integer:=0;
BEGIN
 effect:=ctt_staff_private.check_capture_finality_proof(p_attempt_id);
 SELECT convert_from(p.effect_bytes,'UTF8')::jsonb INTO pending FROM ctt_staff_private.capture_payloads p WHERE attempt_id=p_attempt_id;
 SELECT convert_from(f.finality_envelope,'UTF8')::jsonb->'body' INTO body FROM ctt_staff_private.capture_finalities f WHERE attempt_id=p_attempt_id;
 SELECT convert_from(a.scope_bytes,'UTF8')::jsonb INTO scope FROM ctt_staff_private.capture_attempt_anchors a WHERE attempt_id=p_attempt_id;
 expected:=to_jsonb(jsonb_populate_record(NULL::public.copa_telmex_teams,pending->'team'));
 IF to_jsonb(jsonb_populate_record(NULL::public.copa_telmex_teams,effect->'team')) IS DISTINCT FROM expected
 OR effect->'team' IS DISTINCT FROM pending->'team' THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 SELECT to_jsonb(t) INTO observed FROM public.copa_telmex_teams t WHERE id=(pending->'team'->>'id')::uuid;
 IF observed IS DISTINCT FROM expected OR jsonb_array_length(effect->'players')<>jsonb_array_length(pending->'players') THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 FOR item,ordinal IN SELECT value,ordinality FROM jsonb_array_elements((effect->'players')||(effect->'staff')) WITH ORDINALITY LOOP
 IF ordinal<=jsonb_array_length(effect->'players') THEN
 kind:='PLAYER'; original:=pending->'players'->(ordinal::integer-1); slot:=(original->>'roster_index')::integer;
 IF item->>'kind' IS DISTINCT FROM kind OR item->>'slot' IS DISTINCT FROM slot::text
 OR slot NOT BETWEEN 1 AND 35
 OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(item) key) IS DISTINCT FROM ARRAY['active_row','event','kind','pending_row','receipt','row_id','slot']::text[] THEN RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 tab:='public.copa_telmex_players'::regclass;
 ELSE
 kind:='STAFF'; slot:=(ordinal-jsonb_array_length(effect->'players'))::integer;
 original:=pending->'staff'->(slot-1);
 IF item->>'slot' IS DISTINCT FROM slot::text OR item->>'role' IS DISTINCT FROM original->>'role'
 OR item->>'presence' IS DISTINCT FROM original->>'presence'
 OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(item) key) IS DISTINCT FROM ARRAY['active_row','event','pending_row','presence','receipt','role','row_id','slot']::text[] THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 IF original->>'presence'='ABSENT' THEN
 IF item->'row_id' IS DISTINCT FROM 'null'::jsonb OR item->'pending_row' IS DISTINCT FROM 'null'::jsonb
 OR item->'active_row' IS DISTINCT FROM 'null'::jsonb OR item->'event' IS DISTINCT FROM 'null'::jsonb
 OR item->'receipt' IS DISTINCT FROM 'null'::jsonb THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 CONTINUE; END IF;
 original:=original->'row'; tab:='public.copa_telmex_team_staff'::regclass;
 END IF;
 present:=present+1; item_id:=(original->>'id')::uuid;
 IF item->>'row_id' IS DISTINCT FROM item_id::text OR item->'pending_row' IS DISTINCT FROM original
 OR original->>'governance_state' IS DISTINCT FROM 'PENDING_FINALITY'
 OR original->'finality_receipt_id' IS DISTINCT FROM 'null'::jsonb
 OR original->>'preauthorization_receipt_id' IS DISTINCT FROM body->>'preauthorization_receipt_id' THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 SELECT array_agg(attname::text ORDER BY attname) INTO columns FROM pg_catalog.pg_attribute
 WHERE attrelid=tab AND attnum>0 AND NOT attisdropped;
 IF columns IS DISTINCT FROM (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(item->'pending_row') key)
 OR columns IS DISTINCT FROM (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(item->'active_row') key) THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 EXECUTE format('SELECT to_jsonb(jsonb_populate_record(NULL::%s,$1)),to_jsonb(jsonb_populate_record(NULL::%s,$2))',tab,tab)
 INTO typed_pending,typed_active USING item->'pending_row',item->'active_row';
 IF typed_active->>'governance_state' IS DISTINCT FROM 'ACTIVE'
 OR NOT coalesce((typed_active->>'finality_receipt_id')~'^sha256:[0-9a-f]{64}$',false)
 OR (typed_active-ARRAY['governance_state','finality_receipt_id','updated_at','postcommit_revision','postcommit_snapshot_hash'])
 IS DISTINCT FROM (typed_pending-ARRAY['governance_state','finality_receipt_id','updated_at','postcommit_revision','postcommit_snapshot_hash']) THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 IF kind='PLAYER' THEN
 IF (typed_active->>'updated_at')::timestamp IS DISTINCT FROM (to_timestamp((body->>'issued_at')::bigint) AT TIME ZONE 'UTC')
 OR typed_pending->>'postcommit_revision' IS DISTINCT FROM '0'
 OR typed_active IS DISTINCT FROM ctt_staff_private.project_capture_finality_player(item->'active_row') THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 ELSE
 IF (typed_active->>'updated_at')::timestamptz IS DISTINCT FROM to_timestamp((body->>'issued_at')::bigint) THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 END IF;
 event:=item->'event'; receipt:=item->'receipt';
 IF event->>'event_type' IS DISTINCT FROM (CASE kind WHEN 'PLAYER' THEN 'samchat_registration_player_finality_attestation_v2' ELSE 'samchat_registration_staff_finality_attestation_v2' END)
 OR event->>'decision' IS DISTINCT FROM (CASE kind WHEN 'PLAYER' THEN 'ACTIVATE_PLAYER' ELSE 'ACTIVATE_STAFF' END)
 OR event->>(CASE kind WHEN 'PLAYER' THEN 'player_id' ELSE 'staff_id' END) IS DISTINCT FROM item_id::text
 OR event->>(CASE kind WHEN 'PLAYER' THEN 'player_slot' ELSE 'staff_slot' END) IS DISTINCT FROM slot::text
 OR event->>'database_transaction_id' IS DISTINCT FROM effect->>'database_transaction_id'
 OR event->>'attempt_id' IS DISTINCT FROM effect->>'event_attempt_id'
 OR event->>'preauthorization_receipt_id' IS DISTINCT FROM body->>'preauthorization_receipt_id'
 OR event->>'team_id' IS DISTINCT FROM pending->'team'->>'id'
 OR event->>'tenant_id' IS DISTINCT FROM scope->>'tenant_id'
 OR event->>'tournament_edition_id' IS DISTINCT FROM scope->>'edition_id'
 OR event->>'tournament_slug' IS DISTINCT FROM scope->>'tournament_slug'
 OR event->>'session_id' IS DISTINCT FROM scope->>'session_id'
 OR event->>'draft_id' IS DISTINCT FROM scope->>'draft_id'
 OR event->'draft_version' IS DISTINCT FROM scope->'draft_version'
 OR event->>'policy_hash' IS DISTINCT FROM scope->>'policy_hash'
 OR event->>'workflow_id' IS DISTINCT FROM 'samchat_registration_identity_v2'
 OR event->>'effective_sal' IS DISTINCT FROM 'SAL-3'
 OR event->>'policy_id' IS DISTINCT FROM 'samchat_registration_identity_policy_v2'
 OR event->>'roster_draft_binding' IS DISTINCT FROM original->>'roster_draft_binding'
 OR (event->>'timestamp')::timestamptz IS DISTINCT FROM to_timestamp((body->>'issued_at')::bigint)
 OR (kind='STAFF' AND event->>'staff_role' IS DISTINCT FROM item->>'role')
 OR event->'cas_succeeded' IS DISTINCT FROM 'true'::jsonb
 OR event->'blocking_incident_count' IS DISTINCT FROM '0'::jsonb
 OR receipt->>'receipt_id' IS DISTINCT FROM typed_active->>'finality_receipt_id'
 OR receipt->>'event_type' IS DISTINCT FROM event->>'event_type'
 OR receipt->>'tenant_id' IS DISTINCT FROM event->>'tenant_id'
 OR receipt->>'event_hash' IS DISTINCT FROM 'sha256:'||encode(public.digest(convert_to(public.regs07_canonical_jsonb(event),'UTF8'),'sha256'),'hex')
 OR receipt->>'issuer' IS DISTINCT FROM 'zaubern-evidence-bus' OR receipt->>'alg' IS DISTINCT FROM 'Ed25519'
 OR receipt->'verified' IS DISTINCT FROM 'true'::jsonb THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 refs:=refs||jsonb_build_array(jsonb_build_object('kind',kind,'row_id',item_id::text,'slot',slot,
 'event_hash',receipt->>'event_hash','receipt_id',receipt->>'receipt_id','operation_id',event->>'operation_id'));
 EXECUTE format('SELECT to_jsonb(t) FROM %s t WHERE id=$1',tab) INTO observed USING item_id;
 IF observed IS DISTINCT FROM (CASE WHEN p_complete THEN typed_active ELSE typed_pending END) THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 IF p_complete AND kind='PLAYER' AND (SELECT count(*) FROM public.copa_telmex_registration_postcommit_entity_versions v
 WHERE v.entity_type='PLAYER' AND v.entity_id=item_id AND v.team_id=(pending->'team'->>'id')::uuid
 AND v.revision=1 AND v.mutation_type='GENESIS' AND v.predecessor_snapshot_hash IS NULL AND v.execution_id IS NULL
 AND v.snapshot_hash=typed_active->>'postcommit_snapshot_hash'
 AND v.snapshot::jsonb=public.regs07_player_snapshot(jsonb_populate_record(NULL::public.copa_telmex_players,item->'active_row')))<>1 THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 END LOOP;
 IF present<1 OR refs IS DISTINCT FROM body->'ordered_finality_receipts'
 OR (SELECT count(DISTINCT value->>'receipt_id') FROM jsonb_array_elements(refs))<>present
 OR (SELECT count(DISTINCT value->>'operation_id') FROM jsonb_array_elements(refs))<>present THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
END
$fn$;

CREATE FUNCTION ctt_staff_private.record_capture_finality(p_attempt_id uuid,p_effect_bytes bytea,p_envelope bytea)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE effect jsonb; body jsonb; kid text;
BEGIN
 kid:=ctt_staff_private.current_finality_writer();
 IF NOT(convert_from(p_effect_bytes,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(p_envelope,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_results WHERE attempt_id=p_attempt_id) THEN
 RAISE EXCEPTION 'CTT finality denied' USING ERRCODE='42501'; END IF;
 effect:=convert_from(p_effect_bytes,'UTF8')::jsonb; body:=convert_from(p_envelope,'UTF8')::jsonb->'body';
 INSERT INTO ctt_staff_private.capture_finalities(attempt_id,database_identity,database_oid,pg_top_xid,
 database_transaction_id,event_attempt_id,nonce,scope_hash,review_plan_binding,pending_effect_binding,
 finality_effect_binding,finality_effect_bytes,finality_envelope,issued_at,expires_at,writer_role,kid,gate_kid)
 VALUES(p_attempt_id,effect->>'database_identity',(effect->>'database_oid')::oid,(effect->>'pg_top_xid')::xid8,
 effect->>'database_transaction_id',effect->>'event_attempt_id',(body->>'nonce')::uuid,body->>'scope_hash',
 body->>'review_plan_binding',body->>'pending_effect_binding',body->>'finality_effect_binding',
 p_effect_bytes,p_envelope,to_timestamp((body->>'issued_at')::bigint),to_timestamp((body->>'expires_at')::bigint),session_user,kid,body->>'kid');
 PERFORM ctt_staff_private.validate_capture_finality_rows(p_attempt_id,false);
END
$fn$;

CREATE FUNCTION ctt_staff_private.finality_update_authorized(p_table text,p_old jsonb,p_new jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE candidate record; effect jsonb; item jsonb; typed_old jsonb; typed_new jsonb; hits integer:=0; tab regclass;
BEGIN
 IF p_table NOT IN('copa_telmex_players','copa_telmex_team_staff') THEN RETURN false; END IF;
 tab:=to_regclass('public.'||p_table);
 FOR candidate IN SELECT f.attempt_id FROM ctt_staff_private.capture_finalities f
 WHERE f.writer_role=session_user AND f.pg_top_xid=pg_current_xact_id()
 AND f.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint LOOP
 effect:=ctt_staff_private.check_capture_finality_proof(candidate.attempt_id);
 FOR item IN SELECT value FROM jsonb_array_elements(CASE p_table WHEN 'copa_telmex_players' THEN effect->'players' ELSE effect->'staff' END) LOOP
 IF item->>'row_id' IS DISTINCT FROM p_old->>'id' THEN CONTINUE; END IF;
 EXECUTE format('SELECT to_jsonb(jsonb_populate_record(NULL::%s,$1)),to_jsonb(jsonb_populate_record(NULL::%s,$2))',tab,tab)
 INTO typed_old,typed_new USING item->'pending_row',item->'active_row';
 IF p_old IS NOT DISTINCT FROM typed_old AND p_new IS NOT DISTINCT FROM typed_new THEN hits:=hits+1; END IF;
 END LOOP; END LOOP;
 RETURN hits=1;
END
$fn$;

CREATE FUNCTION ctt_staff_private.check_capture_finality_complete()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
BEGIN
 PERFORM ctt_staff_private.validate_capture_finality_rows(NEW.attempt_id,true);
 RETURN NEW;
END
$fn$;
CREATE FUNCTION ctt_staff_private.check_capture_finality_transaction()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE selected_attempt uuid;
BEGIN
 IF TG_TABLE_NAME='review_challenge_consumptions' THEN
 SELECT a.attempt_id INTO selected_attempt FROM ctt_staff_private.capture_attempt_anchors a
 JOIN ctt_staff_private.capture_payloads p USING(attempt_id) WHERE a.challenge_id=NEW.challenge_id;
 IF selected_attempt IS NULL THEN RETURN NEW; END IF;
 ELSE selected_attempt:=NEW.attempt_id;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.capture_results WHERE attempt_id=selected_attempt) THEN
 RAISE EXCEPTION 'CTT finality transaction incomplete' USING ERRCODE='42501'; END IF;
 PERFORM ctt_staff_private.validate_capture_finality_rows(selected_attempt,true);
 RETURN NEW;
END
$fn$;
CREATE TRIGGER ctt_capture_finality_result_initial BEFORE INSERT ON ctt_staff_private.capture_results
 FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.check_capture_finality_complete();
CREATE CONSTRAINT TRIGGER ctt_capture_finality_result_commit AFTER INSERT ON ctt_staff_private.capture_results
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.check_capture_finality_complete();
CREATE CONSTRAINT TRIGGER ctt_capture_finality_consumption_commit AFTER INSERT ON ctt_staff_private.review_challenge_consumptions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.check_capture_finality_transaction();
CREATE CONSTRAINT TRIGGER ctt_capture_finality_proof_commit AFTER INSERT ON ctt_staff_private.capture_finalities
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.check_capture_finality_transaction();

-- Preserve and narrowly extend the previous function bodies; rollback restores
-- these exact definitions and refuses all configured/evidentiary state.
DO $extend$
DECLARE v_signature text; definition text; old_piece text; new_piece text; changed text;
BEGIN
 FOREACH v_signature IN ARRAY ARRAY['ctt_staff_private.deny_staff_write()','ctt_staff_private.guard_legacy_v2_boundary()'] LOOP
 definition:=pg_get_functiondef(to_regprocedure(v_signature));
 INSERT INTO ctt_staff_private.finality_migration_baseline(signature,original_definition,original_hash)
 VALUES(v_signature,definition,md5(definition));
 IF v_signature='ctt_staff_private.deny_staff_write()' THEN
 old_piece:=E'BEGIN\n  IF TG_OP<>''INSERT''';
 new_piece:=E'BEGIN\n  IF TG_OP=''UPDATE'' AND ctt_staff_private.finality_update_authorized(TG_TABLE_NAME,to_jsonb(OLD),to_jsonb(NEW)) THEN RETURN NEW; END IF;\n  IF TG_OP<>''INSERT''';
 ELSE
 old_piece:=E'  PERFORM ctt_staff_private.lock_legacy_reference_ids(ids);';
 new_piece:=old_piece||E'\n  IF TG_OP=''UPDATE'' AND TG_TABLE_NAME=''copa_telmex_players'' AND ctt_staff_private.finality_update_authorized(TG_TABLE_NAME,to_jsonb(OLD),to_jsonb(NEW)) THEN RETURN NEW; END IF;';
 END IF;
 IF length(definition)-length(replace(definition,old_piece,''))<>length(old_piece) THEN
 RAISE EXCEPTION 'CTT finality baseline body mismatch'; END IF;
 changed:=replace(definition,old_piece,new_piece); EXECUTE changed;
 UPDATE ctt_staff_private.finality_migration_baseline SET installed_hash=md5(pg_get_functiondef(to_regprocedure(v_signature))) WHERE finality_migration_baseline.signature=v_signature;
 END LOOP;
END
$extend$;
DO $functions$
DECLARE signature text;
BEGIN
 FOREACH signature IN ARRAY ARRAY['current_finality_writer()','load_capture_finality_inputs(uuid)',
 'load_capture_finality_context(uuid)','project_capture_finality_player(jsonb)',
 'check_capture_finality_proof(uuid)','validate_capture_finality_rows(uuid,boolean)',
 'record_capture_finality(uuid,bytea,bytea)','finality_update_authorized(text,jsonb,jsonb)',
 'check_capture_finality_complete()','check_capture_finality_transaction()'] LOOP
 EXECUTE 'ALTER FUNCTION ctt_staff_private.'||signature||' OWNER TO samchat_ctt_staff_owner';
 EXECUTE 'REVOKE ALL ON FUNCTION ctt_staff_private.'||signature||' FROM PUBLIC,devnous_user';
 END LOOP;
END
$functions$;
DO $postflight$
DECLARE owner_id oid; item record;
BEGIN
 SELECT oid INTO owner_id FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner';
 FOR item IN SELECT * FROM (VALUES('finality_migration_baseline'),('finality_database_identity'),
 ('finality_inputs'),('capture_finalities')) t(name) LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass('ctt_staff_private.'||item.name)
 AND c.relowner=owner_id AND NOT c.relrowsecurity AND NOT EXISTS(
 SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a WHERE a.grantee<>owner_id)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid
 AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL)) THEN
 RAISE EXCEPTION 'CTT finality installed ACL invalid'; END IF;
 END LOOP;
 FOR item IN SELECT * FROM (VALUES('current_finality_writer()'),('load_capture_finality_inputs(uuid)'),
 ('load_capture_finality_context(uuid)'),('project_capture_finality_player(jsonb)'),
 ('check_capture_finality_proof(uuid)'),('validate_capture_finality_rows(uuid,boolean)'),
 ('record_capture_finality(uuid,bytea,bytea)'),('finality_update_authorized(text,jsonb,jsonb)'),
 ('check_capture_finality_complete()'),('check_capture_finality_transaction()')) t(signature) LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=pg_catalog.to_regprocedure('ctt_staff_private.'||item.signature)
 AND p.proowner=owner_id AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE a.grantee<>owner_id)) THEN RAISE EXCEPTION 'CTT finality installed function ACL invalid'; END IF;
 END LOOP;
END
$postflight$;
COMMIT;
