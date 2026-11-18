BEGIN;

CREATE TABLE amex_partner_payroll_events (
    id uuid PRIMARY KEY,
    cut_item_id uuid NOT NULL REFERENCES amex_direct_cut_items(id),
    actor_id uuid NOT NULL REFERENCES empleados(id),
    kind text NOT NULL CHECK (kind IN ('payroll', 'transfer')),
    request_hash text NOT NULL CHECK (length(request_hash) = 64),
    payroll_line_id uuid REFERENCES payroll_run_lines(id),
    deduction_index integer CHECK (deduction_index >= 0),
    withholding_line_id uuid REFERENCES accounting_poliza_lines(id),
    debit_bank_id uuid REFERENCES bank_movements(id),
    credit_bank_id uuid REFERENCES bank_movements(id),
    amount numeric(18,2) NOT NULL CHECK (amount > 0),
    snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (kind = 'payroll' AND payroll_line_id IS NOT NULL
            AND deduction_index IS NOT NULL AND withholding_line_id IS NOT NULL
            AND debit_bank_id IS NULL AND credit_bank_id IS NULL)
        OR
        (kind = 'transfer' AND payroll_line_id IS NULL
            AND deduction_index IS NULL AND withholding_line_id IS NULL
            AND debit_bank_id IS NOT NULL AND credit_bank_id IS NOT NULL
            AND debit_bank_id <> credit_bank_id)
    )
);

CREATE INDEX ix_amex_partner_payroll_cut ON amex_partner_payroll_events (cut_item_id);
CREATE INDEX ix_amex_partner_payroll_line ON amex_partner_payroll_events
    (payroll_line_id, deduction_index) WHERE kind = 'payroll';
CREATE INDEX ix_amex_partner_payroll_withholding ON amex_partner_payroll_events
    (withholding_line_id) WHERE kind = 'payroll';
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

COMMIT;
