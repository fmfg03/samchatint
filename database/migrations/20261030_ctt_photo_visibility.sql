-- Candidate read gate: a private staged object is never publication authority.
BEGIN;
DO $preflight$
DECLARE owner_id oid; tab text;
BEGIN
  SELECT oid INTO owner_id FROM pg_catalog.pg_roles
  WHERE rolname='samchat_ctt_staff_owner' AND NOT rolcanlogin AND NOT rolsuper
    AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls;
  IF owner_id IS NULL OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid=owner_id OR member=owner_id
  ) OR pg_catalog.has_schema_privilege('devnous_user','ctt_staff_private','USAGE,CREATE') THEN
    RAISE EXCEPTION 'CTT photo boundary invalid';
  END IF;
  FOREACH tab IN ARRAY ARRAY['ctt_staff_private.capture_attempt_anchors',
    'ctt_staff_private.capture_payloads','ctt_staff_private.capture_results',
    'public.copa_telmex_players','public.copa_telmex_team_staff'] LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(tab)
        AND c.relkind='r' AND c.relowner=owner_id
    ) OR pg_catalog.has_table_privilege('devnous_user',tab,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')
      OR pg_catalog.has_any_column_privilege('devnous_user',tab,'INSERT,UPDATE') THEN
      RAISE EXCEPTION 'CTT photo protected relation invalid';
    END IF;
  END LOOP;
END
$preflight$;
CREATE FUNCTION public.ctt_photo_is_committed(p_sha256 text)
RETURNS boolean LANGUAGE sql SECURITY DEFINER STABLE
SET search_path=pg_catalog AS $read$
  WITH candidates AS (
    SELECT p.attempt_id,pg_catalog.convert_from(p.effect_bytes,'UTF8')::jsonb AS effect
    FROM ctt_staff_private.capture_payloads p
    JOIN ctt_staff_private.capture_results r ON r.attempt_id=p.attempt_id
    JOIN ctt_staff_private.capture_attempt_anchors a ON a.attempt_id=p.attempt_id
    JOIN ctt_staff_private.review_challenge_consumptions c ON c.challenge_id=a.challenge_id
    JOIN ctt_staff_private.signed_assertion_guards g ON g.challenge_id=a.challenge_id
    WHERE p_sha256 ~ '^[0-9a-f]{64}$'
  )
  SELECT EXISTS (
    SELECT 1 FROM candidates e
    CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(e.effect->'players') entry
    JOIN public.copa_telmex_players p ON p.id=(entry->>'id')::uuid
      AND p.team_id=(e.effect->'team'->>'id')::uuid
    WHERE entry->>'photo_path'='ctt-photo:'||p_sha256
      AND p.photo_path=entry->>'photo_path' AND p.governance_state='ACTIVE'
      AND p.finality_receipt_id IS NOT NULL
    UNION ALL
    SELECT 1 FROM candidates e
    CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(e.effect->'staff') entry
    JOIN public.copa_telmex_team_staff s ON s.id=(entry->'row'->>'id')::uuid
      AND s.team_id=(e.effect->'team'->>'id')::uuid
    WHERE entry->>'presence'='PRESENT'
      AND entry->'row'->>'photo_path'='ctt-photo:'||p_sha256
      AND s.photo_path=entry->'row'->>'photo_path' AND s.governance_state='ACTIVE'
      AND s.finality_receipt_id IS NOT NULL
  )
$read$;
ALTER FUNCTION public.ctt_photo_is_committed(text) OWNER TO samchat_ctt_staff_owner;
REVOKE ALL ON FUNCTION public.ctt_photo_is_committed(text) FROM PUBLIC,devnous_user;
GRANT EXECUTE ON FUNCTION public.ctt_photo_is_committed(text) TO devnous_user;
COMMIT;
