BEGIN;

CREATE TABLE IF NOT EXISTS cfdi_duplicate_release_operations (
    id UUID PRIMARY KEY,
    actor_empleado_id UUID NOT NULL REFERENCES empleados(id),
    idempotency_key UUID NOT NULL,
    selection_hash VARCHAR(64) NOT NULL,
    motivo TEXT NOT NULL,
    estado VARCHAR(32) NOT NULL DEFAULT 'aplicada',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ux_cfdi_duplicate_release_operation_key
        UNIQUE (actor_empleado_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS cfdi_duplicate_release_operation_items (
    id UUID PRIMARY KEY,
    operation_id UUID NOT NULL REFERENCES cfdi_duplicate_release_operations(id) ON DELETE CASCADE,
    cfdi_report_id UUID NULL REFERENCES cfdi_reports(id),
    cfdi_uuid VARCHAR(64) NOT NULL,
    resultado VARCHAR(32) NOT NULL,
    motivo_resultado TEXT NULL,
    before_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    after_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_cfdi_duplicate_release_operation_items_operation
    ON cfdi_duplicate_release_operation_items(operation_id);

ALTER TABLE aprobaciones DROP CONSTRAINT IF EXISTS aprobaciones_accion_check;
ALTER TABLE aprobaciones ADD CONSTRAINT aprobaciones_accion_check CHECK (
    accion = ANY (ARRAY[
        'enviar','enviar_control_presupuestal','rechazar_control_presupuestal',
        'asignar_partida_presupuestal','asignar_partida_presupuestal_linea',
        'asignar_partidas_presupuestales','reversar_a_control_presupuestal',
        'aprobar','rechazar','cancelar','editar','pagar','aprobar_area','rechazar_area',
        'aprobar_final','rechazar_final','retirar','adjuntar_comprobante_no_deducible',
        'reemplazar_comprobante_no_deducible','eliminar_comprobante_no_deducible',
        'confirmar_cfdi_compartido','regularizar_aprobacion_reembolso',
        'liberar_cfdi_duplicado'
    ])
);

COMMIT;
