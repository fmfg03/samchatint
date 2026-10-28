-- Owner-run migration only. No runtime DDL, backfill or production application.
BEGIN;
CREATE TABLE IF NOT EXISTS payroll_amount_imports (
    id uuid PRIMARY KEY,
    period_id uuid NOT NULL REFERENCES payroll_periods(id),
    payroll_employer_id uuid NOT NULL REFERENCES payroll_employers(id),
    created_by_empleado_id uuid NOT NULL REFERENCES empleados(id),
    file_sha256 varchar(64) NOT NULL,
    content_sha256 varchar(64) NOT NULL,
    parser_version varchar(40) NOT NULL,
    status varchar(30) NOT NULL,
    gross_total numeric(18,2) NOT NULL,
    deductions_total numeric(18,2) NOT NULL,
    net_total numeric(18,2) NOT NULL,
    metadata_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_payroll_amount_file UNIQUE (period_id, payroll_employer_id, file_sha256),
    CONSTRAINT uq_payroll_amount_content UNIQUE (period_id, payroll_employer_id, content_sha256),
    CONSTRAINT ck_payroll_amount_status CHECK (status IN ('validated','pending_mapping'))
);
CREATE INDEX IF NOT EXISTS ix_payroll_amount_imports_period_id ON payroll_amount_imports(period_id);
CREATE INDEX IF NOT EXISTS ix_payroll_amount_imports_payroll_employer_id ON payroll_amount_imports(payroll_employer_id);
CREATE TABLE IF NOT EXISTS payroll_amount_import_lines (
    id uuid PRIMARY KEY,
    import_id uuid NOT NULL REFERENCES payroll_amount_imports(id),
    payroll_employee_id uuid NOT NULL REFERENCES payroll_employees(id),
    source_row integer NOT NULL,
    employee_code varchar(80) NOT NULL,
    source_name varchar(255) NOT NULL,
    days_paid numeric(10,4) NOT NULL,
    daily_salary numeric(18,4),
    gross_total numeric(18,2) NOT NULL,
    deductions_total numeric(18,2) NOT NULL,
    net_total numeric(18,2) NOT NULL,
    source_json jsonb NOT NULL,
    CONSTRAINT uq_payroll_amount_employee UNIQUE (import_id,payroll_employee_id),
    CONSTRAINT uq_payroll_amount_row UNIQUE (import_id,source_row)
);
CREATE INDEX IF NOT EXISTS ix_payroll_amount_import_lines_import_id ON payroll_amount_import_lines(import_id);
CREATE INDEX IF NOT EXISTS ix_payroll_amount_import_lines_payroll_employee_id ON payroll_amount_import_lines(payroll_employee_id);
COMMIT;
