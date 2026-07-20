-- REG-S07 follow-up: align PostgreSQL snapshot hashes with the compact,
-- recursively key-sorted JSON representation used by Python.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION regs07_canonical_jsonb(value jsonb)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
DECLARE
    rendered text;
BEGIN
    CASE jsonb_typeof(value)
        WHEN 'object' THEN
            SELECT '{' || COALESCE(
                string_agg(
                    to_jsonb(entry.key)::text || ':' ||
                        regs07_canonical_jsonb(entry.value),
                    ',' ORDER BY convert_to(entry.key, 'UTF8')
                ),
                ''
            ) || '}'
            INTO rendered
            FROM jsonb_each(value) AS entry;
            RETURN rendered;
        WHEN 'array' THEN
            SELECT '[' || COALESCE(
                string_agg(
                    regs07_canonical_jsonb(item.value),
                    ',' ORDER BY item.ordinality
                ),
                ''
            ) || ']'
            INTO rendered
            FROM jsonb_array_elements(value)
                WITH ORDINALITY AS item(value, ordinality);
            RETURN rendered;
        ELSE
            RETURN value::text;
    END CASE;
END
$$;

CREATE OR REPLACE FUNCTION regs07_snapshot_hash(snapshot jsonb)
RETURNS varchar(71)
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT 'sha256:' || encode(
        digest(
            convert_to(regs07_canonical_jsonb(snapshot), 'UTF8'),
            'sha256'
        ),
        'hex'
    )
$$;

ALTER TABLE copa_telmex_teams
    DISABLE TRIGGER trg_regs07_team_guard;
ALTER TABLE copa_telmex_teams
    DISABLE TRIGGER trg_regs07_team_version;
ALTER TABLE copa_telmex_players
    DISABLE TRIGGER trg_regs07_player_guard;
ALTER TABLE copa_telmex_players
    DISABLE TRIGGER trg_regs07_player_version;
ALTER TABLE copa_telmex_registration_postcommit_entity_versions
    DISABLE TRIGGER trg_regs07_version_immutable;

UPDATE copa_telmex_registration_postcommit_entity_versions
SET snapshot_hash = regs07_snapshot_hash(snapshot);

UPDATE copa_telmex_registration_postcommit_entity_versions AS current_version
SET predecessor_snapshot_hash = predecessor.snapshot_hash
FROM copa_telmex_registration_postcommit_entity_versions AS predecessor
WHERE predecessor.entity_type = current_version.entity_type
  AND predecessor.entity_id = current_version.entity_id
  AND predecessor.revision = current_version.revision - 1;

UPDATE copa_telmex_registration_postcommit_entity_versions
SET predecessor_snapshot_hash = NULL
WHERE revision = 1;

UPDATE copa_telmex_teams
SET postcommit_snapshot_hash = regs07_snapshot_hash(
    regs07_team_snapshot(copa_telmex_teams)
);

UPDATE copa_telmex_players
SET postcommit_snapshot_hash = regs07_snapshot_hash(
    regs07_player_snapshot(copa_telmex_players)
);

ALTER TABLE copa_telmex_registration_postcommit_entity_versions
    ENABLE TRIGGER trg_regs07_version_immutable;
ALTER TABLE copa_telmex_players
    ENABLE TRIGGER trg_regs07_player_version;
ALTER TABLE copa_telmex_players
    ENABLE TRIGGER trg_regs07_player_guard;
ALTER TABLE copa_telmex_teams
    ENABLE TRIGGER trg_regs07_team_version;
ALTER TABLE copa_telmex_teams
    ENABLE TRIGGER trg_regs07_team_guard;

COMMIT;
