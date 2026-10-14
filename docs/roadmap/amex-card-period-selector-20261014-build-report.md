# Informe de construcción: selector AMEX por tarjeta y período

Fecha: 2026-10-14. Historia y especificación aprobadas explícitamente. Rama aislada `fix/amex-card-period-selector-20261014`, base `96537ceb9bff21f3bb8342f74cea82b8eb582b8a` de la corrección visual, con review integrado. La promoción del filtro requiere su aprobación final y autorización externa propias.

## Resultado y alcance

El selector ofrece informes aprobados con al menos una misma partida activa AMEX con tarjeta explícita y fecha dentro del período declarado inclusivo. Rechaza contradicción de identidad canónica de tarjeta. Conserva política de atención por beneficiario, informe completo, todas sus tarjetas/fechas y bloqueos, cargos bancarios, fuentes históricas persistidas y exportaciones. Una selección GET incompatible muestra motivo sin preparar un informe nuevo. Hay acceso explícito a partidas sin tarjeta, sin atribuirlas por persona/importe/fecha.

Se modifican el servicio de lectura y el router de estados, sus pruebas unitarias/integración/browser, historia/spec y este reporte. El router contable lleva el mismo acceso a Conciliación AMEX corregido en PR483; no es cambio adicional financiero. Inventario JSON/matriz CSV regenerados por fuentes/líneas. Sin modelos, migraciones, dependencias, owners POST, contabilización, asociación bancaria ni reparación de datos.

## Verificación

- Regresión AMEX completa: 475 PASS, 1755 deselected. Comando `PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps /root/samchat/.venv/bin/python -m pytest -q tests/unit/gastos -k amex --no-cov --tb=short`. Log `/tmp/samchat-amex-card-period-all-unit-20261014.log`.
- Backend: 32 unitarias + 28 PostgreSQL reales PASS; límites inclusivos con horas, cruce de año, coincidencia en misma partida, identidad reconocida contradictoria, informe completo y historial/exportaciones. Servicio 94,10% de cobertura; cambio backend 22/22 líneas cubiertas.
- Frontend: 46 unitarias ASGI + 6 browser PASS; cuatro regresiones adicionales de navegación PASS. Ruta 95,29% de cobertura. Formularios financieros AST idénticos.
- Validación independiente: 78 unitarias + 28 PostgreSQL PASS. Aceptación independiente: 155 PASS (100 unitarias, 28 PostgreSQL, 6 browser y 21 política de atención), sin fallos ni omisiones. Matriz en recibo separado.
- Inventario: `python scripts/private_plugin/inventory.py`, 590 rutas/44 acciones, cero parse gaps; `pytest -c /dev/null -q tests/unit/private_plugin/test_inventory.py`, 9 PASS.
- Black, flake8 focal max88, mypy focal y `git diff --check` PASS; no se afirma calidad global limpia por deuda existente en otros archivos.

## Fallos, desviaciones y riesgos

Trece setup errors PostgreSQL iniciales correspondieron a base desechable inexistente; se preparó correctamente el entorno y la repetición completa pasó. Deprecaciones heredadas permanecen visibles. Fecha NULL no tiene prueba dedicada; el predicado SQL la excluye sin atribuir período. Sin fallos funcionales pendientes ni desviaciones del alcance aprobado.

Pruebas PG y navegador son sintéticas, no UAT productiva. Un informe puede contener otras tarjetas/fechas: la elegibilidad de una partida no acredita conciliación del informe ni del banco. Preparación/confirmación completa conserva autoridad contable anterior. No se cargó archivo real ni se creó póliza productiva. CI, publicación y aceptación Finance del filtro pendientes.

**Canon unchanged:** se acota descubrimiento con tarjeta/período ya declarados, sin cambiar autoridad, reconocimiento, atención por beneficiario o granularidad del informe completo. Los tres canons mantienen sus hashes registrados.

Evidencia: `/tmp/amex-card-period-backend-coverage.log`, `/tmp/samchat-amex-selector-frontend-build-report-20261014.md`, `/tmp/samchat-amex-card-period-selector-acceptance-20261014.md` y `/tmp/samchat-amex-card-period-selector-validation-20261014.md`.

## Integración con corrección visual

Se integró la revisión final de PR483 antes de publicar. El solapamiento de CSS se resolvió conservando la regla de wrapping más específica; inventario regenerado desde índice resuelto (590 rutas, sin duplicados). Repetición desde fuente integrada: 475 unitarias AMEX y 8 browser (recorrido, paginación e identidad) PASS, sin cambios de servicios financieros durante la integración. El usuario aprobó explícitamente cambio final, commit/push/PR/merge y despliegue del filtro.
