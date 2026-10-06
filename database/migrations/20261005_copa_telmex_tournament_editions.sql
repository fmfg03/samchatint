-- Owner-run catalog migration; never executed by reads or application startup.
BEGIN;
CREATE TABLE IF NOT EXISTS copa_telmex_tournament_editions (
    id UUID PRIMARY KEY,
    tournament_id UUID NOT NULL REFERENCES tournaments(id) ON DELETE RESTRICT,
    edition_year INTEGER NOT NULL,
    roster_slug VARCHAR(80) NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_copa_telmex_project_edition UNIQUE (tournament_id, edition_year)
);
COMMIT;
