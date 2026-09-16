-- Owner-run migration: permit the audited superadmin recovery action.
-- This does not write a recovery record; the application command remains
-- dry-run by default and requires an explicit --apply.
DO $$
DECLARE
    current_def TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid)
    INTO current_def
    FROM pg_constraint
    WHERE conrelid = 'aprobaciones'::regclass
      AND conname = 'aprobaciones_accion_check';

    IF current_def IS NOT NULL
       AND position('regularizar_aprobacion_reembolso' in current_def) = 0 THEN
        ALTER TABLE aprobaciones DROP CONSTRAINT aprobaciones_accion_check;
        ALTER TABLE aprobaciones
            ADD CONSTRAINT aprobaciones_accion_check
            CHECK (
                accion = ANY (
                    ARRAY[
                        'enviar'::text,
                        'enviar_control_presupuestal'::text,
                        'rechazar_control_presupuestal'::text,
                        'asignar_partida_presupuestal'::text,
                        'asignar_partida_presupuestal_linea'::text,
                        'asignar_partidas_presupuestales'::text,
                        'reversar_a_control_presupuestal'::text,
                        'aprobar'::text,
                        'rechazar'::text,
                        'cancelar'::text,
                        'editar'::text,
                        'pagar'::text,
                        'aprobar_area'::text,
                        'rechazar_area'::text,
                        'aprobar_final'::text,
                        'rechazar_final'::text,
                        'retirar'::text,
                        'adjuntar_comprobante_no_deducible'::text,
                        'reemplazar_comprobante_no_deducible'::text,
                        'eliminar_comprobante_no_deducible'::text,
                        'confirmar_cfdi_compartido'::text,
                        'regularizar_aprobacion_reembolso'::text
                    ]
                )
            );
    END IF;
END $$;
