-- Batch consent is a separate domain; never a legacy WebAuthn assertion.
BEGIN;
DO $preflight$ BEGIN
 IF to_regclass('ctt_staff_private.review_batch_runs') IS NULL OR to_regclass('ctt_staff_private.capture_results') IS NULL THEN RAISE EXCEPTION 'CTT batch consent dependencies unavailable'; END IF;
END $preflight$;
CREATE TABLE ctt_staff_private.review_batch_challenges(
 challenge_id uuid PRIMARY KEY,consent_id uuid NOT NULL UNIQUE,block_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_blocks,
 credential_id bytea NOT NULL REFERENCES ctt_staff_private.review_credentials,
 challenge_bytes bytea NOT NULL CHECK(octet_length(challenge_bytes)=32),
 capability_version bigint NOT NULL,credential_version bigint NOT NULL,principal_version bigint NOT NULL,account_version bigint NOT NULL,
 issuer_role name NOT NULL,issuer_version bigint NOT NULL,
 issued_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 CHECK(expires_at>issued_at AND expires_at<=issued_at+interval '5 minutes')
);
CREATE TABLE ctt_staff_private.review_batch_consents(
 consent_id uuid PRIMARY KEY,challenge_id uuid NOT NULL UNIQUE REFERENCES ctt_staff_private.review_batch_challenges,
 block_id uuid NOT NULL UNIQUE REFERENCES ctt_staff_private.review_batch_blocks,
 proof_bytes bytea NOT NULL,proof_hash text NOT NULL CHECK(proof_hash~'^[0-9a-f]{64}$'),
 issuer_role name NOT NULL,issued_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 CHECK(expires_at>issued_at AND expires_at<=issued_at+interval '1 hour')
);
CREATE TABLE ctt_staff_private.review_batch_qualification_activations(
 activation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),qualification_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_qualifications,
 consent_id uuid NOT NULL UNIQUE REFERENCES ctt_staff_private.review_batch_consents,
 principal_id uuid NOT NULL REFERENCES ctt_staff_private.review_principals,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ctt_staff_private.review_batch_child_authorizations(
 authorization_id uuid PRIMARY KEY,consent_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_consents,
 member_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_members,attempt_id uuid NOT NULL,operation_id uuid NOT NULL,
 worker_id uuid NOT NULL,lease_epoch bigint NOT NULL,dispatch_generation bigint NOT NULL,authorization_generation bigint NOT NULL,
 nonce uuid NOT NULL UNIQUE,proof_bytes bytea NOT NULL,proof_hash text NOT NULL,
 issued_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,recorded_by name NOT NULL DEFAULT session_user,
 UNIQUE(member_id,authorization_generation),CHECK(expires_at>issued_at AND expires_at<=issued_at+interval '5 minutes')
);
CREATE TABLE ctt_staff_private.review_batch_consumptions(
 capture_identity uuid PRIMARY KEY REFERENCES ctt_staff_private.review_batch_capture_identities,
 authorization_id uuid NOT NULL UNIQUE REFERENCES ctt_staff_private.review_batch_child_authorizations,
 member_id uuid NOT NULL UNIQUE REFERENCES ctt_staff_private.review_batch_members,
 attempt_id uuid NOT NULL UNIQUE,result_id uuid NOT NULL UNIQUE,team_id uuid NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- Owner-only source identity lookup; no application privileges.
CREATE TABLE ctt_staff_private.batch_source_read_baseline(was_granted boolean NOT NULL);
INSERT INTO ctt_staff_private.batch_source_read_baseline VALUES(has_table_privilege('samchat_ctt_staff_owner','public.copa_telmex_registration_batch_documents','SELECT'));
ALTER TABLE ctt_staff_private.batch_source_read_baseline OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.batch_source_read_baseline FROM PUBLIC,devnous_user;
GRANT SELECT ON public.copa_telmex_registration_batch_documents TO samchat_ctt_staff_owner;
-- Read-only equivalence of exact physical source references; no historical
-- admission, review or capture record is inserted/rewritten by these functions.
CREATE FUNCTION ctt_staff_private.batch_resolve_legacy_source(p_edition uuid,p_document text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE target jsonb;matches jsonb;n int;one jsonb;session_id uuid;draft_row record;session_row record;result_row record;
BEGIN
 IF session_user='devnous_user' THEN RAISE EXCEPTION 'CTT batch source lookup denied' USING ERRCODE='42501';END IF;
 SELECT source_refs INTO target FROM ctt_staff_private.review_source_documents_v2 WHERE edition_id=p_edition AND document_id=p_document ORDER BY manifest_sha256 LIMIT 1;
 IF target IS NULL THEN RETURN jsonb_build_object('status','unknown');END IF;
 SELECT coalesce(jsonb_agg(DISTINCT candidate),'[]') INTO matches FROM (
 SELECT jsonb_build_object('legacy_session_id',b.review_session_id,'legacy_document_id',b.document_id) candidate
 FROM ctt_staff_private.source_admission_documents d
 JOIN ctt_staff_private.manifest_anchors ma USING(manifest_sha256)
 JOIN public.copa_telmex_registration_batch_documents b ON b.tournament_edition_id=ma.edition_id AND b.document_id=d.document_id AND b.pdf_sha256=d.pdf_sha256 AND b.payload_sha256=d.payload_sha256 AND b.source_pages_sha256=d.source_pages_sha256
 WHERE ma.edition_id=p_edition
 AND (SELECT jsonb_agg(jsonb_build_object('pdf_sha256',d.pdf_sha256,'pdf_page',pg.pdf_page) ORDER BY pg.page_index) FROM ctt_staff_private.source_admission_pages pg WHERE pg.manifest_sha256=d.manifest_sha256 AND pg.document_id=d.document_id)=target
 AND b.source_pages::jsonb=(SELECT jsonb_agg(pg.pdf_page ORDER BY pg.page_index) FROM ctt_staff_private.source_admission_pages pg WHERE pg.manifest_sha256=d.manifest_sha256 AND pg.document_id=d.document_id)
 ) candidates;
 n:=jsonb_array_length(matches);
 IF n=0 THEN RETURN jsonb_build_object('status','none');END IF;
 IF n<>1 THEN RETURN jsonb_build_object('status','ambiguous','matches',n);END IF;
 one:=matches->0;session_id:=(one->>'legacy_session_id')::uuid;
 SELECT id,status,committed_team_id INTO session_row FROM public.copa_telmex_registration_review_sessions WHERE id=session_id;
 IF session_row.id IS NULL THEN RETURN jsonb_build_object('status','ambiguous','matches',1);END IF;
 SELECT d.id,d.draft_version,d.content_hash INTO draft_row FROM public.copa_telmex_registration_review_drafts d WHERE d.session_id=session_row.id ORDER BY d.draft_version DESC LIMIT 1;
 SELECT rr.result_id,rr.team_id INTO result_row FROM ctt_staff_private.capture_results rr JOIN ctt_staff_private.capture_attempt_anchors a USING(attempt_id) WHERE a.edition_id=p_edition AND (convert_from(a.scope_bytes,'UTF8')::jsonb)->>'session_id'=session_id::text ORDER BY a.created_at DESC LIMIT 1;
 RETURN one||jsonb_build_object('status',CASE WHEN session_row.committed_team_id IS NOT NULL OR result_row.result_id IS NOT NULL THEN 'committed' ELSE 'pending' END,
 'committed_team_id',coalesce(session_row.committed_team_id,result_row.team_id),'result_id',result_row.result_id,
 'current_draft_id',draft_row.id,'current_draft_version',coalesce(draft_row.draft_version,0),'current_content_hash',draft_row.content_hash,
 'next_draft_version',coalesce(draft_row.draft_version,0)+1,'source_refs',target,
 'proof_hash',encode(public.digest(convert_to((jsonb_build_object('edition_id',p_edition,'document_id',p_document,'source_refs',target,'legacy',one))::text,'UTF8'),'sha256'),'hex'));
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_legacy_capture_identity(p_legacy_session uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE item record;answer jsonb;resolved jsonb;hits int:=0;
BEGIN
 IF session_user='devnous_user' THEN RAISE EXCEPTION 'CTT batch legacy identity denied' USING ERRCODE='42501';END IF;
 FOR item IN SELECT DISTINCT d.edition_id,d.document_id,m.tenant_id FROM ctt_staff_private.review_source_documents_v2 d JOIN ctt_staff_private.review_source_manifests_v2 m USING(manifest_sha256) LOOP
 resolved:=ctt_staff_private.batch_resolve_legacy_source(item.edition_id,item.document_id);
 IF resolved->>'legacy_session_id'=p_legacy_session::text AND resolved->>'status' IN('pending','committed') THEN
 hits:=hits+1;answer:=jsonb_build_object('tenant_id',item.tenant_id,'edition_id',item.edition_id,'document_id',item.document_id,'legacy_session_id',p_legacy_session,'proof_hash',resolved->'proof_hash');END IF;
 END LOOP;
 IF hits=0 THEN RETURN jsonb_build_object('status','none');END IF;
 IF hits<>1 THEN RETURN jsonb_build_object('status','ambiguous');END IF;
 RETURN answer||jsonb_build_object('status','exact');
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_source_context(p_account uuid,p_edition uuid,p_manifests jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE ctx jsonb;docs jsonb;d jsonb;items jsonb:='[]';legacy jsonb;op uuid;sid uuid;current_draft record;current_session record;
BEGIN
 ctx:=ctt_staff_private.batch_preparation_context(p_account,p_edition,p_manifests);
 docs:=ctt_staff_private.batch_source_documents(p_edition,ARRAY(SELECT value#>>'{}' FROM jsonb_array_elements(p_manifests)));
 FOR d IN SELECT value FROM jsonb_array_elements(docs) LOOP
 legacy:=ctt_staff_private.batch_resolve_legacy_source(p_edition,d->>'document_id');
 op:=ctt_staff_private.batch_uuid('6ba7b811-9dad-11d1-80b4-00c04fd430c8'::uuid,'ctt-batch-capture:'||(ctx->>'tenant_id')||':'||p_edition::text||':'||(d->>'document_id'));
 sid:=CASE WHEN legacy->>'status' IN('pending','committed') THEN (legacy->>'legacy_session_id')::uuid ELSE ctt_staff_private.batch_uuid(op,'session') END;
 SELECT rd.id,rd.draft_version,rd.content_hash INTO current_draft FROM public.copa_telmex_registration_review_drafts rd WHERE rd.session_id=sid ORDER BY rd.draft_version DESC LIMIT 1;
 SELECT rs.status,rs.committed_team_id INTO current_session FROM public.copa_telmex_registration_review_sessions rs WHERE rs.id=sid;
 items:=items||jsonb_build_array(d||jsonb_build_object('operation_id',op,'session_id',sid,'legacy',legacy,'expected_draft_version',coalesce(current_draft.draft_version,0),'draft_version',coalesce(current_draft.draft_version,0)+1,'expected_draft_id',current_draft.id,'expected_content_hash',current_draft.content_hash,'session_status',current_session.status,'committed_team_id',current_session.committed_team_id));
 END LOOP;
 RETURN ctx||jsonb_build_object('documents',items);
END $fn$;

CREATE FUNCTION ctt_staff_private.batch_expire_blocks(p_run uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;
BEGIN
 SELECT * INTO svc FROM ctt_staff_private.review_batch_services WHERE service_role=session_user;
 IF svc.kind NOT IN('workflow','worker') THEN RAISE EXCEPTION 'CTT batch expiry service denied' USING ERRCODE='42501';END IF;
 PERFORM ctt_staff_private.batch_require_service(svc.kind);
 PERFORM 1 FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton FOR UPDATE;
 FOR b IN SELECT bb.* FROM ctt_staff_private.review_batch_blocks bb JOIN ctt_staff_private.review_batch_runs rr USING(run_id) LEFT JOIN ctt_staff_private.review_batch_consents cc USING(consent_id) WHERE rr.backend_pin=svc.backend_pin AND (p_run IS NULL OR rr.run_id=p_run) AND (bb.status IN('revoked','expired') OR bb.status IN('active','paused') AND cc.expires_at<=clock_timestamp() OR bb.status='challenged' AND NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_challenges ch WHERE ch.block_id=bb.block_id AND ch.expires_at>clock_timestamp())) ORDER BY bb.block_id FOR UPDATE OF bb LOOP
 IF b.status<>'revoked' THEN UPDATE ctt_staff_private.review_batch_blocks SET status='expired',dispatch_generation=dispatch_generation+1,version=version+1 WHERE block_id=b.block_id AND status<>'expired';END IF;
 FOR m IN SELECT mm.* FROM ctt_staff_private.review_batch_block_members bm JOIN ctt_staff_private.review_batch_members mm USING(member_id) WHERE bm.block_id=b.block_id AND mm.status IN('pending','uncertain','exception') LOOP
 PERFORM 1 FROM ctt_staff_private.review_batch_capture_identities WHERE capture_identity=m.capture_identity FOR UPDATE;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_results WHERE attempt_id=m.attempt_id) THEN RAISE EXCEPTION 'CTT batch result requires canonical reconciliation' USING ERRCODE='55000';END IF;
 UPDATE ctt_staff_private.review_batch_members SET status=CASE WHEN status='exception' THEN 'exception' ELSE 'ready' END WHERE member_id=m.member_id;
 UPDATE ctt_staff_private.review_batch_jobs SET status='failed',lease_until=NULL WHERE member_id=m.member_id;
 UPDATE ctt_staff_private.review_batch_dispatch_slot SET member_id=NULL,worker_id=NULL,lease_until=NULL,lease_epoch=lease_epoch+1 WHERE singleton AND member_id=m.member_id;
 END LOOP;
 END LOOP;RETURN true;
END $fn$;

CREATE FUNCTION ctt_staff_private.batch_private_block(p_account uuid,p_block uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;q ctt_staff_private.review_batch_qualifications%ROWTYPE;members jsonb;
BEGIN
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=p_block;
 r:=ctt_staff_private.batch_require_run(p_account,b.run_id);
 SELECT * INTO q FROM ctt_staff_private.review_batch_qualifications WHERE qualification_id=b.qualification_id;
 SELECT jsonb_agg(to_jsonb(m)||jsonb_build_object('operation_id',ci.operation_id,'verification',
 (SELECT jsonb_build_object('verification_hash',v.verification_hash,'proof_base64',replace(encode(v.proof_bytes,'base64'),E'\n','')) FROM ctt_staff_private.review_batch_verifications v WHERE v.member_id=m.member_id AND v.qualification_id=q.qualification_id ORDER BY recorded_at DESC LIMIT 1)) ORDER BY bm.ordinal) INTO members
 FROM ctt_staff_private.review_batch_block_members bm JOIN ctt_staff_private.review_batch_members m USING(member_id) JOIN ctt_staff_private.review_batch_capture_identities ci USING(capture_identity) WHERE bm.block_id=p_block;
 RETURN to_jsonb(b)||jsonb_build_object('root_bytes',replace(encode(b.root_bytes,'base64'),E'\n',''),'principal_id',r.principal_id,'account_id',r.account_id,'tenant_id',r.tenant_id,'edition_id',r.edition_id,'qualification_hash',q.candidate_hash,'candidate',ctt_staff_private.batch_json(q.candidate_bytes),'members',members);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_prepare_block(p_account uuid,p_run uuid,p_block uuid,p_version bigint,p_members jsonb,p_root bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;q ctt_staff_private.review_batch_qualifications%ROWTYPE;root jsonb;member ctt_staff_private.review_batch_members%ROWTYPE;one jsonb;manifest text;policy text;idx int:=0;lim int;metrics jsonb;expected jsonb;metric_numerator bigint;metric_denominator bigint;
BEGIN
 PERFORM ctt_staff_private.batch_expire_blocks(p_run);
 r:=ctt_staff_private.batch_require_run(p_account,p_run);SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=p_run FOR UPDATE;
 IF r.version<>p_version OR r.qualification_id IS NULL THEN RAISE EXCEPTION 'CTT batch run stale' USING ERRCODE='40001'; END IF;
 SELECT * INTO q FROM ctt_staff_private.review_batch_qualifications WHERE qualification_id=r.qualification_id;
 metrics:=ctt_staff_private.batch_json(q.candidate_bytes)->'metrics';
 lim:=CASE WHEN EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_blocks WHERE run_id=p_run AND status='completed') THEN 50 ELSE 5 END;
 IF lim=50 THEN
 FOR one IN SELECT value FROM jsonb_array_elements(jsonb_build_array(metrics->'teams',metrics->'pages')) LOOP
 IF jsonb_typeof(one) IS DISTINCT FROM 'object' OR jsonb_typeof(one->'numerator') IS DISTINCT FROM 'number' OR jsonb_typeof(one->'denominator') IS DISTINCT FROM 'number' OR NOT(coalesce(one->>'numerator','')~'^(0|[1-9][0-9]{0,9})$') OR NOT(coalesce(one->>'denominator','')~'^(0|[1-9][0-9]{0,9})$') THEN RAISE EXCEPTION 'CTT batch automation metrics incomplete' USING ERRCODE='55000'; END IF;
 metric_numerator:=(one->>'numerator')::bigint;metric_denominator:=(one->>'denominator')::bigint;
 IF metric_denominator<=0 OR metric_numerator>metric_denominator OR metric_numerator*100<metric_denominator*80 THEN RAISE EXCEPTION 'CTT batch automation qualification insufficient' USING ERRCODE='55000'; END IF;
 END LOOP;
 END IF;
 IF jsonb_typeof(p_members)<>'array' OR jsonb_array_length(p_members) NOT BETWEEN 1 AND lim OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(p_members))<>jsonb_array_length(p_members) THEN RAISE EXCEPTION 'CTT batch member set denied' USING ERRCODE='42501'; END IF;
 root:=ctt_staff_private.batch_json(p_root);
 IF root->>'run_id' IS DISTINCT FROM p_run::text OR root->>'block_id' IS DISTINCT FROM p_block::text OR root->>'principal_id' IS DISTINCT FROM r.principal_id::text OR root->>'qualification_hash' IS DISTINCT FROM q.candidate_hash OR root->>'corpus_hash' IS DISTINCT FROM r.corpus_hash OR root->>'profile_hash' IS DISTINCT FROM r.profile_hash OR (root->>'max_execution_seconds')::int IS DISTINCT FROM 3600 OR jsonb_array_length(root->'members')<>jsonb_array_length(p_members) THEN RAISE EXCEPTION 'CTT batch root binding denied' USING ERRCODE='42501'; END IF;
 FOR one IN SELECT value FROM jsonb_array_elements(p_members) LOOP
 SELECT * INTO member FROM ctt_staff_private.review_batch_members WHERE member_id=(one#>>'{}')::uuid AND run_id=p_run FOR UPDATE;
 IF member.member_id IS NULL OR member.status<>'ready' OR EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_capture_identities WHERE capture_identity=member.capture_identity AND committed_team_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_block_members bm JOIN ctt_staff_private.review_batch_blocks b USING(block_id) WHERE bm.member_id=member.member_id AND b.status IN('prepared','challenged','active','paused')) THEN RAISE EXCEPTION 'CTT batch member unavailable' USING ERRCODE='55000'; END IF;
 IF manifest IS NULL THEN manifest:=member.manifest_sha256;policy:=member.policy_hash; END IF;
 IF manifest<>member.manifest_sha256 OR policy<>member.policy_hash THEN RAISE EXCEPTION 'CTT batch mixed manifest denied' USING ERRCODE='42501'; END IF;
 expected:=root->'members'->idx;
 IF expected->>'member_id' IS DISTINCT FROM member.member_id::text OR expected->>'document_id' IS DISTINCT FROM member.document_id OR expected->>'payload_hash' IS DISTINCT FROM member.payload_hash OR expected->>'scope_hash' IS DISTINCT FROM member.scope_hash OR expected->>'verification_hash' IS DISTINCT FROM (SELECT v.verification_hash FROM ctt_staff_private.review_batch_verifications v WHERE v.member_id=member.member_id AND v.qualification_id=q.qualification_id ORDER BY recorded_at DESC LIMIT 1) OR expected->>'human_edits_hash' IS DISTINCT FROM coalesce(member.frozen_snapshot->>'human_edits_hash',encode(public.digest('[]','sha256'),'hex')) THEN RAISE EXCEPTION 'CTT batch root member changed' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_verifications WHERE qualification_id=q.qualification_id AND member_id=member.member_id) THEN RAISE EXCEPTION 'CTT batch verifier proof required' USING ERRCODE='42501'; END IF;
 idx:=idx+1;
 END LOOP;
 IF root->>'manifest_sha256' IS DISTINCT FROM manifest OR root->>'policy_hash' IS DISTINCT FROM policy THEN RAISE EXCEPTION 'CTT batch policy binding denied' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.review_batch_blocks(block_id,run_id,qualification_id,manifest_sha256,policy_hash,root_bytes,root_hash) VALUES(p_block,p_run,q.qualification_id,manifest,policy,p_root,encode(public.digest(p_root,'sha256'),'hex'));
 INSERT INTO ctt_staff_private.review_batch_block_members(block_id,member_id,ordinal) SELECT p_block,(value#>>'{}')::uuid,ordinality FROM jsonb_array_elements(p_members) WITH ORDINALITY;
 UPDATE ctt_staff_private.review_batch_runs SET version=version+1 WHERE run_id=p_run;
 INSERT INTO ctt_staff_private.review_batch_events(run_id,block_id,action) VALUES(p_run,p_block,'BLOCK_PREPARED');
 RETURN jsonb_build_object('run_id',p_run,'block_id',p_block,'version',1,'status','prepared','member_count',idx,'root_hash',encode(public.digest(p_root,'sha256'),'hex'));
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_create_challenge(p_account uuid,p_block uuid,p_version bigint,p_challenge uuid,p_bytes bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;ctx jsonb;issuer ctt_staff_private.review_batch_services%ROWTYPE;ch ctt_staff_private.review_batch_challenges%ROWTYPE;stamp timestamptz:=clock_timestamp();
BEGIN
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=p_block;
 r:=ctt_staff_private.batch_require_run(p_account,b.run_id);SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=b.run_id FOR UPDATE;
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=p_block FOR UPDATE;
 IF b.version<>p_version OR b.status<>'prepared' OR octet_length(p_bytes)<>32 OR EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_blocks WHERE run_id=b.run_id AND status IN('challenged','active','paused')) THEN RAISE EXCEPTION 'CTT batch challenge blocked' USING ERRCODE='40001'; END IF;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_members m JOIN ctt_staff_private.review_batch_block_members bm USING(member_id) JOIN ctt_staff_private.review_batch_capture_identities ci USING(capture_identity) WHERE bm.block_id=p_block AND (m.status<>'ready' OR ci.committed_team_id IS NOT NULL)) THEN RAISE EXCEPTION 'CTT batch member stale' USING ERRCODE='40001'; END IF;
 ctx:=ctt_staff_private.batch_account_context(p_account,r.edition_id,b.manifest_sha256);
 SELECT * INTO issuer FROM ctt_staff_private.review_batch_services WHERE kind='issuer' AND backend_pin=r.backend_pin AND revoked_at IS NULL AND stamp>=valid_from AND stamp<valid_until;
 IF issuer.service_role IS NULL OR (SELECT count(*) FROM ctt_staff_private.review_batch_services WHERE kind='issuer' AND backend_pin=r.backend_pin AND revoked_at IS NULL AND stamp>=valid_from AND stamp<valid_until)<>1 THEN RAISE EXCEPTION 'CTT batch issuer unavailable' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.review_batch_challenges(challenge_id,consent_id,block_id,credential_id,challenge_bytes,capability_version,credential_version,principal_version,account_version,issuer_role,issuer_version,issued_at,expires_at)
 VALUES(p_challenge,gen_random_uuid(),p_block,decode(ctx->>'credential_id','base64'),p_bytes,(ctx->>'authority_version')::bigint,(ctx->>'credential_version')::bigint,(ctx->>'principal_version')::bigint,(ctx->>'account_version')::bigint,issuer.service_role,issuer.authority_version,stamp,least(stamp+interval '5 minutes',(ctx->>'deadline')::timestamptz,issuer.valid_until));
 UPDATE ctt_staff_private.review_batch_blocks SET status='challenged',version=version+1 WHERE block_id=p_block;
 RETURN jsonb_build_object('block_id',p_block,'run_id',r.run_id,'attempt_id',p_challenge,'challenge_id',p_challenge,'version',b.version+1,'status','challenged','credential_id',ctx->'credential_id','challenge',replace(encode(p_bytes,'base64'),E'\n',''),'root_hash',b.root_hash);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_load_consent_context(p_challenge uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;ch ctt_staff_private.review_batch_challenges%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;cr ctt_staff_private.review_credentials%ROWTYPE;q ctt_staff_private.review_batch_qualifications%ROWTYPE;ctx jsonb;prior ctt_staff_private.review_batch_consents%ROWTYPE;deadline timestamptz;
BEGIN
 s:=ctt_staff_private.batch_require_service('issuer');SELECT * INTO ch FROM ctt_staff_private.review_batch_challenges WHERE challenge_id=p_challenge FOR UPDATE;
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=ch.block_id FOR UPDATE;
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=b.run_id;
 IF ch.challenge_id IS NULL OR r.backend_pin IS DISTINCT FROM s.backend_pin OR ch.issuer_role IS DISTINCT FROM session_user OR ch.issuer_version<>s.authority_version THEN RAISE EXCEPTION 'CTT batch issuer scope denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM ctt_staff_private.review_batch_consents WHERE challenge_id=p_challenge;
 IF prior.consent_id IS NOT NULL THEN PERFORM ctt_staff_private.batch_recheck_parent(prior.consent_id);RETURN jsonb_build_object('consent_id',prior.consent_id,'block_id',b.block_id,'proof_bytes',replace(encode(prior.proof_bytes,'base64'),E'\n',''),'root_hash',b.root_hash,'retained',true); END IF;
 ctx:=ctt_staff_private.batch_account_context(r.account_id,r.edition_id,b.manifest_sha256);
 IF b.status<>'challenged' OR clock_timestamp()>=ch.expires_at OR ch.capability_version IS DISTINCT FROM (ctx->>'authority_version')::bigint OR ch.principal_version IS DISTINCT FROM (ctx->>'principal_version')::bigint OR ch.account_version IS DISTINCT FROM (ctx->>'account_version')::bigint OR ch.credential_version IS DISTINCT FROM (ctx->>'credential_version')::bigint THEN RAISE EXCEPTION 'CTT batch challenge expired' USING ERRCODE='42501'; END IF;
 SELECT * INTO cr FROM ctt_staff_private.review_credentials WHERE credential_id=ch.credential_id FOR UPDATE;
 SELECT * INTO q FROM ctt_staff_private.review_batch_qualifications WHERE qualification_id=b.qualification_id;
 deadline:=least(clock_timestamp()+interval '1 hour',(ctx->>'deadline')::timestamptz,s.valid_until);
 RETURN jsonb_build_object('consent_id',ch.consent_id,'challenge_id',ch.challenge_id,'block_id',b.block_id,'run_id',r.run_id,'backend_pin',r.backend_pin,'principal_id',r.principal_id,'tenant_id',r.tenant_id,'edition_id',r.edition_id,
 'credential_id',replace(encode(cr.credential_id,'base64'),E'\n',''),'attested_credential',replace(encode(cr.attested_credential,'base64'),E'\n',''),'user_handle',replace(encode(cr.user_handle,'base64'),E'\n',''),'sign_count',cr.sign_count,'auth_profile',cr.auth_profile,'backup_eligible',cr.backup_eligible,'backup_state',cr.backup_state,'auth_policy_hash',cr.auth_policy_hash,
 'challenge_bytes',replace(encode(ch.challenge_bytes,'base64'),E'\n',''),'issued_at_seconds',floor(extract(epoch FROM ch.issued_at))::bigint,'expires_at_seconds',floor(extract(epoch FROM ch.expires_at))::bigint,
 'root_bytes',replace(encode(b.root_bytes,'base64'),E'\n',''),'root_hash',b.root_hash,'qualification_hash',q.candidate_hash,'capability_version',ch.capability_version,'credential_version',ch.credential_version,'principal_version',ch.principal_version,'account_version',ch.account_version,'service_version',s.authority_version,'authority_deadline',floor(extract(epoch FROM deadline))::bigint,'issuer_kid',s.signing_kid);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_register_consent(p_challenge uuid,p_proof bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE ctx jsonb;env jsonb;body jsonb;ch ctt_staff_private.review_batch_challenges%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;cr ctt_staff_private.review_credentials%ROWTYPE;issued timestamptz;expires timestamptz;proofhash text;member record;
BEGIN
 ctx:=ctt_staff_private.batch_load_consent_context(p_challenge);proofhash:=encode(public.digest(p_proof,'sha256'),'hex');
 IF ctx->'retained'='true'::jsonb THEN
 IF encode(public.digest(decode(ctx->>'proof_bytes','base64'),'sha256'),'hex')<>proofhash THEN RAISE EXCEPTION 'CTT batch retained proof changed' USING ERRCODE='40001'; END IF;
 RETURN ctx-'proof_bytes'||jsonb_build_object('status','active');END IF;
 env:=ctt_staff_private.batch_json(p_proof);PERFORM ctt_staff_private.batch_require_keys(env,ARRAY['body','signature']);body:=env->'body';
 PERFORM ctt_staff_private.batch_require_keys(body,ARRAY['schema','kid','consent_id','challenge_id','principal_id','credential_id','root_hash','qualification_hash','capability_version','credential_version','principal_version','account_version','service_version','counter_before','counter_after','auth_profile','backup_eligible','backup_state','auth_policy_hash','issued_at','expires_at']);
 SELECT * INTO ch FROM ctt_staff_private.review_batch_challenges WHERE challenge_id=p_challenge;
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=ch.block_id;
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=b.run_id;
 SELECT * INTO cr FROM ctt_staff_private.review_credentials WHERE credential_id=ch.credential_id;
 issued:=to_timestamp((body->>'issued_at')::bigint);expires:=to_timestamp((body->>'expires_at')::bigint);
 IF body->>'schema' IS DISTINCT FROM 'ctt.batch-consent.v1' OR body->>'kid' IS DISTINCT FROM ctx->>'issuer_kid'
 OR body->>'consent_id' IS DISTINCT FROM ch.consent_id::text OR body->>'challenge_id' IS DISTINCT FROM p_challenge::text OR body->>'principal_id' IS DISTINCT FROM r.principal_id::text OR body->>'credential_id' IS DISTINCT FROM ctx->>'credential_id'
 OR body->>'root_hash' IS DISTINCT FROM b.root_hash OR body->>'qualification_hash' IS DISTINCT FROM ctx->>'qualification_hash'
 OR (body->>'capability_version')::bigint IS DISTINCT FROM ch.capability_version OR (body->>'credential_version')::bigint IS DISTINCT FROM ch.credential_version OR (body->>'principal_version')::bigint IS DISTINCT FROM ch.principal_version OR (body->>'account_version')::bigint IS DISTINCT FROM ch.account_version OR (body->>'service_version')::bigint IS DISTINCT FROM ch.issuer_version
 OR (body->>'counter_before')::bigint IS DISTINCT FROM cr.sign_count OR (body->>'counter_after')::bigint NOT BETWEEN 0 AND 4294967295
 OR body->>'auth_profile' IS DISTINCT FROM cr.auth_profile OR (body->>'backup_eligible')::boolean IS DISTINCT FROM cr.backup_eligible OR jsonb_typeof(body->'backup_state') IS DISTINCT FROM 'boolean' OR ((body->>'backup_state')::boolean AND NOT cr.backup_eligible) OR (cr.auth_profile<>'platform-passkey-v1' AND (body->>'backup_state')::boolean IS DISTINCT FROM cr.backup_state) OR body->>'auth_policy_hash' IS DISTINCT FROM cr.auth_policy_hash
 OR NOT ((cr.auth_profile='platform-passkey-v1' AND cr.sign_count=0 AND (body->>'counter_after')::bigint=0) OR (body->>'counter_after')::bigint>cr.sign_count)
 OR issued>clock_timestamp() OR issued<clock_timestamp()-interval '10 seconds' OR expires<=clock_timestamp() OR expires>issued+interval '1 hour' OR extract(epoch FROM expires)>(ctx->>'authority_deadline')::bigint
 OR octet_length(decode(env->>'signature','base64'))<>64 THEN RAISE EXCEPTION 'CTT batch consent proof denied' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.review_batch_consents(consent_id,challenge_id,block_id,proof_bytes,proof_hash,issuer_role,issued_at,expires_at) VALUES(ch.consent_id,p_challenge,b.block_id,p_proof,proofhash,session_user,issued,expires);
 UPDATE ctt_staff_private.review_credentials SET sign_count=(body->>'counter_after')::bigint,backup_state=(body->>'backup_state')::boolean WHERE credential_id=ch.credential_id;
 INSERT INTO ctt_staff_private.review_batch_qualification_activations(qualification_id,consent_id,principal_id) VALUES(b.qualification_id,ch.consent_id,r.principal_id);
 UPDATE ctt_staff_private.review_batch_blocks SET status='active',consent_id=ch.consent_id,dispatch_generation=dispatch_generation+1,version=version+1 WHERE block_id=b.block_id;
 FOR member IN SELECT bm.member_id FROM ctt_staff_private.review_batch_block_members bm WHERE block_id=b.block_id LOOP
 INSERT INTO ctt_staff_private.review_batch_jobs(member_id,block_id) VALUES(member.member_id,b.block_id)
 ON CONFLICT(member_id) DO UPDATE SET block_id=excluded.block_id,status='queued',worker_id=NULL,lease_until=NULL WHERE review_batch_jobs.status IN('queued','failed');
 UPDATE ctt_staff_private.review_batch_members SET status='pending' WHERE member_id=member.member_id AND status='ready';END LOOP;
 INSERT INTO ctt_staff_private.review_batch_events(run_id,block_id,action,details) VALUES(r.run_id,b.block_id,'CONSENT_ACTIVATED',jsonb_build_object('proof_hash',proofhash));
 RETURN jsonb_build_object('run_id',r.run_id,'block_id',b.block_id,'consent_id',ch.consent_id,'version',b.version+1,'status','active','proof_hash',proofhash,'expires_at',expires);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_recover_consent(p_account uuid,p_block uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE b ctt_staff_private.review_batch_blocks%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;c ctt_staff_private.review_batch_consents%ROWTYPE;
BEGIN
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=p_block;r:=ctt_staff_private.batch_require_run(p_account,b.run_id);
 SELECT * INTO c FROM ctt_staff_private.review_batch_consents WHERE block_id=p_block;
 RETURN jsonb_build_object('run_id',r.run_id,'block_id',b.block_id,'version',b.version,'status',CASE WHEN c.consent_id IS NOT NULL AND clock_timestamp()>=c.expires_at AND b.status IN('active','paused') THEN 'expired' ELSE b.status END,'consent_id',c.consent_id,'expires_at',c.expires_at);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_transition(p_account uuid,p_block uuid,p_version bigint,p_action text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE b ctt_staff_private.review_batch_blocks%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;c ctt_staff_private.review_batch_consents%ROWTYPE;
BEGIN
 PERFORM 1 FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton FOR UPDATE;
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=p_block;r:=ctt_staff_private.batch_require_run(p_account,b.run_id);
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=p_block FOR UPDATE;
 SELECT * INTO c FROM ctt_staff_private.review_batch_consents WHERE block_id=p_block;
 IF b.version<>p_version OR p_action NOT IN('pause','resume','revoke') OR b.status IN('revoked','completed','expired') THEN RAISE EXCEPTION 'CTT batch transition stale' USING ERRCODE='40001'; END IF;
 IF p_action='resume' THEN PERFORM ctt_staff_private.batch_account_context(r.account_id,r.edition_id,b.manifest_sha256);END IF;
 IF p_action='resume' AND (b.status<>'paused' OR c.consent_id IS NULL OR clock_timestamp()>=c.expires_at) THEN RAISE EXCEPTION 'CTT batch resume denied' USING ERRCODE='42501'; END IF;
 IF p_action='pause' AND b.status<>'active' THEN RAISE EXCEPTION 'CTT batch pause denied' USING ERRCODE='42501'; END IF;
 UPDATE ctt_staff_private.review_batch_blocks SET status=CASE p_action WHEN 'pause' THEN 'paused' WHEN 'resume' THEN 'active' ELSE 'revoked' END,dispatch_generation=dispatch_generation+1,version=version+1 WHERE block_id=p_block RETURNING * INTO b;
 UPDATE ctt_staff_private.review_batch_jobs SET lease_epoch=lease_epoch+1,lease_until=NULL,status=CASE WHEN status='leased' THEN 'uncertain' ELSE status END WHERE block_id=p_block AND status<>'committed';
 UPDATE ctt_staff_private.review_batch_dispatch_slot SET lease_epoch=lease_epoch+1,lease_until=NULL WHERE member_id IN(SELECT member_id FROM ctt_staff_private.review_batch_block_members WHERE block_id=p_block);
 INSERT INTO ctt_staff_private.review_batch_events(run_id,block_id,action) VALUES(r.run_id,b.block_id,upper(p_action));
 RETURN jsonb_build_object('run_id',r.run_id,'block_id',b.block_id,'version',b.version,'status',b.status,'consent_id',b.consent_id);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_pause(uuid,uuid,bigint) RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT ctt_staff_private.batch_transition($1,$2,$3,''pause'')';
CREATE FUNCTION ctt_staff_private.batch_resume(uuid,uuid,bigint) RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT ctt_staff_private.batch_transition($1,$2,$3,''resume'')';
CREATE FUNCTION ctt_staff_private.batch_revoke(uuid,uuid,bigint) RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT ctt_staff_private.batch_transition($1,$2,$3,''revoke'')';

CREATE FUNCTION ctt_staff_private.batch_claim(p_worker uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;slot ctt_staff_private.review_batch_dispatch_slot%ROWTYPE;j ctt_staff_private.review_batch_jobs%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;c ctt_staff_private.review_batch_consents%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;
BEGIN
 s:=ctt_staff_private.batch_require_service('worker');PERFORM ctt_staff_private.batch_expire_blocks(NULL);SELECT * INTO slot FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton FOR UPDATE;
 IF slot.member_id IS NOT NULL THEN
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE member_id=slot.member_id;
 IF m.status NOT IN('committed','failed','already_captured') THEN RETURN jsonb_build_object('status',CASE WHEN slot.lease_until>clock_timestamp() THEN 'busy' ELSE 'recovery_required' END,'attempt_id',m.attempt_id,'member_id',m.member_id,'lease_epoch',slot.lease_epoch); END IF;
 END IF;
 SELECT jobs.* INTO j FROM ctt_staff_private.review_batch_jobs jobs JOIN ctt_staff_private.review_batch_blocks blocks USING(block_id) JOIN ctt_staff_private.review_batch_runs runs USING(run_id) JOIN ctt_staff_private.review_batch_consents cons ON cons.consent_id=blocks.consent_id
 WHERE jobs.status='queued' AND blocks.status='active' AND runs.backend_pin=s.backend_pin AND cons.expires_at>clock_timestamp() ORDER BY jobs.updated_at,jobs.member_id LIMIT 1 FOR UPDATE OF jobs;
 IF j.member_id IS NULL THEN RETURN jsonb_build_object('status','idle');END IF;
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=j.block_id FOR SHARE;SELECT * INTO c FROM ctt_staff_private.review_batch_consents WHERE consent_id=b.consent_id;
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE member_id=j.member_id;SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=m.run_id;
 PERFORM ctt_staff_private.batch_account_context(r.account_id,r.edition_id,b.manifest_sha256);
 UPDATE ctt_staff_private.review_batch_dispatch_slot SET member_id=j.member_id,worker_id=p_worker,lease_epoch=lease_epoch+1,lease_until=least(clock_timestamp()+interval '5 minutes',c.expires_at) WHERE singleton RETURNING * INTO slot;
 UPDATE ctt_staff_private.review_batch_jobs SET status='leased',worker_id=p_worker,lease_epoch=slot.lease_epoch,lease_until=slot.lease_until,updated_at=clock_timestamp() WHERE member_id=j.member_id;
 RETURN jsonb_build_object('status','leased','run_id',r.run_id,'account_id',r.account_id,'block_id',b.block_id,'consent_id',b.consent_id,'member_id',m.member_id,'attempt_id',m.attempt_id,'worker_id',p_worker,'lease_epoch',slot.lease_epoch,'snapshot',m.frozen_snapshot);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_recover_job(p_worker uuid,p_attempt uuid,p_epoch bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;slot ctt_staff_private.review_batch_dispatch_slot%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;ci ctt_staff_private.review_batch_capture_identities%ROWTYPE;result record;j ctt_staff_private.review_batch_jobs%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;c ctt_staff_private.review_batch_consents%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;
BEGIN
 s:=ctt_staff_private.batch_require_service('worker');SELECT * INTO slot FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton FOR UPDATE;
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE attempt_id=p_attempt;
 IF m.member_id IS NULL OR slot.member_id IS DISTINCT FROM m.member_id OR slot.lease_epoch<>p_epoch OR coalesce(slot.lease_until>clock_timestamp(),false) THEN RAISE EXCEPTION 'CTT batch recovery stale' USING ERRCODE='40001'; END IF;
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=(SELECT block_id FROM ctt_staff_private.review_batch_jobs WHERE member_id=m.member_id) FOR SHARE;
 SELECT * INTO ci FROM ctt_staff_private.review_batch_capture_identities WHERE capture_identity=m.capture_identity FOR UPDATE;
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=m.run_id;
 IF r.backend_pin<>s.backend_pin THEN RAISE EXCEPTION 'CTT batch recovery scope denied' USING ERRCODE='42501'; END IF;
 SELECT result_id,team_id INTO result FROM ctt_staff_private.capture_results WHERE attempt_id=p_attempt;
 IF result.result_id IS NOT NULL OR ci.committed_team_id IS NOT NULL THEN
 IF result.result_id IS NULL THEN RAISE EXCEPTION 'CTT batch committed recovery requires reconciliation' USING ERRCODE='55000';END IF;
 UPDATE ctt_staff_private.review_batch_members SET status='committed' WHERE member_id=m.member_id;
 UPDATE ctt_staff_private.review_batch_jobs SET status='committed',lease_until=NULL WHERE member_id=m.member_id;
 UPDATE ctt_staff_private.review_batch_dispatch_slot SET member_id=NULL,worker_id=NULL,lease_until=NULL,lease_epoch=lease_epoch+1 WHERE singleton;
 RETURN jsonb_build_object('status','committed','attempt_id',p_attempt,'result_id',result.result_id,'team_id',result.team_id);END IF;
 -- The identity lock serializes with every canonical writer. Only after it is
 -- acquired and no result exists may a new lease fence the old worker.
 SELECT * INTO c FROM ctt_staff_private.review_batch_consents WHERE consent_id=b.consent_id;
 IF b.status<>'active' OR c.expires_at<=clock_timestamp() THEN
 UPDATE ctt_staff_private.review_batch_jobs SET status='failed',lease_until=NULL WHERE member_id=m.member_id;
 UPDATE ctt_staff_private.review_batch_members SET status='ready' WHERE member_id=m.member_id AND status<>'committed';
 UPDATE ctt_staff_private.review_batch_dispatch_slot SET member_id=NULL,worker_id=NULL,lease_until=NULL,lease_epoch=lease_epoch+1 WHERE singleton;
 RETURN jsonb_build_object('status','recovered_uncommitted','attempt_id',p_attempt);END IF;
 PERFORM ctt_staff_private.batch_account_context(r.account_id,r.edition_id,b.manifest_sha256);
 UPDATE ctt_staff_private.review_batch_dispatch_slot SET worker_id=p_worker,lease_epoch=lease_epoch+1,lease_until=least(clock_timestamp()+interval '5 minutes',c.expires_at) WHERE singleton RETURNING * INTO slot;
 UPDATE ctt_staff_private.review_batch_jobs SET status='leased',worker_id=p_worker,lease_epoch=slot.lease_epoch,lease_until=slot.lease_until,updated_at=clock_timestamp() WHERE member_id=m.member_id;
 RETURN jsonb_build_object('status','leased','run_id',r.run_id,'account_id',r.account_id,'block_id',b.block_id,'consent_id',b.consent_id,'member_id',m.member_id,'attempt_id',m.attempt_id,'worker_id',p_worker,'lease_epoch',slot.lease_epoch,'snapshot',m.frozen_snapshot);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_recheck_parent(p_consent uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE c ctt_staff_private.review_batch_consents%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;q ctt_staff_private.review_batch_qualifications%ROWTYPE;issuer ctt_staff_private.review_batch_services%ROWTYPE;body jsonb;ctx jsonb;cr ctt_staff_private.review_credentials%ROWTYPE;
BEGIN
 SELECT * INTO c FROM ctt_staff_private.review_batch_consents WHERE consent_id=p_consent;
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=c.block_id FOR SHARE;
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=b.run_id;
 SELECT * INTO q FROM ctt_staff_private.review_batch_qualifications WHERE qualification_id=b.qualification_id;
 SELECT * INTO issuer FROM ctt_staff_private.review_batch_services WHERE service_role=c.issuer_role FOR SHARE;
 body:=ctt_staff_private.batch_json(c.proof_bytes)->'body';
 ctx:=ctt_staff_private.batch_account_context(r.account_id,r.edition_id,b.manifest_sha256);
 SELECT * INTO cr FROM ctt_staff_private.review_credentials WHERE credential_id=decode(body->>'credential_id','base64') FOR SHARE;
 IF c.consent_id IS NULL OR b.status NOT IN('active','completed') OR clock_timestamp()>=c.expires_at OR q.qualification_id IS DISTINCT FROM r.qualification_id
 OR issuer.kind IS DISTINCT FROM 'issuer' OR issuer.backend_pin IS DISTINCT FROM r.backend_pin OR issuer.revoked_at IS NOT NULL OR clock_timestamp()<issuer.valid_from OR clock_timestamp()>=issuer.valid_until
 OR body->>'kid' IS DISTINCT FROM issuer.signing_kid OR (body->>'service_version')::bigint IS DISTINCT FROM issuer.authority_version
 OR body->>'principal_id' IS DISTINCT FROM r.principal_id::text OR body->>'root_hash' IS DISTINCT FROM b.root_hash OR body->>'qualification_hash' IS DISTINCT FROM q.candidate_hash
 OR (body->>'capability_version')::bigint IS DISTINCT FROM (ctx->>'authority_version')::bigint OR (body->>'credential_version')::bigint IS DISTINCT FROM (ctx->>'credential_version')::bigint OR (body->>'principal_version')::bigint IS DISTINCT FROM (ctx->>'principal_version')::bigint OR (body->>'account_version')::bigint IS DISTINCT FROM (ctx->>'account_version')::bigint
 OR body->>'credential_id' IS DISTINCT FROM ctx->>'credential_id' OR body->>'auth_profile' IS DISTINCT FROM cr.auth_profile OR body->>'auth_policy_hash' IS DISTINCT FROM cr.auth_policy_hash OR (body->>'backup_eligible')::boolean IS DISTINCT FROM cr.backup_eligible OR (body->>'backup_state')::boolean IS DISTINCT FROM cr.backup_state
 OR body->>'schema' IS DISTINCT FROM 'ctt.batch-consent.v1' OR c.proof_hash IS DISTINCT FROM encode(public.digest(c.proof_bytes,'sha256'),'hex')
 OR NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_qualification_activations WHERE consent_id=p_consent AND qualification_id=q.qualification_id AND principal_id=r.principal_id)
 THEN RAISE EXCEPTION 'CTT batch parent authority stale' USING ERRCODE='42501';END IF;
 RETURN ctx||jsonb_build_object('root_hash',b.root_hash,'qualification_hash',q.candidate_hash,'parent_deadline',least(c.expires_at,(ctx->>'deadline')::timestamptz,issuer.valid_until));
END $fn$;

CREATE FUNCTION ctt_staff_private.batch_load_child_context(p_consent uuid,p_member uuid,p_worker uuid,p_epoch bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;slot ctt_staff_private.review_batch_dispatch_slot%ROWTYPE;c ctt_staff_private.review_batch_consents%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;ci ctt_staff_private.review_batch_capture_identities%ROWTYPE;q ctt_staff_private.review_batch_qualifications%ROWTYPE;v ctt_staff_private.review_batch_verifications%ROWTYPE;ctx jsonb;prior ctt_staff_private.review_batch_child_authorizations%ROWTYPE;deadline timestamptz;
BEGIN
 svc:=ctt_staff_private.batch_require_service('issuer');SELECT * INTO slot FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton FOR SHARE;
 SELECT * INTO c FROM ctt_staff_private.review_batch_consents WHERE consent_id=p_consent;
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=c.block_id FOR SHARE;
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE member_id=p_member;
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=m.run_id;
 SELECT * INTO ci FROM ctt_staff_private.review_batch_capture_identities WHERE capture_identity=m.capture_identity FOR UPDATE;
 IF m.member_id IS NULL OR r.backend_pin<>svc.backend_pin OR b.run_id<>r.run_id OR b.status<>'active' OR c.expires_at<=clock_timestamp() OR slot.member_id IS DISTINCT FROM p_member OR slot.worker_id IS DISTINCT FROM p_worker OR slot.lease_epoch<>p_epoch OR slot.lease_until<=clock_timestamp() OR ci.committed_team_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_block_members WHERE block_id=b.block_id AND member_id=p_member) OR NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_qualification_activations WHERE consent_id=p_consent AND qualification_id=b.qualification_id AND principal_id=r.principal_id) THEN RAISE EXCEPTION 'CTT batch child context denied' USING ERRCODE='42501'; END IF;
 ctx:=ctt_staff_private.batch_recheck_parent(p_consent);
 SELECT * INTO q FROM ctt_staff_private.review_batch_qualifications WHERE qualification_id=b.qualification_id;
 SELECT * INTO v FROM ctt_staff_private.review_batch_verifications WHERE member_id=p_member AND qualification_id=q.qualification_id ORDER BY recorded_at DESC LIMIT 1;
 IF v.verification_hash IS NULL OR NOT ctt_staff_private.batch_assert_source_document(r.edition_id,m.manifest_sha256,m.document_id,m.source_payload_hash) THEN RAISE EXCEPTION 'CTT batch child source denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM ctt_staff_private.review_batch_child_authorizations WHERE member_id=p_member AND authorization_generation=m.authorization_generation;

 deadline:=least(c.expires_at,slot.lease_until,(ctx->>'deadline')::timestamptz,svc.valid_until,to_timestamp((ctt_staff_private.batch_json(v.proof_bytes)->'body'->>'expires_at')::bigint));
 IF deadline<=clock_timestamp() THEN RAISE EXCEPTION 'CTT batch verification expired' USING ERRCODE='42501';END IF;
 RETURN jsonb_build_object('consent_id',p_consent,'member_id',p_member,'attempt_id',m.attempt_id,'operation_id',ci.operation_id,'principal_id',r.principal_id,'scope_hash',m.scope_hash,'root_hash',b.root_hash,'qualification_hash',q.candidate_hash,'verification_hash',v.verification_hash,'authorization_generation',CASE WHEN prior.authorization_id IS NOT NULL AND prior.worker_id=p_worker AND prior.lease_epoch=p_epoch AND prior.dispatch_generation=b.dispatch_generation AND prior.expires_at>clock_timestamp() THEN prior.authorization_generation ELSE m.authorization_generation+1 END,'worker_id',p_worker,'lease_epoch',p_epoch,'dispatch_generation',b.dispatch_generation,'authority_deadline',floor(extract(epoch FROM deadline))::bigint,'authority_deadline_seconds',floor(extract(epoch FROM deadline))::bigint,'qualification_active',true,'retained',coalesce(prior.authorization_id IS NOT NULL AND prior.worker_id=p_worker AND prior.lease_epoch=p_epoch AND prior.dispatch_generation=b.dispatch_generation AND prior.expires_at>clock_timestamp(),false),'proof_bytes',CASE WHEN prior.authorization_id IS NOT NULL AND prior.worker_id=p_worker AND prior.lease_epoch=p_epoch AND prior.dispatch_generation=b.dispatch_generation AND prior.expires_at>clock_timestamp() THEN replace(encode(prior.proof_bytes,'base64'),E'\n','') ELSE NULL END,'parent_proof_bytes',replace(encode(c.proof_bytes,'base64'),E'\n',''),'verification_proof_bytes',replace(encode(v.proof_bytes,'base64'),E'\n',''),'scope_bytes',m.frozen_snapshot->'scope_bytes','snapshot',m.frozen_snapshot,'issuer_kid',svc.signing_kid,'root_bytes',replace(encode(b.root_bytes,'base64'),E'\n',''));
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_issue_child(p_consent uuid,p_member uuid,p_worker uuid,p_epoch bigint,p_proof bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE ctx jsonb;body jsonb;env jsonb;key text;issued timestamptz;expires timestamptz;auth uuid;hash text;
BEGIN
 ctx:=ctt_staff_private.batch_load_child_context(p_consent,p_member,p_worker,p_epoch);hash:=encode(public.digest(p_proof,'sha256'),'hex');
 IF ctx->'retained'='true'::jsonb THEN IF encode(public.digest(decode(ctx->>'proof_bytes','base64'),'sha256'),'hex')<>hash THEN RAISE EXCEPTION 'CTT batch retained child changed' USING ERRCODE='40001'; END IF;RETURN jsonb_build_object('proof_hash',hash,'retained',true);END IF;
 env:=ctt_staff_private.batch_json(p_proof);PERFORM ctt_staff_private.batch_require_keys(env,ARRAY['body','signature']);body:=env->'body';
 PERFORM ctt_staff_private.batch_require_keys(body,ARRAY['schema','kid','authorization_id','consent_id','member_id','attempt_id','operation_id','principal_id','scope_hash','root_hash','qualification_hash','verification_hash','authorization_generation','worker_id','lease_epoch','dispatch_generation','nonce','issued_at','expires_at']);
 FOREACH key IN ARRAY ARRAY['consent_id','member_id','attempt_id','operation_id','principal_id','scope_hash','root_hash','qualification_hash','verification_hash','authorization_generation','worker_id','lease_epoch','dispatch_generation'] LOOP
 IF body->key IS DISTINCT FROM ctx->key THEN RAISE EXCEPTION 'CTT batch child binding denied' USING ERRCODE='42501';END IF;END LOOP;
 issued:=to_timestamp((body->>'issued_at')::bigint);expires:=to_timestamp((body->>'expires_at')::bigint);auth:=(body->>'authorization_id')::uuid;
 IF body->>'schema' IS DISTINCT FROM 'ctt.batch.member-authorization.v1' OR body->>'kid' IS DISTINCT FROM ctx->>'issuer_kid' OR issued>clock_timestamp() OR issued<clock_timestamp()-interval '10 seconds' OR expires<=clock_timestamp() OR expires>issued+interval '5 minutes' OR extract(epoch FROM expires)>(ctx->>'authority_deadline')::bigint OR octet_length(decode(env->>'signature','base64'))<>64 THEN RAISE EXCEPTION 'CTT batch child proof denied' USING ERRCODE='42501';END IF;
 INSERT INTO ctt_staff_private.review_batch_child_authorizations(authorization_id,consent_id,member_id,attempt_id,operation_id,worker_id,lease_epoch,dispatch_generation,authorization_generation,nonce,proof_bytes,proof_hash,issued_at,expires_at)
 VALUES(auth,p_consent,p_member,(body->>'attempt_id')::uuid,(body->>'operation_id')::uuid,p_worker,p_epoch,(body->>'dispatch_generation')::bigint,(body->>'authorization_generation')::bigint,(body->>'nonce')::uuid,p_proof,hash,issued,expires);
 UPDATE ctt_staff_private.review_batch_members SET authorization_generation=(body->>'authorization_generation')::bigint WHERE member_id=p_member;
 UPDATE ctt_staff_private.review_batch_jobs SET authorization_id=auth WHERE member_id=p_member;
 RETURN jsonb_build_object('authorization_id',auth,'attempt_id',body->'attempt_id','proof_hash',hash,'expires_at',expires);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_lock_child(p_attempt uuid,p_authorization uuid,p_worker uuid,p_epoch bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;slot ctt_staff_private.review_batch_dispatch_slot%ROWTYPE;a ctt_staff_private.review_batch_child_authorizations%ROWTYPE;c ctt_staff_private.review_batch_consents%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;ci ctt_staff_private.review_batch_capture_identities%ROWTYPE;ctx jsonb;staged record;own_context boolean:=false;own_consumption boolean:=false;own_result boolean:=false;legacy jsonb;vr ctt_staff_private.review_batch_verifications%ROWTYPE;vs ctt_staff_private.review_batch_services%ROWTYPE;
BEGIN
 svc:=ctt_staff_private.batch_require_service('writer');SELECT * INTO slot FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton FOR SHARE;
 SELECT * INTO a FROM ctt_staff_private.review_batch_child_authorizations WHERE authorization_id=p_authorization;
 SELECT * INTO c FROM ctt_staff_private.review_batch_consents WHERE consent_id=a.consent_id;
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=c.block_id FOR SHARE;
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE member_id=a.member_id;
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=m.run_id;
 PERFORM ctt_staff_private.batch_lock_capture_source_pages(r.edition_id,(SELECT jsonb_agg(jsonb_build_object('pdf_sha256',pp->>'pdf_sha256','pdf_page',(pp->>'pdf_page')::integer)) FROM jsonb_array_elements(m.source_pages) pp),(SELECT operation_id FROM ctt_staff_private.review_batch_capture_identities WHERE capture_identity=m.capture_identity));
 PERFORM pg_advisory_xact_lock(1129600051,hashtext(r.tenant_id||':'||r.edition_id::text||':'||m.document_id));
 SELECT * INTO ci FROM ctt_staff_private.review_batch_capture_identities WHERE capture_identity=m.capture_identity FOR UPDATE;
 SELECT EXISTS(SELECT 1 FROM ctt_staff_private.batch_writer_contexts w WHERE w.attempt_id=p_attempt AND w.authorization_id=p_authorization AND w.pg_top_xid=pg_current_xact_id() AND w.backend_pid=pg_backend_pid() AND w.writer_role=session_user AND w.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint) INTO own_context;
 SELECT own_context AND EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_consumptions z WHERE z.attempt_id=p_attempt AND z.authorization_id=p_authorization AND z.member_id=m.member_id AND z.capture_identity=ci.capture_identity AND z.result_id=ci.committed_result_id AND z.team_id=ci.committed_team_id AND z.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint) INTO own_consumption;
 SELECT own_context AND EXISTS(SELECT 1 FROM ctt_staff_private.capture_results z WHERE z.attempt_id=p_attempt AND z.team_id=(ctt_staff_private.batch_json(decode(m.frozen_snapshot->>'scope_bytes','base64'))->>'team_id')::uuid AND z.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint) INTO own_result;
 IF a.authorization_id IS NULL OR r.backend_pin IS DISTINCT FROM svc.backend_pin OR a.attempt_id IS DISTINCT FROM p_attempt OR a.worker_id IS DISTINCT FROM p_worker OR a.lease_epoch IS DISTINCT FROM p_epoch OR b.dispatch_generation IS DISTINCT FROM a.dispatch_generation OR m.authorization_generation IS DISTINCT FROM a.authorization_generation OR c.expires_at<=clock_timestamp() OR a.expires_at<=clock_timestamp()
 OR (NOT own_consumption AND (slot.member_id IS DISTINCT FROM a.member_id OR slot.worker_id IS DISTINCT FROM p_worker OR slot.lease_epoch IS DISTINCT FROM p_epoch OR slot.lease_until<=clock_timestamp() OR b.status IS DISTINCT FROM 'active' OR ci.committed_team_id IS NOT NULL OR EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_consumptions WHERE capture_identity=ci.capture_identity)))
 OR (own_consumption AND (NOT own_result OR b.status NOT IN('active','completed')))
 THEN RAISE EXCEPTION 'CTT batch final writer denied' USING ERRCODE='42501';END IF;
 ctx:=ctt_staff_private.batch_recheck_parent(c.consent_id);
 legacy:=ctt_staff_private.batch_resolve_legacy_source(r.edition_id,m.document_id);
 IF legacy->>'status'='ambiguous' OR (legacy->>'status'='committed' AND NOT own_result) OR NOT ctt_staff_private.batch_assert_source_document(r.edition_id,m.manifest_sha256,m.document_id,m.source_payload_hash) THEN RAISE EXCEPTION 'CTT batch writer source stale' USING ERRCODE='42501';END IF;
 SELECT * INTO vr FROM ctt_staff_private.review_batch_verifications WHERE verification_hash=ctt_staff_private.batch_json(a.proof_bytes)->'body'->>'verification_hash';
 SELECT * INTO vs FROM ctt_staff_private.review_batch_services WHERE service_role=vr.service_role FOR SHARE;
 IF vr.member_id IS DISTINCT FROM m.member_id OR vr.qualification_id IS DISTINCT FROM b.qualification_id OR vs.kind IS DISTINCT FROM 'verifier' OR vs.profile_hash IS DISTINCT FROM r.profile_hash OR vs.revoked_at IS NOT NULL OR vs.valid_from>clock_timestamp() OR vs.valid_until<=clock_timestamp() OR to_timestamp((ctt_staff_private.batch_json(vr.proof_bytes)->'body'->>'expires_at')::bigint)<=clock_timestamp() THEN RAISE EXCEPTION 'CTT batch verifier stale' USING ERRCODE='42501';END IF;
 SELECT * INTO staged FROM ctt_staff_private.batch_capture_payloads WHERE attempt_id=p_attempt AND authorization_id=p_authorization;
 RETURN jsonb_build_object('attempt_id',p_attempt,'authorization_id',p_authorization,'consent_id',c.consent_id,'block_id',b.block_id,'member_id',m.member_id,'operation_id',ci.operation_id,'principal_id',r.principal_id,'scope_hash',m.scope_hash,'root_hash',b.root_hash,'payload_hash',m.payload_hash,'nonce',a.nonce,'expires_at_seconds',floor(extract(epoch FROM a.expires_at))::bigint,'proof_bytes',replace(encode(a.proof_bytes,'base64'),E'\n',''),'snapshot',m.frozen_snapshot,'scope_bytes',m.frozen_snapshot->'scope_bytes','review_bytes',replace(encode(staged.review_bytes,'base64'),E'\n',''),'effect_bytes',replace(encode(staged.effect_bytes,'base64'),E'\n',''),'evidence_bytes',replace(encode(staged.evidence_bytes,'base64'),E'\n',''),'preauth_bytes',replace(encode(staged.preauthorization_bytes,'base64'),E'\n',''),'gate_envelope',replace(encode(staged.gate_envelope,'base64'),E'\n',''));
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_record_result(p_attempt uuid,p_authorization uuid,p_result uuid,p_team uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE a ctt_staff_private.review_batch_child_authorizations%ROWTYPE;ctx jsonb;m ctt_staff_private.review_batch_members%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;result record;
BEGIN
 SELECT * INTO a FROM ctt_staff_private.review_batch_child_authorizations WHERE authorization_id=p_authorization;
 ctx:=ctt_staff_private.batch_lock_child(p_attempt,p_authorization,a.worker_id,a.lease_epoch);
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE member_id=a.member_id;
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=(ctx->>'block_id')::uuid;
 SELECT result_id,team_id INTO result FROM ctt_staff_private.capture_results WHERE attempt_id=p_attempt;
 IF result.result_id IS DISTINCT FROM p_result OR result.team_id IS DISTINCT FROM p_team THEN RAISE EXCEPTION 'CTT batch canonical result required' USING ERRCODE='42501';END IF;
 INSERT INTO ctt_staff_private.review_batch_consumptions(capture_identity,authorization_id,member_id,attempt_id,result_id,team_id) VALUES(m.capture_identity,p_authorization,m.member_id,p_attempt,p_result,p_team);
 UPDATE ctt_staff_private.review_batch_capture_identities SET committed_result_id=p_result,committed_team_id=p_team WHERE capture_identity=m.capture_identity;
 UPDATE ctt_staff_private.review_batch_members SET status='committed' WHERE capture_identity=m.capture_identity;
 UPDATE ctt_staff_private.review_batch_jobs SET status='committed',lease_until=NULL WHERE member_id=m.member_id;
 UPDATE ctt_staff_private.review_batch_dispatch_slot SET member_id=NULL,worker_id=NULL,lease_until=NULL,lease_epoch=lease_epoch+1 WHERE singleton;
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_block_members bm JOIN ctt_staff_private.review_batch_members mm USING(member_id) WHERE bm.block_id=b.block_id AND mm.status<>'committed') THEN UPDATE ctt_staff_private.review_batch_blocks SET status='completed',version=version+1 WHERE block_id=b.block_id;END IF;
 INSERT INTO ctt_staff_private.review_batch_events(run_id,block_id,member_id,action,details) VALUES(m.run_id,b.block_id,m.member_id,'COMMITTED',jsonb_build_object('result_id',p_result,'team_id',p_team));
 RETURN jsonb_build_object('status','committed','result_id',p_result,'team_id',p_team,'attempt_id',p_attempt);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_attempt_status(p_account uuid,p_block uuid,p_attempt uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE b ctt_staff_private.review_batch_blocks%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;c ctt_staff_private.review_batch_consents%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;result record;
BEGIN
 SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=p_block;r:=ctt_staff_private.batch_require_run(p_account,b.run_id);SELECT * INTO b FROM ctt_staff_private.review_batch_blocks WHERE block_id=p_block FOR SHARE;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_challenges WHERE challenge_id=p_attempt AND block_id=p_block) THEN RETURN ctt_staff_private.batch_recover_consent(p_account,p_block)||jsonb_build_object('attempt_id',p_attempt,'kind','consent');END IF;
 SELECT mm.* INTO m FROM ctt_staff_private.review_batch_block_members bm JOIN ctt_staff_private.review_batch_members mm USING(member_id) WHERE bm.block_id=p_block AND mm.attempt_id=p_attempt;
 IF m.member_id IS NULL THEN RAISE EXCEPTION 'CTT batch attempt denied' USING ERRCODE='42501';END IF;
 PERFORM 1 FROM ctt_staff_private.review_batch_capture_identities WHERE capture_identity=m.capture_identity FOR SHARE;
 SELECT result_id,team_id INTO result FROM ctt_staff_private.capture_results WHERE attempt_id=p_attempt;
 RETURN jsonb_build_object('attempt_id',p_attempt,'kind','member','status',CASE WHEN result.result_id IS NOT NULL THEN 'committed' ELSE m.status END,'result_id',result.result_id,'team_id',result.team_id,'block_id',b.block_id);
END $fn$;

CREATE FUNCTION ctt_staff_private.batch_capture_context(p_attempt uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;c ctt_staff_private.review_batch_consents%ROWTYPE;v ctt_staff_private.review_batch_verifications%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;
BEGIN
 s:=ctt_staff_private.batch_require_service('workflow');SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE attempt_id=p_attempt;SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=m.run_id;
 SELECT bb.* INTO b FROM ctt_staff_private.review_batch_block_members bm JOIN ctt_staff_private.review_batch_blocks bb USING(block_id) WHERE bm.member_id=m.member_id AND bb.status='active';
 IF r.backend_pin IS DISTINCT FROM s.backend_pin OR b.block_id IS NULL THEN RAISE EXCEPTION 'CTT batch context denied' USING ERRCODE='42501';END IF;
 PERFORM ctt_staff_private.batch_recheck_parent(b.consent_id);SELECT * INTO c FROM ctt_staff_private.review_batch_consents WHERE consent_id=b.consent_id;
 SELECT * INTO v FROM ctt_staff_private.review_batch_verifications WHERE member_id=m.member_id AND qualification_id=b.qualification_id ORDER BY recorded_at DESC LIMIT 1;
 RETURN m.frozen_snapshot||jsonb_build_object('run_id',r.run_id,'account_id',r.account_id,'member_id',m.member_id,'scope_bytes',m.frozen_snapshot->'scope_bytes','review_bytes',m.frozen_snapshot->'review_bytes','evidence_bytes',m.frozen_snapshot->'evidence_bytes','root_bytes',replace(encode(b.root_bytes,'base64'),E'\n',''),'parent_proof_bytes',replace(encode(c.proof_bytes,'base64'),E'\n',''),'verification_proof_bytes',replace(encode(v.proof_bytes,'base64'),E'\n',''));
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_private_result(p_attempt uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;result record;
BEGIN
 s:=ctt_staff_private.batch_require_service('workflow');SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE attempt_id=p_attempt;SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=m.run_id;
 IF r.backend_pin IS DISTINCT FROM s.backend_pin THEN RAISE EXCEPTION 'CTT batch result denied' USING ERRCODE='42501';END IF;
 PERFORM 1 FROM ctt_staff_private.review_batch_capture_identities WHERE capture_identity=m.capture_identity FOR SHARE;
 SELECT result_id,team_id INTO result FROM ctt_staff_private.capture_results WHERE attempt_id=p_attempt;
 RETURN jsonb_build_object('attempt_id',p_attempt,'status',CASE WHEN result.result_id IS NOT NULL THEN 'committed' ELSE 'unknown' END,'result_id',result.result_id,'team_id',result.team_id);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_source_asset_context(p_account uuid,p_session uuid,p_asset uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE m ctt_staff_private.review_batch_members%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;page record;
BEGIN
 SELECT mm.* INTO m FROM ctt_staff_private.review_batch_members mm JOIN ctt_staff_private.review_batch_runs rr USING(run_id) WHERE rr.account_id=p_account AND mm.frozen_snapshot->>'session_id'=p_session::text ORDER BY rr.created_at DESC LIMIT 1;
 r:=ctt_staff_private.batch_require_run(p_account,m.run_id);
 SELECT pp.* INTO page FROM ctt_staff_private.review_source_pages_v2 pp WHERE pp.manifest_sha256=m.manifest_sha256 AND pp.document_id=m.document_id AND pp.asset_id=p_asset;
 IF page.asset_id IS NULL OR NOT ctt_staff_private.batch_assert_source_document(r.edition_id,m.manifest_sha256,m.document_id,m.source_payload_hash) THEN RAISE EXCEPTION 'CTT batch asset denied' USING ERRCODE='42501';END IF;
 RETURN jsonb_build_object('run_id',r.run_id,'document_id',m.document_id,'page_index',page.page_index);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_mark_uncertain(p_worker uuid,p_attempt uuid,p_epoch bigint,p_code text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE s ctt_staff_private.review_batch_services%ROWTYPE;slot ctt_staff_private.review_batch_dispatch_slot%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;
BEGIN
 s:=ctt_staff_private.batch_require_service('worker');SELECT * INTO slot FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton FOR UPDATE;
 SELECT mm.* INTO m FROM ctt_staff_private.review_batch_members mm JOIN ctt_staff_private.review_batch_runs rr USING(run_id) WHERE mm.attempt_id=p_attempt AND rr.backend_pin=s.backend_pin;
 IF slot.member_id IS DISTINCT FROM m.member_id OR slot.worker_id IS DISTINCT FROM p_worker OR slot.lease_epoch IS DISTINCT FROM p_epoch THEN RAISE EXCEPTION 'CTT batch uncertainty stale' USING ERRCODE='42501';END IF;
 UPDATE ctt_staff_private.review_batch_jobs SET status='uncertain' WHERE member_id=m.member_id;
 UPDATE ctt_staff_private.review_batch_members SET status='uncertain' WHERE member_id=m.member_id AND status<>'committed';
 INSERT INTO ctt_staff_private.review_batch_events(run_id,member_id,action,details) VALUES(m.run_id,m.member_id,'RESULT_UNCERTAIN',jsonb_build_object('code',CASE WHEN p_code~'^[A-Z_]{1,64}$' THEN p_code ELSE 'CAPTURE_UNCERTAIN' END));
 RETURN jsonb_build_object('attempt_id',p_attempt,'status','uncertain');
END $fn$;
CREATE TABLE ctt_staff_private.review_batch_corrections(
 correction_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),run_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_runs,
 member_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_members,principal_id uuid NOT NULL REFERENCES ctt_staff_private.review_principals,
 expected_version bigint NOT NULL,edits jsonb NOT NULL,source_pages jsonb NOT NULL,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(member_id,expected_version)
);
CREATE FUNCTION ctt_staff_private.batch_validate_edit_rectangle(p_value jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE key text;
BEGIN
 PERFORM ctt_staff_private.batch_require_keys(p_value,ARRAY['page_index','x','y','width','height']);
 FOREACH key IN ARRAY ARRAY['page_index','x','y','width','height'] LOOP
 IF jsonb_typeof(p_value->key) IS DISTINCT FROM 'number' OR p_value->>key !~ '^(0|[1-9][0-9]{0,4})$' THEN RAISE EXCEPTION 'CTT pending rectangle invalid' USING ERRCODE='42501';END IF;
 END LOOP;
 IF (p_value->>'page_index')::int NOT BETWEEN 1 AND 4 OR (p_value->>'width')::int<1 OR (p_value->>'height')::int<1 THEN RAISE EXCEPTION 'CTT pending rectangle invalid' USING ERRCODE='42501';END IF;RETURN true;
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_validate_edit_value(p_field text,p_value jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE name text;text_value jsonb;
BEGIN
 IF p_field IS NULL OR p_field !~ '^(players|staff)\.[1-9][0-9]*\.(first_name|last_name|birth_date|curp|photo|presence)$' THEN RAISE EXCEPTION 'CTT pending field invalid' USING ERRCODE='42501';END IF;
 name:=split_part(p_field,'.',3);
 IF name IN('first_name','last_name','birth_date','curp') THEN
 text_value:=p_value;
 IF jsonb_typeof(p_value)='object' THEN
 PERFORM ctt_staff_private.batch_require_keys(p_value,ARRAY['kind','value','rectangle']);
 IF p_value->>'kind' IS DISTINCT FROM 'source_field' THEN RAISE EXCEPTION 'CTT pending identity kind invalid' USING ERRCODE='42501';END IF;
 PERFORM ctt_staff_private.batch_validate_edit_rectangle(p_value->'rectangle');text_value:=p_value->'value';END IF;
 IF jsonb_typeof(text_value) NOT IN('string','null') OR length(text_value#>>'{}')>500 OR text_value#>>'{}' ~ '[[:cntrl:]]' THEN RAISE EXCEPTION 'CTT pending identity invalid' USING ERRCODE='42501';END IF;
 ELSIF name='photo' THEN
 PERFORM ctt_staff_private.batch_require_keys(p_value,ARRAY['kind','presence','rectangle']);
 IF p_value->>'kind' IS DISTINCT FROM 'photo' OR jsonb_typeof(p_value->'presence') IS DISTINCT FROM 'string' OR p_value->>'presence' NOT IN('PRESENT','ABSENT') THEN RAISE EXCEPTION 'CTT pending photo invalid' USING ERRCODE='42501';END IF;
 PERFORM ctt_staff_private.batch_validate_edit_rectangle(p_value->'rectangle');
 ELSE
 text_value:=p_value;
 IF jsonb_typeof(p_value)='object' THEN
 PERFORM ctt_staff_private.batch_require_keys(p_value,ARRAY['kind','presence']);
 IF p_value->>'kind' IS DISTINCT FROM 'presence' THEN RAISE EXCEPTION 'CTT pending presence invalid' USING ERRCODE='42501';END IF;text_value:=p_value->'presence';END IF;
 IF jsonb_typeof(text_value) IS DISTINCT FROM 'string' OR (text_value#>>'{}') NOT IN('PRESENT','ABSENT') THEN RAISE EXCEPTION 'CTT pending presence invalid' USING ERRCODE='42501';END IF;
 END IF;RETURN true;
END $fn$;

CREATE FUNCTION ctt_staff_private.batch_member_detail(p_account uuid,p_run uuid,p_member uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;fields jsonb;
BEGIN
 r:=ctt_staff_private.batch_require_run(p_account,p_run);SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE member_id=p_member AND run_id=p_run;
 IF m.member_id IS NULL THEN RAISE EXCEPTION 'CTT batch member denied' USING ERRCODE='42501';END IF;
 SELECT coalesce(jsonb_agg(jsonb_build_object('field_id',f->>'field_id','label',f->>'label','value',f->'value','source_page_index',f->'source_page_index','kind',f->'kind','rectangle',f->'rectangle','card_bounds',f->'card_bounds','allow_absent',f->'allow_absent','editable',m.status='exception' AND NOT m.original_auto_ready AND f->'editable'='true'::jsonb)),'[]') INTO fields FROM jsonb_array_elements(coalesce(m.frozen_snapshot->'editable_fields','[]')) f;
 RETURN jsonb_build_object('run_id',p_run,'member_id',p_member,'version',r.version,'status',CASE WHEN r.status='analysis_pending' AND EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_corrections WHERE member_id=p_member) THEN 'analysis_pending' WHEN r.status='paused' THEN 'suspended' ELSE m.status END,'document_id',m.document_id,'team_name',m.label,'fields',fields,'source_pages',m.source_pages);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_propose_member_corrections(p_account uuid,p_run uuid,p_member uuid,p_version bigint,p_edits jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;e jsonb;field jsonb;
BEGIN
 PERFORM ctt_staff_private.batch_expire_blocks(p_run);r:=ctt_staff_private.batch_require_run(p_account,p_run);PERFORM 1 FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton FOR UPDATE;SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=p_run FOR UPDATE;
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE run_id=p_run AND member_id=p_member FOR UPDATE;
 IF r.version IS DISTINCT FROM p_version THEN RAISE EXCEPTION 'CTT batch corrections stale' USING ERRCODE='40001';END IF;
 IF m.status IS DISTINCT FROM 'exception' OR m.original_auto_ready THEN RAISE EXCEPTION 'QUALITY_FALSE_ADMISSION_REPORT_REQUIRED' USING ERRCODE='55000';END IF;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_blocks WHERE run_id=p_run AND status IN('prepared','challenged','active','paused')) OR EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_jobs j JOIN ctt_staff_private.review_batch_members mm USING(member_id) WHERE mm.run_id=p_run AND j.status IN('leased','uncertain')) THEN RAISE EXCEPTION 'CTT batch parent recovery required' USING ERRCODE='55000';END IF;
 IF jsonb_typeof(p_edits) IS DISTINCT FROM 'array' OR jsonb_array_length(p_edits) NOT BETWEEN 1 AND 200 OR (SELECT count(DISTINCT value->>'field_id') FROM jsonb_array_elements(p_edits))<>jsonb_array_length(p_edits) THEN RAISE EXCEPTION 'CTT batch edits denied' USING ERRCODE='42501';END IF;
 FOR e IN SELECT value FROM jsonb_array_elements(p_edits) LOOP
 PERFORM ctt_staff_private.batch_require_keys(e,ARRAY['field_id','value']);
 SELECT f INTO field FROM jsonb_array_elements(coalesce(m.frozen_snapshot->'editable_fields','[]')) f WHERE f->>'field_id'=e->>'field_id' AND f->'editable'='true'::jsonb;
 IF field IS NULL OR NOT ctt_staff_private.batch_validate_edit_value(e->>'field_id',e->'value') THEN RAISE EXCEPTION 'CTT batch source edit denied' USING ERRCODE='42501';END IF;
 IF jsonb_typeof(e->'value')='object' AND e->'value' ? 'rectangle' THEN
 IF field->'card_bounds' IS NULL OR field->'card_bounds'='null'::jsonb OR (e->'value'->'rectangle'->>'page_index')::int IS DISTINCT FROM (field->'card_bounds'->>'page_index')::int OR (e->'value'->'rectangle'->>'x')::bigint<(field->'card_bounds'->>'x')::bigint OR (e->'value'->'rectangle'->>'y')::bigint<(field->'card_bounds'->>'y')::bigint OR (e->'value'->'rectangle'->>'x')::bigint+(e->'value'->'rectangle'->>'width')::bigint>(field->'card_bounds'->>'x')::bigint+(field->'card_bounds'->>'width')::bigint OR (e->'value'->'rectangle'->>'y')::bigint+(e->'value'->'rectangle'->>'height')::bigint>(field->'card_bounds'->>'y')::bigint+(field->'card_bounds'->>'height')::bigint THEN RAISE EXCEPTION 'CTT pending rectangle leaves admitted card' USING ERRCODE='42501';END IF;
 END IF;
 END LOOP;
 INSERT INTO ctt_staff_private.review_batch_corrections(run_id,member_id,principal_id,expected_version,edits,source_pages) VALUES(p_run,p_member,r.principal_id,r.version,p_edits,m.source_pages);
 UPDATE ctt_staff_private.review_batch_runs SET status='analysis_pending',version=version+1,qualification_id=NULL WHERE run_id=p_run;
 UPDATE ctt_staff_private.review_batch_analysis_jobs SET status='queued',owner_id=NULL,lease_until=NULL WHERE run_id=p_run;
 INSERT INTO ctt_staff_private.review_batch_events(run_id,member_id,action) VALUES(p_run,p_member,'HUMAN_FIELD_PROPOSAL_PENDING_UV');
 RETURN jsonb_build_object('run_id',p_run,'member_id',p_member,'version',r.version+1,'status','analysis_pending');
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_report_quality_issue(p_account uuid,p_run uuid,p_member uuid,p_version bigint,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;
BEGIN
 r:=ctt_staff_private.batch_require_run(p_account,p_run);PERFORM 1 FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton FOR UPDATE;
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=p_run FOR UPDATE;
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE member_id=p_member AND run_id=p_run;
 IF r.version IS DISTINCT FROM p_version OR m.member_id IS NULL OR length(p_reason) NOT BETWEEN 1 AND 500 THEN RAISE EXCEPTION 'CTT batch quality report denied' USING ERRCODE='42501';END IF;
 PERFORM 1 FROM ctt_staff_private.review_batch_blocks WHERE run_id=p_run FOR UPDATE;
 UPDATE ctt_staff_private.review_batch_blocks SET status='revoked',version=version+1,dispatch_generation=dispatch_generation+1 WHERE run_id=p_run AND status IN('prepared','challenged','active','paused');
 UPDATE ctt_staff_private.review_batch_jobs SET status='uncertain' WHERE member_id IN(SELECT member_id FROM ctt_staff_private.review_batch_members WHERE run_id=p_run AND status<>'committed') AND status IN('leased','queued');
 UPDATE ctt_staff_private.review_batch_members SET status='exception',issues=issues||jsonb_build_array(jsonb_build_object('code','QUALITY_STRATUM_SUSPENDED')) WHERE run_id=p_run AND status NOT IN('committed','already_captured');
 UPDATE ctt_staff_private.review_batch_runs SET qualification_id=NULL,status='paused',version=version+1 WHERE run_id=p_run;
 INSERT INTO ctt_staff_private.review_batch_events(run_id,member_id,action,details) VALUES(p_run,p_member,'CRITICAL_QUALITY_REPORT',jsonb_build_object('principal_id',r.principal_id,'reason',p_reason,'scope','entire_unstarted_run_conservative'));
 RETURN jsonb_build_object('run_id',p_run,'member_id',p_member,'version',r.version+1,'status','suspended');
END $fn$;

CREATE TABLE ctt_staff_private.review_batch_page_consumptions(
 edition_id uuid NOT NULL,pdf_sha256 text NOT NULL CHECK(pdf_sha256~'^[0-9a-f]{64}$'),pdf_page integer NOT NULL CHECK(pdf_page>0),
 operation_id uuid NOT NULL,attempt_id uuid NOT NULL,result_id uuid NOT NULL,team_id uuid NOT NULL,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(edition_id,pdf_sha256,pdf_page)
);
CREATE FUNCTION ctt_staff_private.batch_lock_capture_source_pages(p_edition uuid,p_pages jsonb,p_operation uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE page jsonb;prior record;
BEGIN
 IF session_user='devnous_user' OR p_edition IS NULL OR p_operation IS NULL OR jsonb_typeof(p_pages) IS DISTINCT FROM 'array' OR jsonb_array_length(p_pages) NOT BETWEEN 1 AND 4 OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(p_pages))<>jsonb_array_length(p_pages) THEN RAISE EXCEPTION 'CTT batch physical source denied' USING ERRCODE='42501';END IF;
 FOR page IN SELECT value FROM jsonb_array_elements(p_pages) ORDER BY value->>'pdf_sha256',(value->>'pdf_page')::integer LOOP
 PERFORM ctt_staff_private.batch_require_keys(page,ARRAY['pdf_sha256','pdf_page']);
 IF page->>'pdf_sha256' !~ '^[0-9a-f]{64}$' OR coalesce((page->>'pdf_page')::integer,0)<1 THEN RAISE EXCEPTION 'CTT batch page invalid' USING ERRCODE='42501';END IF;
 PERFORM pg_advisory_xact_lock(1129600052,hashtext(p_edition::text||':'||(page->>'pdf_sha256')||':'||(page->>'pdf_page')));
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_page_consumptions pc WHERE pc.edition_id=p_edition AND pc.pdf_sha256=page->>'pdf_sha256' AND pc.pdf_page=(page->>'pdf_page')::integer AND pc.operation_id<>p_operation) THEN RAISE EXCEPTION 'CTT source page already captured' USING ERRCODE='55000';END IF;
 -- Existing individual results are inspected, never backfilled or rewritten.
 IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_results cr JOIN ctt_staff_private.capture_attempt_anchors a USING(attempt_id) WHERE a.edition_id=p_edition AND cr.attempt_id<>p_operation AND (convert_from(a.scope_bytes,'UTF8')::jsonb)->>'pdf_sha256'=page->>'pdf_sha256' AND EXISTS(SELECT 1 FROM jsonb_array_elements((convert_from(a.scope_bytes,'UTF8')::jsonb)->'source_pages') pp WHERE coalesce(pp->>'pdf_page',pp#>>'{}')=(page->>'pdf_page'))) THEN RAISE EXCEPTION 'CTT historical source page already captured' USING ERRCODE='55000';END IF;
 END LOOP;
 RETURN true;
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_record_page_consumption(p_edition uuid,p_pages jsonb,p_operation uuid,p_attempt uuid,p_result uuid,p_team uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE page jsonb;
BEGIN
 PERFORM ctt_staff_private.current_finality_writer();
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.capture_results WHERE attempt_id=p_attempt AND result_id=p_result AND team_id=p_team AND xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint) THEN RAISE EXCEPTION 'CTT canonical result required for pages' USING ERRCODE='42501';END IF;
 PERFORM ctt_staff_private.batch_lock_capture_source_pages(p_edition,p_pages,p_operation);
 FOR page IN SELECT value FROM jsonb_array_elements(p_pages) LOOP
 INSERT INTO ctt_staff_private.review_batch_page_consumptions(edition_id,pdf_sha256,pdf_page,operation_id,attempt_id,result_id,team_id) VALUES(p_edition,page->>'pdf_sha256',(page->>'pdf_page')::integer,p_operation,p_attempt,p_result,p_team) ON CONFLICT DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_page_consumptions WHERE edition_id=p_edition AND pdf_sha256=page->>'pdf_sha256' AND pdf_page=(page->>'pdf_page')::integer AND operation_id=p_operation AND attempt_id=p_attempt AND result_id=p_result AND team_id=p_team) THEN RAISE EXCEPTION 'CTT page consumption changed' USING ERRCODE='42501';END IF;
 END LOOP;RETURN true;
END $fn$;

CREATE FUNCTION ctt_staff_private.batch_refresh_verifications(p_account uuid,p_run uuid,p_proofs jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE r ctt_staff_private.review_batch_runs%ROWTYPE;q ctt_staff_private.review_batch_qualifications%ROWTYPE;p jsonb;body jsonb;env jsonb;raw bytea;m ctt_staff_private.review_batch_members%ROWTYPE;s ctt_staff_private.review_batch_services%ROWTYPE;
BEGIN
 r:=ctt_staff_private.batch_require_run(p_account,p_run);SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=p_run FOR UPDATE;
 SELECT * INTO q FROM ctt_staff_private.review_batch_qualifications WHERE qualification_id=r.qualification_id;
 IF q.qualification_id IS NULL OR EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_blocks WHERE run_id=p_run AND status IN('challenged','active','paused')) THEN RAISE EXCEPTION 'CTT batch proof refresh blocked' USING ERRCODE='55000';END IF;
 FOR p IN SELECT value FROM jsonb_array_elements(p_proofs) LOOP
 raw:=decode(p->>'proof_base64','base64');env:=ctt_staff_private.batch_json(raw);body:=env->'body';
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE run_id=p_run AND document_id=body->>'document_id';
 SELECT * INTO s FROM ctt_staff_private.review_batch_services WHERE kind='verifier' AND signing_kid=body->>'kid' AND profile_hash=r.profile_hash AND backend_pin=r.backend_pin AND revoked_at IS NULL AND valid_from<=clock_timestamp() AND valid_until>clock_timestamp();
 IF m.status IS DISTINCT FROM 'ready' OR s.service_role IS NULL OR body IS DISTINCT FROM p->'body' OR p->>'proof_hash' IS DISTINCT FROM encode(public.digest(raw,'sha256'),'hex') OR body->>'qualification_hash' IS DISTINCT FROM q.candidate_hash OR body->>'profile_hash' IS DISTINCT FROM r.profile_hash OR body->>'source_binding_hash' IS DISTINCT FROM m.frozen_snapshot->>'source_binding_hash' OR body->>'scope_hash' IS DISTINCT FROM m.scope_hash OR body->>'payload_hash' IS DISTINCT FROM m.payload_hash OR body->>'manifest_sha256' IS DISTINCT FROM m.manifest_sha256 OR body->>'policy_hash' IS DISTINCT FROM m.policy_hash OR body->'verified_by_human' IS DISTINCT FROM 'false'::jsonb OR body->>'human_edits_hash' IS DISTINCT FROM coalesce(m.frozen_snapshot->>'human_edits_hash',encode(public.digest('[]','sha256'),'hex')) OR body->>'schema' IS DISTINCT FROM 'ctt.batch.verification.v1' OR octet_length(decode(env->>'signature','base64')) IS DISTINCT FROM 64 OR to_timestamp((body->>'issued_at')::bigint)>clock_timestamp() OR to_timestamp((body->>'expires_at')::bigint)<=clock_timestamp() OR (body->>'expires_at')::bigint>(body->>'issued_at')::bigint+3600 OR NOT ctt_staff_private.batch_assert_source_document(r.edition_id,m.manifest_sha256,m.document_id,m.source_payload_hash) THEN RAISE EXCEPTION 'CTT batch refreshed proof denied' USING ERRCODE='42501';END IF;
 INSERT INTO ctt_staff_private.review_batch_verifications(verification_hash,qualification_id,member_id,proof_bytes,service_role) VALUES(p->>'proof_hash',q.qualification_id,m.member_id,raw,s.service_role) ON CONFLICT DO NOTHING;
 END LOOP;
 RETURN jsonb_build_object('run_id',p_run,'version',r.version);
END $fn$;

-- Candidate-only canonical writer bridge. Embedded after batch consent tables.
-- No runtime identities, keys, source admissions, privileges or captures seeded.
CREATE TABLE ctt_staff_private.capture_attempt_registry(
 attempt_id uuid PRIMARY KEY,kind text NOT NULL CHECK(kind IN('individual_v2','batch_v3')),
 legacy_attempt_id uuid UNIQUE REFERENCES ctt_staff_private.capture_attempt_anchors(attempt_id),
 batch_member_id uuid UNIQUE REFERENCES ctt_staff_private.review_batch_members(member_id),
 CHECK((kind='individual_v2' AND legacy_attempt_id=attempt_id AND batch_member_id IS NULL)
    OR (kind='batch_v3' AND legacy_attempt_id IS NULL AND batch_member_id IS NOT NULL))
);
-- References only: preserve every existing individual proof and anchor byte.
INSERT INTO ctt_staff_private.capture_attempt_registry(attempt_id,kind,legacy_attempt_id)
 SELECT attempt_id,'individual_v2',attempt_id FROM ctt_staff_private.capture_attempt_anchors;
CREATE FUNCTION ctt_staff_private.batch_register_individual_attempt()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
BEGIN
 INSERT INTO ctt_staff_private.capture_attempt_registry(attempt_id,kind,legacy_attempt_id)
 VALUES(NEW.attempt_id,'individual_v2',NEW.attempt_id); RETURN NEW;
END $fn$;
CREATE TRIGGER ctt_capture_attempt_typed_registry AFTER INSERT ON ctt_staff_private.capture_attempt_anchors
 FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.batch_register_individual_attempt();
DO $fk$
DECLARE item record;
BEGIN
 FOR item IN SELECT c.conname,c.conrelid::regclass relation FROM pg_constraint c
 WHERE c.contype='f' AND c.confrelid='ctt_staff_private.capture_attempt_anchors'::regclass
 AND c.conrelid IN('ctt_staff_private.capture_payloads'::regclass,'ctt_staff_private.legacy_v2_reservations'::regclass) LOOP
 EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',item.relation,item.conname);
 EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY(attempt_id) REFERENCES ctt_staff_private.capture_attempt_registry(attempt_id) ON DELETE RESTRICT',item.relation,item.conname);
 END LOOP;
END $fk$;
CREATE TABLE ctt_staff_private.batch_capture_payloads(
 attempt_id uuid NOT NULL REFERENCES ctt_staff_private.capture_attempt_registry,
 authorization_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_child_authorizations,
 review_bytes bytea NOT NULL,effect_bytes bytea NOT NULL,gate_envelope bytea NOT NULL,
 evidence_bytes bytea NOT NULL,preauthorization_bytes bytea NOT NULL,nonce uuid NOT NULL,
 PRIMARY KEY(attempt_id,authorization_id),UNIQUE(nonce)
);
CREATE TABLE ctt_staff_private.batch_writer_contexts(
 attempt_id uuid NOT NULL REFERENCES ctt_staff_private.capture_attempt_registry,
 authorization_id uuid NOT NULL REFERENCES ctt_staff_private.review_batch_child_authorizations,
 pg_top_xid xid8 NOT NULL,backend_pid integer NOT NULL,writer_role name NOT NULL,
 PRIMARY KEY(attempt_id,pg_top_xid)
);
CREATE FUNCTION ctt_staff_private.batch_consume_child(p_attempt uuid,p_auth uuid,p_worker uuid,p_epoch bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE ctx jsonb;scope jsonb;ids uuid[];
BEGIN
 PERFORM ctt_staff_private.current_finality_writer();
 ctx:=ctt_staff_private.batch_lock_child(p_attempt,p_auth,p_worker,p_epoch);
 INSERT INTO ctt_staff_private.batch_writer_contexts(attempt_id,authorization_id,pg_top_xid,backend_pid,writer_role)
 VALUES(p_attempt,p_auth,pg_current_xact_id(),pg_backend_pid(),session_user);
 SELECT ctt_staff_private.batch_json(decode(m.frozen_snapshot->>'scope_bytes','base64')) INTO scope
 FROM ctt_staff_private.review_batch_members m WHERE m.attempt_id=p_attempt;
 ids:=ARRAY[(scope->>'session_id')::uuid,(scope->>'draft_id')::uuid];
 ids:=ids||ARRAY(SELECT (p->>'asset_id')::uuid FROM jsonb_array_elements(scope->'source_pages') p);
 PERFORM ctt_staff_private.lock_legacy_reference_ids(ids);
 -- Existing source reservations remain unchanged; the fresh typed child,
 -- current draft and shared source lock supply independent batch authority.
 INSERT INTO ctt_staff_private.legacy_v2_reservations(reserved_id,attempt_id)
 SELECT DISTINCT id,p_attempt FROM unnest(ids) id ON CONFLICT DO NOTHING;
 INSERT INTO ctt_staff_private.capture_payloads(attempt_id,review_bytes,effect_bytes,gate_envelope,nonce)
 SELECT attempt_id,review_bytes,effect_bytes,gate_envelope,nonce FROM ctt_staff_private.batch_capture_payloads
 WHERE attempt_id=p_attempt AND authorization_id=p_auth;
 IF NOT FOUND THEN RAISE EXCEPTION 'CTT batch staged payload absent' USING ERRCODE='42501';END IF;
 INSERT INTO ctt_staff_private.finality_inputs(attempt_id,preauthorization_bytes,evidence_bytes)
 SELECT attempt_id,preauthorization_bytes,evidence_bytes FROM ctt_staff_private.batch_capture_payloads
 WHERE attempt_id=p_attempt AND authorization_id=p_auth;
 RETURN true;
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_current_writer_input(p_attempt uuid)
RETURNS TABLE(scope_bytes bytea,scope_hash text,proof_sha256 text,expires_at_seconds bigint,
 issued_at timestamptz,expires_at timestamptz,preauthorization_bytes bytea,evidence_bytes bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE c ctt_staff_private.batch_writer_contexts%ROWTYPE;a ctt_staff_private.review_batch_child_authorizations%ROWTYPE;ctx jsonb;
BEGIN
 PERFORM ctt_staff_private.current_finality_writer();
 SELECT * INTO c FROM ctt_staff_private.batch_writer_contexts WHERE attempt_id=p_attempt
 AND pg_top_xid=pg_current_xact_id() AND backend_pid=pg_backend_pid() AND writer_role=session_user;
 IF c.attempt_id IS NULL OR (SELECT xmin::text::bigint FROM ctt_staff_private.batch_writer_contexts
 WHERE attempt_id=p_attempt AND pg_top_xid=pg_current_xact_id())<>mod(pg_current_xact_id()::text::numeric,4294967296)::bigint THEN
 RAISE EXCEPTION 'CTT batch writer context denied' USING ERRCODE='42501';END IF;
 SELECT * INTO a FROM ctt_staff_private.review_batch_child_authorizations WHERE authorization_id=c.authorization_id;
 ctx:=ctt_staff_private.batch_lock_child(p_attempt,c.authorization_id,a.worker_id,a.lease_epoch);
 RETURN QUERY SELECT decode(m.frozen_snapshot->>'scope_bytes','base64'),m.scope_hash,
 'sha256:'||a.proof_hash,floor(extract(epoch FROM a.expires_at))::bigint,a.issued_at,a.expires_at,
 i.preauthorization_bytes,i.evidence_bytes FROM ctt_staff_private.review_batch_members m
 JOIN ctt_staff_private.finality_inputs i ON i.attempt_id=m.attempt_id WHERE m.member_id=a.member_id;
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_project_capture_rows(p_attempt uuid,p_team jsonb,p_players jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE m ctt_staff_private.review_batch_members%ROWTYPE;plan jsonb;team public.copa_telmex_teams;
 player public.copa_telmex_players;item jsonb;rows jsonb:='[]';n int:=0;
BEGIN
 PERFORM ctt_staff_private.batch_require_service('workflow');
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE attempt_id=p_attempt;
 IF m.member_id IS NULL OR m.status NOT IN('ready','pending') THEN RAISE EXCEPTION 'CTT batch projection denied' USING ERRCODE='42501';END IF;
 plan:=ctt_staff_private.batch_json(decode(m.frozen_snapshot->>'review_bytes','base64'));
 IF jsonb_typeof(p_players) IS DISTINCT FROM 'array' OR jsonb_array_length(p_players)<>jsonb_array_length(plan->'players')
 OR p_team IS DISTINCT FROM plan->'team' THEN RAISE EXCEPTION 'CTT batch projection denied' USING ERRCODE='42501';END IF;
 team:=jsonb_populate_record(NULL::public.copa_telmex_teams,p_team);
 FOR item IN SELECT value FROM jsonb_array_elements(p_players) LOOP
 IF (item-ARRAY['governance_decision_id','roster_draft_binding','preauthorization_receipt_id','finality_receipt_id']) IS DISTINCT FROM plan->'players'->n
 OR item->>'governance_state' IS DISTINCT FROM 'PENDING_FINALITY' THEN RAISE EXCEPTION 'CTT batch projection denied' USING ERRCODE='42501';END IF;
 player:=jsonb_populate_record(NULL::public.copa_telmex_players,item);
 rows:=rows||jsonb_build_array(jsonb_build_object('id',player.id,'postcommit_revision',0,
 'postcommit_snapshot_hash',public.regs07_snapshot_hash(public.regs07_player_snapshot(player))));n:=n+1;
 END LOOP;
 RETURN jsonb_build_object('team',jsonb_build_object('id',team.id,'postcommit_revision',1,
 'postcommit_snapshot_hash',public.regs07_snapshot_hash(public.regs07_team_snapshot(team))),'players',rows);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_stage_capture_payload(p_attempt uuid,p_auth uuid,p_review bytea,p_effect bytea,p_evidence bytea,p_preauth bytea,p_gate bytea,p_nonce uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;m ctt_staff_private.review_batch_members%ROWTYPE;
 a ctt_staff_private.review_batch_child_authorizations%ROWTYPE;b ctt_staff_private.review_batch_blocks%ROWTYPE;
 r ctt_staff_private.review_batch_runs%ROWTYPE;effect jsonb;gate jsonb;scope jsonb;preauth jsonb;
BEGIN
 svc:=ctt_staff_private.batch_require_service('workflow');
 SELECT * INTO a FROM ctt_staff_private.review_batch_child_authorizations WHERE authorization_id=p_auth;
 SELECT * INTO m FROM ctt_staff_private.review_batch_members WHERE member_id=a.member_id;
 SELECT rr.* INTO r FROM ctt_staff_private.review_batch_runs rr WHERE rr.run_id=m.run_id;
 PERFORM 1 FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton FOR SHARE;
 SELECT bb.* INTO b FROM ctt_staff_private.review_batch_blocks bb JOIN ctt_staff_private.review_batch_consents cc USING(block_id) WHERE cc.consent_id=a.consent_id FOR SHARE OF bb;
 PERFORM 1 FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton AND member_id=m.member_id AND worker_id=a.worker_id AND lease_epoch=a.lease_epoch AND lease_until>clock_timestamp() FOR SHARE;
 IF NOT FOUND OR m.attempt_id IS DISTINCT FROM p_attempt OR r.backend_pin<>svc.backend_pin OR b.status<>'active'
 OR a.authorization_generation<>m.authorization_generation OR a.dispatch_generation<>b.dispatch_generation
 OR a.expires_at<=clock_timestamp() OR a.nonce<>p_nonce OR p_review<>decode(m.frozen_snapshot->>'review_bytes','base64')
 OR p_evidence<>decode(m.frozen_snapshot->>'evidence_bytes','base64') THEN RAISE EXCEPTION 'CTT batch payload denied' USING ERRCODE='42501';END IF;
 effect:=ctt_staff_private.batch_json(p_effect);gate:=ctt_staff_private.batch_json(p_gate)->'body';
 scope:=ctt_staff_private.batch_json(decode(m.frozen_snapshot->>'scope_bytes','base64'));preauth:=ctt_staff_private.batch_json(p_preauth);
 IF effect->>'schema' IS DISTINCT FROM 'ctt.capture.effect.v3' OR effect->'review_context' IS DISTINCT FROM scope-ARRAY['staff_and_changes_binding']
 OR gate->>'schema' IS DISTINCT FROM 'ctt.batch.capture-authorization.v1' OR gate->>'attempt_id' IS DISTINCT FROM p_attempt::text
 OR gate->>'authorization_id' IS DISTINCT FROM p_auth::text OR gate->>'member_proof_hash' IS DISTINCT FROM a.proof_hash
 OR gate->>'scope_hash' IS DISTINCT FROM m.scope_hash OR gate->>'nonce' IS DISTINCT FROM p_nonce::text
 OR gate->>'expires_at' IS DISTINCT FROM floor(extract(epoch FROM a.expires_at))::bigint::text
 OR preauth->>'contract_version' IS DISTINCT FROM 'ctt-registration-staff-batch-v3'
 OR preauth->'roster_decision'->>'authorization_id' IS DISTINCT FROM p_auth::text
 OR preauth->'roster_decision'->>'member_proof_hash' IS DISTINCT FROM a.proof_hash THEN RAISE EXCEPTION 'CTT batch payload binding denied' USING ERRCODE='42501';END IF;
 INSERT INTO ctt_staff_private.capture_attempt_registry(attempt_id,kind,batch_member_id) VALUES(p_attempt,'batch_v3',m.member_id) ON CONFLICT DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_registry WHERE attempt_id=p_attempt AND kind='batch_v3' AND batch_member_id=m.member_id) THEN RAISE EXCEPTION 'CTT batch attempt collision' USING ERRCODE='42501';END IF;
 INSERT INTO ctt_staff_private.batch_capture_payloads(attempt_id,authorization_id,review_bytes,effect_bytes,gate_envelope,evidence_bytes,preauthorization_bytes,nonce)
 VALUES(p_attempt,p_auth,p_review,p_effect,p_gate,p_evidence,p_preauth,p_nonce) ON CONFLICT DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.batch_capture_payloads WHERE attempt_id=p_attempt AND authorization_id=p_auth
 AND review_bytes=p_review AND effect_bytes=p_effect AND gate_envelope=p_gate AND nonce=p_nonce
 AND preauthorization_bytes=p_preauth AND evidence_bytes=p_evidence) THEN RAISE EXCEPTION 'CTT batch staged payload changed' USING ERRCODE='40001';END IF;
 RETURN jsonb_build_object('staged',true,'attempt_id',p_attempt);
END $fn$;

CREATE FUNCTION ctt_staff_private.batch_exact_capture_row(p_table text,p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE candidate record; writer record; effect jsonb; envelope jsonb; scope jsonb;
  proposed jsonb; answer jsonb; hits integer:=0; top_xid bigint; now_at timestamptz;
BEGIN
  IF p_table NOT IN ('copa_telmex_teams','copa_telmex_players','copa_telmex_team_staff') THEN RETURN NULL; END IF;
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
  FOR candidate IN SELECT p.attempt_id,p.effect_bytes,p.gate_envelope,p.nonce,i.scope_bytes,i.scope_hash,
    convert_from(i.scope_bytes,'UTF8')::jsonb->>'tenant_id' tenant_id,
    (convert_from(i.scope_bytes,'UTF8')::jsonb->>'edition_id')::uuid edition_id,
    (convert_from(i.scope_bytes,'UTF8')::jsonb->>'actor_id')::uuid principal_id,
    i.issued_at,i.expires_at,i.expires_at_seconds
    FROM ctt_staff_private.batch_writer_contexts c
    JOIN ctt_staff_private.capture_payloads p USING(attempt_id)
    CROSS JOIN LATERAL ctt_staff_private.batch_current_writer_input(c.attempt_id) i
    WHERE c.pg_top_xid=pg_current_xact_id() AND c.backend_pid=pg_backend_pid() AND c.writer_role=session_user
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
    IF effect->>'schema' IS DISTINCT FROM 'ctt.capture.effect.v3'
      OR scope->>'purpose' IS DISTINCT FROM 'capture_staff_batch_v1'
      OR scope->>'schema' IS DISTINCT FROM 'ctt.staff-review.batch-scope.v3'
      OR effect->'review_context' IS DISTINCT FROM (scope-ARRAY['staff_and_changes_binding'])
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
    ELSIF p_table='copa_telmex_players' THEN SELECT value INTO proposed FROM jsonb_array_elements(effect->'players') WHERE value->>'id'=p_id::text;
    ELSE SELECT value->'row' INTO proposed FROM jsonb_array_elements(effect->'staff') WHERE value->'row'->>'id'=p_id::text; END IF;
    IF proposed->>'id' IS DISTINCT FROM p_id::text THEN CONTINUE; END IF;
    IF p_table<>'copa_telmex_teams' AND (proposed->>'governance_state' IS DISTINCT FROM 'PENDING_FINALITY'
      OR proposed->'finality_receipt_id' IS DISTINCT FROM 'null'::jsonb) THEN CONTINUE; END IF;
    answer:=proposed; hits:=hits+1;
  END LOOP;
  IF hits<>1 THEN RETURN NULL; END IF;
  RETURN answer;
END
$fn$;

CREATE FUNCTION ctt_staff_private.batch_check_capture_finality_proof(p_attempt_id uuid)
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
 SELECT p.effect_bytes,p.gate_envelope,i.scope_bytes,i.scope_hash,i.issued_at,i.expires_at,i.preauthorization_bytes,
 i.proof_sha256,i.expires_at_seconds INTO payload
 FROM ctt_staff_private.capture_payloads p
 CROSS JOIN LATERAL ctt_staff_private.batch_current_writer_input(p.attempt_id) i
 WHERE p.attempt_id=p_attempt_id FOR UPDATE OF p;
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
 OR effect->>'schema' IS DISTINCT FROM 'ctt.capture.finality-effect.v3'
 OR effect->>'column_schema' IS DISTINCT FROM 'ctt.capture.modeled-columns.v1'
 OR effect->>'scope_hash' IS DISTINCT FROM payload.scope_hash
 OR effect->>'review_attempt_id' IS DISTINCT FROM p_attempt_id::text
 OR effect->>'database_transaction_id' IS DISTINCT FROM tx_name
 OR effect->>'event_attempt_id' IS DISTINCT FROM expected_attempt
 OR effect->>'database_identity' IS DISTINCT FROM pin
 OR effect->>'database_oid' IS DISTINCT FROM db_oid::text
 OR effect->>'pg_top_xid' IS DISTINCT FROM pg_current_xact_id()::text
 OR body->>'schema' IS DISTINCT FROM 'ctt.capture.finality-authorization.v3'
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

CREATE FUNCTION ctt_staff_private.batch_validate_capture_finality_rows(p_attempt_id uuid,p_complete boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE effect jsonb; pending jsonb; body jsonb; item jsonb; original jsonb; expected jsonb; observed jsonb;
 event jsonb; receipt jsonb; scope jsonb; refs jsonb:='[]'::jsonb; typed_pending jsonb; typed_active jsonb;
 kind text; tab regclass; item_id uuid; slot integer; ordinal bigint; columns text[]; present integer:=0;
BEGIN
 effect:=ctt_staff_private.check_capture_finality_proof(p_attempt_id);
 SELECT convert_from(p.effect_bytes,'UTF8')::jsonb INTO pending FROM ctt_staff_private.capture_payloads p WHERE attempt_id=p_attempt_id;
 SELECT convert_from(f.finality_envelope,'UTF8')::jsonb->'body' INTO body FROM ctt_staff_private.capture_finalities f WHERE attempt_id=p_attempt_id;
 SELECT convert_from(a.scope_bytes,'UTF8')::jsonb INTO scope FROM ctt_staff_private.batch_current_writer_input(p_attempt_id) a;
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
 IF event->>'event_type' IS DISTINCT FROM (CASE kind WHEN 'PLAYER' THEN 'samchat_registration_player_finality_attestation_v3' ELSE 'samchat_registration_staff_finality_attestation_v3' END)
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
CREATE FUNCTION ctt_staff_private.batch_load_finality_context(p_attempt uuid)
RETURNS TABLE(database_identity text,database_oid bigint,pg_top_xid text,issued_at_seconds bigint,expires_at_seconds bigint,validation jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE src record;scope jsonb;draft record;pin text;validation_bytes bytea;
BEGIN
 SELECT * INTO src FROM ctt_staff_private.batch_current_writer_input(p_attempt);
 IF src IS NULL THEN RAISE EXCEPTION 'CTT batch finality denied' USING ERRCODE='42501';END IF;
 scope:=ctt_staff_private.batch_json(src.scope_bytes);
 PERFORM 1 FROM public.copa_telmex_registration_review_sessions WHERE id=(scope->>'session_id')::uuid AND committed_team_id IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'CTT batch session unavailable' USING ERRCODE='42501';END IF;
 SELECT d.id,d.draft_version,d.content_hash INTO draft FROM public.copa_telmex_registration_review_drafts d
 WHERE d.session_id=(scope->>'session_id')::uuid ORDER BY d.draft_version DESC,d.created_at DESC LIMIT 1 FOR SHARE;
 IF draft.id::text IS DISTINCT FROM scope->>'draft_id' OR draft.draft_version::text IS DISTINCT FROM scope->>'draft_version'
 OR draft.content_hash IS DISTINCT FROM 'sha256:'||(scope->>'draft_content_hash') THEN RAISE EXCEPTION 'CTT batch draft stale' USING ERRCODE='42501';END IF;
 SELECT f.database_identity INTO pin FROM ctt_staff_private.finality_database_identity f WHERE singleton FOR SHARE;
 SELECT decode(m.frozen_snapshot->>'validation_bytes','base64') INTO validation_bytes FROM ctt_staff_private.review_batch_members m WHERE m.attempt_id=p_attempt;
 IF pin IS NULL OR validation_bytes IS NULL THEN RAISE EXCEPTION 'CTT batch finality input denied' USING ERRCODE='42501';END IF;
 RETURN QUERY SELECT pin,d.oid::bigint,pg_current_xact_id()::text,floor(extract(epoch FROM clock_timestamp()))::bigint,
 src.expires_at_seconds,ctt_staff_private.batch_json(validation_bytes) FROM pg_database d WHERE d.datname=current_database();
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_record_canonical_result(p_attempt uuid,p_result uuid,p_hash text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE src record;team uuid;expected text;
BEGIN
 SELECT * INTO src FROM ctt_staff_private.batch_current_writer_input(p_attempt);
 IF src IS NULL THEN RAISE EXCEPTION 'CTT batch result denied' USING ERRCODE='42501';END IF;
 PERFORM ctt_staff_private.validate_capture_finality_rows(p_attempt,true);
 SELECT (ctt_staff_private.batch_json(src.scope_bytes)->>'team_id')::uuid,
 'sha256:'||encode(public.digest(p.gate_envelope,'sha256'),'hex') INTO team,expected
 FROM ctt_staff_private.capture_payloads p WHERE p.attempt_id=p_attempt;
 IF p_hash IS DISTINCT FROM expected THEN RAISE EXCEPTION 'CTT batch result proof denied' USING ERRCODE='42501';END IF;
 INSERT INTO ctt_staff_private.capture_results(attempt_id,result_id,team_id,authorization_hash) VALUES(p_attempt,p_result,team,p_hash);
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_session_commit_authorized(p_old jsonb,p_new jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE item record;hits int:=0;
BEGIN
 FOR item IN SELECT r.*,m.frozen_snapshot,c.issued_at approval_at
 FROM ctt_staff_private.capture_results r JOIN ctt_staff_private.review_batch_members m USING(attempt_id)
 JOIN ctt_staff_private.batch_writer_contexts w USING(attempt_id)
 JOIN ctt_staff_private.review_batch_child_authorizations a USING(authorization_id)
 JOIN ctt_staff_private.review_batch_consents c USING(consent_id)
 WHERE w.pg_top_xid=pg_current_xact_id() AND w.backend_pid=pg_backend_pid() AND w.writer_role=session_user
 AND r.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint LOOP
 IF ctt_staff_private.batch_json(decode(item.frozen_snapshot->>'scope_bytes','base64'))->>'session_id' IS DISTINCT FROM p_old->>'id' THEN CONTINUE;END IF;
 PERFORM ctt_staff_private.batch_current_writer_input(item.attempt_id);
 PERFORM ctt_staff_private.validate_capture_finality_rows(item.attempt_id,true);
 IF (p_old-ARRAY['status','committed_team_id','approved_at','committed_at']) IS NOT DISTINCT FROM
 (p_new-ARRAY['status','committed_team_id','approved_at','committed_at'])
 AND p_old->'committed_team_id'='null'::jsonb AND p_new->>'status'='committed'
 AND p_new->>'committed_team_id'=item.team_id::text
 AND (p_new->>'approved_at')::timestamp=(item.approval_at AT TIME ZONE 'UTC')
 AND (p_new->>'committed_at')::timestamp=(item.recorded_at AT TIME ZONE 'UTC') THEN hits:=hits+1;END IF;
 END LOOP;RETURN hits=1;
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_commit_review_session(p_attempt uuid,p_team uuid,p_recorded timestamptz)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE item record;
BEGIN
 SELECT (ctt_staff_private.batch_json(decode(m.frozen_snapshot->>'scope_bytes','base64'))->>'session_id')::uuid session_id,
 c.issued_at approval_at INTO item FROM ctt_staff_private.review_batch_members m
 JOIN ctt_staff_private.batch_writer_contexts w USING(attempt_id)
 JOIN ctt_staff_private.review_batch_child_authorizations a USING(authorization_id)
 JOIN ctt_staff_private.review_batch_consents c USING(consent_id)
 WHERE m.attempt_id=p_attempt AND w.pg_top_xid=pg_current_xact_id() AND w.backend_pid=pg_backend_pid() AND w.writer_role=session_user;
 IF item.session_id IS NULL THEN RAISE EXCEPTION 'CTT batch session denied' USING ERRCODE='42501';END IF;
 UPDATE public.copa_telmex_registration_review_sessions SET status='committed',committed_team_id=p_team,
 approved_at=item.approval_at AT TIME ZONE 'UTC',committed_at=p_recorded AT TIME ZONE 'UTC'
 WHERE id=item.session_id AND committed_team_id IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'CTT batch session already committed' USING ERRCODE='42501';END IF;
END $fn$;
CREATE FUNCTION ctt_staff_private.batch_lock_legacy_capture_identity(p_attempt uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE identity jsonb;scope jsonb;fresh jsonb;ci ctt_staff_private.review_batch_capture_identities%ROWTYPE;
BEGIN
 -- Acquire shared global dispatch before source identity, before the old anchor.
 PERFORM 1 FROM ctt_staff_private.review_batch_dispatch_slot WHERE singleton FOR SHARE;
 SELECT ctt_staff_private.batch_json(a.scope_bytes) INTO scope FROM ctt_staff_private.capture_attempt_anchors a WHERE attempt_id=p_attempt;
 IF scope IS NULL THEN RAISE EXCEPTION 'CTT individual anchor absent' USING ERRCODE='42501';END IF;
 PERFORM ctt_staff_private.batch_lock_capture_source_pages((scope->>'edition_id')::uuid,
 (SELECT jsonb_agg(jsonb_build_object('pdf_sha256',scope->>'pdf_sha256','pdf_page',p->'pdf_page') ORDER BY (p->>'page_index')::int)
 FROM jsonb_array_elements(scope->'source_pages') p),p_attempt);

 identity:=ctt_staff_private.batch_legacy_capture_identity((scope->>'session_id')::uuid);
 IF identity->>'status'='none' THEN RETURN;END IF;
 IF identity->>'status' IS DISTINCT FROM 'exact' THEN RAISE EXCEPTION 'CTT capture identity ambiguous' USING ERRCODE='42501';END IF;
 -- Deterministic identity advisory lock also covers a not-yet-created batch row.
 PERFORM pg_advisory_xact_lock(1129600051,hashtext((identity->>'tenant_id')||':'||(identity->>'edition_id')||':'||(identity->>'document_id')));
 SELECT * INTO ci FROM ctt_staff_private.review_batch_capture_identities WHERE tenant_id=identity->>'tenant_id'
 AND edition_id=(identity->>'edition_id')::uuid AND document_id=identity->>'document_id' FOR UPDATE;
 fresh:=ctt_staff_private.batch_legacy_capture_identity((scope->>'session_id')::uuid);
 IF fresh IS DISTINCT FROM identity OR ci.committed_team_id IS NOT NULL THEN RAISE EXCEPTION 'CTT capture identity already consumed' USING ERRCODE='42501';END IF;
END $fn$;
-- Preserve original functions verbatim as fallback; add only explicit typed paths.
CREATE TABLE ctt_staff_private.batch_writer_baseline(signature text PRIMARY KEY,original_definition text NOT NULL,installed_hash text NOT NULL);
DO $extend$
DECLARE sig text;definition text;prefix text;changed text;
BEGIN
 FOR sig,prefix IN SELECT * FROM (VALUES
 ('ctt_staff_private.current_exact_capture_row(text,uuid)',E'  IF ctt_staff_private.batch_exact_capture_row(p_table,p_id) IS NOT NULL THEN RETURN ctt_staff_private.batch_exact_capture_row(p_table,p_id);END IF;\n FOR candidate IN SELECT a.attempt_id FROM ctt_staff_private.capture_attempt_anchors a JOIN ctt_staff_private.review_challenge_consumptions c USING(challenge_id) WHERE c.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint LOOP PERFORM ctt_staff_private.batch_lock_legacy_capture_identity(candidate.attempt_id);END LOOP;'),
 ('ctt_staff_private.check_capture_finality_proof(uuid)',E' IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_registry registry WHERE registry.attempt_id=p_attempt_id AND registry.kind=''batch_v3'') THEN RETURN ctt_staff_private.batch_check_capture_finality_proof(p_attempt_id);END IF;'),
 ('ctt_staff_private.validate_capture_finality_rows(uuid,boolean)',E' IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_registry registry WHERE registry.attempt_id=p_attempt_id AND registry.kind=''batch_v3'') THEN PERFORM ctt_staff_private.batch_validate_capture_finality_rows(p_attempt_id,p_complete);RETURN;END IF;'),
 ('ctt_staff_private.load_capture_finality_context(uuid)',E' IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_registry registry WHERE registry.attempt_id=p_attempt_id AND registry.kind=''batch_v3'') THEN RETURN QUERY SELECT * FROM ctt_staff_private.batch_load_finality_context(p_attempt_id);RETURN;END IF;'),
 ('ctt_staff_private.record_capture_result(uuid,uuid,text)',E' IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_registry registry WHERE registry.attempt_id=p_attempt_id AND registry.kind=''batch_v3'') THEN PERFORM ctt_staff_private.batch_record_canonical_result(p_attempt_id,p_result_id,p_authorization_hash);RETURN;END IF;\n PERFORM ctt_staff_private.batch_lock_legacy_capture_identity(p_attempt_id);'),
 ('ctt_staff_private.review_session_commit_authorized(jsonb,jsonb)',E' IF ctt_staff_private.batch_session_commit_authorized(p_old,p_new) THEN RETURN true;END IF;'),
 ('ctt_staff_private.commit_proposal_review_session()',E' IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_registry WHERE attempt_id=NEW.attempt_id AND kind=''batch_v3'') THEN PERFORM ctt_staff_private.batch_commit_review_session(NEW.attempt_id,NEW.team_id,NEW.recorded_at);RETURN NEW;END IF;'),
 ('ctt_staff_private.guard_legacy_v2_boundary()',E' IF TG_OP=''INSERT'' AND TG_TABLE_NAME IN(''copa_telmex_registration_review_sessions'',''copa_telmex_registration_review_drafts'',''copa_telmex_registration_review_assets'') AND ctt_staff_private.batch_preparation_row_allowed(TG_TABLE_NAME,to_jsonb(NEW)) THEN RETURN NEW;END IF;'),
 ('ctt_staff_private.deny_staff_write()',E' IF TG_OP=''INSERT'' AND ctt_staff_private.batch_exact_capture_row(TG_TABLE_NAME,NEW.id) IS NOT NULL THEN\n IF to_jsonb(NEW) IS DISTINCT FROM to_jsonb(jsonb_populate_record(NULL::public.copa_telmex_team_staff,ctt_staff_private.batch_exact_capture_row(TG_TABLE_NAME,NEW.id))) THEN RAISE EXCEPTION ''CTT batch staff row mismatch'' USING ERRCODE=''42501'';END IF;RETURN NEW;END IF;')
 ) x(signature,addition) LOOP
 definition:=pg_get_functiondef(to_regprocedure(sig));
 IF definition IS NULL OR length(definition)-length(replace(definition,E'BEGIN\n',''))<length(E'BEGIN\n') THEN RAISE EXCEPTION 'CTT batch baseline absent';END IF;
 -- Only the outer BEGIN is replaced. All old declarations and body remain.
 changed:=regexp_replace(definition,E'BEGIN\n',E'BEGIN\n'||prefix||E'\n');
 INSERT INTO ctt_staff_private.batch_writer_baseline(signature,original_definition,installed_hash) VALUES(sig,definition,md5(changed));
 EXECUTE changed;
 END LOOP;
END $extend$;
DO $secure$
DECLARE item record;
BEGIN
 FOR item IN SELECT c.oid::regclass rel FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='ctt_staff_private' AND c.relname IN('capture_attempt_registry','batch_capture_payloads','batch_writer_contexts','batch_writer_baseline') LOOP
 EXECUTE format('ALTER TABLE %s OWNER TO samchat_ctt_staff_owner',item.rel);
 EXECUTE format('REVOKE ALL ON %s FROM PUBLIC,devnous_user',item.rel);
 END LOOP;
 FOR item IN SELECT p.oid::regprocedure sig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='ctt_staff_private' AND p.proname LIKE 'batch_%' LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO samchat_ctt_staff_owner',item.sig);
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,devnous_user',item.sig);
 END LOOP;
END $secure$;

DO $immutability$
DECLARE item text;
BEGIN
 FOREACH item IN ARRAY ARRAY['capture_attempt_registry','batch_capture_payloads','batch_writer_contexts'] LOOP
 EXECUTE format('CREATE TRIGGER ctt_batch_writer_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.%I FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',item);
 EXECUTE format('CREATE TRIGGER ctt_batch_writer_no_truncate BEFORE TRUNCATE ON ctt_staff_private.%I FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',item);
 END LOOP;
END $immutability$;
CREATE FUNCTION ctt_staff_private.batch_require_canonical_consumption()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_registry WHERE attempt_id=NEW.attempt_id AND kind='batch_v3') THEN RETURN NEW;END IF;
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_consumptions used
 JOIN ctt_staff_private.batch_writer_contexts ctx ON ctx.attempt_id=used.attempt_id AND ctx.authorization_id=used.authorization_id
 WHERE used.attempt_id=NEW.attempt_id AND used.result_id=NEW.result_id AND used.team_id=NEW.team_id
 AND ctx.pg_top_xid=pg_current_xact_id() AND ctx.backend_pid=pg_backend_pid() AND ctx.writer_role=session_user
 AND used.xmin::text::bigint=mod(pg_current_xact_id()::text::numeric,4294967296)::bigint) THEN
 RAISE EXCEPTION 'CTT batch canonical result lacks consumption' USING ERRCODE='42501';END IF;
 PERFORM ctt_staff_private.batch_current_writer_input(NEW.attempt_id);
 RETURN NEW;
END $fn$;
ALTER FUNCTION ctt_staff_private.batch_require_canonical_consumption() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.batch_require_canonical_consumption() FROM PUBLIC,devnous_user;
CREATE CONSTRAINT TRIGGER ctt_batch_result_consumption_commit AFTER INSERT ON ctt_staff_private.capture_results
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.batch_require_canonical_consumption();

CREATE FUNCTION ctt_staff_private.batch_record_result_source_pages()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE scope jsonb;pages jsonb;operation uuid;
BEGIN
 IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_registry WHERE attempt_id=NEW.attempt_id AND kind='batch_v3') THEN
 SELECT ctt_staff_private.batch_json(i.scope_bytes) INTO scope FROM ctt_staff_private.batch_current_writer_input(NEW.attempt_id) i;
 operation:=(scope->>'operation_id')::uuid;
 SELECT jsonb_agg(jsonb_build_object('pdf_sha256',p->>'pdf_sha256','pdf_page',p->'pdf_page') ORDER BY (p->>'page_index')::int)
 INTO pages FROM jsonb_array_elements(scope->'source_pages') p;
 ELSE
 SELECT ctt_staff_private.batch_json(a.scope_bytes) INTO scope FROM ctt_staff_private.capture_attempt_anchors a WHERE attempt_id=NEW.attempt_id;
 operation:=NEW.attempt_id;
 SELECT jsonb_agg(jsonb_build_object('pdf_sha256',scope->>'pdf_sha256','pdf_page',p->'pdf_page') ORDER BY (p->>'page_index')::int)
 INTO pages FROM jsonb_array_elements(scope->'source_pages') p;
 END IF;
 IF scope IS NULL OR pages IS NULL THEN RAISE EXCEPTION 'CTT capture source identity absent' USING ERRCODE='42501';END IF;
 PERFORM ctt_staff_private.batch_record_page_consumption((scope->>'edition_id')::uuid,pages,operation,NEW.attempt_id,NEW.result_id,NEW.team_id);
 RETURN NEW;
END $fn$;
ALTER FUNCTION ctt_staff_private.batch_record_result_source_pages() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.batch_record_result_source_pages() FROM PUBLIC,devnous_user;
CREATE TRIGGER ctt_batch_source_result_consume AFTER INSERT ON ctt_staff_private.capture_results
 FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.batch_record_result_source_pages();

CREATE FUNCTION ctt_staff_private.batch_load_finality_gate_context(p_attempt uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE answer jsonb;
BEGIN
 -- One committed MVCC projection. No row/advisory locks: the canonical writer
 -- synchronously awaits this RPC while holding its finality transaction locks.
 SELECT jsonb_build_object('database_identity',dbpin.database_identity,'database_oid',db.oid::bigint,
 'scope_bytes',m.frozen_snapshot->'scope_bytes','review_bytes',replace(encode(staged.review_bytes,'base64'),E'\n',''),
 'pending_effect_bytes',replace(encode(staged.effect_bytes,'base64'),E'\n',''),'signed_review',replace(encode(a.proof_bytes,'base64'),E'\n',''),
 'preauthorization_bytes',replace(encode(staged.preauthorization_bytes,'base64'),E'\n',''),'evidence_bytes',replace(encode(staged.evidence_bytes,'base64'),E'\n',''),
 'validation_bytes',m.frozen_snapshot->'validation_bytes','source_context',jsonb_build_object('tenant_id',r.tenant_id,'edition_id',r.edition_id::text,'roster_slug',manifest.roster_slug)) INTO answer
 FROM ctt_staff_private.review_batch_members m
 JOIN ctt_staff_private.review_batch_runs r USING(run_id)
 JOIN ctt_staff_private.review_batch_jobs j ON j.member_id=m.member_id
 JOIN ctt_staff_private.review_batch_blocks b ON b.block_id=j.block_id AND b.run_id=r.run_id AND b.qualification_id=r.qualification_id
 JOIN ctt_staff_private.review_batch_consents c ON c.consent_id=b.consent_id
 CROSS JOIN LATERAL (SELECT convert_from(c.proof_bytes,'UTF8')::jsonb->'body' body) parent
 JOIN ctt_staff_private.review_batch_child_authorizations a ON a.authorization_id=j.authorization_id AND a.member_id=m.member_id AND a.attempt_id=m.attempt_id AND a.authorization_generation=m.authorization_generation AND a.dispatch_generation=b.dispatch_generation
 JOIN ctt_staff_private.review_batch_dispatch_slot slot ON slot.singleton AND slot.member_id=m.member_id AND slot.worker_id=a.worker_id AND slot.lease_epoch=a.lease_epoch
 JOIN ctt_staff_private.batch_capture_payloads staged ON staged.attempt_id=a.attempt_id AND staged.authorization_id=a.authorization_id AND staged.nonce=a.nonce
 JOIN ctt_staff_private.review_batch_verifications v ON v.verification_hash=convert_from(a.proof_bytes,'UTF8')::jsonb->'body'->>'verification_hash' AND v.member_id=m.member_id AND v.qualification_id=b.qualification_id
 JOIN ctt_staff_private.review_batch_services verifier ON verifier.service_role=v.service_role AND verifier.kind='verifier' AND verifier.profile_hash=r.profile_hash
 JOIN ctt_staff_private.review_batch_services issuer ON issuer.service_role=c.issuer_role AND issuer.kind='issuer'
 JOIN ctt_staff_private.review_batch_services svc ON svc.service_role=session_user AND svc.kind='workflow' AND svc.backend_pin=r.backend_pin
 JOIN ctt_staff_private.review_batch_capabilities cap ON cap.backend_pin=r.backend_pin AND cap.account_id=r.account_id AND cap.principal_id=r.principal_id AND cap.edition_id=r.edition_id AND cap.tenant_id=r.tenant_id AND cap.manifest_sha256=m.manifest_sha256 AND cap.policy_hash=m.policy_hash AND cap.profile_hash=r.profile_hash AND cap.revoked_at IS NULL
 JOIN ctt_staff_private.review_principals principal ON principal.principal_id=r.principal_id
 JOIN ctt_staff_private.review_credentials cred ON cred.credential_id=cap.credential_id AND cred.principal_id=r.principal_id
 JOIN ctt_staff_private.review_account_links account ON account.backend_pin=r.backend_pin AND account.account_id=r.account_id AND account.edition_id=r.edition_id AND account.tenant_id=r.tenant_id AND account.principal_id=r.principal_id
 JOIN ctt_staff_private.review_source_manifests_v2 manifest ON manifest.manifest_sha256=m.manifest_sha256 AND manifest.edition_id=r.edition_id AND manifest.tenant_id=r.tenant_id
 CROSS JOIN ctt_staff_private.finality_database_identity dbpin JOIN pg_catalog.pg_database db ON db.datname=current_database()
 JOIN pg_catalog.pg_roles login ON login.rolname=session_user
 WHERE m.attempt_id=p_attempt AND dbpin.singleton AND b.status='active' AND c.expires_at>statement_timestamp() AND a.expires_at>statement_timestamp() AND j.status='leased' AND j.worker_id=a.worker_id AND j.lease_epoch=a.lease_epoch AND slot.lease_until>statement_timestamp()
 AND svc.revoked_at IS NULL AND svc.valid_from<=statement_timestamp() AND svc.valid_until>statement_timestamp()
 AND issuer.revoked_at IS NULL AND issuer.valid_from<=statement_timestamp() AND issuer.valid_until>statement_timestamp() AND issuer.authority_version=(parent.body->>'service_version')::bigint AND issuer.signing_kid=parent.body->>'kid'
 AND verifier.revoked_at IS NULL AND verifier.valid_from<=statement_timestamp() AND verifier.valid_until>statement_timestamp() AND to_timestamp((convert_from(v.proof_bytes,'UTF8')::jsonb->'body'->>'expires_at')::bigint)>statement_timestamp()
 AND cap.valid_from<=statement_timestamp() AND cap.valid_until>statement_timestamp() AND principal.active AND account.active AND cred.revoked_at IS NULL
 AND cap.authority_version=(parent.body->>'capability_version')::bigint AND cap.principal_version=principal.authority_version AND principal.authority_version=(parent.body->>'principal_version')::bigint
 AND cap.account_version=account.authority_version AND account.authority_version=(parent.body->>'account_version')::bigint AND cap.credential_version=cred.authority_version AND cred.authority_version=(parent.body->>'credential_version')::bigint
 AND account.actor_role=cap.actor_role AND parent.body->>'credential_id'=replace(encode(cred.credential_id,'base64'),E'\n','') AND parent.body->>'auth_profile'=cred.auth_profile AND parent.body->>'auth_policy_hash'=cred.auth_policy_hash AND (parent.body->>'backup_eligible')::boolean=cred.backup_eligible AND (parent.body->>'backup_state')::boolean=cred.backup_state
 AND staged.review_bytes=decode(m.frozen_snapshot->>'review_bytes','base64') AND staged.evidence_bytes=decode(m.frozen_snapshot->>'evidence_bytes','base64')
 AND ctt_staff_private.batch_assert_source_document(r.edition_id,m.manifest_sha256,m.document_id,m.source_payload_hash)
 AND ctt_staff_private.batch_assert_source_pages(m.manifest_sha256,m.document_id,m.source_pages)
 AND session_user<>'devnous_user' AND login.rolcanlogin AND NOT(login.rolsuper OR login.rolcreatedb OR login.rolcreaterole OR login.rolreplication OR login.rolbypassrls) AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE member=login.oid);
 IF answer IS NULL THEN RAISE EXCEPTION 'CTT batch committed finality context unavailable' USING ERRCODE='42501';END IF;RETURN answer;
END $fn$;

-- Frozen evidence is append-only; only operational heads and lease rows mutate.
DO $immutable$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['review_batch_challenges','review_batch_consents','review_batch_qualification_activations','review_batch_child_authorizations','review_batch_consumptions','review_batch_corrections','review_batch_page_consumptions'] LOOP
 EXECUTE format('CREATE TRIGGER batch_evidence_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.%I FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',t);
 END LOOP;
END $immutable$;
DO $ownership$
DECLARE item record;
BEGIN
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='ctt_staff_private' AND c.relname LIKE 'review_batch_%' AND c.relkind='r' LOOP
 EXECUTE format('ALTER TABLE ctt_staff_private.%I OWNER TO samchat_ctt_staff_owner',item.relname);EXECUTE format('REVOKE ALL ON ctt_staff_private.%I FROM PUBLIC,devnous_user',item.relname);
 END LOOP;
 FOR item IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='ctt_staff_private' AND p.proname LIKE 'batch_%' LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO samchat_ctt_staff_owner',item.signature);EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,devnous_user',item.signature);
 END LOOP;
END $ownership$;
COMMIT;
