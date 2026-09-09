-- Separate fiscal CFDI date from the budget period used for income actuals.
-- Execute with the PostgreSQL schema-owner role before promoting the web release.

ALTER TABLE IF EXISTS budget_cfdi_income_links
    ADD COLUMN IF NOT EXISTS budget_month SMALLINT NULL;

UPDATE budget_cfdi_income_links
SET budget_month = EXTRACT(MONTH FROM income_date)::smallint
WHERE budget_month IS NULL
  AND income_date IS NOT NULL;

ALTER TABLE budget_cfdi_income_links
    DROP CONSTRAINT IF EXISTS budget_cfdi_income_links_budget_month_check;

ALTER TABLE budget_cfdi_income_links
    ADD CONSTRAINT budget_cfdi_income_links_budget_month_check
    CHECK (budget_month BETWEEN 1 AND 12);

CREATE INDEX IF NOT EXISTS ix_budget_cfdi_income_links_budget_month
    ON budget_cfdi_income_links(budget_version_id, budget_month);
