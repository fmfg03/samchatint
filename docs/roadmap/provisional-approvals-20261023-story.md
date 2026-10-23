# Flujo provisional de aprobaciones — historia aprobada

Fecha: 2026-10-23. Base investigada: `d591cc4a13987d33d2336bcf872401f3a6c7acb6`, coincidente con producción y `main` al investigar.

## Autoridad y fase

El usuario confirmó las reglas y respondió «sí, sí, no»: basta cualquiera de los aprobadores alternativos, la supervisión global de Federico GV también incluye INFORMES, y el motivo de rechazo de Control Presupuestal es opcional. Después aprobó el alcance mediante «apruebo». Esta es aprobación de historia; la especificación técnica, diff final y efectos de publicación/producción conservan sus checkpoints. No se vuelve a pedir aprobación de estas reglas de negocio.

## Historia

Como operador de Plataforma Sports, necesito que solicitudes e informes lleguen al aprobador provisional correcto, que la referencia de Operaciones no cambie indebidamente esa autoridad, y poder registrar opcionalmente el motivo de un rechazo presupuestal.

## Reglas y aceptación

- AC1. Si solicitante **o** beneficiario empleado efectivo es Odilón, Olof, Luis Ángel o Federico GV, la ruta asignada corresponde a Federico GN, con prioridad sobre el proyecto. No se infiere identidad por nombre de un proveedor.
- AC2. Federico GV puede visualizar, aprobar y rechazar cualquier SOLICITUD o INFORME. Su facultad de supervisión es independiente de quién recibe la ruta asignada; mantiene los estados y controles financieros del documento.
- AC3. Para el resto de casos, la asignación es por proyecto, independientemente del solicitante. La matriz íntegra está en la especificación.
- AC4. Donde hay dos aprobadores alternativos, la primera aprobación válida basta; no se exigen ambos ni se produce doble transición.
- AC5. Gestión de Patrocinios y Gestión RRSS y Transmisiones no consumen folios de Operaciones al crear/enviar solicitudes e informes, incluso si el creador es de Operaciones. Los documentos históricos conservan sus folios emitidos y su trazabilidad; tenerlos no cambia la ruta a Odilón.
- AC6. Bandeja, detalle, autorización del backend, notificaciones, Telegram y asistente usan la misma política efectiva.
- AC7. Control Presupuestal admite motivo opcional al rechazar individualmente o por lote. Vacío o ausente permite rechazar. El motivo se conserva con actor/evento y se muestra escapado. No se hace opcional el motivo exigido por otros recorridos.
- AC8. Se conserva la semántica actual: INFORME devuelto por Control pasa a borrador y reabre su cuenta; SOLICITUD rechazada pasa a rechazado. Reembolsos derivados, AMEX, pago y contabilización mantienen sus propietarios y guardas.
- AC9. El caso real confirmado por el usuario es **S-26000450**. Su corrección de ruta requiere preview y recibo propio, sin modificar su importe, estado ni folio histórico 363.

## Fuera de alcance

No aprobar/pagar documentos automáticamente; no alterar pólizas, impuestos, importes, presupuestos o folios históricos; no rehacer catálogos ni la conciliación presupuestal. La recepción de `catalogo_presupuestal (3).xlsx` es un alcance separado y no cancela esta historia. No se incorpora un backfill indiscriminado ni se modifica el alcance de los roles organizacionales de Dirección.

## Caminos de fallo

Identidades/configuración ausentes o ambiguas, contexto cambiado, usuario inactivo/no autorizado y estados no elegibles bloquean la decisión con causa visible. Un folio o snapshot antiguo no sustituye evidencia de la ruta vigente. Fallos y carreras no dejan efectos parciales.
