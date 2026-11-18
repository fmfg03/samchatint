-- Run as the schema owner. Set samchat.direction_bank_reader to the approved
-- application role on each run to grant SELECT. Otherwise access is owner-only.
BEGIN;

CREATE OR REPLACE FUNCTION public.direction_bank_cert_uuid_set(items uuid[])
RETURNS uuid[]
LANGUAGE sql
IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog
AS $$
    SELECT ARRAY(SELECT DISTINCT value FROM unnest(items) AS value
                 WHERE value IS NOT NULL ORDER BY value)
$$;

CREATE OR REPLACE FUNCTION public.direction_bank_cert_scope_digest(items uuid[])
RETURNS bytea
LANGUAGE sql
IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog
AS $$
    SELECT sha256(convert_to(array_to_string(items, ','), 'UTF8'))
$$;

CREATE TABLE IF NOT EXISTS public.direction_bank_statement_certifications (
    id uuid PRIMARY KEY,
    import_run_id uuid NOT NULL REFERENCES public.accounting_import_runs(id) ON DELETE RESTRICT,
    source_sha256 varchar(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    series_sha256 varchar(64) NOT NULL CHECK (series_sha256 ~ '^[0-9a-f]{64}$'),
    account_fingerprint varchar(64) NOT NULL CHECK (account_fingerprint ~ '^[0-9a-f]{64}$'),
    account_id uuid NOT NULL,
    company_id uuid NOT NULL,
    account_last4 varchar(4) NOT NULL CHECK (account_last4 ~ '^[0-9]{4}$'),
    currency varchar(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    portfolio_id uuid NOT NULL REFERENCES public.client_executive_portfolios(id) ON DELETE RESTRICT,
    tournament_ids uuid[] NOT NULL CHECK (
        cardinality(tournament_ids) > 0
        AND tournament_ids = public.direction_bank_cert_uuid_set(tournament_ids)
    ),
    scope_account_ids uuid[] CHECK (
        account_id = ANY(scope_account_ids)
        AND scope_account_ids = public.direction_bank_cert_uuid_set(scope_account_ids)
    ),
    coverage_start date NOT NULL,
    coverage_end date NOT NULL,
    opening_balance numeric(20, 2) CHECK (opening_balance <> 'NaN'::numeric),
    closing_balance numeric(20, 2) CHECK (closing_balance <> 'NaN'::numeric),
    descending_rows boolean NOT NULL,
    evidence_ref uuid NOT NULL,
    certified_by uuid NOT NULL REFERENCES public.empleados(id) ON DELETE RESTRICT,
    certified_at timestamptz NOT NULL CHECK (isfinite(certified_at)),
    revoked_at timestamptz,
    revoked_by uuid REFERENCES public.empleados(id) ON DELETE RESTRICT,
    revocation_reason text,
    CONSTRAINT direction_bank_cert_coverage CHECK (
        isfinite(coverage_start) AND isfinite(coverage_end) AND coverage_end >= coverage_start
    ),
    CONSTRAINT direction_bank_cert_revocation CHECK (
        (revoked_at IS NULL AND revoked_by IS NULL AND revocation_reason IS NULL)
        OR (revoked_at IS NOT NULL AND isfinite(revoked_at) AND revoked_at >= certified_at
            AND revoked_by IS NOT NULL AND revocation_reason IS NOT NULL
            AND btrim(revocation_reason) <> '')
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_direction_bank_current_source_scope
    ON public.direction_bank_statement_certifications
        (import_run_id, account_id, portfolio_id,
         public.direction_bank_cert_scope_digest(tournament_ids))
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_direction_bank_current_scope_range
    ON public.direction_bank_statement_certifications
        (portfolio_id, coverage_end, coverage_start)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_direction_bank_current_account_range
    ON public.direction_bank_statement_certifications
        (account_fingerprint, coverage_end, coverage_start)
    WHERE revoked_at IS NULL;

REVOKE ALL ON TABLE public.direction_bank_statement_certifications FROM PUBLIC CASCADE;

DO $$
DECLARE
    reader text := nullif(current_setting('samchat.direction_bank_reader', true), '');
    acl_role name;
BEGIN
    -- Inherited default grants must not authorize this register.
    FOR acl_role IN
        SELECT DISTINCT role.rolname
        FROM pg_class AS relation
        CROSS JOIN LATERAL aclexplode(relation.relacl) AS acl
        JOIN pg_roles AS role ON role.oid = acl.grantee
        WHERE relation.oid = 'public.direction_bank_statement_certifications'::regclass
          AND acl.grantee <> relation.relowner
    LOOP
        EXECUTE format(
            'REVOKE ALL ON TABLE '
            'public.direction_bank_statement_certifications FROM %I CASCADE', acl_role);
    END LOOP;

    IF reader IS NOT NULL THEN
        IF has_table_privilege(reader, 'public.direction_bank_statement_certifications',
                               'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
           OR EXISTS (
               SELECT 1 FROM pg_class
               WHERE oid = 'public.direction_bank_statement_certifications'::regclass
                 AND pg_has_role(reader, relowner, 'MEMBER')
           ) THEN
            RAISE EXCEPTION 'The configured Direction bank reader has write authority';
        END IF;
        EXECUTE format('GRANT SELECT ON TABLE public.direction_bank_statement_certifications '
                       'TO %I', reader);
    END IF;
END
$$;

COMMIT;
