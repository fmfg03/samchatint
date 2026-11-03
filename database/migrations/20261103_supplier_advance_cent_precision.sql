-- Owner-run after 20261008_supplier_advances.sql; never runtime/startup DDL.
-- Align the paid-balance comparison with money()/ROUND_HALF_UP at two decimals.
-- Preserve historical amounts, reservations, parent locking and evidence guards.
-- Before production application: verified backup and save pg_get_functiondef().
-- Rollback: restore that saved function definition; do not rewrite documents.
BEGIN;
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
        IF NEW.estado NOT IN ('rechazado','cancelado') AND reserved+NEW.supplier_advance_applied>round(parent.monto_solicitado, 2) THEN
            RAISE EXCEPTION 'Supplier advance allocation exceeds paid balance';
        END IF;
    END IF;
    RETURN NEW;
END $$;
COMMIT;
