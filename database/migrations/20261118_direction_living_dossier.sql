-- Owner-run migration. Application startup and reads never execute this DDL.
-- Source IDs are validated at capture. They preserve history without changing
-- the deletion or retention behavior of the source modules.
BEGIN;

CREATE TABLE IF NOT EXISTS direction_dossier_records (
    id UUID PRIMARY KEY,
    tournament_id UUID NOT NULL,
    edition_year INTEGER NOT NULL CHECK (edition_year BETWEEN 2000 AND 2100),
    phase TEXT NOT NULL CHECK (phase IN ('estatal', 'nacional')),
    entity TEXT NOT NULL CHECK (length(entity) <= 120),
    category TEXT NOT NULL CHECK (length(category) <= 80),
    domain TEXT NOT NULL CHECK (domain IN ('operations', 'finance', 'marketing', 'contacts', 'media')),
    kind TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 50),
    fields JSONB NOT NULL CHECK (jsonb_typeof(fields) = 'object'),
    document_id UUID,
    evidence_reference TEXT NOT NULL CHECK (length(btrim(evidence_reference)) BETWEEN 1 AND 240),
    revision INTEGER NOT NULL CHECK (revision > 0),
    updated_by UUID NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (phase <> 'estatal' OR length(btrim(entity)) > 0),
    CHECK ((kind = 'financial_link') = (document_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_direction_dossier_records_scope
    ON direction_dossier_records(tournament_id, edition_year, phase, entity, category);
CREATE UNIQUE INDEX IF NOT EXISTS uq_direction_dossier_document
    ON direction_dossier_records(document_id) WHERE document_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_direction_dossier_operations_edition
    ON direction_dossier_records(tournament_id, edition_year)
    WHERE kind = 'operations_edition';
CREATE UNIQUE INDEX IF NOT EXISTS uq_direction_dossier_operations_source
    ON direction_dossier_records ((fields ->> 'operations_tournament_id'))
    WHERE kind = 'operations_edition';

CREATE TABLE IF NOT EXISTS direction_dossier_revisions (
    record_id UUID NOT NULL REFERENCES direction_dossier_records(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision > 0),
    actor_id UUID NOT NULL,
    evidence_reference TEXT NOT NULL CHECK (length(btrim(evidence_reference)) BETWEEN 1 AND 240),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (record_id, revision)
);

REVOKE ALL ON direction_dossier_records, direction_dossier_revisions FROM PUBLIC;
GRANT SELECT, INSERT ON direction_dossier_records TO devnous_user;
GRANT UPDATE (fields, document_id, evidence_reference, revision, updated_by, updated_at)
    ON direction_dossier_records TO devnous_user;
GRANT INSERT ON direction_dossier_revisions TO devnous_user;

COMMIT;
