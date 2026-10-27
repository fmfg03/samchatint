-- Owner-run migration. Separate versioned schedules from financial versions.
BEGIN;
CREATE TABLE budget_tournament_weekly_revisions (
    id UUID PRIMARY KEY,
    edition_year INTEGER NOT NULL,
    tournament_id UUID NOT NULL REFERENCES tournaments(id) ON DELETE RESTRICT,
    financial_version_id UUID NOT NULL REFERENCES budget_versions(id) ON DELETE RESTRICT,
    financial_root_version_id UUID NOT NULL REFERENCES budget_versions(id) ON DELETE RESTRICT,
    actor_empleado_id UUID NOT NULL REFERENCES empleados(id) ON DELETE RESTRICT,
    previous_revision_id UUID NULL REFERENCES budget_tournament_weekly_revisions(id) ON DELETE RESTRICT,
    previous_effective_revision_id UUID NULL REFERENCES budget_tournament_weekly_revisions(id) ON DELETE RESTRICT,
    restored_revision_id UUID NULL REFERENCES budget_tournament_weekly_revisions(id) ON DELETE RESTRICT,
    week_number INTEGER NOT NULL CHECK (week_number BETWEEN 1 AND 52),
    source_fingerprint VARCHAR(64) NOT NULL CHECK (length(source_fingerprint)=64),
    legacy_fingerprint VARCHAR(64) NOT NULL CHECK (length(legacy_fingerprint)=64),
    plan_hash VARCHAR(64) NOT NULL CHECK (length(plan_hash)=64),
    content_hash VARCHAR(64) NOT NULL CHECK (length(content_hash)=64),
    line_count INTEGER NOT NULL CHECK (line_count > 0),
    total NUMERIC(18,2) NOT NULL CHECK (total >= 0),
    before_snapshot JSONB NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at TIMESTAMPTZ NULL,
    deactivated_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE RESTRICT,
    CHECK ((deactivated_at IS NULL) = (deactivated_by_empleado_id IS NULL)),
    CHECK (deactivated_at IS NULL OR NOT is_current),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_budget_tournament_weekly_current
    ON budget_tournament_weekly_revisions(edition_year,tournament_id)
    WHERE is_current;
CREATE UNIQUE INDEX uq_budget_tournament_weekly_previous
    ON budget_tournament_weekly_revisions(previous_revision_id)
    WHERE previous_revision_id IS NOT NULL;
CREATE TABLE budget_tournament_weekly_lines (
    revision_id UUID NOT NULL REFERENCES budget_tournament_weekly_revisions(id) ON DELETE RESTRICT,
    source_line_id UUID NOT NULL,
    financial_line_id UUID NOT NULL,
    source_snapshot JSONB NOT NULL,
    previous_plan JSONB NOT NULL,
    week_number INTEGER NOT NULL CHECK (week_number BETWEEN 1 AND 52),
    amount NUMERIC(18,2) NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (revision_id, financial_line_id),
    UNIQUE (revision_id, source_line_id)
);
COMMIT;
