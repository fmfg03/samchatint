-- Owner-run only: no startup DDL, no historical postings or data backfill.
-- Apply before deploying the AMEX recognition code. The installation timestamp
-- is the immutable prospective cutoff; replaying the migration preserves it.
BEGIN;

CREATE TABLE IF NOT EXISTS amex_recognition_activation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    activated_at TIMESTAMPTZ NOT NULL
);
INSERT INTO amex_recognition_activation (id, activated_at)
VALUES (1, CURRENT_TIMESTAMP) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS amex_recognition_consumptions (
    id UUID PRIMARY KEY,
    imported_expense_id UUID NOT NULL UNIQUE REFERENCES expense_reports(id) ON DELETE RESTRICT,
    card_account_id UUID NOT NULL REFERENCES amex_card_accounts(id) ON DELETE RESTRICT,
    amount NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    currency VARCHAR(3) NOT NULL,
    economic_date DATE NOT NULL,
    liability_account_id UUID NOT NULL REFERENCES cuentas_contables(id) ON DELETE RESTRICT,
    liability_code VARCHAR(30) NOT NULL,
    classification_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    accounting_poliza_id UUID NULL REFERENCES accounting_polizas(id) ON DELETE RESTRICT,
    actor_id UUID NOT NULL REFERENCES empleados(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_amex_recognition_consumptions_card_account_id
    ON amex_recognition_consumptions(card_account_id);
CREATE INDEX IF NOT EXISTS ix_amex_recognition_consumptions_accounting_poliza_id
    ON amex_recognition_consumptions(accounting_poliza_id);

CREATE TABLE IF NOT EXISTS amex_recognition_representations (
    id UUID PRIMARY KEY,
    consumption_id UUID NOT NULL REFERENCES amex_recognition_consumptions(id) ON DELETE RESTRICT,
    expense_id UUID NOT NULL UNIQUE REFERENCES expense_reports(id) ON DELETE RESTRICT,
    role VARCHAR(20) NOT NULL CHECK (role IN ('statement', 'report')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_amex_recognition_representations_consumption_id
    ON amex_recognition_representations(consumption_id);

CREATE TABLE IF NOT EXISTS amex_accounting_reviews (
    expense_id UUID PRIMARY KEY REFERENCES expense_reports(id) ON DELETE RESTRICT,
    informe_id UUID NOT NULL REFERENCES documentos(id) ON DELETE RESTRICT,
    treatment VARCHAR(30) NOT NULL CHECK (treatment IN ('expense', 'partner_receivable')),
    debtor_account_id UUID NULL REFERENCES cuentas_contables(id) ON DELETE RESTRICT,
    actor_id UUID NOT NULL REFERENCES empleados(id) ON DELETE RESTRICT,
    reason TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    source_key VARCHAR(64) NOT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((treatment = 'expense' AND debtor_account_id IS NULL) OR
           (treatment = 'partner_receivable' AND debtor_account_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_amex_accounting_reviews_informe_id
    ON amex_accounting_reviews(informe_id);

CREATE TABLE IF NOT EXISTS amex_accounting_cuts (
    id UUID PRIMARY KEY,
    informe_id UUID NOT NULL REFERENCES documentos(id) ON DELETE RESTRICT,
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('initial', 'adjustment')),
    state_key VARCHAR(64) NOT NULL UNIQUE,
    accounting_date DATE NOT NULL,
    accounting_poliza_id UUID NOT NULL UNIQUE REFERENCES accounting_polizas(id) ON DELETE RESTRICT,
    actor_id UUID NOT NULL REFERENCES empleados(id) ON DELETE RESTRICT,
    reason TEXT NOT NULL DEFAULT '',
    snapshot_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_amex_accounting_cuts_initial_informe
    ON amex_accounting_cuts(informe_id) WHERE kind = 'initial';
CREATE INDEX IF NOT EXISTS ix_amex_accounting_cuts_informe_id
    ON amex_accounting_cuts(informe_id);

COMMIT;
