# Conciliación segura de presupuestos aprobados y expediente contable

Fecha:2026-11-06. Estado: historia propuesta, pendiente de aprobación. Fase actual: investigación y preparación de evidencia. Sin cambios de código ni datos productivos en esta tarea nueva. PR526 y reparación HWC ya completados permanecen intactos.

## Historia

Como responsable de Control Presupuestal necesito conciliar movimientos a partidas existentes de un presupuesto aprobado, conservando su estado, montos y distribución, para completar la información del tablero sin reabrir el presupuesto ni alterar pagos, gastos fuente o pólizas.

## Hechos verificados

El lector canónico encuentra29 movimientos sin línea:10Liga y19Copa.16 tienen un único candidato estricto por identidad de concepto, mismo torneo, fase compatible, concepto activo y versión financiera:6Liga y10Copa.13 están bloqueados:4Liga y9Copa, por concepto inactivo o ausencia de una línea válida. Estas16 propuestas son NUEVAS: no son los16enlaces ya aprobados y aplicados por PR515, que siguen correctos.

El owner genérico exige draft/reforecast; el formulario assign-existing llama primero a upsert de línea. No constituyen una vía segura para este caso aprobado. No se ha demostrado un defecto de agrupación en los50 enlaces actuales: no se cambiarán lectores ni totales por suposición.

Los11 pending_accounting (9Liga/2Copa) y las16 diferencias históricas de fase HWC (3informes/13gastos) son estados reales que se conservan. El catálogo ya acredita cuenta/concepto/torneo; las85 asociaciones documentales aprobadas no acreditan atribución contable exclusiva, moneda/base fiscal ni las17 hojas de entrada de la plantilla26. Se preparó un expediente privado con85pares,879filas de identidades de conceptos y26renglones para revisión, sin aprobar bindings.

## Criterios de aceptación propuestos

1. Desde Conciliación, la autoridad de presupuesto ya configurada puede preparar una vista previa para asignar un movimiento canónico a una línea expense ya existente de una versión approved. No se crean líneas ni conceptos, no se reabre la versión y no cambian montos, semanas, ingresos esperados, aprobación ni nota Borrador. Los flujos draft/reforecast existentes se conservan; no se habilitan versiones frozen.
2. Antes de confirmar se muestran referencia/clave exacta del movimiento, clasificación y destino, motivo, evidencia y advertencias. Se valida nuevamente la fuente, torneo, ejercicio, versión financiera raíz, concepto UUID, fase y actividad del destino. Sin coincidencia suficiente, destino ambiguo/inactivo o clasificación incompatible, se rechaza sin efectos; no se decide por nombre, proveedor o importe.
3. Cada confirmación requiere permisos existentes del proyecto, autenticación y protección CSRF, conserva restricciones de torneo gobernado y deja actor, motivo y before/after auditables. No se amplían roles ni se habilita un writer genérico. Las asignaciones ya existentes no se sobrescriben como parte de una carga de pendientes.
4. Drift de fuente/destino o una carrera concurrente provoca rechazo/rollback íntegro. Repetir la misma confirmación es idempotente. La identidad más específica manda: asignar una línea contable no extiende la decisión a todos los gastos o conceptos del documento.
5. Tras la asignación, el lector canónico y los desgloses/exportes del módulo deben reflejar el mismo destino sin duplicar gasto. Las medidas conservan sus fuentes: compromiso, pago, gasto fiscal y resultado contable no se mezclan ni se convierten con1.16.
6. Las16 propuestas con evidencia se presentan para revisión; no se aplican automáticamente por aprobar esta historia. Las13 bloqueadas y los11 pendientes de contabilización siguen visibles con su motivo. Las discrepancias históricas HWC permanecen sin reescritura.
7. El expediente de85cuentas/26renglones separa hechos demostrados de decisiones contables pendientes. No escribe historical_accounting_binding(s), approved=true, exclusividad, base fiscal, moneda contable ni un reparto Partida1/2 sin validación expresa. Se mantienen N/D, meses retenidos y ausencia de balanza de septiembre.
8. Antes de cualquier aplicación productiva: pruebas unitarias y PostgreSQL reales, autoridad/CSRF, rollback/concurrencia/idempotencia, comparación de datos protegidos, ensayo de las propuestas aprobadas y revisión independiente. Diff y eventual lote de datos tendrán el checkpoint final establecido por AGENTS.

## Fuera de alcance

Resolver diferencias contables entre archivos, inventar meses/balanzas, emitir/contabilizar pólizas, cambiar base fiscal o pagos, cambiar clasificación/fases de registros históricos, reactivar conceptos retirados, poblar bindings o equivalencias financieras no aprobadas, modificar importes autorizados o sustituir UAT/aceptación de Finanzas.

## Continuidad y siguiente fase

La instrucción «vamos a reparar lo que nos corresponde a nosotros» autoriza esta investigación y propuesta. Esta capacidad nueva sobre presupuestos aprobados no estaba incluida en el PR526: requiere aprobación de esta historia antes de redactar su especificación técnica, conforme a AGENTS/feature-factory. La especificación concretará owners, cambios permitidos, archivos, pruebas, ensayo, canon y promoción. No se solicita volver a entregar presupuestos ni catálogo.
