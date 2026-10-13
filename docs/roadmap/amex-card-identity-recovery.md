# AMEX: identificación explícita de tarjeta y recuperación de selectores vacíos

Fecha: 2026-10-13. Estado: historia y especificación aprobadas por el usuario el 2026-10-13; implementación local terminada, pendiente de revisión del diff.

## Historia aprobada

Finanzas puede identificar explícitamente la tarjeta de las partidas AMEX sin ese dato. Conciliación explica los faltantes en lugar de presentar selectores vacíos sin explicación. Se mantienen auditoría, permisos y las validaciones de tarjeta, fecha, moneda e importe. No se asignan tarjetas automáticamente ni se cambian pólizas ya generadas.

## Evidencia actual

Video local: WhatsApp Video 2026-10-12 at 18.14.49.mp4 (76,29 segundos). El usuario confirmó que el problema son las partidas ausentes en los selectores para vincular/generar la póliza.

Runtime: copa_telmex_dashboard.py, samchat-gastos.service, release 514b6542e90c1eaae5c26351bc9ee18e9d0f86e8. Dominio: gastos/AMEX. Persistencia: PostgreSQL directo. Evidencia: código desplegado y conteos actuales en transacción READ ONLY, no UAT autenticada.

Consulta canónica de septiembre: 56 cargos importados activos de tarjeta terminación 1005; cero candidatos visibles. En la ventana de informes usada por la pantalla hay 60 partidas activas no importadas, marcadas AMEX y vinculadas a cuenta/informe, todas sin últimos cuatro dígitos. No significa que esas 60 correspondan a los 56 cargos ni que pertenezcan a la tarjeta 1005. Hay informes aprobados, enviados y borradores.

Las 60 están COI pendiente, sin representación de consumo ni corte AMEX guardado. 14 preceden a la activación del reconocimiento compartido y permanecen sujetas a la revisión histórica canónica.

La selección visible exige igualdad entre últimos cuatro dígitos de partida y cargo. El servicio de vinculación exige además tarjeta activa/cuenta válida, misma fecha y moneda e importe reconciliado. Marcar AMEX y captura rápida pueden producir una partida sin identidad de tarjeta.

## Implementación propuesta

1. En Conciliación AMEX, añadir una sección 'Partidas AMEX sin tarjeta', conservando período. Mostrar referencia de partida/informe, fecha, importe, estado y explicación del bloqueo. Finanzas selecciona explícitamente una tarjeta activa del catálogo y las partidas concretas, motivo y confirmación. No preseleccionar la tarjeta del encabezado ni inferirla del empleado, importe o CFDI. Mostrar navegación a informe y Revisión contable AMEX.
2. Un POST dentro de las rutas AMEX existentes procesa la identificación mediante el dueño de identidad amex_recognition_service, no SQL directo desde la vista. Usar las dependencias de autorización de Finanzas existentes y revalidar actor activo/rol en el servicio; conservar denegaciones del middleware.
3. Servicio transaccional: lock_recognition y bloqueo/refresco de las filas fuente y tarjeta/cuenta seleccionada. Solo partidas activas de informe, no amex_batch, efectivamente marcadas AMEX, con identidad actualmente ausente y sin reconocimiento/corte ni estado COI contabilizado/reversar. Rechazar selección mixta con cualquier partida no elegible sin aplicar cambios parciales. Validar cuenta pasivo AMEX activa mediante el contrato vigente. Resolver pertenencia al informe canónico y excluir informes cancelados/rechazados; el estado aprobado por sí solo no cambia ni se revoca por esta corrección de metadato previo al reconocimiento.
4. Escribir únicamente ultimos_4_digitos. Auditar mediante Aprobacion/acción editar existente: actor, partida, valor anterior/nuevo, tarjeta seleccionada y motivo. No cambiar montos, fechas, CFDI, conceptos, aprobación, cuenta pasivo, pago ni estados COI. Repetición con la misma selección ya aplicada devuelve resultado idempotente sin duplicar auditoría; una identidad distinta se rechaza. Rollback total ante fallo.
5. Mantener revisiones guardadas y fingerprints: la identificación cambia la fuente revisada; el corte exige nueva revisión si la revisión anterior queda obsoleta. No borrar historia ni recalcular cortes. La vinculación sigue siendo una segunda confirmación humana con todas las validaciones existentes.
6. Evitar reincidencia en los controles propios de AMEX del informe: añadir selección explícita de tarjeta del catálogo en marcado masivo y captura rápida cuando se elija AMEX. La edición individual valida una identidad AMEX explícita cuando se declare AMEX. Reutilizar la validación canónica de catálogo; conservar las rutas/formularios existentes. Las partidas históricas sin dato siguen accesibles por la sección de recuperación.
7. Los selectores vacíos explican si faltan partidas registradas, identidad de tarjeta o candidatos del período. No ofrecer partidas de otra tarjeta para saltar la validación. Distinguir 'Identificar tarjeta', 'Confirmar vínculo' y 'Revisar/generar corte'; asignar identidad nunca genera una póliza.
8. Las partidas previas a activación muestran 'Requiere revisión histórica AMEX'. Identificar la tarjeta no las habilita para reconocimiento ni sustituye activation_reason. No crear un nuevo mecanismo de aprobación histórica en esta entrega.

## Archivos previstos

- src/devnous/gastos/routes/user_routes.py: conciliación, POST de identificación y controles de tarjeta en marcado/captura/edición AMEX.
- src/devnous/gastos/services/amex_recognition_service.py: dueño de identificación, elegibilidad, locks y auditoría.
- src/devnous/gastos/services/amex_expense_service.py: reutilizar la selección validada al marcar AMEX.
- Tests AMEX unitarios y de rutas existentes y nuevos archivos focalizados; pruebas de persistencia/atomicidad cuando corresponda al patrón del repo.
- docs/private-plugin/route-inventory.json y route-matrix.csv: regenerar por cambios de rutas/fuentes.
- Informe de implementación con cobertura, comandos y límites.

Sin nueva dependencia de producción, tabla, migración, job, cambio de catálogo o backfill. Trabajo separado de #462 en un worktree aislado basado en main actual.

## Aceptación y pruebas

- Reproducción roja: partida AMEX vinculada a informe, sin últimos cuatro dígitos, excluida por el selector real; la nueva UI la expone como pendiente y ofrece identificación explícita.
- Identificación confirmada en una tarjeta válida hace a la partida visible en el selector de esa tarjeta; no aparece para otra.
- Flujo estándar autorizado: identificar, confirmar vínculo válido, revisar partidas y crear una única póliza por corte mediante servicios existentes. Usar fixtures con fechas posteriores a activación; no ejecutar efectos productivos.
- No cambia la conciliación económica: fecha/moneda/importe no compatibles siguen rechazados.
- Casos negativos: sin permiso/actor inactivo, tarjeta inactiva o cuenta inválida, partida de otro contexto/cancelada/no AMEX/importada, selección incompleta/ambigua, identidad ya distinta, reconocimiento/corte guardado, COI contabilizado/reversar.
- Repetición idéntica no duplica auditoría; concurrencia con vinculación/corte se serializa. Error en lote revierte todo.
- Identificación no altera aprobación ni campos económicos, y revisión contable previa debe revalidarse si cambia el fingerprint.
- Casos históricos continúan bloqueados con explicación y sin bypass del corte de activación.
- Verificar UI de identidad faltante, ayudas, confirmación, navegación y controles de captura/marcado.
- pytest focalizado AMEX, inventario/plugin aislado, controles de formato/lint/tipos del diff, cobertura de código cambiado >=85%, validación independiente de solo lectura. Deuda ajena se atribuye contra baseline y se reporta; CI obligatorio se verifica antes de merge.

## Riesgos y límites

La tarjeta correcta es una decisión de Finanzas. Ninguno de los 60 registros se corregirá en producción durante esta implementación. No se asume equivalencia entre los cargos importados actuales y las partidas; sus validaciones siguen independientes. Los 14 históricos necesitan una revisión aparte antes del reconocimiento: esta entrega hace visible el bloqueo pero no autoriza resolverlo por inferencia.

Rollback de código: volver a la release anterior. Cualquier eventual aplicación productiva de identidad requiere selección explícita, actor, confirmación y auditoría; revertir metadatos ya usados por un corte no se hará con UPDATE ni rollback informal.

Canon unchanged propuesto: se preservan propietarios canónicos, validaciones, autoridad de Finanzas, identidad explícita y corte inmutable; la entrega completa captura/explicación de identidad sin alterar reglas de reconocimiento. Si la implementación requiere relajar una autoridad o control, se detendrá esa parte y se solicitará revisión específica.

## Checkpoints

Historia aprobada por el usuario. Especificación aprobada explícitamente antes de código. Diff final/PR, commit/push/merge y despliegue se reportan según autorización vigente; no se autoriza aquí reparación de datos productivos ni aceptación de negocio.
