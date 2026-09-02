CREATE TABLE IF NOT EXISTS solicitudes_prestamo (
    id UUID PRIMARY KEY,
    numero_referencia VARCHAR(200) NOT NULL,
    solicitante_empleado_id UUID NOT NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE RESTRICT,
    beneficiario_tipo VARCHAR(40) NOT NULL,
    beneficiario_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
    beneficiario_proveedor_cliente_id UUID NULL REFERENCES proveedores_clientes(id) ON UPDATE CASCADE ON DELETE SET NULL,
    beneficiario_nombre_snapshot TEXT NULL,
    banco_beneficiario TEXT NULL,
    cuenta_beneficiario TEXT NULL,
    monto_solicitado NUMERIC(18, 2) NOT NULL,
    saldo_pendiente NUMERIC(18, 2) NOT NULL DEFAULT 0,
    currency VARCHAR(3) NOT NULL DEFAULT 'MXN',
    motivo TEXT NOT NULL,
    estado VARCHAR(40) NOT NULL DEFAULT 'borrador',
    cuenta_deudor_contable_id UUID NULL REFERENCES cuentas_contables(id) ON UPDATE CASCADE ON DELETE SET NULL,
    banco_cuenta_contable_id UUID NULL REFERENCES cuentas_contables(id) ON UPDATE CASCADE ON DELETE SET NULL,
    aprobado_por_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
    cancelado_por_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
    pagado_por_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
    comprobante_pago_filename TEXT NULL,
    comprobante_pago_storage_key TEXT NULL,
    metadata JSONB NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    enviado_en TIMESTAMPTZ NULL,
    aprobado_en TIMESTAMPTZ NULL,
    cancelado_en TIMESTAMPTZ NULL,
    en_proceso_pago_en TIMESTAMPTZ NULL,
    pagado_en TIMESTAMPTZ NULL,
    liquidado_en TIMESTAMPTZ NULL,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS prestamo_abonos (
    id UUID PRIMARY KEY,
    prestamo_id UUID NOT NULL REFERENCES solicitudes_prestamo(id) ON UPDATE CASCADE ON DELETE CASCADE,
    registrado_por_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
    aprobado_por_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
    monto_reportado NUMERIC(18, 2) NOT NULL,
    monto_aplicado NUMERIC(18, 2) NOT NULL DEFAULT 0,
    monto_excedente NUMERIC(18, 2) NOT NULL DEFAULT 0,
    saldo_antes NUMERIC(18, 2) NOT NULL DEFAULT 0,
    saldo_despues NUMERIC(18, 2) NOT NULL DEFAULT 0,
    estado VARCHAR(40) NOT NULL DEFAULT 'enviado',
    excedente_confirmado BOOLEAN NOT NULL DEFAULT FALSE,
    comprobante_filename TEXT NULL,
    comprobante_storage_key TEXT NULL,
    comentario TEXT NULL,
    metadata JSONB NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    enviado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    aprobado_en TIMESTAMPTZ NULL,
    rechazado_en TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_solicitudes_prestamo_referencia
    ON solicitudes_prestamo(numero_referencia);

CREATE INDEX IF NOT EXISTS ix_solicitudes_prestamo_estado
    ON solicitudes_prestamo(estado, creado_en DESC);

CREATE INDEX IF NOT EXISTS ix_solicitudes_prestamo_solicitante
    ON solicitudes_prestamo(solicitante_empleado_id, creado_en DESC);

CREATE INDEX IF NOT EXISTS ix_prestamo_abonos_prestamo_estado
    ON prestamo_abonos(prestamo_id, estado, creado_en DESC);
