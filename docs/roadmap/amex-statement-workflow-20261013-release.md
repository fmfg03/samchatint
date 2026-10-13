# Preparación de release del recorrido AMEX

Fecha: 2026-10-13. Candidato aislado sobre `4b971ab3ff4576216a7009aaf883d1351b7c7eb2`.

Este documento prepara una operación revisable. No acredita commit, publicación, merge, aplicación de schema, despliegue ni aceptación Finance. La aprobación de historia, especificación y enmiendas exactas de canon autoriza implementación; los efectos posteriores requieren su autorización específica.

## Alcance

Recorrido Finanzas → AMEX → Estados: cargar CSV con tarjeta/titular/período, resolver pendientes, seleccionar fuentes y confirmar/descargar cortes completos de INFORME. La exclusión de atención de Odilón usa su UUID explícito y consistente en documento/cuenta; conserva cargos bancarios, importes, estado y controles financieros. `I-138907` mantiene atención normal. Los 14 históricos bloqueados requieren revisión separada.

## Preflight y aplicación futura

1. Fijar commit exacto aprobado y release inmutable. Revisar diff, hashes de canon/registro, recibos de pruebas y lista de migraciones; no publicar un checkout primario sucio.
2. Verificar en lectura la base/schema/rol objetivo y migraciones previas de AMEX. Generar backup y validar que se puede listar/restaurar. No imprimir DSN, credenciales o filas de negocio.
3. El owner autorizado aplica `database/migrations/20261013_amex_statement_workflow.sql` transaccionalmente. Tres tablas nuevas, FKs restrictivos, identidad única de importación, receipts y guards inmutables. No hay backfill ni reparación de gastos.
4. Ejecutar `scripts/check_amex_statement_schema.py --database-url-env AMEX_STATEMENT_SCHEMA_DATABASE_URL` con entorno explícitamente autorizado. El checker usa transacción de solo lectura y catálogo; exigir `ready=true` sin errores. No crea schema ni lee filas financieras.
5. Preparar entorno del release sin nuevas dependencias productivas. Validar imports, inventario de rutas, guards de regresión/registro y packaging sobre bytes exactos.
6. Promover por el único selector `50-current-release.conf` de `samchat-gastos.service`; comprobar release real, health/readiness, guards y reinicios. No inferir aceptación de negocio de esos checks.

## Rollback

Restaurar selector de código anterior y verificar servicio/health/guards. Conservar receipts nuevos, gastos y cortes financieros. No borrar historial para volver al código anterior. El down-DDL `20261013_amex_statement_workflow_rollback.sql` rechaza tablas pobladas y solo es utilizable en una instalación vacía autorizada; no es una reparación productiva.

## UAT Finance posterior

- Navegar páginas del historial de estados y regresar al recorrido conservando la carga seleccionada.
- Revisar cancelaciones legadas: cargo y monto original visibles, revisión pendiente, asociación histórica preservada. La cancelación de una fuente `report_bridge` permanece impedida por el owner.

- Verificar las nueve cuentas/informes explícitos de Odilón fuera de descubrimiento, selectores y contadores manuales; Bibiana, identidades ausentes/contradictorias y cargos bancarios siguen visibles.
- Cargar un estado real y comparar todas las monedas/importes/fechas con el archivo; revisar filas omitidas. Replay no agrega cargos.
- Resolver evidencia y asociación bancaria, volver al estado sin perder contexto; nunca afirmar asociación por un vínculo de navegación.
- Seleccionar un INFORME completo, incluyendo otras tarjetas/fechas. Revisar fuente/versiones, tratamientos y excepciones; confirmar una póliza inicial canónica única.
- Incorporar un corte real ya existente de forma explícita; conservar actor, fecha y movimientos. Descargar todos los cortes seleccionados con bloqueo completo si alguno falla.
- Validar importación en COI y aceptación contable por el operador autorizado. Descargar un archivo no marca COI importado.
