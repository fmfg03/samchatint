# AMEX: selector de informes por tarjeta y período del estado

**Historia y especificación técnica aprobadas explícitamente por el usuario.** Fecha: 2026-10-14. Cambio solicitado: el selector del paso 3 sólo ofrece INFORMEs aprobados con al menos una partida activa AMEX de la tarjeta del estado y fecha dentro de su período declarado; conserva exclusión por beneficiario explícito de Odilón, faltantes visibles y preparación del informe completo.

Investigación readonly en `/root/samchat/.worktrees/amex-statement-workflow-20261013`, HEAD observado `117dd8c1527f302f59e77060fb8831af8319f0c0`, limpio. Se leyeron AGENTS y los tres canons en orden; SHA coinciden con register: producto `b55ae07a926eb8de78e07345b744e59b6fdf30021e17208e12309580aa32875c`, ingeniería `0d2128b70f40c5670b2153599dd0ab4cb2b768ebba634ce5a9b6283baa9ecd81`, sweep `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985`. No consultas productivas, código, pruebas ni despliegue del filtro ejecutados. La autorización previa de desplegar la UI es independiente.

## Implementation approach

El propietario de esta lectura es `amex_statement_workflow_service._statement_workflow_view`. Hoy su EXISTS sólo exige partida activa AMEX bajo la CuentaDeGastos del documento; por eso mezcla tarjetas y fechas.

Acotar ese EXISTS al estado seleccionado, manteniendo un único predicado reutilizable de elegibilidad para listado y selección GET:

- Documento `tipo=INFORME`, `estado=aprobado` y cuenta de gastos explícita conforme alcance actual.
- En **la misma partida**: `cuenta_gastos_id = Documento.cuenta_gastos_id`, `estado_gasto != cancelado`, predicado canónico `company_amex_sql_condition()`, tarjeta explícita y fecha en período. No satisfacer tarjeta con una partida y fecha con otra.
- Resolver tarjeta exclusivamente desde `AmexStatementImport.card_account_id` y `AmexCardAccount` correspondiente. El catálogo tiene `last4` UNIQUE: comparar `ExpenseReport.ultimos_4_digitos` registrado explícitamente con `card.last4`. No deducirla del nombre FGV, dueño/capturista, importe, CFDI o tarjeta que aparezca en otra partida.
- Si existe representación/reconocimiento canónico, su `card_account_id` no puede contradecir el UUID del estado. Una contradicción excluye la opción y conserva explicación/pendiente; no usar los cuatro dígitos para ocultarla. Esta comprobación sólo pertenece al predicado de elegibilidad; no modifica el owner de reconocimiento ni sus validaciones financieras. Sin representación previa, los dígitos explícitos contra el catálogo único son el patrón vigente de identificación.
- Usar **`ExpenseReport.fecha`**, fecha del gasto, contra `AmexStatementImport.period_start` y `period_end` inclusive. SQL `CAST(fecha AS DATE) BETWEEN inicio AND fin` evita excluir consumos en la tarde/noche del último día. No usar aprobación del documento, captura/importación, fecha contable del corte ni mes de la póliza. No cambiar zona horaria ni fechas almacenadas.
- Después aplicar `load_amex_attention_context` sin alterar su contrato. Odilón queda excluido por UUID de beneficiario inequívoco según política existente; ausencia/conflicto de beneficiario conserva advertencia y no autoriza fallback al capturista. Los cargos bancarios nunca reciben este filtro.

Es condición **existencial**, no unanimidad: basta una partida válida. Un informe con otras tarjetas, fechas fuera del período o partidas con datos pendientes se puede ofrecer si cumple esa condición; al abrirlo se prepara completo y sus bloqueos contables siguen visibles. No filtrar la lista de partidas ni las decisiones/póliza del informe.

Cargar tarjeta declarada una vez en la lectura y reutilizarla en retorno/UI. No N+1 por informe ni lecturas de previews contables para decidir las opciones. Mantener `session.no_autoflush`: GET no hace flush, commit, actualizaciones ni writes financieros.

### Selección GET y vínculos previos

Para `GET /admin/contabilidad/amex/estados/{import_id}/polizas?informe_id=...`, revalidar selección nueva con exactamente el predicado del selector, además de permisos/estado/política existentes. UUID de informe de otra tarjeta/período enviado manualmente o selección stale no se prepara como fuente nueva de esta carga: mostrar mensaje claro y selector vigente, sin efectos.

No modificar owners POST de confirmación/corte/incorporación, sus payloads, firmas, locks o permisos. Esta entrega acota presentación y selección GET; no establece una nueva regla de contabilización o conciliación bancaria. Acceso contable independiente al informe conserva su contrato.

Vínculos de cortes ya persistidos permanecen visibles como evidencia/historial, incluso si su informe ya no cumple el nuevo criterio de descubrimiento. No borrarlos, reconstruir fuentes, reatribuir actores ni invalidar exportaciones congeladas. Distinguir restauración de fuente desde un `AmexStatementCutLink` existente de selección GET nueva; la primera no amplía el selector. Evitar que el auto-restablecimiento de un vínculo antiguo dé un error que haga inaccesible su historial.

## Data model changes

Ninguno. Se utilizan las columnas existentes; sin migración, índices nuevos, dependencias, backfill ni reparación. Cargas legacy sin `AmexStatementImport` identificado no reciben tarjeta/período reconstruidos: siguen su acceso legacy. Tarjeta de catálogo ausente o contexto declarado inválido no ofrece opciones por fallback; muestra motivo explícito.

## API changes

Ninguna ruta nueva. Cambia la proyección `reports` del servicio y el tratamiento de selección nueva en GET. Reutilizar función privada/predicado para no duplicar reglas entre query y validación GET. Mantener respuesta y autorización Finance actuales. No alterar exportación, asociación bancaria, importación o confirmación financiera POST.

## UI changes

Paso 3 informa: **Informes aprobados con al menos una partida AMEX de esta tarjeta dentro del período declarado. Al seleccionar se prepara el informe completo.** Sin opciones, mostrar ausencia explícita con tarjeta/período visibles, no presentar todos los informes como fallback.

Paso 2 conserva acceso contextual visible a **Partidas AMEX sin tarjeta**, mediante el acceso existente `/admin/gastos/amex/conciliacion#sin-tarjeta` y retorno permitido ya existente cuando aplique. Explicar que las partidas sin tarjeta no pueden atribuirse todavía a la tarjeta cargada y deben identificarse por Finanzas. No añadir cola global nueva ni asociarlas por persona/fecha. Eliminar una opción por falta de tarjeta no elimina esas partidas de sus pendientes existentes. El acceso sirve aun cuando el selector esté vacío.

Fecha ausente, cuenta ausente o identidad contradictoria permanece como excepción mediante los accesos vigentes; no adivinar su período, owner o tarjeta. Los 14 históricos conservan sus bloqueos. Cargos, importes originales, vínculos y contadores bancarios no cambian por filtrar opciones.

## Background jobs

Ninguno.

## Test plan

1. PostgreSQL/query real: informe aprobado con partida AMEX de tarjeta correcta y fecha intermedia entra; otra tarjeta, fuera del período, cancelada, no AMEX o documento no aprobado sale. Inicio y fin entran, incluyendo hora final del último día; día anterior/posterior sale. Período que cruza meses/años usa fechas declaradas, no mes actual.
2. Dos partidas diferentes, una con tarjeta correcta fuera de período y otra dentro de período con tarjeta distinta, **no** cumplen. Varias coincidencias no duplican la opción.
3. Informe con una coincidencia y otras partidas de fecha/tarjeta distinta se ofrece y su preview conserva **todas** las partidas activas y sus montos/decisiones. Nada de corte/póliza/CFDI se trunca.
4. Tarjeta faltante no se incluye por persona, importe o fecha; enlace pendiente existente visible en pasos 2/3 sin atribución bancaria. Reconocimiento con card UUID contradictorio no se ofrece. Policy Odilón se mantiene; capturista Odilón sin beneficiario inequívoco no introduce una exclusión nueva.
5. GET con UUID válido de otro contexto/stale muestra mensaje sin preview nuevo ni mutación; selección válida entra. Historial `AmexStatementCutLink` anterior conserva lectura/IDs/exports y no reentra como opción elegible por fallback.
6. Card/import/contexto ausente y legacy no reconstruyen identidad. Usuarios no Finance o inactivos mantienen denegación. GET no autoflush/commit ni crea consumo, vínculo o corte.
7. Regresiones actuales de servicio, rutas, navegación, exports y corte completo permanecen verdes. Ejecutar suites focalizadas de statement workflow y UI, PG aislado, lint/tipos por diff, cobertura modificada >=85%, inventario private-plugin cuando cambien líneas de rutas, y validación independiente readonly contra historia/spec. No pruebas con filas productivas.

## Files expected to change

- `src/devnous/gastos/services/amex_statement_workflow_service.py`: predicado común, listado contextual y selección GET nueva/historial explícito.
- `src/devnous/gastos/routes/admin_amex_statement_routes.py`: mensajes, enlace existente de pendientes y recuperación controlada de selección incompatible.
- `tests/unit/gastos/test_amex_statement_workflow_service.py` y `test_amex_statement_routes.py`: lectura pura, selección, mensajes/pending/historial.
- `tests/integration/test_amex_statement_workflow_postgres.py`: filtro SQL, límites de fechas, misma partida, integridad del informe completo y historial.
- Documento de spec/reporte de build e inventario generado si cambia ruta fuente.

No cambios a modelos, SQL de migración, atención por beneficiario, importador, recognition, corte, posting o esquema financiero.

## Risks

Un informe puede abarcar varias fechas/tarjetas: selector contextual no implica que todo corresponda al estado ni que esté conciliado. Confirmación financiera completa y bloqueos existentes se mantienen. `last4` se usa como identidad explícita registrada contra catálogo único, nunca como matching de consumos. Vínculo de navegación no constituye asociación bancaria.

**Canon unchanged:** el filtro reduce descubrimiento de opciones mediante identidad y período ya declarados, sin cambiar autoridad, fuente contable report-first, atención por beneficiario, reconocimiento único o granularidad del INFORME completo. Documentar esta razón en la misma PR; no editar canon silenciosamente.

## Rollback considerations

Revertir el cambio de servicio/UI restablece descubrimiento amplio previo; no hay DDL o datos que revertir. Conservar cortes, asociaciones, importaciones y evidencia histórica. Aprobación técnica precede a implementación; commit/push/PR/merge y despliegue del filtro requieren autorización correspondiente, independiente del despliegue UI previamente aprobado. La aceptación con Finanzas es evidencia separada.
