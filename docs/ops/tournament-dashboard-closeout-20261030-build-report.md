# Cierre técnico de tableros: candidato validado

Fecha: 2026-10-30. Estado: implementación y ensayo restaurado completos; **sin aplicación productiva de esta corrección**. Historia y especificación aprobadas, incluido el lote explícito de 13 equivalencias de Copa. El usuario aprobó el PR #515 y la continuidad hasta despliegue y carga, condicionada a checks en verde. Merge, promoción y aplicación tendrán sus propios recibos.

## Resultado

El operador completa 40 cuentas finales (36 Liga, 4 Homeless) y 16 asignaciones precisas (3 Liga previamente decididas y auditadas, 13 Copa aprobadas en esta conversación). Conserva conceptos retirados, fases, cantidades, importes, referencias, pagos y pólizas. El registro de acciones queda fijado por SHA-256; una metadata de consolidación editable no concede autoridad. La confirmación canónica audita fuente factual y destino exactos, y el lector verifica esa misma prueba.

La distribución queda en Liga40, Homeless52 y Copa52. Para que la aprobación normal siga vigente, cinco calendarios base de Liga que estaban en cero se regularizan a su misma semana40 efectiva. Son 248,500 ya autorizados, no presupuesto nuevo. El owner sustituye 260 filas de plan y 260 asignaciones; las 520 originales se conservan íntegras en auditoría. Cuatro de esas líneas pertenecen a las 40 cuentas; Operación FEMEBE sólo recibe reparación de calendario, no una cuenta adicional. El guard de aprobación no se omite.

El lector añade evidencia de las 85 cuentas documentadas de PSP2026 sin requerir conceptos ficticios. Separa balanzas oficiales y pólizas, conserva `missing_summary_marker`, valida integridad de la póliza completa y no recupera periodos retenidos de versiones antiguas. La clasificación por rama no acredita exclusividad, moneda o IVA contables: los importes registrados se muestran sin moneda inventada y las comparaciones incompatibles permanecen N/D.

La procedencia de 43 líneas de Copa se reconoce por identidad, fase, importe, fuente/fila/plan y recibo original comprometido. No se fabrica upload_source ni se cambia la definición fiscal del Real. HTML, PDF y XLSX muestran los mismos datos documentales y omiten barras, porcentajes o proyecciones que compararían bases incompatibles. Se conserva el alcance de Finanzas y de los tres torneos/2026.

## Archivos y límites

- Owner `budgets/service.py`: confirmación acotada a 16 pares y validación auditada de asignación/lectura; sin writer de pagos o pólizas.
- Operador `scripts/repair_tournament_dashboard_closeout.py`: preview fresco READ ONLY, perfil de actor, hashes, release servido, transacción exterior, locks, SQL allowlist y recibos.
- Configs: lote exacto aprobado, clasificación documental 2026 y procedencia comprometida de Copa. Documentos de las cinco regularizaciones en `docs/operations/`.
- Lectores/presentación: `client_executive/accounting_bridge.py`, `service.py`, `home.py`, `home_ui.py`, `ui.py`, `reports.py`; `executive/exporter.py`; helper `budgets/source_provenance.py`.
- Nuevas suites: operador unit/PostgreSQL, documentary unit/PostgreSQL, procedencia y presentación/PDF.

No nuevas dependencias, migraciones, permisos, handlers de pagos ni activación de otros candidatos. Checkout principal y su trabajo ajeno intactos. Los artefactos privados y `.coverage` no se incorporan al commit.

## Verificación

- **426 pruebas conjuntas pasadas**, ninguna omitida: nuevos contratos y regresiones de presupuesto, distribución, Dirección, bridge, procedencia, presentación y cinco casos PostgreSQL reales. Runner del Python runtime con `PYTHONPATH=src`, `SAMCHAT_WEEKLY_TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55479/postgres`, bootstrap `import devnous.gastos; pytest.main(...)`.
- Revisión independiente: **119 pruebas pasadas**, incluidos PostgreSQL y PDF. Ningún hallazgo crítico, importante o menor pendiente del alcance; se excluye `.coverage` generado.
- Cobertura focal: helper de procedencia 90%; bridge documental 94% en la medición del builder. Preflight local de líneas modificadas: 420/464, 90.52%; el resultado canónico de CI aún debe verificarse.
- Inventario privado regenerado por el nuevo módulo: 493 fuentes frente a 492, las mismas 613 rutas y 44 acciones, sin parse gaps ni activación nueva; sus nueve pruebas pasaron tras corregir el desajuste de reproducción.
- Black/isort y lint focal de los archivos nuevos/cambiados; F/E9 de operador, reader, presentación y pruebas aprobados. `source_provenance.py` y sus pruebas pasan flake8 con el límite 88 del proyecto. El owner extenso mantiene deuda previa F811 (`require_ungoverned_gastos_project`) y F841 (`subproyecto`) fuera del delta; no se declara lint global limpio.
- Inspección sintética 1440/390 px sin desbordamiento de página, y PDF con prueba de ausencia de barras comparativas falsas. Son fixtures, no UAT autenticada.

Ensayo con copia real `samchat_dashboard_closeout_restore_20261030`, acceso local root-only al puerto55483 y rechazo expreso del destino productivo por el proxy. Fuente de respaldo: `/srv/samchat/backups/dashboard-closeout-20261030/before.dump`, SHA `abeeb54fd736e860a122228c2cf47138a1449320966dc6ff7b243e9b9812e4f0`. Se verificaron rollback tras 36 commits internos, rollback entre calendarios, rechazo de hash alterado, aplicación completa y repetición sin cambios. La aprobación normal pasó tras la regularización exacta.

Recibo: `/root/samchat/tests/artifacts/cierre-tableros-20261030/operator-restore-five-acceptance/acceptance.json`, SHA `92de0e21b5a0f7e538e93abfab61a71686f55d35accf4b9f8b6534758048e067`. Las diez huellas de código del recibo coinciden con la versión revisada. SQL independiente READ ONLY verificó las siete tablas protegidas, campos financieros y otras filas intactos; 263 importes efectivos semanales idénticos, ingresos esperados idénticos, versión aprobada/nota Borrador e historial de Copa conservados.

Comandos relevantes: pytest focal y conjunto; black/isort/flake8 por ruta; `git diff --check`; `pg_dump -Fc`, `pg_restore --list` y restauración sólo a la base de prueba; CLI `--restore` preview/apply con hash y fallos restringidos al ensayo; SQL `REPEATABLE READ READ ONLY` para contraste independiente. No se han ejecutado en producción los comandos apply de esta corrección.

## Pendientes y riesgos

Doce movimientos ambiguos o sin destino acreditado no se asignan automáticamente. Once movimientos ya clasificados pendientes de contabilización no se arreglan reasignándolos. Los seis CFDI compartidos de la revisión requieren elegibilidad/aplicación global; se conserva N/D sin cancelar gastos por inferencia. Las cuentas compartidas, una referencia retirada ajena y la cuenta de año anterior conservan su limitación. Base/moneda contables y tres líneas positivas legacy de Liga carecen de evidencia homogénea de base: no se inventa IVA. Las 26 filas fijas sin crosswalk de Finanzas permanecen N/D.

Sports marzo/abril/junio/julio y GOAT agosto siguen retenidos; no se recibió balanza de septiembre. Aceptación autenticada de Finanzas sigue separada. Estos pendientes no se presentarán como completados por el despliegue técnico.

Canon unchanged: concreta fuente, atribución documental, trazabilidad, conciliación y distinción de bases existentes; preserva autoridad, definición fiscal, estados y economía. La regularización de cinco calendarios es continuidad de la semana40 ya autorizada, documentada y probada, no un permiso para saltar controles.

## Correcciones de revisión del PR #515

La revisión automática detectó dos P1: el adaptador ReadBatch no conservaba las vistas escalares ni el acceso posicional utilizado por los lectores canónicos, y una denegación explícita de Presupuestos no bloqueaba la evidencia contable de la portada. Ambos se corrigieron sin ampliar autoridad: vistas de filas/mappings/scalars separadas, cardinalidad correcta, aislamiento de JSONB en caché y gate conjunto Finanzas/Presupuestos antes de consultar el comparador y el lector. Ocho regresiones cubren tres torneos y la vista conjunta, con y sin superadmin.

Suite conjunta actualizada: **448 pruebas pasadas y un caso opt-in omitido** por no estar habilitada la copia privada; ese caso se ejecutó luego con su URL exacta y pasó junto con la otra prueba PostgreSQL del archivo (**2 pasadas**, sin omisiones). En la copia real la portada obtiene los tres presupuestos y las 85 cuentas documentadas; las 16 pruebas de decisión se verifican también a través del ReadBatch real. Ninguna escritura productiva ni cambios en el respaldo restaurado. Evidencia privada: `readbatch-home-restore-acceptance.json`, SHA `11bd32ffbca29a787e353222452fd778efd6e0b139657c49906e72504d3b021e`.

Los cuatro jobs originales de GitHub fallaron antes de ejecutar pruebas por `OSError: [Errno 28] No space left on device` instalando dependencias. Clasificación: infraestructura. El usuario aprobó expresamente el ajuste de capacidad de los runners y continuar hasta despliegue/carga: tres workflows liberan SDKs no usados y caché pip únicamente en GitHub-hosted/Linux; pip mantiene las dependencias originales con caché deshabilitada. No cambian tests, umbrales, permisos ni triggers; el YAML original es idéntico tras retirar sólo estas adiciones y el contrato de quality gate pasa. Canon unchanged por tratarse de capacidad temporal y conservar el contrato existente. Los checks finales aún requieren recibo de CI.

Revalidación independiente de los P1 y el ajuste de CI: **40 pruebas pasadas sin omisiones**, incluida la copia real con los tres torneos y las 16 decisiones; sin hallazgos pendientes. Black, isort, inventario (9 pruebas), contrato CI y diff hygiene verificados.

Segundo intento remoto: instalación superada; **128 pruebas Browser y 826 Assistant pasaron**, pero sus jobs fallaron en el post-step de setup-python porque la limpieza eliminó la carpeta pip. Corrección dentro del ajuste autorizado: recrear la carpeta vacía tras limpiarla. Los slices aislados de private-plugin revelaron que su fixture AST no inyectaba el nuevo helper; se cargan los cuerpos canónicos puros y el JSON versionado, conservando aislamiento y sin cambiar código productivo ni omitir tests. Clasificación: regresiones de integración/CI causadas por el cambio, corregidas en el mismo alcance.

Corrección de fixture y post-step validada: **136 tests offline y 20 tests de PostgreSQL efímero pasados**, runner ejecutado como postgres, TCP deshabilitado; revisión independiente 22 tests y 18 subtests sin hallazgos. No cambios adicionales al código de aplicación. Se conservan versiones de requisitos aislados; un intento inicial con el MCP viejo del entorno previo fue reemplazado por el entorno de requisitos versionados, no por alteración de dependencias.
