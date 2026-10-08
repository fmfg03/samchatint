-- Owner-run migration, before application deployment. No runtime DDL.
BEGIN;
ALTER TABLE documentos
    ADD COLUMN IF NOT EXISTS is_supplier_advance boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS supplier_advance_due_date date,
    ADD COLUMN IF NOT EXISTS supplier_advance_id uuid REFERENCES documentos(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS supplier_advance_applied numeric(18,2),
    ADD COLUMN IF NOT EXISTS supplier_invoice_total numeric(18,2);
CREATE INDEX IF NOT EXISTS ix_documentos_supplier_advance_id ON documentos(supplier_advance_id);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='documentos'::regclass AND conname='ck_supplier_advance_document') THEN
        ALTER TABLE documentos ADD CONSTRAINT ck_supplier_advance_document CHECK (
            (NOT is_supplier_advance OR (
                tipo='SOLICITUD' AND proveedor_cliente_id IS NOT NULL
                AND beneficiario_empleado_id IS NULL AND cuenta_gastos_id IS NULL
                AND supplier_advance_id IS NULL AND supplier_advance_due_date IS NOT NULL
                AND cfdi_report_id IS NULL AND monto_solicitado > 0 AND monto_total = monto_solicitado
            )) AND (supplier_advance_id IS NULL OR (
                NOT is_supplier_advance AND tipo='SOLICITUD'
                AND proveedor_cliente_id IS NOT NULL AND beneficiario_empleado_id IS NULL
                AND cuenta_gastos_id IS NULL AND supplier_advance_id <> id
                AND supplier_advance_applied IS NOT NULL AND supplier_invoice_total IS NOT NULL
                AND monto_total IS NOT NULL AND monto_solicitado IS NOT NULL
                AND supplier_advance_applied > 0 AND supplier_invoice_total > 0
                AND monto_total >= 0 AND monto_solicitado = monto_total
                AND supplier_invoice_total = supplier_advance_applied + monto_total
                AND cfdi_report_id IS NOT NULL
            ))
        );
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS ux_supplier_invoice_active_cfdi ON documentos(cfdi_report_id)
    WHERE supplier_advance_id IS NOT NULL AND estado NOT IN ('rechazado','cancelado');
CREATE UNIQUE INDEX IF NOT EXISTS ux_supplier_advance_posting_event ON accounting_polizas(origen,numero_poliza)
    WHERE origen IN ('proveedor_anticipo_pago','proveedor_anticipo_factura','proveedor_anticipo_aplica','proveedor_anticipo_remanente');
-- Lock the same parent for every allocation. Application guards alone cannot
-- protect two concurrent submissions from spending the same advance balance.
CREATE OR REPLACE FUNCTION guard_supplier_advance_document() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent documentos%ROWTYPE; reserved numeric;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_supplier_advance OR OLD.supplier_advance_id IS NOT NULL THEN
            RAISE EXCEPTION 'Supplier advance evidence cannot be deleted';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.is_supplier_advance AND OLD.pagado_en IS NOT NULL THEN
        IF ROW(NEW.is_supplier_advance,NEW.empleado_id,NEW.proveedor_cliente_id,NEW.monto_solicitado,NEW.monto_total,NEW.currency,NEW.torneo_id,NEW.proyecto_otro,NEW.fase,NEW.estado,NEW.pagado_en)
          IS DISTINCT FROM ROW(OLD.is_supplier_advance,OLD.empleado_id,OLD.proveedor_cliente_id,OLD.monto_solicitado,OLD.monto_total,OLD.currency,OLD.torneo_id,OLD.proyecto_otro,OLD.fase,OLD.estado,OLD.pagado_en) THEN
            RAISE EXCEPTION 'Paid supplier advance requires accounting reversal';
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.supplier_advance_id IS NOT NULL THEN
        IF ROW(NEW.supplier_advance_id,NEW.supplier_advance_applied,NEW.supplier_invoice_total,NEW.cfdi_report_id,NEW.monto_solicitado,NEW.monto_total,NEW.empleado_id,NEW.proveedor_cliente_id,NEW.currency,NEW.torneo_id,NEW.proyecto_otro,NEW.fase)
          IS DISTINCT FROM ROW(OLD.supplier_advance_id,OLD.supplier_advance_applied,OLD.supplier_invoice_total,OLD.cfdi_report_id,OLD.monto_solicitado,OLD.monto_total,OLD.empleado_id,OLD.proveedor_cliente_id,OLD.currency,OLD.torneo_id,OLD.proyecto_otro,OLD.fase) THEN
            RAISE EXCEPTION 'Supplier invoice allocation and evidence are immutable';
        END IF;
        IF OLD.estado IN ('aprobado','en_proceso_pago','pagado','cerrado') AND NEW.estado NOT IN ('aprobado','en_proceso_pago','pagado','cerrado') THEN
            RAISE EXCEPTION 'Approved supplier invoice requires accounting reversal';
        END IF;
        IF OLD.estado IN ('rechazado','cancelado') AND NEW.estado NOT IN ('rechazado','cancelado') THEN
            RAISE EXCEPTION 'Released supplier invoice cannot be reopened';
        END IF;
    END IF;
    IF NEW.supplier_advance_id IS NOT NULL THEN
        SELECT * INTO parent FROM documentos WHERE id=NEW.supplier_advance_id FOR UPDATE;
        IF NOT FOUND OR NOT parent.is_supplier_advance OR parent.estado <> 'pagado' OR parent.pagado_en IS NULL THEN
            RAISE EXCEPTION 'Supplier invoice requires a confirmed paid advance';
        END IF;
        IF ROW(NEW.empleado_id,NEW.proveedor_cliente_id,NEW.currency,NEW.torneo_id,NEW.proyecto_otro,NEW.fase)
          IS DISTINCT FROM ROW(parent.empleado_id,parent.proveedor_cliente_id,parent.currency,parent.torneo_id,parent.proyecto_otro,parent.fase) THEN
            RAISE EXCEPTION 'Supplier invoice identity does not match advance';
        END IF;
        SELECT COALESCE(SUM(supplier_advance_applied),0) INTO reserved FROM documentos
          WHERE supplier_advance_id=parent.id AND id<>NEW.id AND estado NOT IN ('rechazado','cancelado');
        IF NEW.estado NOT IN ('rechazado','cancelado') AND reserved+NEW.supplier_advance_applied>parent.monto_solicitado THEN
            RAISE EXCEPTION 'Supplier advance allocation exceeds paid balance';
        END IF;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS guard_supplier_advance_document ON documentos;
CREATE TRIGGER guard_supplier_advance_document BEFORE INSERT OR UPDATE OR DELETE ON documentos
    FOR EACH ROW EXECUTE FUNCTION guard_supplier_advance_document();
COMMIT;
