-- Billing and collections approval workflow.
-- Execute with the PostgreSQL schema-owner role before promoting the web release.

ALTER TABLE IF EXISTS budget_concepts
    ADD COLUMN IF NOT EXISTS cxc_cuenta_contable_id UUID NULL
    REFERENCES cuentas_contables(id) ON UPDATE CASCADE ON DELETE SET NULL;

ALTER TABLE IF EXISTS budget_cfdi_income_links
    ADD COLUMN IF NOT EXISTS status VARCHAR(40) NOT NULL DEFAULT 'pending_approval';

ALTER TABLE IF EXISTS budget_cfdi_income_links
    ADD COLUMN IF NOT EXISTS approved_by_empleado_id UUID NULL
    REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL;

ALTER TABLE IF EXISTS budget_cfdi_income_links
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ NULL;

ALTER TABLE IF EXISTS budget_cfdi_income_links
    ADD COLUMN IF NOT EXISTS rejected_by_empleado_id UUID NULL
    REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL;

ALTER TABLE IF EXISTS budget_cfdi_income_links
    ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ NULL;

ALTER TABLE IF EXISTS budget_cfdi_income_links
    ADD COLUMN IF NOT EXISTS decision_comment TEXT NULL;

ALTER TABLE IF EXISTS budget_cfdi_income_links
    ADD COLUMN IF NOT EXISTS accounting_poliza_id UUID NULL
    REFERENCES accounting_polizas(id) ON UPDATE CASCADE ON DELETE SET NULL;

ALTER TABLE IF EXISTS budget_cfdi_income_links
    ADD COLUMN IF NOT EXISTS collection_date TIMESTAMPTZ NULL;

ALTER TABLE IF EXISTS budget_cfdi_income_links
    ADD COLUMN IF NOT EXISTS collected_by_empleado_id UUID NULL
    REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL;

ALTER TABLE IF EXISTS budget_cfdi_income_links
    ADD COLUMN IF NOT EXISTS collection_poliza_id UUID NULL
    REFERENCES accounting_polizas(id) ON UPDATE CASCADE ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_budget_cfdi_income_links_status
    ON budget_cfdi_income_links(status);

UPDATE budget_cfdi_income_links link
SET status = 'approved'
WHERE link.unlinked_at IS NULL
  AND link.status = 'pending_approval'
  AND EXISTS (
      SELECT 1
      FROM accounting_polizas poliza
      WHERE poliza.origen = 'cxc_cfdi_income'
        AND poliza.cfdi_report_id = link.cfdi_report_id
  );

DO $$
DECLARE
    current_def TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid)
    INTO current_def
    FROM pg_constraint
    WHERE conrelid = 'aprobaciones'::regclass
      AND conname = 'aprobaciones_tipo_entidad_check';

    IF current_def IS NOT NULL
       AND (
           position('beneficiary_onboarding' in current_def) = 0
           OR position('budget_cfdi_income_link' in current_def) = 0
       ) THEN
        ALTER TABLE aprobaciones
            DROP CONSTRAINT aprobaciones_tipo_entidad_check;

        ALTER TABLE aprobaciones
            ADD CONSTRAINT aprobaciones_tipo_entidad_check
            CHECK (
                tipo_entidad = ANY (
                    ARRAY[
                        'documento'::text,
                        'gasto'::text,
                        'beneficiary_onboarding'::text,
                        'budget_cfdi_income_link'::text
                    ]
                )
            );
    END IF;
END $$;
