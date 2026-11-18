BEGIN;

LOCK TABLE amex_partner_payroll_events IN ACCESS EXCLUSIVE MODE;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM amex_partner_payroll_events) THEN
        RAISE EXCEPTION 'AMEX payroll settlement evidence prevents rollback';
    END IF;
END
$$;

DROP TABLE amex_partner_payroll_events;
DROP FUNCTION amex_partner_payroll_preserve_events();

COMMIT;
