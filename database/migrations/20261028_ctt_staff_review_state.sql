-- Isolated candidate: protected WebAuthn state, not an authorization endpoint.
-- Requires the separately reviewed staff-security candidate. No operational
-- account, credential, faculty or grant to the gate/app is created here.
BEGIN;
DO $preflight$
BEGIN
    IF pg_catalog.current_setting('max_prepared_transactions')::integer <> 0 THEN
        RAISE EXCEPTION 'CTT protected review requires two-phase commit disabled';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_namespace n
        JOIN pg_catalog.pg_roles r ON r.oid=n.nspowner
        WHERE n.nspname='ctt_staff_private'
          AND r.rolname='samchat_ctt_staff_owner'
          AND NOT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb
          AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls
    ) OR pg_catalog.pg_has_role('devnous_user','samchat_ctt_staff_owner','MEMBER')
      OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','CREATE,USAGE')
      OR EXISTS (
          SELECT 1 FROM pg_catalog.pg_auth_members m
          JOIN pg_catalog.pg_roles r ON r.rolname='samchat_ctt_staff_owner'
          WHERE m.roleid=r.oid OR m.member=r.oid
      ) THEN
        RAISE EXCEPTION 'CTT protected review state precondition failed';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='ctt_staff_private' AND c.relname IN (
            'review_principals','review_credentials','review_faculties',
            'review_challenges','review_challenge_consumptions',
            'review_authority_audit'
        )
    ) THEN RAISE EXCEPTION 'CTT review state already exists'; END IF;
END
$preflight$;

CREATE TABLE ctt_staff_private.review_principals (
    principal_id uuid PRIMARY KEY,
    active boolean NOT NULL DEFAULT false,
    authority_version bigint NOT NULL DEFAULT 1 CHECK (authority_version > 0),
    created_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);
CREATE TABLE ctt_staff_private.review_credentials (
    credential_id bytea PRIMARY KEY CHECK (octet_length(credential_id) BETWEEN 1 AND 1024),
    credential_record_id uuid NOT NULL UNIQUE DEFAULT pg_catalog.gen_random_uuid(),
    principal_id uuid NOT NULL REFERENCES ctt_staff_private.review_principals(principal_id) ON DELETE RESTRICT,
    attested_credential bytea NOT NULL CHECK (octet_length(attested_credential) BETWEEN 1 AND 16384),
    user_handle bytea NOT NULL CHECK (octet_length(user_handle) BETWEEN 1 AND 64),
    sign_count bigint NOT NULL DEFAULT 0 CHECK (sign_count BETWEEN 0 AND 4294967295),
    authority_version bigint NOT NULL DEFAULT 1 CHECK (authority_version > 0),
    revoked_at timestamptz,
    UNIQUE (credential_id, principal_id)
);
CREATE TABLE ctt_staff_private.review_faculties (
    principal_id uuid NOT NULL REFERENCES ctt_staff_private.review_principals(principal_id) ON DELETE RESTRICT,
    tenant_id text NOT NULL CHECK (tenant_id ~ '^[a-z0-9][a-z0-9_-]{0,79}$'),
    edition_id uuid NOT NULL,
    faculty text NOT NULL CHECK (faculty='capture_staff_v2'),
    valid_from timestamptz NOT NULL,
    valid_until timestamptz NOT NULL,
    revoked_at timestamptz,
    authority_version bigint NOT NULL DEFAULT 1 CHECK (authority_version > 0),
    PRIMARY KEY (principal_id, tenant_id, edition_id, faculty),
    CHECK (valid_until > valid_from)
);
CREATE TABLE ctt_staff_private.review_challenges (
    challenge_id uuid PRIMARY KEY,
    principal_id uuid NOT NULL,
    credential_id bytea NOT NULL,
    tenant_id text NOT NULL,
    edition_id uuid NOT NULL,
    faculty text NOT NULL DEFAULT 'capture_staff_v2' CHECK (faculty='capture_staff_v2'),
    principal_authority_version bigint NOT NULL CHECK (principal_authority_version > 0),
    faculty_authority_version bigint NOT NULL CHECK (faculty_authority_version > 0),
    credential_authority_version bigint NOT NULL CHECK (credential_authority_version > 0),
    scope_hash text NOT NULL CHECK (scope_hash ~ '^sha256:[0-9a-f]{64}$'),
    challenge_bytes bytea NOT NULL UNIQUE CHECK (octet_length(challenge_bytes)=32),
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    FOREIGN KEY (credential_id,principal_id) REFERENCES ctt_staff_private.review_credentials(credential_id,principal_id) ON DELETE RESTRICT,
    FOREIGN KEY (principal_id,tenant_id,edition_id,faculty) REFERENCES ctt_staff_private.review_faculties(principal_id,tenant_id,edition_id,faculty) ON DELETE RESTRICT,
    CHECK (expires_at > issued_at AND expires_at <= issued_at + interval '5 minutes')
);
CREATE TABLE ctt_staff_private.review_challenge_consumptions (
    challenge_id uuid PRIMARY KEY REFERENCES ctt_staff_private.review_challenges(challenge_id) ON DELETE RESTRICT,
    credential_id bytea NOT NULL REFERENCES ctt_staff_private.review_credentials(credential_id) ON DELETE RESTRICT,
    prior_counter bigint NOT NULL CHECK (prior_counter BETWEEN 0 AND 4294967295),
    new_counter bigint NOT NULL CHECK (new_counter BETWEEN 1 AND 4294967295),
    consumed_at timestamptz NOT NULL,
    CHECK (new_counter > prior_counter)
);
CREATE TABLE ctt_staff_private.review_authority_audit (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_kind text NOT NULL CHECK (entity_kind IN ('principal','faculty','credential')),
    operation text NOT NULL CHECK (operation IN ('insert','update')),
    principal_id uuid NOT NULL REFERENCES ctt_staff_private.review_principals(principal_id) ON DELETE RESTRICT,
    credential_record_id uuid REFERENCES ctt_staff_private.review_credentials(credential_record_id) ON DELETE RESTRICT,
    tenant_id text,
    edition_id uuid,
    authority_version bigint NOT NULL CHECK (authority_version > 0),
    recorded_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
    recorded_by name NOT NULL DEFAULT session_user,
    CHECK ((entity_kind='credential') = (credential_record_id IS NOT NULL)),
    CHECK ((entity_kind='faculty' AND tenant_id IS NOT NULL AND edition_id IS NOT NULL)
        OR (entity_kind<>'faculty' AND tenant_id IS NULL AND edition_id IS NULL))
);

ALTER TABLE ctt_staff_private.review_principals OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.review_credentials OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.review_faculties OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.review_challenges OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.review_challenge_consumptions OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.review_authority_audit OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON TABLE ctt_staff_private.review_principals,
    ctt_staff_private.review_credentials, ctt_staff_private.review_faculties,
    ctt_staff_private.review_challenges,
    ctt_staff_private.review_challenge_consumptions,
    ctt_staff_private.review_authority_audit FROM PUBLIC, devnous_user;

CREATE FUNCTION ctt_staff_private.guard_review_authority_version()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $version$
BEGIN
    IF NEW.authority_version < OLD.authority_version THEN
        RAISE EXCEPTION 'CTT review authority version invalid';
    END IF;
    IF TG_TABLE_NAME='review_principals' THEN
        IF NEW.principal_id IS DISTINCT FROM OLD.principal_id OR
           (NEW.active IS DISTINCT FROM OLD.active AND NEW.authority_version <= OLD.authority_version) THEN
            RAISE EXCEPTION 'CTT review authority version invalid';
        END IF;
    ELSIF TG_TABLE_NAME='review_faculties' THEN
        IF NEW.principal_id IS DISTINCT FROM OLD.principal_id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
           OR NEW.edition_id IS DISTINCT FROM OLD.edition_id OR NEW.faculty IS DISTINCT FROM OLD.faculty
           OR ((NEW.valid_from IS DISTINCT FROM OLD.valid_from OR NEW.valid_until IS DISTINCT FROM OLD.valid_until
                OR NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) AND NEW.authority_version <= OLD.authority_version) THEN
            RAISE EXCEPTION 'CTT review authority version invalid';
        END IF;
    ELSIF TG_TABLE_NAME='review_credentials' THEN
        IF NEW.credential_id IS DISTINCT FROM OLD.credential_id OR NEW.credential_record_id IS DISTINCT FROM OLD.credential_record_id
           OR NEW.principal_id IS DISTINCT FROM OLD.principal_id
           OR NEW.sign_count < OLD.sign_count
           OR ((NEW.attested_credential IS DISTINCT FROM OLD.attested_credential OR NEW.user_handle IS DISTINCT FROM OLD.user_handle
                OR NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) AND NEW.authority_version <= OLD.authority_version) THEN
            RAISE EXCEPTION 'CTT review authority version invalid';
        END IF;
    END IF;
    RETURN NEW;
END
$version$;
ALTER FUNCTION ctt_staff_private.guard_review_authority_version() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.guard_review_authority_version() FROM PUBLIC, devnous_user;
CREATE TRIGGER ctt_review_principal_version BEFORE UPDATE ON ctt_staff_private.review_principals
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.guard_review_authority_version();
CREATE TRIGGER ctt_review_faculty_version BEFORE UPDATE ON ctt_staff_private.review_faculties
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.guard_review_authority_version();
CREATE TRIGGER ctt_review_credential_version BEFORE UPDATE ON ctt_staff_private.review_credentials
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.guard_review_authority_version();

CREATE FUNCTION ctt_staff_private.reject_review_evidence_mutation()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $immutable$
BEGIN
    RAISE EXCEPTION 'CTT review evidence is append-only' USING ERRCODE='42501';
END
$immutable$;
ALTER FUNCTION ctt_staff_private.reject_review_evidence_mutation() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.reject_review_evidence_mutation() FROM PUBLIC, devnous_user;
CREATE TRIGGER ctt_review_challenge_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.review_challenges
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_review_challenge_no_truncate BEFORE TRUNCATE ON ctt_staff_private.review_challenges
    FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_review_consumption_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.review_challenge_consumptions
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_review_consumption_no_truncate BEFORE TRUNCATE ON ctt_staff_private.review_challenge_consumptions
    FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_review_audit_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.review_authority_audit
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_review_audit_no_truncate BEFORE TRUNCATE ON ctt_staff_private.review_authority_audit
    FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_review_principal_no_delete BEFORE DELETE ON ctt_staff_private.review_principals
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_review_principal_no_truncate BEFORE TRUNCATE ON ctt_staff_private.review_principals
    FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_review_faculty_no_delete BEFORE DELETE ON ctt_staff_private.review_faculties
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_review_faculty_no_truncate BEFORE TRUNCATE ON ctt_staff_private.review_faculties
    FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_review_credential_no_delete BEFORE DELETE ON ctt_staff_private.review_credentials
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_review_credential_no_truncate BEFORE TRUNCATE ON ctt_staff_private.review_credentials
    FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();

CREATE FUNCTION ctt_staff_private.audit_review_authority_change()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $audit$
DECLARE kind text; credential_ref uuid; audit_tenant text; audit_edition uuid;
BEGIN
    IF TG_TABLE_NAME='review_principals' THEN
        kind := 'principal';
    ELSIF TG_TABLE_NAME='review_faculties' THEN
        kind := 'faculty';
        audit_tenant := NEW.tenant_id;
        audit_edition := NEW.edition_id;
    ELSIF TG_TABLE_NAME='review_credentials' THEN
        kind := 'credential';
        credential_ref := NEW.credential_record_id;
        IF TG_OP='UPDATE' AND NEW.attested_credential IS NOT DISTINCT FROM OLD.attested_credential
           AND NEW.user_handle IS NOT DISTINCT FROM OLD.user_handle
           AND NEW.revoked_at IS NOT DISTINCT FROM OLD.revoked_at
           AND NEW.authority_version = OLD.authority_version THEN
            RETURN NEW;
        END IF;
    ELSE
        RAISE EXCEPTION 'CTT review audit scope invalid';
    END IF;
    INSERT INTO ctt_staff_private.review_authority_audit
        (entity_kind,operation,principal_id,credential_record_id,tenant_id,edition_id,authority_version)
        VALUES (kind, pg_catalog.lower(TG_OP), NEW.principal_id, credential_ref,
                audit_tenant, audit_edition, NEW.authority_version);
    RETURN NEW;
END
$audit$;
ALTER FUNCTION ctt_staff_private.audit_review_authority_change() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.audit_review_authority_change() FROM PUBLIC, devnous_user;
CREATE TRIGGER ctt_review_principal_audit AFTER INSERT OR UPDATE ON ctt_staff_private.review_principals
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.audit_review_authority_change();
CREATE TRIGGER ctt_review_faculty_audit AFTER INSERT OR UPDATE ON ctt_staff_private.review_faculties
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.audit_review_authority_change();
CREATE TRIGGER ctt_review_credential_audit AFTER INSERT OR UPDATE ON ctt_staff_private.review_credentials
    FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.audit_review_authority_change();

-- The caller MUST first verify WebAuthn in a trusted gate. This function does
-- not verify signatures, issue a decision, or authorize any materialization.
-- No EXECUTE grant is made to the application or to any operational role.
CREATE FUNCTION ctt_staff_private.consume_review_challenge(
    p_challenge_id uuid, p_principal_id uuid, p_credential_id bytea,
    p_scope_hash text, p_prior_counter bigint, p_new_counter bigint
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $consume$
DECLARE
    item ctt_staff_private.review_challenges%ROWTYPE;
    principal ctt_staff_private.review_principals%ROWTYPE;
    credential ctt_staff_private.review_credentials%ROWTYPE;
    faculty ctt_staff_private.review_faculties%ROWTYPE;
    checked_at timestamptz;
BEGIN
    SELECT * INTO item FROM ctt_staff_private.review_challenges
      WHERE challenge_id=p_challenge_id FOR UPDATE;
    IF NOT FOUND OR item.principal_id IS DISTINCT FROM p_principal_id
       OR item.credential_id IS DISTINCT FROM p_credential_id
       OR item.scope_hash IS DISTINCT FROM p_scope_hash
       OR p_prior_counter IS NULL OR p_new_counter IS NULL
       OR p_prior_counter < 0 OR p_new_counter <= p_prior_counter
       OR p_new_counter > 4294967295
       OR EXISTS (SELECT 1 FROM ctt_staff_private.review_challenge_consumptions
                  WHERE challenge_id=p_challenge_id) THEN
        RAISE EXCEPTION 'CTT review challenge denied' USING ERRCODE='42501';
    END IF;
    SELECT * INTO principal FROM ctt_staff_private.review_principals
      WHERE principal_id=item.principal_id FOR UPDATE;
    SELECT f.* INTO faculty FROM ctt_staff_private.review_faculties AS f
      WHERE f.principal_id=item.principal_id AND f.tenant_id=item.tenant_id
        AND f.edition_id=item.edition_id AND f.faculty=item.faculty FOR UPDATE;
    SELECT * INTO credential FROM ctt_staff_private.review_credentials
      WHERE credential_id=item.credential_id AND principal_id=item.principal_id FOR UPDATE;
    -- Re-read time after all potentially blocking row locks have completed.
    checked_at := pg_catalog.clock_timestamp();
    IF principal.principal_id IS NULL OR NOT principal.active
       OR principal.authority_version <> item.principal_authority_version
       OR faculty.principal_id IS NULL OR faculty.revoked_at IS NOT NULL
       OR faculty.authority_version <> item.faculty_authority_version
       OR checked_at < item.issued_at OR checked_at >= item.expires_at
       OR checked_at < faculty.valid_from OR checked_at >= faculty.valid_until
       OR credential.credential_id IS NULL OR credential.revoked_at IS NOT NULL
       OR credential.authority_version <> item.credential_authority_version
       OR credential.sign_count IS DISTINCT FROM p_prior_counter THEN
        RAISE EXCEPTION 'CTT review challenge denied' USING ERRCODE='42501';
    END IF;
    UPDATE ctt_staff_private.review_credentials SET sign_count=p_new_counter
      WHERE credential_id=item.credential_id;
    INSERT INTO ctt_staff_private.review_challenge_consumptions
      (challenge_id,credential_id,prior_counter,new_counter,consumed_at)
      VALUES (p_challenge_id,p_credential_id,p_prior_counter,p_new_counter,checked_at);
    RETURN true;
END
$consume$;
ALTER FUNCTION ctt_staff_private.consume_review_challenge(uuid,uuid,bytea,text,bigint,bigint) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.consume_review_challenge(uuid,uuid,bytea,text,bigint,bigint) FROM PUBLIC, devnous_user;

-- This deferred check runs at commit unless the caller explicitly advances it
-- with SET CONSTRAINTS. A trusted transaction boundary is still required.
CREATE FUNCTION ctt_staff_private.validate_review_consumption_at_commit()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $commit_guard$
DECLARE checked_at timestamptz := pg_catalog.clock_timestamp();
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ctt_staff_private.review_challenges AS ch
        JOIN ctt_staff_private.review_principals AS p ON p.principal_id=ch.principal_id
        JOIN ctt_staff_private.review_faculties AS f
          ON f.principal_id=ch.principal_id AND f.tenant_id=ch.tenant_id
         AND f.edition_id=ch.edition_id AND f.faculty=ch.faculty
        JOIN ctt_staff_private.review_credentials AS cr
          ON cr.credential_id=ch.credential_id AND cr.principal_id=ch.principal_id
        WHERE ch.challenge_id=NEW.challenge_id AND ch.credential_id=NEW.credential_id
          AND ch.issued_at <= checked_at AND ch.expires_at > checked_at
          AND p.active AND p.authority_version=ch.principal_authority_version
          AND f.revoked_at IS NULL AND f.valid_from <= checked_at AND f.valid_until > checked_at
          AND f.authority_version=ch.faculty_authority_version
          AND cr.revoked_at IS NULL AND cr.authority_version=ch.credential_authority_version
          AND cr.sign_count=NEW.new_counter
    ) THEN
        RAISE EXCEPTION 'CTT review challenge expired or authority changed before commit' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END
$commit_guard$;
ALTER FUNCTION ctt_staff_private.validate_review_consumption_at_commit() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.validate_review_consumption_at_commit() FROM PUBLIC, devnous_user;
CREATE CONSTRAINT TRIGGER ctt_review_consumption_commit_guard
    AFTER INSERT ON ctt_staff_private.review_challenge_consumptions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION ctt_staff_private.validate_review_consumption_at_commit();

DO $postflight$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['review_principals','review_credentials','review_faculties','review_challenges','review_challenge_consumptions','review_authority_audit'] LOOP
        IF pg_catalog.has_table_privilege('devnous_user', 'ctt_staff_private.' || name, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') THEN
            RAISE EXCEPTION 'CTT review state privilege isolation failed';
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(c.relacl, pg_catalog.acldefault('r',c.relowner))
            ) AS acl
            WHERE n.nspname='ctt_staff_private' AND c.relname=name
              AND acl.grantee <> c.relowner
        ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='ctt_staff_private' AND c.relname=name
              AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
        ) THEN RAISE EXCEPTION 'CTT review state unexpected ACL'; END IF;
    END LOOP;
    IF pg_catalog.has_function_privilege('devnous_user','ctt_staff_private.consume_review_challenge(uuid,uuid,bytea,text,bigint,bigint)','EXECUTE') THEN
        RAISE EXCEPTION 'CTT review state function isolation failed';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(p.proacl, pg_catalog.acldefault('f',p.proowner))
        ) AS acl
        WHERE n.nspname='ctt_staff_private'
          AND p.proname IN ('consume_review_challenge',
                            'guard_review_authority_version',
                            'validate_review_consumption_at_commit',
                            'reject_review_evidence_mutation',
                            'audit_review_authority_change')
          AND acl.grantee <> p.proowner
    ) THEN RAISE EXCEPTION 'CTT review state unexpected function ACL'; END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_namespace n
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(n.nspacl, pg_catalog.acldefault('n',n.nspowner))
        ) AS acl
        WHERE n.nspname='ctt_staff_private' AND acl.grantee <> n.nspowner
    ) THEN RAISE EXCEPTION 'CTT review state unexpected schema ACL'; END IF;
    IF pg_catalog.has_sequence_privilege(
        'devnous_user','ctt_staff_private.review_authority_audit_audit_id_seq',
        'USAGE,SELECT,UPDATE'
    ) THEN RAISE EXCEPTION 'CTT review audit sequence isolation failed'; END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(c.relacl,pg_catalog.acldefault('S',c.relowner))
        ) AS acl
        WHERE n.nspname='ctt_staff_private'
          AND c.relname='review_authority_audit_audit_id_seq'
          AND acl.grantee <> c.relowner
    ) THEN RAISE EXCEPTION 'CTT review audit sequence unexpected ACL'; END IF;
END
$postflight$;
COMMIT;
