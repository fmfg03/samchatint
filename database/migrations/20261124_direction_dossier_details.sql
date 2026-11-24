-- Owner-run. Releases before this expansion can keep reading the original tables.
BEGIN;
CREATE TABLE IF NOT EXISTS direction_dossier_extended_records (
    id UUID PRIMARY KEY,
    tournament_id UUID NOT NULL,
    edition_year INTEGER NOT NULL CHECK (edition_year BETWEEN 2000 AND 2100),
    phase TEXT NOT NULL CHECK (phase IN ('estatal', 'nacional')),
    entity TEXT NOT NULL CHECK (length(entity) <= 120),
    category TEXT NOT NULL CHECK (length(category) <= 80),
    domain TEXT NOT NULL,
    kind TEXT NOT NULL,
    fields JSONB NOT NULL CHECK (jsonb_typeof(fields) = 'object'),
    document_id UUID CHECK (document_id IS NULL),
    evidence_reference TEXT NOT NULL CHECK (length(btrim(evidence_reference)) BETWEEN 1 AND 240),
    revision INTEGER NOT NULL CHECK (revision > 0),
    updated_by UUID NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (phase <> 'estatal' OR length(btrim(entity)) > 0),
    CHECK (
        (kind = 'national_itinerary' AND domain = 'operations' AND phase = 'nacional')
        OR (kind = 'medical_transfer' AND domain = 'medical_transfers' AND phase = 'nacional')
        OR (kind = 'sponsor_guest' AND domain = 'sponsor_guests' AND phase = 'nacional')
        OR (kind = 'expense_line_link' AND domain = 'finance'
            AND COALESCE(jsonb_typeof(fields -> 'expense_id'), '') = 'string'
            AND COALESCE(jsonb_typeof(fields -> 'source_document_id'), '') = 'string')
    )
);

CREATE INDEX IF NOT EXISTS ix_direction_dossier_extended_scope
    ON direction_dossier_extended_records(tournament_id, edition_year, domain, phase, entity);
CREATE UNIQUE INDEX IF NOT EXISTS uq_direction_dossier_expense_attribution
    ON direction_dossier_extended_records ((fields ->> 'expense_id'))
    WHERE kind = 'expense_line_link';

CREATE INDEX IF NOT EXISTS ix_direction_dossier_expense_parent
    ON direction_dossier_extended_records ((fields ->> 'source_document_id'))
    WHERE kind = 'expense_line_link';

CREATE TABLE IF NOT EXISTS direction_dossier_extended_revisions (
    record_id UUID NOT NULL REFERENCES direction_dossier_extended_records(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision > 0),
    actor_id UUID NOT NULL,
    snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    evidence_reference TEXT NOT NULL CHECK (length(btrim(evidence_reference)) BETWEEN 1 AND 240),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (record_id, revision)
);
REVOKE ALL ON direction_dossier_extended_records, direction_dossier_extended_revisions FROM PUBLIC;
GRANT SELECT, INSERT ON direction_dossier_extended_records TO devnous_user;
GRANT UPDATE (fields, document_id, evidence_reference, revision, updated_by, updated_at)
    ON direction_dossier_extended_records TO devnous_user;
GRANT INSERT ON direction_dossier_extended_revisions TO devnous_user;
COMMIT;
