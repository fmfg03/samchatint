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
       AND position('beneficiary_onboarding' in current_def) = 0 THEN
        ALTER TABLE aprobaciones
            DROP CONSTRAINT aprobaciones_tipo_entidad_check;

        ALTER TABLE aprobaciones
            ADD CONSTRAINT aprobaciones_tipo_entidad_check
            CHECK (
                tipo_entidad = ANY (
                    ARRAY[
                        'documento'::text,
                        'gasto'::text,
                        'beneficiary_onboarding'::text
                    ]
                )
            );
    END IF;
END $$;
