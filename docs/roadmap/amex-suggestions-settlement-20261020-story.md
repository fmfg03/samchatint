# AMEX: sugerencias de CFDI y contabilización sin duplicidades

Fecha: 2026-10-20. Estado: STORY_APPROVED. Aprobación: el usuario respondió «aprobada» a la historia completa presentada en esta conversación. Este documento conserva ese alcance y sus decisiones; no acredita aprobación técnica, construcción o publicación.

Como usuario de Contabilidad de Plataforma, quiero que los cargos del estado AMEX muestren automáticamente las facturas descargadas del SAT que más se asemejen, para revisar excepciones y terminar en pólizas contables sin búsqueda factura por factura ni duplicar gastos.

## Criterios aprobados

AC1. Sugerencias automáticas por cargo usando importe, proveedor y fechas contra CFDI de la entidad correspondiente; búsqueda manual disponible.
AC2. Fechas de cargo y emisión separadas; el cambio de mes no descarta candidatas. Ejemplo: Suntory, cargo2026-09-29, CFDI2026-10-06 por3814MXN.
AC3. Mostrar proveedor, RFC, UUID, total, fecha y diferencias. Ambigüedad requiere selección; semejanza no confirma vínculo. No convertir automáticamente diferencias en propinas.
AC4. Distinguir PUE/PPD y separar reconocimiento del gasto, liquidación del proveedor y pago posterior de AMEX; sin duplicar registros.
AC5. Política operativa indicada por Plataforma para TODAS sus tarjetas AMEX: ausencia del complemento no bloquea conciliación, registro de IVA acreditable ni póliza. Importes de IVA salen del CFDI, sin inventarlos. Disponibilidad del complemento visible.
AC6. Para gastos nuevos, Contabilidad asigna proyecto/categoría; Juan Pablo asigna partida. Cuenta del gasto desde partida y contrapartida desde tarjeta.
AC7. Si existe gasto previamente contabilizado, incluido Odilón, vincular reconocimiento y cancelar saldo real de cuenta de gastos contra AMEX con cuenta y saldo de origen. No duplicar gasto, IVA o presupuesto. Informes de Odilón fuera de pendientes directos; cargos visibles.
AC8. Revalidar aplicaciones previas/conflictos. Reintentos idempotentes y evidencia reconocida inmutable.
AC9. Pago mediante pantalla existente: cancelar pasivo AMEX contra bancos, sin nuevo gasto/IVA.

Fuera de alcance: nuevas reglas de deducibilidad, modificación de pólizas históricas, reparación masiva y pagos automáticos. UAT incluye gasto nuevo y cancelación de cuenta de gastos.
