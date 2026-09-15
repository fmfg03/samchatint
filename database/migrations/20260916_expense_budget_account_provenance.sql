-- Preserve the budget-concept source of inherited accounting mappings.
-- Manual Finance assignments clear these fields in the application layer.
ALTER TABLE expense_reports
    ADD COLUMN IF NOT EXISTS cuenta_contable_budget_concept_id UUID NULL
    REFERENCES budget_concepts(id) ON UPDATE CASCADE ON DELETE SET NULL;

ALTER TABLE expense_reports
    ADD COLUMN IF NOT EXISTS contra_cuenta_contable_budget_concept_id UUID NULL
    REFERENCES budget_concepts(id) ON UPDATE CASCADE ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_expense_reports_cuenta_contable_budget_concept_id
    ON expense_reports(cuenta_contable_budget_concept_id);

CREATE INDEX IF NOT EXISTS idx_expense_reports_contra_cuenta_contable_budget_concept_id
    ON expense_reports(contra_cuenta_contable_budget_concept_id);
