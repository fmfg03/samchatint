CREATE TABLE IF NOT EXISTS budget_movement_assignments (
    id UUID PRIMARY KEY,
    budget_version_id UUID NOT NULL REFERENCES budget_versions(id) ON DELETE CASCADE,
    budget_concept_id UUID NOT NULL REFERENCES budget_concepts(id) ON DELETE RESTRICT,
    budget_line_id UUID NOT NULL REFERENCES budget_lines(id) ON DELETE RESTRICT,
    movement_key VARCHAR(200) NOT NULL,
    assigned_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (budget_version_id, movement_key)
);

CREATE INDEX IF NOT EXISTS ix_budget_movement_assignments_version_concept
    ON budget_movement_assignments(budget_version_id, budget_concept_id);
