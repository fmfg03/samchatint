-- Protected preparation/admission only. No credentials, keys or role grants seeded.
BEGIN;
DO $preflight$
DECLARE owner_id oid; item record;
BEGIN
 SELECT oid INTO owner_id FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner'
 AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
 AND NOT rolreplication AND NOT rolbypassrls;
 IF owner_id IS NULL OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id)
 OR has_schema_privilege('devnous_user','ctt_staff_private','CREATE,USAGE')
 OR current_setting('max_prepared_transactions')::int<>0 THEN RAISE EXCEPTION 'CTT proposal isolation invalid'; END IF;
 FOR item IN SELECT * FROM (VALUES('review_services'),('review_account_links'),('review_principals'),
 ('review_credentials'),('review_faculties'),('source_admissions'),('source_admission_documents'),
 ('source_admission_pages'),('capture_attempt_anchors'),('capture_payloads'),('capture_results'),
 ('finality_inputs'),('capture_finalities'),('review_assertion_issuances'),('issuer_proofs')) v(name) LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=to_regclass('ctt_staff_private.'||item.name)
 AND c.relowner=owner_id AND c.relkind='r' AND NOT c.relrowsecurity
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a WHERE a.grantee<>owner_id)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL)) THEN
 RAISE EXCEPTION 'CTT proposal dependency invalid'; END IF; END LOOP;
 FOR item IN SELECT * FROM (VALUES('ctt_staff_private.current_review_service()'),
 ('ctt_staff_private.load_review_assertion_context(uuid)'),('ctt_staff_private.load_capture_finality_context(uuid)'),
 ('ctt_staff_private.guard_legacy_v2_boundary()'),('ctt_staff_private.record_capture_result(uuid,uuid,text)')) v(signature) LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(item.signature)
 AND p.proowner=owner_id AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]) THEN
 RAISE EXCEPTION 'CTT proposal function dependency invalid'; END IF; END LOOP;
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
 JOIN pg_catalog.pg_roles r ON r.oid=e.extowner JOIN pg_catalog.pg_proc p ON p.oid=d.objid
 JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pgcrypto' AND r.rolsuper
 AND n.nspname='public' AND p.proowner=e.extowner AND d.objid='public.digest(bytea,text)'::regprocedure AND d.deptype='e') THEN
 RAISE EXCEPTION 'CTT proposal digest dependency invalid'; END IF;
 FOR item IN SELECT * FROM (VALUES
 ('ctt_staff_private.guard_legacy_v2_boundary()','1504531d3bc2bf4fe0fe8971485d8297'),
 ('ctt_staff_private.load_capture_finality_context(uuid)','a7dc54f84b23859f60611cfd7fac8858'),
 ('ctt_staff_private.load_review_assertion_context(uuid)','c95cdca6ea687fead3eebcb21f3d4124')) v(signature,hash) LOOP
 IF md5(pg_get_functiondef(to_regprocedure(item.signature))) IS DISTINCT FROM item.hash THEN
 RAISE EXCEPTION 'CTT proposal dependency fingerprint invalid'; END IF; END LOOP;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_anchors)
 OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_payloads) THEN
 RAISE EXCEPTION 'CTT proposal cutover refuses unadmitted attempts'; END IF;
END
$preflight$;

CREATE TABLE ctt_staff_private.review_proposal_baseline(
 signature text PRIMARY KEY, original_definition text NOT NULL, original_hash text NOT NULL, installed_hash text
);
CREATE TABLE ctt_staff_private.review_proposals(
 proposal_id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES public.copa_telmex_registration_review_sessions(id) ON DELETE RESTRICT,
 previous_proposal_id uuid REFERENCES ctt_staff_private.review_proposals(proposal_id) ON DELETE RESTRICT,
 revision bigint NOT NULL CHECK(revision>0),backend_pin text NOT NULL,account_id uuid NOT NULL,
 principal_id uuid NOT NULL REFERENCES ctt_staff_private.review_principals(principal_id) ON DELETE RESTRICT,
 credential_id bytea NOT NULL REFERENCES ctt_staff_private.review_credentials(credential_id) ON DELETE RESTRICT,
 account_version bigint NOT NULL,principal_version bigint NOT NULL,faculty_version bigint NOT NULL,credential_version bigint NOT NULL,
 scope_bytes bytea NOT NULL CHECK(octet_length(scope_bytes) BETWEEN 2 AND 262144),scope_hash text NOT NULL,
 review_bytes bytea NOT NULL CHECK(octet_length(review_bytes) BETWEEN 2 AND 4194304),
 evidence_bytes bytea NOT NULL CHECK(octet_length(evidence_bytes) BETWEEN 2 AND 4194304),
 preview_bytes bytea NOT NULL CHECK(octet_length(preview_bytes) BETWEEN 2 AND 4194304),
 validation_bytes bytea NOT NULL CHECK(octet_length(validation_bytes) BETWEEN 2 AND 4194304),
 raw_snapshot_bytes bytea NOT NULL CHECK(octet_length(raw_snapshot_bytes) BETWEEN 2 AND 4194304),
 capture_nonce uuid NOT NULL UNIQUE,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(session_id,revision)
);
CREATE TABLE ctt_staff_private.review_proposal_attempts(
 attempt_id uuid PRIMARY KEY,proposal_id uuid NOT NULL REFERENCES ctt_staff_private.review_proposals(proposal_id) ON DELETE RESTRICT,
 challenge_id uuid NOT NULL UNIQUE REFERENCES ctt_staff_private.review_challenges(challenge_id) ON DELETE RESTRICT,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(attempt_id) REFERENCES ctt_staff_private.capture_attempt_anchors(attempt_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE ctt_staff_private.review_proposal_heads(
 session_id uuid PRIMARY KEY REFERENCES public.copa_telmex_registration_review_sessions(id) ON DELETE RESTRICT,
 proposal_id uuid REFERENCES ctt_staff_private.review_proposals(proposal_id) ON DELETE RESTRICT,
 current_attempt_id uuid REFERENCES ctt_staff_private.review_proposal_attempts(attempt_id) ON DELETE RESTRICT,
 revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0)
);
CREATE TABLE ctt_staff_private.review_proposal_events(
 event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),session_id uuid NOT NULL,account_id uuid NOT NULL,backend_pin text NOT NULL,
 action text NOT NULL CHECK(action IN('PUBLISH','CHALLENGE','CANCEL')),revision bigint NOT NULL,
 proposal_id uuid,previous_proposal_id uuid,attempt_id uuid,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ctt_staff_private.review_materializations(
 attempt_id uuid PRIMARY KEY REFERENCES ctt_staff_private.capture_payloads(attempt_id) ON DELETE RESTRICT,
 signed_review bytea NOT NULL CHECK(octet_length(signed_review) BETWEEN 2 AND 8192),
 proof_sha256 text NOT NULL,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),recorded_by name NOT NULL DEFAULT session_user
);
DO $tables$
DECLARE tab text;
BEGIN
 FOREACH tab IN ARRAY ARRAY['review_proposal_baseline','review_proposals','review_proposal_attempts','review_proposal_heads','review_proposal_events','review_materializations'] LOOP
 EXECUTE format('ALTER TABLE ctt_staff_private.%I OWNER TO samchat_ctt_staff_owner',tab);
 EXECUTE format('REVOKE ALL ON ctt_staff_private.%I FROM PUBLIC,devnous_user',tab);
 IF tab NOT IN('review_proposal_heads','review_proposal_baseline') THEN
 EXECUTE format('CREATE TRIGGER ctt_proposal_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.%I FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',tab); END IF;
 EXECUTE format('CREATE TRIGGER ctt_proposal_no_truncate BEFORE TRUNCATE ON ctt_staff_private.%I FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',tab);
 END LOOP;
END
$tables$;

CREATE FUNCTION ctt_staff_private.review_document_session(p_edition uuid,p_document text)
RETURNS uuid LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE raw bytea;
BEGIN
 raw:=public.digest(uuid_send('6ba7b811-9dad-11d1-80b4-00c04fd430c8'::uuid)||convert_to('ctt-registration-document:'||p_edition::text||':'||p_document,'UTF8'),'sha1');
 raw:=set_byte(raw,6,(get_byte(raw,6)&15)|80);raw:=set_byte(raw,8,(get_byte(raw,8)&63)|128);
 RETURN encode(substring(raw FROM 1 FOR 16),'hex')::uuid;
END
$fn$;

CREATE FUNCTION ctt_staff_private.review_photo_duplicate(p_sha text,p_ahash text,p_other_sha text,p_other_ahash text)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE a bytea; b bytea; distance int:=0; i int;
BEGIN
 IF nullif(p_sha,'') IS NOT NULL AND nullif(p_other_sha,'') IS NOT NULL AND p_sha=p_other_sha THEN RETURN true; END IF;
 IF nullif(p_ahash,'') IS NULL OR nullif(p_other_ahash,'') IS NULL THEN RETURN false; END IF;
 IF p_ahash!~'^[0-9a-fA-F]{16}$' OR p_other_ahash!~'^[0-9a-fA-F]{16}$' THEN
 RAISE EXCEPTION 'CTT photo integrity unavailable' USING ERRCODE='42501'; END IF;
 a:=decode(p_ahash,'hex');b:=decode(p_other_ahash,'hex');
 FOR i IN 0..7 LOOP distance:=distance+bit_count((get_byte(a,i)#get_byte(b,i))::bit(8)); END LOOP;
 RETURN distance<=4;
END
$fn$;

CREATE FUNCTION ctt_staff_private.check_review_proposal_policy(p_id uuid,p_capture boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE proposal ctt_staff_private.review_proposals%ROWTYPE; validation jsonb; policy jsonb; plan jsonb;
 result jsonb; incident jsonb; row_value jsonb; other jsonb; minimum int; eligible int:=0; blocks_player boolean;
 seen int[]:=ARRAY[]::int[]; idx int; expected int[]; is_blocking boolean; ordinal bigint;
BEGIN
 SELECT * INTO proposal FROM ctt_staff_private.review_proposals WHERE proposal_id=p_id;
 IF proposal.proposal_id IS NULL THEN RAISE EXCEPTION 'CTT proposal unavailable' USING ERRCODE='42501'; END IF;
 validation:=convert_from(proposal.validation_bytes,'UTF8')::jsonb;policy:=validation->'incident_policy';
 plan:=convert_from(proposal.review_bytes,'UTF8')::jsonb;
 IF plan->>'schema' IS DISTINCT FROM 'ctt.capture.review-plan.v1' THEN
 RAISE EXCEPTION 'CTT incomplete proposal cannot authorize' USING ERRCODE='42501'; END IF;
 IF (convert_from(proposal.preview_bytes,'UTF8')::jsonb)->'ready_to_approve' IS DISTINCT FROM 'true'::jsonb
 OR jsonb_typeof(validation->'blockers') IS DISTINCT FROM 'array' OR jsonb_array_length(validation->'blockers')<>0
 OR jsonb_typeof(policy->'player_results') IS DISTINCT FROM 'array'
 OR jsonb_array_length(policy->'player_results')<>jsonb_array_length(plan->'players')
 OR jsonb_array_length(plan->'players') NOT BETWEEN 16 AND 35
 OR NOT coalesce((policy->>'minimum_roster')~'^[0-9]+$',false)
 OR (policy->>'minimum_roster')::int NOT BETWEEN 16 AND 35 THEN
 RAISE EXCEPTION 'CTT proposal has unresolved incidents' USING ERRCODE='42501'; END IF;
 minimum:=(policy->>'minimum_roster')::int;
 SELECT array_agg((value->>'roster_index')::int ORDER BY (value->>'roster_index')::int) INTO expected FROM jsonb_array_elements(plan->'players');
 FOR result IN SELECT value FROM jsonb_array_elements(policy->'player_results') LOOP
 IF jsonb_typeof(result->'player_index') IS DISTINCT FROM 'number' OR NOT coalesce((result->>'player_index')~'^[0-9]+$',false)
 OR jsonb_typeof(result->'incidents') IS DISTINCT FROM 'array' OR jsonb_typeof(result->'counts_toward_minimum') IS DISTINCT FROM 'boolean'
 OR NOT coalesce(result->>'eligibility_status' IN('CLEARED','INCIDENT_PENDING'),false) THEN RAISE EXCEPTION 'CTT proposal policy invalid' USING ERRCODE='42501'; END IF;
 idx:=(result->>'player_index')::int;
 IF NOT idx=ANY(expected) OR idx=ANY(seen) THEN RAISE EXCEPTION 'CTT proposal policy invalid' USING ERRCODE='42501'; END IF;
 seen:=array_append(seen,idx); blocks_player:=false;
 FOR incident IN SELECT value FROM jsonb_array_elements(result->'incidents') LOOP
 IF jsonb_typeof(incident->'blocks_player_eligibility') IS DISTINCT FROM 'boolean'
 OR jsonb_typeof(incident->'blocks_team_registration') IS DISTINCT FROM 'boolean' THEN
 RAISE EXCEPTION 'CTT proposal policy invalid' USING ERRCODE='42501'; END IF;
 IF (incident->>'blocks_team_registration')::boolean THEN RAISE EXCEPTION 'CTT proposal has unresolved incidents' USING ERRCODE='42501'; END IF;
 blocks_player:=blocks_player OR (incident->>'blocks_player_eligibility')::boolean;
 END LOOP;
 IF result->'counts_toward_minimum' IS DISTINCT FROM to_jsonb(NOT blocks_player)
 OR (result->>'eligibility_status'='CLEARED' AND jsonb_array_length(result->'incidents')<>0)
 OR blocks_player THEN RAISE EXCEPTION 'CTT proposal has unresolved incidents' USING ERRCODE='42501'; END IF;
 IF NOT blocks_player THEN eligible:=eligible+1; END IF;
 END LOOP;
 IF eligible<minimum THEN RAISE EXCEPTION 'CTT proposal minimum roster unresolved' USING ERRCODE='42501'; END IF;
 IF p_capture THEN
 IF NOT pg_try_advisory_xact_lock(1129600050,0) THEN RAISE EXCEPTION 'CTT registration capture busy' USING ERRCODE='42501'; END IF;
 FOR row_value,ordinal IN SELECT value,ordinality FROM jsonb_array_elements(plan->'players') WITH ORDINALITY LOOP
 FOR other IN SELECT value FROM jsonb_array_elements(ctt_staff_private.review_existing_photos(plan->'team'->>'tournament_slug',(plan->'team'->>'id')::uuid))
 UNION ALL SELECT value FROM jsonb_array_elements(plan->'players') WITH ORDINALITY earlier(value,position) WHERE position<ordinal LOOP
 IF ctt_staff_private.review_photo_duplicate(row_value->>'photo_sha256',row_value->>'photo_ahash',other->>'photo_sha256',other->>'photo_ahash') THEN
 RAISE EXCEPTION 'CTT proposal duplicate photo unresolved' USING ERRCODE='42501'; END IF;
 END LOOP; END LOOP;
 END IF;
END
$fn$;

CREATE FUNCTION ctt_staff_private.assert_current_review_proposal(p_attempt uuid,p_allow_committed boolean DEFAULT false,p_capture boolean DEFAULT false)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE proposal ctt_staff_private.review_proposals%ROWTYPE; head ctt_staff_private.review_proposal_heads%ROWTYPE;
 link ctt_staff_private.review_account_links%ROWTYPE; principal ctt_staff_private.review_principals%ROWTYPE;
 credential ctt_staff_private.review_credentials%ROWTYPE; faculty ctt_staff_private.review_faculties%ROWTYPE;
 scope jsonb; draft public.copa_telmex_registration_review_drafts%ROWTYPE; session_row public.copa_telmex_registration_review_sessions%ROWTYPE;
BEGIN
 SELECT p.* INTO proposal FROM ctt_staff_private.review_proposal_attempts a JOIN ctt_staff_private.review_proposals p USING(proposal_id) WHERE a.attempt_id=p_attempt;
 IF proposal.proposal_id IS NULL THEN RAISE EXCEPTION 'CTT admitted proposal required' USING ERRCODE='42501'; END IF;
 scope:=convert_from(proposal.scope_bytes,'UTF8')::jsonb;
 SELECT * INTO principal FROM ctt_staff_private.review_principals p WHERE p.principal_id=proposal.principal_id FOR SHARE;
 SELECT * INTO faculty FROM ctt_staff_private.review_faculties f WHERE f.principal_id=proposal.principal_id
 AND f.tenant_id=scope->>'tenant_id' AND f.edition_id=(scope->>'edition_id')::uuid AND f.faculty='capture_staff_v2' FOR SHARE;
 SELECT * INTO credential FROM ctt_staff_private.review_credentials c WHERE c.credential_id=proposal.credential_id AND c.principal_id=proposal.principal_id FOR SHARE;
 SELECT * INTO link FROM ctt_staff_private.review_account_links l WHERE l.backend_pin=proposal.backend_pin AND l.account_id=proposal.account_id
 AND l.tenant_id=scope->>'tenant_id' AND l.edition_id=(scope->>'edition_id')::uuid FOR SHARE;
 IF NOT coalesce(principal.active,false) OR NOT coalesce(link.active,false) OR faculty.principal_id IS NULL OR credential.credential_id IS NULL
 OR principal.authority_version IS DISTINCT FROM proposal.principal_version OR faculty.authority_version IS DISTINCT FROM proposal.faculty_version
 OR credential.authority_version IS DISTINCT FROM proposal.credential_version OR link.authority_version IS DISTINCT FROM proposal.account_version
 OR link.principal_id IS DISTINCT FROM proposal.principal_id OR link.actor_role IS DISTINCT FROM scope->>'actor_role'
 OR faculty.revoked_at IS NOT NULL OR credential.revoked_at IS NOT NULL
 OR clock_timestamp()<faculty.valid_from OR clock_timestamp()>=faculty.valid_until THEN
 RAISE EXCEPTION 'CTT proposal authority stale' USING ERRCODE='42501'; END IF;
 SELECT * INTO head FROM ctt_staff_private.review_proposal_heads h WHERE h.session_id=proposal.session_id FOR SHARE;
 IF head.proposal_id IS DISTINCT FROM proposal.proposal_id OR head.current_attempt_id IS DISTINCT FROM p_attempt THEN
 RAISE EXCEPTION 'CTT proposal head stale' USING ERRCODE='42501'; END IF;
 SELECT * INTO session_row FROM public.copa_telmex_registration_review_sessions s WHERE s.id=proposal.session_id FOR SHARE;
 SELECT * INTO draft FROM public.copa_telmex_registration_review_drafts d WHERE d.session_id=proposal.session_id
 ORDER BY d.draft_version DESC,d.created_at DESC LIMIT 1 FOR SHARE;
 IF draft.id::text IS DISTINCT FROM scope->>'draft_id' OR draft.draft_version::text IS DISTINCT FROM scope->>'draft_version'
 OR draft.content_hash IS DISTINCT FROM scope->>'draft_content_hash'
 OR ctt_staff_private.review_raw_snapshot(draft) IS DISTINCT FROM convert_from(proposal.raw_snapshot_bytes,'UTF8')::jsonb
 OR session_row.status IN('rejected','cancelled','error') OR (session_row.committed_team_id IS NOT NULL AND NOT p_allow_committed)
 OR (session_row.committed_team_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM ctt_staff_private.capture_results r WHERE r.attempt_id=p_attempt AND r.team_id=session_row.committed_team_id)) THEN
 RAISE EXCEPTION 'CTT proposal source stale' USING ERRCODE='42501'; END IF;
 PERFORM ctt_staff_private.check_review_proposal_policy(proposal.proposal_id,p_capture);
END
$fn$;

CREATE FUNCTION ctt_staff_private.read_review_proposal(p_account uuid,p_session uuid,p_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE context jsonb; proposal ctt_staff_private.review_proposals%ROWTYPE;
BEGIN
 context:=ctt_staff_private.load_review_proposal_context(p_account,p_session);
 IF p_id IS NULL THEN p_id:=(context->>'head_proposal_id')::uuid; END IF;
 IF p_id IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO proposal FROM ctt_staff_private.review_proposals WHERE proposal_id=p_id;
 IF proposal.session_id IS DISTINCT FROM p_session OR proposal.account_id IS DISTINCT FROM p_account
 OR proposal.backend_pin IS DISTINCT FROM context->>'backend_pin'
 OR proposal.principal_id::text IS DISTINCT FROM context->>'principal_id'
 OR proposal.account_version::text IS DISTINCT FROM context->>'account_version'
 OR proposal.principal_version::text IS DISTINCT FROM context->>'principal_version'
 OR proposal.faculty_version::text IS DISTINCT FROM context->>'faculty_version'
 OR proposal.credential_version::text IS DISTINCT FROM context->>'credential_version'
 OR proposal.raw_snapshot_bytes IS NULL OR convert_from(proposal.raw_snapshot_bytes,'UTF8')::jsonb IS DISTINCT FROM context->'raw_snapshot' THEN
 RAISE EXCEPTION 'CTT proposal unavailable' USING ERRCODE='42501'; END IF;
 RETURN ctt_staff_private.review_proposal_json(p_id);
END
$fn$;

CREATE FUNCTION ctt_staff_private.create_proposal_challenge(p_account uuid,p_session uuid,p_id uuid,p_random bytea,p_issued bigint,p_expires bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE context jsonb; proposal ctt_staff_private.review_proposals%ROWTYPE; head ctt_staff_private.review_proposal_heads%ROWTYPE;
 scope jsonb; challenge_uuid uuid:=gen_random_uuid(); attempt_uuid uuid:=gen_random_uuid(); f ctt_staff_private.review_faculties%ROWTYPE;
BEGIN
 context:=ctt_staff_private.load_review_proposal_context(p_account,p_session);
 PERFORM ctt_staff_private.read_review_proposal(p_account,p_session,p_id);
 SELECT * INTO proposal FROM ctt_staff_private.review_proposals WHERE proposal_id=p_id;
 SELECT * INTO head FROM ctt_staff_private.review_proposal_heads h WHERE h.session_id=p_session FOR UPDATE NOWAIT;
 IF head.proposal_id IS DISTINCT FROM p_id OR (context->'session'->>'committed_team_id') IS NOT NULL
 OR octet_length(p_random)<>32 OR p_issued IS NULL OR p_expires IS NULL OR p_expires-p_issued NOT BETWEEN 1 AND 300
 OR p_issued>floor(extract(epoch FROM clock_timestamp()))::bigint
 OR p_issued<floor(extract(epoch FROM clock_timestamp()))::bigint-30 OR p_expires<=floor(extract(epoch FROM clock_timestamp()))::bigint THEN
 RAISE EXCEPTION 'CTT proposal challenge denied' USING ERRCODE='42501'; END IF;
 PERFORM ctt_staff_private.check_review_proposal_policy(p_id,false);
 scope:=convert_from(proposal.scope_bytes,'UTF8')::jsonb;
 SELECT * INTO f FROM ctt_staff_private.review_faculties WHERE principal_id=proposal.principal_id
 AND tenant_id=scope->>'tenant_id' AND edition_id=(scope->>'edition_id')::uuid AND faculty='capture_staff_v2';
 IF to_timestamp(p_expires)>f.valid_until THEN RAISE EXCEPTION 'CTT proposal challenge denied' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.review_challenges(challenge_id,principal_id,credential_id,tenant_id,edition_id,
 principal_authority_version,faculty_authority_version,credential_authority_version,scope_hash,challenge_bytes,issued_at,expires_at)
 VALUES(challenge_uuid,proposal.principal_id,proposal.credential_id,scope->>'tenant_id',(scope->>'edition_id')::uuid,
 proposal.principal_version,proposal.faculty_version,proposal.credential_version,proposal.scope_hash,p_random,to_timestamp(p_issued),to_timestamp(p_expires));
 INSERT INTO ctt_staff_private.capture_attempt_anchors(attempt_id,challenge_id,manifest_sha256,document_id,pdf_sha256,tenant_id,edition_id,scope_hash,scope_bytes)
 VALUES(attempt_uuid,challenge_uuid,scope->>'manifest_sha256',scope->>'document_id',scope->>'pdf_sha256',scope->>'tenant_id',(scope->>'edition_id')::uuid,proposal.scope_hash,proposal.scope_bytes);
 INSERT INTO ctt_staff_private.review_proposal_attempts(attempt_id,proposal_id,challenge_id) VALUES(attempt_uuid,p_id,challenge_uuid);
 UPDATE ctt_staff_private.review_proposal_heads SET current_attempt_id=attempt_uuid,revision=revision+1 WHERE session_id=p_session;
 INSERT INTO ctt_staff_private.review_proposal_events(session_id,account_id,backend_pin,action,revision,proposal_id,attempt_id)
 VALUES(p_session,p_account,proposal.backend_pin,'CHALLENGE',head.revision+1,p_id,attempt_uuid);
 RETURN jsonb_build_object('attempt_id',attempt_uuid::text,'challenge_id',challenge_uuid::text,'proposal_id',p_id::text,
 'issued_at',p_issued,'expires_at',p_expires,'scope_hash',proposal.scope_hash,'scope_bytes',encode(proposal.scope_bytes,'base64'),
 'principal_id',proposal.principal_id::text,'credential_data',encode(c.attested_credential,'base64'),
 'user_handle',encode(c.user_handle,'base64'),'counter',c.sign_count,'auth_profile',c.auth_profile,
 'auth_policy_hash',c.auth_policy_hash,'backup_eligible',c.backup_eligible,'backup_state',c.backup_state,
 'random_bytes',encode(p_random,'base64')) FROM ctt_staff_private.review_credentials c WHERE c.credential_id=proposal.credential_id;
END
$fn$;

CREATE FUNCTION ctt_staff_private.cancel_review_proposal(p_account uuid,p_session uuid,p_expected uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE context jsonb; head ctt_staff_private.review_proposal_heads%ROWTYPE;
BEGIN
 context:=ctt_staff_private.load_review_proposal_context(p_account,p_session);
 SELECT * INTO head FROM ctt_staff_private.review_proposal_heads h WHERE h.session_id=p_session FOR UPDATE NOWAIT;
 IF head.proposal_id IS DISTINCT FROM p_expected OR (context->'session'->>'committed_team_id') IS NOT NULL THEN
 RAISE EXCEPTION 'CTT proposal stale head' USING ERRCODE='40001'; END IF;
 UPDATE ctt_staff_private.review_proposal_heads SET proposal_id=NULL,current_attempt_id=NULL,revision=revision+1 WHERE session_id=p_session;
 INSERT INTO ctt_staff_private.review_proposal_events(session_id,account_id,backend_pin,action,revision,previous_proposal_id)
 VALUES(p_session,p_account,context->>'backend_pin','CANCEL',head.revision+1,p_expected);
END
$fn$;
CREATE FUNCTION ctt_staff_private.resolve_review_source_scope(p_session uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE answer jsonb; hits int;
BEGIN
 PERFORM ctt_staff_private.current_review_service();
 SELECT count(*) INTO hits FROM ctt_staff_private.source_admission_documents d
 JOIN ctt_staff_private.manifest_anchors m USING(manifest_sha256)
 JOIN ctt_staff_private.source_admissions admitted USING(manifest_sha256)
 WHERE ctt_staff_private.review_document_session(m.edition_id,d.document_id)=p_session;
 IF hits<>1 THEN RAISE EXCEPTION 'CTT source scope unavailable' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('tenant_id',m.tenant_id,'edition_id',m.edition_id::text,'tournament_id',m.tournament_id::text,
 'edition_year',m.edition_year,'roster_slug',m.roster_slug,'manifest_sha256',d.manifest_sha256,'document_id',d.document_id,
 'pdf_sha256',d.pdf_sha256,'payload_sha256',d.payload_sha256,'source_pages',
 (SELECT jsonb_agg(jsonb_build_object('page_index',pg.page_index,'pdf_page',pg.pdf_page,'image_sha256',pg.image_sha256) ORDER BY pg.page_index)
 FROM ctt_staff_private.source_admission_pages pg WHERE pg.manifest_sha256=d.manifest_sha256 AND pg.document_id=d.document_id)) INTO answer
 FROM ctt_staff_private.source_admission_documents d JOIN ctt_staff_private.manifest_anchors m USING(manifest_sha256)
 WHERE ctt_staff_private.review_document_session(m.edition_id,d.document_id)=p_session;
 RETURN answer;
END
$fn$;
CREATE FUNCTION ctt_staff_private.review_raw_snapshot(p_draft public.copa_telmex_registration_review_drafts)
RETURNS jsonb LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
 SELECT jsonb_build_object('ocr_raw',p_draft.ocr_raw,'extraction',p_draft.extraction,'validation',p_draft.validation,
 'review_edits',p_draft.review_edits,'layout_regions',p_draft.layout_regions,'page_manifest_hash',p_draft.page_manifest_hash,
 'overall_confidence',p_draft.overall_confidence,'needs_review',p_draft.needs_review)
$fn$;
CREATE FUNCTION ctt_staff_private.review_existing_photos(p_slug text,p_exclude uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
 SELECT coalesce(jsonb_agg(jsonb_build_object('player_id',p.id::text,'player_ref','player-'||p.id::text,
 'team_id',p.team_id::text,'team_ref','team-'||p.team_id::text,'tournament_slug',t.tournament_slug,
 'photo_sha256',p.photo_sha256,'photo_ahash',p.photo_ahash) ORDER BY p.id),'[]'::jsonb)
 FROM public.copa_telmex_players p JOIN public.copa_telmex_teams t ON t.id=p.team_id
 WHERE t.tournament_slug=p_slug AND (p_exclude IS NULL OR p.team_id<>p_exclude)
 AND p.governance_state IN('ACTIVE','LEGACY_ACTIVE') AND (nullif(p.photo_sha256,'') IS NOT NULL OR nullif(p.photo_ahash,'') IS NOT NULL)
$fn$;

CREATE FUNCTION ctt_staff_private.load_review_proposal_context(p_account uuid,p_session uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text; source jsonb; link ctt_staff_private.review_account_links%ROWTYPE;
 principal ctt_staff_private.review_principals%ROWTYPE; faculty ctt_staff_private.review_faculties%ROWTYPE;
 credential ctt_staff_private.review_credentials%ROWTYPE; source_session public.copa_telmex_registration_review_sessions%ROWTYPE;
 draft public.copa_telmex_registration_review_drafts%ROWTYPE; head ctt_staff_private.review_proposal_heads%ROWTYPE;
 pages jsonb; n int;
BEGIN
 pin:=ctt_staff_private.current_review_service();
 PERFORM pg_advisory_xact_lock(1129600051,hashtext(p_session::text));
 source:=ctt_staff_private.resolve_review_source_scope(p_session);
 SELECT * INTO link FROM ctt_staff_private.review_account_links l
 WHERE l.backend_pin=pin AND l.account_id=p_account AND l.tenant_id=source->>'tenant_id' AND l.edition_id=(source->>'edition_id')::uuid;
 IF link.principal_id IS NULL THEN RAISE EXCEPTION 'CTT review authority unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO principal FROM ctt_staff_private.review_principals p WHERE p.principal_id=link.principal_id FOR SHARE;
 SELECT * INTO faculty FROM ctt_staff_private.review_faculties f WHERE f.principal_id=link.principal_id
 AND f.tenant_id=link.tenant_id AND f.edition_id=link.edition_id AND f.faculty='capture_staff_v2' FOR SHARE;
 SELECT count(*) INTO n FROM ctt_staff_private.review_credentials c WHERE c.principal_id=link.principal_id AND c.revoked_at IS NULL;
 IF n<>1 THEN RAISE EXCEPTION 'CTT review credential unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO credential FROM ctt_staff_private.review_credentials c WHERE c.principal_id=link.principal_id AND c.revoked_at IS NULL FOR SHARE;
 SELECT * INTO link FROM ctt_staff_private.review_account_links l WHERE l.backend_pin=pin AND l.account_id=p_account
 AND l.tenant_id=source->>'tenant_id' AND l.edition_id=(source->>'edition_id')::uuid FOR SHARE;
 IF NOT link.active OR NOT principal.active OR faculty.principal_id IS NULL OR faculty.revoked_at IS NOT NULL
 OR clock_timestamp()<faculty.valid_from OR clock_timestamp()>=faculty.valid_until THEN
 RAISE EXCEPTION 'CTT review authority unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO head FROM ctt_staff_private.review_proposal_heads h WHERE h.session_id=p_session FOR SHARE;
 SELECT * INTO source_session FROM public.copa_telmex_registration_review_sessions s WHERE s.id=p_session FOR SHARE;
 IF source_session.id IS NULL OR source_session.tournament_slug IS DISTINCT FROM source->>'roster_slug'
 OR source_session.status IN('rejected','cancelled','error') THEN RAISE EXCEPTION 'CTT review source unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO draft FROM public.copa_telmex_registration_review_drafts d WHERE d.session_id=p_session
 ORDER BY d.draft_version DESC,d.created_at DESC LIMIT 1 FOR SHARE;
 IF draft.id IS NULL THEN RAISE EXCEPTION 'CTT review source unavailable' USING ERRCODE='42501'; END IF;
 SELECT jsonb_agg(jsonb_build_object('page_index',pg.page_index,'pdf_page',pg.pdf_page,'image_sha256',pg.image_sha256,
 'asset_id',a.id::text,'width',a.width,'height',a.height) ORDER BY pg.page_index),count(*) INTO pages,n
 FROM ctt_staff_private.source_admission_pages pg JOIN public.copa_telmex_registration_review_assets a
 ON a.session_id=p_session AND a.page_index=pg.page_index AND 'sha256:'||a.sha256=pg.image_sha256
 WHERE pg.manifest_sha256=source->>'manifest_sha256' AND pg.document_id=source->>'document_id';
 IF n<>jsonb_array_length(source->'source_pages') OR n<>(SELECT count(*) FROM public.copa_telmex_registration_review_assets a WHERE a.session_id=p_session) THEN
 RAISE EXCEPTION 'CTT review source assets unavailable' USING ERRCODE='42501'; END IF;
 RETURN source||jsonb_build_object('backend_pin',pin,'account_id',p_account::text,'principal_id',principal.principal_id::text,
 'actor_role',link.actor_role,'account_version',link.authority_version,'principal_version',principal.authority_version,
 'faculty_valid_until_seconds',floor(extract(epoch FROM faculty.valid_until))::bigint,'faculty_version',faculty.authority_version,'credential_version',credential.authority_version,'credential_id_hex',encode(credential.credential_id,'hex'),
 'session',to_jsonb(source_session),'draft',to_jsonb(draft),'raw_snapshot',ctt_staff_private.review_raw_snapshot(draft),
 'source_pages',pages,'head_proposal_id',head.proposal_id::text,'head_revision',coalesce(head.revision,0),'current_attempt_id',head.current_attempt_id::text,
 'existing_photo_records',ctt_staff_private.review_existing_photos(source->>'roster_slug'));
END
$fn$;

CREATE FUNCTION ctt_staff_private.review_proposal_json(p_id uuid)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
 SELECT jsonb_build_object('proposal_id',p.proposal_id::text,'session_id',p.session_id::text,'previous_proposal_id',p.previous_proposal_id::text,
 'revision',p.revision,'scope_hash',p.scope_hash,'capture_nonce',p.capture_nonce::text,
 'scope_bytes',encode(p.scope_bytes,'base64'),'review_bytes',encode(p.review_bytes,'base64'),'evidence_bytes',encode(p.evidence_bytes,'base64'),
 'preview_bytes',encode(p.preview_bytes,'base64'),'validation_bytes',encode(p.validation_bytes,'base64'),'raw_snapshot_bytes',encode(p.raw_snapshot_bytes,'base64'),
 'head_revision',h.revision,'attempt_id',h.current_attempt_id::text,'is_current',h.proposal_id=p.proposal_id)
 FROM ctt_staff_private.review_proposals p JOIN ctt_staff_private.review_proposal_heads h ON h.session_id=p.session_id WHERE p.proposal_id=p_id
$fn$;

CREATE FUNCTION ctt_staff_private.register_review_proposal(
 p_account uuid,p_session uuid,p_expected uuid,p_id uuid,p_scope bytea,p_review bytea,p_evidence bytea,
 p_preview bytea,p_validation bytea,p_raw bytea,p_nonce uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE context jsonb; scope jsonb; plan jsonb; old ctt_staff_private.review_proposals%ROWTYPE;
 head ctt_staff_private.review_proposal_heads%ROWTYPE; raw_json jsonb; n bigint;
BEGIN
 context:=ctt_staff_private.load_review_proposal_context(p_account,p_session);
 IF (context->'session'->>'committed_team_id') IS NOT NULL THEN RAISE EXCEPTION 'CTT review already captured' USING ERRCODE='42501'; END IF;
 IF NOT(convert_from(p_scope,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(p_review,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(p_evidence,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(p_preview,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(p_validation,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(p_raw,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS) THEN
 RAISE EXCEPTION 'CTT proposal invalid' USING ERRCODE='42501'; END IF;
 scope:=convert_from(p_scope,'UTF8')::jsonb;plan:=convert_from(p_review,'UTF8')::jsonb;raw_json:=convert_from(p_raw,'UTF8')::jsonb;
 IF scope->>'schema' IS DISTINCT FROM 'ctt.staff-review.challenge-scope.v2'
 OR scope->>'purpose' IS DISTINCT FROM 'capture_staff_v2'
 OR scope->>'session_id' IS DISTINCT FROM p_session::text
 OR scope->>'actor_id' IS DISTINCT FROM context->>'principal_id' OR scope->>'actor_role' IS DISTINCT FROM context->>'actor_role'
 OR scope->>'tenant_id' IS DISTINCT FROM context->>'tenant_id' OR scope->>'edition_id' IS DISTINCT FROM context->>'edition_id'
 OR scope->>'tournament_slug' IS DISTINCT FROM context->>'roster_slug'
 OR scope->>'manifest_sha256' IS DISTINCT FROM context->>'manifest_sha256' OR scope->>'document_id' IS DISTINCT FROM context->>'document_id'
 OR scope->>'pdf_sha256' IS DISTINCT FROM context->>'pdf_sha256'
 OR scope->>'draft_id' IS DISTINCT FROM context->'draft'->>'id' OR scope->'draft_version' IS DISTINCT FROM context->'draft'->'draft_version'
 OR scope->>'draft_content_hash' IS DISTINCT FROM context->'draft'->>'content_hash'
 OR raw_json IS DISTINCT FROM context->'raw_snapshot'
 OR NOT coalesce(plan->>'schema'='ctt.capture.review-plan.v1' OR (
 plan->>'schema'='ctt.review.incomplete-proposal.v1'
 AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(plan) k)=ARRAY['observations','review_context','schema','team']::text[]
 AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(plan->'team') k)=ARRAY['id','tournament_slug']::text[]
 AND jsonb_typeof(plan->'observations')='object'
 AND (convert_from(p_preview,'UTF8')::jsonb)->'ready_to_approve'='false'::jsonb
 AND jsonb_typeof((convert_from(p_validation,'UTF8')::jsonb)->'blockers')='array'
 AND jsonb_array_length((convert_from(p_validation,'UTF8')::jsonb)->'blockers')>0),false)
 OR plan->'review_context' IS DISTINCT FROM scope-ARRAY['staff_and_changes_binding']
 OR plan->'team'->>'id' IS DISTINCT FROM scope->>'team_id'
 OR plan->'team'->>'tournament_slug' IS DISTINCT FROM context->>'roster_slug'
 OR p_id IS NULL OR p_nonce IS NULL OR jsonb_array_length(scope->'source_pages')<>jsonb_array_length(context->'source_pages')
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(scope->'source_pages') s
 WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(context->'source_pages') actual
 WHERE (actual-ARRAY['width','height'])=s)) THEN RAISE EXCEPTION 'CTT proposal context invalid' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.review_proposal_heads(session_id) VALUES(p_session) ON CONFLICT DO NOTHING;
 SELECT * INTO head FROM ctt_staff_private.review_proposal_heads h WHERE h.session_id=p_session FOR UPDATE NOWAIT;
 SELECT * INTO old FROM ctt_staff_private.review_proposals WHERE proposal_id=p_id;
 IF old.proposal_id IS NOT NULL THEN
 IF head.proposal_id IS DISTINCT FROM p_id OR old.account_id IS DISTINCT FROM p_account OR old.scope_bytes IS DISTINCT FROM p_scope
 OR old.review_bytes IS DISTINCT FROM p_review OR old.evidence_bytes IS DISTINCT FROM p_evidence OR old.preview_bytes IS DISTINCT FROM p_preview
 OR old.validation_bytes IS DISTINCT FROM p_validation OR old.raw_snapshot_bytes IS DISTINCT FROM p_raw OR old.capture_nonce IS DISTINCT FROM p_nonce THEN
 RAISE EXCEPTION 'CTT proposal conflict' USING ERRCODE='40001'; END IF;
 RETURN ctt_staff_private.review_proposal_json(p_id); END IF;
 IF head.proposal_id IS DISTINCT FROM p_expected THEN RAISE EXCEPTION 'CTT proposal stale head' USING ERRCODE='40001'; END IF;
 SELECT coalesce(max(revision),0)+1 INTO n FROM ctt_staff_private.review_proposals WHERE session_id=p_session;
 INSERT INTO ctt_staff_private.review_proposals(proposal_id,session_id,previous_proposal_id,revision,backend_pin,account_id,principal_id,credential_id,
 account_version,principal_version,faculty_version,credential_version,scope_bytes,scope_hash,review_bytes,evidence_bytes,preview_bytes,validation_bytes,raw_snapshot_bytes,capture_nonce)
 VALUES(p_id,p_session,head.proposal_id,n,context->>'backend_pin',p_account,(context->>'principal_id')::uuid,decode(context->>'credential_id_hex','hex'),
 (context->>'account_version')::bigint,(context->>'principal_version')::bigint,(context->>'faculty_version')::bigint,(context->>'credential_version')::bigint,
 p_scope,'sha256:'||encode(public.digest(p_scope,'sha256'),'hex'),p_review,p_evidence,p_preview,p_validation,p_raw,p_nonce);
 UPDATE ctt_staff_private.review_proposal_heads SET proposal_id=p_id,current_attempt_id=NULL,revision=revision+1 WHERE session_id=p_session;
 INSERT INTO ctt_staff_private.review_proposal_events(session_id,account_id,backend_pin,action,revision,proposal_id,previous_proposal_id)
 VALUES(p_session,p_account,context->>'backend_pin','PUBLISH',head.revision+1,p_id,head.proposal_id);
 RETURN ctt_staff_private.review_proposal_json(p_id);
END
$fn$;

CREATE FUNCTION ctt_staff_private.register_proposal_capture_payload(
 p_account uuid,p_session uuid,p_attempt uuid,p_effect bytea,p_envelope bytea,p_nonce uuid,
 p_pre_authorization bytea,p_evidence bytea,p_signed_review bytea)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text; proposal ctt_staff_private.review_proposals%ROWTYPE; scope jsonb; envelope jsonb; body jsonb; effect jsonb;
 issuer record; key_hits int; proof text; old record;
BEGIN
 pin:=ctt_staff_private.current_review_service();
 PERFORM pg_advisory_xact_lock(1129600051,hashtext(p_session::text));
 SELECT * INTO issuer FROM ctt_staff_private.load_review_assertion_context(p_attempt);
 PERFORM ctt_staff_private.assert_current_review_proposal(p_attempt,false,false);
 SELECT p.* INTO proposal FROM ctt_staff_private.review_proposal_attempts a JOIN ctt_staff_private.review_proposals p USING(proposal_id) WHERE a.attempt_id=p_attempt;
 IF proposal.account_id IS DISTINCT FROM p_account OR proposal.session_id IS DISTINCT FROM p_session OR proposal.backend_pin IS DISTINCT FROM pin
 OR p_nonce IS DISTINCT FROM proposal.capture_nonce OR p_evidence IS DISTINCT FROM proposal.evidence_bytes
 OR octet_length(p_signed_review) NOT BETWEEN 2 AND 8192
 OR NOT(convert_from(p_effect,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(p_envelope,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(p_pre_authorization,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS) THEN
 RAISE EXCEPTION 'CTT proposal payload denied' USING ERRCODE='42501'; END IF;
 proof:='sha256:'||encode(public.digest(p_signed_review,'sha256'),'hex');
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_assertion_issuances i WHERE i.attempt_id=p_attempt AND i.proof_sha256=proof
 AND i.expires_at_seconds=issuer.expires_at_seconds) THEN RAISE EXCEPTION 'CTT proposal proof not issued' USING ERRCODE='42501'; END IF;
 scope:=convert_from(proposal.scope_bytes,'UTF8')::jsonb;envelope:=convert_from(p_envelope,'UTF8')::jsonb;body:=envelope->'body';effect:=convert_from(p_effect,'UTF8')::jsonb;
 IF body->>'schema' IS DISTINCT FROM 'ctt.capture.authorization.v1' OR body->>'scope_hash' IS DISTINCT FROM proposal.scope_hash
 OR body->>'attempt_id' IS DISTINCT FROM p_attempt::text OR body->>'nonce' IS DISTINCT FROM p_nonce::text
 OR body->>'review_plan_binding' IS DISTINCT FROM scope->>'staff_and_changes_binding'
 OR body->>'expires_at' IS DISTINCT FROM issuer.expires_at_seconds::text
 OR effect->>'schema' IS DISTINCT FROM 'ctt.capture.effect.v1'
 OR effect->'review_context' IS DISTINCT FROM scope-ARRAY['staff_and_changes_binding']
 OR effect->'team'->>'id' IS DISTINCT FROM scope->>'team_id'
 OR NOT coalesce((body->>'effect_binding')~'^hmac-sha256:[0-9a-f]{64}$',false) THEN
 RAISE EXCEPTION 'CTT proposal payload scope invalid' USING ERRCODE='42501'; END IF;
 SELECT count(*) INTO key_hits FROM ctt_staff_private.effect_keys k
 WHERE NOT k.revoked AND clock_timestamp()>=k.not_before AND clock_timestamp()<k.not_after
 AND k.not_after>=to_timestamp(issuer.expires_at_seconds)
 AND ctt_staff_private.verify_effect_hmac(k.secret,p_effect,decode(substr(body->>'effect_binding',13),'hex'));
 IF key_hits<>1 THEN RAISE EXCEPTION 'CTT proposal payload MAC invalid' USING ERRCODE='42501'; END IF;
 SELECT p.* INTO old FROM ctt_staff_private.capture_payloads p WHERE attempt_id=p_attempt;
 IF old.attempt_id IS NOT NULL THEN
 IF old.review_bytes IS DISTINCT FROM proposal.review_bytes OR old.effect_bytes IS DISTINCT FROM p_effect
 OR old.gate_envelope IS DISTINCT FROM p_envelope OR old.nonce IS DISTINCT FROM p_nonce
 OR NOT EXISTS(SELECT 1 FROM ctt_staff_private.finality_inputs f WHERE f.attempt_id=p_attempt AND f.preauthorization_bytes=p_pre_authorization AND f.evidence_bytes=p_evidence)
 OR NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_materializations m WHERE m.attempt_id=p_attempt AND m.signed_review=p_signed_review) THEN
 RAISE EXCEPTION 'CTT proposal payload retry differs' USING ERRCODE='42501'; END IF; RETURN; END IF;
 INSERT INTO ctt_staff_private.capture_payloads(attempt_id,review_bytes,effect_bytes,gate_envelope,nonce)
 VALUES(p_attempt,proposal.review_bytes,p_effect,p_envelope,p_nonce);
 INSERT INTO ctt_staff_private.finality_inputs(attempt_id,preauthorization_bytes,evidence_bytes) VALUES(p_attempt,p_pre_authorization,proposal.evidence_bytes);
 INSERT INTO ctt_staff_private.review_materializations(attempt_id,signed_review,proof_sha256) VALUES(p_attempt,p_signed_review,proof);
END
$fn$;

CREATE FUNCTION ctt_staff_private.review_proposal_status(p_account uuid,p_session uuid,p_attempt uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text; proposal ctt_staff_private.review_proposals%ROWTYPE; result_row ctt_staff_private.capture_results%ROWTYPE; session_row record; proof bytea; scope jsonb;
BEGIN
 pin:=ctt_staff_private.current_review_service();
 -- Wait for any capture transaction before deciding result/head/expiry state.
 PERFORM pg_advisory_xact_lock_shared(1129600050,0);
 SELECT p.* INTO proposal FROM ctt_staff_private.review_proposal_attempts a JOIN ctt_staff_private.review_proposals p USING(proposal_id) WHERE a.attempt_id=p_attempt;
 IF proposal.account_id IS DISTINCT FROM p_account OR proposal.session_id IS DISTINCT FROM p_session OR proposal.backend_pin IS DISTINCT FROM pin THEN
 RAISE EXCEPTION 'CTT proposal status unavailable' USING ERRCODE='42501'; END IF;
 scope:=convert_from(proposal.scope_bytes,'UTF8')::jsonb;
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_account_links l WHERE l.backend_pin=pin AND l.account_id=p_account
 AND l.tenant_id=scope->>'tenant_id' AND l.edition_id=(scope->>'edition_id')::uuid AND l.principal_id=proposal.principal_id AND l.active) THEN
 RAISE EXCEPTION 'CTT proposal status unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO result_row FROM ctt_staff_private.capture_results WHERE attempt_id=p_attempt;
 SELECT committed_team_id,status INTO session_row FROM public.copa_telmex_registration_review_sessions WHERE id=p_session;
 IF result_row.result_id IS NOT NULL AND (session_row.status IS DISTINCT FROM 'committed' OR session_row.committed_team_id IS DISTINCT FROM result_row.team_id) THEN
 RAISE EXCEPTION 'CTT proposal result incomplete' USING ERRCODE='42501'; END IF;
 SELECT coalesce(
 (SELECT m.signed_review FROM ctt_staff_private.review_materializations m WHERE m.attempt_id=p_attempt),
 (SELECT i.signed_review FROM ctt_staff_private.issuer_proofs i WHERE i.attempt_id=p_attempt)) INTO proof;
 RETURN jsonb_build_object('attempt_id',p_attempt::text,'challenge_id',(SELECT a.challenge_id::text FROM ctt_staff_private.review_proposal_attempts a WHERE a.attempt_id=p_attempt),
 'issued_at',(SELECT floor(extract(epoch FROM c.issued_at))::bigint FROM ctt_staff_private.review_challenges c JOIN ctt_staff_private.review_proposal_attempts a USING(challenge_id) WHERE a.attempt_id=p_attempt),
 'expires_at',(SELECT floor(extract(epoch FROM c.expires_at))::bigint FROM ctt_staff_private.review_challenges c JOIN ctt_staff_private.review_proposal_attempts a USING(challenge_id) WHERE a.attempt_id=p_attempt),
 'proposal_id',proposal.proposal_id::text,'result_id',result_row.result_id::text,
 'team_id',result_row.team_id::text,'session_status',session_row.status,'committed',result_row.result_id IS NOT NULL,
 'expired',(SELECT floor(extract(epoch FROM clock_timestamp()))::bigint>=floor(extract(epoch FROM c.expires_at))::bigint FROM ctt_staff_private.review_challenges c JOIN ctt_staff_private.review_proposal_attempts a USING(challenge_id) WHERE a.attempt_id=p_attempt),
 'payload_admitted',EXISTS(SELECT 1 FROM ctt_staff_private.review_materializations m WHERE m.attempt_id=p_attempt),'signed_review',CASE WHEN proof IS NOT NULL THEN encode(proof,'base64') ELSE NULL END,
 'is_current',EXISTS(SELECT 1 FROM ctt_staff_private.review_proposal_heads h WHERE h.session_id=p_session AND h.proposal_id=proposal.proposal_id AND h.current_attempt_id=p_attempt));
END
$fn$;

CREATE FUNCTION ctt_staff_private.check_proposal_consumption_current()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE attempt uuid;
BEGIN
 SELECT a.attempt_id INTO attempt FROM ctt_staff_private.review_proposal_attempts a WHERE a.challenge_id=NEW.challenge_id;
 IF attempt IS NULL THEN RAISE EXCEPTION 'CTT admitted proposal required' USING ERRCODE='42501'; END IF;
 PERFORM ctt_staff_private.assert_current_review_proposal(attempt,TG_WHEN='AFTER',true);
 RETURN NEW;
END
$fn$;
CREATE TRIGGER ctt_proposal_consumption_initial BEFORE INSERT ON ctt_staff_private.review_challenge_consumptions
 FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.check_proposal_consumption_current();
CREATE CONSTRAINT TRIGGER ctt_proposal_consumption_commit AFTER INSERT ON ctt_staff_private.review_challenge_consumptions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.check_proposal_consumption_current();

CREATE FUNCTION ctt_staff_private.review_session_commit_authorized(p_old jsonb,p_new jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE item record; hits int:=0;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.effect_writers WHERE writer_role=session_user) THEN RETURN false; END IF;
 FOR item IN SELECT r.*,p.session_id,i.recorded_at approval_at FROM ctt_staff_private.capture_results r
 JOIN ctt_staff_private.review_proposal_attempts a USING(attempt_id) JOIN ctt_staff_private.review_proposals p USING(proposal_id)
 JOIN ctt_staff_private.review_assertion_issuances i USING(attempt_id)
 WHERE p.session_id::text=p_old->>'id'
 AND r.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint LOOP
 PERFORM ctt_staff_private.assert_current_review_proposal(item.attempt_id,true,true);
 PERFORM ctt_staff_private.validate_capture_finality_rows(item.attempt_id,true);
 IF (p_old-ARRAY['status','committed_team_id','approved_at','committed_at']) IS NOT DISTINCT FROM
 (p_new-ARRAY['status','committed_team_id','approved_at','committed_at'])
 AND p_old->'committed_team_id'='null'::jsonb AND p_new->>'status'='committed'
 AND p_new->>'committed_team_id'=item.team_id::text
 AND (p_new->>'approved_at')::timestamp=(item.approval_at AT TIME ZONE 'UTC')
 AND (p_new->>'committed_at')::timestamp=(item.recorded_at AT TIME ZONE 'UTC') THEN hits:=hits+1; END IF;
 END LOOP;
 RETURN hits=1;
END
$fn$;
CREATE FUNCTION ctt_staff_private.commit_proposal_review_session()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE item record;
BEGIN
 SELECT p.session_id,i.recorded_at approval_at INTO item FROM ctt_staff_private.review_proposal_attempts a
 JOIN ctt_staff_private.review_proposals p USING(proposal_id) JOIN ctt_staff_private.review_assertion_issuances i USING(attempt_id)
 WHERE a.attempt_id=NEW.attempt_id;
 IF item.session_id IS NULL THEN RAISE EXCEPTION 'CTT official review proposal required' USING ERRCODE='42501'; END IF;
 UPDATE public.copa_telmex_registration_review_sessions SET status='committed',committed_team_id=NEW.team_id,
 approved_at=item.approval_at AT TIME ZONE 'UTC',committed_at=NEW.recorded_at AT TIME ZONE 'UTC'
 WHERE id=item.session_id AND committed_team_id IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'CTT official review already committed' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END
$fn$;
CREATE TRIGGER ctt_proposal_session_commit AFTER INSERT ON ctt_staff_private.capture_results
 FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.commit_proposal_review_session();

DO $extend$
DECLARE v_signature text; definition text; updated text; needle text;
BEGIN
 FOREACH v_signature IN ARRAY ARRAY['ctt_staff_private.load_review_assertion_context(uuid)',
 'ctt_staff_private.load_capture_finality_context(uuid)','ctt_staff_private.guard_legacy_v2_boundary()'] LOOP
 definition:=pg_get_functiondef(to_regprocedure(v_signature));updated:=definition;
 IF v_signature='ctt_staff_private.load_review_assertion_context(uuid)' THEN
 needle:='  checked_at:=pg_catalog.clock_timestamp();';
 updated:=replace(updated,needle,'  PERFORM ctt_staff_private.assert_current_review_proposal(p_attempt_id,false,false);'||chr(10)||needle);
 ELSIF v_signature='ctt_staff_private.load_capture_finality_context(uuid)' THEN
 needle:=' PERFORM ctt_staff_private.current_finality_writer();';
 updated:=replace(updated,needle,needle||chr(10)||' PERFORM ctt_staff_private.assert_current_review_proposal(p_attempt_id,false,false);');
 updated:=replace(updated,'draft.validation::jsonb',
 '(SELECT convert_from(p.validation_bytes,''UTF8'')::jsonb FROM ctt_staff_private.review_proposals p JOIN ctt_staff_private.review_proposal_attempts a USING(proposal_id) WHERE a.attempt_id=p_attempt_id)');
 ELSE
 needle:='  PERFORM ctt_staff_private.lock_legacy_reference_ids(ids);';
 updated:=replace(updated,needle,needle||chr(10)||'  IF TG_OP=''UPDATE'' AND TG_TABLE_NAME=''copa_telmex_registration_review_sessions'' AND ctt_staff_private.review_session_commit_authorized(to_jsonb(OLD),to_jsonb(NEW)) THEN RETURN NEW; END IF;');
 END IF;
 IF updated=definition OR length(definition)-length(replace(definition,needle,''))<>length(needle) THEN RAISE EXCEPTION 'CTT proposal baseline drift'; END IF;
 INSERT INTO ctt_staff_private.review_proposal_baseline(signature,original_definition,original_hash) VALUES(v_signature,definition,md5(definition));
 EXECUTE updated;
 UPDATE ctt_staff_private.review_proposal_baseline b SET installed_hash=md5(pg_get_functiondef(to_regprocedure(b.signature))) WHERE b.signature=v_signature;
 END LOOP;
END
$extend$;

CREATE FUNCTION ctt_staff_private.project_review_capture_rows(p_account uuid,p_session uuid,p_attempt uuid,p_team jsonb,p_players jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE proposal ctt_staff_private.review_proposals%ROWTYPE; team public.copa_telmex_teams; player public.copa_telmex_players;
 item jsonb; rows jsonb:='[]'::jsonb; plan jsonb; n integer:=0;
BEGIN
 PERFORM ctt_staff_private.current_review_service();
 PERFORM ctt_staff_private.assert_current_review_proposal(p_attempt,false,false);
 SELECT p.* INTO proposal FROM ctt_staff_private.review_proposals p JOIN ctt_staff_private.review_proposal_attempts a USING(proposal_id) WHERE a.attempt_id=p_attempt;
 IF proposal.account_id IS DISTINCT FROM p_account OR proposal.session_id IS DISTINCT FROM p_session
 OR proposal.backend_pin IS DISTINCT FROM ctt_staff_private.current_review_service() THEN RAISE EXCEPTION 'CTT proposal projection denied' USING ERRCODE='42501'; END IF;
 plan:=convert_from(proposal.review_bytes,'UTF8')::jsonb;
 IF jsonb_typeof(p_players) IS DISTINCT FROM 'array' OR jsonb_array_length(p_players)<>jsonb_array_length(plan->'players')
 OR p_team->>'id' IS DISTINCT FROM plan->'team'->>'id' THEN RAISE EXCEPTION 'CTT proposal projection denied' USING ERRCODE='42501'; END IF;
 team:=jsonb_populate_record(NULL::public.copa_telmex_teams,p_team);
 FOR item IN SELECT value FROM jsonb_array_elements(p_players) LOOP
 IF item->>'id' IS DISTINCT FROM plan->'players'->n->>'id' OR item->>'team_id' IS DISTINCT FROM p_team->>'id'
 OR item->>'governance_state' IS DISTINCT FROM 'PENDING_FINALITY' THEN RAISE EXCEPTION 'CTT proposal projection denied' USING ERRCODE='42501'; END IF;
 player:=jsonb_populate_record(NULL::public.copa_telmex_players,item);
 rows:=rows||jsonb_build_array(jsonb_build_object('id',player.id::text,'postcommit_revision',0,
 'postcommit_snapshot_hash',public.regs07_snapshot_hash(public.regs07_player_snapshot(player)))); n:=n+1;
 END LOOP;
 RETURN jsonb_build_object('team',jsonb_build_object('id',team.id::text,'postcommit_revision',1,
 'postcommit_snapshot_hash',public.regs07_snapshot_hash(public.regs07_team_snapshot(team))),'players',rows);
END
$fn$;

-- Gate reads committed review inputs while the writer holds row/global locks.
-- Use one MVCC SELECT; never acquire the writer's anchor/credential/session locks.
CREATE FUNCTION ctt_staff_private.load_review_finality_gate_context(p_attempt uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text; answer jsonb;
BEGIN
 pin:=ctt_staff_private.current_review_service();
 SELECT jsonb_build_object(
 'database_identity',dbpin.database_identity,'database_oid',db.oid::bigint,
 'scope_bytes',encode(p.scope_bytes,'base64'),'review_bytes',encode(p.review_bytes,'base64'),
 'pending_effect_bytes',encode(payload.effect_bytes,'base64'),
 'signed_review',encode(material.signed_review,'base64'),
 'preauthorization_bytes',encode(inputs.preauthorization_bytes,'base64'),
 'evidence_bytes',encode(p.evidence_bytes,'base64'),'validation_bytes',encode(p.validation_bytes,'base64'),
 'source_context',jsonb_build_object('tenant_id',manifest.tenant_id,'edition_id',manifest.edition_id::text,
 'tournament_id',manifest.tournament_id::text,'edition_year',manifest.edition_year,
 'roster_slug',manifest.roster_slug,'credential_id_hex',encode(credential.credential_id,'hex')))
 INTO answer
 FROM ctt_staff_private.review_proposal_attempts bridge
 JOIN ctt_staff_private.review_proposals p USING(proposal_id)
 CROSS JOIN LATERAL (SELECT convert_from(p.scope_bytes,'UTF8')::jsonb value) scope
 JOIN ctt_staff_private.review_proposal_heads head ON head.session_id=p.session_id
 AND head.proposal_id=p.proposal_id AND head.current_attempt_id=bridge.attempt_id
 JOIN ctt_staff_private.capture_attempt_anchors anchor ON anchor.attempt_id=bridge.attempt_id
 AND anchor.challenge_id=bridge.challenge_id AND anchor.scope_bytes=p.scope_bytes AND anchor.scope_hash=p.scope_hash
 JOIN ctt_staff_private.review_challenges challenge ON challenge.challenge_id=bridge.challenge_id
 AND challenge.principal_id=p.principal_id AND challenge.credential_id=p.credential_id
 JOIN ctt_staff_private.review_principals principal ON principal.principal_id=p.principal_id
 JOIN ctt_staff_private.review_credentials credential ON credential.credential_id=p.credential_id
 AND credential.principal_id=p.principal_id
 JOIN ctt_staff_private.review_faculties faculty ON faculty.principal_id=p.principal_id
 AND faculty.tenant_id=scope.value->>'tenant_id' AND faculty.edition_id=(scope.value->>'edition_id')::uuid
 AND faculty.faculty='capture_staff_v2'
 JOIN ctt_staff_private.review_account_links link ON link.backend_pin=p.backend_pin AND link.account_id=p.account_id
 AND link.tenant_id=faculty.tenant_id AND link.edition_id=faculty.edition_id AND link.principal_id=p.principal_id
 JOIN public.copa_telmex_registration_review_sessions session ON session.id=p.session_id
 JOIN LATERAL (SELECT d.* FROM public.copa_telmex_registration_review_drafts d WHERE d.session_id=p.session_id
 ORDER BY d.draft_version DESC,d.created_at DESC LIMIT 1) draft ON true
 JOIN ctt_staff_private.manifest_anchors manifest ON manifest.manifest_sha256=anchor.manifest_sha256
 AND manifest.tenant_id=faculty.tenant_id AND manifest.edition_id=faculty.edition_id
 JOIN ctt_staff_private.source_admission_documents source ON source.manifest_sha256=anchor.manifest_sha256
 AND source.document_id=anchor.document_id AND source.pdf_sha256=anchor.pdf_sha256
 JOIN ctt_staff_private.source_admissions admission ON admission.manifest_sha256=source.manifest_sha256
 JOIN ctt_staff_private.capture_payloads payload ON payload.attempt_id=bridge.attempt_id AND payload.review_bytes=p.review_bytes
 JOIN ctt_staff_private.finality_inputs inputs ON inputs.attempt_id=bridge.attempt_id AND inputs.evidence_bytes=p.evidence_bytes
 JOIN ctt_staff_private.review_materializations material ON material.attempt_id=bridge.attempt_id
 JOIN ctt_staff_private.review_assertion_issuances issuance ON issuance.attempt_id=bridge.attempt_id
 AND issuance.proof_sha256=material.proof_sha256
 JOIN ctt_staff_private.issuer_proofs retained ON retained.attempt_id=bridge.attempt_id
 AND retained.signed_review=material.signed_review
 CROSS JOIN ctt_staff_private.finality_database_identity dbpin
 JOIN pg_catalog.pg_database db ON db.datname=current_database()
 WHERE bridge.attempt_id=p_attempt AND p.backend_pin=pin AND dbpin.singleton
 AND principal.active AND link.active AND credential.revoked_at IS NULL AND faculty.revoked_at IS NULL
 AND principal.authority_version=p.principal_version AND faculty.authority_version=p.faculty_version
 AND credential.authority_version=p.credential_version AND link.authority_version=p.account_version
 AND link.actor_role=scope.value->>'actor_role'
 AND challenge.principal_authority_version=p.principal_version
 AND challenge.faculty_authority_version=p.faculty_version AND challenge.credential_authority_version=p.credential_version
 AND clock_timestamp()>=challenge.issued_at AND clock_timestamp()<challenge.expires_at
 AND clock_timestamp()>=faculty.valid_from AND clock_timestamp()<faculty.valid_until
 AND session.committed_team_id IS NULL AND session.status NOT IN('rejected','cancelled','error','committed')
 AND session.tournament_slug=manifest.roster_slug
 AND p.session_id=ctt_staff_private.review_document_session(manifest.edition_id,source.document_id)
 AND draft.id::text=scope.value->>'draft_id' AND draft.draft_version::text=scope.value->>'draft_version'
 AND draft.content_hash=scope.value->>'draft_content_hash'
 AND ctt_staff_private.review_raw_snapshot(draft)=convert_from(p.raw_snapshot_bytes,'UTF8')::jsonb
 AND material.proof_sha256='sha256:'||encode(public.digest(material.signed_review,'sha256'),'hex')
 AND issuance.expires_at_seconds=floor(extract(epoch FROM challenge.expires_at))::bigint
 AND NOT EXISTS(SELECT 1 FROM ctt_staff_private.capture_results result WHERE result.attempt_id=p_attempt);
 IF answer IS NULL THEN RAISE EXCEPTION 'CTT finality review context unavailable' USING ERRCODE='42501'; END IF;
 RETURN answer;
END
$fn$;

DO $functions$
DECLARE signature text;
BEGIN
 FOREACH signature IN ARRAY ARRAY['review_document_session(uuid,text)','resolve_review_source_scope(uuid)',
 'review_raw_snapshot(public.copa_telmex_registration_review_drafts)','review_existing_photos(text,uuid)',
 'load_review_proposal_context(uuid,uuid)','review_proposal_json(uuid)',
 'register_review_proposal(uuid,uuid,uuid,uuid,bytea,bytea,bytea,bytea,bytea,bytea,uuid)',
 'review_photo_duplicate(text,text,text,text)','check_review_proposal_policy(uuid,boolean)',
 'assert_current_review_proposal(uuid,boolean,boolean)','read_review_proposal(uuid,uuid,uuid)',
 'create_proposal_challenge(uuid,uuid,uuid,bytea,bigint,bigint)','cancel_review_proposal(uuid,uuid,uuid)',
 'register_proposal_capture_payload(uuid,uuid,uuid,bytea,bytea,uuid,bytea,bytea,bytea)',
 'review_proposal_status(uuid,uuid,uuid)','check_proposal_consumption_current()',
 'review_session_commit_authorized(jsonb,jsonb)','commit_proposal_review_session()',
 'project_review_capture_rows(uuid,uuid,uuid,jsonb,jsonb)',
 'load_review_finality_gate_context(uuid)'] LOOP
 EXECUTE 'ALTER FUNCTION ctt_staff_private.'||signature||' OWNER TO samchat_ctt_staff_owner';
 EXECUTE 'REVOKE ALL ON FUNCTION ctt_staff_private.'||signature||' FROM PUBLIC,devnous_user';
 END LOOP;
END
$functions$;
DO $postflight$
DECLARE owner_id oid; name text;
BEGIN
 SELECT oid INTO owner_id FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner';
 FOREACH name IN ARRAY ARRAY['review_proposal_baseline','review_proposals','review_proposal_attempts','review_proposal_heads','review_proposal_events','review_materializations'] LOOP
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_class c CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
 WHERE c.oid=to_regclass('ctt_staff_private.'||name) AND (c.relowner<>owner_id OR c.relrowsecurity OR c.relkind<>'r' OR a.grantee<>owner_id
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute att WHERE att.attrelid=c.oid AND att.attnum>0 AND NOT att.attisdropped AND att.attacl IS NOT NULL))) THEN
 RAISE EXCEPTION 'CTT proposal installed ACL invalid'; END IF; END LOOP;
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE n.nspname='ctt_staff_private' AND p.proname IN('review_document_session','resolve_review_source_scope','review_raw_snapshot',
 'review_existing_photos','load_review_proposal_context','review_proposal_json','register_review_proposal','review_photo_duplicate',
 'check_review_proposal_policy','assert_current_review_proposal','read_review_proposal','create_proposal_challenge','cancel_review_proposal',
 'register_proposal_capture_payload','review_proposal_status','check_proposal_consumption_current','review_session_commit_authorized','commit_proposal_review_session','project_review_capture_rows','load_review_finality_gate_context')
 AND (p.proowner<>owner_id OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']::text[] OR a.grantee<>owner_id)) THEN
 RAISE EXCEPTION 'CTT proposal installed function ACL invalid'; END IF;
END
$postflight$;
COMMIT;
