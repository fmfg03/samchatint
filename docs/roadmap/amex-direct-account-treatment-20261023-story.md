# Historia aprobada: prestaciones AMEX con presupuesto y sin proyecto

Fecha: 2026-10-23. Aprobación: el usuario corrigió «las prestaciones sí requieren control presupuestal, pero no llevan proyecto» y después respondió «apruebo». Esta revisión sustituye la propuesta anterior que excluía las prestaciones de Control Presupuestal. Historia aprobada; especificación, implementación y publicación conservan sus checkpoints.

## Necesidad

Como operador de Contabilidad, quiero asignar el tratamiento real de los cargos AMEX sin exigir proyecto o categoría cuando no corresponden, para completar la póliza con las cuentas y controles que sí requiere cada tratamiento. La mayoría de los cargos son prestaciones absorbidas por la empresa o consumos a la cuenta deudora del socio; ese porcentaje no autoriza clasificarlos automáticamente.

## Flujo aprobado

| Tratamiento | Proyecto/categoría capturados por Contabilidad | Control Presupuestal | Cuenta de cargo |
|---|---|---|---|
| Prestación absorbida por la empresa | No obligatorios | Sí: Juan Pablo elige la partida | Sale de la partida elegida |
| Cargo al socio | No obligatorios | No | Cuenta deudora válida del socio, elegida expresamente |
| Gasto de proyecto/torneo | Conserva el flujo vigente | Sí: Juan Pablo elige la partida | Sale de la partida elegida |

La contrapartida continúa siendo el pasivo de la tarjeta. La prestación no origina reembolso: el consumo fue pagado con la tarjeta. Contabilidad selecciona el tratamiento de cargos individuales o lotes homogéneos; para prestaciones no elige una cuenta de gasto que sustituya la autoridad de Juan Pablo.

## Aceptación

1. Una prestación puede enviarse a Control Presupuestal sin proyecto ni categoría previos. Juan Pablo asigna una partida autorizada y su mapping determina la cuenta; no puede llegar a póliza sin esa asignación.
2. La partida conserva su identidad y ubicación presupuestal real. Su selección no inventa ni copia un proyecto en el gasto de prestación.
3. Un cargo nuevo a cuenta deudora identifica explícitamente la cuenta del socio y motivo, no exige proyecto/categoría/partida y no aparece pendiente de Juan Pablo. No requiere una cuenta de gastos histórica ni origina reembolso.
4. Los gastos de proyecto conservan su contexto, permisos, categorías, partida, cuenta y controles actuales.
5. La vinculación de CFDI se conserva y es independiente de la captura presupuestal. La ausencia de factura se resuelve mediante declaración explícita conforme al tratamiento; no se fabrica evidencia o deducibilidad.
6. Un estado puede mezclar las tres rutas y mantiene revisión completa, confirmación Finance, una póliza por estado y prohibiciones de reconocimiento doble o exportación parcial.
7. Las cuentas deben ser activas y estar autorizadas para entidad y tratamiento; se valida la contrapartida real de la tarjeta. No se infiere una cuenta por nombre de comercio o por falta de proyecto.
8. Aplicar un lote requiere selección y confirmación explícitas; un dato incompatible o una versión cambiada no deja un lote parcialmente modificado.
9. Cambiar ruta antes del corte conserva auditoría y libera asignaciones incompatibles mediante su owner; no borra presupuesto o evidencia silenciosamente. Un corte reconocido no se modifica.
10. Se conservan CSV, signos efectivos corregidos, CFDI e historia. No hay reclasificación automática de cargos históricos.
11. UI, contadores, colas, bloqueos, datos y exportación reflejan la ruta elegida: prestaciones pendientes de partida, deudores sin cola presupuestal y proyectos con controles existentes.

## Fuera de alcance

Automatización PASE; cambios de búsqueda SAT o ranking; parser/signos; nuevas reglas fiscales o deducibilidad; pago bancario de la tarjeta; informes de gastos; reembolsos; cambios de importes presupuestales o reclasificación productiva automática.

## Investigación

Base observada: release d591cc4a13987d33d2336bcf872401f3a6c7acb6. Canons leídos en la conversación y hashes revalidados contra el registro. Los owners actuales de clasificación y corte directo obligan a presupuesto para cargos nuevos. La declaración anterior de historia quedó corregida por la decisión expresa del usuario; no se implementará su exclusión incorrecta de prestaciones.
