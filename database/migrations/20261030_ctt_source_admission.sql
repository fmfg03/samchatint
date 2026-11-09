-- Protected independently approved source membership; no operational grants.
BEGIN;
LOCK TABLE ctt_staff_private.manifest_anchors,ctt_staff_private.source_anchors,
 ctt_staff_private.capture_attempt_anchors IN ACCESS EXCLUSIVE MODE;
DO $preflight$
DECLARE owner_id oid; obj text; dep record;
BEGIN
 SELECT oid INTO owner_id FROM pg_catalog.pg_roles
 WHERE rolname='samchat_ctt_staff_owner' AND NOT rolcanlogin AND NOT rolsuper
   AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls;
 IF owner_id IS NULL OR EXISTS (
   SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id
 ) OR NOT EXISTS (
   SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname='ctt_staff_private' AND nspowner=owner_id
 ) OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE')
   OR pg_catalog.current_setting('max_prepared_transactions')::integer<>0 THEN
   RAISE EXCEPTION 'CTT source admission isolation invalid';
 END IF;
 FOREACH obj IN ARRAY ARRAY['manifest_anchors','source_anchors','capture_attempt_anchors'] LOOP
   IF NOT EXISTS (
     SELECT 1 FROM pg_catalog.pg_class c
     WHERE c.oid=pg_catalog.to_regclass('ctt_staff_private.'||obj)
       AND c.relowner=owner_id AND c.relkind='r' AND NOT c.relrowsecurity
       AND NOT EXISTS (
         SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
         WHERE a.grantee<>c.relowner
       ) AND NOT EXISTS (
         SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid
           AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
       )
   ) THEN RAISE EXCEPTION 'CTT source protected dependency invalid'; END IF;
 END LOOP;
 FOR dep IN SELECT * FROM (VALUES
   ('manifest_anchors','ctt_manifest_anchor_immutable','reject_review_evidence_mutation'),
   ('manifest_anchors','ctt_manifest_anchor_no_truncate','reject_review_evidence_mutation'),
   ('source_anchors','ctt_source_anchor_immutable','reject_review_evidence_mutation'),
   ('source_anchors','ctt_source_anchor_no_truncate','reject_review_evidence_mutation'),
   ('capture_attempt_anchors','ctt_capture_anchor_validate','validate_capture_anchor'),
   ('capture_attempt_anchors','ctt_capture_anchor_immutable','reject_review_evidence_mutation'),
   ('capture_attempt_anchors','ctt_capture_anchor_no_truncate','reject_review_evidence_mutation')
 ) expected(tab,trig,fn) LOOP
   IF NOT EXISTS (
     SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
     WHERE t.tgrelid=pg_catalog.to_regclass('ctt_staff_private.'||dep.tab)
       AND t.tgname=dep.trig AND t.tgenabled IN ('O','A')
       AND p.oid=pg_catalog.to_regprocedure('ctt_staff_private.'||dep.fn||'()')
       AND p.proowner=owner_id AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
       AND NOT EXISTS (
         SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
         WHERE a.grantee<>p.proowner
       )
   ) THEN RAISE EXCEPTION 'CTT source protected guard invalid'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_anchors) THEN
   RAISE EXCEPTION 'CTT source admission requires empty attempt state';
 END IF;
 IF NOT EXISTS (
   SELECT 1 FROM pg_catalog.pg_proc p
   WHERE p.oid=pg_catalog.to_regprocedure('ctt_staff_private.reject_review_evidence_mutation()')
     AND p.proowner=owner_id AND p.prosecdef
     AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
     AND NOT EXISTS (
       SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
       WHERE a.grantee<>p.proowner
     )
 ) OR NOT EXISTS (
   SELECT 1 FROM pg_catalog.pg_depend d
   JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid AND e.extname='pgcrypto'
   JOIN pg_catalog.pg_proc p ON p.oid=d.objid AND p.proowner=e.extowner
   JOIN pg_catalog.pg_roles r ON r.oid=e.extowner AND r.rolsuper
   WHERE d.classid='pg_catalog.pg_proc'::regclass
     AND d.refclassid='pg_catalog.pg_extension'::regclass
     AND p.oid=pg_catalog.to_regprocedure('public.digest(bytea,text)') AND d.deptype='e'
 ) THEN RAISE EXCEPTION 'CTT source protected function invalid'; END IF;
END
$preflight$;

CREATE TABLE ctt_staff_private.source_admissions (
 admission_id uuid PRIMARY KEY,
 manifest_sha256 text NOT NULL UNIQUE REFERENCES ctt_staff_private.manifest_anchors(manifest_sha256) ON DELETE RESTRICT,
 plan_sha256 text NOT NULL UNIQUE CHECK(plan_sha256 ~ '^sha256:[0-9a-f]{64}$'),
 plan_bytes bytea NOT NULL CHECK(octet_length(plan_bytes) BETWEEN 2 AND 1048576),
 proof_sha256 text NOT NULL UNIQUE CHECK(proof_sha256 ~ '^sha256:[0-9a-f]{64}$'),
 authorization_bytes bytea NOT NULL CHECK(octet_length(authorization_bytes) BETWEEN 2 AND 8192),
 issued_at_seconds bigint NOT NULL CHECK(issued_at_seconds>=0),
 expires_at_seconds bigint NOT NULL CHECK(expires_at_seconds>issued_at_seconds AND expires_at_seconds-issued_at_seconds<=300),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 recorded_by name NOT NULL DEFAULT session_user
);
CREATE TABLE ctt_staff_private.source_admission_documents (
 manifest_sha256 text NOT NULL REFERENCES ctt_staff_private.source_admissions(manifest_sha256) ON DELETE RESTRICT,
 document_id text NOT NULL CHECK(document_id ~ '^[0-9a-f]{64}$'),
 pdf_sha256 text NOT NULL CHECK(pdf_sha256 ~ '^[0-9a-f]{64}$'),
 payload_sha256 text NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
 source_pages_sha256 text NOT NULL CHECK(source_pages_sha256 ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(manifest_sha256,document_id),
 FOREIGN KEY(manifest_sha256,document_id,pdf_sha256)
   REFERENCES ctt_staff_private.source_anchors(manifest_sha256,document_id,pdf_sha256) ON DELETE RESTRICT
);
CREATE TABLE ctt_staff_private.source_admission_pages (
 manifest_sha256 text NOT NULL,
 document_id text NOT NULL,
 page_index integer NOT NULL CHECK(page_index BETWEEN 1 AND 4),
 pdf_page integer NOT NULL CHECK(pdf_page>0),
 image_sha256 text NOT NULL CHECK(image_sha256 ~ '^sha256:[0-9a-f]{64}$'),
 PRIMARY KEY(manifest_sha256,document_id,page_index),
 UNIQUE(manifest_sha256,document_id,pdf_page),
 FOREIGN KEY(manifest_sha256,document_id)
   REFERENCES ctt_staff_private.source_admission_documents(manifest_sha256,document_id) ON DELETE RESTRICT
);
ALTER TABLE ctt_staff_private.source_admissions OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.source_admission_documents OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.source_admission_pages OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.source_admissions,ctt_staff_private.source_admission_documents,
 ctt_staff_private.source_admission_pages FROM PUBLIC,devnous_user;

CREATE FUNCTION ctt_staff_private.register_source_admission(
 p_admission_id uuid,p_plan_bytes bytea,p_proof_sha256 text,
 p_issued_at_seconds bigint,p_expires_at_seconds bigint,p_authorization_bytes bytea
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $admit$
DECLARE
 plan_text text; plan jsonb; doc jsonb; page jsonb; pdf jsonb;
 authorization_text text; approval_json jsonb; authorization_body jsonb; signature_bytes bytea;
 manifest text; plan_hash text; old ctt_staff_private.source_admissions%ROWTYPE;
 index_value integer; last_page integer; pages_text text; previous_document text := '';
 previous_pdf text := ''; pdf_hashes text[] := ARRAY[]::text[]; used_pdfs text[] := ARRAY[]::text[];
 now_seconds bigint;
BEGIN
 IF session_user='devnous_user' OR p_admission_id IS NULL OR p_plan_bytes IS NULL OR octet_length(p_plan_bytes) NOT BETWEEN 2 AND 1048576
   OR p_proof_sha256 IS NULL OR p_proof_sha256 !~ '^sha256:[0-9a-f]{64}$'
   OR p_authorization_bytes IS NULL OR octet_length(p_authorization_bytes) NOT BETWEEN 2 AND 8192
   OR p_issued_at_seconds IS NULL OR p_expires_at_seconds IS NULL
   OR p_issued_at_seconds<0 OR p_expires_at_seconds-p_issued_at_seconds NOT BETWEEN 1 AND 300 THEN
   RAISE EXCEPTION 'CTT source admission denied' USING ERRCODE='42501';
 END IF;
 plan_text := pg_catalog.convert_from(p_plan_bytes,'UTF8');
 IF plan_text IS NOT JSON OBJECT WITH UNIQUE KEYS THEN
   RAISE EXCEPTION 'CTT source admission denied' USING ERRCODE='42501';
 END IF;
 plan := plan_text::jsonb;
 IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(plan))<>9
   OR NOT plan ?& ARRAY['schema','tenant_id','edition_id','tournament_id','edition_year','roster_slug','manifest_sha256','documents','pdfs']
   OR EXISTS(SELECT 1 FROM jsonb_each(plan) v WHERE v.key IN
     ('schema','tenant_id','edition_id','tournament_id','roster_slug','manifest_sha256')
     AND jsonb_typeof(v.value) IS DISTINCT FROM 'string')
   OR plan->>'schema' IS DISTINCT FROM 'ctt.source-admission.plan.v1'
   OR plan->>'tenant_id' IS NULL OR plan->>'tenant_id' !~ '^[a-z0-9][a-z0-9_-]{0,79}$'
   OR plan->>'roster_slug' IS NULL OR plan->>'roster_slug' !~ '^[a-z0-9][a-z0-9_-]{0,79}$'
   OR (plan->>'edition_id')::uuid::text IS DISTINCT FROM plan->>'edition_id'
   OR (plan->>'tournament_id')::uuid::text IS DISTINCT FROM plan->>'tournament_id'
   OR jsonb_typeof(plan->'edition_year') IS DISTINCT FROM 'number'
   OR (plan->>'edition_year')::integer NOT BETWEEN 1 AND 9999
   OR plan->>'manifest_sha256' IS NULL OR plan->>'manifest_sha256' !~ '^[0-9a-f]{64}$'
   OR jsonb_typeof(plan->'documents') IS DISTINCT FROM 'array'
   OR jsonb_array_length(plan->'documents') NOT BETWEEN 1 AND 300
   OR jsonb_typeof(plan->'pdfs') IS DISTINCT FROM 'array'
   OR jsonb_array_length(plan->'pdfs') NOT BETWEEN 1 AND 16 THEN
   RAISE EXCEPTION 'CTT source admission denied' USING ERRCODE='42501';
 END IF;
 manifest := plan->>'manifest_sha256';
 plan_hash := 'sha256:'||encode(public.digest(p_plan_bytes,'sha256'::text),'hex');
 authorization_text:=pg_catalog.convert_from(p_authorization_bytes,'UTF8');
 IF authorization_text IS NOT JSON OBJECT WITH UNIQUE KEYS THEN
   RAISE EXCEPTION 'CTT source approval invalid' USING ERRCODE='42501';
 END IF;
 approval_json:=authorization_text::jsonb; authorization_body:=approval_json->'body';
 IF (SELECT count(*) FROM jsonb_object_keys(approval_json))<>2 OR NOT approval_json ?& ARRAY['body','signature']
   OR jsonb_typeof(authorization_body) IS DISTINCT FROM 'object'
   OR (SELECT count(*) FROM jsonb_object_keys(authorization_body))<>6
   OR NOT authorization_body ?& ARRAY['schema','kid','admission_id','plan_sha256','issued_at','expires_at']
   OR EXISTS(SELECT 1 FROM jsonb_each(authorization_body) v WHERE v.key IN
     ('schema','kid','admission_id','plan_sha256') AND jsonb_typeof(v.value) IS DISTINCT FROM 'string')
   OR authorization_body->>'schema' IS DISTINCT FROM 'ctt.source-admission.authorization.v1'
   OR authorization_body->>'kid' IS NULL OR authorization_body->>'kid' !~ '^[A-Za-z0-9_-]{1,64}$'
   OR authorization_body->>'admission_id' IS DISTINCT FROM p_admission_id::text
   OR authorization_body->>'plan_sha256' IS DISTINCT FROM plan_hash
   OR jsonb_typeof(authorization_body->'issued_at') IS DISTINCT FROM 'number'
   OR jsonb_typeof(authorization_body->'expires_at') IS DISTINCT FROM 'number'
   OR (authorization_body->>'issued_at')::bigint IS DISTINCT FROM p_issued_at_seconds
   OR (authorization_body->>'expires_at')::bigint IS DISTINCT FROM p_expires_at_seconds
   OR jsonb_typeof(approval_json->'signature') IS DISTINCT FROM 'string'
   OR p_proof_sha256 IS DISTINCT FROM 'sha256:'||encode(public.digest(p_authorization_bytes,'sha256'::text),'hex') THEN
   RAISE EXCEPTION 'CTT source approval invalid' USING ERRCODE='42501';
 END IF;
 signature_bytes:=pg_catalog.decode(approval_json->>'signature','base64');
 IF octet_length(signature_bytes)<>64 OR replace(pg_catalog.encode(signature_bytes,'base64'),E'\n','') IS DISTINCT FROM approval_json->>'signature' THEN
   RAISE EXCEPTION 'CTT source approval invalid' USING ERRCODE='42501';
 END IF;
 PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('ctt.source-admission:'||manifest,0));
 SELECT * INTO old FROM ctt_staff_private.source_admissions WHERE admission_id=p_admission_id OR manifest_sha256=manifest;
 IF FOUND THEN
   IF old.admission_id=p_admission_id AND old.plan_bytes=p_plan_bytes AND old.proof_sha256=p_proof_sha256
     AND old.authorization_bytes=p_authorization_bytes
     AND old.issued_at_seconds=p_issued_at_seconds AND old.expires_at_seconds=p_expires_at_seconds THEN RETURN true; END IF;
   RAISE EXCEPTION 'CTT source admission conflict' USING ERRCODE='42501';
 END IF;
 now_seconds := floor(extract(epoch FROM pg_catalog.clock_timestamp()))::bigint;
 IF now_seconds<p_issued_at_seconds OR now_seconds>=p_expires_at_seconds THEN
   RAISE EXCEPTION 'CTT source admission denied' USING ERRCODE='42501';
 END IF;
 FOR pdf IN SELECT value FROM pg_catalog.jsonb_array_elements(plan->'pdfs') LOOP
   IF jsonb_typeof(pdf) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(pdf))<>2
     OR NOT pdf ?& ARRAY['pdf_sha256','byte_length']
     OR jsonb_typeof(pdf->'pdf_sha256') IS DISTINCT FROM 'string'
     OR pdf->>'pdf_sha256' IS NULL OR pdf->>'pdf_sha256' !~ '^[0-9a-f]{64}$'
     OR pdf->>'pdf_sha256'<=previous_pdf OR jsonb_typeof(pdf->'byte_length') IS DISTINCT FROM 'number'
     OR (pdf->>'byte_length')::bigint<1 THEN
     RAISE EXCEPTION 'CTT source admission denied' USING ERRCODE='42501';
   END IF;
   previous_pdf := pdf->>'pdf_sha256'; pdf_hashes := array_append(pdf_hashes,previous_pdf);
 END LOOP;
 FOR doc IN SELECT value FROM pg_catalog.jsonb_array_elements(plan->'documents') LOOP
   IF jsonb_typeof(doc) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(doc))<>5
     OR NOT doc ?& ARRAY['document_id','pdf_sha256','payload_sha256','source_pages_sha256','pages']
     OR EXISTS(SELECT 1 FROM jsonb_each(doc) v WHERE v.key<>'pages'
       AND jsonb_typeof(v.value) IS DISTINCT FROM 'string')
     OR doc->>'document_id' IS NULL OR doc->>'document_id' !~ '^[0-9a-f]{64}$'
     OR doc->>'document_id'<=previous_document
     OR doc->>'pdf_sha256' IS NULL OR NOT (doc->>'pdf_sha256'=ANY(pdf_hashes))
     OR doc->>'payload_sha256' IS NULL OR doc->>'payload_sha256' !~ '^[0-9a-f]{64}$'
     OR doc->>'source_pages_sha256' IS NULL OR doc->>'source_pages_sha256' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(doc->'pages') IS DISTINCT FROM 'array'
     OR jsonb_array_length(doc->'pages') NOT BETWEEN 1 AND 4 THEN
     RAISE EXCEPTION 'CTT source admission denied' USING ERRCODE='42501';
   END IF;
   previous_document := doc->>'document_id';
   IF NOT doc->>'pdf_sha256'=ANY(used_pdfs) THEN used_pdfs:=array_append(used_pdfs,doc->>'pdf_sha256'); END IF;
   index_value:=0; last_page:=0; pages_text:='[';
   FOR page IN SELECT value FROM jsonb_array_elements(doc->'pages') LOOP
     index_value:=index_value+1;
     IF jsonb_typeof(page) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(page))<>3
       OR NOT page ?& ARRAY['page_index','pdf_page','image_sha256']
       OR jsonb_typeof(page->'image_sha256') IS DISTINCT FROM 'string'
       OR jsonb_typeof(page->'page_index') IS DISTINCT FROM 'number'
       OR (page->>'page_index')::integer IS DISTINCT FROM index_value
       OR jsonb_typeof(page->'pdf_page') IS DISTINCT FROM 'number'
       OR (page->>'pdf_page')::integer<=last_page
       OR page->>'image_sha256' IS NULL OR page->>'image_sha256' !~ '^sha256:[0-9a-f]{64}$' THEN
       RAISE EXCEPTION 'CTT source admission denied' USING ERRCODE='42501';
     END IF;
     last_page:=(page->>'pdf_page')::integer;
     pages_text:=pages_text||CASE WHEN index_value>1 THEN ',' ELSE '' END||last_page::text;
   END LOOP;
   pages_text:=pages_text||']';
   IF encode(public.digest(convert_to(pages_text,'UTF8'),'sha256'::text),'hex')<>doc->>'source_pages_sha256' THEN
     RAISE EXCEPTION 'CTT source admission denied' USING ERRCODE='42501';
   END IF;
 END LOOP;
 IF cardinality(used_pdfs)<>cardinality(pdf_hashes) THEN
   RAISE EXCEPTION 'CTT source admission denied' USING ERRCODE='42501';
 END IF;
 INSERT INTO ctt_staff_private.manifest_anchors(manifest_sha256,tenant_id,edition_id,tournament_id,edition_year,roster_slug)
 VALUES(manifest,plan->>'tenant_id',(plan->>'edition_id')::uuid,(plan->>'tournament_id')::uuid,
   (plan->>'edition_year')::integer,plan->>'roster_slug') ON CONFLICT DO NOTHING;
 IF NOT EXISTS (
   SELECT 1 FROM ctt_staff_private.manifest_anchors m WHERE m.manifest_sha256=manifest
   AND m.tenant_id=plan->>'tenant_id' AND m.edition_id=(plan->>'edition_id')::uuid
   AND m.tournament_id=(plan->>'tournament_id')::uuid AND m.edition_year=(plan->>'edition_year')::integer
   AND m.roster_slug=plan->>'roster_slug'
 ) OR EXISTS (
   SELECT 1 FROM ctt_staff_private.source_anchors s WHERE s.manifest_sha256=manifest
   AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(plan->'documents') d
     WHERE d->>'document_id'=s.document_id AND d->>'pdf_sha256'=s.pdf_sha256)
 ) THEN RAISE EXCEPTION 'CTT source admission conflict' USING ERRCODE='42501'; END IF;
 FOR doc IN SELECT value FROM jsonb_array_elements(plan->'documents') LOOP
   INSERT INTO ctt_staff_private.source_anchors(manifest_sha256,document_id,pdf_sha256)
   VALUES(manifest,doc->>'document_id',doc->>'pdf_sha256') ON CONFLICT DO NOTHING;
 END LOOP;
 INSERT INTO ctt_staff_private.source_admissions(
   admission_id,manifest_sha256,plan_sha256,plan_bytes,proof_sha256,issued_at_seconds,expires_at_seconds,authorization_bytes)
 VALUES(p_admission_id,manifest,plan_hash,p_plan_bytes,p_proof_sha256,p_issued_at_seconds,p_expires_at_seconds,p_authorization_bytes);
 FOR doc IN SELECT value FROM jsonb_array_elements(plan->'documents') LOOP
   INSERT INTO ctt_staff_private.source_admission_documents
   VALUES(manifest,doc->>'document_id',doc->>'pdf_sha256',doc->>'payload_sha256',doc->>'source_pages_sha256');
   FOR page IN SELECT value FROM jsonb_array_elements(doc->'pages') LOOP
     INSERT INTO ctt_staff_private.source_admission_pages
     VALUES(manifest,doc->>'document_id',(page->>'page_index')::integer,
       (page->>'pdf_page')::integer,page->>'image_sha256');
   END LOOP;
 END LOOP;
 RETURN true;
EXCEPTION WHEN data_exception OR integrity_constraint_violation THEN
 RAISE EXCEPTION 'CTT source admission denied' USING ERRCODE='42501';
END
$admit$;

CREATE FUNCTION ctt_staff_private.check_capture_source_admission()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $check$
DECLARE scope jsonb; page jsonb; index_value integer:=0; expected_count integer; seen_assets text[]:=ARRAY[]::text[];
BEGIN
 scope:=pg_catalog.convert_from(NEW.scope_bytes,'UTF8')::jsonb;
 IF NOT EXISTS (
   SELECT 1 FROM ctt_staff_private.source_admission_documents d
   JOIN ctt_staff_private.source_admissions a ON a.manifest_sha256=d.manifest_sha256
   JOIN ctt_staff_private.manifest_anchors m ON m.manifest_sha256=a.manifest_sha256
   WHERE d.manifest_sha256=NEW.manifest_sha256 AND d.document_id=NEW.document_id AND d.pdf_sha256=NEW.pdf_sha256
     AND m.tenant_id=NEW.tenant_id AND m.edition_id=NEW.edition_id
 ) OR jsonb_typeof(scope->'source_pages') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'CTT source admission required' USING ERRCODE='42501';
 END IF;
 SELECT count(*) INTO expected_count FROM ctt_staff_private.source_admission_pages
 WHERE manifest_sha256=NEW.manifest_sha256 AND document_id=NEW.document_id;
 IF jsonb_array_length(scope->'source_pages')<>expected_count OR expected_count<1 THEN
   RAISE EXCEPTION 'CTT source admission required' USING ERRCODE='42501';
 END IF;
 FOR page IN SELECT value FROM jsonb_array_elements(scope->'source_pages') LOOP
   index_value:=index_value+1;
   IF jsonb_typeof(page) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(page))<>4
     OR NOT page ?& ARRAY['page_index','asset_id','pdf_page','image_sha256']
     OR jsonb_typeof(page->'page_index') IS DISTINCT FROM 'number'
     OR (page->>'page_index')::integer IS DISTINCT FROM index_value
     OR page->>'asset_id' IS NULL
     OR jsonb_typeof(page->'asset_id') IS DISTINCT FROM 'string'
     OR (page->>'asset_id')::uuid::text IS DISTINCT FROM page->>'asset_id'
     OR jsonb_typeof(page->'pdf_page') IS DISTINCT FROM 'number'
     OR page->>'asset_id'=ANY(seen_assets)
     OR NOT EXISTS (
       SELECT 1 FROM ctt_staff_private.source_admission_pages p
       WHERE p.manifest_sha256=NEW.manifest_sha256 AND p.document_id=NEW.document_id
         AND p.page_index=index_value AND p.pdf_page=(page->>'pdf_page')::integer
         AND p.image_sha256=page->>'image_sha256'
     ) THEN RAISE EXCEPTION 'CTT source admission required' USING ERRCODE='42501'; END IF;
   seen_assets:=array_append(seen_assets,page->>'asset_id');
 END LOOP;
 RETURN NEW;
EXCEPTION WHEN data_exception THEN
 RAISE EXCEPTION 'CTT source admission required' USING ERRCODE='42501';
END
$check$;
ALTER FUNCTION ctt_staff_private.register_source_admission(uuid,bytea,text,bigint,bigint,bytea) OWNER TO samchat_ctt_staff_owner;
ALTER FUNCTION ctt_staff_private.check_capture_source_admission() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.register_source_admission(uuid,bytea,text,bigint,bigint,bytea),
 ctt_staff_private.check_capture_source_admission() FROM PUBLIC,devnous_user;
CREATE TRIGGER ctt_capture_anchor_source_admission BEFORE INSERT ON ctt_staff_private.capture_attempt_anchors
FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.check_capture_source_admission();

DO $secure$
DECLARE obj text;
BEGIN
 FOREACH obj IN ARRAY ARRAY['source_admissions','source_admission_documents','source_admission_pages'] LOOP
   EXECUTE format('CREATE TRIGGER ctt_source_admission_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.%I FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',obj);
   EXECUTE format('CREATE TRIGGER ctt_source_admission_no_truncate BEFORE TRUNCATE ON ctt_staff_private.%I FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',obj);
 END LOOP;
END
$secure$;
DO $postflight$
BEGIN
 IF EXISTS (
   SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
   CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
   WHERE n.nspname='ctt_staff_private'
     AND c.relname IN ('source_admissions','source_admission_documents','source_admission_pages')
     AND a.grantee<>c.relowner
 ) OR EXISTS (
   SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid IN (
     'ctt_staff_private.source_admissions'::regclass,'ctt_staff_private.source_admission_documents'::regclass,
     'ctt_staff_private.source_admission_pages'::regclass)
     AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
 ) OR EXISTS (
   SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
   CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
   WHERE n.nspname='ctt_staff_private'
     AND p.proname IN ('register_source_admission','check_capture_source_admission') AND a.grantee<>p.proowner
 ) THEN RAISE EXCEPTION 'CTT source admission ACL invalid'; END IF;
END
$postflight$;
COMMIT;
