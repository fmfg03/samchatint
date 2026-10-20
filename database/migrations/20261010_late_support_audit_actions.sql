-- Owner-run before application release. No production execution by this task.
-- Idempotent; preserves the canonical workflow action allowlist.
BEGIN;
DO $$
        DECLARE
            current_def TEXT;
        BEGIN
            SELECT pg_get_constraintdef(oid)
            INTO current_def
            FROM pg_constraint
            WHERE conrelid = 'aprobaciones'::regclass
              AND conname = 'aprobaciones_accion_check';

            IF current_def IS NOT NULL THEN
                IF position('enviar_control_presupuestal' in current_def) = 0
                   OR position('rechazar_control_presupuestal' in current_def) = 0
                   OR position('''asignar_partida_presupuestal''::text' in current_def) = 0
                   OR position('asignar_partida_presupuestal_linea' in current_def) = 0
                   OR position('asignar_partidas_presupuestales' in current_def) = 0
                   OR position('reversar_a_control_presupuestal' in current_def) = 0
                   OR position('aprobar_area' in current_def) = 0
                   OR position('rechazar_area' in current_def) = 0
                   OR position('aprobar_final' in current_def) = 0
                   OR position('rechazar_final' in current_def) = 0
                   OR position('retirar' in current_def) = 0
                   OR position('adjuntar_comprobante_no_deducible' in current_def) = 0
                   OR position('reemplazar_comprobante_no_deducible' in current_def) = 0
                   OR position('eliminar_comprobante_no_deducible' in current_def) = 0
                   OR position('confirmar_cfdi_compartido' in current_def) = 0
                   OR position('regularizar_aprobacion_reembolso' in current_def) = 0
                   OR position('liberar_cfdi_duplicado' in current_def) = 0
                   OR position('adjuntar_soporte' in current_def) = 0
                   OR position('adjuntar_factura' in current_def) = 0 THEN
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
                                    'regularizar_aprobacion_reembolso'::text,
                                    'liberar_cfdi_duplicado'::text,
                                    'adjuntar_soporte'::text,
                                    'adjuntar_factura'::text
                                ]
                            )
                        );
                END IF;
            END IF;
        END $$;
COMMIT;
