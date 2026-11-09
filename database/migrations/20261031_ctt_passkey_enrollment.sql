-- Isolated supervised enrollment. No service/operator, link, key or faculty seed.
BEGIN;
DO $preflight$
DECLARE owner_id oid; item text;
BEGIN
 SELECT oid INTO owner_id FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner'
 AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
 AND NOT rolreplication AND NOT rolbypassrls;
 IF owner_id IS NULL OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id)
 OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE')
 OR current_setting('max_prepared_transactions')::int<>0 THEN RAISE EXCEPTION 'CTT enrollment isolation invalid'; END IF;
 FOREACH item IN ARRAY ARRAY['review_principals','review_credentials','review_faculties','review_authority_audit'] LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=to_regclass('ctt_staff_private.'||item)
 AND c.relowner=owner_id AND NOT c.relrowsecurity
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a WHERE a.grantee<>owner_id)) THEN
 RAISE EXCEPTION 'CTT enrollment protected dependency invalid'; END IF;
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid='ctt_staff_private.review_credentials'::regclass AND attname='auth_profile') THEN
 RAISE EXCEPTION 'CTT enrollment platform profile missing'; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
 JOIN pg_catalog.pg_roles r ON r.oid=e.extowner JOIN pg_catalog.pg_proc p ON p.oid=d.objid
 JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
 WHERE e.extname='pgcrypto' AND r.rolsuper AND n.nspname='public' AND p.proowner=e.extowner
 AND d.objid='public.digest(bytea,text)'::regprocedure AND d.deptype='e') THEN
 RAISE EXCEPTION 'CTT enrollment digest dependency invalid'; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid='ctt_staff_private.reject_review_evidence_mutation()'::regprocedure
 AND p.proowner=owner_id AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]) THEN
 RAISE EXCEPTION 'CTT enrollment immutable dependency invalid'; END IF;
END
$preflight$;

CREATE TABLE ctt_staff_private.review_services(
 service_role name PRIMARY KEY, backend_pin text NOT NULL CHECK(backend_pin~'^[a-z0-9][a-z0-9_-]{0,79}$'),
 not_before timestamptz NOT NULL,not_after timestamptz NOT NULL,active boolean NOT NULL DEFAULT false,
 authority_version bigint NOT NULL DEFAULT 1 CHECK(authority_version>0),CHECK(not_after>not_before)
);
CREATE TABLE ctt_staff_private.review_enrollment_operators(LIKE ctt_staff_private.review_services INCLUDING ALL);
CREATE TABLE ctt_staff_private.review_account_links(
 backend_pin text NOT NULL CHECK(backend_pin~'^[a-z0-9][a-z0-9_-]{0,79}$'),account_id uuid NOT NULL,
 tenant_id text NOT NULL CHECK(tenant_id~'^[a-z0-9][a-z0-9_-]{0,79}$'),edition_id uuid NOT NULL,
 principal_id uuid NOT NULL REFERENCES ctt_staff_private.review_principals ON DELETE RESTRICT,
 actor_role text NOT NULL CHECK(actor_role~'^[a-z0-9][a-z0-9_-]{0,79}$'),active boolean NOT NULL DEFAULT false,
 authority_version bigint NOT NULL DEFAULT 1 CHECK(authority_version>0),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(backend_pin,account_id,tenant_id,edition_id)
);
CREATE TABLE ctt_staff_private.review_enrollment_invitations(
 invitation_id uuid PRIMARY KEY,backend_pin text NOT NULL,account_id uuid NOT NULL,
 tenant_id text NOT NULL,edition_id uuid NOT NULL,principal_id uuid NOT NULL REFERENCES ctt_staff_private.review_principals ON DELETE RESTRICT,
 actor_role text NOT NULL CHECK(actor_role~'^[a-z0-9][a-z0-9_-]{0,79}$'),user_handle bytea NOT NULL CHECK(octet_length(user_handle) BETWEEN 1 AND 64),
 valid_from timestamptz NOT NULL,valid_until timestamptz NOT NULL,approval_ref text NOT NULL CHECK(approval_ref~'^sha256:[0-9a-f]{64}$'),
 created_by name NOT NULL DEFAULT session_user,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),CHECK(valid_until>valid_from)
);
CREATE TABLE ctt_staff_private.review_enrollment_attempts(
 enrollment_id uuid PRIMARY KEY,invitation_id uuid NOT NULL UNIQUE REFERENCES ctt_staff_private.review_enrollment_invitations ON DELETE RESTRICT,
 challenge_bytes bytea NOT NULL UNIQUE CHECK(octet_length(challenge_bytes)=32),issued_at_seconds bigint NOT NULL,
 expires_at_seconds bigint NOT NULL,existing_credentials bytea[] NOT NULL,created_by name NOT NULL DEFAULT session_user,
 CHECK(expires_at_seconds>issued_at_seconds AND expires_at_seconds-issued_at_seconds<=300)
);
CREATE TABLE ctt_staff_private.review_enrollment_candidates(
 enrollment_id uuid PRIMARY KEY REFERENCES ctt_staff_private.review_enrollment_attempts ON DELETE RESTRICT,
 response_hash text NOT NULL CHECK(response_hash~'^sha256:[0-9a-f]{64}$'),
 credential_id bytea NOT NULL UNIQUE CHECK(octet_length(credential_id) BETWEEN 1 AND 1024),
 attested_credential bytea NOT NULL CHECK(octet_length(attested_credential) BETWEEN 1 AND 16384),
 sign_count bigint NOT NULL CHECK(sign_count BETWEEN 0 AND 4294967295),backup_eligible boolean NOT NULL,backup_state boolean NOT NULL,
 fingerprint text NOT NULL CHECK(fingerprint~'^sha256:[0-9a-f]{64}$'),recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(backup_eligible OR NOT backup_state)
);
CREATE TABLE ctt_staff_private.review_enrollment_activations(
 enrollment_id uuid PRIMARY KEY REFERENCES ctt_staff_private.review_enrollment_candidates ON DELETE RESTRICT,
 credential_record_id uuid NOT NULL REFERENCES ctt_staff_private.review_credentials(credential_record_id) ON DELETE RESTRICT,
 fingerprint text NOT NULL,faculty text NOT NULL CHECK(faculty='capture_staff_v2'),
 valid_from timestamptz NOT NULL,valid_until timestamptz NOT NULL,approval_ref text NOT NULL CHECK(approval_ref~'^sha256:[0-9a-f]{64}$'),
 activated_by name NOT NULL DEFAULT session_user,activated_at timestamptz NOT NULL DEFAULT clock_timestamp(),CHECK(valid_until>valid_from)
);
CREATE TABLE ctt_staff_private.review_enrollment_audit(
 audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,event text NOT NULL CHECK(event IN('invitation','activation','revocation','service_change','link_change')),
 enrollment_id uuid,principal_id uuid,details jsonb NOT NULL,recorded_by name NOT NULL DEFAULT session_user,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

DO $tables$
DECLARE name text;
BEGIN
 FOREACH name IN ARRAY ARRAY['review_services','review_enrollment_operators','review_account_links','review_enrollment_invitations',
 'review_enrollment_attempts','review_enrollment_candidates','review_enrollment_activations','review_enrollment_audit'] LOOP
 EXECUTE format('ALTER TABLE ctt_staff_private.%I OWNER TO samchat_ctt_staff_owner',name);
 EXECUTE format('REVOKE ALL ON ctt_staff_private.%I FROM PUBLIC,devnous_user',name);
 IF name NOT IN('review_services','review_enrollment_operators','review_account_links') THEN
 EXECUTE format('CREATE TRIGGER ctt_enrollment_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.%I FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',name);
 END IF;
 EXECUTE format('CREATE TRIGGER ctt_enrollment_no_truncate BEFORE TRUNCATE ON ctt_staff_private.%I FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation()',name);
 END LOOP;
END
$tables$;
REVOKE ALL ON SEQUENCE ctt_staff_private.review_enrollment_audit_audit_id_seq FROM PUBLIC,devnous_user;

CREATE FUNCTION ctt_staff_private.guard_review_service_configuration()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'CTT enrollment evidence immutable' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' THEN
 IF NEW.authority_version<=OLD.authority_version
 OR (to_jsonb(NEW)-ARRAY['active','authority_version','not_before','not_after'])
 IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['active','authority_version','not_before','not_after']) THEN
 RAISE EXCEPTION 'CTT enrollment configuration version invalid' USING ERRCODE='42501'; END IF;
 END IF;
 INSERT INTO ctt_staff_private.review_enrollment_audit(event,details) VALUES(CASE WHEN TG_TABLE_NAME='review_account_links' THEN 'link_change' ELSE 'service_change' END,
 jsonb_build_object('table',TG_TABLE_NAME,'operation',TG_OP,'version',NEW.authority_version));
 RETURN NEW;
END
$fn$;
CREATE TRIGGER ctt_review_service_configuration BEFORE INSERT OR UPDATE OR DELETE ON ctt_staff_private.review_services FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.guard_review_service_configuration();
CREATE TRIGGER ctt_review_operator_configuration BEFORE INSERT OR UPDATE OR DELETE ON ctt_staff_private.review_enrollment_operators FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.guard_review_service_configuration();
CREATE TRIGGER ctt_review_link_configuration BEFORE INSERT OR UPDATE OR DELETE ON ctt_staff_private.review_account_links FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.guard_review_service_configuration();

CREATE FUNCTION ctt_staff_private.check_review_service_identity()
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE actor pg_catalog.pg_roles%ROWTYPE;
BEGIN
 SELECT * INTO actor FROM pg_catalog.pg_roles WHERE rolname=session_user;
 IF actor.oid IS NULL OR actor.rolname IN('devnous_user','samchat_ctt_staff_owner') OR NOT actor.rolcanlogin
 OR actor.rolsuper OR actor.rolcreatedb OR actor.rolcreaterole OR actor.rolreplication OR actor.rolbypassrls
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE member=actor.oid OR roleid=actor.oid)
 OR pg_catalog.pg_has_role('devnous_user',actor.oid,'MEMBER') OR pg_catalog.pg_has_role(actor.oid,'devnous_user','MEMBER')
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles b WHERE b.rolname IN('pg_read_all_data','pg_write_all_data','pg_read_server_files','pg_write_server_files','pg_execute_server_program') AND pg_catalog.pg_has_role(actor.oid,b.oid,'MEMBER'))
 OR current_setting('max_prepared_transactions')::int<>0 THEN RAISE EXCEPTION 'CTT review service denied' USING ERRCODE='42501'; END IF;
END
$fn$;
CREATE FUNCTION ctt_staff_private.current_review_service()
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text;
BEGIN
 PERFORM ctt_staff_private.check_review_service_identity();
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_enrollment_operators WHERE service_role=session_user AND active) THEN
 RAISE EXCEPTION 'CTT review service operator separation required' USING ERRCODE='42501'; END IF;
 SELECT backend_pin INTO pin FROM ctt_staff_private.review_services WHERE service_role=session_user AND active
 AND clock_timestamp()>=not_before AND clock_timestamp()<not_after FOR SHARE;
 IF pin IS NULL THEN RAISE EXCEPTION 'CTT review service denied' USING ERRCODE='42501'; END IF;
 RETURN pin;
END
$fn$;
CREATE FUNCTION ctt_staff_private.current_enrollment_operator()
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text;
BEGIN
 PERFORM ctt_staff_private.check_review_service_identity();
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_services WHERE service_role=session_user AND active) THEN
 RAISE EXCEPTION 'CTT enrollment operator service separation required' USING ERRCODE='42501'; END IF;
 SELECT backend_pin INTO pin FROM ctt_staff_private.review_enrollment_operators WHERE service_role=session_user AND active
 AND clock_timestamp()>=not_before AND clock_timestamp()<not_after FOR SHARE;
 IF pin IS NULL THEN RAISE EXCEPTION 'CTT enrollment operator denied' USING ERRCODE='42501'; END IF;
 RETURN pin;
END
$fn$;

CREATE FUNCTION ctt_staff_private.admit_passkey_enrollment_invitation(p_id uuid,p_account uuid,p_principal uuid,p_tenant text,p_edition uuid,p_role text,p_handle bytea,p_from timestamptz,p_until timestamptz,p_approval text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text; prior record; existing_handle bytea; chosen_handle bytea;
BEGIN
 pin:=ctt_staff_private.current_enrollment_operator();
 IF p_id IS NULL OR p_account IS NULL OR p_principal IS NULL OR p_edition IS NULL
 OR NOT coalesce(p_tenant~'^[a-z0-9][a-z0-9_-]{0,79}$',false) OR NOT coalesce(p_role~'^[a-z0-9][a-z0-9_-]{0,79}$',false)
 OR NOT coalesce(p_approval~'^sha256:[0-9a-f]{64}$',false) OR (p_handle IS NOT NULL AND octet_length(p_handle) NOT BETWEEN 1 AND 64)
 OR p_from IS NULL OR p_until IS NULL OR p_until<=p_from OR p_until<=clock_timestamp() THEN RAISE EXCEPTION 'CTT enrollment invitation denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM ctt_staff_private.review_enrollment_invitations WHERE invitation_id=p_id;
 IF FOUND THEN
 IF (prior.backend_pin,prior.account_id,prior.principal_id,prior.tenant_id,prior.edition_id,prior.actor_role,prior.valid_from,prior.valid_until,prior.approval_ref)
 IS DISTINCT FROM (pin,p_account,p_principal,p_tenant,p_edition,p_role,p_from,p_until,p_approval)
 OR (p_handle IS NOT NULL AND prior.user_handle IS DISTINCT FROM p_handle) THEN RAISE EXCEPTION 'CTT enrollment invitation denied' USING ERRCODE='42501'; END IF;
 RETURN p_id; END IF;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_account_links WHERE backend_pin=pin AND account_id=p_account AND tenant_id=p_tenant AND edition_id=p_edition
 AND (principal_id<>p_principal OR actor_role<>p_role)) THEN RAISE EXCEPTION 'CTT enrollment binding denied' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.review_principals(principal_id) VALUES(p_principal) ON CONFLICT DO NOTHING;
 PERFORM 1 FROM ctt_staff_private.review_principals WHERE principal_id=p_principal FOR UPDATE;
 SELECT user_handle INTO existing_handle FROM ctt_staff_private.review_credentials WHERE principal_id=p_principal LIMIT 1;
 IF existing_handle IS NULL THEN SELECT user_handle INTO existing_handle FROM ctt_staff_private.review_enrollment_invitations WHERE principal_id=p_principal ORDER BY created_at,invitation_id LIMIT 1; END IF;
 IF existing_handle IS NOT NULL AND p_handle IS NOT NULL AND existing_handle IS DISTINCT FROM p_handle THEN RAISE EXCEPTION 'CTT enrollment handle denied' USING ERRCODE='42501'; END IF;
 chosen_handle:=coalesce(existing_handle,p_handle,pg_catalog.uuid_send(pg_catalog.gen_random_uuid())||pg_catalog.uuid_send(pg_catalog.gen_random_uuid()));
 INSERT INTO ctt_staff_private.review_enrollment_invitations VALUES(p_id,pin,p_account,p_tenant,p_edition,p_principal,p_role,chosen_handle,p_from,p_until,p_approval,session_user,clock_timestamp());
 INSERT INTO ctt_staff_private.review_enrollment_audit(event,principal_id,details) VALUES('invitation',p_principal,jsonb_build_object('invitation_id',p_id,'approval_ref',p_approval));
 RETURN p_id;
END
$fn$;

CREATE FUNCTION ctt_staff_private.load_passkey_enrollment(p_account uuid,p_enrollment uuid)
RETURNS TABLE(enrollment_id uuid,invitation_id uuid,account_id uuid,principal_id uuid,tenant_id text,edition_id uuid,user_handle bytea,
 challenge_bytes bytea,issued_at_seconds bigint,expires_at_seconds bigint,existing_credentials bytea[],response_hash text,fingerprint text,status text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text;
BEGIN
 pin:=ctt_staff_private.current_review_service();
 PERFORM 1 FROM ctt_staff_private.review_enrollment_invitations i JOIN ctt_staff_private.review_enrollment_attempts a USING(invitation_id)
 WHERE a.enrollment_id=p_enrollment AND i.account_id=p_account AND i.backend_pin=pin FOR SHARE OF i;
 IF NOT FOUND THEN RETURN; END IF;
 RETURN QUERY SELECT a.enrollment_id,i.invitation_id,i.account_id,i.principal_id,i.tenant_id,i.edition_id,i.user_handle,
 a.challenge_bytes,a.issued_at_seconds,a.expires_at_seconds,a.existing_credentials,c.response_hash,c.fingerprint,
 CASE WHEN x.enrollment_id IS NOT NULL THEN CASE WHEN cr.revoked_at IS NOT NULL THEN 'REVOKED' ELSE 'ACTIVE' END
 WHEN clock_timestamp()>=i.valid_until THEN 'EXPIRED'
 WHEN c.enrollment_id IS NOT NULL THEN 'PENDING_OPERATOR_REVIEW'
 WHEN floor(extract(epoch FROM clock_timestamp()))>=a.expires_at_seconds THEN 'EXPIRED' ELSE 'AWAITING_REGISTRATION' END
 FROM ctt_staff_private.review_enrollment_attempts a JOIN ctt_staff_private.review_enrollment_invitations i USING(invitation_id)
 LEFT JOIN ctt_staff_private.review_enrollment_candidates c USING(enrollment_id)
 LEFT JOIN ctt_staff_private.review_enrollment_activations x USING(enrollment_id)
 LEFT JOIN ctt_staff_private.review_credentials cr ON cr.credential_record_id=x.credential_record_id
 WHERE a.enrollment_id=p_enrollment AND i.account_id=p_account AND i.backend_pin=pin
 FOR UPDATE OF a;
END
$fn$;
CREATE FUNCTION ctt_staff_private.begin_passkey_enrollment(p_account uuid,p_tenant text,p_edition uuid,p_enrollment uuid,p_challenge bytea,p_lifetime integer)
RETURNS TABLE(enrollment_id uuid,invitation_id uuid,account_id uuid,principal_id uuid,tenant_id text,edition_id uuid,user_handle bytea,
 challenge_bytes bytea,issued_at_seconds bigint,expires_at_seconds bigint,existing_credentials bytea[],response_hash text,fingerprint text,status text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text; invitation record; attempt uuid; issued bigint; expires bigint; credentials bytea[];
BEGIN
 pin:=ctt_staff_private.current_review_service();
 IF p_account IS NULL OR p_enrollment IS NULL OR p_edition IS NULL OR octet_length(p_challenge)<>32 OR p_lifetime NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'CTT enrollment denied' USING ERRCODE='42501'; END IF;
 SELECT i.* INTO invitation FROM ctt_staff_private.review_enrollment_invitations i
 WHERE i.backend_pin=pin AND i.account_id=p_account AND i.tenant_id=p_tenant AND i.edition_id=p_edition
 AND clock_timestamp()>=i.valid_from AND clock_timestamp()<i.valid_until ORDER BY i.created_at DESC,i.invitation_id DESC LIMIT 1 FOR UPDATE;
 IF invitation IS NULL THEN RAISE EXCEPTION 'CTT enrollment invitation required' USING ERRCODE='42501'; END IF;
 SELECT a.enrollment_id INTO attempt FROM ctt_staff_private.review_enrollment_attempts a WHERE a.invitation_id=invitation.invitation_id;
 IF attempt IS NULL THEN
 issued:=floor(extract(epoch FROM clock_timestamp()))::bigint;
 expires:=least(issued+p_lifetime,floor(extract(epoch FROM invitation.valid_until))::bigint);
 IF expires<=issued THEN RAISE EXCEPTION 'CTT enrollment expired' USING ERRCODE='42501'; END IF;
 SELECT coalesce(array_agg(c.attested_credential ORDER BY c.credential_record_id),ARRAY[]::bytea[]) INTO credentials FROM ctt_staff_private.review_credentials c WHERE c.principal_id=invitation.principal_id;
 INSERT INTO ctt_staff_private.review_enrollment_attempts VALUES(p_enrollment,invitation.invitation_id,p_challenge,issued,expires,credentials,session_user);
 attempt:=p_enrollment;
 END IF;
 RETURN QUERY SELECT * FROM ctt_staff_private.load_passkey_enrollment(p_account,attempt);
END
$fn$;

CREATE FUNCTION ctt_staff_private.record_passkey_enrollment_candidate(p_account uuid,p_enrollment uuid,p_response text,p_credential bytea,p_attested bytea,p_counter bigint,p_be boolean,p_bs boolean,p_fingerprint text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text; attempt record; candidate record; calculated text;
BEGIN
 pin:=ctt_staff_private.current_review_service();
 SELECT a.*,i.backend_pin,i.account_id,i.valid_until INTO attempt FROM ctt_staff_private.review_enrollment_attempts a
 JOIN ctt_staff_private.review_enrollment_invitations i USING(invitation_id) WHERE a.enrollment_id=p_enrollment FOR UPDATE OF a;
 IF attempt IS NULL OR attempt.backend_pin<>pin OR attempt.account_id<>p_account THEN RAISE EXCEPTION 'CTT enrollment denied' USING ERRCODE='42501'; END IF;
 calculated:='sha256:'||encode(public.digest(convert_to('samchat.ctt.enrollment-key.v1','UTF8')||decode('00','hex')||int4send(octet_length(p_credential))||p_credential||p_attested,'sha256'),'hex');
 IF calculated IS DISTINCT FROM p_fingerprint THEN RAISE EXCEPTION 'CTT enrollment fingerprint invalid' USING ERRCODE='42501'; END IF;
 SELECT * INTO candidate FROM ctt_staff_private.review_enrollment_candidates WHERE enrollment_id=p_enrollment;
 IF FOUND THEN
 IF (candidate.response_hash,candidate.credential_id,candidate.attested_credential,candidate.sign_count,candidate.backup_eligible,candidate.backup_state,candidate.fingerprint)
 IS DISTINCT FROM (p_response,p_credential,p_attested,p_counter,p_be,p_bs,p_fingerprint) THEN RAISE EXCEPTION 'CTT enrollment already used' USING ERRCODE='42501'; END IF;
 RETURN p_fingerprint; END IF;
 IF floor(extract(epoch FROM clock_timestamp()))>=attempt.expires_at_seconds OR clock_timestamp()>=attempt.valid_until
 OR EXISTS(SELECT 1 FROM ctt_staff_private.review_credentials WHERE credential_id=p_credential) THEN RAISE EXCEPTION 'CTT enrollment expired or duplicate' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.review_enrollment_candidates VALUES(p_enrollment,p_response,p_credential,p_attested,p_counter,p_be,p_bs,p_fingerprint,clock_timestamp());
 RETURN p_fingerprint;
END
$fn$;

CREATE FUNCTION ctt_staff_private.activate_passkey_enrollment(p_enrollment uuid,p_fingerprint text,p_account uuid,p_principal uuid,p_tenant text,p_edition uuid,p_role text,p_faculty text,p_from timestamptz,p_until timestamptz,p_approval text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text; item record; prior record; credential_record uuid;
BEGIN
 pin:=ctt_staff_private.current_enrollment_operator();
 SELECT i.*,c.fingerprint,c.credential_id,c.attested_credential,c.sign_count,c.backup_eligible,c.backup_state INTO item
 FROM ctt_staff_private.review_enrollment_attempts a JOIN ctt_staff_private.review_enrollment_invitations i USING(invitation_id)
 JOIN ctt_staff_private.review_enrollment_candidates c USING(enrollment_id) WHERE a.enrollment_id=p_enrollment FOR UPDATE OF a;
 IF item IS NULL OR (item.backend_pin,item.account_id,item.principal_id,item.tenant_id,item.edition_id,item.actor_role,item.fingerprint)
 IS DISTINCT FROM (pin,p_account,p_principal,p_tenant,p_edition,p_role,p_fingerprint)
 OR p_faculty IS DISTINCT FROM 'capture_staff_v2' OR p_from IS NULL OR p_until IS NULL OR p_until<=p_from
 OR NOT coalesce(p_approval~'^sha256:[0-9a-f]{64}$',false) THEN RAISE EXCEPTION 'CTT enrollment approval denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM ctt_staff_private.review_enrollment_activations WHERE enrollment_id=p_enrollment;
 IF FOUND THEN
 IF (prior.fingerprint,prior.faculty,prior.valid_from,prior.valid_until,prior.approval_ref) IS DISTINCT FROM (p_fingerprint,p_faculty,p_from,p_until,p_approval) THEN RAISE EXCEPTION 'CTT enrollment approval mismatch' USING ERRCODE='42501'; END IF;
 RETURN prior.credential_record_id; END IF;
 IF p_until<=clock_timestamp() OR clock_timestamp()>=item.valid_until THEN RAISE EXCEPTION 'CTT enrollment approval expired' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_account_links WHERE backend_pin=pin AND account_id=p_account AND tenant_id=p_tenant AND edition_id=p_edition
 AND (principal_id<>p_principal OR actor_role<>p_role)) THEN RAISE EXCEPTION 'CTT enrollment binding mismatch' USING ERRCODE='42501'; END IF;
 INSERT INTO ctt_staff_private.review_credentials(credential_id,principal_id,attested_credential,user_handle,sign_count,auth_profile,auth_policy_hash,backup_eligible,backup_state)
 VALUES(item.credential_id,p_principal,item.attested_credential,item.user_handle,item.sign_count,'platform-passkey-v1','sha256:e0a2e1ce21182519a8739ffa11aba21164d565871bf62909cf9746861dfa48d3',item.backup_eligible,item.backup_state)
 RETURNING credential_record_id INTO credential_record;
 UPDATE ctt_staff_private.review_principals SET active=true,authority_version=authority_version+1 WHERE principal_id=p_principal AND NOT active;
 INSERT INTO ctt_staff_private.review_account_links(backend_pin,account_id,tenant_id,edition_id,principal_id,actor_role,active)
 VALUES(pin,p_account,p_tenant,p_edition,p_principal,p_role,true)
 ON CONFLICT(backend_pin,account_id,tenant_id,edition_id) DO UPDATE SET active=true,authority_version=ctt_staff_private.review_account_links.authority_version+1;
 INSERT INTO ctt_staff_private.review_faculties(principal_id,tenant_id,edition_id,faculty,valid_from,valid_until)
 VALUES(p_principal,p_tenant,p_edition,p_faculty,p_from,p_until)
 ON CONFLICT(principal_id,tenant_id,edition_id,faculty) DO UPDATE SET valid_from=p_from,valid_until=p_until,revoked_at=NULL,authority_version=ctt_staff_private.review_faculties.authority_version+1;
 INSERT INTO ctt_staff_private.review_enrollment_activations VALUES(p_enrollment,credential_record,p_fingerprint,p_faculty,p_from,p_until,p_approval,session_user,clock_timestamp());
 INSERT INTO ctt_staff_private.review_enrollment_audit(event,enrollment_id,principal_id,details) VALUES('activation',p_enrollment,p_principal,jsonb_build_object('fingerprint',p_fingerprint,'faculty',p_faculty,'valid_from',p_from,'valid_until',p_until,'approval_ref',p_approval));
 RETURN credential_record;
END
$fn$;

CREATE FUNCTION ctt_staff_private.inspect_passkey_enrollment(p_enrollment uuid)
RETURNS TABLE(enrollment_id uuid,backend_pin text,account_id uuid,principal_id uuid,tenant_id text,edition_id uuid,actor_role text,
 fingerprint text,sign_count bigint,backup_eligible boolean,backup_state boolean,credential_record_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text;
BEGIN
 pin:=ctt_staff_private.current_enrollment_operator();
 RETURN QUERY SELECT a.enrollment_id,i.backend_pin,i.account_id,i.principal_id,i.tenant_id,i.edition_id,i.actor_role,
 c.fingerprint,c.sign_count,c.backup_eligible,c.backup_state,x.credential_record_id
 FROM ctt_staff_private.review_enrollment_attempts a JOIN ctt_staff_private.review_enrollment_invitations i USING(invitation_id)
 JOIN ctt_staff_private.review_enrollment_candidates c USING(enrollment_id)
 LEFT JOIN ctt_staff_private.review_enrollment_activations x USING(enrollment_id)
 WHERE a.enrollment_id=p_enrollment AND i.backend_pin=pin FOR SHARE OF a,i,c;
END
$fn$;

CREATE FUNCTION ctt_staff_private.revoke_enrolled_credential(p_record uuid,p_principal uuid,p_reason text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pin text;
BEGIN
 pin:=ctt_staff_private.current_enrollment_operator();
 IF NOT coalesce(p_reason~'^sha256:[0-9a-f]{64}$',false) OR NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_account_links WHERE backend_pin=pin AND principal_id=p_principal) THEN RAISE EXCEPTION 'CTT enrollment revocation denied' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM ctt_staff_private.review_principals WHERE principal_id=p_principal FOR UPDATE;
 UPDATE ctt_staff_private.review_credentials SET revoked_at=clock_timestamp(),authority_version=authority_version+1 WHERE credential_record_id=p_record AND principal_id=p_principal AND revoked_at IS NULL;
 IF FOUND THEN INSERT INTO ctt_staff_private.review_enrollment_audit(event,principal_id,details) VALUES('revocation',p_principal,jsonb_build_object('credential_record_id',p_record,'reason_ref',p_reason));
 ELSIF NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_credentials WHERE credential_record_id=p_record AND principal_id=p_principal AND revoked_at IS NOT NULL) THEN RAISE EXCEPTION 'CTT enrollment revocation denied' USING ERRCODE='42501'; END IF;
END
$fn$;

DO $functions$
DECLARE signature text; owner_id oid; item record;
BEGIN
 SELECT oid INTO owner_id FROM pg_catalog.pg_roles WHERE rolname='samchat_ctt_staff_owner';
 FOREACH signature IN ARRAY ARRAY['guard_review_service_configuration()','check_review_service_identity()','current_review_service()','current_enrollment_operator()',
 'admit_passkey_enrollment_invitation(uuid,uuid,uuid,text,uuid,text,bytea,timestamp with time zone,timestamp with time zone,text)',
 'load_passkey_enrollment(uuid,uuid)','begin_passkey_enrollment(uuid,text,uuid,uuid,bytea,integer)',
 'record_passkey_enrollment_candidate(uuid,uuid,text,bytea,bytea,bigint,boolean,boolean,text)',
 'activate_passkey_enrollment(uuid,text,uuid,uuid,text,uuid,text,text,timestamp with time zone,timestamp with time zone,text)',
 'revoke_enrolled_credential(uuid,uuid,text)','inspect_passkey_enrollment(uuid)'] LOOP
 EXECUTE 'ALTER FUNCTION ctt_staff_private.'||signature||' OWNER TO samchat_ctt_staff_owner';
 EXECUTE 'REVOKE ALL ON FUNCTION ctt_staff_private.'||signature||' FROM PUBLIC,devnous_user';
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a WHERE p.oid=to_regprocedure('ctt_staff_private.'||signature) AND a.grantee<>owner_id) THEN RAISE EXCEPTION 'CTT enrollment installed function ACL invalid'; END IF;
 END LOOP;
 FOR item IN SELECT c.* FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='ctt_staff_private'
 AND c.relname IN('review_services','review_enrollment_operators','review_account_links','review_enrollment_invitations','review_enrollment_attempts','review_enrollment_candidates','review_enrollment_activations','review_enrollment_audit') LOOP
 IF item.relowner<>owner_id OR item.relrowsecurity OR EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(item.relacl,pg_catalog.acldefault('r',item.relowner))) a WHERE a.grantee<>owner_id)
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=item.oid AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL) THEN RAISE EXCEPTION 'CTT enrollment installed relation ACL invalid'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_class c CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('S',c.relowner))) a
 WHERE c.oid='ctt_staff_private.review_enrollment_audit_audit_id_seq'::regclass AND (c.relowner<>owner_id OR a.grantee<>owner_id)) THEN
 RAISE EXCEPTION 'CTT enrollment installed sequence ACL invalid'; END IF;
END
$functions$;
COMMIT;
