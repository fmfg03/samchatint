BEGIN;

LOCK TABLE amex_partner_payroll_events, amex_partner_coi_deductions IN ACCESS EXCLUSIVE MODE;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM amex_partner_payroll_events)
        OR EXISTS (SELECT 1 FROM amex_partner_coi_deductions) THEN
        RAISE EXCEPTION 'AMEX payroll settlement evidence prevents rollback';
    END IF;
END
$$;

DROP TABLE amex_partner_payroll_events;
DROP TABLE amex_partner_coi_deductions;
DROP FUNCTION amex_partner_payroll_preserve_events();

COMMIT;
