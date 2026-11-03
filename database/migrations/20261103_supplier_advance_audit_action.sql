-- Owner-run after 20261010_late_support_audit_actions.sql and cent precision.
-- Only admit the event already emitted by submit_supplier_invoice.
-- No financial rows, historical audit events or runtime startup patches change.
-- Preserve a verified backup and the exact prior constraint definition first.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
LOCK TABLE aprobaciones IN ACCESS EXCLUSIVE MODE;
DO $audit_action$
DECLARE
    allowed_actions text[] := ARRAY[
        'enviar',
        'enviar_control_presupuestal',
        'rechazar_control_presupuestal',
        'asignar_partida_presupuestal',
        'asignar_partida_presupuestal_linea',
        'asignar_partidas_presupuestales',
        'reversar_a_control_presupuestal',
        'aprobar',
        'rechazar',
        'cancelar',
        'editar',
        'pagar',
        'aprobar_area',
        'rechazar_area',
        'aprobar_final',
        'rechazar_final',
        'retirar',
        'adjuntar_comprobante_no_deducible',
        'reemplazar_comprobante_no_deducible',
        'eliminar_comprobante_no_deducible',
        'confirmar_cfdi_compartido',
        'regularizar_aprobacion_reembolso',
        'liberar_cfdi_duplicado',
        'adjuntar_soporte',
        'adjuntar_factura'
    ];
    current_definition text;
    is_validated boolean;
    old_array text;
    new_array text;
    old_definitions text[];
    new_definitions text[];
BEGIN
    SELECT pg_get_constraintdef(oid), convalidated
      INTO current_definition, is_validated
      FROM pg_constraint
     WHERE conrelid = 'aprobaciones'::regclass
       AND conname = 'aprobaciones_accion_check' AND contype = 'c';
    IF current_definition IS NULL OR is_validated IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Supplier audit action requires a validated existing action CHECK';
    END IF;

    SELECT 'ARRAY[' || string_agg(format('%L::text', action), ', ' ORDER BY ordinal) || ']'
      INTO old_array FROM unnest(allowed_actions) WITH ORDINALITY AS a(action, ordinal);
    SELECT 'ARRAY[' || string_agg(format('%L::text', action), ', ' ORDER BY ordinal) || ']'
      INTO new_array
      FROM unnest(allowed_actions || ARRAY['comprobar_anticipo_proveedor'])
           WITH ORDINALITY AS a(action, ordinal);
    -- Exact deparsed shapes for production text and ORM varchar columns.
    -- Unexpected clauses, action sets or nonvalidated CHECKs fail closed.
    old_definitions := ARRAY[
        format('CHECK ((accion = ANY (%s)))', old_array),
        format('CHECK (((accion)::text = ANY (%s)))', old_array)
    ];
    new_definitions := ARRAY[
        format('CHECK ((accion = ANY (%s)))', new_array),
        format('CHECK (((accion)::text = ANY (%s)))', new_array)
    ];
    IF current_definition = ANY (new_definitions) THEN
        RETURN;
    END IF;
    IF NOT (current_definition = ANY (old_definitions)) THEN
        RAISE EXCEPTION 'Unexpected supplier audit action CHECK; owner review required';
    END IF;
    ALTER TABLE aprobaciones DROP CONSTRAINT aprobaciones_accion_check;
    EXECUTE format(
        'ALTER TABLE aprobaciones ADD CONSTRAINT aprobaciones_accion_check CHECK (accion = ANY (%s))',
        new_array
    );
END $audit_action$;
COMMIT;
