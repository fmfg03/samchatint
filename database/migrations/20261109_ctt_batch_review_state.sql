-- CTT-BULK-CAPTURE-001: additive private batch state. No runtime identities seeded.
BEGIN;
DO $check$
DECLARE owner_id oid;
BEGIN
 SELECT oid INTO owner_id FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner'
 AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
 AND NOT rolreplication AND NOT rolbypassrls;
 IF owner_id IS NULL OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id)
 OR to_regclass('ctt_staff_private.review_credentials') IS NULL
 OR to_regprocedure('ctt_staff_private.batch_assert_source_document(uuid,text,text,text)') IS NULL
 OR current_setting('max_prepared_transactions')::int<>0 THEN
 RAISE EXCEPTION 'CTT batch state dependency invalid'; END IF;
END $check$;

CREATE TABLE ctt_staff_private.review_batch_services(
 service_role name PRIMARY KEY, kind text NOT NULL CHECK(kind IN('workflow','verifier','issuer','worker','writer','operator')),
 backend_pin text NOT NULL, signing_kid text, public_key bytea, profile_hash text,
 authority_version bigint NOT NULL DEFAULT 1 CHECK(authority_version>0),
 valid_from timestamptz NOT NULL,valid_until timestamptz NOT NULL,revoked_at timestamptz,
 CHECK(valid_until>valid_from),CHECK(public_key IS NULL OR octet_length(public_key)=32)
);
CREATE TABLE ctt_staff_private.review_batch_capabilities(
 capability_id uuid PRIMARY KEY,backend_pin text NOT NULL,account_id uuid NOT NULL,
 principal_id uuid NOT NULL REFERENCES ctt_staff_private.review_principals(principal_id),
 credential_id bytea NOT NULL REFERENCES ctt_staff_private.review_credentials(credential_id),
 tenant_id text NOT NULL,edition_id uuid NOT NULL,manifest_sha256 text NOT NULL,
 policy_hash text NOT NULL CHECK(policy_hash~'^[0-9a-f]{64}$'),profile_hash text NOT NULL CHECK(profile_hash~'^[0-9a-f]{64}$'),
 actor_role text NOT NULL,account_version bigint NOT NULL,principal_version bigint NOT NULL,credential_version bigint NOT NULL,
 authority_version bigint NOT NULL DEFAULT 1 CHECK(authority_version>0),
 valid_from timestamptz NOT NULL,valid_until timestamptz NOT NULL,revoked_at timestamptz,
 approval_ref text NOT NULL CHECK(approval_ref~'^sha256:[0-9a-f]{64}$'),
 provider_budget_units bigint NOT NULL DEFAULT 0 CHECK(provider_budget_units>=0),
 provider_price_hash text CHECK(provider_price_hash~'^[0-9a-f]{64}$'),
 FOREIGN KEY(manifest_sha256) REFERENCES ctt_staff_private.review_source_manifests_v2(manifest_sha256),
 CHECK(valid_until>valid_from)
);
CREATE UNIQUE INDEX ctt_batch_capability_live ON ctt_staff_private.review_batch_capabilities(backend_pin,account_id,edition_id,manifest_sha256) WHERE revoked_at IS NULL;

CREATE TABLE ctt_staff_private.review_batch_runs(
 run_id uuid PRIMARY KEY,backend_pin text NOT NULL,account_id uuid NOT NULL,principal_id uuid NOT NULL,
 tenant_id text NOT NULL,edition_id uuid NOT NULL,profile_hash text NOT NULL,
 corpus_hash text NOT NULL CHECK(corpus_hash~'^[0-9a-f]{64}$'),manifest_hashes jsonb NOT NULL CHECK(jsonb_typeof(manifest_hashes)='array'),
 request_hash text NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),input_snapshot jsonb NOT NULL,input_hash text NOT NULL CHECK(input_hash~'^[0-9a-f]{64}$'),
 idempotency_key uuid NOT NULL,version bigint NOT NULL DEFAULT 1,
 status text NOT NULL DEFAULT 'analysis_pending' CHECK(status IN('analysis_pending','prepared','qualified','paused','completed')),
 qualification_id uuid,cohort_metrics jsonb,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(backend_pin,account_id,idempotency_key)
);
CREATE TABLE ctt_staff_private.review_batch_capture_identities(
 capture_identity uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id text NOT NULL,edition_id uuid NOT NULL,document_id text NOT NULL,
 operation_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),committed_result_id uuid,committed_team_id uuid,
 UNIQUE(tenant_id,edition_id,document_id)
);
CREATE TABLE ctt_staff_private.review_batch_members(
 member_id uuid PRIMARY KEY,run_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_runs,
 capture_identity uuid NOT NULL REFERENCES ctt_staff_private.review_batch_capture_identities,
 document_id text NOT NULL,manifest_sha256 text NOT NULL,policy_hash text NOT NULL,
 source_payload_hash text NOT NULL,payload_hash text,scope_hash text,
 frozen_snapshot jsonb,source_pages jsonb NOT NULL,
 status text NOT NULL CHECK(status IN('analysis_pending','ready','exception','already_captured','pending','uncertain','committed','failed')),
 issues jsonb NOT NULL DEFAULT '[]',label text NOT NULL DEFAULT '',
 original_auto_ready boolean NOT NULL DEFAULT false,attempt_id uuid,authorization_generation bigint NOT NULL DEFAULT 0,
 UNIQUE(run_id,document_id),UNIQUE(attempt_id),
 FOREIGN KEY(manifest_sha256,document_id) REFERENCES ctt_staff_private.review_source_documents_v2(manifest_sha256,document_id),
 CHECK(source_payload_hash~'^[0-9a-f]{64}$'),
 CHECK(status IN('analysis_pending','exception','already_captured') OR (payload_hash~'^[0-9a-f]{64}$' AND scope_hash~'^[0-9a-f]{64}$' AND frozen_snapshot IS NOT NULL AND attempt_id IS NOT NULL))
);
CREATE TABLE ctt_staff_private.review_batch_qualifications(
 qualification_id uuid PRIMARY KEY,run_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_runs,
 candidate_bytes bytea NOT NULL,candidate_hash text NOT NULL UNIQUE CHECK(candidate_hash~'^[0-9a-f]{64}$'),
 profile_hash text NOT NULL,corpus_hash text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ctt_staff_private.review_batch_verifications(
 verification_hash text PRIMARY KEY CHECK(verification_hash~'^[0-9a-f]{64}$'),
 qualification_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_qualifications,
 member_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_members,
 proof_bytes bytea NOT NULL,service_role name NOT NULL REFERENCES ctt_staff_private.review_batch_services,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ctt_staff_private.review_batch_blocks(
 block_id uuid PRIMARY KEY,run_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_runs,
 qualification_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_qualifications,
 manifest_sha256 text NOT NULL,policy_hash text NOT NULL,root_bytes bytea NOT NULL,root_hash text NOT NULL,
 version bigint NOT NULL DEFAULT 1,status text NOT NULL DEFAULT 'prepared' CHECK(status IN('prepared','challenged','active','paused','completed','expired','revoked')),
 dispatch_generation bigint NOT NULL DEFAULT 0,consent_id uuid,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),CHECK(root_hash~'^[0-9a-f]{64}$')
);
CREATE UNIQUE INDEX ctt_batch_one_active_block ON ctt_staff_private.review_batch_blocks(run_id) WHERE status IN('challenged','active','paused');
CREATE TABLE ctt_staff_private.review_batch_block_members(
 block_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_blocks,
 member_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_members,
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 50),PRIMARY KEY(block_id,member_id),UNIQUE(block_id,ordinal)
);
CREATE TABLE ctt_staff_private.review_batch_jobs(
 member_id uuid PRIMARY KEY REFERENCES ctt_staff_private.review_batch_members,
 block_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_blocks,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN('queued','leased','uncertain','committed','failed')),
 worker_id uuid,lease_epoch bigint NOT NULL DEFAULT 0,lease_until timestamptz,
 authorization_id uuid,updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ctt_staff_private.review_batch_dispatch_slot(
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),member_id uuid REFERENCES ctt_staff_private.review_batch_members,
 worker_id uuid,lease_epoch bigint NOT NULL DEFAULT 0,lease_until timestamptz
);
INSERT INTO ctt_staff_private.review_batch_dispatch_slot(singleton) VALUES(true);
CREATE TABLE ctt_staff_private.review_batch_events(
 event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),run_id uuid NOT NULL,block_id uuid,member_id uuid,
 action text NOT NULL,recorded_by name NOT NULL DEFAULT session_user,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),details jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE ctt_staff_private.review_batch_budget_reservations(
 reservation_id uuid PRIMARY KEY,run_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_runs,
 maximum_units bigint NOT NULL CHECK(maximum_units>0),actual_units bigint CHECK(actual_units>=0 AND actual_units<=maximum_units),
 receipt_hash text,created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE ctt_staff_private.review_batch_preparations(
 preparation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),run_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_runs,
 revision bigint NOT NULL,request_hash text NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),input_snapshot jsonb NOT NULL,input_hash text NOT NULL,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(run_id,revision)
);
CREATE TABLE ctt_staff_private.review_batch_analysis_jobs(
 run_id uuid PRIMARY KEY REFERENCES ctt_staff_private.review_batch_runs,status text NOT NULL DEFAULT 'queued' CHECK(status IN('queued','leased','completed','blocked')),
 owner_id uuid,lease_epoch bigint NOT NULL DEFAULT 0,lease_until timestamptz,error_code text
);
CREATE FUNCTION ctt_staff_private.batch_uuid(p_namespace uuid,p_name text)
RETURNS uuid LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $uuid$
DECLARE raw bytea;
BEGIN
 raw:=public.digest(uuid_send(p_namespace)||convert_to(p_name,'UTF8'),'sha1');
 raw:=set_byte(raw,6,(get_byte(raw,6)&15)|80);raw:=set_byte(raw,8,(get_byte(raw,8)&63)|128);
 RETURN encode(substring(raw FROM 1 FOR 16),'hex')::uuid;
END $uuid$;
CREATE FUNCTION ctt_staff_private.batch_require_keys(p_value jsonb,p_keys text[])
RETURNS void LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $keys$
BEGIN
 IF jsonb_typeof(p_value) IS DISTINCT FROM 'object' OR NOT p_value ?& p_keys OR (SELECT count(*) FROM jsonb_object_keys(p_value))<>cardinality(p_keys) THEN RAISE EXCEPTION 'CTT batch object denied' USING ERRCODE='42501';END IF;
END $keys$;
CREATE FUNCTION ctt_staff_private.batch_json(p_bytes bytea)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE raw text;
BEGIN
 IF p_bytes IS NULL OR octet_length(p_bytes) NOT BETWEEN 2 AND 16777216 THEN RAISE EXCEPTION 'CTT batch bytes denied' USING ERRCODE='42501'; END IF;
 raw:=convert_from(p_bytes,'UTF8');
 IF raw IS NOT JSON OBJECT WITH UNIQUE KEYS THEN RAISE EXCEPTION 'CTT batch bytes denied' USING ERRCODE='42501'; END IF;
 RETURN raw::jsonb;
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_require_service(p_kind text)
RETURNS ctt_staff_private.review_batch_services LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;r record;
BEGIN
 SELECT * INTO s FROM ctt_staff_private.review_batch_services WHERE service_role=session_user AND kind=p_kind FOR SHARE;
 SELECT * INTO r FROM pg_catalog.pg_roles WHERE rolname=session_user;
 IF s.service_role IS NULL OR s.revoked_at IS NOT NULL OR clock_timestamp()<s.valid_from OR clock_timestamp()>=s.valid_until
 OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls OR NOT r.rolcanlogin
 OR session_user='devnous_user' OR current_setting('max_prepared_transactions')::int<>0
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE member=r.oid) THEN
 RAISE EXCEPTION 'CTT batch service denied' USING ERRCODE='42501'; END IF;
 RETURN s;
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_account_context(p_account uuid,p_edition uuid,p_manifest text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE cap ctt_staff_private.review_batch_capabilities%ROWTYPE;s ctt_staff_private.review_batch_services%ROWTYPE;
 p ctt_staff_private.review_principals%ROWTYPE;c ctt_staff_private.review_credentials%ROWTYPE;l ctt_staff_private.review_account_links%ROWTYPE;
BEGIN
 SELECT * INTO s FROM ctt_staff_private.review_batch_services WHERE service_role=session_user;
 SELECT * INTO cap FROM ctt_staff_private.review_batch_capabilities WHERE backend_pin=s.backend_pin AND account_id=p_account AND edition_id=p_edition AND manifest_sha256=p_manifest AND revoked_at IS NULL FOR SHARE;
 SELECT * INTO p FROM ctt_staff_private.review_principals WHERE principal_id=cap.principal_id FOR SHARE;
 SELECT * INTO c FROM ctt_staff_private.review_credentials WHERE credential_id=cap.credential_id AND principal_id=cap.principal_id FOR SHARE;
 SELECT * INTO l FROM ctt_staff_private.review_account_links WHERE backend_pin=cap.backend_pin AND account_id=cap.account_id AND edition_id=cap.edition_id AND tenant_id=cap.tenant_id FOR SHARE;
 IF cap.capability_id IS NULL OR cap.revoked_at IS NOT NULL OR clock_timestamp()<cap.valid_from OR clock_timestamp()>=cap.valid_until
 OR NOT coalesce(p.active,false) OR c.credential_id IS NULL OR c.revoked_at IS NOT NULL OR NOT coalesce(l.active,false)
 OR l.principal_id IS DISTINCT FROM p.principal_id OR l.actor_role IS DISTINCT FROM cap.actor_role
 OR l.authority_version IS DISTINCT FROM cap.account_version OR p.authority_version IS DISTINCT FROM cap.principal_version
 OR c.authority_version IS DISTINCT FROM cap.credential_version THEN
 RAISE EXCEPTION 'CTT batch capability denied' USING ERRCODE='42501'; END IF;
 RETURN to_jsonb(cap)||jsonb_build_object('credential_id',replace(encode(cap.credential_id,'base64'),E'\n',''),
 'deadline',least(cap.valid_until,s.valid_until),'service_version',s.authority_version);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_require_run(p_account uuid,p_run uuid)
RETURNS ctt_staff_private.review_batch_runs LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;manifest jsonb;ctx jsonb;s ctt_staff_private.review_batch_services%ROWTYPE;
BEGIN
 s:=ctt_staff_private.batch_require_service('workflow');
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=p_run;
 IF r.run_id IS NULL OR r.account_id IS DISTINCT FROM p_account OR r.backend_pin IS DISTINCT FROM s.backend_pin THEN
 RAISE EXCEPTION 'CTT batch run denied' USING ERRCODE='42501'; END IF;
 FOR manifest IN SELECT value FROM jsonb_array_elements(r.manifest_hashes) LOOP
 ctx:=ctt_staff_private.batch_account_context(p_account,r.edition_id,manifest#>>'{}');
 IF ctx->>'principal_id' IS DISTINCT FROM r.principal_id::text OR ctx->>'profile_hash' IS DISTINCT FROM r.profile_hash OR ctx->>'tenant_id' IS DISTINCT FROM r.tenant_id THEN
 RAISE EXCEPTION 'CTT batch run stale' USING ERRCODE='40001'; END IF;
 END LOOP;
 RETURN r;
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_catalog(p_account uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE; sources jsonb; runs jsonb;
BEGIN
 s:=ctt_staff_private.batch_require_service('workflow');
 SELECT coalesce(jsonb_agg(jsonb_build_object('manifest_sha256',c.manifest_sha256,'edition_id',c.edition_id,
 'label',m.roster_slug||' · '||m.edition_year::text,'pdf_count',jsonb_array_length(m.manifest_json->'pdfs'),
 'document_count',jsonb_array_length(m.manifest_json->'documents'))),'[]') INTO sources
 FROM ctt_staff_private.review_batch_capabilities c JOIN ctt_staff_private.review_source_manifests_v2 m USING(manifest_sha256)
 WHERE c.backend_pin=s.backend_pin AND c.account_id=p_account AND c.revoked_at IS NULL AND clock_timestamp()>=c.valid_from AND clock_timestamp()<c.valid_until;
 SELECT coalesce(jsonb_agg(jsonb_build_object('run_id',run_id,'label',left(corpus_hash,12),'status',status) ORDER BY created_at DESC),'[]') INTO runs
 FROM ctt_staff_private.review_batch_runs WHERE backend_pin=s.backend_pin AND account_id=p_account;
 RETURN jsonb_build_object('source_manifests',sources,'runs',runs,'backend_pin',s.backend_pin);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_preparation_context(p_account uuid,p_edition uuid,p_manifests jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;ctx jsonb;caps jsonb:='[]';manifest jsonb;first_ctx jsonb;
BEGIN
 s:=ctt_staff_private.batch_require_service('workflow');
 IF jsonb_typeof(p_manifests)<>'array' OR jsonb_array_length(p_manifests) NOT BETWEEN 1 AND 32
 OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(p_manifests))<>jsonb_array_length(p_manifests) THEN
 RAISE EXCEPTION 'CTT batch source selection denied' USING ERRCODE='42501'; END IF;
 FOR manifest IN SELECT value FROM jsonb_array_elements(p_manifests) LOOP
 ctx:=ctt_staff_private.batch_account_context(p_account,p_edition,manifest#>>'{}');
 IF first_ctx IS NULL THEN first_ctx:=ctx; END IF;
 IF ctx->>'principal_id' IS DISTINCT FROM first_ctx->>'principal_id' OR ctx->>'tenant_id' IS DISTINCT FROM first_ctx->>'tenant_id' OR ctx->>'profile_hash' IS DISTINCT FROM first_ctx->>'profile_hash' THEN
 RAISE EXCEPTION 'CTT batch source scope denied' USING ERRCODE='42501'; END IF;
 caps:=caps||jsonb_build_array(ctx);
 END LOOP;
 RETURN jsonb_build_object('backend_pin',s.backend_pin,'account_id',p_account,'edition_id',p_edition,
 'principal_id',first_ctx->'principal_id','tenant_id',first_ctx->'tenant_id','profile_hash',first_ctx->'profile_hash','capabilities',caps);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_assert_source_pages(p_manifest text,p_document text,p_pages jsonb)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
 SELECT p_pages IS NOT NULL AND p_pages IS NOT DISTINCT FROM (SELECT jsonb_agg(jsonb_build_object('page_index',p.page_index,'pdf_sha256',p.pdf_sha256,'pdf_page',p.pdf_page,'image_sha256',p.image_sha256,'asset_id',p.asset_id,'width',p.width,'height',p.height) ORDER BY p.page_index) FROM ctt_staff_private.review_source_pages_v2 p WHERE p.manifest_sha256=p_manifest AND p.document_id=p_document)
$fn$;
CREATE FUNCTION ctt_staff_private.batch_create_run(p_account uuid,p_run uuid,p_input jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE ctx jsonb;r ctt_staff_private.review_batch_runs%ROWTYPE;item jsonb;id uuid;mid uuid;legacy jsonb;ci ctt_staff_private.review_batch_capture_identities%ROWTYPE;
BEGIN
 ctx:=ctt_staff_private.batch_preparation_context(p_account,(p_input->>'edition_id')::uuid,p_input->'source_manifest_hashes');
 PERFORM pg_advisory_xact_lock(hashtextextended('ctt-batch-run:'||p_account::text||':'||(p_input->>'idempotency_key'),0));
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE backend_pin=ctx->>'backend_pin' AND account_id=p_account AND idempotency_key=(p_input->>'idempotency_key')::uuid;
 IF r.run_id IS NOT NULL THEN
 IF r.request_hash IS DISTINCT FROM p_input->>'request_hash' THEN RAISE EXCEPTION 'CTT batch retry bytes changed' USING ERRCODE='40001'; END IF;
 RETURN jsonb_build_object('run_id',r.run_id,'version',r.version,'status',r.status); END IF;
 IF p_input->>'corpus_hash'!~'^[0-9a-f]{64}$' OR p_input->>'input_hash'!~'^[0-9a-f]{64}$'
 OR jsonb_typeof(p_input->'members')<>'array' OR jsonb_array_length(p_input->'members')<1 THEN
 RAISE EXCEPTION 'CTT batch source payload denied' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.review_batch_runs(run_id,backend_pin,account_id,principal_id,tenant_id,edition_id,profile_hash,corpus_hash,manifest_hashes,request_hash,input_snapshot,input_hash,idempotency_key)
 VALUES(p_run,ctx->>'backend_pin',p_account,(ctx->>'principal_id')::uuid,ctx->>'tenant_id',(p_input->>'edition_id')::uuid,ctx->>'profile_hash',p_input->>'corpus_hash',p_input->'source_manifest_hashes',p_input->>'request_hash',p_input,p_input->>'input_hash',(p_input->>'idempotency_key')::uuid);
 FOR item IN SELECT value FROM jsonb_array_elements(p_input->'members') LOOP
 IF item->>'status' IS DISTINCT FROM 'analysis_pending' OR item->>'payload_hash' IS NOT NULL OR item->>'scope_hash' IS NOT NULL OR item->>'attempt_id' IS NOT NULL OR item->>'snapshot' IS NOT NULL THEN RAISE EXCEPTION 'CTT initial analysis cannot authorize capture' USING ERRCODE='42501';END IF;
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_capabilities cap WHERE cap.backend_pin=ctx->>'backend_pin' AND cap.account_id=p_account AND cap.manifest_sha256=item->>'manifest_sha256' AND cap.policy_hash=item->>'policy_hash' AND cap.profile_hash=ctx->>'profile_hash' AND cap.revoked_at IS NULL AND cap.valid_from<=clock_timestamp() AND cap.valid_until>clock_timestamp()) OR NOT ctt_staff_private.batch_assert_source_pages(item->>'manifest_sha256',item->>'document_id',item->'source_pages') OR NOT (p_input->'source_manifest_hashes') ? (item->>'manifest_sha256') OR NOT ctt_staff_private.batch_assert_source_document((p_input->>'edition_id')::uuid,item->>'manifest_sha256',item->>'document_id',item->>'source_payload_hash') THEN
 RAISE EXCEPTION 'CTT batch admitted source required' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.review_batch_capture_identities(tenant_id,edition_id,document_id,operation_id)
 VALUES(ctx->>'tenant_id',(p_input->>'edition_id')::uuid,item->>'document_id',ctt_staff_private.batch_uuid('6ba7b811-9dad-11d1-80b4-00c04fd430c8'::uuid,'ctt-batch-capture:'||(ctx->>'tenant_id')||':'||(p_input->>'edition_id')||':'||(item->>'document_id'))) ON CONFLICT(tenant_id,edition_id,document_id) DO NOTHING;
 SELECT * INTO ci FROM ctt_staff_private.review_batch_capture_identities WHERE tenant_id=ctx->>'tenant_id' AND edition_id=(p_input->>'edition_id')::uuid AND document_id=item->>'document_id' FOR UPDATE;
 legacy:=ctt_staff_private.batch_resolve_legacy_source((p_input->>'edition_id')::uuid,item->>'document_id');
 mid:=(item->>'member_id')::uuid;
 INSERT INTO ctt_staff_private.review_batch_members(member_id,run_id,capture_identity,document_id,manifest_sha256,policy_hash,source_payload_hash,payload_hash,scope_hash,frozen_snapshot,source_pages,status,issues,label,attempt_id)
 VALUES(mid,p_run,ci.capture_identity,item->>'document_id',item->>'manifest_sha256',item->>'policy_hash',item->>'source_payload_hash',item->>'payload_hash',item->>'scope_hash',item->'snapshot',item->'source_pages',
 CASE WHEN ci.committed_team_id IS NOT NULL OR legacy->>'status'='committed' THEN 'already_captured' ELSE 'analysis_pending' END,coalesce(item->'issues','[]'),coalesce(item->>'label',''),(item->>'attempt_id')::uuid);
 END LOOP;
 INSERT INTO ctt_staff_private.review_batch_analysis_jobs(run_id) VALUES(p_run);
 INSERT INTO ctt_staff_private.review_batch_events(run_id,action) VALUES(p_run,'ANALYSIS_QUEUED');
 RETURN jsonb_build_object('run_id',p_run,'version',1,'status','analysis_pending');
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_private_run(p_account uuid,p_run uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;members jsonb;candidate jsonb;
BEGIN
 r:=ctt_staff_private.batch_require_run(p_account,p_run);
 SELECT coalesce(jsonb_agg(coalesce((SELECT value FROM jsonb_array_elements(r.input_snapshot->'members') x(value) WHERE value->>'document_id'=m.document_id),'{}'::jsonb)||to_jsonb(m)||jsonb_build_object('operation_id',ci.operation_id,'human_edits',coalesce((SELECT jsonb_agg(jsonb_build_object('correction_id',x.correction_id,'principal_id',x.principal_id,'expected_version',x.expected_version,'edits',x.edits,'source_pages',x.source_pages) ORDER BY x.recorded_at,x.correction_id) FROM ctt_staff_private.review_batch_corrections x WHERE x.member_id=m.member_id),'[]'::jsonb),'human_edits_hash',coalesce(m.frozen_snapshot->>'human_edits_hash',encode(public.digest('[]','sha256'),'hex')),'verification_hash',(SELECT verification_hash FROM ctt_staff_private.review_batch_verifications v WHERE v.member_id=m.member_id AND v.qualification_id=r.qualification_id ORDER BY recorded_at DESC LIMIT 1)) ORDER BY m.document_id),'[]') INTO members FROM ctt_staff_private.review_batch_members m JOIN ctt_staff_private.review_batch_capture_identities ci USING(capture_identity) WHERE m.run_id=p_run;
 SELECT jsonb_build_object('qualification_id',q.qualification_id,'candidate_hash',q.candidate_hash,'candidate',ctt_staff_private.batch_json(q.candidate_bytes)) INTO candidate FROM ctt_staff_private.review_batch_qualifications q WHERE qualification_id=r.qualification_id;
 RETURN to_jsonb(r)||jsonb_build_object('members',members,'qualification',candidate);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_read_run(p_account uuid,p_run uuid,p_offset int,p_limit int)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;members jsonb;blocks jsonb;counts jsonb;q jsonb;qh text;lim int;next_ids jsonb;sample jsonb;
BEGIN
 r:=ctt_staff_private.batch_require_run(p_account,p_run);
 IF p_offset<0 OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'CTT batch page denied' USING ERRCODE='42501';END IF;
 SELECT coalesce(jsonb_agg(jsonb_build_object('member_id',member_id,'document_id',document_id,'session_id',frozen_snapshot->'session_id','team_name',label,'label',label,'status',status,'issues',issues,'source_pages',source_pages) ORDER BY document_id),'[]') INTO members FROM (SELECT * FROM ctt_staff_private.review_batch_members WHERE run_id=p_run ORDER BY document_id LIMIT p_limit OFFSET p_offset) mm;
 SELECT coalesce(jsonb_agg(jsonb_build_object('block_id',b.block_id,'version',b.version,'status',b.status,'root_hash',b.root_hash,'attempt_id',(SELECT ch.challenge_id FROM ctt_staff_private.review_batch_challenges ch WHERE ch.block_id=b.block_id ORDER BY ch.issued_at DESC LIMIT 1),'expires_at',(SELECT c.expires_at FROM ctt_staff_private.review_batch_consents c WHERE c.block_id=b.block_id),'member_count',(SELECT count(*) FROM ctt_staff_private.review_batch_block_members bm WHERE bm.block_id=b.block_id),'members',(SELECT jsonb_agg(jsonb_build_object('member_id',mm.member_id,'team_name',mm.label) ORDER BY bm.ordinal) FROM ctt_staff_private.review_batch_block_members bm JOIN ctt_staff_private.review_batch_members mm USING(member_id) WHERE bm.block_id=b.block_id)) ORDER BY b.created_at),'[]') INTO blocks FROM ctt_staff_private.review_batch_blocks b WHERE run_id=p_run;
 SELECT jsonb_build_object('identified',count(*),'ready',count(*)FILTER(WHERE status='ready'),'exceptions',count(*)FILTER(WHERE status='exception'),'already_captured',count(*)FILTER(WHERE status='already_captured'),'committed',count(*)FILTER(WHERE status='committed'),'failed',count(*)FILTER(WHERE status='failed'),'pending',count(*)FILTER(WHERE status IN('analysis_pending','ready','pending','uncertain'))) INTO counts FROM ctt_staff_private.review_batch_members WHERE run_id=p_run;
 SELECT ctt_staff_private.batch_json(candidate_bytes),candidate_hash INTO q,qh FROM ctt_staff_private.review_batch_qualifications WHERE qualification_id=r.qualification_id;
 lim:=CASE WHEN EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_blocks WHERE run_id=p_run AND status='completed') THEN 50 ELSE 5 END;
 SELECT coalesce(jsonb_agg(member_id ORDER BY document_id),'[]') INTO next_ids FROM (SELECT m.member_id,m.document_id FROM ctt_staff_private.review_batch_members m WHERE m.run_id=p_run AND m.status='ready' AND NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_block_members bm JOIN ctt_staff_private.review_batch_blocks bb USING(block_id) WHERE bm.member_id=m.member_id AND bb.status IN('prepared','challenged','active','paused')) AND (m.manifest_sha256,m.policy_hash)=(SELECT manifest_sha256,policy_hash FROM ctt_staff_private.review_batch_members WHERE run_id=p_run AND status='ready' ORDER BY document_id LIMIT 1) ORDER BY document_id LIMIT lim) choices;
 SELECT coalesce(jsonb_agg(jsonb_build_object('document_id',m.document_id,'team_name',m.label,'fields',coalesce(m.frozen_snapshot->'sample_fields','[]'),'source_pages',m.source_pages) ORDER BY m.document_id),'[]') INTO sample FROM ctt_staff_private.review_batch_members m WHERE m.run_id=p_run AND EXISTS(SELECT 1 FROM jsonb_array_elements(coalesce(r.input_snapshot->'sample_documents','[]')) d WHERE coalesce(d->>'document_id',d#>>'{}')=m.document_id);
 RETURN jsonb_build_object('run_id',r.run_id,'version',r.version,'status',r.status,'counts',counts,'members',members,'blocks',blocks,'metrics',q->'metrics','qualification',CASE WHEN q IS NULL THEN NULL ELSE jsonb_build_object('candidate_hash',qh,'metrics',q->'metrics','status',CASE WHEN EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_qualification_activations WHERE qualification_id=r.qualification_id) THEN 'activated' ELSE 'proposed' END) END,'sample',sample,'next_block_limit',lim,'next_member_ids',next_ids,'offset',p_offset,'limit',p_limit,'total',counts->'identified');
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_register_sample(p_account uuid,p_run uuid,p_version bigint,p_bytes bytea,p_attestations jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;c jsonb;qh text;qid uuid;proof jsonb;body jsonb;member ctt_staff_private.review_batch_members%ROWTYPE;svc ctt_staff_private.review_batch_services%ROWTYPE;
BEGIN
 r:=ctt_staff_private.batch_require_run(p_account,p_run);SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=p_run FOR UPDATE;
 IF r.version<>p_version OR EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_blocks WHERE run_id=p_run AND status IN('challenged','active','paused')) THEN RAISE EXCEPTION 'CTT batch sample stale' USING ERRCODE='40001'; END IF;
 c:=ctt_staff_private.batch_json(p_bytes);qh:=encode(public.digest(p_bytes,'sha256'),'hex');qid:=gen_random_uuid();
 IF c->>'corpus_hash' IS DISTINCT FROM r.corpus_hash OR c->>'profile_hash' IS DISTINCT FROM r.profile_hash OR jsonb_typeof(c->'metrics') IS DISTINCT FROM 'object' OR c->'metrics' IS DISTINCT FROM r.cohort_metrics OR jsonb_typeof(c->'observations')<>'array' OR coalesce((c->>'critical_false_admissions')::int,-1)<>0 THEN
 RAISE EXCEPTION 'CTT batch qualification denied' USING ERRCODE='55000'; END IF;
 INSERT INTO ctt_staff_private.review_batch_qualifications(qualification_id,run_id,candidate_bytes,candidate_hash,profile_hash,corpus_hash) VALUES(qid,p_run,p_bytes,qh,r.profile_hash,r.corpus_hash);
 FOR proof IN SELECT value FROM jsonb_array_elements(p_attestations) LOOP
 body:=proof->'body';
 PERFORM ctt_staff_private.batch_require_keys(body,ARRAY['schema','kid','document_id','manifest_sha256','policy_hash','profile_hash','scope_hash','payload_hash','source_binding_hash','qualification_hash','checks_hash','human_edits_hash','issued_at','expires_at','verified_by_human']);
 IF ctt_staff_private.batch_json(decode(proof->>'proof_base64','base64'))->'body' IS DISTINCT FROM body OR encode(public.digest(decode(proof->>'proof_base64','base64'),'sha256'),'hex') IS DISTINCT FROM proof->>'proof_hash' OR octet_length(decode(ctt_staff_private.batch_json(decode(proof->>'proof_base64','base64'))->>'signature','base64')) IS DISTINCT FROM 64 THEN RAISE EXCEPTION 'CTT batch proof bytes mismatch' USING ERRCODE='42501';END IF;
 SELECT * INTO member FROM ctt_staff_private.review_batch_members WHERE run_id=p_run AND document_id=body->>'document_id';
 SELECT * INTO svc FROM ctt_staff_private.review_batch_services WHERE kind='verifier' AND signing_kid=body->>'kid' AND profile_hash=r.profile_hash AND backend_pin=r.backend_pin AND revoked_at IS NULL AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_until;
 IF member.member_id IS NULL OR member.status<>'ready' OR svc.service_role IS NULL OR body->>'schema' IS DISTINCT FROM 'ctt.batch.verification.v1' OR body->>'qualification_hash' IS DISTINCT FROM qh OR body->>'payload_hash' IS DISTINCT FROM member.payload_hash OR body->>'scope_hash' IS DISTINCT FROM member.scope_hash OR body->>'manifest_sha256' IS DISTINCT FROM member.manifest_sha256 OR body->>'policy_hash' IS DISTINCT FROM member.policy_hash OR body->'verified_by_human' IS DISTINCT FROM 'false'::jsonb OR body->>'profile_hash' IS DISTINCT FROM r.profile_hash OR body->>'source_binding_hash' IS DISTINCT FROM member.frozen_snapshot->>'source_binding_hash' OR to_timestamp((body->>'issued_at')::bigint)>clock_timestamp() OR to_timestamp((body->>'expires_at')::bigint)<=clock_timestamp() OR (body->>'expires_at')::bigint>(body->>'issued_at')::bigint+3600 OR to_timestamp((body->>'expires_at')::bigint)>svc.valid_until OR body->>'human_edits_hash' IS DISTINCT FROM coalesce(member.frozen_snapshot->>'human_edits_hash',encode(public.digest('[]','sha256'),'hex')) THEN
 RAISE EXCEPTION 'CTT batch verifier binding denied' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.review_batch_verifications(verification_hash,qualification_id,member_id,proof_bytes,service_role)
 VALUES(proof->>'proof_hash',qid,member.member_id,decode(proof->>'proof_base64','base64'),svc.service_role);
 END LOOP;
 IF (SELECT count(*) FROM ctt_staff_private.review_batch_verifications WHERE qualification_id=qid)<>(SELECT count(*) FROM ctt_staff_private.review_batch_members WHERE run_id=p_run AND status='ready') THEN RAISE EXCEPTION 'CTT batch verification coverage denied' USING ERRCODE='42501'; END IF;
 UPDATE ctt_staff_private.review_batch_runs SET qualification_id=qid,version=version+1,status='qualified' WHERE run_id=p_run;
 INSERT INTO ctt_staff_private.review_batch_events(run_id,action,details) VALUES(p_run,'SAMPLE_PROPOSED',jsonb_build_object('candidate_hash',qh));
 RETURN jsonb_build_object('run_id',p_run,'version',r.version+1,'qualification_id',qid,'candidate_hash',qh,'status','qualified');
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_reserve_budget(p_run uuid,p_id uuid,p_units bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;cap bigint;used bigint;old ctt_staff_private.review_batch_budget_reservations%ROWTYPE;
BEGIN
 s:=ctt_staff_private.batch_require_service('verifier');SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=p_run FOR UPDATE;
 IF r.backend_pin IS DISTINCT FROM s.backend_pin OR p_units<=0 THEN RAISE EXCEPTION 'CTT batch budget denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO old FROM ctt_staff_private.review_batch_budget_reservations WHERE reservation_id=p_id;
 IF old.reservation_id IS NOT NULL THEN IF old.run_id<>p_run OR old.maximum_units<>p_units THEN RAISE EXCEPTION 'CTT batch reservation mismatch' USING ERRCODE='40001'; END IF;RETURN jsonb_build_object('reservation_id',p_id,'maximum_units',p_units);END IF;
 SELECT min(provider_budget_units) INTO cap FROM ctt_staff_private.review_batch_capabilities WHERE account_id=r.account_id AND edition_id=r.edition_id AND (r.manifest_hashes ? manifest_sha256) AND revoked_at IS NULL AND backend_pin=s.backend_pin AND profile_hash=r.profile_hash AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_until AND provider_price_hash IS NOT NULL;
 SELECT coalesce(sum(coalesce(actual_units,maximum_units)),0) INTO used FROM ctt_staff_private.review_batch_budget_reservations WHERE run_id=p_run;
 IF cap IS NULL OR used+p_units>cap THEN RAISE EXCEPTION 'CTT batch budget unavailable' USING ERRCODE='55000'; END IF;
 INSERT INTO ctt_staff_private.review_batch_budget_reservations(reservation_id,run_id,maximum_units) VALUES(p_id,p_run,p_units);
 RETURN jsonb_build_object('reservation_id',p_id,'maximum_units',p_units,'remaining_units',cap-used-p_units);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_reconcile_budget(p_id uuid,p_actual bigint,p_receipt text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;b ctt_staff_private.review_batch_budget_reservations%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;
BEGIN
 s:=ctt_staff_private.batch_require_service('verifier');SELECT * INTO b FROM ctt_staff_private.review_batch_budget_reservations WHERE reservation_id=p_id FOR UPDATE;
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=b.run_id;
 IF b.reservation_id IS NULL OR r.backend_pin IS DISTINCT FROM s.backend_pin OR p_actual NOT BETWEEN 0 AND b.maximum_units OR p_receipt!~'^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'CTT batch budget receipt denied' USING ERRCODE='42501'; END IF;
 IF b.actual_units IS NOT NULL AND (b.actual_units<>p_actual OR b.receipt_hash<>p_receipt) THEN RAISE EXCEPTION 'CTT batch budget receipt changed' USING ERRCODE='40001'; END IF;
 UPDATE ctt_staff_private.review_batch_budget_reservations SET actual_units=p_actual,receipt_hash=p_receipt WHERE reservation_id=p_id;
 RETURN jsonb_build_object('reservation_id',p_id,'actual_units',p_actual);
END $fn$;

CREATE FUNCTION ctt_staff_private.batch_verifier_context(p_kid text,p_profile text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;
BEGIN
 s:=ctt_staff_private.batch_require_service('verifier');
 IF s.signing_kid IS DISTINCT FROM p_kid OR s.profile_hash IS DISTINCT FROM p_profile OR octet_length(s.public_key)<>32 THEN RAISE EXCEPTION 'CTT batch verifier denied' USING ERRCODE='42501';END IF;
 RETURN jsonb_build_object('kid',p_kid,'profile_hash',p_profile,'public_key',replace(encode(s.public_key,'base64'),E'\n',''),'not_after_seconds',floor(extract(epoch FROM s.valid_until))::bigint,'backend_pin',s.backend_pin);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_claim_analysis(p_owner uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;j ctt_staff_private.review_batch_analysis_jobs%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;
BEGIN
 svc:=ctt_staff_private.batch_require_service('workflow');
 PERFORM pg_advisory_xact_lock(1129600051,0);
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_analysis_jobs WHERE status='leased' AND lease_until>clock_timestamp()) THEN RETURN jsonb_build_object('status','busy');END IF;
 SELECT a.* INTO j FROM ctt_staff_private.review_batch_analysis_jobs a JOIN ctt_staff_private.review_batch_runs rr USING(run_id)
 WHERE rr.backend_pin=svc.backend_pin AND (a.status='queued' OR (a.status='leased' AND a.lease_until<=clock_timestamp())) ORDER BY rr.created_at LIMIT 1 FOR UPDATE OF a;
 IF j.run_id IS NULL THEN RETURN jsonb_build_object('status','idle');END IF;
 UPDATE ctt_staff_private.review_batch_analysis_jobs SET status='leased',owner_id=p_owner,lease_epoch=lease_epoch+1,lease_until=clock_timestamp()+interval '5 minutes',error_code=NULL WHERE run_id=j.run_id RETURNING * INTO j;
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=j.run_id;
 RETURN jsonb_build_object('status','leased','run_id',r.run_id,'account_id',r.account_id,'owner_id',p_owner,'lease_epoch',j.lease_epoch);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_analysis_heartbeat(p_run uuid,p_owner uuid,p_epoch bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;
BEGIN
 s:=ctt_staff_private.batch_require_service('workflow');
 UPDATE ctt_staff_private.review_batch_analysis_jobs j SET lease_until=clock_timestamp()+interval '5 minutes' FROM ctt_staff_private.review_batch_runs r
 WHERE j.run_id=p_run AND r.run_id=j.run_id AND r.backend_pin=s.backend_pin AND j.owner_id=p_owner AND j.lease_epoch=p_epoch AND j.status='leased' AND j.lease_until>clock_timestamp();
 IF NOT FOUND THEN RAISE EXCEPTION 'CTT batch analysis lease stale' USING ERRCODE='40001';END IF;
 RETURN jsonb_build_object('status','leased');
END $fn$;
CREATE TABLE ctt_staff_private.review_batch_preparation_rows(
 nonce uuid PRIMARY KEY DEFAULT gen_random_uuid(),preparation_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_preparations,
 run_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_runs,member_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_members,
 purpose text NOT NULL CHECK(purpose='prepare_batch_draft_v1'),table_name text NOT NULL CHECK(table_name IN('copa_telmex_registration_review_sessions','copa_telmex_registration_review_drafts','copa_telmex_registration_review_assets')),
 row_id uuid NOT NULL,expected_row jsonb NOT NULL,top_xid xid8 NOT NULL,backend_pid integer NOT NULL,service_role name NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(preparation_id,table_name,row_id)
);
CREATE FUNCTION ctt_staff_private.batch_preparation_row_allowed(p_table text,p_new jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE marker ctt_staff_private.review_batch_preparation_rows%ROWTYPE;svc ctt_staff_private.review_batch_services%ROWTYPE;run ctt_staff_private.review_batch_runs%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;
BEGIN
 IF p_table NOT IN('copa_telmex_registration_review_sessions','copa_telmex_registration_review_drafts','copa_telmex_registration_review_assets') OR p_new->>'id' IS NULL THEN RETURN false;END IF;
 SELECT * INTO marker FROM ctt_staff_private.review_batch_preparation_rows WHERE table_name=p_table AND row_id=(p_new->>'id')::uuid AND top_xid=pg_current_xact_id() AND backend_pid=pg_backend_pid() AND service_role=session_user AND purpose='prepare_batch_draft_v1';
 IF marker.nonce IS NULL OR marker.expected_row IS DISTINCT FROM p_new THEN RETURN false;END IF;
 svc:=ctt_staff_private.batch_require_service('workflow');
 SELECT * INTO run FROM ctt_staff_private.review_batch_runs WHERE run_id=marker.run_id;
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE member_id=marker.member_id;
 IF run.backend_pin IS DISTINCT FROM svc.backend_pin OR run.status<>'analysis_pending' OR run.qualification_id IS NOT NULL OR m.run_id IS DISTINCT FROM run.run_id OR NOT ctt_staff_private.batch_assert_source_document(run.edition_id,m.manifest_sha256,m.document_id,m.source_payload_hash) THEN RETURN false;END IF;
 PERFORM ctt_staff_private.batch_account_context(run.account_id,run.edition_id,m.manifest_sha256);
 RETURN true;
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_guard_preparation_claims()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE next_row jsonb:=to_jsonb(NEW);old_row jsonb;typed_claim boolean;
BEGIN
 IF TG_OP='UPDATE' THEN old_row:=to_jsonb(OLD);END IF;
 typed_claim:=CASE TG_TABLE_NAME
 WHEN 'copa_telmex_registration_review_drafts' THEN
 next_row->>'mutation_type'='prepare_batch_draft_v1' OR next_row->>'mutation_operation_id' LIKE 'batch:%' OR next_row->>'mutation_receipt_id' LIKE 'ctt-batch-preparation:%' OR next_row->'review_edits'->>'source'='automatic_batch'
 WHEN 'copa_telmex_registration_review_assets' THEN
 next_row->>'image_path' LIKE 'ctt-cas-v2:%' OR next_row->>'source_ocr_run_ref' LIKE 'ctt.source.v2:%'
 WHEN 'copa_telmex_registration_review_sessions' THEN next_row->>'provider'='batch_automatic'
 ELSE false END;
 IF TG_OP='INSERT' AND coalesce(typed_claim,false) AND NOT ctt_staff_private.batch_preparation_row_allowed(TG_TABLE_NAME,next_row) THEN
 RAISE EXCEPTION 'CTT batch preparation provenance denied' USING ERRCODE='42501';
 END IF;
 IF TG_OP='UPDATE' THEN
 IF TG_TABLE_NAME='copa_telmex_registration_review_drafts' AND (coalesce(typed_claim,false) OR old_row->>'mutation_type'='prepare_batch_draft_v1') AND next_row IS DISTINCT FROM old_row THEN RAISE EXCEPTION 'CTT batch prepared draft immutable' USING ERRCODE='42501';END IF;
 IF TG_TABLE_NAME='copa_telmex_registration_review_assets' AND (coalesce(typed_claim,false) OR old_row->>'image_path' LIKE 'ctt-cas-v2:%') AND next_row IS DISTINCT FROM old_row THEN RAISE EXCEPTION 'CTT batch prepared asset immutable' USING ERRCODE='42501';END IF;
 IF TG_TABLE_NAME='copa_telmex_registration_review_sessions' AND old_row->>'provider'='batch_automatic' AND (SELECT jsonb_object_agg(key,value) FROM jsonb_each(next_row) WHERE key=ANY(ARRAY['id','provider','source','tournament_slug','created_by_user_id','started_at'])) IS DISTINCT FROM (SELECT jsonb_object_agg(key,value) FROM jsonb_each(old_row) WHERE key=ANY(ARRAY['id','provider','source','tournament_slug','created_by_user_id','started_at'])) THEN RAISE EXCEPTION 'CTT batch session provenance immutable' USING ERRCODE='42501';END IF;
 IF TG_TABLE_NAME='copa_telmex_registration_review_sessions' AND coalesce(typed_claim,false) AND old_row->>'provider' IS DISTINCT FROM 'batch_automatic' THEN RAISE EXCEPTION 'CTT batch session provenance denied' USING ERRCODE='42501';END IF;
 END IF;
 RETURN NEW;
END $fn$;
CREATE TRIGGER ctt_batch_preparation_claim_guard BEFORE INSERT OR UPDATE ON public.copa_telmex_registration_review_sessions FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.batch_guard_preparation_claims();
CREATE TRIGGER ctt_batch_preparation_claim_guard BEFORE INSERT OR UPDATE ON public.copa_telmex_registration_review_drafts FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.batch_guard_preparation_claims();
CREATE TRIGGER ctt_batch_preparation_claim_guard BEFORE INSERT OR UPDATE ON public.copa_telmex_registration_review_assets FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.batch_guard_preparation_claims();

CREATE FUNCTION ctt_staff_private.batch_materialize_review(p_run uuid,p_member uuid,p_preparation uuid,p_snapshot jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;prep ctt_staff_private.review_batch_preparations%ROWTYPE;scope jsonb;raw jsonb;raw_bytes bytea;row_json jsonb;latest record;existing record;page record;manifest record;now_utc timestamp:=clock_timestamp() AT TIME ZONE 'UTC';session_row public.copa_telmex_registration_review_sessions;draft_row public.copa_telmex_registration_review_drafts;asset_row public.copa_telmex_registration_review_assets;v_session_id uuid;draft_id uuid;version bigint;raw_hash text;
BEGIN
 r:=ctt_staff_private.batch_require_run((SELECT account_id FROM ctt_staff_private.review_batch_runs WHERE run_id=p_run),p_run);
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE member_id=p_member AND run_id=p_run;
 SELECT * INTO prep FROM ctt_staff_private.review_batch_preparations WHERE preparation_id=p_preparation AND run_id=p_run;
 IF m.member_id IS NULL OR prep.preparation_id IS NULL OR r.status<>'analysis_pending' THEN RAISE EXCEPTION 'CTT batch preparation record required' USING ERRCODE='42501';END IF;
 scope:=ctt_staff_private.batch_json(decode(p_snapshot->>'scope_bytes','base64'));raw_bytes:=decode(p_snapshot->>'raw_snapshot_bytes','base64');raw:=ctt_staff_private.batch_json(raw_bytes);raw_hash:=encode(public.digest(raw_bytes,'sha256'),'hex');
 PERFORM ctt_staff_private.batch_require_keys(raw,ARRAY['ocr_raw','extraction','validation','review_edits','layout_regions','page_manifest_hash','overall_confidence','needs_review']);
 IF raw_hash IS DISTINCT FROM scope->>'draft_content_hash' OR scope->>'run_id' IS DISTINCT FROM p_run::text OR scope->>'member_id' IS DISTINCT FROM p_member::text OR scope->>'document_id' IS DISTINCT FROM m.document_id OR scope->>'manifest_sha256' IS DISTINCT FROM m.manifest_sha256 THEN RAISE EXCEPTION 'CTT batch draft source binding denied' USING ERRCODE='42501';END IF;
 v_session_id:=(scope->>'session_id')::uuid;draft_id:=(scope->>'draft_id')::uuid;version:=(scope->>'draft_version')::bigint;
 SELECT * INTO session_row FROM public.copa_telmex_registration_review_sessions s WHERE s.id=v_session_id FOR UPDATE;
 IF FOUND AND (session_row.status IN('committed','rejected','cancelled','error') OR session_row.committed_team_id IS NOT NULL) THEN RAISE EXCEPTION 'CTT batch draft session closed' USING ERRCODE='42501';END IF;
 SELECT d.id,d.draft_version,d.content_hash,ctt_staff_private.review_raw_snapshot(d) AS raw INTO latest FROM public.copa_telmex_registration_review_drafts d WHERE d.session_id=v_session_id ORDER BY d.draft_version DESC LIMIT 1;
 IF latest.id IS NOT NULL AND latest.id=draft_id AND latest.draft_version=version AND latest.content_hash='sha256:'||raw_hash AND latest.raw=raw THEN RETURN;END IF;
 IF coalesce(latest.draft_version,0) IS DISTINCT FROM (p_snapshot->>'expected_draft_version')::bigint OR latest.id::text IS DISTINCT FROM p_snapshot->>'expected_draft_id' OR latest.content_hash IS DISTINCT FROM p_snapshot->>'expected_content_hash' OR version<>coalesce(latest.draft_version,0)+1 THEN RAISE EXCEPTION 'CTT batch canonical draft changed' USING ERRCODE='40001';END IF;
 SELECT * INTO manifest FROM ctt_staff_private.review_source_manifests_v2 WHERE manifest_sha256=m.manifest_sha256;
 IF session_row.id IS NULL THEN
 session_row:=jsonb_populate_record(NULL::public.copa_telmex_registration_review_sessions,jsonb_build_object('id',v_session_id,'status','uploaded','source','batch','provider','batch_automatic','tournament_slug',manifest.roster_slug,'created_by_user_id',r.account_id::text,'started_at',now_utc,'updated_at',now_utc));
 INSERT INTO ctt_staff_private.review_batch_preparation_rows(preparation_id,run_id,member_id,purpose,table_name,row_id,expected_row,top_xid,backend_pid,service_role) VALUES(p_preparation,p_run,p_member,'prepare_batch_draft_v1','copa_telmex_registration_review_sessions',v_session_id,to_jsonb(session_row),pg_current_xact_id(),pg_backend_pid(),session_user);
 INSERT INTO public.copa_telmex_registration_review_sessions SELECT (session_row).*;
 END IF;
 draft_row:=jsonb_populate_record(NULL::public.copa_telmex_registration_review_drafts,raw||jsonb_build_object('id',draft_id,'session_id',v_session_id,'draft_version',version,'predecessor_draft_id',latest.id,'predecessor_content_hash',latest.content_hash,'content_hash','sha256:'||raw_hash,'mutation_type','prepare_batch_draft_v1','mutation_actor_binding','sha256:'||encode(public.digest(convert_to('{"actor_id":"'||r.account_id::text||'"}','UTF8'),'sha256'),'hex'),'mutation_operation_id','batch:'||p_preparation::text||':'||p_member::text,'mutation_decision_id','sha256:'||prep.input_hash,'mutation_receipt_id','ctt-batch-preparation:'||p_preparation::text,'created_at',now_utc,'updated_at',now_utc));
 INSERT INTO ctt_staff_private.review_batch_preparation_rows(preparation_id,run_id,member_id,purpose,table_name,row_id,expected_row,top_xid,backend_pid,service_role) VALUES(p_preparation,p_run,p_member,'prepare_batch_draft_v1','copa_telmex_registration_review_drafts',draft_id,to_jsonb(draft_row),pg_current_xact_id(),pg_backend_pid(),session_user);
 INSERT INTO public.copa_telmex_registration_review_drafts SELECT (draft_row).*;
 FOR page IN SELECT * FROM ctt_staff_private.review_source_pages_v2 WHERE manifest_sha256=m.manifest_sha256 AND document_id=m.document_id ORDER BY page_index LOOP
 SELECT a.id,a.session_id,a.sha256,a.page_index INTO existing FROM public.copa_telmex_registration_review_assets a WHERE a.id=page.asset_id;
 IF existing.id IS NOT NULL THEN IF existing.session_id<>v_session_id OR existing.sha256<>page.image_sha256 OR existing.page_index<>page.page_index THEN RAISE EXCEPTION 'CTT batch asset identity conflict' USING ERRCODE='42501';END IF;CONTINUE;END IF;
 asset_row:=jsonb_populate_record(NULL::public.copa_telmex_registration_review_assets,jsonb_build_object('id',page.asset_id,'session_id',v_session_id,'page_index',page.page_index,'image_path','ctt-cas-v2:'||page.image_sha256,'sha256',page.image_sha256,'width',page.width,'height',page.height,'source_ocr_run_ref','ctt.source.v2:'||page.image_sha256,'admission_operation_id',manifest.admission_id::text,'admission_decision_id','sha256:'||encode(public.digest(manifest.authorization_bytes,'sha256'),'hex'),'admission_receipt_id','ctt-source-v2:'||manifest.admission_id::text,'created_at',now_utc));
 INSERT INTO ctt_staff_private.review_batch_preparation_rows(preparation_id,run_id,member_id,purpose,table_name,row_id,expected_row,top_xid,backend_pid,service_role) VALUES(p_preparation,p_run,p_member,'prepare_batch_draft_v1','copa_telmex_registration_review_assets',page.asset_id,to_jsonb(asset_row),pg_current_xact_id(),pg_backend_pid(),session_user);
 INSERT INTO public.copa_telmex_registration_review_assets SELECT (asset_row).*;
 END LOOP;
END $fn$;

CREATE FUNCTION ctt_staff_private.batch_complete_preparation(p_account uuid,p_run uuid,p_version bigint,p_owner uuid,p_epoch bigint,p_input jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;j ctt_staff_private.review_batch_analysis_jobs%ROWTYPE;item jsonb;m ctt_staff_private.review_batch_members%ROWTYPE;ci ctt_staff_private.review_batch_capture_identities%ROWTYPE;scope jsonb;scope_bytes bytea;review_bytes bytea;sid uuid;prep uuid:=gen_random_uuid();
BEGIN
 r:=ctt_staff_private.batch_require_run(p_account,p_run);SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=p_run FOR UPDATE;
 SELECT * INTO j FROM ctt_staff_private.review_batch_analysis_jobs WHERE run_id=p_run FOR UPDATE;
 IF r.version<>p_version OR r.status<>'analysis_pending' OR r.qualification_id IS NOT NULL OR j.owner_id IS DISTINCT FROM p_owner OR j.lease_epoch<>p_epoch OR j.status<>'leased' OR j.lease_until<=clock_timestamp()
 OR p_input->>'corpus_hash' IS DISTINCT FROM r.corpus_hash OR p_input->'source_manifest_hashes' IS DISTINCT FROM r.manifest_hashes
 OR jsonb_typeof(p_input->'members') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'CTT batch preparation stale' USING ERRCODE='40001';END IF;
 IF (SELECT count(DISTINCT value->>'document_id') FROM jsonb_array_elements(p_input->'members'))<>(SELECT count(*) FROM ctt_staff_private.review_batch_members WHERE run_id=p_run) THEN RAISE EXCEPTION 'CTT batch preparation coverage denied' USING ERRCODE='42501';END IF;
 INSERT INTO ctt_staff_private.review_batch_preparations(preparation_id,run_id,revision,request_hash,input_snapshot,input_hash) VALUES(prep,p_run,r.version,r.request_hash,p_input,p_input->>'input_hash');
 FOR item IN SELECT value FROM jsonb_array_elements(p_input->'members') LOOP
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE run_id=p_run AND document_id=item->>'document_id' FOR UPDATE;
 SELECT * INTO ci FROM ctt_staff_private.review_batch_capture_identities WHERE capture_identity=m.capture_identity FOR UPDATE;
 IF m.status='already_captured' THEN IF item->>'status' IS DISTINCT FROM 'already_captured' THEN RAISE EXCEPTION 'CTT batch captured source changed' USING ERRCODE='42501';END IF;CONTINUE;END IF;
 IF m.member_id IS NULL OR item->>'source_payload_hash' IS DISTINCT FROM m.source_payload_hash OR item->>'manifest_sha256' IS DISTINCT FROM m.manifest_sha256 OR item->>'policy_hash' IS DISTINCT FROM m.policy_hash OR item->'source_pages' IS DISTINCT FROM m.source_pages OR NOT ctt_staff_private.batch_assert_source_document(r.edition_id,m.manifest_sha256,m.document_id,m.source_payload_hash) THEN RAISE EXCEPTION 'CTT batch preparation source denied' USING ERRCODE='42501';END IF;
 IF m.status='committed' THEN
 IF item->>'status' IS DISTINCT FROM 'committed' OR item->'snapshot' IS DISTINCT FROM m.frozen_snapshot OR item->>'payload_hash' IS DISTINCT FROM m.payload_hash OR item->>'scope_hash' IS DISTINCT FROM m.scope_hash OR item->>'attempt_id' IS DISTINCT FROM m.attempt_id::text THEN RAISE EXCEPTION 'CTT batch committed preparation changed' USING ERRCODE='42501';END IF;
 CONTINUE;
 END IF;
 IF item->>'status' NOT IN('ready','exception') THEN RAISE EXCEPTION 'CTT batch preparation status denied' USING ERRCODE='42501';END IF;
 IF item->>'status'='ready' THEN
 IF coalesce(item->'snapshot'->'human_edits','[]'::jsonb) IS DISTINCT FROM coalesce((SELECT jsonb_agg(jsonb_build_object('correction_id',x.correction_id,'principal_id',x.principal_id,'expected_version',x.expected_version,'edits',x.edits,'source_pages',x.source_pages) ORDER BY x.recorded_at,x.correction_id) FROM ctt_staff_private.review_batch_corrections x WHERE x.member_id=m.member_id),'[]'::jsonb) OR EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_corrections x WHERE x.member_id=m.member_id AND x.principal_id<>r.principal_id) THEN RAISE EXCEPTION 'CTT batch human edit binding denied' USING ERRCODE='42501';END IF;
 scope_bytes:=decode(item->'snapshot'->>'scope_bytes','base64');review_bytes:=decode(item->'snapshot'->>'review_bytes','base64');scope:=ctt_staff_private.batch_json(scope_bytes);
 IF encode(public.digest(scope_bytes,'sha256'),'hex') IS DISTINCT FROM item->>'scope_hash' OR encode(public.digest(review_bytes,'sha256'),'hex') IS DISTINCT FROM item->>'payload_hash'
 OR scope->>'run_id' IS DISTINCT FROM p_run::text OR scope->>'member_id' IS DISTINCT FROM m.member_id::text OR scope->>'actor_id' IS DISTINCT FROM r.principal_id::text OR scope->>'tenant_id' IS DISTINCT FROM r.tenant_id OR scope->>'edition_id' IS DISTINCT FROM r.edition_id::text OR scope->>'manifest_sha256' IS DISTINCT FROM m.manifest_sha256 OR scope->>'document_id' IS DISTINCT FROM m.document_id OR scope->>'operation_id' IS DISTINCT FROM ci.operation_id::text OR scope->>'policy_hash' IS DISTINCT FROM m.policy_hash OR scope->'source_pages' IS DISTINCT FROM m.source_pages THEN RAISE EXCEPTION 'CTT batch prepared scope denied' USING ERRCODE='42501';END IF;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_members other JOIN ctt_staff_private.review_batch_jobs oldjob USING(member_id) WHERE other.capture_identity=m.capture_identity AND other.member_id<>m.member_id AND oldjob.status IN('leased','uncertain')) THEN RAISE EXCEPTION 'CTT batch previous outcome uncertain' USING ERRCODE='55000';END IF;
 -- Canonical session/draft/asset materialization is a narrow preparation-only
 -- owner operation, not a team/player writer and not a fabricated human proof.
 PERFORM ctt_staff_private.batch_materialize_review(p_run,m.member_id,prep,item->'snapshot');
 UPDATE ctt_staff_private.review_batch_members SET original_auto_ready=original_auto_ready OR NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_corrections c WHERE c.member_id=m.member_id),payload_hash=item->>'payload_hash',scope_hash=item->>'scope_hash',frozen_snapshot=item->'snapshot',status='ready',issues=coalesce(item->'issues','[]'),label=coalesce(item->>'label',''),attempt_id=ctt_staff_private.batch_uuid(ci.operation_id,(item->>'scope_hash')||':'||(item->>'payload_hash')) WHERE member_id=m.member_id;
 ELSE UPDATE ctt_staff_private.review_batch_members SET frozen_snapshot=item->'snapshot',status='exception',issues=coalesce(item->'issues','[]'),label=coalesce(item->>'label','') WHERE member_id=m.member_id;
 END IF;
 END LOOP;
 UPDATE ctt_staff_private.review_batch_runs SET cohort_metrics=coalesce(cohort_metrics,p_input->'metrics'),input_snapshot=p_input,input_hash=p_input->>'input_hash',status='prepared',version=version+1 WHERE run_id=p_run;
 UPDATE ctt_staff_private.review_batch_analysis_jobs SET status='completed',lease_until=NULL WHERE run_id=p_run;
 INSERT INTO ctt_staff_private.review_batch_events(run_id,action,details) VALUES(p_run,'ANALYSIS_COMPLETED',jsonb_build_object('preparation_id',prep));
 RETURN jsonb_build_object('run_id',p_run,'version',r.version+1,'status','prepared');
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_block_analysis(p_run uuid,p_owner uuid,p_epoch bigint,p_code text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;
BEGIN
 s:=ctt_staff_private.batch_require_service('workflow');
 IF p_code NOT IN('REVIEW_BLOCKED','SOURCE_UNAVAILABLE','SCOPE_DENIED','BUDGET_UNAVAILABLE') THEN RAISE EXCEPTION 'CTT batch analysis denial unavailable' USING ERRCODE='42501';END IF;
 UPDATE ctt_staff_private.review_batch_analysis_jobs j SET status='blocked',lease_until=NULL,error_code=p_code FROM ctt_staff_private.review_batch_runs r WHERE j.run_id=p_run AND r.run_id=j.run_id AND r.backend_pin=s.backend_pin AND j.owner_id=p_owner AND j.lease_epoch=p_epoch AND j.status='leased';
 IF NOT FOUND THEN RAISE EXCEPTION 'CTT batch analysis stale' USING ERRCODE='40001';END IF;
 RETURN jsonb_build_object('status','blocked','code',p_code);
END $fn$;

CREATE FUNCTION ctt_staff_private.batch_protect_frozen_state()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
BEGIN
 IF TG_TABLE_NAME='review_batch_blocks' THEN
 IF (to_jsonb(OLD)-ARRAY['status','version','dispatch_generation','consent_id']) IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','version','dispatch_generation','consent_id']) THEN RAISE EXCEPTION 'CTT batch frozen root immutable' USING ERRCODE='42501';END IF;
 ELSIF TG_TABLE_NAME='review_batch_runs' THEN
 IF (to_jsonb(OLD)-ARRAY['version','status','qualification_id','input_snapshot','input_hash','cohort_metrics']) IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['version','status','qualification_id','input_snapshot','input_hash','cohort_metrics']) OR (OLD.cohort_metrics IS NOT NULL AND OLD.cohort_metrics IS DISTINCT FROM NEW.cohort_metrics) THEN RAISE EXCEPTION 'CTT batch frozen cohort immutable' USING ERRCODE='42501';END IF;
 ELSIF TG_TABLE_NAME='review_batch_members' THEN
 IF (to_jsonb(OLD)-ARRAY['status','issues','label','authorization_generation','payload_hash','scope_hash','frozen_snapshot','attempt_id','original_auto_ready']) IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','issues','label','authorization_generation','payload_hash','scope_hash','frozen_snapshot','attempt_id','original_auto_ready']) THEN RAISE EXCEPTION 'CTT batch source identity immutable' USING ERRCODE='42501';END IF;
 IF (OLD.payload_hash,OLD.scope_hash,OLD.frozen_snapshot,OLD.attempt_id) IS DISTINCT FROM (NEW.payload_hash,NEW.scope_hash,NEW.frozen_snapshot,NEW.attempt_id) AND (OLD.original_auto_ready OR NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_runs WHERE run_id=OLD.run_id AND status='analysis_pending' AND qualification_id IS NULL)) THEN RAISE EXCEPTION 'CTT batch executable snapshot immutable' USING ERRCODE='42501';END IF;
 END IF;RETURN NEW;
END $fn$;
CREATE TRIGGER batch_frozen_root BEFORE UPDATE ON ctt_staff_private.review_batch_blocks FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.batch_protect_frozen_state();
CREATE TRIGGER batch_frozen_cohort BEFORE UPDATE ON ctt_staff_private.review_batch_runs FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.batch_protect_frozen_state();
CREATE TRIGGER batch_frozen_member BEFORE UPDATE ON ctt_staff_private.review_batch_members FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.batch_protect_frozen_state();
DO $immutable$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['review_batch_qualifications','review_batch_verifications','review_batch_preparations','review_batch_events','review_batch_block_members','review_batch_preparation_rows'] LOOP
 EXECUTE format('CREATE TRIGGER batch_evidence_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.%I FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',t);
 END LOOP;
END $immutable$;
CREATE TABLE ctt_staff_private.review_batch_preparation_acl(table_name text PRIMARY KEY,was_granted boolean NOT NULL);
INSERT INTO ctt_staff_private.review_batch_preparation_acl SELECT t,has_table_privilege('samchat_ctt_staff_owner','public.'||t,'INSERT') FROM unnest(ARRAY['copa_telmex_registration_review_sessions','copa_telmex_registration_review_drafts','copa_telmex_registration_review_assets']) t;
DO $ownership$
DECLARE item record;
BEGIN
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='ctt_staff_private' AND c.relname LIKE 'review_batch_%' AND c.relkind='r' LOOP
 EXECUTE format('ALTER TABLE ctt_staff_private.%I OWNER TO samchat_ctt_staff_owner',item.relname);
 EXECUTE format('REVOKE ALL ON ctt_staff_private.%I FROM PUBLIC,devnous_user',item.relname);
 END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='ctt_staff_private' AND p.proname LIKE 'batch_%' LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO samchat_ctt_staff_owner',item.signature);
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,devnous_user',item.signature);
 END LOOP;
END $ownership$;
GRANT INSERT ON public.copa_telmex_registration_review_sessions,public.copa_telmex_registration_review_drafts,public.copa_telmex_registration_review_assets TO samchat_ctt_staff_owner;
COMMIT;
