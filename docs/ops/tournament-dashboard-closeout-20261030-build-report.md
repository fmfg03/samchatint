# Cierre técnico de tableros: candidato validado

Fecha: 2026-10-30. Estado: implementación y ensayo restaurado completos; **sin aplicación productiva de esta corrección**. Historia y especificación aprobadas, incluido el lote explícito de 13 equivalencias de Copa. La publicación, CI, revisión del diff/PR, merge, promoción y aplicación tendrán sus propios recibos.

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
