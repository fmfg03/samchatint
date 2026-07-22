BEGIN;

ALTER TABLE proveedores_clientes
    ADD COLUMN IF NOT EXISTS empleado_id UUID NULL
    REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_proveedores_clientes_empleado_id
    ON proveedores_clientes(empleado_id);

COMMIT;
