BEGIN;

CREATE TABLE amex_partner_coi_deductions (
    source_key text PRIMARY KEY CHECK (length(source_key) = 64),
    debtor_account_id uuid NOT NULL REFERENCES cuentas_contables(id),
    actor_id uuid NOT NULL REFERENCES empleados(id),
    document_sha256 text NOT NULL CHECK (length(document_sha256) = 64),
    document_format text NOT NULL CHECK (document_format IN ('pdf', 'xlsx')),
    document_bytes bytea NOT NULL
        CHECK (octet_length(document_bytes) BETWEEN 1 AND 5242880),
    amount numeric(18,2) NOT NULL CHECK (amount > 0),
    facts jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE amex_partner_payroll_events (
    id uuid PRIMARY KEY,
    cut_item_id uuid NOT NULL REFERENCES amex_direct_cut_items(id),
    actor_id uuid NOT NULL REFERENCES empleados(id),
    kind text NOT NULL CHECK (kind IN ('payroll', 'transfer')),
    request_hash text NOT NULL CHECK (length(request_hash) = 64),
    coi_source_key text REFERENCES amex_partner_coi_deductions(source_key),
    debit_bank_id uuid REFERENCES bank_movements(id),
    credit_bank_id uuid REFERENCES bank_movements(id),
    amount numeric(18,2) NOT NULL CHECK (amount > 0),
    snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (kind = 'payroll' AND coi_source_key IS NOT NULL
            AND debit_bank_id IS NULL AND credit_bank_id IS NULL)
        OR
        (kind = 'transfer' AND coi_source_key IS NULL
            AND debit_bank_id IS NOT NULL AND credit_bank_id IS NOT NULL
            AND debit_bank_id <> credit_bank_id)
    )
);

CREATE INDEX ix_amex_partner_payroll_cut ON amex_partner_payroll_events (cut_item_id);
CREATE INDEX ix_amex_partner_payroll_coi ON amex_partner_payroll_events (coi_source_key);
CREATE INDEX ix_amex_partner_payroll_debit ON amex_partner_payroll_events
    (debit_bank_id) WHERE kind = 'transfer';
CREATE INDEX ix_amex_partner_payroll_credit ON amex_partner_payroll_events
    (credit_bank_id) WHERE kind = 'transfer';
CREATE INDEX ix_amex_partner_payroll_bank_key ON amex_partner_payroll_events
    ((snapshot->>'bank_key')) WHERE kind = 'transfer';

CREATE FUNCTION amex_partner_payroll_preserve_events() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'AMEX payroll settlement evidence is immutable' USING ERRCODE = '23514';
END
$$;

CREATE TRIGGER amex_partner_payroll_preserve_events
BEFORE UPDATE OR DELETE OR TRUNCATE ON amex_partner_payroll_events
FOR EACH STATEMENT EXECUTE FUNCTION amex_partner_payroll_preserve_events();

CREATE TRIGGER amex_partner_payroll_preserve_coi
BEFORE UPDATE OR DELETE OR TRUNCATE ON amex_partner_coi_deductions
FOR EACH STATEMENT EXECUTE FUNCTION amex_partner_payroll_preserve_events();

COMMIT;
