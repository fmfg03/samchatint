# AMEX: especificación de corrección auditada de signos

Fecha: 2026-10-22. Historia aprobada en conversación: `/tmp/samchat-amex-sign-correction-story-20261022.md`. Estado: especificación y texto exacto del canon aprobados explícitamente en conversación. Implementación autorizada; reparación productiva aún pendiente de validación y publicación.

## Resultado y alcance

Corregir exclusivamente el estado directo `43119aac-2cbe-4875-ad88-9133c187221c`, con las mismas 16 filas y la misma URL. Declaración original `debit_negative`; interpretación corregida `debit_positive`. Resultado esperado: 14 cargos por MXN 8,747.10 y 2 abonos por MXN 17,357.40. Google fila 6 +939.00 será cargo; Amazon fila 10 -46.53 será abono.

El CSV, cabecera y comprobantes de importación, campos físicos de `amex_direct_items`, importes originales, fechas, identidad fiscal y tarjeta permanecen como evidencia original. No se reimporta ni se altera el archivo. No se requiere nueva dependencia, UI general ni tarea de fondo. Se conservarán las decisiones de negocio pendientes; esta reparación no vincula CFDI, clasifica, asigna presupuesto, excluye abonos ni genera póliza.

## Evidencia y propietarios existentes

Runtime objetivo: `samchat-gastos.service`, aplicación `copa_telmex_dashboard.py`, dominio `src/devnous/gastos`, PostgreSQL directo. Fuente inspeccionada: release `gastos-prod-14c17c538-amex-no-cfdi`.

- `amex_direct_intake_service.py`: parser puro y propietario de creación de gastos mediante `expense_service.create_expense_from_data`.
- `amex_direct_effective_source_service.py`: relación SQL canónica `amex_direct_effective_sources`, que ya superpone revisiones de recuperación por período a fuentes físicas inmutables.
- `amex_direct_common.py`: autorización, bloqueo de estado antes de reconocimiento global, prohibición de corte, versiones, hashes y eventos.
- `amex_direct_context_service.py`: listado efectivo; algunos campos presentados aún proceden del objeto físico y deben resolverse coherentemente.
- `amex_direct_schema_service.py` y `amex_direct_schema_contract.py`: admisión de vista, funciones y triggers exactos; una modificación SQL requiere contrato actualizado.
- Model `ExpenseReport`: estado `cancelado`, `cancelado_en`, `cancelado_por_id`, `motivo_cancelacion`; existe transición en `routes/user_routes.py:cancelar_gasto`. La reparación no invocará handlers HTTP ni simulará una identidad de sesión.
- Recuperación de período existente admite exclusivamente cargos omitidos por fecha. No se extenderá su significado para registrar esta corrección.

## Arquitectura propuesta

Extender el patrón existente de fuente efectiva con una revisión específica de interpretación de signos. Dos tablas nuevas, con pertenencia exacta y protección append-only:

1. `amex_direct_sign_revisions`: UUID, import_id único, archivo/hash/parser originales, perfil anterior y nuevo, actor, motivo, timestamp de servidor, versión fuente esperada, contexto y versión, clave idempotente, hash de previsualización y prueba de pertenencia; conteos y sumas antes/después. Sólo una corrección de signos por estado en este alcance. Unique para import y para ámbito de idempotencia. No historial de correcciones arbitrarias.
2. `amex_direct_sign_rows`: UUID, revision_id, direct_item_id, import_item_id, row_number, perfil/movement_kind efectivos, fuente JSON efectiva, raw_signed_amount conservado, imported_expense_id efectivo nullable, original_expense_id nullable y hash de prueba. Unique por revisión/fila, por direct_item y por gasto efectivo no nulo. Deben existir exactamente las 16 filas de la carga y una relación 1:1 con los recibos originales.

FK explícitas a los propietarios existentes. Ninguna escritura por startup. Las columnas nuevas de la vista se añaden al final para conservar las existentes: `sign_revision_id`, `sign_row_id`, `sign_proof_hash`. `EffectiveSource` y el mapeo read-only incorporan esa evidencia.

La vista resolverá primero la revisión de signos, después la recuperación de período y finalmente la fuente física. Sin embargo, para esta primera versión se rechaza cualquier estado con recuperación por período: no se permite combinar revisiones no verificadas. Para estados sin revisión de signos, todas las columnas anteriores conservan exactamente su semántica.

Usar CASE por presencia de fila de revisión, no COALESCE para imported_expense_id: un abono corregido debe resolver explícitamente NULL aunque el registro físico original apunte a un gasto. La fuente efectiva conserva celdas y signo originales; sólo cambia la interpretación y su perfil. La cabecera y contexto originales siguen declarando el perfil de carga original; los consumidores obtienen el perfil efectivo desde la revisión. No crear un nuevo contexto fiscal idéntico ni presentar la declaración original como si hubiera sido modificada.

## Propietario y operación de mantenimiento

Nuevo servicio `amex_direct_sign_correction_service.py` con dos operaciones: preview y confirm. Reutiliza carga/autorización/bloqueos/eventos y creación de gastos del dominio. Comando específico `scripts/correct_amex_direct_sign_profile.py`, sin endpoint nuevo ni botón: `preview` por defecto, `apply` exige import_id exacto, actor UUID explícito, motivo, nuevo perfil, clave idempotente, versión y hash de preview. No actor por defecto, no impersonación del usuario que subió el archivo, no imprimir credenciales.

La aplicación productiva de esta tarea queda restringida por manifiesto al UUID anterior y a la preimagen revisada. No ejecutar sobre otros estados. El actor efectivo debe ser un empleado activo con la autoridad financiera y ámbito de tarjeta/entidad ya existentes; la aprobación conversacional no sustituye esa comprobación. Se comprobará el actor disponible antes del paso productivo y se informará si falta.

### Preview de sólo lectura

1. Autorizar el actor y resolver alcance. Leer estado, contexto, fuente original y recibos en transacción read-only.
2. Validar SHA-256 del contenido y parser soportado. Reproducir el perfil original y comparar cada recibo/fila física, no sólo conteos o ejemplos.
3. Reinterpretar exactamente el mismo contenido con perfil opuesto. Soportar sólo inversión explícita `debit_negative` / `debit_positive`; rechazar separate_columns, filas ambiguas, omitidas, cero, moneda distinta de MXN, fechas fuera del período o diferencias de pertenencia.
4. Comprobar independencia de cada registro anterior: sin CFDI, evidencia SAT vinculada, decisión Sin CFDI, selección de cuenta histórica, reconocimiento/representación/consumo, pago, póliza, documento/informe/cuenta de gastos o presupuesto. Tampoco selección/exclusión de crédito u otros efectos incompatibles. La búsqueda debe cubrir ambos gastos originales y todas las dependencias por ID de fila/estado/gasto; no basta con flags de pantalla.
5. Exigir pendiente contable y sin clasificación, sin cortes/completion, recuperación o adopción incompatible. Rechazar toda dependencia encontrada, sin limpiarla automáticamente.
6. Emitir manifiesto sin contenido completo del CSV ni RFC: identificadores, versiones, hash del archivo, cambios por fila, gastos a cancelar/crear y totales. Calcular hash de preview sobre la preimagen y el plan canónicos. No crear gastos durante preview.

### Confirmación transaccional

1. Validar argumentos y confirmación. Resolver primero replay: misma clave y payload exactos devuelve recibo anterior sin crear gastos; misma clave con otro payload se rechaza. Un segundo intento distinto para estado ya corregido se rechaza.
2. Bloquear cabecera/estado y reconocimiento global en el orden del propietario existente; bloquear los dos gastos originales y dependencias mutables con orden estable. Recalcular preview/autoridad/configuración bajo bloqueo. Hash/versiones diferentes: abortar.
3. Crear revisión y sus 16 filas mediante savepoint/flush en orden compatible con FK y guardas diferidas. Para cada crédito original que será cargo, crear gasto mediante `create_expense_from_data`, con mismos datos bancarios, empleado, tarjeta y moneda que intake. En este caso se crean 14 gastos.
4. Para los dos débitos originales que serán abono, efectuar cancelación auditada de los gastos originales sin borrarlos. Reutilizar las reglas comprobadas de cancelación; si no existe servicio compartido, implementar un helper de dominio limitado a gastos AMEX de esta revisión, con los campos de cancelación existentes y registro de evento. No modificar el handler de cancelación general para este alcance. El gasto cancelado mantiene sus importes y trazabilidad original.
5. Ningún abono efectivo tiene gasto activo asociado. Registrar por fila IDs anterior/efectivo, motivo y prueba. Incrementar versiones de estado/filas según propietario; recalcular hashes incorporando evidencia efectiva de revisión incluso cuando no hay recovery_id. Invalidar progreso/preview anteriores; no resetear silenciosamente decisiones existentes (se rechazaron antes).
6. Commit único con guardas de integridad diferidas. Fallo en cualquier paso: rollback total, incluidos gastos y cancelaciones. Repetición tras pérdida de respuesta devuelve el mismo recibo.

## Integridad SQL y consumidores

Migración owner-run nueva `database/migrations/20261022_amex_direct_sign_correction.sql` y rollback guardado. Conservar íntegramente la inmutabilidad de CSV, cabecera, recibos y fuente física.

Las guardas nuevas validan bajo lock del estado: revisión única, autoridad registrada, preimagen/contexto/perfil, pertenencia exacta de filas, ausencia de efectos incompatibles, datos bancarios iguales a los recibos, perfil y signo coherentes, gasto efectivo por cada cargo y NULL por cada abono, cancelación auditada de los gastos sustituidos y versión final mayor. Un sello diferido exige que no pueda confirmarse una revisión incompleta. Revisiones y filas no admiten UPDATE/DELETE; un trigger de bloqueo de corte protege su evidencia.

Actualizar las guardas existentes que verifican fuentes, consumos, decisiones Sin CFDI y snapshots para usar la fuente efectiva y probar la revisión de signos cuando exista, manteniendo todas las comprobaciones actuales de IVA/retenciones/cortes y la igualdad multiconjunto de líneas. No sustituir guardas completas por versiones antiguas ni habilitar excepciones mediante variables de sesión.

Revisar propietarios de conciliación, presupuesto, settlement/prior, clasificación, hash, CFDI, corte, exportación, contexto y listados por todo acceso a imported_expense_id/movement_kind/profile físico. Cambiar sólo accesos cuya semántica deba ser efectiva; conservar acceso físico para auditoría. Snapshots y exportación contienen la evidencia de la revisión, no alteran el importe original. Cortes posteriores validan los IDs efectivos, no los dos gastos cancelados. Las UI existentes deben mostrar la interpretación correcta y distinguir perfil original y corregido, mediante un aviso pequeño en el mismo estilo actual, sin formulario nuevo.

Replays del archivo original permanecen idempotentes: al detectar una revisión ya aplicada deben resolver al mismo estado y mostrar su interpretación efectiva, no fallar por confundir el perfil original con el efectivo ni crear otra carga.

## Archivos previstos

- Nuevos servicio, script de mantenimiento y dos migraciones anteriores.
- `src/devnous/gastos/models.py`: modelos de las dos tablas; sin DDL automático.
- `amex_direct_effective_source_service.py`, `amex_direct_common.py`, `amex_direct_schema_service.py`, `amex_direct_schema_contract.py`.
- `amex_direct_context_service.py`, `amex_direct_intake_service.py`, `amex_direct_recovery_service.py` (rechazo de combinación), y consumidores de matching/budget/accounting/no_cfdi/settlement sólo donde la revisión efectiva lo requiera.
- `routes/admin_amex_direct_routes.py`: presentación coherente y aviso de revisión, sin nueva acción UI.
- Tests unitarios, PostgreSQL, aceptación HTTP/browser y contratos de rutas/schema afectados.
- `docs/roadmap/amex-sign-correction-20261022-*` con historia, spec, build/aceptación/validación y recibos públicos sin información sensible.
- Enmienda explícita al canon de ingeniería y hash correspondiente en registro, según el texto propuesto al final.

Si el inventario descubre otro propietario obligatorio fuera de esta lista funcional, documentarlo antes de tocarlo; si cambia arquitectura o regla de negocio, detener sólo esa ampliación y presentarla para aprobación.

## Verificación y aceptación

Mapear AC1–10 de la historia en reporte. Probar parser con el patrón real de signos (fixture sintético, sin publicar el CSV privado). En PG desechable: 16 fuentes físicas idénticas antes/después, 16 efectivas corregidas, dos gastos cancelados y 14 activos nuevos; ausencia de duplicados; hashes/versiones/contexto correctos. Probar CASE NULL contra regresión de COALESCE.

Casos negativos: archivo/hash/parser alterados, sólo 15/17 filas, mezcla de estados, perfil inválido, contexto/actor fuera de ámbito, cada dependencia incompatible, revisión adulterada o incompleta, escritura directa que no cumpla sellos, y modificaciones de snapshot/consumo. Verificar que la fuente física y el corte siguen inmutables.

Concurrencia: dos confirmaciones idénticas, dos claves distintas y confirmación contra vínculo CFDI/clasificación/corte; obtener sólo un resultado coherente o rechazo sin efectos parciales. Inyectar fallo entre cancelación y creación y comprobar rollback real.

Recorrido HTTP/browser: Google seleccionable, Amazon sin casilla de cargo, cuenta de cargos/abonos y sumas correctas, histórico original accesible, gastos cancelados fuera de pendientes activos. Completar en PG desechable el flujo autorizado desde conciliación a preview/póliza/export con los cargos efectivos y los abonos tratados mediante las decisiones existentes; no inferir ni excluir créditos automáticamente. Probar replay/export y no duplicación; no hacer UAT sintético en producción.

Regresión focal de recovery, intake/replay, Sin CFDI, prior/settlement, presupuesto, CFDI y cortes, incluidos estados sin revisión. Ejecutar lint/tipos de alcance, cobertura cambiada >=85%, compilación y gates del repo. Clasificar fallos ajenos en vez de reparar fuera de alcance. Verificador de aceptación y validador de sólo lectura independientes antes del diff final/PR.

## Promoción y reparación productiva

Después de implementación, verificaciones y revisión humana del diff final: completar efectos Git y promoción sólo con la autorización correspondiente. La autorización previa de corregir este estado se conserva; no autoriza otros estados ni otros cambios productivos.

Antes de DDL o reparación: confirmar release/DB/schema/actor, obtener backup y verificar restaurabilidad, almacenar preimagen/manifiesto de 16 filas en directorio privado de auditoría. No incluir datos privados en Git. Planificar compatibilidad de contrato SQL exacto: detener de forma controlada las escrituras del dominio durante cambio de esquema/release, no dejar el runtime anterior atendiendo con una vista incompatible. Aplicar migración sin business backfill, promover release inmutable, comprobar schema/gates/health/readiness/ruta protegida y sólo después aplicar la operación exacta con la preimagen revisada.

Post-aplicación: verificar las 16 filas, ambas muestras, conteos/sumas, cancelaciones, nuevas anclas, ausencia de duplicados, integridad del CSV y otras cargas sin cambios. Guardar recibo transaccional. La reparación no genera póliza ni equivale a UAT contable aceptada.

Rollback: antes de uso, SQL de reversión sólo si tablas de revisión están vacías y no hay dependencias; restaurar esquema/contrato y release compatibles. Tras aplicar una revisión, prohibir rollback destructivo y downgrade que ignore la revisión. Conservar release compatible y corregir hacia adelante mediante plan auditado separado; restauración desde backup sólo mediante recuperación explícita coordinada, no automática. No ofrecer borrar evidencia o reactivar gastos cancelados para facilitar rollback.

## Canon: texto exacto propuesto para revisión humana

Añadir al canon de ingeniería una enmienda titulada **Corrección auditada de signos AMEX directo**, con fecha 2026-10-22 y el texto siguiente; no editar los otros dos canons. Actualizar hash en el registro en el mismo PR. El estado descrito es diseño aprobado, no despliegue.

> Approved design, 2026-10-22: AMEX DIRECT sign-profile corrections preserve the original statement file, import receipts and physical source fields. A dedicated append-only revision may supply the effective movement type and expense anchor only for an unrecognized, unclassified statement with no incompatible fiscal, payment, budget or accounting dependencies. The operation requires the owning financial authority, exact-source preview, confirmation, idempotency, audit and atomic application. Expense anchors created under the superseded interpretation must be cancelled with traceability; corrected debits receive their own anchors through the expense owner. All matching, budget and accounting consumers must use the same effective revision. No automatic correction, trigger bypass, historical backfill or new fiscal classification is authorized. This amendment records approved design only; implementation, deployment and Finance acceptance require their own evidence.

La aprobación de esta especificación incluirá la revisión humana de ese texto exacto. Si no se aprueba el texto, no se editará el canon y se resolverá ese punto antes del PR.
