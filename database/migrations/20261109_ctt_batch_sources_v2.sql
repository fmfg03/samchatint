-- Additive multi-original source catalog. No runtime/operator grants are made.
BEGIN;
DO $preflight$
DECLARE owner_id oid;
BEGIN
 SELECT oid INTO owner_id FROM pg_catalog.pg_roles
 WHERE rolname='samchat_ctt_staff_owner' AND NOT rolcanlogin AND NOT rolsuper
 AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls;
 IF owner_id IS NULL OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id)
 OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname='ctt_staff_private' AND nspowner=owner_id)
 OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE') THEN
   RAISE EXCEPTION 'CTT batch source isolation invalid';
 END IF;
END $preflight$;

CREATE TABLE ctt_staff_private.review_source_manifests_v2 (
 manifest_sha256 text PRIMARY KEY CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
 edition_id uuid NOT NULL, tenant_id text NOT NULL,
 tournament_id uuid NOT NULL,edition_year integer NOT NULL CHECK(edition_year BETWEEN 1 AND 9999),
 roster_slug text NOT NULL,phase text NOT NULL,
 manifest_json jsonb NOT NULL,manifest_bytes bytea NOT NULL,
 admission_id uuid NOT NULL UNIQUE,plan_bytes bytea NOT NULL,authorization_bytes bytea NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),recorded_by name NOT NULL DEFAULT session_user,
 CHECK(octet_length(manifest_bytes) BETWEEN 2 AND 8388608),
 CHECK(octet_length(plan_bytes) BETWEEN 2 AND 8388608),
 CHECK(octet_length(authorization_bytes) BETWEEN 2 AND 8192),
 UNIQUE(edition_id,manifest_sha256)
);
CREATE TABLE ctt_staff_private.review_source_documents_v2 (
 manifest_sha256 text NOT NULL REFERENCES ctt_staff_private.review_source_manifests_v2 ON DELETE RESTRICT,
 document_id text NOT NULL CHECK(document_id ~ '^[0-9a-f]{64}$'),edition_id uuid NOT NULL,
 payload_hash text NOT NULL CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
 extraction jsonb NOT NULL,source_refs jsonb NOT NULL,
 PRIMARY KEY(manifest_sha256,document_id),
 FOREIGN KEY(edition_id,manifest_sha256) REFERENCES ctt_staff_private.review_source_manifests_v2(edition_id,manifest_sha256),
 CHECK(jsonb_typeof(source_refs)='array' AND jsonb_array_length(source_refs) BETWEEN 1 AND 4)
);
CREATE TABLE ctt_staff_private.review_source_pages_v2 (
 manifest_sha256 text NOT NULL,document_id text NOT NULL,
 page_index integer NOT NULL CHECK(page_index BETWEEN 1 AND 4),
 pdf_sha256 text NOT NULL CHECK(pdf_sha256 ~ '^[0-9a-f]{64}$'),pdf_page integer NOT NULL CHECK(pdf_page>0),
 image_sha256 text NOT NULL CHECK(image_sha256 ~ '^[0-9a-f]{64}$'),
 width integer NOT NULL CHECK(width BETWEEN 1 AND 8000),height integer NOT NULL CHECK(height BETWEEN 1 AND 8000),
 asset_id uuid NOT NULL,PRIMARY KEY(manifest_sha256,document_id,page_index),
 UNIQUE(manifest_sha256,pdf_sha256,pdf_page),
 CHECK(width::bigint*height<=25000000),
 FOREIGN KEY(manifest_sha256,document_id) REFERENCES ctt_staff_private.review_source_documents_v2 ON DELETE RESTRICT
);
CREATE TABLE ctt_staff_private.review_source_legacy_bridges_v2 (
 edition_id uuid NOT NULL,document_id text NOT NULL CHECK(document_id ~ '^[0-9a-f]{64}$'),
 legacy_session_id uuid NOT NULL UNIQUE,committed_team_id uuid,
 proof_hash text NOT NULL CHECK(proof_hash ~ '^[0-9a-f]{64}$'),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),recorded_by name NOT NULL DEFAULT session_user,
 PRIMARY KEY(edition_id,document_id)
);
DO $owners$
DECLARE tab text;
BEGIN
 FOREACH tab IN ARRAY ARRAY['review_source_manifests_v2','review_source_documents_v2','review_source_pages_v2','review_source_legacy_bridges_v2'] LOOP
 EXECUTE format('ALTER TABLE ctt_staff_private.%I OWNER TO samchat_ctt_staff_owner',tab);
 EXECUTE format('REVOKE ALL ON ctt_staff_private.%I FROM PUBLIC,devnous_user',tab);
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.%I FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',tab);
 EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON ctt_staff_private.%I FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',tab);
 END LOOP;
END $owners$;

CREATE FUNCTION ctt_staff_private.batch_assert_source_document(p_edition uuid,p_manifest text,p_document text,p_source_payload_hash text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT session_user<>'devnous_user' AND EXISTS(
 SELECT 1 FROM ctt_staff_private.review_source_documents_v2 d
 JOIN ctt_staff_private.review_source_manifests_v2 m USING(manifest_sha256)
 WHERE d.edition_id=p_edition AND m.edition_id=p_edition AND d.manifest_sha256=p_manifest
 AND d.document_id=p_document AND d.payload_hash=p_source_payload_hash)
$$;

-- This entry is for the separately admitted source operator only. The Python
-- operator verifies its pinned signature before this owner-granted SQL call.
CREATE FUNCTION ctt_staff_private.register_batch_source_admission(
 p_admission uuid,p_manifest_bytes bytea,p_manifest_identity_bytes bytea,
 p_plan_bytes bytea,p_authorization_bytes bytea,p_documents jsonb,p_pages jsonb
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $admit$
DECLARE m jsonb;identity_json jsonb;plan jsonb;proof jsonb;body jsonb;sha text;d jsonb;r jsonb;
 e uuid;doc_count integer;page_count integer;now_seconds bigint;old bytea;
BEGIN
 IF session_user='devnous_user' OR current_setting('role')<>'none'
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=session_user AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
 OR pg_catalog.pg_has_role(session_user,'samchat_ctt_staff_owner','MEMBER')
 OR p_admission IS NULL OR octet_length(p_manifest_bytes) NOT BETWEEN 2 AND 8388608
 OR octet_length(p_manifest_identity_bytes) NOT BETWEEN 2 AND 8388608
 OR octet_length(p_plan_bytes) NOT BETWEEN 2 AND 8388608 OR octet_length(p_authorization_bytes) NOT BETWEEN 2 AND 8192
 THEN RAISE EXCEPTION 'CTT batch source admission denied' USING ERRCODE='42501'; END IF;
 IF NOT(convert_from(p_manifest_bytes,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(p_manifest_identity_bytes,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(p_plan_bytes,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 OR NOT(convert_from(p_authorization_bytes,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 THEN RAISE EXCEPTION 'CTT batch source admission denied' USING ERRCODE='42501'; END IF;
 m:=convert_from(p_manifest_bytes,'UTF8')::jsonb;identity_json:=convert_from(p_manifest_identity_bytes,'UTF8')::jsonb;
 plan:=convert_from(p_plan_bytes,'UTF8')::jsonb;proof:=convert_from(p_authorization_bytes,'UTF8')::jsonb;body:=proof->'body';
 sha:=encode(public.digest(p_manifest_identity_bytes,'sha256'),'hex');e:=(m->'edition'->>'edition_id')::uuid;
 now_seconds:=floor(extract(epoch FROM clock_timestamp()))::bigint;
 IF m->>'schema_version'<>'ctt.registration.source-manifest.v2'
 OR identity_json IS DISTINCT FROM m-'manifest_sha256' OR m->>'manifest_sha256' IS DISTINCT FROM sha
 OR plan->>'schema' IS DISTINCT FROM 'ctt.source-admission.plan.v2'
 OR plan->>'manifest_sha256' IS DISTINCT FROM sha
 OR plan->'documents' IS DISTINCT FROM p_documents OR plan->'pages' IS DISTINCT FROM p_pages
 OR body->>'schema' IS DISTINCT FROM 'ctt.source-admission.authorization.v2'
 OR body->>'admission_id' IS DISTINCT FROM p_admission::text
 OR body->>'plan_sha256' IS DISTINCT FROM encode(public.digest(p_plan_bytes,'sha256'),'hex')
 OR (body->>'issued_at')::bigint>now_seconds OR (body->>'expires_at')::bigint<=now_seconds
 OR (body->>'expires_at')::bigint-(body->>'issued_at')::bigint NOT BETWEEN 1 AND 300
 OR jsonb_typeof(p_documents) IS DISTINCT FROM 'array' OR jsonb_typeof(p_pages) IS DISTINCT FROM 'array'
 OR jsonb_array_length(p_documents) NOT BETWEEN 1 AND 300 OR jsonb_array_length(p_pages) NOT BETWEEN 1 AND 1200
 OR jsonb_array_length(m->'pdfs') NOT BETWEEN 1 AND 16
 THEN RAISE EXCEPTION 'CTT batch source admission denied' USING ERRCODE='42501'; END IF;
 PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('ctt.source.v2:'||sha,0));
 SELECT manifest_bytes INTO old FROM ctt_staff_private.review_source_manifests_v2 WHERE manifest_sha256=sha;
 IF FOUND THEN
   IF old IS DISTINCT FROM p_manifest_bytes THEN RAISE EXCEPTION 'CTT source identity conflict'; END IF;
   RETURN true;
 END IF;
 INSERT INTO ctt_staff_private.review_source_manifests_v2 VALUES
 (sha,e,m->'edition'->>'tenant_id',(m->'edition'->>'tournament_id')::uuid,(m->'edition'->>'edition_year')::integer,
 m->'edition'->>'roster_slug',m->'edition'->>'phase',m,p_manifest_bytes,p_admission,p_plan_bytes,p_authorization_bytes,clock_timestamp(),session_user);
 doc_count:=0;page_count:=0;
 FOR d IN SELECT value FROM jsonb_array_elements(p_documents) LOOP
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(m->'documents') x
   WHERE x->>'document_id'=d->>'document_id' AND x->'extraction'=d->'extraction')
   OR d->>'payload_hash' IS DISTINCT FROM encode(public.digest(decode(d->>'payload_bytes_hex','hex'),'sha256'),'hex')
   OR convert_from(decode(d->>'payload_bytes_hex','hex'),'UTF8')::jsonb IS DISTINCT FROM d->'extraction'
  THEN RAISE EXCEPTION 'CTT batch source document denied'; END IF;
  INSERT INTO ctt_staff_private.review_source_documents_v2 VALUES(sha,d->>'document_id',e,d->>'payload_hash',d->'extraction',d->'source_refs');
  doc_count:=doc_count+1;
 END LOOP;
 FOR r IN SELECT value FROM jsonb_array_elements(p_pages) LOOP
  IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_source_documents_v2 d2
   WHERE d2.manifest_sha256=sha AND d2.document_id=r->>'document_id'
    AND d2.source_refs->((r->>'page_index')::integer-1)=jsonb_build_object('pdf_sha256',r->>'pdf_sha256','pdf_page',(r->>'pdf_page')::integer))
  THEN RAISE EXCEPTION 'CTT batch source page denied'; END IF;
  INSERT INTO ctt_staff_private.review_source_pages_v2 VALUES(sha,r->>'document_id',(r->>'page_index')::integer,
   r->>'pdf_sha256',(r->>'pdf_page')::integer,r->>'image_sha256',(r->>'width')::integer,(r->>'height')::integer,(r->>'asset_id')::uuid);
  page_count:=page_count+1;
 END LOOP;
 IF doc_count<>jsonb_array_length(m->'documents') OR page_count<>(SELECT sum(jsonb_array_length(source_refs)) FROM ctt_staff_private.review_source_documents_v2 WHERE manifest_sha256=sha)
 THEN RAISE EXCEPTION 'CTT batch source coverage denied'; END IF;
 RETURN true;
END $admit$;
ALTER FUNCTION ctt_staff_private.batch_assert_source_document(uuid,text,text,text) OWNER TO samchat_ctt_staff_owner;
ALTER FUNCTION ctt_staff_private.register_batch_source_admission(uuid,bytea,bytea,bytea,bytea,jsonb,jsonb) OWNER TO samchat_ctt_staff_owner;
-- Read-only discovery for the scoped private workflow. These candidate
-- definitions implement approved spec sections 2/3; they grant no EXECUTE.
-- In particular, source admission does NOT insert historical identity bridges.
CREATE FUNCTION ctt_staff_private.batch_source_catalog(p_edition uuid,p_manifests text[])
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN session_user='devnous_user' OR cardinality(p_manifests) NOT BETWEEN 1 AND 16 THEN '[]'::jsonb ELSE
 coalesce(jsonb_agg(jsonb_build_object('manifest_sha256',m.manifest_sha256,'manifest_id',m.manifest_sha256,
 'edition_id',m.edition_id,'label',m.roster_slug,'pdf_count',jsonb_array_length(m.manifest_json->'pdfs'),
 'document_count',jsonb_array_length(m.manifest_json->'documents')) ORDER BY m.manifest_sha256),'[]'::jsonb) END
 FROM ctt_staff_private.review_source_manifests_v2 m
 WHERE m.edition_id=p_edition AND m.manifest_sha256=ANY(p_manifests)
$$;
CREATE FUNCTION ctt_staff_private.batch_source_documents(p_edition uuid,p_manifests text[])
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN session_user='devnous_user' OR cardinality(p_manifests) NOT BETWEEN 1 AND 16 THEN '[]'::jsonb ELSE
 coalesce(jsonb_agg(jsonb_build_object('manifest_sha256',d.manifest_sha256,'document_id',d.document_id,
 'source_payload_hash',d.payload_hash,'extraction',d.extraction,'source_refs',d.source_refs,
 'manifest',m.manifest_json,'source_pages',(SELECT jsonb_agg(jsonb_build_object('page_index',p.page_index,
 'pdf_sha256',p.pdf_sha256,'pdf_page',p.pdf_page,'image_sha256',p.image_sha256,'asset_id',p.asset_id,
 'width',p.width,'height',p.height) ORDER BY p.page_index)
 FROM ctt_staff_private.review_source_pages_v2 p WHERE p.manifest_sha256=d.manifest_sha256 AND p.document_id=d.document_id))
 ORDER BY d.manifest_sha256,d.document_id),'[]'::jsonb) END
 FROM ctt_staff_private.review_source_documents_v2 d
 JOIN ctt_staff_private.review_source_manifests_v2 m USING(manifest_sha256)
 WHERE d.edition_id=p_edition AND d.manifest_sha256=ANY(p_manifests)
$$;
ALTER FUNCTION ctt_staff_private.batch_source_catalog(uuid,text[]) OWNER TO samchat_ctt_staff_owner;
ALTER FUNCTION ctt_staff_private.batch_source_documents(uuid,text[]) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.batch_source_catalog(uuid,text[]),
 ctt_staff_private.batch_source_documents(uuid,text[]) FROM PUBLIC,devnous_user;
REVOKE ALL ON FUNCTION ctt_staff_private.batch_assert_source_document(uuid,text,text,text),
 ctt_staff_private.register_batch_source_admission(uuid,bytea,bytea,bytea,bytea,jsonb,jsonb) FROM PUBLIC,devnous_user;
COMMIT;
