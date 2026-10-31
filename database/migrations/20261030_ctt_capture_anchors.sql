-- Isolated candidate only: immutable source bindings; no admission authority,
-- writer grants, live credentials, or capture functions are introduced.
BEGIN;
DO $preflight$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace n
    JOIN pg_catalog.pg_roles r ON r.oid=n.nspowner
    WHERE n.nspname='ctt_staff_private'
      AND r.rolname='samchat_ctt_staff_owner'
      AND NOT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb
      AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members m
    JOIN pg_catalog.pg_roles r ON r.rolname='samchat_ctt_staff_owner'
    WHERE m.roleid=r.oid OR m.member=r.oid
  ) OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','CREATE,USAGE')
    OR NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_extension e
      JOIN pg_catalog.pg_roles r ON r.oid=e.extowner
      JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
      WHERE e.extname='pgcrypto' AND n.nspname='public' AND r.rolsuper
    ) OR pg_catalog.to_regclass('ctt_staff_private.review_challenges') IS NULL THEN
    RAISE EXCEPTION 'CTT anchor prerequisites failed';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_depend d
    JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid AND e.extname='pgcrypto'
    JOIN pg_catalog.pg_proc proc ON proc.oid=d.objid AND proc.proowner=e.extowner
    WHERE d.classid='pg_catalog.pg_proc'::regclass
      AND d.refclassid='pg_catalog.pg_extension'::regclass
      AND d.objid=pg_catalog.to_regprocedure('public.digest(bytea,text)')
      AND d.deptype='e'
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
    WHERE c.oid='ctt_staff_private.review_challenges'::regclass
      AND c.relkind='r' AND r.rolname='samchat_ctt_staff_owner'
      AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
        WHERE a.grantee <> c.relowner
      )
      AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
        WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
      )
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
    WHERE p.oid=pg_catalog.to_regprocedure('ctt_staff_private.reject_review_evidence_mutation()')
      AND r.rolname='samchat_ctt_staff_owner' AND p.prosecdef
      AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
      AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
        WHERE a.grantee <> p.proowner
      )
  ) OR (
    SELECT count(*) FROM pg_catalog.pg_trigger t
    WHERE t.tgrelid='ctt_staff_private.review_challenges'::regclass
      AND t.tgname IN ('ctt_review_challenge_immutable','ctt_review_challenge_no_truncate')
      AND t.tgenabled IN ('O','A')
      AND t.tgfoid=pg_catalog.to_regprocedure('ctt_staff_private.reject_review_evidence_mutation()')
  ) <> 2 THEN
    RAISE EXCEPTION 'CTT anchor protected dependencies invalid';
  END IF;
END
$preflight$;

-- The presence of an anchor does not establish who approved it. A separate
-- authenticated admission workflow must verify original bytes and provenance.
CREATE TABLE ctt_staff_private.manifest_anchors (
  manifest_sha256 text PRIMARY KEY CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
  tenant_id text NOT NULL CHECK (tenant_id ~ '^[a-z0-9][a-z0-9_-]{0,79}$'),
  edition_id uuid NOT NULL,
  tournament_id uuid NOT NULL,
  edition_year integer NOT NULL CHECK (edition_year BETWEEN 1 AND 9999),
  roster_slug text NOT NULL CHECK (roster_slug <> ''),
  created_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
  UNIQUE (manifest_sha256,tenant_id,edition_id)
);
CREATE TABLE ctt_staff_private.source_anchors (
  manifest_sha256 text NOT NULL REFERENCES ctt_staff_private.manifest_anchors(manifest_sha256) ON DELETE RESTRICT,
  document_id text NOT NULL CHECK (document_id ~ '^[0-9a-f]{64}$'),
  pdf_sha256 text NOT NULL CHECK (pdf_sha256 ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY (manifest_sha256,document_id),
  UNIQUE (manifest_sha256,document_id,pdf_sha256)
);
CREATE TABLE ctt_staff_private.capture_attempt_anchors (
  attempt_id uuid PRIMARY KEY,
  challenge_id uuid NOT NULL UNIQUE REFERENCES ctt_staff_private.review_challenges(challenge_id) ON DELETE RESTRICT,
  manifest_sha256 text NOT NULL,
  document_id text NOT NULL,
  pdf_sha256 text NOT NULL,
  tenant_id text NOT NULL,
  edition_id uuid NOT NULL,
  scope_hash text NOT NULL CHECK (scope_hash ~ '^sha256:[0-9a-f]{64}$'),
  scope_bytes bytea NOT NULL CHECK (octet_length(scope_bytes) BETWEEN 2 AND 262144),
  created_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
  FOREIGN KEY (manifest_sha256,tenant_id,edition_id)
    REFERENCES ctt_staff_private.manifest_anchors(manifest_sha256,tenant_id,edition_id) ON DELETE RESTRICT,
  FOREIGN KEY (manifest_sha256,document_id,pdf_sha256)
    REFERENCES ctt_staff_private.source_anchors(manifest_sha256,document_id,pdf_sha256) ON DELETE RESTRICT
);
ALTER TABLE ctt_staff_private.manifest_anchors OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.source_anchors OWNER TO samchat_ctt_staff_owner;
ALTER TABLE ctt_staff_private.capture_attempt_anchors OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON ctt_staff_private.manifest_anchors, ctt_staff_private.source_anchors,
  ctt_staff_private.capture_attempt_anchors FROM PUBLIC, devnous_user;

CREATE FUNCTION ctt_staff_private.validate_capture_anchor()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $anchor$
DECLARE scope jsonb; scope_text text; challenge ctt_staff_private.review_challenges%ROWTYPE;
BEGIN
  IF NEW.scope_hash <> 'sha256:' || pg_catalog.encode(public.digest(NEW.scope_bytes,'sha256'::text),'hex') THEN
    RAISE EXCEPTION 'CTT capture anchor denied' USING ERRCODE='42501';
  END IF;
  BEGIN
    scope_text := pg_catalog.convert_from(NEW.scope_bytes,'UTF8');
    IF scope_text IS NOT JSON OBJECT WITH UNIQUE KEYS THEN
      RAISE EXCEPTION 'CTT capture anchor denied' USING ERRCODE='42501';
    END IF;
    scope := scope_text::jsonb;
  EXCEPTION WHEN data_exception THEN
    RAISE EXCEPTION 'CTT capture anchor denied' USING ERRCODE='42501';
  END;
  SELECT * INTO challenge FROM ctt_staff_private.review_challenges
    WHERE challenge_id=NEW.challenge_id;
  IF NOT FOUND OR challenge.scope_hash IS DISTINCT FROM NEW.scope_hash
    OR challenge.tenant_id IS DISTINCT FROM NEW.tenant_id
    OR challenge.edition_id IS DISTINCT FROM NEW.edition_id
    OR scope->>'schema' IS DISTINCT FROM 'ctt.staff-review.challenge-scope.v1'
    OR scope->>'purpose' IS DISTINCT FROM 'capture_staff_v2'
    OR scope->>'manifest_sha256' IS DISTINCT FROM NEW.manifest_sha256
    OR scope->>'document_id' IS DISTINCT FROM NEW.document_id
    OR scope->>'pdf_sha256' IS DISTINCT FROM NEW.pdf_sha256
    OR scope->>'tenant_id' IS DISTINCT FROM NEW.tenant_id
    OR scope->>'edition_id' IS DISTINCT FROM NEW.edition_id::text
    OR scope->>'actor_id' IS DISTINCT FROM challenge.principal_id::text THEN
    RAISE EXCEPTION 'CTT capture anchor denied' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END
$anchor$;
ALTER FUNCTION ctt_staff_private.validate_capture_anchor() OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.validate_capture_anchor() FROM PUBLIC, devnous_user;
CREATE TRIGGER ctt_capture_anchor_validate BEFORE INSERT ON ctt_staff_private.capture_attempt_anchors
  FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.validate_capture_anchor();
CREATE TRIGGER ctt_manifest_anchor_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.manifest_anchors
  FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_source_anchor_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.source_anchors
  FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_capture_anchor_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.capture_attempt_anchors
  FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_manifest_anchor_no_truncate BEFORE TRUNCATE ON ctt_staff_private.manifest_anchors
  FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_source_anchor_no_truncate BEFORE TRUNCATE ON ctt_staff_private.source_anchors
  FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER ctt_capture_anchor_no_truncate BEFORE TRUNCATE ON ctt_staff_private.capture_attempt_anchors
  FOR EACH STATEMENT EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();

-- Exposes only an opaque binding to a separately authorized writer role.
-- This candidate intentionally grants EXECUTE to no operational role.
CREATE FUNCTION ctt_staff_private.lock_capture_anchor(p_attempt_id uuid)
RETURNS TABLE(attempt_id uuid, challenge_id uuid, principal_id uuid,
              credential_id bytea, scope_hash text, scope_bytes bytea,
              tenant_id text, edition_id uuid, tournament_id uuid,
              edition_year integer, roster_slug text)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $read$
  SELECT a.attempt_id,a.challenge_id,c.principal_id,c.credential_id,a.scope_hash,
         a.scope_bytes, m.tenant_id, m.edition_id, m.tournament_id,
         m.edition_year, m.roster_slug
  FROM ctt_staff_private.capture_attempt_anchors a
  JOIN ctt_staff_private.review_challenges c ON c.challenge_id=a.challenge_id
  JOIN ctt_staff_private.manifest_anchors m ON m.manifest_sha256=a.manifest_sha256
    AND m.tenant_id=a.tenant_id AND m.edition_id=a.edition_id
  JOIN ctt_staff_private.source_anchors s ON s.manifest_sha256=a.manifest_sha256
    AND s.document_id=a.document_id AND s.pdf_sha256=a.pdf_sha256
  WHERE a.attempt_id=p_attempt_id AND c.scope_hash=a.scope_hash
    AND c.tenant_id=a.tenant_id AND c.edition_id=a.edition_id
  FOR UPDATE OF a FOR SHARE OF c,m,s
$read$;
ALTER FUNCTION ctt_staff_private.lock_capture_anchor(uuid) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION ctt_staff_private.lock_capture_anchor(uuid) FROM PUBLIC, devnous_user;

DO $postflight$
DECLARE name text;
BEGIN
  FOREACH name IN ARRAY ARRAY['manifest_anchors','source_anchors','capture_attempt_anchors'] LOOP
    IF EXISTS (
      SELECT 1 FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
      WHERE n.nspname='ctt_staff_private' AND c.relname=name AND a.grantee <> c.relowner
    ) OR EXISTS (
      SELECT 1 FROM pg_catalog.pg_attribute a
      JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='ctt_staff_private' AND c.relname=name
        AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
    ) THEN RAISE EXCEPTION 'CTT anchor ACL invalid'; END IF;
  END LOOP;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
    WHERE n.nspname='ctt_staff_private' AND p.proname IN ('validate_capture_anchor','lock_capture_anchor')
      AND a.grantee <> p.proowner
  ) THEN RAISE EXCEPTION 'CTT anchor function ACL invalid'; END IF;
END
$postflight$;
COMMIT;
